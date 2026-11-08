from fastapi.testclient import TestClient

from app import __version__


def test_health_reports_loaded_content(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["ruleset_version"] == "mvp-2027.1"
    assert body["rules"] == 9
    assert body["catalog_version"] == "2027-v1"
    assert body["scenarios"] == 3


def test_openapi_schema_builds(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert "/api/sessions/{session_id}/findings" in schema["paths"]
