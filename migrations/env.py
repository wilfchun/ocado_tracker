from alembic import context
from sqlalchemy import create_engine, text

from household_purchases.config import Settings
from household_purchases.db import LOCK_ID

# One external transaction also serializes migrations with importer writes.
engine = create_engine(
    (context.config.attributes.get("database_url") or Settings().database_url()),
    hide_parameters=True,
)
with engine.begin() as connection:
    connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOCK_ID})
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
