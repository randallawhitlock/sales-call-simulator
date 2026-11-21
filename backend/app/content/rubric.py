"""Scoring rubric. The 100-point design is original and educational, not issued by CMS."""

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from app.content.common import (
    CheckId,
    ContentModel,
    Evaluation,
    EvidencePolicy,
    Id,
    RuleId,
    require_unique,
)


class Dimension(StrEnum):
    DISCOVERY = "discovery"
    ELIGIBILITY = "eligibility"
    VERIFICATION = "verification"
    RECOMMENDATION = "recommendation"
    DOCUMENTATION = "documentation"
    COMMUNICATION = "communication"
    COMPLETION = "completion"


DIMENSION_WEIGHTS: dict[Dimension, int] = {
    Dimension.DISCOVERY: 15,
    Dimension.ELIGIBILITY: 15,
    Dimension.VERIFICATION: 20,
    Dimension.RECOMMENDATION: 20,
    Dimension.DOCUMENTATION: 15,
    Dimension.COMMUNICATION: 10,
    Dimension.COMPLETION: 5,
}


class RubricCriterion(ContentModel):
    criterion_id: Id
    dimension: Dimension
    title: str
    points: int = Field(ge=1)
    method: Evaluation
    evidence_policy: EvidencePolicy
    critical: bool = False
    rule_ids: tuple[RuleId, ...] = ()
    check_id: CheckId | None = None
    judged_spec: str | None = None
    """What the judge looks for, in plain language. Required for judged and hybrid items."""

    @model_validator(mode="after")
    def _method_inputs(self) -> Self:
        needs_check = self.method is not Evaluation.JUDGED
        needs_spec = self.method is not Evaluation.DETERMINISTIC
        if needs_check != (self.check_id is not None):
            raise ValueError(
                f"{self.criterion_id}: check_id must be set exactly when "
                f"method is deterministic or hybrid"
            )
        if needs_spec != bool(self.judged_spec):
            raise ValueError(
                f"{self.criterion_id}: judged_spec must be set exactly when "
                f"method is judged or hybrid"
            )
        return self


class Rubric(ContentModel):
    rubric_id: Id
    version: int = Field(ge=1)
    criteria: tuple[RubricCriterion, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _weights(self) -> Self:
        require_unique("criterion ids", (c.criterion_id for c in self.criteria))
        for dimension, weight in DIMENSION_WEIGHTS.items():
            total = sum(c.points for c in self.criteria if c.dimension is dimension)
            if total != weight:
                raise ValueError(f"{dimension.value} criteria total {total}, expected {weight}")
        return self
