"""Event log contract. Every learner action and every utterance is an append-only event.

One utterance contract serves text and voice. In voice mode ``heard_text`` is what the other
party actually heard, which can be shorter than ``text`` when speech was cut off. Grading reads
``heard_text`` only, so words that were never played never count as said.
"""

import re
import unicodedata
from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)

from app.content.common import Id

UttId = Annotated[str, StringConstraints(pattern=r"^u\d{4,}$")]
ClientUttId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{8,64}$")]


class Speaker(StrEnum):
    LEARNER = "learner"
    CUSTOMER = "customer"


class Channel(StrEnum):
    TEXT = "text"
    VOICE = "voice"


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class UtteranceIn(_Frozen):
    """Request body for posting an utterance. ``client_utt_id`` makes retries idempotent."""

    client_utt_id: ClientUttId
    speaker: Speaker
    channel: Channel
    text: str = Field(min_length=1)
    heard_text: str | None = None
    t_ms: int | None = Field(default=None, ge=0)
    """Offset from call start. Text turns are stamped by the server when omitted."""
    duration_ms: int | None = Field(default=None, ge=0)
    interrupted: bool = False

    @model_validator(mode="after")
    def _text_turns_are_whole(self) -> Self:
        if self.channel is Channel.TEXT:
            if self.interrupted:
                raise ValueError("text turns cannot be interrupted")
            if self.heard_text is not None and self.heard_text != self.text:
                raise ValueError("text turns are heard exactly as typed")
        return self


class Utterance(_Frozen):
    utt_id: UttId
    session_id: Id
    speaker: Speaker
    channel: Channel
    text: str
    heard_text: str
    t_ms: int = Field(ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    interrupted: bool = False

    @classmethod
    def from_input(cls, body: UtteranceIn, *, utt_id: str, session_id: str, t_ms: int) -> Self:
        return cls(
            utt_id=utt_id,
            session_id=session_id,
            speaker=body.speaker,
            channel=body.channel,
            text=body.text,
            heard_text=body.text if body.heard_text is None else body.heard_text,
            t_ms=body.t_ms if body.t_ms is not None else t_ms,
            duration_ms=body.duration_ms,
            interrupted=body.interrupted,
        )

    @property
    def delivered(self) -> bool:
        """Speech cut off before any of it played doesn't count as said."""
        return bool(normalize_for_quote(self.heard_text))

    def contains_quote(self, quote: str) -> bool:
        """Whether a judge's quote really appears in what was heard.

        Matching ignores case, whitespace runs, curly versus straight quotes and surrounding
        punctuation, and nothing else. A paraphrase does not match, and the quote has to start
        and end on whole words: "no" is not in "I know", "you can" is not in "you can't".
        """
        return quote_in(quote, self.heard_text)


_QUOTE_MAP = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})
_SPACE = re.compile(r"\s+")
_EDGE_PUNCT = " \t\n.,;:!?\"'()[]-–—…"


def normalize_for_quote(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).translate(_QUOTE_MAP).casefold()
    return _SPACE.sub(" ", folded).strip(_EDGE_PUNCT)


def quote_in(quote: str, text: str) -> bool:
    """Whether ``quote`` appears in ``text`` as a run of whole words, after normalizing both.

    An apostrophe or hyphen inside a word is part of the word: "you can" is not in "you can't",
    and "preferred" is not in "non-preferred". Either would let a quote verify against a line
    that says the opposite."""
    needle = normalize_for_quote(quote)
    if not needle:
        return False
    pattern = rf"(?<!\w)(?<!\w['-]){re.escape(needle)}(?!\w|['-]\w)"
    return re.search(pattern, normalize_for_quote(text)) is not None


class EventType(StrEnum):
    CALL_STARTED = "call_started"
    UTTERANCE = "utterance"
    SCOPE_RECORDED = "scope_recorded"
    SEARCH = "search"
    LOOKUP = "lookup"
    DOCUMENT_OPENED = "document_opened"
    EVIDENCE_PINNED = "evidence_pinned"
    RECORD_SAVED = "record_saved"
    PECL_ITEM_MARKED = "pecl_item_marked"
    CONSENT_MARKED = "consent_marked"
    APPLICATION_CREATED = "application_created"
    APPLICATION_UPDATED = "application_updated"
    APPLICATION_SUBMITTED = "application_submitted"
    APPLICATION_WITHDRAWN = "application_withdrawn"
    FOLLOWUP_CREATED = "followup_created"
    HOLD_STARTED = "hold_started"
    HOLD_ENDED = "hold_ended"
    CALL_ENDED = "call_ended"


class Event(_Frozen):
    """One append-only log entry. ``seq`` is arrival order; ``at`` is the server clock."""

    session_id: Id
    seq: int = Field(ge=1)
    type: EventType
    at: AwareDatetime
    t_ms: int = Field(ge=0)
    utt_id: UttId | None = None
    payload: dict[str, JsonValue] = {}

    @model_validator(mode="after")
    def _utterance_link(self) -> Self:
        if (self.type is EventType.UTTERANCE) != (self.utt_id is not None):
            raise ValueError("utt_id is set exactly on utterance events")
        return self
