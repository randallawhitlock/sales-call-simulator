"""Drives a call through the learner API, the way the call screen will.

Scripted customer turns (``hear``) go straight to the session service so golden tests control
exactly what the customer says; ``turn`` goes through the actor the way the call screen does.
Everything the learner does goes through HTTP, so tests exercise the endpoints the browser uses.
"""

import itertools
from collections.abc import Callable, Iterable
from typing import Any

from fastapi.testclient import TestClient

from app.engine.events import Channel, Speaker, UtteranceIn
from app.engine.session import SessionService
from app.judge.contract import JudgeClaim, JudgeEvidence, JudgeOutput, JudgeVerdict, Verdict
from app.judge.judges import JudgeError, JudgeReply
from app.judge.prompt import JudgePrompt

DISCLAIMER = (
    "Before we go further: we do not offer every plan available in your area. Currently we "
    "represent 3 organizations which offer 6 products in your area. Please contact "
    "Medicare.gov or 1-800-MEDICARE to get information on all of your options."
)
GREETING = "Thanks for calling Larkspur Benefits Group, this is Sam. This call is recorded."
SCOPE_ASK = "Which kinds of coverage would you like to talk about today? Medicare Advantage?"


def ruling(criterion_id: str, verdict: str, *evidence: tuple[str, str]) -> JudgeVerdict:
    """A judge ruling. ``evidence`` is (utt_id, quote) pairs."""
    return JudgeVerdict(
        criterion_id=criterion_id,
        verdict=Verdict(verdict),
        rationale=f"Because of what was said ({verdict}).",
        evidence=[JudgeEvidence(utt_id=u, quote=q) for u, q in evidence],
    )


def claim(
    utt_id: str,
    quote: str,
    kind: str,
    plan_id: str,
    subject_id: str,
    covered: bool = True,
    year: int = 2027,
) -> JudgeClaim:
    return JudgeClaim.model_validate(
        {
            "utt_id": utt_id,
            "quote": quote,
            "kind": kind,
            "plan_id": plan_id,
            "subject_id": subject_id,
            "covered": covered,
            "year": year,
        }
    )


class FakeJudge:
    """Returns scripted rulings and records what it was asked. ``answer`` may be the output,
    a function of the prompt, or a JudgeError to raise."""

    name = "fake:judge-1"

    def __init__(
        self, answer: JudgeOutput | JudgeError | Callable[[JudgePrompt], JudgeOutput]
    ) -> None:
        self.answer = answer
        self.prompts: list[JudgePrompt] = []

    def grade(self, prompt: JudgePrompt) -> JudgeReply:
        self.prompts.append(prompt)
        if isinstance(self.answer, JudgeError):
            raise self.answer
        output = self.answer(prompt) if callable(self.answer) else self.answer
        return JudgeReply(
            output=output, served_model="judge-1", input_tokens=1200, output_tokens=340
        )


class Call:
    def __init__(self, client: TestClient, scenario_id: str, seed: int = 7) -> None:
        self.c = client
        resp = client.post("/api/sessions", json={"scenario_id": scenario_id, "seed": seed})
        assert resp.status_code == 201, resp.text
        self.session = resp.json()
        self.sid: str = self.session["session_id"]
        self.base = f"/api/sessions/{self.sid}"
        self._ids = itertools.count(1)
        self.responses: list[str] = [resp.text]

    @property
    def service(self) -> SessionService:
        service: SessionService = self.c.app.state.sessions  # type: ignore[attr-defined]
        return service

    # HTTP

    def request(self, method: str, path: str, expect: int = 200, **kwargs: Any) -> Any:
        resp = self.c.request(method, self.base + path, **kwargs)
        assert resp.status_code == expect, f"{method} {path}: {resp.status_code} {resp.text}"
        self.responses.append(resp.text)
        return resp.json()

    def get(self, path: str, expect: int = 200, **params: Any) -> Any:
        return self.request("GET", path, expect, params=params)

    def post(self, path: str, body: Any = None, expect: int = 200) -> Any:
        return self.request("POST", path, expect, json=body)

    def put(self, path: str, body: Any, expect: int = 200) -> Any:
        return self.request("PUT", path, expect, json=body)

    # Lifecycle and talk

    def start(self) -> "Call":
        self.post("/start")
        return self

    def end(self) -> "Call":
        self.post("/end")
        return self

    def say(self, text: str) -> str:
        body = {
            "client_utt_id": f"learner-{next(self._ids):04d}",
            "speaker": "learner",
            "channel": "text",
            "text": text,
        }
        return str(self.post("/utterances", body, expect=201)["utt_id"])

    def turn(self, text: str, expect: int = 200) -> Any:
        """A learner line through the call screen's endpoint; the actor answers."""
        body = {"client_utt_id": f"turn-{next(self._ids):04d}", "text": text}
        return self.post("/turns", body, expect=expect)

    def hear(self, text: str, *, heard: str | None = None, interrupted: bool = False) -> str:
        """A customer turn. ``heard`` and ``interrupted`` simulate voice barge-in."""
        voice = heard is not None or interrupted
        utt = self.service.add_utterance(
            self.sid,
            UtteranceIn(
                client_utt_id=f"customer-{next(self._ids):04d}",
                speaker=Speaker.CUSTOMER,
                channel=Channel.VOICE if voice else Channel.TEXT,
                text=text,
                heard_text=heard,
                interrupted=interrupted,
            ),
        )
        return utt.utt_id

    def opening(self) -> "Call":
        """Recording notice, disclaimer and scope, in a compliant order."""
        self.say(GREETING)
        self.hear("Hi, I have some questions about my coverage.")
        self.say(DISCLAIMER)
        self.say(SCOPE_ASK)
        yes = self.hear("Yes, Medicare Advantage plans.")
        self.post("/scope", {"products": ["ma"], "source_utt_id": yes}, expect=201)
        return self

    # Research

    def network(self, practice_id: str, plan_id: str, year: int = 2027) -> int:
        path = "/research/providers/network"
        return int(self.get(path, practice_id=practice_id, plan_id=plan_id, year=year)["event_seq"])

    def formulary(self, plan_id: str, drug_id: str) -> int:
        return int(self.get("/research/formulary", plan_id=plan_id, drug_id=drug_id)["event_seq"])

    def pharmacy(self, plan_id: str, pharmacy_id: str) -> int:
        path = "/research/pharmacies/network"
        return int(self.get(path, plan_id=plan_id, pharmacy_id=pharmacy_id)["event_seq"])

    def document(self, document_id: str) -> int:
        return int(self.get(f"/documents/{document_id}")["event_seq"])

    def pin(self, seq: int) -> None:
        self.post("/evidence", {"source_seq": seq}, expect=201)

    def verify(
        self, plan_id: str, practices: list[str], drugs: list[str], pharmacy_id: str
    ) -> None:
        for practice_id in practices:
            self.pin(self.network(practice_id, plan_id))
        for drug_id in drugs:
            self.formulary(plan_id, drug_id)
        self.pharmacy(plan_id, pharmacy_id)

    # Records

    def save(self, section: str, data: dict[str, Any]) -> None:
        self.put(f"/record/{section}", data)

    def record(
        self,
        *,
        zip_code: str,
        doctors: list[dict[str, Any]],
        drugs: list[str],
        pharmacy_id: str,
        coverage: dict[str, Any],
        days_supply: int = 30,
    ) -> None:
        self.save("contact", {"residence_zip": zip_code})
        self.save("doctors", {"doctors": doctors})
        self.save(
            "drugs",
            {
                "status": "listed" if drugs else "none",
                "drugs": [{"drug_id": d, "days_supply": days_supply} for d in drugs],
            },
        )
        self.save("pharmacy", {"pharmacy_id": pharmacy_id})
        self.save("coverage", coverage)

    def decide(self, outcome: str, plan_id: str | None = None, rationale: str = "") -> None:
        self.save("disposition", {"outcome": outcome, "plan_id": plan_id, "rationale": rationale})

    # Enrollment

    def apply(self, plan_id: str, name: str, zip_code: str, pcp: str | None = None) -> str:
        body = {
            "plan_id": plan_id,
            "applicant_name": name,
            "residence_zip": zip_code,
            "pcp_practice_id": pcp,
            "effective_date": "2027-01-01",
        }
        return str(self.post("/applications", body, expect=201)["application"]["application_id"])

    def review_checklist(self, app_id: str, items: int = 9) -> None:
        for i in range(items):
            self.post(f"/applications/{app_id}/pecl", {"item_index": i})

    def consent(self, app_id: str, utt_id: str) -> None:
        self.post(f"/applications/{app_id}/consent", {"utt_id": utt_id})

    def submit(self, app_id: str, key: str = "submit-key-0001", expect: int = 200) -> Any:
        return self.post(f"/applications/{app_id}/submit", {"idempotency_key": key}, expect)

    def enroll(self, plan_id: str, name: str, zip_code: str, pcp: str | None = None) -> str:
        """Opens the SOB, reviews the checklist, takes a yes, submits."""
        short = {
            "TRN-1001-001": "maple-value",
            "TRN-1001-002": "maple-choice",
            "TRN-2002-001": "riverbend-essentials",
            "TRN-2002-002": "riverbend-select",
            "TRN-3003-001": "summit-classic",
            "TRN-3003-002": "summit-rewards",
        }[plan_id]
        self.document(f"sob-{short}-2027")
        app_id = self.apply(plan_id, name, zip_code, pcp)
        self.say("Let's go through the Pre-Enrollment Checklist together, item by item.")
        self.review_checklist(app_id)
        self.say("Would you like me to submit your enrollment in this plan?")
        yes = self.hear("Yes, please go ahead and sign me up.")
        self.consent(app_id, yes)
        self.submit(app_id)
        return app_id

    # Review

    def findings(self) -> dict[str, tuple[str, bool]]:
        body = self.get("/findings")
        return {r["check_id"]: (r["status"], r["critical"]) for r in body["results"]}

    def detail(self, check_id: str) -> dict[str, Any]:
        body = self.get("/findings")
        found: dict[str, Any] = next(r for r in body["results"] if r["check_id"] == check_id)
        return found

    def utt(self, fragment: str) -> str:
        """The id of the one utterance containing ``fragment``."""
        hits = [u.utt_id for u in self.service.transcript(self.sid) if fragment in u.text]
        assert len(hits) == 1, f"{fragment!r} is in {len(hits)} utterances"
        return hits[0]

    def judged(
        self, verdicts: Iterable[JudgeVerdict] = (), claims: Iterable[JudgeClaim] = ()
    ) -> FakeJudge:
        """Installs a judge that returns these rulings."""
        judge = FakeJudge(JudgeOutput(verdicts=list(verdicts), coverage_claims=list(claims)))
        self.service.judge = judge
        return judge

    def debrief(self, judge: bool = True) -> "DebriefView":
        """The debrief, after asking the judge unless ``judge`` is false."""
        body = self.post("/debrief/judge") if judge else self.get("/debrief")
        return DebriefView(body)


class DebriefView:
    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body

    def __getitem__(self, criterion_id: str) -> dict[str, Any]:
        found: dict[str, Any] = next(
            c for c in self.body["criteria"] if c["criterion_id"] == criterion_id
        )
        return found

    def status(self, criterion_id: str) -> tuple[str, bool]:
        c = self[criterion_id]
        return c["status"], c["critical"]

    def statuses(self) -> dict[str, str]:
        return {c["criterion_id"]: c["status"] for c in self.body["criteria"]}
