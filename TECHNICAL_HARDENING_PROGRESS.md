# Technical Hardening Progress

- Branch: `codex/technical-hardening`
- Reviewed baseline: `70d3da7162b63864e479e9a1998aa138046c2433`
- Execution rule: one numbered task per commit; Phase 3 is excluded.
- Safety baseline: Gmail/manual intake, employee review, blank selling prices,
  suggestion-only matching, row evidence, preview-before-send, verified reply
  headers, send idempotency, ambiguous-send lockout, and reconciliation-only
  behavior must remain intact.

## Phase 1

### 1.1 — Production-equivalent PostgreSQL concurrency tests

- Status: completed as a characterization checkpoint.
- Files changed:
  - `.github/workflows/ci.yml`
  - `backend/quotations/test_quotation_concurrency.py`
  - `TECHNICAL_HARDENING_PROGRESS.md`
- Implementation:
  - Retained the existing SQLite backend job.
  - Added an isolated PostgreSQL 17.10 CI job with an explicit PostgreSQL 17
    and `READ COMMITTED` preflight.
  - Added deterministic, separate-connection tests for quotation PATCH locks,
    quotation-line DELETE locks, concurrent double-send, and concurrent
    provider success/late-failure handling.
  - Added the existing product-alias concurrency suite to the PostgreSQL lane
    as a row-lock canary.
- Tests run:
  - SQLite discovery/skip check:
    `python manage.py test quotations.test_quotation_concurrency quotations.test_product_matching_rework.ProductAliasConcurrencyTests --noinput --verbosity 2`
    — passed; 9 PostgreSQL-only tests skipped as intended.
  - Local isolated PostgreSQL 17 / `READ COMMITTED`:
    `python manage.py test quotations.test_product_matching_rework.ProductAliasConcurrencyTests quotations.test_quotation_concurrency --noinput --verbosity 2`
    — passed; 6 tests passed and 3 expected-failure characterizations recorded.
- Migrations: none.
- API changes: none.
- Frontend changes: none.
- Accuracy/security impact: test-only; no production behavior or safety
  invariant changed.
- Remaining risks proven by this checkpoint:
  - quotation PATCH does not hold the quotation lock while saving;
  - quotation-line DELETE does not hold the quotation lock while deleting;
  - PostgreSQL rejects the persisted email-preview lock query because its
    unrestricted `FOR UPDATE` includes nullable outer joins.
  These three expected failures must be removed by task 1.2 before advancing
  beyond quotation mutation hardening.
- Rollback: revert the task 1.1 commit. This removes only the new CI lane,
  tests, and this progress entry.

## Phase 2

Not started.

## Phase 3

Intentionally not implemented.
