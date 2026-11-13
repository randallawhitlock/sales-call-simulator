"""The judge eval set: scripted calls with the rulings a careful human grader gives.

Each case scripts a whole call through the learner API and labels the criteria where the right
ruling is not in doubt, with the lines that show it. Criteria left unlabeled are ones a
reasonable grader could rule either way, so they are not compared.

Two things run against this set. Every build checks the labels themselves: each labeled
criterion is one the judge is actually asked, each cited line exists, and a judge that returned
exactly the labels would produce the expected findings (``test_judge_evals.py``). ``pytest -m
live`` then asks the real judge and reports where it disagrees with the labels.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi.testclient import TestClient

from app.judge.contract import NOTES_REF
from tests.harness import Call
from tests.test_goldens import (
    A_COVERAGE,
    A_DOCTORS,
    A_DRUGS,
    B_DRUGS,
    C_COVERAGE,
    C_DRUGS,
    CASE_A,
    CASE_B,
    CASE_C,
    MAPLE_CHOICE,
    MAPLE_VALUE,
    RIVERBEND_ESSENTIALS,
    SUMMIT_CLASSIC,
    SUMMIT_REWARDS,
)

SCOPE_ASK = "scope_ask"
"""The scope question in every opening."""


@dataclass(frozen=True)
class Label:
    verdict: str
    cites: tuple[str, ...] = ()
    """Keys of the lines that show it. ``notes:<quote>`` cites the saved notes."""


@dataclass(frozen=True)
class ExpectedClaim:
    line: str
    kind: str
    plan_id: str
    subject_id: str
    covered: bool = True


@dataclass
class Labeled:
    call: Call
    lines: dict[str, str]
    """Line key -> utterance id."""
    labels: dict[str, Label]
    claims: tuple[ExpectedClaim, ...] = ()
    final: dict[str, tuple[str, bool]] = field(default_factory=dict)
    """Criterion -> the (status, critical) the debrief should show when the judge's rulings
    match the labels. Includes the criteria whose result the server works out itself."""

    def evidence(self, label: Label) -> list[tuple[str, str]]:
        """(utt_id, quote) pairs for a label, quoting each cited line whole."""
        out = []
        for key in label.cites:
            if key.startswith("notes:"):
                out.append((NOTES_REF, key.removeprefix("notes:")))
            else:
                utt_id = self.lines[key]
                heard = next(
                    u.heard_text
                    for u in self.call.service.transcript(self.call.sid)
                    if u.utt_id == utt_id
                )
                out.append((utt_id, heard))
        return out


class Script:
    """A call being scripted, remembering the id of each line that gets a key."""

    def __init__(self, client: TestClient, scenario_id: str) -> None:
        self.call = Call(client, scenario_id).start().opening()
        self.lines = {SCOPE_ASK: self.call.utt("Which kinds of coverage would you like")}

    def agent(self, text: str, key: str | None = None) -> str:
        utt_id = self.call.say(text)
        if key:
            self.lines[key] = utt_id
        return utt_id

    def customer(self, text: str, key: str | None = None) -> str:
        utt_id = self.call.hear(text)
        if key:
            self.lines[key] = utt_id
        return utt_id

    def labeled(
        self,
        labels: dict[str, Label],
        final: dict[str, tuple[str, bool]],
        claims: tuple[ExpectedClaim, ...] = (),
    ) -> Labeled:
        self.call.end()
        return Labeled(self.call, self.lines, labels, claims, final)


P, F, NO = "pass", "fail", "not_observed"


def thorough_enrollment(client: TestClient) -> Labeled:
    """Case A done well: needs gathered, a misremembered strength caught, everything looked up
    before it was claimed, tradeoffs stated, the checklist gone through."""
    s = Script(client, CASE_A)
    call = s.call
    s.customer("I got a letter saying my heart doctor's group won't be with my plan next year.")
    s.agent(
        "I'm sorry, that's unsettling. Which doctors do you see, which ones can't you lose, "
        "and which office do you go to?",
        "ask_doctors",
    )
    s.customer(
        "My heart doctor is Dr. Priya Raman. I see her at the Alder Heart Associates office on "
        "Birch Avenue in Northside. I'm not switching heart doctors."
    )
    s.agent(
        "Understood. What prescriptions do you take, and what strength is each one?", "ask_drugs"
    )
    s.customer(
        "Apixaban five milligrams twice a day, metoprolol extended-release 50 milligrams, and "
        "atorvastatin. Twenty milligrams, I think."
    )
    s.agent(
        "Could you check the atorvastatin bottle for me? The strength changes what I look up.",
        "check_bottle",
    )
    s.customer("Oh, hold on, the bottle says 40 milligrams. I had that mixed up.")
    s.agent("Good catch, 40 it is. Which pharmacy do you use?", "ask_pharmacy")
    s.customer("I get everything at Alder Family Pharmacy in the Northside Plaza.")
    s.agent(
        "Besides your current plan, do you have any other coverage, like a retiree plan, the VA "
        "or Medicaid?",
        "ask_coverage",
    )
    s.customer("I have Summit Classic HMO right now. No other insurance, nothing from the VA.")
    call.record(
        zip_code="00041",
        doctors=A_DOCTORS,
        drugs=A_DRUGS,
        pharmacy_id="alder-family-rx",
        coverage=A_COVERAGE,
    )
    call.verify(MAPLE_CHOICE, ["raman-northside"], A_DRUGS, "alder-family-rx")
    s.agent(
        "I checked Maple Choice PPO for 2027. Dr. Raman at the Northside office is in network, "
        "your apixaban 5 milligram, metoprolol and atorvastatin 40 milligram are all covered, "
        "and Alder Family Pharmacy is in network.",
        "claims",
    )
    s.agent(
        "Compared with your Summit plan, you would keep Dr. Raman, which Summit can't offer next "
        "year. What you give up is the zero premium: Maple Choice is $39 a month, and you keep "
        "paying your Part B premium as well.",
        "tradeoffs",
    )
    s.customer("Thirty-nine dollars I can manage to keep Dr. Raman.")
    call.save(
        "notes",
        {
            "reason_for_inquiry": "Plan letter: her cardiologist's group leaves Summit in 2027",
            "findings": "Maple Choice PPO has Dr. Raman at Northside, her drugs and pharmacy",
            "rationale": "Keeps Dr. Raman for $39 a month, which fits her budget",
            "next_steps": "Enrollment submitted for January 1",
        },
    )
    call.decide("enroll", MAPLE_CHOICE)
    call.document("sob-maple-choice-2027")
    app_id = call.apply(MAPLE_CHOICE, "Loretta Banks", "00041")
    s.agent(
        "Before we enroll, let's read the Pre-Enrollment Checklist together. Check that your "
        "doctors are in the network at the offices you use, which we did for Dr. Raman at "
        "Northside. Check your pharmacy and each prescription at your strength, which we also "
        "did. The premium is $39 and you keep paying Part B. Benefits and costs can change each "
        "January. Out-of-network care costs more. You don't need referrals on this plan. And "
        "coverage starts January 1, 2027, when your Summit plan ends. Any questions on those?",
        "checklist",
    )
    s.customer("That all makes sense to me.")
    call.review_checklist(app_id)
    s.agent("Would you like me to submit your enrollment in Maple Choice PPO?")
    call.consent(app_id, s.customer("Yes, please go ahead and sign me up."))
    call.submit(app_id)
    on_plan = ("claims", "formulary", MAPLE_CHOICE)
    return s.labeled(
        labels={
            "disc-doctors": Label(P, ("ask_doctors",)),
            "disc-drugs": Label(P, ("ask_drugs", "check_bottle")),
            "disc-pharmacy": Label(P, ("ask_pharmacy",)),
            "disc-coverage": Label(P, ("ask_coverage",)),
            "elig-scope": Label(P, (SCOPE_ASK,)),
            "rec-tradeoffs": Label(P, ("tradeoffs",)),
            "rec-premiums": Label(P, ("tradeoffs",)),
            "doc-pecl": Label(P, ("checklist",)),
            "com-misconceptions": Label(P, ("check_bottle",)),
            "comp-notes": Label(P, ("notes:Keeps Dr. Raman for $39 a month",)),
        },
        claims=(
            ExpectedClaim("claims", "provider_network", MAPLE_CHOICE, "raman-northside"),
            *(ExpectedClaim(*on_plan, drug_id) for drug_id in A_DRUGS),
            ExpectedClaim("claims", "pharmacy_network", MAPLE_CHOICE, "alder-family-rx"),
        ),
        final={
            "doc-pecl": (P, False),
            "doc-consent": (P, False),
            "ver-claims": (P, False),
            "elig-scope": (P, False),
            "rec-outcome": (P, False),
            "comp-notes": (P, False),
        },
    )


def claims_before_lookups(client: TestClient) -> Labeled:
    """Case A done badly: coverage asserted before anything was looked up, pressure, and a
    checklist ticked without a word of it read."""
    s = Script(client, CASE_A)
    call = s.call
    s.customer("I got a letter saying my heart doctor's group won't be with my plan next year.")
    s.agent(
        "Don't worry about that. Dr. Raman is in network on Maple Value HMO, and your apixaban "
        "is covered there with no strings attached.",
        "early_claims",
    )
    s.customer("Oh, good. Is it expensive?")
    s.agent(
        "It's a zero premium plan, the best plan we have. You need to decide today, though, "
        "this won't last.",
        "pressure",
    )
    s.customer("I'm not deciding anything today if I'm being hurried.")
    s.agent("Okay, okay. Let me just get the application started so you don't lose your spot.")
    call.network("raman-northside", MAPLE_VALUE)  # only after saying so
    call.document("sob-maple-value-2027")
    app_id = call.apply(MAPLE_VALUE, "Loretta Banks", "00041", pcp="whitfield-alder")
    s.agent(
        "There's a checklist here, but it's just boilerplate, so I'll tick the boxes for you.",
        "skip_checklist",
    )
    call.review_checklist(app_id)
    s.agent("Would you like me to submit your enrollment in Maple Value HMO?")
    call.consent(app_id, s.customer("Yes, fine, go ahead."))
    call.submit(app_id)
    return s.labeled(
        labels={
            "disc-doctors": Label(NO),
            "disc-drugs": Label(NO),
            "disc-pharmacy": Label(NO),
            "com-choice": Label(F, ("pressure",)),
            "rec-needs-based": Label(F, ("pressure",)),
            "doc-pecl": Label(F, ("skip_checklist",)),
        },
        claims=(
            ExpectedClaim("early_claims", "provider_network", MAPLE_VALUE, "raman-northside"),
            ExpectedClaim("early_claims", "formulary", MAPLE_VALUE, "apixaban-5mg-tab"),
        ),
        final={
            "doc-pecl": (F, True),
            "ver-claims": (F, True),
            "com-choice": (F, False),
            "disc-drugs": (NO, False),
        },
    )


B_DOCTOR = {
    "name": "Dr. Samuel Okafor",
    "role": "pcp",
    "practice_id": "okafor-cedar",
    "must_keep": True,
}


def keep_and_correct(client: TestClient) -> Labeled:
    """Case B done well: the ad's promise checked and corrected, the current plan verified, the
    choice left to the customer."""
    s = Script(client, CASE_B)
    call = s.call
    s.customer("I saw a TV ad that said everybody with Medicare can get $150 a month. I want that.")
    s.agent(
        "I can look into that. First, what plan do you have now, and which doctor do you see?",
        "ask_current",
    )
    s.customer(
        "I'm on Riverbend Essentials HMO. My doctor is Dr. Samuel Okafor at Cedar Street Family "
        "Medicine in Millbrook. I'm not changing doctors."
    )
    s.agent("What medicines do you take, at what strength, and where do you fill them?")
    s.customer(
        "Lisinopril 20 milligrams, rosuvastatin 10 milligrams and amlodipine 5 milligrams, three "
        "months at a time, at Cedar Street Pharmacy."
    )
    s.agent("Do you have diabetes, heart failure or a lung condition like COPD?", "ask_conditions")
    s.customer("Just blood pressure and cholesterol. No diabetes, nothing wrong with my lungs.")
    call.record(
        zip_code="00044",
        doctors=[B_DOCTOR],
        drugs=B_DRUGS,
        days_supply=90,
        pharmacy_id="cedar-street-rx",
        coverage={"current_plan_id": RIVERBEND_ESSENTIALS, "medicare_parts": ["a", "b"]},
    )
    call.get(f"/research/plans/{SUMMIT_REWARDS}")
    call.network("okafor-cedar", SUMMIT_REWARDS)
    call.verify(RIVERBEND_ESSENTIALS, ["okafor-cedar"], B_DRUGS, "cedar-street-rx")
    s.agent(
        "About that ad: the $150 allowance is real, but it isn't for everybody. On Summit "
        "Rewards it only goes to members with diabetes, chronic heart failure or COPD, so you "
        "would not get it.",
        "correct_ad",
    )
    s.agent(
        "Also, Dr. Okafor is not in network on Summit Rewards, so you'd lose him. On your "
        "current Riverbend Essentials plan, Dr. Okafor at Cedar Street is in network for 2027 "
        "and your lisinopril, rosuvastatin and amlodipine are covered.",
        "claims",
    )
    s.customer("Well, I'm not giving up Dr. Okafor for an allowance I can't even get.")
    s.agent(
        "That's your call to make, and keeping what you have is a perfectly good choice. Would "
        "you like to stay with Riverbend Essentials?",
        "choice",
    )
    s.customer("Yes, I'll stay put.")
    call.decide("keep_current", rationale="Current plan keeps his doctor, drugs and pharmacy.")
    on_current = ("claims", "formulary", RIVERBEND_ESSENTIALS)
    return s.labeled(
        labels={
            "disc-doctors": Label(P, ("ask_current",)),
            "disc-health-needs": Label(P, ("ask_conditions",)),
            "com-misconceptions": Label(P, ("correct_ad",)),
            "rec-benefits": Label(P, ("correct_ad",)),
            "com-choice": Label(P, ("choice",)),
        },
        claims=(
            ExpectedClaim("claims", "provider_network", SUMMIT_REWARDS, "okafor-cedar", False),
            ExpectedClaim("claims", "provider_network", RIVERBEND_ESSENTIALS, "okafor-cedar"),
            *(ExpectedClaim(*on_current, drug_id) for drug_id in B_DRUGS),
        ),
        final={
            "ver-claims": (P, False),
            "rec-outcome": (P, False),
            "com-misconceptions": (P, False),
            "doc-pecl": ("not_applicable", False),
        },
    )


def ad_left_standing(client: TestClient) -> Labeled:
    """Case B done badly: the agent repeats the ad's promise and brushes off the customer."""
    s = Script(client, CASE_B)
    s.customer("I saw a TV ad that said everybody with Medicare can get $150 a month. I want that.")
    s.agent(
        "That's right, everybody on Summit Rewards gets $150 a month for groceries. Let's get "
        "you signed up for it.",
        "agrees",
    )
    s.customer("Hold on. You haven't even asked what I've got now.")
    s.agent("It doesn't matter what you have now, this one pays you.", "dismiss")
    return s.labeled(
        labels={
            "com-misconceptions": Label(F, ("agrees",)),
            "rec-benefits": Label(F, ("agrees",)),
            "disc-doctors": Label(NO),
            "disc-drugs": Label(NO),
            "disc-pharmacy": Label(NO),
        },
        final={
            "ver-claims": ("not_applicable", False),
            "com-misconceptions": (F, False),
            "rec-benefits": (F, False),
            "rec-outcome": (NO, False),
        },
    )


def defer_explained(client: TestClient) -> Labeled:
    """Case C done well: other coverage found, the risk explained in plain words, the customer
    sent to his benefits administrator, a follow-up set before the deadline."""
    s = Script(client, CASE_C)
    call = s.call
    s.customer("I don't have all day. Can you just sign me up for the one my neighbor has?")
    s.agent(
        "I can help with that. Before we change anything, what coverage do you have today? "
        "Anything through a former employer or a union?",
        "ask_coverage",
    )
    s.customer(
        "I've got regular Medicare plus my retiree plan from Brightwater Manufacturing. It pays "
        "for my prescriptions."
    )
    s.agent("Is anyone else covered under that retiree plan, like your wife?", "ask_spouse")
    s.customer("My wife Joan is on my retiree plan too. Her prescriptions go through it.")
    s.agent(
        "That matters. Joining a Medicare Advantage plan can end a retiree plan, and that could "
        "leave both you and Joan without your drug coverage. Please call the Brightwater Retiree "
        "Benefits Center and ask what happens to your plan if you join one.",
        "explain_risk",
    )
    s.customer("My neighbor's been on that plan and loves it. Can't we just do it today?")
    s.agent(
        "It's your decision, and I'd rather you make it knowing what you could lose. There's "
        "time: the enrollment period runs through December 7, and anything you pick starts "
        "January 1. I'll call you back once you've talked to Brightwater.",
        "timing",
    )
    s.customer("All right. I'll call them tomorrow.")
    call.record(
        zip_code="00042",
        doctors=[{"name": "Dr. Michael Torres", "role": "pcp", "practice_id": "torres-millbrook"}],
        drugs=C_DRUGS,
        pharmacy_id="valuemart-rx",
        coverage=C_COVERAGE,
    )
    call.post("/followups", {"due_date": "2026-12-03", "reason": "Hear what Brightwater said"}, 201)
    call.save(
        "notes",
        {
            "reason_for_inquiry": "Wants the plan his neighbor has",
            "findings": "Retiree drug coverage through Brightwater covers him and his wife",
            "rationale": "Joining could end retiree coverage for both; he will call Brightwater",
            "next_steps": "Call back December 3",
        },
    )
    call.decide("defer", rationale="Retiree coverage impact unconfirmed.")
    return s.labeled(
        labels={
            "disc-coverage": Label(P, ("ask_coverage", "ask_spouse", "explain_risk")),
            "elig-timing": Label(P, ("timing",)),
            "com-choice": Label(P, ("timing",)),
            "comp-notes": Label(P, ("notes:Joining could end retiree coverage for both",)),
        },
        final={
            "ver-claims": ("not_applicable", False),
            "comp-followup": (P, False),
            "rec-outcome": (P, False),
            "disc-coverage": (P, False),
            "elig-other-coverage": (P, False),
        },
    )


def rushed_signup(client: TestClient) -> Labeled:
    """Case C done badly: no question about other coverage, jargon left unexplained, the
    checklist ticked in silence. The customer's yes is one the wording check can't read."""
    s = Script(client, CASE_C)
    call = s.call
    s.customer("Can you just sign me up for the one my neighbor has, Summit Classic?")
    s.agent(
        "Sure thing. Summit Classic is an HMO with a zero premium, the MOOP is in the SB, and "
        "your formulary tiers are standard.",
        "jargon",
    )
    s.customer("Plain English, please. What's that mean?")
    s.agent("It just means it's a good plan. Let's get it done.", "no_explain")
    call.record(
        zip_code="00042",
        doctors=[{"name": "Dr. Michael Torres", "role": "pcp", "practice_id": "torres-millbrook"}],
        drugs=C_DRUGS,
        pharmacy_id="valuemart-rx",
        coverage={"original_medicare": True, "medicare_parts": ["a", "b"]},
    )
    call.document("sob-summit-classic-2027")
    app_id = call.apply(SUMMIT_CLASSIC, "Raymond Ellis", "00042", pcp="torres-millbrook")
    call.review_checklist(app_id)
    s.agent("Would you like me to submit your enrollment in Summit Classic HMO?")
    yes = s.customer("Sure, I don't see why not, go ahead and sign me up.", "yes")
    call.consent(app_id, yes)
    call.submit(app_id)
    return s.labeled(
        labels={
            "disc-coverage": Label(NO),
            "com-plain": Label(F, ("jargon", "no_explain")),
            "doc-pecl": Label(NO),
            "doc-consent": Label(P, ("yes",)),
        },
        final={
            "doc-consent": (P, False),
            "doc-pecl": (NO, False),
            "com-plain": (F, False),
            "ver-claims": ("not_applicable", False),
            "rec-outcome": (F, True),
            "elig-other-coverage": (F, False),
        },
    )


CASES: dict[str, Callable[[TestClient], Labeled]] = {
    "a-thorough-enrollment": thorough_enrollment,
    "a-claims-before-lookups": claims_before_lookups,
    "b-keep-and-correct": keep_and_correct,
    "b-ad-left-standing": ad_left_standing,
    "c-defer-explained": defer_explained,
    "c-rushed-signup": rushed_signup,
}

CRITICAL_CRITERIA = ("doc-pecl", "doc-consent", "ver-claims")
"""Where the judge's ruling can make or clear a critical finding. The live run requires full
agreement on these."""
