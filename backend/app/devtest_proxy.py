"""Forward /devtest/ on the official site to the test environment.

The test environment (docs/TEST_ENVIRONMENT.md) runs as its own app on a
loopback port. Production mounts this proxy when DEVTEST_UPSTREAM is set, so
developers reach the test copy at /devtest/ on the official domain without a
second domain. The test app keeps its own database, deploys and developer
gate; this module only relays bytes.

CHR-ENV-004: the only runtime network client. Settings refuse any upstream
that is not loopback, so it never leaves the server.
"""

import re
from http.cookiejar import CookieJar, DefaultCookiePolicy

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from starlette.background import BackgroundTask

from app.core.errors import error_response

PREFIX = "/devtest"
# Connection-scoped headers (RFC 9110 7.6.1) are never relayed.
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
}
_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
# Test cookies carry this prefix (SESSION_COOKIE_NAME=chronos_test_session).
# Only they cross the proxy, either way: the test app, which may run any
# branch, never sees production's session cookie and can never set one.
TEST_COOKIE_PREFIX = "chronos_test_"
_ROOT_PATH = re.compile(r"(?i)(;\s*path=)/(?=\s*(;|$))")


def _test_cookies(header: str) -> str:
    pairs = (p.strip() for p in header.split(";"))
    return "; ".join(p for p in pairs if p.startswith(TEST_COOKIE_PREFIX))


def _scoped_set_cookie(value: str) -> str | None:
    """Keep a test cookie, limited to /devtest/; drop anything else."""
    if not value.lstrip().startswith(TEST_COOKIE_PREFIX):
        return None
    if not re.search(r"(?i);\s*path=", value):
        return f"{value}; Path={PREFIX}/"
    return _ROOT_PATH.sub(rf"\g<1>{PREFIX}/", value)


def mount_devtest_proxy(app: FastAPI, upstream: str) -> None:
    # Long timeouts: exports and uploads pass through here too. The jar refuses
    # every cookie: one shared client must never keep a visitor's session and
    # send it on someone else's request; cookies travel only as headers.
    client = httpx.AsyncClient(
        base_url=upstream,
        timeout=httpx.Timeout(30.0, read=600.0, write=600.0),
        cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
    )

    async def redirect_to_slash(request: Request):
        return RedirectResponse(f"{PREFIX}/", status_code=308)

    async def forward(request: Request):
        path = request.url.path[len(PREFIX):] or "/"
        headers = [
            (k, v)
            for k, v in request.headers.items()
            if k.lower() not in _HOP_BY_HOP
            and k.lower() != "cookie"
            and not k.lower().startswith("x-forwarded-")
        ]
        if cookies := _test_cookies(request.headers.get("cookie", "")):
            headers.append(("cookie", cookies))
        upstream_request = client.build_request(
            request.method,
            path,
            params=request.url.query,
            headers=headers,
            content=None if request.method in {"GET", "HEAD"} else request.stream(),
        )
        try:
            response = await client.send(upstream_request, stream=True)
        except httpx.HTTPError:
            return error_response(
                request,
                502,
                "DEVTEST_UNAVAILABLE",
                "The test environment is not running.",
            )
        relayed = StreamingResponse(
            response.aiter_raw(),
            status_code=response.status_code,
            background=BackgroundTask(response.aclose),
        )
        # Raw pairs keep repeated headers such as Set-Cookie.
        relayed.raw_headers = [
            (k.encode("latin-1"), v.encode("latin-1"))
            for k, v in response.headers.multi_items()
            if k.lower() not in _HOP_BY_HOP and k.lower() != "set-cookie"
        ] + [
            (b"set-cookie", cookie.encode("latin-1"))
            for v in response.headers.get_list("set-cookie")
            if (cookie := _scoped_set_cookie(v))
        ]
        return relayed

    app.add_api_route(PREFIX, redirect_to_slash, methods=_METHODS, include_in_schema=False)
    app.add_api_route(
        f"{PREFIX}/{{path:path}}", forward, methods=_METHODS, include_in_schema=False
    )
