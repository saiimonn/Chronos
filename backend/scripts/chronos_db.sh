#!/usr/bin/env bash
# CHR-179 / CHR-OPS-001: local PostgreSQL backup and restore for Chronos.
#
#   backend/scripts/chronos_db.sh backup  [DATABASE] [OUTPUT_FILE]
#   backend/scripts/chronos_db.sh restore ARTIFACT TARGET_DATABASE
#
# Backups are pg_dump custom-format archives of the `public` schema, which
# holds every Chronos table and alembic_version. Supabase's own schemas (auth,
# storage, ...) are not used by Chronos and are not included. A backup never
# overwrites an existing file. Restore only writes into a database whose public
# schema has no relations, creates the database if it does not exist, and
# loads in one transaction.
#
# The client must be PostgreSQL 17 or newer. A host pg_dump that new enough is
# used directly; otherwise the commands run inside the local Supabase database
# container, so no host install is needed. In that mode CHRONOS_DB_HOST and
# CHRONOS_DB_PORT are ignored: the container's own server is the one used.
# See docs/BACKUP_RESTORE.md.
set -euo pipefail
# Archives hold password and session hashes: owner-only files.
umask 077

CONTAINER="${CHRONOS_DB_CONTAINER:-supabase_db_chronos}"
DB_HOST="${CHRONOS_DB_HOST:-127.0.0.1}"
DB_PORT="${CHRONOS_DB_PORT:-54322}"
export PGUSER="${PGUSER:-postgres}"
export PGPASSWORD="${PGPASSWORD:-postgres}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

die() { echo "chronos_db: $*" >&2; exit 1; }
partial="" status_file=""
# Further signals are ignored once cleanup starts, so a second Ctrl-C cannot
# cut the rm short and leave a partial file behind.
trap 'trap "" INT HUP TERM PIPE; rm -f ${partial:+"$partial"} ${status_file:+"$status_file"}' EXIT
# `docker exec` can exit 0 on Ctrl-C (OrbStack does), so a client's status
# alone does not show an interruption. Bash runs these traps as soon as the
# interrupted command returns, before the next step, so an interrupted run
# never publishes an archive or reports a restore; the EXIT trap cleans up.
#
# An interrupted `docker exec` can also leave its process running inside the
# container. Every connection this run opens carries PGAPPNAME, so on a signal
# the run terminates its own server sessions: the orphaned client then fails
# and exits, and an open restore transaction rolls back.
export PGAPPNAME="chronos_db_$$_$RANDOM"
terminate_sessions() {
  PGCONNECT_TIMEOUT=3 PGOPTIONS="-c statement_timeout=3000" \
    pg psql -X -d postgres -Atqc "SELECT pg_terminate_backend(pid) FROM pg_stat_activity
      WHERE application_name = '$PGAPPNAME' AND pid <> pg_backend_pid()"
}
# Signals are ignored from here on, so nothing in the handler may block or
# fail: the session cleanup runs in the background and is killed after 5s
# (an unreachable server or a stalled `docker exec`), and the message is
# best-effort, since stderr may be a closed pipe or a hung-up terminal.
on_signal() {
  trap '' INT HUP TERM PIPE
  terminate_sessions </dev/null >/dev/null 2>&1 &
  local cleanup=$! waited=0
  while kill -0 "$cleanup" 2>/dev/null && [ "$waited" -lt 50 ]; do
    sleep 0.1 || true
    waited=$((waited + 1))
  done
  # KILL: the job inherited the ignored TERM. Its client child goes first.
  pkill -KILL -P "$cleanup" 2>/dev/null || true
  kill -KILL "$cleanup" 2>/dev/null || true
  echo "chronos_db: $1" >&2 2>/dev/null || true
  exit "$2"
}
trap 'on_signal interrupted 130' INT
trap 'on_signal hangup 129' HUP
trap 'on_signal terminated 143' TERM

host_client_ok() {
  command -v pg_dump >/dev/null 2>&1 || return 1
  local major
  major="$(pg_dump --version | grep -oE '[0-9]+' | head -1)"
  [ "$major" -ge 17 ]
}

# pg TOOL ARGS... runs a PostgreSQL client tool with stdin/stdout passed through.
if host_client_ok; then
  pg() { local tool="$1"; shift; "$tool" -h "$DB_HOST" -p "$DB_PORT" "$@"; }
else
  docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -qx true ||
    die "no PostgreSQL 17 client on the host and container '$CONTAINER' is not running (run 'supabase start')"
  pg() {
    local tool="$1"; shift
    docker exec -i -e PGUSER -e PGPASSWORD -e PGAPPNAME -e PGCONNECT_TIMEOUT -e PGOPTIONS "$CONTAINER" "$tool" -h 127.0.0.1 -p 5432 "$@"
  }
fi

check_name() {
  [[ "$1" =~ ^[A-Za-z0-9_]+$ ]] || die "invalid database name: '$1'"
}

# Streams the archive through pg_restore, which parses the header and table of
# contents and reads the data blocks, so a file with a damaged header or cut
# inside its TOC or data fails here before the target is touched. On some
# client builds bytes after the last data block are not read, so damage there
# is not detected; it does not affect the restore. Restore does not rely on
# this alone: its transaction rolls back if the load stream is cut.
check_archive() {
  pg pg_restore -f /dev/null <"$1" || die "not a complete pg_dump archive: $1"
}

backup() {
  local database="${1:-postgres}"
  check_name "$database"
  local output="${2:-$REPO_ROOT/backups/chronos-$database-$(date -u +%Y%m%dT%H%M%SZ).dump}"
  case "$output" in */) die "output must be a file, not a directory: $output" ;; esac
  [ ! -d "$output" ] || die "output must be a file, not a directory: $output"
  [ ! -e "$output" ] || die "refusing to overwrite existing backup: $output"
  mkdir -p "$(dirname "$output")"
  # A per-run partial file, so concurrent backups never touch each other's.
  # The X's must end the template: BSD mktemp only randomizes trailing ones.
  partial="$(mktemp "$(dirname "$output")/.$(basename "$output").partial.XXXXXX")"
  pg pg_dump --format=custom --schema=public --no-owner --no-privileges \
    -d "$database" >"$partial" || die "pg_dump failed"
  check_archive "$partial"
  # ln is an atomic create-if-absent: of two runs racing for one name, the
  # second fails instead of replacing the first's archive.
  if ! ln "$partial" "$output" 2>/dev/null; then
    [ ! -e "$output" ] || die "refusing to overwrite existing backup: $output"
    # No hard links here (exFAT/FAT drives, some network mounts). mv -n never
    # replaces a file, and leaves the partial behind if the name was taken.
    # ponytail: check-then-rename, not atomic; two runs racing for one name on
    # such a filesystem could still both pass the check.
    mv -n "$partial" "$output" 2>/dev/null || true
    [ ! -e "$partial" ] || die "could not publish $output (it appeared meanwhile, or the directory is not writable)"
  fi
  echo "$output"
}

restore() {
  [ $# -eq 2 ] || die "usage: restore ARTIFACT TARGET_DATABASE"
  local artifact="$1" target="$2"
  check_name "$target"
  [ -f "$artifact" ] || die "no such artifact: $artifact"
  check_archive "$artifact"

  local exists
  exists="$(pg psql -X -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname = '$target'")"
  if [ "$exists" = 1 ]; then
    local relations
    relations="$(pg psql -X -d "$target" -Atc \
      "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public'")"
    [ "$relations" = 0 ] || die "target database '$target' is not empty ($relations relations in public); restore needs a clean database"
  else
    pg psql -X -d postgres -qc "CREATE DATABASE \"$target\""
  fi

  # One transaction holds the DROP of the empty public schema (the archive
  # recreates it) and the whole load, so a failure at any point rolls the
  # target back to how it was. No CASCADE: a schema still holding functions or
  # types refuses to drop instead of losing them.
  #
  # COMMIT is only sent after pg_restore finished cleanly. If it fails
  # mid-stream, psql may still exit 0 (the cut can leave a valid COPY row and
  # EOF ends the COPY), so its status is recorded in a file and checked on its
  # own. The `\.` ends a COPY in progress so the RAISE runs as SQL, not data.
  # The producer is a subshell with traps of its own: a signal that reaches it
  # (Ctrl-C reaches the whole pipeline) means abort, never COMMIT, even when
  # the interrupted client returned 0.
  status_file="$(mktemp)"
  local psql_status=0
  {
    signalled=0
    trap 'signalled=1' INT HUP TERM
    echo 'BEGIN;'
    echo 'DROP SCHEMA IF EXISTS public;'
    if pg pg_restore --no-owner --no-privileges -f - <"$artifact" && [ "$signalled" = 0 ]; then
      echo 0 >"$status_file"
      echo 'COMMIT;'
    else
      echo 1 >"$status_file"
      printf '\n\\.\n'
      echo "DO \$\$ BEGIN RAISE EXCEPTION 'chronos_db: archive read failed'; END \$\$;"
    fi
  } | pg psql -X -q -v ON_ERROR_STOP=1 -d "$target" >/dev/null || psql_status=$?
  if [ "$psql_status" != 0 ] || [ "$(cat "$status_file")" != 0 ]; then
    die "restore failed; '$target' was rolled back and is unchanged"
  fi
  echo "restored $artifact into $target"
}

case "${1:-}" in
  backup) shift; backup "$@" ;;
  restore) shift; restore "$@" ;;
  *) die "usage: chronos_db.sh backup [DATABASE] [OUTPUT_FILE] | restore ARTIFACT TARGET_DATABASE" ;;
esac
