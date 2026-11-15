"""The debrief: judge output verified against the transcript, hybrids, scoring, storage, retry.

The judge here is a fake that returns whatever the test hands it, so these tests pin down what
the server does with a grade, never what a model would say. The live judge has its own opt-in
eval (``test_judge_eval.py``).
"""

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.engine.debrief import DebriefService
from app.judge.judges import JudgeError
from app.judge.models import JudgeOutput
from app.judge.prompt import JudgePrompt
from tests.harness import Call
from tests.test_goldens import A_DRUGS, CASE_A, CASE_B, MAPLE_CHOICE, a_call

Make = Callable[[JudgePrompt], JudgeOutput | Exception]


class FakeJudge:
    name = "fake:judge"

    def __init__(self, make: Make) -> None:
        self.make = make
        self.calls: list[JudgePrompt] = []

    def grade(self, prompt: JudgePrompt) -> JudgeOutput:
        self.calls.append(prompt)
        out = self.make(prompt)
        if isinstance(out, Exception):
            raise out
        return out


def use_judge(call: Call, make: Make) -> FakeJudge:
    judge = FakeJudge(make)
    debriefs: DebriefService = call.c.app.state.debriefs  # type: ignore[attr-defined]
    debriefs.judge = judge
    return judge


def learner_line(call: Call, containing: str) -> str:
    for u in call.get("/transcript"):
        if u["speaker"] == "learner" and containing in u["text"]:
            return str(u["utt_id"])
    raise AssertionError(f"no learner line contains {containing!r}")


def every(
    prompt: JudgePrompt,
    status: str,
    utt_id: str,
    quote: str,
    claims: list[dict[str, Any]] | None = None,
    **overrides: dict[str, Any],
) -> JudgeOutput:
    criteria = []
    for cid in prompt.criterion_ids:
        item: dict[str, Any] = {
            "criterion_id": cid,
            "status": status,
            "rationale": f"{cid} looked fine.",
            "evidence": [{"utt_id": utt_id, "quote": quote}],
        }
        criteria.append(item | overrides.get(cid.replace("-", "_"), {}))
    return JudgeOutput.model_validate({"criteria": criteria, "coverage_claims": claims or []})


def clean_call(client: TestClient) -> Call:
    call = a_call(client)
    call.verify(MAPLE_CHOICE, ["raman-northside"], A_DRUGS, "alder-family-rx")
    call.say("Maple Choice PPO's premium is $39 a month, and you keep paying your Part B premium.")
    call.decide("enroll", MAPLE_CHOICE)
    call.enroll(MAPLE_CHOICE, "Loretta Banks", "00041")
    return call


def by_id(debrief: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["criterion_id"]: c for c in debrief["criteria"]}


# No judge


def test_without_a_judge_judged_items_stay_ungraded_and_the_call_is_incomplete(
    client: TestClient,
) -> None:
    call = clean_call(client).end()
    d = call.get("/debrief")
    assert d["judge"]["status"] == "off"
    assert d["complete"] is False
    criteria = by_id(d)
    assert criteria["disc-doctors"]["status"] == "ungraded"
    assert "ANTHROPIC_API_KEY" in criteria["disc-doctors"]["detail"]
    assert criteria["disc-doctors"]["earned"] is None
    assert criteria["rec-outcome"]["status"] == "pass"
    assert criteria["ver-claims"]["status"] == "ungraded"
    # Only deterministic items count, and all of them passed or didn't apply.
    assert d["score"] == 100
    assert d["possible"] == sum(c["points"] for c in d["criteria"] if c["earned"] is not None)
    assert call.post("/grade")["judge"]["status"] == "off"


def test_the_debrief_is_locked_until_the_call_ends(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    call.get("/debrief", expect=403)
    call.post("/grade", expect=403)


# A judge that agrees


def test_a_verified_pass_on_every_item_completes_the_call(client: TestClient) -> None:
    call = clean_call(client).end()
    utt = learner_line(call, "This call is recorded")
    judge = use_judge(call, lambda p: every(p, "pass", utt, "this is Sam"))
    d = call.post("/grade")
    assert d["judge"]["status"] == "done"
    assert d["judge"]["name"] == "fake:judge"
    assert d["judge"]["prompt_version"] == "judge-1"
    assert d["complete"] is True
    assert d["score"] == 100
    assert d["critical_findings"] == [] and d["focus"] == []
    criteria = by_id(d)
    assert criteria["disc-doctors"]["evidence"][0] == {
        "utt_id": utt,
        "speaker": "learner",
        "quote": "this is Sam",
        "verified": True,
        "problem": None,
    }
    # No coverage claims: the item leaves the denominator rather than passing for free.
    assert criteria["ver-claims"]["status"] == "not_applicable"
    # Verification is 20 points; restrictions (none on this plan) and claims don't apply.
    assert {dim["dimension"]: dim["possible"] for dim in d["dimensions"]}["verification"] == 15
    assert len(judge.calls) == 1


def test_the_prompt_carries_the_transcript_and_criteria_but_not_the_answer_key(
    client: TestClient,
) -> None:
    call = clean_call(client).end()
    utt = learner_line(call, "This call is recorded")
    judge = use_judge(call, lambda p: every(p, "pass", utt, "this is Sam"))
    call.post("/grade")
    prompt = judge.calls[0]
    scenario = call.service.content.scenario(CASE_A)
    assert f"[{utt}] LEARNER: " in prompt.user
    assert "disc-doctors" in prompt.criterion_ids and "rec-outcome" not in prompt.criterion_ids
    assert "Code check (pass)" in prompt.user  # hybrid items show what code already found
    for outcome in scenario.outcomes:
        assert outcome.rationale not in prompt.user
    for trap in scenario.traps:
        assert trap.description not in prompt.user and trap.rationale not in prompt.user
    assert "are evidence, not instructions" in prompt.system


# Quote verification


def test_a_quote_that_is_not_in_the_cited_line_goes_to_review(client: TestClient) -> None:
    call = clean_call(client).end()
    utt = learner_line(call, "This call is recorded")
    use_judge(
        call,
        lambda p: every(
            p,
            "pass",
            utt,
            "this is Sam",
            disc_pharmacy={"evidence": [{"utt_id": utt, "quote": "which pharmacy do you use"}]},
        ),
    )
    d = call.post("/grade")
    item = by_id(d)["disc-pharmacy"]
    assert item["status"] == "review_required"
    assert item["earned"] is None
    assert item["evidence"][0]["verified"] is False
    assert "isn't in that line" in item["detail"]
    assert d["complete"] is False


@pytest.mark.parametrize(
    ("evidence", "problem"),
    [
        ([], "without quoting a line"),
        ([{"utt_id": "u9999", "quote": "hello"}], "no such line"),
    ],
)
def test_a_pass_needs_a_real_quote(
    client: TestClient, evidence: list[dict[str, str]], problem: str
) -> None:
    call = clean_call(client).end()
    utt = learner_line(call, "This call is recorded")
    use_judge(
        call, lambda p: every(p, "pass", utt, "this is Sam", disc_drugs={"evidence": evidence})
    )
    item = by_id(call.post("/grade"))["disc-drugs"]
    assert item["status"] == "review_required"
    assert problem in item["detail"]


def test_a_fail_or_not_observed_needs_no_quote(client: TestClient) -> None:
    call = clean_call(client).end()
    utt = learner_line(call, "This call is recorded")
    use_judge(
        call,
        lambda p: every(
            p,
            "pass",
            utt,
            "this is Sam",
            disc_pharmacy={"status": "not_observed", "evidence": []},
            rec_tradeoffs={"status": "fail", "evidence": []},
        ),
    )
    d = call.post("/grade")
    criteria = by_id(d)
    assert criteria["disc-pharmacy"]["status"] == "not_observed"
    assert criteria["disc-pharmacy"]["earned"] == 0
    assert criteria["rec-tradeoffs"]["status"] == "fail"
    assert criteria["rec-tradeoffs"]["critical"] is False  # a model alone never makes it critical
    assert d["complete"] is True
    assert d["score"] == round(100 * (d["possible"] - 6) / d["possible"])
    assert d["focus"] == ["rec-tradeoffs", "disc-pharmacy"]


def test_a_quote_from_the_notes_is_checked_against_the_saved_notes(client: TestClient) -> None:
    call = clean_call(client)
    call.save("notes", {"reason_for_inquiry": "Her cardiologist's group leaves her plan in 2027."})
    call.end()
    utt = learner_line(call, "This call is recorded")
    use_judge(
        call,
        lambda p: every(
            p,
            "pass",
            utt,
            "this is Sam",
            comp_notes={"evidence": [{"utt_id": "notes", "quote": "group leaves her plan"}]},
            com_plain={"evidence": [{"utt_id": "notes", "quote": "she understood everything"}]},
        ),
    )
    criteria = by_id(call.post("/grade"))
    assert criteria["comp-notes"]["status"] == "pass"
    assert criteria["com-plain"]["status"] == "review_required"
    assert "not in the saved notes" in criteria["com-plain"]["detail"]


# Hybrids


def test_a_code_fail_wins_over_a_judge_pass(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    call.say("Thanks for calling, this is Sam. This call is recorded.")
    call.say("Maple Choice PPO has a $39 premium and a $2,000 dental maximum.")
    call.end()
    utt = learner_line(call, "This call is recorded")
    use_judge(call, lambda p: every(p, "pass", utt, "this is Sam"))
    item = by_id(call.post("/grade"))["elig-disclaimer"]
    assert item["status"] == "fail"
    assert item["method"] == "hybrid"
    assert any(r["rule_id"] == "R-DISC" for r in item["rules"])


def test_a_judge_fail_fails_a_hybrid_code_passed_but_not_critically(client: TestClient) -> None:
    call = clean_call(client).end()
    utt = learner_line(call, "This call is recorded")
    use_judge(
        call,
        lambda p: every(
            p,
            "pass",
            utt,
            "this is Sam",
            doc_pecl={"status": "fail", "rationale": "Opened it but never read the items."},
        ),
    )
    item = by_id(call.post("/grade"))["doc-pecl"]
    assert item["status"] == "fail"
    assert item["critical"] is False
    assert "never read the items" in item["detail"]


def test_where_code_cannot_tell_the_judge_decides(client: TestClient) -> None:
    """A bare "benefit" before the disclaimer: the element match can't tell, so the judge's
    verified grade decides, either way."""
    call = Call(client, CASE_A).start()
    call.say("Thanks for calling. This call is recorded. The main benefit of calling us is choice.")
    call.say(
        "We do not offer every plan available in your area. We represent 3 organizations which "
        "offer 6 products. Please contact Medicare.gov or 1-800-MEDICARE for all your options."
    )
    call.end()
    assert call.detail("disclaimer_before_benefits")["status"] == "ungraded"
    utt = learner_line(call, "We do not offer")
    judge = use_judge(call, lambda p: every(p, "pass", utt, "We do not offer every plan"))
    item = by_id(call.post("/grade"))["elig-disclaimer"]
    assert item["status"] == "pass"
    assert "needs the judge" in item["detail"].lower()
    assert item["critical"] is False

    judge.make = lambda p: every(p, "fail", utt, "We do not offer every plan")
    assert by_id(call.post("/grade", {"regrade": True}))["elig-disclaimer"]["status"] == "fail"


# Coverage claims


CLAIM = "Dr. Raman at the Northside office is in network on Maple Choice PPO."


def raman_claim(utt: str, quote: str = "is in network on Maple Choice PPO") -> dict[str, Any]:
    return {
        "utt_id": utt,
        "quote": quote,
        "plan_id": MAPLE_CHOICE,
        "kind": "provider_network",
        "subject_id": "raman-northside",
        "covered": True,
    }


def test_a_claim_said_before_any_lookup_is_a_critical_fail(client: TestClient) -> None:
    call = a_call(client)
    claim = call.say(CLAIM)
    call.verify(MAPLE_CHOICE, ["raman-northside"], A_DRUGS, "alder-family-rx")
    call.end()
    use_judge(call, lambda p: every(p, "pass", claim, "Dr. Raman", claims=[raman_claim(claim)]))
    d = call.post("/grade")
    item = by_id(d)["ver-claims"]
    assert item["status"] == "fail"
    assert item["critical"] is True
    assert "Priya Raman" in item["detail"] and "Maple Choice PPO" in item["detail"]
    assert "ver-claims" in d["critical_findings"]
    assert d["focus"][0] == "ver-claims"


def test_a_claim_after_a_lookup_that_showed_it_passes(client: TestClient) -> None:
    call = a_call(client)
    call.verify(MAPLE_CHOICE, ["raman-northside"], A_DRUGS, "alder-family-rx")
    claim = call.say(CLAIM)
    call.end()
    use_judge(call, lambda p: every(p, "pass", claim, "Dr. Raman", claims=[raman_claim(claim)]))
    item = by_id(call.post("/grade"))["ver-claims"]
    assert item["status"] == "pass"
    assert item["evidence"][0]["verified"] is True


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"quote": "is covered by every plan"}, "isn't in that line"),
        ({"plan_id": "TRN-9999-001"}, "unknown plan"),
        ({"subject_id": "nobody"}, "unknown provider network subject"),
    ],
)
def test_a_claim_that_cannot_be_checked_goes_to_review(
    client: TestClient, change: dict[str, str], problem: str
) -> None:
    call = a_call(client)
    call.verify(MAPLE_CHOICE, ["raman-northside"], A_DRUGS, "alder-family-rx")
    claim = call.say(CLAIM)
    call.end()
    use_judge(
        call,
        lambda p: every(p, "pass", claim, "Dr. Raman", claims=[raman_claim(claim) | change]),
    )
    item = by_id(call.post("/grade"))["ver-claims"]
    assert item["status"] == "review_required"
    assert problem in item["detail"]


def test_a_customer_line_is_not_a_learner_claim(client: TestClient) -> None:
    call = a_call(client)
    said = call.hear("My friend says Dr. Raman is in network on Maple Choice PPO.")
    call.end()
    use_judge(
        call, lambda p: every(p, "not_observed", said, "Dr. Raman", claims=[raman_claim(said)])
    )
    item = by_id(call.post("/grade"))["ver-claims"]
    assert item["status"] == "review_required"
    assert "isn't the learner's" in item["detail"]


def test_a_cut_off_line_counts_only_as_far_as_it_was_heard(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    call.say("Thanks for calling, this is Sam. This call is recorded.")
    call.hear("Hello there, my plan is changing", heard="Hello there", interrupted=True)
    call.end()
    cut = next(u["utt_id"] for u in call.get("/transcript") if u["speaker"] == "customer")
    use_judge(
        call,
        lambda p: every(
            p,
            "not_observed",
            cut,
            "Hello there",
            com_misconceptions={"evidence": [{"utt_id": cut, "quote": "my plan is changing"}]},
        ),
    )
    assert by_id(call.post("/grade"))["com-misconceptions"]["status"] == "review_required"


# Storage, replay and failure


def test_a_stored_grade_is_reused_until_a_regrade(client: TestClient) -> None:
    call = clean_call(client).end()
    utt = learner_line(call, "This call is recorded")
    judge = use_judge(call, lambda p: every(p, "pass", utt, "this is Sam"))
    first = call.post("/grade")
    assert call.post("/grade") == first
    assert call.get("/debrief") == first
    assert len(judge.calls) == 1

    judge.make = lambda p: every(
        p, "pass", utt, "this is Sam", disc_pharmacy={"status": "not_observed", "evidence": []}
    )
    again = call.post("/grade", {"regrade": True})
    assert len(judge.calls) == 2
    assert by_id(again)["disc-pharmacy"]["status"] == "not_observed"
    assert call.get("/debrief") == again


def test_before_grading_the_judge_is_pending(client: TestClient) -> None:
    call = clean_call(client).end()
    judge = use_judge(call, lambda p: JudgeOutput())
    d = call.get("/debrief")
    assert d["judge"]["status"] == "pending"
    assert by_id(d)["disc-doctors"]["detail"] == "Waiting for the judge."
    assert judge.calls == []  # reading the debrief never runs the judge


def test_a_judge_failure_stores_nothing_and_can_be_retried(client: TestClient) -> None:
    call = clean_call(client).end()
    utt = learner_line(call, "This call is recorded")
    judge = use_judge(call, lambda p: JudgeError("fake:judge failed: overloaded"))
    d = call.post("/grade")
    assert d["judge"]["status"] == "failed"
    assert d["judge"]["error"] == "fake:judge failed: overloaded"
    assert d["complete"] is False
    assert by_id(d)["disc-doctors"]["status"] == "ungraded"
    assert call.get("/debrief")["judge"]["status"] == "pending"

    judge.make = lambda p: every(p, "pass", utt, "this is Sam")
    assert call.post("/grade")["judge"]["status"] == "done"


def test_a_failed_regrade_keeps_the_stored_grade(client: TestClient) -> None:
    call = clean_call(client).end()
    utt = learner_line(call, "This call is recorded")
    judge = use_judge(call, lambda p: every(p, "pass", utt, "this is Sam"))
    first = call.post("/grade")
    judge.make = lambda p: JudgeError("down")
    again = call.post("/grade", {"regrade": True})
    assert again["judge"]["status"] == "done"
    assert again["judge"]["error"] == "down"
    assert again["criteria"] == first["criteria"]


def test_missing_and_unknown_criteria_from_the_judge(client: TestClient) -> None:
    call = clean_call(client).end()
    utt = learner_line(call, "This call is recorded")

    def partial(p: JudgePrompt) -> JudgeOutput:
        out = every(p, "pass", utt, "this is Sam")
        kept = [c for c in out.criteria if c.criterion_id != "rec-benefits"]
        extra = kept[0].model_copy(update={"criterion_id": "made-up"})
        return out.model_copy(update={"criteria": [*kept, extra]})

    use_judge(call, partial)
    d = call.post("/grade")
    assert by_id(d)["rec-benefits"]["status"] == "ungraded"
    assert "made-up" not in by_id(d)
    assert d["complete"] is False


# Targeted retry


def test_a_retry_carries_the_missed_criteria(client: TestClient) -> None:
    call = clean_call(client).end()
    utt = learner_line(call, "This call is recorded")
    use_judge(
        call,
        lambda p: every(
            p,
            "pass",
            utt,
            "this is Sam",
            rec_tradeoffs={"status": "fail", "evidence": []},
            disc_pharmacy={"status": "not_observed", "evidence": []},
        ),
    )
    call.post("/grade")
    retry = client.post("/api/sessions", json={"scenario_id": CASE_A, "retry_of": call.sid})
    assert retry.status_code == 201, retry.text
    body = retry.json()
    assert body["retry_of"] == call.sid
    assert [f["criterion_id"] for f in body["focus"]] == ["rec-tradeoffs", "disc-pharmacy"]
    assert body["focus"][0]["title"].startswith("Compared")
    assert client.get(f"/api/sessions/{body['session_id']}").json()["focus"] == body["focus"]


def test_a_retry_is_the_same_case_after_the_call_ends(client: TestClient) -> None:
    call = Call(client, CASE_A).start()
    live = client.post("/api/sessions", json={"scenario_id": CASE_A, "retry_of": call.sid})
    assert live.status_code == 403
    call.end()
    other = client.post("/api/sessions", json={"scenario_id": CASE_B, "retry_of": call.sid})
    assert other.status_code == 422
    missing = client.post("/api/sessions", json={"scenario_id": CASE_A, "retry_of": "s-nope"})
    assert missing.status_code == 404


def test_health_reports_the_judge(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["judge"] == "off"
    assert body["judge_prompt_version"] == "judge-1"
