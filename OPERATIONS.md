# Quotation System Operations Runbook

| Field | Value |
|---|---|
| Document version | 1.2.0 |
| Status | Initial current-state runbook; unresolved items are marked explicitly |
| Owner | Assign a primary and backup production operator |
| Last verified | 2026-08-01 |
| Reviewed code | `d88b767` baseline plus the Task 2.1, Task 2.2, and Task 2.3 checkpoints |
| Production snapshot | Railway deployment `c234c4bc-ba7e-4ed0-ab88-b5a1dcc2a6b8`, commit `70d3da7162b63864e479e9a1998aa138046c2433` |

This runbook preserves employee review, blank selling prices, suggestion-only
matching, evidence, preview-before-send, verified reply headers, one successful
send per revision, ambiguous-send lockout, and reconciliation that never sends email.

## 1. Current operational truth

| Area | Verified state on 2026-08-01 | Gap/action |
|---|---|---|
| Backend | Railway, one replica in Singapore, runtime V2/Railpack | no configured health check |
| Code | production at `70d3da7`; hardening branch at `d88b767` | branch work is not deployed |
| Database | PostgreSQL 17.10, `READ COMMITTED`; no unapplied migrations at inspection | backup/RPO/RTO and production lock/statement timeouts not verified |
| Migration gate | no Railway pre-deploy command in inspected manifest | Task 2.8; verify explicitly before any migration release |
| Private evidence | dedicated alias and dual reader implemented; local default path; no Railway volume/provider | application-ready but still ephemeral and not a reliable backup |
| Gmail | consumer mailbox `pharmacydxb@gmail.com`; add-on enabled in observed env | deployment/publication/verification and ownership records not in repo |
| AI | observed OpenAI provider and moving `gpt-5.4` aliases | exact snapshot/provider policy must be recorded per evaluation |
| Error monitoring | Sentry integration exists | `SENTRY_DSN` not configured in snapshot |
| Async work | no Redis/background worker | Gmail analysis is synchronous with resumable status/polling |
| Retention | handoff tokens expire; no general scheduled purge | approve retention/deletion policy |

Do not infer current provider configuration from this table after its date.

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

### Weekly

- Review failed imports, repeated reanalysis, cache behavior, and validation failures.
- Review AI timing/token aggregates by route, provider, model, pipeline, schema,
  prompt/contract hash, and success. Do not group unlike contracts together.
- Check database capacity/connections, Railway memory/CPU/restarts, Gmail API
  errors, and OpenAI spend/budget alerts in their provider consoles.
- Check private-source missing-file reports until durable storage exists.
- Review staff/superuser access and the shared credential owner's status.

### Before every release

Use [DEPLOYMENT.md](DEPLOYMENT.md). Record exact commit, deployment IDs,
migration plan, backup/recovery point, operator, UTC time, and smoke-test result.

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

The inspected Railway manifest had no pre-deploy command even though the
Procfile declares a `release` line. Before a migration release, configure and
verify the Railway pre-deploy mechanism described in
[DEPLOYMENT.md](DEPLOYMENT.md). A green old deployment and zero unapplied
migrations on 2026-08-01 do not prove future migrations will run.

Current application defaults include:

- connection lifetime: 60 seconds;
- connection health checks: enabled;
- connect timeout: 8 seconds;
- server-side cursors: disabled;
- production lock timeout: not configured;
- production statement timeout: not configured.

Task 2.8 prepares explicit migration and timeout handling but does not deploy
it. If a lock timeout occurs, inspect the transaction and business state before
retrying; never translate a database timeout into an assumption that Gmail did
or did not send.

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
transferred and verified. Current model cascades can delete the shared Gmail
connection and related mailbox inventory. If the expected designated-mailbox
configuration is unavailable, Task 2.7 must remain disabled rather than guess
an address or owner.

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
- [Railway pre-deploy commands](https://docs.railway.com/deployments/pre-deploy-command)
- [Railway volumes](https://docs.railway.com/volumes/reference)
