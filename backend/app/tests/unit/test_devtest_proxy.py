"""The official app relays /devtest/ to the test app, bytes and cookies intact."""

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import create_app


def fake_test_app() -> FastAPI:
    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def echo(path: str, request: Request):
        response = Response(
            content=f"{request.method} /{path}?{request.url.query} "
            f"host={request.headers['host']} body={(await request.body()).decode()}"
            f"{' cookie=' + request.headers['cookie'] if 'cookie' in request.headers else ''}",
            status_code=201,
        )
        response.set_cookie("chronos_test_session", "1")
        response.set_cookie("chronos_test_other", "2", path="/api")
        # Production's cookie name: a test branch must never set it.
        response.set_cookie("chronos_session", "stolen")
        return response

    return app


def official_client(monkeypatch, transport) -> TestClient:
    monkeypatch.setattr(settings, "devtest_upstream", "http://127.0.0.1:20310")
    real = httpx.AsyncClient

    def client_with_transport(**kwargs):
        return real(transport=transport, **kwargs)

    monkeypatch.setattr("app.devtest_proxy.httpx.AsyncClient", client_with_transport)
    return TestClient(
        create_app(),
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
        follow_redirects=False,
    )


def test_devtest_is_relayed_without_its_prefix(monkeypatch):
    client = official_client(monkeypatch, httpx.ASGITransport(app=fake_test_app()))

    response = client.post("/devtest/api/v1/x?y=1", content="hello")

    assert response.status_code == 201
    assert response.text == "POST /api/v1/x?y=1 host=localhost body=hello"
    assert response.headers.get_list("set-cookie") == [
        "chronos_test_session=1; Path=/devtest/; SameSite=lax",
        "chronos_test_other=2; Path=/api; SameSite=lax",
    ]
    assert client.get("/devtest").headers["location"] == "/devtest/"
    assert client.get("/devtest/").text.startswith("GET /?")


def test_devtest_never_shares_one_visitors_cookies_with_another(monkeypatch):
    client = official_client(monkeypatch, httpx.ASGITransport(app=fake_test_app()))

    client.post("/devtest/login", content="")  # answers with Set-Cookie
    client.cookies.clear()  # the next visitor has no cookies

    assert "cookie=" not in client.get("/devtest/me").text
    # A visitor's own test cookie still reaches the test app; production's
    # session cookie and any other cookie never do.
    own = client.get(
        "/devtest/me",
        headers={"Cookie": "chronos_session=prod; chronos_test_session=x; theme=dark"},
    )
    assert own.text.endswith("cookie=chronos_test_session=x")
    prod_only = client.get("/devtest/me", headers={"Cookie": "chronos_session=prod"})
    assert "cookie=" not in prod_only.text


def test_devtest_drops_client_forwarding_headers(monkeypatch):
    seen = {}
    upstream = FastAPI()

    @upstream.get("/")
    async def capture(request: Request):
        seen.update(request.headers)
        return {}

    client = official_client(monkeypatch, httpx.ASGITransport(app=upstream))
    client.get("/devtest/", headers={"X-Forwarded-For": "10.0.0.1", "X-Forwarded-Host": "evil"})

    assert seen["host"] == "localhost"
    assert not any(k.startswith("x-forwarded-") for k in seen)


def test_devtest_reports_a_stopped_test_app(monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    client = official_client(monkeypatch, httpx.MockTransport(refuse))

    response = client.get("/devtest/")

    assert response.status_code == 502
    assert response.json()["code"] == "DEVTEST_UNAVAILABLE"


def test_no_devtest_route_without_an_upstream(monkeypatch):
    monkeypatch.setattr(settings, "devtest_upstream", None)
    client = TestClient(create_app(), base_url="http://localhost", client=("127.0.0.1", 50000))
    assert client.get("/devtest/").status_code == 404
