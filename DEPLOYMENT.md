# Al Ameen Pharmacy Deployment Guide

| Field | Value |
|---|---|
| Document version | 2.6.0 |
| Status | Operator guide; live values require independent verification |
| Owner | Al Ameen platform maintainers |
| Last verified | 2026-08-01 |
| Reviewed code | `70d3da7` production baseline; Task 2.8 pre-remediation checkpoint `7bc7054`; release remediation reviewed through `7a29096123c09879579e8215d409a00cc23465e6` |
| Production snapshot | Railway deployment `c234c4bc-ba7e-4ed0-ab88-b5a1dcc2a6b8`, commit `70d3da7162b63864e479e9a1998aa138046c2433` |

This guide separates repository behavior from live provider configuration. A
checked application test does not prove a Railway, Neon, Google, Cloudinary,
or OpenAI console setting. Record the operator, UTC time, and provider evidence
when completing any production checklist.

See [OPERATIONS.md](OPERATIONS.md) for monitoring, backup, incident, and
recovery procedures; [SECURITY.md](SECURITY.md) for security controls;
[RELEASE_CONFIGURATION_PACK.md](RELEASE_CONFIGURATION_PACK.md) for the exact
external release gates and operator evidence record;
[FRONTEND_DEPENDENCY_SECURITY.md](FRONTEND_DEPENDENCY_SECURITY.md) for the
time-bounded frontend audit decision;
[ATTACHMENT_SECURITY_AND_SPREADSHEET_FIDELITY.md](ATTACHMENT_SECURITY_AND_SPREADSHEET_FIDELITY.md)
for Task 2.4 attachment limits; and
[gmail_addon/README.md](gmail_addon/README.md) for the Gmail add-on.

## 1. Repository deployment shape

The intended hosted shape is:

- a Django/DRF backend rooted at `/backend`;
- a React SPA rooted at `/frontend`;
- PostgreSQL for production data;
- Cloudinary for configured Django media fields;
- a separate private quotation-source filesystem path;
- Gmail OAuth/API and an optional HTTP Gmail add-on;
- an optional configured AI provider.

The repository defaults to SQLite when `DATABASE_URL` is absent, including
when `DEBUG=0`. Production operators must therefore verify `DATABASE_URL`
explicitly; the code does not fail closed to PostgreSQL.

### Read-only production snapshot on 2026-08-01

| Setting | Observed value |
|---|---|
| Backend service | `al-ameen-pharmacy` in Railway production environment |
| Backend public domain | `al-ameen-pharmacy-production.up.railway.app` |
| Runtime/build | Railway runtime V2/Railpack; Python 3.12.8 |
| Root/build command | `/backend`; `python manage.py collectstatic --noinput` |
| Pre-deploy command | none configured in the inspected deployment manifest |
| Health check | none configured |
| Replica | one, Singapore |
| Persistent volume | none |
| Database | PostgreSQL 17.10; default isolation `READ COMMITTED` |
| Migration plan | zero unapplied migrations at the inspection time |
| Application error reporting | Sentry support exists; `SENTRY_DSN` was not configured |

This is a dated snapshot, not a guarantee about the next deployment.

## 2. Prerequisites

- Access to the correct GitHub repository and protected deployment branch.
- Railway project access for backend and frontend.
- A production PostgreSQL database and independently verified backup plan.
- Cloudinary credentials if catalog/branding media are required.
- Gmail SMTP or Gmail OAuth credentials for the enabled email workflows.
- Google/OpenAI credentials only for features the organization has approved.
- Named primary and backup operators for database, Google, Railway, and secrets.

Do not copy a production `DATABASE_URL` into a local `backend/.env`. Use SQLite,
local PostgreSQL, or a separate development database.

## 3. Pre-release checks

Run from a clean checkout of the exact commit to be deployed:

```text
cd backend
python -m pip install -r requirements.txt
python manage.py makemigrations --check --dry-run
python manage.py check
python manage.py test --keepdb --noinput

cd ../frontend
npm ci
npm run test:ci
npm run build
npm audit --omit=dev
```

The frontend audit must satisfy the release rules in
[FRONTEND_DEPENDENCY_SECURITY.md](FRONTEND_DEPENDENCY_SECURITY.md): zero
Critical findings, no unlisted High path, and no expired exception. Never use
`npm audit fix --force` as a release shortcut.

The canonical production-equivalent quotation-concurrency lane is
`postgres-concurrency` in `.github/workflows/ci.yml`. It uses PostgreSQL 17.10,
`READ COMMITTED`, `lock_timeout=5s`, and `statement_timeout=30s`. Those two
timeout limits are CI settings. The prepared migration runner uses separate
migration-only defaults; neither setting is added to the pooled web connection.

Before release, save:

- commit SHA and CI run URL;
- migration plan output;
- database snapshot/restore point appropriate to the change;
- approved rollback decision, including whether the migration is reversible;
- expected environment-variable diff with values redacted.

Task 2.5 adds a direct `idna==3.11` runtime pin for matching-only email-domain
normalization. It has no migration, environment-variable, OAuth, AI model,
prompt, schema, or infrastructure change. After installing requirements, run a
Gmail forward/identity smoke test and reanalyze any unconfirmed review that
shows the pre-v4 matcher warning, including unversioned records; never trust its
cleared old suggestion. Also verify that duplicate/multi-address `From` headers
cannot prepare a Gmail reply, reconcile a Sent message, or create an automatic
mailbox-LPO match. Confirm that a same-domain but different sender remains
review-only, and that a legacy frozen Gmail reply without the current sender
validation contract is blocked before any provider call.

## 4. Backend service

Set Railway root directory to `/backend`. The current `backend/Procfile`
declares:

```text
web: gunicorn pharmacy_api.wsgi --timeout ${GUNICORN_TIMEOUT:-300} --workers ${WEB_CONCURRENCY:-2} --threads ${GUNICORN_THREADS:-2} --log-file -
```

The Procfile deliberately has no release entry or migration path; the observed
Railway build command already collects static files. The inspected live
deployment had no explicit Railway pre-deploy command. Railway's documented
migration hook is a **Pre-Deploy Command**, and a failing pre-deploy command
prevents the new deployment from proceeding.

Task 2.8 adds the repository configuration
[`backend/railway.json`](backend/railway.json), whose pre-deploy command is:

```text
python run_deploy_migrations.py
```

This is prepared and tested, but **not activated or deployed**. Because the
service is in a monorepo, an operator must explicitly select Config File Path
`/backend/railway.json` in Railway and verify the deployment preview before a
release. Once selected, Railway runs the hook before every backend deployment,
including a code-only deploy or rollback; a missing/invalid migration variable
therefore blocks that deployment. The guarded runner:

- requires `MIGRATION_DATABASE_URL` to be a direct/unpooled PostgreSQL URL;
- requires it to identify the same database, port, and recognized host lineage
  as the application `DATABASE_URL`;
- rejects URL options that could override the validated database target;
- requires encrypted TLS (`require`, `verify-ca`, or `verify-full`) for every
  non-local migration host; prefer `verify-full` where the provider supports
  it and independently verify the provider certificate requirements;
- removes inherited libpq `PG*` variables that could redirect or weaken the
  validated connection, then sets only guarded migration `PGOPTIONS`;
- defaults connect timeout to 8 seconds (allowed range 1-60 seconds), lock
  timeout to 10,000 ms (allowed range 1-300,000 ms), and statement timeout to
  900,000 ms (allowed range 1-3,600,000 ms and never below lock timeout);
- acquires one PostgreSQL advisory lock under the bounded lock timeout and
  holds it across the migration child, preventing concurrent guarded runners
  against the same database cluster;
- disables persistent connections for the migration child process; and
- returns a nonzero status for invalid configuration, process-start failure,
  or any failed Django migration, so promotion stops.

The PostgreSQL statement timeout bounds database statements, not arbitrary
non-database Python inside a `RunPython` migration. Review such migrations in
advance and monitor the Railway pre-deploy duration; do not treat the database
timeout as a total wall-clock guarantee.

Only the runner consumes `MIGRATION_DATABASE_URL` in application code, but a
Railway service variable is available to build, pre-deploy, and the running web
container. Mark it sealed in Railway and never record it in logs, screenshots,
tickets, source, command arguments, or the frontend. Sealing prevents
dashboard/API retrieval; it does not isolate the secret from build dependencies
or the web process. Prefer the existing least-privileged application role
through its direct endpoint. Do not introduce a more privileged schema
credential into the shared service environment without explicit risk
acceptance; use a separately isolated migration service if that separation is
required. Neon pooled startup options are intentionally not changed.
`collectstatic` remains the independent Railway build command observed above;
the migration pre-deploy command does not replace it.

The advisory lock coordinates only `run_deploy_migrations.py`. Do not run raw
`python manage.py migrate`, a Procfile migration hook, or a concurrent manual
migration against the same database; those bypass the guard. Every authorized
migration path must use the guarded runner or a separately reviewed migration
service with equivalent serialization.

Before a release containing migrations, an operator must verify in Railway:

1. Config File Path is exactly `/backend/railway.json` and the deployment
   preview shows `python run_deploy_migrations.py` as the pre-deploy command;
2. `MIGRATION_DATABASE_URL` is an independently verified direct endpoint for
   the same intended database; it is sealed and is not recorded in logs,
   screenshots, tickets, or release evidence;
3. the target build contains the reviewed migrations and they are compatible
   with the still-running code;
4. the migration plan was reviewed and a database recovery point exists;
5. timeout overrides, if any, are justified and remain inside the runner's
   safety bounds;
6. static collection/build behavior is independently configured.

Migration success is not automatically reversible. Rolling the application
image back does not undo schema changes or data mutations.

Against the dated production baseline, a full hardening-branch promotion is
expected to include additive/state migrations `quotations.0035`, `0036`,
`0037`, and `0038`; verify the live plan independently because production may
have changed.
Task 2.8 itself adds no Django migration and does not run any production
migration.

### Required environment groups

Use [backend/.env.example](backend/.env.example) as the canonical variable
inventory. Never commit filled values. At minimum verify these groups:

- Django: `DJANGO_SECRET_KEY`, `DEBUG=0`, `ALLOWED_HOSTS`, frontend/CORS/CSRF URLs;
- database: `DATABASE_URL`, connection lifetime/health checks, connect timeout,
  disabled server-side cursors, and the runner-consumed but service-visible
  `MIGRATION_DATABASE_URL` plus migration timeout bounds;
- media/private evidence: `CLOUDINARY_URL` where used, plus the separate
  `QUOTATION_EVIDENCE_STORAGE_BACKEND`,
  `QUOTATION_EVIDENCE_STORAGE_OPTIONS_JSON`, and local fallback
  `QUOTATION_PRIVATE_STORAGE_ROOT`, with
  `QUOTATION_PRIVATE_EVIDENCE_MAX_BYTES` as the read/write ceiling;
- email/Gmail: SMTP variables and/or website `GOOGLE_OAUTH_*` settings;
- Gmail add-on: only the variables documented in
  [gmail_addon/README.md](gmail_addon/README.md);
- AI: approved provider/key/model/timeouts and explicit privacy feature flags;
- attachment parsing: upload, PDF page, Excel visible-sheet/row/column,
  OOXML archive entry/expanded-size/member, and price-reference row bounds
  listed below;
- workers: `GUNICORN_TIMEOUT`, `WEB_CONCURRENCY`, `GUNICORN_THREADS`;
- observability: log levels and optional Sentry variables.

The current defaults include an 8-second PostgreSQL connect timeout, a
60-second persistent-connection lifetime with health checks, a 60-second text
AI timeout, a 180-second native Gmail AI timeout, and a 300-second Gunicorn
timeout. Long Gmail analysis is synchronous; browser polling makes its state
resumable but does not create a background worker.

### Gmail workflow metrics (disabled by default)

`QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=0` is the safe repository default.
Migration `quotations.0039_gmailworkflowmetric` adds only a new metrics table;
it does not rewrite Gmail imports, inquiries, quotations, delivery records, or
customer content. Old application code can run after the migration. New code
can run before it only while this flag remains disabled; apply the migration
before any rollout that sets the flag to `1`.

Enabled metrics are linked internally to a `GmailInquiryImport`, but the
supported telemetry envelope omits database IDs and stores only an allow-listed
event, bounded numeric duration/counts, selection mode, cache state, safe
outcome code, feature-flag state, and contract versions. It cannot accept
names, addresses, email addresses, subjects, filenames, Gmail IDs, item text,
document contents, prices, recipients, handoff/OAuth tokens, or raw model
output. There is no public API for these rows.

Rollout requires an approved retention/monitoring owner and a staging check
that the flag-off workflow is unchanged. Roll back by setting the flag to `0`;
no quotation or delivery rollback is required. Prefer retaining the additive
table during an application rollback. Reversing `0039` deletes metrics only
and must happen after every running application instance has the flag disabled.

### Gmail review UI V2 (disabled by default)

`QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=0` preserves the existing review UI and
confirmation contract. Enabling it exposes a server-owned workflow flag and
requires staff to persist an evidence-bound company acknowledgement before a
new Gmail inquiry can create a quotation. It also permits an explicit
`reviewed` decision for an unchanged uncertain row. Suggested-company approval
never selects a purchaser, and forwarded-only, missing, stale, ambiguous, or
conflicting suggestions cannot use the one-click approval path.

No migration is required: the bounded approval audit is stored in the existing
Gmail import analysis JSON. Roll out after the application code is deployed,
then enable for a controlled mailbox cohort. Roll back by setting the flag to
`0`; legacy company confirmation and full-row review behavior remain intact.
Disabling the flag does not delete approval audit data and does not change any
Inquiry, Quotation, Product, price, preview, or delivery record.

### Gmail chained review actions (disabled by default)

`QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED=0` preserves the separate Gmail row
save, confirmation, quotation-line save, and email-preview requests. The
server projects this feature as enabled only when both this setting and
`QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED` are strictly enabled; a partial rollout
fails closed.

When enabled, the optional chained Gmail save/create contract binds both the
row save and confirmation to the current source fingerprint, analysis attempt,
keyed complete reviewed-row fingerprint, and keyed identity-review fingerprint
while holding the Gmail import lock. A
stale tuple returns HTTP 409 and performs no mutation. The optional quotation
line-save contract accepts the current `quotation_review_fingerprint`, checks
it after locking the quotation and all review dependencies, and serializes the
new quotation fingerprint before releasing those locks. Neither path
finalizes a quotation, opens a preview after a failed save, or sends email.

No migration is required. Roll out review UI V2 first, then enable chained
actions for a controlled cohort. Roll back by setting the chained-actions flag
to `0`; legacy requests that omit the optional stale bindings retain their
existing behavior and no database reversal is needed.

### Quotation editor progressive loading (disabled by default)

`QUOTATION_EDITOR_PROGRESSIVE_LOAD_ENABLED=0` preserves the existing quotation
editor rendering path. When strictly enabled, the server projects
`quotation_editor_progressive_load=true` in the existing quotation workflow
feature object so the editor can progressively reveal already-fetched content.
The flag does not add an API endpoint or change quotation data, validation,
pricing, PDF output, finalization, preview, or email delivery behavior.

This flag is independent of both Gmail review feature flags. No migration is
required. Roll back immediately by setting it to `0`; there is no stored data
or schema change to reverse.

### Content-free Gmail analysis progress (disabled by default)

`QUOTATION_GMAIL_ANALYSIS_PROGRESS_ENABLED=0` preserves the synchronous Gmail
analysis response and existing review workflow without progress polling. When
strictly enabled, import retrieval adds the allow-listed
`gmail_analysis_progress_v1` projection and the existing import API exposes
`GET /gmail-inquiry-imports/{id}/analysis_progress/`. The detail action uses
the import's existing staff access policy and sends `private, no-store` cache
control. It contains only state, a bounded stage, attempt number, opaque random
source generation, safe failure category, timestamps, and retryability. It
never contains Gmail identifiers, sender/recipient addresses, subjects,
filenames, customer/item text, document content, prices, tokens, or counts.

Migration `quotations.0040_gmailinquiryimport_analysis_progress` adds five
dedicated progress fields with empty/zero/null defaults and performs no
customer-content backfill. Apply `0040` before promoting the new application;
old application code can run after the additive migration, while new model
queries are not safe before the columns exist. Deploy with the flag still at
`0`, verify synchronous analysis, then enable it for a controlled cohort.

Rollback the feature first by setting the flag to `0`; synchronous analysis
remains the fallback and no background worker is introduced by this phase.
Prefer a schema-compatible application rollback that retains `0040`. Reversing
`0040` removes only progress metadata but should be considered only before a
newer application instance can query the fields and after active analyses have
finished.

PostgreSQL lock interruption (`55P03`), deadlock (`40P01`), and query
cancellation/statement timeout (`57014`) are normalized only when Django wraps
the exact driver SQLSTATE. The API returns a generic 503 stating that current
state may be uncertain. It does not expose SQL/database details, set a
`Retry-After` header, or claim that a mutation is safe to retry. Staff must
refresh and verify the quotation and delivery state first; Gmail sends remain
subject to the existing ambiguous-delivery lock and no-send reconciliation.
Other database errors retain the existing generic 500 path.

### Task 2.4 attachment limits

The reviewed repository exposes these document-parser controls in
[backend/.env.example](backend/.env.example):

- `QUOTATION_IMPORT_MAX_UPLOAD_BYTES`;
- `QUOTATION_IMPORT_MAX_EXCEL_ROWS`,
  `QUOTATION_IMPORT_MAX_EXCEL_SHEETS`, and
  `QUOTATION_IMPORT_MAX_EXCEL_COLUMNS`;
- `QUOTATION_IMPORT_MAX_PDF_PAGES`;
- `QUOTATION_IMPORT_MAX_PDF_OBJECTS` and
  `QUOTATION_IMPORT_MAX_PDF_STREAMS`;
- `QUOTATION_IMPORT_MAX_PDF_DECODED_STREAM_BYTES` and
  `QUOTATION_IMPORT_MAX_PDF_TOTAL_DECODED_STREAM_BYTES`;
- `QUOTATION_IMPORT_MAX_PDF_PAGE_DIMENSION_POINTS`,
  `QUOTATION_IMPORT_MAX_PDF_PAGE_AREA_POINTS`,
  `QUOTATION_IMPORT_MAX_PDF_RENDER_PIXELS`, and
  `QUOTATION_IMPORT_MAX_PDF_IMAGE_PIXELS`;
- `QUOTATION_IMPORT_MAX_PDF_TEXT_CHARS_PER_PAGE`,
  `QUOTATION_IMPORT_MAX_PDF_TOTAL_TEXT_CHARS`,
  `QUOTATION_IMPORT_MAX_PDF_WORDS_PER_PAGE`, and
  `QUOTATION_IMPORT_MAX_PDF_TOTAL_WORDS`;
- `QUOTATION_IMPORT_MAX_PDF_TABLE_ROWS` and
  `QUOTATION_IMPORT_MAX_PDF_TABLE_CELLS`;
- `QUOTATION_IMPORT_MAX_ARCHIVE_ENTRIES`,
  `QUOTATION_IMPORT_MAX_ARCHIVE_UNCOMPRESSED_BYTES`, and
  `QUOTATION_IMPORT_MAX_ARCHIVE_MEMBER_BYTES`; and
- `QUOTATION_PRICE_REFERENCE_MAX_EXCEL_ROWS`.

Gmail native AI retains its separate existing file/count/byte/page/row limits.
Product and branding image byte limits are controlled by
`PRODUCT_IMAGE_MAX_UPLOAD_BYTES` and
`QUOTATION_BRANDING_IMAGE_MAX_UPLOAD_BYTES`; fixed decoded-image ceilings are
12,000 pixels on either edge, 25 million total pixels, and one frame.

Task 2.4 requires additive migration
`quotations.0036_quotationoutcomepoimport_parsed_meta` before application-code
promotion. It adds an empty-default JSON field to outcome-PO imports so
structured inspection evidence survives review; it deletes no records and has
no customer-content backfill. The task changes no public endpoint or request
shape. Outcome-PO responses add the read-only `parsed_meta` field, and preview
responses may contain additive validation warnings and bounded attachment
safety/fidelity metadata. Invalid files now fail through the existing
validation-error path. It does not change production/provider configuration,
OAuth scopes, AI models, prompts, or extraction schemas. Before promotion,
review the defaults and any environment overrides against memory/CPU capacity,
then test a normal file, a warning-only fidelity file, and a hard failure on
every enabled route. Passing these checks is not malware/AV proof, and parsers
are not sandboxed.

The corrected, unreleased form of `0036` retains the PostgreSQL/SQLite database
default `{}` as well as the Python `dict` default. This is intentional rolling-
deployment compatibility: a pre-hardening application process does not know
about `parsed_meta` and omits the column from its INSERT, so the database must
supply the empty object until every old process is gone. The new application
continues to create independent Python dictionaries and can read, insert, and
update structured metadata normally. New application code is not safe before
`0036` because the column does not yet exist.

`quotations.0038_ensure_po_import_parsed_meta_db_default` is the forward-only,
idempotent repair for a target that may already have recorded the original
`0036`, whose generated PostgreSQL SQL dropped the temporary default. On
PostgreSQL it performs only `ALTER COLUMN parsed_meta SET DEFAULT '{}'::jsonb`
and does not rewrite rows. A fresh SQLite database already receives the
persistent default in corrected `0036`; if an older SQLite target lacks it,
`0038` uses Django's schema editor to preserve the logical rows while rebuilding
that table. Before promotion, PostgreSQL `sqlmigrate quotations 0036` must show
the JSONB default on the `ADD COLUMN` and no later `DROP DEFAULT`; the migration
plan must also include `0038`.

### Gmail/AI disclosure

When Gmail inquiry analysis and its document privacy gate are enabled, the
application may send bounded newest non-quoted email bodies and original
inbound PDF/XLS/XLSX documents to the configured OpenAI API with `store=false`.
Signature graphics and normal Gmail image attachments are excluded. Manual
image uploads use a separate bounded vision path. Confirm the organization's
processor, retention, residency, and restricted-Gmail-scope obligations before
enabling these features.

## 5. Frontend service

Set Railway root directory to `/frontend` and use:

```text
Build command: npm run build
Start command: npm run serve
```

Set `REACT_APP_API_URL` to the backend `/api` URL. Build-time React variables
are embedded in the bundle; changing one requires a new frontend build.

## 6. Domains and browser security

Copy the exact DNS records Railway and the DNS provider display. Do not assume
that an apex domain supports a CNAME. After domains are active, verify:

- `ALLOWED_HOSTS` contains only intended backend hosts;
- `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS` contain exact HTTPS origins;
- `FRONTEND_URL` is the canonical customer/staff website;
- `REACT_APP_API_URL` points to the intended API;
- HTTPS redirect, HSTS, secure session cookies, and secure CSRF cookies are
  active with `DEBUG=0`.

Certificate and DNS propagation times are provider-dependent; do not use a
fixed completion estimate as a release guarantee.

## 7. Private-source storage and durability warning

Task 2.3 routes quotation evidence through the dedicated
`quotation_evidence` Django storage alias. It never uses the public/default
Cloudinary media storage. New objects use opaque, content-addressed
`inquiry_sources/v1/...` keys, are SHA-256 checked, bounded on read/write, and
retain no customer filename in the key. Existing unversioned references remain
readable from the confined legacy root and, after an operator-controlled copy,
from the active backend under their unchanged keys. After a definite active
backend miss, a versioned key may also use the previous confined local copy,
but only when its bytes match the full digest embedded in the key. A backend
error never permits fallback.

The repository deliberately selects no remote provider. The default
`QUOTATION_EVIDENCE_STORAGE_BACKEND` is still a private local filesystem rooted
at `QUOTATION_PRIVATE_STORAGE_ROOT`. A Railway filesystem without a mounted
volume is ephemeral, and the inspected deployment had no volume. Therefore
application abstraction is implemented but live durability remains unverified.

Before activating an approved durable backend:

1. install and pin its Django storage package in a separate reviewed change;
2. verify private-by-default access, encryption, residency, timeouts, object
   size behavior, stable exact-key synchronous `save/open/exists` semantics,
   and a maximum returned key length of 500;
3. inventory **all** surviving safe references, including versioned and legacy
   keys, and copy each source under the same relative key, verifying the
   embedded/recorded SHA-256 where available before switching backends;
4. configure `QUOTATION_EVIDENCE_STORAGE_BACKEND` and the JSON-object
   `QUOTATION_EVIDENCE_STORAGE_OPTIONS_JSON` without exposing credentials;
5. smoke-test new writes, versioned reads, legacy reads, failure reporting,
   backup, and restore before declaring the source durable.

Until those steps are approved and evidenced:

- do not describe private sources as durable or backed up;
- verify critical evidence against the canonical customer email/document;
- monitor for database references whose file is missing;
- do not mount, purchase, or select storage as part of an unrelated release.

## 8. Deployment and smoke test

After the approved commit and explicit migration gate are configured:

1. Confirm a current database recovery point, review
   `python manage.py showmigrations --plan`, and then deploy the backend.
2. Inspect the guarded pre-deploy, build, and start logs; a missing/mismatched
   direct URL or failed migration must stop promotion.
3. Confirm the deployed commit and deployment ID.
4. Run `python manage.py showmigrations --plan` in the production context and
   confirm no intended migration remains unapplied.
5. Verify API/admin availability and authenticated staff access.
6. Deploy the frontend and verify its embedded API URL.
7. Test a production-safe, non-destructive representative workflow:
   - login and permissions;
   - company/product lookup;
   - manual inquiry review with blank selling prices;
   - representative attachment acceptance, warning visibility, and hard
     rejection without a persisted invalid source;
   - Gmail import review if enabled, without creating duplicates;
   - Gmail native hard-failure behavior in staging/mocks: no provider call and
     a bounded rejection record for the selected source;
   - quotation preview and **Finalize Only**;
   - a controlled email send only to an approved test recipient;
   - ambiguous-send reconciliation in a mocked/staging environment.
8. Record the result, UTC time, operator, commit, deployment IDs, and any
   deviations in the release record.

The application currently has no configured Railway health check in the
inspected deployment. A green deployment alone is therefore not a complete
application smoke test.

## 9. Monitoring and error reporting

Sentry is already installed and conditionally initialized. Configure it with
environment variables rather than one-off `pip install` or uncommitted settings
edits. `send_default_pii` is false and tracing defaults to zero. Also retain
Railway application logs and database/provider metrics according to an
approved retention policy.

As of the snapshot, there are no documented SLOs, alert thresholds, cost
budgets, stuck-delivery sweeper, or configured Sentry DSN. These are operational
gaps, not proof of an outage. Suggested signals are listed in
[OPERATIONS.md](OPERATIONS.md).

## 10. Backup and restore

Provider restore history varies by plan and can change. Do not rely on an
undated free-tier claim. Verify the current database plan and restore window in
the provider console, and maintain an independent policy that covers:

- PostgreSQL data and pre-migration recovery points;
- any durable private evidence store;
- Cloudinary media where business-required;
- secrets/configuration inventory without exporting secret values;
- periodic restore drills;
- named RPO and RTO owners.

Current provider references:

- [Neon pricing and restore-history limits](https://neon.com/pricing)
- [Railway volumes](https://docs.railway.com/volumes/reference)

Backup configuration, most recent successful restore drill, RPO, and RTO were
not verified during the 2026-08-01 read-only audit.

## 11. Rollback

Application rollback and data rollback are separate decisions.

1. Stop promotion and capture logs/evidence.
2. Determine whether the new version sent emails or mutated business data.
3. If code-only and schema-compatible, redeploy the last known-good commit.
4. If migrations ran, follow the reviewed migration-specific recovery plan;
   never assume redeploying old code reverses the schema.
5. If customer email status is `sending` or `unknown`, do not resend. Use the
   no-send reconciliation path and inspect the shared Sent mailbox.
6. Verify API, migrations, data integrity, private evidence, and the exact
   quotation/email state after recovery.

Before activation, do not blindly revert the whole checkpoint: that would
restore the legacy Procfile `manage.py migrate` declaration. Use a reviewed
code rollback that retains the Procfile's web-only state. After Railway selects
the config file, first clear its **Config File Path**
`/backend/railway.json` and confirm the prior deployment remains active; only
then perform a reviewed code rollback. Once any migration runs, do not treat
removing the pre-deploy command or rolling back an image as a schema rollback.
Preserve the migration logs and recovery point, apply the reviewed
migration-specific plan, and prefer a compatible forward fix. Do not switch
migrations back to the pooled application URL as a recovery shortcut.

Destructive migration reversal, data deletion, or production restore requires
explicit authority and is outside an ordinary code rollback.

### Task 2.4 rollback note

Before deployment, revert the Task 2.4 checkpoint as one unit; an unused `0036`
may also be reversed. After any outcome import stores `parsed_meta`, do not
reverse `0036` as an ordinary rollback because doing so deletes retained
inspection evidence. Keep the additive column and use a forward fix or
schema-compatible application rollback. First restore an accidentally tightened
limit to its previous reviewed environment value when that is the cause.
Reverting inspection code and call sites weakens file defenses, so it requires
an explicit security decision and must include their tests and documentation.
Re-run fresh previews after rollback; do not reuse a result produced under a
different inspection contract. No provider, OAuth, AI, or infrastructure
rollback is required because Task 2.4 changes none of them.

`0038` deliberately has a no-op reverse operation: marking it unapplied does
not drop the database default. Do not manually drop that default while any old
application instance can still serve traffic, and normally retain it after
cutover because it is compatible with both releases. Reversing `0036` still
drops the entire column and is destructive once structured evidence exists.

### Task 2.2 migration note

Migration `quotations.0035_quotationemailoutboundsnapshot_and_more` creates
three new empty tables (snapshot, provider attempt, and append-only attempt
event), their indexes, and integrity constraints. It does not rewrite or
backfill `QuotationEmailDelivery`. Apply the migration before promoting Task
2.2 application code and verify it on a production-sized staging schema.

After any snapshot, attempt, or event row exists, do **not** reverse `0035` as
an ordinary rollback: reversal deletes forensic send history and exact retry
bytes. Do not run pre-Task-2.2 send/retry code against those rows either: it
does not enforce the frozen snapshot and can rebuild a failed retry. Prefer a
forward fix. If application rollback below Task 2.2 is unavoidable, suspend
all quotation-email sends/retries (for example by disabling the send endpoints
at the deployment boundary or disconnecting the shared Gmail credential),
retain all three tables, and restore a compatible snapshot guard before
reenabling sends. Reconciliation must also remain no-send. Capacity planning
must include up to 35 MiB of raw MIME per quotation delivery plus database
backups; actual storage should be measured from representative quotations
before deployment. No production migration or deployment is authorized by
this document.

## 12. Cost guidance

Railway and database/provider prices are usage- and plan-dependent. Railway's
Hobby plan is not a fixed fee per service; it combines a workspace subscription
with metered resource usage. AI, database, email/domain, monitoring, and storage
charges are separate. Use the current provider calculators and actual monthly
usage instead of the previous fixed monthly estimate.

- [Railway plans and usage pricing](https://docs.railway.com/pricing/plans)
- [Neon pricing](https://neon.com/pricing)
- [OpenAI API pricing/model reference](https://developers.openai.com/api/docs/models)

## 13. Release checklist

All boxes are deliberately unchecked until an operator verifies them for a
specific release.

- [ ] Commit, CI run, owner, UTC time, and deployment IDs recorded.
- [ ] Environment diff reviewed without exposing secret values.
- [ ] Production `DATABASE_URL`, PostgreSQL version, and isolation verified.
- [ ] `/backend/railway.json`, direct `MIGRATION_DATABASE_URL`, migration plan,
      compatibility, timeout bounds, and recovery point verified.
- [ ] Backend and frontend build/test suites passed.
- [ ] CORS, CSRF, hosts, HTTPS, and public URLs verified.
- [ ] Private evidence durability limitation accepted or durable storage verified.
- [ ] Database and file backup/restore procedures verified.
- [ ] Gmail mailbox, scopes, owner, and reconnect behavior verified if enabled.
- [ ] AI processor/privacy settings and exact model recorded if enabled.
- [ ] Attachment limits and warning/hard-failure behavior verified on each enabled route; no AV/sandbox claim recorded.
- [ ] Smoke test completed without sending to an unintended recipient.
- [ ] Monitoring/log access and rollback decision owner confirmed.

## 14. References

- [Railway pre-deploy commands](https://docs.railway.com/deployments/pre-deploy-command)
- [Railway config as code](https://docs.railway.com/config-as-code)
- [Railway service variables](https://docs.railway.com/variables)
- [Railway deployment reference](https://docs.railway.com/deployments/reference)
- [PostgreSQL TLS modes](https://www.postgresql.org/docs/17/libpq-ssl.html)
- [Railway volumes](https://docs.railway.com/volumes/reference)
- [Django deployment checklist](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/)
- [Attachment security and spreadsheet fidelity](ATTACHMENT_SECURITY_AND_SPREADSHEET_FIDELITY.md)
