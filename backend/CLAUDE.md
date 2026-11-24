# Chronos backend

FastAPI, Python 3.12, virtualenv at `backend/.venv`.

## Layout

- `app/core/config.py` — pydantic-settings, reads `backend/.env`
- `app/db/session.py` — engine, `SessionLocal`, `Base`
- `app/models/` — SQLAlchemy models; every model must be imported from
  `app/models/__init__.py` or Alembic autogenerate will miss it
- `app/schemas/` — Pydantic request/response schemas
- `app/api/routes_*.py` — routers, mounted under `/api/v1` in `app/main.py`
- `app/domain/` — ORM-free scheduling types (`SchedulingProblem`,
  `ConstraintLimits`, meeting patterns) shared by GA, validator and services
- `app/repositories/` — ORM ↔ domain mapping and the job queue
- `app/services/{catalog,ingestion,arm,ga,validation,jobs,migration}/` and
  `app/services/*.py` — domain logic, no FastAPI imports
- `app/worker.py` — durable worker for GA, ARM and evaluation jobs
  (`python -m app.worker`)
- `app/cli/` — seeding, history import and maintenance commands
  (`python -m app.cli.<name>`)
- `app/tests/{unit,integration,support}/`

Hard-constraint limits (load caps, Block day target and limit, back-to-back
limit) come from the active `hard_constraint_configuration` version via
`services/hard_constraints.active_constraint_limits`, falling back to the
`ConstraintLimits` defaults. Never hardcode them in GA or validator code; read
them from the problem.

## Commands

Run from `backend/` with the venv active:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
python -m app.worker
pytest app/tests
pytest app/tests -m postgres   # needs TEST_DATABASE_URL, see README
alembic revision --autogenerate -m "message"
alembic upgrade head
```

## Rules

- Alembic reads the DB URL from `settings.database_url`; `alembic.ini` has no URL.
  Never hardcode credentials there.
- Keep algorithm code (ARM/GA) out of route handlers — routes call services.
- `requirements.txt` pins the thesis-mandated versions (mlxtend, deap, sqlalchemy,
  fastapi). Do not bump without updating the thesis tool table.
