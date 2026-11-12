from __future__ import annotations

import argparse
import os
from pathlib import Path

from .config import PiiProtector, Settings, generate_key
from .database import Database
from .event_ingestion import ingest_events
from .lead_ingestion import ingest_leads
from .reporting import generate_reports
from .schema_loader import load_schemas


def _settings(database: str) -> Settings:
    return Settings.from_env(database)


def _pipeline(database: str):
    settings = _settings(database)
    db = Database(settings.database)
    db.initialize()
    schemas = load_schemas(Path(__file__).parent.parent / "assignment-a-data", db)
    return db, PiiProtector(settings.pii_key, settings.lookup_key), schemas


def command_ingest(args: argparse.Namespace) -> None:
    db, protector, schemas = _pipeline(args.database)
    source = Path(args.path)
    drops = sorted(source.glob("drop_*")) if (source / "drop_1").exists() else [source]
    for drop in drops:
        ingest_leads(db, protector, drop, schemas)
        ingest_events(db, protector, drop)


def command_report(args: argparse.Namespace) -> None:
    db, protector, _ = _pipeline(args.database)
    generate_reports(db, protector, args.out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Assignment A interaction ledger pipeline")
    parser.add_argument("--database", default=os.environ.get("AU_DATABASE", "solution/ledger.sqlite3"))
    sub = parser.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("ingest")
    ingest.add_argument("path", help="A drop directory or a directory containing drop_1/drop_2")
    ingest.set_defaults(func=command_ingest)
    report = sub.add_parser("report")
    report.add_argument("--out", required=True)
    report.set_defaults(func=command_report)
    keys = sub.add_parser("generate-keys")
    keys.set_defaults(func=lambda _: print(f"AU_PII_KEY={generate_key()}\nAU_LOOKUP_KEY={generate_key()}"))
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
