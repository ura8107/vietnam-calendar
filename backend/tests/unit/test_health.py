from fastapi.testclient import TestClient
from vietnam_calendar.api import app


def test_health_does_not_require_database():
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
