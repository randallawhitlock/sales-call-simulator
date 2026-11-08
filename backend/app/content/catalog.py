"""Synthetic plan catalog: carriers, plans, providers, drugs, pharmacies and documents.

Everything here is fictional. Provider participation is keyed by practice (provider at a
location) x network x effective period, because "in network" without a location and a date is
the most common way agents get it wrong.
"""

from datetime import date
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from app.content.common import (
    ContentModel,
    FictionalPhone,
    FictionalZip,
    Id,
    Money,
    PlanId,
    require_known,
    require_unique,
)


class County(ContentModel):
    county_id: Id
    name: str
    state: str
    zips: tuple[FictionalZip, ...] = Field(min_length=1)


class Carrier(ContentModel):
    carrier_id: Id
    name: str
    customer_service_phone: FictionalPhone | None = None


class PlanType(StrEnum):
    HMO = "hmo"
    PPO = "ppo"


class BenefitCategory(StrEnum):
    PCP_VISIT = "pcp_visit"
    SPECIALIST_VISIT = "specialist_visit"
    INPATIENT = "inpatient"
    OUTPATIENT_SURGERY = "outpatient_surgery"
    EMERGENCY = "emergency"
    URGENT_CARE = "urgent_care"
    LAB = "lab"
    IMAGING = "imaging"
    DME = "dme"
    THERAPY = "therapy"
    TELEHEALTH = "telehealth"
    DENTAL = "dental"
    VISION = "vision"
    HEARING = "hearing"
    OTHER = "other"


class Benefit(ContentModel):
    category: BenefitCategory
    label: str
    in_network: str
    out_of_network: str | None = None
    prior_auth: bool = False
    referral_required: bool = False


class Supplemental(ContentModel):
    supplemental_id: Id
    name: str
    description: str
    amount_text: str | None = None
    conditional: bool
    eligibility_text: str | None = None

    @model_validator(mode="after")
    def _conditional_needs_eligibility(self) -> Self:
        if self.conditional and not self.eligibility_text:
            raise ValueError(f"{self.supplemental_id}: conditional benefits need eligibility_text")
        return self


class Plan(ContentModel):
    plan_id: PlanId
    carrier_id: Id
    name: str
    plan_type: PlanType
    plan_year: int
    county_id: Id
    network_id: Id
    formulary_id: Id
    pharmacy_network_id: Id
    monthly_premium: Money
    medical_deductible: Money
    drug_deductible: Money
    moop_in_network: Money
    moop_combined: Money | None = None
    """In- and out-of-network maximum. PPOs only."""
    pcp_required: bool
    referrals_required: bool
    benefits: tuple[Benefit, ...] = Field(min_length=1)
    supplementals: tuple[Supplemental, ...] = ()

    @model_validator(mode="after")
    def _ppo_moop(self) -> Self:
        if (self.plan_type is PlanType.PPO) != (self.moop_combined is not None):
            raise ValueError(f"{self.plan_id}: moop_combined is set exactly when the plan is a PPO")
        if self.moop_combined is not None and self.moop_combined < self.moop_in_network:
            raise ValueError(f"{self.plan_id}: moop_combined is below moop_in_network")
        return self


class Provider(ContentModel):
    provider_id: Id
    name: str
    specialty: str
    credentials: str | None = None


class Location(ContentModel):
    location_id: Id
    name: str
    address: str
    city: str
    zip: FictionalZip
    county_id: Id


class Practice(ContentModel):
    """A provider at one location. Network status attaches here, not to the provider."""

    practice_id: Id
    provider_id: Id
    location_id: Id
    group_name: str | None = None


class NetworkParticipation(ContentModel):
    practice_id: Id
    network_id: Id
    effective_from: date
    effective_to: date | None = None
    accepting_new_patients: bool = True

    @model_validator(mode="after")
    def _dates(self) -> Self:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError(f"{self.practice_id}/{self.network_id}: effective_to before from")
        return self

    def covers(self, on: date) -> bool:
        return self.effective_from <= on and (self.effective_to is None or on <= self.effective_to)


class Drug(ContentModel):
    """Strength is part of identity: atorvastatin 20 mg and 40 mg are different drugs."""

    drug_id: Id
    name: str
    strength: str
    form: str
    generic: bool


class FormularyEntry(ContentModel):
    formulary_id: Id
    drug_id: Id
    tier: int = Field(ge=1, le=6)
    prior_auth: bool = False
    step_therapy: bool = False
    quantity_limit: str | None = None


class Pharmacy(ContentModel):
    pharmacy_id: Id
    name: str
    location_id: Id | None = None
    mail_order: bool = False

    @model_validator(mode="after")
    def _retail_has_location(self) -> Self:
        if not self.mail_order and self.location_id is None:
            raise ValueError(f"{self.pharmacy_id}: retail pharmacies need a location")
        return self


class PharmacyNetworkEntry(ContentModel):
    pharmacy_network_id: Id
    pharmacy_id: Id
    preferred: bool


class PharmacyChannel(StrEnum):
    PREFERRED_RETAIL = "preferred_retail"
    STANDARD_RETAIL = "standard_retail"
    MAIL = "mail"


class DrugCostFixture(ContentModel):
    plan_id: PlanId
    drug_id: Id
    channel: PharmacyChannel
    days_supply: Literal[30, 90]
    copay: Money


class AnnualEstimate(ContentModel):
    estimate_id: Id
    plan_id: PlanId
    scenario_id: Id
    premiums: Money
    drug_costs: Money
    medical_costs: Money
    total: Money
    label: Literal["Training estimate"] = "Training estimate"

    @model_validator(mode="after")
    def _total(self) -> Self:
        if self.premiums + self.drug_costs + self.medical_costs != self.total:
            raise ValueError(f"{self.estimate_id}: total does not equal its parts")
        return self


class DocumentKind(StrEnum):
    SUMMARY_OF_BENEFITS = "summary_of_benefits"
    PRE_ENROLLMENT_CHECKLIST = "pre_enrollment_checklist"
    ANNUAL_NOTICE_OF_CHANGE = "annual_notice_of_change"
    FORMULARY = "formulary"
    PROVIDER_DIRECTORY = "provider_directory"


class Document(ContentModel):
    """Rendered with a "Training document" watermark."""

    document_id: Id
    kind: DocumentKind
    title: str
    plan_id: PlanId | None = None
    plan_year: int
    body_markdown: str
    checklist_items: tuple[str, ...] = ()
    seeded_defect: str | None = None
    """Truth-only note on a deliberate error in the document. Never shown to the learner."""

    @model_validator(mode="after")
    def _checklist(self) -> Self:
        is_pecl = self.kind is DocumentKind.PRE_ENROLLMENT_CHECKLIST
        if is_pecl != bool(self.checklist_items):
            raise ValueError(f"{self.document_id}: checklist_items belong to the PECL only")
        return self


class Catalog(ContentModel):
    catalog_version: str = Field(pattern=r"^\d{4}-v\d+$")
    plan_year: int
    county: County
    carriers: tuple[Carrier, ...] = ()
    plans: tuple[Plan, ...] = ()
    providers: tuple[Provider, ...] = ()
    locations: tuple[Location, ...] = ()
    practices: tuple[Practice, ...] = ()
    participation: tuple[NetworkParticipation, ...] = ()
    drugs: tuple[Drug, ...] = ()
    formulary: tuple[FormularyEntry, ...] = ()
    pharmacies: tuple[Pharmacy, ...] = ()
    pharmacy_networks: tuple[PharmacyNetworkEntry, ...] = ()
    drug_costs: tuple[DrugCostFixture, ...] = ()
    documents: tuple[Document, ...] = ()

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        """Ids are unique and every reference resolves. Deeper cross-surface consistency
        (numbers on cards and documents matching their source records) lives in the content
        validator."""
        require_unique("carrier ids", (c.carrier_id for c in self.carriers))
        require_unique("plan ids", (p.plan_id for p in self.plans))
        require_unique("provider ids", (p.provider_id for p in self.providers))
        require_unique("location ids", (loc.location_id for loc in self.locations))
        require_unique("practice ids", (p.practice_id for p in self.practices))
        require_unique("drug ids", (d.drug_id for d in self.drugs))
        require_unique("pharmacy ids", (p.pharmacy_id for p in self.pharmacies))
        require_unique("document ids", (d.document_id for d in self.documents))
        require_unique(
            "formulary entries",
            (f"{f.formulary_id}/{f.drug_id}" for f in self.formulary),
        )

        carriers = {c.carrier_id for c in self.carriers}
        plans = {p.plan_id for p in self.plans}
        providers = {p.provider_id for p in self.providers}
        locations = {loc.location_id for loc in self.locations}
        practices = {p.practice_id for p in self.practices}
        drugs = {d.drug_id for d in self.drugs}
        pharmacies = {p.pharmacy_id for p in self.pharmacies}
        networks = {p.network_id for p in self.plans}

        require_known("carrier ids on plans", (p.carrier_id for p in self.plans), carriers)
        require_known("county ids", (p.county_id for p in self.plans), {self.county.county_id})
        require_known(
            "county ids on locations",
            (loc.county_id for loc in self.locations),
            {self.county.county_id},
        )
        wrong_year = [p.plan_id for p in self.plans if p.plan_year != self.plan_year]
        if wrong_year:
            raise ValueError(f"plans outside plan year {self.plan_year}: {', '.join(wrong_year)}")
        require_known("provider ids", (p.provider_id for p in self.practices), providers)
        require_known("location ids", (p.location_id for p in self.practices), locations)
        require_known("practice ids", (n.practice_id for n in self.participation), practices)
        require_known("network ids", (n.network_id for n in self.participation), networks)
        require_known("drug ids in formulary", (f.drug_id for f in self.formulary), drugs)
        require_known(
            "formulary ids",
            (f.formulary_id for f in self.formulary),
            {p.formulary_id for p in self.plans},
        )
        require_known(
            "pharmacy locations",
            (p.location_id for p in self.pharmacies if p.location_id is not None),
            locations,
        )
        require_known(
            "pharmacy ids in networks", (e.pharmacy_id for e in self.pharmacy_networks), pharmacies
        )
        require_known("plan ids on drug costs", (c.plan_id for c in self.drug_costs), plans)
        require_known("drug ids on drug costs", (c.drug_id for c in self.drug_costs), drugs)
        require_known(
            "plan ids on documents",
            (d.plan_id for d in self.documents if d.plan_id is not None),
            plans,
        )
        return self
