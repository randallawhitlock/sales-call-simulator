"""What the learner writes down, and the session state folded from the event log.

The customer record keeps the distinctions grading relies on: a drug list that is empty
because the customer takes none versus one nobody asked about, a residence versus a mailing
address, a helper versus an authorized representative. Every section is saved whole as a
``record_saved`` event; the current record is the last save of each section.
"""

from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.content.common import FictionalZip, Id, OutcomeKind, PlanId
from app.content.scenario import OtherCoverageKind
from app.engine.events import Event, EventType, UttId


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# Customer record sections


class DoctorEntry(_In):
    name: str = Field(min_length=1)
    role: Literal["pcp", "specialist", "other"]
    practice_id: Id | None = None
    """Set once the learner matched the doctor to a practice (provider at an office)."""
    must_keep: bool = False
    source_utt_id: UttId | None = None


class DoctorsSection(_In):
    doctors: tuple[DoctorEntry, ...] = ()


class DrugEntry(_In):
    drug_id: Id | None = None
    name_reported: str | None = None
    days_supply: Literal[30, 90] = 30
    source_utt_id: UttId | None = None

    @model_validator(mode="after")
    def _identified(self) -> Self:
        if self.drug_id is None and not self.name_reported:
            raise ValueError("a drug needs a catalog drug_id or the name the customer gave")
        return self


class DrugsSection(_In):
    status: Literal["listed", "none", "unknown"]
    """``none`` means the customer said they take nothing; ``unknown`` means not yet asked."""
    drugs: tuple[DrugEntry, ...] = ()

    @model_validator(mode="after")
    def _status(self) -> Self:
        if (self.status == "listed") != bool(self.drugs):
            raise ValueError("drugs are listed exactly when status is 'listed'")
        return self


class PharmacySection(_In):
    pharmacy_id: Id | None = None
    name_reported: str | None = None
    source_utt_id: UttId | None = None


class OtherCoverageEntry(_In):
    kind: OtherCoverageKind
    includes_drug_coverage: bool | None = None
    covers_spouse: bool | None = None
    administrator: str | None = None


class CoverageSection(_In):
    current_plan_id: PlanId | None = None
    """None with ``original_medicare`` true means Original Medicare."""
    original_medicare: bool | None = None
    medicare_parts: tuple[Literal["a", "b"], ...] = ()
    medicaid: Literal["yes", "no", "unknown"] = "unknown"
    other_coverage: tuple[OtherCoverageEntry, ...] = ()
    source_utt_id: UttId | None = None


class ContactSection(_In):
    residence_zip: FictionalZip | None = None
    mailing_same_as_residence: bool = True
    mailing_zip: FictionalZip | None = None
    helper_name: str | None = None
    """Someone helping on the call. Not a legal representative."""
    authorized_representative: str | None = None
    source_utt_id: UttId | None = None


class NotesSection(_In):
    reason_for_inquiry: str = ""
    priorities: str = ""
    findings: str = ""
    options_discussed: str = ""
    rationale: str = ""
    next_steps: str = ""


class DispositionSection(_In):
    outcome: OutcomeKind
    plan_id: PlanId | None = None
    rationale: str = ""

    @model_validator(mode="after")
    def _plan(self) -> Self:
        if (self.outcome is OutcomeKind.ENROLL) != (self.plan_id is not None):
            raise ValueError("plan_id is set exactly when the outcome is enroll")
        return self


SECTION_MODELS: dict[str, type[_In]] = {
    "doctors": DoctorsSection,
    "drugs": DrugsSection,
    "pharmacy": PharmacySection,
    "coverage": CoverageSection,
    "contact": ContactSection,
    "notes": NotesSection,
    "disposition": DispositionSection,
}


class CustomerRecord(BaseModel):
    doctors: DoctorsSection | None = None
    drugs: DrugsSection | None = None
    pharmacy: PharmacySection | None = None
    coverage: CoverageSection | None = None
    contact: ContactSection | None = None
    notes: NotesSection | None = None
    disposition: DispositionSection | None = None
    saved_seq: dict[str, int] = {}
    """Section -> seq of the event that saved its current value."""


# Other learner inputs


class ScopeIn(_In):
    products: tuple[Literal["ma", "pdp", "medigap", "dental_vision_hearing"], ...] = Field(
        min_length=1
    )
    method: Literal["verbal", "written"] = "verbal"
    source_utt_id: UttId | None = None


class EvidenceIn(_In):
    source_seq: int = Field(ge=1)
    """The lookup or document-open event this evidence comes from. The server copies plan,
    year and location from that event, so evidence can't claim a context it didn't show."""
    note: str = ""


class FollowupIn(_In):
    due_date: date
    method: Literal["phone", "mail", "email"] = "phone"
    reason: str = Field(min_length=1)


class ApplicationFields(_In):
    plan_id: PlanId
    applicant_name: str = Field(min_length=1)
    residence_zip: FictionalZip
    mailing_zip: FictionalZip | None = None
    pcp_practice_id: Id | None = None
    effective_date: date


class PeclMarkIn(_In):
    item_index: int = Field(ge=0)


class ConsentIn(_In):
    utt_id: UttId


class SubmitIn(_In):
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")


# Folded state


class Application(BaseModel):
    application_id: str
    fields: ApplicationFields
    status: Literal["draft", "submitted", "withdrawn"] = "draft"
    created_seq: int
    updated_seq: int | None = None
    """Seq of the last update that changed a field. Checklist marks and consent given before
    it no longer describe the application."""
    submitted_seq: int | None = None
    """Kept after a withdrawal: the submission still happened."""
    idempotency_key: str | None = None
    pecl_marks: dict[int, int] = {}
    """Checklist item index -> seq of the mark."""
    consent_utt_id: str | None = None
    consent_seq: int | None = None


class SessionState(BaseModel):
    record: CustomerRecord = CustomerRecord()
    applications: dict[str, Application] = {}
    call_started_seq: int | None = None
    call_ended_seq: int | None = None
    on_hold: bool = False

    @classmethod
    def fold(cls, events: list[Event]) -> Self:
        state = cls()
        record: dict[str, object] = {}
        saved: dict[str, int] = {}
        for e in events:
            p = e.payload
            match e.type:
                case EventType.CALL_STARTED:
                    state.call_started_seq = e.seq
                case EventType.CALL_ENDED:
                    state.call_ended_seq = e.seq
                case EventType.HOLD_STARTED:
                    state.on_hold = True
                case EventType.HOLD_ENDED:
                    state.on_hold = False
                case EventType.RECORD_SAVED:
                    section = str(p["section"])
                    record[section] = SECTION_MODELS[section].model_validate(p["data"])
                    saved[section] = e.seq
                case EventType.APPLICATION_CREATED:
                    app_id = str(p["application_id"])
                    state.applications[app_id] = Application(
                        application_id=app_id,
                        fields=ApplicationFields.model_validate(p["fields"]),
                        created_seq=e.seq,
                    )
                case EventType.APPLICATION_UPDATED:
                    app = state.applications[str(p["application_id"])]
                    fields = ApplicationFields.model_validate(p["fields"])
                    if fields != app.fields:
                        app.fields = fields
                        app.updated_seq = e.seq
                case EventType.PECL_ITEM_MARKED:
                    app = state.applications[str(p["application_id"])]
                    app.pecl_marks.setdefault(_int(p["item_index"]), e.seq)
                case EventType.CONSENT_MARKED:
                    app = state.applications[str(p["application_id"])]
                    app.consent_utt_id = str(p["utt_id"])
                    app.consent_seq = e.seq
                case EventType.APPLICATION_SUBMITTED:
                    app = state.applications[str(p["application_id"])]
                    app.status = "submitted"
                    app.submitted_seq = e.seq
                    app.idempotency_key = str(p["idempotency_key"])
                case EventType.APPLICATION_WITHDRAWN:
                    state.applications[str(p["application_id"])].status = "withdrawn"
                case _:
                    pass
        state.record = CustomerRecord.model_validate({**record, "saved_seq": saved})
        return state

    def submitted_application(self) -> Application | None:
        """The enrollment that stands at the end: the latest submission not withdrawn."""
        submitted = [a for a in self.applications.values() if a.status == "submitted"]
        return max(submitted, key=lambda a: a.submitted_seq or 0) if submitted else None

    def submissions(self) -> list[Application]:
        """Every application that was submitted, withdrawn or not, in submission order.
        Compliance is audited on each one: a later clean submission doesn't erase an earlier
        faulty one."""
        done = [a for a in self.applications.values() if a.submitted_seq is not None]
        return sorted(done, key=lambda a: a.submitted_seq or 0)


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"expected an integer, got {value!r}")
    return value
