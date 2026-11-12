"""Rule register models. See docs/PROPOSAL.md Appendix A and docs/rule-register.md."""

from datetime import date
from enum import StrEnum
from typing import Self

from pydantic import Field, HttpUrl, model_validator

from app.content.common import (
    AUTHORITY_PREFIX,
    Authority,
    CheckId,
    ContentModel,
    Evaluation,
    EvidencePolicy,
    OutcomeKind,
    RuleId,
    require_unique,
)


class RuleStatus(StrEnum):
    UNVERIFIED = "unverified"
    APPROVED = "approved"
    RETIRED = "retired"
    DISPUTED = "disputed"


class OnUnknown(StrEnum):
    """What evaluation does when the facts it needs are missing."""

    ASK = "ask"
    DEFER = "defer"
    INSTRUCTOR_REVIEW = "instructor_review"


class WorkflowEffect(StrEnum):
    """What the simulator does in the moment. Kept apart from grading, so a repair can fix a
    problem without erasing that it happened."""

    NONE = "none"
    WARNING = "warning"
    CORRECTION = "correction"
    STOP = "stop"
    ROUTING = "routing"


class GradingEffect(StrEnum):
    POINTS = "points"
    CRITICAL = "critical"
    INFORMATIONAL = "informational"
    UNGRADED = "ungraded"


class AppliesWhen(ContentModel):
    """Empty tuples mean "any"."""

    outcomes: tuple[OutcomeKind, ...] = ()


class Rule(ContentModel):
    rule_id: RuleId
    version: int = Field(ge=1)
    title: str
    requirement: str
    authority: Authority
    authority_outside_scope: Authority | None = None
    """Authority when ``applies_when`` does not hold. R-NEEDS is regulatory on an enrollment
    and a training objective otherwise."""
    citation: str
    source_urls: tuple[HttpUrl, ...] = Field(min_length=1)
    status: RuleStatus
    verified_as_of: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    applies_when: AppliesWhen = AppliesWhen()
    required_facts: tuple[str, ...] = ()
    evaluation: Evaluation
    evidence_policy: EvidencePolicy
    on_unknown: OnUnknown
    workflow_effect: WorkflowEffect = WorkflowEffect.NONE
    grading_effect: GradingEffect
    check_ids: tuple[CheckId, ...] = ()
    notes: str | None = None
    standard_text: str | None = None
    """CMS-standardized wording the agent must say, exactly as published, with ``{organizations}``
    and ``{products}`` as fill-ins. The reference drawer shows it apart from the app's own
    wording. Only approved, verified rules may carry it."""

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        prefix = AUTHORITY_PREFIX[self.authority]
        if not self.rule_id.startswith(f"{prefix}-"):
            raise ValueError(
                f"{self.rule_id}: {self.authority.value} rules use the {prefix}- prefix"
            )
        if self.status is RuleStatus.APPROVED and self.verified_as_of is None:
            raise ValueError(f"{self.rule_id}: approved rules need verified_as_of")
        if self.standard_text is not None:
            if self.status is not RuleStatus.APPROVED:
                raise ValueError(f"{self.rule_id}: only approved rules carry standard_text")
            try:
                self.standard_text.format(organizations=0, products=0)
            except (KeyError, IndexError, ValueError) as exc:
                raise ValueError(
                    f"{self.rule_id}: standard_text fill-ins are {{organizations}} and "
                    f"{{products}} only ({exc!r})"
                ) from exc
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_to < self.effective_from
        ):
            raise ValueError(f"{self.rule_id}: effective_to is before effective_from")
        if self.evaluation is not Evaluation.JUDGED and not self.check_ids:
            raise ValueError(f"{self.rule_id}: {self.evaluation.value} rules need check_ids")
        if self.authority_outside_scope is not None and not self.applies_when.outcomes:
            raise ValueError(f"{self.rule_id}: authority_outside_scope needs a scoped applies_when")
        return self

    def current_on(self, on: date) -> bool:
        """Not retired, and in effect on ``on``."""
        return (
            self.status is not RuleStatus.RETIRED
            and (self.effective_from is None or self.effective_from <= on)
            and (self.effective_to is None or on <= self.effective_to)
        )


class RuleSet(ContentModel):
    ruleset_version: str
    verified_as_of: date
    rules: tuple[Rule, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> Self:
        require_unique("rule ids", (r.rule_id for r in self.rules))
        return self

    def get(self, rule_id: str) -> Rule:
        for rule in self.rules:
            if rule.rule_id == rule_id:
                return rule
        raise KeyError(rule_id)
