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
- Commit: `01d47c2` (`test: add PostgreSQL quotation concurrency coverage`).
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

### 1.2 — Fix quotation mutation races proven by PostgreSQL tests

- Status: completed; all task 1.1 expected failures are now active passing
  tests.
- Files changed:
  - `backend/quotations/views.py`
  - `backend/quotations/quotation_email_delivery.py`
  - `backend/quotations/test_quotation_concurrency.py`
  - `TECHNICAL_HARDENING_PROGRESS.md`
- Implementation:
  - Quotation PATCH now opens an atomic transaction, locks and reloads the
    quotation before DRF validation, rechecks editability, and saves the
    locked instance. Concurrent company/contact changes are therefore
    validated against the latest locked state.
  - Quotation-line DELETE now follows the global quotation→line lock order,
    rechecks editability under the quotation lock, and recalculates totals in
    the same transaction.
  - A second concurrent line DELETE now receives the normal not-found response
    if the first request removes the row while it waits, instead of a 500.
  - PATCH also returns a normal not-found response if a concurrent quotation
    DELETE commits between its permission lookup and lock acquisition.
  - Email-preview/send locks are restricted to their base tables when nullable
    relations are joined. This avoids PostgreSQL's invalid `FOR UPDATE` over a
    nullable outer join without reducing the intended lock coverage.
- Tests run:
  - Local isolated PostgreSQL 17 / `READ COMMITTED`:
    `python manage.py test quotations.test_product_matching_rework.ProductAliasConcurrencyTests quotations.test_quotation_concurrency --noinput --verbosity 2`
    — 12/12 passed; no skips or expected failures.
  - SQLite quotation regressions:
    `python manage.py test quotations.tests.QuotationPermissionTests quotations.tests.QuotationWorkflowTests quotations.test_quotation_email_delivery quotations.test_product_matching_rework.ProductMatchingReworkTests --noinput --verbosity 1`
    — 154/154 passed.
  - `python manage.py makemigrations --check --dry-run` — no changes detected.
  - `python manage.py check` — no issues.
- Migrations: none.
- API changes: no request/response schema change. Concurrent edits/deletes now
  serialize, and PostgreSQL email send no longer raises a nullable-join 500.
- Frontend changes: none.
- Accuracy/security impact: prevents stale mutation from bypassing finalized
  quotation immutability; preserves blank prices, review, evidence, verified
  reply, idempotency, and reconciliation guarantees.
- Remaining risks: waits still inherit database timeout configuration; task
  2.8 will make those bounds explicit. No race proven by task 1.1 remains.
- Rollback: revert the task 1.2 commit. No database rollback is required.

## Phase 2

Not started.

## Phase 3

Intentionally not implemented.
