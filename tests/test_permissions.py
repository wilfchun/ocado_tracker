from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from household_purchases.db import engine_for
from household_purchases.ingestion import Ingestor

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[1]


def test_actual_writer_and_reader_roles(db, tmp_path):
    with db.begin() as c:
        c.execute(text((ROOT / "scripts/bootstrap-roles.sql").read_text()))
        c.execute(text((ROOT / "scripts/grants.sql").read_text()))
    writer = engine_for(
        db.url.set(username="purchases_importer").render_as_string(hide_password=False)
    )
    reader = engine_for(
        db.url.set(username="purchases_reader").render_as_string(hide_password=False)
    )
    try:
        raw = (ROOT / "tests/fixtures/upcoming.eml").read_bytes()
        assert Ingestor(writer, tmp_path / "archive").ingest(raw)[1] == "imported"
        with reader.connect() as c:
            assert c.scalar(
                text("SELECT sum(order_total_gbp) FROM analytics.order_totals")
            ) == Decimal("92.91")
        for sql in (
            "SELECT * FROM source_messages",
            "UPDATE products SET name = name",
            "DELETE FROM analytics.order_totals",
            "CREATE TABLE public.forbidden (id int)",
        ):
            with pytest.raises(DBAPIError), reader.begin() as c:
                c.execute(text(sql))
    finally:
        writer.dispose()
        reader.dispose()
