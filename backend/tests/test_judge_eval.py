"""Judge eval set: labeled transcripts in ``tests/judge_eval``.

Offline (always): every label names a judged criterion, every labeled quote is really in its
line, every expected claim points at a learner line and real ids, and the prompt carries the
whole transcript.

Live (``pytest -m live``, needs ``ANTHROPIC_API_KEY``): runs the real judge on each transcript
and reports agreement with the labels. Fail and not-observed both earn nothing, so they count
as the same answer. Run it on every change to the judge prompt, model or rubric.
"""

import json
import os
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict

from app.content.common import Evaluation
from app.content.loader import load_bundle
from app.engine.events import Channel, Speaker, Utterance
from app.engine.grading import verify_quote
from app.engine.records import NotesSection
from app.judge.judges import judge_from_env
from app.judge.models import NOTES, JudgedStatus, JudgeOutput, Quote
from app.judge.prompt import JudgePrompt, build_prompt, notes_text

EVAL_DIR = Path(__file__).parent / "judge_eval"
BUNDLE = load_bundle()
JUDGED = {
    c.criterion_id for c in BUNDLE.rubric.criteria if c.method is not Evaluation.DETERMINISTIC
}
MIN_AGREEMENT = 0.85


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Line(_Strict):
    speaker: Speaker
    text: str


class Label(_Strict):
    status: JudgedStatus
    line: int | Literal["notes"] | None = None
    quote: str | None = None


class ExpectedClaim(_Strict):
    line: int
    plan_id: str
    kind: Literal["provider_network", "formulary", "pharmacy_network"]
    subject_id: str
    covered: bool


class EvalCase(_Strict):
    eval_id: str
    scenario_id: str
    description: str
    notes: NotesSection | None = None
    lines: list[Line]
    labels: dict[str, Label]
    claims: list[ExpectedClaim]

    def utterances(self) -> list[Utterance]:
        return [
            Utterance(
                utt_id=utt_id(i),
                session_id="eval",
                speaker=line.speaker,
                channel=Channel.TEXT,
                text=line.text,
                heard_text=line.text,
                t_ms=i * 5000,
            )
            for i, line in enumerate(self.lines, start=1)
        ]


def utt_id(line: int) -> str:
    return f"u{line:04d}"


CASES = {
    case.eval_id: case
    for case in (
        EvalCase.model_validate(json.loads(p.read_text())) for p in sorted(EVAL_DIR.glob("*.json"))
    )
}


def prompt_for(case: EvalCase) -> JudgePrompt:
    return build_prompt(
        scenario=BUNDLE.scenario(case.scenario_id),
        catalog=BUNDLE.catalog,
        frame=BUNDLE.frame,
        rubric=BUNDLE.rubric,
        utterances=case.utterances(),
        notes=case.notes,
        checks={},
    )


def test_the_eval_set_covers_every_case_and_both_ends() -> None:
    assert {c.scenario_id for c in CASES.values()} == set(BUNDLE.scenarios)
    statuses = {label.status for c in CASES.values() for label in c.labels.values()}
    assert {JudgedStatus.PASS, JudgedStatus.FAIL, JudgedStatus.NOT_OBSERVED} <= statuses
    assert all(
        c.eval_id == p.stem
        for c, p in zip(CASES.values(), sorted(EVAL_DIR.glob("*.json")), strict=True)
    )


@pytest.mark.parametrize("eval_id", sorted(CASES))
def test_labels_name_judged_criteria_and_their_quotes_verify(eval_id: str) -> None:
    case = CASES[eval_id]
    utterances = {u.utt_id: u for u in case.utterances()}
    notes = notes_text(case.notes)
    for criterion_id, label in case.labels.items():
        assert criterion_id in JUDGED, f"{criterion_id} is not a judged or hybrid criterion"
        if label.status is JudgedStatus.PASS:
            assert label.line is not None and label.quote, f"{criterion_id}: a pass needs a quote"
        if label.line is None:
            continue
        assert label.quote
        cited = NOTES if label.line == "notes" else utt_id(label.line)
        view = verify_quote(Quote(utt_id=cited, quote=label.quote), utterances, notes)
        assert view.verified, f"{criterion_id}: {view.problem}"
        if cited != NOTES:
            assert view.speaker is Speaker.LEARNER, f"{criterion_id}: cite the learner's line"


@pytest.mark.parametrize("eval_id", sorted(CASES))
def test_expected_claims_point_at_learner_lines_and_real_ids(eval_id: str) -> None:
    case = CASES[eval_id]
    catalog = BUNDLE.catalog
    lookup = {
        "provider_network": catalog.practice,
        "formulary": catalog.drug,
        "pharmacy_network": catalog.pharmacy,
    }
    for claim in case.claims:
        assert case.lines[claim.line - 1].speaker is Speaker.LEARNER
        catalog.plan(claim.plan_id)
        lookup[claim.kind](claim.subject_id)


@pytest.mark.parametrize("eval_id", sorted(CASES))
def test_the_prompt_carries_every_line(eval_id: str) -> None:
    case = CASES[eval_id]
    prompt = prompt_for(case)
    for i, line in enumerate(case.lines, start=1):
        assert f"[{utt_id(i)}] {line.speaker.value.upper()}: {line.text}" in prompt.user
    assert set(prompt.criterion_ids) == JUDGED


# Live


def _agrees(expected: JudgedStatus, got: JudgedStatus) -> bool:
    miss = {JudgedStatus.FAIL, JudgedStatus.NOT_OBSERVED}
    return expected == got or (expected in miss and got in miss)


@pytest.mark.live
def test_the_live_judge_agrees_with_the_labels() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("needs ANTHROPIC_API_KEY")
    judge = judge_from_env({**os.environ, "SIM_JUDGE": "claude"})
    assert judge is not None
    agreed = total = unverified = 0
    missed_claims: list[str] = []
    report: list[str] = []
    for case in CASES.values():
        out: JudgeOutput = judge.grade(prompt_for(case))
        got = {c.criterion_id: c for c in out.criteria}
        utterances = {u.utt_id: u for u in case.utterances()}
        notes = notes_text(case.notes)
        for criterion_id, label in case.labels.items():
            total += 1
            item = got.get(criterion_id)
            if item is not None and _agrees(label.status, item.status):
                agreed += 1
            else:
                said = item.status.value if item else "nothing"
                why = item.rationale if item else ""
                report.append(
                    f"{case.eval_id} {criterion_id}: want {label.status.value}, got {said}. {why}"
                )
        for item in out.criteria:
            for q in item.evidence:
                if not verify_quote(q, utterances, notes).verified:
                    unverified += 1
                    report.append(f"{case.eval_id} {item.criterion_id}: unverified quote {q}")
        found = {
            (c.utt_id, c.plan_id, c.kind, c.subject_id, c.covered) for c in out.coverage_claims
        }
        for claim in case.claims:
            key = (utt_id(claim.line), claim.plan_id, claim.kind, claim.subject_id, claim.covered)
            if key not in found:
                missed_claims.append(f"{case.eval_id}: {key}")
    rate = agreed / total
    print(
        f"\njudge {judge.name}: {agreed}/{total} labels agree ({rate:.0%}); "
        f"{unverified} unverified quotes; {len(missed_claims)} missed claims"
    )
    print("\n".join(report + missed_claims))
    assert rate >= MIN_AGREEMENT
    assert not missed_claims
