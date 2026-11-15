from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.core.config import settings

# CHR-184 / CHR-FIX-014: replace connections a DB restart left stale, and
# bound new connects to 5 s (at or under the worker heartbeat interval, so one
# stalled connect is retried before the lease ends). Queries on an already open
# connection, including the pre-ping, are not bounded by this.
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"connect_timeout": 5},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
