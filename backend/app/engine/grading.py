"""Turns the deterministic checks and the judge's output into graded criteria and a score.

Pure: the same log and the same stored judge output always give the same debrief.

- Every judge quote is looked up in the line it cites. A quote that isn't there, or a pass
  with no quote, sends the criterion to ``review_required`` instead of counting.
- A hybrid criterion needs both halves: a code fail or a judge fail fails it, and an
  ungraded half leaves it ungraded. Only code can make a finding critical; a model's judgment
  alone never does.
- ``coverage_claims_backed`` is decided by code from the claims the judge found: each claim
  needs an earlier lookup that showed what the learner said.
- Not-applicable, ungraded and review-required criteria leave the denominator. Any ungraded
  or review-required criterion makes the call incomplete, never failed.
"""

from datetime import date
from enum import StrEnum

from pydantic import BaseModel

from app.content.catalog import Catalog
from app.content.common import Authority, CheckId, Evaluation
from app.content.rubric import DIMENSION_WEIGHTS, Dimension, Rubric, RubricCriterion
from app.content.rules import RuleSet, RuleStatus
from app.content.scenario import CallFrame, Scenario
from app.engine.checks import (
    CheckResult,
    CheckStatus,
    CoverageClaim,
    Facts,
    claim_backed,
    run_checks,
)
from app.engine.events import Event, Speaker, Utterance, quote_in_text
from app.judge.models import NOTES, ClaimOut, JudgedCriterion, JudgedStatus, JudgeOutput, Quote
from app.judge.prompt import notes_text

FOCUS_SIZE = 3
"""How many missed criteria a targeted retry asks the learner to work on."""


class GradeStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"
    NOT_OBSERVED = "not_observed"
    REVIEW_REQUIRED = "review_required"
    UNGRADED = "ungraded"


_FROM_CHECK = {
    CheckStatus.PASS: GradeStatus.PASS,
    CheckStatus.FAIL: GradeStatus.FAIL,
    CheckStatus.NOT_APPLICABLE: GradeStatus.NOT_APPLICABLE,
    CheckStatus.NOT_OBSERVED: GradeStatus.NOT_OBSERVED,
    CheckStatus.UNGRADED: GradeStatus.UNGRADED,
}
_FROM_JUDGE = {
    JudgedStatus.PASS: GradeStatus.PASS,
    JudgedStatus.FAIL: GradeStatus.FAIL,
    JudgedStatus.NOT_OBSERVED: GradeStatus.NOT_OBSERVED,
    JudgedStatus.NOT_APPLICABLE: GradeStatus.NOT_APPLICABLE,
}
# For combining the halves of a hybrid: a definite miss from either half wins, then doubt,
# then a pass.
_SEVERITY = [
    GradeStatus.FAIL,
    GradeStatus.NOT_OBSERVED,
    GradeStatus.REVIEW_REQUIRED,
    GradeStatus.UNGRADED,
    GradeStatus.PASS,
]
_SCORED = {GradeStatus.PASS, GradeStatus.FAIL, GradeStatus.NOT_OBSERVED}
_INCOMPLETE = {GradeStatus.UNGRADED, GradeStatus.REVIEW_REQUIRED}


class EvidenceView(BaseModel):
    utt_id: str
    """A transcript line, or ``notes``."""
    speaker: Speaker | None = None
    quote: str
    verified: bool
    """The quote was found in the cited line. Code-cited lines are verified by definition."""
    problem: str | None = None


class RuleCite(BaseModel):
    rule_id: str
    title: str
    authority: Authority
    citation: str
    status: RuleStatus
    verified_as_of: date | None


class CriterionResult(BaseModel):
    criterion_id: str
    dimension: Dimension
    title: str
    method: Evaluation
    points: int
    earned: int | None
    """None when the criterion leaves the denominator."""
    status: GradeStatus
    critical: bool = False
    detail: str
    evidence: list[EvidenceView] = []
    event_seqs: list[int] = []
    rules: list[RuleCite] = []


class DimensionScore(BaseModel):
    dimension: Dimension
    weight: int
    earned: int
    possible: int


class Graded(BaseModel):
    criteria: list[CriterionResult]
    dimensions: list[DimensionScore]
    earned: int
    possible: int
    score: int | None
    """Earned over possible, out of 100, across the criteria that count. None if none do."""
    complete: bool
    critical_findings: list[str]
    focus: list[str]
    """Criteria a targeted retry should work on: critical misses first, then the costliest."""


def grade_call(
    *,
    scenario: Scenario,
    catalog: Catalog,
    frame: CallFrame,
    rubric: Rubric,
    ruleset: RuleSet,
    events: list[Event],
    utterances: list[Utterance],
    judge_output: JudgeOutput | None,
    judge_note: str = "Waiting for the judge.",
) -> Graded:
    """``judge_note`` explains judged items left ungraded when ``judge_output`` is None."""
    checks = {r.check_id: r for r in run_checks(scenario, catalog, frame, events, utterances)}
    facts = Facts(scenario, catalog, frame, events, utterances)
    ctx = _Context(
        facts=facts,
        catalog=catalog,
        utterances={u.utt_id: u for u in utterances},
        notes=notes_text(facts.state.record.notes),
        judged=_first_by_id(judge_output.criteria) if judge_output else None,
        claims=judge_output.coverage_claims if judge_output else None,
        judge_note=judge_note,
    )
    results = [
        _with_rules(_criterion(c, checks, ctx), c, ruleset, frame.call_date)
        for c in rubric.criteria
    ]
    return _score(results, rubric)


class _Context:
    def __init__(
        self,
        *,
        facts: Facts,
        catalog: Catalog,
        utterances: dict[str, Utterance],
        notes: str,
        judged: dict[str, JudgedCriterion] | None,
        claims: list[ClaimOut] | None,
        judge_note: str,
    ) -> None:
        self.facts = facts
        self.catalog = catalog
        self.utterances = utterances
        self.notes = notes
        self.judged = judged
        self.claims = claims
        self.judge_note = judge_note


def _first_by_id(items: list[JudgedCriterion]) -> dict[str, JudgedCriterion]:
    found: dict[str, JudgedCriterion] = {}
    for item in items:
        found.setdefault(item.criterion_id, item)
    return found


# One criterion


def _criterion(
    c: RubricCriterion, checks: dict[CheckId, CheckResult], ctx: _Context
) -> CriterionResult:
    base = {
        "criterion_id": c.criterion_id,
        "dimension": c.dimension,
        "title": c.title,
        "method": c.method,
        "points": c.points,
    }
    if c.check_id is CheckId.COVERAGE_CLAIMS_BACKED:
        status, critical, detail, evidence = _claims(c, ctx)
        return _finish(base, status, critical, detail, evidence, [])
    check = checks[c.check_id] if c.check_id is not None else None
    if c.method is Evaluation.DETERMINISTIC:
        assert check is not None
        return _finish(
            base,
            _FROM_CHECK[check.status],
            check.critical,
            check.detail,
            _cited_lines(check, ctx),
            check.event_seqs,
        )
    j_status, j_detail, j_evidence = _judged(c, ctx)
    if check is None:  # judged
        return _finish(base, j_status, False, j_detail, j_evidence, [])
    return _hybrid(base, check, j_status, j_detail, j_evidence, ctx)


def _hybrid(
    base: dict[str, object],
    check: CheckResult,
    j_status: GradeStatus,
    j_detail: str,
    j_evidence: list[EvidenceView],
    ctx: _Context,
) -> CriterionResult:
    c_status = _FROM_CHECK[check.status]
    evidence = _cited_lines(check, ctx) + j_evidence
    seqs = check.event_seqs
    if c_status is GradeStatus.NOT_APPLICABLE:
        return _finish(base, c_status, False, check.detail, evidence, seqs)
    if c_status is GradeStatus.FAIL:
        return _finish(base, c_status, check.critical, check.detail, evidence, seqs)
    detail = f"{check.detail} {j_detail}".strip()
    if c_status is GradeStatus.UNGRADED:  # code couldn't tell; the judge decides alone
        return _finish(base, j_status, False, detail, evidence, seqs)
    if j_status is GradeStatus.NOT_APPLICABLE:
        return _finish(base, c_status, False, check.detail, evidence, seqs)
    status = min(c_status, j_status, key=_SEVERITY.index)
    return _finish(base, status, False, detail, evidence, seqs)


def _finish(
    base: dict[str, object],
    status: GradeStatus,
    critical: bool,
    detail: str,
    evidence: list[EvidenceView],
    seqs: list[int],
) -> CriterionResult:
    points = base["points"]
    assert isinstance(points, int)
    earned = (points if status is GradeStatus.PASS else 0) if status in _SCORED else None
    return CriterionResult.model_validate(
        base
        | {
            "status": status,
            "earned": earned,
            "critical": critical and status is GradeStatus.FAIL,
            "detail": detail,
            "evidence": evidence,
            "event_seqs": seqs,
        }
    )


def _cited_lines(check: CheckResult, ctx: _Context) -> list[EvidenceView]:
    views = []
    for utt_id in check.utt_ids:
        u = ctx.utterances.get(utt_id)
        if u is not None:
            views.append(
                EvidenceView(utt_id=utt_id, speaker=u.speaker, quote=u.heard_text, verified=True)
            )
    return views


# The judge's half


def _judged(c: RubricCriterion, ctx: _Context) -> tuple[GradeStatus, str, list[EvidenceView]]:
    if ctx.judged is None:
        return GradeStatus.UNGRADED, ctx.judge_note, []
    j = ctx.judged.get(c.criterion_id)
    if j is None:
        return GradeStatus.UNGRADED, "The judge returned no grade for this item.", []
    evidence = [verify_quote(q, ctx.utterances, ctx.notes) for q in j.evidence]
    problems = [f"{e.utt_id}: {e.problem}" for e in evidence if not e.verified]
    rationale = j.rationale.strip()
    if problems:
        why = "; ".join(problems)
        return GradeStatus.REVIEW_REQUIRED, f"{rationale} Needs review: {why}.".strip(), evidence
    if j.status is JudgedStatus.PASS and not evidence:
        return (
            GradeStatus.REVIEW_REQUIRED,
            f"{rationale} Needs review: the judge passed this without quoting a line.".strip(),
            evidence,
        )
    return _FROM_JUDGE[j.status], rationale, evidence


def verify_quote(q: Quote, utterances: dict[str, Utterance], notes: str) -> EvidenceView:
    """Whether a judge's quote really is in the line it cites. ``notes`` is the saved notes
    as the judge saw them."""
    if q.utt_id == NOTES:
        ok = quote_in_text(q.quote, notes)
        return EvidenceView(
            utt_id=NOTES,
            quote=q.quote,
            verified=ok,
            problem=None if ok else "not in the saved notes",
        )
    u = utterances.get(q.utt_id)
    if u is None:
        return EvidenceView(utt_id=q.utt_id, quote=q.quote, verified=False, problem="no such line")
    problem = None
    if not u.delivered:
        problem = "the line was never heard"
    elif not u.contains_quote(q.quote):
        problem = "the quote isn't in that line"
    return EvidenceView(
        utt_id=q.utt_id, speaker=u.speaker, quote=q.quote, verified=problem is None, problem=problem
    )


# Coverage claims


def _claims(c: RubricCriterion, ctx: _Context) -> tuple[GradeStatus, bool, str, list[EvidenceView]]:
    if ctx.claims is None:
        note = ctx.judge_note if ctx.judged is None else "Needs the judge's coverage claims."
        return GradeStatus.UNGRADED, False, note, []
    if not ctx.claims:
        return (
            GradeStatus.NOT_APPLICABLE,
            False,
            "No statements that a doctor, drug or pharmacy is covered on a plan.",
            [],
        )
    evidence: list[EvidenceView] = []
    problems: list[str] = []
    unbacked: list[str] = []
    for claim in ctx.claims:
        view = verify_quote(Quote(utt_id=claim.utt_id, quote=claim.quote), ctx.utterances, "")
        problem = view.problem or _claim_problem(claim, ctx)
        if problem is None and view.speaker is not Speaker.LEARNER:
            problem = "the line isn't the learner's"
        view = view.model_copy(update={"verified": problem is None, "problem": problem})
        evidence.append(view)
        if problem is not None:
            problems.append(f"{claim.utt_id}: {problem}")
            continue
        backed = claim_backed(
            ctx.facts,
            CoverageClaim(
                utt_id=claim.utt_id,
                plan_id=claim.plan_id,
                kind=claim.kind,
                subject_id=claim.subject_id,
                covered=claim.covered,
            ),
        )
        if not backed:
            unbacked.append(_describe_claim(claim, ctx.catalog))
    if unbacked:
        detail = (
            "Said before any lookup showed it: " + "; ".join(unbacked) + "."
            " Look it up first, then tell the customer."
        )
        return GradeStatus.FAIL, c.critical, detail, evidence
    if problems:
        return (
            GradeStatus.REVIEW_REQUIRED,
            False,
            "Needs review: " + "; ".join(problems) + ".",
            evidence,
        )
    detail = "Every coverage claim followed a lookup that showed it."
    return GradeStatus.PASS, False, detail, evidence


def _claim_problem(claim: ClaimOut, ctx: _Context) -> str | None:
    catalog = ctx.catalog
    try:
        catalog.plan(claim.plan_id)
    except KeyError:
        return f"unknown plan {claim.plan_id}"
    lookup = {
        "provider_network": catalog.practice,
        "formulary": catalog.drug,
        "pharmacy_network": catalog.pharmacy,
    }[claim.kind]
    try:
        lookup(claim.subject_id)
    except KeyError:
        return f"unknown {claim.kind.replace('_', ' ')} subject {claim.subject_id}"
    return None


def _describe_claim(claim: ClaimOut, catalog: Catalog) -> str:
    plan = catalog.plan(claim.plan_id).name
    if claim.kind == "provider_network":
        practice = catalog.practice(claim.subject_id)
        provider = catalog.provider(practice.provider_id)
        location = catalog.location(practice.location_id)
        subject = f"{provider.name} at {location.name}"
        what = "in network" if claim.covered else "out of network"
    elif claim.kind == "formulary":
        drug = catalog.drug(claim.subject_id)
        subject = f"{drug.name} {drug.strength}"
        what = "covered" if claim.covered else "not covered"
    else:
        subject = catalog.pharmacy(claim.subject_id).name
        what = "in network" if claim.covered else "out of network"
    return f"{subject} is {what} on {plan}"


# Rules and score


def _with_rules(
    result: CriterionResult, c: RubricCriterion, ruleset: RuleSet, on: date
) -> CriterionResult:
    ids = list(c.rule_ids)
    if c.check_id is not None:
        ids += [r.rule_id for r in ruleset.rules if c.check_id in r.check_ids and r.current_on(on)]
    cites = []
    for rule_id in dict.fromkeys(ids):
        try:
            rule = ruleset.get(rule_id)
        except KeyError:
            continue
        cites.append(
            RuleCite(
                rule_id=rule.rule_id,
                title=rule.title,
                authority=rule.authority,
                citation=rule.citation,
                status=rule.status,
                verified_as_of=rule.verified_as_of,
            )
        )
    return result.model_copy(update={"rules": cites})


def _score(results: list[CriterionResult], rubric: Rubric) -> Graded:
    dimensions = []
    for dimension, weight in DIMENSION_WEIGHTS.items():
        mine = [r for r in results if r.dimension is dimension and r.earned is not None]
        dimensions.append(
            DimensionScore(
                dimension=dimension,
                weight=weight,
                earned=sum(r.earned or 0 for r in mine),
                possible=sum(r.points for r in mine),
            )
        )
    earned = sum(d.earned for d in dimensions)
    possible = sum(d.possible for d in dimensions)
    order = {c.criterion_id: i for i, c in enumerate(rubric.criteria)}
    missed = [r for r in results if r.status in (GradeStatus.FAIL, GradeStatus.NOT_OBSERVED)]
    missed.sort(key=lambda r: (not r.critical, -r.points, order[r.criterion_id]))
    return Graded(
        criteria=results,
        dimensions=dimensions,
        earned=earned,
        possible=possible,
        score=round(100 * earned / possible) if possible else None,
        complete=not any(r.status in _INCOMPLETE for r in results),
        critical_findings=[r.criterion_id for r in results if r.critical],
        focus=[r.criterion_id for r in missed[:FOCUS_SIZE]],
    )
