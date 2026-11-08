"""Research results: participation by office and year, drug identity, costs and documents.

Every number a learner sees comes from one record. These tests check displayed figures
against their source records instead of against hand-copied constants.
"""

import re
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.content.catalog import DocumentKind, Plan
from app.content.loader import load_bundle
from app.engine import research
from tests.harness import Call

CATALOG = load_bundle().catalog
MONEY = re.compile(r"\$\d[\d,]*(?:\.\d{2})?")


def amounts(text: str) -> set[Decimal]:
    return {Decimal(m.replace("$", "").replace(",", "")) for m in MONEY.findall(text)}


def plan_amounts(plan: Plan) -> set[Decimal]:
    """Every dollar figure the plan record holds, in fields or in benefit text."""
    found = {
        plan.monthly_premium,
        plan.medical_deductible,
        plan.drug_deductible,
        plan.moop_in_network,
    }
    if plan.moop_combined is not None:
        found.add(plan.moop_combined)
    for t in plan.drug_tiers:
        found |= {t.preferred_retail_30, t.standard_retail_30, t.mail_90}
    for b in plan.benefits:
        found |= amounts(f"{b.in_network} {b.out_of_network or ''}")
    for s in plan.supplementals:
        found |= amounts(f"{s.amount_text or ''} {s.description} {s.eligibility_text or ''}")
    return found


@pytest.mark.parametrize("plan", CATALOG.plans, ids=lambda p: p.plan_id)
def test_summary_of_benefits_numbers_come_from_the_plan(plan: Plan) -> None:
    sob = CATALOG.plan_document(plan.plan_id, DocumentKind.SUMMARY_OF_BENEFITS)
    view = research.render_document(CATALOG, sob.document_id)
    shown = amounts(view.body_markdown)
    assert shown <= plan_amounts(plan)
    assert plan.monthly_premium in shown and plan.moop_in_network in shown


def test_authored_plan_documents_quote_only_record_numbers() -> None:
    for doc in CATALOG.documents:
        if doc.plan_id and doc.body_markdown and doc.plan_year == CATALOG.plan_year:
            assert amounts(doc.body_markdown) <= plan_amounts(CATALOG.plan(doc.plan_id)), (
                doc.document_id
            )


def test_documents_carry_the_training_watermark_and_hide_defects() -> None:
    for doc in CATALOG.documents:
        view = research.render_document(CATALOG, doc.document_id)
        assert view.watermark.startswith("Training document")
        assert "seeded_defect" not in view.model_dump()


def test_participation_is_by_office_and_year() -> None:
    def status(practice: str, plan: str, year: int) -> bool:
        return research.provider_network(CATALOG, practice, plan, year).in_network

    assert status("raman-northside", "TRN-3003-001", 2026)
    assert not status("raman-northside", "TRN-3003-001", 2027)
    assert status("raman-lakeview", "TRN-2002-001", 2027)
    assert not status("raman-northside", "TRN-2002-001", 2027)
    assert status("raman-northside", "TRN-1001-002", 2027)
    assert not status("okafor-cedar", "TRN-3003-002", 2027)


def test_drug_identity_includes_strength() -> None:
    on_rewards = research.formulary_lookup(CATALOG, "TRN-3003-002", "apixaban-2-5mg-tab")
    assert not on_rewards.covered
    five = research.formulary_lookup(CATALOG, "TRN-1001-001", "apixaban-5mg-tab")
    assert five.covered and five.prior_auth and five.tier == 3


def test_compare_arithmetic() -> None:
    drugs = [
        research.CompareDrugIn(drug_id="apixaban-5mg-tab"),
        research.CompareDrugIn(drug_id="atorvastatin-40mg-tab", days_supply=90),
    ]
    result = research.compare(CATALOG, ["TRN-1001-002", "TRN-2002-001"], drugs, "alder-family-rx")
    assert result.label == "Training estimate"
    by_plan = {row.plan.plan_id: row for row in result.plans}

    ppo = by_plan["TRN-1001-002"]  # Alder Family is preferred on Maple
    assert ppo.pharmacy_preferred
    assert ppo.annual_premium == Decimal("468.00")
    assert [ln.annual_cost for ln in ppo.lines] == [Decimal("504.00"), Decimal("0.00")]
    assert ppo.total == Decimal("972.00")

    hmo = by_plan["TRN-2002-001"]  # standard on Riverbend
    assert not hmo.pharmacy_preferred
    assert [ln.copay_per_fill for ln in hmo.lines] == [Decimal("47.00"), Decimal("24.00")]
    assert hmo.total == Decimal("564.00") + Decimal("96.00")
    assert [r.plan.plan_id for r in result.plans] == ["TRN-2002-001", "TRN-1001-002"]


def test_compare_flags_uncovered_drugs_and_out_of_network_pharmacies() -> None:
    drugs = [research.CompareDrugIn(drug_id="rosuvastatin-10mg-tab", days_supply=90)]
    (row,) = research.compare(CATALOG, ["TRN-3003-002"], drugs, "cedar-street-rx").plans
    assert not row.pharmacy_in_network
    assert row.lines[0].annual_cost is None
    assert any("not covered" in w for w in row.warnings)
    assert any("not in this plan's pharmacy network" in w for w in row.warnings)


def test_compare_adds_the_drug_deductible_once_for_higher_tiers() -> None:
    drugs = [research.CompareDrugIn(drug_id="sitagliptin-100mg-tab")]
    (row,) = research.compare(CATALOG, ["TRN-2002-002"], drugs, "valuemart-rx").plans
    assert row.drug_deductible_applied == Decimal("250.00")
    assert row.total == Decimal("708.00") + Decimal("540.00") + Decimal("250.00")


def test_compare_limits() -> None:
    with pytest.raises(ValueError, match="1 to 4"):
        research.compare(CATALOG, [p.plan_id for p in CATALOG.plans], [], "valuemart-rx")


def test_research_endpoints_log_events(client: TestClient) -> None:
    call = Call(client, "case-a-must-keep-specialist").start()
    plans = call.get("/research/plans", zip="00041")
    assert len(plans["result"]) == 6
    assert call.get("/research/plans", zip="00099")["result"] == []
    found = call.get("/research/providers", q="Raman")["result"]
    assert {p["provider_id"] for p in found} == {"priya-raman", "arjun-raman"}
    priya = next(p for p in found if p["provider_id"] == "priya-raman")
    assert {pr["practice_id"] for pr in priya["practices"]} == {"raman-northside", "raman-lakeview"}
    call.network("raman-northside", "TRN-1001-002")
    call.get(
        "/research/providers/network",
        expect=422,
        practice_id="raman-northside",
        plan_id="TRN-1001-002",
        year=2025,
    )
    call.get("/research/formulary", expect=404, plan_id="TRN-1001-002", drug_id="nope")
    kinds = [(e["type"], e["payload"].get("kind")) for e in call.get("/events")]
    assert kinds == [
        ("call_started", None),
        ("search", "plans"),
        ("search", "plans"),
        ("search", "providers"),
        ("lookup", "provider_network"),
    ]
