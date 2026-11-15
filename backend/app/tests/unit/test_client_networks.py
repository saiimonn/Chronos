from ipaddress import ip_network

from fastapi.testclient import TestClient

from app.core.config import Settings, settings
from app.main import create_app


def get_health(client_host: str, headers: dict | None = None):
    client = TestClient(
        create_app(), base_url="http://localhost", client=(client_host, 50000)
    )
    return client.get("/health", headers=headers)


def test_default_networks_allow_only_loopback(monkeypatch):
    # Pin the built-in default even if a local backend/.env opens a LAN subnet.
    monkeypatch.delenv("ALLOWED_CLIENT_NETWORKS", raising=False)
    defaults = Settings(_env_file=None).allowed_client_networks
    monkeypatch.setattr(settings, "allowed_client_networks", defaults)

    assert get_health("127.0.0.1").status_code == 200
    assert get_health("::1").status_code == 200
    assert get_health("::ffff:127.0.0.1").status_code == 200

    refused = get_health("192.168.1.20")
    assert refused.status_code == 403
    assert refused.json()["code"] == "UNTRUSTED_CLIENT"
    assert get_health("testclient").status_code == 403


def test_configured_subnet_allows_lan_clients_and_refuses_others(monkeypatch):
    monkeypatch.setattr(
        settings,
        "allowed_client_networks",
        [ip_network("127.0.0.0/8"), ip_network("192.168.1.0/24")],
    )

    assert get_health("192.168.1.20").status_code == 200
    assert get_health("::ffff:192.168.1.20").status_code == 200
    assert get_health("192.168.2.20").status_code == 403
    assert get_health("10.0.0.5").status_code == 403
    # A forwarded-for header never widens access; only the peer address counts.
    spoofed = get_health("10.0.0.5", headers={"X-Forwarded-For": "192.168.1.20"})
    assert spoofed.status_code == 403


def test_client_networks_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("ALLOWED_CLIENT_NETWORKS", '["127.0.0.0/8","192.168.1.10/24"]')

    networks = Settings(_env_file=None).allowed_client_networks

    assert networks == [ip_network("127.0.0.0/8"), ip_network("192.168.1.0/24")]


def test_built_frontend_is_served_with_spa_fallback(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log('chronos')")
    (tmp_path / "index.html").write_text("<div id=root></div>")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    monkeypatch.setattr(settings, "frontend_dist_dir", str(tmp_path))
    client = TestClient(
        create_app(), base_url="http://localhost", client=("127.0.0.1", 50000)
    )

    assert client.get("/assets/app.js").text == "console.log('chronos')"
    spa = client.get("/schedule")
    assert spa.text == "<div id=root></div>"
    assert spa.headers["Cache-Control"] == "no-cache"
    # The explicit entrypoint path must revalidate too.
    for response in (client.get("/index.html"), client.head("/index.html")):
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-cache"
    assert client.get("/" + "x" * 300).text == "<div id=root></div>"
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/favicon.svg").text == "<svg/>"
    assert client.head("/").status_code == 200
    assert client.get("/api/v1/missing").json()["code"] == "NOT_FOUND"
    # API routing is unchanged: wrong method is 405, trailing slash redirects.
    assert client.get("/api/v1/auth/login").status_code == 405
    redirect = client.get("/health/", follow_redirects=False)
    assert redirect.status_code == 307
    assert client.post("/schedule").status_code == 404
