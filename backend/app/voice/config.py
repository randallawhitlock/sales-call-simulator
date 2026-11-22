"""Voice settings, read from the environment by the API and the worker alike.

Voice is on when ``SIM_LIVE_MODEL`` is set. The model id is never defaulted, so every voice
session records the exact model it ran. Configuration:

- ``SIM_LIVE_MODEL``: the Gemini Live model id, e.g. ``gemini-3.8-live``.
- ``GEMINI_API_KEY``: the Gemini key. The worker hands it to the LiveKit plugin explicitly, so
  ``GOOGLE_API_KEY`` (what the plugin reads on its own) isn't needed.
- ``LIVEKIT_URL``: the LiveKit server the browser and the worker join (default
  ``ws://127.0.0.1:7880``, where ``livekit-server --dev`` listens).
- ``LIVEKIT_API_KEY``, ``LIVEKIT_API_SECRET``: LiveKit credentials. Never defaulted;
  ``livekit-server --dev`` uses ``devkey`` and ``secret``.
- ``SIM_VOICE_SILENCE_MS``: how long Gemini waits after the rep stops talking before it
  answers (default 700). The spike's 500 ms clipped spoken digits; see docs/voice-spike.md.
- ``SIM_VOICE_NUDGE_S``: seconds of silence on both sides, with the customer not on hold,
  before she checks the agent is still there (default 25; 0 turns nudges off). Each later
  nudge waits twice as long.
- ``SIM_RECORDINGS_DIR``: where call audio is saved (default ``recordings``).
- ``SIM_LIVE_PRICE_IN_PER_MIN``, ``SIM_LIVE_PRICE_OUT_PER_MIN``: dollars per minute of audio
  in and out, for the per-call cost estimate (defaults 0.005 and 0.018, the paid-tier figures in
  docs/voice-spike.md).
"""

import math
import os
from dataclasses import dataclass
from pathlib import Path

MODEL_ENV = "SIM_LIVE_MODEL"
KEY_ENV = "GEMINI_API_KEY"
URL_ENV = "LIVEKIT_URL"
LK_KEY_ENV = "LIVEKIT_API_KEY"
LK_SECRET_ENV = "LIVEKIT_API_SECRET"
SILENCE_ENV = "SIM_VOICE_SILENCE_MS"
NUDGE_ENV = "SIM_VOICE_NUDGE_S"
RECORDINGS_ENV = "SIM_RECORDINGS_DIR"
PRICE_IN_ENV = "SIM_LIVE_PRICE_IN_PER_MIN"
PRICE_OUT_ENV = "SIM_LIVE_PRICE_OUT_PER_MIN"

DEFAULT_URL = "ws://127.0.0.1:7880"
DEFAULT_SILENCE_MS = 700
DEFAULT_NUDGE_S = 25.0
DEFAULT_PRICE_IN_PER_MIN = 0.005
DEFAULT_PRICE_OUT_PER_MIN = 0.018

AGENT_NAME = "sim-customer"
"""The worker registers under this name and is dispatched only to rooms whose join token asks
for it, so it never joins a room it wasn't sent to."""
LEARNER_IDENTITY = "learner"
WRAP_UP_RPC = "sim.wrap_up"
"""The call screen calls this on the agent before ending the call, so lines still being
transcribed are saved first."""
DROP_MODEL_RPC = "sim.drop_model"
"""Testing only: cuts the worker's Gemini connection mid-call, to check reconnects. Registered
only when ``SIM_VOICE_DEBUG_DROPS=1``."""
DEBUG_DROPS_ENV = "SIM_VOICE_DEBUG_DROPS"

VOICES = {
    "warm-alto": "Aoede",
    "bright-mezzo": "Zephyr",
    "gravel-baritone": "Charon",
    "steady-baritone": "Orus",
    "dry-tenor": "Puck",
    "warm-tenor": "Fenrir",
}
"""Persona voices in content are provider-neutral; these are the Gemini prebuilt voices that
play them. Every voice a case can roll must be here (a test checks)."""


class VoiceConfigError(ValueError):
    pass


@dataclass(frozen=True)
class VoiceConfig:
    model: str
    api_key: str
    livekit_url: str
    livekit_key: str
    livekit_secret: str
    silence_ms: int = DEFAULT_SILENCE_MS
    nudge_s: float = DEFAULT_NUDGE_S
    recordings_dir: Path = Path("recordings")
    price_in_per_min: float = DEFAULT_PRICE_IN_PER_MIN
    price_out_per_min: float = DEFAULT_PRICE_OUT_PER_MIN

    @property
    def actor(self) -> str:
        """Pinned on voice sessions the way ``gemini:<model>`` is pinned on text ones."""
        return f"gemini-live:{self.model}"

    @staticmethod
    def room_for(session_id: str) -> str:
        return f"call-{session_id}"

    @staticmethod
    def session_for(room: str) -> str | None:
        return room.removeprefix("call-") if room.startswith("call-") else None

    def recording_path(self, session_id: str) -> Path:
        return self.recordings_dir / f"{session_id}.ogg"


def voice_from_env(env: dict[str, str] | None = None) -> VoiceConfig | None:
    """None when voice is off. A half-configured voice setup is an error, not a silent off."""
    env = dict(os.environ) if env is None else env
    model = env.get(MODEL_ENV, "")
    if not model:
        return None
    missing = [k for k in (KEY_ENV, LK_KEY_ENV, LK_SECRET_ENV) if not env.get(k)]
    if missing:
        raise VoiceConfigError(f"voice ({MODEL_ENV}={model}) also needs {', '.join(missing)}")
    return VoiceConfig(
        model=model,
        api_key=env[KEY_ENV],
        livekit_url=env.get(URL_ENV) or DEFAULT_URL,
        livekit_key=env[LK_KEY_ENV],
        livekit_secret=env[LK_SECRET_ENV],
        silence_ms=int(_number(env, SILENCE_ENV, DEFAULT_SILENCE_MS, 100, 5000)),
        nudge_s=_number(env, NUDGE_ENV, DEFAULT_NUDGE_S, 0, 600),
        recordings_dir=Path(env.get(RECORDINGS_ENV) or "recordings"),
        price_in_per_min=_number(env, PRICE_IN_ENV, DEFAULT_PRICE_IN_PER_MIN, 0, 10),
        price_out_per_min=_number(env, PRICE_OUT_ENV, DEFAULT_PRICE_OUT_PER_MIN, 0, 10),
    )


def _number(env: dict[str, str], name: str, default: float, lo: float, hi: float) -> float:
    raw = env.get(name, "")
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not (math.isfinite(value) and lo <= value <= hi):
        raise VoiceConfigError(f"{name} must be a number from {lo:g} to {hi:g}, not {raw!r}")
    return value
