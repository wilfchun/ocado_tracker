import os
import subprocess
import sys

import pytest
from sqlalchemy import text

from household_purchases.db import engine_for


@pytest.fixture(scope="session")
def pg_engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("set TEST_DATABASE_URL to a disposable PostgreSQL database")
    engine = engine_for(url)
    # Explicit opt in: this database is disposable, never a live purchase DB.
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS analytics CASCADE"))
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    env = dict(os.environ, DATABASE_URL=url)
    env.pop("DATABASE_URL_FILE", None)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True)
    yield engine
    engine.dispose()


@pytest.fixture
def db(pg_engine):
    with pg_engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE purchase_items, orders, products, source_messages, scan_state RESTART IDENTITY CASCADE"
            )
        )
    return pg_engine
