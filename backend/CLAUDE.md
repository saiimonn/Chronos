# Chronos backend

FastAPI, Python 3.12, virtualenv at `backend/.venv`.

## Layout

- `app/core/config.py` — pydantic-settings, reads `backend/.env`
- `app/db/session.py` — engine, `SessionLocal`, `Base`
- `app/models/` — SQLAlchemy models; every model must be imported from
  `app/models/__init__.py` or Alembic autogenerate will miss it
- `app/schemas/` — Pydantic request/response schemas
- `app/api/` — routers
- `app/services/{ingestion,arm,ga,validation}/` — domain logic, no FastAPI imports
- `app/tests/{unit,integration}/`

## Commands

Run from `backend/` with the venv active:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
pytest app/tests
alembic revision --autogenerate -m "message"
alembic upgrade head
```

## Rules

- Alembic reads the DB URL from `settings.database_url`; `alembic.ini` has no URL.
  Never hardcode credentials there.
- Keep algorithm code (ARM/GA) out of route handlers — routes call services.
- `requirements.txt` pins the thesis-mandated versions (mlxtend, deap, sqlalchemy,
  fastapi). Do not bump without updating the thesis tool table.
