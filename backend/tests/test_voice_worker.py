"""The voice worker's pieces that run without LiveKit or Gemini, and the plugin internals it
depends on."""

import inspect
from types import SimpleNamespace

import pytest
from livekit.agents import llm
from livekit.plugins.google.realtime import realtime_api

from app.engine.events import Channel, Speaker, Utterance
from app.voice.config import VoiceConfigError, voice_from_env
from app.voice.ledger import VoiceStats
from app.voice.worker import SimRealtimeSession, live_model, seed_history, session_for_job
from tests.conftest import VOICE_ENV

HOOKS = (
    "_emit_error",
    "_handle_go_away",
    "_report_connection_acquired",
    "_mark_current_generation_done",
)


def test_the_plugin_still_has_what_the_worker_hooks() -> None:
    """The fresh-session reconnect overrides private methods of livekit-plugins-google. If an
    upgrade renames or reshapes them, this fails instead of reconnects quietly resuming."""
    base = realtime_api.RealtimeSession
    for name in HOOKS:
        ours = inspect.signature(getattr(SimRealtimeSession, name)).parameters
        theirs = inspect.signature(getattr(base, name)).parameters
        assert list(ours) == list(theirs), name
    assert callable(base._send_client_event)
    init = inspect.getsource(base.__init__)
    main = inspect.getsource(base._main_task)
    for attr in ("_fresh_session_needed", "_pending_chat_ctx", "_current_generation"):
        assert f"self.{attr}" in init, attr
    # The connect loop is what reads the two flags the worker sets.
    assert "if self._fresh_session_needed:" in main
    assert "self._pending_chat_ctx = self._pending_chat_ctx, None" in main
    fields = realtime_api._ResponseGeneration.__dataclass_fields__
    assert {"response_id", "output_text", "_done"} <= set(fields)


def test_a_fresh_start_replays_the_call_as_heard() -> None:
    history = llm.ChatContext.empty()
    history.add_message(role="system", content="You are the customer.")
    history.add_message(role="user", content="Shall I go ahead and enroll you?")
    history.add_message(role="assistant", content="Well, yes,", interrupted=True)
    history.add_message(role="assistant", content="")
    fake = SimpleNamespace(
        _sim=SimpleNamespace(heard_history=lambda: history),
        _fresh_session_needed=False,
        _pending_chat_ctx=None,
    )
    SimRealtimeSession._fresh_start(fake)  # type: ignore[arg-type]
    assert fake._fresh_session_needed is True
    replay = [(m.role, m.text_content) for m in fake._pending_chat_ctx.messages()]
    assert replay == [("user", "Shall I go ahead and enroll you?"), ("assistant", "Well, yes,")]
    assert len(history.items) == 4  # the session's own history is untouched


def test_live_model_settings() -> None:
    config = voice_from_env(VOICE_ENV | {"SIM_VOICE_SILENCE_MS": "900"})
    assert config is not None
    stats = VoiceStats(model="m", silence_ms=900, price_in_per_min=0, price_out_per_min=0)
    model = live_model(config, "Aoede", llm.ChatContext.empty, stats)
    opts = model._opts
    assert (opts.model, opts.voice, opts.api_version) == ("test-live-model", "Aoede", "v1beta")
    detection = opts.realtime_input_config.automatic_activity_detection  # type: ignore[union-attr]
    assert detection is not None
    assert detection.silence_duration_ms == 900
    assert opts.context_window_compression.sliding_window is not None  # type: ignore[union-attr]
    assert opts.input_audio_transcription is not None
    assert opts.output_audio_transcription is not None
    assert model.stats is stats


def test_a_dispatch_names_its_session_twice() -> None:
    assert session_for_job('{"session_id": "abc123"}', "call-abc123") == "abc123"
    for metadata, room in [
        ('{"session_id": "abc123"}', "call-zzz999"),
        ("{}", "call-abc123"),
        ("", "call-abc123"),
        ('{"session_id": "abc123"}', "abc123"),
        ("not json", "call-abc123"),
        ('["abc123"]', "call-abc123"),
    ]:
        with pytest.raises(VoiceConfigError):
            session_for_job(metadata, room)


def utt(n: int, speaker: Speaker, heard: str, t_ms: int) -> Utterance:
    return Utterance(
        utt_id=f"u{n:04d}",
        session_id="s1",
        speaker=speaker,
        channel=Channel.VOICE,
        text=heard or "Yes, go ahead.",
        heard_text=heard,
        t_ms=t_ms,
        interrupted=not heard,
    )


def test_a_worker_joining_mid_call_starts_from_the_call_as_heard() -> None:
    ctx, ids = seed_history(
        [
            utt(2, Speaker.CUSTOMER, "Hi, I have some questions.", 3000),
            utt(1, Speaker.LEARNER, "Thanks for calling.", 1000),
            utt(3, Speaker.CUSTOMER, "", 5000),  # cut off before any of it played
            utt(4, Speaker.LEARNER, "Sure, go ahead.", 4000),
        ]
    )
    assert [(m.role, m.text_content) for m in ctx.messages()] == [
        ("user", "Thanks for calling."),
        ("assistant", "Hi, I have some questions."),
        ("user", "Sure, go ahead."),
    ]
    assert ids == {"seed-u0001", "seed-u0002", "seed-u0004"}
