"""A call screen that reloads mid-call gets back everything it showed: live hold and turn
state on the session view, and the catalog items its searches had returned."""

import pytest
from fastapi.testclient import TestClient

from app.engine.events import EventType
from app.engine.records import SessionState
from tests.harness import Call
from tests.test_actor import blocked_turn

CASE_A = "scenario-101"


def test_found_is_empty_before_any_search(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    assert call.get("/found") == {"plans": [], "providers": [], "drugs": [], "pharmacies": []}


def test_found_rebuilds_search_results_in_first_seen_order(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    raman = call.get("/research/providers", q="raman")["result"]
    cardiology = call.get("/research/providers", specialty="cardiology")["result"]
    plans = call.get("/research/plans", zip="00042")["result"]
    drugs = call.get("/research/drugs", q="apix")["result"]
    pharmacies = call.get("/research/pharmacies", q="", zip="00044")["result"]
    call.formulary("TRN-1001-001", "apixaban-5mg-tab")  # lookups don't add items
    events_before = len(call.get("/events"))

    found = call.get("/found")

    seen: dict[str, dict[str, object]] = {}
    for p in raman + cardiology:
        seen.setdefault(p["provider_id"], p)
    assert found["providers"] == list(seen.values())
    assert found["plans"] == plans
    assert found["drugs"] == drugs
    assert found["pharmacies"] == pharmacies
    assert len(call.get("/events")) == events_before, "reading /found logs nothing"


def test_found_skips_ids_the_catalog_no_longer_has(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    call.get("/research/drugs", q="apix")
    call.service.log(call.sid, EventType.SEARCH, {"kind": "drugs", "result_ids": ["gone-drug"]})
    call.service.log(call.sid, EventType.SEARCH, {"kind": "unknown", "result_ids": ["x"]})
    ids = [d["drug_id"] for d in call.get("/found")["drugs"]]
    assert "gone-drug" not in ids and ids


def test_found_still_answers_after_the_call_ends(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    call.get("/research/drugs", q="apix")
    call.end()
    assert call.get("/found")["drugs"]


def test_session_view_reports_hold(client: TestClient) -> None:
    call = Call(client, CASE_A)
    assert call.get("")["on_hold"] is False
    call.start()
    call.post("/hold", {"on": True})
    assert call.get("")["on_hold"] is True
    call.post("/hold", {"on": False})
    assert call.get("")["on_hold"] is False
    call.post("/hold", {"on": True})
    call.end()
    assert call.get("")["on_hold"] is False, "an ended call is not on hold"


def test_session_view_does_not_fold_the_event_log(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The call screen polls the view every second while a reply is pending."""
    call = Call(client, CASE_A).start()
    call.post("/hold", {"on": True})

    def no_fold(events: object) -> None:
        raise AssertionError("the session view folded the whole log")

    monkeypatch.setattr(SessionState, "fold", no_fold)
    monkeypatch.setattr(call.service.store, "events", no_fold)
    assert call.get("")["on_hold"] is True


def test_session_view_reports_a_reply_in_flight(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    assert call.get("")["turn_pending"] is False
    actor, (t1, first) = blocked_turn(call)
    assert call.get("")["turn_pending"] is True
    actor.release.set()
    t1.join(5)
    assert first["value"].customer is not None
    assert call.get("")["turn_pending"] is False
