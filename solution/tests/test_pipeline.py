import json
from pathlib import Path

from solution.config import PiiProtector
from solution.database import Database
from solution.event_ingestion import ingest_event_file
from solution.lead_ingestion import ingest_lead_file
from solution.reporting import generate_reports
from solution.schema_loader import load_schemas


def test_late_orphan_relinks_and_reports(tmp_path):
    protector = PiiProtector(b"x" * 32, b"y" * 32)
    db = Database(tmp_path / "ledger.sqlite3")
    db.initialize()
    schemas = load_schemas(Path("assignment-a-data"), db)
    event_file = tmp_path / "events.jsonl"
    event_file.write_text(json.dumps({"event_id": "e1", "event_type": "call.initiated",
        "workflow_id": "motor_renewal", "call_id": "c1", "occurred_at": "2026-10-01 10:00:00",
        "phone": "9876543210", "payload": {}}) + "\n", encoding="utf-8")
    ingest_event_file(db, protector, event_file)
    lead_file = tmp_path / "batch_001.csv"
    lead_file.write_text("customer_name,phone,policy_number\nA,9876543210,P1\n", encoding="utf-8")
    ingest_lead_file(db, protector, lead_file, schemas["motor_renewal"])
    out = tmp_path / "reports"
    generate_reports(db, protector, out)
    quality = json.loads((out / "data_quality.json").read_text())
    assert quality["orphan_events"] == 0
    assert b"9876543210" not in db.path.read_bytes()
    assert "in_progress_dialed" in (out / "lead_report.csv").read_text()
