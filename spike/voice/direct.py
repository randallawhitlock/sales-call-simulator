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

Usage: uv run python direct.py [n_turns] [silence_ms]
Env: GEMINI_API_KEY, SIM_LIVE_MODEL.
"""

import asyncio
import hashlib
import os
import statistics
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np
from google import genai
from google.genai import types

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "backend"))

from agent import customer_prompt  # noqa: E402
from caller import LOUD, REP_VOICE, SCRIPT  # noqa: E402

SR_IN = 16_000
CHUNK = SR_IN // 50  # 20 ms
SR_OUT = 24_000  # Gemini Live returns 24 kHz 16-bit mono
WIN_OUT = SR_OUT // 100  # 10 ms, the caller's frame size
TTS_DIR = HERE / "runs" / "tts16"


def tts16(text: str) -> bytes:
    TTS_DIR.mkdir(parents=True, exist_ok=True)
    path = TTS_DIR / f"{hashlib.sha1(f'{REP_VOICE}|{text}'.encode()).hexdigest()[:16]}.wav"
    if not path.exists():
        subprocess.run(
            ["say", "-v", REP_VOICE, "-o", str(path), "--data-format=LEI16@16000", text], check=True
        )
    with wave.open(str(path)) as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    voiced = np.flatnonzero(np.abs(pcm) > 200)
    return pcm[voiced[0] : voiced[-1] + 1].tobytes()


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
        turn_done = asyncio.Event()
        clip_sent = asyncio.Event()
        clip_sent_at: list[float] = [0.0]
        # Current generation: first chunk arrival, first audible time, samples received so far.
        gen: dict[str, float | None] = {"first": None, "audible": None, "samples": 0}
        completed: list[tuple[float, float | None]] = []  # (first chunk, first audible) per reply
        false_starts: list[float] = []  # first-chunk times of generations Gemini cancelled

        def on_audio(pcm_bytes: bytes, now: float) -> None:
            if gen["first"] is None:
                gen["first"] = now
            pcm = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float64)
            if gen["audible"] is None:
                for w in range(0, len(pcm) - WIN_OUT + 1, WIN_OUT):
                    if np.sqrt(np.mean(pcm[w : w + WIN_OUT] ** 2)) > LOUD:
                        offset = (gen["samples"] + w) / SR_OUT
                        gen["audible"] = max(now, gen["first"] + offset)
                        break
            gen["samples"] += len(pcm)

        async def recv() -> None:
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
                                completed.append((gen["first"], gen["audible"]))
                                turn_done.set()
                        gen.update(first=None, audible=None, samples=0)

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

        rx = asyncio.create_task(recv())
        n_false = 0
        for i, (line, clip) in enumerate(zip(lines, clips, strict=True)):
            turn_done.clear()
            clip_sent.clear()
            stop.clear()
            n_done, n_fs = len(completed), len(false_starts)
            task = asyncio.create_task(pump(clip))
            try:
                await asyncio.wait_for(turn_done.wait(), 40)
                await clip_sent.wait()
                first, aud = completed[n_done]
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
        f"cancelled false starts: {n_false}"
    )


if __name__ == "__main__":
    asyncio.run(
        main(
            int(sys.argv[1]) if len(sys.argv) > 1 else 18,
            int(sys.argv[2]) if len(sys.argv) > 2 else 500,
        )
    )
