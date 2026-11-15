"""Shared Alembic invocation for the PostgreSQL integration tests.

Migrations run in a subprocess rather than through the Alembic API so that the
test database URL is supplied the same way the developer supplies it — via the
environment — and so a failed revision surfaces as a non-zero exit code instead
of a half-applied in-process transaction.
"""

import os
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[3]


def run_preflight(
    database_url: str, *arguments: str
) -> subprocess.CompletedProcess[str]:
    """Run the Block migration preflight the way an operator would.

    Its exit code is the answer, so unlike `run_alembic` this returns the
    completed process instead of raising on a non-zero status.
    """
    environment = os.environ | {"DATABASE_URL": database_url, "ENVIRONMENT": "test"}
    return subprocess.run(
        [sys.executable, "-m", "app.cli.blk002_preflight", *arguments],
        check=False,
        capture_output=True,
        text=True,
        cwd=BACKEND_DIR,
        env=environment,
    )


def run_alembic(database_url: str, *arguments: str) -> None:
    environment = os.environ | {
        "DATABASE_URL": database_url,
        "ENVIRONMENT": "test",
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        check=True,
        cwd=BACKEND_DIR,
        env=environment,
    )
