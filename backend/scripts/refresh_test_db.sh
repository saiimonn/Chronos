#!/usr/bin/env bash
# Copy the production database into the test database, then clean it.
#
# Runs on the DCISM server over an ordinary SSH session, from the active test
# release (docs/TEST_ENVIRONMENT.md):
#
#   cd ~/chronos-test.dcism.org/current/backend
#   scripts/refresh_test_db.sh
#
# Every account's password becomes testtest (app.cli.sanitize_test_db).
#
# Production is only read (pg_dump). Everything in the test database is
# replaced: its public schema is dropped and rebuilt from the dump, migrated
# to this release's head, cleaned by app.cli.sanitize_test_db, then given
# history-based faculty availability (app.cli.fill_availability_from_history).
set -Eeuo pipefail
umask 077

prod_env="${CHRONOS_PROD_ENV:-$HOME/.chronos/backend.env}"
test_env="${CHRONOS_TEST_ENV:-$HOME/.chronos-test/backend.env}"
worker="chronos-test-worker"
api="chronos-test-api"

die() { printf 'refresh_test_db: %s\n' "$*" >&2; exit 1; }
setting() { sed -n "s/^$1=//p" "$2" | tail -n 1; }

prod_url="$(setting DATABASE_URL "$prod_env")"
test_url="$(setting DATABASE_URL "$test_env")"
[[ -n "$prod_url" && -n "$test_url" ]] || die "DATABASE_URL missing in $prod_env or $test_env"
[[ "$prod_url" != "$test_url" ]] || die "test and production use the same database"
[[ "$(setting ENVIRONMENT "$test_env")" == "test" ]] || die "$test_env must set ENVIRONMENT=test"
[[ "$(readlink -f .env)" == "$(readlink -f "$test_env")" ]] ||
  die "run from the test release's backend/ directory"

dump="$(mktemp "${TMPDIR:-/tmp}/chronos-prod.XXXXXX.dump")"
trap 'rm -f "$dump"; pm2 restart "$api" "$worker" >/dev/null 2>&1 || true' EXIT

pg_dump --format=custom --no-owner --no-privileges --schema=public \
  --file="$dump" "$prod_url"
pm2 stop "$worker" >/dev/null 2>&1 || true

# The dump recreates the schema itself (CREATE SCHEMA public).
psql "$test_url" --quiet --set ON_ERROR_STOP=1 -c 'DROP SCHEMA IF EXISTS public CASCADE'
pg_restore --no-owner --no-privileges --single-transaction --exit-on-error \
  --dbname="$test_url" "$dump"
# The test release can be ahead of production.
.venv/bin/alembic upgrade head
.venv/bin/python -m app.cli.sanitize_test_db
# Demo-ready roster: fill blank faculty availability and preferred days from
# past schedules, as production does once the administrator starts using it.
# Without an active term there is no slot grid to fill, so it is skipped.
if ! .venv/bin/python -m app.cli.fill_availability_from_history --apply; then
  printf 'refresh_test_db: availability not filled from history (no active term?); continuing.\n' >&2
fi
printf 'Test database refreshed from production.\n'
