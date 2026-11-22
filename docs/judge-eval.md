# Judge eval: first live run

Run on November 21, 2026, for `docs/PROPOSAL.md` §12 and §13. The labeled transcripts are in
[`backend/tests/judge_eval/`](../backend/tests/judge_eval/) and the run is
`uv run pytest -m live tests/test_judge_eval.py`.

## Result

| | |
|---|---|
| Judge | `claude-opus-5-5`, prompt `judge-3` |
| Rubric | `mvp` v2 |
| Set | Four labeled transcripts (two of Case A, one each of B and C), 61 labels, 8 expected coverage claims |

| Labels agreed | Quotes that failed to verify | Expected claims missed |
|---|---|---|
| 57 of 61 (93%) | 0 | 0 |

The run passes its thresholds (85% agreement, no missed claim). It is one run, and the judge is
not deterministic, so this is a first reading, not a rate.

## The four disagreements

None of these changed code or labels. Each is a question about the label or the rubric.

| Transcript | Item | Label | Judge | What it is |
|---|---|---|---|---|
| `a-thorough` | `doc-pecl` | pass | fail | The transcript says "let's go through the checklist item by item" and then covers one item. The judge wanted the rest. The judge has the better of it: the label rewards the announcement, not the review |
| `a-thorough` | `ver-restrictions` | pass | not observed | The learner explains prior authorization on the plan the customer did not choose. The chosen plan has no restriction, so on a real call the code check makes this item not applicable whatever the judge says. An artifact of grading a bare transcript |
| `c-defer` | `rec-premiums` | not observed | not applicable | The call rightly ends in a deferral before any plan is reviewed. The judge says premiums had no occasion to come up; the label counts the silence against the learner |
| `c-defer` | `rec-service-costs` | not observed | not applicable | The same |

The last two are one open question: on a call that correctly defers, are the needs-discussion
items about plan costs not applicable, or missed? The rule register makes the needs discussion
a regulation only when the call ends in an enrollment. If the answer is "not applicable", the
labels change and the judge's instructions should say so; if it is "missed", the instructions
should say that instead. Either way the judge needs to be told, because it will keep choosing.

## Limits

- Four short scripted transcripts with tidy wording, graded without their events. Nothing here
  is a real 30-minute call, a call with a live customer model, or a voice transcript.
- No transcript made the judge misquote, so the "needs review" path was not exercised by the
  real judge. The offline tests cover it.
- The eval compares rulings on judged items. It does not run the whole pipeline, so the
  code-side rules (claim backing, the silent-checklist finding, consent as a backstop) are
  covered by the offline tests only.
- §0's acceptance test is agreement with the user on a 10-call sample. This is not that.
