# Gmail Workflow Optimization Progress

This document records implementation checkpoints for
`codex/gmail-workflow-optimization`. The merged technical-hardening controls
remain the mandatory baseline. Every workflow feature in this project is
disabled by default, and no checkpoint authorizes deployment or flag rollout.

## Starting point

- Starting/main SHA: `2d84af31801b9f0c0ec775f14883bf1bc3437c2b`
- Branch: `codex/gmail-workflow-optimization`
- Preserved untracked directory: `output/`

## Phase 1 checkpoint — complete

| Task | Commit | Default-off flag | Result |
| --- | --- | --- | --- |
| 1.1 Privacy-safe employee funnel metrics | `30e12afcd219f14333fc331df109676510ca5844` | `QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED` | Allow-listed, content-free workflow metrics with privacy validation. |
| 1.2 Company approval and uncertainty ergonomics | `a8f68cbbf821faaa79efabd8d60e0811b1076aa2` | `QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED` | Explicit suggested-company approval, durable identity acknowledgement, and per-row review controls. |
| 1.3 Safe chained actions | `7ef179a0f768dc4769f385334d1a37b043967acb` | `QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED` | Save-before-create and save-before-preview chains with four-part stale-state binding and no automatic finalize/send. |
| 1.4 Progressive quotation editor | `b7c8b74e783740b40d8afec039ade2d77140d242` | `QUOTATION_EDITOR_PROGRESSIVE_LOAD_ENABLED` | Quote-first rendering, independent reference-data states/retries, and predictable blank-price keyboard navigation. |
| 1.5 Content-free Gmail analysis progress | `8ebfa0284fb938067e07178cf364b12684b503b6` | `QUOTATION_GMAIL_ANALYSIS_PROGRESS_ENABLED` | Attempt/source/generation-bound stages, safe errors, reload polling, rollback compatibility, and stale-response rejection. |

### Phase 1 schema and API changes

- Migration `quotations.0040_gmailinquiryimport_analysis_progress` is additive
  and adds only bounded progress metadata with empty/zero/null defaults.
- `GET /api/quotations/gmail-inquiry-imports/{id}/analysis_progress/` is
  available only when its flag is strictly enabled. It reuses the existing
  import authorization policy and returns a private, no-store, content-free
  `gmail_analysis_progress_v1` projection.
- Existing Gmail review, synchronous analysis, quotation editor, secure email
  preview, finalization, delivery, and reconciliation paths remain available
  when all Phase 1 flags are disabled.

### Phase 1 validation

All commands used local SQLite, mocked provider behavior, and the local
frontend toolchain. No live Gmail, customer content, production database, or
paid AI-provider call was used.

- Focused Task 1.5 backend and documentation tests: `30/30` passed.
- Focused Task 1.5 frontend tests: `58/58` passed.
- Gmail phase regression group: `176/176` passed in 301.254 seconds.
- Quotation workflow/editor/email-delivery regression group: `315/315`
  passed in 621.517 seconds; `16` PostgreSQL-only concurrency cases were
  correctly skipped by SQLite.
- Complete frontend suite: `300/300` passed across 20 suites.
- Frontend production build: compiled successfully with no warning.
- Django system check: no issues.
- Migration drift check: no changes detected.
- Documentation contracts: `15/15` passed.
- Repository diff check: clean apart from the existing settings-file CRLF
  normalization warning.
- Independent cumulative Task 1.5 audit: no Critical, High, or Medium finding
  remains.

Expected test logs for deliberately simulated OAuth, stale-summary, parsing,
and service-unavailable failures were asserted fail-safe cases; all runners
exited successfully.

### Phase 1 rollback

Disable the relevant flag to restore its prior workflow. Migration `0040`
should normally remain in place during application rollback because it is
additive and old code safely ignores it. An analysis that already obtained a
progress binding is allowed to finish its authoritative import transaction
even if the progress flag is disabled mid-flight; the progress endpoint hides
immediately and the browser falls back to the legacy full-record status path.

### Safety confirmation

Phase 1 did not change AI models or OAuth scopes, create products or aliases,
set selling prices, finalize quotations, send email, weaken verified reply or
stale-preview controls, modify production infrastructure, or access production
customer content.

## Phase 2 checkpoint

Complete. The Phase 2 implementation remains disabled by default and does not
configure or start a worker service.

| Task | Commit | Default-off flag | Result |
| --- | --- | --- | --- |
| 2.1 Unified Gmail inquiry and quotation workspace | `054ce5c1b0aa8b75e735b3756a58ed68d820bcde` | `QUOTATION_GMAIL_UNIFIED_WORKSPACE_ENABLED` | One review/pricing surface with explicit company, uncertainty, Product, and price decisions; the established two-screen route remains the fallback. |
| 2.2 Bounded parallel Gmail reads | `64392c310bbd51f3366223bf68ced67447c77d9b` | `QUOTATION_GMAIL_PARALLEL_FETCH_ENABLED` | Canonically verified message and attachment reads may run with a bounded limit (default `4`), then return to deterministic chronological order. |
| 2.3 Durable background analysis | `93fb48d6f2b486ce375cec89726dd56904231d32` | `QUOTATION_GMAIL_BACKGROUND_ANALYSIS_ENABLED` | PostgreSQL job/lease ledger, idempotent enqueue, safe progress, stale-result rejection, crash recovery, and a separate management-command worker. |
| PostgreSQL test correction | `1ab7bd7a4f4adf5b6a6a78e9316e92855cc00fc3` | Not applicable | Corrected one concurrency assertion to use the model's actual `analysis` field; no application behavior changed. |

### Phase 2 schema, API, and worker changes

- `POST /api/quotations/gmail-inquiry-imports/{id}/confirm_and_prepare_quotation/`
  is additive and available only when unified workspace and review UI V2 are
  strictly enabled. It is source-, generation-, review-, and company-approval
  bound, and atomically creates or reuses the hardened Inquiry/Quotation path.
- Existing `POST .../analyze/` remains synchronous with HTTP 200 while
  background analysis is disabled. When enabled, it idempotently returns the
  current job (HTTP 202 while active, HTTP 200 for a current completed result).
- Migration `quotations.0041_gmailinquiryanalysisjob` creates only an empty job
  ledger. It has one uniqueness constraint per import/generation and one
  partial uniqueness constraint for a queued/running job per import.
- `python manage.py run_gmail_inquiry_worker` is prepared but no Railway worker,
  scheduler, provider, credential, or production flag was configured.
- Combined job transitions use import -> job -> user lock order. The short
  claim transaction uses `SKIP LOCKED`; lease tokens, heartbeats, employee
  authorization, canonical source fingerprints, and generations are
  revalidated before provider work and again before persistence.

### Phase 2 validation

All tests used synthetic/local data, mocked Gmail/AI providers, local SQLite,
or an isolated temporary PostgreSQL cluster. No live Gmail mailbox, customer
content, paid provider, or production system was accessed.

- Complete backend SQLite suite: `1,429/1,429` passed in 633.879 seconds;
  `26` PostgreSQL-only cases were skipped as designed.
- Local PostgreSQL 17.5 `READ COMMITTED` migration compatibility lane: `3/3`
  passed. The exact concurrency command passed `25/25`, including all three
  Gmail analysis-job races, under 5-second lock and 30-second statement limits.
- Exact PostgreSQL 17.10 was unavailable locally. Protected CI is pinned to
  `postgres:17.10-alpine`, asserts server version `170010` and `READ COMMITTED`,
  and remains a release gate after the draft PR is opened.
- Complete frontend suite: `320/320` passed across 20 suites on Node 20.20.2.
- Frontend production build: compiled successfully.
- Django system check: no issues. Migration drift: none. Python dependency
  integrity: no broken requirements.
- Frontend critical dependency audit: passed with zero Critical advisories.
  The full audit reports 11 High, 7 Moderate, and 9 Low advisories, all High
  paths covered by the repository's time-bounded FE-EX-002--005 exceptions.
- Repository diff/whitespace checks: clean. The existing untracked `output/`
  directory remained unchanged.

### Phase 2 migration and rollback

Deploy migration `0041` before enabling either web enqueueing or a worker. Old
application code safely ignores the additive table. New code can run before
the migration only while background analysis is disabled. For rollback, first
disable enqueueing on every web process, drain or stop workers, and normally
retain `0041`; reversing it destroys the job ledger and requires an explicit
operator decision. Disabling unified workspace restores the previous Gmail
review plus QuotationEditor route, disabling parallel reads restores sequential
retrieval, and disabling background analysis restores synchronous analysis.

### Phase 2 safety confirmation

Phase 2 did not change AI models, prompts, OAuth scopes, verified Gmail
recipient/thread handling, immutable outbound snapshots, send idempotency,
reconciliation, selling-price defaults, Product/company approval rules, or
row-evidence requirements. It did not send email, deploy, or alter Railway,
Neon, Gmail, Cloudinary, production credentials, or customer content.

## Phase 3 checkpoint

Pending. Phase 3 remains shadow-only and disabled by default.
