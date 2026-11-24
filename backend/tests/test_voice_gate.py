"""The M4 gate report's checks, on hand-built calls."""

from pydantic import JsonValue

from app.engine.events import Channel, Speaker, Utterance
from app.voice.gate import (
    ANSWER_GAP_MS,
    DUPLICATE_WINDOW_MS,
    OVERLAP_SLACK_MS,
    Section,
    Status,
    build_report,
    evidence_section,
    fmt_time,
    interruptions_section,
    latency_section,
    overall_status,
    parse_transitions,
    render_text,
)

L = Speaker.LEARNER
C = Speaker.CUSTOMER


def utt(
    n: int,
    speaker: Speaker,
    t_ms: int,
    text: str = "",
    *,
    heard: str | None = None,
    duration_ms: int | None = 1000,
    interrupted: bool = False,
) -> Utterance:
    body = text or f"line number {n}"
    return Utterance(
        utt_id=f"u{n:04d}",
        session_id="s1",
        speaker=speaker,
        channel=Channel.VOICE,
        text=body,
        heard_text=body if heard is None else heard,
        t_ms=t_ms,
        duration_ms=duration_ms,
        interrupted=interrupted,
    )


def stats_with(*transitions: JsonValue, **extra: JsonValue) -> dict[str, JsonValue]:
    out: dict[str, JsonValue] = {"final": True, "model": "m", "transitions": list(transitions)}
    out.update(extra)
    return out


def transition(kind: str, t_ms: int) -> JsonValue:
    return {"kind": kind, "t_ms": t_ms}


def section(report_sections: list[Section], title: str) -> Section:
    return next(s for s in report_sections if s.title == title)


def test_a_transition_answered_in_time_passes() -> None:
    lines = [
        utt(1, C, 0),
        utt(2, L, 10_000, duration_ms=2000),
        utt(3, C, 12_000 + ANSWER_GAP_MS),
    ]
    report = build_report("s1", lines, [], stats_with(transition("drop", 9000)))
    (result,) = report.transitions
    assert result.status is Status.PASS
    assert result.answer_gap_ms == ANSWER_GAP_MS
    assert [s.speaker for s in result.after] == ["learner", "customer"]
    assert len(result.before) == 1


def test_a_late_or_missing_answer_fails() -> None:
    late = [utt(1, L, 10_000, duration_ms=2000), utt(2, C, 12_000 + ANSWER_GAP_MS + 1)]
    report = build_report("s1", late, [], stats_with(transition("restart", 9000)))
    assert report.transitions[0].status is Status.FAIL
    assert section(report.sections, "Transitions").status is Status.FAIL

    silent = [utt(1, L, 10_000)]
    report = build_report("s1", silent, [], stats_with(transition("rejoin", 9000)))
    assert report.transitions[0].status is Status.FAIL


def test_no_agent_line_after_a_transition_is_review() -> None:
    report = build_report("s1", [utt(1, C, 0)], [], stats_with(transition("go_away", 5000)))
    (result,) = report.transitions
    assert result.status is Status.REVIEW
    assert result.note == "no agent line after it"
    assert section(report.sections, "Transitions").status is Status.REVIEW


def test_a_customer_line_that_overlaps_the_agent_end_clamps_to_zero() -> None:
    lines = [utt(1, L, 10_000, duration_ms=4000), utt(2, C, 12_000)]
    report = build_report("s1", lines, [], stats_with(transition("drop", 9000)))
    assert report.transitions[0].answer_gap_ms == 0


def test_go_away_pairs_with_the_next_reconnect() -> None:
    lines = [utt(1, L, 10_000), utt(2, C, 11_500)]
    stats = stats_with(transition("go_away", 8000), transition("reconnect", 8600))
    report = build_report("s1", lines, [], stats)
    assert [r.kind for r in report.transitions] == ["go_away"]
    assert report.transitions[0].recovery_ms == 600
    texts = [c.text for c in section(report.sections, "Transitions").checks]
    assert any("reconnected after 600 ms" in t for t in texts)


def test_malformed_transitions_are_skipped() -> None:
    stats = stats_with(
        transition("drop", 1000),
        {"kind": "nope", "t_ms": 5},
        {"kind": "drop"},
        {"kind": "drop", "t_ms": "5"},
        {"kind": "drop", "t_ms": True},
        {"kind": "drop", "t_ms": -4},
        "drop",
        None,
    )
    assert [(t.kind, t.t_ms) for t in parse_transitions(stats)] == [("drop", 1000)]
    assert parse_transitions({"transitions": "x"}) == []


def test_stats_without_transitions_are_not_covered() -> None:
    report = build_report("s1", [utt(1, L, 0)], [], {"final": True})
    assert section(report.sections, "Transitions").status is Status.NOT_COVERED
    report = build_report("s1", [utt(1, L, 0)], [], None)
    assert section(report.sections, "Transitions").status is Status.NOT_COVERED


def test_a_long_call_with_too_few_drops_fails() -> None:
    lines = [utt(1, L, 0), utt(2, C, 1000)]
    stats = stats_with(transition("drop", 500_000), call_seconds=3600)
    report = build_report("s1", lines, [], stats)
    assert section(report.sections, "Transitions").status is Status.FAIL
    short = stats_with(transition("drop", 500_000), call_seconds=600)
    report = build_report("s1", lines, [], short)
    assert section(report.sections, "Transitions").status is not Status.FAIL


def test_an_interrupted_line_heard_in_full_is_flagged_for_review() -> None:
    cut = utt(1, C, 0, "I can do Tuesday afternoon", heard="I can do", interrupted=True)
    whole = utt(2, C, 5000, "Sounds fine", interrupted=True)
    assert interruptions_section([cut], {"barge_ins": 1}).status is Status.REVIEW
    flagged = interruptions_section([cut, whole], {"barge_ins": 2})
    assert flagged.status is Status.REVIEW
    assert any(c.status is Status.REVIEW and "heard text equals" in c.text for c in flagged.checks)
    blank = utt(3, C, 9000, " ", heard="", interrupted=True)
    assert interruptions_section([blank], {"barge_ins": 1}).status is Status.FAIL


def test_barge_in_count_mismatch_is_review_not_fail() -> None:
    cut = utt(1, C, 0, "I can do Tuesday", heard="I can", interrupted=True)
    result = interruptions_section([cut], {"barge_ins": 3})
    assert result.status is Status.REVIEW
    assert any("3 barge-ins" in c.text for c in result.checks)
    assert interruptions_section([utt(1, C, 0)], {}).status is Status.NOT_COVERED


def test_duplicates_are_caught_inside_the_window_only() -> None:
    line, same = "Is that the one on Main Street?", "is that the one on main street"
    inside = [utt(1, C, 0, line), utt(2, C, DUPLICATE_WINDOW_MS, same)]
    assert evidence_section(inside).status is Status.FAIL
    outside = [utt(1, C, 0, line), utt(2, C, DUPLICATE_WINDOW_MS + 1, same)]
    assert evidence_section(outside).status is Status.REVIEW
    short = [utt(1, C, 0, "Okay, sure."), utt(2, C, 4000, "okay sure")]
    assert evidence_section(short).status is Status.REVIEW
    other_side = [utt(1, C, 0, "Hello"), utt(2, L, 1000, "Hello")]
    assert evidence_section(other_side).status is Status.REVIEW


def test_same_speaker_overlap_beyond_slack_fails() -> None:
    ok = [utt(1, C, 0, duration_ms=2000), utt(2, C, 2000 - OVERLAP_SLACK_MS)]
    assert evidence_section(ok).status is Status.REVIEW
    bad = [utt(1, C, 0, duration_ms=2000), utt(2, C, 2000 - OVERLAP_SLACK_MS - 1)]
    assert evidence_section(bad).status is Status.FAIL


def test_empty_text_fails_the_log() -> None:
    blank = utt(1, C, 0).model_copy(update={"text": " ", "heard_text": " "})
    assert evidence_section([blank]).status is Status.FAIL


def test_late_writes_are_counted_but_do_not_fail() -> None:
    written = [utt(1, L, 0), utt(2, C, 5000), utt(3, L, 3000)]
    result = evidence_section(written)
    assert result.status is Status.REVIEW
    assert any(c.text.startswith("late writes: 2") for c in result.checks)
    assert any(c.text.startswith("late writes: 0") for c in evidence_section([utt(1, L, 0)]).checks)


def test_back_to_back_turns_are_listed() -> None:
    lines = [utt(1, L, 0), utt(2, L, 3000), utt(3, C, 6000)]
    result = evidence_section(lines)
    assert any("back-to-back turns by one side: 1" in c.text for c in result.checks)
    assert any("learner x2 at 00:00 and 00:03" in c.text for c in result.checks)


def test_latency_pass_fail_and_missing() -> None:
    ok = latency_section({"latency_median_s": 1.0, "latency_p95_s": 2.0, "turns_timed": 30})
    assert ok.status is Status.PASS
    slow = latency_section({"latency_median_s": 1.0, "latency_p95_s": 2.6})
    assert slow.status is Status.FAIL
    assert latency_section({}).status is Status.NOT_COVERED
    assert latency_section(None).status is Status.NOT_COVERED


def test_overall_status_rolls_up() -> None:
    def sec(status: Status) -> Section:
        return Section("t", "g", status)

    assert overall_status([sec(Status.PASS), sec(Status.NOT_COVERED)]) is Status.PASS
    assert overall_status([sec(Status.PASS), sec(Status.REVIEW)]) is Status.REVIEW
    assert overall_status([sec(Status.REVIEW), sec(Status.FAIL)]) is Status.FAIL


def test_times_render_as_minutes_and_hours() -> None:
    assert fmt_time(0) == "00:00"
    assert fmt_time(65_000) == "01:05"
    assert fmt_time(3_599_999) == "59:59"
    assert fmt_time(3_600_000) == "1:00:00"
    assert fmt_time(3_725_000) == "1:02:05"


def test_the_text_report_has_every_section() -> None:
    lines = [utt(1, L, 0), utt(2, C, 1500)]
    report = build_report("s1", lines, [], stats_with(transition("drop", 100)))
    text = render_text(report)
    for title in (
        "Call",
        "Transitions",
        "Interruptions",
        "Evidence log",
        "Latency",
        "Not covered by one call",
        "Summary",
        "Overall:",
    ):
        assert title in text
