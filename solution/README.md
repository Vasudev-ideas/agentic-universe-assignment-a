# Assignment A solution

This is a Python 3.11+ SQLite pipeline for Assignment A. Run it from the repository root:

```powershell
python -m solution.main generate-keys
# Copy the generated values into the current session; never commit or document them.
$env:AU_PII_KEY = "<copy the generated AU_PII_KEY value>"
$env:AU_LOOKUP_KEY = "<copy the generated AU_LOOKUP_KEY value>"
python -m solution.main ingest data/drop_1
python -m solution.main ingest data/drop_2
python -m solution.main report --out reports
pytest -q
```

`ingest` also accepts the parent `data` directory and processes `drop_*` directories in lexical
order. The default database is `solution/ledger.sqlite3`; use `--database` or `AU_DATABASE` to
change it. Generated data, databases, reports, and keys must not be committed.

## Design

SQLite stores schemas, dispositions, leads, rejection rows, an append-only event ledger, processed
file identities, and quality counters. PII fields are AES-GCM encrypted. Phone matching uses a
keyed HMAC-SHA256 token; plaintext phones and names are not stored in indexes, rejections, events, or
logs. Phones are decrypted only to produce the required masked report value.

Orphan events remain in the ledger with a null lead id. Lead insertion relinks matching phone
events and phone-less overrides through their call id. Reports derive calls and dispositions from
the ledger rather than maintaining mutable call state.

## Explicit implementation decisions

- Duplicate normalized headers, duplicate schema mappings, ambiguous aliases, empty/unreadable
  files, and files with no recognizable headers are recorded as file errors and skipped safely.
- The first non-malformed occurrence of an event id wins; later occurrences are counted as duplicate
  events, even if their payload differs.
- Equal timestamps are ordered by ingestion sequence. Missing timestamps are retained but sort
  before valid timestamps; generated inputs always have valid timestamps.
- Unknown event types are retained in the append-only ledger but have no report effect or extra
  quality counter.
- Disposition validation follows the specified order: malformed, duplicate, orphan/linking, then
  unknown disposition. Unknown disposition events are counted and excluded from effective
  dispositions. If both orphan and unknown, both applicable counters are reflected.
- Defaults are materialized after each workflow's files are merged. Later explicit non-null duplicate
  values still overwrite a default, while later null/invalid values do not.
- Reports are generated in memory and each file is atomically replaced only after successful write.
- SQLite is used for portability. Environment variables provide both 32-byte secrets; no fallback
  or hardcoded secret exists.
