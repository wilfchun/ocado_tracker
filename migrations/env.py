from alembic import context
from sqlalchemy import create_engine

from household_purchases.config import Settings

config = context.config
engine = create_engine(Settings().database_url(), hide_parameters=True)
with engine.connect() as connection:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
