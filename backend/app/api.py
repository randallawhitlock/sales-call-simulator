"""Learner-facing HTTP API. Every research call and record change becomes an event.

Responses carry only catalog data, the CRM seed and what the learner produced. Scenario
truth, customer knowledge, persona rolls and seeded document defects stay server-side until
the call ends, when the answer key and findings unlock.
"""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, Request
from pydantic import BaseModel, Field

from app.content.catalog import Drug
from app.content.common import Id, PlanId
from app.content.scenario import CrmSeed, ElectionPeriod, Scenario
from app.engine import research
from app.engine.checks import CheckResult, run_checks
from app.engine.events import Event, EventType, Utterance, UtteranceIn
from app.engine.records import (
    Application,
    ApplicationFields,
    ConsentIn,
    CustomerRecord,
    EvidenceIn,
    FollowupIn,
    PeclMarkIn,
    ScopeIn,
    SubmitIn,
)
from app.engine.session import Invalid, NotFound, SessionService, TurnIn, TurnOut
from app.engine.store import SessionRow, SessionStatus

router = APIRouter(prefix="/api")


def get_service(request: Request) -> SessionService:
    service: SessionService = request.app.state.sessions
    return service


Service = Annotated[SessionService, Depends(get_service)]


# Views


class ScenarioCard(BaseModel):
    scenario_id: str
    learner_title: str


class FrameView(BaseModel):
    call_date: date
    plan_year: int
    coverage_effective: date
    election_period: ElectionPeriod
    agency_name: str
    learner_role: str
    county_name: str
    carriers_represented: int
    products_offered: int


class SessionView(BaseModel):
    session_id: str
    scenario_id: str
    learner_title: str
    status: SessionStatus
    frame: FrameView
    crm_seed: CrmSeed
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    on_hold: bool
    """The customer is on hold. Live state, so a reloaded call screen shows it correctly."""
    turn_pending: bool
    """A customer reply is being generated right now."""


class CreateSession(BaseModel):
    scenario_id: str
    seed: int | None = Field(default=None, ge=0)


class HoldIn(BaseModel):
    on: bool


class Logged[T](BaseModel):
    """A research result and the event that logged it. Pin evidence with ``event_seq``."""

    event_seq: int
    result: T


class CompareIn(BaseModel):
    plan_ids: list[PlanId] = Field(min_length=1, max_length=research.MAX_COMPARE)
    drugs: list[research.CompareDrugIn] = []
    pharmacy_id: Id


class ApplicationView(BaseModel):
    application: Application
    warnings: list[str]


class AnswerKey(BaseModel):
    scenario: Scenario
    document_defects: dict[str, str]


class Findings(BaseModel):
    session_id: str
    scenario_version: int
    catalog_version: str
    ruleset_version: str
    results: list[CheckResult]


def session_view(service: SessionService, row: SessionRow) -> SessionView:
    scenario = service.content.scenario(row.scenario_id)
    frame = service.content.frame
    return SessionView(
        session_id=row.session_id,
        scenario_id=row.scenario_id,
        learner_title=scenario.learner_title,
        status=row.status,
        frame=FrameView(
            call_date=frame.call_date,
            plan_year=frame.plan_year,
            coverage_effective=frame.coverage_effective,
            election_period=frame.election_period,
            agency_name=frame.agency_name,
            learner_role=frame.learner_role,
            county_name=service.content.catalog.county.name,
            carriers_represented=frame.carriers_represented,
            products_offered=frame.products_offered,
        ),
        crm_seed=scenario.crm_seed,
        created_at=row.created_at,
        started_at=row.started_at,
        ended_at=row.ended_at,
        on_hold=row.status is SessionStatus.ACTIVE and service.state(row.session_id).on_hold,
        turn_pending=service.turn_pending(row.session_id),
    )


def _found(fn: Any, *args: Any) -> Any:
    try:
        return fn(*args)
    except KeyError as exc:
        raise NotFound(str(exc.args[0]) if exc.args else "not found") from exc


# Sessions


@router.get("/scenarios")
def list_scenarios(service: Service) -> list[ScenarioCard]:
    return [
        ScenarioCard(scenario_id=s.scenario_id, learner_title=s.learner_title)
        for s in service.content.scenarios.values()
    ]


@router.post("/sessions", status_code=201)
def create_session(body: CreateSession, service: Service) -> SessionView:
    return session_view(service, service.create(body.scenario_id, body.seed))


@router.get("/sessions/{session_id}")
def get_session(session_id: str, service: Service) -> SessionView:
    return session_view(service, service.get(session_id))


@router.post("/sessions/{session_id}/start")
def start_call(session_id: str, service: Service) -> SessionView:
    return session_view(service, service.start(session_id))


@router.post("/sessions/{session_id}/end")
def end_call(session_id: str, service: Service) -> SessionView:
    return session_view(service, service.end(session_id))


@router.post("/sessions/{session_id}/hold")
def hold(session_id: str, body: HoldIn, service: Service) -> Event:
    return service.hold(session_id, body.on)


@router.post("/sessions/{session_id}/turns")
def post_turn(session_id: str, body: TurnIn, service: Service) -> TurnOut:
    """The learner's typed line and the customer's reply."""
    return service.take_turn(session_id, body)


@router.post("/sessions/{session_id}/utterances", status_code=201)
def post_utterance(session_id: str, body: UtteranceIn, service: Service) -> Utterance:
    return service.add_learner_text(session_id, body)


@router.get("/sessions/{session_id}/transcript")
def transcript(session_id: str, service: Service) -> list[Utterance]:
    return service.transcript(session_id)


@router.get("/sessions/{session_id}/events")
def events(session_id: str, service: Service) -> list[Event]:
    return service.events(session_id)


@router.get("/sessions/{session_id}/found")
def found(session_id: str, service: Service) -> research.Found:
    """What the learner's searches have returned so far, rebuilt from the event log for a
    reloaded call screen. Reading it logs nothing."""
    searches = [
        (str(e.payload.get("kind")), [str(i) for i in _ids(e.payload.get("result_ids"))])
        for e in service.events(session_id)
        if e.type is EventType.SEARCH
    ]
    return research.found(service.content.catalog, searches)


def _ids(value: object) -> Sequence[object]:
    return value if isinstance(value, list) else []


# Research


@router.get("/sessions/{session_id}/research/plans")
def research_plans(
    session_id: str, service: Service, zip: Annotated[str, Query(pattern=r"^\d{5}$")]
) -> Logged[list[research.PlanSummary]]:
    service.require_active(session_id)
    plans = research.plans_for_zip(service.content.catalog, zip)
    event = service.log(
        session_id,
        EventType.SEARCH,
        {"kind": "plans", "zip": zip, "result_ids": [p.plan_id for p in plans]},
    )
    return Logged(event_seq=event.seq, result=plans)


@router.get("/sessions/{session_id}/research/plans/{plan_id}")
def research_plan(session_id: str, plan_id: str, service: Service) -> Logged[research.PlanDetail]:
    service.require_active(session_id)
    detail = _found(research.plan_detail, service.content.catalog, plan_id)
    event = service.log(session_id, EventType.LOOKUP, {"kind": "plan_detail", "plan_id": plan_id})
    return Logged(event_seq=event.seq, result=detail)


@router.get("/sessions/{session_id}/research/providers")
def research_providers(
    session_id: str, service: Service, q: str = "", specialty: str = ""
) -> Logged[list[research.ProviderResult]]:
    service.require_active(session_id)
    found = research.search_providers(service.content.catalog, q, specialty)
    event = service.log(
        session_id,
        EventType.SEARCH,
        {
            "kind": "providers",
            "q": q,
            "specialty": specialty,
            "result_ids": [p.provider_id for p in found],
        },
    )
    return Logged(event_seq=event.seq, result=found)


@router.get("/sessions/{session_id}/research/providers/network")
def research_provider_network(
    session_id: str,
    service: Service,
    practice_id: str,
    plan_id: str,
    year: int | None = None,
) -> Logged[research.NetworkStatus]:
    service.require_active(session_id)
    plan_year = service.content.frame.plan_year
    year = plan_year if year is None else year
    if year not in (plan_year - 1, plan_year):
        raise Invalid(f"directories are available for {plan_year - 1} and {plan_year}")
    status = _found(research.provider_network, service.content.catalog, practice_id, plan_id, year)
    event = service.log(
        session_id,
        EventType.LOOKUP,
        {
            "kind": "provider_network",
            "practice_id": practice_id,
            "provider_id": status.provider_id,
            "location_id": status.location.location_id,
            "plan_id": plan_id,
            "year": year,
            "in_network": status.in_network,
        },
    )
    return Logged(event_seq=event.seq, result=status)


@router.get("/sessions/{session_id}/research/drugs")
def research_drugs(session_id: str, service: Service, q: str = "") -> Logged[list[Drug]]:
    service.require_active(session_id)
    found = research.search_drugs(service.content.catalog, q)
    event = service.log(
        session_id,
        EventType.SEARCH,
        {"kind": "drugs", "q": q, "result_ids": [d.drug_id for d in found]},
    )
    return Logged(event_seq=event.seq, result=found)


@router.get("/sessions/{session_id}/research/formulary")
def research_formulary(
    session_id: str, service: Service, plan_id: str, drug_id: str
) -> Logged[research.FormularyResult]:
    service.require_active(session_id)
    result = _found(research.formulary_lookup, service.content.catalog, plan_id, drug_id)
    event = service.log(
        session_id,
        EventType.LOOKUP,
        {
            "kind": "formulary",
            "plan_id": plan_id,
            "drug_id": drug_id,
            "covered": result.covered,
            "tier": result.tier,
            "prior_auth": result.prior_auth,
            "step_therapy": result.step_therapy,
            "quantity_limit": result.quantity_limit,
        },
    )
    return Logged(event_seq=event.seq, result=result)


@router.get("/sessions/{session_id}/research/pharmacies")
def research_pharmacies(
    session_id: str, service: Service, q: str = "", zip: str = ""
) -> Logged[list[research.PharmacyView]]:
    service.require_active(session_id)
    found = research.search_pharmacies(service.content.catalog, q, zip)
    event = service.log(
        session_id,
        EventType.SEARCH,
        {"kind": "pharmacies", "q": q, "zip": zip, "result_ids": [p.pharmacy_id for p in found]},
    )
    return Logged(event_seq=event.seq, result=found)


@router.get("/sessions/{session_id}/research/pharmacies/network")
def research_pharmacy_network(
    session_id: str, service: Service, plan_id: str, pharmacy_id: str
) -> Logged[research.PharmacyNetworkResult]:
    service.require_active(session_id)
    result = _found(research.pharmacy_network, service.content.catalog, plan_id, pharmacy_id)
    event = service.log(
        session_id,
        EventType.LOOKUP,
        {
            "kind": "pharmacy_network",
            "plan_id": plan_id,
            "pharmacy_id": pharmacy_id,
            "in_network": result.in_network,
            "preferred": result.preferred,
        },
    )
    return Logged(event_seq=event.seq, result=result)


@router.post("/sessions/{session_id}/research/compare")
def research_compare(
    session_id: str, body: CompareIn, service: Service
) -> Logged[research.CompareResult]:
    service.require_active(session_id)
    try:
        result = _found(
            research.compare, service.content.catalog, body.plan_ids, body.drugs, body.pharmacy_id
        )
    except ValueError as exc:
        raise Invalid(str(exc)) from exc
    event = service.log(
        session_id,
        EventType.LOOKUP,
        {
            "kind": "compare",
            "plan_ids": list(body.plan_ids),
            "drug_ids": [d.drug_id for d in body.drugs],
            "pharmacy_id": body.pharmacy_id,
            "lines": [
                {
                    "plan_id": row.plan.plan_id,
                    "drug_id": line.drug.drug_id,
                    "covered": line.covered,
                    "prior_auth": line.prior_auth,
                    "step_therapy": line.step_therapy,
                    "quantity_limit": line.quantity_limit,
                }
                for row in result.plans
                for line in row.lines
            ],
            "pharmacy": [
                {
                    "plan_id": row.plan.plan_id,
                    "pharmacy_id": body.pharmacy_id,
                    "in_network": row.pharmacy_in_network,
                    "preferred": row.pharmacy_preferred,
                }
                for row in result.plans
            ],
        },
    )
    return Logged(event_seq=event.seq, result=result)


@router.get("/sessions/{session_id}/documents")
def documents(
    session_id: str, service: Service, plan_id: str | None = None
) -> list[research.DocumentSummary]:
    service.require_active(session_id)
    return research.list_documents(service.content.catalog, plan_id)


@router.get("/sessions/{session_id}/documents/{document_id}")
def open_document(
    session_id: str, document_id: str, service: Service
) -> Logged[research.DocumentView]:
    service.require_active(session_id)
    view = _found(research.render_document, service.content.catalog, document_id)
    event = service.log(
        session_id,
        EventType.DOCUMENT_OPENED,
        {
            "document_id": view.document_id,
            "kind": view.kind.value,
            "plan_id": view.plan_id,
            "plan_year": view.plan_year,
        },
    )
    return Logged(event_seq=event.seq, result=view)


# Records


@router.get("/sessions/{session_id}/record")
def get_record(session_id: str, service: Service) -> CustomerRecord:
    return service.state(session_id).record


@router.put("/sessions/{session_id}/record/{section}")
def save_record(
    session_id: str, section: str, service: Service, data: Annotated[dict[str, Any], Body()]
) -> CustomerRecord:
    service.save_section(session_id, section, data)
    return service.state(session_id).record


@router.post("/sessions/{session_id}/scope", status_code=201)
def record_scope(session_id: str, body: ScopeIn, service: Service) -> Event:
    return service.record_scope(session_id, body)


@router.post("/sessions/{session_id}/evidence", status_code=201)
def pin_evidence(session_id: str, body: EvidenceIn, service: Service) -> Event:
    return service.pin_evidence(session_id, body)


@router.get("/sessions/{session_id}/evidence")
def list_evidence(session_id: str, service: Service) -> list[Event]:
    return [e for e in service.events(session_id) if e.type is EventType.EVIDENCE_PINNED]


@router.post("/sessions/{session_id}/followups", status_code=201)
def create_followup(session_id: str, body: FollowupIn, service: Service) -> Event:
    return service.create_followup(session_id, body)


# Applications


def _app_view(service: SessionService, app: Application) -> ApplicationView:
    return ApplicationView(application=app, warnings=service.application_warnings(app))


@router.get("/sessions/{session_id}/applications")
def list_applications(session_id: str, service: Service) -> list[ApplicationView]:
    return [_app_view(service, a) for a in service.state(session_id).applications.values()]


@router.post("/sessions/{session_id}/applications", status_code=201)
def create_application(
    session_id: str, body: ApplicationFields, service: Service
) -> ApplicationView:
    return _app_view(service, service.create_application(session_id, body))


@router.put("/sessions/{session_id}/applications/{app_id}")
def update_application(
    session_id: str, app_id: str, body: ApplicationFields, service: Service
) -> ApplicationView:
    return _app_view(service, service.update_application(session_id, app_id, body))


@router.post("/sessions/{session_id}/applications/{app_id}/pecl")
def mark_pecl(session_id: str, app_id: str, body: PeclMarkIn, service: Service) -> ApplicationView:
    return _app_view(service, service.mark_pecl(session_id, app_id, body))


@router.post("/sessions/{session_id}/applications/{app_id}/consent")
def mark_consent(
    session_id: str, app_id: str, body: ConsentIn, service: Service
) -> ApplicationView:
    return _app_view(service, service.mark_consent(session_id, app_id, body))


@router.post("/sessions/{session_id}/applications/{app_id}/submit")
def submit_application(
    session_id: str, app_id: str, body: SubmitIn, service: Service
) -> ApplicationView:
    return _app_view(service, service.submit_application(session_id, app_id, body))


@router.post("/sessions/{session_id}/applications/{app_id}/withdraw")
def withdraw_application(session_id: str, app_id: str, service: Service) -> ApplicationView:
    return _app_view(service, service.withdraw_application(session_id, app_id))


# Review (locked until the call ends)


@router.get("/sessions/{session_id}/answer-key")
def answer_key(session_id: str, service: Service) -> AnswerKey:
    row = service.require_ended(session_id)
    content = service.pinned_content(row)
    catalog = content.catalog
    return AnswerKey(
        scenario=content.scenario(row.scenario_id),
        document_defects={
            d.document_id: d.seeded_defect for d in catalog.documents if d.seeded_defect
        },
    )


@router.get("/sessions/{session_id}/findings")
def findings(session_id: str, service: Service) -> Findings:
    row = service.require_ended(session_id)
    content = service.pinned_content(row)
    results = run_checks(
        content.scenario(row.scenario_id),
        content.catalog,
        content.frame,
        service.store.events(session_id),
        service.store.utterances(session_id),
    )
    return Findings(
        session_id=session_id,
        scenario_version=row.scenario_version,
        catalog_version=row.catalog_version,
        ruleset_version=row.ruleset_version,
        results=results,
    )
