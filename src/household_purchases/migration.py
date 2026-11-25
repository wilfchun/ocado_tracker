from pathlib import Path

from alembic import command
from alembic.config import Config


def migrate(database_url: str) -> None:
    location = Path(__file__).parent / "migrations"
    if not location.is_dir():
        location = Path(__file__).resolve().parents[2] / "migrations"
    config = Config()
    config.set_main_option("script_location", str(location))
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")
