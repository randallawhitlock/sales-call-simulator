"""Golden paths: scripted calls per case with the exact findings each must produce.

Every deterministic check appears in every expectation, so a change that flips any finding on
any path fails here. Customer turns are scripted; the learner acts through the API.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.engine.events import EventType
from tests.harness import Call

P, F, NA, NO, UG = "pass", "fail", "not_applicable", "not_observed", "ungraded"

CASE_A = "scenario-101"  # must-keep specialist
CASE_B = "scenario-102"  # the ad promised money
CASE_C = "scenario-103"  # retiree coverage

MAPLE_VALUE, MAPLE_CHOICE = "TRN-1001-001", "TRN-1001-002"
RIVERBEND_ESSENTIALS, RIVERBEND_SELECT = "TRN-2002-001", "TRN-2002-002"
SUMMIT_CLASSIC, SUMMIT_REWARDS = "TRN-3003-001", "TRN-3003-002"

A_DRUGS = ["apixaban-5mg-tab", "metoprolol-succ-er-50mg-tab", "atorvastatin-40mg-tab"]
B_DRUGS = ["lisinopril-20mg-tab", "rosuvastatin-10mg-tab", "amlodipine-5mg-tab"]
C_DRUGS = ["metformin-er-500mg-tab", "sitagliptin-100mg-tab", "levothyroxine-75mcg-tab"]

ENROLLED_CLEAN: dict[str, str] = {
    "outcome_acceptable": P,
    "provider_verified_at_location": P,
    "drugs_checked": P,
    "restriction_seen": NA,
    "pharmacy_checked": P,
    "needs_match_truth": P,
    "evidence_context_matches": P,
    "sob_for_submitted_plan": P,
    "pecl_complete_before_submit": P,
    "consent_before_submit": P,
    "application_matches_record": P,
    "no_enroll_with_unresolved_other_coverage": NA,
    "followup_created": NA,
    "coverage_claims_backed": UG,
    "scope_before_plan_discussion": P,
    "disclaimer_before_benefits": P,
    "recording_notice_given": P,
    "pcp_matches_plan_rule": P,
}
NO_ENROLLMENT = {
    "sob_for_submitted_plan": NA,
    "pecl_complete_before_submit": NA,
    "consent_before_submit": NA,
    "application_matches_record": NA,
    "pcp_matches_plan_rule": NA,
}


def expected(
    base: dict[str, str], critical: tuple[str, ...] = (), **overrides: str
) -> dict[str, tuple[str, bool]]:
    statuses = base | overrides
    assert set(statuses) == set(ENROLLED_CLEAN)
    return {k: (v, k in critical) for k, v in statuses.items()}


# Case A


A_DOCTORS: list[dict[str, Any]] = [
    {
        "name": "Dr. Priya Raman",
        "role": "specialist",
        "practice_id": "raman-northside",
        "must_keep": True,
    },
    {"name": "Dr. Thomas Whitfield", "role": "pcp", "practice_id": "whitfield-alder"},
]
A_COVERAGE: dict[str, Any] = {"current_plan_id": SUMMIT_CLASSIC, "medicare_parts": ["a", "b"]}


def a_call(client: TestClient, *, atorvastatin: str = "atorvastatin-40mg-tab") -> Call:
    call = Call(client, CASE_A).start().opening()
    call.hear("My heart doctor's group is leaving my plan next year.")
    call.record(
        zip_code="00041",
        doctors=A_DOCTORS,
        drugs=[A_DRUGS[0], A_DRUGS[1], atorvastatin],
        pharmacy_id="alder-family-rx",
        coverage=A_COVERAGE,
    )
    return call


def test_a_right_plan_ppo(client: TestClient) -> None:
    call = a_call(client)
    call.verify(MAPLE_CHOICE, ["raman-northside"], A_DRUGS, "alder-family-rx")
    call.say("Maple Choice PPO's premium is $39 a month, and you keep paying your Part B premium.")
    call.decide("enroll", MAPLE_CHOICE)
    call.enroll(MAPLE_CHOICE, "Loretta Banks", "00041")
    call.end()
    assert call.findings() == expected(ENROLLED_CLEAN)


def test_a_right_plan_hmo_with_restriction_seen(client: TestClient) -> None:
    call = a_call(client)
    call.verify(MAPLE_VALUE, ["raman-northside"], A_DRUGS, "alder-family-rx")
    call.say("Apixaban needs prior authorization on this plan. Here is what that means for you.")
    call.decide("enroll", MAPLE_VALUE)
    call.enroll(MAPLE_VALUE, "Loretta Banks", "00041", pcp="whitfield-alder")
    call.end()
    assert call.findings() == expected(ENROLLED_CLEAN, restriction_seen=P)


def test_a_prior_auth_drug_never_looked_up(client: TestClient) -> None:
    call = a_call(client)
    call.verify(MAPLE_VALUE, ["raman-northside"], A_DRUGS[1:], "alder-family-rx")
    call.decide("enroll", MAPLE_VALUE)
    call.enroll(MAPLE_VALUE, "Loretta Banks", "00041", pcp="whitfield-alder")
    call.end()
    assert call.findings() == expected(
        ENROLLED_CLEAN, outcome_acceptable=F, drugs_checked=F, restriction_seen=F
    )
    assert "restriction_seen" in call.detail("outcome_acceptable")["detail"]


def test_a_same_doctor_at_the_wrong_office(client: TestClient) -> None:
    call = a_call(client)
    call.verify(RIVERBEND_ESSENTIALS, ["raman-lakeview"], A_DRUGS, "alder-family-rx")
    call.decide("enroll", RIVERBEND_ESSENTIALS)
    call.enroll(RIVERBEND_ESSENTIALS, "Loretta Banks", "00041", pcp="whitfield-alder")
    call.end()
    assert call.findings() == expected(
        ENROLLED_CLEAN,
        critical=("outcome_acceptable",),
        outcome_acceptable=F,
        provider_verified_at_location=F,
        evidence_context_matches=F,
    )
    assert "Lakeview" in call.detail("evidence_context_matches")["detail"]


def test_a_last_years_network(client: TestClient) -> None:
    call = a_call(client)
    call.pin(call.network("raman-northside", SUMMIT_CLASSIC, year=2026))
    call.pin(call.document("dir-summit-classic-2026"))
    for drug_id in A_DRUGS:
        call.formulary(SUMMIT_CLASSIC, drug_id)
    call.pharmacy(SUMMIT_CLASSIC, "alder-family-rx")
    call.say("Good news, Dr. Raman is still in network, so you can keep your plan.")
    call.decide("keep_current")
    call.end()
    assert call.findings() == expected(
        ENROLLED_CLEAN | NO_ENROLLMENT,
        outcome_acceptable=F,
        provider_verified_at_location=F,
        evidence_context_matches=F,
    )
    detail = call.detail("provider_verified_at_location")["detail"]
    assert "never checked for 2027" in detail


def test_a_wrong_strength(client: TestClient) -> None:
    call = a_call(client, atorvastatin="atorvastatin-20mg-tab")
    wrong = [A_DRUGS[0], A_DRUGS[1], "atorvastatin-20mg-tab"]
    call.verify(MAPLE_CHOICE, ["raman-northside"], wrong, "alder-family-rx")
    call.decide("enroll", MAPLE_CHOICE)
    call.enroll(MAPLE_CHOICE, "Loretta Banks", "00041")
    call.end()
    assert call.findings() == expected(
        ENROLLED_CLEAN, outcome_acceptable=F, drugs_checked=F, needs_match_truth=F
    )
    assert "atorvastatin 40 mg" in call.detail("drugs_checked")["detail"]


def test_a_no_checklist(client: TestClient) -> None:
    call = a_call(client)
    call.verify(MAPLE_CHOICE, ["raman-northside"], A_DRUGS, "alder-family-rx")
    call.decide("enroll", MAPLE_CHOICE)
    call.document("sob-maple-choice-2027")
    app_id = call.apply(MAPLE_CHOICE, "Loretta Banks", "00041")
    yes = call.hear("Yes, sign me up.")
    call.consent(app_id, yes)
    call.submit(app_id)
    call.end()
    assert call.findings() == expected(
        ENROLLED_CLEAN,
        critical=("pecl_complete_before_submit",),
        pecl_complete_before_submit=F,
    )


def test_a_double_submit_creates_one_application(client: TestClient) -> None:
    call = a_call(client)
    call.verify(MAPLE_CHOICE, ["raman-northside"], A_DRUGS, "alder-family-rx")
    call.decide("enroll", MAPLE_CHOICE)
    app_id = call.enroll(MAPLE_CHOICE, "Loretta Banks", "00041")
    again = call.submit(app_id)
    assert again["application"]["status"] == "submitted"
    call.submit(app_id, key="another-key-002", expect=409)
    submits = [e for e in call.get("/events") if e["type"] == "application_submitted"]
    assert len(submits) == 1
    assert len(call.get("/applications")) == 1
    call.end()
    assert call.findings() == expected(ENROLLED_CLEAN)


def test_a_record_filled_in_after_submission(client: TestClient) -> None:
    # The record is graded as it stood at submission, not as it was tidied up afterward.
    call = Call(client, CASE_A).start().opening()
    call.hear("My heart doctor's group is leaving my plan next year.")
    call.verify(MAPLE_CHOICE, ["raman-northside"], A_DRUGS, "alder-family-rx")
    call.decide("enroll", MAPLE_CHOICE)
    call.enroll(MAPLE_CHOICE, "Loretta Banks", "00041")
    call.record(
        zip_code="00041",
        doctors=A_DOCTORS,
        drugs=A_DRUGS,
        pharmacy_id="alder-family-rx",
        coverage=A_COVERAGE,
    )
    call.end()
    assert call.findings() == expected(
        ENROLLED_CLEAN, needs_match_truth=F, application_matches_record=F
    )
    assert "Residence ZIP" in call.detail("application_matches_record")["detail"]


def test_a_withdrawn_submission_is_still_audited(client: TestClient) -> None:
    call = a_call(client)
    call.verify(MAPLE_CHOICE, ["raman-northside"], A_DRUGS, "alder-family-rx")
    call.decide("enroll", MAPLE_CHOICE)
    call.document("sob-maple-choice-2027")
    first = call.apply(MAPLE_CHOICE, "Loretta Banks", "00041")
    call.review_checklist(first)
    call.submit(first, key="first-key-0001")  # no consent
    call.post(f"/applications/{first}/withdraw")
    call.enroll(MAPLE_CHOICE, "Loretta Banks", "00041")
    call.end()
    assert call.findings() == expected(
        ENROLLED_CLEAN, critical=("consent_before_submit",), consent_before_submit=F
    )
    detail = call.detail("consent_before_submit")["detail"]
    assert detail.startswith(f"Application {first} (later withdrawn): No consent")


def test_a_two_standing_submissions_enroll_twice(client: TestClient) -> None:
    call = a_call(client)
    call.verify(MAPLE_CHOICE, ["raman-northside"], A_DRUGS, "alder-family-rx")
    call.decide("enroll", MAPLE_CHOICE)
    call.enroll(MAPLE_CHOICE, "Loretta Banks", "00041")
    call.enroll(MAPLE_CHOICE, "Loretta Banks", "00041")
    call.end()
    assert call.findings() == expected(
        ENROLLED_CLEAN, critical=("outcome_acceptable",), outcome_acceptable=F
    )
    assert "enrolls the customer twice" in call.detail("outcome_acceptable")["detail"]


def test_a_benefits_before_disclaimer(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    call.say("Thanks for calling. This call is recorded. Our plans have great dental benefits.")
    call.say(
        "We do not offer every plan available in your area. We represent 3 organizations which "
        "offer 6 products. Please contact Medicare.gov or 1-800-MEDICARE for all your options."
    )
    call.end()
    result = call.detail("disclaimer_before_benefits")
    assert result["status"] == F
    assert "before the disclaimer" in result["detail"]


def test_a_bare_benefit_before_disclaimer_goes_to_the_judge(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    call.say("Thanks for calling. This call is recorded. The main benefit of calling us is choice.")
    call.say(
        "We do not offer every plan available in your area. We represent 3 organizations which "
        "offer 6 products. Please contact Medicare.gov or 1-800-MEDICARE for all your options."
    )
    call.end()
    result = call.detail("disclaimer_before_benefits")
    assert (result["status"], result["critical"]) == (UG, False)
    assert "needs the judge" in result["detail"].lower()


def test_a_disclaimer_with_wrong_numbers(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    call.say(
        "This call is recorded. We do not offer every plan available in your area. We represent "
        "4 organizations which offer 6 products. Contact Medicare.gov for all your options."
    )
    call.end()
    result = call.detail("disclaimer_before_benefits")
    assert result["status"] == F
    assert "3 organizations" in result["detail"]


def test_a_scope_after_research(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    call.say(GREETING_WITH_DISCLAIMER)
    call.network("raman-northside", MAPLE_CHOICE)
    call.post("/scope", {"products": ["ma"]}, expect=201)
    call.end()
    assert call.detail("scope_before_plan_discussion")["status"] == F


GREETING_WITH_DISCLAIMER = (
    "Thanks for calling, this call is recorded. We do not offer every plan available in your "
    "area. Currently we represent three organizations which offer six products in your area. "
    "Please contact Medicare.gov or 1-800-MEDICARE to get information on all of your options."
)


def enroll_to_consent(client: TestClient) -> tuple[Call, str]:
    call = a_call(client)
    call.verify(MAPLE_CHOICE, ["raman-northside"], A_DRUGS, "alder-family-rx")
    call.decide("enroll", MAPLE_CHOICE)
    call.document("sob-maple-choice-2027")
    app_id = call.apply(MAPLE_CHOICE, "Loretta Banks", "00041")
    call.review_checklist(app_id)
    return call, app_id


@pytest.mark.parametrize(
    ("script", "reason"),
    [
        ("learner", "did not say"),
        ("interrupted", "cut off"),
        ("refusal", "not a clear yes"),
        ("objection", "objected after"),
    ],
)
def test_a_consent_must_be_a_heard_customer_yes(
    client: TestClient, script: str, reason: str
) -> None:
    call, app_id = enroll_to_consent(client)
    if script == "learner":
        utt = call.say("Yes, I'll enroll you now.")
        # The API refuses a learner line as consent; the grader still catches one that gets
        # into a log some other way.
        body = call.post(f"/applications/{app_id}/consent", {"utt_id": utt}, expect=422)
        assert "not the customer's" in body["detail"]
        call.service.log(
            call.sid, EventType.CONSENT_MARKED, {"application_id": app_id, "utt_id": utt}
        )
    elif script == "interrupted":
        utt = call.hear("Yes, go ahead and sign me up.", heard="Yes, go", interrupted=True)
    elif script == "refusal":
        utt = call.hear("No, not yet. I want to think about it.")
    else:
        utt = call.hear("Yes, sign me up.")
    if script != "learner":
        call.consent(app_id, utt)
    if script == "objection":
        call.hear("Wait, hold on. I want to ask my daughter first.")
    call.submit(app_id)
    call.end()
    result = call.detail("consent_before_submit")
    assert (result["status"], result["critical"]) == (F, True)
    assert reason in result["detail"]


@pytest.mark.parametrize(
    ("yes", "after", "status"),
    [
        ("Yes, I don't have any questions. Go ahead.", None, P),
        ("Yeah, no problem, sign me up.", None, P),
        ("Okay, not a problem, go ahead.", None, P),
        ("Yes, sign me up.", "No, that's everything.", P),
        ("Okay, sure, why not.", None, UG),
        ("Yes, sign me up.", "No.", UG),
        ("Yes, but don't submit it yet.", None, F),
    ],
)
def test_a_consent_wording(client: TestClient, yes: str, after: str | None, status: str) -> None:
    call, app_id = enroll_to_consent(client)
    call.say("Would you like me to submit your enrollment in this plan?")
    utt = call.hear(yes)
    call.consent(app_id, utt)
    if after is not None:
        call.say("Anything else before I submit?")
        call.hear(after)
    call.submit(app_id)
    call.end()
    result = call.detail("consent_before_submit")
    assert (result["status"], result["critical"]) == (status, status == F)


def test_a_application_changed_after_consent(client: TestClient) -> None:
    # The API refuses this edit; the grader still catches it in a log that has one.
    call, app_id = enroll_to_consent(client)
    yes = call.hear("Yes, sign me up.")
    call.consent(app_id, yes)
    fields = call.get("/applications")[0]["application"]["fields"]
    call.service.log(
        call.sid,
        EventType.APPLICATION_UPDATED,
        {"application_id": app_id, "fields": fields | {"pcp_practice_id": "whitfield-alder"}},
    )
    call.submit(app_id)
    call.end()
    consent = call.detail("consent_before_submit")
    assert (consent["status"], consent["critical"]) == (F, True)
    assert "changed after the customer said yes" in consent["detail"]
    pecl = call.detail("pecl_complete_before_submit")
    assert (pecl["status"], pecl["critical"]) == (F, True)
    assert "after the application last changed" in pecl["detail"]


def test_a_consent_before_checklist_finished(client: TestClient) -> None:
    call = a_call(client)
    call.verify(MAPLE_CHOICE, ["raman-northside"], A_DRUGS, "alder-family-rx")
    call.decide("enroll", MAPLE_CHOICE)
    app_id = call.apply(MAPLE_CHOICE, "Loretta Banks", "00041")
    yes = call.hear("Yes, sign me up.")
    call.review_checklist(app_id)
    call.consent(app_id, yes)
    call.submit(app_id)
    call.end()
    assert "before the checklist" in call.detail("consent_before_submit")["detail"]


# Case B


def b_call(client: TestClient) -> Call:
    call = Call(client, CASE_B).start().opening()
    call.hear("I saw an ad that says everyone gets $150 a month for groceries.")
    call.record(
        zip_code="00044",
        doctors=[
            {
                "name": "Dr. Samuel Okafor",
                "role": "pcp",
                "practice_id": "okafor-cedar",
                "must_keep": True,
            }
        ],
        drugs=B_DRUGS,
        days_supply=90,
        pharmacy_id="cedar-street-rx",
        coverage={"current_plan_id": RIVERBEND_ESSENTIALS, "medicare_parts": ["a", "b"]},
    )
    return call


def test_b_keep_the_plan_that_fits(client: TestClient) -> None:
    call = b_call(client)
    call.verify(RIVERBEND_ESSENTIALS, ["okafor-cedar"], B_DRUGS, "cedar-street-rx")
    call.say("That allowance only goes to members with certain conditions, which you don't have.")
    call.decide("keep_current", rationale="Current plan keeps his doctor, drugs and pharmacy.")
    call.end()
    assert call.findings() == expected(ENROLLED_CLEAN | NO_ENROLLMENT)


def test_b_the_ad_plan(client: TestClient) -> None:
    call = b_call(client)
    call.verify(SUMMIT_REWARDS, ["okafor-cedar"], B_DRUGS, "cedar-street-rx")
    call.decide("enroll", SUMMIT_REWARDS)
    call.enroll(SUMMIT_REWARDS, "Harold Pruitt", "00044", pcp="okafor-cedar")
    call.end()
    assert call.findings() == expected(
        ENROLLED_CLEAN,
        critical=("outcome_acceptable",),
        outcome_acceptable=F,
        provider_verified_at_location=F,
        evidence_context_matches=F,
        pcp_matches_plan_rule=F,
    )
    assert "out of network" in call.detail("provider_verified_at_location")["detail"]


def test_b_a_real_gain_with_losses_stated(client: TestClient) -> None:
    call = b_call(client)
    call.verify(MAPLE_VALUE, ["okafor-cedar"], B_DRUGS, "cedar-street-rx")
    call.say(
        "You'd gain a $50 quarterly allowance and more dental, but Cedar Street becomes a "
        "standard pharmacy, so your copays go up, and specialist visits cost $5 more."
    )
    call.decide("enroll", MAPLE_VALUE)
    call.enroll(MAPLE_VALUE, "Harold Pruitt", "00044", pcp="okafor-cedar")
    call.end()
    assert call.findings() == expected(ENROLLED_CLEAN)


# Case C

C_COVERAGE: dict[str, Any] = {
    "original_medicare": True,
    "medicare_parts": ["a", "b"],
    "other_coverage": [
        {
            "kind": "employer_retiree",
            "includes_drug_coverage": True,
            "covers_spouse": True,
            "administrator": "Brightwater Retiree Benefits Center",
        }
    ],
}
NO_PLAN_DECISION = {
    "provider_verified_at_location": NA,
    "drugs_checked": NA,
    "restriction_seen": NA,
    "pharmacy_checked": NA,
    "evidence_context_matches": NA,
    "no_enroll_with_unresolved_other_coverage": P,
}


def c_call(client: TestClient) -> Call:
    call = Call(client, CASE_C).start().opening()
    call.hear("Can you just sign me up for the plan my neighbor has?")
    call.record(
        zip_code="00042",
        doctors=[{"name": "Dr. Michael Torres", "role": "pcp", "practice_id": "torres-millbrook"}],
        drugs=C_DRUGS,
        pharmacy_id="valuemart-rx",
        coverage=C_COVERAGE,
    )
    return call


def test_c_defer_and_follow_up(client: TestClient) -> None:
    call = c_call(client)
    call.say("Joining could end your retiree drug coverage, and Joan's. Call Brightwater first.")
    call.post("/followups", {"due_date": "2026-12-03", "reason": "Hear what Brightwater said"}, 201)
    call.decide("defer", rationale="Retiree coverage impact unconfirmed.")
    call.end()
    assert call.findings() == expected(
        ENROLLED_CLEAN | NO_ENROLLMENT | NO_PLAN_DECISION, followup_created=P
    )


def test_c_signs_him_up_under_pressure(client: TestClient) -> None:
    call = c_call(client)
    call.verify(SUMMIT_CLASSIC, [], C_DRUGS, "valuemart-rx")
    call.decide("enroll", SUMMIT_CLASSIC)
    call.enroll(SUMMIT_CLASSIC, "Raymond Ellis", "00042", pcp="torres-millbrook")
    call.end()
    assert call.findings() == expected(
        ENROLLED_CLEAN,
        critical=("outcome_acceptable",),
        outcome_acceptable=F,
        provider_verified_at_location=NA,
        no_enroll_with_unresolved_other_coverage=F,
        evidence_context_matches=NO,
    )


def test_c_defer_without_follow_up(client: TestClient) -> None:
    call = c_call(client)
    call.decide("defer")
    call.end()
    assert call.findings() == expected(
        ENROLLED_CLEAN | NO_ENROLLMENT | NO_PLAN_DECISION,
        outcome_acceptable=F,
        followup_created=F,
    )


def test_c_follow_up_after_the_deadline(client: TestClient) -> None:
    call = c_call(client)
    call.post("/followups", {"due_date": "2026-12-10", "reason": "Check back"}, 201)
    call.decide("defer")
    call.end()
    assert call.detail("followup_created")["status"] == F
    assert "2026-12-07" in call.detail("followup_created")["detail"]
