"""The debrief: grading runs, stored judge output and the targeted retry.

A debrief is always computed from the stored log and the stored judge output, so it can be
rebuilt at any time and always comes out the same. Running the judge is a separate, explicit
step (``grade``), and its output is stored and reused until someone asks to regrade.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.engine.checks import run_checks
from app.engine.grading import Graded, grade_call
from app.engine.records import SessionState
from app.engine.session import Invalid, SessionService
from app.engine.store import SessionRow
from app.judge.judges import Judge, JudgeError
from app.judge.models import GradeRow
from app.judge.prompt import JUDGE_PROMPT_VERSION, JudgePrompt, build_prompt

JudgeStatus = Literal["off", "pending", "done", "failed"]

_NOTES: dict[JudgeStatus, str] = {
    "off": "No judge is configured (set ANTHROPIC_API_KEY), so this item isn't graded.",
    "pending": "Waiting for the judge.",
    "failed": "The judge couldn't grade this call. Try grading again.",
    "done": "",
}


class JudgeInfo(BaseModel):
    status: JudgeStatus
    """``off``: no judge configured. ``pending``: not graded yet. ``failed``: the last run
    failed and nothing is stored. ``done``: judged items come from a stored run."""
    name: str | None = None
    prompt_version: str | None = None
    graded_at: datetime | None = None
    error: str | None = None


class Debrief(Graded):
    session_id: str
    scenario_id: str
    scenario_version: int
    catalog_version: str
    ruleset_version: str
    rubric_id: str
    rubric_version: int
    judge: JudgeInfo


class DebriefService:
    def __init__(self, sessions: SessionService, judge: Judge | None) -> None:
        self.sessions = sessions
        self.judge = judge

    @property
    def judge_name(self) -> str:
        return self.judge.name if self.judge is not None else "off"

    def debrief(self, session_id: str) -> Debrief:
        row = self.sessions.require_ended(session_id)
        return self._debrief(row, self.sessions.store.get_grade(session_id), None)

    def grade(self, session_id: str, *, regrade: bool = False) -> Debrief:
        """Runs the judge unless a stored run exists (or ``regrade``), stores the result, and
        returns the debrief. A failed run stores nothing and keeps any earlier run; the
        debrief reports the error so the learner can try again."""
        with self.sessions.turn_lock(f"{session_id}#grade"):
            row = self.sessions.require_ended(session_id)
            stored = self.sessions.store.get_grade(session_id)
            if self.judge is None or (stored is not None and not regrade):
                return self._debrief(row, stored, None)
            try:
                output = self.judge.grade(self._prompt(row))
            except JudgeError as exc:
                return self._debrief(row, stored, str(exc))
            fresh = GradeRow(
                session_id=session_id,
                judge=self.judge.name,
                prompt_version=JUDGE_PROMPT_VERSION,
                rubric_id=row.rubric_id,
                rubric_version=row.rubric_version,
                graded_at=self.sessions.clock(),
                output=output,
            )
            self.sessions.store.put_grade(fresh)
            return self._debrief(row, fresh, None)

    def retry_focus(self, scenario_id: str, retry_of: str) -> tuple[str, ...]:
        """What a targeted retry of ``retry_of`` works on. A retry is the same case again."""
        row = self.sessions.require_ended(retry_of)
        if row.scenario_id != scenario_id:
            raise Invalid("a retry is the same case: scenario_id must match the call it retries")
        return tuple(self.debrief(retry_of).focus)

    def _prompt(self, row: SessionRow) -> JudgePrompt:
        content = self.sessions.pinned_content(row)
        scenario = content.scenario(row.scenario_id)
        events = self.sessions.store.events(row.session_id)
        utterances = self.sessions.store.utterances(row.session_id)
        checks = run_checks(scenario, content.catalog, content.frame, events, utterances)
        record = SessionState.fold(events).record
        return build_prompt(
            scenario=scenario,
            catalog=content.catalog,
            frame=content.frame,
            rubric=content.rubric,
            utterances=utterances,
            notes=record.notes,
            checks={r.check_id: r for r in checks},
        )

    def _debrief(self, row: SessionRow, stored: GradeRow | None, error: str | None) -> Debrief:
        content = self.sessions.pinned_content(row)
        if stored is not None:
            status: JudgeStatus = "done"
        elif error is not None:
            status = "failed"
        elif self.judge is None:
            status = "off"
        else:
            status = "pending"
        graded = grade_call(
            scenario=content.scenario(row.scenario_id),
            catalog=content.catalog,
            frame=content.frame,
            rubric=content.rubric,
            ruleset=content.ruleset,
            events=self.sessions.store.events(row.session_id),
            utterances=self.sessions.store.utterances(row.session_id),
            judge_output=stored.output if stored is not None else None,
            judge_note=_NOTES[status],
        )
        return Debrief(
            **graded.model_dump(),
            session_id=row.session_id,
            scenario_id=row.scenario_id,
            scenario_version=row.scenario_version,
            catalog_version=row.catalog_version,
            ruleset_version=row.ruleset_version,
            rubric_id=row.rubric_id,
            rubric_version=row.rubric_version,
            judge=JudgeInfo(
                status=status,
                name=stored.judge if stored else (self.judge.name if self.judge else None),
                prompt_version=stored.prompt_version if stored else None,
                graded_at=stored.graded_at if stored else None,
                error=error,
            ),
        )
