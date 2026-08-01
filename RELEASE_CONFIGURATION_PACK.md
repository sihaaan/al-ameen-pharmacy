# Technical-Hardening Release Configuration Pack

Release packaging, the repository-derived storage map, and outstanding
live-verification requirements are summarized in
[RELEASE_HANDOFF.md](RELEASE_HANDOFF.md).

| Field | Value |
|---|---|
| Document version | 1.0.0 |
| Status | Prepared operator runbook; no external setting has been applied |
| Owner | Al Ameen platform maintainers and named production operators |
| Last verified | 2026-08-01 |
| Reviewed code | Application/security remediation through `7a29096123c09879579e8215d409a00cc23465e6` |
| Accepted implementation candidate | `36db6762d3f92db5cfd341f50dfcb1318a17aba1` |

This pack turns the remaining external release blockers into reproducible
operator checks. It does not select a storage provider, contain credentials,
enable Gmail mailbox enforcement, change Railway, access customer content,
run a migration, or deploy an application.

The employee workflow is unchanged: manual and Gmail inquiry routes still
require review, selling prices remain blank until staff enter them, evidence is
retained, email is previewed before send, Gmail replies are source-verified,
and an ambiguous result is reconciled without sending again.

## 1. Gate meanings and current blocker summary

- **Merge blocker** means the branch must not enter the protected integration
  branch. Railway's externally configured main-branch deployment behavior is
  currently unknown, so it is a merge blocker until an authorized operator
  records whether merging this pull request would deploy production and
  whether Railway waits for required GitHub checks.
- **Staging blocker** means the affected production-like route cannot be
  accepted in staging until the row is verified. Unrelated synthetic staging
  routes may still run.
- **Production blocker** means production promotion, or the specifically named
  feature activation, must stop until evidence is recorded.

| Area | Repository-ready state | Required external evidence | Gate |
|---|---|---|---|
| Railway auto-deploy | Repository CI contains test/build jobs only; Railway linkage and triggers are not repository configuration | Confirm linked branch/environment, auto-deploy setting, and whether Railway waits for required GitHub checks | Before PR merge |
| Private evidence | Dedicated private storage alias, bounded exact-key writes, SHA-256 reads, and dual-read fallback exist | Approved durable private provider or mounted volume, pinned package if needed, exact-key qualification, copied-object manifest, backup and restore result | Staging for durable-storage acceptance; production |
| Migration connection | Guarded runner validates a direct TLS PostgreSQL URL and serializes runs | Sealed Railway MIGRATION_DATABASE_URL for the same database and a successful staging pre-deploy | Staging; production |
| Railway config | backend/railway.json contains the guarded pre-deploy command | Railway Config File Path exactly /backend/railway.json and deployment preview evidence | Staging; production |
| Database recovery | Migration strategy is documented | Current provider recovery-point identifier, UTC time, restore window, named owner, RPO/RTO, and isolated restore result | Before each staging migration rehearsal and production |
| Migration head | Branch head is quotations.0038 | showmigrations/migrate plan and post-deploy confirmation against the target | Staging; production |
| Gmail mailbox | Exact-mailbox checks exist and fail closed when enforcement is on | Verify the intended pharmacydxb@gmail.com value against the connected Google profile; do not rely on this document as proof | Gmail staging; production Gmail hardening |
| Gmail owner | Owner deletion is protected and a transfer command exists | Current active staff owner, active conflict-free successor, active superuser operator, and successful read-only preflight while enforcement remains off; transfer dry run is deferred until a later authorized enforcement rollout | Production Gmail hardening |
| Retention | Expiring handoffs and immutable delivery evidence exist | Approved category-by-category policy, legal-hold rules, capacity owner, deletion design, and audit evidence | Production |
| Health check | No dedicated health/readiness endpoint exists | A separately reviewed endpoint plus Railway Healthcheck Path and failure test | Production |
| Sentry | Conditional integration exists with PII disabled | DSN, environment, sampling decision, access owner, sanitized test event, and alert routing | Production |
| Delivery alerts | Durable states and immutable attempts/events are queryable | Approved thresholds, contacts, run frequency, no-send incident procedure, and alert test | Production |
| Backup/restore | Application relationships and hashes can be checked | Database and evidence-store backup policies plus an isolated restore drill | Production |

Every verification record must include UTC time, target environment, exact code
commit, operator, independent reviewer, sanitized command/result, provider
resource identifier, and rollback owner. Never paste URLs containing passwords,
OAuth tokens, raw MIME, customer documents, or email bodies into the record.

## 2. Durable private quotation-evidence storage

### 2.1 Required settings

| Setting | Required value shape | Verification | Expected safe result | Failure behavior | Rollback | Gate |
|---|---|---|---|---|---|---|
| QUOTATION_IMPORT_STORE_SOURCE_FILES | 1 | Inspect effective Django setting in an authorized console without printing secrets | New accepted manual evidence is retained | 0 intentionally stops new source retention and makes evidence guarantees incomplete | Restore 1 and repeat a synthetic write/read; it cannot recover files never stored | Production |
| QUOTATION_PRIVATE_STORAGE_ROOT | Absolute private mounted path used as the local/legacy root, for example /mounted/private/quotations; never a public media directory | Resolve the path inside the container; confirm it is confined, non-public, writable only by the service, and survives a staging redeploy when it is the selected backend | Legacy local references remain readable; local-backend data survives restart/redeploy | Missing/ephemeral path produces missing evidence after container replacement | Reattach the verified prior volume/path; do not create empty replacement files | Staging if filesystem option is tested; production |
| QUOTATION_PRIVATE_EVIDENCE_MAX_BYTES | Positive decimal bytes; normally 5242880 and never below the largest retained/application-accepted source unless an approved migration accompanies the reduction | Django settings check plus below-limit and above-limit synthetic tests; compare it with QUOTATION_IMPORT_MAX_UPLOAD_BYTES and the secured inventory maximum | Writes and reads are bounded consistently | Invalid/non-positive value stops startup; oversized evidence fails closed; lowering it can make old evidence unreadable | Restore the last reviewed adequate bound | Staging; production |
| QUOTATION_EVIDENCE_STORAGE_BACKEND | Importable Django Storage class path; current safe fallback is quotations.private_storage.QuotationEvidenceFileSystemStorage | Import class, run the exact-key probe below, restart/redeploy, and reread the object | Private synchronous save/open/exists with the requested key unchanged | Import/config/storage errors surface as unavailable; renamed keys or digest mismatches fail closed | Restore the prior backend only after all objects written since cutover are copied back and verified | Staging for provider qualification; production |
| QUOTATION_EVIDENCE_STORAGE_OPTIONS_JSON | One JSON object accepted by the backend constructor; use {} for the local backend and provider-specific non-secret options only | Parse it as JSON and initialize Django storage; confirm no credential appears in logs or the release record | Storage initializes without public URLs or embedded secrets | Invalid/non-object JSON stops startup | Restore the prior reviewed JSON object | Staging; production |
| Provider-native credential variables | Names and shapes defined by the approved provider; secret values sealed and never embedded in the options JSON | Provider identity/access check from staging and least-privilege review | Only the intended private namespace is readable/writable by the app | Authentication/authorization errors fail storage operations; no local fallback on provider errors | Rotate/revoke according to provider procedure, then restore the last working credential | Staging; production |

No remote evidence-storage package is currently pinned in
backend/requirements.txt. Choosing a provider therefore requires a separate,
reviewed dependency/configuration change. The public/default Cloudinary storage
must not be reused for confidential quotation evidence.

### 2.2 Provider acceptance and exact-key probe

An approved backend must be private by default, encrypted as required by the
organization, residency-approved, bounded by timeouts, and synchronously
support save, open, and exists. It must return the requested key unchanged,
including when two workers store identical bytes concurrently. The maximum
stored key length is 500 characters. A definite missing open must surface as
FileNotFoundError; authorization, timeout, transport, and provider failures
must not masquerade as missing.

Run this only in an isolated staging namespace with synthetic bytes after the
provider package and settings are reviewed:

~~~text
cd backend
python manage.py check
python manage.py shell -c "import hashlib,re; from quotations.private_storage import store_import_source,read_private_ref; b=b'al-ameen-private-evidence-probe-v1'; h=hashlib.sha256(b).hexdigest(); k=store_import_source(b,filename='probe.txt',sha256=h); assert re.fullmatch(r'inquiry_sources/v1/[0-9]{4}/[0-9]{2}/[0-9]{2}/'+h+r'\.txt',k); assert read_private_ref(k,expected_sha256=h)==b; print('private-evidence exact-key/readback PASS',h)"
~~~

Repeat the write concurrently from two staging workers and confirm both return
the same canonical key, only content-identical bytes exist there, and readback
passes after a staging restart. A renamed/suffixed object, public URL, digest
mismatch, timeout without a surfaced error, or successful fallback during a
provider outage fails qualification.

The probe leaves one synthetic content-addressed object. Delete it only through
the approved provider lifecycle procedure after retaining the test result; do
not add an unreviewed application delete path.

### 2.3 Existing-evidence inventory, copy, and verification

The repository does not contain a bulk provider-copy management command.
Provider selection determines the destination API and authentication model, so
a reviewed, dry-run-first copy tool is a storage-cutover prerequisite. It must
use the following contract:

1. Record a database recovery point and storage backup. Control new source
   uploads during the final inventory/copy window so the manifest has a clear
   UTC high-water mark.
2. Inventory every non-empty safe source reference in Inquiry,
   HistoricalPriceImport, QuotationOutcomePOImport, QuotationLPO, and
   ProformaInvoice, plus source references retained inside approved structured
   attachment metadata. Also inventory every surviving safe object under the
   local inquiry_sources namespace, including currently unreferenced objects,
   so a database-only scan cannot silently abandon stored evidence. Reject
   unsafe or conflicting references; do not print customer filenames or
   content to ordinary logs.
3. Produce a secured manifest containing relative key, byte count, SHA-256,
   source kind, reference count, source location, copy result, and verification
   time. Do not store credentials or document bytes in the manifest.
4. For inquiry_sources/v1 keys, require the filename digest to equal the bytes.
   For legacy keys, require the recorded source_sha256 when present. If a
   legacy record has no digest, compute one for the secured manifest without
   silently changing business rows. Conflicting recorded digests block cutover.
5. Copy each object under the identical relative key. If the destination key
   exists, compare its full SHA-256 instead of overwriting it. A backend that
   changes the key fails qualification.
6. Reread every destination object through the provider, compare full SHA-256
   and byte count, and reconcile manifest counts:

   source object count = verified destination count + explicitly documented missing count

   source bytes = verified destination bytes + explicitly documented missing bytes

   The safe production requirement is zero undocumented or hash-mismatched
   objects. A missing canonical source remains an incident, not a fabricated
   empty object.
7. Switch the active backend only after an independent reviewer signs the
   manifest. Keep QUOTATION_PRIVATE_STORAGE_ROOT and its backup intact through
   the acceptance window.
8. Smoke-test a new versioned write, a copied versioned read, a copied legacy
   read, a definite not-found, and a simulated provider outage.

Copying does not rewrite database references. It is idempotent only when an
existing destination object is accepted after a full digest comparison.

### 2.4 Dual-read cutover and rollback

The current reader has intentional asymmetric behavior:

- a versioned key reads the active backend first and uses the confined previous
  local file only after a definite not-found and only with the digest embedded
  in the key;
- a legacy key reads the confined local file first, then the active backend
  under the unchanged key;
- an active-backend error is never treated as not-found and never triggers
  fallback.

For a configuration rollback, control new uploads, inventory objects created
after cutover, copy each remote-only object to the restored backend under the
same key, verify every digest, restore the prior backend/options, and rerun the
read matrix. Do not roll application code below the dual reader while any
remote-only evidence exists. Do not delete the old store until a completed
restore drill and the approved acceptance window both pass.

## 3. Backup, recovery point, and restore verification

These are provider settings and controlled records, not application
environment variables.

| Control | Required value shape | Verification | Expected safe result | Failure behavior | Rollback | Gate |
|---|---|---|---|---|---|---|
| Database recovery point | Provider recovery-point/snapshot ID, database identity, UTC timestamp immediately before migration, verified retention window | Confirm in provider console and have a second operator record the identifier before release | Point predates migration and can restore the intended database | Missing, stale, wrong-database, or too-short window stops migration | Do not migrate; if already migrated, use the reviewed migration/data recovery decision rather than blindly restoring | Before staging rehearsal; production |
| RPO/RTO | Approved durations, business owner, platform owner, escalation contact | Tabletop review and timed isolated restore drill | Measured restore meets both objectives | Breach opens incident and blocks production claim | Revert promotion; retain current production unless recovery itself is required | Production |
| Database backup policy | Current provider plan, PITR/snapshot frequency and retention, independent backup decision | Inspect current provider configuration; restore to isolated environment | Quotations, audits, Gmail state, snapshots, attempts, and events restore consistently | Unverified backup is treated as unavailable | Keep production unchanged; establish/verify backup before migration | Production |
| Evidence-store backup policy | Approved namespace, versioning/backup frequency, retention, encryption and residency | Restore a secured synthetic set and verified sampled business objects by key/hash in an isolated environment | Exact keys and bytes reopen through the application reader | Missing keys or hash mismatch fails the drill | Keep prior store and backups; do not cut over or delete source | Production |
| Configuration inventory | Variable names, provider resource IDs, owners and rotation dates, but no secret values | Compare repository-required names with Railway/provider inventories | Every dependency has an owner and recovery procedure | Missing variable/owner blocks the affected route | Restore last reviewed configuration as a unit | Staging for affected route; production |

The isolated restore environment must have email sending, Gmail actions, and
external customer callbacks disabled. Verify at least:

- migrations reach quotations.0038 with no drift;
- quotation/company/product relationships and audit rows are intact;
- each outbound snapshot has the recorded raw MIME byte count and SHA-256;
- attempts reference the correct delivery and snapshot, and events reference
  the correct attempt;
- restored private evidence reopens by unchanged key and valid SHA-256;
- no restore test sends or reconciles an email.

Record measured restore duration, oldest recoverable point, counts, sampled hash
results, deviations, and destruction of the isolated restore after sign-off.

## 4. Guarded database migrations on Railway

### 4.1 Exact settings

| Setting | Required value shape | Verification | Expected safe result | Failure behavior | Rollback | Gate |
|---|---|---|---|---|---|---|
| Railway backend Config File Path | /backend/railway.json | Inspect deployment preview; it must show python run_deploy_migrations.py as the pre-deploy command and no competing release migration | Every backend promotion uses the guarded runner | Missing/wrong path can deploy code without migrations; stop promotion | Before any migration, cancel the candidate; clear the setting only while returning to the last known-good release and do not promote new code without the hook. After migration, retain schema and use reviewed compatible code rollback | Staging; production |
| MIGRATION_DATABASE_URL | Sealed postgresql:// URL to one direct/unpooled TCP host, same decoded database, port, and host lineage as DATABASE_URL; non-local URLs require sslmode=require, verify-ca, or preferably verify-full | Run guarded runner in staging and inspect sanitized success/failure; never print the URL | Direct TLS connection, same database, advisory lock, successful migrate | Missing, malformed, pooled, target-mismatched, query-overridden, or non-TLS value fails closed before migration | Restore last verified sealed value; do not substitute pooled DATABASE_URL | Staging; production |
| MIGRATION_CONNECT_TIMEOUT_SECONDS | Integer 1-60; default 8 | Unit tests plus staging connection failure test | Connection attempts are bounded | Invalid/out-of-range value fails closed | Restore 8 or last reviewed value | Staging; production |
| MIGRATION_LOCK_TIMEOUT_MS | Integer 1-300000; default 10000 | Unit tests plus controlled staging lock contention | DDL lock waits are bounded | Timeout causes nonzero pre-deploy and blocks promotion | Resolve contention; rerun the same guarded deployment, not raw migrate | Staging; production |
| MIGRATION_STATEMENT_TIMEOUT_MS | Integer 1-3600000, at least lock timeout; default 900000 | Unit tests and staging migration rehearsal | Database statements are bounded | Invalid relation or timeout blocks promotion | Diagnose; use reviewed forward fix or adjusted approved bound | Staging; production |

Railway service variables are visible to build, pre-deploy, and running web
code even when sealed from dashboard/API retrieval. The migration role must not
be more privileged than required without a documented risk decision or an
isolated migration service. The runner removes inherited libpq PG variables,
sets its own bounded options, disables persistent migration connections, and
holds advisory lock 0x414C414D45454E while the Django child runs. Raw
manage.py migrate bypasses that lock and is not an approved concurrent path.

### 4.2 Plan, SQL review, and order

From the exact staging release commit and with sanitized output retained:

~~~text
cd backend
python manage.py check
python manage.py makemigrations --check --dry-run
python -c "from run_deploy_migrations import load_migration_configuration; c=load_migration_configuration(); print({'validated':True,'connect_timeout_seconds':c.connect_timeout_seconds,'lock_timeout_ms':c.lock_timeout_ms,'statement_timeout_ms':c.statement_timeout_ms})"
python manage.py showmigrations quotations
python manage.py migrate --plan
python manage.py sqlmigrate quotations 0035
python manage.py sqlmigrate quotations 0036
python manage.py sqlmigrate quotations 0037
python manage.py sqlmigrate quotations 0038
python run_deploy_migrations.py
python manage.py showmigrations quotations
python manage.py migrate --check
~~~

Before the guarded runner, expected unapplied branch migrations relative to the
dated production baseline are 0035, 0036, 0037, and 0038. Current target state
must be checked independently. After the runner, all four must be marked
applied and migration drift must be empty. Migration 0037 is state-only and
normally emits no SQL. Corrected 0036 must retain a PostgreSQL database default
of an empty JSON object; 0038 is the forward-only repair for a target that may
have recorded the earlier 0036 form.

Deployment order:

1. Record recovery point and verify restore window.
2. Seal/verify MIGRATION_DATABASE_URL and timeout settings.
3. Set Config File Path to /backend/railway.json and inspect preview.
4. Rehearse the exact release on PostgreSQL 17 at READ COMMITTED.
5. Deploy backend; pre-deploy migrations must finish before application
   promotion.
6. Confirm migration head and backend smoke tests.
7. Deploy the frontend only after backend compatibility is confirmed.

A nonzero pre-deploy result stops promotion. Do not bypass it, point the runner
at a pooled URL, add a second migration hook, or reverse 0035/0036 after
retained evidence exists. Prefer schema-compatible application rollback or a
reviewed forward migration.

## 5. Designated Gmail mailbox and credential succession

### 5.1 Mailbox settings

| Setting | Required value shape | Verification | Expected safe result | Failure behavior | Rollback | Gate |
|---|---|---|---|---|---|---|
| GMAIL_ADDON_SHARED_MAILBOX_EMAIL | One normalized mailbox address. The intended candidate is pharmacydxb@gmail.com, but an operator must verify it against the connected Google profile | As authenticated quotation staff, inspect GET /api/quotations/gmail/connection/ and independently compare Railway value, Google profile, add-on identity, and stored connection; record only the address and connection ID, never tokens | configured=true, empty configuration_error, connection email is the intended mailbox, is_shared/status/is_connected/mailbox_matches_designated are healthy, send_scope_granted=true, and reconnect_required=false | Mismatch blocks enforcement and must be treated as a credential/configuration incident | Restore prior verified address or reconnect the correct account through the website | Gmail staging; production Gmail hardening |
| QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED | Keep 0 during this remediation | Inspect effective Railway/Django setting | Existing workflow remains available while verification is completed | If changed to 1 with missing/mismatched identity, shared Gmail/OAuth resolution fails closed | Return to 0 while correcting identity; this does not repair a wrong credential | Must remain 0 now; verification blocks later activation |
| GMAIL_ADDON_ENABLED | 0 or 1 according to the already approved add-on rollout; this pack does not change it | Verify signed contextual/action requests in staging and exact mailbox/audience configuration | Only verified Google requests for the intended mailbox are accepted | Disabled returns unavailable; invalid identities/audiences are rejected | Restore last reviewed value and deployment | Gmail staging; production Gmail route |

The remaining add-on URL, audience, service-account and OAuth client variables
are documented in backend/.env.example and gmail_addon/README.md. Keep their
exact HTTPS URLs aligned; this pack does not change OAuth scopes, clients, or
credentials.

### 5.2 Owner and backup successor

The Gmail OAuth row is owned by one website user. The shared Gmail login cannot
identify individual employees, so website audit attribution remains tied to
each staff login. Record in the secured operational system:

- current active staff credential owner and connection ID;
- active staff backup successor who owns no conflicting Gmail connection;
- active superuser authorized to execute a transfer;
- Google Cloud primary/backup owners and credential rotation date;
- last verified Gmail status, with no token values.

While enforcement remains 0, run this read-only preflight in the target
environment. Substitute verified website usernames. It inspects identity and
ownership metadata only and does not read or change tokens:

~~~text
python manage.py shell -c "from django.conf import settings; from django.contrib.auth import get_user_model; from quotations.email_identity import canonicalize_email_address; from quotations.models import GmailOAuthConnection; mailbox=canonicalize_email_address(settings.GMAIL_ADDON_SHARED_MAILBOX_EMAIL); assert mailbox=='pharmacydxb@gmail.com'; assert not settings.QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED; rows=list(GmailOAuthConnection.objects.filter(is_shared=True).select_related('user')[:2]); assert len(rows)==1; c=rows[0]; assert canonicalize_email_address(c.email)==mailbox; assert c.status==GmailOAuthConnection.STATUS_CONNECTED; assert c.user.is_active and c.user.is_staff; U=get_user_model(); key=U.USERNAME_FIELD; admin=U.objects.get(**{key:'admin_username'}); successor=U.objects.get(**{key:'successor_username'}); assert admin.is_active and admin.is_superuser; assert successor.is_active and successor.is_staff and successor.pk!=c.user_id; assert not GmailOAuthConnection.objects.filter(user=successor).exists(); print({'mailbox':mailbox,'connection_id':c.pk,'current_owner':c.user.get_username(),'initiator':admin.get_username(),'successor':successor.get_username(),'enforcement_enabled':False,'preflight':'PASS'})"
~~~

Expected: a token-free dictionary ends with preflight=PASS and identifies the
single connected shared row, its active staff owner, active superuser operator,
and distinct conflict-free active staff successor while enforcement_enabled is
false. A missing/inactive user, non-superuser initiator, mailbox mismatch,
disconnected/ambiguous row, same-user successor, or conflicting Gmail row
fails without changing data.

The transfer_shared_gmail_owner command intentionally refuses both dry-run and
apply operations while enforcement is 0. Do not change the flag or run that
command as part of this remediation. During a future separately authorized
enforcement/succession rollout, first enable enforcement only after all
mailbox checks pass, then run the following token-free dry run:

~~~text
python manage.py transfer_shared_gmail_owner --initiated-by admin_username --new-owner successor_username --confirm-mailbox pharmacydxb@gmail.com
~~~

Only after independent review may an authorized operator add the command's
apply switch. Verify Gmail status, inquiry intake, preview, source-verified
reply, and audit attribution through the unchanged connection. Roll back by
dry-running and applying the same transfer in reverse while the former owner
remains active and conflict-free. Never delete the current owner first;
migration 0037 protects the row with PROTECT.

Enable designated-mailbox enforcement only in a later, separately recorded
configuration step after mailbox identity, owner, successor, and rollback have
all passed. A mismatch under enforcement is intentionally unavailable, not a
reason to bypass the check.

## 6. Retention and deletion decision record

There is no general scheduled purge and no approved repository-wide retention
duration. Do not invent one. Production sign-off requires the data owner,
security/privacy owner, legal-hold behavior, minimum and maximum period,
provider-side retention/residency, deletion or anonymization mechanism,
capacity budget, access review, incident procedure, and audit proof for every
category:

| Category | Current behavior that the policy must cover |
|---|---|
| Manual inquiry/LPO/proforma source evidence | Private objects may outlive or be missing from their database references; durable-store lifecycle and legal holds must preserve evidence integrity |
| Gmail imports and provenance | Message/thread IDs, sender facts, attachment manifests, extracted evidence, analysis and review state remain in PostgreSQL; Gmail is canonical for original mailbox content |
| Frozen outbound email and attached quotation PDF | Exact RFC-MIME bytes, including the PDF, are stored in PostgreSQL and bounded to 35 MiB per delivery; snapshot rows are application-immutable |
| Provider attempts and events | Attempts and result/reconciliation events are append-only, protected evidence for send idempotency and incident response |
| AI cache and observability | Cache results and content-free usage/latency metadata have no general purge; policy must distinguish potentially extracted content from operational metrics and address legacy cache versions |
| Quotation audit logs | Business/security attribution may reference users later deleted; required audit period and anonymization rules need approval |
| Handoff/selection tokens | Tokens expire by code, but expired database-row cleanup and proof are not a general scheduled service |
| Provider backups and logs | Database, storage, Railway, Gmail, AI and Sentry retention/residency are external provider settings and must match the approved policy |

Immutable outbound snapshot/attempt/event models reject ordinary update/delete,
and protected relationships prevent casual cascading deletion. Any eventual
purge, archive, legal-hold, or anonymization implementation therefore needs a
separate reviewed design, migrations, authorization, dry run, reconciliation
proof, and tested restore path. Until then, no destructive command is
authorized. If an approved purge later fails, stop it, preserve the manifest
and audit evidence, restore only from the verified recovery point, and do not
reconstruct email state by resending.

## 7. Health checks, Sentry, and delivery alerts

### 7.1 Railway health check

There is currently no dedicated public liveness/readiness URL in the Django URL
configuration, and backend/railway.json does not declare one. Do not point
Railway at an authenticated page, admin page, mutating endpoint, or a guessed
path.

| Railway setting | Required value shape | Verification | Expected safe result | Failure behavior | Rollback | Gate |
|---|---|---|---|---|---|---|
| Healthcheck Path | Absolute path to a separately reviewed, non-mutating endpoint that returns 2xx without credentials or customer data and has documented dependency semantics | Exercise success, application-start failure, and dependency-failure behavior in staging; confirm Railway blocks an unhealthy promotion | Platform promotes only a responsive compatible backend | No endpoint currently exists, so this remains unconfigured and production is not health-check ready | Clear/revert the path and endpoint configuration to the last known-good deployment; do not mask a real startup failure | Production; separate code task required |

The endpoint design must decide whether it is liveness-only or readiness
including database access. That decision affects outage behavior and must be
reviewed before implementation.

### 7.2 Sentry and application logging

| Setting | Required value shape | Verification | Expected safe result | Failure behavior | Rollback | Gate |
|---|---|---|---|---|---|---|
| SENTRY_DSN | Sealed project DSN for the exact environment; never commit or print it | Emit one sanitized staging test exception and verify project, environment, release and alert route without customer content | Event arrives with send_default_pii=false | Empty DSN disables Sentry; wrong project leaks operational metadata or loses alerts | Remove/restore prior DSN and rotate if exposed | Staging monitor acceptance; production |
| SENTRY_ENVIRONMENT | Stable label such as staging or production, never customer-derived | Inspect test event | Events cannot be confused across environments | Wrong label misroutes triage | Restore reviewed label | Staging; production |
| SENTRY_TRACES_SAMPLE_RATE | Decimal 0.0-1.0; current privacy/cost-safe default 0.0 until approved | Django startup plus sanitized test request and spend/privacy review | Sampling matches approved budget and data policy | Invalid value can stop startup; excessive value increases cost/data | Restore 0.0 | Production |
| DJANGO_LOG_LEVEL | Standard level; current default INFO | Inspect sanitized staging logs | Useful application events without secrets/content | Too low is noisy; too high hides incidents | Restore INFO | Production |
| DJANGO_REQUEST_LOG_LEVEL | Standard level; current default ERROR | Trigger a synthetic handled error | Request failures appear without SQL, tokens or document bodies | Misconfiguration hides or overexposes request data | Restore ERROR | Production |

Sentry access must have named primary/backup owners, MFA, environment-specific
projects or filters, alert recipients, retention, and secret-rotation records.
No DSN was configured in the dated production snapshot.

### 7.3 Delivery-state alerts

There is no scheduler/worker, alert environment variable, stuck-delivery
sweeper, approved threshold, or contact route in the repository. Monitoring
must remain read-only and must never call send. Configure external alert rules
only after approving:

- age threshold and immediate escalation rules for sending and unknown;
- failed-rate/count window, business hours, severity, and recipients;
- reconciliation-unavailable and duplicate-candidate escalation;
- query frequency, database load limit, alert deduplication and runbook link;
- privacy-safe fields: counts, status, internal IDs and timestamps only.

The underlying privacy-safe queries are:

~~~sql
SELECT status, COUNT(*)
FROM quotations_quotationemaildelivery
GROUP BY status;

SELECT id, status, updated_at
FROM quotations_quotationemaildelivery
WHERE status IN ('sending', 'unknown')
  AND updated_at < CURRENT_TIMESTAMP - INTERVAL '<approved duration>';
~~~

Use a read-only monitoring role and an operator-approved duration; do not paste
subjects, recipients, bodies, raw MIME, Gmail tokens, or customer attachments
into alert payloads. Validate in staging with synthetic delivery rows that each
rule fires once, routes to both primary and backup, links to the no-send
reconciliation runbook, and clears without mutating the delivery.

Any unknown or stale sending result remains locked. Operators inspect Gmail
Sent and use only the existing reconciliation action, which searches and never
sends. Alert failure does not authorize retry. Roll back a noisy rule by
disabling that external rule while preserving delivery data and the incident
record.

## 8. Operator sign-off record

Copy this template into the secured release system; do not fill it in this
repository:

~~~text
Environment:
Release commit:
UTC start/end:
Primary operator / independent reviewer:

Private storage provider/resource (no credential):
Backend class and package version:
Exact-key/concurrency probe:
Evidence inventory manifest ID/count/bytes:
Copy verified count/bytes/hash failures:
Dual-read and outage tests:
Storage backup/restore result:

Database provider/resource:
Recovery-point ID and UTC time:
RPO/RTO and restore-drill result:
MIGRATION_DATABASE_URL target equality/TLS/direct check (no URL):
Railway Config File Path:
Pre-deploy result:
Migration head before/after:

Designated mailbox:
Stored connection ID/profile match:
Current owner / successor:
Owner/successor read-only preflight:
Later enforcement/transfer dry-run result (not performed now):
Enforcement flag confirmed 0:

Retention policy ID/owner:
Health endpoint and Railway check result:
Sentry sanitized event/alert result:
Delivery alert thresholds/contacts/test:

Smoke tests:
Known deviations:
Go/no-go decision:
Rollback owner and last known-good commit/config:
~~~

An unchecked field is not evidence of safety. Record a no-go when any required
production row in this pack is absent, mismatched, expired, or cannot be
independently verified.
