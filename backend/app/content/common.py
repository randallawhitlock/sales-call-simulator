"""Shared base model, identifier types and enums for versioned content.

Content files live in ``content/`` as JSON. Every model is frozen and rejects unknown fields,
so a typo in a content file fails at load time instead of silently changing a grade.
"""

from collections import Counter
from collections.abc import Iterable
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class ContentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


Id = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]*$", max_length=64)]
"""Lowercase slug used for every catalog and scenario identifier."""

PlanId = Annotated[str, StringConstraints(pattern=r"^TRN-\d{4}-\d{3}$")]
"""Training plan id. The TRN prefix keeps them from looking like real CMS contract ids."""

RuleId = Annotated[str, StringConstraints(pattern=r"^[RGPOT]-[A-Z0-9]+(-[A-Z0-9]+)*$")]
"""Rule id. The prefix letter names the authority: R regulatory, G CMS guidance, P plan,
O organization, T training."""

FictionalZip = Annotated[str, StringConstraints(pattern=r"^000\d{2}$")]
"""ZIP codes 00000-00099 are unassigned by USPS, so a catalog address can never be real."""

FictionalPhone = Annotated[str, StringConstraints(pattern=r"^\(\d{3}\) 555-01\d{2}$")]
"""555-0100 through 555-0199 are reserved for fictional use."""

Money = Annotated[Decimal, Field(ge=0, max_digits=9, decimal_places=2)]


class Authority(StrEnum):
    REGULATORY = "regulatory"
    CMS_GUIDANCE = "cms_guidance"
    PLAN = "plan"
    ORGANIZATION = "organization"
    TRAINING = "training"


AUTHORITY_PREFIX: dict[Authority, str] = {
    Authority.REGULATORY: "R",
    Authority.CMS_GUIDANCE: "G",
    Authority.PLAN: "P",
    Authority.ORGANIZATION: "O",
    Authority.TRAINING: "T",
}


class Evaluation(StrEnum):
    DETERMINISTIC = "deterministic"
    JUDGED = "judged"
    HYBRID = "hybrid"


class EvidencePolicy(StrEnum):
    EVENT = "event"
    TRANSCRIPT = "transcript"
    BOTH = "both"


class OutcomeKind(StrEnum):
    ENROLL = "enroll"
    KEEP_CURRENT = "keep_current"
    DEFER = "defer"
    STOP = "stop"


class CheckId(StrEnum):
    """Deterministic checks. Each result cites the event or utterance ids it relied on.

    "Outcome plan" means the submitted plan when the learner enrolled, else the current plan.
    """

    OUTCOME_ACCEPTABLE = "outcome_acceptable"
    PROVIDER_VERIFIED_AT_LOCATION = "provider_verified_at_location"
    DRUGS_CHECKED = "drugs_checked"
    RESTRICTION_SEEN = "restriction_seen"
    PHARMACY_CHECKED = "pharmacy_checked"
    NEEDS_MATCH_TRUTH = "needs_match_truth"
    EVIDENCE_CONTEXT_MATCHES = "evidence_context_matches"
    SOB_FOR_SUBMITTED_PLAN = "sob_for_submitted_plan"
    PECL_COMPLETE_BEFORE_SUBMIT = "pecl_complete_before_submit"
    CONSENT_BEFORE_SUBMIT = "consent_before_submit"
    APPLICATION_MATCHES_RECORD = "application_matches_record"
    NO_ENROLL_WITH_UNRESOLVED_OTHER_COVERAGE = "no_enroll_with_unresolved_other_coverage"
    FOLLOWUP_CREATED = "followup_created"
    COVERAGE_CLAIMS_BACKED = "coverage_claims_backed"
    SCOPE_BEFORE_PLAN_DISCUSSION = "scope_before_plan_discussion"
    DISCLAIMER_BEFORE_BENEFITS = "disclaimer_before_benefits"
    RECORDING_NOTICE_GIVEN = "recording_notice_given"
    PCP_MATCHES_PLAN_RULE = "pcp_matches_plan_rule"


def require_unique(label: str, ids: Iterable[str]) -> None:
    dupes = sorted(k for k, n in Counter(ids).items() if n > 1)
    if dupes:
        raise ValueError(f"duplicate {label}: {', '.join(dupes)}")


def require_known(label: str, refs: Iterable[str], known: set[str]) -> None:
    missing = sorted(set(refs) - known)
    if missing:
        raise ValueError(f"unknown {label}: {', '.join(missing)}")
