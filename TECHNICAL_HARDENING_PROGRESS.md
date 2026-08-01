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
- Commit: `1f230de` (`fix: harden Gmail send reconciliation`).
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
    — 16/16 passed.
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

### 1.4 — Add comparable privacy-safe AI intake instrumentation

- Status: completed.
- Commit: `d29a6f3` (`feat: add AI intake observability`).
- Files changed:
  - `backend/quotations/ai_parsing.py`
  - `backend/quotations/gmail_inquiry_import.py`
  - `backend/quotations/test_ai_observability.py`
  - `backend/quotations/test_gmail_inquiry_import.py`
  - `backend/quotations/test_inquiry_image_import.py`
  - `backend/quotations/tests.py`
  - `TECHNICAL_HARDENING_PROGRESS.md`
- Implementation:
  - Added one content-free observability envelope to existing `AIParseLog`
    usage JSON for manual inquiry parsing, Gmail thread parsing, and the
    mailbox-PO vision route. No new telemetry table or external service is
    required.
  - Both intake paths now record comparable source-preparation, provider,
    validation, and total timing boundaries. Existing Gmail route-stage
    timings remain available for its additional fetch, extraction, matching,
    and persistence work.
  - Cost comparison is based on allow-listed provider token counters,
    including cached and reasoning token detail when reported. Dollar cost is
    deliberately not hard-coded because model pricing changes; the existing
    provider/model columns plus the normalized token basis support auditable
    cost calculation.
  - Cache hits explicitly record that no provider call was attempted and zero
    provider-token cost. Provider usage is retained on validation failures so
    paid failed calls are not hidden.
  - Added versioned SHA-256 identities for the exact prompt, effective JSON
    schema, and pipeline contract. Prompt, schema, customer text, filenames,
    storage references, Gmail message IDs, and Gmail thread IDs are not copied
    into the new observability payload.
  - The complete contract identity, including pipeline version, is part of the
    manual AI cache key so a changed processing contract cannot be attributed
    to or served from an older cache entry.
  - Existing public/manual result usage remains unchanged; only the internal
    audit copy is numerically allow-listed.
- Tests run:
  - Focused contract, timing, privacy, cost, and cache tests:
    `python manage.py test quotations.test_ai_observability quotations.test_inquiry_image_import.InquiryImageAITests.test_in_memory_image_uses_normalized_vision_input_and_cache quotations.test_inquiry_image_import.InquiryImageAITests.test_ai_cache_key_includes_prompt_and_schema_contract quotations.test_gmail_inquiry_import.GmailInquiryImportTests.test_native_ai_call_logs_actor_usage_and_validation_failures --noinput --verbosity 2`
    — 6/6 passed across the two focused runs (one initial selector typo was
    corrected; all discovered tests passed).
  - Cross-route AI/Gmail regression suite:
    `python manage.py test quotations.test_ai_observability quotations.tests.AIImportParsingTests quotations.test_inquiry_image_import quotations.test_mailbox_po_vision quotations.test_gmail_inquiry_import --noinput --verbosity 1`
    — 186/186 passed.
  - `python manage.py makemigrations --check --dry-run` — no changes detected.
  - `python manage.py check` — no issues.
  - `python -m compileall -q quotations/ai_parsing.py quotations/gmail_inquiry_import.py quotations/test_ai_observability.py quotations/test_inquiry_image_import.py quotations/test_gmail_inquiry_import.py quotations/tests.py`
    and `git diff --check` — passed.
  - Independent follow-up review — no remaining actionable correctness,
    security, or API-compatibility issues.
- Migrations: none; instrumentation uses the existing `AIParseLog.usage` JSON
  field.
- API changes: none. Public request/response schemas and the existing provider
  usage returned with an AI parse result are unchanged.
- Frontend changes: none.
- Accuracy/security impact: extraction prompts, schemas, models, OAuth scopes,
  review gates, blank selling prices, evidence, uncertainty, matching, and
  email-send guarantees are unchanged. Audit metrics are content-free and
  external usage dictionaries are allow-listed before persistence.
- Remaining risks: route durations are application-side measurements rather
  than distributed traces; provider token counters depend on what the provider
  reports; dollar-cost conversion must use the price schedule effective at the
  recorded time; manual source-shape byte counts originate from already
  validated preview metadata and are diagnostic only. Contract hashes prove
  identity, not extraction quality. Existing bounded error text remains in the
  audit log and may contain provider-supplied diagnostic detail.
- Rollback: revert the task 1.4 commit. Existing JSON logs remain readable and
  no database rollback is required.

### 1.5 — Fix manual AI cache provenance and sensitive-content persistence

- Status: completed.
- Commit: `a6548aa` (`fix: bind AI cache hits to current uploads`).
- Files changed:
  - `backend/quotations/ai_parsing.py`
  - `backend/quotations/test_inquiry_image_import.py`
  - `backend/quotations/tests.py`
  - `TECHNICAL_HARDENING_PROGRESS.md`
- Implementation:
  - Proved that a same-content Excel cache hit returned the first upload's
    private `source_file_ref` instead of the current upload's reference.
  - Manual AI cache rows now contain only reusable AI semantic output and the
    row-level raw evidence needed for staff review. They no longer duplicate
    upload source fields, original full text, parse method, arbitrary preview
    metadata, raw provider usage, or a stored cache-hit flag.
  - Every manual text, Excel, stored-PDF, in-memory PDF, image, and historical
    cache hit now reconstructs source fields, parse method, and preview metadata
    from the current source. Server-generated AI identity and document fields
    take precedence over browser-echoed metadata.
  - Cache reads apply the same allow-list before hydration, so legacy full
    cache payloads cannot return stale paths, arbitrary private keys, or raw
    provider usage. Legacy database rows are not rewritten or deleted.
  - Browser-echoed `ai_*` outcome metadata is reserved for server values;
    only the explicit `ai_normalized_*` image-source dimensions are accepted
    from the current prepared preview.
  - Cache-hit `meta.ai_usage` is now an empty object because no provider call
    occurred. The task 1.4 audit log continues to record the cache hit with
    zero provider cost, while the originating provider call retains its own
    sanitized audit usage.
- Tests run:
  - Pre-fix characterization:
    `python manage.py test quotations.test_inquiry_image_import.InquiryImageAITests.test_text_cache_rebinds_current_upload_and_omits_source_payload --noinput --verbosity 2`
    — failed as expected because the second upload received the first private
    source reference.
  - Focused text/image/PDF cache tests:
    `python manage.py test quotations.test_inquiry_image_import.InquiryImageAITests.test_text_cache_rebinds_current_upload_and_omits_source_payload quotations.test_inquiry_image_import.InquiryImageAITests.test_in_memory_image_uses_normalized_vision_input_and_cache quotations.test_inquiry_image_import.InquiryImageAITests.test_ai_cache_key_includes_prompt_and_schema_contract quotations.test_mailbox_po_vision.InMemoryPDFVisionTests.test_in_memory_path_uses_provider_cache_and_log_without_private_storage --noinput --verbosity 2`
    — 4/4 passed.
  - Focused current-source and historical cache-hit tests:
    `python manage.py test quotations.test_inquiry_image_import.InquiryImageAITests.test_text_cache_rebinds_current_upload_and_omits_source_payload quotations.tests.AIImportParsingTests.test_historical_ai_cache_hit_uses_current_import_provenance --noinput --verbosity 1`
    — 2/2 passed.
  - Manual AI, image, PDF, mailbox-vision, and LPO parser regressions:
    `python manage.py test quotations.test_ai_observability quotations.tests.AIImportParsingTests quotations.test_inquiry_image_import quotations.test_mailbox_po_vision quotations.test_lpo_parser_regressions --noinput --verbosity 1`
    — 159/159 passed.
  - `python manage.py makemigrations --check --dry-run` — no changes detected.
  - `python manage.py check` — no issues.
  - `python -m compileall -q quotations/ai_parsing.py quotations/test_inquiry_image_import.py quotations/tests.py`
    and `git diff --check` — passed.
  - Independent final review — no remaining actionable Task 1.5 issue.
- Migrations: none.
- API changes: no request/response schema change. On a cache hit, source
  provenance and parse metadata now belong to the current upload, unknown
  legacy cache keys are filtered, and `meta.ai_usage` is `{}` rather than the
  first provider call's raw usage.
- Frontend changes: none.
- Accuracy/security impact: semantic rows and their raw row evidence remain in
  the cache because removing them would weaken employee review and evidence
  guarantees. The change removes duplicated full source text/private paths,
  prevents cross-upload provenance leakage, and does not alter extraction,
  matching, blank-price, or send-safety behavior.
- Remaining risks: legacy cache rows may still contain old full payloads at
  rest, but current contract keys make older versions unreachable and every
  read is filtered. Purging or rewriting those rows would delete persisted
  data and is intentionally not performed without separate approval. The
  cache still retains semantic item text and row evidence by design.
- Rollback: revert the task 1.5 commit. No database rollback is required; cache
  rows written in the source-neutral format remain valid reusable results.

### 1.6 — Add a privacy-safe golden quotation-intake evaluation corpus

- Status: completed.
- Commit: `d88b767` (`test: add synthetic quotation intake evaluation`).
- Files changed:
  - `backend/quotations/evaluation_corpus/quotation_intake_v1.json`
  - `backend/quotations/evaluation_corpus/README.md`
  - `backend/quotations/golden_evaluation.py`
  - `backend/quotations/management/commands/evaluate_quotation_intake.py`
  - `backend/quotations/test_golden_evaluation.py`
  - `TECHNICAL_HARDENING_PROGRESS.md`
- Implementation:
  - Added a versioned seed corpus of 30 fully invented cases: 13 manual and 17
    Gmail cases across clean and messy Excel, selectable and scanned PDF,
    email-body tables, follow-ups, partial and full revisions, conflicting
    documents, and similar company branches.
  - Corpus validation rejects undeclared or customer-derived provenance,
    non-`.test` email addresses, malformed source/message contracts, incomplete
    message classification, nonblank selling prices, unknown evidence sources,
    and evidence excerpts that are absent from their source.
  - Cases exercise `used`, `context`, and `excluded` message decisions,
    exact customer snapshot names, quantities and units, revision operations,
    uncertainty, customer budget evidence, prompt-injection text, outbound
    quotation context, complete row-level evidence, and an unresolved sibling-
    branch identity that must not be guessed.
  - Added a deterministic offline scorer for strict row precision/recall,
    exact names, quantities, units, revision operations, parse status, customer
    price evidence, complete citation sets, message selection, identity and
    identity evidence, ambiguity handling, and blank selling prices. Duplicate,
    unnamed, malformed, or unsupported output rows cannot bypass scoring.
  - Reports separate missing and invalid predictions, aggregate and route-
    stratified quality, p50/p90/p95 latency, separate latency/token sample
    counts, and token totals as a historical cost basis.
  - Added `evaluate_quotation_intake`: with no prediction file it performs a
    concise corpus validation; with an explicitly supplied JSON result file it
    scores predictions entirely offline. It does not call Gmail, an AI provider,
    or the database.
- Tests run:
  - `python manage.py test quotations.test_golden_evaluation --noinput --verbosity 1`
    — 16/16 passed.
  - `python manage.py test quotations.tests.InquiryParserRuleTests quotations.tests.InquiryImportTests quotations.tests.AIImportParsingTests quotations.test_gmail_inquiry_import quotations.test_inquiry_image_import --noinput --verbosity 1`
    — 142/142 passed.
  - `python manage.py evaluate_quotation_intake` — validation passed for 30/30
    cases, both routes, and all ten case families.
  - `python manage.py makemigrations --check --dry-run` — no changes detected.
  - `python manage.py check` — no issues.
  - Python compilation and `git diff --check` — passed.
  - Independent review identified six scoring/coverage edge cases; each was
    corrected and the final re-review found no remaining actionable issue.
- Migrations: none.
- API changes: none. The evaluator is a local management command and library;
  production endpoints do not import it.
- Frontend changes: none.
- Accuracy/security impact: no production extraction model, prompt, schema,
  cache, matching, review, evidence, blank-price, OAuth, or delivery behavior
  changed. All cases use invented people, companies, identifiers, values, and
  reserved `.test` domains. The scorer makes later experiments measurable
  without weakening employee review or exposing production content.
- Remaining risks: this is a synthetic semantic seed, not a representative
  production benchmark or a claim of production accuracy. Scanned-document
  cases are auditable transcripts rather than opaque binary fidelity fixtures.
  No release threshold, reviewer-agreement sample, confidence interval, or
  live-provider baseline is established yet. Future versions require privacy
  review and independent adjudication before admitting any de-identified real
  case.
- Rollback: revert the task 1.6 commit. No database, API, or deployment rollback
  is required.

### 1.7 — Correct and version architecture, deployment, and operational documentation

- Status: completed as a documentation-only checkpoint.
- Commit: `dcb2b96` (`docs: correct quotation operations guidance`).
- Files changed:
  - `GMAIL_QUOTATION_ARCHITECTURE_REVIEW.md`
  - `DEPLOYMENT.md`
  - `SECURITY.md`
  - `OPERATIONS.md`
  - `gmail_addon/README.md`
  - `README.md`
  - `backend/.env.example`
  - `backend/pharmacy_api/settings.py` (comment only)
  - `backend/quotations/ai_parsing.py` (comment only)
  - `QUOTATION_MODULE.md`
  - `TODO_QUOTATIONS.md`
  - `current_status.md`
  - `backend/quotations/test_documentation_contract.py`
  - `TECHNICAL_HARDENING_PROGRESS.md`
- Implementation:
  - Added document versions, owners, verification dates, code provenance, and
    an explicit distinction between implemented, measured, derived, deployment-
    snapshot, external, proposed, and unknown claims.
  - Replaced a one-case Gmail latency conclusion with a scoped historical
    measurement; documented the current pipeline/schema identities,
    instrumentation, synthetic evaluation seed, cache provenance, and exact
    limits of what those results establish.
  - Corrected the Gmail data flow, separate add-on/website OAuth scopes,
    consumer-account deployment limitations, AI processor boundary, manual
    private-source durability, mutable delivery ledger, stale-preview gap, and
    strict no-send reconciliation behavior.
  - Replaced stale fixed-cost, backup, automatic-migration, Sentry-install, and
    fully-secured claims with dated provider references and unchecked operator
    verification steps.
  - Added a deployment/incident/backup/retention/credential/ambiguous-send
    operations runbook and marked older roadmap/status documents as historical.
  - Added automated documentation contracts for metadata, runtime version
    agreement, relative links, operational gaps, OAuth scope separation,
    environment inventory, and forbidden stale claims.
  - Performed a read-only Railway/production database inspection only. No
    production variable, deployment, migration, provider, model, prompt,
    schema, or OAuth scope was changed.
- Tests run:
  - Initial invocation without a database override was stopped before Django
    started by the repository's local Debug/Neon safety guard; no test or
    database operation ran.
  - `DATABASE_URL=sqlite:///db.sqlite3 python manage.py test quotations.test_documentation_contract --noinput --verbosity 2`
    — 9/9 passed.
  - `DATABASE_URL=sqlite:///db.sqlite3 python manage.py test pharmacy_api.test_database_settings quotations.test_gmail_addon quotations.test_quotation_email_delivery --noinput --verbosity 1`
    — 73/73 passed; expected mocked `503` cases were exercised.
  - `DATABASE_URL=sqlite:///db.sqlite3 python manage.py makemigrations --check --dry-run`
    — no changes detected.
  - `DATABASE_URL=sqlite:///db.sqlite3 python manage.py check` — no issues.
  - `git diff --check`, stale-claim scan, and relative Markdown-link checks — passed.
  - Independent review found three remaining wording inaccuracies about outbound
    thread context, model-snapshot capture, and smoke-test safety; all were
    corrected, with no remaining high-severity finding.
- Migrations: none.
- API changes: none.
- Frontend changes: none.
- Accuracy/security impact: documentation now preserves and names all intake,
  evidence, review, blank-price, verified-recipient, idempotency, ambiguous-send
  lockout, and reconciliation-only guarantees. No runtime logic changed.
- Remaining risks: the inspected Railway deployment has no explicit pre-deploy
  command, health check, volume, or Sentry DSN; private evidence is therefore
  not durable there. Backup/RPO/RTO, retention/deletion, OAuth publication and
  security-assessment status, credential successors, provider retention, and
  monitoring thresholds remain operator-owned unknowns. Tasks 2.1, 2.2, 2.3,
  2.7, and 2.8 address the corresponding code/configuration preparation without
  authorizing a production deployment or provider purchase.
- Rollback: revert the task 1.7 commit. No database, API, frontend, provider, or
  infrastructure rollback is required.

### 1.8 — Make audit/history records read-only in Django administration

- Status: completed.
- Commit: `801d045` (`fix: protect admin audit history`).
- Files changed:
  - `backend/quotations/admin.py`
  - `backend/quotations/test_admin_history_readonly.py`
  - `SECURITY.md`
  - `TECHNICAL_HARDENING_PROGRESS.md`
- Implementation:
  - Extended the existing history-admin mixin so every concrete model field is
    displayed read-only, including fields added by future migrations.
  - Applied that view-only policy to `AIParseLog`, `CompanyPriceHistory`, and
    `QuotationAuditLog`. Even a superuser can inspect but cannot add, edit,
    delete, or bulk-delete those records through Django administration.
  - Retained the existing view-only protection for Gmail imports and mailbox
    audit/match/message history.
  - Deliberately kept `AIParseCache` deletable by a superuser for legitimate
    cache invalidation/privacy purge while continuing to block cache add/edit.
  - Left historical-import, unconfirmed LPO, and PO review/evidence models out
    of this task because they are active review workflows, not append-only
    audit/history ledgers.
- Tests run:
  - Pre-fix characterization:
    `DATABASE_URL=sqlite:///db.sqlite3 python manage.py test quotations.test_admin_history_readonly --noinput --verbosity 2`
    — failed as expected, proving the audit-log/price-history add/change/delete
    paths and AI-log delete path were available. The initial broad inventory
    also identified mutable PO review records, which were intentionally
    excluded after scope review because their lifecycle remains operational.
  - Focused permissions and real Django-admin endpoint tests:
    `DATABASE_URL=sqlite:///db.sqlite3 python manage.py test quotations.test_admin_history_readonly --noinput --verbosity 2`
    — 5/5 passed.
  - Admin/LPO, AI observability, price-context, and historical-import regressions:
    `DATABASE_URL=sqlite:///db.sqlite3 python manage.py test quotations.test_admin_history_readonly quotations.test_lpo_history_guards quotations.test_ai_observability quotations.test_price_history_context quotations.tests.HistoricalPriceImportTests --noinput --verbosity 1`
    — 80/80 passed.
  - Independent final review — no actionable scope, permission, workflow, or
    migration issue found.
- Migrations: none.
- API changes: none.
- Frontend changes: none.
- Admin behavior changes: audit/history change pages remain viewable to users
  with view permission, while add/change/delete endpoints return forbidden and
  `delete_selected` is absent.
- Accuracy/security impact: closes direct Django-admin tampering of AI parse
  logs, generated company price history, and quotation audit events without
  changing employee quotation/LPO/evidence workflows or any intake, pricing,
  delivery, and reconciliation invariant.
- Remaining risks: this is an administration-layer control, not a database
  append-only trigger; trusted application code and direct database operators
  can still mutate rows. PO review/evidence and historical-import staging are
  intentionally mutable workflow records. `AIParseCache` deletion remains an
  explicit operational capability.
- Rollback: revert the task 1.8 commit. No database or deployment rollback is
  required.

## Phase 2

### 2.1 — Reject sends from stale quotation-email previews

- Status: completed.
- Commit: `644a7c5` (`fix: reject stale quotation email previews`).
- Files changed:
  - `backend/quotations/quotation_email_delivery.py`
  - `backend/quotations/pdf.py`
  - `backend/quotations/serializers.py`
  - `backend/quotations/services.py`
  - `backend/quotations/views.py`
  - `backend/quotations/test_quotation_email_delivery.py`
  - `backend/quotations/test_quotation_concurrency.py`
  - `frontend/src/components/quotations/QuotationEmailPreviewDialog.js`
  - `frontend/src/components/quotations/QuotationEmailPreviewDialog.test.js`
  - `frontend/src/components/quotations/QuotationEditor.js`
  - `frontend/src/components/quotations/QuotationEditor.test.js`
  - `GMAIL_QUOTATION_ARCHITECTURE_REVIEW.md`
  - `SECURITY.md`
  - `TECHNICAL_HARDENING_PROGRESS.md`
- Implementation:
  - Every email-preview response now includes a keyed SHA-256 fingerprint of a
    versioned, canonical customer-facing state. It covers the quotation and
    ordered lines, effective finalized brand, customer/contact details, PDF
    configuration and asset identities, creator signature identity, staff
    actor, and the exact verified Gmail source/thread/recipient/subject. The
    browser receives only the fingerprint, never the canonical values.
  - Quotation detail responses also carry a separate keyed editor-revision
    fingerprint. The detail endpoint locks the quotation and all PDF
    dependencies until both the visible response rows and token are fully
    serialized, preventing a hybrid old-payload/new-token response. Before
    opening a preview, the shipped editor retrieves that atomic snapshot. If it
    differs in either token or displayed payload from the revision on screen,
    the editor is refreshed, the preview remains closed, and a second explicit
    action is required. Stale-preview refresh uses this same gate, including
    after an unlocked error response. The final editor-
    token comparison and email-token creation are also atomic under the render-
    dependency locks.
  - Finalize-and-send and send-email require the reviewed fingerprint for any
    transition from prepared/failed to sending. It is checked before preview
    persistence and again under the authoritative quotation/delivery locks,
    before finalization, PDF generation, or Gmail. Attachment filename, digest,
    and size are also bound to the token. Missing previews return a
    safe `400 email_preview_required`; changed previews return a safe
    `409 stale_email_preview`. Sent remains idempotent, while sending/unknown
    retain their stronger existing lockout/reconciliation behavior.
  - Quote lines, company/contact, creator/profile, PDF settings, referenced
    QuoteItems/Products/Brands/images, and the Gmail connection are locked in a
    primary-key-ordered sequence. The finalized PDF and exact raw MIME are built while
    those dependencies remain locked; Gmail receives those already-frozen
    in-memory bytes only after the database transaction commits.
  - Quotation PDF generation now enables ReportLab invariant output. A known
    provider rejection can therefore regenerate the same visible finalized
    quotation after the normal PDF timestamp boundary and reproduce the stored
    attachment digest instead of falsely failing as a changed attachment.
  - Finalization is projected into the preview state, so its deterministic
    status/brand/total updates do not invalidate a correctly reviewed draft.
    PDF preparation failure still commits the finalized quotation and a failed,
    retryable delivery record instead of losing reconciliation state.
  - The browser treats either preview error as a hard block, offers an explicit
    Refresh preview action, preserves an explicitly selected Gmail thread, and
    requires a second deliberate Send click after refresh. Failed refreshes do
    not clear the stale block or trigger a send.
  - The two remaining PDF-affecting mutation paths now follow the global
    quotation-then-line lock order: single-line Product creation and quotation-
    line Product-image upload. Both recheck editability under the quotation lock;
    image upload also bumps the quotation state through total recalculation.
    A concurrent line deletion now returns a normal not-found response instead
    of raising a server error during image upload.
  - Email preview remains read-only when no settings singleton exists; it uses
    the PDF fallback configuration instead of creating settings and falsely
    invalidating the just-loaded editor revision.
  - Internal outcome/follow-up state is deliberately excluded from the
    fingerprint so unrelated history work does not invalidate an email review.
- Tests run:
  - Focused backend fingerprint/source/send checks:
    `DATABASE_URL=sqlite:///db.sqlite3 python manage.py test quotations.test_quotation_email_delivery.QuotationEmailDeliveryAPITests.test_manual_preview_is_read_only_and_prefills_explicit_contact quotations.test_quotation_email_delivery.QuotationEmailDeliveryAPITests.test_send_requires_a_reviewed_preview_before_any_side_effect quotations.test_quotation_email_delivery.QuotationEmailDeliveryAPITests.test_changed_quotation_rejects_old_preview_then_fresh_review_sends quotations.test_quotation_email_delivery.QuotationEmailDeliveryAPITests.test_preview_fingerprint_is_bound_to_quote_and_staff_actor quotations.test_quotation_email_delivery.QuotationEmailDeliveryAPITests.test_gmail_reply_sends_exact_thread_headers_and_marks_quote_sent --noinput --verbosity 2`
    — 5/5 passed.
  - Full SQLite delivery suite (final run):
    `DATABASE_URL=sqlite:///db.sqlite3 python manage.py test quotations.test_quotation_email_delivery --noinput --verbosity 1`
    — 51/51 passed.
  - Additional canonical-state/source regression:
    `DATABASE_URL=sqlite:///db.sqlite3 python manage.py test quotations.test_quotation_email_delivery.QuotationEmailDeliveryAPITests.test_preview_fingerprint_tracks_customer_facing_but_not_internal_state quotations.test_quotation_email_delivery.QuotationEmailDeliveryAPITests.test_gmail_preview_uses_latest_relevant_inbound_even_when_context quotations.test_quotation_email_delivery.QuotationEmailDeliveryAPITests.test_changed_quotation_rejects_old_preview_then_fresh_review_sends --noinput --verbosity 1`
    — 3/3 passed.
  - Local isolated PostgreSQL 17 / `READ COMMITTED` concurrency suite:
    `DATABASE_URL=postgresql://postgres@127.0.0.1:55432/pharmacy_ci_local python manage.py test quotations.test_product_matching_rework.ProductAliasConcurrencyTests quotations.test_quotation_concurrency --noinput --verbosity 2`
    — 16/16 passed, including an atomic detail-payload/token snapshot, preview
    → concurrent edit → stale send, dependency-lock/frozen-render, concurrent
    deletion, and concurrent double-send coverage.
  - Quotation permissions/workflow/Product regressions:
    `DATABASE_URL=sqlite:///db.sqlite3 python manage.py test quotations.tests.QuotationPermissionTests quotations.tests.QuotationWorkflowTests quotations.test_product_matching_rework.ProductMatchingReworkTests --noinput --verbosity 1`
    — 121/121 passed.
  - Frontend preview/editor tests:
    `npm test -- --watchAll=false --runInBand QuotationEmailPreviewDialog.test.js QuotationEditor.test.js`
    — 60/60 passed, including same-token/hybrid-payload and stale-refresh
    regressions.
  - `npm run build` — optimized production build compiled successfully.
  - Documentation contract suite:
    `DATABASE_URL=sqlite:///db.sqlite3 python manage.py test quotations.test_documentation_contract --noinput --verbosity 1`
    — 9/9 passed.
  - `DATABASE_URL=sqlite:///db.sqlite3 python manage.py makemigrations --check --dry-run`
    — no changes detected.
  - `DATABASE_URL=sqlite:///db.sqlite3 python manage.py check`, Python
    compilation, and `git diff --check` — passed.
- Migrations: none.
- API changes: quotation detail adds `quotation_review_fingerprint`, and
  `GET email_preview` adds `preview_fingerprint`. The optional internal
  `quotation_review_fingerprint` preview query parameter rejects an editor
  revision changed in another session. The two internal staff send actions
  require the email-preview value for a new/retry send and add refresh guidance
  on missing/stale errors. Reconcile and finalize-only APIs are unchanged, and
  terminal sends retain existing idempotency.
- Frontend changes: the editor performs one fresh quotation check before
  preview. Stale quotation or email reviews are visibly blocked and can only be
  replaced by employee review followed by another explicit preview/send action.
- Accuracy/security impact: prevents a changed quotation, line, PDF setting,
  customer identity, or Gmail reply source from being finalized/emailed under
  an older review. The keyed digest is actor/quotation-bound and compared in
  constant time. Verified reply headers, recipient confirmation, one-successful-
  email idempotency, ambiguous-send lockout, and reconciliation-never-sends
  remain unchanged.
- Remaining risks: remote asset bytes replaced out of band at an unchanged
  storage key are not content-hashed by the review token. The exact PDF/MIME is
  frozen only in process memory for one attempt, not persisted as a complete
  immutable snapshot, and the ledger still lacks immutable provider-attempt
  rows; Task 2.2 addresses those gaps. The editor query parameter is optional
  for backward compatibility: the shipped frontend always supplies it, while
  an unknown legacy internal caller can preview current server state but cannot
  prove it matches that caller's earlier screen. Storage I/O while render locks
  are held can extend lock waits, and Product-image upload still performs
  external storage work while holding quotation/line locks. Generic reverse-FK
  deletion can also take a dependency lock before PostgreSQL applies changes to
  quotation lines, the reverse of reviewed render order; PostgreSQL safely
  aborts a deadlock participant, but Task 2.8 must bound and normalize that
  availability failure. Neither limitation permits a stale send.
- Rollback: revert the task 2.1 commit as one backend/frontend unit. No database,
  provider, OAuth, prompt, model, or infrastructure rollback is required.

### 2.2 — Freeze outbound snapshots and preserve provider-attempt history

- Status: completed in the branch; production migration/deployment not performed.
- Commit: this task checkpoint (`feat: freeze quotation email delivery attempts`).
- Files changed:
  - `backend/quotations/models.py`
  - `backend/quotations/quotation_email_delivery.py`
  - `backend/quotations/views.py`
  - `backend/quotations/admin.py`
  - `backend/quotations/migrations/0035_quotationemailoutboundsnapshot_and_more.py`
  - `backend/quotations/test_quotation_email_delivery.py`
  - `backend/quotations/test_quotation_concurrency.py`
  - `backend/quotations/test_product_matching_rework.py`
  - `backend/quotations/test_admin_history_readonly.py`
  - `frontend/src/components/quotations/QuotationEmailPreviewDialog.js`
  - `frontend/src/components/quotations/QuotationEmailPreviewDialog.test.js`
  - `frontend/src/components/quotations/QuotationEditor.js`
  - `frontend/src/components/quotations/QuotationEditor.test.js`
  - `frontend/src/components/quotations/QuotationModule.css`
  - `GMAIL_QUOTATION_ARCHITECTURE_REVIEW.md`
  - `SECURITY.md`
  - `DEPLOYMENT.md`
  - `OPERATIONS.md`
  - `TECHNICAL_HARDENING_PROGRESS.md`
- Implementation:
  - Added one write-once outbound snapshot per delivery. It stores versioned
    structured send facts, the exact RFC-MIME bytes (including the PDF), raw
    and attachment digests/sizes, and the Gmail API thread argument. The raw
    MIME is capped at 35 MiB, remains database-private, and is excluded from
    APIs and the admin form.
  - Snapshot metadata plus bytes receive a complete SHA-256 digest. Every
    provider call rechecks the raw and complete digests before Gmail receives
    base64url re-encoding of the persisted bytes.
  - Added an immutable child row per actual Gmail call with request identity
    and employee/mailbox attribution. Provider outcomes and later strict
    reconciliation proof are separate append-only immutable event rows, so an
    original HTTP/network ambiguity and its safe error classification are
    never overwritten.
  - The snapshot, immutable attempt row, and aggregate `sending` state commit
    in one transaction before the provider call. Normal success, known
    rejection, ambiguity, incomplete receipt, and later reconciliation append
    event facts while retaining the existing aggregate API and lock order.
  - The in-memory Gmail credential generation used to obtain the access token
    is rechecked under the Gmail-connection row lock. A concurrent OAuth
    replacement aborts before finalization, snapshot creation, or Gmail, so
    immutable mailbox attribution cannot be paired with another credential.
  - An already ambiguous or active delivery returns its persisted safety state
    before entering the separate send-token path. A late provider result is
    still appended when strict reconciliation has already made the aggregate
    terminal `sent`; it can never downgrade that aggregate or emit a false
    failure audit.
  - Known-safe retries show and send the exact frozen recipient, CC, subject,
    body, thread headers, and PDF. They do not rerender the PDF, rebuild MIME,
    or refetch a different source email. Changes require a quotation revision.
  - Legacy rows were not backfilled. A legacy failed row may create its first
    honest snapshot on an explicitly reviewed retry after the prior PDF digest
    check; legacy sent/unknown reconciliation remains supported.
  - Frozen retry fields are read-only in the browser, but manual new-email
    retries still require explicit recipient confirmation and another Send
    click. A first failed attempt immediately marks the displayed preview as
    frozen and requires a refresh/review before retry. Unknown/sending remain
    blocked and reconciliation never sends.
  - Metadata-only preview/admin queries defer the potentially 35 MiB raw MIME.
    Only send-integrity verification loads it. Reconciliation treats the
    frozen mailbox, Message-ID, delivery mode, and expected thread as canonical
    even if the mutable aggregate is stale.
  - Reviewed preview construction uses the freshly locked delivery row rather
    than status read before the lock. PostgreSQL worker finalizers also close
    their thread-owned connections so the production-equivalent suite removes
    its temporary database cleanly.
- Tests run:
  - Focused snapshot/attempt/admin suite:
    `DATABASE_URL=sqlite:///db.sqlite3 python manage.py test quotations.test_quotation_email_delivery.QuotationEmailDeliveryAPITests.test_gmail_reply_sends_exact_thread_headers_and_marks_quote_sent quotations.test_quotation_email_delivery.QuotationEmailDeliveryAPITests.test_known_provider_rejection_leaves_quote_finalized_and_retryable quotations.test_quotation_email_delivery.QuotationEmailDeliveryAPITests.test_real_pdf_known_failure_retries_once_without_refinalizing quotations.test_quotation_email_delivery.QuotationEmailDeliveryAPITests.test_pdf_preparation_exception_is_known_failure_not_unbound_or_unknown quotations.test_quotation_email_delivery.QuotationEmailDeliveryAPITests.test_legacy_failed_delivery_freezes_first_honest_snapshot_on_retry quotations.test_quotation_email_delivery.QuotationEmailDeliveryAPITests.test_temporary_http_send_response_is_unknown_not_retryable quotations.test_quotation_email_delivery.QuotationEmailDeliveryAPITests.test_ambiguous_attempt_preserves_initial_outcome_when_reconciled quotations.test_quotation_email_delivery.QuotationEmailDeliveryAPITests.test_retry_uses_persisted_mime_without_rerendering_pdf quotations.test_quotation_email_delivery.QuotationEmailDeliveryAPITests.test_frozen_retry_rejects_edited_customer_facing_fields quotations.test_quotation_email_delivery.QuotationEmailDeliveryAPITests.test_corrupt_frozen_mime_blocks_retry_before_gmail quotations.test_quotation_email_delivery.QuotationEmailDeliveryAPITests.test_snapshot_and_completed_attempt_reject_mutation_and_deletion quotations.test_admin_history_readonly --noinput --verbosity 2`
    — 17/17 passed.
  - Full SQLite delivery/admin suite:
    `DATABASE_URL=sqlite:///db.sqlite3 python manage.py test quotations.test_quotation_email_delivery quotations.test_admin_history_readonly --noinput --verbosity 1`
    — 71/71 passed.
  - Local PostgreSQL 17 / `READ COMMITTED` concurrency suite:
    `DATABASE_URL=postgresql://postgres@127.0.0.1:55432/pharmacy_ci_local python manage.py test quotations.test_product_matching_rework.ProductAliasConcurrencyTests quotations.test_quotation_concurrency --noinput --verbosity 1`
    — 16/16 passed, including proof that snapshot + immutable attempt are visible
    before Gmail is invoked and concurrent confirmation still calls Gmail once;
    the temporary PostgreSQL database was dropped cleanly.
  - Quotation permissions/workflow/Product regressions:
    `DATABASE_URL=sqlite:///db.sqlite3 python manage.py test quotations.tests.QuotationPermissionTests quotations.tests.QuotationWorkflowTests quotations.test_product_matching_rework.ProductMatchingReworkTests --noinput --verbosity 1`
    — 121/121 passed.
  - Frontend preview/editor tests:
    `npm test -- --watchAll=false --runInBand QuotationEmailPreviewDialog.test.js QuotationEditor.test.js`
    — 65/65 passed, including the first-failure frozen-response merge,
    explicit refresh, read-only review, and exact retry integration flow.
  - `npm run build` — optimized production build compiled successfully.
  - Documentation contract suite:
    `DATABASE_URL=sqlite:///db.sqlite3 python manage.py test quotations.test_documentation_contract --noinput --verbosity 1`
    — 9/9 passed.
  - `DATABASE_URL=sqlite:///db.sqlite3 python manage.py makemigrations --check --dry-run`
    — no changes detected beyond the committed `0035` migration.
  - `DATABASE_URL=sqlite:///db.sqlite3 python manage.py check`, Python
    compilation, and `git diff --check` — passed.
- Migration:
  - `0035` is additive and schema-only: three new empty tables, indexes, and
    constraints; no existing row rewrite and no fabricated backfill.
  - Apply before promoting Task 2.2 code. Reversing `0035` after rows exist
    would destroy audit/retry evidence and requires explicit destructive-data
    authority.
- API/frontend changes: existing paths and request fields are unchanged.
  Preview and delivery responses add only `outbound_snapshot_frozen`. The
  server adds non-breaking `409 outbound_snapshot_mismatch`,
  `409 gmail_connection_changed`, and safe snapshot integrity/size errors. The
  browser makes a frozen retry read-only and directs content changes to the
  existing revision workflow.
- Accuracy/security impact: exact retries can no longer change recipient, CC,
  subject, body, reply headers, MIME boundaries, or PDF bytes. Provider calls
  and learned outcomes are individually attributable without overwriting
  earlier facts. Raw MIME
  contains customer/PDF data, so database access, backups, retention, and
  capacity require operator governance; normal APIs/admin forms never return
  it. Privileged raw SQL remains outside application immutability, while digest
  verification blocks a corrupted snapshot before retry.
- Remaining risks: the preview token still identifies remote asset keys, not
  out-of-band bytes replaced at the same key before the first snapshot. A crash
  after committing the attempt but before reaching Gmail cannot be distinguished
  safely; the attempt may have no result event and remains locked for no-send
  reconciliation. There is no automated stuck-delivery sweeper, and legacy
  historical attempts cannot be reconstructed honestly. Reconciliation success
  is append-only, but repeated unsuccessful checks do not yet receive one
  immutable event per invocation. Task 2.3 addresses private evidence-storage
  abstraction; Task 2.8 addresses bounded database availability handling.
- Rollback: before any `0035` rows exist, the application commit may be reverted
  and the unused migration may be reversed with an approved recovery point.
  After rows exist, retain all three tables and prefer a forward fix. If code
  must be rolled back below Task 2.2, suspend every quotation-email send/retry
  until a compatibility guard that honors existing frozen snapshots is
  restored; old send code can otherwise rebuild a failed retry. No OAuth, AI
  model/prompt/schema, provider, or production infrastructure changed.

### 2.3 — Abstract durable private evidence storage with dual-read compatibility

- Status: completed in code; live durable-provider configuration is explicitly
  deferred because the repository has no approved provider package, bucket,
  credentials, volume, residency decision, or backup/restore evidence.
- Commit: this task checkpoint (`feat: abstract private quotation evidence storage`).
- Finding verified:
  - All application-managed quotation-source writes already converged on
    `store_import_source`, and all rereads used `read_private_ref`, but those
    helpers directly wrote/read `QUOTATION_PRIVATE_STORAGE_ROOT` with `Path`.
  - The inspected Railway service has no volume and no explicit private root,
    so database references can outlive local bytes. Default Cloudinary media is
    public-media oriented and is not an acceptable private-evidence backend.
  - Gmail inquiry and mailbox-PO binaries intentionally remain canonical in
    Gmail; exact outbound email MIME/PDF remains in its immutable PostgreSQL
    snapshot and is outside this task.
- Files changed:
  - `backend/quotations/private_storage.py`
  - `backend/pharmacy_api/settings.py`
  - `backend/quotations/ai_parsing.py`
  - `backend/quotations/ai_learning.py`
  - `backend/quotations/serializers.py`
  - `backend/quotations/views.py`
  - `backend/quotations/test_private_storage.py`
  - `backend/quotations/tests.py`
  - `backend/quotations/test_documentation_contract.py`
  - `backend/.env.example`
  - `GMAIL_QUOTATION_ARCHITECTURE_REVIEW.md`
  - `DEPLOYMENT.md`
  - `SECURITY.md`
  - `OPERATIONS.md`
  - `TECHNICAL_HARDENING_PROGRESS.md`
- Implementation:
  - Added a dedicated `quotation_evidence` Django storage alias. Its default is
    a private filesystem backend whose root follows
    `QUOTATION_PRIVATE_STORAGE_ROOT`; it never uses the default
    media/Cloudinary alias and never requests a public URL.
  - Added provider-neutral backend/options configuration hooks without adding
    a provider dependency or configuring live infrastructure.
  - New writes use stable `inquiry_sources/v1/...` content-addressed keys with
    the full SHA-256 and a bounded extension. Customer filenames and absolute
    paths are not retained in keys. Supplied digests, existing objects, and
    every versioned read are integrity checked.
  - Writes are bounded, idempotent for existing identical content, immediately
    read back, and fail closed on corruption/backend errors. A backend-returned
    alternate key is validated and content-checked but never automatically
    deleted; stable exact-key save semantics are a provider cutover prerequisite.
    Local files/directories default to private permission modes.
  - Versioned refs prefer the active backend and may use a previous local copy
    only after a definite miss and verification of the embedded full digest.
    Existing unversioned refs remain local-first, then read an operator-copied
    active-backend object under the same legacy key after definite local
    absence or a recorded-hash mismatch. Backend failure is never converted to
    not-found or permission to fall back.
  - Absolute, traversal, URL, Windows-path, NUL, Gmail pseudo-, malformed,
    unknown-version, control-character, and non-evidence-namespace refs are
    rejected before storage access.
  - Historical source previews now return controlled `503` for storage outage
    or integrity failure, retain `404` for definite absence, and add
    private/no-store/nosniff response headers.
- Tests run:
  - Focused private-storage and historical-preview suite:
    `DATABASE_URL=sqlite:///db.sqlite3 python manage.py test quotations.test_private_storage --noinput --verbosity 2`
    — 19/19 passed, including file-backed outcome, LPO, and proforma routes.
  - Private storage, inquiry, historical import, AI parsing, image import,
    mailbox-PO, PO attachment, LPO parsing, and documentation regressions:
    `DATABASE_URL=sqlite:///db.sqlite3 python manage.py test quotations.test_private_storage quotations.tests.InquiryImportTests quotations.tests.HistoricalPriceImportTests quotations.tests.AIImportParsingTests quotations.test_inquiry_image_import quotations.test_mailbox_po_audit.MailboxPOAuditTests quotations.test_po_evidence_attachment quotations.test_lpo_parser_regressions.LPOTextParserRegressionTests quotations.test_documentation_contract --noinput --verbosity 1`
    — 167/167 passed.
  - Final workflow, Gmail-intake, outbound-delivery, and mailbox-vision safety
    regressions were split into three commands to avoid the shell command-time
    ceiling:
    `DATABASE_URL=sqlite:///db.sqlite3 python manage.py test quotations.tests.QuotationWorkflowTests quotations.test_quotation_email_delivery --noinput --verbosity 1`
    — 134/134 passed;
    `DATABASE_URL=sqlite:///db.sqlite3 python manage.py test quotations.test_gmail_inquiry_import --noinput --verbosity 1`
    — 77/77 passed; and
    `DATABASE_URL=sqlite:///db.sqlite3 python manage.py test quotations.test_mailbox_po_vision --noinput --verbosity 1`
    — 77/77 passed (288/288 total).
  - `python manage.py check`, `python manage.py makemigrations --check --dry-run`,
    `python -m compileall -q pharmacy_api quotations`, and `git diff --check`
    — all passed.
- Migrations: none. Existing max-500 `source_file_ref` fields hold both legacy
  and new versioned keys; no row rewrite or backfill is performed.
- API/frontend changes: no request/response schema, route, or frontend change.
  The staff-only historical preview endpoint adds cache-safety headers and a
  precise `503` storage-unavailable result instead of an unhandled server error.
- Accuracy/security impact: manual and Gmail workflows, employee review, blank
  selling prices, suggestion-only matching, row evidence, preview-before-send,
  verified reply headers, single-send guarantees, ambiguous-send lockout, and
  no-send reconciliation are unchanged. Content identity is stronger and
  private keys disclose less customer information.
- Remaining risks/deferred configuration:
  - The default backend is still ephemeral on unmounted Railway. Live
    durability requires a separately approved provider/volume, pinned package,
    credentials, private-access/residency policy, complete versioned-and-legacy
    copy with hash verification, and backup/restore drill.
  - Files already lost from the ephemeral filesystem cannot be recreated by
    this abstraction.
  - Abandoned manual previews and price-reference parsing can leave
    unreferenced objects; contract-intelligence may retain supported Gmail
    attachments. No destructive purge was added without approved retention and
    legal-hold rules.
  - A future remote-backend outage in most upload actions still reaches the
    generic server-error boundary, and automatic PDF AI fallback does not yet
    present a dedicated storage warning. These are availability/UX gaps, not
    evidence or send-safety bypasses, and live remote configuration is blocked.
- Rollback: while the default local backend remains active, revert this commit;
  the versioned keys are ordinary relative local paths and the pre-Task 2.3
  reader can still resolve them. After any remote-only write, do not roll below
  the dual reader until exact hash-verified objects are restored to the legacy
  root; prefer a configuration rollback or forward fix with uploads paused.

### 2.4 — Harden attachment checks and spreadsheet fidelity

- Status: completed in code; not deployed.
- Commit: this task checkpoint (`fix: harden quotation attachment parsing`).
- Finding verified:
  - Supported Office uploads were checked mainly by filename/signature before
    openpyxl/calamine, so archive expansion, unsafe/duplicate package members,
    package-kind mismatches, formulas, hidden content, merges, and fallback
    truncation could be invisible.
  - Direct price-reference XLSX and historical-PDF parsing did not share one
    safety/fidelity boundary. Gmail native AI preserved original bytes but did
    not expose the same bounded inspection evidence before provider submission.
  - Quotation-line product-image upload persisted a file without the complete
    shared decode validation used by other image paths.
  - Contract-intelligence returned only the first ten attachment records, so
    later customer attachments could disappear rather than remain explicit
    skipped evidence.
- Files changed:
  - `backend/quotations/attachment_inspection.py`
  - `backend/quotations/import_parsers.py`
  - `backend/quotations/import_rules.py`
  - `backend/quotations/ai_parsing.py`
  - `backend/quotations/gmail_inquiry_import.py`
  - `backend/quotations/contract_intelligence.py`
  - `backend/quotations/price_reference.py`
  - `backend/quotations/historical_import_parsers.py`
  - `backend/quotations/quote_po_intelligence.py`
  - `backend/quotations/models.py`
  - `backend/quotations/migrations/0036_quotationoutcomepoimport_parsed_meta.py`
  - `backend/api/upload_validation.py`
  - `backend/api/serializers.py`
  - `backend/quotations/serializers.py`
  - `backend/quotations/views.py`
  - `backend/pharmacy_api/settings.py`
  - `backend/.env.example`
  - `backend/api/tests.py`
  - `backend/quotations/tests.py`
  - `backend/quotations/test_attachment_fidelity.py`
  - `backend/quotations/test_attachment_meta_retention.py`
  - `backend/quotations/test_gmail_attachment_fidelity.py`
  - `backend/quotations/test_import_rule_fidelity.py`
  - `backend/quotations/test_lpo_parser_regressions.py`
  - `backend/quotations/test_pdf_resource_bounds.py`
  - `backend/quotations/test_reference_attachment_fidelity.py`
  - `backend/quotations/test_email_lpo.py`
  - `backend/quotations/test_documentation_contract.py`
  - `frontend/src/components/quotations/QuotationOutcomeReview.js`
  - `frontend/src/components/quotations/QuotationOutcomeReview.test.js`
  - `frontend/src/components/quotations/ProformaInvoiceManager.js`
  - `frontend/src/components/quotations/ProformaInvoiceManager.test.js`
  - `frontend/src/components/quotations/InquiryManager.js`
  - `frontend/src/components/quotations/InquiryManager.test.js`
  - `frontend/src/components/quotations/InquiryManagerCompanySafety.test.js`
  - `frontend/src/components/quotations/ContractIntelligenceManager.js`
  - `frontend/src/components/quotations/ContractIntelligenceManager.test.js`
  - `frontend/src/components/quotations/QuotationEditor.js`
  - `frontend/src/components/quotations/QuotationEditor.test.js`
  - `ATTACHMENT_SECURITY_AND_SPREADSHEET_FIDELITY.md`
  - `GMAIL_QUOTATION_ARCHITECTURE_REVIEW.md`
  - `SECURITY.md`
  - `OPERATIONS.md`
  - `DEPLOYMENT.md`
  - `TECHNICAL_HARDENING_PROGRESS.md`
- Implementation:
  - Added provider-neutral PDF/Office inspection before parsing. OOXML entry,
    member, total-expansion, traversal, duplicate, symlink, encryption,
    required-part, package-kind, macro-masquerade, and unsafe XML violations
    fail closed within code-level ceilings. PDF preflight follows bounded
    revision/xref chains, validates exact direct/compressed-object mappings and
    stream boundaries, and constrains `PdfReader` to the validated graph before
    complete object/page inspection. MIME mismatch after validated bytes
    remains a warning.
  - XLSX formulas and missing caches, explicit error/date cells, hidden
    sheets/rows/columns, merged cells, protection, external links, embedded
    objects, suspicious compression, and bounded/limited inspection are
    warning-only. Legacy XLS and binary XLSB explicitly retain limited-
    inspection warnings. Form/embedded/active PDF markers are warning-only and
    are never executed.
  - Visible sheets are selected before caps; row/column/sheet limits warn only
    when exceeded. A late primary-reader failure cannot leak partial rows into
    fallback output. Cross-sheet duplicates remain present and are flagged for
    staff rather than silently removed.
  - Missing quantity/unit and ambiguous signed/grouped numeric source text now
    deterministically require review while preserving the previous parsed
    compatibility value. No locale reinterpretation or price invention was
    added.
  - Gmail PDF/XLS/XLSX inspection retains exact original provider bytes and
    hashes. Bounded warning/safety/fidelity metadata enters the manifest and
    evidence without hidden sheet names or binary content. Any selected hard
    failure records explicit failed evidence and blocks the whole AI provider
    call so a partial selected source set cannot be analyzed silently.
    Selected supported documents that cannot be fetched/prepared or exceed the
    per-file, selected-file-count, or combined-byte limit use the same
    fail-closed behavior. A selected inbound message with more than 100
    attachment metadata entries also blocks before fetch/provider use; only a
    Gmail `SENT` message with the exact singleton mailbox `From` is exempt as
    outbound context. Native workbooks also receive visible-sheet,
    per-sheet row/column, aggregate-row, and aggregate-cell bounds before the
    provider call.
  - Every local PDF vision render repeats inspection immediately before the
    native renderer and requires a bounded local-traversal result. Reachable
    inline images in Page, recursive Form, Pattern, Type3, soft-mask, and
    annotation-appearance streams block local rendering conservatively;
    unsupported filters and per-page image aggregates use the same boundary.
  - Direct price-reference XLSX and historical PDF now share inspection and
    expose additive bounded metadata. Existing explicit reference-price
    application and historical review/commit semantics are unchanged.
  - Product, quotation-line, logo, signature, and stamp images now require a
    bounded full Pillow verification and pixel decode, decoded-format/extension
    and supplied-MIME agreement, positive dimensions, at most 12,000 pixels per
    edge, at most 25 million pixels, and one frame before persistence. Uploads
    are rewound on every result. Company `Brand.logo` uses the same boundary.
  - Parser/inspection warnings are rebound to the current source after AI
    cleanup without entering reusable cache payloads. Deterministic customer
    price evidence and notes are restored only to a unique matching AI row; if
    that mapping is not unique, the AI replacement is rejected and the
    deterministic rows remain. Selling-price fields stay blank.
  - The first-ten contract-intelligence processing ceiling is unchanged, but
    every later attachment remains JSON-safe skipped metadata and is never
    fetched or parsed. PO/outcome and proforma file pickers now advertise only
    the PDF/Excel types their backend routes accept.
- Tests run:
  - Final complete quotation regression from a fresh SQLite test database:
    `DATABASE_URL=sqlite:///task24-all-quotations-final.sqlite3 python manage.py test quotations --noinput --verbosity 1`
    — 1,068/1,068 passed; 16 intentional environment-specific skips. Logged
    service-unavailable, revoked-OAuth, and stale-summary errors are asserted
    resilience cases.
  - Final API regression:
    `DATABASE_URL=sqlite:///task24-api-final.sqlite3 python manage.py test api --noinput --verbosity 1`
    — 32/32 passed.
  - Final combined attachment/Gmail regression:
    `DATABASE_URL=sqlite:///task24-root-final.sqlite3 python manage.py test quotations.test_pdf_resource_bounds quotations.test_attachment_fidelity quotations.test_import_rule_fidelity quotations.test_reference_attachment_fidelity quotations.test_gmail_attachment_fidelity quotations.test_gmail_inquiry_import --noinput --verbosity 1`
    — 169/169 passed.
  - Independent defensive regression: 38/38 focused PDF/Gmail boundary tests
    passed; a read-only compatibility sample of 500/500 existing private PDFs
    passed, including compressed-object PDFs.
  - Final frontend regression:
    `npm test -- --watchAll=false --runInBand QuotationOutcomeReview.test.js ProformaInvoiceManager.test.js ContractIntelligenceManager.test.js InquiryManager.test.js InquiryManagerCompanySafety.test.js QuotationEditor.test.js`
    — 97/97 passed across six suites; the logged replacement-parse error is an
    asserted UI failure-state test.
  - `npm run build` — optimized production build compiled successfully.
  - `python manage.py check`, `python manage.py makemigrations --check --dry-run`,
    `python -m compileall -q api quotations pharmacy_api`, and
    `git diff --check` — passed (line-ending notices only where noted by Git).
- Migration: `0036_quotationoutcomepoimport_parsed_meta` adds one
  empty-default JSON field to `QuotationOutcomePOImport`; it deletes no record
  and has no customer-content backfill. It must run before Task 2.4 code. Once
  structured evidence exists, reversing it would delete that evidence, so keep
  the column and prefer a forward fix.
- API/frontend changes: no endpoint or request shape changed. The outcome-PO
  serializer adds the read-only `parsed_meta` response field. Preview, Gmail
  manifest/evidence, price-reference, and historical responses gain additive
  warning/safety/fidelity metadata. Hard invalid Gmail attachments become
  explicit failed evidence with no provider call. File-picker accept filters
  now match existing server behavior.
- Accuracy/security impact: definite malformed/container/resource violations
  fail before parser/provider/persistence where applicable; possible business-
  fidelity problems remain visible warnings and never cause automatic formula
  recalculation, hidden-data recovery, merge filling, deduplication, product/
  alias creation, company assignment, or selling-price invention. Employee
  review, row evidence, Gmail/manual routes, preview-before-send, verified
  replies, one-send-per-revision, ambiguous lockout, and no-send reconciliation
  remain unchanged.
- Remaining risks: this is not malware/AV scanning and parsers still run in the
  web process without a sandbox. XLS/XLSB, large XLSX worksheet XML, style-based
  dates, formulas/rendering, PDF markers/forms, OCR, and semantic extraction
  remain incomplete or fallible. A legitimate file can exceed a hard resource
  ceiling. No practical generated XLS/XLSB fixture proves every binary feature;
  those formats deliberately retain limited-inspection warnings. Native PDF
  image codecs retain in-process complexity after geometry/output bounds;
  inline/unsupported-filter PDFs deliberately skip local rendering. Gmail's
  response JSON/MIME tree is materialized before the 100-entry attachment cap,
  and trusted direct Django-admin/model image assignment is outside the custom
  serializer boundary.
- Rollback: revert this checkpoint as one unit before deployment; an unused
  `0036` may be reversed. After structured outcome metadata exists, retain the
  column and use a forward or schema-compatible application rollback. After
  deployment, first restore an accidentally tightened environment limit or
  prefer a forward fix; reverting the inspection call sites weakens defenses.
  No provider/OAuth/AI/production rollback is needed. Re-run a fresh preview
  after rollback rather than trusting output produced under a different
  inspection contract.

### 2.5 — Preserve forwarded RFQs and canonicalize matching identities

- Status: completed in code; not deployed.
- Commit: this task checkpoint (`fix: harden forwarded Gmail identity`).
- Finding verified:
  - Gmail fetch removed valid forwarded RFQ text/HTML, while permissive edge
    cases could duplicate or retain nested Outlook reply history.
  - Gmail inquiry and mailbox-PO identity used collapsed/set-like sender
    parsing in places, so multi-address or duplicate physical `From` fields
    could appear exact. Reply preparation and Sent reconciliation also lacked
    the original physical-header multiplicity.
  - Matching did not consistently canonicalize root-dot and Unicode/IDNA
    domains. Public-provider coverage was incomplete, and unsaved company-name
    or acronym/domain inference could satisfy an automatic LPO identity gate.
  - AI identity could be populated without source keys, and only explicitly
    versioned v3 reviews were quarantined after trust-boundary changes.
- Files changed:
  - `backend/requirements.txt`
  - `backend/quotations/email_identity.py`
  - `backend/quotations/contract_intelligence.py`
  - `backend/quotations/gmail_inquiry_import.py`
  - `backend/quotations/mailbox_po_audit.py`
  - `backend/quotations/mailbox_po_matching.py`
  - `backend/quotations/mailbox_po_reconciliation.py`
  - `backend/quotations/quotation_email_delivery.py`
  - `backend/quotations/test_email_identity.py`
  - `backend/quotations/test_gmail_inquiry_import.py`
  - `backend/quotations/test_mailbox_po_adversarial.py`
  - `backend/quotations/test_mailbox_po_audit.py`
  - `backend/quotations/test_mailbox_po_matching.py`
  - `backend/quotations/test_mailbox_po_reconciliation.py`
  - `backend/quotations/test_mailbox_po_vision.py`
  - `backend/quotations/test_quotation_email_delivery.py`
  - `frontend/src/components/quotations/GmailInquiryReview.js`
  - `frontend/src/components/quotations/GmailInquiryReview.test.js`
  - `GMAIL_QUOTATION_ARCHITECTURE_REVIEW.md`
  - `SECURITY.md`
  - `OPERATIONS.md`
  - `DEPLOYMENT.md`
  - `TECHNICAL_HARDENING_PROGRESS.md`
- Implementation:
  - Gmail inquiry fetch opts into a strict, bounded forwarded-content path.
    Gmail/Outlook headers must be complete and unique; empty optional `Cc`/`Bcc`
    is accepted. Attachment-only forwarding is accepted only with a supported
    non-inline PDF/Excel attachment. Outlook `Re:` ancestry, malformed headers,
    and later nested thread history fail closed or are trimmed. Header-only,
    plain, and HTML forwards are removed from the newest-body view and supplied
    only as explicitly unverified transient analyzer input. When an ordinary
    quoted-reply end cannot be mapped safely back to HTML, the current forward
    remains as bounded text and HTML is omitted so older tables cannot leak in.
  - Stored manifests retain only forward hashes, lengths, truncation, and an
    unverified flag. Gmail's raw snippet is also replaced with a preview derived
    from the sanitized outer body. Raw forwarded bodies are not persisted. Attachments remain
    owned by the physical outer Gmail message; embedded forwarding headers
    never replace its envelope sender, establish exact identity/contact, or
    affect reply routing. Selling prices remain blank.
  - Added pinned `idna==3.11` matching-only canonicalization using UTS #46 and
    non-transitional IDNA 2008. One root dot and Unicode domains normalize to
    lowercase ASCII; malformed, IP, single-label, empty-label, and overlong
    values fail closed. Local dots and `+tags` are preserved. Public-provider
    domains, including regional and legacy variants, never become company-
    domain evidence; exact saved public-mail addresses remain usable.
  - Added one shared singleton physical-`From` validator. Exactly one field
    containing exactly one canonical address is required. Gmail intake,
    mailbox-PO ranking, Gmail reply preparation, and Sent reconciliation all
    preserve/check physical multiplicity. Ambiguous/malformed senders provide
    no identity and can never trigger an automatic LPO link.
  - Mailbox-PO reconciliation now passes stored full headers and uses
    `mailbox_match_v6`, invalidating unsafe v5 reuse. Company-name/acronym
    domain inference and same-domain/different-sender evidence remain visible
    for review but cannot satisfy automatic identity. Automatic linking now
    requires an exact saved sender, exact quotation reference, or selected
    attachment identity.
  - Gmail reply metadata now preserves every `From` and `Reply-To` field. A
    singleton physical `From` is mandatory before a singleton `Reply-To` may be
    used only for routing. Existing recipient, subject, thread, RFC Message-ID,
    single-send, ambiguous lockout, and no-send reconciliation gates remain.
    Newly verified replies carry a sender-validation contract; a frozen failed
    reply from before that contract cannot be retried without a new reviewed
    quotation revision.
  - The Gmail matcher is `gmail_identity_v4`. Every unconfirmed non-v4 or
    unversioned stored identity result is quarantined: candidates,
    recommendations, and exact status are cleared and a reanalysis warning is
    shown. Confirmed history is unchanged. Populated AI identity must cite at
    least one valid current source, and forwarded-derived identity is visibly
    labeled unverified in the review UI; purchaser auto-selection remains off.
- Tests run:
  - Full mailbox-PO/email-identity regression:
    `DATABASE_URL=sqlite:///task25-mailbox-all-final5.sqlite3 python manage.py test quotations.test_email_identity quotations.test_mailbox_po_vision quotations.test_mailbox_po_resumable quotations.test_mailbox_po_reconciliation quotations.test_mailbox_po_portal_layouts quotations.test_mailbox_po_matching quotations.test_mailbox_po_identity_payload quotations.test_mailbox_po_candidate_precision quotations.test_mailbox_po_audit quotations.test_mailbox_po_adversarial --noinput --verbosity 1`
    — 299/299 passed.
  - Gmail import and documentation contracts:
    `DATABASE_URL=sqlite:///task25-gmail-final4.sqlite3 python manage.py test quotations.test_gmail_inquiry_import quotations.test_documentation_contract --noinput --verbosity 1`
    — 109/109 passed.
  - Quotation email delivery:
    `DATABASE_URL=sqlite:///task25-delivery-final5.sqlite3 python manage.py test quotations.test_quotation_email_delivery --noinput --verbosity 1`
    — 71/71 passed.
  - Gmail review frontend:
    `CI=true npm test -- --watchAll=false --runInBand --runTestsByPath src/components/quotations/GmailInquiryReview.test.js`
    — 27/27 passed; logged request errors are asserted failure-state tests.
  - Complete quotation regression:
    `DATABASE_URL=sqlite:///task25-all-quotations-final3.sqlite3 python manage.py test quotations --noinput --verbosity 1`
    — 1,118/1,118 passed; 16 intentionally skipped.
  - `npm run build` — compiled successfully (229.85 kB main JS and 41.36 kB
    main CSS gzip output).
  - `DATABASE_URL=sqlite:///task25-static-check.sqlite3 python manage.py check`
    — no issues.
  - `DATABASE_URL=sqlite:///task25-static-check.sqlite3 python manage.py makemigrations --check --dry-run`
    — no changes detected.
  - `python -m pip check`, `python -m compileall -q api quotations pharmacy_api`,
    and `git diff --check` — passed.
  - Two independent final reviews found no remaining Critical, High, or Medium
    correctness/security issues. Their focused security set passed 9/9.
- Migration: none. No stored address or confirmed import is rewritten.
- API/frontend changes: no endpoint or request shape changed. Gmail reply
  metadata is internal. Existing Gmail import/manifest/candidate responses gain
  additive forward/trust/reanalysis flags and warnings. The review screen adds
  an unverified-forward identity label and a prominent reanalysis action for
  stale identity results. Automatic LPO status is now conservatively withheld
  when identity depends only on inferred or same-domain evidence.
- Accuracy/security impact: closes ambiguous sender identity across intake,
  LPO matching, reply preparation, and reconciliation; prevents unproven domain
  inference from creating an automatic link; and retains valid forwarded RFQ
  content without trusting embedded headers. Gmail/manual routes, employee
  review, row evidence, uncertainty, blank selling prices, suggestion-only
  matching, preview-before-send, verified reply headers, one successful email
  per revision, ambiguous-send lockout, and reconciliation-never-sends remain.
  No AI prompt, output schema, model, OAuth scope, migration, or production
  configuration changed.
- Remaining risks: unsupported forwarding formats remain trimmed and require
  manual intake. IDNA normalization does not prove ownership, affiliation,
  DMARC, or resistance to visual homographs. The public-provider list is
  maintained rather than obtained from a live service, so domain inference is
  intentionally review-only. AI can still misread forwarded content; every
  suggestion and row requires employee review against source evidence.
- Rollback: revert this checkpoint before deployment; no database rollback is
  needed. After deployment, rollback restores the older parser/matcher and
  weakens these boundaries, so prefer a forward fix. Reanalyze unconfirmed
  identity after any rollback rather than trusting results made under a
  different matcher version.

## Phase 3

Intentionally not implemented.
