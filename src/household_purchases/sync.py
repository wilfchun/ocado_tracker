import logging
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from household_purchases.config import Settings
from household_purchases.db import lock
from household_purchases.email.fastmail import FastmailClient, matches_headers, received_time
from household_purchases.ingestion import Ingestor
from household_purchases.models import ScanState
from household_purchases.parsers.base import ParseError
from household_purchases.parsers.ocado import OcadoParser

log = logging.getLogger(__name__)


def scan(
    client: FastmailClient,
    settings: Settings,
    engine: Engine | None,
    *,
    dry_run: bool = False,
    since: str | None = None,
    until: str | None = None,
) -> Counter:
    """Full scans by design: late/restored mail stays discoverable; no cursor loss."""
    counts = Counter(discovered=0, imported=0, duplicate=0, failed=0, validated=0)
    complete = True
    parser = OcadoParser(settings.timezone)
    importer = Ingestor(engine, settings.raw_email_dir, parser) if engine is not None else None
    for metadata in client.discover(settings, since=since, until=until):
        counts["discovered"] += 1
        try:
            raw = client.download(metadata["blobId"])
            received_at = received_time(metadata["receivedAt"])
        except Exception:
            complete = False
            counts["failed"] += 1
            log.error("fetch_failed; source remains discoverable on next full scan")
            continue
        rejection = None if matches_headers(raw, settings) else "source_filter_mismatch"
        if dry_run:
            try:
                if rejection:
                    raise ParseError(rejection)
                parser.parse(raw, received_at=received_at)
                counts["validated"] += 1
            except ParseError:
                counts["failed"] += 1
        else:
            if importer is None:
                raise ValueError("database_required")
            source_id, status = importer.ingest(
                raw,
                account=settings.source_account,
                remote_id=metadata["id"],
                received_at=received_at,
                rejection=rejection,
            )
            counts[status] += 1
            log.info("source_id=%d status=%s parser=%s", source_id, status, parser.version)
    # Parsing failures are durable records; failed downloads are not a complete scan.
    if complete and not dry_run and engine is not None:
        with Session(engine) as session, session.begin():
            lock(session)
            state = session.get(ScanState, settings.source_account)
            if state is None:
                state = ScanState(source_account=settings.source_account)
                session.add(state)
            state.completed_at = datetime.now(timezone.utc)
    return counts
