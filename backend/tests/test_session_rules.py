"""Session lifecycle, learner input rules and replay."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.content.loader import load_bundle
from app.engine.checks import run_checks
from app.engine.records import SessionState
from app.engine.store import Store
from tests.harness import Call
from tests.test_goldens import A_DRUGS, MAPLE_CHOICE, a_call

CASE_A = "case-a-must-keep-specialist"


def test_research_and_records_need_an_active_call(client: TestClient) -> None:
    call = Call(client, CASE_A)
    call.get("/research/plans", expect=409, zip="00041")
    call.put("/record/notes", {"reason_for_inquiry": "x"}, expect=409)
    call.start()
    call.post("/start", expect=409)
    call.end()
    call.get("/research/plans", expect=409, zip="00041")
    call.post("/end", expect=409)


def test_unknown_session_and_scenario(client: TestClient) -> None:
    assert client.get("/api/sessions/s-missing").status_code == 404
    assert client.post("/api/sessions", json={"scenario_id": "nope"}).status_code == 404


def test_learner_cannot_speak_for_the_customer(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    body = {"client_utt_id": "fake-0001", "speaker": "customer", "channel": "text", "text": "Yes"}
    call.post("/utterances", body, expect=422)


def test_utterance_retries_are_idempotent(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    body = {"client_utt_id": "retry-0001", "speaker": "learner", "channel": "text", "text": "Hi"}
    first = call.post("/utterances", body, expect=201)
    second = call.post("/utterances", body, expect=201)
    assert first == second
    assert len(call.get("/transcript")) == 1
    assert [e["type"] for e in call.get("/events")].count("utterance") == 1


def test_record_input_is_validated(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    call.put("/record/drugs", {"status": "listed", "drugs": [{"drug_id": "nope"}]}, expect=422)
    call.put("/record/drugs", {"status": "listed", "drugs": []}, expect=422)
    call.put("/record/drugs", {"status": "none"})
    call.put("/record/pharmacy", {"pharmacy_id": "alder-family-rx", "source_utt_id": "u0099"}, 422)
    call.put("/record/disposition", {"outcome": "enroll"}, expect=422)
    call.put("/record/unknown", {}, expect=404)
    utt = call.hear("I use Alder Family Pharmacy.")
    record = call.put("/record/pharmacy", {"pharmacy_id": "alder-family-rx", "source_utt_id": utt})
    assert record["pharmacy"]["source_utt_id"] == utt
    assert record["drugs"] == {"status": "none", "drugs": []}


def test_evidence_copies_context_from_its_source(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    seq = call.network("raman-lakeview", "TRN-2002-001")
    pin = call.post("/evidence", {"source_seq": seq}, expect=201)
    assert pin["payload"]["source"]["location_id"] == "lakeview-heart"
    assert pin["payload"]["source"]["year"] == 2027
    utt_seq = call.get("/events")[0]["seq"]
    call.post("/evidence", {"source_seq": utt_seq}, expect=422)
    call.post("/evidence", {"source_seq": 999}, expect=422)


def test_application_warnings_do_not_block(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    body = {
        "plan_id": "TRN-1001-001",
        "applicant_name": "Loretta Banks",
        "residence_zip": "00041",
        "effective_date": "2027-02-01",
    }
    view = call.post("/applications", body, expect=201)
    assert any("requires a primary care doctor" in w for w in view["warnings"])
    assert any("2027-01-01" in w for w in view["warnings"])
    ppo = call.post(
        "/applications",
        body | {"plan_id": MAPLE_CHOICE, "pcp_practice_id": "whitfield-alder"},
        expect=201,
    )
    assert any("organization policy" in w for w in ppo["warnings"])
    app_id = view["application"]["application_id"]
    call.post(f"/applications/{app_id}/pecl", {"item_index": 9}, expect=422)
    call.post(f"/applications/{app_id}/consent", {"utt_id": "u0042"}, expect=422)
    call.submit(app_id)
    call.post(f"/applications/{app_id}/pecl", {"item_index": 0}, expect=409)


def test_hold_toggles(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    call.post("/hold", {"on": False}, expect=409)
    call.post("/hold", {"on": True})
    call.post("/hold", {"on": True}, expect=409)


def test_sessions_pin_versions_and_seeded_rolls(client: TestClient) -> None:
    first = Call(client, CASE_A, seed=11)
    second = Call(client, CASE_A, seed=11)
    a, b = first.service.get(first.sid), second.service.get(second.sid)
    assert (a.mood, a.voice) == (b.mood, b.voice)
    assert (a.catalog_version, a.ruleset_version, a.rubric_version) == ("2027-v1", "mvp-2027.1", 1)


def test_a_stored_log_replays_to_the_same_findings(client: TestClient, tmp_path: Path) -> None:
    call = a_call(client)
    call.verify(MAPLE_CHOICE, ["raman-northside"], A_DRUGS, "alder-family-rx")
    call.decide("enroll", MAPLE_CHOICE)
    call.enroll(MAPLE_CHOICE, "Loretta Banks", "00041")
    call.end()
    live = call.get("/findings")["results"]

    # Copy the log into a fresh file-backed store and grade it again.
    store = Store(str(tmp_path / "replay.db"))
    source = call.service.store
    for row in [source.get_session(call.sid)]:
        assert row is not None
        store.put_session(row)
    with store.transaction() as conn:
        for e in source.events(call.sid):
            conn.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    e.session_id,
                    e.seq,
                    e.type.value,
                    e.at.isoformat(),
                    e.t_ms,
                    e.utt_id,
                    json.dumps(e.payload),
                ),
            )
        for i, u in enumerate(source.utterances(call.sid)):
            conn.execute(
                "INSERT INTO utterances VALUES (?, ?, ?, ?)",
                (u.session_id, u.utt_id, f"replay-{i:04d}", u.model_dump_json()),
            )
    bundle = load_bundle()
    replayed = run_checks(
        bundle.scenario(CASE_A),
        bundle.catalog,
        bundle.frame,
        store.events(call.sid),
        store.utterances(call.sid),
    )
    assert [r.model_dump(mode="json") for r in replayed] == live
    assert SessionState.fold(store.events(call.sid)) == call.service.state(call.sid)
    store.close()


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_every_case_runs_with_any_seed(client: TestClient, seed: int) -> None:
    for scenario_id in load_bundle().scenarios:
        Call(client, scenario_id, seed=seed).start().end()
