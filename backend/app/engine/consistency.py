"""Actor-consistency QA: doctors, drugs and dollar amounts the AI customer said that nothing
gave it.

The customer may only use facts from its prompt, or repeat something the agent said earlier in
the call. Anything else is invented, and an invented fact can mislead the learner or make a
case unwinnable. This is content QA for authors: a flag never counts against the learner.

Detection is deliberately simple and errs toward missing a flag rather than raising a false
one. Doctors are caught by title ("Dr. Fischer") or by a catalog provider's full name; drugs
only by catalog name; amounts by "$", "dollars" or "bucks". Any number that appears anywhere in
what the customer was given counts as given, whatever its unit. Only words that were heard
count, for both speakers.
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
_TITLED = re.compile(
    r"\b(?i:dr\.?|doctor)\s+(?!(?i:dr|doctor)\b)([A-Z][a-z'’-]+(?:\s+[A-Z][a-z'’-]+)?)"
)
"""A title and the one or two capitalized words after it. A second title is not a name, so
"my doctor Dr. Smith" names Smith."""
_NOT_NAMES = set(
    "monday tuesday wednesday thursday friday saturday sunday january february march "  # noqa: SIM905
    "april may june july august september october november december "
    "i he she we they you it and but so the my that this well yes no okay oh thanks thank".split()
)
"""Capitalized words that can follow a title or a surname without being part of the name."""
_CONTRACTION = re.compile(r"['’](?:m|ve|ll|d|re|s)$", re.IGNORECASE)
_VAGUE = {"couple", "few", "several", "some", "many"}
"""Before a scale these make a rough quantity ("a couple hundred dollars"), not an amount."""
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
_BREAK = "|"
"""Stands in the token list for punctuation, which ends a run of number words."""


def check_consistency(
    prompt_text: str, catalog: Catalog, transcript: Sequence[Utterance]
) -> list[ConsistencyFlag]:
    """Flags for every customer line, judged against the prompt plus the agent's lines before
    it. ``transcript`` is in call order."""
    given = [prompt_text]
    flags: list[ConsistencyFlag] = []
    for utt in transcript:
        if not utt.delivered:
            continue  # never played, so never said
        if utt.speaker is Speaker.LEARNER:
            given.append(utt.heard_text)
            continue
        flags += _flags_for(utt, "\n".join(given), catalog)
    return flags


def _flags_for(utt: Utterance, given: str, catalog: Catalog) -> list[ConsistencyFlag]:
    said = utt.heard_text
    out: list[ConsistencyFlag] = []

    def flag(kind: FlagKind, value: str) -> None:
        if not any(f.kind is kind and f.said.casefold() == value.casefold() for f in out):
            out.append(ConsistencyFlag(utt_id=utt.utt_id, kind=kind, said=value, text=said))

    for name in _doctors_named(said, catalog):
        if not _doctor_given(given, name):
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
    names = [" ".join(w for w in words if _is_name_word(w)) for words in titled]
    names += [p.name for p in catalog.providers if _has_phrase(text, p.name)]
    return [re.sub(r"['’]s$", "", n) for n in names if n]


def _is_name_word(word: str) -> bool:
    """Not a pronoun, sentence opener or contraction: in "Dr. Raman I'm worried" the name is
    Raman. A possessive stays ("Raman's")."""
    base = _CONTRACTION.sub("", word)
    if base != word and word[-1] not in "sS":
        return False
    return base.casefold() not in _NOT_NAMES


def _doctor_given(given: str, name: str) -> bool:
    """A surname alone matches any given doctor with it. With a first name, both must match:
    "Dr. Arjun Raman" is not the "Dr. Priya Raman" the customer was told about."""
    words = name.split()
    return _has_phrase(given, name) if len(words) > 1 else _has_word(given, words[-1])


# Drugs


def _drug_names(catalog: Catalog) -> list[str]:
    """First word of each catalog drug name ("metoprolol" for "metoprolol succinate ER")."""
    return sorted({d.name.split()[0].casefold() for d in catalog.drugs})


# Amounts


def amounts_said(text: str) -> list[tuple[Decimal, str]]:
    """Dollar amounts in ``text``, each with the words that said it."""
    tokens = _tokens(text)
    run_ending_at = {end: start for start, end in _number_runs(tokens)}
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
            start = run_ending_at.get(i, i)
            before = [t.casefold() for t in tokens[max(start - 2, 0) : start]]
            if before[-1:] == ["of"]:
                before.pop()
            if tokens[start].casefold() in _SCALES and before and before[-1] in _VAGUE:
                continue  # "a couple hundred dollars" is no exact figure
            run = tokens[start:i]
            value = _words_value(run)
            if value is not None:
                out.append((value, " ".join([*run, tok])))
    return out


def numbers_in(text: str) -> set[Decimal]:
    """Every number in ``text``, in digits or words."""
    tokens = _tokens(text)
    out: set[Decimal] = set()
    for start, end in _number_runs(tokens):
        value = _words_value(tokens[start:end])
        if value is not None:
            out.add(value)
    for tok in tokens:
        if tok.lstrip("$")[:1].isdigit():
            value = _decimal(tok.lstrip("$"))
            if value is not None:
                out.add(value)
    return out


def _tokens(text: str) -> list[str]:
    """Words and numbers in order, with ``_BREAK`` wherever anything but spaces or a hyphen
    ("forty-five") stood between two of them."""
    out: list[str] = []
    last = 0
    for m in _TOKEN.finditer(text):
        if out and text[last : m.start()].strip(" \t\n-"):
            out.append(_BREAK)
        out.append(m.group())
        last = m.end()
    return out


def _number_runs(tokens: Sequence[str]) -> list[tuple[int, int]]:
    """Each run of number words that reads as one number, as ``(start, end)`` slices. "a" only
    opens a run before a scale ("a hundred"); "and" only joins a scale to what follows ("two
    hundred and fifty"). Anywhere else they are ordinary words and end the run, as punctuation
    does: "for one, two hundred for the other" is 1 and 200, not 300."""
    folded = [t.casefold() for t in tokens]

    def at(i: int) -> str:
        return folded[i] if i < len(folded) else ""

    runs: list[tuple[int, int]] = []
    i = 0
    while i < len(folded):
        if not (folded[i] in _NUMBER_WORDS or (folded[i] == "a" and at(i + 1) in _SCALES)):
            i += 1
            continue
        end = i + 1
        while at(end) in _NUMBER_WORDS or (
            at(end) == "and"
            and folded[end - 1] in _SCALES
            and (at(end + 1) in _UNITS or at(end + 1) in _TENS)
        ):
            end += 1
        runs.append((i, end))
        i = end
    return runs


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
    words = [w for w in folded if w in _NUMBER_WORDS]
    if not any(w in _SCALES for w in words):
        return _small_or_price(words)
    total = current = 0
    small: list[str] = []
    for w in [*words, ""]:
        if w in _SCALES or not w:
            value = _under_hundred(small) if small else 0
            if value is None:
                return None  # "one two hundred" is not one number
            current += value
            small = []
        if w == "hundred":
            current = (current or 1) * 100
        elif w == "thousand":
            total += (current or 1) * 1000
            current = 0
        elif w:
            small.append(w)
    return Decimal(total + current)


def _under_hundred(words: Sequence[str]) -> int | None:
    """ "nineteen", "ninety", "ninety nine". None for anything else."""
    match words:
        case [w] if w in _UNITS:
            return _UNITS[w]
        case [w] if w in _TENS:
            return _TENS[w]
        case [t, u] if t in _TENS and u in _UNITS and 0 < _UNITS[u] < 10:
            return _TENS[t] + _UNITS[u]
    return None


def _small_or_price(words: Sequence[str]) -> Decimal | None:
    """A number under a hundred, or a price said as two of them: "nineteen ninety-nine" is
    19.99, never 19 + 99. Any other string of number words is no one number."""
    whole = _under_hundred(words)
    if whole is not None:
        return Decimal(whole)
    for cut in range(1, len(words)):
        dollars, cents = _under_hundred(words[:cut]), _under_hundred(words[cut:])
        if dollars is not None and cents is not None and cents >= 10:
            return Decimal(dollars) + Decimal(cents) / 100
    return None


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
