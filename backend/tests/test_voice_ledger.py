"""The voice worker's bookkeeping: spoken order, heard text, speech timing and cost."""

import math

import pytest

from app.engine.events import Channel, Speaker, UtteranceIn
from app.voice.ledger import (
    Ledger,
    SpeechTracker,
    VoiceStats,
    client_utt_id,
    heard_note,
    hold_note,
    instructions,
    level_db,
)

T0 = 1_000.0  # call start on the worker's clock


def texts(lines: list[UtteranceIn]) -> list[tuple[str, str]]:
    return [(u.speaker.value, u.text) for u in lines]


def test_lines_save_in_spoken_order_when_the_agent_transcript_is_late() -> None:
    ledger = Ledger(T0)
    ledger.learner_heard("agent-1", "Do I have your", at=T0 + 1.0)
    # The customer answers before Gemini finishes transcribing the agent.
    assert (
        ledger.customer_line(
            "cust-1",
            heard="Yes, sign me up.",
            generated="Yes, sign me up.",
            started_at=T0 + 4.0,
            ended_at=T0 + 5.5,
            interrupted=False,
            now=T0 + 5.6,
        )
        == []
    )
    out = ledger.learner_final(
        "agent-1", "Do I have your permission to enroll you?", started_at=T0 + 0.8, now=T0 + 6.0
    )
    assert texts(out) == [
        ("learner", "Do I have your permission to enroll you?"),
        ("customer", "Yes, sign me up."),
    ]
    agent, customer = out
    assert (agent.channel, agent.t_ms, agent.heard_text) == (Channel.VOICE, 800, agent.text)
    assert (customer.t_ms, customer.duration_ms) == (4000, 1500)
    assert ledger.pending == 0


def test_a_barge_in_keeps_what_was_generated_and_what_was_heard() -> None:
    ledger = Ledger(T0)
    (line,) = ledger.customer_line(
        "cust-1",
        heard="Well, yes,",
        generated="Well, yes, go ahead and sign me up.",
        started_at=T0 + 2.0,
        ended_at=T0 + 2.9,
        interrupted=True,
        now=T0 + 3.0,
    )
    assert (line.speaker, line.interrupted) == (Speaker.CUSTOMER, True)
    assert line.text == "Well, yes, go ahead and sign me up."
    assert line.heard_text == "Well, yes,"


def test_a_line_cut_off_before_any_of_it_played_is_still_saved() -> None:
    ledger = Ledger(T0)
    (line,) = ledger.customer_line(
        "cust-1",
        heard="",
        generated="Yes, go ahead.",
        started_at=T0 + 2.0,
        ended_at=None,
        interrupted=True,
        now=T0 + 2.1,
    )
    assert (line.text, line.heard_text, line.duration_ms) == ("Yes, go ahead.", "", None)
    assert (
        ledger.customer_line(
            "cust-2",
            heard="",
            generated=None,
            started_at=T0 + 3,
            ended_at=None,
            interrupted=True,
            now=T0 + 3,
        )
        == []
    )


def test_an_agent_line_that_starts_after_the_customer_doesnt_hold_her_line() -> None:
    ledger = Ledger(T0)
    ledger.learner_heard("agent-1", "Sorry, go", at=T0 + 3.0)  # talks over her
    out = ledger.customer_line(
        "cust-1",
        heard="I was",
        generated="I was saying",
        started_at=T0 + 2.0,
        ended_at=T0 + 3.1,
        interrupted=True,
        now=T0 + 3.2,
    )
    assert texts(out) == [("customer", "I was saying")]


def test_a_held_line_goes_out_when_the_agent_transcript_never_comes() -> None:
    ledger = Ledger(T0, hold_s=4.0)
    ledger.learner_heard("agent-1", "Let me check", at=T0 + 1.0)
    ledger.customer_line(
        "cust-1",
        heard="Okay.",
        generated="Okay.",
        started_at=T0 + 3.0,
        ended_at=T0 + 3.5,
        interrupted=False,
        now=T0 + 3.6,
    )
    assert ledger.due(T0 + 5.0) == []
    assert texts(ledger.due(T0 + 7.7)) == [("customer", "Okay.")]
    # The agent line still saves when its transcript turns up.
    assert texts(ledger.learner_final("agent-1", "Let me check that.", T0 + 0.9, T0 + 9)) == [
        ("learner", "Let me check that.")
    ]


def test_flush_saves_interim_agent_lines_in_order() -> None:
    ledger = Ledger(T0)
    ledger.learner_heard("agent-1", "Thanks for", at=T0 + 1.0)
    ledger.learner_heard("agent-1", "Thanks for your time today", at=T0 + 1.5)
    ledger.customer_line(
        "cust-1",
        heard="Bye now.",
        generated="Bye now.",
        started_at=T0 + 3.0,
        ended_at=T0 + 3.4,
        interrupted=False,
        now=T0 + 3.5,
    )
    out = ledger.flush(T0 + 4.0)
    assert texts(out) == [("learner", "Thanks for your time today"), ("customer", "Bye now.")]
    assert ledger.pending == 0
    assert ledger.flush(T0 + 5.0) == []


def test_flush_dates_lines_from_the_agent_audio() -> None:
    ledger = Ledger(T0, start_of=lambda t: t - 0.8)
    ledger.learner_heard("agent-1", "Great, I'll submit that now", at=T0 + 2.0)
    (line,) = ledger.flush(T0 + 3)
    assert (line.text, line.t_ms) == ("Great, I'll submit that now", 1200)


def test_each_line_saves_once() -> None:
    ledger = Ledger(T0)
    ledger.skip({"seed-u0001"})
    assert ledger.learner_final("seed-u0001", "Hello", T0, T0) == []
    assert len(ledger.learner_final("agent-1", "Hello", T0, T0)) == 1
    assert ledger.learner_final("agent-1", "Hello", T0, T0) == []
    ledger.learner_heard("agent-1", "Hello again", at=T0 + 1)
    assert ledger.pending == 0


def test_times_before_the_call_started_clamp_to_zero() -> None:
    (line,) = Ledger(T0).learner_final("agent-1", "Hi", started_at=T0 - 2, now=T0)
    assert line.t_ms == 0


@pytest.mark.parametrize(
    ("item_id", "expected"),
    [("GR_abc123", "lk-GR_abc123"), ("item/with spaces", "lk-item-with-spaces")],
)
def test_client_ids_are_safe_and_stable(item_id: str, expected: str) -> None:
    assert client_utt_id(item_id) == expected
    assert len(client_utt_id("x" * 200)) == 64


def test_notes_are_framed_as_instructions() -> None:
    assert instructions("x") == "<instructions>\nx\n</instructions>"
    assert 'heard you say was: "Well, yes,"' in heard_note(" Well, yes, ")
    assert "didn't hear any of it" in heard_note("")
    assert "about 42 seconds" in hold_note(41.7)
    assert "cut you off" not in hold_note(5)
    assert 'all they heard you say was "My doctor is"' in hold_note(5, " My doctor is ")
    assert "before they heard any" in hold_note(5, "")


def test_speech_runs_merge_short_pauses() -> None:
    tracker = SpeechTracker(threshold_db=-45, merge_gap_s=0.7)
    for i in range(10):  # 1.0-2.0 s talking
        tracker.frame(1.1 + i * 0.1, 0.1, -20)
    tracker.frame(2.5, 0.1, -60)  # quiet frames don't count
    tracker.frame(2.6, 0.1, -20)  # 0.5 s pause: same run
    tracker.frame(4.0, 0.1, -20)  # 1.4 s pause: new run
    assert [(round(r.start, 1), round(r.end, 1)) for r in tracker.runs] == [
        (1.0, 2.6),
        (3.9, 4.0),
    ]
    assert tracker.last_voice == 4.0
    assert tracker.run_started_before(3.0) is tracker.runs[0]
    assert tracker.run_ended_before(3.95) is tracker.runs[0]
    assert tracker.run_started_before(0.5) is None
    assert tracker.speech_start(3.0, max_s=60) == pytest.approx(1.0)
    assert tracker.speech_start(3.0, max_s=1) == 3.0  # that run began too long before
    assert tracker.speech_start(0.5, max_s=60) == 0.5


def test_level_db() -> None:
    assert level_db([]) == -math.inf
    assert level_db([0, 0]) == -math.inf
    assert level_db([32767, -32767]) == pytest.approx(0, abs=0.01)
    assert level_db([328, -328]) == pytest.approx(-40, abs=0.1)


def stats() -> VoiceStats:
    return VoiceStats(model="m", silence_ms=700, price_in_per_min=0.005, price_out_per_min=0.018)


def test_latency_is_timed_from_the_end_of_the_agent_speaking() -> None:
    s, tracker = stats(), SpeechTracker()
    tracker.frame(10.0, 2.0, -20)  # agent talks 8-10 s
    s.answered(tracker, started_at=10.9, after=None)
    s.answered(tracker, started_at=12.0, after=10.9)  # no agent speech since her last line
    tracker.frame(30.0, 1.0, -20)
    s.answered(tracker, started_at=45.0, after=12.0)  # a nudge after a long silence
    assert s.latencies == [pytest.approx(0.9)]


def test_stats_json() -> None:
    s = stats()
    s.latencies = [0.6, 0.8, 1.0, 2.4]
    s.connects, s.restarts, s.barge_ins = 3, 1, 2
    s.usage(input_audio=3000, input_text=500, cached=0, output_audio=1500, output_text=80)
    s.usage(input_audio=1500, input_text=0, cached=100, output_audio=0, output_text=0)
    for i in range(30):
        s.error(f"e{i}")
    data = s.to_json(call_seconds=185.04)
    assert data["call_seconds"] == 185.0
    assert (data["latency_median_s"], data["latency_p95_s"], data["latency_max_s"]) == (
        0.9,
        2.4,
        2.4,
    )
    assert (data["reconnects"], data["session_restarts"], data["barge_ins"]) == (1, 1, 2)
    assert data["audio_minutes"] == {"input": 3.0, "output": 1.0}
    assert data["cost_usd_estimate"] == pytest.approx(3 * 0.005 + 1 * 0.018)
    assert data["tokens"] == {
        "input_audio": 4500,
        "input_text": 500,
        "input_cached": 100,
        "output_audio": 1500,
        "output_text": 80,
    }
    assert len(data["errors"]) == 20  # type: ignore[arg-type]
    assert stats().to_json(0)["latency_median_s"] is None
    assert data["final"] is False
    assert s.to_json(1, final=True)["final"] is True


def test_a_worker_sent_back_adds_to_the_figures_before_it() -> None:
    first = stats()
    first.latencies = [1.0, 2.0]
    first.connects, first.barge_ins, first.nudges = 2, 1, 1  # one reconnect
    first.usage(input_audio=1500, input_text=10, cached=0, output_audio=1500, output_text=0)
    first.error("e1")
    saved = first.to_json(60, final=True)

    second = stats()
    second.carry(saved)
    second.latencies.append(3.0)
    second.connects = 1
    second.usage(input_audio=1500, input_text=0, cached=0, output_audio=0, output_text=0)
    data = second.to_json(120)
    assert data["latencies_s"] == [1.0, 2.0, 3.0]
    assert data["latency_median_s"] == 2.0
    # Its own arrival counts as a session restart, not a reconnect.
    assert (data["reconnects"], data["session_restarts"]) == (1, 1)
    assert (data["barge_ins"], data["nudges"]) == (1, 1)
    assert data["errors"] == ["e1"]
    assert data["cost_usd_estimate"] == pytest.approx(2 * 0.005 + 1 * 0.018)
    assert data["final"] is False
