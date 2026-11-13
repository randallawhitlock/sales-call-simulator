"""Deterministic checks over a finished call.

Each check reads the event log, the transcript and the saved records, compares them with
scenario truth and the catalog, and returns a result that cites the events or utterances it
relied on. Checks never read the AI judge's output; hybrid criteria combine the two later.

"Outcome plan" means the submitted plan when the learner enrolled, the current plan when the
customer keeps it, and nothing when the decision was deferred or stopped.
"""

import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, JsonValue

from app.content.catalog import Catalog, DocumentKind
from app.content.common import CheckId, OutcomeKind
from app.content.loader import aep_end
from app.content.scenario import CallFrame, OutcomeVerdict, Scenario
from app.engine.events import Event, EventType, Speaker, Utterance
from app.engine.records import Application, CustomerRecord, SessionState


class CheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"
    NOT_OBSERVED = "not_observed"
    UNGRADED = "ungraded"


class CheckResult(BaseModel):
    check_id: CheckId
    status: CheckStatus
    critical: bool = False
    detail: str
    event_seqs: list[int] = []
    utt_ids: list[str] = []
    disputable: bool = False
    """The result rests on how a line reads (a pattern match), not on events or state. Only
    then may a quote-verified judge dispute a failure, and the dispute goes to review; it never
    turns the failure into a pass on its own."""


@dataclass
class Facts:
    scenario: Scenario
    catalog: Catalog
    frame: CallFrame
    events: list[Event]
    utterances: list[Utterance]
    state: SessionState = field(init=False)
    outcome: OutcomeKind | None = field(init=False)
    outcome_seqs: list[int] = field(init=False)
    enrolled: Application | None = field(init=False)
    """The enrollment standing at the end of the call; decides the outcome."""
    submissions: list[Application] = field(init=False)
    """Every submission, withdrawn or not; compliance checks audit each."""
    outcome_plan_id: str | None = field(init=False)
    cutoff: int = field(init=False)

    def __post_init__(self) -> None:
        self.state = SessionState.fold(self.events)
        self.enrolled = self.state.submitted_application()
        self.submissions = self.state.submissions()
        disposition = self.state.record.disposition
        self.outcome, self.outcome_seqs = None, []
        if self.enrolled is not None:
            self.outcome = OutcomeKind.ENROLL
            self.outcome_seqs = [self.enrolled.submitted_seq or 0]
        elif disposition is not None and disposition.outcome is not OutcomeKind.ENROLL:
            self.outcome = disposition.outcome
            self.outcome_seqs = [self.state.record.saved_seq["disposition"]]
        if self.enrolled is not None:
            self.outcome_plan_id = self.enrolled.fields.plan_id
        elif self.outcome is OutcomeKind.KEEP_CURRENT:
            self.outcome_plan_id = self.scenario.truth.current_plan_id
        else:
            self.outcome_plan_id = None
        end = self.state.call_ended_seq or (self.events[-1].seq + 1 if self.events else 1)
        self.cutoff = (self.enrolled.submitted_seq or end) if self.enrolled else end

    @property
    def plan_year(self) -> int:
        return self.frame.plan_year

    def before_cutoff(self, type_: EventType) -> list[Event]:
        return [e for e in self.events if e.type is type_ and e.seq < self.cutoff]

    def record_at(self, seq: int) -> CustomerRecord:
        """The customer record as it stood just before event ``seq``. Edits made after a
        submission can't make that submission look better prepared than it was."""
        return SessionState.fold([e for e in self.events if e.seq < seq]).record

    @property
    def needs_cutoff(self) -> int:
        """Where the record is graded: the first submission, else the end of the call."""
        if self.submissions and self.submissions[0].submitted_seq is not None:
            return self.submissions[0].submitted_seq
        return self.cutoff

    def lookups(self, kind: str) -> list[Event]:
        return [e for e in self.before_cutoff(EventType.LOOKUP) if e.payload.get("kind") == kind]

    def drug_lookups(self, plan_id: str, drug_id: str) -> list[int]:
        """Formulary lookups and compare lines both show coverage and restrictions."""
        seqs = [
            e.seq
            for e in self.lookups("formulary")
            if e.payload.get("plan_id") == plan_id and e.payload.get("drug_id") == drug_id
        ]
        for e in self.lookups("compare"):
            if any(
                _get(line, "plan_id") == plan_id and _get(line, "drug_id") == drug_id
                for line in _list(e.payload.get("lines"))
            ):
                seqs.append(e.seq)
        return sorted(seqs)

    def pharmacy_lookups(self, plan_id: str, pharmacy_id: str) -> list[int]:
        seqs = [
            e.seq
            for e in self.lookups("pharmacy_network")
            if e.payload.get("plan_id") == plan_id and e.payload.get("pharmacy_id") == pharmacy_id
        ]
        for e in self.lookups("compare"):
            if any(
                _get(row, "plan_id") == plan_id and _get(row, "pharmacy_id") == pharmacy_id
                for row in _list(e.payload.get("pharmacy"))
            ):
                seqs.append(e.seq)
        return sorted(seqs)

    def learner_utterances(self) -> list[Utterance]:
        return [u for u in self.utterances if u.speaker is Speaker.LEARNER and u.delivered]

    def utterance(self, utt_id: str) -> Utterance | None:
        return next((u for u in self.utterances if u.utt_id == utt_id), None)

    def utt_seq(self, utt_id: str) -> int:
        return next((e.seq for e in self.events if e.utt_id == utt_id), 0)


def _get(obj: JsonValue, key: str) -> JsonValue:
    return obj.get(key) if isinstance(obj, dict) else None


def _list(obj: JsonValue) -> list[JsonValue]:
    return obj if isinstance(obj, list) else []


def _result(
    check: CheckId,
    status: CheckStatus,
    detail: str,
    seqs: Iterable[int] = (),
    utts: Iterable[str] = (),
    critical: bool = False,
    disputable: bool = False,
) -> CheckResult:
    return CheckResult(
        check_id=check,
        status=status,
        critical=critical,
        detail=detail,
        event_seqs=sorted(set(seqs)),
        utt_ids=sorted(set(utts)),
        disputable=disputable,
    )


def _no_outcome_plan(check: CheckId, f: Facts) -> CheckResult | None:
    if f.outcome is None:
        return _result(check, CheckStatus.NOT_OBSERVED, "No outcome was recorded.")
    if f.outcome_plan_id is None:
        return _result(
            check,
            CheckStatus.NOT_APPLICABLE,
            f"No plan decision to verify for outcome {f.outcome.value}.",
            f.outcome_seqs,
        )
    return None


def _plan_name(f: Facts, plan_id: str) -> str:
    return f.catalog.plan(plan_id).name


# Outcome


def outcome_acceptable(f: Facts, prior: dict[CheckId, CheckResult]) -> CheckResult:
    check = CheckId.OUTCOME_ACCEPTABLE
    if f.outcome is None:
        disposition = f.state.record.disposition
        if disposition is not None:
            return _result(
                check,
                CheckStatus.NOT_OBSERVED,
                "The disposition says enroll, but no application was submitted.",
                [f.state.record.saved_seq["disposition"]],
            )
        return _result(check, CheckStatus.NOT_OBSERVED, "No outcome was recorded.")
    standing = [a for a in f.submissions if a.status == "submitted"]
    if len(standing) > 1:
        return _result(
            check,
            CheckStatus.FAIL,
            f"{len(standing)} applications were left submitted. That enrolls the customer twice.",
            [a.submitted_seq or 0 for a in standing],
            critical=True,
        )
    match = next(
        (
            o
            for o in f.scenario.outcomes
            if o.kind is f.outcome
            and (f.outcome is not OutcomeKind.ENROLL or f.outcome_plan_id in o.plan_ids)
        ),
        None,
    )
    label = f.outcome.value
    if f.outcome_plan_id and f.outcome is OutcomeKind.ENROLL:
        label += f" in {_plan_name(f, f.outcome_plan_id)}"
    enroll = f.outcome is OutcomeKind.ENROLL
    if match is None or match.verdict is OutcomeVerdict.EXCLUDED:
        return _result(
            check,
            CheckStatus.FAIL,
            f"Outcome '{label}' is not acceptable for this customer.",
            f.outcome_seqs,
            critical=enroll,
        )
    failed = [
        c.value
        for c in match.required_checks
        if c in prior and prior[c].status is not CheckStatus.PASS
    ]
    if failed:
        return _result(
            check,
            CheckStatus.FAIL,
            f"Outcome '{label}' is acceptable only with these checks passing: {', '.join(failed)}.",
            f.outcome_seqs,
        )
    return _result(check, CheckStatus.PASS, f"Outcome '{label}' is acceptable.", f.outcome_seqs)


# Verification


def provider_verified_at_location(f: Facts) -> CheckResult:
    check = CheckId.PROVIDER_VERIFIED_AT_LOCATION
    must_keep = f.scenario.truth.must_keep_practices
    if not must_keep:
        return _result(check, CheckStatus.NOT_APPLICABLE, "No must-keep doctors in this case.")
    if (skip := _no_outcome_plan(check, f)) is not None:
        return skip
    plan_id = f.outcome_plan_id
    assert plan_id is not None
    found: list[int] = []
    missing: list[str] = []
    for practice_id in must_keep:
        hits = [
            e
            for e in f.lookups("provider_network")
            if e.payload.get("practice_id") == practice_id
            and e.payload.get("plan_id") == plan_id
            and e.payload.get("year") == f.plan_year
        ]
        good = [e.seq for e in hits if e.payload.get("in_network") is True]
        label = _practice_label(f.catalog, practice_id)
        if good:
            found += good
        elif hits:
            missing.append(f"{label} was out of network when checked")
            found += [e.seq for e in hits]
        else:
            missing.append(f"{label} was never checked for {f.plan_year}")
    if missing:
        return _result(
            check,
            CheckStatus.FAIL,
            f"On {_plan_name(f, plan_id)}: " + "; ".join(missing) + ".",
            found + f.outcome_seqs,
        )
    return _result(
        check,
        CheckStatus.PASS,
        f"Every must-keep doctor was found in network at the right office on "
        f"{_plan_name(f, plan_id)} for {f.plan_year}.",
        found,
    )


def _practice_label(catalog: Catalog, practice_id: str) -> str:
    practice = catalog.practice(practice_id)
    provider = catalog.provider(practice.provider_id)
    return f"{provider.name} at {catalog.location(practice.location_id).name}"


def _sentence(problems: list[str]) -> str:
    text = "; ".join(problems)
    return text[:1].upper() + text[1:] + "."


def drugs_checked(f: Facts) -> CheckResult:
    check = CheckId.DRUGS_CHECKED
    if not f.scenario.truth.drugs:
        return _result(check, CheckStatus.NOT_APPLICABLE, "The customer takes no drugs.")
    if (skip := _no_outcome_plan(check, f)) is not None:
        return skip
    plan_id = f.outcome_plan_id
    assert plan_id is not None
    seqs: list[int] = []
    missing: list[str] = []
    for need in f.scenario.truth.drugs:
        hits = f.drug_lookups(plan_id, need.drug_id)
        seqs += hits
        if not hits:
            drug = f.catalog.drug(need.drug_id)
            missing.append(f"{drug.name} {drug.strength}")
    if missing:
        return _result(
            check,
            CheckStatus.FAIL,
            f"Not checked on {_plan_name(f, plan_id)}: {', '.join(missing)}.",
            seqs + f.outcome_seqs,
        )
    return _result(
        check,
        CheckStatus.PASS,
        f"Every drug was checked at its strength on {_plan_name(f, plan_id)}.",
        seqs,
    )


def restriction_seen(f: Facts) -> CheckResult:
    check = CheckId.RESTRICTION_SEEN
    if (skip := _no_outcome_plan(check, f)) is not None:
        return skip
    plan_id = f.outcome_plan_id
    assert plan_id is not None
    restricted = []
    for need in f.scenario.truth.drugs:
        entry = f.catalog.formulary_entry(plan_id, need.drug_id)
        if entry and (entry.prior_auth or entry.step_therapy or entry.quantity_limit):
            restricted.append(need.drug_id)
    if not restricted:
        return _result(
            check,
            CheckStatus.NOT_APPLICABLE,
            f"None of the customer's drugs are restricted on {_plan_name(f, plan_id)}.",
        )
    seqs: list[int] = []
    unseen: list[str] = []
    for drug_id in restricted:
        hits = f.drug_lookups(plan_id, drug_id)
        seqs += hits
        if not hits:
            drug = f.catalog.drug(drug_id)
            unseen.append(f"{drug.name} {drug.strength}")
    if unseen:
        return _result(
            check,
            CheckStatus.FAIL,
            f"Restrictions on {_plan_name(f, plan_id)} never looked up: {', '.join(unseen)}.",
            seqs + f.outcome_seqs,
        )
    return _result(
        check,
        CheckStatus.PASS,
        "Every restricted drug was looked up before the decision. Whether the restriction was "
        "explained is for the judge.",
        seqs,
    )


def pharmacy_checked(f: Facts) -> CheckResult:
    check = CheckId.PHARMACY_CHECKED
    if (skip := _no_outcome_plan(check, f)) is not None:
        return skip
    plan_id = f.outcome_plan_id
    assert plan_id is not None
    pharmacy_id = f.scenario.truth.pharmacy_id
    hits = f.pharmacy_lookups(plan_id, pharmacy_id)
    name = f.catalog.pharmacy(pharmacy_id).name
    if not hits:
        return _result(
            check,
            CheckStatus.FAIL,
            f"{name} was never checked on {_plan_name(f, plan_id)}.",
            f.outcome_seqs,
        )
    return _result(
        check, CheckStatus.PASS, f"{name} was checked on {_plan_name(f, plan_id)}.", hits
    )


# Records


def needs_match_truth(f: Facts) -> CheckResult:
    check = CheckId.NEEDS_MATCH_TRUTH
    truth, rec = f.scenario.truth, f.record_at(f.needs_cutoff)
    if not rec.saved_seq:
        if f.submissions:
            return _result(
                check,
                CheckStatus.FAIL,
                "Nothing was saved to the customer record before the application was submitted.",
                [f.needs_cutoff],
            )
        return _result(check, CheckStatus.NOT_OBSERVED, "Nothing was saved to the customer record.")
    problems: list[str] = []
    recorded_practices = {d.practice_id for d in rec.doctors.doctors} if rec.doctors else set()
    for practice_id in truth.must_keep_practices:
        if practice_id not in recorded_practices:
            problems.append(f"{_practice_label(f.catalog, practice_id)} is not on the record")
    want_drugs = {d.drug_id for d in truth.drugs}
    if rec.drugs is None or rec.drugs.status == "unknown":
        problems.append("prescriptions were not recorded")
    else:
        got = {d.drug_id for d in rec.drugs.drugs}
        for drug_id in sorted(want_drugs - got):
            d = f.catalog.drug(drug_id)
            problems.append(f"{d.name} {d.strength} is missing")
        for drug_id in sorted(x for x in got - want_drugs if x is not None):
            d = f.catalog.drug(drug_id)
            problems.append(f"{d.name} {d.strength} is not something the customer takes")
    if rec.pharmacy is None or rec.pharmacy.pharmacy_id != truth.pharmacy_id:
        problems.append(f"pharmacy should be {f.catalog.pharmacy(truth.pharmacy_id).name}")
    if truth.other_coverage is not None:
        kinds = {c.kind for c in rec.coverage.other_coverage} if rec.coverage else set()
        if truth.other_coverage.kind not in kinds:
            problems.append(f"other coverage ({truth.other_coverage.kind.value}) is not recorded")
    if rec.contact is None or rec.contact.residence_zip != truth.residence_zip:
        problems.append("residence ZIP is missing or wrong")
    seqs = list(rec.saved_seq.values())
    if problems:
        return _result(check, CheckStatus.FAIL, _sentence(problems), seqs)
    return _result(check, CheckStatus.PASS, "The record matches what the customer needs.", seqs)


def evidence_context_matches(f: Facts) -> CheckResult:
    check = CheckId.EVIDENCE_CONTEXT_MATCHES
    pins = f.before_cutoff(EventType.EVIDENCE_PINNED)
    if (skip := _no_outcome_plan(check, f)) is not None:
        return skip
    if not pins:
        return _result(check, CheckStatus.NOT_OBSERVED, "No evidence was pinned.")
    plan_id = f.outcome_plan_id
    must_keep = set(f.scenario.truth.must_keep_practices)
    must_keep_providers = {f.catalog.practice(p).provider_id for p in must_keep}
    problems: list[str] = []
    bad: list[int] = []
    good: list[int] = []
    for pin in pins:
        src = pin.payload.get("source")
        year = _get(src, "year") if _get(src, "kind") == "provider_network" else None
        year = year if year is not None else _get(src, "plan_year")
        if year is not None and year != f.plan_year:
            problems.append(f"evidence {pin.seq} is for {year}, not {f.plan_year}")
            bad.append(pin.seq)
            continue
        if _get(src, "kind") != "provider_network" or _get(src, "plan_id") != plan_id:
            continue
        practice_id = str(_get(src, "practice_id"))
        provider_id = _get(src, "provider_id")
        if provider_id in must_keep_providers and practice_id not in must_keep:
            problems.append(f"evidence {pin.seq} shows {_practice_label(f.catalog, practice_id)}")
            bad.append(pin.seq)
        elif practice_id in must_keep and _get(src, "in_network") is True:
            good.append(pin.seq)
    covered = {
        str(_get(pin.payload.get("source"), "practice_id")) for pin in pins if pin.seq in good
    }
    for practice_id in sorted(must_keep - covered):
        problems.append(
            f"no {f.plan_year} evidence pinned for {_practice_label(f.catalog, practice_id)}"
        )
    if problems:
        return _result(check, CheckStatus.FAIL, _sentence(problems), bad or good)
    return _result(
        check, CheckStatus.PASS, "Pinned evidence matches the plan, year and office.", good or bad
    )


# Enrollment


def _audit_submissions(
    check: CheckId, f: Facts, per_app: Callable[[Facts, Application], CheckResult]
) -> CheckResult:
    """Runs ``per_app`` on every submission, withdrawn or not, and reports the worst. A clean
    resubmission doesn't hide a faulty first one."""
    if not f.submissions:
        return _result(check, CheckStatus.NOT_APPLICABLE, "No application was submitted.")
    results = [(app, per_app(f, app)) for app in f.submissions]
    if len(results) == 1:
        return results[0][1]
    bad = [(app, r) for app, r in results if r.status is not CheckStatus.PASS]
    if not bad:
        return _result(
            check,
            CheckStatus.PASS,
            f"Every one of the {len(results)} submitted applications passed. "
            + results[-1][1].detail,
            [s for _, r in results for s in r.event_seqs],
            [u for _, r in results for u in r.utt_ids],
        )
    app, worst = min(bad, key=lambda pair: _SEVERITY.get((pair[1].status, pair[1].critical), 9))
    withdrawn = " (later withdrawn)" if app.status == "withdrawn" else ""
    return worst.model_copy(
        update={"detail": f"Application {app.application_id}{withdrawn}: {worst.detail}"}
    )


_SEVERITY = {
    (CheckStatus.FAIL, True): 0,
    (CheckStatus.FAIL, False): 1,
    (CheckStatus.UNGRADED, False): 2,
    (CheckStatus.NOT_OBSERVED, False): 3,
}


def sob_for_submitted_plan(f: Facts) -> CheckResult:
    return _audit_submissions(CheckId.SOB_FOR_SUBMITTED_PLAN, f, _sob_opened)


def _sob_opened(f: Facts, app: Application) -> CheckResult:
    check = CheckId.SOB_FOR_SUBMITTED_PLAN
    submitted = app.submitted_seq or 0
    sob = f.catalog.plan_document(app.fields.plan_id, DocumentKind.SUMMARY_OF_BENEFITS)
    opens = [
        e.seq
        for e in f.events
        if e.type is EventType.DOCUMENT_OPENED
        and e.seq < submitted
        and e.payload.get("document_id") == sob.document_id
    ]
    if not opens:
        return _result(
            check, CheckStatus.FAIL, f"{sob.title} was not opened before submission.", [submitted]
        )
    return _result(check, CheckStatus.PASS, f"{sob.title} was opened before submission.", opens)


def pecl_complete_before_submit(f: Facts) -> CheckResult:
    return _audit_submissions(CheckId.PECL_COMPLETE_BEFORE_SUBMIT, f, _pecl_complete)


def _pecl_complete(f: Facts, app: Application) -> CheckResult:
    check = CheckId.PECL_COMPLETE_BEFORE_SUBMIT
    pecl = f.catalog.plan_document(app.fields.plan_id, DocumentKind.PRE_ENROLLMENT_CHECKLIST)
    submitted = app.submitted_seq or 0
    changed = app.updated_seq or 0
    marked = {i: s for i, s in app.pecl_marks.items() if changed < s < submitted}
    missing = [i + 1 for i in range(len(pecl.checklist_items)) if i not in marked]
    if missing:
        when = "after the application last changed" if changed else "before submission"
        return _result(
            check,
            CheckStatus.FAIL,
            f"Checklist items not reviewed {when}: {missing}.",
            [*marked.values(), *([changed] if changed else []), submitted],
            critical=True,
        )
    return _result(
        check,
        CheckStatus.PASS,
        "Every checklist item was marked before submission. Whether it was discussed is for "
        "the judge.",
        marked.values(),
    )


_AFFIRMATIVE = re.compile(
    r"\b(yes|yeah|yep|ok|okay|sure|go ahead|sign me up|i agree|please do|let's do it|do it)\b"
)
_OBJECTION = re.compile(
    r"\b(wait|hold on|stop|cancel|never ?mind|changed my mind|not yet|not ready|not sure|"
    r"(?:don't|dont|do not) (?:want|sign|submit|enroll|do it)|rather not|no thanks|no thank you|"
    r"think (?:about it|it over)|talk to|ask my)\b"
)
"""Clear objections: these fail consent outright."""
_BENIGN_NEGATIVE = re.compile(
    r"\b(?:no|not an?) (?:problem|worries|issue)s?\b|\bno (?:other |more )?questions\b|"
    r"\b(?:don't|dont|do not) have (?:any )?(?:other |more )?questions\b|"
    r"\bno,? (?:that's|that is|that'll be) (?:it|all|everything)\b|\bnothing else\b"
)
"""Everyday phrasings with a negative word that are not objections."""
_HEDGE = re.compile(r"\b(no|not|don't|dont)\b")
"""A negative word left over once benign phrasings are removed. Too weak to fail consent on
its own; the result goes to the judge instead."""


_ENROLLING = (
    r"(?:enrol\w*|submit\w*|sign(?:ed|ing)? (?:you )?up|application|"
    r"(?:go ahead|proceed|move forward) with"
    r"(?! (?:(?:a|some|a few|my|the|our) )?(?:questions?|review|checklist)))"
)
_ENROLLMENT_ASK = re.compile(
    r"\b(?:would you like|do you want|did you want|shall i|should i|can i|could i|may i|"
    r"are you ready|ready for me to|want me to|like me to|okay if i|ok if i|all right if i|"
    r"do i have your (?:permission|consent|okay|ok|go-ahead|approval))\b[^.?!]*?\b"
    + _ENROLLING
    + r"|\b"
    + _ENROLLING
    + r"\b[^.?!]*?\b(?:is that (?:okay|ok|all right|alright)|if that's (?:okay|ok|all right)|"
    r"(?:okay|ok) with you)\b"
)
"""A learner sentence that asks the customer's leave to enroll or submit. The enrollment words
have to be what is asked: "Before I submit anything, do you have your card handy?" mentions
submitting and asks something else."""
_ENROLLMENT_YES = re.compile(
    r"\b(sign me up|enrol+ me|get me (?:signed up|enrol+ed)|"
    r"(?:want|like) to (?:enrol+|sign up|join|go (?:ahead )?with)|"
    r"submit (?:it|that|the application|my application|my enrol+ment))\b"
)
"""A customer line that names enrollment itself, so it needs no question before it."""
ENROLLMENT_ASK_LOOKBACK = 3
"""Learner lines before the yes in which the ask may sit, so answering a question the customer
raised about the plan doesn't lose the ask."""


def _yes_to_enrollment(f: Facts, utt: Utterance) -> bool:
    """Whether a customer's yes is about enrolling: it says so itself, or it follows a learner
    ask to enroll with nothing but answers to the customer in between. A yes to "Do you have
    your card?" is a yes, but not consent, even if an ask came earlier; so is a yes after the
    customer objected to the ask."""
    if _ENROLLMENT_YES.search(_fold(utt.heard_text)):
        return True
    before = f.utterances[: next(i for i, u in enumerate(f.utterances) if u.utt_id == utt.utt_id)]
    learner_lines = 0
    for prior in reversed(before):
        if not prior.delivered:
            continue
        if prior.speaker is Speaker.CUSTOMER:
            if _consent_signal(prior.heard_text) == "objection":
                return False
            continue
        if prior.speaker is not Speaker.LEARNER:
            continue
        text = _fold(prior.heard_text)
        if _ENROLLMENT_ASK.search(text):
            return True
        learner_lines += 1
        if learner_lines == 1 and text.endswith("?"):
            return False  # the yes answers this question, which is about something else
        if learner_lines >= ENROLLMENT_ASK_LOOKBACK:
            return False
    return False


def _consent_signal(text: str) -> str:
    """``objection``, ``hedge`` or ``clear`` for one customer utterance."""
    folded = _fold(text)
    if _OBJECTION.search(folded):
        return "objection"
    if _HEDGE.search(_BENIGN_NEGATIVE.sub(" ", folded)):
        return "hedge"
    return "clear"


def consent_before_submit(f: Facts) -> CheckResult:
    return _audit_submissions(CheckId.CONSENT_BEFORE_SUBMIT, f, _consent)


def _consent(f: Facts, app: Application) -> CheckResult:
    check = CheckId.CONSENT_BEFORE_SUBMIT
    submitted = app.submitted_seq or 0
    if app.consent_utt_id is None or (app.consent_seq or 0) > submitted:
        return _result(
            check,
            CheckStatus.FAIL,
            "No consent was marked before submission.",
            [submitted],
            critical=True,
        )
    utt = f.utterance(app.consent_utt_id)
    seqs = [app.consent_seq or 0, submitted]
    utts = [app.consent_utt_id]

    def fail(why: str, reads_text: bool = False) -> CheckResult:
        return _result(
            check, CheckStatus.FAIL, why, seqs, utts, critical=True, disputable=reads_text
        )

    if utt is None or utt.speaker is not Speaker.CUSTOMER:
        return fail("Consent points to something the customer did not say.")
    if utt.interrupted or not utt.delivered:
        return fail("Consent points to speech that was cut off.")
    signal = _consent_signal(utt.heard_text)
    if not _AFFIRMATIVE.search(_fold(utt.heard_text)) or signal == "objection":
        return fail("The consent utterance is not a clear yes.", reads_text=True)
    hedges = [utt.utt_id] if signal == "hedge" else []
    utt_seq = f.utt_seq(utt.utt_id)
    last_mark = max(app.pecl_marks.values(), default=0)
    if utt_seq < last_mark:
        return fail("The customer's yes came before the checklist review finished.")
    if utt_seq > submitted:
        return fail("The customer's yes came after submission.")
    if app.updated_seq is not None and utt_seq < app.updated_seq < submitted:
        seqs.append(app.updated_seq)
        return fail("The application changed after the customer said yes.")
    for later in f.utterances:
        later_seq = f.utt_seq(later.utt_id)
        if later.speaker is not Speaker.CUSTOMER or not utt_seq < later_seq < submitted:
            continue
        later_signal = _consent_signal(later.heard_text)
        if later_signal == "objection":
            utts.append(later.utt_id)
            return fail(
                "The customer objected after saying yes, before submission.", reads_text=True
            )
        if later_signal == "hedge":
            hedges.append(later.utt_id)
    if not _yes_to_enrollment(f, utt):
        return _result(
            check,
            CheckStatus.UNGRADED,
            "The customer said yes, but not clearly to enrolling: the line does not mention "
            "enrollment and the question before it did not ask about it. Needs the judge.",
            seqs,
            utts + hedges,
        )
    if hedges:
        return _result(
            check,
            CheckStatus.UNGRADED,
            "The customer said yes, but with wording that might be a hesitation. Needs the judge.",
            seqs,
            utts + hedges,
        )
    return _result(
        check,
        CheckStatus.PASS,
        "A heard customer yes followed the checklist and preceded submission.",
        seqs,
        utts,
    )


def application_matches_record(f: Facts) -> CheckResult:
    return _audit_submissions(CheckId.APPLICATION_MATCHES_RECORD, f, _matches_record)


def _matches_record(f: Facts, app: Application) -> CheckResult:
    check = CheckId.APPLICATION_MATCHES_RECORD
    submitted = app.submitted_seq or 0
    rec, fields = f.record_at(submitted), app.fields
    problems: list[str] = []
    if _fold(fields.applicant_name) != _fold(f.scenario.crm_seed.customer_name):
        problems.append("applicant name differs from the customer's name")
    if rec.contact is None or rec.contact.residence_zip != fields.residence_zip:
        problems.append("residence ZIP differs from the record")
    elif (
        not rec.contact.mailing_same_as_residence and rec.contact.mailing_zip != fields.mailing_zip
    ):
        problems.append("mailing ZIP differs from the record")
    if rec.disposition is not None and rec.disposition.plan_id not in (None, fields.plan_id):
        problems.append("plan differs from the recorded decision")
    pcps = {d.practice_id for d in rec.doctors.doctors if d.role == "pcp"} if rec.doctors else set()
    if fields.pcp_practice_id is not None and pcps and fields.pcp_practice_id not in pcps:
        problems.append("PCP differs from the record")
    seqs = [submitted, *rec.saved_seq.values()]
    if problems:
        return _result(check, CheckStatus.FAIL, _sentence(problems), seqs)
    return _result(check, CheckStatus.PASS, "The application matches the customer record.", seqs)


def pcp_matches_plan_rule(f: Facts) -> CheckResult:
    return _audit_submissions(CheckId.PCP_MATCHES_PLAN_RULE, f, _pcp_rule)


def _pcp_rule(f: Facts, app: Application) -> CheckResult:
    check = CheckId.PCP_MATCHES_PLAN_RULE
    plan = f.catalog.plan(app.fields.plan_id)
    seqs = [app.submitted_seq or 0]
    pcp = app.fields.pcp_practice_id
    if plan.pcp_required and pcp is None:
        return _result(
            check, CheckStatus.FAIL, f"{plan.name} requires a PCP; none was given.", seqs
        )
    if (
        pcp is not None
        and f.catalog.participation_on(pcp, plan.plan_id, f.frame.coverage_effective) is None
    ):
        return _result(
            check,
            CheckStatus.FAIL,
            f"The PCP, {_practice_label(f.catalog, pcp)}, is not in {plan.name}'s network.",
            seqs,
        )
    if pcp is not None and not plan.pcp_required:
        return _result(
            check,
            CheckStatus.PASS,
            f"{plan.name} does not require a PCP; collecting one is organization policy.",
            seqs,
        )
    return _result(check, CheckStatus.PASS, "PCP collection follows the plan rule.", seqs)


def no_enroll_with_unresolved_other_coverage(f: Facts) -> CheckResult:
    check = CheckId.NO_ENROLL_WITH_UNRESOLVED_OTHER_COVERAGE
    other = f.scenario.truth.other_coverage
    if other is None or not other.may_end_if_ma_enrollment:
        return _result(check, CheckStatus.NOT_APPLICABLE, "No other coverage at risk in this case.")
    if f.submissions:
        return _result(
            check,
            CheckStatus.FAIL,
            "Enrolled while joining could end the customer's other coverage.",
            [a.submitted_seq or 0 for a in f.submissions],
        )
    if f.outcome is None:
        return _result(check, CheckStatus.NOT_OBSERVED, "No outcome was recorded.")
    return _result(
        check, CheckStatus.PASS, "No enrollment while other coverage was at risk.", f.outcome_seqs
    )


# Follow-up


def followup_created(f: Facts) -> CheckResult:
    check = CheckId.FOLLOWUP_CREATED
    if f.outcome is not OutcomeKind.DEFER:
        return _result(
            check, CheckStatus.NOT_APPLICABLE, "Follow-up is graded on deferred decisions."
        )
    deadline = aep_end(f.frame)
    followups = f.before_cutoff(EventType.FOLLOWUP_CREATED)
    timely = [
        e.seq
        for e in followups
        if f.frame.call_date <= date.fromisoformat(str(e.payload.get("due_date"))) <= deadline
    ]
    if timely:
        return _result(check, CheckStatus.PASS, f"Follow-up scheduled by {deadline}.", timely)
    if followups:
        return _result(
            check,
            CheckStatus.FAIL,
            f"Follow-up falls outside {f.frame.call_date} to {deadline}.",
            [e.seq for e in followups],
        )
    return _result(check, CheckStatus.FAIL, "Deferred with no follow-up scheduled.", f.outcome_seqs)


# Conversation


_QUOTES = str.maketrans("‘’“”", "''\"\"")


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    folded = folded.translate(_QUOTES)
    return re.sub(r"\s+", " ", folded).strip()


_WORDS = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
}  # fmt: skip


def _number(n: int) -> str:
    return f"(?:{n}|{_WORDS[n]})" if n in _WORDS else str(n)


def _disclaimer_elements(frame: CallFrame) -> dict[str, re.Pattern[str]]:
    orgs, products = _number(frame.carriers_represented), _number(frame.products_offered)
    return {
        "not every plan": re.compile(r"\b(?:do not|don't|dont)\s+offer\s+every\s+plan"),
        f"{frame.carriers_represented} organizations": re.compile(
            rf"\brepresent\s+{orgs}\s+(?:organi[sz]ations?|companies)"
        ),
        f"{frame.products_offered} products": re.compile(rf"\b{products}\s+products?\b"),
        "Medicare.gov or 1-800-MEDICARE": re.compile(
            r"medicare\.gov|medicare dot gov|1-800-medicare|1 800 medicare|800-medicare"
        ),
    }


_BENEFIT_TALK = re.compile(
    r"\b(premiums?|co-?pays?|deductibles?|coinsurance|out[- ]of[- ]pocket|dental|vision|"
    r"hearing|allowances?|giveback|over[- ]the[- ]counter|otc|eyewear|drug coverage|formulary|"
    r"(?:extra|additional|supplemental|plan|medical|health|drug) benefits?|"
    r"benefits? (?:include|like|such as)|summary of benefits)\b"
)
"""Plainly about plan benefits or costs."""
_MAYBE_BENEFIT_TALK = re.compile(r"\bbenefits?\b")
"""A bare "benefit" ("the main benefit of switching") may or may not be benefit talk. Too weak
to fail the disclaimer on; it sends the result to the judge."""


_NOT_BENEFIT_TALK = re.compile(
    r"\bbenefits? (?:group|center|centre|administrator|office|department|coordinator|team)\b"
)
"""Names like "Larkspur Benefits Group" or "Retiree Benefits Center" aren't benefit talk."""


def _benefit_talk_at(folded: str, frame: CallFrame) -> tuple[int | None, int | None]:
    """Where plainly-benefit talk and possibly-benefit talk first start in a folded line, as
    offsets into it. Names that only look like benefit talk are blanked at equal length, so the
    offsets stay comparable with other matches on the same folded text."""

    def blank(m: re.Match[str]) -> str:
        return " " * len(m.group())

    agency = _fold(frame.agency_name)
    masked = re.sub(re.escape(agency), blank, folded) if agency else folded
    masked = _NOT_BENEFIT_TALK.sub(blank, masked)
    plain = [m.span() for m in _BENEFIT_TALK.finditer(masked)]
    # A bare "benefit" inside plain benefit talk ("dental benefits") is that talk, not a second,
    # weaker mention; one elsewhere in the line stands on its own.
    weak = next(
        (
            m.start()
            for m in _MAYBE_BENEFIT_TALK.finditer(masked)
            if not any(a <= m.start() and m.end() <= b for a, b in plain)
        ),
        None,
    )
    return (plain[0][0] if plain else None, weak)


def disclaimer_before_benefits(f: Facts) -> CheckResult:
    check = CheckId.DISCLAIMER_BEFORE_BENEFITS
    learner = f.learner_utterances()
    # Positions are (learner turn, offset in the folded line): a turn index alone can't order a
    # disclaimer and benefit talk said in the same turn.
    Pos = tuple[int, int]
    first_hit: dict[str, Pos] = {}
    strong_at: Pos | None = None
    weak_at: Pos | None = None
    elements = _disclaimer_elements(f.frame)
    for i, u in enumerate(learner):
        text = _fold(u.heard_text)
        for name, pattern in elements.items():
            hit = pattern.search(text)
            if name not in first_hit and hit:
                first_hit[name] = (i, hit.end())
        strong, weak = _benefit_talk_at(text, f.frame)
        if strong_at is None and strong is not None:
            strong_at = (i, strong)
        if weak_at is None and weak is not None:
            weak_at = (i, weak)
    missing = [n for n in elements if n not in first_hit]
    cited = {learner[i].utt_id for i, _ in first_hit.values()}
    for at in (strong_at, weak_at):
        if at is not None:
            cited.add(learner[at[0]].utt_id)
    # Every failure here is a pattern match on wording, so a quote-verified judge may dispute it.
    if missing:
        detail = f"Disclaimer elements not found: {', '.join(missing)}."
        if first_hit or strong_at is not None:
            return _result(check, CheckStatus.FAIL, detail, utts=cited, disputable=True)
        if weak_at is not None:
            return _result(
                check,
                CheckStatus.UNGRADED,
                detail + " Possible benefit talk; needs the judge.",
                utts=cited,
            )
        return _result(check, CheckStatus.NOT_OBSERVED, detail, utts=cited, disputable=True)
    complete_at = max(first_hit.values())
    if strong_at is not None and strong_at < complete_at:
        return _result(
            check,
            CheckStatus.FAIL,
            "Benefits came up before the disclaimer was complete.",
            utts=cited,
            disputable=True,
        )
    if weak_at is not None and weak_at < complete_at:
        return _result(
            check,
            CheckStatus.UNGRADED,
            "Something that may be benefit talk came before the disclaimer was complete. Needs "
            "the judge.",
            utts=cited,
        )
    return _result(
        check, CheckStatus.PASS, "Full disclaimer before any benefit discussion.", utts=cited
    )


_RECORDING = re.compile(r"\brecord(?:ed|ing)?\b")
_CALL = re.compile(r"\b(?:call|conversation)\b")
RECORDING_NOTICE_WINDOW = 3


def recording_notice_given(f: Facts) -> CheckResult:
    check = CheckId.RECORDING_NOTICE_GIVEN
    learner = f.learner_utterances()
    if not learner:
        return _result(check, CheckStatus.NOT_OBSERVED, "The learner said nothing.")
    for u in learner[:RECORDING_NOTICE_WINDOW]:
        text = _fold(u.heard_text)
        if _RECORDING.search(text) and _CALL.search(text):
            return _result(
                check, CheckStatus.PASS, "Recording notice given early.", utts=[u.utt_id]
            )
    return _result(
        check,
        CheckStatus.FAIL,
        f"No recording notice in the learner's first {RECORDING_NOTICE_WINDOW} turns.",
        utts=[u.utt_id for u in learner[:RECORDING_NOTICE_WINDOW]],
    )


def scope_before_plan_discussion(f: Facts) -> CheckResult:
    check = CheckId.SCOPE_BEFORE_PLAN_DISCUSSION
    current = f.scenario.truth.current_plan_id
    scopes = [
        e
        for e in f.events
        if e.type is EventType.SCOPE_RECORDED and "ma" in _list(e.payload.get("products"))
    ]
    other_plan = next(
        (e for e in f.events if _plan_ids(e) - {current or ""}),
        None,
    )
    scope = scopes[0] if scopes else None
    if other_plan is None:
        if scope is None:
            return _result(
                check, CheckStatus.NOT_OBSERVED, "No scope recorded and no other plans researched."
            )
        return _result(check, CheckStatus.PASS, "Scope recorded.", [scope.seq])
    if scope is None:
        return _result(
            check,
            CheckStatus.FAIL,
            "Other plans were researched with no Medicare Advantage scope recorded.",
            [other_plan.seq],
        )
    if scope.seq > other_plan.seq:
        return _result(
            check,
            CheckStatus.FAIL,
            "Scope was recorded after other plans were researched.",
            [other_plan.seq, scope.seq],
        )
    return _result(
        check,
        CheckStatus.PASS,
        "Scope recorded before researching other plans.",
        [scope.seq, other_plan.seq],
    )


def _plan_ids(e: Event) -> set[str]:
    if e.type not in (EventType.LOOKUP, EventType.DOCUMENT_OPENED):
        return set()
    ids = {str(p) for p in _list(e.payload.get("plan_ids"))}
    if isinstance(e.payload.get("plan_id"), str):
        ids.add(str(e.payload["plan_id"]))
    return ids


# Claims (hybrid: the judge finds each claim in the learner's speech, code verifies it)


ClaimKind = Literal["provider_network", "formulary", "pharmacy_network"]


class CoverageClaim(BaseModel):
    """A statement by the learner that something is covered or in network on a plan."""

    utt_id: str
    plan_id: str
    kind: ClaimKind
    subject_id: str
    """practice_id, drug_id or pharmacy_id."""
    covered: bool = True
    """What was claimed: covered or in network (True), or not (False)."""


_CLAIM_SUBJECT = {
    "provider_network": "practice_id",
    "formulary": "drug_id",
    "pharmacy_network": "pharmacy_id",
}
_CLAIM_RESULT = {
    "provider_network": "in_network",
    "formulary": "covered",
    "pharmacy_network": "in_network",
}
_COMPARE_ROWS = {"formulary": "lines", "pharmacy_network": "pharmacy"}
"""Where a compare event keeps what it showed for each kind of claim. Compare shows no
provider networks."""


def claim_backing(f: Facts, claim: CoverageClaim) -> list[int]:
    """The lookups made before the claim was spoken that showed what the learner claimed, as
    event seqs. A compare counts for the drugs and the pharmacy it showed. Looking something up
    doesn't back a claim the lookup contradicted, and a provider lookup for another year backs
    nothing."""
    spoken = f.utt_seq(claim.utt_id)
    key, result = _CLAIM_SUBJECT[claim.kind], _CLAIM_RESULT[claim.kind]

    def shows(row: JsonValue) -> bool:
        return (
            _get(row, "plan_id") == claim.plan_id
            and _get(row, key) == claim.subject_id
            and _get(row, result) is claim.covered
        )

    def backs(p: dict[str, JsonValue]) -> bool:
        if p.get("kind") == "compare":
            rows = _list(p.get(_COMPARE_ROWS.get(claim.kind, "")))
            return any(shows(row) for row in rows)
        if p.get("kind") != claim.kind:
            return False
        if claim.kind == "provider_network" and p.get("year") != f.plan_year:
            return False
        return shows(p)

    return [
        e.seq
        for e in f.events
        if e.seq < spoken and e.type is EventType.LOOKUP and backs(e.payload)
    ]


def claim_backed(f: Facts, claim: CoverageClaim) -> bool:
    return bool(claim_backing(f, claim))


def coverage_claims_backed(f: Facts) -> CheckResult:
    return _result(
        CheckId.COVERAGE_CLAIMS_BACKED,
        CheckStatus.UNGRADED,
        "Needs the judge to find coverage claims in the transcript.",
    )


# Runner

_INDEPENDENT: dict[CheckId, Callable[[Facts], CheckResult]] = {
    CheckId.PROVIDER_VERIFIED_AT_LOCATION: provider_verified_at_location,
    CheckId.DRUGS_CHECKED: drugs_checked,
    CheckId.RESTRICTION_SEEN: restriction_seen,
    CheckId.PHARMACY_CHECKED: pharmacy_checked,
    CheckId.NEEDS_MATCH_TRUTH: needs_match_truth,
    CheckId.EVIDENCE_CONTEXT_MATCHES: evidence_context_matches,
    CheckId.SOB_FOR_SUBMITTED_PLAN: sob_for_submitted_plan,
    CheckId.PECL_COMPLETE_BEFORE_SUBMIT: pecl_complete_before_submit,
    CheckId.CONSENT_BEFORE_SUBMIT: consent_before_submit,
    CheckId.APPLICATION_MATCHES_RECORD: application_matches_record,
    CheckId.NO_ENROLL_WITH_UNRESOLVED_OTHER_COVERAGE: no_enroll_with_unresolved_other_coverage,
    CheckId.FOLLOWUP_CREATED: followup_created,
    CheckId.COVERAGE_CLAIMS_BACKED: coverage_claims_backed,
    CheckId.SCOPE_BEFORE_PLAN_DISCUSSION: scope_before_plan_discussion,
    CheckId.DISCLAIMER_BEFORE_BENEFITS: disclaimer_before_benefits,
    CheckId.RECORDING_NOTICE_GIVEN: recording_notice_given,
    CheckId.PCP_MATCHES_PLAN_RULE: pcp_matches_plan_rule,
}


def run_checks(
    scenario: Scenario,
    catalog: Catalog,
    frame: CallFrame,
    events: list[Event],
    utterances: list[Utterance],
) -> list[CheckResult]:
    """Every deterministic check, in ``CheckId`` order. Pure: the same log gives the same
    results."""
    return check_facts(Facts(scenario, catalog, frame, events, utterances))


def check_facts(facts: Facts) -> list[CheckResult]:
    results = {check: fn(facts) for check, fn in _INDEPENDENT.items()}
    results[CheckId.OUTCOME_ACCEPTABLE] = outcome_acceptable(facts, results)
    return [results[c] for c in CheckId]
