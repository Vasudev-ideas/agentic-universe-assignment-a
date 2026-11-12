from __future__ import annotations

import base64
import csv
import json
from pathlib import Path

from .config import PiiProtector
from .database import Database
from .normalization import normalize_header, normalize_phone, parse_date, parse_decimal
from .schema_loader import Field, WorkflowSchema


def _field_value(field: Field, raw: str) -> tuple[object | None, bool, str | None]:
    value = raw.strip()
    if not value:
        return None, False, None
    if field.type == "string":
        return value, True, None
    if field.type == "phone":
        parsed = normalize_phone(value)
        return parsed, parsed is not None, "invalid_phone" if parsed is None else None
    if field.type == "date":
        parsed = parse_date(value)
        return parsed, parsed is not None, None
    if field.type == "decimal":
        parsed = parse_decimal(value)
        return parsed, parsed is not None, None
    if field.type == "enum":
        lowered = value.lower()
        return lowered, lowered in {x.lower() for x in field.values}, None
    return None, False, None


def _header_mapping(headers: list[str], schema: WorkflowSchema) -> dict[str, int]:
    aliases: dict[str, list[str]] = {}
    for field in schema.fields:
        for candidate in (field.name, *field.aliases):
            aliases.setdefault(normalize_header(candidate), []).append(field.name)
    seen: set[str] = set()
    result: dict[str, int] = {}
    for index, header in enumerate(headers):
        normalized = normalize_header(header)
        if normalized in seen:
            raise ValueError(f"duplicate normalized header: {normalized}")
        seen.add(normalized)
        matches = aliases.get(normalized, [])
        unique = sorted(set(matches))
        if len(unique) > 1:
            raise ValueError(f"ambiguous header alias: {normalized}")
        if unique:
            if unique[0] in result:
                raise ValueError(f"duplicate schema field header: {unique[0]}")
            result[unique[0]] = index
    if not result:
        raise ValueError("missing recognizable headers")
    return result


def _encoded(protector: PiiProtector, value: str) -> str:
    return base64.b64encode(protector.encrypt(value)).decode("ascii")


def ingest_lead_file(db: Database, protector: PiiProtector, path: Path,
                     schema: WorkflowSchema) -> None:
    path_key = str(path.resolve())
    with db.connect() as con:
        if not db.mark_file(con, path_key, "lead"):
            return
        try:
            with path.open(newline="", encoding="utf-8-sig") as fh:
                reader = csv.reader(fh)
                headers = next(reader, None)
                if not headers:
                    raise ValueError("missing header row")
                mapping = _header_mapping(headers, schema)
                rows = list(reader)
        except (OSError, UnicodeError, csv.Error, ValueError) as exc:
            con.execute("INSERT OR REPLACE INTO file_errors VALUES(?,?,?)", (path_key, "lead", str(exc)))
            return

        batch_id = path.stem
        nonblank_number = 0
        for raw_row in rows:
            if not raw_row or all(not cell.strip() for cell in raw_row):
                continue
            nonblank_number += 1
            Database.increment(con, "lead_rows_total")
            row_number = nonblank_number
            values: dict[str, object | None] = {}
            rejected: str | None = None
            warnings = 0
            for field in schema.fields:
                raw = raw_row[mapping[field.name]] if field.name in mapping and mapping[field.name] < len(raw_row) else ""
                parsed, valid, special = _field_value(field, raw)
                if not valid and field.required:
                    rejected = special if special == "invalid_phone" else f"missing_required:{field.name}"
                    break
                if not valid:
                    parsed = None
                    if raw.strip():
                        warnings += 1
                values[field.name] = parsed
            if rejected:
                Database.increment(con, "lead_rows_rejected")
                con.execute("INSERT OR IGNORE INTO rejections(workflow_id,batch_id,row_number,reason) VALUES(?,?,?,?)",
                            (schema.workflow_id, batch_id, row_number, rejected))
                continue
            Database.increment(con, "field_warnings", warnings)
            phone = values["phone"]
            assert isinstance(phone, str)
            token = protector.token(phone)
            cur = con.execute("SELECT * FROM leads WHERE workflow_id=? AND phone_token=?",
                              (schema.workflow_id, token))
            existing = cur.fetchone()
            stored: dict[str, object | None] = {}
            for field in schema.fields:
                value = values.get(field.name)
                if field.name == "phone":
                    continue
                if field.pii and value is not None:
                    stored[field.name] = {"encrypted": _encoded(protector, str(value))}
                else:
                    stored[field.name] = value
            if existing:
                Database.increment(con, "duplicate_lead_rows_merged")
                merged = json.loads(existing["fields_json"])
                for key, value in stored.items():
                    if value is not None:
                        merged[key] = value
                con.execute("UPDATE leads SET fields_json=? WHERE lead_id=?",
                            (json.dumps(merged, sort_keys=True), existing["lead_id"]))
                reference_value = values.get(schema.reference.name)
                if reference_value is not None:
                    con.execute("UPDATE leads SET external_ref=? WHERE lead_id=?",
                                (str(reference_value), existing["lead_id"]))
            else:
                external = str(values[schema.reference.name])
                con.execute("INSERT INTO leads(workflow_id,batch_id,phone_token,phone_cipher,external_ref,fields_json,created_seq) VALUES(?,?,?,?,?,?,?)",
                            (schema.workflow_id, batch_id, token, protector.encrypt(phone), external,
                             json.dumps(stored, sort_keys=True), 0))
                lead_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
                con.execute("UPDATE events SET lead_id=? WHERE workflow_id=? AND phone_token=? AND lead_id IS NULL",
                            (lead_id, schema.workflow_id, token))
                con.execute("""UPDATE events SET lead_id=? WHERE workflow_id=? AND call_id IN
                    (SELECT call_id FROM events WHERE lead_id=? AND call_id IS NOT NULL) AND lead_id IS NULL""",
                            (lead_id, schema.workflow_id, lead_id))


def _apply_defaults(db: Database, schema: WorkflowSchema) -> None:
    defaults = {field.name: field.default for field in schema.fields if field.default is not None}
    if not defaults:
        return
    with db.connect() as con:
        for row in con.execute("SELECT lead_id,fields_json FROM leads WHERE workflow_id=?", (schema.workflow_id,)):
            fields = json.loads(row["fields_json"])
            changed = False
            for name, default in defaults.items():
                if fields.get(name) is None:
                    fields[name] = default
                    changed = True
            if changed:
                con.execute("UPDATE leads SET fields_json=? WHERE lead_id=?",
                            (json.dumps(fields, sort_keys=True), row["lead_id"]))


def ingest_leads(db: Database, protector: PiiProtector, drop: Path,
                 schemas: dict[str, WorkflowSchema]) -> None:
    root = drop / "leads"
    if not root.exists():
        return
    for workflow_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        schema = schemas.get(workflow_dir.name)
        if schema:
            for path in sorted(workflow_dir.glob("*.csv")):
                ingest_lead_file(db, protector, path, schema)
            _apply_defaults(db, schema)
