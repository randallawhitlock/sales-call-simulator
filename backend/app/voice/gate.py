"""Scores one finished voice call against the M4 acceptance gate in docs/voice-spike.md.

Run ``uv run python -m app.voice.gate <session_id> [--json]`` from ``backend/``. The checks are
pure functions over a call's utterances and stats, so they run the same on a person's 60-minute
call and on a bot run. It's a report, not a test: the exit code is 0 whenever the report ran.
"""

import dataclasses
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import JsonValue

from app.engine.events import Channel, Event, Speaker, Utterance
from app.engine.store import DB_PATH_ENV, DEFAULT_DB_PATH, Store

ANSWER_GAP_MS = 5000
"""After a transition the customer must answer the agent's next line within this long."""
LATENCY_MEDIAN_S = 1.2
"""Median response latency target, in seconds."""
LATENCY_P95_S = 2.5
"""95th percentile response latency target, in seconds."""
DUPLICATE_WINDOW_MS = 15000
"""Two lines by one speaker with the same words, starting this close, count as a duplicate."""
DUPLICATE_MIN_WORDS = 4
"""Shorter lines ("Okay.", "Yes, I do.") repeat for real, so they aren't checked for duplicates."""
OVERLAP_SLACK_MS = 300
"""A line may start this much before the same speaker's previous line ends."""
FULL_CALL_MINUTES = 50
"""Calls this long are held to the gate's forced-drop count."""
MIN_DROPS = 2
"""The gate wants at least this many forced drops on a full call."""
GO_AWAY_EVERY_MIN = 10
"""Gemini sends a GoAway about this often."""
EXCERPT_CHARS = 120
"""Longest line shown in the report."""

TRANSITION_KINDS = frozenset({"go_away", "drop", "reconnect", "restart", "rejoin"})
JUDGED_KINDS = frozenset({"go_away", "drop", "restart", "rejoin"})


class Status(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    REVIEW = "REVIEW"
    NOT_COVERED = "NOT_COVERED"


@dataclass(frozen=True)
class Check:
    status: Status
    text: str


@dataclass(frozen=True)
class Section:
    title: str
    gate: str
    status: Status
    checks: list[Check] = field(default_factory=list)


@dataclass(frozen=True)
class Spoken:
    """One line as a person would read it in the log."""

    speaker: str
    at: str
    text: str


@dataclass(frozen=True)
class Transition:
    kind: str
    t_ms: int


@dataclass(frozen=True)
class TransitionResult:
    kind: str
    t_ms: int
    status: Status
    note: str
    answer_gap_ms: int | None
    recovery_ms: int | None
    before: list[Spoken]
    after: list[Spoken]


@dataclass(frozen=True)
class Report:
    session_id: str
    sections: list[Section]
    transitions: list[TransitionResult]
    overall: Status


def fmt_time(ms: int) -> str:
    """``mm:ss``, or ``h:mm:ss`` past an hour."""
    seconds = max(ms, 0) // 1000
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def excerpt(text: str) -> str:
    """``text`` on one line, cut to ``EXCERPT_CHARS``."""
    flat = " ".join(text.split())
    return flat if len(flat) <= EXCERPT_CHARS else flat[: EXCERPT_CHARS - 3] + "..."


def spoken(u: Utterance) -> Spoken:
    """A line as the other side heard it, for the report."""
    return Spoken(u.speaker.value, fmt_time(u.t_ms), excerpt(u.heard_text))


def by_time(utterances: list[Utterance]) -> list[Utterance]:
    """Lines in the order they were said."""
    return sorted(utterances, key=lambda u: u.t_ms)


def end_ms(u: Utterance) -> int:
    """When a line finished, or when it started if its length wasn't recorded."""
    return u.t_ms + (u.duration_ms or 0)


def _number(value: JsonValue) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _count(stats: dict[str, JsonValue] | None, key: str) -> int | None:
    value = _number(stats.get(key)) if stats else None
    return None if value is None else int(value)


def parse_transitions(stats: dict[str, JsonValue] | None) -> list[Transition]:
    """The stats' transition list in time order. Malformed entries are skipped."""
    raw = stats.get("transitions") if stats else None
    if not isinstance(raw, list):
        return []
    found: list[Transition] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind, t_ms = item.get("kind"), _number(item.get("t_ms"))
        if not isinstance(kind, str) or kind not in TRANSITION_KINDS:
            continue
        if t_ms is None or t_ms < 0:
            continue
        found.append(Transition(kind, int(t_ms)))
    return sorted(found, key=lambda t: t.t_ms)


def call_ms(utterances: list[Utterance], stats: dict[str, JsonValue] | None) -> int:
    """The call's length: the worker's figure, or else when the last line ended."""
    seconds = _number(stats.get("call_seconds")) if stats else None
    if seconds is not None and seconds > 0:
        return int(seconds * 1000)
    return max((end_ms(u) for u in utterances), default=0)


def recovery_ms(index: int, transitions: list[Transition]) -> int | None:
    """How long the go-away or drop at ``index`` took to reconnect. A reconnect only counts
    when it comes next: a session rebuilt by a restart connects without one, and a later
    reconnect belongs to whatever came after."""
    transition = transitions[index]
    if transition.kind not in ("go_away", "drop") or index + 1 >= len(transitions):
        return None
    later = transitions[index + 1]
    return later.t_ms - transition.t_ms if later.kind == "reconnect" else None


def owed_line(transition: Transition, ordered: list[Utterance]) -> Utterance | None:
    """The agent line the customer has to answer after a transition. That's one she still owed
    an answer to when it hit (the agent was mid-line or had just finished), else the agent's
    next line."""
    owed: Utterance | None = None
    for u in ordered:
        if u.t_ms >= transition.t_ms:
            break
        if u.speaker is Speaker.LEARNER:
            owed = u
        elif owed is not None:
            owed = None  # she answered it
    if owed is not None and transition.t_ms - end_ms(owed) <= ANSWER_GAP_MS:
        return owed
    return next(
        (u for u in ordered if u.speaker is Speaker.LEARNER and u.t_ms >= transition.t_ms), None
    )


def check_transition(
    transition: Transition, ordered: list[Utterance], recovery: int | None
) -> TransitionResult:
    """Did the customer answer the agent's line in time? Character and recall are left to a
    person, who gets the lines around the transition."""
    learner = owed_line(transition, ordered)
    earlier = [u for u in ordered if u.t_ms < transition.t_ms and u is not learner]
    before = [spoken(u) for u in earlier[-2:]]
    if learner is None:
        return TransitionResult(
            transition.kind,
            transition.t_ms,
            Status.REVIEW,
            "no agent line after it",
            None,
            recovery,
            before,
            [],
        )
    customer = next(
        (u for u in ordered if u.speaker is Speaker.CUSTOMER and u.t_ms > learner.t_ms), None
    )
    after = [spoken(learner)] + ([spoken(customer)] if customer else [])
    if customer is None:
        return TransitionResult(
            transition.kind,
            transition.t_ms,
            Status.FAIL,
            "the customer never answered the agent's line",
            None,
            recovery,
            before,
            after,
        )
    gap = max(customer.t_ms - end_ms(learner), 0)
    if gap <= ANSWER_GAP_MS:
        status = Status.PASS
        note = f"answered {gap} ms after the agent finished (limit {ANSWER_GAP_MS} ms)"
    elif learner.duration_ms is None:
        # Lines saved before agent lines had a length: the gap includes the agent talking.
        status = Status.REVIEW
        note = f"answered {gap} ms after the agent started; the line has no end time"
    else:
        status = Status.FAIL
        note = f"answered {gap} ms after the agent finished (limit {ANSWER_GAP_MS} ms)"
    return TransitionResult(
        transition.kind, transition.t_ms, status, note, gap, recovery, before, after
    )


def _kind_counts(transitions: list[Transition]) -> str:
    counts = {k: sum(1 for t in transitions if t.kind == k) for k in sorted(TRANSITION_KINDS)}
    return ", ".join(f"{k} {n}" for k, n in counts.items() if n)


def call_section(
    utterances: list[Utterance], stats: dict[str, JsonValue] | None, transitions: list[Transition]
) -> Section:
    """The call's length, model, counts and errors, from the worker's stats."""
    gate = "A 60-minute call on a real microphone, through every GoAway and two forced drops."
    checks: list[Check] = []
    if stats is None:
        checks.append(Check(Status.REVIEW, "no voice stats were saved for this call"))
    else:
        model = stats.get("model")
        checks.append(
            Check(Status.PASS, f"model: {model if isinstance(model, str) else 'unknown'}")
        )
        if stats.get("final") is not True:
            checks.append(
                Check(Status.REVIEW, "stats are not final: the worker hadn't written its last row")
            )
    ms = call_ms(utterances, stats)
    checks.append(Check(Status.PASS, f"call length: {fmt_time(ms)}"))
    checks.append(Check(Status.PASS, f"transitions: {_kind_counts(transitions) or 'none'}"))
    for key in ("holds", "nudges"):
        n = _count(stats, key)
        checks.append(Check(Status.PASS, f"{key}: {n if n is not None else 'unknown'}"))
    errors = stats.get("errors") if stats else None
    if isinstance(errors, list) and errors:
        checks.append(Check(Status.REVIEW, f"errors: {len(errors)}"))
        checks.extend(Check(Status.REVIEW, f"  {excerpt(str(e))}") for e in errors)
    elif isinstance(errors, int | float) and not isinstance(errors, bool) and errors:
        checks.append(Check(Status.REVIEW, f"errors: {int(errors)}"))
    else:
        checks.append(Check(Status.PASS, "errors: none"))
    status = Status.REVIEW if any(c.status is Status.REVIEW for c in checks) else Status.PASS
    return Section("Call", gate, status, checks)


def transitions_section(
    results: list[TransitionResult], transitions: list[Transition], minutes: float
) -> Section:
    """Each go-away, drop, restart and rejoin, and whether the call had enough drops."""
    gate = (
        "After each transition the customer answers the next line within 5 s, stays in "
        "character, and recalls something said before it."
    )
    checks: list[Check] = []
    go_aways = sum(1 for t in transitions if t.kind == "go_away")
    drops = sum(1 for t in transitions if t.kind == "drop")
    checks.append(
        Check(
            Status.PASS,
            f"go_aways: {go_aways} (about {math.floor(minutes / GO_AWAY_EVERY_MIN)} expected "
            f"on a {minutes:.0f} minute call)",
        )
    )
    short = minutes >= FULL_CALL_MINUTES and drops < MIN_DROPS
    checks.append(
        Check(
            Status.FAIL if short else Status.PASS,
            f"drops: {drops} (gate wants at least {MIN_DROPS})",
        )
    )
    for i, t in enumerate(transitions):
        if t.kind in ("go_away", "drop"):
            recovery = recovery_ms(i, transitions)
            if recovery is not None:
                checks.append(
                    Check(
                        Status.PASS,
                        f"{t.kind} at {fmt_time(t.t_ms)} reconnected after {recovery} ms",
                    )
                )
            else:
                checks.append(
                    Check(
                        Status.REVIEW, f"{t.kind} at {fmt_time(t.t_ms)} has no reconnect after it"
                    )
                )
    for r in results:
        checks.append(Check(r.status, f"{r.kind} at {fmt_time(r.t_ms)}: {r.note}"))
    checks.append(
        Check(Status.REVIEW, "a person has to judge character and recall for each transition")
    )
    if short or any(r.status is Status.FAIL for r in results):
        status = Status.FAIL
    elif results:
        status = Status.REVIEW
    else:
        status = Status.NOT_COVERED
        checks.append(Check(Status.NOT_COVERED, "no transitions on this call"))
    return Section("Transitions", gate, status, checks)


def interruptions_section(
    utterances: list[Utterance], stats: dict[str, JsonValue] | None
) -> Section:
    """Every line that was cut off, with what was generated and what was heard."""
    gate = "Each interruption is logged with what was heard and what was generated."
    checks: list[Check] = []
    cut = [u for u in by_time(utterances) if u.interrupted or u.heard_text != u.text]
    failed = False
    for u in cut:
        label = f"{u.speaker.value} {fmt_time(u.t_ms)}: generated {excerpt(u.text)!r}, heard "
        label += repr(excerpt(u.heard_text))
        if not u.text.strip():
            failed = True
            checks.append(Check(Status.FAIL, f"{label} (no generated text)"))
        elif u.interrupted and u.heard_text == u.text:
            # A cut on the last word is real; a generated text that was never captured looks the
            # same, so a person checks it.
            checks.append(
                Check(Status.REVIEW, f"{label} (heard text equals the full text; check it was cut)")
            )
        else:
            checks.append(Check(Status.REVIEW, label))
    customer_cuts = sum(1 for u in cut if u.interrupted and u.speaker is Speaker.CUSTOMER)
    barge_ins = _count(stats, "barge_ins")
    if barge_ins is not None and barge_ins != customer_cuts:
        checks.append(
            Check(
                Status.REVIEW,
                f"stats count {barge_ins} barge-ins, the log has {customer_cuts} interrupted "
                "customer lines (holds can also cut a line)",
            )
        )
    checks.append(
        Check(Status.NOT_COVERED, "made-up-number fixture: needs its own run, not judged here")
    )
    if failed:
        status = Status.FAIL
    elif cut:
        status = Status.REVIEW
    else:
        status = Status.NOT_COVERED
        checks.insert(0, Check(Status.NOT_COVERED, "no interruptions on this call"))
    return Section("Interruptions", gate, status, checks)


def normalize(text: str) -> str:
    """Lowercase words without punctuation, for comparing two lines."""
    words = re.sub(r"[^\w\s]", "", text.lower())
    return " ".join(words.split())


def evidence_section(utterances: list[Utterance]) -> Section:
    """Gaps, repeats and ordering in the log. ``utterances`` must be in the order they were
    saved, which is how the store returns them."""
    gate = "Complete, ordered by audio time across every transition, no turn missing or duplicated."
    checks: list[Check] = []
    ordered = by_time(utterances)
    failed = False

    for u in ordered:
        if not u.text.strip():
            failed = True
            checks.append(Check(Status.FAIL, f"empty text: {u.speaker.value} {fmt_time(u.t_ms)}"))

    for i, a in enumerate(ordered):
        key = normalize(a.text)
        if len(key.split()) < DUPLICATE_MIN_WORDS:
            continue
        for b in ordered[i + 1 :]:
            if b.t_ms - a.t_ms > DUPLICATE_WINDOW_MS:
                break
            if b.speaker is a.speaker and normalize(b.text) == key:
                where = f"{a.speaker.value} line at {fmt_time(a.t_ms)} and {fmt_time(b.t_ms)}"
                if b.t_ms <= end_ms(a):
                    # One line saved twice: the copy starts before the first one ends.
                    failed = True
                    checks.append(Check(Status.FAIL, f"duplicate {where}: {excerpt(a.text)!r}"))
                else:
                    # People repeat themselves, most of all after a drop or a barge-in.
                    checks.append(Check(Status.REVIEW, f"repeated {where}: {excerpt(a.text)!r}"))

    for speaker in Speaker:
        mine = [u for u in ordered if u.speaker is speaker]
        for prev, cur in zip(mine, mine[1:], strict=False):
            if cur.t_ms < end_ms(prev) - OVERLAP_SLACK_MS:
                over = end_ms(prev) - cur.t_ms
                text = (
                    f"{speaker.value} line at {fmt_time(cur.t_ms)} starts {over} ms before "
                    "the previous one ends"
                )
                if speaker is Speaker.CUSTOMER:
                    failed = True
                    checks.append(Check(Status.FAIL, text))
                else:
                    # Agent times come from the mic level, so two lines Gemini split out of one
                    # stretch of speech share it.
                    checks.append(Check(Status.REVIEW, f"{text} (one stretch of speech)"))

    late, latest = 0, -1
    for u in utterances:
        if u.t_ms < latest:
            late += 1
        latest = max(latest, u.t_ms)
    checks.append(
        Check(
            Status.PASS,
            f"late writes: {late} lines were saved after a line said later (expected, the "
            "grader orders voice lines by audio time)",
        )
    )

    runs = [
        f"{cur.speaker.value} x2 at {fmt_time(prev.t_ms)} and {fmt_time(cur.t_ms)}"
        for prev, cur in zip(ordered, ordered[1:], strict=False)
        if prev.speaker is cur.speaker
    ]
    checks.append(
        Check(
            Status.PASS,
            f"back-to-back turns by one side: {len(runs)} (pauses, nudges and barge-ins cause "
            "these)",
        )
    )
    checks.extend(Check(Status.PASS, f"  {r}") for r in runs)
    checks.append(
        Check(
            Status.REVIEW,
            "a missing turn can't be proven without the audio: a person has to compare the "
            "log with the recording",
        )
    )
    return Section("Evidence log", gate, Status.FAIL if failed else Status.REVIEW, checks)


def latency_section(stats: dict[str, JsonValue] | None) -> Section:
    """The worker's reply-delay figures against the gate's targets."""
    gate = f"Median <= {LATENCY_MEDIAN_S} s and p95 <= {LATENCY_P95_S} s."
    checks: list[Check] = []
    median = _number(stats.get("latency_median_s")) if stats else None
    p95 = _number(stats.get("latency_p95_s")) if stats else None
    turns = _count(stats, "turns_timed")
    for label, value, limit in (
        ("median", median, LATENCY_MEDIAN_S),
        ("p95", p95, LATENCY_P95_S),
    ):
        if value is None:
            checks.append(Check(Status.NOT_COVERED, f"{label}: not measured"))
        else:
            ok = value <= limit
            checks.append(
                Check(
                    Status.PASS if ok else Status.FAIL,
                    f"{label}: {value:.2f} s (target {limit} s)",
                )
            )
    if turns is not None:
        checks.append(Check(Status.PASS, f"turns timed: {turns}"))
    basis = stats.get("latency_basis") if stats else None
    if isinstance(basis, str):
        checks.append(Check(Status.PASS, f"measured on the worker: {basis}"))
    checks.append(
        Check(Status.NOT_COVERED, "direct control run in the same window: needs separate runs")
    )
    checks.append(
        Check(Status.NOT_COVERED, "end-of-speech clipping on number-heavy lines: needs its own run")
    )
    judged = [c.status for c in checks[:2]]
    if Status.FAIL in judged:
        status = Status.FAIL
    elif Status.PASS in judged:
        status = Status.PASS
    else:
        status = Status.NOT_COVERED
    return Section("Latency", gate, status, checks)


def not_covered_section() -> Section:
    """The gate items one call can't show, listed so the report doesn't read as complete."""
    gate = "These need more than one call."
    lines = [
        "concurrency: as many calls at once as a class would run",
        "direct against worker: the control run alternated in the same window",
        "made-up-number fixture: proof she doesn't refer back to unheard words",
        "real network and browser: this report only sees what the worker logged",
    ]
    checks = [Check(Status.NOT_COVERED, line) for line in lines]
    return Section("Not covered by one call", gate, Status.NOT_COVERED, checks)


def overall_status(sections: list[Section]) -> Status:
    """FAIL if any section failed, else REVIEW if any needs a person, else PASS."""
    statuses = {s.status for s in sections}
    if Status.FAIL in statuses:
        return Status.FAIL
    if Status.REVIEW in statuses:
        return Status.REVIEW
    return Status.PASS


def build_report(
    session_id: str,
    utterances: list[Utterance],
    events: list[Event],
    stats: dict[str, JsonValue] | None,
) -> Report:
    """Every gate section for one call. ``events`` is accepted for the log's whole record but the
    checks read only utterances and stats, since voice lines carry their own audio times."""
    voice = [u for u in utterances if u.channel is Channel.VOICE] or utterances
    ordered = by_time(voice)
    transitions = parse_transitions(stats)
    results = [
        check_transition(t, ordered, recovery_ms(i, transitions))
        for i, t in enumerate(transitions)
        if t.kind in JUDGED_KINDS
    ]
    minutes = call_ms(voice, stats) / 60000
    sections = [
        call_section(voice, stats, transitions),
        transitions_section(results, transitions, minutes),
        interruptions_section(voice, stats),
        evidence_section(voice),
        latency_section(stats),
        not_covered_section(),
    ]
    return Report(session_id, sections, results, overall_status(sections))


def _render_spoken(prefix: str, lines: list[Spoken]) -> list[str]:
    return [f"      {prefix} {s.at} {s.speaker}: {s.text}" for s in lines]


def render_text(report: Report) -> str:
    """The report as a person reads it in a terminal."""
    out = [f"M4 acceptance gate report for session {report.session_id}", ""]
    for number, section in enumerate(report.sections, 1):
        out.append(f"{number}. {section.title}: {section.status}")
        out.append(f"   gate: {section.gate}")
        out.extend(f"   [{c.status}] {c.text}" for c in section.checks)
        if section.title == "Transitions":
            for r in report.transitions:
                out.append(f"   {r.kind} at {fmt_time(r.t_ms)} ({r.status})")
                if r.recovery_ms is not None:
                    out.append(f"      recovery: {r.recovery_ms} ms")
                out.extend(_render_spoken("before:", r.before))
                out.extend(_render_spoken("after: ", r.after))
        out.append("")
    out.append("Summary")
    width = max(len(s.title) for s in report.sections)
    for s in report.sections:
        out.append(f"  {s.title.ljust(width)}  {s.status}")
    out.append(f"Overall: {report.overall}")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    """Prints the report. Exits 0 when it ran, whatever it found, and 2 on bad input."""
    args = [a for a in argv if a != "--json"]
    as_json = len(args) != len(argv)
    if len(args) != 1:
        print("usage: python -m app.voice.gate <session_id> [--json]", file=sys.stderr)
        return 2
    path = os.environ.get(DB_PATH_ENV, DEFAULT_DB_PATH)
    if not os.path.exists(path):
        print(f"no database at {path} (set {DB_PATH_ENV})", file=sys.stderr)
        return 2
    store = Store(path)
    try:
        row = store.get_session(args[0])
        if row is None:
            print(f"unknown session {args[0]}", file=sys.stderr)
            return 2
        if row.channel is not Channel.VOICE:
            print(f"session {args[0]} is not a voice call", file=sys.stderr)
            return 2
        report = build_report(
            args[0],
            store.utterances(args[0]),
            store.events(args[0]),
            store.get_voice_stats(args[0]),
        )
    finally:
        store.close()
    print(json.dumps(dataclasses.asdict(report), indent=2) if as_json else render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
