from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
        readiness = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "dataquery-agent"}
    assert readiness.status_code == 200
    assert readiness.json()["components"]["sqlite"] is True
    assert readiness.json()["components"]["duckdb"] is True
