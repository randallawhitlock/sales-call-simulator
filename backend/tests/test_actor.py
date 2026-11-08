"""The AI customer: prompt, leak guard, actors and the turn endpoint."""

import os
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
from app.engine.events import Speaker
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
    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        return type("Response", (), {"text": self.reply})()


class FakeClient:
    def __init__(self, reply: Any) -> None:
        self.models = FakeModels(reply)


def gemini(reply: Any) -> tuple[GeminiActor, FakeModels]:
    client = FakeClient(reply)
    return GeminiActor(model="test-model", api_key="unused", client=client), client.models


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

    def reply(self, prompt: CustomerPrompt, history: Sequence[Turn], seed: int) -> str:
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
