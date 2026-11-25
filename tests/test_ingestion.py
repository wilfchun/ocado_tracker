from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from household_purchases.ingestion import Ingestor
from household_purchases.models import Order, Product, PurchaseItem, SourceMessage

pytestmark = pytest.mark.postgres
RAW = (Path(__file__).parent / "fixtures/upcoming.eml").read_bytes()


def test_idempotent_catalogue_and_analytics(db, tmp_path):
    importer = Ingestor(db, tmp_path / "archive")
    assert importer.ingest(RAW)[1] == "imported"
    assert importer.ingest(RAW)[1] == "duplicate"
    assert importer.ingest(RAW.replace(b"SYNTHETIC-001", b"SYNTHETIC-002"))[1] == "imported"
    with Session(db) as s:
        assert s.scalar(select(func.count()).select_from(Order)) == 2
        assert s.scalar(select(func.count()).select_from(PurchaseItem)) == 60
        assert s.scalar(select(func.count()).select_from(Product)) == 30
        assert s.scalar(
            text("SELECT sum(line_spend_gbp) FROM analytics.purchase_lines")
        ) == Decimal("184.62")
        assert s.scalar(text("SELECT sum(order_total_gbp) FROM analytics.order_totals")) == Decimal(
            "185.82"
        )
        assert s.scalar(
            text(
                "SELECT effective_unit_price_gbp FROM analytics.purchase_lines WHERE product_name LIKE 'Kallo%' LIMIT 1"
            )
        ) == Decimal("1.40")


def test_failure_conflict_and_reprocess(db, tmp_path):
    importer = Ingestor(db, tmp_path / "archive")
    source_id, _ = importer.ingest(RAW)
    invalid = RAW.replace(b"92.91", b"92.90")
    failed, status = importer.ingest(invalid)
    assert status == "failed"
    assert importer.retry(failed)[1] == "failed"
    different = RAW.replace(b"Coca-Cola Zero Sugar", b"Changed product")
    conflict, status = importer.ingest(different)
    assert status == "failed"
    with Session(db) as s, s.begin():
        product = s.scalar(select(Product).where(Product.name.like("Kallo%")))
        product.classification_method = "manual"
    assert importer.retry(conflict, reprocess=True)[1] == "imported"
    with Session(db) as s:
        assert s.scalar(select(func.count()).select_from(Order)) == 1
        assert s.scalar(select(func.count()).select_from(PurchaseItem)) == 30
        assert s.get(SourceMessage, source_id).status == "superseded"
        assert (
            s.scalar(select(Product).where(Product.name.like("Kallo%"))).classification_method
            == "manual"
        )


def test_atomic_failure(db, tmp_path):
    from sqlalchemy import event

    importer = Ingestor(db, tmp_path / "archive")

    def fail(*args):
        raise RuntimeError("sensitive injected failure")

    event.listen(PurchaseItem, "before_insert", fail)
    try:
        assert importer.ingest(RAW)[1] == "failed"
    finally:
        event.remove(PurchaseItem, "before_insert", fail)
    with Session(db) as s:
        assert s.scalar(select(func.count()).select_from(Order)) == 0
        assert s.scalar(select(func.count()).select_from(Product)) == 0
        assert s.scalar(select(SourceMessage)).error_code == "order_write_failed"
    assert importer.retry(1)[1] == "imported"


def test_archive_private_and_dry_run(tmp_path):
    from household_purchases.ingestion import archive

    directory = tmp_path / "archive"
    name = archive(RAW, directory)
    assert directory.stat().st_mode & 0o777 == 0o700
    assert (directory / name).stat().st_mode & 0o777 == 0o600
