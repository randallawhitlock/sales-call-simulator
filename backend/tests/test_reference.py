"""The reference drawer: standardized CMS text apart from the app's own wording, general
requirements only."""

from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.content.loader import load_bundle
from app.content.rules import Rule, RuleStatus
from tests.harness import GREETING, SCOPE_ASK, Call

BUNDLE = load_bundle()


def test_reference_fills_the_standard_disclaimer_from_the_call_frame(client: TestClient) -> None:
    body = client.get("/api/reference").json()
    disc = next(s for s in body["standard"] if s["rule_id"] == "R-DISC")
    frame = BUNDLE.frame
    assert (
        f"represent {frame.carriers_represented} organizations which offer "
        f"{frame.products_offered} products" in disc["text"]
    )
    assert "{" not in disc["text"] and "}" not in disc["text"]
    assert disc["verified_as_of"] == "2026-11-07"
    assert disc["citation"].startswith("42 CFR 422.2267(e)(41)")


def test_every_rule_is_listed_with_its_authority_and_status(client: TestClient) -> None:
    body = client.get("/api/reference").json()
    listed = {r["rule_id"]: r for r in body["rules"]}
    assert set(listed) == {r.rule_id for r in BUNDLE.ruleset.rules}
    assert listed["O-RECNOTE"]["authority"] == "organization"
    assert listed["R-SB"]["status"] == "unverified"
    assert all("notes" not in r and "check_ids" not in r for r in body["rules"])


def test_retired_and_out_of_date_rules_are_left_out(client: TestClient) -> None:
    """A retired rule, or one not in effect on the call date, is not a current requirement.
    A disputed one stays, with its status, so the drawer can say so."""
    service = client.app.state.sessions  # type: ignore[attr-defined]
    day = BUNDLE.frame.call_date
    changes: dict[str, dict[str, Any]] = {
        "O-RECNOTE": {"status": RuleStatus.RETIRED},
        "R-DISC": {"effective_to": day - timedelta(days=1)},
        "R-SOA": {"effective_from": day + timedelta(days=1)},
        "R-SB": {"status": RuleStatus.DISPUTED},
    }
    rules = tuple(r.model_copy(update=changes.get(r.rule_id, {})) for r in BUNDLE.ruleset.rules)
    assert len(rules) > len(changes)
    service.content = replace(BUNDLE, ruleset=BUNDLE.ruleset.model_copy(update={"rules": rules}))
    body = client.get("/api/reference").json()
    listed = {r["rule_id"]: r["status"] for r in body["rules"]}
    assert set(listed) == {r.rule_id for r in rules} - {"O-RECNOTE", "R-DISC", "R-SOA"}
    assert listed["R-SB"] == "disputed"
    assert "R-DISC" not in {s["rule_id"] for s in body["standard"]}


def test_saying_the_standard_text_passes_the_disclaimer_check(client: TestClient) -> None:
    """The drawer's wording and the deterministic check agree."""
    text = next(
        s["text"]
        for s in client.get("/api/reference").json()["standard"]
        if s["rule_id"] == "R-DISC"
    )
    call = Call(client, "scenario-101").start()
    call.say(GREETING)
    call.hear("Hi, I have some questions about my coverage.")
    call.say(text)
    call.say(SCOPE_ASK)
    call.say("Let's look at premiums and copays.")
    call.end()
    assert call.findings()["disclaimer_before_benefits"][0] == "pass"


def test_standard_text_needs_an_approved_rule() -> None:
    raw = next(r for r in BUNDLE.ruleset.rules if r.rule_id == "R-SB").model_dump(mode="json")
    raw["standard_text"] = "Say this."
    with pytest.raises(ValidationError, match="only approved rules carry standard_text"):
        Rule.model_validate(raw)


def test_standard_text_fill_ins_are_checked() -> None:
    raw = next(r for r in BUNDLE.ruleset.rules if r.rule_id == "R-DISC").model_dump(mode="json")
    raw["standard_text"] = "We represent {carriers} organizations."
    with pytest.raises(ValidationError, match="fill-ins"):
        Rule.model_validate(raw)


@pytest.mark.parametrize(
    "text",
    [
        "We represent {organizations.count} organizations and {products} products.",
        "We represent {organizations[0]} organizations and {products} products.",
        "We represent {organizations} organizations and {} products.",
        "We represent {organizations} organizations and {products products.",
        "We represent {organizations} organizations.",
        "We do not offer every plan available in your area.",
    ],
)
def test_standard_text_needs_exactly_the_two_plain_fill_ins(text: str) -> None:
    """The drawer says the numbers are filled in, so a text without them is an authoring
    slip, and a fill-in that cannot format must fail as a validation error, not a crash."""
    raw = next(r for r in BUNDLE.ruleset.rules if r.rule_id == "R-DISC").model_dump(mode="json")
    raw["standard_text"] = text
    with pytest.raises(ValidationError, match="standard_text"):
        Rule.model_validate(raw)
