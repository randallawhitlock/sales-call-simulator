"""Session lifecycle and learner actions. Every action validates, then appends one event.

The API layer stays thin: it calls these methods and maps ``SessionError`` subclasses to HTTP
status codes.
"""

import random
import secrets
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from app.actor.actors import Actor, ActorError, ScriptedActor, Turn
from app.actor.prompt import PROMPT_VERSION, CustomerPrompt, build_customer_prompt
from app.content.catalog import DocumentKind
from app.content.loader import ContentBundle
from app.engine.events import (
    Channel,
    ClientUttId,
    Event,
    EventType,
    Speaker,
    Utterance,
    UtteranceIn,
)
from app.engine.records import (
    SECTION_MODELS,
    Application,
    ApplicationFields,
    ConsentIn,
    EvidenceIn,
    FollowupIn,
    PeclMarkIn,
    ScopeIn,
    SessionState,
    SubmitIn,
)
from app.engine.store import SessionRow, SessionStatus, Store

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


class SessionError(Exception):
    status_code = 400


class NotFound(SessionError):
    status_code = 404


class Conflict(SessionError):
    status_code = 409


class Locked(SessionError):
    """Answer key and findings stay locked until the call ends."""

    status_code = 403


class Invalid(SessionError):
    status_code = 422


class ActorUnavailable(SessionError):
    """The customer model didn't answer. The learner's line is saved; retry the same turn."""

    status_code = 502


class TurnIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_utt_id: ClientUttId
    text: str = Field(min_length=1, max_length=4000)


class TurnOut(BaseModel):
    learner: Utterance
    customer: Utterance | None
    """None when the turn is not the latest learner line, so no stale reply is made."""


EVIDENCE_SOURCES = {EventType.LOOKUP, EventType.DOCUMENT_OPENED}


class SessionService:
    def __init__(
        self,
        store: Store,
        content: ContentBundle,
        clock: Clock = utc_now,
        actor: Actor | None = None,
    ) -> None:
        self.store = store
        self.content = content
        self.clock = clock
        self.actor: Actor = actor or ScriptedActor()
        self._turn_locks: dict[str, threading.Lock] = {}
        self._turn_locks_guard = threading.Lock()

    @contextmanager
    def turn_lock(self, session_id: str) -> Iterator[None]:
        """One customer turn at a time per session. Hold and end take the same lock, so they
        wait for a reply in flight instead of racing it: a reply never lands after the
        customer was put on hold or the call ended. In-process only, which fits the
        single-process SQLite MVP."""
        with self._turn_locks_guard:
            lock = self._turn_locks.setdefault(session_id, threading.Lock())
        with lock:
            yield

    # Lifecycle

    def create(self, scenario_id: str, seed: int | None = None) -> SessionRow:
        scenario = self.content.scenarios.get(scenario_id)
        if scenario is None:
            raise NotFound(f"unknown scenario {scenario_id}")
        seed = secrets.randbelow(2**31) if seed is None else seed
        rolls = random.Random(seed)
        row = SessionRow(
            session_id=f"s-{secrets.token_hex(8)}",
            scenario_id=scenario.scenario_id,
            scenario_version=scenario.version,
            catalog_version=self.content.catalog.catalog_version,
            ruleset_version=self.content.ruleset.ruleset_version,
            rubric_id=self.content.rubric.rubric_id,
            rubric_version=self.content.rubric.version,
            frame_id=self.content.frame.frame_id,
            prompt_version=PROMPT_VERSION,
            actor=self.actor.name,
            seed=seed,
            mood=rolls.choice(scenario.persona.moods),
            voice=rolls.choice(scenario.persona.voices),
            status=SessionStatus.CREATED,
            created_at=self.clock(),
        )
        self.store.put_session(row)
        return row

    def get(self, session_id: str) -> SessionRow:
        row = self.store.get_session(session_id)
        if row is None:
            raise NotFound(f"unknown session {session_id}")
        return row

    def start(self, session_id: str) -> SessionRow:
        row = self.get(session_id)
        if row.status is not SessionStatus.CREATED:
            raise Conflict(f"session is {row.status.value}")
        now = self.clock()
        row = row.model_copy(update={"status": SessionStatus.ACTIVE, "started_at": now})
        self.store.put_session(row)
        self.store.append_event(session_id, EventType.CALL_STARTED, at=now, t_ms=0)
        return row

    def end(self, session_id: str, reason: str = "learner_ended") -> SessionRow:
        with self.turn_lock(session_id):
            row = self.require_active(session_id)
            now = self.clock()
            self.store.append_event(
                session_id,
                EventType.CALL_ENDED,
                at=now,
                t_ms=self._t_ms(row, now),
                payload={"reason": reason},
            )
            row = row.model_copy(update={"status": SessionStatus.ENDED, "ended_at": now})
            self.store.put_session(row)
            return row

    def require_active(self, session_id: str) -> SessionRow:
        row = self.get(session_id)
        if row.status is not SessionStatus.ACTIVE:
            raise Conflict(f"the call is not active (session is {row.status.value})")
        return row

    def require_ended(self, session_id: str) -> SessionRow:
        row = self.get(session_id)
        if row.status is not SessionStatus.ENDED:
            raise Locked("available after the call ends")
        return row

    def pinned_content(self, row: SessionRow) -> ContentBundle:
        """The content bundle the session was pinned to at creation. Grading against any other
        version would judge the call on facts the learner never saw while reporting the
        session's versions, so a mismatch refuses instead."""
        c = self.content
        scenario = c.scenarios.get(row.scenario_id)
        pinned = {
            "scenario": f"{row.scenario_id} v{row.scenario_version}",
            "catalog": row.catalog_version,
            "ruleset": row.ruleset_version,
            "rubric": f"{row.rubric_id} v{row.rubric_version}",
            "frame": row.frame_id,
        }
        loaded = {
            "scenario": f"{row.scenario_id} v{scenario.version}" if scenario else "missing",
            "catalog": c.catalog.catalog_version,
            "ruleset": c.ruleset.ruleset_version,
            "rubric": f"{c.rubric.rubric_id} v{c.rubric.version}",
            "frame": c.frame.frame_id,
        }
        diff = [f"{k} {pinned[k]} (loaded: {loaded[k]})" for k in pinned if pinned[k] != loaded[k]]
        if diff:
            raise Conflict(
                "this session was pinned to content that is not loaded: " + "; ".join(diff)
            )
        return c

    def _t_ms(self, row: SessionRow, now: datetime) -> int:
        if row.started_at is None:
            return 0
        return max(0, int((now - row.started_at).total_seconds() * 1000))

    # Log

    def log(
        self,
        session_id: str,
        type: EventType,
        payload: dict[str, JsonValue] | None = None,
    ) -> Event:
        row = self.require_active(session_id)
        now = self.clock()
        return self.store.append_event(
            session_id, type, at=now, t_ms=self._t_ms(row, now), payload=payload
        )

    def events(self, session_id: str) -> list[Event]:
        self.get(session_id)
        return self.store.events(session_id)

    def state(self, session_id: str) -> SessionState:
        return SessionState.fold(self.events(session_id))

    # Utterances

    def add_utterance(self, session_id: str, body: UtteranceIn) -> Utterance:
        """Idempotent per ``client_utt_id``. Reusing an id for a different line is refused,
        so a client bug can't silently get back someone else's turn."""
        row = self.require_active(session_id)
        now = self.clock()
        utt, new = self.store.append_utterance(
            session_id,
            client_utt_id=body.client_utt_id,
            at=now,
            build=lambda utt_id: Utterance.from_input(
                body, utt_id=utt_id, session_id=session_id, t_ms=self._t_ms(row, now)
            ),
        )
        if not new and (utt.speaker, utt.channel, utt.text) != (
            body.speaker,
            body.channel,
            body.text,
        ):
            raise Conflict(
                f"client_utt_id {body.client_utt_id} was already used for a different line"
            )
        return utt

    def add_learner_text(self, session_id: str, body: UtteranceIn) -> Utterance:
        """The learner's own turn. Customer turns come from the actor, never from the
        learner's browser."""
        if body.speaker is not Speaker.LEARNER or body.channel is not Channel.TEXT:
            raise Invalid("learners post their own text turns only")
        return self.add_utterance(session_id, body)

    def take_turn(self, session_id: str, body: TurnIn) -> TurnOut:
        """The learner says a line and the customer answers. Retrying with the same
        ``client_utt_id`` returns the same pair; if the customer model failed last time, it is
        asked again.

        Turns are serialized per session, and the reply is checked again before it is saved:
        if a newer learner line, a hold or the end of the call arrived while the model was
        thinking, the reply is dropped instead of landing out of order."""
        with self.turn_lock(session_id):
            row = self.require_active(session_id)
            if self.state(session_id).on_hold:
                raise Conflict("the customer is on hold and can't hear you")
            learner = self.add_learner_text(
                session_id,
                UtteranceIn(
                    client_utt_id=body.client_utt_id,
                    speaker=Speaker.LEARNER,
                    channel=Channel.TEXT,
                    text=body.text,
                ),
            )
            reply_key = f"reply-{learner.utt_id}"
            existing = self.store.utterance_by_client_id(session_id, reply_key)
            if existing is not None:
                return TurnOut(learner=learner, customer=existing)
            transcript = self.store.utterances(session_id)
            if not self._still_current(session_id, learner):
                return TurnOut(learner=learner, customer=None)
            prompt = self.customer_prompt(row)
            history = [Turn(u.speaker, u.heard_text) for u in transcript if u.delivered]
            try:
                text = self.actor.reply(prompt, history, row.seed)
            except ActorError as exc:
                raise ActorUnavailable(str(exc)) from exc
            with self.store.transaction():
                if not self._still_current(session_id, learner):
                    return TurnOut(learner=learner, customer=None)
                customer = self.add_utterance(
                    session_id,
                    UtteranceIn(
                        client_utt_id=reply_key,
                        speaker=Speaker.CUSTOMER,
                        channel=Channel.TEXT,
                        text=text,
                    ),
                )
            return TurnOut(learner=learner, customer=customer)

    def _still_current(self, session_id: str, learner: Utterance) -> bool:
        """Whether a reply to ``learner`` may still be said: the call is active, the customer
        is not on hold, and no learner line came after this one."""
        row = self.get(session_id)
        if row.status is not SessionStatus.ACTIVE or self.state(session_id).on_hold:
            return False
        latest = next(
            (
                u
                for u in reversed(self.store.utterances(session_id))
                if u.speaker is Speaker.LEARNER
            ),
            None,
        )
        return latest is not None and latest.utt_id == learner.utt_id

    def customer_prompt(self, row: SessionRow) -> CustomerPrompt:
        """The prompt the session's customer plays from. Refuses if the server's prompt
        template or model differs from what the session pinned."""
        pinned = (row.prompt_version, row.actor)
        running = (PROMPT_VERSION, self.actor.name)
        if pinned != running:
            raise Conflict(
                f"this session's customer is {row.actor} on {row.prompt_version}; the server "
                f"runs {self.actor.name} on {PROMPT_VERSION}"
            )
        scenario = self.content.scenario(row.scenario_id)
        return build_customer_prompt(scenario, self.content.frame, row.mood)

    def transcript(self, session_id: str) -> list[Utterance]:
        self.get(session_id)
        return self.store.utterances(session_id)

    # Records

    def save_section(self, session_id: str, section: str, data: dict[str, Any]) -> Event:
        model = SECTION_MODELS.get(section)
        if model is None:
            raise NotFound(f"unknown record section {section}")
        try:
            parsed = model.model_validate(data)
        except ValidationError as exc:
            raise Invalid(str(exc)) from exc
        self._check_refs(session_id, parsed)
        return self.log(
            session_id,
            EventType.RECORD_SAVED,
            {"section": section, "data": parsed.model_dump(mode="json")},
        )

    def record_scope(self, session_id: str, body: ScopeIn) -> Event:
        self._check_refs(session_id, body)
        return self.log(session_id, EventType.SCOPE_RECORDED, body.model_dump(mode="json"))

    def pin_evidence(self, session_id: str, body: EvidenceIn) -> Event:
        source = self._event(session_id, body.source_seq)
        if source.type not in EVIDENCE_SOURCES:
            raise Invalid("evidence must come from a lookup or an opened document")
        return self.log(
            session_id,
            EventType.EVIDENCE_PINNED,
            {"source_seq": source.seq, "source": source.payload, "note": body.note},
        )

    def create_followup(self, session_id: str, body: FollowupIn) -> Event:
        return self.log(session_id, EventType.FOLLOWUP_CREATED, body.model_dump(mode="json"))

    def hold(self, session_id: str, on: bool) -> Event:
        with self.turn_lock(session_id):
            state = self.state(session_id)
            if state.on_hold == on:
                raise Conflict("already on hold" if on else "not on hold")
            return self.log(session_id, EventType.HOLD_STARTED if on else EventType.HOLD_ENDED)

    # Applications

    def create_application(self, session_id: str, fields: ApplicationFields) -> Application:
        self._check_application(fields)
        with self.store.transaction():
            app_id = f"app-{len(self.state(session_id).applications) + 1}"
            self.log(
                session_id,
                EventType.APPLICATION_CREATED,
                {"application_id": app_id, "fields": fields.model_dump(mode="json")},
            )
        return self.application(session_id, app_id)

    def update_application(
        self, session_id: str, app_id: str, fields: ApplicationFields
    ) -> Application:
        app = self._draft(session_id, app_id)
        if fields != app.fields and (app.pecl_marks or app.consent_seq is not None):
            # The checklist and the customer's yes were for the application as it stood.
            raise Conflict("start a new application to change it after checklist review")
        self._check_application(fields)
        self.log(
            session_id,
            EventType.APPLICATION_UPDATED,
            {"application_id": app_id, "fields": fields.model_dump(mode="json")},
        )
        return self.application(session_id, app_id)

    def mark_pecl(self, session_id: str, app_id: str, body: PeclMarkIn) -> Application:
        app = self._draft(session_id, app_id)
        pecl = self.content.catalog.plan_document(
            app.fields.plan_id, DocumentKind.PRE_ENROLLMENT_CHECKLIST
        )
        if body.item_index >= len(pecl.checklist_items):
            raise Invalid(f"the checklist has {len(pecl.checklist_items)} items")
        self.log(
            session_id,
            EventType.PECL_ITEM_MARKED,
            {
                "application_id": app_id,
                "document_id": pecl.document_id,
                "item_index": body.item_index,
            },
        )
        return self.application(session_id, app_id)

    def mark_consent(self, session_id: str, app_id: str, body: ConsentIn) -> Application:
        self._draft(session_id, app_id)
        utt = next((u for u in self.store.utterances(session_id) if u.utt_id == body.utt_id), None)
        if utt is None:
            raise Invalid(f"no utterance {body.utt_id} in this call")
        # The mark is the learner's annotation. Whether the line really is a clear, timely yes
        # is graded by consent_before_submit; here it only has to be the customer's own heard
        # words.
        if utt.speaker is not Speaker.CUSTOMER:
            raise Invalid(f"{body.utt_id} is not the customer's; consent must cite the customer")
        if not utt.delivered:
            raise Invalid(f"{body.utt_id} was cut off before the customer said anything")
        self.log(
            session_id,
            EventType.CONSENT_MARKED,
            {"application_id": app_id, "utt_id": body.utt_id},
        )
        return self.application(session_id, app_id)

    def submit_application(self, session_id: str, app_id: str, body: SubmitIn) -> Application:
        """Idempotent: a retried submit with the same key returns the submitted application
        without a second event."""
        with self.store.transaction():
            app = self.application(session_id, app_id)
            if app.status == "submitted":
                if app.idempotency_key == body.idempotency_key:
                    return app
                raise Conflict("application already submitted")
            if app.status != "draft":
                raise Conflict(f"application is {app.status}")
            self.log(
                session_id,
                EventType.APPLICATION_SUBMITTED,
                {
                    "application_id": app_id,
                    "idempotency_key": body.idempotency_key,
                    "fields": app.fields.model_dump(mode="json"),
                },
            )
        return self.application(session_id, app_id)

    def withdraw_application(self, session_id: str, app_id: str) -> Application:
        app = self.application(session_id, app_id)
        if app.status == "withdrawn":
            raise Conflict("application already withdrawn")
        self.log(session_id, EventType.APPLICATION_WITHDRAWN, {"application_id": app_id})
        return self.application(session_id, app_id)

    def application(self, session_id: str, app_id: str) -> Application:
        app = self.state(session_id).applications.get(app_id)
        if app is None:
            raise NotFound(f"unknown application {app_id}")
        return app

    def application_warnings(self, app: Application) -> list[str]:
        """Workflow prompts, kept apart from grading: fixing one doesn't erase that it
        happened, and ignoring one doesn't block submission."""
        catalog = self.content.catalog
        plan = catalog.plan(app.fields.plan_id)
        warnings: list[str] = []
        if plan.pcp_required and app.fields.pcp_practice_id is None:
            warnings.append(f"{plan.name} requires a primary care doctor.")
        if app.fields.pcp_practice_id is not None:
            on = self.content.frame.coverage_effective
            if catalog.participation_on(app.fields.pcp_practice_id, plan.plan_id, on) is None:
                warnings.append("The chosen primary care doctor is not in this plan's network.")
            if not plan.pcp_required:
                warnings.append(
                    f"{plan.name} does not require a primary care doctor. Collecting one is "
                    "organization policy, not a plan rule."
                )
        if app.fields.effective_date != self.content.frame.coverage_effective:
            warnings.append(
                f"Annual Election Period changes take effect "
                f"{self.content.frame.coverage_effective.isoformat()}."
            )
        return warnings

    # Validation helpers

    def _draft(self, session_id: str, app_id: str) -> Application:
        app = self.application(session_id, app_id)
        if app.status != "draft":
            raise Conflict(f"application is {app.status}")
        return app

    def _event(self, session_id: str, seq: int) -> Event:
        for e in self.store.events(session_id):
            if e.seq == seq:
                return e
        raise Invalid(f"no event {seq} in this call")

    def _check_application(self, fields: ApplicationFields) -> None:
        catalog = self.content.catalog
        try:
            catalog.plan(fields.plan_id)
            if fields.pcp_practice_id is not None:
                catalog.practice(fields.pcp_practice_id)
        except KeyError as exc:
            raise Invalid(str(exc)) from exc

    def _check_refs(self, session_id: str, model: BaseModel) -> None:
        """Catalog ids and utterance ids in learner input must exist."""
        catalog = self.content.catalog
        lookups: dict[str, Callable[[str], object]] = {
            "practice_id": catalog.practice,
            "drug_id": catalog.drug,
            "pharmacy_id": catalog.pharmacy,
            "plan_id": catalog.plan,
            "current_plan_id": catalog.plan,
        }
        utt_ids: set[str] | None = None
        for key, value in _walk(model.model_dump()):
            if value is None:
                continue
            if key in lookups:
                try:
                    lookups[key](str(value))
                except KeyError as exc:
                    raise Invalid(str(exc)) from exc
            elif key == "source_utt_id":
                if utt_ids is None:
                    utt_ids = {u.utt_id for u in self.store.utterances(session_id)}
                if value not in utt_ids:
                    raise Invalid(f"no utterance {value} in this call")


def _walk(data: object, key: str = "") -> list[tuple[str, object]]:
    if isinstance(data, dict):
        return [pair for k, v in data.items() for pair in _walk(v, str(k))]
    if isinstance(data, list | tuple):
        return [pair for v in data for pair in _walk(v, key)]
    return [(key, data)]
