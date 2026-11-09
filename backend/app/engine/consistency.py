"""Actor-consistency QA: doctors, drugs and dollar amounts the AI customer said that nothing
gave it.

The customer may only use facts from its prompt, or repeat something the agent said earlier in
the call. Anything else is invented, and an invented fact can mislead the learner or make a
case unwinnable. This is content QA for authors: a flag never counts against the learner.

Detection is deliberately simple and errs toward missing a flag rather than raising a false
one. Doctors are caught by title ("Dr. Fischer") or by a catalog provider's full name; drugs
only by catalog name; amounts by "$", "dollars" or "bucks". Any number that appears anywhere in
what the customer was given counts as given, whatever its unit.
"""

import re
from collections.abc import Iterable, Sequence
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from pydantic import BaseModel

from app.content.catalog import Catalog
from app.engine.events import Speaker, Utterance


class FlagKind(StrEnum):
    DOCTOR = "doctor"
    DRUG = "drug"
    AMOUNT = "amount"


class ConsistencyFlag(BaseModel):
    utt_id: str
    kind: FlagKind
    said: str
    """The doctor, drug or amount as the customer said it."""
    text: str
    """The customer's whole line."""


_TOKEN = re.compile(r"\$?\d+(?:,\d{3})*(?:\.\d+)?|[A-Za-z]+")
_TITLED = re.compile(r"\b(?i:dr\.?|doctor)\s+([A-Z][a-z'’-]+(?:\s+[A-Z][a-z'’-]+)?)")
_NOT_NAMES = set(
    "monday tuesday wednesday thursday friday saturday sunday january february march "  # noqa: SIM905
    "april may june july august september october november december".split()
)
_CURRENCY = {"dollar", "dollars", "buck", "bucks"}
_UNIT_WORDS = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "  # noqa: SIM905
    "fourteen fifteen sixteen seventeen eighteen nineteen".split()
)
_TEN_WORDS = "twenty thirty forty fifty sixty seventy eighty ninety".split()  # noqa: SIM905
_UNITS = {w: i for i, w in enumerate(_UNIT_WORDS)}
_TENS = {w: 10 * i for i, w in enumerate(_TEN_WORDS, 2)}
_SCALES = {"hundred": 100, "thousand": 1000}
_NUMBER_WORDS = _UNITS.keys() | _TENS.keys() | _SCALES.keys()


def check_consistency(
    prompt_text: str, catalog: Catalog, transcript: Sequence[Utterance]
) -> list[ConsistencyFlag]:
    """Flags for every customer line, judged against the prompt plus the agent's lines before
    it. ``transcript`` is in call order."""
    given = [prompt_text]
    flags: list[ConsistencyFlag] = []
    for utt in transcript:
        if utt.speaker is Speaker.LEARNER:
            given.append(utt.text)
            continue
        flags += _flags_for(utt, "\n".join(given), catalog)
    return flags


def _flags_for(utt: Utterance, given: str, catalog: Catalog) -> list[ConsistencyFlag]:
    said = utt.text
    out: list[ConsistencyFlag] = []

    def flag(kind: FlagKind, value: str) -> None:
        if not any(f.kind is kind and f.said.casefold() == value.casefold() for f in out):
            out.append(ConsistencyFlag(utt_id=utt.utt_id, kind=kind, said=value, text=said))

    for name in _doctors_named(said, catalog):
        if not _has_word(given, _surname(name)):
            flag(FlagKind.DOCTOR, name)
    for drug in _drug_names(catalog):
        if _has_word(said, drug) and not _has_word(given, drug):
            flag(FlagKind.DRUG, drug)
    known = numbers_in(given)
    for amount, as_said in amounts_said(said):
        if amount not in known:
            flag(FlagKind.AMOUNT, as_said)
    return out


# Doctors


def _doctors_named(text: str, catalog: Catalog) -> list[str]:
    titled = [m.group(1).split() for m in _TITLED.finditer(text)]
    names = [" ".join(w for w in words if w.casefold() not in _NOT_NAMES) for words in titled]
    names += [p.name for p in catalog.providers if _has_phrase(text, p.name)]
    return [re.sub(r"['’]s$", "", n) for n in names if n]


def _surname(name: str) -> str:
    return re.sub(r"['’]s$", "", name.split()[-1])


# Drugs


def _drug_names(catalog: Catalog) -> list[str]:
    """First word of each catalog drug name ("metoprolol" for "metoprolol succinate ER")."""
    return sorted({d.name.split()[0].casefold() for d in catalog.drugs})


# Amounts


def amounts_said(text: str) -> list[tuple[Decimal, str]]:
    """Dollar amounts in ``text``, each with the words that said it."""
    tokens = _TOKEN.findall(text)
    out: list[tuple[Decimal, str]] = []
    for i, tok in enumerate(tokens):
        if tok.startswith("$"):
            value = _decimal(tok[1:])
            if value is not None:
                out.append((value, tok))
        elif tok.casefold() in _CURRENCY and i > 0:
            prev = tokens[i - 1]
            if prev[0].isdigit():
                value = _decimal(prev)
                if value is not None:
                    out.append((value, f"{prev} {tok}"))
                continue
            start = i
            while start > 0 and _is_number_word(tokens[start - 1]):
                start -= 1
            run = tokens[start:i]
            value = _words_value(run)
            if value is not None:
                out.append((value, " ".join([*run, tok])))
    return out


def numbers_in(text: str) -> set[Decimal]:
    """Every number in ``text``, in digits or words."""
    tokens = _TOKEN.findall(text)
    out: set[Decimal] = set()
    run: list[str] = []
    for tok in [*tokens, ""]:
        if tok and _is_number_word(tok):
            run.append(tok)
            continue
        value = _words_value(run)
        if value is not None:
            out.add(value)
        run = []
        if tok and tok.lstrip("$")[:1].isdigit():
            value = _decimal(tok.lstrip("$"))
            if value is not None:
                out.add(value)
    return out


def _is_number_word(tok: str) -> bool:
    return tok.casefold() in _NUMBER_WORDS or tok.casefold() in ("a", "and")


def _words_value(words: Iterable[str]) -> Decimal | None:
    """ "a hundred and fifty" -> 150. None if the words hold no number."""
    folded = [w.casefold() for w in words]
    while (
        folded
        and folded[0] in ("a", "and")
        and not (folded[0] == "a" and len(folded) > 1 and folded[1] in _SCALES)
    ):
        folded.pop(0)
    while folded and folded[-1] in ("a", "and"):
        folded.pop()
    if not any(w in _NUMBER_WORDS for w in folded):
        return None
    total = current = 0
    for w in folded:
        if w in _UNITS:
            current += _UNITS[w]
        elif w in _TENS:
            current += _TENS[w]
        elif w == "hundred":
            current = (current or 1) * 100
        elif w == "thousand":
            total += (current or 1) * 1000
            current = 0
    return Decimal(total + current)


def _decimal(digits: str) -> Decimal | None:
    try:
        return Decimal(digits.replace(",", "").rstrip("."))
    except InvalidOperation:
        return None


# Matching


def _has_word(text: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", text, re.IGNORECASE) is not None


def _has_phrase(text: str, phrase: str) -> bool:
    pattern = r"\s+".join(re.escape(w) for w in phrase.split())
    return re.search(rf"\b{pattern}\b", text, re.IGNORECASE) is not None
