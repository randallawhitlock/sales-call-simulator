"""Content models reject the mistakes a content author is most likely to make."""

from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from app.content.catalog import Catalog, NetworkParticipation, Plan, Supplemental
from app.content.rubric import Rubric
from app.content.rules import Rule
from app.content.scenario import CallFrame, Scenario


def plan(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "plan_id": "TRN-1001-001",
        "carrier_id": "maple",
        "name": "Maple Value HMO",
        "plan_type": "hmo",
        "plan_year": 2027,
        "county_id": "alder",
        "network_id": "maple-hmo",
        "formulary_id": "maple-f1",
        "pharmacy_network_id": "maple-rx",
        "monthly_premium": "0.00",
        "medical_deductible": "0.00",
        "drug_deductible": "0.00",
        "moop_in_network": "4900.00",
        "pcp_required": True,
        "referrals_required": True,
        "benefits": [{"category": "pcp_visit", "label": "PCP visit", "in_network": "$0"}],
    }
    return base | overrides


def rule(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "rule_id": "R-TEST",
        "version": 1,
        "title": "Test rule",
        "requirement": "Do the thing.",
        "authority": "regulatory",
        "citation": "42 CFR 422.0",
        "source_urls": ["https://www.ecfr.gov/"],
        "status": "approved",
        "verified_as_of": "2026-11-07",
        "evaluation": "deterministic",
        "evidence_policy": "event",
        "on_unknown": "instructor_review",
        "grading_effect": "points",
        "check_ids": ["sob_for_submitted_plan"],
    }
    return base | overrides


def scenario(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "scenario_id": "case-test",
        "version": 1,
        "title": "Test case",
        "frame_id": "aep-2027",
        "catalog_version": "2027-v1",
        "persona": {
            "first_name": "Dana",
            "last_name": "Ortiz",
            "age": 71,
            "speaking_style": "plain",
            "moods": ["calm"],
            "voices": ["voice-a"],
        },
        "crm_seed": {
            "customer_name": "Dana Ortiz",
            "phone": "(555) 555-0142",
            "zip": "00042",
            "lead_source": "Inbound call",
        },
        "truth": {"current_plan_id": "TRN-1001-001", "pharmacy_id": "corner-rx"},
        "knowledge": [
            {
                "item_id": "k1",
                "lay_text": "I see Dr. Lee.",
                "reveal": "when_asked",
                "topics": ["doc"],
            }
        ],
        "outcomes": [
            {
                "outcome_id": "keep",
                "kind": "keep_current",
                "verdict": "acceptable",
                "rationale": "Current plan still fits. CANARY-7Q2",
            }
        ],
        "rule_ids": ["R-SOA"],
        "leak_canaries": ["CANARY-7Q2"],
    }
    return base | overrides


def test_plan_ids_use_training_prefix() -> None:
    with pytest.raises(ValidationError):
        Plan.model_validate(plan(plan_id="H1234-001"))


def test_ppo_needs_combined_moop_and_hmo_must_not_have_one() -> None:
    Plan.model_validate(plan(plan_type="ppo", moop_combined="9000.00"))
    with pytest.raises(ValidationError, match="moop_combined"):
        Plan.model_validate(plan(plan_type="ppo"))
    with pytest.raises(ValidationError, match="moop_combined"):
        Plan.model_validate(plan(moop_combined="9000.00"))


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="extra"):
        Plan.model_validate(plan(premium="0.00"))


def test_conditional_supplemental_needs_eligibility_text() -> None:
    with pytest.raises(ValidationError, match="eligibility_text"):
        Supplemental.model_validate(
            {
                "supplemental_id": "allowance",
                "name": "Allowance",
                "description": "x",
                "conditional": True,
            }
        )


def test_participation_dates_and_coverage() -> None:
    row = NetworkParticipation(
        practice_id="lee-north",
        network_id="maple-hmo",
        effective_from=date(2026, 1, 1),
        effective_to=date(2026, 12, 31),
    )
    assert row.covers(date(2026, 11, 12))
    assert not row.covers(date(2027, 1, 1))
    with pytest.raises(ValidationError, match="effective_to"):
        NetworkParticipation(
            practice_id="lee-north",
            network_id="maple-hmo",
            effective_from=date(2027, 1, 1),
            effective_to=date(2026, 1, 1),
        )


def test_catalog_rejects_dangling_references() -> None:
    catalog = {
        "catalog_version": "2027-v1",
        "plan_year": 2027,
        "county": {"county_id": "alder", "name": "Alder", "state": "Fictional", "zips": ["00042"]},
        "carriers": [{"carrier_id": "maple", "name": "Maple Health"}],
        "plans": [plan()],
    }
    Catalog.model_validate(catalog)
    with pytest.raises(ValidationError, match="carrier ids on plans"):
        Catalog.model_validate(catalog | {"carriers": []})
    with pytest.raises(ValidationError, match="duplicate plan ids"):
        Catalog.model_validate(catalog | {"plans": [plan(), plan()]})


def test_catalog_addresses_must_be_fictional() -> None:
    with pytest.raises(ValidationError):
        Catalog.model_validate(
            {
                "catalog_version": "2027-v1",
                "plan_year": 2027,
                "county": {"county_id": "alder", "name": "A", "state": "F", "zips": ["90210"]},
            }
        )


def test_rule_prefix_must_match_authority() -> None:
    with pytest.raises(ValidationError, match="prefix"):
        Rule.model_validate(rule(authority="training"))


def test_approved_rule_needs_verified_date() -> None:
    with pytest.raises(ValidationError, match="verified_as_of"):
        Rule.model_validate(rule(verified_as_of=None))
    Rule.model_validate(rule(verified_as_of=None, status="unverified"))


def test_non_judged_rule_needs_a_check() -> None:
    with pytest.raises(ValidationError, match="check_ids"):
        Rule.model_validate(rule(check_ids=[]))
    Rule.model_validate(rule(check_ids=[], evaluation="judged"))


def test_aep_frame_must_fall_inside_aep() -> None:
    frame = {
        "frame_id": "aep-2027",
        "call_date": "2026-12-08",
        "plan_year": 2027,
        "election_period": "aep",
        "direction": "inbound",
        "county_id": "alder",
        "agency_name": "Agency",
        "learner_role": "Agent",
        "carriers_represented": 3,
        "products_offered": 6,
    }
    with pytest.raises(ValidationError, match="AEP"):
        CallFrame.model_validate(frame)


def test_scenario_canaries_must_be_planted_in_truth_only_text() -> None:
    Scenario.model_validate(scenario())
    with pytest.raises(ValidationError, match="not found in truth-only text"):
        Scenario.model_validate(scenario(leak_canaries=["CANARY-MISSING"]))
    leaky = scenario(
        knowledge=[
            {"item_id": "k1", "lay_text": "CANARY-7Q2", "reveal": "volunteer", "topics": ["x"]}
        ]
    )
    with pytest.raises(ValidationError, match="customer knowledge"):
        Scenario.model_validate(leaky)


def test_scenario_needs_an_acceptable_outcome() -> None:
    excluded = scenario(
        outcomes=[
            {
                "outcome_id": "bad",
                "kind": "enroll",
                "verdict": "excluded",
                "plan_ids": ["TRN-1001-002"],
                "rationale": "Wrong plan. CANARY-7Q2",
            }
        ]
    )
    with pytest.raises(ValidationError, match="acceptable outcome"):
        Scenario.model_validate(excluded)


def test_enroll_outcomes_name_plans() -> None:
    with pytest.raises(ValidationError, match="plan_ids"):
        Scenario.model_validate(
            scenario(
                outcomes=[
                    {
                        "outcome_id": "enroll",
                        "kind": "enroll",
                        "verdict": "acceptable",
                        "rationale": "CANARY-7Q2",
                    }
                ]
            )
        )


def test_rubric_dimensions_must_sum_to_their_weights() -> None:
    criterion = {
        "criterion_id": "c1",
        "dimension": "discovery",
        "title": "Asked about doctors",
        "points": 15,
        "method": "judged",
        "evidence_policy": "transcript",
        "judged_spec": "Learner asked which doctors the customer must keep.",
    }
    with pytest.raises(ValidationError, match="eligibility criteria total 0"):
        Rubric.model_validate({"rubric_id": "r", "version": 1, "criteria": [criterion]})


def test_rubric_method_inputs() -> None:
    with pytest.raises(ValidationError, match="check_id"):
        Rubric.model_validate(
            {
                "rubric_id": "r",
                "version": 1,
                "criteria": [
                    {
                        "criterion_id": "c1",
                        "dimension": "discovery",
                        "title": "x",
                        "points": 15,
                        "method": "deterministic",
                        "evidence_policy": "event",
                    }
                ],
            }
        )
