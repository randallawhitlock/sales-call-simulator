"""Session lifecycle, learner input rules and replay."""

import json
import threading
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.content.loader import load_bundle
from app.engine.checks import CoverageClaim, Facts, claim_backed, run_checks
from app.engine.events import Channel, EventType, Speaker, UtteranceIn
from app.engine.records import SessionState, SubmitIn
from app.engine.store import Store
from tests.harness import Call
from tests.test_goldens import A_DRUGS, MAPLE_CHOICE, SUMMIT_CLASSIC, a_call, enroll_to_consent

CASE_A = "scenario-101"


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
    assert (a.catalog_version, a.ruleset_version, a.rubric_version) == ("2027-v1", "mvp-2027.1", 2)


def test_findings_refuse_content_the_session_was_not_pinned_to(client: TestClient) -> None:
    call = Call(client, CASE_A).start().end()
    assert call.get("/findings")["catalog_version"] == "2027-v1"
    row = call.service.get(call.sid)
    call.service.store.put_session(row.model_copy(update={"catalog_version": "2027-v9"}))
    for path in ("/findings", "/answer-key"):
        body = call.get(path, expect=409)
        assert "catalog 2027-v9 (loaded: 2027-v1)" in body["detail"]


@pytest.mark.parametrize(
    "change",
    [
        {"applicant_name": "Loretta B. Banks"},
        {"residence_zip": "00042"},
        {"pcp_practice_id": "whitfield-alder"},
        {"effective_date": "2027-02-01"},
        {"plan_id": "TRN-1001-001"},
    ],
)
def test_application_fields_lock_after_checklist_review(
    client: TestClient, change: dict[str, str]
) -> None:
    call, app_id = enroll_to_consent(client)
    fields = call.get("/applications")[0]["application"]["fields"]
    call.put(f"/applications/{app_id}", fields)  # unchanged is fine
    body = call.put(f"/applications/{app_id}", fields | change, expect=409)
    assert "start a new application" in body["detail"]


def test_application_fields_lock_after_consent(client: TestClient) -> None:
    call = a_call(client)
    app_id = call.apply(MAPLE_CHOICE, "Loretta Banks", "00041")
    call.consent(app_id, call.hear("Yes, sign me up."))
    fields = call.get("/applications")[0]["application"]["fields"]
    call.put(f"/applications/{app_id}", fields | {"residence_zip": "00042"}, expect=409)


def test_claims_are_backed_only_by_lookups_that_showed_them(client: TestClient) -> None:
    call = a_call(client)
    early = call.say("Dr. Raman is in network on Summit Classic.")
    call.network("raman-northside", SUMMIT_CLASSIC)  # out of network for 2027
    call.network("raman-northside", MAPLE_CHOICE)  # in network
    said = call.say("Dr. Raman is covered on both plans.")
    bundle = load_bundle()
    facts = Facts(
        bundle.scenario(CASE_A),
        bundle.catalog,
        bundle.frame,
        call.service.store.events(call.sid),
        call.service.store.utterances(call.sid),
    )

    def backed(utt_id: str, plan_id: str, covered: bool = True) -> bool:
        claim = CoverageClaim(
            utt_id=utt_id,
            plan_id=plan_id,
            kind="provider_network",
            subject_id="raman-northside",
            covered=covered,
        )
        return claim_backed(facts, claim)

    assert backed(said, MAPLE_CHOICE)
    assert not backed(said, SUMMIT_CLASSIC)
    assert backed(said, SUMMIT_CLASSIC, covered=False)
    assert not backed(early, SUMMIT_CLASSIC, covered=False)


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


def test_a_failed_start_leaves_the_session_unstarted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = Call(client, CASE_A)
    service = call.service

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(service.store, "append_event", boom)
        with pytest.raises(RuntimeError):
            service.start(call.sid)
    assert service.get(call.sid).status.value == "created"
    call.start()
    assert [e["type"] for e in call.get("/events")] == ["call_started"]


@pytest.mark.parametrize("write", ["log", "utterance"])
def test_an_end_cannot_slip_inside_a_write(client: TestClient, write: str) -> None:
    """The end is attempted between the write's active check and its append. It has to wait,
    so nothing lands after the call-ended event."""
    call = Call(client, CASE_A).start()
    service = call.service
    clock = service.clock
    ender = threading.Thread(target=service.end, args=(call.sid,))
    waited: list[bool] = []

    def end_mid_write() -> datetime:
        if not waited:
            ender.start()
            ender.join(timeout=0.3)
            waited.append(ender.is_alive())
        return clock()

    service.clock = end_mid_write
    try:
        if write == "log":
            service.log(call.sid, EventType.FOLLOWUP_CREATED, {})
        else:
            service.add_utterance(
                call.sid,
                UtteranceIn(
                    client_utt_id="race-0001",
                    speaker=Speaker.LEARNER,
                    channel=Channel.TEXT,
                    text="Hi",
                ),
            )
    finally:
        service.clock = clock
        ender.join()
    assert waited == [True]
    assert [e.type for e in service.events(call.sid)][-1] is EventType.CALL_ENDED


def test_a_turn_refuses_content_the_session_was_not_pinned_to(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    service = call.service
    row = service.get(call.sid)
    service.store.put_session(row.model_copy(update={"scenario_version": row.scenario_version + 1}))
    assert "pinned to content" in json.dumps(call.turn("Hello?", expect=409))


def test_nothing_is_logged_against_unpinned_content_but_the_call_can_end(
    client: TestClient,
) -> None:
    call = Call(client, CASE_A).start()
    service = call.service
    row = service.get(call.sid)
    service.store.put_session(row.model_copy(update={"catalog_version": "an-older-catalog"}))
    before = len(call.get("/events"))
    call.get("/research/plans", expect=409, zip="00041")
    call.put("/record/drugs", {"status": "none"}, expect=409)
    assert len(call.get("/events")) == before
    call.end()


def test_a_field_save_cannot_land_after_a_submit(client: TestClient) -> None:
    """The submit is attempted between the save's draft check and its append; it has to wait,
    so the save is logged first and the submission stands on the saved fields."""
    call, app_id = enroll_to_consent(client)
    service = call.service
    fields = service.application(call.sid, app_id).fields
    clock = service.clock

    def submit() -> None:
        service.submit_application(call.sid, app_id, SubmitIn(idempotency_key="k" * 12))

    racer = threading.Thread(target=submit)
    waited: list[bool] = []

    def submit_mid_save() -> datetime:
        if not waited:
            racer.start()
            racer.join(timeout=0.3)
            waited.append(racer.is_alive())
        return clock()

    service.clock = submit_mid_save
    try:
        service.update_application(call.sid, app_id, fields)
    finally:
        service.clock = clock
        racer.join()
    assert waited == [True]
    types = [e.type for e in service.events(call.sid)]
    assert types[-2:] == [EventType.APPLICATION_UPDATED, EventType.APPLICATION_SUBMITTED]
