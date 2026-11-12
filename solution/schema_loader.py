from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

from .database import Database


@dataclass(frozen=True)
class Field:
    name: str
    type: str
    required: bool = False
    pii: bool = False
    reference: bool = False
    aliases: tuple[str, ...] = ()
    values: tuple[str, ...] = ()
    default: str | None = None


@dataclass(frozen=True)
class WorkflowSchema:
    workflow_id: str
    fields: tuple[Field, ...]

    @property
    def reference(self) -> Field:
        return next(field for field in self.fields if field.reference)


def load_schemas(directory: str | Path, db: Database) -> dict[str, WorkflowSchema]:
    root = Path(directory)
    result: dict[str, WorkflowSchema] = {}
    with db.connect() as con:
        for path in sorted((root / "schemas").glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            fields = tuple(Field(
                name=f["name"], type=f["type"], required=f.get("required", False),
                pii=f.get("pii", False), reference=f.get("reference", False),
                aliases=tuple(f.get("aliases", ())), values=tuple(f.get("values", ())),
                default=f.get("default"),
            ) for f in raw["fields"])
            schema = WorkflowSchema(raw["workflow_id"], fields)
            result[schema.workflow_id] = schema
            con.execute("INSERT OR REPLACE INTO workflows(workflow_id,schema_json) VALUES(?,?)",
                        (schema.workflow_id, json.dumps(raw, sort_keys=True)))
        with (root / "schemas" / "dispositions.csv").open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                con.execute("INSERT OR REPLACE INTO dispositions VALUES(?,?,?,?,?)", (
                    row["workflow_id"], row["code"], row["label"],
                    int(row["is_terminal"].lower() == "true"), int(row["is_success"].lower() == "true"),
                ))
    return result
