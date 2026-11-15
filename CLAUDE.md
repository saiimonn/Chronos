# Chronos

DCISM Automated Class Scheduling System. Thesis project: Association Rule Mining (ARM)
feeds a Genetic Algorithm (GA) that produces conflict-free class schedules.

## Layout

- `backend/` — FastAPI + SQLAlchemy + Alembic (Python 3.12)
- `frontend/` — React 19 + Vite + TypeScript
- `supabase/` — local Supabase CLI stack (Docker); config lives here
- `docs/`, `dataset/`, `experiments/` — symlinks into `private/`, a gitignored
  clone of the private repo `saiimonn/Chronos-docs`
  (`gh repo clone saiimonn/Chronos-docs private`). `docs/` holds SRS excerpts, implementation plan, and the thesis master document
  (`chronos-master-document.pdf`, 198 pp.; `chronos-master-document.txt` is a
  page-marked text extraction of the same file — grep that one)
  — both are snapshots; the authoritative manuscript is named in `docs/manuscript/README.md`
- `.github/workflows/ci.yml` — dev-time CI only, never part of the deployed system
- `.github/workflows/cd.yml` — deploys each merge into `main` to the DCISM server once CI passes
- `.github/workflows/cd-test.yml` — then deploys the same commit to the `/devtest/` test environment (also by hand, any branch)

## Hard constraints

- **Local only.** Never point at Supabase's managed cloud. Never run `supabase link`.
  Postgres runs in Docker via `supabase start`.
- No external network calls at runtime.
- Secrets live in `backend/.env` and `frontend/.env`, both gitignored. Commit only
  the `.env.example` templates.
- `supabase/.temp/` and `supabase/.branches/` are gitignored — they contain local
  service secrets. Do not force-add them.
- Research-only reference review and draft RuleSet import may be approved by a
  named researcher (`approved_for_research_by_researcher`). Never record it as
  Department Administrator approval. Activation still needs the administrator.
  See `AGENTS.md`.
- **Demos and tests run only on `/devtest/`.** Follow the mandatory demo flow
  and rules in `docs/TEST_ENVIRONMENT.md`: log out of the official site, use a
  private window or isolated browser context, and use only the shared developer
  password there. Never demo, test or experiment on the official site or
  production database. Never deploy, refresh the test DB or touch production
  settings without explicit approval in the session.

## Running the stack

```bash
supabase start                                    # from repo root
cd backend && source .venv/bin/activate && uvicorn app.main:app --reload
cd frontend && npm run dev
```

Node 24 is required (nvm). Run everything inside WSL2 on Windows.

## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- For cross-module "how does X relate to Y" questions, prefer `graphify query "<question>"`, `graphify path "<A>" "<B>"`, or `graphify explain "<concept>"` over grep — these traverse the graph's EXTRACTED + INFERRED edges instead of scanning files
- After modifying code files in this session, run `graphify update .` to keep the graph current (AST-only, no API cost)
