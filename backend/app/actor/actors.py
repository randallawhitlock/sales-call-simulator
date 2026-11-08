"""Models that play the customer.

``GeminiActor`` is the real text actor. ``ScriptedActor`` answers from the knowledge items by
topic, honoring reveal rules, so the app and its tests run with no API key. It is a stand-in,
not a realistic customer.

Configuration:

- ``SIM_ACTOR``: ``gemini`` or ``scripted``. Defaults to ``gemini`` when ``GEMINI_API_KEY`` is
  set, else ``scripted``.
- ``SIM_ACTOR_MODEL``: the Gemini model id. Required for ``gemini`` and never defaulted, so a
  session always records the exact model it ran.
"""

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.actor.prompt import CustomerPrompt
from app.content.scenario import KnowledgeItem, Reveal
from app.engine.events import Speaker

ACTOR_ENV = "SIM_ACTOR"
MODEL_ENV = "SIM_ACTOR_MODEL"
KEY_ENV = "GEMINI_API_KEY"


@dataclass(frozen=True)
class Turn:
    speaker: Speaker
    text: str


class ActorError(Exception):
    """The model failed or returned nothing usable. The learner's turn is already saved; a
    retry of the same turn asks again."""


class Actor(Protocol):
    @property
    def name(self) -> str:
        """Pinned on each session, e.g. ``scripted`` or ``gemini:<model>``."""
        ...

    def reply(self, prompt: CustomerPrompt, history: Sequence[Turn], seed: int) -> str:
        """The customer's next line. ``history`` ends with the learner's latest turn."""
        ...


# Gemini


class GeminiActor:
    def __init__(self, model: str, api_key: str, client: Any = None) -> None:
        if client is None:
            from google import genai

            client = genai.Client(api_key=api_key)
        self.model = model
        self._client = client

    @property
    def name(self) -> str:
        return f"gemini:{self.model}"

    def reply(self, prompt: CustomerPrompt, history: Sequence[Turn], seed: int) -> str:
        from google.genai import types

        contents = [
            types.Content(role=role, parts=[types.Part.from_text(text=text)])
            for role, text in _merged_roles(history)
        ]
        config = types.GenerateContentConfig(
            system_instruction=prompt.text, temperature=0.9, seed=seed
        )
        try:
            response = self._client.models.generate_content(
                model=self.model, contents=contents, config=config
            )
        except Exception as exc:  # network, quota, safety: all mean "no reply this time"
            raise ActorError(f"{self.name} failed: {exc}") from exc
        text = (getattr(response, "text", None) or "").strip()
        if not text:
            raise ActorError(f"{self.name} returned an empty reply")
        return text


def _merged_roles(history: Sequence[Turn]) -> list[tuple[str, str]]:
    """Gemini roles, with back-to-back turns by one speaker joined into one message."""
    out: list[tuple[str, str]] = []
    for t in history:
        role = "user" if t.speaker is Speaker.LEARNER else "model"
        if out and out[-1][0] == role:
            out[-1] = (role, out[-1][1] + "\n" + t.text)
        else:
            out.append((role, t.text))
    return out


# Scripted


_ASKING_START = re.compile(
    r"^(tell me|can|could|would|will|do|does|did|are|is|have|has|may|what|which|who|whose|"
    r"where|when|how|let me know)\b"
)
_CONSENT_ASK = re.compile(
    r"\b(enroll you|sign you up|submit (?:your|the) (?:enrollment|application)|"
    r"go ahead and enroll|want to enroll|like to enroll)\b"
)
MAX_ITEMS_PER_REPLY = 2


class ScriptedActor:
    """Offline stand-in. Volunteers ``volunteer`` items one per turn, answers questions with
    the items whose topics the question mentions, and says yes when asked to enroll."""

    name = "scripted"

    def reply(self, prompt: CustomerPrompt, history: Sequence[Turn], seed: int) -> str:
        knowledge = prompt.scenario.knowledge
        said = " ".join(t.text for t in history if t.speaker is Speaker.CUSTOMER)
        last = _fold(history[-1].text) if history else ""
        asked = _is_question(last)
        if asked and _CONSENT_ASK.search(last):
            return "Yes, go ahead."

        def unsaid(k: KnowledgeItem) -> bool:
            return k.lay_text not in said

        lines: list[str] = []
        if asked:
            hits = [k for k in knowledge if k.reveal is not Reveal.VOLUNTEER and _asks(last, k)]
            fresh = [k for k in hits if unsaid(k)]
            lines = [k.lay_text for k in (fresh or hits)[:MAX_ITEMS_PER_REPLY]]
        if not lines:
            volunteer = [k for k in knowledge if k.reveal is Reveal.VOLUNTEER and unsaid(k)]
            lines = [k.lay_text for k in volunteer[:1]]
        if not lines:
            return "Hmm, I'm not sure about that." if asked else "Okay."
        return " ".join(lines)


def _is_question(text: str) -> bool:
    """A question mark, or a sentence that opens like a question or request. Statements such
    as the disclaimer ("organizations which offer...") don't count."""
    if "?" in text:
        return True
    sentences = (s.strip() for s in re.split(r"[.!]", text))
    return any(_ASKING_START.match(s) for s in sentences if s)


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _asks(text: str, item: KnowledgeItem) -> bool:
    for topic in item.topics:
        t = _fold(topic)
        forms = {t, t[:-1]} if t.endswith("s") and len(t) > 3 else {t}
        if any(re.search(rf"\b{re.escape(f)}", text) for f in forms):
            return True
    return False


# Selection


class ActorConfigError(ValueError):
    pass


def actor_from_env(env: dict[str, str] | None = None) -> Actor:
    env = dict(os.environ) if env is None else env
    key = env.get(KEY_ENV, "")
    kind = env.get(ACTOR_ENV) or ("gemini" if key else "scripted")
    if kind == "scripted":
        return ScriptedActor()
    if kind != "gemini":
        raise ActorConfigError(f"{ACTOR_ENV} must be 'gemini' or 'scripted', not {kind!r}")
    model = env.get(MODEL_ENV, "")
    if not key or not model:
        raise ActorConfigError(f"the gemini actor needs {KEY_ENV} and {MODEL_ENV}")
    return GeminiActor(model=model, api_key=key)
