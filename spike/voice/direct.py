"""Control for the spike: the same lines sent straight to Gemini Live, no LiveKit in between.

This is roughly the browser-direct path's network shape (one WebSocket to Google) minus the
browser. Same prompt, model and voice activity settings as the worker, same macOS `say` lines.

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

Usage: uv run python direct.py [n_turns] [silence_ms]
Env: GEMINI_API_KEY, SIM_LIVE_MODEL.
"""

import asyncio
import os
import statistics
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from google import genai
from google.genai import types

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "backend"))

from agent import customer_prompt  # noqa: E402
from caller import LOUD, SCRIPT, synth  # noqa: E402

SR_IN = 16_000
CHUNK = SR_IN // 50  # 20 ms
SR_OUT = 24_000  # Gemini Live returns 24 kHz 16-bit mono
WIN_OUT = SR_OUT // 100  # 10 ms, the caller's frame size
TTS_DIR = HERE / "runs" / "tts16"


def tts16(text: str) -> bytes:
    path = synth(text, TTS_DIR, SR_IN)
    with wave.open(str(path)) as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    voiced = np.flatnonzero(np.abs(pcm) > 200)
    return pcm[voiced[0] : voiced[-1] + 1].tobytes()


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


async def main(n_turns: int, silence_ms: int) -> None:
    client = genai.Client(
        api_key=os.environ["GEMINI_API_KEY"], http_options=types.HttpOptions(api_version="v1beta")
    )
    config = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        system_instruction=customer_prompt(),
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Aoede")
            )
        ),
        temperature=0.8,
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_HIGH,
                silence_duration_ms=silence_ms,
            )
        ),
    )
    lines = [s[1] for s in SCRIPT if s[0] == "turn"][:n_turns]
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
                    f"[{i:02d}] chunk {lat:+.2f}s  audible {aud_s}  false starts {fs}  {line[:44]}",
                    flush=True,
                )
            except TimeoutError:
                print(f"[{i:02d}]  none  {line[:60]}", flush=True)
            # let the reply "play out" before the next line, as a listener would
            await asyncio.sleep(1.0)
            stop.set()
            await task
        rx.cancel()

    def stats(xs: list[float]) -> str:
        good = sorted(x for x in xs if x > 0)
        if not good:
            return "no turns"
        p95 = good[max(0, int(np.ceil(0.95 * len(good))) - 1)]
        return (
            f"{len(good)} turns, median {statistics.median(good):.2f}s, p95 {p95:.2f}s, "
            f"max {max(good):.2f}s"
        )

    print(
        f"\ndirect, {os.environ['SIM_LIVE_MODEL']}, silence {silence_ms} ms\n"
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
            int(sys.argv[2]) if len(sys.argv) > 2 else 500,
        )
    )
