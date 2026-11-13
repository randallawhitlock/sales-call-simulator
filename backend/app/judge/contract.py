"""The judge's output contract.

Evidence is ``{utt_id, quote}``: a quote copied from one utterance. A model can misquote, cite
the wrong line or invent a line, so nothing here counts until the server has found each quote
where the judge said it was.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.engine.checks import ClaimKind

NOTES_REF = "notes"
"""Stands in for an utterance id when the quote comes from the learner's saved notes."""


class _Strict(BaseModel):
    """Every field required and nothing extra, which is what structured outputs can enforce."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Verdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_OBSERVED = "not_observed"
    NOT_APPLICABLE = "not_applicable"


class JudgeEvidence(_Strict):
    utt_id: str
    """The utterance the quote is from, or ``notes`` for the saved notes."""
    quote: str


class JudgeVerdict(_Strict):
    criterion_id: str
    verdict: Verdict
    rationale: str
    evidence: list[JudgeEvidence]


class JudgeClaim(_Strict):
    """A statement by the learner that a doctor, drug or pharmacy is, or is not, covered or in
    network on a plan."""

    utt_id: str
    quote: str
    kind: ClaimKind
    plan_id: str
    subject_id: str
    """practice_id, drug_id or pharmacy_id from the catalog reference."""
    covered: bool


class JudgeOutput(_Strict):
    verdicts: list[JudgeVerdict]
    coverage_claims: list[JudgeClaim]


class Judgment(BaseModel):
    """One stored judge run. Debriefs are rebuilt from the event log and the latest of these,
    so a stored call grades the same way until someone asks for a regrade."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    attempt: int
    judge: str
    """The judge that was asked, e.g. ``claude:claude-opus-5-5``."""
    served_model: str | None = None
    """The model that answered, when the provider reports it. It differs from the one asked
    only if the provider fell back to another model."""
    prompt_version: str
    created_at: datetime
    input_tokens: int | None = None
    output_tokens: int | None = None
    output: JudgeOutput


def output_schema() -> dict[str, Any]:
    """JSON schema for the judge's reply, without the titles pydantic adds."""
    schema: dict[str, Any] = _without_titles(JudgeOutput.model_json_schema())
    return schema


def _without_titles(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            k: (
                {name: _without_titles(prop) for name, prop in v.items()}
                if k in ("properties", "$defs")
                else _without_titles(v)
            )
            for k, v in node.items()
            if k != "title"
        }
    if isinstance(node, list):
        return [_without_titles(v) for v in node]
    return node
