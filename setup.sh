#!/usr/bin/env bash
#
# Chronos — one-shot local development setup.
#
# Prepares a freshly cloned checkout: environment files, the local Supabase
# Docker stack, the backend virtual environment and migrations, and the
# frontend dependencies.
#
# This project is local-only. The script never contacts a managed Supabase
# project and never runs `supabase link`.
#
# Usage:
#   ./setup.sh [options]
#
# Options:
#   --skip-supabase     Do not start the local Supabase stack.
#   --skip-backend      Do not create the venv or install Python dependencies.
#   --skip-frontend     Do not install Node dependencies.
#   --skip-migrations   Do not run `alembic upgrade head`.
#   --force-env         Overwrite existing backend/.env and frontend/.env.
#   -h, --help          Show this help text.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

REQUIRED_PYTHON="3.12"
REQUIRED_NODE_MAJOR="24"
DB_HOST="127.0.0.1"
DB_PORT="54322"

SKIP_SUPABASE=0
SKIP_BACKEND=0
SKIP_FRONTEND=0
SKIP_MIGRATIONS=0
FORCE_ENV=0

if [ -t 1 ]; then
  BOLD="$(printf '\033[1m')"; RED="$(printf '\033[31m')"
  GREEN="$(printf '\033[32m')"; YELLOW="$(printf '\033[33m')"
  BLUE="$(printf '\033[34m')"; RESET="$(printf '\033[0m')"
else
  BOLD=""; RED=""; GREEN=""; YELLOW=""; BLUE=""; RESET=""
fi

step() { printf '\n%s==> %s%s\n' "${BOLD}${BLUE}" "$*" "$RESET"; }
info() { printf '    %s\n' "$*"; }
ok()   { printf '    %s%s%s\n' "$GREEN" "$*" "$RESET"; }
warn() { printf '    %s%s%s\n' "$YELLOW" "$*" "$RESET"; }
die()  { printf '\n%serror:%s %s\n' "$RED" "$RESET" "$*" >&2; exit 1; }

usage() { sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//; $d'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --skip-supabase)   SKIP_SUPABASE=1 ;;
    --skip-backend)    SKIP_BACKEND=1 ;;
    --skip-frontend)   SKIP_FRONTEND=1 ;;
    --skip-migrations) SKIP_MIGRATIONS=1 ;;
    --force-env)       FORCE_ENV=1 ;;
    -h|--help)         usage; exit 0 ;;
    *)                 die "unknown option: $1 (try --help)" ;;
  esac
  shift
done

have() { command -v "$1" >/dev/null 2>&1; }

# Resolve a Python interpreter that is exactly $REQUIRED_PYTHON.
#
# Exact, not ">=": the pinned dependency set does not run on a newer Python.
# On 3.14 every package installs cleanly and SQLAlchemy then dies at *import*
# with "Can't replace canonical symbol for '__firstlineno__'", long after this
# script has reported success. Refusing the interpreter up front trades that
# baffling failure for a clear one.
find_python() {
  local candidate
  for candidate in "python$REQUIRED_PYTHON" python3 python; do
    have "$candidate" || continue
    if "$candidate" -c "import sys; sys.exit(0 if '%d.%d' % sys.version_info[:2] == '$REQUIRED_PYTHON' else 1)" 2>/dev/null; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

# Path to the virtualenv interpreter, POSIX or Windows layout. May not exist yet.
venv_python() {
  if [ -x "$REPO_ROOT/backend/.venv/Scripts/python.exe" ]; then
    printf '%s' "$REPO_ROOT/backend/.venv/Scripts/python.exe"
  else
    printf '%s' "$REPO_ROOT/backend/.venv/bin/python"
  fi
}

# Major.minor of an interpreter, or empty if it cannot run.
python_version() {
  "$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true
}

# Load nvm into this shell so `nvm use` works from a non-interactive script.
load_nvm() {
  local nvm_dir="${NVM_DIR:-$HOME/.nvm}"
  # shellcheck disable=SC1091
  [ -s "$nvm_dir/nvm.sh" ] && . "$nvm_dir/nvm.sh" >/dev/null 2>&1
}

node_major() { node -v 2>/dev/null | sed 's/^v//; s/\..*//'; }

# Copy an .env template only when the target does not already exist.
seed_env_file() {
  local template="$1" target="$2"
  [ -f "$template" ] || die "missing template: $template"
  if [ -f "$target" ] && [ "$FORCE_ENV" -eq 0 ]; then
    info "$target already exists — left untouched"
    return
  fi
  if [ -f "$target" ]; then
    cp "$target" "$target.bak"
    warn "overwrote $target (previous copy saved as $target.bak)"
  fi
  cp "$template" "$target"
  ok "created $target from $(basename "$template")"
}

wait_for_postgres() {
  local attempts="${1:-60}" i=1
  while [ "$i" -le "$attempts" ]; do
    if (exec 3<>"/dev/tcp/$DB_HOST/$DB_PORT") 2>/dev/null; then
      exec 3>&- 3<&- 2>/dev/null || true
      return 0
    fi
    sleep 1
    i=$((i + 1))
  done
  return 1
}

# ---------------------------------------------------------------- prerequisites

step "Checking prerequisites"

MISSING=""
[ "$SKIP_SUPABASE" -eq 1 ] || have docker   || MISSING="$MISSING docker"
[ "$SKIP_SUPABASE" -eq 1 ] || have supabase || MISSING="$MISSING supabase"
[ "$SKIP_FRONTEND" -eq 1 ] || have node     || MISSING="$MISSING node"
[ "$SKIP_FRONTEND" -eq 1 ] || have npm      || MISSING="$MISSING npm"
[ -n "$MISSING" ] && die "not installed:$MISSING"

PYTHON=""
if [ "$SKIP_BACKEND" -eq 0 ]; then
  PYTHON="$(find_python)" || die "Python $REQUIRED_PYTHON not found.
    The pinned dependencies only run on $REQUIRED_PYTHON — a newer interpreter
    installs without complaint and then fails at import. Install it with:

      uv python install $REQUIRED_PYTHON
      brew install python@$REQUIRED_PYTHON
      sudo apt install python$REQUIRED_PYTHON python$REQUIRED_PYTHON-venv"
  ok "python: $PYTHON ($("$PYTHON" -V 2>&1))"
fi

if [ "$SKIP_FRONTEND" -eq 0 ]; then
  if [ "$(node_major)" != "$REQUIRED_NODE_MAJOR" ]; then
    load_nvm
    if have nvm; then
      info "switching to Node $REQUIRED_NODE_MAJOR via nvm"
      nvm install "$REQUIRED_NODE_MAJOR" >/dev/null
      nvm use "$REQUIRED_NODE_MAJOR" >/dev/null
    fi
  fi
  if [ "$(node_major)" != "$REQUIRED_NODE_MAJOR" ]; then
    warn "Node $REQUIRED_NODE_MAJOR expected, found $(node -v) — continuing anyway"
  else
    ok "node: $(node -v), npm: $(npm -v)"
  fi
fi

if [ "$SKIP_SUPABASE" -eq 0 ]; then
  docker info >/dev/null 2>&1 || die "Docker is installed but not running — start Docker Desktop first"
  ok "docker daemon reachable"
  ok "supabase: $(supabase --version 2>&1 | head -1)"
fi

# ------------------------------------------------------------------ environment

step "Creating environment files"
seed_env_file backend/.env.example backend/.env
seed_env_file frontend/.env.example frontend/.env
info "both .env files are gitignored — never commit them"

# --------------------------------------------------------------------- supabase

if [ "$SKIP_SUPABASE" -eq 0 ]; then
  step "Starting local Supabase stack"
  if supabase status >/dev/null 2>&1; then
    ok "already running"
  else
    info "this pulls Docker images on the first run and can take a few minutes"
    supabase start
    ok "stack started"
  fi
else
  step "Skipping Supabase stack (--skip-supabase)"
fi

# ---------------------------------------------------------------------- backend

if [ "$SKIP_BACKEND" -eq 0 ]; then
  step "Setting up the backend"
  # A venv built by a different interpreter is the exact failure this script
  # guards against, so reuse it only while its version still matches. Otherwise
  # a stale 3.14 venv survives every later run and keeps poisoning the setup.
  if [ -d backend/.venv ]; then
    VENV_VERSION="$(python_version "$(venv_python)")"
    if [ "$VENV_VERSION" = "$REQUIRED_PYTHON" ]; then
      info "reusing backend/.venv (Python $VENV_VERSION)"
    else
      warn "backend/.venv is Python ${VENV_VERSION:-unusable}, not $REQUIRED_PYTHON — recreating"
      rm -rf backend/.venv
    fi
  fi

  if [ ! -d backend/.venv ]; then
    "$PYTHON" -m venv backend/.venv
    ok "created backend/.venv"
  fi

  VENV_PY="$(venv_python)"
  [ -x "$VENV_PY" ] || die "virtual environment interpreter not found under backend/.venv"

  info "installing Python dependencies"
  "$VENV_PY" -m pip install --upgrade pip --quiet
  "$VENV_PY" -m pip install -r backend/requirements.txt --quiet
  # A clean pip run is not proof the stack imports: wheels for a too-new
  # interpreter install happily and blow up on first import instead. Check here
  # so the failure lands in setup rather than in someone's first alembic call.
  "$VENV_PY" -c "import sqlalchemy, alembic, fastapi, mlxtend, deap" >/dev/null 2>&1 || die \
    "dependencies installed but fail to import under $("$VENV_PY" -V 2>&1).
    Remove backend/.venv and re-run with Python $REQUIRED_PYTHON."
  ok "dependencies installed"

  if [ "$SKIP_MIGRATIONS" -eq 0 ]; then
    info "waiting for PostgreSQL on $DB_HOST:$DB_PORT"
    if wait_for_postgres 60; then
      ok "database reachable"
      (cd backend && "$VENV_PY" -m alembic upgrade head)
      ok "migrations applied"
    else
      warn "database not reachable — skipped migrations"
      warn "run 'supabase start', then: cd backend && source .venv/bin/activate && alembic upgrade head"
    fi
  else
    info "skipped migrations (--skip-migrations)"
  fi
else
  step "Skipping backend setup (--skip-backend)"
fi

# --------------------------------------------------------------------- frontend

if [ "$SKIP_FRONTEND" -eq 0 ]; then
  step "Setting up the frontend"
  if [ -f frontend/package-lock.json ]; then
    (cd frontend && npm ci)
  else
    (cd frontend && npm install)
  fi
  ok "node modules installed"
else
  step "Skipping frontend setup (--skip-frontend)"
fi

# ----------------------------------------------------------------------- finish

step "Setup complete"
cat <<'NEXT'
    Start the stack in three terminals:

      1)  supabase start
      2)  cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
      3)  cd frontend && npm run dev

    Local URLs:

      Frontend          http://localhost:5173
      Backend API       http://localhost:8000
      API docs          http://localhost:8000/docs
      Supabase Studio   http://127.0.0.1:54323

    Stop Supabase with `supabase stop` when you are done.
NEXT
