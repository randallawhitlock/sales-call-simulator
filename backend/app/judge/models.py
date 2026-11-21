"""What the judge returns, and what the server stores of each grading run."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

NOTES = "notes"
"""The ``utt_id`` a quote uses when it cites the saved call notes instead of a line."""


class JudgedStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_OBSERVED = "not_observed"
    NOT_APPLICABLE = "not_applicable"


class _Out(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Quote(_Out):
    utt_id: str
    """A transcript line, or ``notes`` for the saved call notes."""
    quote: str


class JudgedCriterion(_Out):
    criterion_id: str
    status: JudgedStatus
    rationale: str = ""
    evidence: list[Quote] = []


ClaimKind = Literal["provider_network", "formulary", "pharmacy_network"]


class ClaimOut(_Out):
    """A learner statement that something is, or isn't, covered or in network on a plan."""

    utt_id: str
    quote: str
    plan_id: str
    kind: ClaimKind
    subject_id: str
    covered: bool = True
    year: int | None = None
    """The year the statement is about. None, as in output stored before this field existed,
    means the plan year."""


class JudgeOutput(_Out):
    criteria: list[JudgedCriterion] = []
    coverage_claims: list[ClaimOut] = []


class GradeRow(BaseModel):
    """One stored grading run. A debrief reuses it until someone regrades, so the same call
    always shows the same judged results."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    judge: str
    """``claude:<model>``."""
    prompt_version: str
    rubric_id: str
    rubric_version: int = Field(ge=1)
    graded_at: datetime
    output: JudgeOutput


def output_schema() -> dict[str, object]:
    """JSON schema for the judge's structured output. Written out by hand so it stays within
    what structured outputs accept: every object closed, every field required."""
    quote = {
        "type": "object",
        "properties": {"utt_id": {"type": "string"}, "quote": {"type": "string"}},
        "required": ["utt_id", "quote"],
        "additionalProperties": False,
    }
    criterion = {
        "type": "object",
        "properties": {
            "criterion_id": {"type": "string"},
            "status": {"type": "string", "enum": [s.value for s in JudgedStatus]},
            "rationale": {"type": "string"},
            "evidence": {"type": "array", "items": quote},
        },
        "required": ["criterion_id", "status", "rationale", "evidence"],
        "additionalProperties": False,
    }
    claim = {
        "type": "object",
        "properties": {
            "utt_id": {"type": "string"},
            "quote": {"type": "string"},
            "plan_id": {"type": "string"},
            "kind": {
                "type": "string",
                "enum": ["provider_network", "formulary", "pharmacy_network"],
            },
            "subject_id": {"type": "string"},
            "covered": {"type": "boolean"},
            "year": {"type": "integer"},
        },
        "required": ["utt_id", "quote", "plan_id", "kind", "subject_id", "covered", "year"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "criteria": {"type": "array", "items": criterion},
            "coverage_claims": {"type": "array", "items": claim},
        },
        "required": ["criteria", "coverage_claims"],
        "additionalProperties": False,
    }
