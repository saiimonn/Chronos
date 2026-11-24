# Chronos

Chronos is the DCISM Automated Class Scheduling System. It uses Association
Rule Mining (ARM) to inform a Genetic Algorithm (GA) that generates
conflict-free class schedules.

The application is designed to run entirely on the local machine. Supabase is
used only as a local Docker-based PostgreSQL stack; do not link this project to
a managed Supabase project.

## Project Structure

| Path | Purpose |
| --- | --- |
| `setup.sh` | One-shot local development setup script |
| `backend/` | FastAPI API, SQLAlchemy models, Alembic migrations, and ARM/GA services |
| `frontend/` | React 19, TypeScript, and Vite web application |
| `supabase/` | Local Supabase CLI configuration and generated local state |
| `scripts/deploy/` | Deployment helpers used by the CD workflows |
| `docker-compose.yml`, `docker-compose.prod.yml` | Development and local-production container stacks |
| `docs/` | Project, SRS and thesis documentation (private, see Private Files) |
| `dataset/` | Approved inputs, versioned preprocessing, and review handoff evidence (private, see Private Files) |
| `experiments/` | Immutable research run evidence and frozen-rule preparation (private, see Private Files) |
| `.github/workflows/ci.yml` | Pull request checks for the backend and frontend; a new push to a pull request cancels that PR's run still in progress (runs on `main` are never cancelled) |
| `.github/workflows/cd.yml` | Deploys `main` to the DCISM server after CI passes (`docs/DCISM_DEPLOYMENT.md`) |
| `.github/workflows/cd-test.yml` | Deploys `main` to the `/devtest/` test environment after production, or any branch by hand (`docs/TEST_ENVIRONMENT.md`) |

## Private Files

`docs/` (SRS, implementation plan, runbooks, deployment and test-environment
guides, thesis manuscript), `dataset/` (approved research data) and
`experiments/` (research run evidence) live in the private repository
`saiimonn/Chronos-docs`. Team members clone it into `private/`, which is
gitignored; the committed symlinks `docs`, `dataset` and `experiments` point
into it, so every path below resolves locally:

```bash
gh repo clone saiimonn/Chronos-docs private
```

Without it the app still builds and runs; the private research-publication
tests skip.

## Prerequisites

The supported development environment is WSL2 on Windows. Install:

- Docker Desktop with WSL2 integration enabled
- Supabase CLI
- Python 3.12
- Node.js 24 and npm, preferably managed with `nvm`
- Git

For Jira/GitHub-assisted development through OpenCode, also install GitHub CLI,
the official GitHub MCP server, and OpenCode. See
`docs/MCP_WORKFLOW.md`.

Verify the main tools before continuing:

```bash
docker --version
supabase --version
python3.12 --version
node --version
npm --version
```

All commands below assume the repository root is the current directory unless
another directory is shown.

## Quick Start

After cloning, run the setup script from the repository root with Docker
running:

```bash
./setup.sh
```

The script performs the whole first-time setup:

1. Checks that Docker, the Supabase CLI, Python 3.12, and Node 24 are available,
   switching to Node 24 through `nvm` when it is installed.
2. Creates `backend/.env` and `frontend/.env` from the committed templates,
   leaving existing files untouched.
3. Starts the local Supabase stack, or reports that it is already running.
4. Creates `backend/.venv` and installs the Python dependencies.
5. Waits for local PostgreSQL and applies the Alembic migrations.
6. Installs the frontend dependencies with `npm ci`.

The first run downloads the Supabase Docker images and can take several
minutes. The script is safe to re-run; it reuses whatever is already in place.

Options:

| Option | Effect |
| --- | --- |
| `--skip-supabase` | Do not start the local Supabase stack |
| `--skip-backend` | Do not create the virtual environment or install Python dependencies |
| `--skip-frontend` | Do not install Node dependencies |
| `--skip-migrations` | Do not run `alembic upgrade head` |
| `--force-env` | Overwrite existing `.env` files, saving each previous copy as `.env.bak` |
| `-h`, `--help` | Show the available options |

When the script finishes, start the stack as described in
[Run the Full Stack](#run-the-full-stack).

The remaining sections describe the same steps manually, which is useful when a
single part of the environment has to be rebuilt or when the script reports a
failed step.

## Manual Setup: Environment Files

Create the local environment files from the committed templates:

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
```

The defaults connect the backend to local Supabase PostgreSQL at
`127.0.0.1:54322` and the frontend to the API at `http://localhost:8000`.
Both `.env` files are gitignored and must not be committed.

## Supabase Setup

Supabase provides the local PostgreSQL database and related development
services. Docker must be running before starting it.

Start the local stack from the repository root:

```bash
supabase start
```

Useful local endpoints include:

| Service | URL or port |
| --- | --- |
| Supabase API | `http://127.0.0.1:54321` |
| PostgreSQL | `127.0.0.1:54322` |
| Supabase Studio | `http://127.0.0.1:54323` |
| Local email viewer | `http://127.0.0.1:54324` |

Check the stack or stop it with:

```bash
supabase status
supabase stop
```

Do not run `supabase link`; this project is local-only. The generated
`supabase/.temp/` and `supabase/.branches/` directories contain local service
state and secrets and must remain untracked.

## Backend Setup

The backend requires Python 3.12 and a running local PostgreSQL instance.

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
alembic upgrade head
printf 'Seed password: '; IFS= read -rs CHRONOS_SEED_PASSWORD; echo
CHRONOS_SEED_PASSWORD="$CHRONOS_SEED_PASSWORD" python -m app.cli.seed_users
unset CHRONOS_SEED_PASSWORD
python -m app.cli.seed_catalog
python -m app.cli.seed_scenario
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

`python -m app.cli.seed_scenario` is optional: it creates a ready-to-generate
test term.

The API is available at `http://localhost:8000`. Useful endpoints are:

- Health check: `http://localhost:8000/health`
- OpenAPI documentation: `http://localhost:8000/docs`

### Development accounts

A migrated database has no users and the API exposes no registration route, so
`python -m app.cli.seed_users` provisions one development account per role.
Run it after `alembic upgrade head`; it is idempotent and never modifies an
account that already exists.

| Username | Role |
| --- | --- |
| `admin` | `department_administrator` |
| `scheduler` | `scheduling_committee` |

Both use the password you enter at the prompt in the setup steps above, passed
to the command as `CHRONOS_SEED_PASSWORD`. There is no default: if it is unset,
blank, not valid UTF-8, shorter than 8 or longer than 1024 characters, the
command exits with an error and creates nothing. Do not put it in
`backend/.env`; the CLI reads the shell environment only and the backend
settings reject unknown variables. These are local development fixtures and
carry no institutional meaning.

A database seeded before this change may still have `admin` and `scheduler`
with the old password `password`, and re-running the command leaves them
untouched ("already present"). Create a new Department Administrator under a
different username on the Users page, sign in as it, then deactivate `admin`
and `scheduler`; on a disposable local database, reset it and seed again.

### Reference catalogs

Subjects, course programs, and curricula are reference data: the same for every
installation, rarely changed, and knowable before deployment. A migrated
database leaves their tables empty, so `python -m app.cli.seed_catalog` loads
them from three CSVs in `backend/data/`:

| File | Holds |
| --- | --- |
| `course_programs.csv` | BSCS, BSIT, and BSIS — the programs thesis §1.4 puts in scope |
| `subjects.csv` | the subject catalog, one row per subject code |
| `curriculum.csv` | what each cohort must take, per year level and term |

DCISM lab subjects meet only in the lab, with no separate lecture session, so a
lab subject is a single `subjects.csv` row with `subject_type` LAB.
`subject_type` derives the meeting length — LEC is 90 minutes, LAB is 150 — and
belongs to the code across every program, so a subject shared by BSCS, BSIT,
and BSIS must carry the same type for all three.

Electives are not pre-seeded: neither the elective slots in the prospectus
(`CS ELEC`, `IT FREE EL 2`, …) nor the concrete elective subjects they draw on
(`CS 5101N`, `IS 5102`, …). A slot is a choice rather than a subject, the
elective that runs is decided each term, and the prospectus lists elective codes
without titles, units, or a known LEC/LAB type. When an elective is offered, the
administrator creates it on the Subjects page, which requires choosing its type,
and adds it to the cohort by hand.

`curriculum.csv` keys each prospectus by the **entry** academic year it governs,
not the year it is used in, because a cohort follows the prospectus in force
when it enrolled. Resolution prefers an exact match and otherwise falls back to
the most recent earlier year, so a program only needs a row per revision.

The script validates all three files before writing anything, reports every
problem with its file and line number, and exits non-zero without a partial
write. Each row is an upsert on its natural key, so the script is safe to re-run
and correcting a CSV value updates that row alone. Curricula are reconciled
instead: for every program in `course_programs.csv`, `curriculum.csv` is the
complete list of its curriculum versions and their placements. Moving or
deleting a row removes the old placement, and a version the file no longer
names at all — its last row deleted, or its `effective_year` corrected on every
row — is deleted with its requirements, so cohort resolution cannot keep
selecting it. Curricula of programs the file does not name are left alone.
Use `--dry-run` to see what would change, removals included,
without committing, and `--data-dir` to point at another directory.

The committed catalog comes from the BSCS, BSIT, and BSIS prospecti, effective
year 2023: 71 subjects and 110 curriculum placements under a single
`2023-2024` curriculum per program. It holds only the regular subjects Chronos
generates automatically for a cohort. Electives, practicums, and general
education, NSTP, PATH-FIT, and EDM subjects need a scheduling decision each
term, so the administrator creates and attaches them by hand; the headers of
`subjects.csv` and `curriculum.csv` list them.

Faculty availability saves (`PUT /api/v1/faculty/{id}/availability`) require
both `term_id` and `time_slot_ids`, even for an empty selection (CHR-169 /
CHR-FIX-009). Use the term ID returned with the displayed availability grid,
not a later active-term lookup. Missing IDs return 422; a term that is no longer
active returns 409 `AVAILABILITY_REVIEW_STALE` without changing the stored mask
or review marker. Save and confirmation hold the term stable until commit.
Deploy the updated frontend and API together; old save requests fail closed.

CHR-162 / CHR-FIX-002 serializes slot creation, deletion, and canonical
restoration with availability saves and confirmations through the same Term
row. Grid writers take `FOR NO KEY UPDATE`; availability writes take `FOR SHARE`.
This blocks competing grid reviews without blocking foreign-key checks. Both
lock Term before Faculty to avoid deadlocks. Membership changes
clear review stamps without rewriting masks, including reviews committed just
before the grid change.

Run backend tests from `backend/` with the virtual environment active:

```bash
pytest app/tests
```

The full integration suite requires a dedicated PostgreSQL 17 database. Never
point `TEST_DATABASE_URL` at the normal local runtime database because the
migration test returns its target to Alembic base before upgrading it again:

```bash
docker run --rm --detach --name chronos-test-postgres \
  --env POSTGRES_DB=chronos_test \
  --env POSTGRES_PASSWORD=postgres \
  --env POSTGRES_USER=postgres \
  --publish 5432:5432 postgres:17
docker exec chronos-test-postgres pg_isready -U postgres -d chronos_test
```

If port `5432` is already in use, publish another local port and use it in the
test URL. Stop the disposable database after testing with
`docker stop chronos-test-postgres`.

```bash
export TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/chronos_test
export DATABASE_URL="$TEST_DATABASE_URL"
pytest app/tests -m postgres
```

Create and apply a database migration after changing SQLAlchemy models:

```bash
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```

Alembic reads `DATABASE_URL` from `backend/.env`; database credentials should
not be added to `backend/alembic.ini`.

### Test scenario term

`python -m app.cli.seed_scenario` loads one synthetic, DCISM-sized term so a
tester can generate a schedule without typing anything in. Run it after
`seed_users` and `seed_catalog`. It reads `backend/data/scenario/` and, through
the same services the API uses, creates:

- term `2026-2027` T1 with its canonical slot grid, made the current term
- 2 buildings and 8 rooms (4 lecture, 4 laboratory)
- 12 placeholder faculty (`Sample Instructor 01` to `12`), 8 full-time and 4
  part-time, each with availability saved and reviewed for the term
- 11 Blocks of BSCS, BSIT, and BSIS years 1 to 3, with 51 offerings derived
  from `curriculum.csv`

It ends by printing the term's readiness report and exits non-zero if anything
blocks generation. Re-running it creates nothing and keeps any record that
already exists, including a mask someone has since edited for the term. If a
different term is current the command refuses, because it would take over term
selection; pass `--force` to switch anyway. Faculty not listed in the scenario
CSV are left alone, so on a database that already has other faculty the term
stays blocked until their availability is reviewed for it. The CSVs are checked
against the database (programs, curricula, building names) before anything is
written. All names and rooms are invented.

## Historical Bootstrap

CHR-133 / CHR-COLAB-007 publishes the private review inputs and dated evidence in
[the portable handoff bundle](dataset/handoff/CHR-133-CHR-COLAB-007-2026-10-24-v1/README.md).
Collaborators can resume the review from a fresh checkout without files from the
original workstation. Administrator decisions are still required before import.

CHR-108 / CHR-ING-001 provides a privileged local CLI for reviewed canonical
history. Validation performs database-enforced read-only checks; committing
requires a reviewed hash receipt and explicit confirmation. It creates only
immutable history, not catalogs, operational assignments, mining runs, or rules.
See the bootstrap workflow (`docs/HISTORICAL_BOOTSTRAP.md`) before using it.
Pending onboarding worksheets are not importable mappings. Bootstrap and
finalized-term capture are separate workflows; upload/staging remains separate work.

CHR-110 / CHR-ING-002 adds administrator-only capture of a finalized, approved,
inactive term's master schedule after fresh independent validation. It does not
run mining or activate rules. See the capture contract (`docs/HISTORICAL_CAPTURE.md`)
and [the parallel teammate plan](tasks/plan.md). Capture UI, time comparison, and
data-review runbook work are separate teammate tickets; uploads/staging remain
outside this backend capture feature.

CHR-113 / CHR-GA-004 connects the active RuleSet to local GA workers through a
versioned, persisted guidance snapshot and the explicitly approved Offering-level
runtime scorer. With no active guidance, generation remains explicitly unguided.
It does not mine or activate rules, and queued jobs do not adopt later active-set
changes. See runtime guidance policy and codec compatibility (`docs/RUNTIME_GUIDANCE.md`).

CHR-126 / CHR-ARM-006 makes local ARM publication retry-safe per execution job.
The worker supplies the current claim identity, and publication validates and
locks that live claim before atomically storing a job-linked draft and its
recovery envelope. A reclaimed job reuses that artifact/result without mining
again; a separate new job remains a separate mining run. Existing and imported
RuleSets are not backfilled with invented job provenance. Publication renews a
full configured lease but must finish inside it; expiry rolls back the write.
Stop old workers before applying the additive migration and restart with the
updated code. Do not run mixed old/new workers: old workers lack this fence.
Publication does not activate rules or mark a job succeeded; the parent still
performs its fenced terminal update.

CHR-165 / CHR-FIX-005 applies the same live-claim fence to term-wide GA schedule
publication, including recovery of an existing job-linked schedule. The worker
injects the current claim identity even for previously queued payloads; no queue
rewrite or migration is needed. Stop old workers before deploying the updated
worker and schedule service together, since old workers do not enforce this
publication fence. Publication renews the lease and must finish within it.

The forward merge revision `c6e8a0b2d4f7` joins publication with the historical
ingestion/provenance lineage without rewriting either branch. Use the normal
`alembic upgrade head` from either prior branch. Recovery envelopes are deferred
on ordinary RuleSet reads and explicitly loaded only for recovery.

## Scheduling Features

### Hard Constraints page

`/hard-constraints` (Administration, administrator only) lists every hard
constraint the validator checks and lets the Department Administrator change
their numeric limits. The checks themselves cannot be switched off.

| Limit | Default |
| --- | --- |
| Full-time teaching load cap | 24 units |
| Part-time teaching load cap | 17 units |
| Block day target (first class start to last class end) | 8 hours |
| Block day limit | 10 hours |
| Back-to-back limit for a teacher or a Block | 6 hours |

Each save is a new version in `hard_constraint_configuration` (migration
`a1d3f5b7c9e2`) and becomes the only active one. Problems copy the active
limits when they are built, so the validator, the GA and readiness read them
from one place and a queued job keeps the limits it was submitted with.
Payloads without limits decode to the defaults; versions saved with the older
per-type fields (lectures and labs in a row, `block_run_limit_hours`) still
load. The API is `GET /api/v1/hard-constraints` and `GET .../versions`
(committee members and administrators) and `PUT /api/v1/hard-constraints`
(administrators; a concurrent save returns 409 `CONFIGURATION_CHANGED`).

Subject allowed rooms are also a hard constraint: a class outside its
subject's allowed rooms is reported as `ROOM_NOT_ALLOWED`, including manual
edits and pinned classes. A run of back-to-back classes at or over the limit
is reported as `BLOCK_CONSECUTIVE_EXCEEDED`.

### Per-Block generation

Each Block (or group of Blocks sharing an Offering) gets one day window of the
target length. Sampling, mutation, crossover and repair draw start slots only
from that window. The window is chosen around other Blocks' locked classes and
only counts teachers still under their unit cap. A short per-Block break keeps
back-to-back runs under the limit. A run is retried with a 9-hour and then a
10-hour day only when the 8-hour run leaves a class unplaced or conflicting.
The first complete, conflict-free run wins; otherwise the run placing the most
classes wins. A paired Block's labs are placed in the first Block's run, at
the same time with their own teacher and room, and held as reservations until
the partner's own proposal exists. Labs are repaired before lectures. A
Mon–Thu day-balance soft score is used in master-schedule selection; recorded
generation metrics strip it, so stored P(C) means the same in every run.

### Master Schedule

- The Block list follows the generation order used by Generate. The order
  panel goes by program or year and ranks by default order, most students,
  most Blocks, or a custom order, remembered per browser.
- Select all skips locked Blocks; **Unlock selected** unlocks every ticked
  locked Block in turn.
- Batch generation sends review-edited proposals as `context_edits`, so the
  next Block is generated around the edits.
- Proposals under review are checked against each other for a shared room or
  instructor at the same time. Clashes show on the Block list, in the review
  panel and on the grid card before approval.
- The class editor counts other Blocks' pending proposals as taken, for both
  clashes and teaching load, and shows where the paired Block takes the same
  subject.
- Approving a Block copies each shared lecture it places onto an approved
  partner Block that has no record for it. An approved Block with unplaced
  classes shows "N classes missing".
- Term 3 (summer) hand edits allow only the MTWTh or F patterns.

### Committee Timetable

`/timetable` (formerly `/committee-timetable`, which redirects) has a Block,
Room and Faculty view switch. Each view shows one Block, room or member at a
time, chosen with a searchable picker; Room view opens on LB442. Exports follow
the current view, with a Whole schedule option. A joint class is drawn as one
card naming each Block.

### Dashboard schedule metrics

`GET /api/v1/schedules/{schedule_id}/metrics` returns schedule quality and
utilization metrics, each with its numerator and denominator: room
utilization, faculty load against the caps, assignment completeness, hard
conflicts, Block day balance and preference satisfaction. Student counts are
approximated by Block capacity.

The Dashboard shows the three metrics the thesis reports: **Assignment
completeness**, **Room utilization** and **Faculty load**. Each is green when
it meets its ideal and red when it does not, with a help tooltip giving the
description, formula and ideal. Hard conflicts, Block day balance and subject
preference are still computed and returned by the API but have no Dashboard
card. The GA's day-balance and subject-preference behaviour is unchanged.
Definitions and verified sources are in `docs/SCHEDULE_QUALITY_METRICS.md`.

### Users

Administrators can rename an account from the Users page
(`PATCH /api/v1/users/{user_id}`). Usernames follow the account-creation rules
and are checked case-insensitively against other accounts.

### Matched-pair count override

The official GA evaluation uses 10 matched seed pairs. For the
adviser-requested 3-pair test, set `CHRONOS_MATCHED_PAIR_COUNT=3` for both the
worker and the analyzer, then run from `backend/`:

```bash
CHRONOS_MATCHED_PAIR_COUNT=3 THREE_PAIR_OUT=<dir> PYTHONPATH=. python scripts/three_pair_evaluation.py
```

The script submits seeds 0–2 for T1–T3 one term at a time with the official
Rule Set and GA configurations, skipping a term whose
`<dir>/<term>/evaluation.csv` exists. Its results run on the current code and
are not comparable with the official batches. The results are in
`experiments/CHR-152-CHR-EVAL-010/2026-11-23-three-pair/`.

## Frontend Setup

Use Node.js 24 for the frontend. If `nvm` is installed, select it before
installing dependencies:

```bash
nvm install 24
nvm use 24
cd frontend
npm ci
npm run dev
```

The application is available at `http://localhost:5173`.

Other frontend commands:

```bash
npm run typecheck
npm run build
npm run preview
```

`VITE_API_BASE_URL` is configured in `frontend/.env`. Keep it pointed at the
local backend and update `frontend/.env.example` if new public Vite environment
variables are introduced.

## Run the Full Stack

Use four WSL2 terminal sessions for native development.

Terminal 1, from the repository root:

```bash
supabase start
```

Terminal 2:

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Terminal 3:

```bash
cd backend
source .venv/bin/activate
python -m app.worker
```

Terminal 4:

```bash
cd frontend
npm run dev
```

Start services in this order so the API and worker can connect to PostgreSQL
before the frontend begins making API requests. GA and ARM requests remain
queued until the worker is running; their durable status can be polled through
the URL returned in the response `Location` header.

## Docker Compose Alternative

The root Compose file can run the backend, worker, and frontend in containers.
Supabase is not included in Compose and must still be started separately with
the CLI. Ensure both `.env` files exist before running:

```bash
supabase start
docker compose up --build
```

The Compose backend connects to the host-published Supabase database through
`host.docker.internal`. Stop the application containers with:

```bash
docker compose down
```

Stop Supabase separately when it is no longer needed:

```bash
supabase stop
```

The development Compose file publishes its ports on `127.0.0.1` only.

## Local-Production Stack (LAN)

`docker-compose.prod.yml` builds the frontend for production and serves it
from the backend container on port 8000, together with the API, so the
browser talks to a single origin. No Vite dev server runs. The worker runs
alongside it. Both containers use host networking so the backend sees each
client's real address.

By default the stack is local-only: it binds `127.0.0.1` and answers only
loopback clients. To open it to the LAN, say the server is `192.168.1.10`
on `192.168.1.0/24`, set these in `backend/.env`:

```bash
TRUSTED_HOSTS=["localhost","127.0.0.1","192.168.1.10"]
ALLOWED_CLIENT_NETWORKS=["127.0.0.0/8","::1/128","192.168.1.0/24"]
```

`ALLOWED_CLIENT_NETWORKS` replaces the default, so keep the loopback entries.
Requests from any other address get `403 UNTRUSTED_CLIENT`. Only the TCP peer
address is checked; `X-Forwarded-For` is ignored. Then start the stack bound
to all interfaces:

```bash
supabase start
CHRONOS_BIND_HOST=0.0.0.0 docker compose -f docker-compose.prod.yml up --build
```

Open `http://192.168.1.10:8000`. Compose interpolates `CHRONOS_BIND_HOST` on
every `up`, so set it on every `up` that includes `app` (`up -d`,
`up --build`, `up --force-recreate`); an `up` without it recreates `app` on
`127.0.0.1` only, and it still reports healthy. `restart` keeps the stored
bind address. Stop it with
`docker compose -f docker-compose.prod.yml down`.

Host networking needs Docker Engine on Linux (including inside WSL2) or Docker
Desktop with host networking enabled. On WSL2, LAN clients reach the server
only with mirrored networking; NAT mode and `netsh` port proxies hide the
client's address, so the subnet check would see the wrong one.

`docker compose -f docker-compose.prod.yml ps` shows each container's health.
Startup, restart, worker recovery and failure diagnosis are in
`docs/LOCAL_DEPLOYMENT_RUNBOOK.md`.

## Backup and Restore

Back up the local database to `backups/` and restore an archive into a clean
database, all on the machine:

```bash
backend/scripts/chronos_db.sh backup
backend/scripts/chronos_db.sh restore backups/<archive>.dump chronos_restored
```

See the backup and restore runbook (`docs/BACKUP_RESTORE.md`) for prerequisites,
retention and recovery boundaries.

## Verification

With the full stack running, verify each application service:

```bash
curl http://localhost:8000/health
curl -I http://localhost:5173
```

For the same checks used by pull request CI, run:

```bash
cd backend
source .venv/bin/activate
pytest app/tests

cd ../frontend
npm run build
npm run check:offline
```

Network boundaries, the no-egress checks and a repeatable offline run are in
`docs/LOCAL_RUNTIME_BOUNDARIES.md`.

## Development Workflow

Jira project `CHR` tracks execution; GitHub tracks branches, pull requests,
review, and CI. Every task has both a native Jira key such as `CHR-39` and a
stable implementation-plan ID such as `CHR-DOC-002`.

- Team workflow and naming rules: [`GUIDE.md`](GUIDE.md)
- Requirement and Jira traceability: `docs/REQUIREMENTS_TRACEABILITY.md`
- MCP setup and operating checks: `docs/MCP_WORKFLOW.md`

OpenCode users can run `/start-ticket`, `/submit-ticket`, and
`/complete-ticket` after MCP authentication. Restart OpenCode after first
cloning or after changing `.opencode` configuration files.

## Local URLs

| Component | URL |
| --- | --- |
| Frontend | `http://localhost:5173` |
| Backend API | `http://localhost:8000` |
| API documentation | `http://localhost:8000/docs` |
| Supabase Studio | `http://127.0.0.1:54323` |

## File Structure

Main paths only; tests sit next to the code they cover (`*.test.tsx`) or under
`backend/app/tests/{unit,integration,support}/`.

```
chronos/
├── AGENTS.md, CLAUDE.md, GUIDE.md      # agent rules, layout notes, team workflow
├── PROJECT_OVERVIEW.md, design.md      # thesis summary, UI design notes
├── setup.sh                            # one-shot local dev setup
├── docker-compose.yml                  # dev stack: backend, worker, frontend
├── docker-compose.prod.yml             # local production: built frontend served by the backend
├── docs, dataset, experiments          # symlinks into private/ (saiimonn/Chronos-docs)
├── scripts/deploy/                     # CD helpers
├── supabase/config.toml                # local Supabase stack
├── tasks/plan.md                       # completed-term capture lane plan
├── .github/workflows/{ci,cd,cd-test}.yml
│
├── backend/
│   ├── alembic/versions/               # additive forward migrations
│   ├── data/                           # catalog CSVs and scenario/ (see data/README.md)
│   ├── scripts/
│   │   ├── chronos_db.sh               # backup and restore
│   │   ├── refresh_test_db.sh          # /devtest/ database refresh
│   │   ├── three_pair_evaluation.py    # 3-pair research run
│   │   └── colab_prototype/            # Colab notebooks and analyzers, not runtime
│   └── app/
│       ├── main.py                     # FastAPI app, routers under /api/v1
│       ├── worker.py                   # durable job worker (GA, ARM, evaluation)
│       ├── core/                       # settings, API errors
│       ├── db/session.py               # engine, SessionLocal, Base
│       ├── models/                     # SQLAlchemy models
│       ├── schemas/                    # Pydantic request/response models
│       ├── domain/                     # ORM-free scheduling types, ConstraintLimits, meeting patterns
│       ├── repositories/               # ORM <-> domain mapping, job queue
│       ├── api/routes_*.py             # auth, users, terms, catalog, faculty, blocks, cohorts,
│       │                               # offerings, time slots, readiness, schedules, jobs,
│       │                               # rule sets, GA configuration, hard constraints,
│       │                               # historical uploads, evaluations, audit
│       ├── cli/                        # seed_users, seed_catalog, seed_scenario, import_history, ...
│       └── services/
│           ├── catalog/                # terms, faculty, rooms, subjects, curricula, Blocks, offerings
│           ├── ingestion/              # historical files, uploads, completed-term capture
│           ├── arm/                    # preprocessing, Apriori mining, rule packages, faculty metrics
│           ├── ga/                     # chromosome, fitness, operators, repair, day balance, engine
│           ├── validation/             # the single shared hard-constraint validator
│           ├── jobs/                   # payload codecs, handlers, supervisor
│           ├── hard_constraints.py     # versioned ConstraintLimits
│           ├── schedule_metrics.py     # Dashboard quality metrics
│           ├── schedules.py, schedule_export.py, evaluation.py, rule_sets.py, readiness.py, ...
│
└── frontend/
    ├── scripts/check-no-external-urls.mjs
    ├── wireframes/
    └── src/
        ├── App.tsx                     # routes
        ├── api/client.ts               # the only backend client
        ├── pages/                      # Dashboard, Term Setup (SchedulePage), Master Schedule,
        │   │                           # Committee Timetable, Roster, Subjects, Rooms, Buildings,
        │   │                           # Job History, Completed History, Historical Upload,
        │   │                           # Rule Sets, Hard Constraints, GA Configuration,
        │   │                           # GA Evaluation, Users, Login
        │   ├── master-schedule/        # class editor, generation order, proposal clashes
        │   └── roster/                 # preferred subjects, time windows
        ├── components/                 # layout, schedule-grid, modals, forms, catalog,
        │                               # metrics, readiness, RoleGate
        ├── state/                      # Block generation state, app events
        ├── config/, hooks/, utils/, styles/
```
