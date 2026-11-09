"""Objections: the customer reacts to what the agent does, the same way in text and voice,
because the reactions live in the prompt. The scripted stand-in spots the simpler triggers so
the behavior is testable offline."""

from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.actor.actors import ScriptedActor, Turn
from app.actor.prompt import TRIGGER_TEXT, build_customer_prompt
from app.content.loader import load_bundle, validate_bundle
from app.content.scenario import Objection, ObjectionTrigger, Scenario
from app.engine.events import Speaker

BUNDLE = load_bundle()
CASE_A, CASE_B, CASE_C = "scenario-101", "scenario-102", "scenario-103"
L, C = Speaker.LEARNER, Speaker.CUSTOMER


def objection(scenario_id: str, trigger: ObjectionTrigger) -> Objection:
    return next(o for o in BUNDLE.scenario(scenario_id).objections if o.trigger is trigger)


def reply(scenario_id: str, *lines: tuple[Speaker, str]) -> str:
    s = BUNDLE.scenario(scenario_id)
    prompt = build_customer_prompt(s, BUNDLE.frame, s.persona.moods[0])
    return ScriptedActor().reply(prompt, [Turn(sp, t) for sp, t in lines], seed=1)


# Content and prompt


def test_every_trigger_has_lay_wording() -> None:
    assert set(TRIGGER_TEXT) == set(ObjectionTrigger)


def test_prompt_renders_reactions_with_how_they_settle() -> None:
    s = BUNDLE.scenario(CASE_A)
    text = build_customer_prompt(s, BUNDLE.frame, s.persona.moods[0]).text
    assert "How you react" in text
    for o in s.objections:
        assert o.lay_text in text
        assert TRIGGER_TEXT[o.trigger] in text
        if o.settles_when:
            assert f"You let it go once {o.settles_when}." in text
    held = objection(CASE_A, ObjectionTrigger.PRESSURE)
    assert held.settles_when is None, "case A keeps one refusal that holds"
    assert "You hold to this for the rest of the call" in text


def test_every_case_has_objections() -> None:
    assert all(s.objections for s in BUNDLE.scenarios.values())


def test_objection_text_is_held_to_lay_words() -> None:
    s = BUNDLE.scenario(CASE_B)
    bad = Objection(
        objection_id="jargony",
        trigger=ObjectionTrigger.RUSHED,
        lay_text="Is that on the formulary?",
    )
    broken = s.model_copy(update={"objections": (*s.objections[:1], bad)})
    problems = validate_bundle(replace(BUNDLE, scenarios={CASE_B: broken}))
    assert f"{CASE_B}: objection jargony uses agent jargon 'formulary'" in problems


def test_one_objection_per_trigger() -> None:
    s = BUNDLE.scenario(CASE_A)
    raw = s.model_dump(mode="json")
    raw["objections"].append({**raw["objections"][0], "objection_id": "again"})
    with pytest.raises(ValidationError, match="duplicate objection triggers"):
        Scenario.model_validate(raw)


# Scripted customer


def test_unexplained_jargon_draws_the_objection_once() -> None:
    plain = objection(CASE_B, ObjectionTrigger.JARGON).lay_text
    ask = "Does your plan's formulary cover rosuvastatin?"
    assert reply(CASE_B, (L, ask)) == plain
    assert reply(CASE_B, (L, ask), (C, plain), (L, ask)) != plain


def test_explained_jargon_draws_no_objection() -> None:
    plain = objection(CASE_B, ObjectionTrigger.JARGON).lay_text
    line = "The formulary, which is the plan's list of covered drugs, has rosuvastatin. Okay?"
    assert reply(CASE_B, (L, line)) != plain


def test_pitching_before_asking_about_needs_draws_the_objection() -> None:
    early = objection(CASE_B, ObjectionTrigger.EARLY_PITCH).lay_text
    pitch = "I'd recommend Summit Rewards. Want to hear more?"
    assert reply(CASE_B, (L, pitch)) == early
    asked_first = ((L, "Who is your doctor?"), (C, "Dr. Okafor."), (L, pitch))
    assert reply(CASE_B, *asked_first) != early


def test_a_refusal_under_pressure_holds() -> None:
    held = objection(CASE_A, ObjectionTrigger.PRESSURE).lay_text
    assert reply(CASE_A, (L, "You really need to decide today, it's a great deal.")) == held


def test_being_told_to_wait_draws_pushback() -> None:
    push = objection(CASE_C, ObjectionTrigger.ASKED_TO_WAIT).lay_text
    line = "I'd hold off and check with your retiree benefits center first. Is that okay?"
    assert reply(CASE_C, (L, line)) == push


def test_asking_for_numbers_draws_a_refusal() -> None:
    no = objection(CASE_B, ObjectionTrigger.PERSONAL_INFO).lay_text
    assert reply(CASE_B, (L, "Can I get your Medicare number?")) == no


def test_piling_on_draws_the_rushed_objection() -> None:
    slow = objection(CASE_A, ObjectionTrigger.RUSHED).lay_text
    wall = " ".join(["The plan covers a lot of things and here is another detail."] * 10)
    assert reply(CASE_A, (L, wall)) == slow


def test_a_case_without_the_trigger_answers_normally() -> None:
    """Case C has no rushed objection, so a long line gets a normal answer."""
    wall = " ".join(["The plan covers a lot of things and here is another detail."] * 10)
    out = reply(CASE_C, (L, wall))
    assert out not in {o.lay_text for o in BUNDLE.scenario(CASE_C).objections}
