from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from household_purchases.config import Settings
from household_purchases.models import Order, ScanState, SourceMessage
from household_purchases.sync import scan

RAW = (Path(__file__).parent / "fixtures/upcoming.eml").read_bytes()


class FakeClient:
    fail_download = False
    interrupt = False

    def discover(self, *args, **kwargs):
        yield {"id": "e1", "blobId": "b1", "receivedAt": "2026-11-18T10:00:00Z"}
        if self.interrupt:
            raise ValueError("interrupted scan")
        yield {"id": "e2", "blobId": "b2", "receivedAt": "2026-11-18T10:00:00Z"}

    def download(self, blob):
        if self.fail_download and blob == "b2":
            raise ValueError("fetch failed")
        return (
            RAW
            if blob == "b1"
            else RAW.replace(b"SYNTHETIC-001", b"SYNTHETIC-002").replace(b"92.91", b"92.90")
        )


def test_dry_run_no_archive(tmp_path):
    archive = tmp_path / "archive"
    counts = scan(FakeClient(), Settings(raw_email_dir=archive), None, dry_run=True)
    assert counts["validated"] == 1 and counts["failed"] == 1
    assert not archive.exists()


@pytest.mark.postgres
def test_durable_failures_and_repeat_scans(db, tmp_path):
    settings = Settings(raw_email_dir=tmp_path / "archive")
    counts = scan(FakeClient(), settings, db)
    assert counts["imported"] == 1 and counts["failed"] == 1
    counts = scan(FakeClient(), settings, db)
    assert counts["duplicate"] == 1 and counts["failed"] == 1
    with Session(db) as s:
        assert s.get(ScanState, "fastmail").completed_at
        assert s.scalar(select(func.count()).select_from(SourceMessage)) == 2
        assert s.scalar(select(func.count()).select_from(Order)) == 1


@pytest.mark.postgres
def test_interruption_and_fetch_failure_do_not_complete_scan(db, tmp_path):
    settings = Settings(raw_email_dir=tmp_path / "archive")
    client = FakeClient()
    client.interrupt = True
    with pytest.raises(ValueError, match="interrupted"):
        scan(client, settings, db)
    with Session(db) as s:
        assert s.get(ScanState, "fastmail") is None
        assert s.scalar(select(func.count()).select_from(Order)) == 1
    client.interrupt = False
    client.fail_download = True
    assert scan(client, settings, db)["failed"] == 1
    with Session(db) as s:
        assert s.get(ScanState, "fastmail") is None
