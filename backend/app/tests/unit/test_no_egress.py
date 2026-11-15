"""CHR-ENV-004: the runtime (API, worker, CLI, migrations) makes no external calls."""

import ast
import json
import re
import socket
from ipaddress import ip_address, ip_network
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from app.core.config import Settings, settings
from app.main import create_app

BACKEND = Path(__file__).resolve().parents[3]
RUNTIME_SOURCES = [
    path
    for root in (BACKEND / "app", BACKEND / "alembic")
    for path in root.rglob("*.py")
    if "tests" not in path.relative_to(BACKEND).parts
]

# Network clients and hosted-service SDKs. httpx is installed for TestClient
# only and must stay out of runtime code.
EGRESS_MODULES = {
    "requests", "httpx", "aiohttp", "urllib3", "urllib.request", "http.client",
    "smtplib", "ftplib", "poplib", "imaplib", "telnetlib", "xmlrpc.client",
    "websocket", "websockets", "supabase", "openai", "anthropic", "boto3",
    "botocore", "google", "azure", "firebase_admin", "pymongo", "redis",
}
# The worker names itself after the host; nothing else on socket is allowed.
ALLOWED_SOCKET_ATTRS = {"gethostname"}
# asyncio / loop methods that open a connection without importing socket.
CONNECT_CALLS = {
    "open_connection", "create_connection", "create_datagram_endpoint", "sock_connect",
}
DYNAMIC_IMPORTS = {"import_module", "__import__"}
URL = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s\"'<>`]+", re.I)
CLOUD_PACKAGES = re.compile(
    r"^(supabase|openai|anthropic|boto3|botocore|google-|azure-|firebase|"
    r"requests|aiohttp|sentry-sdk|newrelic|datadog|ddtrace|posthog|boto$|"
    r"opentelemetry-exporter|stripe|twilio|sendgrid)"
)
CLOUD_NPM = re.compile(
    r"^(@supabase/|firebase|@firebase/|@aws-sdk/|aws-sdk|@google-cloud/|@azure/|"
    r"openai|@anthropic-ai/|@sentry/|posthog|@vercel/|@datadog/|mixpanel|"
    r"@segment/|@amplitude/|logrocket)"
)


def _is_external_module(name: str) -> bool:
    return any(name == m or name.startswith(m + ".") for m in EGRESS_MODULES)


def egress_hits(source: str) -> list[str]:
    tree = ast.parse(source)
    socket_names = set()
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "socket":
                    socket_names.add(alias.asname or "socket")
                elif _is_external_module(alias.name):
                    hits.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
            hits += [n for n in names if _is_external_module(n)]
            if node.module == "socket":
                hits += [
                    f"socket.{a.name}"
                    for a in node.names
                    if a.name not in ALLOWED_SOCKET_ATTRS
                ]
            hits += [a.name for a in node.names if a.name in CONNECT_CALLS]
        elif isinstance(node, ast.Attribute) and node.attr in CONNECT_CALLS:
            hits.append(node.attr)
        elif isinstance(node, ast.Call):
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            arg = node.args[0] if node.args else next(
                (k.value for k in node.keywords if k.arg == "name"), None
            )
            if (
                called in DYNAMIC_IMPORTS
                and isinstance(arg, ast.Constant)
                and _is_external_module(str(arg.value))
            ):
                hits.append(f"{called}({arg.value!r})")
    hits += [
        f"socket.{node.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in socket_names
        and node.attr not in ALLOWED_SOCKET_ATTRS
    ]
    return hits


def _local_host(host: str) -> bool:
    # LAN addresses are an allowed boundary, so private/link-local count too.
    if host == "localhost" or host.endswith(".local"):
        return True
    try:
        ip = ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_unspecified


def external_urls(text: str) -> list[str]:
    hits = []
    for url in URL.findall(text):
        # Drop prose punctuation after the URL; a bracket only when unbalanced,
        # so http://[::1] keeps its own.
        while url[-1] in ".,;:)'\"" or (
            url[-1] in "]}" and url.count(url[-1]) > url.count("[{"["]}".index(url[-1])])
        ):
            url = url[:-1]
        # Raw host text, checked before urlsplit, which rejects "[{host}]".
        # Host only: userinfo may legitimately hold %-escapes or templates.
        netloc = re.split(r"[/?#]", url.split("://", 1)[1], maxsplit=1)[0]
        hostport = netloc.rpartition("@")[2]
        # Drop the port: "[...]" keeps up to its "]", otherwise cut at ":".
        if hostport.startswith("[") and "]" in hostport:
            host_text = hostport[: hostport.index("]") + 1]
        else:
            host_text = hostport.split(":", 1)[0]
        if any(c in host_text for c in "{}%"):
            continue  # a format template's host, not a destination
        try:
            host = urlsplit(url).hostname
        except ValueError:
            hits.append(url)
            continue
        if host is not None and not _local_host(host):
            hits.append(url)
    return hits


def _normalize(package: str) -> str:
    # PEP 503: runs of -, _ and . are equivalent and case is ignored.
    return re.sub(r"[-_.]+", "-", package).lower()


def cloud_packages(requirement_lines: list[str]) -> list[str]:
    names = [
        _normalize(re.split(r"[\[=<>~!;\s]", line.strip(), maxsplit=1)[0])
        for line in requirement_lines
        if line.strip() and line.strip()[0].isalnum()
    ]
    return [n for n in names if CLOUD_PACKAGES.match(n)]


def test_scanners_catch_aliased_dynamic_and_non_http_egress():
    for source in (
        "import requests",
        "from urllib import request",
        "from socket import create_connection",
        "import socket as s\ns.create_connection(('h', 1))",
        "import asyncio\nasyncio.open_connection('h', 1)",
        "from asyncio import open_connection",
        "loop.create_datagram_endpoint(P, remote_addr=('h', 1))",
        "import importlib\nimportlib.import_module('requests')",
        "__import__('http.client')",
        "import_module(name='requests')",
    ):
        assert egress_hits(source), source
    assert egress_hits("import socket\nsocket.gethostname()") == []
    assert egress_hits("import_module('app.services.arm.providers')") == []

    assert external_urls("postgresql://u:p@db.x.supabase.co:5432/postgres")
    assert external_urls("https://api.openai.com/v1 redis://cache.example:6379")
    # Template or escape characters outside the host do not hide a real host.
    for url in (
        "https://api.openai.com/v1?q=a%20b",
        "https://api.openai.com/v1/{model}",
        "https://evil.com/%s",
        "postgresql://postgres:p%40ss@db.abc.supabase.co:5432/postgres",
        "postgresql://{user}:{pw}@db.abc.supabase.co:5432/postgres",
        "postgresql://%(user)s:%(pw)s@db.abc.supabase.co/postgres",
        "http://[::1",  # malformed IPv6, not a template
        "https://api.openai.com:{port}/v1",  # template only in the port
        "postgresql://u:p@db.x.supabase.co:%(port)s/db",
        "redis://cache.example:%d",
    ):
        assert external_urls(url) == [url]
    assert external_urls(
        "postgresql://postgres:postgres@127.0.0.1:54322/postgres "
        "http://[::1]:8000 http://localhost:5173 https://chronos.dcism.local/x "
        "http://0.0.0.0:8000 sqlite:///local.db http://192.168.1.10:8000 "
        "http://{host}:{port}/x %s://%s http://[{host}]:{port}/x 'http://u@[{h}]'"
    ) == []
    # Trailing prose punctuation is not part of the host.
    assert external_urls(
        "(see http://localhost) Open http://127.0.0.1. or http://localhost., "
        "'http://[::1]:8000' [http://[::1]]"
    ) == []

    assert cloud_packages(
        ["sentry_sdk==2", "Google_Cloud.Storage>=1", "supabase[x]==2", "# boto3"]
    ) == ["sentry-sdk", "google-cloud-storage", "supabase"]
    assert cloud_packages(["uvicorn[standard]==0.52.4", "psycopg2-binary==2"]) == []


# The /devtest/ proxy relays to the test environment on this machine; its
# upstream is validated as loopback (test_devtest_upstream_must_be_loopback).
LOOPBACK_ONLY_CLIENTS = {"app/devtest_proxy.py": ["httpx"]}


def test_runtime_sources_import_no_network_client():
    assert len(RUNTIME_SOURCES) > 50, "source discovery broke"
    hits = {
        str(path.relative_to(BACKEND)): found
        for path in RUNTIME_SOURCES
        if (found := egress_hits(path.read_text()))
    }
    assert hits == LOOPBACK_ONLY_CLIENTS


def test_devtest_upstream_must_be_loopback(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    for good in ("http://127.0.0.1:20310", "http://localhost:20310/", "http://[::1]:9"):
        monkeypatch.setenv("DEVTEST_UPSTREAM", good)
        assert Settings(_env_file=None).devtest_upstream == good.rstrip("/")
    for bad in (
        "http://192.168.1.5:20310",
        "https://127.0.0.1:20310",
        "http://chronos.dcism.org",
        "http://127.0.0.1:20310/elsewhere",
        "127.0.0.1:20310",
    ):
        monkeypatch.setenv("DEVTEST_UPSTREAM", bad)
        with pytest.raises(ValueError):
            Settings(_env_file=None)


def test_runtime_sources_name_no_external_url():
    hits = []
    for path in RUNTIME_SOURCES:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                hits += [
                    f"{path.relative_to(BACKEND)}: {url}"
                    for url in external_urls(node.value)
                ]
    assert hits == []


def test_dependencies_add_no_hosted_service_sdk():
    requirements = (BACKEND / "requirements.txt").read_text().splitlines()
    assert cloud_packages(requirements) == []

    package = json.loads((BACKEND.parent / "frontend" / "package.json").read_text())
    # Runtime bundle only; devDependencies (build/deploy CLIs) never ship.
    npm = list(package.get("dependencies", {}))
    assert [name for name in npm if CLOUD_NPM.match(name)] == []


def test_documented_and_default_configuration_is_loopback_only(monkeypatch):
    example = dict(
        line.split("=", 1)
        for line in (BACKEND / ".env.example").read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    assert make_url(example["DATABASE_URL"]).host in {"127.0.0.1", "localhost"}
    assert json.loads(example["TRUSTED_HOSTS"]) == ["localhost", "127.0.0.1"]
    assert all(
        re.match(r"^http://(localhost|127\.0\.0\.1):\d+$", o)
        for o in json.loads(example["CORS_ORIGINS"])
    )
    assert all(
        ip_network(n).is_loopback for n in json.loads(example["ALLOWED_CLIENT_NETWORKS"])
    )

    for name in ("TRUSTED_HOSTS", "CORS_ORIGINS", "ALLOWED_CLIENT_NETWORKS"):
        monkeypatch.delenv(name, raising=False)
    defaults = Settings(_env_file=None)
    assert defaults.trusted_hosts == ["localhost", "127.0.0.1"]
    assert all(
        re.match(r"^http://(localhost|127\.0\.0\.1):\d+$", o)
        for o in defaults.cors_origins
    )
    assert all(net.is_loopback for net in defaults.allowed_client_networks)


def test_cors_preflight_refuses_non_local_origin(monkeypatch):
    # Built-in defaults, not whatever a developer's backend/.env sets.
    for name in ("TRUSTED_HOSTS", "CORS_ORIGINS", "ALLOWED_CLIENT_NETWORKS"):
        monkeypatch.delenv(name, raising=False)
    defaults = Settings(_env_file=None)
    for name in ("trusted_hosts", "cors_origins", "allowed_client_networks"):
        monkeypatch.setattr(settings, name, getattr(defaults, name))
    client = TestClient(
        create_app(), base_url="http://localhost", client=("127.0.0.1", 50000)
    )
    headers = {"Access-Control-Request-Method": "POST"}

    refused = client.options(
        "/api/v1/auth/login", headers={**headers, "Origin": "https://evil.example"}
    )
    allowed = client.options(
        "/api/v1/auth/login", headers={**headers, "Origin": "http://localhost:5173"}
    )

    assert refused.status_code == 400
    assert "access-control-allow-origin" not in refused.headers
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_suite_blocks_non_loopback_connections():
    # Guard installed when conftest.py is imported. 203.0.113.1 is TEST-NET-3.
    with socket.socket() as sock, pytest.raises(PermissionError, match="CHR-ENV-004"):
        sock.connect(("203.0.113.1", 80))
    with socket.socket() as sock, pytest.raises(PermissionError, match="CHR-ENV-004"):
        sock.connect_ex(("203.0.113.1", 80))
    with socket.socket(type=socket.SOCK_DGRAM) as udp:
        with pytest.raises(PermissionError, match="CHR-ENV-004"):
            udp.sendto(b"x", ("203.0.113.1", 53))
        with pytest.raises(PermissionError, match="CHR-ENV-004"):
            udp.sendto(b"x", 0, ("203.0.113.1", 53))
        with pytest.raises(PermissionError, match="CHR-ENV-004"):
            udp.sendmsg([b"x"], [], 0, ("203.0.113.1", 53))

    with socket.socket() as server, socket.socket() as sock:
        server.bind(("127.0.0.1", 0))
        server.listen()
        sock.connect(server.getsockname())
