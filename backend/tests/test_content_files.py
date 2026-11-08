"""The shipped content files validate and match docs/rule-register.md."""

from dataclasses import replace
from datetime import date

from app.content.common import Authority
from app.content.loader import load_bundle, load_call_frame, load_ruleset, validate_bundle
from app.content.rules import RuleStatus
from app.content.scenario import OutcomeVerdict

REGISTER_IDS = {
    "R-SOA",
    "R-DISC",
    "R-NEEDS",
    "R-PECL",
    "R-SB",
    "R-CONSENT",
    "G-PREENR",
    "P-PCP",
    "O-RECNOTE",
}


def test_ruleset_matches_register() -> None:
    ruleset = load_ruleset()
    assert {r.rule_id for r in ruleset.rules} == REGISTER_IDS


def test_only_open_items_are_unverified() -> None:
    unverified = {r.rule_id for r in load_ruleset().rules if r.status is RuleStatus.UNVERIFIED}
    assert unverified == {"R-SB", "R-CONSENT"}


def test_needs_rule_is_regulatory_only_on_enrollment() -> None:
    rule = load_ruleset().get("R-NEEDS")
    assert rule.authority is Authority.REGULATORY
    assert rule.authority_outside_scope is Authority.TRAINING
    assert [o.value for o in rule.applies_when.outcomes] == ["enroll"]


def test_cy2027_rules_carry_effective_date() -> None:
    ruleset = load_ruleset()
    for rule_id in ("R-SOA", "R-DISC"):
        assert ruleset.get(rule_id).effective_from == date(2026, 6, 1)


def test_call_frame_is_aep_2027() -> None:
    frame = load_call_frame()
    assert frame.call_date == date(2026, 11, 12)
    assert frame.coverage_effective == date(2027, 1, 1)
    assert (frame.carriers_represented, frame.products_offered) == (3, 6)


# Catalog, scenarios and rubric, checked against each other.

BUNDLE = load_bundle()


def test_catalog_matches_the_mvp_scope() -> None:
    c = BUNDLE.catalog
    assert (len(c.carriers), len(c.plans), len(c.providers)) == (3, 6, 12)
    assert (len(c.drugs), len(c.pharmacies)) == (12, 4)
    assert {p.plan_type.value for p in c.plans} == {"hmo", "ppo"}


def test_three_launch_cases_with_planted_canaries() -> None:
    assert set(BUNDLE.scenarios) == {
        "scenario-101",
        "scenario-102",
        "scenario-103",
    }
    for scenario in BUNDLE.scenarios.values():
        assert scenario.leak_canaries
        assert scenario.learner_title != scenario.title


def test_keeping_and_deferring_can_be_right() -> None:
    kinds = {
        (s.scenario_id, o.kind.value)
        for s in BUNDLE.scenarios.values()
        for o in s.outcomes
        if o.verdict is OutcomeVerdict.ACCEPTABLE
    }
    assert ("scenario-102", "keep_current") in kinds
    assert ("scenario-103", "defer") in kinds
    assert not any(k == ("scenario-103", "enroll") for k in kinds)


def test_bundle_rejects_a_frame_that_disagrees_with_the_catalog() -> None:
    frame = BUNDLE.frame.model_copy(update={"products_offered": 5})
    problems = validate_bundle(replace(BUNDLE, frame=frame))
    assert any("offers 5 products" in p for p in problems)


def test_bundle_rejects_unclassified_plans_and_unknown_ids() -> None:
    scenario = BUNDLE.scenario("scenario-102")
    outcomes = tuple(o for o in scenario.outcomes if o.outcome_id != "enroll-other")
    truth = scenario.truth.model_copy(update={"pharmacy_id": "nowhere-rx"})
    broken = scenario.model_copy(update={"outcomes": outcomes, "truth": truth})
    problems = validate_bundle(replace(BUNDLE, scenarios={scenario.scenario_id: broken}))
    assert any("plans with no enroll verdict" in p for p in problems)
    assert any("pharmacy nowhere-rx" in p for p in problems)


def test_leak_guard_rejects_agent_words_in_customer_knowledge() -> None:
    scenario = BUNDLE.scenario("scenario-101")
    item = scenario.knowledge[0].model_copy(
        update={"lay_text": "Is apixaban-5mg-tab on the formulary for TRN-1001-002?"}
    )
    broken = scenario.model_copy(update={"knowledge": (item, *scenario.knowledge[1:])})
    problems = validate_bundle(replace(BUNDLE, scenarios={scenario.scenario_id: broken}))
    assert any("jargon 'formulary'" in p for p in problems)
    assert any("internal id 'apixaban-5mg-tab'" in p for p in problems)
    assert any("internal id 'TRN-1001-002'" in p for p in problems)


def test_bundle_rejects_overlapping_participation() -> None:
    row = BUNDLE.catalog.participation[0]
    catalog = BUNDLE.catalog.model_copy(
        update={"participation": (*BUNDLE.catalog.participation, row)}
    )
    assert any("overlapping" in p for p in validate_bundle(replace(BUNDLE, catalog=catalog)))
