"""Plan research over the synthetic catalog: what the learner's center pane shows.

Every function here is pure and reads only the catalog and the learner's own inputs, never
scenario truth. Compare estimates use the drugs and pharmacy the learner entered, so a wrong
entry produces a wrong estimate, as it would in real quoting software.
"""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.content.catalog import (
    Benefit,
    Catalog,
    Document,
    DocumentKind,
    Drug,
    Location,
    PharmacyChannel,
    Plan,
    PlanType,
    Supplemental,
    TierCopay,
)
from app.content.common import Id

ESTIMATE_LABEL = "Training estimate"
WATERMARK = "Training document. Fictional plan for practice only."
MAX_COMPARE = 4


class PlanSummary(BaseModel):
    plan_id: str
    name: str
    carrier_name: str
    plan_type: PlanType
    plan_year: int
    monthly_premium: Decimal
    medical_deductible: Decimal
    drug_deductible: Decimal
    moop_in_network: Decimal
    moop_combined: Decimal | None
    pcp_required: bool
    referrals_required: bool


class PlanDetail(PlanSummary):
    benefits: tuple[Benefit, ...]
    supplementals: tuple[Supplemental, ...]
    drug_tiers: tuple[TierCopay, ...]
    customer_service_phone: str | None


class PracticeView(BaseModel):
    practice_id: str
    group_name: str | None
    location: Location


class ProviderResult(BaseModel):
    provider_id: str
    name: str
    specialty: str
    credentials: str | None
    practices: list[PracticeView]


class NetworkStatus(BaseModel):
    practice_id: str
    provider_id: str
    provider_name: str
    location: Location
    plan_id: str
    plan_name: str
    year: int
    in_network: bool
    accepting_new_patients: bool | None


class Copays(BaseModel):
    preferred_retail_30: Decimal
    standard_retail_30: Decimal
    mail_90: Decimal


class FormularyResult(BaseModel):
    plan_id: str
    plan_name: str
    drug: Drug
    covered: bool
    tier: int | None = None
    prior_auth: bool = False
    step_therapy: bool = False
    quantity_limit: str | None = None
    deductible_applies: bool = False
    copays: Copays | None = None


class PharmacyView(BaseModel):
    pharmacy_id: str
    name: str
    mail_order: bool
    location: Location | None


class PharmacyNetworkResult(BaseModel):
    plan_id: str
    plan_name: str
    pharmacy: PharmacyView
    in_network: bool
    preferred: bool
    channel: PharmacyChannel | None


class CompareDrugIn(BaseModel):
    drug_id: Id
    days_supply: Literal[30, 90] = 30


class CompareLine(BaseModel):
    drug: Drug
    covered: bool
    tier: int | None
    prior_auth: bool
    step_therapy: bool
    quantity_limit: str | None
    copay_per_fill: Decimal | None
    fills_per_year: int
    annual_cost: Decimal | None
    note: str | None = None


class ComparePlan(BaseModel):
    plan: PlanSummary
    pharmacy_in_network: bool
    pharmacy_preferred: bool
    annual_premium: Decimal
    annual_drug_cost: Decimal
    drug_deductible_applied: Decimal
    total: Decimal
    lines: list[CompareLine]
    warnings: list[str]


class CompareResult(BaseModel):
    label: str = ESTIMATE_LABEL
    ranking: str = (
        "Listed by estimated yearly premium plus drug costs for the drugs and pharmacy entered. "
        "Medical costs are not included. This is not a recommendation."
    )
    method: str = (
        "Copays come from each plan's tier copays at the pharmacy's channel. A 90-day retail fill "
        "costs three 30-day copays. The drug deductible is added once when any entered drug is "
        "on tier 3 or higher. Drugs that are not covered, or a pharmacy outside the network, "
        "are flagged and left out of the totals."
    )
    plans: list[ComparePlan]


class DocumentSummary(BaseModel):
    document_id: str
    kind: DocumentKind
    title: str
    plan_id: str | None
    plan_year: int


class DocumentView(DocumentSummary):
    watermark: str = WATERMARK
    body_markdown: str
    checklist_items: tuple[str, ...]


def plan_summary(catalog: Catalog, plan: Plan) -> PlanSummary:
    return PlanSummary(
        plan_id=plan.plan_id,
        name=plan.name,
        carrier_name=catalog.carrier(plan.carrier_id).name,
        plan_type=plan.plan_type,
        plan_year=plan.plan_year,
        monthly_premium=plan.monthly_premium,
        medical_deductible=plan.medical_deductible,
        drug_deductible=plan.drug_deductible,
        moop_in_network=plan.moop_in_network,
        moop_combined=plan.moop_combined,
        pcp_required=plan.pcp_required,
        referrals_required=plan.referrals_required,
    )


def plans_for_zip(catalog: Catalog, zip_code: str) -> list[PlanSummary]:
    if zip_code not in catalog.county.zips:
        return []
    return [plan_summary(catalog, p) for p in catalog.plans]


def plan_detail(catalog: Catalog, plan_id: str) -> PlanDetail:
    plan = catalog.plan(plan_id)
    return PlanDetail(
        **plan_summary(catalog, plan).model_dump(),
        benefits=plan.benefits,
        supplementals=plan.supplementals,
        drug_tiers=plan.drug_tiers,
        customer_service_phone=catalog.carrier(plan.carrier_id).customer_service_phone,
    )


def search_providers(catalog: Catalog, q: str = "", specialty: str = "") -> list[ProviderResult]:
    needle, spec = q.strip().casefold(), specialty.strip().casefold()
    results = []
    for provider in catalog.providers:
        if needle and needle not in provider.name.casefold():
            continue
        if spec and spec not in provider.specialty.casefold():
            continue
        practices = [
            PracticeView(
                practice_id=pr.practice_id,
                group_name=pr.group_name,
                location=catalog.location(pr.location_id),
            )
            for pr in catalog.practices
            if pr.provider_id == provider.provider_id
        ]
        results.append(
            ProviderResult(
                provider_id=provider.provider_id,
                name=provider.name,
                specialty=provider.specialty,
                credentials=provider.credentials,
                practices=practices,
            )
        )
    return results


def provider_network(catalog: Catalog, practice_id: str, plan_id: str, year: int) -> NetworkStatus:
    """Participation of one practice in a plan's network on January 1 of ``year``. The
    directory shows status, not end dates, as most real directories do."""
    practice = catalog.practice(practice_id)
    provider = catalog.provider(practice.provider_id)
    plan = catalog.plan(plan_id)
    row = catalog.participation_on(practice_id, plan_id, date(year, 1, 1))
    return NetworkStatus(
        practice_id=practice_id,
        provider_id=provider.provider_id,
        provider_name=provider.name,
        location=catalog.location(practice.location_id),
        plan_id=plan_id,
        plan_name=plan.name,
        year=year,
        in_network=row is not None,
        accepting_new_patients=None if row is None else row.accepting_new_patients,
    )


def search_drugs(catalog: Catalog, q: str) -> list[Drug]:
    needle = q.strip().casefold()
    return [d for d in catalog.drugs if not needle or needle in d.name.casefold()]


def formulary_lookup(catalog: Catalog, plan_id: str, drug_id: str) -> FormularyResult:
    plan = catalog.plan(plan_id)
    drug = catalog.drug(drug_id)
    entry = catalog.formulary_entry(plan_id, drug_id)
    if entry is None:
        return FormularyResult(plan_id=plan_id, plan_name=plan.name, drug=drug, covered=False)
    tier = plan.tier_copay(entry.tier)
    return FormularyResult(
        plan_id=plan_id,
        plan_name=plan.name,
        drug=drug,
        covered=True,
        tier=entry.tier,
        prior_auth=entry.prior_auth,
        step_therapy=entry.step_therapy,
        quantity_limit=entry.quantity_limit,
        deductible_applies=entry.tier >= 3 and plan.drug_deductible > 0,
        copays=Copays(
            preferred_retail_30=tier.preferred_retail_30,
            standard_retail_30=tier.standard_retail_30,
            mail_90=tier.mail_90,
        ),
    )


def pharmacy_view(catalog: Catalog, pharmacy_id: str) -> PharmacyView:
    ph = catalog.pharmacy(pharmacy_id)
    return PharmacyView(
        pharmacy_id=ph.pharmacy_id,
        name=ph.name,
        mail_order=ph.mail_order,
        location=None if ph.location_id is None else catalog.location(ph.location_id),
    )


def search_pharmacies(catalog: Catalog, q: str = "", zip_code: str = "") -> list[PharmacyView]:
    needle = q.strip().casefold()
    out = []
    for ph in catalog.pharmacies:
        view = pharmacy_view(catalog, ph.pharmacy_id)
        if needle and needle not in ph.name.casefold():
            continue
        if (
            zip_code
            and not ph.mail_order
            and (view.location is None or view.location.zip != zip_code)
        ):
            continue
        out.append(view)
    return out


def pharmacy_network(catalog: Catalog, plan_id: str, pharmacy_id: str) -> PharmacyNetworkResult:
    plan = catalog.plan(plan_id)
    entry = catalog.pharmacy_entry(plan_id, pharmacy_id)
    view = pharmacy_view(catalog, pharmacy_id)
    return PharmacyNetworkResult(
        plan_id=plan_id,
        plan_name=plan.name,
        pharmacy=view,
        in_network=entry is not None,
        preferred=entry is not None and entry.preferred,
        channel=None if entry is None else _channel(view.mail_order, entry.preferred),
    )


def _channel(mail_order: bool, preferred: bool) -> PharmacyChannel:
    if mail_order:
        return PharmacyChannel.MAIL
    return PharmacyChannel.PREFERRED_RETAIL if preferred else PharmacyChannel.STANDARD_RETAIL


def compare(
    catalog: Catalog, plan_ids: list[str], drugs: list[CompareDrugIn], pharmacy_id: str
) -> CompareResult:
    if not 1 <= len(plan_ids) <= MAX_COMPARE:
        raise ValueError(f"compare takes 1 to {MAX_COMPARE} plans")
    if len(set(plan_ids)) != len(plan_ids):
        raise ValueError("compare lists a plan twice")
    rows = [_compare_plan(catalog, pid, drugs, pharmacy_id) for pid in plan_ids]
    rows.sort(key=lambda r: (r.total, r.plan.plan_id))
    return CompareResult(plans=rows)


def _compare_plan(
    catalog: Catalog, plan_id: str, drugs: list[CompareDrugIn], pharmacy_id: str
) -> ComparePlan:
    plan = catalog.plan(plan_id)
    network = pharmacy_network(catalog, plan_id, pharmacy_id)
    warnings: list[str] = []
    if not network.in_network:
        warnings.append(f"{network.pharmacy.name} is not in this plan's pharmacy network.")
    lines: list[CompareLine] = []
    deductible_drug = False
    for want in drugs:
        f = formulary_lookup(catalog, plan_id, want.drug_id)
        days = 90 if network.channel is PharmacyChannel.MAIL else want.days_supply
        fills = 12 if days == 30 else 4
        copay: Decimal | None = None
        note = None
        if not f.covered:
            note = "Not on this plan's drug list. Full cost not estimated."
            warnings.append(f"{f.drug.name} {f.drug.strength} is not covered.")
        elif network.channel is None or f.copays is None:
            note = "Pharmacy not in network. Not estimated."
        else:
            copay = _copay(f.copays, network.channel, days)
            if f.deductible_applies:
                deductible_drug = True
        if f.prior_auth or f.step_therapy or f.quantity_limit:
            warnings.append(f"{f.drug.name} {f.drug.strength} has coverage restrictions.")
        lines.append(
            CompareLine(
                drug=f.drug,
                covered=f.covered,
                tier=f.tier,
                prior_auth=f.prior_auth,
                step_therapy=f.step_therapy,
                quantity_limit=f.quantity_limit,
                copay_per_fill=copay,
                fills_per_year=fills,
                annual_cost=None if copay is None else copay * fills,
                note=note,
            )
        )
    drug_cost = sum((ln.annual_cost or Decimal(0) for ln in lines), Decimal(0))
    deductible = plan.drug_deductible if deductible_drug else Decimal(0)
    premium = plan.monthly_premium * 12
    return ComparePlan(
        plan=plan_summary(catalog, plan),
        pharmacy_in_network=network.in_network,
        pharmacy_preferred=network.preferred,
        annual_premium=premium,
        annual_drug_cost=drug_cost,
        drug_deductible_applied=deductible,
        total=premium + drug_cost + deductible,
        lines=lines,
        warnings=warnings,
    )


def _copay(copays: Copays, channel: PharmacyChannel, days: int) -> Decimal:
    if channel is PharmacyChannel.MAIL:
        return copays.mail_90
    per_30 = (
        copays.preferred_retail_30
        if channel is PharmacyChannel.PREFERRED_RETAIL
        else copays.standard_retail_30
    )
    return per_30 * (days // 30)


def document_summary(doc: Document) -> DocumentSummary:
    return DocumentSummary(
        document_id=doc.document_id,
        kind=doc.kind,
        title=doc.title,
        plan_id=doc.plan_id,
        plan_year=doc.plan_year,
    )


def list_documents(catalog: Catalog, plan_id: str | None = None) -> list[DocumentSummary]:
    return [
        document_summary(d) for d in catalog.documents if plan_id is None or d.plan_id == plan_id
    ]


def render_document(catalog: Catalog, document_id: str) -> DocumentView:
    """The learner's view of a document. ``seeded_defect`` is truth and never rendered."""
    doc = catalog.document(document_id)
    parts: list[str] = []
    if doc.kind is DocumentKind.SUMMARY_OF_BENEFITS and doc.plan_id is not None:
        parts.append(_render_sob(catalog, catalog.plan(doc.plan_id)))
    if doc.body_markdown:
        parts.append(doc.body_markdown)
    if doc.checklist_items:
        parts.append(
            "Before you enroll, review each item with the customer.\n\n"
            + "\n".join(f"{i + 1}. {item}" for i, item in enumerate(doc.checklist_items))
        )
    return DocumentView(
        **document_summary(doc).model_dump(),
        body_markdown="\n\n".join(parts),
        checklist_items=doc.checklist_items,
    )


def _usd(amount: Decimal) -> str:
    return f"${amount:,.2f}".replace(".00", "")


def _render_sob(catalog: Catalog, plan: Plan) -> str:
    carrier = catalog.carrier(plan.carrier_id)
    moop = f"{_usd(plan.moop_in_network)} in network"
    if plan.moop_combined is not None:
        moop += f"; {_usd(plan.moop_combined)} in and out of network combined"
    lines = [
        f"## {plan.name} ({plan.plan_type.value.upper()}), {plan.plan_year}",
        f"Offered by {carrier.name} in {catalog.county.name}.",
        "",
        "| Item | Amount |",
        "|---|---|",
        f"| Monthly plan premium | {_usd(plan.monthly_premium)} "
        "(you also keep paying your Part B premium) |",
        f"| Medical deductible | {_usd(plan.medical_deductible)} |",
        f"| Drug deductible (tiers 3 and up) | {_usd(plan.drug_deductible)} |",
        f"| Maximum out-of-pocket | {moop} |",
        f"| Primary care doctor required | {'Yes' if plan.pcp_required else 'No'} |",
        f"| Referrals to specialists required | {'Yes' if plan.referrals_required else 'No'} |",
        "",
        "### Medical benefits",
        "",
        "| Service | In network | Out of network | Prior authorization |",
        "|---|---|---|---|",
    ]
    for ben in plan.benefits:
        oon = ben.out_of_network or "Not covered except emergencies and urgent care"
        lines.append(
            f"| {ben.label} | {ben.in_network} | {oon} | {'Yes' if ben.prior_auth else 'No'} |"
        )
    lines += [
        "",
        "### Prescription drugs",
        "",
        "| Tier | Preferred retail, 30 days | Standard retail, 30 days | Mail order, 90 days |",
        "|---|---|---|---|",
    ]
    for t in plan.drug_tiers:
        lines.append(
            f"| {t.tier} | {_usd(t.preferred_retail_30)} | {_usd(t.standard_retail_30)} "
            f"| {_usd(t.mail_90)} |"
        )
    if plan.supplementals:
        lines += ["", "### Extra benefits", ""]
        for s in plan.supplementals:
            line = f"- **{s.name}**"
            if s.amount_text:
                line += f": {s.amount_text}"
            line += f". {s.description}"
            if s.conditional and s.eligibility_text:
                line += f" *Eligibility:* {s.eligibility_text}"
            lines.append(line)
    return "\n".join(lines)
