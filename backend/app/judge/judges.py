"""Models that judge a call.

``ClaudeJudge`` is the only judge. With no ``ANTHROPIC_API_KEY`` there is no judge at all:
judged criteria stay ungraded and the debrief says the call is incomplete. There is no offline
stand-in, because a heuristic judge would hand out grades nobody could defend.

Configuration:

- ``SIM_JUDGE``: ``claude`` or ``off``. Defaults to ``claude`` when ``ANTHROPIC_API_KEY`` is
  set, else ``off``.
- ``SIM_JUDGE_MODEL``: the Claude model id (default ``claude-opus-5-5``). Every stored grade
  records the model that produced it.
- ``SIM_JUDGE_TIMEOUT_S``: how long one grading run may take (default 180).
"""

import json
import math
import os
from typing import Any, Protocol

from pydantic import ValidationError

from app.judge.models import JudgeOutput, output_schema
from app.judge.prompt import JudgePrompt

JUDGE_ENV = "SIM_JUDGE"
MODEL_ENV = "SIM_JUDGE_MODEL"
KEY_ENV = "ANTHROPIC_API_KEY"
TIMEOUT_ENV = "SIM_JUDGE_TIMEOUT_S"
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_TIMEOUT_S = 180.0
MIN_TIMEOUT_S = 1.0
MAX_TIMEOUT_S = 900.0
MAX_TOKENS = 16000


class JudgeError(Exception):
    """The judge failed or returned nothing usable. Nothing is stored; grading again asks
    again."""


class JudgeConfigError(ValueError):
    pass


class Judge(Protocol):
    @property
    def name(self) -> str:
        """Stored with each grade, e.g. ``claude:<model>``."""
        ...

    def grade(self, prompt: JudgePrompt) -> JudgeOutput: ...


class ClaudeJudge:
    def __init__(self, *, model: str, api_key: str, timeout_s: float, client: Any = None) -> None:
        self.model = model
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s, max_retries=2)
        self._client = client

    @property
    def name(self) -> str:
        return f"claude:{self.model}"

    def grade(self, prompt: JudgePrompt) -> JudgeOutput:
        try:
            message = self._client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=prompt.system,
                messages=[{"role": "user", "content": prompt.user}],
                output_config={"format": {"type": "json_schema", "schema": output_schema()}},
            )
        except Exception as exc:  # the SDK's errors, timeouts and transport failures alike
            raise JudgeError(f"{self.name} failed: {exc}") from exc
        if getattr(message, "stop_reason", None) == "max_tokens":
            raise JudgeError(f"{self.name} ran out of output tokens")
        text = "".join(
            getattr(block, "text", "")
            for block in getattr(message, "content", [])
            if getattr(block, "type", None) == "text"
        )
        try:
            return JudgeOutput.model_validate(json.loads(text))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise JudgeError(f"{self.name} returned output that isn't a grade: {exc}") from exc


def judge_from_env(env: dict[str, str] | None = None) -> Judge | None:
    env = dict(os.environ) if env is None else env
    key = env.get(KEY_ENV, "")
    kind = env.get(JUDGE_ENV) or ("claude" if key else "off")
    if kind == "off":
        return None
    if kind != "claude":
        raise JudgeConfigError(f"{JUDGE_ENV} must be 'claude' or 'off', not {kind!r}")
    if not key:
        raise JudgeConfigError(f"the claude judge needs {KEY_ENV}")
    return ClaudeJudge(
        model=env.get(MODEL_ENV) or DEFAULT_MODEL,
        api_key=key,
        timeout_s=judge_timeout_from_env(env),
    )


def judge_timeout_from_env(env: dict[str, str] | None = None) -> float:
    env = dict(os.environ) if env is None else env
    raw = env.get(TIMEOUT_ENV, "")
    if not raw:
        return DEFAULT_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not (math.isfinite(value) and MIN_TIMEOUT_S <= value <= MAX_TIMEOUT_S):
        raise JudgeConfigError(
            f"{TIMEOUT_ENV} must be seconds from {MIN_TIMEOUT_S:g} to {MAX_TIMEOUT_S:g}, "
            f"not {raw!r}"
        )
    return value
