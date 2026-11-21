"""The Claude judge client and its configuration, with a fake SDK client. No network."""

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.judge.judges import (
    DEFAULT_MODEL,
    ClaudeJudge,
    JudgeConfigError,
    JudgeError,
    judge_from_env,
    judge_timeout_from_env,
)
from app.judge.models import JudgeOutput, output_schema
from app.judge.prompt import JudgePrompt

PROMPT = JudgePrompt(system="sys", user="user", criterion_ids=("disc-doctors",))
GRADE = {
    "criteria": [
        {
            "criterion_id": "disc-doctors",
            "status": "pass",
            "rationale": "Asked about her doctors.",
            "evidence": [{"utt_id": "u0003", "quote": "which doctors"}],
        }
    ],
    "coverage_claims": [],
}


class FakeMessages:
    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def message(text: str, stop_reason: str = "end_turn") -> Any:
    return SimpleNamespace(
        stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)]
    )


def judge_with(reply: Any, timeout_s: float = 120) -> tuple[ClaudeJudge, FakeMessages]:
    messages = FakeMessages(reply)
    client = SimpleNamespace(messages=messages)
    judge = ClaudeJudge(
        model="m-1", api_key="k", timeout_s=timeout_s, client=client, sleep=lambda s: None
    )
    return judge, messages


class Flaky(FakeMessages):
    """Fails with each error in turn, then answers."""

    def __init__(self, errors: list[Exception], reply: Any) -> None:
        super().__init__(reply)
        self.errors = errors
        self.timeouts: list[float] = []

    def create(self, **kwargs: Any) -> Any:
        self.timeouts.append(kwargs["timeout"])
        if self.errors:
            raise self.errors.pop(0)
        return super().create(**kwargs)


class ApiStatus(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class APIConnectionError(Exception):
    pass


class APITimeoutError(Exception):
    pass


def flaky_judge(errors: list[Exception], timeout_s: float = 120) -> tuple[ClaudeJudge, Flaky]:
    messages = Flaky(errors, message(json.dumps(GRADE)))
    judge = ClaudeJudge(
        model="m-1",
        api_key="k",
        timeout_s=timeout_s,
        client=SimpleNamespace(messages=messages),
        sleep=lambda s: None,
    )
    return judge, messages


@pytest.mark.parametrize("error", [ApiStatus(429), ApiStatus(529), APIConnectionError("reset")])
def test_one_retry_inside_the_deadline(error: Exception) -> None:
    judge, messages = flaky_judge([error])
    assert judge.grade(PROMPT) == JudgeOutput.model_validate(GRADE)
    assert len(messages.timeouts) == 2
    assert all(0 < t <= 120 for t in messages.timeouts)  # each try gets only what's left


@pytest.mark.parametrize(
    ("errors", "timeout_s", "tries"),
    [
        ([ApiStatus(529), ApiStatus(529)], 120, 2),  # one retry, not more
        ([ApiStatus(400)], 120, 1),  # our request is wrong; asking again won't help
        ([APITimeoutError("timed out")], 120, 1),  # the run's time is spent
        ([ApiStatus(529)], 15, 1),  # too little time left to start again
    ],
)
def test_no_retry_when_it_cannot_help(
    errors: list[Exception], timeout_s: float, tries: int
) -> None:
    judge, messages = flaky_judge(errors, timeout_s)
    with pytest.raises(JudgeError):
        judge.grade(PROMPT)
    assert len(messages.timeouts) == tries


def test_a_grade_is_parsed_from_structured_output() -> None:
    judge, messages = judge_with(message(json.dumps(GRADE)))
    out = judge.grade(PROMPT)
    assert out == JudgeOutput.model_validate(GRADE)
    assert judge.name == "claude:m-1"
    assert messages.kwargs["model"] == "m-1"
    assert messages.kwargs["system"] == "sys"
    assert messages.kwargs["messages"] == [{"role": "user", "content": "user"}]
    assert 0 < messages.kwargs["timeout"] <= 120
    fmt = messages.kwargs["output_config"]["format"]
    assert fmt == {"type": "json_schema", "schema": output_schema()}


@pytest.mark.parametrize(
    ("reply", "says"),
    [
        (RuntimeError("connection reset"), "connection reset"),
        (message(json.dumps(GRADE), stop_reason="max_tokens"), "ran out of output tokens"),
        (message("not json"), "isn't a grade"),
        (message(json.dumps({"criteria": [{"criterion_id": "x"}]})), "isn't a grade"),
    ],
)
def test_failures_raise_judge_error(reply: Any, says: str) -> None:
    judge, _ = judge_with(reply)
    with pytest.raises(JudgeError, match=says):
        judge.grade(PROMPT)


def test_the_schema_closes_every_object() -> None:
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                walk(value)

    walk(output_schema())


def test_the_judge_is_off_without_a_key() -> None:
    assert judge_from_env({}) is None
    assert judge_from_env({"ANTHROPIC_API_KEY": "k", "SIM_JUDGE": "off"}) is None


def test_the_judge_defaults_its_model() -> None:
    judge = judge_from_env({"ANTHROPIC_API_KEY": "k"})
    assert judge is not None and judge.name == f"claude:{DEFAULT_MODEL}"
    pinned = judge_from_env({"ANTHROPIC_API_KEY": "k", "SIM_JUDGE_MODEL": "claude-x"})
    assert pinned is not None and pinned.name == "claude:claude-x"


@pytest.mark.parametrize(
    "env",
    [
        {"SIM_JUDGE": "claude"},
        {"SIM_JUDGE": "gpt", "ANTHROPIC_API_KEY": "k"},
        {"ANTHROPIC_API_KEY": "k", "SIM_JUDGE_TIMEOUT_S": "0"},
        {"ANTHROPIC_API_KEY": "k", "SIM_JUDGE_TIMEOUT_S": "soon"},
    ],
)
def test_bad_judge_config_is_refused(env: dict[str, str]) -> None:
    with pytest.raises(JudgeConfigError):
        judge_from_env(env)


def test_the_timeout_defaults_and_parses() -> None:
    assert judge_timeout_from_env({}) == 180
    assert judge_timeout_from_env({"SIM_JUDGE_TIMEOUT_S": "45"}) == 45


def test_a_claim_carries_the_year_it_is_about() -> None:
    claim = output_schema()["properties"]["coverage_claims"]["items"]  # type: ignore[index]
    assert claim["properties"]["year"] == {"type": "integer"}
    assert "year" in claim["required"]
    # Output stored before the field existed still loads, and means the plan year.
    stored = {
        "utt_id": "u0001",
        "quote": "is covered",
        "plan_id": "TRN-1001-002",
        "kind": "formulary",
        "subject_id": "apixaban-5mg-tab",
        "covered": True,
    }
    out = JudgeOutput.model_validate({"criteria": [], "coverage_claims": [stored]})
    assert out.coverage_claims[0].year is None
