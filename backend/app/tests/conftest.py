import os
import socket
from ipaddress import ip_address

import pytest
from sqlalchemy.engine import make_url

# Settings requires DATABASE_URL and is built at import time, so importing the
# app constructs it before any test runs. Unit tests never open a connection,
# but a checkout without a .env (CI, a fresh clone) would fail at collection.
# setdefault leaves an exported value alone.
os.environ.setdefault(
    "DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
)
os.environ.setdefault("ENVIRONMENT", "test")


def _is_loopback(address) -> bool:
    if not isinstance(address, tuple):  # AF_UNIX path
        return True
    host = str(address[0]).split("%")[0]
    if host == "localhost":
        return True
    try:
        ip = ip_address(host)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_loopback


def _refuse(address) -> None:
    if not _is_loopback(address):
        raise PermissionError(f"CHR-ENV-004: test egress blocked to {address!r}")


def _guard_connect(real):
    def connect(sock, address):
        _refuse(address)
        return real(sock, address)

    return connect


def _guard_sendto(real):
    # sendto(data, address) or sendto(data, flags, address).
    def sendto(sock, data, *args):
        _refuse(args[-1])
        return real(sock, data, *args)

    return sendto


def _guard_sendmsg(real):
    # The destination is the optional fourth argument.
    def sendmsg(sock, buffers, *args):
        if len(args) >= 3 and args[2] is not None:
            _refuse(args[2])
        return real(sock, buffers, *args)

    return sendmsg


# CHR-ENV-004: installed when pytest imports this conftest, before any test
# module (and so any app module) is imported, so the whole suite (API, worker,
# ARM, GA) runs with Python socket traffic limited to loopback.
# ponytail: covers connect/connect_ex/sendto/sendmsg on Python sockets only.
# DNS lookups, libpq (psycopg2) and child processes bypass it; the static scans
# in test_no_egress.py and the loopback TEST_DATABASE_URL cover those.
socket.socket.connect = _guard_connect(socket.socket.connect)
socket.socket.connect_ex = _guard_connect(socket.socket.connect_ex)
socket.socket.sendto = _guard_sendto(socket.socket.sendto)
socket.socket.sendmsg = _guard_sendmsg(socket.socket.sendmsg)


@pytest.fixture(scope="session")
def postgres_test_url() -> str:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("set TEST_DATABASE_URL to run PostgreSQL integration tests")
    database_name = make_url(database_url).database
    if database_name is None or not database_name.endswith("_test"):
        raise pytest.UsageError(
            "TEST_DATABASE_URL must use a dedicated database ending in '_test'"
        )
    return database_url
