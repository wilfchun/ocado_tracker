import argparse
import logging
import os
import sys
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from household_purchases.config import Settings, secret_file
from household_purchases.db import engine_for
from household_purchases.email.fastmail import FastmailClient
from household_purchases.ingestion import Ingestor
from household_purchases.models import Order, Product, ScanState, SourceMessage
from household_purchases.parsers.base import ParseError
from household_purchases.parsers.ocado import OcadoParser
from household_purchases.sync import scan


def arguments() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="household-purchases")
    sub = parser.add_subparsers(dest="command", required=True)
    eml = sub.add_parser("import-eml")
    eml.add_argument("path", type=Path)
    eml.add_argument("--dry-run", action="store_true")
    eml.add_argument("--show-json", action="store_true", help="Print private purchase data locally")
    sub.add_parser("retry-failed")
    reprocess = sub.add_parser("reprocess")
    reprocess.add_argument("--source-id", type=int, required=True)
    sub.add_parser("status")
    for name in ("import-history", "sync"):
        remote = sub.add_parser(name)
        remote.add_argument("--since", type=date.fromisoformat)
        remote.add_argument("--until", type=date.fromisoformat)
        remote.add_argument("--dry-run", action="store_true")
    return parser


def execute(args: argparse.Namespace, settings: Settings) -> int:
    parser = OcadoParser(settings.timezone)
    if args.command == "import-eml" and args.dry_run:
        order = parser.parse(args.path.read_bytes())
        print(
            order.model_dump_json(indent=2)
            if args.show_json
            else f"Validated {len(order.items)} lines; total {order.total_pence} pence; provisional"
        )
        return 0
    if args.command in ("import-history", "sync"):
        token = secret_file("FASTMAIL_TOKEN_FILE")
        if not token:
            raise ValueError("fastmail_token_file_required")
        client = FastmailClient(token, settings.fastmail_account_id)
        engine = None
        try:
            if not args.dry_run:
                engine = engine_for(settings.database_url())

            def bound(value):
                return (
                    datetime.combine(value, time(), ZoneInfo(settings.timezone))
                    .astimezone(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z")
                    if value
                    else None
                )

            counts = scan(
                client,
                settings,
                engine,
                dry_run=args.dry_run,
                since=bound(args.since),
                until=bound(args.until),
            )
            print(" ".join(f"{key}={value}" for key, value in counts.items()))
            return int(counts["failed"] > 0)
        finally:
            client.close()
            if engine is not None:
                engine.dispose()
    engine = engine_for(settings.database_url())
    importer = Ingestor(engine, settings.raw_email_dir, parser)
    try:
        if args.command == "import-eml":
            source_id, status = importer.ingest(args.path.read_bytes())
            print(f"source_id={source_id} status={status} parser={parser.version}")
            return int(status == "failed")
        if args.command == "reprocess":
            source_id, status = importer.retry(args.source_id, reprocess=True)
            print(f"source_id={source_id} status={status}")
            return int(status == "failed")
        if args.command == "retry-failed":
            with Session(engine) as session:
                ids = list(
                    session.scalars(
                        select(SourceMessage.id).where(
                            SourceMessage.status.in_(["failed", "pending"])
                        )
                    )
                )
            failures = 0
            for source_id in ids:
                try:
                    _, status = importer.retry(source_id)
                except (ValueError, OSError):
                    status = "failed"
                print(f"source_id={source_id} status={status}")
                failures += status == "failed"
            return int(failures > 0)
        if args.command == "status":
            with Session(engine) as session:
                print(
                    f"last_complete_scan={session.scalar(select(func.max(ScanState.completed_at)))}"
                )
                print(f"last_imported_order={session.scalar(select(func.max(Order.created_at)))}")
                for status in ("failed", "pending"):
                    count = session.scalar(
                        select(func.count())
                        .select_from(SourceMessage)
                        .where(SourceMessage.status == status)
                    )
                    print(f"{status}={count}")
                count = session.scalar(
                    select(func.count()).select_from(Product).where(Product.category_id.is_(None))
                )
                print(f"uncategorised={count}")
            return 0
        raise ValueError("unknown_command")
    finally:
        engine.dispose()


def main() -> int:
    args = arguments().parse_args()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"), format="%(levelname)s %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        return execute(args, Settings())
    except Exception as exc:
        # Driver and HTTP exception strings can contain private parameters/URLs.
        reason = str(exc) if isinstance(exc, ParseError) else type(exc).__name__
        print(f"Command failed: {reason}", file=sys.stderr)
        return 1
