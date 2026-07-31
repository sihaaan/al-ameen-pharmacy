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
- Commit: `70932b6` (`fix: serialize quotation mutations on PostgreSQL`).
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

### 1.3 — Strengthen Gmail ambiguous-send reconciliation

- Status: completed.
- Files changed:
  - `backend/quotations/quotation_email_delivery.py`
  - `backend/quotations/test_quotation_email_delivery.py`
  - `TECHNICAL_HARDENING_PROGRESS.md`
- Implementation:
  - Gmail reconciliation searches the Sent mailbox by the stable outbound RFC
    Message-ID and the exact connected shared-mailbox address.
  - A candidate is accepted only when its fetched metadata independently
    confirms the `SENT` label, exactly one `From` address equal to the connected
    shared mailbox, the exact outbound RFC Message-ID, and a non-empty Gmail
    thread ID.
  - Gmail replies additionally require the exact stored source thread. New
    messages require the recorded sent thread whenever one already exists; for
    an ambiguous first send, Gmail assigns the new thread ID and there is no
    pre-send thread ID to compare, so the three independent Sent/From/RFC-ID
    signals plus a non-empty returned thread are required.
  - Every candidate is inspected before accepting a result. Missing or
    malformed metadata, multiple fully verified matches, missing reconciliation
    keys, and Gmail API failures remain non-retryable ambiguous states and can
    never trigger another send.
  - A successful empty Gmail search is the only condition reported as genuine
    not-found. Search or metadata failures use a distinct error response.
- Tests run:
  - SQLite email-delivery suite:
    `python manage.py test quotations.test_quotation_email_delivery --noinput --verbosity 2`
    — 43/43 passed.
  - Local isolated PostgreSQL 17 / `READ COMMITTED`:
    `python manage.py test quotations.test_product_matching_rework.ProductAliasConcurrencyTests quotations.test_quotation_concurrency --noinput --verbosity 2`
    — 12/12 passed.
  - SQLite quotation regressions:
    `python manage.py test quotations.tests.QuotationPermissionTests quotations.tests.QuotationWorkflowTests quotations.test_quotation_email_delivery quotations.test_product_matching_rework.ProductMatchingReworkTests --noinput --verbosity 1`
    — 164/164 passed.
  - `python manage.py makemigrations --check --dry-run` — no changes detected.
  - `python manage.py check` — no issues.
  - `python -m compileall -q quotations/quotation_email_delivery.py quotations/test_quotation_email_delivery.py`
    and `git diff --check` — passed.
- Migrations: none.
- API changes: request and success schemas are unchanged. A successful Gmail
  search with no verified message still returns `200` with `reconciled=false`.
  Gmail search/metadata outages now return a distinct non-retryable `503`
  (`gmail_reconciliation_unavailable`) rather than being misreported as
  not-found. Invalid reconciliation provenance returns a non-retryable `400`,
  and multiple fully verified candidates return a non-retryable `409`.
- Frontend changes: none.
- Accuracy/security impact: materially reduces false-positive send
  reconciliation and preserves the one-successful-email, blind-retry lockout,
  verified-reply, and reconciliation-never-sends guarantees. Send-as aliases
  are intentionally not accepted because this task requires the exact
  connected shared-mailbox identity.
- Remaining risks: Gmail eventual consistency can temporarily produce a
  genuine not-found, but the delivery remains `unknown` and locked so a later
  reconciliation can be attempted safely. A new email has no independently
  known Gmail thread ID until Gmail accepts it; this limitation is explicitly
  compensated by the other independently verified signals.
- Rollback: revert the task 1.3 commit. No database rollback is required.

## Phase 2

Not started.

## Phase 3

Intentionally not implemented.
