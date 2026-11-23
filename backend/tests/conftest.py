from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.voice import rooms
from app.voice.config import VoiceConfig


class StepClock:
    """Advances one second per reading, so event times are distinct and repeatable."""

    def __init__(self) -> None:
        self.now = datetime(2026, 11, 12, 15, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


VOICE_ENV = {
    "SIM_LIVE_MODEL": "test-live-model",
    "GEMINI_API_KEY": "test-gemini-key",
    "LIVEKIT_URL": "ws://127.0.0.1:7880",
    "LIVEKIT_API_KEY": "test-livekit-key",
    "LIVEKIT_API_SECRET": "test-livekit-secret-for-the-suite-only",
}
"""Fake values: voice tests mint tokens but never reach LiveKit or Gemini."""


def _client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SIM_DB_PATH", ":memory:")
    monkeypatch.setenv("SIM_ACTOR", "scripted")  # never call a real model from the test suite
    monkeypatch.setenv("SIM_JUDGE", "off")
    with TestClient(app) as c:
        app.state.sessions.clock = StepClock()
        yield c


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    for name in VOICE_ENV:
        monkeypatch.delenv(name, raising=False)
    yield from _client(monkeypatch)


@pytest.fixture
def voice_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    """A server with voice calls on."""
    for name, value in VOICE_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("SIM_RECORDINGS_DIR", str(tmp_path / "recordings"))
    # Joining checks the room for the customer, which would reach LiveKit.
    monkeypatch.setattr(rooms, "ensure_customer", _no_room)
    yield from _client(monkeypatch)


async def _no_room(config: VoiceConfig, session_id: str) -> bool:
    return False
