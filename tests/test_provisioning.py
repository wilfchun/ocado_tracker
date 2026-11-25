import re
import uuid
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import text

from household_purchases.db import engine_for
from household_purchases.ingestion import Ingestor
from household_purchases.provisioning import (
    ProvisionSettings,
    persistent_secret,
    provision,
    scram_verifier,
)


def test_persistent_private_credentials(tmp_path):
    path = tmp_path / "password"
    password = persistent_secret(path)
    assert len(password) >= 48
    assert path.stat().st_mode & 0o777 == 0o600
    assert persistent_secret(path) == password
    verifier = scram_verifier(password)
    assert re.fullmatch(
        r"SCRAM-SHA-256\$4096:[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+", verifier
    )
    assert password not in verifier
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        persistent_secret(path)


@pytest.mark.parametrize(
    "database", ["postgres", "template0", "template1", "x; DROP DATABASE x", "../data", "Name"]
)
def test_database_names_are_restricted(tmp_path, database):
    with pytest.raises(ValueError):
        ProvisionSettings(state_dir=tmp_path, database=database)


@pytest.mark.postgres
def test_local_database_provisioning_is_idempotent(db, tmp_path):
    name = "purchases_provision_" + uuid.uuid4().hex
    app = name + "_mb"
    settings = ProvisionSettings(
        state_dir=tmp_path / "state",
        database=name,
        application_database=app,
        socket_dir=db.url.host,
        port=db.url.port,
    )
    writer = engine_for(settings.url("purchases_importer"))
    with psycopg.connect(
        dbname="postgres", host=db.url.host, port=db.url.port, autocommit=True
    ) as admin:
        try:
            provision(settings)
            passwords = {p.name: p.read_bytes() for p in settings.state_dir.iterdir()}
            raw = (Path(__file__).parent / "fixtures/ocado_upcoming_sanitised.eml").read_bytes()
            importer = Ingestor(writer, tmp_path / "archive")
            assert importer.ingest(raw)[1] == "imported"
            with psycopg.connect(
                dbname=name, user="purchases_owner", host=db.url.host, port=db.url.port
            ) as owner:
                owner.execute("GRANT UPDATE ON products TO purchases_reader")
                owner.execute("GRANT UPDATE ON analytics.order_totals TO purchases_reader")
                owner.execute("GRANT CREATE ON SCHEMA public TO purchases_reader")
            provision(settings)
            assert {p.name: p.read_bytes() for p in settings.state_dir.iterdir()} == passwords
            assert importer.ingest(raw)[1] == "duplicate"
            with writer.connect() as c:
                assert c.scalar(text("SELECT count(*) FROM purchase_items")) == 30
            reader_password = (settings.state_dir / "reader-password").read_text().strip()
            with psycopg.connect(
                dbname=name,
                user="purchases_reader",
                password=reader_password,
                host=db.url.host,
                port=db.url.port,
            ) as reader:
                assert reader.execute(
                    "SELECT sum(order_total_gbp) FROM analytics.order_totals"
                ).fetchone()[0] == Decimal("92.91")
                for query in (
                    "SELECT * FROM source_messages",
                    "UPDATE products SET name = name",
                    "UPDATE analytics.order_totals SET status = 'final'",
                    "CREATE TABLE public.forbidden (id int)",
                ):
                    with pytest.raises(psycopg.errors.InsufficientPrivilege):
                        with reader.transaction():
                            reader.execute(query)
        finally:
            writer.dispose()
            for database in (name, app):
                admin.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(database)
                    )
                )


@pytest.mark.postgres
def test_existing_database_owner_is_not_taken_over(db, tmp_path):
    from household_purchases.provisioning import ensure_database

    with psycopg.connect(
        dbname="postgres", host=db.url.host, port=db.url.port, autocommit=True
    ) as admin:
        with pytest.raises(ValueError, match="database_owner_mismatch"):
            ensure_database(admin, db.url.database, "purchases_owner")
