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

from pydantic import BaseModel, JsonValue

from app.content.catalog import Catalog, DocumentKind
from app.content.common import CheckId, OutcomeKind
from app.content.loader import aep_end
from app.content.scenario import CallFrame, OutcomeVerdict, Scenario
from app.engine.events import Event, EventType, Speaker, Utterance
from app.engine.records import Application, SessionState


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
    outcome_plan_id: str | None = field(init=False)
    cutoff: int = field(init=False)

    def __post_init__(self) -> None:
        self.state = SessionState.fold(self.events)
        self.enrolled = self.state.submitted_application()
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
) -> CheckResult:
    return CheckResult(
        check_id=check,
        status=status,
        critical=critical,
        detail=detail,
        event_seqs=sorted(set(seqs)),
        utt_ids=sorted(set(utts)),
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
    truth, rec = f.scenario.truth, f.state.record
    if not rec.saved_seq:
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


def _enrollment_only(check: CheckId, f: Facts) -> CheckResult | None:
    if f.enrolled is None:
        return _result(check, CheckStatus.NOT_APPLICABLE, "No application was submitted.")
    return None


def sob_for_submitted_plan(f: Facts) -> CheckResult:
    check = CheckId.SOB_FOR_SUBMITTED_PLAN
    if (skip := _enrollment_only(check, f)) is not None:
        return skip
    assert f.enrolled is not None
    sob = f.catalog.plan_document(f.enrolled.fields.plan_id, DocumentKind.SUMMARY_OF_BENEFITS)
    opens = [
        e.seq
        for e in f.before_cutoff(EventType.DOCUMENT_OPENED)
        if e.payload.get("document_id") == sob.document_id
    ]
    if not opens:
        return _result(
            check,
            CheckStatus.FAIL,
            f"{sob.title} was not opened before submission.",
            [f.enrolled.submitted_seq or 0],
        )
    return _result(check, CheckStatus.PASS, f"{sob.title} was opened before submission.", opens)


def pecl_complete_before_submit(f: Facts) -> CheckResult:
    check = CheckId.PECL_COMPLETE_BEFORE_SUBMIT
    if (skip := _enrollment_only(check, f)) is not None:
        return skip
    app = f.enrolled
    assert app is not None
    pecl = f.catalog.plan_document(app.fields.plan_id, DocumentKind.PRE_ENROLLMENT_CHECKLIST)
    submitted = app.submitted_seq or 0
    marked = {i: s for i, s in app.pecl_marks.items() if s < submitted}
    missing = [i + 1 for i in range(len(pecl.checklist_items)) if i not in marked]
    if missing:
        return _result(
            check,
            CheckStatus.FAIL,
            f"Checklist items not reviewed before submission: {missing}.",
            [*marked.values(), submitted],
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
_NEGATIVE = re.compile(
    r"\b(no|not|don't|dont|wait|hold on|stop|cancel|never mind|nevermind|changed my mind)\b"
)


def consent_before_submit(f: Facts) -> CheckResult:
    check = CheckId.CONSENT_BEFORE_SUBMIT
    if (skip := _enrollment_only(check, f)) is not None:
        return skip
    app = f.enrolled
    assert app is not None
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

    def fail(why: str) -> CheckResult:
        return _result(check, CheckStatus.FAIL, why, seqs, utts, critical=True)

    if utt is None or utt.speaker is not Speaker.CUSTOMER:
        return fail("Consent points to something the customer did not say.")
    if utt.interrupted or not utt.delivered:
        return fail("Consent points to speech that was cut off.")
    text = _fold(utt.heard_text)
    if not _AFFIRMATIVE.search(text) or _NEGATIVE.search(text):
        return fail("The consent utterance is not a clear yes.")
    utt_seq = f.utt_seq(utt.utt_id)
    last_mark = max(app.pecl_marks.values(), default=0)
    if utt_seq < last_mark:
        return fail("The customer's yes came before the checklist review finished.")
    if utt_seq > submitted:
        return fail("The customer's yes came after submission.")
    for later in f.utterances:
        later_seq = f.utt_seq(later.utt_id)
        if (
            later.speaker is Speaker.CUSTOMER
            and utt_seq < later_seq < submitted
            and _NEGATIVE.search(_fold(later.heard_text))
        ):
            utts.append(later.utt_id)
            return fail("The customer objected after saying yes, before submission.")
    return _result(
        check,
        CheckStatus.PASS,
        "A heard customer yes followed the checklist and preceded submission.",
        seqs,
        utts,
    )


def application_matches_record(f: Facts) -> CheckResult:
    check = CheckId.APPLICATION_MATCHES_RECORD
    if (skip := _enrollment_only(check, f)) is not None:
        return skip
    app = f.enrolled
    assert app is not None
    rec, fields = f.state.record, app.fields
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
    seqs = [app.submitted_seq or 0, *rec.saved_seq.values()]
    if problems:
        return _result(check, CheckStatus.FAIL, _sentence(problems), seqs)
    return _result(check, CheckStatus.PASS, "The application matches the customer record.", seqs)


def pcp_matches_plan_rule(f: Facts) -> CheckResult:
    check = CheckId.PCP_MATCHES_PLAN_RULE
    if (skip := _enrollment_only(check, f)) is not None:
        return skip
    app = f.enrolled
    assert app is not None
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
    if f.outcome is None:
        return _result(check, CheckStatus.NOT_OBSERVED, "No outcome was recorded.")
    if f.enrolled is not None:
        return _result(
            check,
            CheckStatus.FAIL,
            "Enrolled while joining could end the customer's other coverage.",
            f.outcome_seqs,
        )
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
    r"hearing|allowances?|benefits?|giveback|over[- ]the[- ]counter|otc|eyewear|"
    r"drug coverage|formulary)\b"
)


_NOT_BENEFIT_TALK = re.compile(
    r"\bbenefits? (?:group|center|centre|administrator|office|department|coordinator|team)\b"
)
"""Names like "Larkspur Benefits Group" or "Retiree Benefits Center" aren't benefit talk."""


def _talks_benefits(text: str, frame: CallFrame) -> bool:
    folded = _fold(text).replace(_fold(frame.agency_name), " ")
    return bool(_BENEFIT_TALK.search(_NOT_BENEFIT_TALK.sub(" ", folded)))


def disclaimer_before_benefits(f: Facts) -> CheckResult:
    check = CheckId.DISCLAIMER_BEFORE_BENEFITS
    learner = f.learner_utterances()
    first_hit: dict[str, int] = {}
    for i, u in enumerate(learner):
        text = _fold(u.heard_text)
        for name, pattern in _disclaimer_elements(f.frame).items():
            if name not in first_hit and pattern.search(text):
                first_hit[name] = i
    benefit_at = next(
        (i for i, u in enumerate(learner) if _talks_benefits(u.heard_text, f.frame)), None
    )
    names = list(_disclaimer_elements(f.frame))
    missing = [n for n in names if n not in first_hit]
    cited = {learner[i].utt_id for i in first_hit.values()}
    if benefit_at is not None:
        cited.add(learner[benefit_at].utt_id)
    if missing:
        status = (
            CheckStatus.NOT_OBSERVED if not first_hit and benefit_at is None else CheckStatus.FAIL
        )
        return _result(
            check, status, f"Disclaimer elements not found: {', '.join(missing)}.", utts=cited
        )
    complete_at = max(first_hit.values())
    if benefit_at is not None and benefit_at < complete_at:
        return _result(
            check,
            CheckStatus.FAIL,
            "Benefits came up before the disclaimer was complete.",
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


# Claims (hybrid; the judge supplies claims in M3)


class CoverageClaim(BaseModel):
    """A statement by the learner that something is covered or in network on a plan."""

    utt_id: str
    plan_id: str
    kind: str
    """provider_network, formulary or pharmacy_network."""
    subject_id: str
    """practice_id, drug_id or pharmacy_id."""


def claim_backed(f: Facts, claim: CoverageClaim) -> bool:
    """Whether a lookup that supports the claim happened before the claim was spoken."""
    spoken = f.utt_seq(claim.utt_id)
    key = {
        "provider_network": "practice_id",
        "formulary": "drug_id",
        "pharmacy_network": "pharmacy_id",
    }[claim.kind]
    for e in f.events:
        if e.seq >= spoken or e.type is not EventType.LOOKUP:
            continue
        p = e.payload
        if (
            p.get("kind") == claim.kind
            and p.get("plan_id") == claim.plan_id
            and p.get(key) == claim.subject_id
        ):
            if claim.kind == "provider_network" and p.get("year") != f.plan_year:
                continue
            return True
    return False


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
    facts = Facts(scenario, catalog, frame, events, utterances)
    results = {check: fn(facts) for check, fn in _INDEPENDENT.items()}
    results[CheckId.OUTCOME_ACCEPTABLE] = outcome_acceptable(facts, results)
    return [results[c] for c in CheckId]
