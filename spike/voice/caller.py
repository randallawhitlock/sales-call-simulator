"""Throwaway M4 spike caller: a bot rep that talks to the agent and times what it hears.

Joins a room on the local LiveKit server as "rep", speaks lines synthesized with macOS `say`
through a real-time-paced microphone track, and watches the customer's audio track frame by
frame. Writes runs/<room>-caller.json, then prints a report joined with the worker's
runs/<room>-agent.jsonl.

Timing: every timestamp is wall-clock on this machine (the worker logs the same clock). "End of
turn" is when the last voiced sample of a line enters the outgoing WebRTC track; "first audio" is
when the first frame of the customer's reply above the loudness threshold arrives here. So the
number includes Gemini's end-of-speech detection, the model, the worker and both LiveKit hops,
but not a browser jitter buffer or speaker output.

Usage: uv run python caller.py [room-name]
"""

import asyncio
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from livekit import api, rtc

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"
TTS_DIR = RUNS / "tts"
SR = 48_000
FRAME = SR // 100  # 10 ms
LOUD = 300.0  # int16 RMS; Gemini speech sits in the thousands, silence frames are zero
REP_VOICE = os.environ.get("SPIKE_REP_VOICE", "Samantha")
AGENCY = "Larkspur Benefits Group"

# (kind, line[, interruption]) kinds: turn = timed turn, barge = open question then talk over the
# answer, kill_idle = turn then drop the model socket, kill_speaking = drop it mid-answer.
SCRIPT: list[tuple[str, ...]] = [
    ("turn", f"Thank you for calling {AGENCY}, this is Marcus. How can I help you today?"),
    ("turn", "I'm sorry that worried you. We'll look at it together. Can I get your full name?"),
    ("turn", "Thank you, Loretta. What's the address where you live?"),
    ("turn", "Do you have Medicare Part A and Part B?"),
    ("turn", "And what plan do you have right now?"),
    ("turn", "Who is your heart doctor?"),
    ("turn", "And who's your regular doctor?"),
    ("turn", "Is keeping Doctor Raman the most important thing for you?"),
    ("turn", "What prescriptions do you take?"),
    ("turn", "Could you check the bottle on the atorvastatin for me? What strength does it say?"),
    ("turn", "Which pharmacy do you use?"),
    ("turn", "Do you have any other insurance, like a retiree plan or the VA?"),
    ("turn", "What could you manage for a monthly premium?"),
    ("turn", "Does Doctor Raman see patients at more than one office?"),
    ("kill_idle", "Just so you have it, my extension is four four one seven."),
    (
        "turn",
        "Sorry, I think the line dropped for a second. Can you tell me what my extension was?",
    ),
    ("turn", "Thanks. Do you get any help paying for your medicines?"),
    (
        "barge",
        "Tell me a little about your family. Do you have grandkids nearby?",
        "Sorry to jump in. What's the name of that pharmacy again?",
    ),
    ("probe", "Sorry I cut you off. What was the last thing you told me about your grandkids?"),
    (
        "barge",
        "How did you feel when you got that letter?",
        "Sorry, one quick thing. What ZIP code is that?",
    ),
    (
        "barge",
        "What do you like to do with your grandkids?",
        "Sorry to cut you off. Which office do you go to for Doctor Raman?",
    ),
    ("kill_speaking", "Tell me about your heart scare nine years ago."),
    ("turn", "You cut out for a moment there. What were you saying?"),
    ("turn", "Is there anything else you'd like me to look at today?"),
    ("turn", "Alright. I'll pull up plans that keep Doctor Raman in network. Is that okay?"),
]

# One forced reconnect per call, for repeating in fresh rooms (SPIKE_SCRIPT=reconnect).
RECONNECT_SCRIPT: list[tuple[str, ...]] = [
    SCRIPT[0],
    SCRIPT[1],
    SCRIPT[17],  # barge: family / grandkids
    ("probe", "Sorry I cut you off. What was the last thing you told me about your grandkids?"),
    ("kill_idle", "Just so you have it, my extension is four four one seven."),
    (
        "turn",
        "Sorry, I think the line dropped for a second. Can you tell me what my extension was?",
    ),
    ("turn", "Thanks. Which pharmacy do you use?"),
]
if os.environ.get("SPIKE_SCRIPT") == "reconnect":
    SCRIPT = RECONNECT_SCRIPT


def tts(text: str) -> np.ndarray:
    """Synthesize a line once, cache it, and trim silence so its end is the last voiced sample."""
    TTS_DIR.mkdir(parents=True, exist_ok=True)
    path = TTS_DIR / f"{hashlib.sha1(f'{REP_VOICE}|{text}'.encode()).hexdigest()[:16]}.wav"
    if not path.exists():
        subprocess.run(
            ["say", "-v", REP_VOICE, "-o", str(path), "--data-format=LEI16@48000", text],
            check=True,
        )
    with wave.open(str(path)) as w:
        assert w.getframerate() == SR and w.getnchannels() == 1
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    voiced = np.flatnonzero(np.abs(pcm) > 200)
    return pcm[voiced[0] : voiced[-1] + 1].copy()


class Mic:
    """A microphone that never stops: speech when there is some, a faint noise floor otherwise."""

    def __init__(self) -> None:
        self.source = rtc.AudioSource(SR, 1, queue_size_ms=40)
        self._pending: list[tuple[np.ndarray, asyncio.Future[tuple[float, float]]]] = []
        self._rng = np.random.default_rng(7)

    async def run(self) -> None:
        cur: np.ndarray | None = None
        fut: asyncio.Future[tuple[float, float]] | None = None
        pos = 0
        t_first = 0.0
        while True:
            if cur is None and self._pending:
                cur, fut = self._pending.pop(0)
                pos = 0
            if cur is not None:
                chunk = cur[pos : pos + FRAME]
                if len(chunk) < FRAME:
                    chunk = np.concatenate([chunk, self._noise(FRAME - len(chunk))])
            else:
                chunk = self._noise(FRAME)
            frame = rtc.AudioFrame(chunk.astype(np.int16).tobytes(), SR, 1, FRAME)
            await self.source.capture_frame(frame)
            if cur is not None and fut is not None:
                now = time.time() + self.source.queued_duration
                if pos == 0:
                    t_first = now
                pos += FRAME
                if pos >= len(cur):
                    fut.set_result((t_first, now))
                    cur, fut = None, None

    def _noise(self, n: int) -> np.ndarray:
        return self._rng.normal(0, 3, n).astype(np.int16)

    async def say(self, text: str) -> tuple[float, float]:
        """Speak a line; return (first voiced sample sent, last voiced sample sent)."""
        fut: asyncio.Future[tuple[float, float]] = asyncio.get_running_loop().create_future()
        self._pending.append((tts(text), fut))
        return await fut


class Ear:
    """Per-frame loudness of the customer's audio, with arrival times."""

    def __init__(self) -> None:
        self.frames: list[tuple[float, float]] = []
        self.room: rtc.Room | None = None

    @property
    def agent_state(self) -> str:
        """The worker publishes its state as a participant attribute; read it live."""
        for p in self.room.remote_participants.values() if self.room else []:
            if "lk.agent.state" in p.attributes:
                return p.attributes["lk.agent.state"]
        return "initializing"

    async def listen(self, track: rtc.Track) -> None:
        async for ev in rtc.AudioStream(track, sample_rate=SR, num_channels=1):
            x = np.frombuffer(ev.frame.data, dtype=np.int16).astype(np.float32)
            self.frames.append((time.time(), float(np.sqrt(np.mean(x * x))) if len(x) else 0.0))

    def last_loud(self) -> float:
        for t, rms in reversed(self.frames):
            if rms > LOUD:
                return t
        return 0.0

    def first_loud_after(self, t0: float) -> float | None:
        for t, rms in self.frames:
            if t > t0 and rms > LOUD:
                return t
        return None

    def silence_start_after(self, t0: float, gap: float) -> float | None:
        """End of the last loud frame after t0 that is followed by at least `gap` of quiet."""
        last = None
        for t, rms in self.frames:
            if t <= t0:
                continue
            if rms > LOUD:
                last = t
            elif last is not None and t - last >= gap:
                return last
        return None

    async def wait_onset(self, after: float, timeout: float = 15.0) -> float | None:
        end = time.time() + timeout
        while time.time() < end:
            if (t := self.first_loud_after(after)) is not None:
                return t
            await asyncio.sleep(0.01)
        return None

    async def wait_idle(self, quiet: float = 1.2, timeout: float = 45.0) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if self.agent_state != "speaking" and time.time() - self.last_loud() >= quiet:
                return
            await asyncio.sleep(0.05)


@dataclass
class Step:
    i: int
    kind: str
    line: str
    said_from: float = 0.0
    said_to: float = 0.0
    onset: float | None = None
    latency_s: float | None = None
    reply_end: float | None = None
    notes: dict[str, object] = field(default_factory=dict)


async def main(room_name: str) -> None:
    token = (
        api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        .with_identity("rep")
        .with_grants(api.VideoGrants(room_join=True, room=room_name))
        .to_jwt()
    )
    room = rtc.Room()
    ear = Ear()
    ear.room = room
    mic = Mic()
    agent_audio = asyncio.Event()

    @room.on("track_subscribed")
    def _sub(track: rtc.Track, _pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant) -> None:
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            asyncio.ensure_future(ear.listen(track))
            agent_audio.set()

    await room.connect(os.environ["LIVEKIT_URL"], token)
    track = rtc.LocalAudioTrack.create_audio_track("mic", mic.source)
    await room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    )
    pump = asyncio.ensure_future(mic.run())
    for t in {s[1] for s in SCRIPT} | {s[2] for s in SCRIPT if len(s) > 2}:
        tts(t)  # synthesize everything up front so `say` never sits inside a timed turn
    print("waiting for agent audio track", flush=True)
    await asyncio.wait_for(agent_audio.wait(), 60)
    print("agent audio subscribed", flush=True)
    while ear.agent_state not in ("listening", "speaking"):
        await asyncio.sleep(0.1)
    await asyncio.sleep(1.0)

    async def kill() -> float:
        t = time.time()
        await room.local_participant.publish_data(
            json.dumps({"cmd": "kill_model"}).encode(), reliable=True, topic="spike"
        )
        return t

    steps: list[Step] = []
    for i, item in enumerate(SCRIPT):
        kind, line = item[0], item[1]
        await ear.wait_idle()
        s = Step(i, kind, line)
        s.said_from, s.said_to = await mic.say(line)
        s.onset = await ear.wait_onset(after=s.said_from)
        if s.onset is not None:
            s.latency_s = s.onset - s.said_to
        if kind == "barge" and s.onset is not None:
            await asyncio.sleep(max(0.0, s.onset + 2.5 - time.time()))
            s.notes["still_talking_at_barge"] = time.time() - ear.last_loud() < 0.3
            b_from, b_to = await mic.say(item[2])
            await ear.wait_idle(quiet=0.6, timeout=10)
            stop = ear.silence_start_after(b_from, gap=0.5)
            s.notes.update(barge_line=item[2], barge_from=b_from, barge_to=b_to, stopped=stop)
            if stop is not None:
                s.notes["cutoff_s"] = stop - b_from
            reply = await ear.wait_onset(after=max(b_to, stop or b_to))
            if reply is not None:
                s.notes["reply_latency_s"] = reply - b_to
        elif kind == "kill_speaking" and s.onset is not None:
            await asyncio.sleep(max(0.0, s.onset + 1.2 - time.time()))
            kill_at = await kill()
            s.notes["kill_at"] = kill_at
            await ear.wait_idle(quiet=0.6, timeout=20)
            stop = ear.silence_start_after(kill_at, gap=0.5)
            s.notes["audio_after_kill_s"] = (stop - kill_at) if stop else None
        await ear.wait_idle()
        s.reply_end = ear.last_loud()
        if kind == "kill_idle":
            s.notes["kill_at"] = await kill()
            await asyncio.sleep(2.0)
        steps.append(s)
        lat = f"{s.latency_s:+.2f}s" if s.latency_s is not None else "none"
        print(f"[{i:02d}] {kind:13s} {lat:>8s}  {line[:60]}", flush=True)

    await asyncio.sleep(2.0)
    pump.cancel()
    out = RUNS / f"{room_name}-caller.json"
    out.write_text(json.dumps([asdict(s) for s in steps], indent=1))
    (RUNS / f"{room_name}-frames.json").write_text(json.dumps(ear.frames))
    await room.disconnect()
    report(room_name)


def p95(xs: list[float]) -> float:
    """Nearest-rank p95. With ~20 samples this is the second-worst turn, so read it loosely."""
    ordered = sorted(xs)
    return ordered[max(0, int(np.ceil(0.95 * len(ordered))) - 1)]


def report(room_name: str) -> None:
    steps = json.loads((RUNS / f"{room_name}-caller.json").read_text())
    agent_path = RUNS / f"{room_name}-agent.jsonl"
    events = (
        [json.loads(x) for x in agent_path.read_text().splitlines()] if agent_path.exists() else []
    )
    start = next((e for e in events if e["kind"] == "start"), {})
    print(f"\nmodel={start.get('model')} resumption={start.get('resumption')}")

    timed = [
        s for s in steps if s["kind"] in ("turn", "probe", "kill_idle", "barge", "kill_speaking")
    ]
    after_kill = {s["i"] + 1 for s in steps if s["kind"] in ("kill_idle", "kill_speaking")}
    normal = [s for s in timed if s["i"] not in after_kill]
    lats = [s["latency_s"] for s in normal if s["latency_s"] is not None and s["latency_s"] > 0]
    early = [s for s in normal if s["latency_s"] is not None and s["latency_s"] <= 0]
    missing = [s for s in normal if s["latency_s"] is None]
    if lats:
        print(
            f"end-of-turn -> first audio over {len(lats)} turns: median "
            f"{statistics.median(lats):.2f}s, p95 {p95(lats):.2f}s, min {min(lats):.2f}s, "
            f"max {max(lats):.2f}s"
        )
    print(f"  replies that started before the rep finished: {len(early)}; no reply: {len(missing)}")

    rt = [e for e in events if e["kind"] == "rt_metrics" and e.get("ttft", -1) > 0]
    if rt:
        tt = [e["ttft"] for e in rt]
        print(
            f"  plugin ttft (model only): median {statistics.median(tt):.2f}s, max {max(tt):.2f}s"
        )

    for s in steps:
        if s["kind"] == "barge":
            n = s["notes"]
            heard = [
                e
                for e in events
                if e["kind"] == "item"
                and e.get("role") == "assistant"
                and e.get("interrupted")
                and n["barge_from"] - 1 < e["t"] < n["barge_from"] + 15
            ]
            gen = [
                e
                for e in events
                if e["kind"] == "model_turn_text"
                and e.get("interrupted")
                and n["barge_from"] - 1 < e["t"] < n["barge_from"] + 15
            ]
            print(
                f"barge [{s['i']}]: talking at barge={n.get('still_talking_at_barge')} "
                f"cutoff={_f(n.get('cutoff_s'))} reply={_f(n.get('reply_latency_s'))}"
            )
            print(f"    heard (LiveKit, interrupted item): {heard[0]['text'] if heard else None!r}")
            print(
                f"    generated (Gemini transcript):     {gen[0]['generated'] if gen else None!r}"
            )

    for s in steps:
        if s["kind"] in ("kill_idle", "kill_speaking"):
            if "kill_at" not in s["notes"]:
                print(f"{s['kind']} [{s['i']}]: not run (no reply to interrupt)")
                continue
            k = s["notes"]["kill_at"]
            conn = [e for e in events if e["kind"] == "model_connected" and e["t"] > k]
            errs = [e for e in events if e["kind"] in ("model_error", "error") and e["t"] > k - 1][
                :2
            ]
            nxt = next((x for x in steps if x["i"] == s["i"] + 1), None)
            reply = None
            if nxt:
                reply = next(
                    (
                        e["text"]
                        for e in events
                        if e["kind"] == "item"
                        and e.get("role") == "assistant"
                        and e["t"] > nxt["said_to"]
                    ),
                    None,
                )
            print(
                f"{s['kind']} [{s['i']}]: reconnected after "
                f"{_f(conn[0]['t'] - k) if conn else 'never'} "
                f"(connect {_f(conn[0]['connect_s']) if conn else '-'}, resumed handle="
                f"{bool(conn[0].get('handle')) if conn else '-'}); "
                f"audio after kill={_f(s['notes'].get('audio_after_kill_s'))}"
            )
            print(f"    errors: {[e.get('error', '')[:120] for e in errs]}")
            if nxt:
                print(f"    next turn latency {_f(nxt['latency_s'])}, reply: {reply!r}")

    for s in steps:
        if s["kind"] == "probe":
            reply = next(
                (
                    e["text"]
                    for e in events
                    if e["kind"] == "item"
                    and e.get("role") == "assistant"
                    and e["t"] > s["said_to"]
                ),
                None,
            )
            print(f"probe [{s['i']}]: {s['line']!r}\n    reply: {reply!r}")

    interim_lag, final_lag = [], []
    for s in steps:
        if s["kind"] not in ("turn", "kill_idle", "probe"):
            continue
        nxt_t = next((x["said_from"] for x in steps if x["i"] == s["i"] + 1), 1e18)
        first = next(
            (
                e["t"]
                for e in events
                if e["kind"] == "user_interim" and s["said_from"] < e["t"] < nxt_t
            ),
            None,
        )
        final = next(
            (
                e["t"]
                for e in events
                if e["kind"] == "user_said" and s["said_from"] < e["t"] < nxt_t
            ),
            None,
        )
        if first:
            interim_lag.append(first - s["said_to"])
        if final:
            final_lag.append(final - s["said_to"])
    if interim_lag:
        print(
            f"rep transcript after end of line: first interim median "
            f"{statistics.median(interim_lag):+.2f}s, final median "
            f"{statistics.median(final_lag):+.2f}s (n={len(interim_lag)})"
        )

    print("\ntranscript:")
    for e in events:
        if e["kind"] in ("item",):
            tag = " (interrupted)" if e.get("interrupted") else ""
            print(f"  {e.get('role')}: {e.get('text')}{tag}")


def _f(x: object) -> str:
    return f"{x:.2f}s" if isinstance(x, (int, float)) else str(x)


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else f"spike-{int(time.time())}"
    if len(sys.argv) > 2 and sys.argv[2] == "--report":
        report(name)
    else:
        asyncio.run(main(name))
