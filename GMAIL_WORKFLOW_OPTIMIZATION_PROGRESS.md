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

Pending.

## Phase 3 checkpoint

Pending. Phase 3 remains shadow-only and disabled by default.
