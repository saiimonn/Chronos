from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.errors import APIError
from app.main import create_app


def make_client() -> TestClient:
    app = create_app()

    @app.get("/_test/conflict")
    def conflict():
        raise APIError(409, "TEST_CONFLICT", "A safe conflict occurred.")

    @app.get("/_test/http-error")
    def http_error():
        raise HTTPException(400, detail="unsafe framework detail")

    @app.get("/_test/unexpected")
    def unexpected():
        raise RuntimeError("database password=do-not-expose")

    # TestClient defaults to the host "testserver", which is not a trusted
    # host; the middleware would reject every request before it routes.
    return TestClient(
        app, base_url="http://localhost", client=("127.0.0.1", 50000), raise_server_exceptions=False
    )


def assert_error_envelope(response, code: str):
    body = response.json()
    assert set(body) == {"code", "message", "details", "correlation_id"}
    assert body["code"] == code
    assert body["correlation_id"]
    assert response.headers["X-Correlation-ID"] == body["correlation_id"]
    return body


def test_framework_errors_use_the_stable_envelope():
    client = make_client()

    not_found = client.get("/does-not-exist")
    method_not_allowed = client.post("/health")
    bad_request = client.get("/_test/http-error")

    assert not_found.status_code == 404
    assert_error_envelope(not_found, "NOT_FOUND")
    assert method_not_allowed.status_code == 405
    assert_error_envelope(method_not_allowed, "METHOD_NOT_ALLOWED")
    assert bad_request.status_code == 400
    body = assert_error_envelope(bad_request, "BAD_REQUEST")
    assert "unsafe framework detail" not in str(body)


def test_application_error_preserves_only_explicit_safe_details():
    response = make_client().get("/_test/conflict")

    assert response.status_code == 409
    body = assert_error_envelope(response, "TEST_CONFLICT")
    assert body["message"] == "A safe conflict occurred."
    assert body["details"] == {}


def test_validation_error_does_not_echo_password_input():
    secret = "never-return-this-password"
    response = make_client().post(
        "/api/v1/auth/login", json={"username": "", "password": secret}
    )

    assert response.status_code == 422
    body = assert_error_envelope(response, "VALIDATION_ERROR")
    assert secret not in str(body)
    assert body["details"]["errors"] == [
        {"field": "body.username", "type": "string_too_short"}
    ]


def test_unexpected_error_is_sanitized():
    origin = "http://localhost:5173"
    response = make_client().get("/_test/unexpected", headers={"origin": origin})

    assert response.status_code == 500
    body = assert_error_envelope(response, "INTERNAL_ERROR")
    assert body["message"] == "An unexpected error occurred."
    assert "password" not in str(body).lower()
    assert "do-not-expose" not in str(body)
    assert response.headers["Access-Control-Allow-Origin"] == origin
    assert response.headers["Access-Control-Allow-Credentials"] == "true"


def test_untrusted_hosts_and_origins_are_rejected():
    client = make_client()

    untrusted_host = client.get("/health", headers={"host": "malicious.example"})
    untrusted_origin = client.post(
        "/api/v1/auth/logout", headers={"origin": "https://malicious.example"}
    )
    allowed_origin = client.post(
        "/api/v1/auth/logout", headers={"origin": "http://localhost:5173"}
    )

    assert untrusted_host.status_code == 400
    assert_error_envelope(untrusted_host, "UNTRUSTED_HOST")
    assert untrusted_origin.status_code == 403
    assert_error_envelope(untrusted_origin, "UNTRUSTED_ORIGIN")
    assert allowed_origin.status_code == 204


def test_oversized_password_is_rejected_before_hashing():
    response = make_client().post(
        "/api/v1/auth/login",
        json={"username": "scheduler", "password": "x" * 1025},
    )

    assert response.status_code == 422
    body = assert_error_envelope(response, "VALIDATION_ERROR")
    assert body["details"]["errors"] == [
        {"field": "body.password", "type": "string_too_long"}
    ]
