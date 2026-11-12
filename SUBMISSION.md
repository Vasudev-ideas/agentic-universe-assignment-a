# Submission

## Candidate
- **Name:** Vasu P
- **Assignment chosen:** A (Data)
- **Time spent:** 2 hours

## How to run

```powershell
python -m venv .venv
. .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

$keys = python -m solution.main generate-keys
$keys

# Copy the two printed values into this same PowerShell session; do not commit them.
$env:AU_PII_KEY = '<copy the AU_PII_KEY value printed above>'
$env:AU_LOOKUP_KEY = '<copy the AU_LOOKUP_KEY value printed above>'

python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
python -m solution.main ingest data
python -m solution.main report --out reports
python -m pytest -q
```

## Design

The implementation is under `solution/`. SQLite stores schemas, dispositions, encrypted leads, rejections, processed files, quality counters, and an append-only event ledger.

Sensitive PII fields are encrypted using AES-GCM, while phone-based identity matching uses keyed HMAC-SHA256 tokens. Encryption and lookup keys are supplied through environment variables.

Orphan events are retained and relinked when matching leads arrive. Reports derive call counts and effective dispositions from the event ledger.

## Decisions and assumptions

The complete ambiguity decisions are documented in `solution/README.md`, including file-level header errors, first-event-wins duplicate IDs, timestamp tie ordering, unknown event types, and disposition processing order.

SQLite is used for portability. Secrets are supplied only through environment variables and are not committed to the repository.

## Failure modes considered

Malformed input, duplicate files/events, late orphan relinking, invalid rows, and plaintext PII are handled.

Secret rotation and concurrent multi-process ingestion are outside this local implementation.

## Testing

Tests cover normalization, bucket priority, PII storage, late orphan relinking, and report generation.

The final local test run completed successfully: **15 passed, 0 failed, and 0 skipped**. An earlier managed-sandbox run emitted a PytestCacheWarning because of cache-directory permissions; this did not affect test execution.

The seed-42 small dataset was run end-to-end, including idempotency and split-drop equivalence.

## Results

The seed-42 small dataset produced all five required reports:

- `batch_summary.csv`
- `daily_overview.csv`
- `lead_report.csv`
- `rejections.csv`
- `data_quality.json`

Processing results:
- 2,057 lead rows
- 108 rejected rows
- 8 malformed events
- 237 duplicate events
- 462 final orphan events

The focused test suite contains 15 passing tests.

The large dataset was profiled after indexed and bulk aggregation improvements but still exceeded 12 minutes, so the optional two-minute stretch goal is not claimed.

## If I had another day

Further optimize large-dataset ingestion with carefully benchmarked bulk inserts, add stronger crash/recovery integration tests, and document production scheduling, backfills, secret rotation, and observability.