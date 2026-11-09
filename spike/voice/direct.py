"""Control for the spike: the same lines sent straight to Gemini Live, no LiveKit in between.

This is roughly the browser-direct path's network shape (one WebSocket to Google) minus the
browser. Same prompt, model and voice activity settings as the worker, same macOS `say` lines.
"End of turn to first audio" here is the last voiced sample sent to the WebSocket to the first
audio chunk received from it, so compare it with caller.py's numbers to see what the worker and
LiveKit hops cost.

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
from caller import REP_VOICE, SCRIPT  # noqa: E402

SR_IN = 16_000
CHUNK = SR_IN // 50  # 20 ms
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
    lats: list[float] = []

    async with client.aio.live.connect(model=os.environ["SIM_LIVE_MODEL"], config=config) as s:
        turn_done = asyncio.Event()
        gen_first: list[float | None] = [None]  # first audio chunk of the current generation
        completed: list[float] = []  # first-chunk times of generations that finished
        false_starts: list[float] = []  # first-chunk times of generations Gemini cancelled

        async def recv() -> None:
            while True:
                async for msg in s.receive():
                    sc = msg.server_content
                    if sc and sc.model_turn:
                        for p in sc.model_turn.parts:
                            if p.inline_data and p.inline_data.data and gen_first[0] is None:
                                gen_first[0] = time.time()
                    if sc and sc.interrupted:
                        if gen_first[0] is not None:
                            false_starts.append(gen_first[0])
                        gen_first[0] = None
                    elif sc and sc.turn_complete:
                        if gen_first[0] is not None:
                            completed.append(gen_first[0])
                            turn_done.set()
                        gen_first[0] = None

        stop = asyncio.Event()

        async def pump(clip: bytes | None) -> None:
            """Stream at real time: the clip (if any), then silence until told to stop."""
            data = clip or b""
            t_next = time.perf_counter()
            for off in range(0, len(data), CHUNK * 2):
                await s.send_realtime_input(
                    audio=types.Blob(
                        data=data[off : off + CHUNK * 2], mime_type="audio/pcm;rate=16000"
                    )
                )
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
            stop.clear()
            n_done, n_fs = len(completed), len(false_starts)
            t_start = time.time()
            task = asyncio.create_task(pump(clip))
            t_end = t_start + len(clip) / 2 / SR_IN
            try:
                await asyncio.wait_for(turn_done.wait(), 40)
                lat = completed[n_done] - t_end
                lats.append(lat)
                fs = len(false_starts) - n_fs
                n_false += fs
                print(f"[{i:02d}] {lat:+.2f}s  false starts {fs}  {line[:50]}", flush=True)
            except TimeoutError:
                print(f"[{i:02d}]  none  {line[:60]}", flush=True)
            # let the reply "play out" before the next line, as a listener would
            await asyncio.sleep(1.0)
            stop.set()
            await task
        rx.cancel()

    good = [x for x in lats if x > 0]
    ordered = sorted(good)
    p95 = ordered[max(0, int(np.ceil(0.95 * len(ordered))) - 1)]
    print(
        f"\ndirect, {os.environ['SIM_LIVE_MODEL']}, silence {silence_ms} ms: {len(good)} turns, "
        f"median {statistics.median(good):.2f}s, p95 {p95:.2f}s, max {max(good):.2f}s; "
        f"replies that began before the line ended: {len(lats) - len(good)}; "
        f"cancelled false starts: {n_false}"
    )


if __name__ == "__main__":
    asyncio.run(
        main(
            int(sys.argv[1]) if len(sys.argv) > 1 else 18,
            int(sys.argv[2]) if len(sys.argv) > 2 else 500,
        )
    )
