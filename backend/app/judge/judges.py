"""Models that judge a call.

``ClaudeJudge`` is the only judge. With no ``ANTHROPIC_API_KEY`` there is no judge at all:
judged criteria stay ungraded and the debrief says the call is incomplete. There is no offline
stand-in, because a heuristic judge would hand out grades nobody could defend.

Configuration:

- ``SIM_JUDGE``: ``claude`` or ``off``. Defaults to ``claude`` when ``ANTHROPIC_API_KEY`` is
  set, else ``off``.
- ``SIM_JUDGE_MODEL``: the Claude model id (default ``claude-opus-5-5``). Every stored grade
  records the model that produced it.
- ``SIM_JUDGE_TIMEOUT_S``: how long one grading run may take in all, a retry included
  (default 180).
"""

import json
import math
import os
import time
from collections.abc import Callable
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
MAX_ATTEMPTS = 2
RETRY_WAIT_S = 2.0
MIN_RETRY_S = 20.0
"""A retry isn't started with less than this left of the run's time."""


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
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        timeout_s: float,
        client: Any = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.model = model
        self.timeout_s = timeout_s
        self._sleep = sleep
        if client is None:
            import anthropic

            # Retries happen here, inside one deadline, not in the SDK on top of it.
            client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s, max_retries=0)
        self._client = client

    @property
    def name(self) -> str:
        return f"claude:{self.model}"

    def grade(self, prompt: JudgePrompt) -> JudgeOutput:
        message = self._ask(prompt)
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

    def _ask(self, prompt: JudgePrompt) -> Any:
        """One request, retried once on a rate limit, an overload or a dropped connection.
        The whole run, wait and retry included, fits in ``timeout_s``."""
        deadline = time.monotonic() + self.timeout_s
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return self._client.messages.create(
                    model=self.model,
                    max_tokens=MAX_TOKENS,
                    system=prompt.system,
                    messages=[{"role": "user", "content": prompt.user}],
                    output_config={"format": {"type": "json_schema", "schema": output_schema()}},
                    timeout=max(deadline - time.monotonic(), 1.0),
                )
            except Exception as exc:  # the SDK's errors, timeouts and transport failures alike
                left = deadline - time.monotonic() - RETRY_WAIT_S
                if attempt == MAX_ATTEMPTS or not _worth_retrying(exc) or left < MIN_RETRY_S:
                    raise JudgeError(f"{self.name} failed: {exc}") from exc
                self._sleep(RETRY_WAIT_S)
        raise AssertionError("unreachable")


def _worth_retrying(exc: Exception) -> bool:
    """Rate limits, overloads and server errors by status; a dropped connection by name. A
    timeout isn't: the run's time is spent."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status == 429 or status >= 500
    return type(exc).__name__ == "APIConnectionError"


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
