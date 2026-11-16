from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app


class StepClock:
    """Advances one second per reading, so event times are distinct and repeatable."""

    def __init__(self) -> None:
        self.now = datetime(2026, 11, 12, 15, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SIM_DB_PATH", ":memory:")
    monkeypatch.setenv("SIM_ACTOR", "scripted")  # never call a real model from the test suite
    monkeypatch.setenv("SIM_JUDGE", "off")
    with TestClient(app) as c:
        app.state.sessions.clock = StepClock()
        yield c
