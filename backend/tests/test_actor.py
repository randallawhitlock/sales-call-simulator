"""The AI customer: prompt, leak guard, actors and the turn endpoint."""

import asyncio
import os
import threading
import time
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.actor.actors import (
    ActorConfigError,
    ActorError,
    GeminiActor,
    ScriptedActor,
    Turn,
    actor_from_env,
    actor_timeout_from_env,
)
from app.actor.prompt import (
    PROMPT_VERSION,
    CustomerPrompt,
    build_customer_prompt,
    check_all_prompts,
    prompt_problems,
)
from app.content.loader import load_bundle
from app.content.scenario import Reveal
from app.engine.events import EventType, Speaker
from app.engine.session import TurnIn
from tests.harness import Call

BUNDLE = load_bundle()
CASE_A = "scenario-101"


def prompt_for(scenario_id: str = CASE_A, mood: str | None = None) -> CustomerPrompt:
    s = BUNDLE.scenario(scenario_id)
    return build_customer_prompt(s, BUNDLE.frame, mood or s.persona.moods[0])


# Prompt


def test_every_prompt_passes_the_leak_guard() -> None:
    assert check_all_prompts(BUNDLE) == []


@pytest.mark.parametrize("scenario_id", sorted(BUNDLE.scenarios))
def test_prompt_carries_persona_and_all_knowledge_but_no_truth(scenario_id: str) -> None:
    s = BUNDLE.scenario(scenario_id)
    text = prompt_for(scenario_id).text
    assert f"{s.persona.first_name} {s.persona.last_name}" in text
    assert BUNDLE.frame.agency_name in text
    for k in s.knowledge:
        assert k.lay_text in text
    for canary in s.leak_canaries:
        assert canary not in text
    for o in s.outcomes:
        assert o.rationale not in text
    assert s.title not in text


def test_prompt_is_deterministic_and_carries_the_rolled_mood() -> None:
    s = BUNDLE.scenario(CASE_A)
    first, second = (build_customer_prompt(s, BUNDLE.frame, "worried") for _ in range(2))
    assert first.text == second.text
    assert first.version == PROMPT_VERSION
    assert "feeling worried" in first.text
    assert "feeling chatty" in build_customer_prompt(s, BUNDLE.frame, "chatty").text


def test_prompt_marks_reveal_rules_and_misconceptions() -> None:
    text = prompt_for().text
    s = BUNDLE.scenario(CASE_A)
    specific = next(k for k in s.knowledge if k.reveal is Reveal.ONLY_IF_SPECIFIC)
    volunteer = next(k for k in s.knowledge if k.reveal is Reveal.VOLUNTEER)
    assert text.index("only if the agent asks about that specific") < text.index(specific.lay_text)
    assert text.index("on your own") < text.index(volunteer.lay_text)
    wrong = next(k for k in s.knowledge if k.misconception)
    line = next(ln for ln in text.splitlines() if wrong.lay_text in ln)
    assert "you may have it wrong" in line


@pytest.mark.parametrize(
    ("injected", "problem"),
    [
        ("Your plan has a nice formulary.", "agent jargon 'formulary'"),
        ("You are on TRN-3003-001.", "plan code"),
        ("You see raman-northside.", "internal id 'raman-northside'"),
    ],
)
def test_leak_guard_catches_what_the_customer_couldnt_know(injected: str, problem: str) -> None:
    prompt = prompt_for()
    bad = replace(prompt, text=prompt.text + "\n" + injected)
    assert any(problem in p for p in prompt_problems(bad, BUNDLE))


def test_leak_guard_catches_canaries_and_truth() -> None:
    prompt = prompt_for()
    s = prompt.scenario
    bad = replace(prompt, text=prompt.text + s.leak_canaries[0] + s.outcomes[0].rationale)
    problems = prompt_problems(bad, BUNDLE)
    assert any("leak canary" in p for p in problems)
    assert any("scenario truth" in p for p in problems)


def test_jargon_in_a_topic_fails_the_startup_check() -> None:
    s = BUNDLE.scenario(CASE_A)
    k = next(k for k in s.knowledge if k.reveal is Reveal.WHEN_ASKED)
    broken = k.model_copy(update={"topics": (*k.topics, "step therapy")})
    scenario = s.model_copy(
        update={"knowledge": tuple(broken if x is k else x for x in s.knowledge)}
    )
    bundle = replace(BUNDLE, scenarios={CASE_A: scenario})
    assert any("step therapy" in p for p in check_all_prompts(bundle))


# Scripted actor


def scripted(*lines: tuple[Speaker, str]) -> str:
    history = [Turn(speaker, text) for speaker, text in lines]
    return ScriptedActor().reply(prompt_for(), history, seed=1)


L, C = Speaker.LEARNER, Speaker.CUSTOMER


def test_scripted_customer_volunteers_then_answers_by_topic() -> None:
    s = BUNDLE.scenario(CASE_A)
    letter = next(k.lay_text for k in s.knowledge if k.reveal is Reveal.VOLUNTEER)
    assert scripted((L, "Thanks for calling, this call is recorded.")) == letter
    reply = scripted((L, "Hi."), (C, letter), (L, "What medications do you take?"))
    assert "apixaban" in reply and "Metoprolol" in reply
    later = scripted((L, "Hi."), (C, reply), (L, "Any other medications?"))
    assert "Atorvastatin" in later and "apixaban" not in later


def test_scripted_customer_does_not_answer_statements() -> None:
    disclaimer = (
        "We do not offer every plan available in your area. Currently we represent 3 "
        "organizations which offer 6 products in your area. Please contact Medicare.gov or "
        "1-800-MEDICARE to get information on all of your options."
    )
    s = BUNDLE.scenario(CASE_A)
    letter = next(k.lay_text for k in s.knowledge if k.reveal is Reveal.VOLUNTEER)
    assert scripted((L, "Hi."), (C, letter), (L, disclaimer)) == "Okay."
    assert "Part A" in scripted((L, "Tell me about your Medicare coverage."))


def test_scripted_customer_keeps_specific_facts_for_specific_questions() -> None:
    general = scripted((L, "What medications do you take?"))
    assert "40 milligrams" not in general
    specific = scripted((L, "Could you check the bottle for the strength?"))
    assert "the bottle says 40 milligrams" in specific


def test_scripted_customer_answers_consent_and_unknowns() -> None:
    assert scripted((L, "Would you like me to submit your enrollment now?")) == "Yes, go ahead."
    s = BUNDLE.scenario(CASE_A)
    letter = next(k.lay_text for k in s.knowledge if k.reveal is Reveal.VOLUNTEER)
    # With nothing left to volunteer, an unknown question gets an honest "not sure".
    unknown = scripted((L, "Hi."), (C, letter), (L, "What is your favorite color?"))
    assert unknown == "Hmm, I'm not sure about that."


# Gemini actor


class FakeModels:
    """Stands in for ``client.aio.models``. With ``hang`` the request never finishes unless
    cancelled, like a stuck provider call."""

    def __init__(self, reply: Any, hang: bool = False) -> None:
        self.reply = reply
        self.hang = hang
        self.calls: list[dict[str, Any]] = []
        self.cancelled = threading.Event()

    async def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.hang:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
        if isinstance(self.reply, Exception):
            raise self.reply
        return type("Response", (), {"text": self.reply})()


class FakeClient:
    def __init__(self, reply: Any, hang: bool = False) -> None:
        self.aio = type("Aio", (), {})()
        self.aio.models = FakeModels(reply, hang)


def gemini(reply: Any, hang: bool = False) -> tuple[GeminiActor, FakeModels]:
    client = FakeClient(reply, hang)
    actor = GeminiActor(model="test-model", api_key="unused", client=client)
    return actor, client.aio.models


def test_gemini_actor_sends_prompt_history_and_seed() -> None:
    actor, models = gemini("  Hello there.  ")
    history: Sequence[Turn] = [
        Turn(L, "Thanks for calling."),
        Turn(C, "Hi."),
        Turn(L, "First line."),
        Turn(L, "Second line."),
    ]
    prompt = prompt_for()
    assert actor.reply(prompt, history, seed=42) == "Hello there."
    assert actor.name == "gemini:test-model"
    (call,) = models.calls
    assert call["model"] == "test-model"
    assert call["config"].system_instruction == prompt.text
    assert call["config"].seed == 42
    roles = [(c.role, c.parts[0].text) for c in call["contents"]]
    assert roles == [
        ("user", "Thanks for calling."),
        ("model", "Hi."),
        ("user", "First line.\nSecond line."),
    ]


@pytest.mark.parametrize("reply", ["", "   ", RuntimeError("quota")])
def test_gemini_failures_become_actor_errors(reply: Any) -> None:
    actor, _ = gemini(reply)
    with pytest.raises(ActorError):
        actor.reply(prompt_for(), [Turn(L, "Hi.")], seed=1)


def test_a_timeout_raised_by_the_gemini_request_fails_fast() -> None:
    """TimeoutError from the request itself must not read as "still waiting": since Python
    3.11 it is the same class as the poll timeout, and mixing them up spun forever."""
    actor, _ = gemini(TimeoutError("timed out"))
    t, out = in_thread(lambda: actor.reply(prompt_for(), [Turn(L, "Hi.")], seed=1))
    t.join(5)
    assert not t.is_alive(), "reply returned instead of spinning"
    assert isinstance(out.get("error"), ActorError)
    assert "timed out" in str(out["error"])


def test_cancelling_stops_the_gemini_request_itself() -> None:
    """Giving up on the wait and stopping the provider call are separate; this is the second."""
    actor, models = gemini("never sent", hang=True)
    cancel = threading.Event()
    threading.Timer(0.1, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(ActorError, match="cancelled"):
        actor.reply(prompt_for(), [Turn(L, "Hi.")], seed=1, cancel=cancel)
    assert time.monotonic() - started < 2
    assert models.cancelled.wait(2), "the HTTP request task was cancelled"


def test_actor_selection_from_env() -> None:
    assert actor_from_env({}).name == "scripted"
    assert actor_from_env({"GEMINI_API_KEY": "k", "SIM_ACTOR": "scripted"}).name == "scripted"
    picked = actor_from_env({"GEMINI_API_KEY": "k", "SIM_ACTOR_MODEL": "m"})
    assert picked.name == "gemini:m"
    with pytest.raises(ActorConfigError, match="SIM_ACTOR_MODEL"):
        actor_from_env({"GEMINI_API_KEY": "k"})
    with pytest.raises(ActorConfigError, match="must be"):
        actor_from_env({"SIM_ACTOR": "other"})


# Turn endpoint


def test_a_turn_returns_the_learner_line_and_the_customer_reply(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    body = {"client_utt_id": "turn-fixed-0001", "text": "Hi, this call is recorded."}
    first = call.post("/turns", body)
    assert first["learner"]["speaker"] == "learner"
    assert first["customer"]["speaker"] == "customer"
    assert "letter" in first["customer"]["text"]
    again = call.post("/turns", body)
    assert again == first
    assert len(call.get("/transcript")) == 2
    utterance_events = [e for e in call.get("/events") if e["type"] == "utterance"]
    assert [e["payload"]["speaker"] for e in utterance_events] == ["learner", "customer"]


def test_turns_need_an_active_call_and_no_hold(client: TestClient) -> None:
    call = Call(client, CASE_A)
    call.turn("Hello?", expect=409)
    call.start()
    call.post("/hold", {"on": True})
    body = call.turn("Are you there?", expect=409)
    assert "on hold" in body["detail"]
    assert call.get("/transcript") == []
    call.post("/hold", {"on": False})
    assert call.turn("Thanks for holding.")["customer"] is not None


class FlakyActor(ScriptedActor):
    def __init__(self) -> None:
        self.fail = True

    def reply(
        self,
        prompt: CustomerPrompt,
        history: Sequence[Turn],
        seed: int,
        cancel: threading.Event | None = None,
    ) -> str:
        if self.fail:
            raise ActorError("model down")
        return super().reply(prompt, history, seed)


def test_a_failed_reply_keeps_the_learner_line_and_retries(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    flaky = FlakyActor()
    call.service.actor = flaky
    body = {"client_utt_id": "turn-retry-0001", "text": "What medications do you take?"}
    failed = call.post("/turns", body, expect=502)
    assert "model down" in failed["detail"]
    assert [u["speaker"] for u in call.get("/transcript")] == ["learner"]
    flaky.fail = False
    ok = call.post("/turns", body)
    assert "apixaban" in ok["customer"]["text"]
    assert [u["speaker"] for u in call.get("/transcript")] == ["learner", "customer"]


def test_an_old_turn_gets_no_stale_reply(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    flaky = FlakyActor()
    call.service.actor = flaky
    old = {"client_utt_id": "turn-old-00001", "text": "What pharmacy do you use?"}
    call.post("/turns", old, expect=502)
    flaky.fail = False
    call.turn("Sorry, let me ask again. What pharmacy do you use?")
    assert call.post("/turns", old)["customer"] is None


def test_sessions_pin_the_actor_and_prompt(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    row = call.service.get(call.sid)
    assert (row.actor, row.prompt_version) == ("scripted", PROMPT_VERSION)
    assert client.get("/api/health").json()["actor"] == "scripted"
    call.service.store.put_session(row.model_copy(update={"actor": "gemini:old-model"}))
    body = call.turn("Hello?", expect=409)
    assert "gemini:old-model" in body["detail"]


# Live (opt-in: GEMINI_API_KEY and SIM_ACTOR_MODEL set, run with pytest -m live)


@pytest.mark.live
def test_live_gemini_customer_stays_in_lay_words() -> None:
    actor = actor_from_env({**os.environ, "SIM_ACTOR": "gemini"})
    prompt = prompt_for()
    history = [
        Turn(L, "Thanks for calling Larkspur Benefits Group. What can I help you with today?")
    ]
    reply = actor.reply(prompt, history, seed=7)
    assert reply
    problems = prompt_problems(replace(prompt, text=reply), BUNDLE)
    assert problems == [], problems


# Turn coordination


class BlockingActor(ScriptedActor):
    """Blocks inside its first reply (every reply with ``block_all``) until released, to
    overlap requests deterministically. With ``honors_cancel`` it stops when told to, like the
    Gemini actor; without, it behaves like a provider that ignores cancellation."""

    def __init__(self, honors_cancel: bool = True, block_all: bool = False) -> None:
        self.honors_cancel = honors_cancel
        self.block_all = block_all
        self.entered = threading.Event()
        self.release = threading.Event()
        self.cancel_seen: threading.Event | None = None
        self.calls = 0

    def reply(
        self,
        prompt: CustomerPrompt,
        history: Sequence[Turn],
        seed: int,
        cancel: threading.Event | None = None,
    ) -> str:
        self.calls += 1
        if self.calls == 1 or self.block_all:
            self.cancel_seen = cancel
            self.entered.set()
            give_up = time.monotonic() + 10
            while not self.release.wait(0.01):
                if self.honors_cancel and cancel is not None and cancel.is_set():
                    raise ActorError("cancelled")
                assert time.monotonic() < give_up, "never released"
        return super().reply(prompt, history, seed)


def customer_reply_threads() -> int:
    return sum(1 for t in threading.enumerate() if t.name == "customer-reply")


def in_thread(fn: Any) -> tuple[threading.Thread, dict[str, Any]]:
    out: dict[str, Any] = {}

    def run() -> None:
        try:
            out["value"] = fn()
        except Exception as exc:  # surfaced by the asserting test
            out["error"] = exc

    t = threading.Thread(target=run, daemon=True)  # a stuck thread fails a test, not the run
    t.start()
    return t, out


def blocked_turn(call: Call, text: str = "What medications do you take?") -> Any:
    actor = BlockingActor()
    call.service.actor = actor
    first = in_thread(
        lambda: call.service.take_turn(call.sid, TurnIn(client_utt_id="turn-first-0001", text=text))
    )
    assert actor.entered.wait(5)
    return actor, first


def speakers(call: Call) -> list[str]:
    return [u.speaker.value for u in call.service.transcript(call.sid)]


def test_overlapping_turns_are_answered_in_order(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    actor, (t1, first) = blocked_turn(call)
    t2, second = in_thread(
        lambda: call.service.take_turn(
            call.sid, TurnIn(client_utt_id="turn-second-001", text="What pharmacy do you use?")
        )
    )
    t2.join(0.3)
    assert t2.is_alive(), "the second turn waits for the first reply"
    assert speakers(call) == ["learner"]
    actor.release.set()
    t1.join(5)
    t2.join(5)
    assert speakers(call) == ["learner", "customer", "learner", "customer"]
    assert "apixaban" in first["value"].customer.text
    assert "Alder Family Pharmacy" in second["value"].customer.text


def test_a_reply_overtaken_by_a_newer_line_is_dropped(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    actor, (t1, first) = blocked_turn(call)
    call.say("Actually, never mind that. Let me start over.")  # bypasses the turn lock
    actor.release.set()
    t1.join(5)
    assert first["value"].customer is None
    assert speakers(call) == ["learner", "learner"]


@pytest.mark.parametrize("action", ["hold", "end"])
def test_hold_and_end_cut_off_a_reply_in_flight(client: TestClient, action: str) -> None:
    """Both return at once, and so does the turn waiting on the model: it doesn't sit out
    the deadline. The actor is told to stop its request."""
    call = Call(client, CASE_A).start()
    call.service.actor_deadline_s = 30
    actor, (t1, first) = blocked_turn(call)
    act = (
        (lambda: call.service.hold(call.sid, True))
        if action == "hold"
        else (lambda: call.service.end(call.sid))
    )
    t2, done = in_thread(act)
    t2.join(2)
    assert not t2.is_alive(), f"{action} doesn't wait for the model"
    assert "error" not in done
    t1.join(2)
    assert not t1.is_alive(), "the waiting turn is cut off, not left until the deadline"
    assert first["value"].customer is None
    assert actor.cancel_seen is not None and actor.cancel_seen.is_set()
    assert speakers(call) == ["learner"]
    if action == "end":
        assert call.service.get(call.sid).status.value == "ended"
    actor.release.set()


def test_after_hold_and_resume_the_next_turn_does_not_wait_for_the_old_one(
    client: TestClient,
) -> None:
    call = Call(client, CASE_A).start()
    call.service.actor_deadline_s = 30
    actor = BlockingActor(honors_cancel=False)  # a provider that keeps going regardless
    call.service.actor = actor
    t1, first = in_thread(
        lambda: call.service.take_turn(
            call.sid, TurnIn(client_utt_id="turn-first-0001", text="What medications?")
        )
    )
    assert actor.entered.wait(5)
    call.service.hold(call.sid, True)
    t1.join(2)
    assert not t1.is_alive() and first["value"].customer is None
    call.service.hold(call.sid, False)
    call.service.actor = ScriptedActor()
    second = call.service.take_turn(
        call.sid, TurnIn(client_utt_id="turn-second-001", text="What pharmacy do you use?")
    )
    assert second.customer is not None and "Alder Family Pharmacy" in second.customer.text
    actor.release.set()  # the old call finishes late; its reply is never saved
    assert speakers(call) == ["learner", "learner", "customer"]


def test_a_reply_whose_generation_overlapped_a_hold_is_dropped_even_after_resume(
    client: TestClient,
) -> None:
    call = Call(client, CASE_A).start()
    actor, (t1, first) = blocked_turn(call)
    # Hold and resume written straight to the log, so only the overlap check can catch it.
    call.service.log(call.sid, EventType.HOLD_STARTED)
    call.service.log(call.sid, EventType.HOLD_ENDED)
    actor.release.set()
    t1.join(5)
    assert first["value"].customer is None
    assert speakers(call) == ["learner"]
    # Retrying the same line after the resume starts a fresh generation, which may answer.
    retry = call.service.take_turn(
        call.sid, TurnIn(client_utt_id="turn-first-0001", text="What medications do you take?")
    )
    assert retry.customer is not None and "apixaban" in retry.customer.text


def test_end_cannot_slip_between_the_turn_checks_and_the_learner_line(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The live-call checks and the learner line commit together; an end that arrives in
    between waits for them, so the line never lands after the call ended."""
    call = Call(client, CASE_A).start()
    service = call.service
    write = service.add_learner_text
    ender: dict[str, threading.Thread] = {}

    def racing_write(session_id: str, body: Any) -> Any:
        t, _ = in_thread(lambda: service.end(session_id))
        t.join(0.3)
        assert t.is_alive(), "end waits for the turn's transaction"
        ender["t"] = t
        return write(session_id, body)

    monkeypatch.setattr(service, "add_learner_text", racing_write)
    out = service.take_turn(call.sid, TurnIn(client_utt_id="turn-race-00001", text="Hello?"))
    ender["t"].join(5)
    events = service.events(call.sid)
    said = next(e.seq for e in events if e.utt_id == out.learner.utt_id)
    ended = next(e.seq for e in events if e.type is EventType.CALL_ENDED)
    assert said < ended
    assert service.get(call.sid).status.value == "ended"


def test_retries_against_a_hung_model_are_bounded(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    service = call.service
    service._actor_slots = threading.BoundedSemaphore(2)
    service.actor_deadline_s = 0.1
    actor = BlockingActor(honors_cancel=False, block_all=True)
    service.actor = actor
    before = customer_reply_threads()
    body = {"client_utt_id": "turn-hung-00001", "text": "What medications do you take?"}
    for _ in range(2):
        assert "didn't answer within" in call.post("/turns", body, expect=502)["detail"]
    busy = call.post("/turns", body, expect=502)
    assert "still busy" in busy["detail"]
    assert customer_reply_threads() - before <= 2
    actor.release.set()
    for _ in range(200):
        if customer_reply_threads() == before:
            break
        time.sleep(0.01)
    assert customer_reply_threads() == before, "slots come back when the calls finish"
    service.actor = ScriptedActor()
    assert "apixaban" in call.post("/turns", body)["customer"]["text"]


def test_a_hung_model_times_out_and_the_turn_can_be_retried(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    actor = BlockingActor()
    call.service.actor = actor
    call.service.actor_deadline_s = 0.2
    body = {"client_utt_id": "turn-hung-00001", "text": "What medications do you take?"}
    failed = call.post("/turns", body, expect=502)
    assert "didn't answer within 0.2s" in failed["detail"]
    assert actor.cancel_seen is not None and actor.cancel_seen.is_set()
    actor.release.set()
    assert speakers(call) == ["learner"]
    call.service.actor = ScriptedActor()
    assert "apixaban" in call.post("/turns", body)["customer"]["text"]
    assert speakers(call) == ["learner", "customer"]


def test_an_actor_bug_ends_the_turn_cleanly(client: TestClient) -> None:
    class Broken(ScriptedActor):
        def reply(
            self,
            prompt: CustomerPrompt,
            history: Sequence[Turn],
            seed: int,
            cancel: threading.Event | None = None,
        ) -> str:
            raise KeyError("oops")

    call = Call(client, CASE_A).start()
    call.service.actor = Broken()
    assert "failed" in call.turn("Hello?", expect=502)["detail"]
    assert call.service._turn_locks == {}
    assert call.service._inflight == {}


def test_turn_locks_live_only_while_a_turn_is_in_flight(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    call.turn("Hello?")
    assert call.service._turn_locks == {} and call.service._inflight == {}
    actor, (t1, _) = blocked_turn(call)
    assert set(call.service._turn_locks) == {call.sid}
    assert set(call.service._inflight) == {call.sid}
    actor.release.set()
    t1.join(5)
    assert call.service._turn_locks == {} and call.service._inflight == {}


def test_actor_timeout_from_env() -> None:
    assert actor_timeout_from_env({}) == 30.0
    assert actor_timeout_from_env({"SIM_ACTOR_TIMEOUT_S": "12.5"}) == 12.5
    assert actor_timeout_from_env({"SIM_ACTOR_TIMEOUT_S": "0.1"}) == 0.1
    for bad in ("0", "0.05", "-1", "abc", "601", "inf", "nan"):
        with pytest.raises(ActorConfigError, match="SIM_ACTOR_TIMEOUT_S"):
            actor_timeout_from_env({"SIM_ACTOR_TIMEOUT_S": bad})


def test_a_reply_after_a_hold_that_slipped_in_is_dropped(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    actor, (t1, first) = blocked_turn(call)
    call.service.log(call.sid, EventType.HOLD_STARTED)  # bypasses the turn lock
    actor.release.set()
    t1.join(5)
    assert first["value"].customer is None
    assert speakers(call) == ["learner"]


def test_reusing_a_turn_id_for_a_different_line_is_refused(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    call.post("/turns", {"client_utt_id": "turn-reused-001", "text": "Hello?"})
    body = call.post("/turns", {"client_utt_id": "turn-reused-001", "text": "Bye."}, expect=409)
    assert "different line" in body["detail"]
    utt = {"client_utt_id": "turn-reused-001", "speaker": "learner", "channel": "text"}
    call.post("/utterances", utt | {"text": "Something else"}, expect=409)
    assert len(call.get("/transcript")) == 2


def test_consent_must_cite_the_customers_heard_words(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    app_id = call.apply("TRN-1001-002", "Loretta Banks", "00041")
    mine = call.say("Yes, I'll enroll you.")
    cut = call.hear("Yes.", heard="", interrupted=True)
    for utt, reason in [(mine, "not the customer's"), (cut, "cut off")]:
        body = call.post(f"/applications/{app_id}/consent", {"utt_id": utt}, expect=422)
        assert reason in body["detail"]
    call.consent(app_id, call.hear("Yes, go ahead."))
