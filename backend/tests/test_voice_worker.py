"""The voice worker's pieces that run without LiveKit or Gemini, and the plugin internals it
depends on."""

import asyncio
import dataclasses
import inspect
import json
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from livekit import api, rtc
from livekit.agents import llm
from livekit.plugins.google.realtime import realtime_api

from app.engine.events import Channel, Speaker, Utterance, UtteranceIn
from app.engine.session import Conflict
from app.engine.store import SessionStatus
from app.voice import rooms, worker
from app.voice.config import VoiceConfig, VoiceConfigError, voice_from_env
from app.voice.ledger import VoiceStats
from app.voice.worker import (
    WRAP_UP_GRACE_S,
    SimRealtimeSession,
    VoiceCall,
    live_model,
    seed_history,
    session_for_job,
)
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


# The call's own handling, without LiveKit or Gemini


def config() -> VoiceConfig:
    c = voice_from_env(VOICE_ENV)
    assert c is not None
    return c


@pytest.fixture
def call(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[VoiceCall]:
    monkeypatch.setenv("SIM_DB_PATH", str(tmp_path / "voice.db"))
    shutdowns: list[str] = []
    ctx = SimpleNamespace(shutdown=shutdowns.append, shutdowns=shutdowns)
    c = VoiceCall(ctx, config(), "s-test")  # type: ignore[arg-type]
    yield c
    c._db.shutdown()
    c.store.close()


class Notes:
    """Stands in for the Gemini session: keeps the notes and audio the worker sends."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.audio: list[rtc.AudioFrame] = []

    def send_note(self, text: str) -> None:
        self.sent.append(text)

    def push_audio(self, frame: rtc.AudioFrame) -> None:
        self.audio.append(frame)

    def generated_text(self, item_id: str) -> str | None:
        return None


def said(call: VoiceCall, role: str, text: str, item_id: str, at: float, **kw: Any) -> None:
    item = llm.ChatMessage(role=role, content=[text], id=item_id, created_at=at, **kw)  # type: ignore[arg-type]
    call._on_item(SimpleNamespace(item=item))  # type: ignore[arg-type]


def saved(call: VoiceCall) -> list[UtteranceIn]:
    out = []
    while not call.queue.empty():
        out.append(call.queue.get_nowait())
    return out


def test_a_hold_closes_the_agent_turn_and_nothing_said_on_hold_is_heard(
    call: VoiceCall, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gemini only ends the agent's turn after hearing quiet, so the hold plays it some. Then
    whatever the customer answers during the hold is saved as never heard."""
    monkeypatch.setattr(worker, "HOLD_SILENCE_S", 0.1)
    call.config = dataclasses.replace(call.config, silence_ms=200)
    rt = Notes()
    call.model = SimpleNamespace(current=rt)  # type: ignore[assignment]
    t0 = call.ledger.call_started_at

    async def run() -> None:
        call._hold(True, t0 + 2)
        # The hold cut her off mid-line: not a barge-in, and no "talked over you" note.
        said(call, "assistant", "My doctor is", "cust-1", t0 + 1.5, interrupted=True)
        # She answers what the agent said just before the hold, into the hold.
        said(call, "assistant", "Oh, that's all right.", "cust-2", t0 + 3)
        await asyncio.sleep(0.5)
        call._hold(False, t0 + 32)
        played = len(rt.audio)
        await asyncio.sleep(0.2)
        assert len(rt.audio) == played  # the quiet stops with the hold

    asyncio.run(run())
    quiet = sum(f.duration for f in rt.audio)
    assert 0.2 <= quiet <= 0.4
    assert all(not any(f.data) for f in rt.audio)
    assert call.stats.barge_ins == 0
    (note,) = rt.sent
    assert "about 30 seconds" in note
    assert 'all they heard you say was "My doctor is"' in note
    # A reply that began on hold but ended after it still never played.
    said(call, "assistant", "And another thing.", "cust-3", t0 + 20)
    lines = [(u.text, u.heard_text, u.interrupted) for u in saved(call)]
    assert lines == [
        ("My doctor is", "My doctor is", True),
        ("Oh, that's all right.", "", True),
        ("And another thing.", "", True),
    ]


def test_a_hold_note_says_when_nothing_she_said_got_through(call: VoiceCall) -> None:
    rt = Notes()
    call.model = SimpleNamespace(current=rt)  # type: ignore[assignment]
    t0 = call.ledger.call_started_at
    call.on_hold, call.hold_started_at = True, t0 + 2
    said(call, "assistant", "Oh, that's all right.", "cust-1", t0 + 3)
    call._hold(False, t0 + 12)
    (note,) = rt.sent
    assert "before they heard any of what you were saying" in note


def test_a_barge_in_outside_a_hold_still_counts_and_sends_what_was_heard(
    call: VoiceCall,
) -> None:
    rt = Notes()
    call.model = SimpleNamespace(current=rt)  # type: ignore[assignment]
    said(call, "assistant", "Well,", "cust-1", call.ledger.call_started_at + 1, interrupted=True)
    assert call.stats.barge_ins == 1
    (note,) = rt.sent
    assert "talked over you" in note


def poll_once(call: VoiceCall) -> None:
    active = (SimpleNamespace(status=SessionStatus.ACTIVE), False)
    call._status = lambda: active  # type: ignore[method-assign,assignment,return-value]

    async def run() -> None:
        call.finishing = False
        task = asyncio.create_task(call._poll())
        await asyncio.sleep(0.05)
        call.finishing = True
        await task

    asyncio.run(run())


def test_a_wrap_up_that_never_ended_the_call_wears_off(call: VoiceCall) -> None:
    call.wrapping_up, call.wrap_up_at = True, time.time() - 1
    poll_once(call)
    assert call.wrapping_up  # the end request may still be on its way
    call.wrap_up_at = time.time() - WRAP_UP_GRACE_S - 1
    poll_once(call)
    assert not call.wrapping_up  # nudges and barge-in notes are back on


def test_a_call_that_fails_to_start_leaves_nothing_running(call: VoiceCall) -> None:
    async def run() -> bool:
        task = call._spawn(asyncio.sleep(60))
        await call.abandon("couldn't start the call")
        await asyncio.sleep(0)
        return task.cancelled()

    assert asyncio.run(run())
    assert call.ctx.shutdowns == ["couldn't start the call"]  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError):
        call._db.submit(time.time)


def joined(call: VoiceCall, others: list[SimpleNamespace]) -> str:
    """Sets the call up for run() on a real voice session, in a fake room holding ``others``.
    Returns the session id."""
    row = call.service.create("scenario-101", channel=Channel.VOICE)
    call.service.start(row.session_id)
    call.session_id = row.session_id

    async def connect() -> None:
        return None

    local = SimpleNamespace(register_rpc_method=lambda name, fn: None)
    room = SimpleNamespace(
        remote_participants={p.identity: p for p in others},
        local_participant=local,
        on=lambda event, fn: None,
    )
    call.ctx = SimpleNamespace(  # type: ignore[assignment]
        connect=connect,
        room=room,
        add_shutdown_callback=lambda fn: None,
        shutdown=lambda reason: None,
    )
    call.store.put_voice_stats(
        row.session_id,
        {"final": True, "barge_ins": 2, "latencies_s": [1.2], "session_restarts": 0},
    )
    return row.session_id


def test_a_worker_sent_back_replaces_the_last_final_stats_at_once(
    call: VoiceCall, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The debrief stops waiting at the first final row, so the old worker's can't linger."""
    sid = joined(call, [])

    async def no_session(seed: llm.ChatContext) -> None:
        return None

    monkeypatch.setattr(call, "_start_session", no_session)

    async def run() -> None:
        await call.run()
        call.finishing = True
        tasks = list(call._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(run())
    stats = call.store.get_voice_stats(sid)
    assert stats is not None
    assert stats["final"] is False
    assert (stats["barge_ins"], stats["latencies_s"], stats["session_restarts"]) == (2, [1.2], 1)


def test_a_worker_refused_by_the_room_leaves_the_stats_alone(call: VoiceCall) -> None:
    agent = SimpleNamespace(identity="agent-1", kind=rtc.ParticipantKind.PARTICIPANT_KIND_AGENT)
    sid = joined(call, [agent])
    with pytest.raises(Conflict):
        asyncio.run(call.run())
    stats = call.store.get_voice_stats(sid)
    assert stats is not None
    assert stats["final"] is True


# Sending the customer back


class FakeLiveKit:
    def __init__(self, participants: list[int] | None, jobs: list[int], age_s: float) -> None:
        self.created: list[api.CreateAgentDispatchRequest] = []
        self.closed = False
        fake = self

        class Room:
            async def list_participants(
                self, req: api.ListParticipantsRequest
            ) -> api.ListParticipantsResponse:
                if participants is None:
                    raise api.TwirpError(api.TwirpErrorCode.NOT_FOUND, "no room", status=404)
                return api.ListParticipantsResponse(
                    participants=[api.ParticipantInfo(kind=k) for k in participants]  # type: ignore[arg-type]
                )

        class Dispatch:
            async def list_dispatch(self, room: str) -> list[api.AgentDispatch]:
                state = api.AgentDispatchState(
                    jobs=[api.Job(state=api.JobState(status=s)) for s in jobs],  # type: ignore[arg-type]
                    created_at=time.time_ns() - int(age_s * 1e9),
                )
                return [api.AgentDispatch(agent_name="sim-customer", room=room, state=state)]

            async def create_dispatch(self, req: api.CreateAgentDispatchRequest) -> None:
                fake.created.append(req)

        self.room = Room()
        self.agent_dispatch = Dispatch()

    async def aclose(self) -> None:
        self.closed = True


AGENT = api.ParticipantInfo.Kind.AGENT
STANDARD = api.ParticipantInfo.Kind.STANDARD


@pytest.mark.parametrize(
    ("participants", "jobs", "age_s", "sends"),
    [
        (None, [], 60, False),  # no room yet: the join token dispatches the worker
        ([STANDARD, AGENT], [api.JobStatus.JS_RUNNING], 60, False),  # the customer is there
        ([STANDARD], [api.JobStatus.JS_PENDING], 60, False),  # one is on the way
        ([STANDARD], [], 2, False),  # just dispatched, no worker has taken it yet
        ([STANDARD], [], 60, True),  # no worker ever took it (none was running)
        ([STANDARD], [api.JobStatus.JS_FAILED], 2, True),  # the worker crashed
        ([], [api.JobStatus.JS_SUCCESS], 60, True),  # it gave up after the learner left
    ],
)
def test_the_customer_is_sent_back_only_to_an_open_room_without_one(
    monkeypatch: pytest.MonkeyPatch,
    participants: list[int] | None,
    jobs: list[int],
    age_s: float,
    sends: bool,
) -> None:
    fake = FakeLiveKit(participants, jobs, age_s)
    monkeypatch.setattr(api, "LiveKitAPI", lambda *a, **kw: fake)
    assert asyncio.run(rooms.ensure_customer(config(), "abc123")) is sends
    assert fake.closed
    if sends:
        (req,) = fake.created
        assert (req.agent_name, req.room) == ("sim-customer", "call-abc123")
        assert json.loads(req.metadata) == {"session_id": "abc123"}
    else:
        assert fake.created == []
