"""Turn bookkeeping for a voice call: what each side said, when, and in what order.

Plain Python, so it's tested without LiveKit. The worker feeds it what LiveKit and Gemini report
and writes out the utterances it returns.

Two timing facts from the spike (docs/voice-spike.md) shape it:

- Gemini's transcript of the agent is only final once the customer's reply has finished, seconds
  after the agent spoke. A customer line that arrives while the agent line before it is still
  being transcribed is held until that line is final, so lines are saved in spoken order.
- After a barge-in, LiveKit knows how much of the customer's line actually played; Gemini only
  knows what it generated. The saved line keeps both (``text`` generated, ``heard_text``
  played), and the worker tells Gemini what wasn't heard (``heard_note``).
"""

import math
import re
import statistics
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import JsonValue

from app.engine.events import Channel, Speaker, UtteranceIn

HOLD_FOR_TRANSCRIPT_S = 4.0
"""Longest a customer line waits for the agent line before it to be transcribed."""
AUDIO_TOKENS_PER_MIN = 1500
"""Gemini counts 25 audio tokens per second."""
MAX_LATENCY_GAP_S = 10.0
"""A customer line that starts later than this after the agent stopped talking isn't timed as
an answer to them (a nudge after a long silence, say)."""


def instructions(text: str) -> str:
    """How a note to the model is framed, the same way LiveKit frames reply instructions for
    Gemini 3.x, so the customer doesn't read it as something the agent said."""
    return f"<instructions>\n{text}\n</instructions>"


def heard_note(heard: str) -> str:
    heard = heard.strip()
    said = f'All they heard you say was: "{heard}"' if heard else "They didn't hear any of it."
    return instructions(
        f"The agent talked over you. {said} They never heard the rest, so don't talk as if they "
        "did. If it matters, you can say it again."
    )


def hold_note(seconds: float) -> str:
    return instructions(
        f"The agent put you on hold for about {round(seconds)} seconds and is back now. You "
        "heard nothing while you were on hold."
    )


NUDGE = (
    "The agent has been quiet for a while, probably looking something up. Say one short thing "
    "a caller would say after a long silence on the phone, like checking they're still there. "
    "A few words only."
)
"""Reply instructions for a research silence. LiveKit adds the ``instructions`` framing."""


def client_utt_id(item_id: str) -> str:
    """A resent line keeps its id, so the session service stores it once."""
    return ("lk-" + re.sub(r"[^A-Za-z0-9_-]", "-", item_id))[:64]


def level_db(samples: Sequence[int]) -> float:
    """RMS level of 16-bit PCM samples in dBFS."""
    if not samples:
        return -math.inf
    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
    return 20 * math.log10(rms / 32768) if rms else -math.inf


@dataclass
class Run:
    start: float
    end: float


class SpeechTracker:
    """When the agent was talking, from the level of their audio track on the worker's clock.

    Gemini doesn't report when the agent started or stopped talking, so this does: it starts
    each agent line's clock and measures how long the customer took to answer. Pauses shorter
    than ``merge_gap_s`` (Gemini's own end-of-speech window) stay inside one run."""

    def __init__(
        self, threshold_db: float = -45.0, merge_gap_s: float = 0.7, keep: int = 500
    ) -> None:
        self.threshold_db = threshold_db
        self.merge_gap_s = merge_gap_s
        self.runs: deque[Run] = deque(maxlen=keep)

    def frame(self, t_end: float, duration_s: float, level: float) -> None:
        if level < self.threshold_db:
            return
        start = t_end - duration_s
        if self.runs and start - self.runs[-1].end <= self.merge_gap_s:
            self.runs[-1].end = max(self.runs[-1].end, t_end)
        else:
            self.runs.append(Run(start, t_end))

    @property
    def last_voice(self) -> float | None:
        return self.runs[-1].end if self.runs else None

    def run_started_before(self, t: float) -> Run | None:
        """The latest run that began by ``t``."""
        return next((r for r in reversed(self.runs) if r.start <= t), None)

    def run_ended_before(self, t: float) -> Run | None:
        """The latest run that was over by ``t``."""
        return next((r for r in reversed(self.runs) if r.end <= t), None)


@dataclass
class _Open:
    first_seen: float
    text: str


@dataclass(order=True)
class _Pending:
    started_at: float
    seq: int
    body: UtteranceIn = field(compare=False)
    blocked_by: set[str] = field(compare=False, default_factory=set)
    held_since: float = field(compare=False, default=0.0)


class Ledger:
    """Turns LiveKit's conversation items into utterances, in spoken order."""

    def __init__(self, call_started_at: float, hold_s: float = HOLD_FOR_TRANSCRIPT_S) -> None:
        self.call_started_at = call_started_at
        self.hold_s = hold_s
        self._open: dict[str, _Open] = {}
        """Agent lines Gemini has started transcribing but not finished."""
        self._held: list[_Pending] = []
        self._done: set[str] = set()
        self._seq = 0

    def skip(self, item_ids: set[str]) -> None:
        """Items already in the log (history replayed after a worker restart)."""
        self._done |= item_ids

    def t_ms(self, at: float) -> int:
        return max(0, round((at - self.call_started_at) * 1000))

    # Agent (learner) lines

    def first_seen(self, item_id: str) -> float | None:
        """When the first interim transcript of an agent line arrived."""
        entry = self._open.get(item_id)
        return entry.first_seen if entry else None

    def learner_heard(self, item_id: str, text: str, at: float) -> None:
        """An interim transcript of an agent line."""
        if item_id in self._done or not text.strip():
            return
        entry = self._open.get(item_id)
        if entry is None:
            self._open[item_id] = _Open(first_seen=at, text=text)
        else:
            entry.text = text

    def learner_final(
        self, item_id: str, text: str, started_at: float, now: float
    ) -> list[UtteranceIn]:
        self._open.pop(item_id, None)
        out: list[UtteranceIn] = []
        if item_id not in self._done and text.strip():
            self._done.add(item_id)
            out.append(self._learner(item_id, text, started_at))
        return self._release(out, item_id, now)

    def _learner(self, item_id: str, text: str, started_at: float) -> UtteranceIn:
        return UtteranceIn(
            client_utt_id=client_utt_id(item_id),
            speaker=Speaker.LEARNER,
            channel=Channel.VOICE,
            text=text.strip(),
            heard_text=text.strip(),
            t_ms=self.t_ms(started_at),
        )

    # Customer lines

    def customer_line(
        self,
        item_id: str,
        *,
        heard: str,
        generated: str | None,
        started_at: float,
        ended_at: float | None,
        interrupted: bool,
        now: float,
    ) -> list[UtteranceIn]:
        heard = heard.strip()
        text = (generated or "").strip() or heard
        if item_id in self._done or not text:
            return []
        self._done.add(item_id)
        body = UtteranceIn(
            client_utt_id=client_utt_id(item_id),
            speaker=Speaker.CUSTOMER,
            channel=Channel.VOICE,
            text=text,
            heard_text=heard,
            t_ms=self.t_ms(started_at),
            duration_ms=None if ended_at is None else max(0, round((ended_at - started_at) * 1000)),
            interrupted=interrupted,
        )
        blockers = {k for k, v in self._open.items() if v.first_seen <= started_at}
        self._seq += 1
        pending = _Pending(started_at, self._seq, body, blockers, now)
        if not blockers:
            return self._release([], None, now, extra=[pending])
        self._held.append(pending)
        return []

    # Flushing

    def due(self, now: float) -> list[UtteranceIn]:
        """Held lines whose wait ran out: their agent line never got a final transcript."""
        late = [p for p in self._held if now - p.held_since >= self.hold_s]
        for p in late:
            p.blocked_by.clear()
        return self._release([], None, now)

    def flush(self, now: float) -> list[UtteranceIn]:
        """Everything still pending, for the end of the call: an agent line still being
        transcribed is saved as transcribed so far."""
        out = []
        for item_id, entry in sorted(self._open.items(), key=lambda kv: kv[1].first_seen):
            if item_id not in self._done:
                self._done.add(item_id)
                out.append((entry.first_seen, self._learner(item_id, entry.text, entry.first_seen)))
        self._open.clear()
        for p in self._held:
            p.blocked_by.clear()
        released = self._release([], None, now)
        merged = sorted(
            [(b.t_ms or 0, i, b) for i, (_, b) in enumerate(out)]
            + [(b.t_ms or 0, len(out) + i, b) for i, b in enumerate(released)],
            key=lambda x: (x[0], x[1]),
        )
        return [b for _, _, b in merged]

    def _release(
        self,
        out: list[UtteranceIn],
        resolved: str | None,
        now: float,
        extra: list[_Pending] | None = None,
    ) -> list[UtteranceIn]:
        for p in self._held:
            if resolved is not None:
                p.blocked_by.discard(resolved)
        ready = sorted([p for p in self._held if not p.blocked_by] + (extra or []))
        self._held = [p for p in self._held if p.blocked_by]
        merged = sorted(
            [(b.t_ms or 0, 0, i, b) for i, b in enumerate(out)]
            + [(p.body.t_ms or 0, 1, p.seq, p.body) for p in ready],
            key=lambda x: (x[0], x[1], x[2]),
        )
        return [b for *_, b in merged]

    @property
    def pending(self) -> int:
        return len(self._open) + len(self._held)


@dataclass
class VoiceStats:
    """What one voice call cost and how it behaved, written to the voice_stats table."""

    model: str
    silence_ms: int
    price_in_per_min: float
    price_out_per_min: float
    latencies: list[float] = field(default_factory=list)
    barge_ins: int = 0
    nudges: int = 0
    holds: int = 0
    connects: int = 0
    go_aways: int = 0
    restarts: int = 0
    errors: list[str] = field(default_factory=list)
    tokens: dict[str, int] = field(
        default_factory=lambda: {
            "input_audio": 0,
            "input_text": 0,
            "input_cached": 0,
            "output_audio": 0,
            "output_text": 0,
        }
    )
    recording: str | None = None

    def answered(self, tracker: SpeechTracker, started_at: float, after: float | None) -> None:
        """Time a customer line as an answer to the agent's last run of speech, if it is one:
        the run ended after the customer's previous line and not long before this one."""
        run = tracker.run_ended_before(started_at)
        if run is None or (after is not None and run.end <= after):
            return
        gap = started_at - run.end
        if 0 <= gap <= MAX_LATENCY_GAP_S:
            self.latencies.append(gap)

    def error(self, text: str) -> None:
        if len(self.errors) < 20:
            self.errors.append(text[:300])

    def usage(
        self, *, input_audio: int, input_text: int, cached: int, output_audio: int, output_text: int
    ) -> None:
        t = self.tokens
        t["input_audio"] += input_audio
        t["input_text"] += input_text
        t["input_cached"] += cached
        t["output_audio"] += output_audio
        t["output_text"] += output_text

    def cost_usd(self) -> float:
        audio_in = self.tokens["input_audio"] / AUDIO_TOKENS_PER_MIN
        audio_out = self.tokens["output_audio"] / AUDIO_TOKENS_PER_MIN
        return round(audio_in * self.price_in_per_min + audio_out * self.price_out_per_min, 4)

    def to_json(self, call_seconds: float) -> dict[str, JsonValue]:
        lat = sorted(self.latencies)
        p95 = lat[min(len(lat) - 1, math.ceil(0.95 * len(lat)) - 1)] if lat else None
        return {
            "model": self.model,
            "silence_ms": self.silence_ms,
            "call_seconds": round(call_seconds, 1),
            "turns_timed": len(lat),
            "latency_median_s": round(statistics.median(lat), 3) if lat else None,
            "latency_p95_s": round(p95, 3) if p95 is not None else None,
            "latency_max_s": round(lat[-1], 3) if lat else None,
            "latency_basis": "end of the agent's speech on the worker to the first frame of the "
            "customer's reply leaving the worker; excludes the network and the browser",
            "barge_ins": self.barge_ins,
            "nudges": self.nudges,
            "holds": self.holds,
            "reconnects": max(0, self.connects - 1 - self.restarts),
            "go_aways": self.go_aways,
            "session_restarts": self.restarts,
            "errors": list(self.errors),
            "tokens": dict(self.tokens),
            "audio_minutes": {
                "input": round(self.tokens["input_audio"] / AUDIO_TOKENS_PER_MIN, 2),
                "output": round(self.tokens["output_audio"] / AUDIO_TOKENS_PER_MIN, 2),
            },
            "cost_usd_estimate": self.cost_usd(),
            "cost_basis": f"audio tokens at 25 a second, ${self.price_in_per_min}/min in and "
            f"${self.price_out_per_min}/min out; text tokens are counted, not priced",
            "recording": self.recording,
        }
