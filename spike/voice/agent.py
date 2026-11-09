"""Throwaway M4 spike worker: Case A's customer on Gemini Live through LiveKit Agents.

Joins every new room on the local LiveKit server, plays Loretta Banks (scenario-101) with the
prompt from backend/app/actor/prompt.py, and appends timestamped events to
runs/<room>-agent.jsonl so caller.py can line them up with what it heard.

A data packet {"cmd": "kill_model"} on topic "spike" aborts the Gemini WebSocket's TCP
transport, the closest thing to a network drop we can force from inside the process.

Env: GEMINI_API_KEY, LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, SIM_LIVE_MODEL (required,
never defaulted), SPIKE_VOICE (default Aoede), SPIKE_RESUMPTION (1 = Gemini session resumption
handles, the default; 0 = reconnect by replaying LiveKit's local chat history), SPIKE_SILENCE_MS
(Gemini end-of-speech silence window; unset = Google's default), SPIKE_FIRST_RETRY_S (delay before
the first reconnect attempt; the plugin's default is 0.1).
"""

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

from google.genai import types
from livekit import rtc
from livekit.agents import (
    NOT_GIVEN,
    Agent,
    AgentServer,
    AgentSession,
    APIConnectOptions,
    JobContext,
    cli,
)
from livekit.plugins import google

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "backend"))

from app.actor.prompt import build_customer_prompt, prompt_problems  # noqa: E402
from app.content.loader import load_bundle  # noqa: E402

SCENARIO_ID = "scenario-101"
MOOD = "worried"
RUNS = HERE / "runs"
log = logging.getLogger("spike.agent")


def customer_prompt() -> str:
    bundle = load_bundle()
    prompt = build_customer_prompt(bundle.scenarios[SCENARIO_ID], bundle.frame, MOOD)
    problems = prompt_problems(prompt, bundle)
    if problems:
        raise SystemExit(f"prompt guard failed: {problems}")
    return prompt.text


class EventLog:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._f = path.open("a", buffering=1)

    def __call__(self, kind: str, **fields: Any) -> None:
        self._f.write(json.dumps({"t": time.time(), "kind": kind, **fields}, default=str) + "\n")


class FirstRetryAfter(APIConnectOptions):
    """The plugin retries a dropped Gemini socket after 0.1 s, which can beat Google noticing
    the old socket is gone; the resumed connect then fails with 1008 and the session dies."""

    first_retry_s: float = 0.1

    def _interval_for_retry(self, num_retries: int) -> float:
        return self.first_retry_s if num_retries == 0 else self.retry_interval


MODELS = {"m38": "gemini-3.8-live", "m31": "gemini-3.1-flash-live-preview"}


def room_overrides(room: str) -> dict[str, str]:
    """Per-room settings from dash-separated tokens in the room name, so configs can run side by
    side on one worker: m38/m31 (model), res0/res1, vad<ms>, fr<tenths of a second>."""
    out: dict[str, str] = {}
    for tok in room.split("-"):
        if tok in MODELS:
            out["model"] = MODELS[tok]
        elif tok in ("res0", "res1"):
            out["resumption"] = tok[-1]
        elif tok.startswith("vad") and tok[3:].isdigit():
            out["silence_ms"] = tok[3:]
        elif tok.startswith("fr") and tok[2:].isdigit():
            out["first_retry_s"] = str(int(tok[2:]) / 10)
    return out


server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    opts = room_overrides(ctx.room.name)
    model = opts.get("model") or os.environ["SIM_LIVE_MODEL"]
    resumption = opts.get("resumption", os.environ.get("SPIKE_RESUMPTION", "1")) == "1"
    ev = EventLog(RUNS / f"{ctx.room.name}-agent.jsonl")
    ev("start", model=model, resumption=resumption, scenario=SCENARIO_ID, mood=MOOD, overrides=opts)
    instructions = customer_prompt()
    vad: types.RealtimeInputConfig | None = None
    if silence_ms := opts.get("silence_ms") or os.environ.get("SPIKE_SILENCE_MS"):
        # Gemini's server-side end-of-speech detection; unset leaves Google's defaults.
        vad = types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_HIGH,
                silence_duration_ms=int(silence_ms),
            )
        )
        ev("vad", silence_ms=int(silence_ms), end_sensitivity="high")
    conn = FirstRetryAfter()
    conn.first_retry_s = float(
        opts.get("first_retry_s") or os.environ.get("SPIKE_FIRST_RETRY_S", "0.1")
    )
    ev("retry", first_retry_s=conn.first_retry_s, max_retry=conn.max_retry)

    llm = google.realtime.RealtimeModel(
        model=model,
        # google-genai 3.x defaults to API v1, where the Live endpoint returns HTTP 404.
        api_version="v1beta",
        voice=os.environ.get("SPIKE_VOICE", "Aoede"),
        temperature=0.8,
        session_resumption=types.SessionResumptionConfig() if resumption else NOT_GIVEN,
        context_window_compression=types.ContextWindowCompressionConfig(
            sliding_window=types.SlidingWindow()
        ),
        realtime_input_config=vad if vad else NOT_GIVEN,
        conn_options=conn,
    )
    session = AgentSession(llm=llm)

    session.on("agent_state_changed", lambda e: ev("agent_state", old=e.old_state, new=e.new_state))
    session.on("user_state_changed", lambda e: ev("user_state", old=e.old_state, new=e.new_state))
    session.on(
        "user_input_transcribed",
        lambda e: ev("user_said" if e.is_final else "user_interim", text=e.transcript),
    )
    session.on(
        "conversation_item_added",
        lambda e: ev(
            "item",
            role=getattr(e.item, "role", None),
            text=getattr(e.item, "text_content", None),
            interrupted=getattr(e.item, "interrupted", None),
        ),
    )
    session.on("metrics_collected", lambda e: _log_metrics(ev, e.metrics))
    session.on("error", lambda e: ev("error", error=repr(e.error)))
    session.on("close", lambda e: ev("close", reason=str(getattr(e, "reason", ""))))

    await session.start(agent=Agent(instructions=instructions), room=ctx.room, record=False)
    rt = session._activity._rt_session  # spike: private handle to the Gemini session
    _instrument(rt, ev)

    @ctx.room.on("data_received")
    def _on_data(packet: rtc.DataPacket) -> None:
        if packet.topic != "spike":
            return
        cmd = json.loads(packet.data).get("cmd")
        if cmd == "kill_model":
            live = rt._active_session
            ws = getattr(live, "_ws", None)
            transport = getattr(ws, "transport", None)
            ev("kill_model", had_session=live is not None, handle=rt.session_resumption_handle)
            if not resumption:
                # The plugin always asks Gemini for resumption handles; dropping the handle makes
                # the reconnect a fresh session that LiveKit fills by replaying its chat history.
                rt._session_resumption_handle = None
                rt._resumption_chat_ctx = None
            if transport is not None:
                transport.abort()
            else:
                ev("kill_model_failed", reason="no transport on the live session")


def _log_metrics(ev: EventLog, m: Any) -> None:
    if getattr(m, "type", "") == "realtime_model_metrics":
        ev(
            "rt_metrics",
            ttft=m.ttft,
            duration=m.duration,
            cancelled=m.cancelled,
            input_tokens=m.input_tokens,
            output_tokens=m.output_tokens,
            acquire_time=m.acquire_time,
        )


def _instrument(rt: Any, ev: EventLog) -> None:
    """Log reconnects and the full text Gemini generated, to compare with what was heard."""
    acquired = rt._report_connection_acquired

    def report_acquired(dt: float, *a: Any, **k: Any) -> Any:
        ev("model_connected", connect_s=dt, handle=rt.session_resumption_handle)
        return acquired(dt, *a, **k)

    rt._report_connection_acquired = report_acquired

    handle_sc = rt._handle_server_content
    generated: list[str] = []

    def on_server_content(sc: types.LiveServerContent) -> Any:
        if sc.output_transcription and sc.output_transcription.text:
            generated.append(sc.output_transcription.text)
        if sc.interrupted:
            ev("model_interrupted", generated="".join(generated))
        if sc.turn_complete or sc.interrupted:
            if generated:
                ev(
                    "model_turn_text",
                    generated="".join(generated),
                    interrupted=bool(sc.interrupted),
                )
            generated.clear()
        return handle_sc(sc)

    rt._handle_server_content = on_server_content

    emit_error = rt._emit_error

    def on_error(error: Exception, recoverable: bool) -> Any:
        ev("model_error", error=repr(error)[:300], recoverable=recoverable)
        return emit_error(error, recoverable)

    rt._emit_error = on_error


if __name__ == "__main__":
    cli.run_app(server)
