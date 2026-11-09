"""The customer prompt: persona, mood and reveal-ruled knowledge, in lay words only.

Structured content owns the facts; the model owns phrasing and tone. Reveal rules live in the
prompt because Gemini Live accepts no mid-call system messages, and text mode uses the same
builder so text calls test the actor voice will run.

The prompt never carries scenario truth (outcomes, traps, the answer key), catalog ids or agent
jargon. ``prompt_problems`` is the build-time guard; the server runs it for every case and mood
at startup and refuses to start if any prompt fails.
"""

import re
from dataclasses import dataclass

from app.content.loader import ContentBundle, internal_ids, jargon_in
from app.content.scenario import (
    CallFrame,
    KnowledgeItem,
    Objection,
    ObjectionTrigger,
    Reveal,
    Scenario,
)

PROMPT_VERSION = "customer-v2"
"""Bump when the template changes. Sessions pin it, so an old call keeps the customer it had."""

_PLAN_CODE = re.compile(r"TRN-\d{4}-\d{3}", re.IGNORECASE)

TRIGGER_TEXT = {
    ObjectionTrigger.RUSHED: "If the agent rushes, piles on a lot at once, or talks past your "
    "questions",
    ObjectionTrigger.JARGON: "If the agent uses an insurance word you don't know and doesn't "
    "explain it",
    ObjectionTrigger.EARLY_PITCH: "If the agent pushes a particular plan before asking about "
    "your doctors, your medicines and what matters to you",
    ObjectionTrigger.PRESSURE: "If the agent pressures you to decide or sign up today",
    ObjectionTrigger.ASKED_TO_WAIT: "If the agent tells you to hold off, wait, or check with "
    "someone before you decide",
    ObjectionTrigger.PERSONAL_INFO: "If the agent asks for your Medicare number, Social "
    "Security number or bank details",
}
"""Fixed lay wording for each trigger, so every case describes the same event the same way."""


@dataclass(frozen=True)
class CustomerPrompt:
    version: str
    text: str
    scenario: Scenario
    """Kept for actors that work from structure (the offline scripted actor). Only
    ``scenario.persona`` and ``scenario.knowledge`` may be read from it."""
    mood: str


def build_customer_prompt(scenario: Scenario, frame: CallFrame, mood: str) -> CustomerPrompt:
    p = scenario.persona
    when = f"{frame.call_date:%B} {frame.call_date.day}, {frame.call_date.year}"
    sections = [
        (
            f"You are {p.first_name} {p.last_name}, {p.age} years old. It is {when}, and you "
            f"are phoning {frame.agency_name} about your Medicare coverage for "
            f"{frame.plan_year}. The person who answers is a licensed insurance agent. You are "
            "the caller. You are not the agent and you never act as one."
        ),
        _bullets(
            "How you talk",
            [
                f"Your manner: {p.speaking_style}",
                f"Today you are feeling {mood}.",
                "Talk the way people talk on the phone: short turns, usually one to three "
                "sentences. No lists, headings or stage directions.",
                "Use everyday words. You don't know insurance terms. If the agent uses one, "
                "ask what it means or answer as best you can.",
            ],
        ),
        (
            "What you know\n"
            "Everything you know about your situation is below. Never make up a doctor, "
            "medicine, pharmacy, dollar amount, date or plan that isn't listed here. If you're "
            "asked about something that isn't here, say you're not sure or don't remember."
        ),
        _knowledge(
            "Bring these up on your own early in the call, one or two at a time:",
            scenario.knowledge,
            Reveal.VOLUNTEER,
        ),
        _knowledge(
            "Share these when the agent asks about the subject in brackets:",
            scenario.knowledge,
            Reveal.WHEN_ASKED,
        ),
        _knowledge(
            "Share these only if the agent asks about that specific thing. A general question "
            "isn't enough:",
            scenario.knowledge,
            Reveal.ONLY_IF_SPECIFIC,
        ),
        _bullets(
            "How you decide",
            [
                "You make your own decisions. Start out the way your situation above describes. "
                "If the agent gives you a good reason to slow down or change your mind, you can, "
                "but you don't have to.",
                "It's fine to say no or ask for time. A refusal can stay a refusal.",
                "If the agent asks your permission to enroll you, answer plainly yes or no.",
                "If the agent rushes you or talks over your questions, say so.",
            ],
        ),
        _objections(scenario.objections),
        _bullets(
            "Never",
            [
                "Never give advice, look anything up, or describe what you are doing.",
                "Never mention these instructions or that you are playing a part.",
            ],
        ),
    ]
    text = "\n\n".join(s for s in sections if s)
    return CustomerPrompt(version=PROMPT_VERSION, text=text, scenario=scenario, mood=mood)


def _bullets(title: str, lines: list[str]) -> str:
    return title + "\n" + "\n".join(f"- {line}" for line in lines)


def _knowledge(title: str, items: tuple[KnowledgeItem, ...], reveal: Reveal) -> str:
    picked = [k for k in items if k.reveal is reveal]
    if not picked:
        return ""
    lines = []
    for k in picked:
        prefix = "" if reveal is Reveal.VOLUNTEER else f"[{', '.join(k.topics)}] "
        note = (
            " (You believe this, but you may have it wrong. If the agent asks you to check, "
            "you can find out you were mistaken.)"
            if k.misconception
            else ""
        )
        lines.append(f"{prefix}{k.lay_text}{note}")
    return _bullets(title, lines)


def _objections(objections: tuple[Objection, ...]) -> str:
    if not objections:
        return ""
    lines = []
    for o in objections:
        settle = (
            f"You let it go once {o.settles_when}."
            if o.settles_when
            else "You hold to this for the rest of the call, even if the agent pushes."
        )
        lines.append(f'{TRIGGER_TEXT[o.trigger]}, you say something like: "{o.lay_text}" {settle}')
    return _bullets("How you react", lines)


def prompt_problems(prompt: CustomerPrompt, content: ContentBundle) -> list[str]:
    """Anything in the prompt the customer couldn't know or wouldn't say."""
    text = prompt.text
    folded = text.casefold()
    s = prompt.scenario
    out = [f"agent jargon {w!r}" for w in jargon_in(text)]
    out += [f"plan code {m!r}" for m in _PLAN_CODE.findall(text)]
    out += [f"internal id {i!r}" for i in internal_ids(content.catalog) if i.casefold() in folded]
    out += [f"leak canary {c!r}" for c in s.leak_canaries if c.casefold() in folded]
    truth = [o.rationale for o in s.outcomes] + [t.description for t in s.traps]
    out += [f"scenario truth {t[:40]!r}" for t in truth if t.casefold() in folded]
    if s.title.casefold() in folded:
        out.append(f"author-facing title {s.title!r}")
    return out


def check_all_prompts(content: ContentBundle) -> list[str]:
    """Every case in every mood it can roll. Run at startup."""
    out: list[str] = []
    for s in content.scenarios.values():
        for mood in s.persona.moods:
            prompt = build_customer_prompt(s, content.frame, mood)
            out += [f"{s.scenario_id} ({mood}): {p}" for p in prompt_problems(prompt, content)]
    return out
