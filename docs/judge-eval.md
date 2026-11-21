# Judge eval: first live runs

Run on November 21, 2026, for `docs/PROPOSAL.md` §12 and §13. The eval set and how to run it
are in [`backend/tests/judge_evals.py`](../backend/tests/judge_evals.py) and
[`backend/tests/test_judge_evals.py`](../backend/tests/test_judge_evals.py).

## Result

| | |
|---|---|
| Judge | `claude-opus-5-5`, effort `high`, prompt `judge-v1` |
| Rubric | `mvp` v2 |
| Set | Six scripted calls, two per case, 34 labeled rulings |

| Run | Labels agreed | Quotes and claims verified | Disagreements on critical items |
|---|---|---|---|
| 1 | 33 of 34 (97%) | 81 of 81 | 2 |
| 2, after the two changes below | 34 of 34 (100%) | 74 of 74 | 0 |

This is one run on each side of the changes, not a rate. The judge is not deterministic, and
nothing here says how often run 2's result repeats.

## What the two disagreements were

**1. An unverified claim in the "thorough" call. The eval script was wrong.** The script had
the agent say "you would keep Dr. Raman, which Summit can't offer next year" without ever
looking Dr. Raman up on Summit for 2027; it took the customer's letter at its word. The judge
listed that as a coverage claim, the server found no lookup behind it, and the call got a
critical finding. That is the grader working as designed: a statement that a doctor is not in
network needs a lookup as much as a statement that she is. The script now looks it up first,
and the claim is in the labels.

**2. A checklist ticked in silence. The instructions were ambiguous.** In the rushed Case C
call the agent marks all nine checklist items and says nothing about them. The label was "not
observed". The judge ruled "fail" with a quote that verified, which made it critical. No line
in that call mentions the checklist, so whichever line it quoted showed the gap around it, not
the failure itself. Both rulings describe the same fact; they differ in what it costs. The
instructions said a fail is "did it wrongly", and ticking boxes without a word fits that. They
now say a fail quotes the line in which the agent does the wrong thing, and that something
which never happened is "not observed" however plain the gap is. Run 2 ruled "not observed".

The second one leaves a decision open. As built, a checklist marked in silence scores zero on
that item and is not a critical finding, because nothing can be quoted for an absence. §12
lists "submitting before the checklist review" as critical. If a silent tick-through should be
critical, the fair way to get there is a rule the server can check, not the judge's choice of
word: for example, critical when the judge saw no review and the log shows nothing said between
starting the application and the last mark.

## One fix the run needed

The live test as first written could never have run: the shared test fixture turns the judge
off, and the test read its configuration after that. It now asks for the judge by name.

## Limits

- The labels were written by the same hand as the grader, not by an independent person. §0's
  acceptance test is agreement with the user on a 10-call sample. This is not that.
- Six short scripted calls with tidy wording. Nothing here is a real 30-minute call, a typed
  call with a live customer model, or a voice transcript.
- No call in the set makes the judge's quote fail to verify, so the "held for review" path was
  not exercised by the real judge. The offline tests cover it.
- Cost was not recorded. Each run is six requests.
