"""No peeking: scenario truth never reaches a learner endpoint before the call ends.

Canary strings are planted in each case's truth-only text and in seeded document defects. The
sweep calls every learner endpoint before, during and after the call and fails if any canary
shows up. The answer key and findings must refuse until the call ends, then contain the
canaries, which proves the sweep can see them.
"""

import re

import pytest
from fastapi.testclient import TestClient

from app.content.loader import load_bundle
from tests.harness import Call

BUNDLE = load_bundle()
DEFECTS = [d.seeded_defect for d in BUNDLE.catalog.documents if d.seeded_defect]


def sweep(call: Call) -> None:
    """Touch every learner endpoint once."""
    call.responses.append(call.c.get("/api/scenarios").text)
    call.responses.append(call.c.get("/api/reference").text)
    call.get("")
    call.get("/research/plans", zip="00042")
    for plan in BUNDLE.catalog.plans:
        call.get(f"/research/plans/{plan.plan_id}")
    call.get("/research/providers", q="raman")
    call.get("/research/providers", specialty="cardiology")
    call.network("raman-northside", "TRN-3003-001", year=2026)
    call.get("/research/drugs", q="")
    call.formulary("TRN-1001-001", "apixaban-5mg-tab")
    call.get("/research/pharmacies", q="", zip="00044")
    call.pharmacy("TRN-3003-002", "cedar-street-rx")
    call.post(
        "/research/compare",
        {
            "plan_ids": ["TRN-1001-001", "TRN-1001-002", "TRN-2002-001", "TRN-3003-002"],
            "drugs": [{"drug_id": "apixaban-5mg-tab"}, {"drug_id": "rosuvastatin-10mg-tab"}],
            "pharmacy_id": "alder-family-rx",
        },
    )
    call.get("/documents")
    for doc in BUNDLE.catalog.documents:
        call.document(doc.document_id)
    call.save("notes", {"reason_for_inquiry": "Plan letter"})
    app_id = call.apply("TRN-1001-002", "Test Person", "00042")
    call.get("/applications")
    call.post(f"/applications/{app_id}/withdraw")
    call.pin(call.formulary("TRN-1001-002", "apixaban-5mg-tab"))
    call.get("/evidence")
    call.post("/followups", {"due_date": "2026-12-01", "reason": "Call back"}, 201)
    call.post("/hold", {"on": True})
    call.post("/hold", {"on": False})
    # Ask about every topic so the customer says everything it knows.
    topics = sorted({t for s in BUNDLE.scenarios.values() for k in s.knowledge for t in k.topics})
    for topic in topics:
        call.turn(f"Can you tell me about your {topic}?")
    call.get("/record")
    call.get("/transcript")
    call.get("/events")
    call.get("/found")


def assert_clean(call: Call, canaries: list[str]) -> None:
    blob = "\n".join(call.responses)
    for secret in [*canaries, *DEFECTS]:
        assert secret not in blob, f"leaked: {secret}"


@pytest.mark.parametrize("scenario_id", sorted(BUNDLE.scenarios))
def test_no_truth_reaches_the_learner(client: TestClient, scenario_id: str) -> None:
    canaries = list(BUNDLE.scenario(scenario_id).leak_canaries)
    call = Call(client, scenario_id)
    call.get("")
    call.get("/answer-key", expect=403)
    call.get("/findings", expect=403)
    call.start().opening()
    call.hear("I have a question.")
    sweep(call)
    call.get("/answer-key", expect=403)
    call.get("/findings", expect=403)
    call.get("/actor-qa", expect=403)
    call.get("/debrief", expect=403)
    call.post("/debrief/judge", expect=403)
    call.end()
    call.get("")
    call.get("/record")
    call.get("/events")
    call.get("/transcript")
    assert_clean(call, canaries)

    key = call.c.get(f"{call.base}/answer-key")
    assert key.status_code == 200
    for secret in [*canaries, *DEFECTS]:
        assert secret in key.text

    # A retry is a new call: what it shows before it is answered is as clean as any other.
    retry = Call.__new__(Call)
    resp = client.post("/api/sessions", json={"scenario_id": scenario_id, "retry_of": call.sid})
    assert resp.status_code == 201
    retry.responses = [resp.text, client.get(f"/api/sessions/{resp.json()['session_id']}").text]
    assert_clean(retry, canaries)
    scenario = BUNDLE.scenario(scenario_id)
    for truth in (scenario.title, *(t.description for t in scenario.traps)):
        assert truth not in "".join(retry.responses)


def test_session_view_hides_persona_rolls(client: TestClient) -> None:
    call = Call(client, "scenario-101")
    row = call.service.get(call.sid)
    body = call.get("")
    assert "mood" not in body and "voice" not in body and "seed" not in body
    assert row.mood not in str(body) and row.voice not in str(body)


def test_scenario_cards_do_not_name_the_lesson(client: TestClient) -> None:
    cards = client.get("/api/scenarios").json()
    titles = {s.title for s in BUNDLE.scenarios.values()}
    assert cards
    for card in cards:
        assert set(card) == {"scenario_id", "learner_title"}
        assert card["learner_title"] not in titles
        assert re.fullmatch(r"scenario-\d{3}", card["scenario_id"]), card["scenario_id"]
