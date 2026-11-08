from fastapi.testclient import TestClient

from app import __version__
from app.main import app


def test_health_reports_loaded_content() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["ruleset_version"] == "mvp-2027.1"
    assert body["rules"] == 9
