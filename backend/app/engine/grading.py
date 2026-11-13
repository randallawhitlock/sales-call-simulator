"""Grading: one result per rubric criterion, from the deterministic checks and the judge.

Deterministic checks come first. The judge's rulings count only after the server has found
every quote it cited in the utterance it named; a ruling that fails that test is held for
review instead of being scored. Hybrid criteria combine the two the way the rubric says
(``Combine``). Nothing here calls a model: the same log and the same stored judge output give
the same scorecard.

Scoring: a pass earns the criterion's points. Pass, fail and not-observed count toward the
total; not-applicable, ungraded and review-required items leave it, so a penalty only ever
rests on an event or a verified quote. Any ungraded item makes the call "incomplete", never
failed.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from pydantic import BaseModel

from app.content.common import Authority, CheckId, Evaluation, EvidencePolicy, OutcomeKind
from app.content.rubric import DIMENSION_WEIGHTS, Combine, Dimension, Rubric, RubricCriterion
from app.content.rules import RuleSet, RuleStatus
from app.content.scenario import Scenario
from app.engine.checks import (
    CheckResult,
    CheckStatus,
    ClaimKind,
    CoverageClaim,
    Facts,
    check_facts,
    claim_backing,
)
from app.engine.events import Speaker, Utterance, quote_in
from app.engine.records import CustomerRecord
from app.engine.timeline import drug_label, practice_label
from app.judge.contract import NOTES_REF, JudgeClaim, JudgeOutput, JudgeVerdict, Verdict


class GradeStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"
    NOT_OBSERVED = "not_observed"
    REVIEW_REQUIRED = "review_required"
    UNGRADED = "ungraded"


SCORED = {GradeStatus.PASS, GradeStatus.FAIL, GradeStatus.NOT_OBSERVED}
"""Statuses that count toward the total. The rest leave the denominator."""


class GradingState(StrEnum):
    COMPLETE = "complete"
    NEEDS_REVIEW = "needs_review"
    """Every item was ruled on, but at least one ruling is held for a person to look at."""
    INCOMPLETE = "incomplete"
    """At least one item has no ruling, usually because the judge has not run."""


class Quote(BaseModel):
    """A quote the judge cited, and whether the server found it where the judge said."""

    utt_id: str | None
    """None when the quote is from the saved notes."""
    quote: str
    verified: bool


class RuleLabel(BaseModel):
    """Where a criterion's requirement comes from, as it applies to this call's outcome."""

    rule_id: str
    title: str
    authority: Authority
    citation: str
    status: RuleStatus
    verified_as_of: date | None


class ClaimStatus(StrEnum):
    BACKED = "backed"
    UNBACKED = "unbacked"
    UNVERIFIABLE = "unverifiable"


class ClaimResult(BaseModel):
    """One coverage claim the judge found, after the server checked it against the log."""

    utt_id: str
    quote: str
    kind: ClaimKind
    plan_id: str
    subject_id: str
    covered: bool
    label: str
    status: ClaimStatus
    detail: str
    event_seqs: list[int] = []
    """The lookups that showed what was claimed."""


class CriterionResult(BaseModel):
    criterion_id: str
    dimension: Dimension
    title: str
    points: int
    earned: int
    method: Evaluation
    status: GradeStatus
    critical: bool = False
    """A failed critical item: enrolling without consent, enrolling when the case excludes it,
    claiming unverified coverage, or submitting before the checklist review."""
    detail: str = ""
    """What the rule check found, or why the item is not applicable, ungraded or held."""
    rationale: str | None = None
    """The judge's reasoning, in its own words."""
    check_id: CheckId | None = None
    check_status: CheckStatus | None = None
    utt_ids: list[str] = []
    event_seqs: list[int] = []
    quotes: list[Quote] = []
    claims: list[ClaimResult] = []
    rules: list[RuleLabel] = []


class Score(BaseModel):
    earned: int
    possible: int
    """Points of the items that were scored. Not-applicable, ungraded and held items are out."""
    percent: int | None
    """None when nothing was scored."""


class DimensionScore(Score):
    dimension: Dimension
    weight: int


class Scorecard(BaseModel):
    state: GradingState
    score: Score
    dimensions: list[DimensionScore]
    criteria: list[CriterionResult]
    critical: list[str]
    """Criterion ids of the critical findings."""


# What the judge is asked


@dataclass(frozen=True)
class JudgeTasks:
    rulings: tuple[RubricCriterion, ...]
    """Criteria the judge returns a verdict on."""
    claims: RubricCriterion | None
    """The criterion that has the judge list coverage claims, when it applies."""


def judge_tasks(
    rubric: Rubric, scenario: Scenario, checks: dict[CheckId, CheckResult]
) -> JudgeTasks:
    """What the judge can still change, given what the checks found. A hybrid item the check
    already settled is not sent: asking would cost tokens and change nothing."""
    rulings: list[RubricCriterion] = []
    claims: RubricCriterion | None = None
    for c in rubric.criteria:
        if c.judged_spec is None or c.criterion_id in scenario.not_applicable:
            continue
        if c.combine is Combine.CLAIMS:
            claims = c
        elif _ruling_matters(c, checks.get(c.check_id) if c.check_id else None):
            rulings.append(c)
    return JudgeTasks(rulings=tuple(rulings), claims=claims)


def _ruling_matters(c: RubricCriterion, det: CheckResult | None) -> bool:
    if c.method is Evaluation.JUDGED:
        return True
    assert det is not None, c.criterion_id
    if c.combine is Combine.BOTH:
        return det.status is CheckStatus.PASS
    return det.status is CheckStatus.UNGRADED or _disputable_failure(det)


def _disputable_failure(det: CheckResult) -> bool:
    return det.disputable and det.status in (CheckStatus.FAIL, CheckStatus.NOT_OBSERVED)


# The judge's rulings, verified


def notes_text(record: CustomerRecord) -> str:
    """The learner's saved notes and decision rationale, as the judge sees and quotes them."""
    parts: list[str] = []
    if record.notes is not None:
        parts += [
            f"{name.replace('_', ' ').capitalize()}: {value}"
            for name, value in record.notes.model_dump().items()
            if value
        ]
    if record.disposition is not None and record.disposition.rationale:
        parts.append(f"Decision rationale: {record.disposition.rationale}")
    return "\n".join(parts)


@dataclass(frozen=True)
class _Ruling:
    status: GradeStatus
    rationale: str | None = None
    quotes: tuple[Quote, ...] = ()
    held: str = ""
    """Why the ruling is ungraded or held for review."""


@dataclass(frozen=True)
class _Context:
    facts: Facts
    ruleset: RuleSet
    output: JudgeOutput | None
    pending: str
    """What to say on an item the judge has not ruled on."""

    def utterance(self, utt_id: str) -> Utterance | None:
        return self.facts.utterance(utt_id)

    @property
    def notes(self) -> str:
        return notes_text(self.facts.state.record)

    def notes_seqs(self) -> list[int]:
        saved = self.facts.state.record.saved_seq
        return [saved[s] for s in ("notes", "disposition") if s in saved]


def _verify(ctx: _Context, c: RubricCriterion, verdict: JudgeVerdict) -> _Ruling:
    quotes: list[Quote] = []
    problems: list[str] = []
    for ev in verdict.evidence:
        if ev.utt_id == NOTES_REF:
            ok = c.evidence_policy is not EvidencePolicy.TRANSCRIPT and quote_in(
                ev.quote, ctx.notes
            )
            quotes.append(Quote(utt_id=None, quote=ev.quote, verified=ok))
            if not ok:
                problems.append("a quote it cited is not in the saved notes")
            continue
        utt = ctx.utterance(ev.utt_id)
        ok = utt is not None and utt.delivered and utt.contains_quote(ev.quote)
        quotes.append(Quote(utt_id=ev.utt_id, quote=ev.quote, verified=ok))
        if utt is None:
            problems.append(f"it cited {ev.utt_id}, which is not a line of this call")
        elif not ok:
            problems.append(f"a quote it cited is not in {ev.utt_id}")
    status = GradeStatus(verdict.verdict.value)
    if verdict.verdict in (Verdict.PASS, Verdict.FAIL) and not quotes:
        problems.append("it cited no line")
    if problems:
        return _Ruling(
            GradeStatus.REVIEW_REQUIRED,
            verdict.rationale,
            tuple(quotes),
            f"The judge ruled {verdict.verdict.value.replace('_', ' ')}, but "
            f"{'; '.join(dict.fromkeys(problems))}. Held for review.",
        )
    return _Ruling(status, verdict.rationale, tuple(quotes))


def _ruling(ctx: _Context, c: RubricCriterion) -> _Ruling:
    if ctx.output is None:
        return _Ruling(GradeStatus.UNGRADED, held=ctx.pending)
    verdict = next((v for v in ctx.output.verdicts if v.criterion_id == c.criterion_id), None)
    if verdict is None:
        return _Ruling(GradeStatus.UNGRADED, held="The judge returned no ruling on this item.")
    return _verify(ctx, c, verdict)


# Combining


def _rule_labels(ctx: _Context, c: RubricCriterion) -> list[RuleLabel]:
    labels = []
    for rule_id in c.rule_ids:
        rule = ctx.ruleset.get(rule_id)
        scoped = rule.applies_when.outcomes
        in_scope = not scoped or ctx.facts.outcome in scoped
        authority = (
            rule.authority if in_scope or rule.authority_outside_scope is None
            else rule.authority_outside_scope
        )  # fmt: skip
        labels.append(
            RuleLabel(
                rule_id=rule.rule_id,
                title=rule.title,
                authority=authority,
                citation=rule.citation,
                status=rule.status,
                verified_as_of=rule.verified_as_of,
            )
        )
    return labels


def _result(
    ctx: _Context,
    c: RubricCriterion,
    status: GradeStatus,
    *,
    detail: str = "",
    critical: bool = False,
    det: CheckResult | None = None,
    ruling: _Ruling | None = None,
    claims: Iterable[ClaimResult] = (),
    utt_ids: Iterable[str] = (),
    event_seqs: Iterable[int] = (),
) -> CriterionResult:
    quotes = list(ruling.quotes) if ruling else []
    utts = {*utt_ids, *(q.utt_id for q in quotes if q.utt_id is not None and q.verified)}
    seqs = set(event_seqs)
    if det is not None:
        utts |= set(det.utt_ids)
        seqs |= set(det.event_seqs)
    if any(q.utt_id is None and q.verified for q in quotes):
        seqs |= set(ctx.notes_seqs())
    return CriterionResult(
        criterion_id=c.criterion_id,
        dimension=c.dimension,
        title=c.title,
        points=c.points,
        earned=c.points if status is GradeStatus.PASS else 0,
        method=c.method,
        status=status,
        critical=critical and status is GradeStatus.FAIL,
        detail=detail,
        rationale=ruling.rationale if ruling else None,
        check_id=c.check_id,
        check_status=det.status if det else None,
        utt_ids=sorted(utts),
        event_seqs=sorted(seqs),
        quotes=quotes,
        claims=list(claims),
        rules=_rule_labels(ctx, c),
    )


def _join(*parts: str) -> str:
    return " ".join(p for p in parts if p)


_FROM_CHECK = {
    CheckStatus.PASS: GradeStatus.PASS,
    CheckStatus.FAIL: GradeStatus.FAIL,
    CheckStatus.NOT_APPLICABLE: GradeStatus.NOT_APPLICABLE,
    CheckStatus.NOT_OBSERVED: GradeStatus.NOT_OBSERVED,
    CheckStatus.UNGRADED: GradeStatus.UNGRADED,
}


def _deterministic(ctx: _Context, c: RubricCriterion, det: CheckResult) -> CriterionResult:
    return _result(
        ctx, c, _FROM_CHECK[det.status], detail=det.detail, critical=det.critical, det=det
    )


def _judged(ctx: _Context, c: RubricCriterion) -> CriterionResult:
    ruling = _ruling(ctx, c)
    return _result(ctx, c, ruling.status, detail=ruling.held, critical=c.critical, ruling=ruling)


def _after_check_passed(
    ctx: _Context, c: RubricCriterion, det: CheckResult, ruling: _Ruling
) -> CriterionResult:
    """The check is satisfied or couldn't tell, so the judge's ruling decides. A judged
    failure is critical only if the criterion is and a verified quote shows it; something the
    judge merely did not see earns nothing but is never critical."""
    status, held = ruling.status, ruling.held
    if status is GradeStatus.NOT_APPLICABLE:
        status = GradeStatus.REVIEW_REQUIRED
        held = "The judge called this not applicable, but the check applied. Held for review."
    return _result(
        ctx, c, status, detail=_join(det.detail, held), critical=c.critical, det=det, ruling=ruling
    )


def _both(ctx: _Context, c: RubricCriterion, det: CheckResult) -> CriterionResult:
    if det.status is not CheckStatus.PASS:
        return _deterministic(ctx, c, det)
    return _after_check_passed(ctx, c, det, _ruling(ctx, c))


def _backstop(ctx: _Context, c: RubricCriterion, det: CheckResult) -> CriterionResult:
    if det.status is CheckStatus.UNGRADED:
        return _after_check_passed(ctx, c, det, _ruling(ctx, c))
    if not _disputable_failure(det):
        return _deterministic(ctx, c, det)
    ruling = _ruling(ctx, c)
    if ruling.status is GradeStatus.PASS:
        return _result(
            ctx,
            c,
            GradeStatus.REVIEW_REQUIRED,
            detail=_join(
                det.detail,
                "The judge read the same lines as meeting the requirement, so this is held for "
                "review instead of counting against you.",
            ),
            det=det,
            ruling=ruling,
        )
    # The check's failure stands. The judge's view is shown only if it was verified.
    shown = ruling if ruling.status in (GradeStatus.FAIL, GradeStatus.NOT_OBSERVED) else None
    return _result(
        ctx,
        c,
        _FROM_CHECK[det.status],
        detail=det.detail,
        critical=det.critical,
        det=det,
        ruling=shown,
    )


# Coverage claims


def _subject_label(ctx: _Context, kind: ClaimKind, subject_id: str) -> str:
    catalog = ctx.facts.catalog
    if kind == "provider_network":
        catalog.practice(subject_id)
        return practice_label(catalog, subject_id)
    if kind == "formulary":
        catalog.drug(subject_id)
        return drug_label(catalog, subject_id)
    return catalog.pharmacy(subject_id).name


def _claim(ctx: _Context, raw: JudgeClaim) -> ClaimResult:
    def result(status: ClaimStatus, label: str, detail: str, seqs: list[int]) -> ClaimResult:
        return ClaimResult(
            **raw.model_dump(), label=label, status=status, detail=detail, event_seqs=seqs
        )

    utt = ctx.utterance(raw.utt_id)
    if utt is None or utt.speaker is not Speaker.LEARNER or not utt.delivered:
        why = f"{raw.utt_id} is not a line the agent was heard to say."
        return result(ClaimStatus.UNVERIFIABLE, raw.quote, why, [])
    if not utt.contains_quote(raw.quote):
        why = f"The quote is not in {raw.utt_id}."
        return result(ClaimStatus.UNVERIFIABLE, raw.quote, why, [])
    try:
        plan = ctx.facts.catalog.plan(raw.plan_id).name
        subject = _subject_label(ctx, raw.kind, raw.subject_id)
    except KeyError:
        why = "The judge named a plan, doctor, drug or pharmacy that is not in the catalog."
        return result(ClaimStatus.UNVERIFIABLE, raw.quote, why, [])
    word = "in network" if raw.kind != "formulary" else "covered"
    label = f"{subject} is {'' if raw.covered else 'not '}{word} on {plan}"
    seqs = claim_backing(
        ctx.facts,
        CoverageClaim(
            utt_id=raw.utt_id,
            plan_id=raw.plan_id,
            kind=raw.kind,
            subject_id=raw.subject_id,
            covered=raw.covered,
        ),
    )
    if seqs:
        return result(ClaimStatus.BACKED, label, "A lookup made before the claim showed it.", seqs)
    year = f" for {ctx.facts.plan_year}" if raw.kind == "provider_network" else ""
    why = f"No lookup{year} made before {raw.utt_id} showed this."
    return result(ClaimStatus.UNBACKED, label, why, [])


def _claims(ctx: _Context, c: RubricCriterion, det: CheckResult) -> CriterionResult:
    if ctx.output is None:
        return _result(ctx, c, GradeStatus.UNGRADED, detail=ctx.pending, det=det)
    # The judge may list one claim twice. Keep one entry per claim, and prefer a listing the
    # server could check over one it couldn't.
    by_claim: dict[tuple[object, ...], ClaimResult] = {}
    for raw in ctx.output.coverage_claims:
        key = (raw.utt_id, raw.kind, raw.plan_id, raw.subject_id, raw.covered)
        kept = by_claim.get(key)
        if kept is None or kept.status is ClaimStatus.UNVERIFIABLE:
            checked = _claim(ctx, raw)
            if kept is None or checked.status is not ClaimStatus.UNVERIFIABLE:
                by_claim[key] = checked
    claims = list(by_claim.values())

    def of(status: ClaimStatus) -> list[ClaimResult]:
        return [x for x in claims if x.status is status]

    unbacked, unverifiable = of(ClaimStatus.UNBACKED), of(ClaimStatus.UNVERIFIABLE)
    cited = [x.utt_id for x in claims if x.status is not ClaimStatus.UNVERIFIABLE]
    seqs = [s for x in claims for s in x.event_seqs]
    if unbacked:
        status = GradeStatus.FAIL
        detail = (
            f"{len(unbacked)} of {len(claims)} coverage claims had no lookup behind them: "
            + "; ".join(f"{x.label} ({x.utt_id})" for x in unbacked)
            + "."
        )
    elif unverifiable:
        status = GradeStatus.REVIEW_REQUIRED
        detail = (
            f"{len(unverifiable)} of {len(claims)} claims the judge listed could not be checked. "
            "Held for review."
        )
    elif not claims:
        status = GradeStatus.NOT_APPLICABLE
        detail = "The judge found no coverage claims in the call."
    else:
        status = GradeStatus.PASS
        detail = f"All {len(claims)} coverage claims followed a lookup that showed them."
    return _result(
        ctx,
        c,
        status,
        detail=detail,
        critical=c.critical,
        claims=claims,
        utt_ids=cited,
        event_seqs=seqs,
    )


# Scorecard


def _criterion(
    ctx: _Context, c: RubricCriterion, checks: dict[CheckId, CheckResult]
) -> CriterionResult:
    reason = ctx.facts.scenario.not_applicable.get(c.criterion_id)
    if reason is not None:
        return _result(ctx, c, GradeStatus.NOT_APPLICABLE, detail=reason)
    if c.method is Evaluation.JUDGED:
        return _judged(ctx, c)
    assert c.check_id is not None
    det = checks[c.check_id]
    if c.method is Evaluation.DETERMINISTIC:
        return _deterministic(ctx, c, det)
    if c.combine is Combine.BOTH:
        return _both(ctx, c, det)
    if c.combine is Combine.CLAIMS:
        return _claims(ctx, c, det)
    return _backstop(ctx, c, det)


def _score(results: Iterable[CriterionResult]) -> Score:
    scored = [r for r in results if r.status in SCORED]
    earned, possible = sum(r.earned for r in scored), sum(r.points for r in scored)
    return Score(
        earned=earned,
        possible=possible,
        percent=round(100 * earned / possible) if possible else None,
    )


def grade(
    facts: Facts,
    rubric: Rubric,
    ruleset: RuleSet,
    output: JudgeOutput | None,
    pending: str = "The judge has not graded this call.",
) -> Scorecard:
    """The scorecard for a finished call. ``output`` is the stored judge output, or None when
    there is none; ``pending`` then explains each item that is waiting on it."""
    checks = {r.check_id: r for r in check_facts(facts)}
    ctx = _Context(facts=facts, ruleset=ruleset, output=output, pending=pending)
    results = [_criterion(ctx, c, checks) for c in rubric.criteria]
    statuses = {r.status for r in results}
    if GradeStatus.UNGRADED in statuses:
        state = GradingState.INCOMPLETE
    elif GradeStatus.REVIEW_REQUIRED in statuses:
        state = GradingState.NEEDS_REVIEW
    else:
        state = GradingState.COMPLETE
    return Scorecard(
        state=state,
        score=_score(results),
        dimensions=[
            DimensionScore(
                dimension=d,
                weight=DIMENSION_WEIGHTS[d],
                **_score(r for r in results if r.dimension is d).model_dump(),
            )
            for d in Dimension
        ],
        criteria=results,
        critical=[r.criterion_id for r in results if r.critical],
    )


def outcome_label(facts: Facts) -> tuple[OutcomeKind | None, str | None]:
    """The outcome the call reached and the plan it involves, by name."""
    plan = facts.catalog.plan(facts.outcome_plan_id).name if facts.outcome_plan_id else None
    return facts.outcome, plan
