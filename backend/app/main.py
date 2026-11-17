import base64
import binascii
import hashlib
import uuid
from ipaddress import ip_address
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes_audit import router as audit_router
from app.api.routes_auth import router as auth_router
from app.api.routes_catalog import router as catalog_router
from app.api.routes_cohorts import router as cohorts_router
from app.api.routes_evaluations import router as evaluations_router
from app.api.routes_faculty import router as faculty_router
from app.api.routes_historical_uploads import router as historical_uploads_router
from app.api.routes_jobs import router as jobs_router
from app.api.routes_offerings import router as offerings_router
from app.api.routes_readiness import router as readiness_router
from app.api.routes_rule_sets import router as rule_sets_router
from app.api.routes_ga_configuration import router as ga_configuration_router
from app.api.routes_hard_constraints import router as hard_constraints_router
from app.api.routes_schedules import router as schedules_router
from app.api.routes_blocks import router as blocks_router
from app.api.routes_terms import router as terms_router
from app.api.routes_time_slots import router as time_slots_router
from app.api.routes_users import router as users_router
from app.core.config import settings
from app.core.errors import error_response, register_error_handlers
from app.services.security import verify_password

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# Probed by the deploy receiver's health checks, which carry no credentials.
_DEV_ACCESS_EXEMPT_PATHS = {"/health", "/deployment.txt"}
# Digests of credentials that already passed Argon2, so each asset request
# does not pay for a full verification.
_verified_dev_access: set[str] = set()


def _dev_access_allowed(request: Request) -> bool:
    if not settings.dev_access_users or request.url.path in _DEV_ACCESS_EXEMPT_PATHS:
        return True
    scheme, _, encoded = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "basic":
        return False
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    username, _, password = decoded.partition(":")
    password_hash = settings.dev_access_users.get(username)
    if password_hash is None:
        return False
    digest = hashlib.sha256(f"{password_hash}\0{password}".encode()).hexdigest()
    if digest in _verified_dev_access:
        return True
    if not verify_password(password, password_hash):
        return False
    _verified_dev_access.add(digest)
    return True


def _client_allowed(request: Request) -> bool:
    # The TCP peer address only; X-Forwarded-For is never consulted here.
    if request.client is None:
        return False
    try:
        address = ip_address(request.client.host)
    except ValueError:
        return False
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    return any(address in network for network in settings.allowed_client_networks)


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    # Local-production build: the SPA is served from the API's own origin.
    index = dist / "index.html"
    if not index.is_file():
        raise RuntimeError(f"Frontend build has no index.html: {dist}")
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    # index.html is excluded so every path to it gets the no-cache header.
    root_files = {p.name for p in dist.iterdir() if p.is_file()} - {"index.html"}
    not_found = app.router.default

    # Runs only when no route matched, so API 404/405s and trailing-slash
    # redirects behave exactly as they do without the frontend.
    async def frontend(scope, receive, send):
        path = scope["path"].lstrip("/")
        if (
            scope["type"] != "http"
            or scope["method"] not in {"GET", "HEAD"}
            or path == "api"
            or path.startswith("api/")
        ):
            await not_found(scope, receive, send)
            return
        # Root-level build files (favicon etc.), served only by exact name.
        if path in root_files:
            await FileResponse(dist / path)(scope, receive, send)
            return
        # Revalidate index.html so a rebuild's new asset hashes load at once.
        response = FileResponse(index, headers={"Cache-Control": "no-cache"})
        await response(scope, receive, send)

    app.router.default = frontend


def create_app() -> FastAPI:
    app = FastAPI(
        title="Chronos", description="DCISM Automated Class Scheduling System"
    )

    # The Vite dev server runs on a different port, so the browser needs CORS
    # permission to call this API. Local origins only.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # Lets the cross-origin client read export filenames.
        expose_headers=["Content-Disposition"],
    )

    @app.middleware("http")
    async def add_correlation_id(request: Request, call_next):
        request.state.correlation_id = str(uuid.uuid4())
        if not _client_allowed(request):
            return error_response(
                request,
                403,
                "UNTRUSTED_CLIENT",
                "The client address is not allowed.",
            )
        if request.url.hostname not in settings.trusted_hosts:
            return error_response(
                request,
                400,
                "UNTRUSTED_HOST",
                "The request host is not allowed.",
            )
        if not _dev_access_allowed(request):
            return error_response(
                request,
                401,
                "DEV_ACCESS_REQUIRED",
                "This test environment is for Chronos developers only.",
                headers={
                    "WWW-Authenticate": 'Basic realm="Chronos test", charset="UTF-8"'
                },
            )

        origin = request.headers.get("origin")
        host = request.headers.get("host")
        same_origin = f"{request.url.scheme}://{host}" if host else None
        allowed_origins = {*settings.cors_origins, same_origin}
        if (
            request.method in _UNSAFE_METHODS
            and origin is not None
            and origin.rstrip("/") not in allowed_origins
        ):
            return error_response(
                request,
                403,
                "UNTRUSTED_ORIGIN",
                "The request origin is not allowed.",
            )

        response = await call_next(request)
        response.headers["X-Correlation-ID"] = request.state.correlation_id
        return response

    register_error_handlers(app)
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(users_router, prefix="/api/v1")
    app.include_router(terms_router, prefix="/api/v1")
    app.include_router(catalog_router, prefix="/api/v1")
    app.include_router(faculty_router, prefix="/api/v1")
    app.include_router(time_slots_router, prefix="/api/v1")
    app.include_router(blocks_router, prefix="/api/v1")
    app.include_router(offerings_router, prefix="/api/v1")
    app.include_router(cohorts_router, prefix="/api/v1")
    app.include_router(evaluations_router, prefix="/api/v1")
    app.include_router(readiness_router, prefix="/api/v1")
    app.include_router(jobs_router, prefix="/api/v1")
    app.include_router(rule_sets_router, prefix="/api/v1")
    app.include_router(historical_uploads_router, prefix="/api/v1")
    app.include_router(ga_configuration_router, prefix="/api/v1")
    app.include_router(hard_constraints_router, prefix="/api/v1")
    app.include_router(schedules_router, prefix="/api/v1")
    app.include_router(audit_router, prefix="/api/v1")

    @app.get("/health")
    def health_check():
        return {"status": "ok"}

    if settings.devtest_upstream:
        from app.devtest_proxy import mount_devtest_proxy

        mount_devtest_proxy(app, settings.devtest_upstream)

    if settings.frontend_dist_dir:
        _mount_frontend(app, Path(settings.frontend_dist_dir))

    return app


app = create_app()
