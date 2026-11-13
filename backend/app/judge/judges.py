"""Models that judge a finished call.

``ClaudeJudge`` is the real judge. With no judge configured the app still grades: the
deterministic checks score what they can, every judged item is "ungraded" and the call is
"incomplete".

Configuration:

- ``SIM_JUDGE``: ``claude`` or ``off``. Defaults to ``claude`` when ``ANTHROPIC_API_KEY`` is
  set, else ``off``.
- ``SIM_JUDGE_MODEL``: the Claude model id (default ``claude-opus-5-5``). Each stored judgment
  records the model that was asked and the model that answered.
- ``SIM_JUDGE_TIMEOUT_S``: how long one grading request may take (default 300).
"""

import math
import os
from dataclasses import dataclass
from typing import Protocol

import anthropic
from pydantic import ValidationError

from app.judge.contract import JudgeOutput, output_schema
from app.judge.prompt import JudgePrompt

JUDGE_ENV = "SIM_JUDGE"
MODEL_ENV = "SIM_JUDGE_MODEL"
KEY_ENV = "ANTHROPIC_API_KEY"
TIMEOUT_ENV = "SIM_JUDGE_TIMEOUT_S"
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_TIMEOUT_S = 300.0
MIN_TIMEOUT_S = 5.0
MAX_TIMEOUT_S = 1800.0
MAX_TOKENS = 32000
"""Room for the rulings and the model's thinking. The reply is streamed, so a long one doesn't
hit an HTTP timeout."""
FALLBACK_BETA = "server-side-fallback-2026-07-01"
"""If the judge model declines a request for policy reasons, Anthropic reruns it on that
model's default fallback inside the same call. The judgment records which model answered."""


class JudgeError(Exception):
    """The judge gave no usable answer. Nothing is stored; grading can be asked for again."""


class JudgeConfigError(ValueError):
    pass


@dataclass(frozen=True)
class JudgeReply:
    output: JudgeOutput
    served_model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class Judge(Protocol):
    @property
    def name(self) -> str:
        """Stored on each judgment, e.g. ``claude:claude-opus-5-5``."""
        ...

    def grade(self, prompt: JudgePrompt) -> JudgeReply:
        """Rulings for one call. Raises JudgeError if the model gives no usable answer."""
        ...


class ClaudeJudge:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        client: anthropic.Anthropic | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.model = model
        # The SDK finds the credentials itself (ANTHROPIC_API_KEY and its other sources) and
        # retries connection errors, 429s and 5xxs before giving up.
        self._client = client or anthropic.Anthropic(timeout=timeout_s)

    @property
    def name(self) -> str:
        return f"claude:{self.model}"

    def grade(self, prompt: JudgePrompt) -> JudgeReply:
        try:
            with self._client.beta.messages.stream(
                model=self.model,
                max_tokens=MAX_TOKENS,
                betas=[FALLBACK_BETA],
                fallbacks="default",
                # The instructions, rubric and catalog are the same for every call on one
                # content version, so a run of gradings reads them from the cache.
                system=[
                    {
                        "type": "text",
                        "text": prompt.system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": prompt.user}],
                output_config={
                    "effort": "high",
                    "format": {"type": "json_schema", "schema": output_schema()},
                },
            ) as stream:
                message = stream.get_final_message()
        except anthropic.APIConnectionError as exc:
            raise JudgeError(f"{self.name} could not be reached: {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise JudgeError(f"{self.name} is rate limited; try again shortly") from exc
        except anthropic.APIStatusError as exc:
            raise JudgeError(f"{self.name} failed ({exc.status_code}): {exc.message}") from exc
        if message.stop_reason == "refusal":
            raise JudgeError(f"{self.name} declined to grade this call")
        if message.stop_reason == "max_tokens":
            raise JudgeError(f"{self.name} ran out of room before finishing its rulings")
        text = next((b.text for b in message.content if b.type == "text"), "")
        try:
            output = JudgeOutput.model_validate_json(text)
        except ValidationError as exc:
            raise JudgeError(f"{self.name} returned rulings that don't fit the contract") from exc
        return JudgeReply(
            output=output,
            served_model=message.model,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
        )


def judge_from_env(env: dict[str, str] | None = None) -> Judge | None:
    """The configured judge, or None when grading runs on the deterministic checks alone."""
    env = dict(os.environ) if env is None else env
    kind = env.get(JUDGE_ENV) or ("claude" if env.get(KEY_ENV) else "off")
    if kind == "off":
        return None
    if kind != "claude":
        raise JudgeConfigError(f"{JUDGE_ENV} must be 'claude' or 'off', not {kind!r}")
    return ClaudeJudge(
        model=env.get(MODEL_ENV) or DEFAULT_MODEL, timeout_s=judge_timeout_from_env(env)
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
