from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime

from .normalization import IST


def disposition_map(con) -> dict[tuple[str, str], tuple[bool, bool]]:
    return {(r["workflow_id"], r["code"]): (bool(r["is_terminal"]), bool(r["is_success"]))
            for r in con.execute("SELECT * FROM dispositions")}


def calls_for_lead(con, lead_id: int, rows=None, dmap=None) -> list[dict]:
    rows = list(rows) if rows is not None else con.execute(
        "SELECT * FROM events WHERE lead_id=? ORDER BY ingestion_seq", (lead_id,)).fetchall()
    calls: dict[str, dict] = {}
    for row in rows:
        call_id = row["call_id"]
        if not call_id:
            continue
        call = calls.setdefault(call_id, {"events": [], "sequence": row["ingestion_seq"]})
        call["events"].append(row)
    result = []
    for call_id, call in calls.items():
        events = call["events"]
        initiated = [e for e in events if e["event_type"] == "call.initiated"]
        if not initiated:
            continue
        initiated.sort(key=lambda e: (e["occurred_at"] or "", e["ingestion_seq"]))
        dmap = dmap or disposition_map(con)
        sets = [e for e in events if e["event_type"] == "disposition.set" and
                (json.loads(e["payload_json"]).get("disposition_code") in
                 {code for workflow, code in dmap if workflow == events[0]["workflow_id"]})]
        overrides = [e for e in events if e["event_type"] == "disposition.override" and
                     (json.loads(e["payload_json"]).get("disposition_code") in
                      {code for workflow, code in dmap if workflow == events[0]["workflow_id"]})]
        chosen = overrides if overrides else sets
        chosen = sorted(chosen, key=lambda e: (e["occurred_at"] or "", e["ingestion_seq"]))
        code = None
        if chosen:
            code = json.loads(chosen[-1]["payload_json"]).get("disposition_code")
        result.append({
            "call_id": call_id,
            "initiated": initiated[-1]["occurred_at"],
            "connected": any(e["event_type"] == "call.connected" for e in events),
            "telephony_failed": any(e["event_type"] == "call.telephony_failed" for e in events),
            "disposition": code,
            "sequence": initiated[-1]["ingestion_seq"],
            "workflow_id": events[0]["workflow_id"],
        })
    return result


def lead_bucket(calls: list[dict], dmap: dict[tuple[str, str], tuple[bool, bool]]) -> str:
    if not calls:
        return "yet_to_reach_out"
    if any(c["disposition"] and dmap.get((c["workflow_id"], c["disposition"]), (False, False))[0] for c in calls):
        return "completed"
    if all(c["telephony_failed"] for c in calls):
        return "in_progress_telephony_failed"
    return "in_progress_dialed"


def as_ist(value: str | None) -> str | None:
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(IST).isoformat(timespec="seconds")
