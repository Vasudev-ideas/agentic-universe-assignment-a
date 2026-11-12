from __future__ import annotations

import json
from pathlib import Path

from .config import PiiProtector
from .database import Database
from .normalization import normalize_phone, parse_timestamp


KNOWN_TYPES = {
    "call.initiated", "call.telephony_failed", "call.connected", "call.ended",
    "disposition.set", "disposition.override",
}


def _next_sequence(con) -> int:
    row = con.execute("SELECT COALESCE(MAX(ingestion_seq),0)+1 FROM events").fetchone()
    return int(row[0])


def _relink(con, workflow_id: str | None, phone_token: str | None, call_id: str | None) -> None:
    if workflow_id and phone_token:
        con.execute("""UPDATE events SET lead_id=(SELECT lead_id FROM leads l
            WHERE l.workflow_id=events.workflow_id AND l.phone_token=events.phone_token)
            WHERE lead_id IS NULL AND workflow_id=? AND phone_token=?""",
                    (workflow_id, phone_token))
    if workflow_id and call_id:
        con.execute("""UPDATE events SET lead_id=(SELECT MIN(e2.lead_id) FROM events e2
            WHERE e2.workflow_id=events.workflow_id AND e2.call_id=events.call_id AND e2.lead_id IS NOT NULL)
            WHERE lead_id IS NULL AND workflow_id=? AND call_id=?""",
                    (workflow_id, call_id))


def _relink_all(con) -> None:
    con.execute("""UPDATE events SET lead_id=(SELECT lead_id FROM leads l
        WHERE l.workflow_id=events.workflow_id AND l.phone_token=events.phone_token)
        WHERE lead_id IS NULL AND phone_token IS NOT NULL""")
    con.execute("""UPDATE events SET lead_id=(SELECT MIN(e2.lead_id) FROM events e2
        WHERE e2.workflow_id=events.workflow_id AND e2.call_id=events.call_id AND e2.lead_id IS NOT NULL)
        WHERE lead_id IS NULL AND event_type='disposition.override'""")


def ingest_event_file(db: Database, protector: PiiProtector, path: Path) -> None:
    path_key = str(path.resolve())
    with db.connect() as con:
        if not db.mark_file(con, path_key, "event"):
            return
        try:
            fh = path.open(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            con.execute("INSERT OR REPLACE INTO file_errors VALUES(?,?,?)", (path_key, "event", str(exc)))
            return
        with fh:
            sequence = _next_sequence(con)
            for line in fh:
                if not line.strip():
                    continue
                Database.increment(con, "events_total")
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict) or not event.get("event_id"):
                        raise ValueError
                except (json.JSONDecodeError, ValueError, TypeError):
                    Database.increment(con, "malformed_events")
                    continue
                event_id = str(event["event_id"])
                if con.execute("SELECT 1 FROM events WHERE event_id=?", (event_id,)).fetchone():
                    Database.increment(con, "duplicate_events_dropped")
                    continue
                workflow = event.get("workflow_id")
                event_type = event.get("event_type")
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                code = payload.get("disposition_code")
                token = None
                phone = event.get("phone")
                if isinstance(phone, str):
                    normalized = normalize_phone(phone)
                    if normalized:
                        token = protector.token(normalized)
                known_disposition = True
                if isinstance(event_type, str) and event_type.startswith("disposition."):
                    known_disposition = bool(con.execute(
                        "SELECT 1 FROM dispositions WHERE workflow_id=? AND code=?", (workflow, code)
                    ).fetchone())
                    if not known_disposition:
                        Database.increment(con, "unknown_disposition_events")
                occurred = parse_timestamp(str(event.get("occurred_at", "")))
                con.execute("""INSERT INTO events(event_id,event_type,workflow_id,call_id,occurred_at,
                    phone_token,payload_json,ingestion_seq,lead_id) VALUES(?,?,?,?,?,?,?,?,NULL)""",
                            (event_id, event_type if isinstance(event_type, str) else "unknown",
                             workflow, event.get("call_id"), occurred.isoformat() if occurred else None,
                             token, json.dumps(payload, sort_keys=True), sequence))
                sequence += 1
            _relink_all(con)


def ingest_events(db: Database, protector: PiiProtector, drop: Path) -> None:
    root = drop / "events"
    if not root.exists():
        return
    for path in sorted(root.glob("*.jsonl")):
        ingest_event_file(db, protector, path)
