"""The debrief: the scorecard, the evidence it links to, the case notes and what to retry.

Built only for a call that has ended. Everything in it is derived from the stored event log,
the transcript and the latest stored judgment, so opening a debrief twice shows the same thing.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

from app.content.common import OutcomeKind
from app.content.loader import ContentBundle
from app.content.scenario import OutcomeVerdict, Scenario
from app.engine.checks import Facts
from app.engine.events import Event, Utterance
from app.engine.grading import (
    CriterionResult,
    DimensionScore,
    GradeStatus,
    GradingState,
    Score,
    Scorecard,
    grade,
    outcome_label,
)
from app.engine.store import SessionRow
from app.engine.timeline import TimelineEntry, timeline
from app.judge.contract import Judgment

RETRY_FOCUS_ITEMS = 3


class JudgeState(StrEnum):
    GRADED = "graded"
    """The debrief uses a stored judgment."""
    NOT_RUN = "not_run"
    """A judge is configured and has not graded this call yet."""
    OFF = "off"
    """No judge is configured. Judged items stay ungraded."""
    FAILED = "failed"
    """The judge was asked and gave no usable answer. Asking again may work."""


class JudgeInfo(BaseModel):
    state: JudgeState
    judge: str | None = None
    """The judge behind the judgment in use, else the configured judge."""
    served_model: str | None = None
    prompt_version: str | None = None
    attempt: int | None = None
    graded_at: datetime | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None
    """Why the latest grading request failed. A stored judgment, if any, is still in use."""


PENDING = {
    JudgeState.NOT_RUN: "Waiting for the judge.",
    JudgeState.OFF: "No judge is configured, so this item was not graded.",
    JudgeState.FAILED: "The judge did not answer, so this item was not graded.",
}


class Versions(BaseModel):
    scenario_version: int
    catalog_version: str
    ruleset_version: str
    rubric: str


class OutcomeView(BaseModel):
    kind: OutcomeKind | None
    """None when the call ended with no outcome recorded."""
    plan_name: str | None


class CaseOutcome(BaseModel):
    kind: OutcomeKind
    plans: list[str]
    acceptable: bool
    rationale: str


class CaseTrap(BaseModel):
    description: str
    rationale: str


class CaseNotes(BaseModel):
    """What the case was built to teach. Scenario truth, shown only after the call."""

    title: str
    outcomes: list[CaseOutcome]
    traps: list[CaseTrap]


class RetryFocus(BaseModel):
    criterion_id: str
    title: str
    why: str


class Debrief(BaseModel):
    session_id: str
    scenario_id: str
    versions: Versions
    state: GradingState
    score: Score
    dimensions: list[DimensionScore]
    criteria: list[CriterionResult]
    critical: list[str]
    judge: JudgeInfo
    outcome: OutcomeView
    timeline: list[TimelineEntry]
    case: CaseNotes
    retry_focus: list[RetryFocus]
    """Where to put attention on another attempt at this case, most costly first."""


def judge_info(
    judgment: Judgment | None, configured: str | None, error: str | None = None
) -> JudgeInfo:
    if judgment is not None:
        return JudgeInfo(
            state=JudgeState.GRADED,
            judge=judgment.judge,
            served_model=judgment.served_model,
            prompt_version=judgment.prompt_version,
            attempt=judgment.attempt,
            graded_at=judgment.created_at,
            input_tokens=judgment.input_tokens,
            output_tokens=judgment.output_tokens,
            error=error,
        )
    if error is not None:
        return JudgeInfo(state=JudgeState.FAILED, judge=configured, error=error)
    state = JudgeState.OFF if configured is None else JudgeState.NOT_RUN
    return JudgeInfo(state=state, judge=configured)


def scorecard(facts: Facts, content: ContentBundle, judgment: Judgment | None) -> Scorecard:
    return grade(facts, content.rubric, content.ruleset, judgment.output if judgment else None)


def build_debrief(
    row: SessionRow,
    content: ContentBundle,
    events: list[Event],
    utterances: list[Utterance],
    judgment: Judgment | None,
    info: JudgeInfo,
) -> Debrief:
    scenario = content.scenario(row.scenario_id)
    facts = Facts(scenario, content.catalog, content.frame, events, utterances)
    card = grade(
        facts,
        content.rubric,
        content.ruleset,
        judgment.output if judgment else None,
        pending=PENDING.get(info.state, ""),
    )
    kind, plan_name = outcome_label(facts)
    return Debrief(
        session_id=row.session_id,
        scenario_id=row.scenario_id,
        versions=Versions(
            scenario_version=row.scenario_version,
            catalog_version=row.catalog_version,
            ruleset_version=row.ruleset_version,
            rubric=f"{row.rubric_id} v{row.rubric_version}",
        ),
        state=card.state,
        score=card.score,
        dimensions=card.dimensions,
        criteria=card.criteria,
        critical=card.critical,
        judge=info,
        outcome=OutcomeView(kind=kind, plan_name=plan_name),
        timeline=timeline(events, content.catalog),
        case=case_notes(scenario, content),
        retry_focus=retry_focus(card.criteria),
    )


def case_notes(scenario: Scenario, content: ContentBundle) -> CaseNotes:
    return CaseNotes(
        title=scenario.title,
        outcomes=[
            CaseOutcome(
                kind=o.kind,
                plans=[content.catalog.plan(i).name for i in o.plan_ids],
                acceptable=o.verdict is OutcomeVerdict.ACCEPTABLE,
                rationale=scenario.without_canaries(o.rationale),
            )
            for o in scenario.outcomes
        ],
        traps=[
            CaseTrap(
                description=scenario.without_canaries(t.description),
                rationale=scenario.without_canaries(t.rationale),
            )
            for t in scenario.traps
        ],
    )


def retry_focus(criteria: list[CriterionResult]) -> list[RetryFocus]:
    """The scored items that earned nothing, critical ones first, then by points lost."""
    missed = [c for c in criteria if c.status in (GradeStatus.FAIL, GradeStatus.NOT_OBSERVED)]
    missed.sort(key=lambda c: (not c.critical, -c.points))
    return [
        RetryFocus(criterion_id=c.criterion_id, title=c.title, why=c.rationale or c.detail)
        for c in missed[:RETRY_FOCUS_ITEMS]
    ]
