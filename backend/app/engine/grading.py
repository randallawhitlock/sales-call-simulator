"""Turns the deterministic checks and the judge's output into graded criteria and a score.

Pure: the same log and the same stored judge output always give the same debrief.

- Every judge quote is looked up in the line it cites. A quote that isn't there, or a pass
  with no quote, sends the criterion to ``review_required`` instead of counting.
- A hybrid criterion needs both halves: a code fail or a judge fail fails it, and an
  ungraded half leaves it ungraded. Only code can make a finding critical; a model's judgment
  alone never does.
- A pass also needs a quote of at least three words from the learner's own lines or the
  notes. A customer line or a stray word can support a grade but can't carry one.
- ``coverage_claims_backed`` is decided by code from the claims the judge found: each claim
  needs an earlier lookup that showed what the learner said. An unbacked claim only fails,
  critically, when code can confirm the claim from the cited line itself: the quote says
  covered or not, the line names the plan and the doctor, drug or pharmacy, and no lookup
  backs any reading of the line. Anything the judge had to infer goes to review instead.
- Not-applicable, ungraded and review-required criteria leave the denominator. Any ungraded
  or review-required criterion makes the call incomplete, never failed.
"""

import re
from datetime import date
from enum import StrEnum

from pydantic import BaseModel

from app.content.catalog import Catalog, Drug, Practice
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
from app.engine.events import (
    Event,
    Speaker,
    Utterance,
    normalize_for_quote,
    quote_in_text,
)
from app.judge.models import NOTES, ClaimOut, JudgedCriterion, JudgedStatus, JudgeOutput, Quote
from app.judge.prompt import notes_text

FOCUS_SIZE = 3
"""How many missed criteria a targeted retry asks the learner to work on."""
MIN_PASS_QUOTE_WORDS = 3
"""A pass needs one verified quote this long from the learner's lines or the notes."""


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
    if j.status is JudgedStatus.PASS and not any(_carries_a_pass(e) for e in evidence):
        why = (
            f"a pass needs a quote of at least {MIN_PASS_QUOTE_WORDS} words from your own "
            "lines or the notes"
            if evidence
            else "the judge passed this without quoting a line"
        )
        return GradeStatus.REVIEW_REQUIRED, f"{rationale} Needs review: {why}.".strip(), evidence
    return _FROM_JUDGE[j.status], rationale, evidence


def _carries_a_pass(e: EvidenceView) -> bool:
    """A quote that can carry a pass: verified, in the learner's own words (a line of theirs
    or the notes), and long enough to say something."""
    own = e.utt_id == NOTES or e.speaker is Speaker.LEARNER
    return e.verified and own and len(normalize_for_quote(e.quote).split()) >= MIN_PASS_QUOTE_WORDS


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
    seen: set[tuple[object, ...]] = set()
    for claim in ctx.claims:
        key = (claim.utt_id, claim.plan_id, claim.kind, claim.subject_id, claim.covered)
        if key in seen:
            continue
        seen.add(key)
        view = verify_quote(Quote(utt_id=claim.utt_id, quote=claim.quote), ctx.utterances, "")
        problem = view.problem or _claim_problem(claim, ctx)
        if problem is None and view.speaker is not Speaker.LEARNER:
            problem = "the line isn't the learner's"
        if problem is None and not _backed(ctx, claim, claim.plan_id, claim.subject_id):
            line = ctx.utterances[claim.utt_id].heard_text
            problem = _unconfirmed(claim, line, ctx)
            if problem is None:
                unbacked.append(_describe_claim(claim, ctx.catalog))
        evidence.append(view.model_copy(update={"verified": problem is None, "problem": problem}))
        if problem is not None:
            problems.append(f"{claim.utt_id}: {problem}")
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


def _backed(ctx: _Context, claim: ClaimOut, plan_id: str, subject_id: str) -> bool:
    return claim_backed(
        ctx.facts,
        CoverageClaim(
            utt_id=claim.utt_id,
            plan_id=plan_id,
            kind=claim.kind,
            subject_id=subject_id,
            covered=claim.covered,
        ),
    )


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


# An unbacked claim is a critical finding, so code has to be able to confirm it from the line.
# The judge's plan, subject and polarity tags are its reading; these checks read the line.

_COVERED = (
    r"in[- ]network|covered|covers|accepts?|takes|"
    r"on (?:its|the|their|that) (?:drug list|formulary)"
)
_NOT_COVERED = re.compile(
    rf"(?<!\w)(?:not|never|no longer|\w+n't)\s+(?:\w+\s+){{0,3}}?(?:{_COVERED})"
    r"|out[- ]of[- ]network|non-formulary|excluded"
)
_IS_COVERED = re.compile(rf"(?<!\w)(?:{_COVERED})(?!\w)")
_GENERIC_PLACE_WORDS = {"office", "the", "and", "of"}
_PHARMACY_WORDS = {"pharmacy", "rx", "drug", "drugs"}


def said_covered(quote: str) -> bool | None:
    """What the quoted words say: covered or in network (True), not (False), or neither."""
    text = normalize_for_quote(quote)
    if _NOT_COVERED.search(text):
        return False
    if _IS_COVERED.search(text):
        return True
    return None


def plans_named(line: str, catalog: Catalog) -> list[str]:
    """Plans the line names, with or without the HMO or PPO on the end."""
    named = []
    for plan in catalog.plans:
        short = plan.name.rsplit(" ", 1)[0]
        if quote_in_text(plan.name, line) or quote_in_text(short, line):
            named.append(plan.plan_id)
    return named


def subjects_named(kind: str, line: str, catalog: Catalog) -> list[str]:
    """Every doctor's office, drug or pharmacy the line could mean. "Dr. Raman" means every
    Raman at every office until the line says which; "atorvastatin" means every strength
    until it gives one."""
    if kind == "provider_network":
        return _practices_named(line, catalog)
    if kind == "formulary":
        return _drugs_named(line, catalog)
    named = []
    for pharmacy in catalog.pharmacies:
        words = pharmacy.name.split()
        stop = next((i for i, w in enumerate(words) if w.casefold() in _PHARMACY_WORDS), None)
        stem = " ".join(words[:stop]) or pharmacy.name
        if quote_in_text(stem, line):
            named.append(pharmacy.pharmacy_id)
    return named


def _practices_named(line: str, catalog: Catalog) -> list[str]:
    by_surname: dict[str, list[Practice]] = {}
    for practice in catalog.practices:
        surname = catalog.provider(practice.provider_id).name.split()[-1]
        if quote_in_text(surname, line):
            by_surname.setdefault(surname, []).append(practice)
    named: list[str] = []
    for group in by_surname.values():
        first_names = [
            p for p in group if quote_in_text(catalog.provider(p.provider_id).name.split()[0], line)
        ]
        group = first_names or group
        # Each word of an office name the line uses narrows the reading to the offices with it.
        place_words: dict[str, set[str]] = {}
        for practice in group:
            name = catalog.location(practice.location_id).name
            for word in normalize_for_quote(name).replace(",", " ").split():
                if word not in _GENERIC_PLACE_WORDS:
                    place_words.setdefault(word, set()).add(practice.practice_id)
        keep = {p.practice_id for p in group}
        for word, ids in place_words.items():
            if quote_in_text(word, line) and keep & ids:
                keep &= ids
        named += [p.practice_id for p in group if p.practice_id in keep]
    return named


def _drugs_named(line: str, catalog: Catalog) -> list[str]:
    text = normalize_for_quote(line)

    def strength_given(strength: str) -> bool:
        number = re.escape(strength.split()[0])
        unit = r"\s*(?:mg|mcg|milligrams?|micrograms?)(?!\w)"
        return re.search(rf"(?<![\w.]){number}{unit}", text) is not None

    by_name: dict[str, list[Drug]] = {}
    for drug in catalog.drugs:
        if quote_in_text(drug.name.split()[0], line):
            by_name.setdefault(drug.name, []).append(drug)
    named: list[str] = []
    for group in by_name.values():
        with_strength = [d for d in group if strength_given(d.strength)]
        named += [d.drug_id for d in (with_strength or group)]
    return named


def _unconfirmed(claim: ClaimOut, line: str, ctx: _Context) -> str | None:
    """Why code can't confirm an unbacked claim from the cited line, or None if it can: the
    claim is then a real miss, whichever way the judge tagged it."""
    if said_covered(claim.quote) is not claim.covered:
        what = "is covered" if claim.covered else "isn't covered"
        return f"the quote doesn't say it {what}"
    plans = plans_named(line, ctx.catalog)
    if claim.plan_id not in plans:
        return f"the line doesn't name {ctx.catalog.plan(claim.plan_id).name}"
    subjects = subjects_named(claim.kind, line, ctx.catalog)
    if claim.subject_id not in subjects:
        return (
            "the line doesn't name the "
            + {
                "provider_network": "doctor and office",
                "formulary": "drug",
                "pharmacy_network": "pharmacy",
            }[claim.kind]
        )
    for plan_id in plans:
        for subject_id in subjects:
            if _backed(ctx, claim, plan_id, subject_id):
                other = claim.model_copy(update={"plan_id": plan_id, "subject_id": subject_id})
                meant = _describe_claim(other, ctx.catalog)
                return f"the line could mean {meant}, which was looked up"
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
