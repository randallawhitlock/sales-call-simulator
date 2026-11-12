"""Models that play the customer.

``GeminiActor`` is the real text actor. ``ScriptedActor`` answers from the knowledge items by
topic, honoring reveal rules, so the app and its tests run with no API key. It is a stand-in,
not a realistic customer.

Configuration:

- ``SIM_ACTOR``: ``gemini`` or ``scripted``. Defaults to ``gemini`` when ``GEMINI_API_KEY`` is
  set, else ``scripted``.
- ``SIM_ACTOR_MODEL``: the Gemini model id. Required for ``gemini`` and never defaulted, so a
  session always records the exact model it ran.
- ``SIM_ACTOR_TIMEOUT_S``: how long one reply may take (default 30). The session service stops
  waiting at this deadline, and the Gemini client's own HTTP timeout matches it.
"""

import asyncio
import concurrent.futures
import math
import os
import re
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.actor.prompt import CustomerPrompt
from app.content.loader import jargon_in
from app.content.scenario import KnowledgeItem, Objection, ObjectionTrigger, Reveal
from app.engine.events import Speaker

ACTOR_ENV = "SIM_ACTOR"
MODEL_ENV = "SIM_ACTOR_MODEL"
KEY_ENV = "GEMINI_API_KEY"
TIMEOUT_ENV = "SIM_ACTOR_TIMEOUT_S"
DEFAULT_TIMEOUT_S = 30.0
MIN_TIMEOUT_S = 0.1
MAX_TIMEOUT_S = 600.0
CANCEL_POLL_S = 0.05


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

    def reply(
        self,
        prompt: CustomerPrompt,
        history: Sequence[Turn],
        seed: int,
        cancel: threading.Event | None = None,
    ) -> str:
        """The customer's next line. ``history`` ends with the learner's latest turn.

        ``cancel`` is set when nobody is waiting for the reply any more (a hold, the end of the
        call, or the deadline). An actor that calls a provider should abort that request and
        raise ActorError, so the call stops costing time and money."""
        ...


# Gemini


class GeminiActor:
    def __init__(
        self, model: str, api_key: str, client: Any = None, timeout_s: float = DEFAULT_TIMEOUT_S
    ) -> None:
        if client is None:
            from google import genai
            from google.genai import types

            client = genai.Client(
                api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_s * 1000))
            )
        self.model = model
        self._client = client
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_guard = threading.Lock()

    @property
    def name(self) -> str:
        return f"gemini:{self.model}"

    def _event_loop(self) -> asyncio.AbstractEventLoop:
        """One long-lived loop for the async client, so its HTTP connections stay on one loop
        and a request can be cancelled from the waiting thread."""
        with self._loop_guard:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(target=loop.run_forever, name="gemini-loop", daemon=True).start()
                self._loop = loop
            return self._loop

    def reply(
        self,
        prompt: CustomerPrompt,
        history: Sequence[Turn],
        seed: int,
        cancel: threading.Event | None = None,
    ) -> str:
        from google.genai import types

        contents = [
            types.Content(role=role, parts=[types.Part.from_text(text=text)])
            for role, text in _merged_roles(history)
        ]
        config = types.GenerateContentConfig(
            system_instruction=prompt.text, temperature=0.9, seed=seed
        )
        request = asyncio.run_coroutine_threadsafe(
            self._client.aio.models.generate_content(
                model=self.model, contents=contents, config=config
            ),
            self._event_loop(),
        )
        # Wait on the future rather than catching result()'s timeout: since Python 3.11 that
        # is the same class as a TimeoutError the request itself raises, and confusing the
        # two would poll a finished request forever.
        while not request.done():
            concurrent.futures.wait([request], timeout=CANCEL_POLL_S)
            if not request.done() and cancel is not None and cancel.is_set():
                request.cancel()  # cancels the task, which closes the HTTP request
                raise ActorError(f"{self.name} request cancelled")
        try:
            response = request.result()
        except Exception as exc:  # network, quota, safety, timeouts: "no reply this time"
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
RUSHED_WORDS = 90
"""A learner line longer than this counts as piling on, for the scripted customer."""


class ScriptedActor:
    """Offline stand-in. Raises each of the case's objections once when it spots the trigger,
    volunteers ``volunteer`` items one per turn, answers questions with the items whose topics
    the question mentions, and says yes when asked to enroll, unless it already refused with
    an objection that holds for the call."""

    name = "scripted"

    def reply(
        self,
        prompt: CustomerPrompt,
        history: Sequence[Turn],
        seed: int,
        cancel: threading.Event | None = None,
    ) -> str:
        knowledge = prompt.scenario.knowledge
        said = " ".join(t.text for t in history if t.speaker is Speaker.CUSTOMER)
        last = _fold(history[-1].text) if history else ""
        asked = _is_question(last)
        objection = _objection_for(prompt.scenario.objections, last, history, said)
        if objection is not None:
            return objection.lay_text
        if asked and _CONSENT_ASK.search(last):
            # A refusal with nothing to settle it holds for the rest of the call.
            held = [o for o in prompt.scenario.objections if o.settles_when is None]
            refused = next((o for o in held if o.lay_text in said), None)
            return refused.lay_text if refused is not None else "Yes, go ahead."

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


_PERSONAL_INFO = re.compile(
    r"\b(medicare (number|card)|social security number|ssn|bank (account|details|information)|"
    r"routing number|account number|card number)\b"
)
_DECIDE_NOW = re.compile(
    r"\b(decide|sign (you )?up|enroll|lock (it|this|you) in|commit)\b[^.?!]{0,40}"
    r"\b(today|right now|tonight)\b"
)
_URGENCY = re.compile(r"\bact (fast|now)\b|\blimited time\b|\bwon't last\b")
_PERMISSION_ASK = re.compile(
    r"(?:^|[,;:]\s*)(?:(?:okay|ok|so|well|alright|all right|great|good|now|then|and|sure|right)"
    r"\b[\s,]*)*"
    r"(would you like|do you want|are you ready|is it (ok|okay|all right|alright)|"
    r"(may|can|could|shall|should) i|do i have your (permission|okay|ok))\b"
)
"""How a sentence that asks leave to enroll opens, after any filler ("Okay, would you like...").
"Would you like me to enroll you today?" is the permission question, not pressure."""
_NO_RUSH = re.compile(
    r"\b(?:(?:don['’]?t|do not|won['’]?t|will not) (?:have|need) to|needn['’]?t|"
    r"no (?:need|rush|pressure|hurry|obligation) to|"
    r"not (?:asking|going|trying|here) (?:you )?to(?: \w+ you to)?)\s+(?:\w+\s+){0,3}$"
)
"""What comes right before "decide today" when the agent is taking the pressure off: "you don't
have to decide today"."""
_EXPLAINED = re.compile(r"\b(means|meaning|which is|that is|in other words|basically)\b")
_PITCH = re.compile(
    r"\b(i'?d recommend|i would recommend|i recommend|you should (switch|go with|enroll|join|"
    r"pick|take)|best plan for you|the plan for you|i'?ll (sign|enroll) you)\b"
)
_NEEDS_ASKED = re.compile(
    r"\b(doctors?|physicians?|medications?|medicines?|prescriptions?|pills?)\b"
)
_WAIT = re.compile(
    r"\b(hold off|wait (on|before|until)|don'?t (switch|sign|enroll|change)|"
    r"before you (switch|decide|sign)|check with|talk to (your|the) (benefits|retiree|hr))\b"
)


def _objection_for(
    objections: Sequence[Objection], last: str, history: Sequence[Turn], said: str
) -> Objection | None:
    """The first objection whose trigger the learner's latest line sets off and that the
    customer hasn't raised yet. Simple patterns; the real actor reads the prompt instead."""
    earlier = [_fold(t.text) for t in history[:-1] if t.speaker is Speaker.LEARNER]
    by_trigger = {o.trigger: o for o in objections}
    for trigger, fires in DETECTORS.items():
        o = by_trigger.get(trigger)
        if o is not None and o.lay_text not in said and fires(last, earlier):
            return o
    return None


def _pressures(text: str) -> bool:
    """Urgency talk, or a push to decide today that is neither the agent asking permission nor
    the agent saying there is no need to."""
    if _URGENCY.search(text):
        return True
    sentences = (s.strip() for s in re.split(r"(?<=[.?!])\s+", text))
    return any(_pushes(s) for s in sentences)


def _pushes(sentence: str) -> bool:
    ask = _PERMISSION_ASK.search(sentence)
    for push in _DECIDE_NOW.finditer(sentence):
        if _NO_RUSH.search(sentence[: push.start()]):
            continue
        # A push that comes before the question is still a push.
        if ask is None or push.start() < ask.start():
            return True
    return False


DETECTORS: dict[ObjectionTrigger, Callable[[str, Sequence[str]], bool]] = {
    ObjectionTrigger.PERSONAL_INFO: lambda last, _: bool(_PERSONAL_INFO.search(last)),
    ObjectionTrigger.PRESSURE: lambda last, _: _pressures(last),
    ObjectionTrigger.JARGON: lambda last, _: bool(jargon_in(last)) and not _EXPLAINED.search(last),
    ObjectionTrigger.RUSHED: lambda last, _: len(last.split()) > RUSHED_WORDS,
    ObjectionTrigger.EARLY_PITCH: lambda last, earlier: (
        bool(_PITCH.search(last)) and not any(_NEEDS_ASKED.search(e) for e in earlier)
    ),
    ObjectionTrigger.ASKED_TO_WAIT: lambda last, _: bool(_WAIT.search(last)),
}
"""How the scripted customer spots each trigger in the learner's folded latest line, given the
learner's earlier lines. One per ``ObjectionTrigger``, like ``TRIGGER_TEXT`` for the prompt."""


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
    return GeminiActor(model=model, api_key=key, timeout_s=actor_timeout_from_env(env))


def actor_timeout_from_env(env: dict[str, str] | None = None) -> float:
    env = dict(os.environ) if env is None else env
    raw = env.get(TIMEOUT_ENV, "")
    if not raw:
        return DEFAULT_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not (math.isfinite(value) and MIN_TIMEOUT_S <= value <= MAX_TIMEOUT_S):
        raise ActorConfigError(
            f"{TIMEOUT_ENV} must be seconds from {MIN_TIMEOUT_S:g} to {MAX_TIMEOUT_S:g}, "
            f"not {raw!r}"
        )
    return value
