"""Control for the spike: the worker's plain lines sent straight to Gemini Live, no LiveKit.

This is roughly the browser-direct path's network shape (one WebSocket to Google) minus the
browser. The prompt, voice, temperature, end-of-speech setting and context compression come from
live_settings.py, the same builder the worker uses. What still differs from the worker's
connection: the LiveKit plugin always asks Gemini for session resumption handles and this
doesn't, and the model comes from SIM_LIVE_MODEL alone (no room-name override).

Lines: the control can't drop the model or barge in, so it speaks only the script lines that are
plain question-and-answer turns on the worker path too (control_lines): `turn` lines and the line
of each forced-drop step, which the worker times before the drop. It leaves out the line right
after a drop ("what was my extension?", "you cut out"), which only makes sense after one and
which the worker's latency figure also leaves out, and the barge-in and probe steps. Each
printed row carries its script step number, so a control line can be matched to the same step
in a caller.py report. The run in results/direct-control.txt predates this and used a different
selection; see docs/voice-spike.md.

It times from the moment the send of the line's last voiced chunk returns to two endpoints:
  first chunk    the first audio chunk of the reply arriving, loud or not
  first audible  the first 10 ms window above caller.py's loudness threshold, assuming playback
                 starts the moment the first chunk arrives (never before its chunk arrives)
caller.py times the worker path from the last voiced sample entering the WebRTC track to the
first loud frame arriving after real-time playout. "first audible" is the closer match, but the
two still differ (WebRTC capture and jitter buffers on both legs, the worker's playout pacing),
so the gap between this and caller.py is not a measurement of LiveKit overhead.

Each reply is tied to the line that was current when its first chunk arrived. A reply to an
earlier line that finishes late, or one that finishes while the current line is still being
sent, is counted and set aside instead of being timed against the wrong line.

Usage: uv run python direct.py [n_turns] [silence_ms | default]
  silence_ms  Gemini's end-of-speech silence window, with high end-of-speech sensitivity, as the
              worker sets it for SPIKE_SILENCE_MS (default 500). "default" leaves Google's
              defaults, like a worker run with SPIKE_SILENCE_MS unset.
Env: GEMINI_API_KEY, SIM_LIVE_MODEL, SPIKE_VOICE.
"""

import asyncio
import os
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from google import genai
from google.genai import types

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from caller import LOUD, SCRIPT, voiced_clip  # noqa: E402
from live_settings import connect_config  # noqa: E402
from summarize import p95  # noqa: E402

SR_IN = 16_000
CHUNK = SR_IN // 50  # 20 ms
SR_OUT = 24_000  # Gemini Live returns 24 kHz 16-bit mono
WIN_OUT = SR_OUT // 100  # 10 ms, the caller's frame size
TTS_DIR = HERE / "runs" / "tts16"


PLAIN = ("turn", "kill_idle", "kill_speaking")  # steps whose line is a plain turn for the worker


def tts16(text: str) -> bytes:
    return voiced_clip(text, TTS_DIR, SR_IN).tobytes()


def control_lines(script: list[tuple[str, ...]], n: int) -> list[tuple[int, str]]:
    """The first n (script step, line) pairs the control speaks: the plain turns the worker's
    latency figure also counts (summarize.turn_latencies), minus the barge-in and probe steps,
    which need an interruption the control can't make. The line right after a forced drop is
    left out, as it is there."""
    after_kill = {i + 1 for i, s in enumerate(script) if s[0].startswith("kill")}
    plain = [(i, s[1]) for i, s in enumerate(script) if s[0] in PLAIN and i not in after_kill]
    return plain[:n]


@dataclass
class Reply:
    line: int  # the line being sent, or just sent, when the first chunk arrived
    first: float  # first chunk arrival
    audible: float | None  # first audible window
    done_at: float  # turn_complete arrival


def credit(reply: Reply, line: int, clip_sent_at: float | None) -> str:
    """Whether a completed reply answers `line`, whose last chunk went out at clip_sent_at (None
    while the clip is still being sent).
      stale     it began during an earlier line and finished late; not this line's reply
      mid_line  it finished before this line did, so the rest of the line is still unanswered
      reply     it's this line's reply (it may still have begun before the line ended)
    """
    if reply.line != line:
        return "stale"
    if clip_sent_at is None or reply.done_at < clip_sent_at:
        return "mid_line"
    return "reply"


def stats(xs: list[float]) -> str:
    good = [x for x in xs if x > 0]
    if not good:
        return "no turns"
    return (
        f"{len(good)} turns, median {statistics.median(good):.2f}s, p95 {p95(good):.2f}s, "
        f"max {max(good):.2f}s"
    )


def silence_arg(arg: str) -> int | None:
    return None if arg == "default" else int(arg)


async def main(n_turns: int, silence_ms: int | None) -> None:
    client = genai.Client(
        api_key=os.environ["GEMINI_API_KEY"], http_options=types.HttpOptions(api_version="v1beta")
    )
    config = connect_config(silence_ms)
    picked = control_lines(SCRIPT, n_turns)
    lines = [line for _, line in picked]
    clips = [tts16(line) for line in lines]
    silence = np.zeros(CHUNK, dtype=np.int16).tobytes()
    lats: list[float] = []  # to first chunk
    audible: list[float] = []  # to first audible window

    async with client.aio.live.connect(model=os.environ["SIM_LIVE_MODEL"], config=config) as s:
        clip_sent = asyncio.Event()
        clip_sent_at: list[float] = [0.0]
        cur_line: list[int] = [0]
        # Current generation: first chunk arrival, first audible time, samples received so far,
        # and the line that was current when it began.
        gen: dict[str, float | None] = {"first": None, "audible": None, "samples": 0, "line": 0}
        # Finished replies, or the exception that ended the receive loop.
        completed: asyncio.Queue[Reply | BaseException] = asyncio.Queue()
        false_starts: list[float] = []  # first-chunk times of generations Gemini cancelled

        def on_audio(pcm_bytes: bytes, now: float) -> None:
            if gen["first"] is None:
                gen["first"] = now
                gen["line"] = cur_line[0]
            pcm = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float64)
            if gen["audible"] is None:
                for w in range(0, len(pcm) - WIN_OUT + 1, WIN_OUT):
                    if np.sqrt(np.mean(pcm[w : w + WIN_OUT] ** 2)) > LOUD:
                        offset = (gen["samples"] + w) / SR_OUT
                        gen["audible"] = max(now, gen["first"] + offset)
                        break
            gen["samples"] += len(pcm)

        async def recv() -> None:
            try:
                while True:
                    async for msg in s.receive():
                        sc = msg.server_content
                        if sc and sc.model_turn:
                            for p in sc.model_turn.parts:
                                if p.inline_data and p.inline_data.data:
                                    on_audio(p.inline_data.data, time.time())
                        if sc and (sc.interrupted or sc.turn_complete):
                            if gen["first"] is not None:
                                if sc.interrupted:
                                    false_starts.append(gen["first"])
                                else:
                                    completed.put_nowait(
                                        Reply(
                                            int(gen["line"] or 0),
                                            gen["first"],
                                            gen["audible"],
                                            time.time(),
                                        )
                                    )
                            gen.update(first=None, audible=None, samples=0)
            except Exception as e:
                # Hand the failure to the main loop; otherwise every later line just times out.
                completed.put_nowait(e)

        stop = asyncio.Event()

        async def pump(clip: bytes | None) -> None:
            """Stream at real time: the clip (if any), then silence until told to stop. Records
            when the send of the clip's last chunk returned."""
            data = clip or b""
            t_next = time.perf_counter()
            for off in range(0, len(data), CHUNK * 2):
                await s.send_realtime_input(
                    audio=types.Blob(
                        data=data[off : off + CHUNK * 2], mime_type="audio/pcm;rate=16000"
                    )
                )
                if off + CHUNK * 2 >= len(data):
                    clip_sent_at[0] = time.time()
                    clip_sent.set()
                t_next += 0.02
                await asyncio.sleep(max(0.0, t_next - time.perf_counter()))
            while not stop.is_set():
                await s.send_realtime_input(
                    audio=types.Blob(data=silence, mime_type="audio/pcm;rate=16000")
                )
                t_next += 0.02
                await asyncio.sleep(max(0.0, t_next - time.perf_counter()))

        async def reply_for(line: int, timeout: float) -> Reply:
            """The next finished reply that answers this line. Raises TimeoutError if none comes,
            and RuntimeError (chained to the cause) if the receive loop failed."""
            deadline = time.monotonic() + timeout
            while True:
                got = await asyncio.wait_for(completed.get(), max(0.0, deadline - time.monotonic()))
                if isinstance(got, BaseException):
                    raise RuntimeError("the receive loop failed") from got
                verdict = credit(got, line, clip_sent_at[0] if clip_sent.is_set() else None)
                if verdict == "reply":
                    return got
                set_aside[verdict] += 1

        rx = asyncio.create_task(recv())
        n_false = 0
        set_aside = {"stale": 0, "mid_line": 0}
        for i, (line, clip) in enumerate(zip(lines, clips, strict=True)):
            clip_sent.clear()
            stop.clear()
            cur_line[0] = i
            n_fs = len(false_starts)
            task = asyncio.create_task(pump(clip))
            try:
                got = await reply_for(i, 40)
                first, aud = got.first, got.audible
                lat = first - clip_sent_at[0]
                lats.append(lat)
                if aud is not None:
                    audible.append(aud - clip_sent_at[0])
                fs = len(false_starts) - n_fs
                n_false += fs
                aud_s = f"{aud - clip_sent_at[0]:+.2f}s" if aud is not None else "  none"
                print(
                    f"[{i:02d}] step {picked[i][0]:02d}  chunk {lat:+.2f}s  audible {aud_s}  "
                    f"false starts {fs}  {line[:36]}",
                    flush=True,
                )
            except TimeoutError:
                print(f"[{i:02d}] step {picked[i][0]:02d}  none  {line[:52]}", flush=True)
            # let the reply "play out" before the next line, as a listener would
            await asyncio.sleep(1.0)
            stop.set()
            await task
        rx.cancel()

    window = "Google's default" if silence_ms is None else f"{silence_ms} ms"
    print(
        f"\ndirect, {os.environ['SIM_LIVE_MODEL']}, silence {window}\n"
        f"  to first chunk:   {stats(lats)}\n"
        f"  to first audible: {stats(audible)}\n"
        f"  replies that began before the line ended: {sum(x <= 0 for x in lats)}; "
        f"cancelled false starts: {n_false}\n"
        f"  set aside, not timed: {set_aside['stale']} late replies to an earlier line, "
        f"{set_aside['mid_line']} replies that finished before the line did"
    )


if __name__ == "__main__":
    asyncio.run(
        main(
            int(sys.argv[1]) if len(sys.argv) > 1 else 18,
            silence_arg(sys.argv[2]) if len(sys.argv) > 2 else 500,
        )
    )
