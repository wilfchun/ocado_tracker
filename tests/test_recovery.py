"""Restore a dump and archive into a separate disposable database, then reimport."""

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from household_purchases.db import engine_for
from household_purchases.ingestion import Ingestor

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not shutil.which("pg_dump") or not shutil.which("pg_restore"),
        reason="PostgreSQL CLI tools required",
    ),
]


def test_restore_and_reimport(db, tmp_path):
    raw = (Path(__file__).parent / "fixtures/upcoming.eml").read_bytes()
    archive = tmp_path / "archive"
    assert Ingestor(db, archive).ingest(raw)[1] == "imported"
    shutil.copytree(archive, tmp_path / "restored-archive")
    env = dict(os.environ, PGDATABASE=db.url.database)
    for field, setting in [
        ("host", "PGHOST"),
        ("port", "PGPORT"),
        ("username", "PGUSER"),
        ("password", "PGPASSWORD"),
    ]:
        if (value := getattr(db.url, field)) is not None:
            env[setting] = str(value)
    dump = tmp_path / "purchases.dump"
    subprocess.run(
        ["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--file", str(dump)],
        env=env,
        check=True,
        capture_output=True,
    )
    name = "purchases_restore_" + uuid.uuid4().hex
    restored = engine_for(db.url.set(database=name).render_as_string(hide_password=False))
    with db.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text(f"CREATE DATABASE \"{name}\" TEMPLATE template0 ENCODING 'UTF8'"))
    try:
        subprocess.run(
            ["pg_restore", "--dbname", name, "--no-owner", "--no-acl", str(dump)],
            env=env,
            check=True,
            capture_output=True,
        )
        importer = Ingestor(restored, tmp_path / "restored-archive")
        assert importer.ingest(raw)[1] == "duplicate"
        assert importer.retry(1, reprocess=True)[1] == "imported"
        with restored.connect() as c:
            assert c.scalar(text("SELECT count(*) FROM purchase_items")) == 30
            assert c.scalar(text("SELECT sum(total_pence) FROM orders")) == 9291
    finally:
        restored.dispose()
        with db.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
            c.execute(text(f'DROP DATABASE "{name}"'))
