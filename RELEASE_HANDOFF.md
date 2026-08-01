# Technical-Hardening Release Handoff

| Field | Value |
|---|---|
| Document version | 1.0.0 |
| Status | Release packaging only; production posture is **NO-GO** until every applicable gate below is independently verified |
| Owner | Al Ameen platform maintainers and named release operators |
| Last verified | 2026-08-01 |
| Reviewed code | Implementation baseline `36db6762d3f92db5cfd341f50dfcb1318a17aba1`; final packaging SHA is recorded in the pull request after commit |
| Target branch | `codex/technical-hardening` |
| Target base | `main` at `70d3da7162b63864e479e9a1998aa138046c2433` |

This document packages the accepted Phase 1 and Phase 2 technical-hardening
release for operator review. It does not authorize a staging or production
deployment, a Railway change, a production migration, a storage-provider
choice, Gmail credential work, a feature-flag change, or access to customer
content. Phase 3 and the separate Gmail workflow/speed redesign are excluded.

The exact implementation task ledger is in
`TECHNICAL_HARDENING_PROGRESS.md`. Detailed configuration value shapes,
verification procedures, safe failure behavior, and rollback instructions are
in `RELEASE_CONFIGURATION_PACK.md`.

## Merge safety warning: Railway deployment trigger is unknown

Repository configuration does not establish whether merging `main`
automatically deploys production through Railway.

- `.github/workflows/ci.yml` has test/build jobs only and no Railway deploy
  action, CLI call, or webhook.
- `backend/railway.json` defines only the guarded pre-deploy migration command.
  It does not declare the linked Railway project, environment, source branch,
  auto-deploy setting, or whether Railway waits for GitHub checks.
- `backend/Procfile` defines only the web process.

Railway's source-repository and deployment-trigger settings live outside this
repository. **Do not merge the pull request until an authorized Railway
operator confirms whether a merge to `main` deploys production and whether
Railway waits for all required GitHub checks.** Record that confirmation in
the pull request or release ticket.

## Current storage map

The presence of Neon PostgreSQL does not mean uploaded file bytes are stored
in Neon. Structured database rows, private evidence objects, public media, and
Gmail originals use distinct storage paths.

### 1. Neon PostgreSQL / `DATABASE_URL`

When the production `DATABASE_URL` points to Neon, Django stores model-backed
structured records there, including:

- users and staff permissions;
- companies, contacts, products, aliases, images' storage names, and price
  history;
- inquiries, inquiry lines, quotation revisions/lines/totals/outcomes,
  proformas, LPO records, orders, and accounting data;
- parse results, source filenames, MIME types, byte counts, SHA-256 values,
  private `source_file_ref` keys, warnings, and evidence metadata;
- audit logs, AI cache/log JSON, Gmail connection/token records, Gmail import
  state, mailbox audit/matching/reconciliation state, message/thread IDs,
  attachment manifests, mailbox-audit newest body text,
  contract-intelligence body text, extracted evidence, and delivery state;
- after migration 0035, immutable outbound RFC-MIME snapshots, provider
  attempts, and append-only attempt events.

Ordinary Django `ImageField` columns store a storage name/reference, not image
bytes. Ordinary manual inquiry/LPO/import source bytes are also not stored in
PostgreSQL; their rows store a private reference and digest. The repository's
only model `BinaryField` is the outbound `raw_mime` snapshot described below.

This map is derived from repository configuration and models. No live Neon
configuration or customer row was inspected during release packaging.

### 2. Confidential manual-upload source files

Manual PDF, Excel, image, LPO/outcome-PO, proforma, and historical-import
source bytes use the dedicated Django storage alias `quotation_evidence` when
`QUOTATION_IMPORT_STORE_SOURCE_FILES=1`.

The repository default is:

```text
Backend: quotations.private_storage.QuotationEvidenceFileSystemStorage
Root:    QUOTATION_PRIVATE_STORAGE_ROOT
Default: <backend>/private_media/quotations
New key: inquiry_sources/v1/YYYY/MM/DD/<sha256>.<extension>
```

Writes are size-bounded, content-addressed, exact-key checked, reread after
save, and verified against full SHA-256. Reads support the documented
versioned/legacy dual-read behavior.

On Railway, this default is Railway-local filesystem storage. The repository's
dated deployment snapshot recorded no persistent volume. Unless an operator
has since mounted a volume or configured another approved private backend,
these bytes are ephemeral across service replacement. This live fact was not
rechecked because Railway access is outside this task.

The private evidence backend is intentionally separate from the public/default
Cloudinary media backend.

### 3. Cloudinary or default media storage

When `CLOUDINARY_URL` is configured, Django's `default` storage is
`MediaCloudinaryStorage`. It stores the bytes for:

- brand logos;
- product images;
- quotation logo, signature, and stamp images;
- user quotation signatures.

Their database columns retain storage names. If `CLOUDINARY_URL` is absent,
the same fields use local `MEDIA_ROOT`. The current live value was not read, so
the active production media backend must be verified by an operator without
printing the credential.

### 4. Gmail-canonical content

For Gmail inquiry intake, the original source message and original attachment
bytes remain canonical in Gmail. PostgreSQL retains the reviewed workflow and
provenance: message/thread identifiers, sender/date/subject facts, snippets,
body hashes/lengths, attachment manifests, extraction results, evidence,
classification, company/contact candidates, and resulting inquiry/quotation.

Important qualifications:

- mailbox PO audit records may retain headers, selected/newest body text,
  attachment metadata, and extracted references/evidence in PostgreSQL, but
  audit attachment bytes are fetched transiently;
- contract-intelligence sources retain body text and metadata, and supported
  Gmail attachments processed through that route may be copied into private
  evidence storage;
- these structured or deliberately retained copies do not change Gmail's role
  as the canonical original mailbox/document source.

### 5. Exact outbound MIME/PDF snapshots after migration 0035

Migration 0035 creates one immutable `QuotationEmailOutboundSnapshot` per
delivery plus immutable provider-attempt and append-only event tables.

The snapshot stores in PostgreSQL:

- reviewed actor, mailbox, send mode, recipients, subject, body, thread and
  source/outbound message identifiers;
- generated quotation PDF filename, size, and SHA-256;
- exact RFC-MIME bytes in `raw_mime`, their size and SHA-256, and a whole
  snapshot digest.

The generated quotation PDF is embedded as the MIME `application/pdf`
attachment. It is not stored as a second standalone database blob. The MIME
snapshot therefore contains the exact send bytes, including the encoded PDF,
and is capped at 35 MiB. Retries verify and reuse those exact bytes. Attempts
and events store structured facts/digests only; they do not duplicate the PDF
or MIME blob.

### 6. Can existing manual-upload files be enumerated now?

Not within this release-packaging task. An actual production inventory would
require reading production database references and source bytes, which is
customer-content access and was explicitly prohibited.

The repository also has no provider-neutral bulk inventory/copy command.
After storage is selected and access is separately authorized, a read-only,
dry-run-first tool must enumerate:

- every non-empty safe reference in `Inquiry`, `HistoricalPriceImport`,
  `QuotationOutcomePOImport`, `QuotationLPO`, and `ProformaInvoice`;
- references inside approved structured attachment metadata;
- every surviving object in the local `inquiry_sources` namespace, including
  unreferenced objects.

It must create a secured manifest of relative key, byte count, SHA-256, source
kind, reference count, source location, result, and verification time. It must
not print customer filenames/content to ordinary logs or modify business rows.
Versioned filename digests and recorded legacy digests must match the bytes.
The detailed copy/reconciliation contract is in
`RELEASE_CONFIGURATION_PACK.md` section 2.3.

## Database migrations

| Migration | Purpose | Deployment/rollback constraint |
|---|---|---|
| `0035` | Immutable outbound snapshots, attempts, and events | Additive. Do not reverse after sends; reversal would destroy delivery history. New code requires these tables. |
| `0036` | Add `parsed_meta` JSON with Python and persistent database defaults | PostgreSQL uses `jsonb DEFAULT '{}'::jsonb NOT NULL`; old-code INSERTs that omit the column remain valid. Reversal after writes would discard metadata. |
| `0037` | Protect the Gmail connection owner relation | PostgreSQL SQL is a no-op; application ownership behavior changes after promotion. |
| `0038` | Forward-only repair for a target that applied the original 0036 without its database default | PostgreSQL `ALTER COLUMN ... SET DEFAULT '{}'::jsonb`; reverse is deliberately a no-op. |

Only the guarded `python run_deploy_migrations.py` path is approved. A direct,
TLS, unpooled `MIGRATION_DATABASE_URL`, a recovery point, reviewed plan, and
Railway Config File Path `/backend/railway.json` are required first.

## Local pre-handoff evidence — not release approval

Frontend production dependency audit:

| State | Critical | High | Moderate | Low |
|---|---:|---:|---:|---:|
| Before remediation | 1 | 16 | 7 | 9 |
| Accepted release | 0 | 11 | 7 | 9 |

The remaining findings are mapped to time-bounded Create React App/build-chain
exceptions in `FRONTEND_DEPENDENCY_SECURITY.md`. They must be reviewed by
2026-09-01; a new Critical or unmapped High blocks release.

The following results belong to implementation baseline
`36db6762d3f92db5cfd341f50dfcb1318a17aba1`:

- backend SQLite: 1,270 completed; 1,251 passed, 19 intentional skips, zero
  failures/errors;
- PostgreSQL 17.10 at `READ COMMITTED`: 3/3 migration compatibility and 18/18
  concurrency tests passed;
- frontend: 20/20 suites and 252/252 tests passed;
- production frontend build: passed with zero warnings;
- Django system checks, migration drift, Python dependency integrity,
  documentation contracts, requirements installation, compilation,
  repository whitespace/integrity, and Railway JSON checks: passed;
- the named cumulative reviews reported no known unresolved Critical, High, or
  Medium code finding. This is scoped review evidence, not certification of
  live configuration or production readiness.

The pull request CI must independently validate the final packaging commit and
repeat the repository-defined lanes.
Whether those jobs are configured as required branch-protection checks is a
GitHub repository setting and must be confirmed by an authorized operator.

## Release gates and operator-owned blockers

Current production posture is **NO-GO**. Repository packaging cannot satisfy
the following design, provider, live-configuration, or governance gates.

### Repository/design blockers

| Area | Required resolution | Gate |
|---|---|---|
| Durable private evidence backend | Select an approved provider or mounted volume. A remote provider may require a separately reviewed pinned dependency and configuration change; do not use Cloudinary for confidential evidence. | Durable-storage acceptance in staging; production |
| Health/readiness | Make an explicit liveness/readiness decision and separately review an endpoint; no dedicated endpoint exists in this release. | Production |

### External live-configuration and evidence blockers

| Area | Required operator evidence | Gate |
|---|---|---|
| Exact-key and SHA-256 qualification | Synthetic write returns the requested content-addressed key; reread bytes and full SHA-256 match | Durable-storage acceptance in staging; production |
| Existing evidence copy | Secured inventory/copy manifest with zero undocumented missing or mismatched objects | Production storage cutover |
| Dual-read rollback | Versioned active-first/local fallback and legacy local-first/active fallback exercised, including definite-not-found and provider-failure cases | Durable-storage acceptance in staging; production |
| `MIGRATION_DATABASE_URL` | Sealed direct/unpooled TLS PostgreSQL URL for the same host lineage, port, and database as `DATABASE_URL` | Staging and production |
| Railway Config File Path | Exactly `/backend/railway.json`; preview shows only the guarded pre-deploy runner | Staging and production |
| Database engine | Effective target is supported PostgreSQL, with exact version and `READ COMMITTED` isolation recorded; never the SQLite fallback | Staging and production |
| Database recovery point | Current snapshot/PITR identifier, UTC time, verified retention, RPO/RTO, and rollback owner | Before any migration |
| Migration head | Preflight current live head and post-deploy confirmation of `quotations.0038` | Staging and production |
| Runtime security posture | `DEBUG=0`; reviewed hosts, CORS/CSRF/frontend URLs; HTTPS/HSTS, secure cookies, and proxy-header behavior verified | Staging and production |
| Redacted environment diff | Compare required variable names and non-secret value shapes with the target; record missing/stale values without printing secrets | Staging and production |
| Backup and restore | Isolated database and evidence-store restore drill with relationship/hash verification and outbound actions disabled | Production |
| Retention policy | Approved retention/deletion rules for database, evidence, Gmail provenance, immutable email history, AI/audit data, provider backups/logs | Production |
| Sentry | Environment-specific DSN, PII-disabled test event, owners, access, retention, and alert route | Staging monitor acceptance and production |
| Delivery-state monitoring | Thresholds, scheduler/query owner, primary/backup recipients, deduplication and runbook links for unknown/failed states | Production |
| Railway auto-deploy | Confirm main-branch source trigger and whether CI completion is required before Railway deploys | **Before PR merge** |
| Frontend exception expiry | Reconfirm every remaining High maps to an approved exception for every release; reapproval or closure is mandatory on or after 2026-09-01 | Every release; production |
| Cloudinary/media posture | Verify active backend, access owners, backup/recovery behavior, retention, and credential rotation without printing secrets | Production media |
| AI processor approval | If AI remains enabled, confirm provider/project/model, processing terms, retention/residency, privacy controls, and evaluation evidence | Production AI routes |
| Native file-picker acceptance | Human staging pass for every enabled PDF/Excel/image/attachment route using the browser file picker | Production |
| PyMuPDF licensing | Record the applicable operator/legal deployment decision before non-internal production use | Production where applicable |

### Deferred Gmail-specific gates

These rows gate a later Gmail enforcement/ownership rollout while enforcement
remains `0`; they do not authorize that deferred rollout and are not blockers
for unrelated manual workflows.

| Area | Required operator evidence | Gate |
|---|---|---|
| Gmail mailbox identity | Independently verify the intended `pharmacydxb@gmail.com` against Google profile, stored connection, and add-on identity | Gmail staging and production |
| Gmail credential owner/successor | Active staff owner, active backup successor, superuser initiator, Google Cloud owners, rotation and reconnect records | Before enforcement |
| Mailbox enforcement flag | Keep `QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=0` until separately approved checks pass | Deferred production Gmail hardening |
| Google OAuth/add-on posture | Verify publication state, exact scopes, OAuth verification/security-assessment requirements, add-on audience/service account, and reconnect status | Gmail staging and production |

## Staging checklist

1. Confirm the pull request checks are required and all are green.
2. Confirm Railway main-merge auto-deployment behavior before merging anything.
3. Select/configure durable private evidence storage and complete the
   exact-key, SHA-256, dual-read, backup, and isolated restore tests.
4. Record a staging database recovery point.
5. Seal `MIGRATION_DATABASE_URL`; set Config File Path to
   `/backend/railway.json`; inspect the deployment preview.
6. Review migrations 0035-0038 and run only the guarded pre-deploy path.
7. Verify migration head 0038 before application smoke tests.
8. Use synthetic records, the designated test mailbox, and approved test
   recipients only. Disable outbound Gmail actions in isolated restore drills
   and prohibit customer callbacks.
9. Exercise manual upload; Gmail current, selected and thread imports;
   company/product review; quotation creation/pricing; blank selling prices;
   evidence reopening; and forwarded RFQs.
10. In staging or mocks, exercise stale-preview rejection, Gmail reply, manual
    new-email delivery, known failure/exact retry, unknown-result lockout, and
    reconciliation.
11. Keep designated-mailbox enforcement disabled until mailbox and succession
    evidence is approved.
12. Verify monitoring without customer content and record results/owners.

## Production checklist

1. Obtain final sign-off for every production-applicable repository/design and
   external live-configuration gate above. Apply deferred Gmail gates when the
   corresponding Gmail route or enforcement rollout is in scope.
2. Confirm a fresh recovery point and both restore drills.
3. Confirm storage-copy manifest reconciliation and retention approvals.
4. Confirm Railway auto-deploy/CI behavior and a staffed monitoring window.
5. Review the live migration plan and require Railway's configured guarded
   pre-deploy step to complete; never substitute a manual migration invocation.
   Verify head 0038 before promoting application traffic.
6. Run only controlled production smoke tests with approved test recipients
   and non-customer synthetic records. Verify evidence reopening and normal
   send/reconciliation visibility; never deliberately induce an ambiguous send
   or customer callback in production.
7. Record release identifiers, migration output, storage manifest ID,
   monitoring links, and named rollback owners.

## Rollback constraints

- Before migration, cancel the candidate deployment and retain the last
  known-good code/configuration.
- After migrations 0035-0038, prefer a schema-compatible application rollback
  or forward fix. Do not drop immutable delivery history or parsed metadata.
- If application code must roll below Task 2.2 after migrations, suspend all
  quotation sends and retries while retaining the snapshot, attempt, and event
  tables for reconciliation and audit.
- Keep the prior private evidence root/backend and backup until the secured
  manifest, destination reread, restore drill, and acceptance window pass.
- After a durable-storage cutover, pause new uploads and copy every remote-only
  object back under the same verified key before reverting configuration or
  code below the dual reader.
- Never treat an active storage-provider error as not-found.
- Never blindly retry an unknown Gmail send; reconcile the exact immutable
  snapshot instead.
- Do not roll back only the frontend dependency overrides; that would restore
  the previously Critical `websocket-driver` version.

## Exact next operator actions

1. Commit the reviewed packaging delta, push the branch, and open the pull
   request without merging it.
2. Review the pull request and wait for every CI job to finish successfully.
3. Confirm GitHub branch-protection required checks.
4. Confirm Railway's linked branch, production environment, auto-deploy toggle,
   and whether it waits for GitHub checks. **Do not merge until recorded.**
5. Assign owners and complete the external configuration evidence in
   `RELEASE_CONFIGURATION_PACK.md`.
6. Schedule a separately authorized staging exercise only after its blockers
   are cleared.

## Explicitly excluded work

This handoff does not merge or deploy the release. It does not authorize or
implement Phase 3, including model benchmarking/model changes, a queue/worker/
Redis redesign, Gmail draft creation or a new Gmail workflow/speed redesign,
new reporting/analytics, granular roles/groups, or rich/DOCX/static-background
quotation templates. The existing Gmail API import remains in scope and is
unchanged by release packaging.
