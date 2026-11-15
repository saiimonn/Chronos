from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import create_app
from app.services.security import hash_password


def client() -> TestClient:
    return TestClient(
        create_app(), base_url="http://localhost", client=("127.0.0.1", 50000)
    )


def past_gate(response) -> bool:
    return response.json().get("code") != "DEV_ACCESS_REQUIRED"


def test_gate_is_off_when_no_developers_are_configured(monkeypatch):
    monkeypatch.setattr(settings, "dev_access_users", {})
    assert past_gate(client().get("/api/v1/terms"))


def test_gate_requires_developer_credentials(monkeypatch):
    monkeypatch.setattr(settings, "dev_access_users", {"dev": hash_password("s3cret-pass")})
    c = client()

    refused = c.get("/api/v1/terms")
    assert refused.status_code == 401
    assert refused.json()["code"] == "DEV_ACCESS_REQUIRED"
    assert refused.headers["WWW-Authenticate"].startswith("Basic ")

    assert c.get("/api/v1/terms", auth=("dev", "wrong")).status_code == 401
    assert c.get("/api/v1/terms", auth=("someone", "s3cret-pass")).status_code == 401
    bad_header = c.get("/api/v1/terms", headers={"Authorization": "Basic !!!"})
    assert bad_header.status_code == 401

    # Past the gate, the app's own login still applies.
    assert past_gate(c.get("/api/v1/terms", auth=("dev", "s3cret-pass")))
    # Second request takes the cached path.
    assert past_gate(c.get("/api/v1/terms", auth=("dev", "s3cret-pass")))


def test_health_checks_pass_the_gate(monkeypatch):
    monkeypatch.setattr(settings, "dev_access_users", {"dev": hash_password("s3cret-pass")})
    assert client().get("/health").status_code == 200
