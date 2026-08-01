# Quotation System Operations Runbook

| Field | Value |
|---|---|
| Document version | 1.8.0 |
| Status | Initial current-state runbook; unresolved items are marked explicitly |
| Owner | Assign a primary and backup production operator |
| Last verified | 2026-08-01 |
| Reviewed code | Production baseline `70d3da7`; Task 2.8 pre-remediation checkpoint `7bc7054`; release remediation reviewed through `7a29096123c09879579e8215d409a00cc23465e6` |
| Production snapshot | Railway deployment `c234c4bc-ba7e-4ed0-ab88-b5a1dcc2a6b8`, commit `70d3da7162b63864e479e9a1998aa138046c2433` |

This runbook preserves employee review, blank selling prices, suggestion-only
matching, evidence, preview-before-send, verified reply headers, one successful
send per revision, ambiguous-send lockout, and reconciliation that never sends email.
Use [RELEASE_CONFIGURATION_PACK.md](RELEASE_CONFIGURATION_PACK.md) for the
setting-by-setting staging and production gates; that pack records no live
configuration as complete.

## 1. Current operational truth

| Area | Verified state on 2026-08-01 | Gap/action |
|---|---|---|
| Backend | Railway, one replica in Singapore, runtime V2/Railpack | no configured health check |
| Code | production at `70d3da7`; technical hardening through Task 2.8 on its branch | branch work is not deployed |
| Database | PostgreSQL 17.10, `READ COMMITTED`; no unapplied migrations at inspection | backup/RPO/RTO and production lock/statement timeouts not verified |
| Migration gate | no Railway pre-deploy command in inspected live manifest; guarded repository config is prepared | explicitly activate `/backend/railway.json` and verify a direct migration credential before any release |
| Private evidence | dedicated alias and dual reader implemented; local default path; no Railway volume/provider | application-ready but still ephemeral and not a reliable backup |
| Gmail | consumer mailbox `pharmacydxb@gmail.com`; add-on enabled in observed env | deployment/publication/verification and ownership records not in repo |
| AI | observed OpenAI provider and moving `gpt-5.4` aliases | exact snapshot/provider policy must be recorded per evaluation |
| Error monitoring | Sentry integration exists | `SENTRY_DSN` not configured in snapshot |
| Async work | no Redis/background worker | Gmail analysis is synchronous with resumable status/polling |
| Retention | handoff tokens expire; no general scheduled purge | approve retention/deletion policy |

Do not infer current provider configuration from this table after its date.

### Privacy-safe Gmail employee-funnel metrics

The `QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED` feature flag defaults to `0`.
When enabled after migration `quotations.0039_gmailworkflowmetric`, it records
content-free workflow events in an additive PostgreSQL table. Each row is
bound internally to its Gmail import for funnel/duration analysis; supported
telemetry output deliberately excludes the import ID and every Gmail,
quotation, delivery, user, and handoff identifier.

Only the documented event choices, bounded numeric durations/counts, selection
mode, cache state, Boolean flag state, safe outcome codes, and validated
contract versions are accepted. Never add arbitrary metadata or exception
messages to this channel. In particular, do not add names, addresses, email
addresses, subjects, filenames, Gmail message/thread IDs, item text, document
contents, prices, recipients, tokens, or raw AI output.

Before enabling, assign a retention owner and alert owner, apply `0039`, and
verify the flag-off path first. Monitor event volumes and missing-stage ratios,
not customer-level content. Disable the flag for immediate rollback. The
workflow remains operational if a metric cannot be written, and metric
persistence never changes quotation, pricing, preview, send, or reconciliation
decisions. A database rollback is normally unnecessary; if explicitly
approved, reverse `0039` only after all application instances have the flag
disabled because reversal deletes the metrics table.

### Persisted Gmail company and uncertainty review

`QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED` defaults to `0`. When enabled, the
browser receives a keyed, opaque identity-review fingerprint and the server
records the employee's explicit company acknowledgement. The acknowledgement
survives item wording, quantity, unit, inclusion, and row-review edits, but it
becomes invalid after source selection, analysis generation, company/contact,
sender evidence, or identity evidence changes. An unchanged uncertain row has
a visible `Mark reviewed` action; a substantive correction plus save is also
an explicit review decision.

One-click approval is available only for a current safe recommendation and
always leaves the purchaser blank. Forwarded-only, conflicting, ambiguous, or
missing identities require manual company selection and approval. Monitor 409
stale-review responses and rejected suggestion attempts during rollout. Set
the flag back to `0` for immediate rollback; no database reversal is needed.

### Gmail chained review actions

`QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED` defaults to `0` and is projected as
enabled only when `QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED` is also strictly
enabled. With both flags enabled, chained row-save/create requests carry the
current source fingerprint, analysis attempt, keyed complete reviewed-row
fingerprint, and keyed identity-review fingerprint. Chained quotation-line
saves carry the current quotation review fingerprint. The backend validates
each value while holding the owning record and review-dependency locks; stale
state returns 409 without saving or creating anything.

Monitor stale-response rates and save latency during rollout. A chained action
never finalizes or sends, and a save failure must stop before the existing
secure preview route is called. Set the chained-actions flag to `0` for
immediate rollback. There is no migration and no stored data to reverse.

### Quotation editor progressive loading

`QUOTATION_EDITOR_PROGRESSIVE_LOAD_ENABLED` is disabled by default and is
projected as a strict Boolean in each quotation's existing workflow-feature
object. It can be rolled out and rolled back independently of Gmail review and
chained-action flags. It changes only frontend rendering behavior; quotation
writes, validation, finalization, document generation, and email safety
controls remain unchanged.

Monitor editor load/render errors and time-to-interactive during rollout. Set
the flag to `0` for immediate rollback. No migration or stored-data reversal is
required.

## 2. Roles and access

| Operation | Required identity/control |
|---|---|
| Gmail add-on callback | verified Google system token, exact audience/service account, verified configured mailbox user |
| OAuth callback | signed/time-bounded state tied to an authenticated staff initiation; internal return path |
| Import review | quotation staff; active review belongs to claimant, with defined superuser override |
| Confirm/open quotation | authenticated quotation staff after saved review |
| Replace shared Gmail connection | current credential owner or superuser; first connection is allowed to staff only when none exists |
| Preview/send | quotation staff; server revalidates recipient, source, state, and Gmail authorization |
| Reconcile | quotation staff; operation searches only and never sends |
| Provider/secret changes | named primary or backup platform operator with MFA |

Keep a current list of the Google Cloud owner, Railway owner, database owner,
OpenAI project owner, shared website credential owner, and one successor for
each. This list must live in the organization's secured operational system,
not in the repository.

## 3. Routine health review

### Daily or before a quotation session

- Confirm the frontend and backend load and staff login succeeds.
- Check Railway backend error logs without exporting customer content.
- Check Gmail connection status in quotation settings.
- Review any quotation email in `sending` or `unknown`; never resend it blindly.
- Check recent AI failures/latency and whether provider/model unexpectedly changed.
- Spot-check that new parsed quotation rows have evidence and blank selling prices.
- Review attachment hard failures and fidelity warnings; compare warning-only
  rows with the visible customer source rather than clearing warnings blindly.
- Reanalyze any pre-v4 Gmail identity review that displays the matcher-upgrade
  warning; unversioned results are included, and their cleared historical
  company/contact suggestion must not be trusted.
- For forwarded RFQs, verify the original Gmail message and confirm that the
  suggested customer belongs to the forwarded request, not the employee or
  intermediary who forwarded it.

### Weekly

- Review failed imports, repeated reanalysis, cache behavior, and validation failures.
- Look for repeated archive-limit, malformed/encrypted file, parser-fallback,
  PDF xref/object/stream/geometry/output-limit, hidden/formula/merged-cell,
  inline-image/local-render skip, Gmail attachment-metadata overflow,
  truncation, or invalid-image failures by route.
- Review AI timing/token aggregates by route, provider, model, pipeline, schema,
  prompt/contract hash, and success. Do not group unlike contracts together.
- Check database capacity/connections, Railway memory/CPU/restarts, Gmail API
  errors, and OpenAI spend/budget alerts in their provider consoles.
- Check private-source missing-file reports until durable storage exists.
- Review recurring ambiguous/duplicate `From`, cross-domain `Reply-To`, invalid
  IDN/domain, and truncated-forward warnings. These are review signals, not
  permission to infer a customer from the embedded forwarded headers.
- Treat company-name/acronym-to-domain and same-domain/different-sender matches
  as review suggestions. Automatic identity requires an exact saved sender,
  exact quotation reference, or customer identity in the selected PO
  attachment. Never override an automatic-match
  blocker manually without checking the source document.
- Do not retry a frozen failed Gmail reply that reports
  `gmail_reply_source_reverification_required`; it predates the current sender
  validation contract. Create and review a quotation revision instead.
- Review staff/superuser access and the shared credential owner's status.

### Before every release

Use [DEPLOYMENT.md](DEPLOYMENT.md). Record exact commit, deployment IDs,
migration plan, backup/recovery point, operator, UTC time, and smoke-test result.
Against the dated production baseline, the full hardening branch is expected to
apply `quotations.0035`, `0036`, `0037`, and `0038`; verify the current live
plan rather than assuming that snapshot is unchanged. Task 2.8 adds no Django
migration.
Verify that outcome-PO inspection metadata survives a parse/reload smoke test
and retain all Task 2.2 delivery evidence tables.

## 4. AI intake monitoring

Reviewed-branch `AIParseLog.usage` contains a content-free
`ai_parse_observability_v1` envelope for manual, Gmail, and mailbox-PO AI
routes. Compare:

- provider and configured model value; current logs do not retain the exact
  model snapshot returned by the provider;
- route, source shape, cache hit, success and validation outcome;
- preparation, provider, validation, total AI, and Gmail route-stage durations;
- numeric input/cached-input/output/reasoning token counts;
- pipeline/schema versions and prompt/schema/contract hashes.

Do not treat one slow import as a population percentile. Establish alert/SLO
thresholds only after representative data is collected. No dashboard, SLO,
alert threshold, or cost budget is currently encoded in the repository.

When analysis appears stuck:

1. Confirm the import ID/status without copying email content into logs/chat.
2. Check whether the request is still within the native AI timeout (default
   180 seconds) and Gunicorn timeout (default 300 seconds).
3. Reload the review: polling resumes visibility but does not start a separate
   worker.
4. If status is failed, use the explicit review retry. If selection changed,
   reanalysis must use the new fingerprint; do not merge stale output.
5. If provider/API availability is uncertain, preserve the failure and usage
   log before retrying.

## 5. Failure and recovery matrix

| Failure | System behavior | Operator action | Automatic resend? |
|---|---|---|---|
| AI timeout/provider failure | import fails or remains reviewable with errors | inspect sanitized log; explicit reanalyze after service recovers | not applicable |
| Malformed/unsupported AI output | validation rejects/flags rows | do not bypass; correct source/selection or review manually | not applicable |
| Attachment hard inspection failure | manual/historical upload is rejected before new source storage; Gmail records the rejection and blocks the selected provider call | retain the canonical customer source, inspect the sanitized reason, and request a valid/smaller/exported copy; never rename or bypass it | not applicable |
| Attachment fidelity warning | parsing may continue with bounded warning/safety/fidelity metadata | compare formulas/cached values, hidden or merged content, visible-sheet limits, duplicate rows, PDF forms, and source evidence before saving review | not applicable |
| Invalid product/branding image | upload is rejected before persistence | verify PNG/JPEG/WebP bytes, size, dimensions, and single-frame format; obtain a valid export | not applicable |
| Selection changed during analysis | stale response rejected | reopen and analyze current selection | not applicable |
| Handoff expired | claim rejected | return to Gmail and create a new handoff | not applicable |
| Database lock wait/failure | request fails or waits within DB behavior | inspect logs; retry only the non-send operation after state refresh | never infer email status |
| PDF generation definite failure | quotation may be finalized; delivery not sent | fix cause and use reviewed allowed path | only if server marks retryable and hash rules pass |
| Gmail definite failure before acceptance | delivery may be retryable with an exact frozen snapshot | reopen/review the frozen preview; do not edit it | only explicit byte-identical retry |
| Gmail timeout/429/5xx/network ambiguity | delivery becomes/remains `unknown` | reconcile and inspect Sent | **No** |
| Process crash after snapshot/attempt commit | state may be stale `sending` with a durable attempt but no result event, whether or not Gmail was reached | use no-send reconciliation | **No** |
| Reconciliation API unavailable | distinct non-retryable unavailable result | wait, then reconcile later; inspect Sent | **No** |
| Successful reconciliation search, no verified match | genuine not-found, delivery remains locked | wait for Gmail consistency and inspect Sent | **No** |
| Multiple/malformed reconciliation candidates | conflict/provenance error | escalate; compare exact RFC ID/From/thread/SENT evidence | **No** |
| Frozen snapshot mismatch/corruption | retry blocked before Gmail | preserve hashes/attempt history; create a reviewed revision or escalate corruption | **No** |
| Private evidence missing | authenticated open fails/reference is stale | recover exact hash-matched canonical source; audit action | not applicable |

### Attachment rejection and fidelity-warning procedure

1. Identify the route: manual inquiry/LPO/proforma, Gmail native, price
   reference, historical PDF, or product/branding image.
2. For a hard failure, preserve only the sanitized reason, source digest or
   evidence reference already recorded by the application. Do not copy the
   customer file into logs, tickets, or chat, and do not bypass inspection.
3. In Gmail, a shared hard inspection failure intentionally prevents the AI
   provider call for the entire selected source set. Remove or replace a source
   only through the normal employee selection/review flow, then reanalyze.
4. For warning-only results, open the canonical source and compare every
   affected value. Formula caches, hidden/merged cells, external links,
   truncated rows/columns/sheets, PDF forms, and cross-sheet duplicates require
   particular attention.
5. Confirm that inquiry selling prices remain blank and that source/customer
   prices are evidence only. Price-reference application is a separate,
   explicit employee action.
6. Record route, UTC time, sanitized validation class, configured limit names,
   employee decision, and retry outcome. Do not describe a passed inspection as
   malware-free.

There is no malware/AV scanner and no parser sandbox. Legacy `.xls` and binary
`.xlsb` inspection is limited. Configuration and rollback details are in
[ATTACHMENT_SECURITY_AND_SPREADSHEET_FIDELITY.md](ATTACHMENT_SECURITY_AND_SPREADSHEET_FIDELITY.md).

## 6. Ambiguous Gmail delivery procedure

1. Preserve the quotation number, revision, delivery status, stable outbound
   RFC Message-ID, snapshot digest, provider-attempt sequence/correlation ID,
   UTC time, and sanitized error. Do not disclose OAuth tokens or raw MIME.
2. Do not use send again and do not construct a manual duplicate.
3. Run **Check Gmail status**. Reconciliation must:
   - search by stable RFC Message-ID and exact connected mailbox;
   - fetch and verify `SENT`, exact `From`, exact RFC Message-ID, and thread;
   - require the expected Gmail thread for a reply;
   - never call the send endpoint.
4. If Gmail is unavailable, wait and try reconciliation later.
5. If a successful search finds no verified message, check Sent manually and
   allow for Gmail indexing delay. Keep the record locked.
6. If multiple verified matches appear, escalate as a potential duplicate-send
   incident. Do not choose by subject or timestamp alone.

There is no periodic stuck-delivery sweeper or alert. Recovery is initiated by
staff or a later request; this remains a known operational gap.

Outbound snapshot, provider-attempt, and attempt-event records are view-only in Django admin.
The raw MIME itself is deliberately omitted from the admin form and every API
response. A privileged database investigation may verify digest/size and
relationships but must not export customer MIME into tickets or chat. Legacy
sent/unknown deliveries may have no Task-2.2 snapshot or child attempt; do not
fabricate one during incident review.

## 7. Database migrations and timeouts

The inspected Railway manifest had no pre-deploy command. The Procfile now has
no release entry or alternative migration path. Task 2.8 prepares
`/backend/railway.json` with `python run_deploy_migrations.py`, but it does not
activate that config, add a Railway variable, run a production migration, or
deploy. Before a migration release, follow [DEPLOYMENT.md](DEPLOYMENT.md), set
Railway's Config File Path to `/backend/railway.json`, and verify that the
deployment preview shows the guarded command. A green old deployment and zero
unapplied migrations on 2026-08-01 do not prove future migrations will run.
Once activated, the hook runs before every backend deploy, including code-only
deploys and rollbacks; missing or invalid guarded configuration blocks them.

Current application defaults include:

- connection lifetime: 60 seconds;
- connection health checks: enabled;
- connect timeout: 8 seconds;
- server-side cursors: disabled;
- pooled runtime lock timeout: not configured;
- pooled runtime statement timeout: not configured;
- prepared direct migration connect timeout: 8 seconds by default, allowed
  range 1-60 seconds;
- prepared direct migration lock timeout: 10 seconds by default;
- prepared direct migration lock range: 1-300,000 ms;
- prepared direct migration statement timeout: 15 minutes by default, allowed
  range 1-3,600,000 ms and never below the lock timeout.

The migration runner fails closed unless `MIGRATION_DATABASE_URL` is a direct
PostgreSQL URL for the same host lineage, port, and database as
`DATABASE_URL`, with encrypted TLS for a non-local host (`verify-full` is
preferred where supported). It removes inherited libpq `PG*` target/security
variables, then sets only guarded `PGOPTIONS`, applies bounded
migration-process timeouts, disables persistent migration connections, forces
the intended Django settings module, acquires a bounded PostgreSQL advisory
lock across the migration child, and propagates failure so the new deployment
cannot proceed. Do not point it at the pooled URL and do not add these startup
options to the normal Neon pooled web connection.

Railway exposes service variables to build, pre-deploy, and the running web
container. The application only consumes `MIGRATION_DATABASE_URL` in the
runner, but build dependencies and web code can still read it. Seal it to
prevent dashboard/API retrieval, exclude it from all logs/screenshots/tickets,
and do not give it more privilege than the application role without an explicit
risk decision or an isolated migration service.

The advisory lock covers only `run_deploy_migrations.py`. Never run raw
`python manage.py migrate`, a Procfile migration hook, or another concurrent
manual migration against the same database; those bypass the lock. Stop and
review any deployment path that does not use the guarded runner. Database
statement timeout does not bound arbitrary non-database Python in a migration;
review `RunPython` work and monitor the pre-deploy duration.

For an API response with code `database_request_interrupted` (HTTP 503):

1. Do not blindly repeat the mutation and do not treat the response as proof
   that it did or did not commit an external side effect.
2. Refresh and inspect the current quotation, line, preview, and delivery state.
3. If email delivery is `sending` or `unknown`, keep sends blocked and use only
   the reconciliation operation, which never sends.
4. Check the privacy-safe log event for SQLSTATE `55P03`, `40P01`, or `57014`,
   database locks/provider metrics, and the request path/view. The client and
   log event intentionally omit SQL and exception text.
5. Resolve the lock, deadlock, cancellation, or migration issue, then choose a
   business-state-aware next action. There is no `Retry-After` promise or broad
   application retry.

Other database failures remain generic 500 errors and require normal incident
diagnosis. The 503 normalization handles exact interruptions; it does not
create a runtime lock/statement timeout or prove that a database outage is
transient.

## 8. Backup, restore, and private evidence

RPO, RTO, database restore retention, independent backups, and latest restore
drill are **not verified**. Provider plan limits change; record them from the
current console rather than this repository.

An approved plan must cover:

- PostgreSQL and a pre-migration recovery point;
- exact outbound MIME snapshots and immutable provider-attempt history;
- durable private quotation evidence after approved provider/volume activation;
- Cloudinary business media if it cannot be recreated;
- configuration inventory and ownership, without plain-text secret exports;
- restore drills that verify quotation/audit/delivery relationships.

Task 2.3 provides a dedicated `quotation_evidence` storage alias, opaque
content-addressed keys, SHA-256 verification, and dual-read support. It does not
make the default backend durable: `QUOTATION_PRIVATE_STORAGE_ROOT` still points
to a local path and the inspected Railway service has no volume. Treat manual
source files as ephemeral until an approved backend or volume, backup, restore,
and legacy-copy procedure are configured and verified. Gmail remains canonical
for Gmail inquiry and mailbox-PO documents, but that does not recover unrelated
manual uploads or the contract-intelligence attachment copies retained locally.

For a future storage cutover:

1. pause or closely control new source uploads;
2. inventory every safe `source_file_ref` and surviving local object, including
   both `inquiry_sources/v1/...` and unversioned keys, without exporting
   customer content into logs;
3. copy every object to the new backend under the same relative key and verify
   its embedded/recorded SHA-256 where available;
4. configure the backend/options and verify that new versioned writes/readbacks
   succeed while old refs remain readable;
5. exercise a restore and retain the legacy root until the acceptance window
   closes.

Do not roll application code back below the dual reader after remote-only
versioned objects are written. Prefer a configuration rollback or forward fix;
otherwise pause source-dependent operations and first restore exact verified
objects to the legacy root.

## 9. Retention and privacy operations

There is no approved application-wide retention/deletion schedule for
structured imports, evidence excerpts, AI caches/logs, message identifiers,
mailbox inventory, audit logs, delivery records, or local source files. Do not
run bulk deletion as routine cleanup without approved legal/business rules,
referential-impact analysis, backup, and explicit authority.

Define at minimum:

- data owner and purpose per category;
- minimum/maximum retention and legal holds;
- deletion/anonymization behavior and audit proof;
- provider-side retention/residency commitments;
- legacy AI-cache treatment;
- access-review and incident-disclosure procedure.

## 10. Credential rotation and ownership transfer

For normal OAuth reconnect, use the website workflow; do not paste refresh
tokens into Railway consoles or tickets. When rotating `DJANGO_SECRET_KEY`,
schedule downtime/coordination, expect stored Gmail token decryption to fail,
and reconnect the shared mailbox afterward.

Do not delete the website credential-owner account until ownership has been
transferred and verified. Task 2.7 migration `0037` changes this relationship
to `PROTECT`, so an attempted deletion of the current owner is rejected instead
of deleting the Gmail connection or its mailbox provenance. Treat that
rejection as a succession warning, not as a reason to bypass protection. The
designated-mailbox enforcement itself remains disabled by default:

1. Verify the Railway value of `GMAIL_ADDON_SHARED_MAILBOX_EMAIL` against the
   Google profile of the currently connected physical mailbox. Do not copy a
   value from documentation or guess it.
2. Set `QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=1` only after
   that verification. A missing/invalid expected address, wrong OAuth profile,
   or mismatched stored shared connection then fails closed.
3. Before an owner leaves, run `transfer_shared_gmail_owner` without `--apply`
   using an active superuser for audit attribution, an active staff successor,
   and the exact configured mailbox confirmation. The successor must not own a
   different Gmail connection.
4. Review the token-free dry-run output, rerun with `--apply`, then verify that
   shared Gmail status, inquiry intake, read-only discovery, reviewed email
   preview, and Gmail evidence still resolve through the same connection ID.
5. Only then delete/deactivate the former owner. Preserve the resulting
   `QuotationAuditLog` record. The audit payload retains the initiating
   superuser ID/username even if that user is later deleted. For rollback,
   dry-run and apply the same command in reverse while the former owner is
   still active staff and conflict-free.

Example (the first invocation is read-only):

```text
python manage.py transfer_shared_gmail_owner --initiated-by admin_username --new-owner successor_username --confirm-mailbox exact-shared-mailbox@example.com
python manage.py transfer_shared_gmail_owner --initiated-by admin_username --new-owner successor_username --confirm-mailbox exact-shared-mailbox@example.com --apply
```

The command relies on trusted administrative shell access. The
`--initiated-by` username is validated and audited, but does not authenticate
the person at the shell. Apply migration `0037` as part of the normal release;
it rewrites no stored Gmail data and changes only Django's owner-deletion
policy. If validation fails, change no rows; correct the configuration or user
state and rerun the dry run. Reversing `0037` restores unsafe cascade behavior,
so prefer a forward fix and keep the protective migration applied.

## 11. Production evidence record template

```text
UTC time:
Operator / reviewer:
Code commit:
Railway backend/frontend deployment IDs:
Database engine/version/isolation:
Migration plan and pre-deploy result:
Backup/recovery-point ID and restore window:
Gmail mailbox and OAuth/add-on status (no tokens):
AI provider/model exact value and contract hashes (no content):
Private evidence storage/volume status:
Health check, monitoring and alerts:
Smoke-test result:
Known deviations and rollback owner:
```

## 12. References

- [Architecture reference](GMAIL_QUOTATION_ARCHITECTURE_REVIEW.md)
- [Deployment guide](DEPLOYMENT.md)
- [Security guide](SECURITY.md)
- [Technical hardening progress](TECHNICAL_HARDENING_PROGRESS.md)
- [Attachment security and spreadsheet fidelity](ATTACHMENT_SECURITY_AND_SPREADSHEET_FIDELITY.md)
- [Railway pre-deploy commands](https://docs.railway.com/deployments/pre-deploy-command)
- [Railway service variables](https://docs.railway.com/variables)
- [Railway volumes](https://docs.railway.com/volumes/reference)
