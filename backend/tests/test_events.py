"""The utterance contract, and the quote check that keeps judge evidence honest."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.engine.events import Channel, Event, EventType, Speaker, Utterance, UtteranceIn


def utterance(heard: str, *, interrupted: bool = False) -> Utterance:
    return Utterance(
        utt_id="u0007",
        session_id="s1",
        speaker=Speaker.CUSTOMER,
        channel=Channel.VOICE,
        text="Yes, go ahead and sign me up for that plan.",
        heard_text=heard,
        t_ms=61_200,
        interrupted=interrupted,
    )


def test_text_turn_is_heard_as_typed() -> None:
    body = UtteranceIn(
        client_utt_id="c-00000001", speaker=Speaker.LEARNER, channel=Channel.TEXT, text="Hi there"
    )
    stored = Utterance.from_input(body, utt_id="u0001", session_id="s1", t_ms=1500)
    assert stored.heard_text == "Hi there"
    assert stored.t_ms == 1500


def test_text_turns_cannot_be_interrupted_or_rewritten() -> None:
    common = {"client_utt_id": "c-00000001", "speaker": "learner", "channel": "text", "text": "Hi"}
    with pytest.raises(ValidationError, match="interrupted"):
        UtteranceIn.model_validate(common | {"interrupted": True})
    with pytest.raises(ValidationError, match="heard exactly"):
        UtteranceIn.model_validate(common | {"heard_text": "Hello"})


def test_voice_turn_keeps_what_was_heard() -> None:
    body = UtteranceIn(
        client_utt_id="v-00000001",
        speaker=Speaker.CUSTOMER,
        channel=Channel.VOICE,
        text="Yes, go ahead and sign me up.",
        heard_text="Yes, go ahead",
        t_ms=90_000,
        interrupted=True,
    )
    stored = Utterance.from_input(body, utt_id="u0002", session_id="s1", t_ms=123)
    assert stored.heard_text == "Yes, go ahead"
    assert stored.t_ms == 90_000


def test_quote_must_appear_in_heard_text() -> None:
    u = utterance("Yes, go ahead and sign me up for that plan.")
    assert u.contains_quote("go ahead and SIGN me up")
    assert u.contains_quote("“Yes, go ahead”")
    assert not u.contains_quote("Yes, please enroll me")
    assert not u.contains_quote("   ")
    # Whole words only: a short quote can't be found inside a longer word.
    assert u.contains_quote("go ahead") and not u.contains_quote("o ahead and sig")
    assert not u.contains_quote("head")


def test_a_quote_cannot_drop_the_part_of_a_word_that_changes_its_meaning() -> None:
    cannot = utterance("No, you can't keep that doctor, and it's non-preferred on that plan.")
    for partial in ("you can", "preferred on that plan", "it", "can't keep that doc"):
        assert not cannot.contains_quote(partial), partial
    for whole in ("you can't keep that doctor", "it's non-preferred", "non-preferred on that plan"):
        assert cannot.contains_quote(whole), whole
    # Quote marks and dashes around a phrase are punctuation, not part of a word.
    said = utterance("She said 'go ahead' and then - well - 'sign me up'.")
    assert said.contains_quote("go ahead") and said.contains_quote("sign me up")
    assert said.contains_quote("and then - well")


def test_cut_off_speech_does_not_count() -> None:
    cut = utterance("Yes, go", interrupted=True)
    assert cut.delivered
    assert not cut.contains_quote("sign me up")
    assert not utterance("", interrupted=True).delivered


def test_only_utterance_events_link_an_utterance() -> None:
    now = datetime(2026, 11, 12, 15, 0, tzinfo=UTC)
    Event(session_id="s1", seq=1, type=EventType.UTTERANCE, at=now, t_ms=0, utt_id="u0001")
    with pytest.raises(ValidationError, match="utt_id"):
        Event(session_id="s1", seq=2, type=EventType.UTTERANCE, at=now, t_ms=0)
    with pytest.raises(ValidationError, match="utt_id"):
        Event(session_id="s1", seq=3, type=EventType.SEARCH, at=now, t_ms=0, utt_id="u0001")


def test_event_clock_must_be_timezone_aware() -> None:
    with pytest.raises(ValidationError):
        Event(
            session_id="s1", seq=1, type=EventType.CALL_STARTED, at=datetime(2026, 11, 12), t_ms=0
        )
