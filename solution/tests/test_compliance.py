import csv
import json
from pathlib import Path

import pytest

from solution.aggregation import lead_bucket
from solution.config import PiiProtector
from solution.database import Database
from solution.event_ingestion import ingest_event_file
from solution.lead_ingestion import _header_mapping, ingest_lead_file, ingest_leads
from solution.normalization import normalize_phone, parse_date, parse_decimal, parse_timestamp
from solution.reporting import generate_reports
from solution.schema_loader import Field, WorkflowSchema, load_schemas


ROOT = Path(__file__).parents[2]


@pytest.fixture
def context(tmp_path):
    db = Database(tmp_path / "ledger.sqlite3")
    db.initialize()
    protector = PiiProtector(b"p" * 32, b"l" * 32)
    schemas = load_schemas(ROOT / "assignment-a-data", db)
    return tmp_path, db, protector, schemas


def write_lead(path: Path, rows: list[dict], headers=None):
    headers = headers or list(rows[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def write_events(path: Path, events: list[object]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(event if isinstance(event, str) else json.dumps(event))
            fh.write("\n")


def motor_row(phone="9876543210", ref="P1", language=""):
    return {"customer_name": "Synthetic", "phone": phone, "policy_number": ref,
            "policy_expiry": "01-Dec-2026", "premium_amount": "₹1,234.5",
            "preferred_language": language}


def test_all_phone_forms_and_invalid_values():
    valid = ["9876543210", "+91 98765 43210", "09876543210", "91-98765-43210",
             "(+91) 9876543210", "+919876543210"]
    assert {normalize_phone(value) for value in valid} == {"+919876543210"}
    for invalid in ("987654321", "5" + "987654321", "022-2000-1000", "N/A", "+1 415 555 0134"):
        assert normalize_phone(invalid) is None


def test_dates_decimals_and_enum_normalization():
    assert [parse_date(x) for x in ("2026-12-01", "01/12/2026", "01-12-2026", "01-dec-2026")] == ["2026-12-01"] * 4
    assert parse_date("31/02/2026") is None
    assert parse_decimal("₹1,234.5") == "1234.50"
    assert parse_decimal("â‚¹1,234.5") == "1234.50"
    assert parse_decimal("INR 10") == "10.00"
    assert parse_decimal("-0.01") is None
    field = Field("language", "enum", values=("en", "hi"))
    from solution.lead_ingestion import _field_value
    assert _field_value(field, " HI ") == ("hi", True, None)
    assert _field_value(field, "Hindi")[1] is False


def test_header_aliases_duplicates_and_ambiguity():
    schema = WorkflowSchema("w", (Field("phone", "phone", aliases=("mobile",)), Field("name", "string", aliases=("person",))))
    assert _header_mapping([" Mobile ", "person", "extra"], schema) == {"phone": 0, "name": 1}
    with pytest.raises(ValueError, match="duplicate normalized header"):
        _header_mapping(["phone", "PHONE"], schema)
    ambiguous = WorkflowSchema("w", (Field("a", "string", aliases=("same",)), Field("b", "string", aliases=("same",))))
    with pytest.raises(ValueError, match="ambiguous"):
        _header_mapping(["same"], ambiguous)


def test_rejections_warnings_duplicate_merge_and_defaults(context):
    tmp_path, db, protector, schemas = context
    drop = tmp_path / "drop_1" / "leads" / "motor_renewal"
    write_lead(drop / "batch_001.csv", [motor_row(language=""), motor_row(phone="987654321", ref="BAD"),
        {**motor_row(ref="MISSING"), "customer_name": ""}])
    write_lead(drop / "batch_002.csv", [motor_row(ref="P2", language="hi")])
    ingest_leads(db, protector, tmp_path / "drop_1", schemas)
    with db.connect() as con:
        lead = con.execute("SELECT * FROM leads").fetchone()
        fields = json.loads(lead["fields_json"])
        quality = {r["key"]: r["value"] for r in con.execute("SELECT * FROM quality")}
        assert lead["batch_id"] == "batch_001"
        assert lead["external_ref"] == "P2"
        assert fields["preferred_language"] == "hi"
        assert quality["duplicate_lead_rows_merged"] == 1
        assert quality["lead_rows_rejected"] == 2
        assert quality["field_warnings"] == 0


def test_optional_invalid_value_counts_warning(context):
    tmp_path, db, protector, schemas = context
    path = tmp_path / "batch.csv"
    row = {**motor_row(), "policy_expiry": "not-a-date", "premium_amount": "-5", "preferred_language": "Hindi"}
    write_lead(path, [row])
    ingest_lead_file(db, protector, path, schemas["motor_renewal"])
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 1
        assert con.execute("SELECT value FROM quality WHERE key='field_warnings'").fetchone()[0] == 3


def test_event_malformed_duplicate_unknown_and_equal_timestamp(context):
    tmp_path, db, protector, schemas = context
    lead_path = tmp_path / "drop_1" / "leads" / "motor_renewal" / "batch_001.csv"
    write_lead(lead_path, [motor_row()])
    ingest_lead_file(db, protector, lead_path, schemas["motor_renewal"])
    first = {"event_id": "e1", "event_type": "call.initiated", "workflow_id": "motor_renewal",
             "call_id": "c1", "occurred_at": "2026-10-01 10:00:00", "phone": "9876543210", "payload": {}}
    unknown = {"event_id": "e2", "event_type": "disposition.set", "workflow_id": "motor_renewal",
               "call_id": "c1", "occurred_at": "2026-10-01 10:00:00", "phone": "9876543210",
               "payload": {"disposition_code": "not_real"}}
    path1 = tmp_path / "drop_1" / "events" / "events_0001.jsonl"
    path2 = tmp_path / "drop_2" / "events" / "events_1001.jsonl"
    write_events(path1, [first, unknown, "{not json", {"event_type": "call.connected"}])
    write_events(path2, [{**first, "payload": {"changed": True}}])
    ingest_event_file(db, protector, path1)
    ingest_event_file(db, protector, path2)
    with db.connect() as con:
        quality = {r["key"]: r["value"] for r in con.execute("SELECT * FROM quality")}
        assert quality["events_total"] == 5
        assert quality["malformed_events"] == 2
        assert quality["duplicate_events_dropped"] == 1
        assert quality["unknown_disposition_events"] == 1
        assert con.execute("SELECT COUNT(*) FROM events WHERE event_id='e1'").fetchone()[0] == 1
        assert con.execute("SELECT lead_id FROM events WHERE event_id='e1'").fetchone()[0] is not None


def test_orphan_override_relinks_before_or_after_call(context):
    tmp_path, db, protector, schemas = context
    lead_path = tmp_path / "lead.csv"
    write_lead(lead_path, [motor_row()])
    ingest_lead_file(db, protector, lead_path, schemas["motor_renewal"])
    override = {"event_id": "o1", "event_type": "disposition.override", "workflow_id": "motor_renewal",
                "call_id": "c1", "occurred_at": "2026-10-01T10:05:00+05:30",
                "payload": {"disposition_code": "renewed"}}
    initiated = {"event_id": "i1", "event_type": "call.initiated", "workflow_id": "motor_renewal",
                 "call_id": "c1", "occurred_at": "2026-10-01T10:00:00Z", "phone": "9876543210", "payload": {}}
    path = tmp_path / "events.jsonl"
    write_events(path, [override, initiated])
    ingest_event_file(db, protector, path)
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM events WHERE lead_id IS NULL").fetchone()[0] == 0


def test_override_is_effective_and_original_remains(context):
    tmp_path, db, protector, schemas = context
    lead_path = tmp_path / "lead.csv"
    write_lead(lead_path, [motor_row()])
    ingest_lead_file(db, protector, lead_path, schemas["motor_renewal"])
    events = [
        {"event_id": "i", "event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "c", "occurred_at": "2026-10-01T10:00:00+05:30", "phone": "9876543210", "payload": {}},
        {"event_id": "s", "event_type": "disposition.set", "workflow_id": "motor_renewal", "call_id": "c", "occurred_at": "2026-10-01T10:01:00+05:30", "phone": "9876543210", "payload": {"disposition_code": "payment_link_sent"}},
        {"event_id": "o", "event_type": "disposition.override", "workflow_id": "motor_renewal", "call_id": "c", "occurred_at": "2026-10-01T10:02:00+05:30", "payload": {"disposition_code": "renewed"}},
    ]
    path = tmp_path / "events.jsonl"
    write_events(path, events)
    ingest_event_file(db, protector, path)
    out = tmp_path / "reports"
    generate_reports(db, protector, out)
    row = (out / "lead_report.csv").read_text().splitlines()[1]
    assert ",renewed,true" in row
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3


def test_bucket_priority_and_timestamp():
    dmap = {("w", "terminal"): (True, False), ("w", "success"): (False, True)}
    assert lead_bucket([], dmap) == "yet_to_reach_out"
    assert lead_bucket([{"workflow_id": "w", "disposition": "terminal", "telephony_failed": True}], dmap) == "completed"
    assert lead_bucket([{"workflow_id": "w", "disposition": None, "telephony_failed": True}], dmap) == "in_progress_telephony_failed"
    assert lead_bucket([{"workflow_id": "w", "disposition": None, "telephony_failed": False}], dmap) == "in_progress_dialed"
    assert parse_timestamp("2026-10-01 10:00:00").isoformat() == "2026-10-01T10:00:00+05:30"
    assert parse_timestamp("2026-10-01T04:30:00Z").isoformat() == "2026-10-01T10:00:00+05:30"


def test_reports_columns_daily_success_and_pii(context):
    tmp_path, db, protector, schemas = context
    lead_path = tmp_path / "lead.csv"
    write_lead(lead_path, [motor_row()])
    ingest_lead_file(db, protector, lead_path, schemas["motor_renewal"])
    events = [
        {"event_id": "i", "event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "c", "occurred_at": "2026-10-01T04:30:00Z", "phone": "9876543210", "payload": {}},
        {"event_id": "c", "event_type": "call.connected", "workflow_id": "motor_renewal", "call_id": "c", "occurred_at": "2026-10-01 10:01:00", "phone": "9876543210", "payload": {}},
        {"event_id": "d", "event_type": "disposition.set", "workflow_id": "motor_renewal", "call_id": "c", "occurred_at": "2026-10-01 10:02:00", "phone": "9876543210", "payload": {"disposition_code": "renewed"}},
    ]
    event_path = tmp_path / "events.jsonl"
    write_events(event_path, events)
    ingest_event_file(db, protector, event_path)
    out = tmp_path / "reports"
    generate_reports(db, protector, out)
    assert (out / "daily_overview.csv").read_text().splitlines()[1].endswith(",1.0000,1")
    expected = {
        "batch_summary.csv": "workflow_id,batch_id,total_leads,yet_to_reach_out,in_progress_telephony_failed,in_progress_dialed,completed",
        "daily_overview.csv": "workflow_id,date,total_calls,connected_calls,connectivity_rate,success_leads",
        "lead_report.csv": "workflow_id,external_ref,batch_id,phone_masked,bucket,total_calls,connected_calls,last_call_at,current_disposition,ever_success",
        "rejections.csv": "workflow_id,batch_id,row_number,reason",
    }
    for name, header in expected.items():
        assert (out / name).read_text().splitlines()[0] == header
    assert "9876543210" not in db.path.read_bytes().decode("utf-8", errors="ignore")
    assert "9876543210" not in (out / "lead_report.csv").read_text()
    assert protector.token("+919876543210") != protector.token("+916123456789")
