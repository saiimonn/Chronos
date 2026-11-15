from fastapi.testclient import TestClient

from app.main import app

# TestClient defaults to the host "testserver", which is not a trusted host.
client = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000))


def test_health_check_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_cors_exposes_content_disposition_to_allowed_origin():
    from app.core.config import settings

    response = client.get("/health", headers={"Origin": settings.cors_origins[0]})
    exposed = response.headers.get("access-control-expose-headers", "")
    assert "content-disposition" in exposed.lower()
