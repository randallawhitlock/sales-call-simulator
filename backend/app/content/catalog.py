"""Synthetic plan catalog: carriers, plans, providers, drugs, pharmacies and documents.

Everything here is fictional. Provider participation is keyed by practice (provider at a
location) x network x effective period, because "in network" without a location and a date is
the most common way agents get it wrong.
"""

from datetime import date
from enum import StrEnum
from typing import Self

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


class TierCopay(ContentModel):
    """Authored copays for one formulary tier. A drug's cost on a plan is its tier's copay at
    the pharmacy's channel, so screens, documents and estimates read one number."""

    tier: int = Field(ge=1, le=6)
    preferred_retail_30: Money
    standard_retail_30: Money
    mail_90: Money


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
    """Applies to tiers 3 and up. Tiers 1 and 2 are copay-only from the first fill."""
    moop_in_network: Money
    moop_combined: Money | None = None
    """In- and out-of-network maximum. PPOs only."""
    pcp_required: bool
    referrals_required: bool
    benefits: tuple[Benefit, ...] = Field(min_length=1)
    supplementals: tuple[Supplemental, ...] = ()
    drug_tiers: tuple[TierCopay, ...] = Field(min_length=1)

    def tier_copay(self, tier: int) -> TierCopay:
        for row in self.drug_tiers:
            if row.tier == tier:
                return row
        raise KeyError(f"{self.plan_id} has no copay for tier {tier}")

    @model_validator(mode="after")
    def _ppo_moop(self) -> Self:
        require_unique(f"{self.plan_id} drug tiers", (str(t.tier) for t in self.drug_tiers))
        require_unique(
            f"{self.plan_id} supplementals", (x.supplemental_id for x in self.supplementals)
        )
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
    body_markdown: str = ""
    """Authored text. Summaries of Benefits and checklists are rendered from records, so their
    numbers never drift from the plan."""
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
        require_known(
            "pharmacy network ids",
            (e.pharmacy_network_id for e in self.pharmacy_networks),
            {p.pharmacy_network_id for p in self.plans},
        )
        require_unique(
            "pharmacy network entries",
            (f"{e.pharmacy_network_id}/{e.pharmacy_id}" for e in self.pharmacy_networks),
        )
        for plan in self.plans:
            tiers = {f.tier for f in self.formulary if f.formulary_id == plan.formulary_id}
            missing = sorted(tiers - {t.tier for t in plan.drug_tiers})
            if missing:
                raise ValueError(f"{plan.plan_id}: no copay for formulary tiers {missing}")
        require_known(
            "plan ids on documents",
            (d.plan_id for d in self.documents if d.plan_id is not None),
            plans,
        )
        return self

    # Lookups. Content is small, so linear scans keep the model simple.

    def plan(self, plan_id: str) -> Plan:
        return _find(self.plans, "plan_id", plan_id)

    def carrier(self, carrier_id: str) -> Carrier:
        return _find(self.carriers, "carrier_id", carrier_id)

    def provider(self, provider_id: str) -> Provider:
        return _find(self.providers, "provider_id", provider_id)

    def location(self, location_id: str) -> Location:
        return _find(self.locations, "location_id", location_id)

    def practice(self, practice_id: str) -> Practice:
        return _find(self.practices, "practice_id", practice_id)

    def drug(self, drug_id: str) -> Drug:
        return _find(self.drugs, "drug_id", drug_id)

    def pharmacy(self, pharmacy_id: str) -> Pharmacy:
        return _find(self.pharmacies, "pharmacy_id", pharmacy_id)

    def document(self, document_id: str) -> Document:
        return _find(self.documents, "document_id", document_id)

    def formulary_entry(self, plan_id: str, drug_id: str) -> FormularyEntry | None:
        formulary_id = self.plan(plan_id).formulary_id
        for entry in self.formulary:
            if entry.formulary_id == formulary_id and entry.drug_id == drug_id:
                return entry
        return None

    def pharmacy_entry(self, plan_id: str, pharmacy_id: str) -> PharmacyNetworkEntry | None:
        network_id = self.plan(plan_id).pharmacy_network_id
        for entry in self.pharmacy_networks:
            if entry.pharmacy_network_id == network_id and entry.pharmacy_id == pharmacy_id:
                return entry
        return None

    def participation_on(
        self, practice_id: str, plan_id: str, on: date
    ) -> NetworkParticipation | None:
        network_id = self.plan(plan_id).network_id
        for row in self.participation:
            if row.practice_id == practice_id and row.network_id == network_id and row.covers(on):
                return row
        return None

    def plan_document(self, plan_id: str, kind: DocumentKind) -> Document:
        for doc in self.documents:
            if doc.plan_id == plan_id and doc.kind is kind and doc.plan_year == self.plan_year:
                return doc
        raise KeyError(f"{plan_id} has no {self.plan_year} {kind.value}")


def _find[T: ContentModel](items: tuple[T, ...], field: str, value: str) -> T:
    for item in items:
        if getattr(item, field) == value:
            return item
    raise KeyError(f"unknown {field}: {value}")
