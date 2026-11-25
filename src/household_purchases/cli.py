import argparse
from pathlib import Path
import sys

from household_purchases.parsers.base import ParseError
from household_purchases.parsers.ocado import OcadoParser


def main() -> int:
    parser = argparse.ArgumentParser(prog="household-purchases")
    sub = parser.add_subparsers(dest="command", required=True)
    eml = sub.add_parser("import-eml")
    eml.add_argument("path", type=Path)
    eml.add_argument("--dry-run", action="store_true")
    eml.add_argument("--show-json", action="store_true", help="Print private purchase data locally")
    args = parser.parse_args()
    try:
        order = OcadoParser().parse(args.path.read_bytes())
        if not args.dry_run:
            parser.error("persistence not configured; use --dry-run")
        print(order.model_dump_json(indent=2) if args.show_json else f"Validated {len(order.items)} lines; total {order.total_pence} pence; provisional")
        return 0
    except (ParseError, OSError) as exc:
        print(f"Import failed: {exc if isinstance(exc, ParseError) else 'file_error'}", file=sys.stderr)
        return 1
