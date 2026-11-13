"""Grading: the judge's rulings count only once the server has verified them, hybrid criteria
combine the check and the judge the way the rubric says, and the score never rests on anything
unverified.

The judge here is a fake that returns scripted rulings, so every assertion is about what the
server does with a ruling, never about what a model said.
"""

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.content.loader import load_bundle
from app.engine.checks import Facts
from app.engine.events import Utterance
from app.engine.grading import GradeStatus, grade
from app.engine.session import SessionService
from app.engine.store import Store
from app.judge.contract import JudgeClaim, JudgeOutput, JudgeVerdict
from app.judge.judges import JudgeError
from app.judge.prompt import JudgePrompt
from tests.conftest import StepClock
from tests.harness import DISCLAIMER, Call, FakeJudge, claim, ruling
from tests.test_goldens import (
    A_DRUGS,
    CASE_A,
    CASE_B,
    MAPLE_CHOICE,
    SUMMIT_CLASSIC,
    a_call,
    c_call,
    enroll_to_consent,
)

ASK_DOCTORS = "Which doctors do you need to keep, and which office do you see them at?"
CLAIM = "Dr. Raman at Northside is in network on Maple Choice PPO."
CHECKLIST = "Let's go through the Pre-Enrollment Checklist together, item by item."
NOTES = {
    "reason_for_inquiry": "Letter about her cardiologist leaving the plan",
    "rationale": "Maple Choice keeps Dr. Raman at Northside",
}


def researched(client: TestClient) -> Call:
    """Case A up to the decision: needs asked, the right plan verified, one claim made after
    its lookup, notes saved."""
    call = a_call(client)
    call.say(ASK_DOCTORS)
    call.hear("Dr. Priya Raman, at the Northside office.")
    call.verify(MAPLE_CHOICE, ["raman-northside"], A_DRUGS, "alder-family-rx")
    call.say(CLAIM)
    call.save("notes", NOTES)
    call.decide("enroll", MAPLE_CHOICE)
    return call


def enrolled(client: TestClient) -> Call:
    call = researched(client)
    call.enroll(MAPLE_CHOICE, "Loretta Banks", "00041")
    return call.end()


def answering(call: Call, *rulings: JudgeVerdict, claims: tuple[JudgeClaim, ...] = ()) -> FakeJudge:
    """A judge that rules on everything it is asked: ``rulings`` where given, "not observed"
    for the rest. Rulings on items it was not asked about are returned too."""

    def answer(prompt: JudgePrompt) -> JudgeOutput:
        given = {r.criterion_id: r for r in rulings}
        asked = [given.pop(cid, ruling(cid, "not_observed")) for cid in prompt.asked]
        return JudgeOutput(verdicts=[*asked, *given.values()], coverage_claims=list(claims))

    judge = FakeJudge(answer)
    call.service.judge = judge
    return judge


# Locked, and gradable with no judge


def test_the_debrief_is_locked_until_the_call_ends(client: TestClient) -> None:
    call = researched(client)
    assert "after the call ends" in call.get("/debrief", expect=403)["detail"]
    call.post("/debrief/judge", expect=403)
    call.end()
    call.get("/debrief")


def test_without_a_judge_the_call_is_incomplete_never_failed(client: TestClient) -> None:
    debrief = enrolled(client).debrief()
    body = debrief.body
    assert body["state"] == "incomplete"
    assert body["judge"]["state"] == "off" and body["judge"]["judge"] is None
    statuses = debrief.statuses()
    assert statuses["disc-doctors"] == statuses["ver-claims"] == statuses["doc-pecl"] == "ungraded"
    assert "No judge is configured" in debrief["disc-doctors"]["detail"]
    assert "fail" not in statuses.values() and body["critical"] == []
    # Only what the rule checks could settle is in the total.
    assert body["score"] == {"earned": 42, "possible": 42, "percent": 100}
    assert sum(d["possible"] for d in body["dimensions"]) == 42
    assert debrief.status("doc-consent") == ("pass", False)
    assert body["outcome"] == {"kind": "enroll", "plan_name": "Maple Choice PPO"}


# Quotes


def test_a_verified_quote_earns_the_points_and_links_the_line(client: TestClient) -> None:
    call = enrolled(client)
    ask = call.utt(ASK_DOCTORS)
    answering(call, ruling("disc-doctors", "pass", (ask, "which office do you SEE them at")))
    result = call.debrief()["disc-doctors"]
    assert (result["status"], result["earned"], result["points"]) == ("pass", 3, 3)
    assert result["utt_ids"] == [ask]
    assert result["quotes"] == [
        {"utt_id": ask, "quote": "which office do you SEE them at", "verified": True}
    ]
    assert result["rationale"] and result["detail"] == ""


@pytest.mark.parametrize(
    ("evidence", "why"),
    [
        ([("ASK", "which location do you visit")], "not in u"),
        ([("CLAIM", "which office do you see them at")], "not in u"),
        ([("u0999", "which office do you see them at")], "not a line of this call"),
        ([], "cited no line"),
        ([("notes", "Maple Choice keeps Dr. Raman")], "not evidence of what was said"),
        ([("ASK", "which office do you see them at"), ("ASK", "a made-up line")], "not in u"),
    ],
    ids=["paraphrase", "wrong-line", "no-such-line", "no-evidence", "notes-on-speech", "one-bad"],
)
def test_a_ruling_the_server_cannot_verify_is_held_for_review(
    client: TestClient, evidence: list[tuple[str, str]], why: str
) -> None:
    call = enrolled(client)
    ids = {"ASK": call.utt(ASK_DOCTORS), "CLAIM": call.utt(CLAIM)}
    cited = [(ids.get(u, u), q) for u, q in evidence]
    answering(call, ruling("disc-doctors", "pass", *cited))
    debrief = call.debrief()
    result = debrief["disc-doctors"]
    assert (result["status"], result["earned"]) == ("review_required", 0)
    assert why in result["detail"] and "Held for review" in result["detail"]
    assert debrief.body["state"] == "needs_review"
    # Held items are out of the total, so a judge's citation error never costs the learner.
    scored = [
        c for c in debrief.body["criteria"] if c["status"] in ("pass", "fail", "not_observed")
    ]
    assert debrief.body["score"]["possible"] == sum(c["points"] for c in scored)
    assert "disc-doctors" not in {c["criterion_id"] for c in scored}


def test_a_failing_ruling_needs_a_verified_quote_too(client: TestClient) -> None:
    call = enrolled(client)
    answering(call, ruling("com-choice", "fail", (call.utt(CLAIM), "you have to decide today")))
    debrief = call.debrief()
    assert debrief.status("com-choice") == ("review_required", False)
    assert "com-choice" not in {f["criterion_id"] for f in debrief.body["retry_focus"]}


def test_a_quote_must_fall_on_whole_words(client: TestClient) -> None:
    call = researched(client)
    know = call.say("I know this is a lot to take in.")
    call.end()
    answering(call, ruling("com-plain", "fail", (know, "no")))
    assert call.debrief().status("com-plain") == ("review_required", False)


def test_only_heard_words_can_be_quoted(client: TestClient) -> None:
    call = researched(client)
    cut = call.hear("I thought it was twenty milligrams of that one.", heard="I thought it was")
    unheard = call.hear("Never mind.", heard="", interrupted=True)
    call.end()
    answering(call, ruling("com-misconceptions", "not_observed", (cut, "twenty milligrams")))
    assert call.debrief().status("com-misconceptions") == ("review_required", False)
    answering(call, ruling("com-misconceptions", "not_observed", (cut, "I thought it was")))
    assert call.post("/debrief/judge", {"regrade": True})
    assert call.debrief()["com-misconceptions"]["status"] == "not_observed"
    answering(call, ruling("com-misconceptions", "not_observed", (unheard, "Never mind")))
    call.post("/debrief/judge", {"regrade": True})
    assert call.debrief()["com-misconceptions"]["status"] == "review_required"


def test_not_observed_scores_zero_and_is_never_critical(client: TestClient) -> None:
    call = enrolled(client)
    answering(call)
    debrief = call.debrief()
    assert debrief.body["state"] == "complete"
    result = debrief["disc-pharmacy"]
    assert (result["status"], result["earned"], result["critical"]) == ("not_observed", 0, False)
    assert debrief.body["critical"] == []
    # Everything was ruled on; only the not-applicable items are out of the total.
    out = sum(c["points"] for c in debrief.body["criteria"] if c["status"] == "not_applicable")
    assert debrief.body["score"]["possible"] == 100 - out
    assert debrief.body["score"]["earned"] == 42


def test_the_notes_criterion_quotes_the_saved_notes(client: TestClient) -> None:
    call = enrolled(client)
    answering(call, ruling("comp-notes", "pass", ("notes", "maple choice keeps Dr. Raman")))
    result = call.debrief()["comp-notes"]
    assert (result["status"], result["earned"]) == ("pass", 2)
    assert result["quotes"][0] == {
        "utt_id": None,
        "quote": "maple choice keeps Dr. Raman",
        "verified": True,
    }
    saved = call.get("/record")["saved_seq"]
    assert result["event_seqs"] == sorted([saved["notes"], saved["disposition"]])


def test_notes_are_evidence_for_the_notes_criterion_only(client: TestClient) -> None:
    # What the learner wrote down afterwards can't show what was said. The quotes below are
    # really in the notes; on a criterion about the conversation they still verify nothing.
    call, utt = consent_call(client, "Okay, sure, why not.", notes="Customer gave a clear yes")
    in_notes = ("notes", "Customer gave a clear yes")
    answering(
        call,
        ruling("doc-consent", "pass", in_notes),
        ruling("doc-pecl", "fail", in_notes),
        ruling("elig-scope", "pass", in_notes),
        ruling("comp-notes", "pass", (utt, "Okay, sure, why not")),
    )
    debrief = call.debrief()
    for criterion_id in ("doc-consent", "doc-pecl", "elig-scope"):
        assert debrief.status(criterion_id) == ("review_required", False), criterion_id
        assert "not evidence of what was said" in debrief[criterion_id]["detail"]
        assert debrief[criterion_id]["quotes"][0]["verified"] is False
    assert debrief.body["critical"] == []
    # And the notes criterion needs something from the notes, not a line of the call.
    assert debrief.status("comp-notes") == ("review_required", False)
    assert "cited nothing from the saved notes" in debrief["comp-notes"]["detail"]


# Hybrid: both


@pytest.mark.parametrize(
    ("verdict", "quote", "expected"),
    [
        ("pass", "go through the Pre-Enrollment Checklist together", ("pass", False)),
        ("not_observed", None, ("not_observed", False)),
        ("fail", "go through the Pre-Enrollment Checklist together", ("fail", True)),
        ("not_applicable", None, ("review_required", False)),
        ("pass", "we read every item aloud", ("review_required", False)),
    ],
)
def test_marked_checklist_items_still_need_the_judge(
    client: TestClient, verdict: str, quote: str | None, expected: tuple[str, bool]
) -> None:
    call = enrolled(client)
    evidence = [(call.utt(CHECKLIST), quote)] if quote else []
    judge = answering(call, ruling("doc-pecl", verdict, *evidence))
    debrief = call.debrief()
    assert debrief.status("doc-pecl") == expected
    assert "doc-pecl" in judge.prompts[0].asked
    assert debrief["doc-pecl"]["check_status"] == "pass"
    assert debrief.body["critical"] == (["doc-pecl"] if expected[1] else [])


def test_a_skipped_checklist_fails_whatever_the_judge_says(client: TestClient) -> None:
    call = researched(client)
    call.document("sob-maple-choice-2027")
    app_id = call.apply(MAPLE_CHOICE, "Loretta Banks", "00041")
    call.say("Would you like me to submit your enrollment in this plan?")
    yes = call.hear("Yes, sign me up.")
    call.consent(app_id, yes)
    call.submit(app_id)
    call.end()
    judge = answering(call, ruling("doc-pecl", "pass", (yes, "Yes, sign me up")))
    debrief = call.debrief()
    assert "doc-pecl" not in judge.prompts[0].asked
    assert debrief.status("doc-pecl") == ("fail", True)
    assert debrief["doc-pecl"]["rationale"] is None
    assert debrief.body["critical"] == ["doc-pecl"]
    assert debrief.body["retry_focus"][0]["criterion_id"] == "doc-pecl"


def test_scope_recorded_in_order_still_needs_the_ask(client: TestClient) -> None:
    call = enrolled(client)
    ask = call.utt("Which kinds of coverage would you like to talk about today?")
    answering(call, ruling("elig-scope", "pass", (ask, "Which kinds of coverage would you like")))
    result = call.debrief()["elig-scope"]
    assert (result["status"], result["earned"]) == ("pass", 4)
    assert result["utt_ids"] == [ask] and result["event_seqs"]


# Hybrid: backstop


def consent_call(client: TestClient, yes: str, notes: str = "") -> tuple[Call, str]:
    call, app_id = enroll_to_consent(client)
    call.say("Would you like me to submit your enrollment in this plan?")
    utt = call.hear(yes)
    call.consent(app_id, utt)
    call.submit(app_id)
    if notes:
        call.save("notes", {"rationale": notes})
    return call.end(), utt


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        ("pass", ("pass", False)),
        ("fail", ("fail", True)),
        ("not_observed", ("not_observed", False)),
    ],
)
def test_the_judge_rules_on_a_yes_the_check_could_not_read(
    client: TestClient, verdict: str, expected: tuple[str, bool]
) -> None:
    call, utt = consent_call(client, "Okay, sure, why not.")
    assert call.detail("consent_before_submit")["status"] == "ungraded"
    evidence = [(utt, "Okay, sure, why not")] if verdict != "not_observed" else []
    judge = answering(call, ruling("doc-consent", verdict, *evidence))
    debrief = call.debrief()
    assert "doc-consent" in judge.prompts[0].asked
    assert debrief.status("doc-consent") == expected
    assert "might be a hesitation" in debrief["doc-consent"]["detail"]


def test_a_yes_the_check_misread_goes_to_review_not_to_a_critical_finding(
    client: TestClient,
) -> None:
    call, utt = consent_call(client, "Absolutely, let's get it done.")
    check = call.detail("consent_before_submit")
    assert (check["status"], check["critical"], check["disputable"]) == ("fail", True, True)
    # With no judge, the check's reading stands.
    assert call.debrief().status("doc-consent") == ("fail", True)

    answering(call, ruling("doc-consent", "pass", (utt, "Absolutely, let's get it done")))
    debrief = call.debrief()
    assert debrief.status("doc-consent") == ("review_required", False)
    assert "held for review" in debrief["doc-consent"]["detail"]
    assert debrief.body["critical"] == []

    # A judge who reads it the same way leaves the failure in place.
    answering(call, ruling("doc-consent", "fail", (utt, "let's get it done")))
    call.post("/debrief/judge", {"regrade": True})
    assert call.debrief().status("doc-consent") == ("fail", True)

    # So does a judge whose own quote doesn't check out.
    answering(call, ruling("doc-consent", "pass", (utt, "Yes, please enroll me")))
    call.post("/debrief/judge", {"regrade": True})
    debrief = call.debrief()
    assert debrief.status("doc-consent") == ("fail", True)
    assert debrief["doc-consent"]["rationale"] is None


def test_a_yes_at_the_wrong_time_fails_however_it_reads(client: TestClient) -> None:
    # The wording check can't read this yes, and it also came before the checklist review.
    # When it happened decides, so the judge is not asked and can't send it to review.
    call = researched(client)
    call.document("sob-maple-choice-2027")
    app_id = call.apply(MAPLE_CHOICE, "Loretta Banks", "00041")
    early = call.hear("Absolutely, let's get it done.")
    call.review_checklist(app_id)
    call.consent(app_id, early)
    call.submit(app_id)
    call.end()
    check = call.detail("consent_before_submit")
    assert "before the checklist review finished" in check["detail"]
    assert (check["status"], check["critical"], check["disputable"]) == ("fail", True, False)
    judge = answering(call, ruling("doc-consent", "pass", (early, "Absolutely, let's get it done")))
    debrief = call.debrief()
    assert "doc-consent" not in judge.prompts[0].asked
    assert debrief.status("doc-consent") == ("fail", True)
    assert "doc-consent" in debrief.body["critical"]


def two_submissions(client: TestClient, first_yes: str | None, second_yes: str | None) -> Call:
    """Two applications submitted in one call, the first withdrawn. None: no consent marked.
    The second yes answers a question that isn't about enrolling."""
    call, first = enroll_to_consent(client)
    call.say("Would you like me to submit your enrollment in this plan?")
    if first_yes is not None:
        call.consent(first, call.hear(first_yes))
    call.submit(first, key="first-key-0001")
    call.post(f"/applications/{first}/withdraw")
    second = call.apply(MAPLE_CHOICE, "Loretta Banks", "00041")
    call.review_checklist(second)
    call.say("Do you have your Medicare card handy?")
    if second_yes is not None:
        call.consent(second, call.hear(second_yes))
    call.submit(second, key="second-key-0002")
    return call.end()


def test_one_applications_wording_cannot_cover_anothers_missing_consent(
    client: TestClient,
) -> None:
    call = two_submissions(client, "Absolutely, let's get it done.", None)
    check = call.detail("consent_before_submit")
    assert check["detail"].startswith("Application app-2: No consent was marked")
    assert (check["status"], check["critical"], check["disputable"]) == ("fail", True, False)
    line = call.utt("Absolutely, let's get it done.")
    judge = answering(call, ruling("doc-consent", "pass", (line, "Absolutely, let's get it done")))
    debrief = call.debrief()
    assert "doc-consent" not in judge.prompts[0].asked
    assert debrief.status("doc-consent") == ("fail", True)


def test_two_unread_consents_are_held_not_settled_by_one_ruling(client: TestClient) -> None:
    call = two_submissions(client, "Okay, sure, why not.", "Yes.")
    check = call.detail("consent_before_submit")
    assert (check["status"], check["held"], check["critical"]) == ("ungraded", True, False)
    assert "Application app-1 (later withdrawn): " in check["detail"]
    assert "Application app-2: " in check["detail"]
    line = call.utt("Okay, sure, why not.")
    judge = answering(call, ruling("doc-consent", "pass", (line, "Okay, sure, why not")))
    debrief = call.debrief()
    assert "doc-consent" not in judge.prompts[0].asked
    assert debrief.status("doc-consent") == ("review_required", False)
    assert "held for review" in debrief["doc-consent"]["detail"]
    # With no judge at all it is held the same way, not left as merely ungraded.
    call.service.judge = None
    assert call.debrief(judge=False).status("doc-consent") == ("review_required", False)


def test_missing_consent_is_not_the_judges_to_dispute(client: TestClient) -> None:
    call, app_id = enroll_to_consent(client)
    yes = call.hear("Yes, sign me up.")
    call.submit(app_id)  # the yes was never marked as consent
    call.end()
    assert call.detail("consent_before_submit")["disputable"] is False
    judge = answering(call, ruling("doc-consent", "pass", (yes, "Yes, sign me up")))
    debrief = call.debrief()
    assert "doc-consent" not in judge.prompts[0].asked
    assert debrief.status("doc-consent") == ("fail", True)


def test_the_judge_settles_a_possible_benefit_mention(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    first = call.say(
        "Thanks for calling. This call is recorded. The main benefit of calling us is choice."
    )
    call.say(DISCLAIMER)
    call.end()
    answering(call, ruling("elig-disclaimer", "pass", (first, "The main benefit of calling us")))
    assert call.debrief().status("elig-disclaimer") == ("pass", False)


def test_a_paraphrased_disclaimer_goes_to_review(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    said = call.say(
        "This call is recorded. We don't carry all the plans out there; we work with 3 companies "
        "that have 6 plans here. You can check Medicare.gov or call 1-800-MEDICARE for the rest."
    )
    call.end()
    assert call.debrief().status("elig-disclaimer") == ("fail", False)
    answering(call, ruling("elig-disclaimer", "pass", (said, "We don't carry all the plans")))
    debrief = call.debrief()
    assert debrief.status("elig-disclaimer") == ("review_required", False)
    assert "Disclaimer elements not found" in debrief["elig-disclaimer"]["detail"]


# Coverage claims


def provider_claim(utt: str, plan: str = MAPLE_CHOICE, covered: bool = True) -> JudgeClaim:
    quote = "is in network on" if covered else "is not in network on"
    return claim(utt, quote, "provider_network", plan, "raman-northside", covered)


def test_a_claim_made_after_its_lookup_is_backed(client: TestClient) -> None:
    call = enrolled(client)
    said = call.utt(CLAIM)
    misquoted = claim(
        said, "is surely covered", "provider_network", MAPLE_CHOICE, "raman-northside"
    )
    answering(call, claims=(misquoted, provider_claim(said), provider_claim(said)))
    debrief = call.debrief()
    result = debrief["ver-claims"]
    assert (result["status"], result["earned"], result["critical"]) == ("pass", 2, False)
    # The judge listed the claim three times, once with a quote that isn't there. One listing
    # the server could check is enough.
    assert len(result["claims"]) == 1
    one = result["claims"][0]
    assert one["status"] == "backed"
    assert one["label"] == (
        "Priya Raman at Alder Heart Associates, Northside office is in network on Maple Choice PPO"
    )
    lookups = [
        e["seq"] for e in call.get("/events") if e["payload"].get("kind") == "provider_network"
    ]
    assert one["event_seqs"] == lookups and result["event_seqs"] == lookups
    assert result["utt_ids"] == [said]


def test_a_claim_with_no_lookup_behind_it_is_a_critical_finding(client: TestClient) -> None:
    call = a_call(client)
    early = call.say("Good news, Dr. Raman is in network on Maple Choice PPO.")
    call.network("raman-northside", MAPLE_CHOICE)  # looked up only after saying so
    call.end()
    answering(call, claims=(provider_claim(early),))
    debrief = call.debrief()
    result = debrief["ver-claims"]
    assert (result["status"], result["critical"]) == ("fail", True)
    assert result["claims"][0]["status"] == "unbacked"
    assert f"No lookup for 2027 made before {early}" in result["claims"][0]["detail"]
    assert "1 of 1 coverage claims" in result["detail"] and early in result["detail"]
    assert debrief.body["critical"] == ["ver-claims"]
    assert debrief.body["retry_focus"][0]["criterion_id"] == "ver-claims"


def test_a_lookup_that_said_otherwise_backs_nothing(client: TestClient) -> None:
    call = a_call(client)
    call.network("raman-northside", SUMMIT_CLASSIC)  # out of network for 2027
    call.network("raman-northside", MAPLE_CHOICE, year=2026)  # the wrong year
    wrong = call.say("Dr. Raman is in network on Summit Classic and is in network on Maple Choice.")
    right = call.say("Sorry, Dr. Raman is not in network on Summit Classic next year.")
    call.end()
    answering(
        call,
        claims=(
            provider_claim(wrong, SUMMIT_CLASSIC),
            provider_claim(wrong, MAPLE_CHOICE),
            provider_claim(right, SUMMIT_CLASSIC, covered=False),
        ),
    )
    result = call.debrief()["ver-claims"]
    assert [c["status"] for c in result["claims"]] == ["unbacked", "unbacked", "backed"]
    assert (result["status"], result["critical"]) == ("fail", True)
    assert "2 of 3 coverage claims" in result["detail"]


def test_a_claim_about_this_year_is_checked_against_this_years_lookup(
    client: TestClient,
) -> None:
    call = a_call(client)
    call.network("raman-northside", SUMMIT_CLASSIC, year=2026)  # in network this year
    call.network("raman-northside", SUMMIT_CLASSIC)  # out for 2027
    said = call.say("Dr. Raman is in network on Summit Classic this year, but not next year.")
    call.end()
    now = claim(
        said,
        "is in network on Summit Classic this year",
        "provider_network",
        SUMMIT_CLASSIC,
        "raman-northside",
        year=2026,
    )
    later = claim(
        said,
        "but not next year",
        "provider_network",
        SUMMIT_CLASSIC,
        "raman-northside",
        covered=False,
    )
    answering(call, claims=(now, later))
    result = call.debrief()["ver-claims"]
    assert [c["status"] for c in result["claims"]] == ["backed", "backed"]
    assert (result["status"], result["critical"]) == ("pass", False)
    assert result["claims"][0]["label"].endswith("is in network on Summit Classic HMO for 2026")
    assert result["claims"][1]["label"].endswith("is not in network on Summit Classic HMO")

    # The same words with only the 2027 lookup behind them are not backed.
    other = a_call(client)
    other.network("raman-northside", SUMMIT_CLASSIC)
    said = other.say("Dr. Raman is in network on Summit Classic this year.")
    other.end()
    answering(
        other,
        claims=(
            claim(
                said,
                "is in network on Summit Classic this year",
                "provider_network",
                SUMMIT_CLASSIC,
                "raman-northside",
                year=2026,
            ),
        ),
    )
    result = other.debrief()["ver-claims"]
    assert (result["status"], result["critical"]) == ("fail", True)
    assert "No lookup for 2026 made before" in result["claims"][0]["detail"]


@pytest.mark.parametrize(
    ("kind", "subject", "year"),
    [
        ("provider_network", "raman-northside", 2025),
        ("provider_network", "raman-northside", 2028),
        ("formulary", "apixaban-5mg-tab", 2026),
        ("pharmacy_network", "alder-family-rx", 2026),
    ],
)
def test_a_claim_about_a_year_the_catalog_lacks_is_held_not_failed(
    client: TestClient, kind: str, subject: str, year: int
) -> None:
    call = enrolled(client)
    said = call.utt(CLAIM)
    answering(
        call, claims=(claim(said, "is in network on", kind, MAPLE_CHOICE, subject, year=year),)
    )
    result = call.debrief()["ver-claims"]
    assert (result["status"], result["critical"]) == ("review_required", False)
    assert f"nothing for {year}" in result["claims"][0]["detail"]


def test_a_compare_backs_the_drugs_and_pharmacy_it_showed(client: TestClient) -> None:
    call = a_call(client)
    call.post(
        "/research/compare",
        {
            "plan_ids": [MAPLE_CHOICE],
            "drugs": [{"drug_id": "apixaban-5mg-tab"}],
            "pharmacy_id": "alder-family-rx",
        },
    )
    said = call.say(
        "Your apixaban is covered on Maple Choice, and Alder Family Pharmacy is in network. "
        "Your atorvastatin is covered too."
    )
    call.end()
    answering(
        call,
        claims=(
            claim(said, "apixaban is covered", "formulary", MAPLE_CHOICE, "apixaban-5mg-tab"),
            claim(said, "is in network", "pharmacy_network", MAPLE_CHOICE, "alder-family-rx"),
            claim(
                said, "atorvastatin is covered", "formulary", MAPLE_CHOICE, "atorvastatin-40mg-tab"
            ),
        ),
    )
    result = call.debrief()["ver-claims"]
    assert [c["status"] for c in result["claims"]] == ["backed", "backed", "unbacked"]
    assert result["claims"][2]["label"] == "atorvastatin 40 mg is covered on Maple Choice PPO"


def test_no_claims_means_nothing_to_grade(client: TestClient) -> None:
    call = enrolled(client)
    answering(call)
    result = call.debrief()["ver-claims"]
    assert (result["status"], result["earned"]) == ("not_applicable", 0)


@pytest.mark.parametrize(
    "bad",
    [
        lambda said, heard: claim(
            said, "is covered everywhere", "provider_network", MAPLE_CHOICE, "whitfield-alder"
        ),
        lambda said, heard: claim(
            heard, "at the Northside office", "provider_network", MAPLE_CHOICE, "raman-northside"
        ),
        lambda said, heard: claim(
            said, "is in network on", "provider_network", "TRN-9999-001", "raman-northside"
        ),
        lambda said, heard: claim(
            said, "is in network on", "provider_network", MAPLE_CHOICE, "apixaban-5mg-tab"
        ),
        lambda said, heard: claim(
            "u0999", "is in network on", "provider_network", MAPLE_CHOICE, "raman-northside"
        ),
    ],
    ids=["misquote", "customer-line", "unknown-plan", "wrong-kind-of-id", "no-such-line"],
)
def test_a_claim_the_server_cannot_check_is_held_for_review(
    client: TestClient, bad: Callable[[str, str], JudgeClaim]
) -> None:
    call = enrolled(client)
    said, heard = call.utt(CLAIM), call.utt("at the Northside office.")
    answering(call, claims=(provider_claim(said), bad(said, heard)))
    result = call.debrief()["ver-claims"]
    assert (result["status"], result["critical"]) == ("review_required", False)
    assert [c["status"] for c in result["claims"]] == ["backed", "unverifiable"]
    assert "1 of 2 claims the judge listed could not be checked" in result["detail"]


# Case rules and authority labels


def test_items_a_case_marks_not_applicable_leave_the_total(client: TestClient) -> None:
    call = enrolled(client)
    bundle = load_bundle()
    scenario = bundle.scenario(CASE_A).model_copy(
        update={"not_applicable": {"ver-provider": "No doctor to keep.", "rec-premiums": "N/A."}}
    )
    facts = Facts(
        scenario,
        bundle.catalog,
        bundle.frame,
        call.service.store.events(call.sid),
        call.service.store.utterances(call.sid),
    )
    card = grade(facts, bundle.rubric, bundle.ruleset, None)
    by_id = {c.criterion_id: c for c in card.criteria}
    assert by_id["ver-provider"].status is GradeStatus.NOT_APPLICABLE
    assert by_id["ver-provider"].detail == "No doctor to keep."
    assert by_id["rec-premiums"].status is GradeStatus.NOT_APPLICABLE
    assert card.score.possible == 42 - 7


def test_rule_labels_follow_the_outcome_and_carry_unverified(client: TestClient) -> None:
    def rules(debrief: Any, criterion_id: str) -> dict[str, tuple[str, str]]:
        return {r["rule_id"]: (r["authority"], r["status"]) for r in debrief[criterion_id]["rules"]}

    enrolled_debrief = enrolled(client).debrief()
    assert rules(enrolled_debrief, "ver-provider") == {"R-NEEDS": ("regulatory", "approved")}
    assert rules(enrolled_debrief, "doc-sob") == {"R-SB": ("regulatory", "unverified")}
    assert rules(enrolled_debrief, "elig-recording") == {"O-RECNOTE": ("organization", "approved")}
    assert rules(enrolled_debrief, "disc-coverage") == {"G-PREENR": ("cms_guidance", "approved")}
    assert enrolled_debrief["rec-outcome"]["rules"] == []

    deferred = c_call(client)
    deferred.post("/followups", {"due_date": "2026-12-03", "reason": "Hear from Brightwater"}, 201)
    deferred.decide("defer")
    deferred.end()
    # The needs discussion is a regulation on an enrollment and a training objective otherwise.
    assert rules(deferred.debrief(), "ver-provider") == {"R-NEEDS": ("training", "approved")}


# Stored judge output


def test_the_judge_is_asked_once_and_its_output_is_reused(client: TestClient) -> None:
    call = enrolled(client)
    ask = call.utt(ASK_DOCTORS)
    judge = answering(
        call, ruling("disc-doctors", "pass", (ask, "which office do you see them at"))
    )
    before = call.debrief(judge=False).body
    assert before["judge"]["state"] == "not_run" and before["judge"]["judge"] == "fake:judge-1"
    assert "Waiting for the judge" in call.debrief(judge=False)["disc-doctors"]["detail"]
    assert judge.prompts == []  # reading the debrief never calls the judge

    first = call.debrief().body
    second = call.debrief().body
    read = call.debrief(judge=False).body
    assert len(judge.prompts) == 1
    assert first == second == read
    info = first["judge"]
    assert (info["state"], info["judge"], info["served_model"]) == (
        "graded",
        "fake:judge-1",
        "judge-1",
    )
    assert (info["attempt"], info["input_tokens"], info["output_tokens"]) == (1, 1200, 340)
    assert info["prompt_version"] == judge.prompts[0].version and info["error"] is None

    # A different judge later changes nothing until someone asks for a regrade.
    other = answering(call)
    assert call.debrief().body == first and other.prompts == []
    regraded = call.post("/debrief/judge", {"regrade": True})
    assert len(other.prompts) == 1 and regraded["judge"]["attempt"] == 2
    assert next(c for c in regraded["criteria"] if c["criterion_id"] == "disc-doctors")[
        "status"
    ] == ("not_observed")


def test_a_judge_outage_leaves_the_call_incomplete(client: TestClient) -> None:
    call = enrolled(client)
    call.service.judge = FakeJudge(JudgeError("fake:judge-1 could not be reached"))
    failed = call.debrief()
    assert failed.body["state"] == "incomplete"
    assert failed.body["judge"]["state"] == "failed"
    assert failed.body["judge"]["error"] == "fake:judge-1 could not be reached"
    assert "did not answer" in failed["disc-doctors"]["detail"]
    assert "fail" not in failed.statuses().values()
    assert failed.body["score"]["possible"] == 42

    # Nothing was stored, so the next request asks again.
    answering(call)
    assert call.debrief().body["judge"]["state"] == "graded"

    # A failed regrade keeps the stored judgment and says what went wrong.
    call.service.judge = FakeJudge(JudgeError("rate limited"))
    kept = call.post("/debrief/judge", {"regrade": True})
    assert (kept["judge"]["state"], kept["judge"]["attempt"]) == ("graded", 1)
    assert kept["judge"]["error"] == "rate limited" and kept["state"] == "complete"


def test_a_judge_that_crashes_still_leaves_a_debrief(client: TestClient) -> None:
    call = enrolled(client)

    def crash(prompt: JudgePrompt) -> JudgeOutput:
        raise RuntimeError("stream broke")

    call.service.judge = FakeJudge(crash)
    body = call.debrief().body
    assert body["judge"]["state"] == "failed" and body["state"] == "incomplete"
    assert body["judge"]["error"] == "fake:judge-1 failed: RuntimeError: stream broke"
    assert call.debrief(judge=False).body["judge"]["state"] == "not_run"


@pytest.mark.parametrize("regrade", [False, True])
def test_two_grading_requests_at_once_ask_the_judge_once(client: TestClient, regrade: bool) -> None:
    call = enrolled(client)
    if regrade:
        answering(call)
        call.debrief()
    first = 2 if regrade else 1
    started, release = threading.Event(), threading.Event()

    def slow(prompt: JudgePrompt) -> JudgeOutput:
        started.set()
        assert release.wait(5)
        return JudgeOutput(verdicts=[], coverage_claims=[])

    judge = FakeJudge(slow)
    call.service.judge = judge
    results: list[int] = []

    def ask() -> None:
        resp = client.post(f"{call.base}/debrief/judge", json={"regrade": regrade})
        results.append(resp.json()["judge"]["attempt"])

    threads = [threading.Thread(target=ask) for _ in range(2)]
    threads[0].start()
    assert started.wait(5)
    threads[1].start()
    # Both requests are in before the first one finishes: one holds the lock, one waits.
    deadline = time.monotonic() + 5
    while call.service._judge_locks.users(call.sid) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert call.service._judge_locks.users(call.sid) == 2
    release.set()
    for t in threads:
        t.join(5)
    assert results == [first, first] and len(judge.prompts) == 1
    assert call.service._judge_locks.keys() == set()
    # A regrade asked for afterwards is a new ask.
    call.post("/debrief/judge", {"regrade": True})
    assert len(judge.prompts) == 2


def test_a_stored_log_and_judgment_replay_to_the_same_debrief(
    client: TestClient, tmp_path: Path
) -> None:
    call = enrolled(client)
    said = call.utt(CLAIM)
    answering(
        call,
        ruling("doc-pecl", "pass", (call.utt(CHECKLIST), "item by item")),
        claims=(provider_claim(said),),
    )
    live = call.debrief().body

    source = call.service.store
    store = Store(str(tmp_path / "replay.db"))
    row = source.get_session(call.sid)
    judgment = source.latest_judgment(call.sid)
    assert row is not None and judgment is not None
    store.put_session(row)

    def stored(utt: Utterance) -> Callable[[str], Utterance]:
        return lambda _: utt

    by_id = {u.utt_id: u for u in source.utterances(call.sid)}
    for e in source.events(call.sid):
        if e.utt_id is None:
            store.append_event(call.sid, e.type, at=e.at, t_ms=e.t_ms, payload=e.payload)
        else:
            store.append_utterance(
                call.sid,
                client_utt_id=f"replay-{e.utt_id}",
                at=e.at,
                build=stored(by_id[e.utt_id]),
            )
    store.add_judgment(call.sid, lambda attempt: judgment)
    store.close()

    # A fresh process with no judge at all grades the stored call the same way.
    replayed = SessionService(Store(str(tmp_path / "replay.db")), load_bundle(), clock=StepClock())
    assert replayed.debrief(call.sid).model_dump(mode="json") == live


# Retry


def test_a_retry_starts_the_same_case_with_what_to_work_on(client: TestClient) -> None:
    first = a_call(client)
    first.say("Good news, Dr. Raman is in network on Maple Choice PPO.")
    first.decide("keep_current")
    first.end()
    answering(first, claims=(provider_claim(first.utt("Good news")),))
    focus = first.debrief().body["retry_focus"]
    assert [f["criterion_id"] for f in focus][0] == "ver-claims"  # the critical one leads
    assert len(focus) == 3 and all(f["why"] for f in focus)

    body = {"scenario_id": CASE_A, "retry_of": first.sid}
    retry = client.post("/api/sessions", json=body).json()
    assert retry["retry_of"] == first.sid and retry["status"] == "created"
    assert retry["retry_focus"] == [f["title"] for f in focus]
    # The focus was fixed at creation: regrading the first call later doesn't change it.
    answering(first)
    first.post("/debrief/judge", {"regrade": True})
    again = client.get(f"/api/sessions/{retry['session_id']}").json()
    assert again["retry_focus"] == retry["retry_focus"]

    plain = client.post("/api/sessions", json={"scenario_id": CASE_A}).json()
    assert plain["retry_of"] is None and plain["retry_focus"] == []


def test_a_retry_needs_an_ended_call_on_the_same_case(client: TestClient) -> None:
    live = a_call(client)

    def retry(scenario_id: str, of: str) -> Any:
        return client.post("/api/sessions", json={"scenario_id": scenario_id, "retry_of": of})

    assert retry(CASE_A, live.sid).status_code == 409
    live.end()
    assert retry(CASE_B, live.sid).status_code == 422
    assert retry(CASE_A, "s-nope").status_code == 404
    assert retry(CASE_A, live.sid).status_code == 201


# The debrief around the scorecard


def test_the_debrief_links_findings_to_a_readable_timeline(client: TestClient) -> None:
    call = enrolled(client)
    body = call.debrief().body
    by_seq = {t["seq"]: t["summary"] for t in body["timeline"]}
    events = call.get("/events")
    assert [t["seq"] for t in body["timeline"]] == [e["seq"] for e in events]
    spoken = {t["utt_id"] for t in body["timeline"] if t["type"] == "utterance"}
    assert spoken == {u["utt_id"] for u in call.get("/transcript")}
    assert all(t["summary"] for t in body["timeline"] if t["type"] != "utterance")
    cited = {s for c in body["criteria"] for s in c["event_seqs"]}
    assert cited and cited <= set(by_seq)
    lookup = body["criteria"][11]
    assert lookup["criterion_id"] == "ver-provider"
    assert by_seq[lookup["event_seqs"][0]] == (
        "Checked Priya Raman at Alder Heart Associates, Northside office on Maple Choice PPO "
        "for 2027: in network."
    )
    assert body["versions"] == {
        "scenario_version": 2,
        "catalog_version": "2027-v1",
        "ruleset_version": "mvp-2027.1",
        "rubric": "mvp v2",
    }


def test_case_notes_show_the_lesson_without_the_canaries(client: TestClient) -> None:
    call = enrolled(client)
    body = call.debrief().body
    scenario = load_bundle().scenario(CASE_A)
    assert body["case"]["title"] == scenario.title
    text = str(body["case"])
    assert "Lakeview" in text and "prior authorization" in text
    for canary in scenario.leak_canaries:
        assert canary not in call.c.get(f"{call.base}/debrief").text
    acceptable = [o for o in body["case"]["outcomes"] if o["acceptable"]]
    assert ["Maple Choice PPO"] in [o["plans"] for o in acceptable]
    assert {o["kind"] for o in acceptable} == {"enroll", "defer"}
