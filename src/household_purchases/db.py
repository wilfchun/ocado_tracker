from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

LOCK_ID = 803164721


def engine_for(url: str) -> Engine:
    engine = create_engine(url, hide_parameters=True, pool_pre_ping=True)
    if engine.dialect.name != "postgresql":
        raise ValueError("postgresql_required")
    return engine


def lock(session: Session) -> None:
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOCK_ID})
