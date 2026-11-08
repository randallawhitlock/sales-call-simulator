"""Scenario models: the call frame, the customer, what is true, and what the customer knows.

Four views of truth are kept apart. ``truth``, ``outcomes`` and ``traps`` are scenario truth and
never reach the learner. ``knowledge`` is what the customer can say, under reveal rules.
``crm_seed`` is the only customer data the learner sees before the customer says it.
"""

from datetime import date
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from app.content.common import (
    CheckId,
    ContentModel,
    FictionalPhone,
    FictionalZip,
    Id,
    OutcomeKind,
    PlanId,
    RuleId,
    require_unique,
)


class ElectionPeriod(StrEnum):
    AEP = "aep"
    OEP = "oep"
    IEP = "iep"
    SEP = "sep"


class CallDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class CallFrame(ContentModel):
    frame_id: Id
    call_date: date
    plan_year: int
    election_period: ElectionPeriod
    direction: CallDirection
    county_id: Id
    agency_name: str
    learner_role: str
    carriers_represented: int = Field(ge=1)
    """Feeds the "[#] organizations" slot of the TPMO disclaimer."""
    products_offered: int = Field(ge=1)
    """Feeds the "[#] products" slot of the TPMO disclaimer."""

    @property
    def coverage_effective(self) -> date:
        return date(self.plan_year, 1, 1)

    @model_validator(mode="after")
    def _aep_window(self) -> Self:
        if self.election_period is ElectionPeriod.AEP:
            start = date(self.plan_year - 1, 10, 15)
            end = date(self.plan_year - 1, 12, 7)
            if not start <= self.call_date <= end:
                raise ValueError(
                    f"{self.frame_id}: AEP call date must fall between {start} and {end}"
                )
        return self


class Persona(ContentModel):
    first_name: str
    last_name: str
    age: int = Field(ge=18, le=110)
    speaking_style: str
    moods: tuple[str, ...] = Field(min_length=1)
    voices: tuple[str, ...] = Field(min_length=1)


class CrmSeed(ContentModel):
    customer_name: str
    phone: FictionalPhone
    zip: FictionalZip
    lead_source: str
    notes: str | None = None


class DrugNeed(ContentModel):
    drug_id: Id
    days_supply: Literal[30, 90] = 30


class OtherCoverageKind(StrEnum):
    EMPLOYER_RETIREE = "employer_retiree"
    MEDIGAP = "medigap"
    MEDICAID = "medicaid"
    VA = "va"
    TRICARE = "tricare"


class OtherCoverage(ContentModel):
    kind: OtherCoverageKind
    includes_drug_coverage: bool
    may_end_if_ma_enrollment: bool
    covers_spouse: bool = False
    administrator: str | None = None


class Truth(ContentModel):
    current_plan_id: PlanId
    must_keep_practices: tuple[Id, ...] = ()
    drugs: tuple[DrugNeed, ...] = ()
    pharmacy_id: Id
    other_coverage: OtherCoverage | None = None
    ad_claim: str | None = None


class Reveal(StrEnum):
    VOLUNTEER = "volunteer"
    WHEN_ASKED = "when_asked"
    ONLY_IF_SPECIFIC = "only_if_specific"


class KnowledgeItem(ContentModel):
    item_id: Id
    lay_text: str
    reveal: Reveal
    topics: tuple[str, ...] = Field(min_length=1)
    misconception: bool = False


class OutcomeVerdict(StrEnum):
    ACCEPTABLE = "acceptable"
    EXCLUDED = "excluded"


class Outcome(ContentModel):
    outcome_id: Id
    kind: OutcomeKind
    verdict: OutcomeVerdict
    plan_ids: tuple[PlanId, ...] = ()
    required_checks: tuple[CheckId, ...] = ()
    rationale: str

    @model_validator(mode="after")
    def _plans_only_on_enroll(self) -> Self:
        if (self.kind is OutcomeKind.ENROLL) != bool(self.plan_ids):
            raise ValueError(f"{self.outcome_id}: plan_ids are set exactly on enroll outcomes")
        return self


class Trap(ContentModel):
    trap_id: Id
    description: str
    check_ids: tuple[CheckId, ...] = ()
    rationale: str


class Scenario(ContentModel):
    scenario_id: Id
    version: int = Field(ge=1)
    title: str
    frame_id: Id
    catalog_version: str
    persona: Persona
    crm_seed: CrmSeed
    truth: Truth
    knowledge: tuple[KnowledgeItem, ...] = Field(min_length=1)
    outcomes: tuple[Outcome, ...] = Field(min_length=1)
    traps: tuple[Trap, ...] = ()
    rule_ids: tuple[RuleId, ...] = Field(min_length=1)
    not_applicable: dict[str, str] = {}
    """Rubric criterion id -> reason. N/A criteria leave the denominator."""
    leak_canaries: tuple[str, ...] = Field(min_length=1)
    """Strings planted in truth-only text. The leak sweep fails if any reaches the learner."""

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        require_unique("knowledge ids", (k.item_id for k in self.knowledge))
        require_unique("outcome ids", (o.outcome_id for o in self.outcomes))
        require_unique("trap ids", (t.trap_id for t in self.traps))
        if not any(o.verdict is OutcomeVerdict.ACCEPTABLE for o in self.outcomes):
            raise ValueError(f"{self.scenario_id}: needs at least one acceptable outcome")
        truth_text = " ".join(
            [o.rationale for o in self.outcomes]
            + [f"{t.description} {t.rationale}" for t in self.traps]
        )
        unplanted = [c for c in self.leak_canaries if c not in truth_text]
        if unplanted:
            raise ValueError(
                f"{self.scenario_id}: canaries not found in truth-only text: {unplanted}"
            )
        customer_text = " ".join(k.lay_text for k in self.knowledge)
        exposed = [c for c in self.leak_canaries if c in customer_text]
        if exposed:
            raise ValueError(f"{self.scenario_id}: canaries in customer knowledge: {exposed}")
        return self
