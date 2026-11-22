"""Voice calls on the API side: settings, sessions, join tokens, stats and recordings."""

import json

import pytest
from fastapi.testclient import TestClient
from livekit import api

from app.content.loader import load_bundle
from app.engine.events import Channel, Speaker, UtteranceIn
from app.engine.session import Conflict, Invalid
from app.voice.config import (
    AGENT_NAME,
    LEARNER_IDENTITY,
    VOICES,
    VoiceConfig,
    VoiceConfigError,
    voice_from_env,
)
from tests.conftest import VOICE_ENV
from tests.harness import Call

CASE_A = "scenario-101"


# Settings


def test_voice_is_off_without_a_model() -> None:
    assert voice_from_env({}) is None
    assert voice_from_env({"GEMINI_API_KEY": "k", "LIVEKIT_API_KEY": "k"}) is None


def test_half_configured_voice_is_an_error_not_off() -> None:
    with pytest.raises(VoiceConfigError, match="LIVEKIT_API_KEY, LIVEKIT_API_SECRET"):
        voice_from_env({"SIM_LIVE_MODEL": "m", "GEMINI_API_KEY": "k"})


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SIM_VOICE_SILENCE_MS", "50"),
        ("SIM_VOICE_SILENCE_MS", "soon"),
        ("SIM_VOICE_NUDGE_S", "-1"),
        ("SIM_LIVE_PRICE_IN_PER_MIN", "nan"),
    ],
)
def test_voice_numbers_are_range_checked(name: str, value: str) -> None:
    with pytest.raises(VoiceConfigError, match=name):
        voice_from_env(VOICE_ENV | {name: value})


def test_voice_defaults() -> None:
    config = voice_from_env(dict(VOICE_ENV))
    assert config is not None
    assert (config.silence_ms, config.nudge_s) == (700, 25.0)
    assert config.actor == "gemini-live:test-live-model"
    assert config.room_for("abc123") == "call-abc123"
    assert VoiceConfig.session_for("call-abc123") == "abc123"
    assert VoiceConfig.session_for("abc123") is None
    assert voice_from_env(VOICE_ENV | {"SIM_VOICE_NUDGE_S": "0"}).nudge_s == 0  # type: ignore[union-attr]


def test_every_persona_voice_has_a_gemini_voice() -> None:
    bundle = load_bundle()
    rolled = {v for s in bundle.scenarios.values() for v in s.persona.voices}
    assert rolled <= set(VOICES), f"no Gemini voice for {sorted(rolled - set(VOICES))}"


# Sessions


def test_voice_sessions_need_voice_on(client: TestClient) -> None:
    resp = client.post("/api/sessions", json={"scenario_id": CASE_A, "channel": "voice"})
    assert resp.status_code == 409
    assert "SIM_LIVE_MODEL" in resp.json()["detail"]
    assert client.get("/api/health").json()["voice"] == "off"


def test_voice_session_pins_the_live_model(voice_client: TestClient) -> None:
    assert voice_client.get("/api/health").json()["voice"] == "gemini-live:test-live-model"
    call = Call(voice_client, CASE_A, voice=True)
    assert call.session["channel"] == "voice"
    row = call.service.get(call.sid)
    assert (row.channel, row.actor) == (Channel.VOICE, "gemini-live:test-live-model")
    # Typed calls on the same server still pin the text actor.
    text = Call(voice_client, CASE_A)
    assert text.session["channel"] == "text"
    assert text.service.get(text.sid).actor == "scripted"


def test_a_voice_call_refuses_typed_turns(voice_client: TestClient) -> None:
    call = Call(voice_client, CASE_A, voice=True).start()
    body = {"client_utt_id": "learner-0001", "speaker": "learner", "channel": "text", "text": "Hi"}
    assert "talk to the customer" in call.post("/utterances", body, expect=409)["detail"]
    turn = {"client_utt_id": "turn-0001", "text": "Hi"}
    assert "talk to the customer" in call.post("/turns", turn, expect=409)["detail"]


def test_voice_lines_go_to_voice_calls_only(voice_client: TestClient) -> None:
    text = Call(voice_client, CASE_A).start()
    line = UtteranceIn(
        client_utt_id="lk-item-0001",
        speaker=Speaker.CUSTOMER,
        channel=Channel.VOICE,
        text="Hello?",
    )
    with pytest.raises(Conflict, match="typed call"):
        text.service.add_voice_utterance(text.sid, line)
    voice = Call(voice_client, CASE_A, voice=True).start()
    with pytest.raises(Invalid, match="voice turns only"):
        voice.service.add_voice_utterance(
            voice.sid, line.model_copy(update={"channel": Channel.TEXT})
        )
    first = voice.service.add_voice_utterance(voice.sid, line)
    again = voice.service.add_voice_utterance(voice.sid, line)
    assert first.utt_id == again.utt_id
    assert [u.text for u in voice.service.transcript(voice.sid)] == ["Hello?"]


def test_a_voice_line_keeps_when_it_was_said(voice_client: TestClient) -> None:
    call = Call(voice_client, CASE_A, voice=True).start()
    late = call.hear("Yes, Medicare Advantage.", t_ms=1200)
    utt = next(u for u in call.service.transcript(call.sid) if u.utt_id == late)
    assert utt.t_ms == 1200


def test_the_customer_prompt_refuses_another_live_model(voice_client: TestClient) -> None:
    call = Call(voice_client, CASE_A, voice=True)
    service = call.service
    row = service.get(call.sid)
    service.customer_prompt(row)
    service.voice_actor = "gemini-live:some-other-model"
    try:
        with pytest.raises(Conflict, match="some-other-model"):
            service.customer_prompt(row)
    finally:
        service.voice_actor = "gemini-live:test-live-model"


# Join tokens


def test_join_token_admits_the_learner_to_their_call_only(voice_client: TestClient) -> None:
    call = Call(voice_client, CASE_A, voice=True)
    call.post("/voice", expect=409)  # not started yet
    call.start()
    join = call.post("/voice")
    assert join["url"] == VOICE_ENV["LIVEKIT_URL"]
    assert join["room"] == f"call-{call.sid}"
    assert join["identity"] == LEARNER_IDENTITY
    claims = api.TokenVerifier(
        VOICE_ENV["LIVEKIT_API_KEY"], VOICE_ENV["LIVEKIT_API_SECRET"]
    ).verify(join["token"])
    assert claims.identity == LEARNER_IDENTITY
    assert claims.video is not None
    assert (claims.video.room, claims.video.room_join) == (join["room"], True)
    assert not claims.video.room_admin
    assert claims.room_config is not None
    (dispatch,) = claims.room_config.agents
    assert dispatch.agent_name == AGENT_NAME
    assert json.loads(dispatch.metadata) == {"session_id": call.sid}
    # Nothing secret rides along to the browser.
    assert VOICE_ENV["GEMINI_API_KEY"] not in call.responses[-1]
    call.end()
    call.post("/voice", expect=409)


def test_typed_calls_get_no_join_token(voice_client: TestClient) -> None:
    call = Call(voice_client, CASE_A).start()
    assert "typed call" in call.post("/voice", expect=409)["detail"]


# Stats and recordings


def test_voice_stats_and_recording_after_the_call(voice_client: TestClient) -> None:
    call = Call(voice_client, CASE_A, voice=True).start()
    call.get("/voice-stats", expect=403)
    call.get("/recording", expect=403)
    call.service.store.put_voice_stats(call.sid, {"latency_median_s": 0.8, "barge_ins": 1})
    voice = voice_client.app.state.voice  # type: ignore[attr-defined]
    path = voice.recording_path(call.sid)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"OggS-test")
    call.end()
    assert call.get("/voice-stats") == {"latency_median_s": 0.8, "barge_ins": 1}
    resp = voice_client.get(f"/api/sessions/{call.sid}/recording")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/ogg"
    assert resp.content == b"OggS-test"


def test_no_stats_or_recording_is_a_404(voice_client: TestClient) -> None:
    call = Call(voice_client, CASE_A, voice=True).start().end()
    call.get("/voice-stats", expect=404)
    call.get("/recording", expect=404)
