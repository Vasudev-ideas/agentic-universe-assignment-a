from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS workflows(workflow_id TEXT PRIMARY KEY, schema_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dispositions(
 workflow_id TEXT NOT NULL, code TEXT NOT NULL, label TEXT NOT NULL,
 is_terminal INTEGER NOT NULL, is_success INTEGER NOT NULL,
 PRIMARY KEY(workflow_id, code));
CREATE TABLE IF NOT EXISTS leads(
 lead_id INTEGER PRIMARY KEY, workflow_id TEXT NOT NULL, batch_id TEXT NOT NULL,
 phone_token TEXT NOT NULL, phone_cipher BLOB NOT NULL, external_ref TEXT NOT NULL,
 fields_json TEXT NOT NULL, created_seq INTEGER NOT NULL,
 UNIQUE(workflow_id, phone_token));
CREATE TABLE IF NOT EXISTS rejections(
 id INTEGER PRIMARY KEY, workflow_id TEXT NOT NULL, batch_id TEXT NOT NULL,
 row_number INTEGER NOT NULL, reason TEXT NOT NULL,
 UNIQUE(workflow_id, batch_id, row_number));
CREATE TABLE IF NOT EXISTS events(
 event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, workflow_id TEXT,
 call_id TEXT, occurred_at TEXT, phone_token TEXT, payload_json TEXT NOT NULL,
 ingestion_seq INTEGER NOT NULL, lead_id INTEGER REFERENCES leads(lead_id));
CREATE INDEX IF NOT EXISTS ix_events_call ON events(workflow_id, call_id);
CREATE INDEX IF NOT EXISTS ix_events_lead ON events(lead_id);
CREATE INDEX IF NOT EXISTS ix_events_phone ON events(workflow_id, phone_token);
CREATE TABLE IF NOT EXISTS processed_files(
 path TEXT PRIMARY KEY, kind TEXT NOT NULL, processed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS file_errors(
 path TEXT PRIMARY KEY, kind TEXT NOT NULL, message TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS quality(key TEXT PRIMARY KEY, value INTEGER NOT NULL);
"""


QUALITY_KEYS = (
    "lead_rows_total", "lead_rows_rejected", "duplicate_lead_rows_merged", "field_warnings",
    "events_total", "malformed_events", "duplicate_events_dropped", "orphan_events",
    "unknown_disposition_events",
)


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def initialize(self) -> None:
        with self.connect() as con:
            con.executescript(SCHEMA)
            for key in QUALITY_KEYS:
                con.execute("INSERT OR IGNORE INTO quality(key,value) VALUES(?,0)", (key,))

    @staticmethod
    def increment(con: sqlite3.Connection, key: str, amount: int = 1) -> None:
        con.execute("UPDATE quality SET value=value+? WHERE key=?", (amount, key))

    @staticmethod
    def mark_file(con: sqlite3.Connection, path: str, kind: str) -> bool:
        cur = con.execute(
            "INSERT OR IGNORE INTO processed_files(path,kind,processed_at) VALUES(?,?,datetime('now'))",
            (path, kind),
        )
        return cur.rowcount == 1
