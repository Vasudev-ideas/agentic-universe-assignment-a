from __future__ import annotations

import csv
import json
import os
import tempfile
from collections import defaultdict
from pathlib import Path

from .aggregation import as_ist, calls_for_lead, disposition_map, lead_bucket
from .config import PiiProtector
from .database import Database


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        os.replace(temp, path)
    except Exception:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def _csv(path: Path, headers: list[str], rows: list[dict]) -> None:
    import io
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    _atomic_text(path, buffer.getvalue())


def generate_reports(db: Database, protector: PiiProtector, out: str | Path) -> None:
    out = Path(out)
    with db.connect() as con:
        dmap = disposition_map(con)
        leads = con.execute("SELECT * FROM leads ORDER BY workflow_id, external_ref, lead_id").fetchall()
        events_by_lead = defaultdict(list)
        for event in con.execute("SELECT * FROM events WHERE lead_id IS NOT NULL ORDER BY lead_id, ingestion_seq"):
            events_by_lead[event["lead_id"]].append(event)
        by_batch = defaultdict(lambda: defaultdict(int))
        daily = defaultdict(lambda: {"total": 0, "connected": 0, "success": set()})
        lead_rows = []
        for lead in leads:
            calls = calls_for_lead(con, lead["lead_id"], events_by_lead.get(lead["lead_id"], ()), dmap)
            bucket = lead_bucket(calls, dmap)
            by_batch[(lead["workflow_id"], lead["batch_id"])][bucket] += 1
            latest = sorted(calls, key=lambda c: (c["initiated"] or "", c["sequence"]))
            current = next((c["disposition"] for c in reversed(latest) if c["disposition"]), "")
            ever_success = False
            for call in calls:
                key = (call["workflow_id"], call["disposition"])
                success = bool(call["disposition"] and dmap.get(key, (False, False))[1])
                ever_success = ever_success or success
                if call["initiated"]:
                    day = call["initiated"][:10]
                    daily[(call["workflow_id"], day)]["total"] += 1
                    daily[(call["workflow_id"], day)]["connected"] += int(call["connected"])
                    if success:
                        daily[(call["workflow_id"], day)]["success"].add(lead["lead_id"])
            phone = protector.decrypt(lead["phone_cipher"])
            fields = json.loads(lead["fields_json"])
            last = latest[-1]["initiated"] if latest else None
            lead_rows.append({
                "workflow_id": lead["workflow_id"], "external_ref": lead["external_ref"],
                "batch_id": lead["batch_id"], "phone_masked": "+91******" + phone[-4:],
                "bucket": bucket, "total_calls": len(calls),
                "connected_calls": sum(int(c["connected"]) for c in calls),
                "last_call_at": as_ist(last) or "", "current_disposition": current,
                "ever_success": "true" if ever_success else "false",
            })
        batch_rows = []
        for (workflow, batch), counts in sorted(by_batch.items()):
            batch_rows.append({"workflow_id": workflow, "batch_id": batch, "total_leads": sum(counts.values()),
                "yet_to_reach_out": counts["yet_to_reach_out"], "in_progress_telephony_failed": counts["in_progress_telephony_failed"],
                "in_progress_dialed": counts["in_progress_dialed"], "completed": counts["completed"]})
        daily_rows = [{"workflow_id": workflow, "date": day, "total_calls": data["total"],
            "connected_calls": data["connected"], "connectivity_rate": f"{data['connected'] / data['total']:.4f}",
            "success_leads": len(data["success"])} for (workflow, day), data in sorted(daily.items())]
        rejection_rows = [dict(row) for row in con.execute(
            "SELECT workflow_id,batch_id,row_number,reason FROM rejections ORDER BY workflow_id,batch_id,row_number")]
        quality = {row["key"]: row["value"] for row in con.execute("SELECT key,value FROM quality")}
        quality["orphan_events"] = con.execute("SELECT COUNT(*) FROM events WHERE lead_id IS NULL").fetchone()[0]
        reasons = defaultdict(int)
        for row in rejection_rows:
            reasons[row["reason"]] += 1
        quality["rejections_by_reason"] = dict(sorted(reasons.items()))
        quality = {key: quality[key] for key in ("lead_rows_total", "lead_rows_rejected", "rejections_by_reason",
            "duplicate_lead_rows_merged", "field_warnings", "events_total", "malformed_events",
            "duplicate_events_dropped", "orphan_events", "unknown_disposition_events")}
        _csv(out / "batch_summary.csv", list(batch_rows[0]) if batch_rows else ["workflow_id", "batch_id", "total_leads", "yet_to_reach_out", "in_progress_telephony_failed", "in_progress_dialed", "completed"], batch_rows)
        _csv(out / "daily_overview.csv", ["workflow_id", "date", "total_calls", "connected_calls", "connectivity_rate", "success_leads"], daily_rows)
        _csv(out / "lead_report.csv", ["workflow_id", "external_ref", "batch_id", "phone_masked", "bucket", "total_calls", "connected_calls", "last_call_at", "current_disposition", "ever_success"], sorted(lead_rows, key=lambda x: (x["workflow_id"], x["external_ref"])))
        _csv(out / "rejections.csv", ["workflow_id", "batch_id", "row_number", "reason"], rejection_rows)
        _atomic_text(out / "data_quality.json", json.dumps(quality, indent=2) + "\n")
