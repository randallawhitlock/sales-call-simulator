"""The reference drawer: standardized CMS text apart from the app's own wording, general
requirements only."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.content.loader import load_bundle
from app.content.rules import Rule
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
