"""The voice worker: plays the customer on voice calls, on Gemini Live through LiveKit.

Run it beside the API with the same environment (the two share the SQLite file):

    uv run python -m app.voice.worker start

(``dev`` instead of ``start`` reloads on code changes.) The browser's join token asks LiveKit to
dispatch this worker to the call's room (see ``tokens``), so it only joins rooms it was sent to.

On each call it:

- plays the customer from the same prompt a typed call uses, refusing a session pinned to
  another model or content version;
- writes each line to the session log as it's spoken, in spoken order, with what the agent
  actually heard of the customer's lines (``ledger``);
- tells the model what the agent didn't hear after they talk over the customer;
- reconnects to Gemini with a fresh session and the heard-only history instead of resuming,
  since the spike saw resumes fail (docs/voice-spike.md);
- goes quiet and deaf while the agent has the customer on hold;
- has the customer check in after a long silence, the way a caller would;
- records the call and keeps latency, token and cost figures (``voice_stats``).
"""

import asyncio
import contextlib
import json
import logging
import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import cache, partial
from pathlib import Path
from typing import Any, TypeVar

from google.genai import types
from livekit import rtc
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, cli, llm
from livekit.agents.metrics import RealtimeModelMetrics
from livekit.agents.voice import room_io
from livekit.agents.voice.events import (
    CloseEvent,
    ConversationItemAddedEvent,
    ErrorEvent,
    UserInputTranscribedEvent,
)
from livekit.agents.voice.recorder_io import RecorderIO
from livekit.plugins.google.realtime.realtime_api import RealtimeModel, RealtimeSession

from app.content.loader import ContentBundle, load_bundle
from app.engine.events import Channel, Speaker, Utterance, UtteranceIn
from app.engine.session import Conflict, SessionError, SessionService
from app.engine.store import DB_PATH_ENV, DEFAULT_DB_PATH, SessionRow, SessionStatus, Store
from app.voice.config import (
    AGENT_NAME,
    DEBUG_DROPS_ENV,
    DROP_MODEL_RPC,
    LEARNER_IDENTITY,
    MODEL_ENV,
    VOICES,
    WRAP_UP_RPC,
    VoiceConfig,
    VoiceConfigError,
    recording_file,
    voice_from_env,
)
from app.voice.ledger import (
    NUDGE,
    Ledger,
    SpeechTracker,
    VoiceStats,
    heard_note,
    hold_note,
    level_db,
)
from app.voice.tokens import DEPARTURE_TIMEOUT_S

log = logging.getLogger("sim.voice")

T = TypeVar("T")

TEMPERATURE = 0.8
POLL_S = 0.25
"""How often the worker checks the session for a hold or the end of the call."""
STATS_EVERY_S = 30.0
MAX_RESTARTS = 3
"""Times the customer's session is rebuilt after it dies, per call."""
WRITE_ATTEMPTS = 3
DRAIN_S = 5.0
"""Longest the worker waits for pending lines to be written when the call wraps up."""
WRAP_UP_GRACE_S = 15.0
"""A call still going this long after the call screen wrapped it up didn't end (the end request
failed), so the customer carries on as before."""
MAX_TURN_S = 60.0
"""A learner line's start is taken from their audio only if it's this close to the
transcript; past that the speech run belongs to something else."""
LEARNER_SAMPLE_RATE = 16000
HOLD_SILENCE_S = 0.8
"""Quiet played to Gemini past its end-of-speech window when a hold starts (see
``_end_turn_for_hold``)."""
SILENCE_FRAME = LEARNER_SAMPLE_RATE // 10


class SimRealtimeSession(RealtimeSession):
    """A Gemini session that reconnects fresh, from what the agent actually heard.

    The plugin resumes a dropped session from a server handle and treats a failed resume as
    fatal; the spike saw resumes fail with 1008 ("already connected"). This starts a new session
    instead and replays the call as heard: ``AgentSession.history`` holds the played part of an
    interrupted line, while the plugin's own copy holds all of it. It also keeps the full text
    of each line, for the log. These hooks are private methods of livekit-plugins-google 1.8.11;
    a test fails if an upgrade moves them."""

    def __init__(self, model: "SimRealtimeModel") -> None:
        super().__init__(model)
        self._sim = model
        self._generated: dict[str, str] = {}
        self.on("metrics_collected", self._on_metrics)

    def _fresh_start(self) -> None:
        heard = self._sim.heard_history().copy(
            exclude_instructions=True,
            exclude_empty_message=True,
            exclude_handoff=True,
            exclude_config_update=True,
        )
        # A line cut off before any of it played was never said.
        self._fresh_session_needed = True
        self._pending_chat_ctx = llm.ChatContext(
            [
                item
                for item in heard.items
                if not (item.type == "message" and not (item.text_content or "").strip())
            ]
        )

    def _emit_error(self, error: Exception, recoverable: bool) -> None:
        if recoverable:
            self._fresh_start()
        super()._emit_error(error, recoverable)

    def _handle_go_away(self, go_away: types.LiveServerGoAway) -> None:
        self._sim.stats.go_aways += 1
        self._fresh_start()
        super()._handle_go_away(go_away)

    def _report_connection_acquired(self, acquire_time: float) -> None:
        self._sim.stats.connects += 1
        super()._report_connection_acquired(acquire_time)

    def _mark_current_generation_done(self) -> None:
        gen = self._current_generation
        if gen is not None and not gen._done and gen.output_text:
            self._generated[gen.response_id] = gen.output_text
        super()._mark_current_generation_done()

    def generated_text(self, item_id: str) -> str | None:
        """Everything Gemini generated for a customer line, heard or not, so far."""
        text = self._generated.pop(item_id, None)
        if text is not None:
            return text
        gen = self._current_generation
        return gen.output_text if gen is not None and gen.response_id == item_id else None

    def send_note(self, text: str) -> None:
        """Context for the model that starts no reply."""
        self._send_client_event(
            types.LiveClientContent(
                turns=[types.Content(role="user", parts=[types.Part(text=text)])],
                turn_complete=False,
            )
        )

    def _on_metrics(self, m: RealtimeModelMetrics) -> None:
        i, o = m.input_token_details, m.output_token_details
        self._sim.stats.usage(
            input_audio=i.audio_tokens,
            input_text=i.text_tokens,
            cached=i.cached_tokens,
            output_audio=o.audio_tokens,
            output_text=o.text_tokens,
        )


class SimRealtimeModel(RealtimeModel):
    def __init__(
        self, *, heard_history: Callable[[], llm.ChatContext], stats: VoiceStats, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self.heard_history = heard_history
        self.stats = stats
        self.current: SimRealtimeSession | None = None

    def session(self, *, turn_detection_disabled: bool = False) -> SimRealtimeSession:
        sess = SimRealtimeSession(self)
        self._sessions.add(sess)
        self.current = sess
        return sess


def live_model(
    config: VoiceConfig,
    voice: str,
    heard_history: Callable[[], llm.ChatContext],
    stats: VoiceStats,
) -> SimRealtimeModel:
    """Settings from the spike (spike/voice/live_settings.py), with the M4 silence window."""
    return SimRealtimeModel(
        heard_history=heard_history,
        stats=stats,
        model=config.model,
        api_key=config.api_key,
        # google-genai 3.x defaults to API v1, where the Live endpoint returns HTTP 404.
        api_version="v1beta",
        voice=voice,
        temperature=TEMPERATURE,
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_HIGH,
                silence_duration_ms=config.silence_ms,
            )
        ),
        context_window_compression=types.ContextWindowCompressionConfig(
            sliding_window=types.SlidingWindow()
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )


def session_for_job(metadata: str, room: str) -> str:
    """The session a dispatch is for. The token puts it in both the dispatch metadata and the
    room name; they must agree."""
    try:
        meta = json.loads(metadata or "{}")
    except json.JSONDecodeError as e:
        raise VoiceConfigError(f"dispatch metadata isn't JSON: {metadata!r}") from e
    from_meta = meta.get("session_id") if isinstance(meta, dict) else None
    from_room = VoiceConfig.session_for(room)
    if not from_meta or from_meta != from_room:
        raise VoiceConfigError(f"room {room!r} and dispatch metadata {metadata!r} disagree")
    return str(from_meta)


def seed_history(utterances: list[Utterance]) -> tuple[llm.ChatContext, set[str]]:
    """The call so far, as heard, for a worker that joins a call already under way."""
    ctx = llm.ChatContext.empty()
    ids: set[str] = set()
    for u in sorted(utterances, key=lambda u: (u.t_ms, u.utt_id)):
        if not u.heard_text.strip():
            continue
        item_id = f"seed-{u.utt_id}"
        role: llm.ChatRole = "user" if u.speaker is Speaker.LEARNER else "assistant"
        ctx.add_message(role=role, content=u.heard_text, id=item_id)
        ids.add(item_id)
    return ctx, ids


@cache
def _content() -> ContentBundle:
    return load_bundle()


class VoiceCall:
    """One call: the customer's AgentSession and everything around it."""

    def __init__(self, ctx: JobContext, config: VoiceConfig, session_id: str) -> None:
        self.ctx = ctx
        self.config = config
        self.session_id = session_id
        self.store = Store(os.environ.get(DB_PATH_ENV, DEFAULT_DB_PATH))
        self.service = SessionService(self.store, _content(), voice_actor=config.actor)
        # One thread for the database: the store's connection is shared, so its calls queue.
        self._db = ThreadPoolExecutor(max_workers=1, thread_name_prefix="voice-db")
        self.stats = VoiceStats(
            model=config.model,
            silence_ms=config.silence_ms,
            price_in_per_min=config.price_in_per_min,
            price_out_per_min=config.price_out_per_min,
        )
        self.tracker = SpeechTracker(merge_gap_s=config.silence_ms / 1000)
        self.ledger = Ledger(time.time(), start_of=self._speech_start)
        self.queue: asyncio.Queue[UtteranceIn] = asyncio.Queue()
        self.session: AgentSession[None] | None = None
        self.model: SimRealtimeModel | None = None
        self.recorder: RecorderIO | None = None
        self.voice = ""
        self.instructions = ""
        self.seeded: set[str] = set()
        self.call_started_at = time.time()
        self.on_hold = False
        self.hold_started_at = 0.0
        self.hold_ended_at = 0.0
        self.cut_by_hold: str | None = None
        """What the agent heard of a customer line the hold cut off."""
        self.wrapping_up = False
        self.wrap_up_at = 0.0
        self.finishing = False
        self.nudge_level = 0
        self.last_activity = time.time()
        self.last_customer_start: float | None = None
        self.learner_left_at: float | None = None
        self.stats_written_at = time.time()
        self._tasks: set[asyncio.Task[None]] = set()
        self._tracker_task: asyncio.Task[None] | None = None

    async def db(self, fn: Callable[..., T], *args: Any) -> T:
        return await asyncio.get_running_loop().run_in_executor(self._db, partial(fn, *args))

    # Setup

    async def run(self) -> None:
        row = await self.db(self.service.require_active, self.session_id)
        if row.channel is not Channel.VOICE:
            raise Conflict(f"session {self.session_id} is a typed call")
        prompt = await self.db(self.service.customer_prompt, row)
        if row.started_at is None:
            raise Conflict(f"session {self.session_id} hasn't started")
        self.call_started_at = row.started_at.timestamp()
        self.ledger = Ledger(self.call_started_at, start_of=self._speech_start)
        self.voice = VOICES[row.voice]
        self.instructions = prompt.text
        seed, self.seeded = seed_history(await self.db(self.service.transcript, self.session_id))
        self.ledger.skip(self.seeded)
        self.on_hold = await self.db(self.service.on_hold, self.session_id)
        if self.on_hold:
            self.hold_started_at = time.time()
        # A worker sent back to the call adds to what the one before it measured.
        prior = await self.db(self.store.get_voice_stats, self.session_id)
        if prior:
            self.stats.carry(prior)

        await self.ctx.connect()
        room = self.ctx.room
        if any(
            p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT
            for p in room.remote_participants.values()
        ):
            raise Conflict(f"session {self.session_id} already has a customer on the call")
        room.on("participant_connected", self._on_joined)
        room.on("participant_disconnected", self._on_left)
        room.local_participant.register_rpc_method(WRAP_UP_RPC, self._wrap_up)
        if os.environ.get(DEBUG_DROPS_ENV) == "1":
            room.local_participant.register_rpc_method(DROP_MODEL_RPC, self._drop_model)
        self.ctx.add_shutdown_callback(self._on_shutdown)

        await self._start_session(seed)
        for loop in (self._writer, self._poll, self._nudges):
            self._spawn(loop())
        if any(p.identity == LEARNER_IDENTITY for p in room.remote_participants.values()):
            self._track_learner()
        else:
            self.learner_left_at = time.time()
        log.info("voice call %s: customer on %s (%s)", self.session_id, self.voice, row.voice)

    async def _start_session(self, seed: llm.ChatContext) -> None:
        self.model = live_model(self.config, self.voice, self._heard_history, self.stats)
        session: AgentSession[None] = AgentSession(llm=self.model)
        session.on("conversation_item_added", self._on_item)
        session.on("user_input_transcribed", self._on_transcribed)
        session.on("error", self._on_error)
        session.on("close", self._on_close)
        await session.start(
            agent=Agent(instructions=self.instructions, chat_ctx=seed),
            room=self.ctx.room,
            room_options=room_io.RoomOptions(
                participant_identity=LEARNER_IDENTITY,
                close_on_disconnect=False,
                text_input=False,
            ),
            record=False,
        )
        self.session = session
        if self.on_hold:
            self._mute(True)
        await self._record(session)

    async def _record(self, session: AgentSession[None]) -> None:
        if session.input.audio is None or session.output.audio is None:
            return
        path = self._recording_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            recorder = RecorderIO(agent_session=session)
            session.input.audio = recorder.record_input(session.input.audio)
            session.output.audio = recorder.record_output(session.output.audio)
            await recorder.start(output_path=path)
        except Exception as e:
            log.exception("voice call %s: recording failed", self.session_id)
            self.stats.error(f"recording failed: {e!r}")
            return
        self.recorder = recorder
        self.stats.recording = path.name

    def _recording_path(self) -> Path:
        """The call's file, or a numbered part if the customer's session was rebuilt."""
        n = 1
        while (path := recording_file(self.config.recordings_dir, self.session_id, n)).exists():
            n += 1
        return path

    def _spawn(self, coro: Any) -> asyncio.Task[None]:
        task: asyncio.Task[None] = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _heard_history(self) -> llm.ChatContext:
        return self.session.history if self.session else llm.ChatContext.empty()

    def _speech_start(self, first_seen: float) -> float:
        return self.tracker.speech_start(first_seen, MAX_TURN_S)

    # Learner presence and speech timing

    def _on_joined(self, participant: rtc.RemoteParticipant) -> None:
        if participant.identity == LEARNER_IDENTITY:
            self.learner_left_at = None
            self._track_learner()

    def _on_left(self, participant: rtc.RemoteParticipant) -> None:
        if participant.identity == LEARNER_IDENTITY:
            self.learner_left_at = time.time()
            if self._tracker_task:
                self._tracker_task.cancel()

    def _track_learner(self) -> None:
        if self._tracker_task:
            self._tracker_task.cancel()
        self._tracker_task = self._spawn(self._learner_audio())

    async def _learner_audio(self) -> None:
        """Feeds the speech tracker from the learner's mic, on a stream of its own."""
        participant = await self.ctx.wait_for_participant(identity=LEARNER_IDENTITY)
        stream = rtc.AudioStream.from_participant(
            participant=participant,
            track_source=rtc.TrackSource.SOURCE_MICROPHONE,
            sample_rate=LEARNER_SAMPLE_RATE,
            num_channels=1,
        )
        try:
            async for event in stream:
                frame = event.frame
                self.tracker.frame(
                    time.time(),
                    frame.samples_per_channel / frame.sample_rate,
                    level_db(frame.data.tolist()),
                )
        finally:
            await stream.aclose()

    # Session events

    def _on_transcribed(self, ev: UserInputTranscribedEvent) -> None:
        if ev.item_id:
            self.ledger.learner_heard(ev.item_id, ev.transcript, ev.created_at)

    def _on_item(self, ev: ConversationItemAddedEvent) -> None:
        item = ev.item
        if not isinstance(item, llm.ChatMessage) or item.id in self.seeded:
            return
        now = time.time()
        text = item.text_content or ""
        if item.role == "user":
            started = self._speech_start(self.ledger.first_seen(item.id) or item.created_at)
            self.nudge_level = 0
            self.last_activity = now
            self._queue(self.ledger.learner_final(item.id, text, started, now))
        elif item.role == "assistant":
            started = item.metrics.get("started_speaking_at") or item.created_at
            ended = item.metrics.get("stopped_speaking_at")
            rt = self.model.current if self.model else None
            generated = rt.generated_text(item.id) if rt else None
            heard, interrupted = text, item.interrupted
            if self._began_on_hold(item.created_at):
                # Her answer to what the agent said just before the hold: none of it played.
                heard, interrupted, generated = "", True, generated or text
                if self.cut_by_hold is None:
                    self.cut_by_hold = ""
            else:
                self.stats.answered(self.tracker, started, self.last_customer_start)
                self.last_customer_start = started
                self.last_activity = ended or now
                if item.interrupted and not (self.wrapping_up or self.finishing):
                    if self.on_hold:
                        # Not a barge-in: the note at the end of the hold says what she got out.
                        self.cut_by_hold = text
                    else:
                        self.stats.barge_ins += 1
                        if rt:
                            rt.send_note(heard_note(text))
            self._queue(
                self.ledger.customer_line(
                    item.id,
                    heard=heard,
                    generated=generated,
                    started_at=started,
                    ended_at=ended,
                    interrupted=interrupted,
                    now=now,
                )
            )

    def _began_on_hold(self, t: float) -> bool:
        return self.hold_started_at <= t and (self.on_hold or t < self.hold_ended_at)

    def _on_error(self, ev: ErrorEvent) -> None:
        recoverable = getattr(ev.error, "recoverable", False)
        self.stats.error(f"{'recoverable' if recoverable else 'fatal'}: {ev.error!r}")

    def _on_close(self, ev: CloseEvent) -> None:
        if self.finishing:
            return
        log.warning("voice call %s: customer session closed (%s)", self.session_id, ev.reason)
        self._spawn(self._restart())

    async def _restart(self) -> None:
        """Rebuilds the customer's session from the call as heard."""
        old = self.session
        seed = old.history.copy() if old else llm.ChatContext.empty()
        self.stats.restarts += 1
        if self.stats.restarts > MAX_RESTARTS:
            await self.finish("the customer's session kept failing")
            return
        if self.recorder:
            with contextlib.suppress(Exception):
                await self.recorder.aclose()
            self.recorder = None
        self.session = None
        try:
            await self._start_session(seed)
        except Exception as e:
            log.exception("voice call %s: restart failed", self.session_id)
            self.stats.error(f"restart failed: {e!r}")
            await self.finish("the customer's session couldn't restart")

    # Writing lines

    def _queue(self, bodies: list[UtteranceIn]) -> None:
        for body in bodies:
            self.queue.put_nowait(body)

    async def _writer(self) -> None:
        while True:
            body = await self.queue.get()
            try:
                await self._write(body)
            finally:
                self.queue.task_done()

    async def _write(self, body: UtteranceIn) -> None:
        for attempt in range(WRITE_ATTEMPTS):
            try:
                await self.db(self.service.add_voice_utterance, self.session_id, body)
                return
            except SessionError as e:
                # The call ended under a late line, or the line was refused: retrying won't help.
                log.info("voice call %s: line not saved: %s", self.session_id, e)
                return
            except Exception as e:
                if attempt == WRITE_ATTEMPTS - 1:
                    log.exception("voice call %s: lost a line", self.session_id)
                    self.stats.error(f"lost a line: {e!r}")
                    return
                await asyncio.sleep(0.5 * 2**attempt)

    async def _drain(self) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.queue.join(), DRAIN_S)

    async def _wrap_up(self, data: rtc.RpcInvocationData) -> str:
        """Called by the call screen just before it ends the call: saves what's pending."""
        self.wrapping_up = True
        self.wrap_up_at = time.time()
        if self.session:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.session.interrupt(force=True), 2)
        self._queue(self.ledger.flush(time.time()))
        await self._drain()
        return json.dumps({"pending": self.queue.qsize()})

    async def _drop_model(self, data: rtc.RpcInvocationData) -> str:
        """Testing only: aborts the Gemini socket the way a network drop would."""
        rt = self.model.current if self.model else None
        live = rt._active_session if rt else None
        transport = getattr(getattr(live, "_ws", None), "transport", None)
        if transport is None:
            return json.dumps({"dropped": False})
        transport.abort()
        log.warning("voice call %s: dropped the model connection on request", self.session_id)
        return json.dumps({"dropped": True})

    # Hold, silence and the end of the call

    def _status(self) -> tuple[SessionRow, bool]:
        return self.service.get(self.session_id), self.service.on_hold(self.session_id)

    async def _poll(self) -> None:
        while not self.finishing:
            try:
                row, on_hold = await self.db(self._status)
            except Exception:
                log.exception("voice call %s: couldn't read the session", self.session_id)
                await asyncio.sleep(1)
                continue
            now = time.time()
            if row.status is not SessionStatus.ACTIVE:
                await self.finish("the call ended", delete_room=True)
                return
            if self.wrapping_up and now - self.wrap_up_at > WRAP_UP_GRACE_S:
                log.warning("voice call %s: didn't end after a wrap-up", self.session_id)
                self.wrapping_up = False
            if on_hold != self.on_hold:
                self._hold(on_hold, now)
            self._queue(self.ledger.due(now))
            if self.learner_left_at and now - self.learner_left_at > DEPARTURE_TIMEOUT_S:
                await self.finish("the learner left")
                return
            if now - self.stats_written_at >= STATS_EVERY_S:
                await self._write_stats()
            await asyncio.sleep(POLL_S)

    def _mute(self, muted: bool) -> None:
        if self.session:
            self.session.input.set_audio_enabled(not muted)
            self.session.output.set_audio_enabled(not muted)

    def _hold(self, on: bool, now: float) -> None:
        self.on_hold = on
        if on:
            self.stats.holds += 1
            self.hold_started_at = now
            self.cut_by_hold = None
            if self.session:
                self.session.interrupt(force=True)
            self._mute(True)
            self._spawn(self._end_turn_for_hold())
        else:
            self._mute(False)
            self.hold_ended_at = now
            self.last_activity = now
            if self.model and self.model.current:
                self.model.current.send_note(
                    hold_note(now - self.hold_started_at, self.cut_by_hold)
                )
            self.cut_by_hold = None

    async def _end_turn_for_hold(self) -> None:
        """Gemini ends the agent's turn, and only then transcribes it, after ``silence_ms`` of
        quiet. The hold stops the agent's audio at once, so a line said just before the hold
        would stay open and run together with the first line after it. The worker plays Gemini
        that quiet itself. Whatever the customer answers during the hold is never heard."""
        frame = rtc.AudioFrame(bytes(2 * SILENCE_FRAME), LEARNER_SAMPLE_RATE, 1, SILENCE_FRAME)
        until = time.time() + self.config.silence_ms / 1000 + HOLD_SILENCE_S
        while self.on_hold and time.time() < until:
            rt = self.model.current if self.model else None
            if rt:
                rt.push_audio(frame)
            await asyncio.sleep(SILENCE_FRAME / LEARNER_SAMPLE_RATE)

    async def _nudges(self) -> None:
        """After a long silence the customer checks the agent is still there. Each nudge
        without the agent speaking waits twice as long as the one before."""
        if self.config.nudge_s <= 0:
            return
        while not self.finishing:
            await asyncio.sleep(1)
            session = self.session
            if (
                session is None
                or self.on_hold
                or self.wrapping_up
                or self.learner_left_at is not None
                or session.agent_state not in ("listening", "idle")
                or session.user_state == "speaking"
            ):
                continue
            now = time.time()
            quiet_since = max(self.last_activity, self.tracker.last_voice or 0.0)
            if now - quiet_since < self.config.nudge_s * 2**self.nudge_level:
                continue
            session.generate_reply(instructions=NUDGE)
            self.stats.nudges += 1
            self.nudge_level += 1
            self.last_activity = now

    async def _write_stats(self, *, final: bool = False) -> None:
        self.stats_written_at = time.time()
        data = self.stats.to_json(time.time() - self.call_started_at, final=final)
        try:
            await self.db(self.store.put_voice_stats, self.session_id, data)
        except Exception:
            log.exception("voice call %s: couldn't save stats", self.session_id)

    async def _on_shutdown(self, reason: str) -> None:
        await self.finish(f"job shut down: {reason}")

    async def finish(self, reason: str, *, delete_room: bool = False) -> None:
        if self.finishing:
            return
        self.finishing = True
        log.info("voice call %s: finishing (%s)", self.session_id, reason)
        if self.session:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.session.interrupt(force=True), 2)
        self._queue(self.ledger.flush(time.time()))
        await self._drain()
        await self._close(reason, delete_room=delete_room, save_stats=True)

    async def abandon(self, reason: str) -> None:
        """Cleans up after a call that failed to start."""
        self.finishing = True
        await self._close(reason)

    async def _close(
        self, reason: str, *, delete_room: bool = False, save_stats: bool = False
    ) -> None:
        current = asyncio.current_task()
        for task in list(self._tasks):
            if task is not current:
                task.cancel()
        if self.session:
            with contextlib.suppress(Exception):
                await self.session.aclose()
        if self.recorder:
            with contextlib.suppress(Exception):
                await self.recorder.aclose()
        if save_stats:
            await self._write_stats(final=True)
        if delete_room:
            with contextlib.suppress(Exception):
                await self.ctx.delete_room()
        # Job processes are reused, so nothing of this call may outlive it.
        self._db.shutdown(wait=True)
        self.store.close()
        self.ctx.shutdown(reason)


IDLE_PROCESSES = 1
"""Job processes kept warm. LiveKit's default is one per CPU, each holding the agent framework
and a Gemini client in memory; one keeps the next call's start fast, and more start on demand."""

server = AgentServer(num_idle_processes=IDLE_PROCESSES)


@server.rtc_session(agent_name=AGENT_NAME)
async def customer(ctx: JobContext) -> None:
    config = voice_from_env()
    if config is None:
        raise VoiceConfigError(f"the voice worker needs {MODEL_ENV}")
    call = VoiceCall(ctx, config, session_for_job(ctx.job.metadata, ctx.job.room.name))
    try:
        await call.run()
    except Exception:
        log.exception("voice call %s: couldn't start", call.session_id)
        await call.abandon("couldn't start the call")
        raise


def main() -> None:
    cli.run_app(server)


if __name__ == "__main__":
    main()
