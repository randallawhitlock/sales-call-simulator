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


def judge_with(reply: Any) -> tuple[ClaudeJudge, FakeMessages]:
    messages = FakeMessages(reply)
    client = SimpleNamespace(messages=messages)
    return ClaudeJudge(model="m-1", api_key="k", timeout_s=5, client=client), messages


def test_a_grade_is_parsed_from_structured_output() -> None:
    judge, messages = judge_with(message(json.dumps(GRADE)))
    out = judge.grade(PROMPT)
    assert out == JudgeOutput.model_validate(GRADE)
    assert judge.name == "claude:m-1"
    assert messages.kwargs["model"] == "m-1"
    assert messages.kwargs["system"] == "sys"
    assert messages.kwargs["messages"] == [{"role": "user", "content": "user"}]
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
