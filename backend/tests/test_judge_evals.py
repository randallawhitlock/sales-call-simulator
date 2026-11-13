"""Runs the judge eval set.

Every build checks the labels with a reference judge that returns exactly what a human grader
labeled. That proves the set is sound (labels are asked for, cited lines exist, the pipeline
turns the labels into the expected findings) without a model grading anything.

``pytest -m live`` asks the real judge (needs ``ANTHROPIC_API_KEY``; ``SIM_JUDGE_MODEL`` picks
the model) and fails with a report of every disagreement on a criterion that carries a
critical finding, every quote that didn't verify beyond a small allowance, and low agreement
overall. A model's rulings are compared with human labels here; they are never the evidence.
"""

import pytest
from fastapi.testclient import TestClient

from app.judge.contract import JudgeOutput
from app.judge.judges import judge_from_env
from app.judge.prompt import JudgePrompt, build_judge_prompt
from tests.harness import Call, FakeJudge, claim, ruling
from tests.judge_evals import CASES, CRITICAL_CRITERIA, Labeled

MIN_AGREEMENT = 0.8
MIN_QUOTES_VERIFIED = 0.95


def prompt_for(call: Call) -> JudgePrompt:
    service = call.service
    row = service.get(call.sid)
    return build_judge_prompt(service._facts(row, service.content), service.content.rubric)


def reference_output(case: Labeled, prompt: JudgePrompt) -> JudgeOutput:
    """What a judge that agreed with every label would return: the labeled verdicts with the
    cited lines quoted whole, "not observed" for the rest, and the expected claims."""
    verdicts = []
    for criterion_id in prompt.asked:
        label = case.labels.get(criterion_id)
        if label is None:
            verdicts.append(ruling(criterion_id, "not_observed"))
        else:
            verdicts.append(ruling(criterion_id, label.verdict, *case.evidence(label)))
    lines = {u.utt_id: u.heard_text for u in case.call.service.transcript(case.call.sid)}
    claims = [
        claim(
            case.lines[c.line],
            lines[case.lines[c.line]],
            c.kind,
            c.plan_id,
            c.subject_id,
            c.covered,
        )
        for c in case.claims
    ]
    return JudgeOutput(verdicts=verdicts, coverage_claims=claims)


@pytest.mark.parametrize("name", sorted(CASES))
def test_labels_are_sound(client: TestClient, name: str) -> None:
    case = CASES[name](client)
    prompt = prompt_for(case.call)
    dead = sorted(set(case.labels) - set(prompt.asked))
    assert not dead, f"labeled but never asked of the judge: {dead}"
    assert prompt.wants_claims

    case.call.service.judge = FakeJudge(reference_output(case, prompt))
    debrief = case.call.debrief()
    held = [c["criterion_id"] for c in debrief.body["criteria"] if c["status"] == "review_required"]
    assert not held, f"a label cites something the server can't verify: {held}"
    assert debrief.body["state"] == "complete"
    for criterion_id, label in case.labels.items():
        if criterion_id not in case.final:
            assert debrief[criterion_id]["status"] == label.verdict, criterion_id
    for criterion_id, expected in case.final.items():
        assert debrief.status(criterion_id) == expected, criterion_id
    assert len(debrief["ver-claims"]["claims"]) == len(case.claims)


def test_the_set_covers_every_case_and_every_kind_of_ruling(client: TestClient) -> None:
    built = [build(client) for build in CASES.values()]
    assert {c.call.session["scenario_id"] for c in built} == {
        "scenario-101",
        "scenario-102",
        "scenario-103",
    }
    verdicts = {label.verdict for c in built for label in c.labels.values()}
    assert verdicts == {"pass", "fail", "not_observed"}
    labeled = {criterion_id for c in built for criterion_id in c.labels}
    assert set(CRITICAL_CRITERIA) - {"ver-claims"} <= labeled
    claim_results = {c.final["ver-claims"][0] for c in built}
    assert claim_results == {"pass", "fail", "not_applicable"}


@pytest.mark.live
def test_live_judge_agrees_with_the_labels(client: TestClient) -> None:
    judge = judge_from_env()
    if judge is None:
        pytest.skip("no judge configured: set ANTHROPIC_API_KEY")
    compared = agreed = quotes = verified = 0
    critical: list[str] = []
    report: list[str] = []
    for name, build in sorted(CASES.items()):
        case = build(client)
        output = judge.grade(prompt_for(case.call)).output
        case.call.service.judge = FakeJudge(output)
        debrief = case.call.debrief()
        said = {v.criterion_id: v for v in output.verdicts}
        for criterion_id, label in sorted(case.labels.items()):
            got = said.get(criterion_id)
            compared += 1
            if got is not None and got.verdict.value == label.verdict:
                agreed += 1
                continue
            line = (
                f"{name} {criterion_id}: labeled {label.verdict}, judge said "
                f"{got.verdict.value if got else 'nothing'}"
                f"{': ' + got.rationale if got else ''}"
            )
            report.append(line)
        for criterion_id in CRITICAL_CRITERIA:
            if (
                criterion_id in case.final
                and debrief.status(criterion_id) != case.final[criterion_id]
            ):
                critical.append(
                    f"{name} {criterion_id}: expected {case.final[criterion_id]}, got "
                    f"{debrief.status(criterion_id)}. {debrief[criterion_id]['detail']}"
                )
        for c in debrief.body["criteria"]:
            quotes += len(c["quotes"]) + len(c["claims"])
            verified += sum(q["verified"] for q in c["quotes"])
            verified += sum(x["status"] != "unverifiable" for x in c["claims"])
            report += [
                f"{name} {c['criterion_id']}: quote not found in {q['utt_id']}: {q['quote']!r}"
                for q in c["quotes"]
                if not q["verified"]
            ]
    agreement = agreed / compared
    quote_rate = verified / quotes if quotes else 1.0
    summary = (
        f"{judge.name}: agreed on {agreed} of {compared} labels ({agreement:.0%}); "
        f"{verified} of {quotes} quotes and claims verified ({quote_rate:.0%})"
    )
    print("\n".join([summary, *critical, *report]))
    problems = "\n".join([summary, *critical, *report])
    assert not critical, problems
    assert agreement >= MIN_AGREEMENT, problems
    assert quote_rate >= MIN_QUOTES_VERIFIED, problems
