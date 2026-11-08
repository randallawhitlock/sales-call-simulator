"""The shipped content files validate and match docs/rule-register.md."""

from datetime import date

from app.content.common import Authority
from app.content.loader import load_call_frame, load_ruleset
from app.content.rules import RuleStatus

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
