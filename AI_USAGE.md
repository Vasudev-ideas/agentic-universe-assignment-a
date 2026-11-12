# AI Usage Disclosure

## Tools used

Codex.

## What I used them for

Repository inspection, implementation scaffolding, tests, debugging, and verification.

## What I wrote or decided myself

The database model, AES-GCM/HMAC privacy design, orphan relinking strategy, processing order,
idempotency behavior, and report aggregation decisions.

## Where the AI was wrong or unhelpful

An initial implementation treated blank required values as valid. The generated-data run exposed
the missing-phone failure, which was corrected to emit the required rejection reason.

## How I verified AI-generated code

Compilation, pytest, generated seed-42 data, exact report headers/files, idempotency checks, and
drop-by-drop versus all-at-once report comparison. The final run collected 15 tests and passed all
15; the managed sandbox produced one cache-permission warning during pytest cache writing.
