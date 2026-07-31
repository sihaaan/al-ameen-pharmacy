# Security Configuration and Operations Guide

| Field | Value |
|---|---|
| Document version | 2.0.0 |
| Status | Repository control reference and operator checklist; not a certification |
| Owner | Al Ameen platform maintainers and designated production operators |
| Last verified | 2026-08-01 |
| Reviewed code | `d88b767` |
| Production snapshot | Railway deployment `c234c4bc-ba7e-4ed0-ab88-b5a1dcc2a6b8`, commit `70d3da7162b63864e479e9a1998aa138046c2433` |

Source control can prove implemented controls and tests; it cannot prove live
secrets, provider policy, OAuth publication, backups, or current production
configuration. Complete the unchecked verification items for each release.

## 1. Implemented repository controls

### Django and browser boundaries

- Secrets and deployment settings are environment-driven; `.env` files are
  ignored and example files contain placeholders.
- Production mode enables HTTPS redirect, HSTS, and secure session/CSRF
  cookies. Allowed hosts, CORS, and trusted CSRF origins remain operator inputs.
- Staff quotation APIs enforce role checks. Add-on callbacks do not use a
  browser session; they require verified Google system/user identity, exact
  audience/service account, host application, permissions, and mailbox.
- OAuth return state is signed, time-bounded, and restricted to an internal
  admin return path.
- Inquiry creation, product/company matching, pricing, and email delivery keep
  explicit employee-review gates.

### Gmail credentials and delivery

- The website mailbox OAuth connection uses `gmail.readonly` plus `gmail.send`.
- Refresh/access tokens are encrypted with Fernet using a key derived from
  `DJANGO_SECRET_KEY`.
- Add-on handoffs and manual Gmail-thread selections are opaque, short-lived,
  hashed server-side, and bound to context. The initial bearer token still
  appears in the browser URL, so users must not share screenshots/history or
  forward that link.
- Gmail reply recipient, subject, thread ID, and RFC reply headers are
  re-fetched and verified by the server.
- One aggregate delivery record per quotation revision, database locking, and
  delivery-state checks prevent ordinary double sends.
- An ambiguous result becomes `unknown`; blind retry is blocked. Reconciliation
  verifies Sent/From/RFC Message-ID/thread evidence and never sends email.

### AI and evidence

- Provider requests use strict structured output and `store=false`.
- Email/document content is handled as untrusted data, including embedded
  instructions.
- File count, type, byte, page, sheet, row, and image dimensions are bounded.
- AI output cannot bypass employee review, create products/aliases, populate
  selling prices, or send customer email.
- Each usable extracted row retains source evidence and uncertain rows remain
  visible.
- Reviewed-branch instrumentation stores numeric usage/timing and contract
  hashes without copying full customer content into its observability envelope.

### Database and file access

- PostgreSQL connections support health checks, bounded connect timeout, and
  disabled server-side cursors.
- Private source references are path-confined and served only through
  authenticated application paths.
- API audit/history viewsets are read-only where defined. Django-admin audit
  immutability remains Task 1.8 and must not be assumed yet.

## 2. Data processors and retained categories

| Boundary | Data that may cross it | Current control | Operator decision still required |
|---|---|---|---|
| Railway application | accounts, quotations, evidence, audit and delivery state | authenticated APIs, TLS at platform edge, environment secrets | access review, log retention, region/contract |
| PostgreSQL provider | application records and encrypted Gmail credentials | database authentication/TLS configuration | backup, restore window, RPO/RTO, deletion policy |
| Google/Gmail | mailbox contents and outgoing quotation | OAuth scopes, canonical re-fetch, verified reply/send | publication/verification, owners, retention/legal basis |
| OpenAI API | bounded bodies/documents or parsed rows | explicit feature gates, strict schema, `store=false` | approved project, retention/residency/DPA/security assessment |
| Cloudinary | configured catalog/branding media | provider credentials and Django storage | account controls and backup requirements |
| Private source filesystem | manual inquiry files/evidence | confined application path | durable storage and recovery; current Railway snapshot has no volume |

The database retains structured inquiry/AI results, hashes, message and
attachment manifests, bounded evidence excerpts, identifiers, cache rows,
audit records, and delivery state. There is no general scheduled purge or
formal retention policy. Legacy AI cache rows written before branch commit
`a6548aa` may still contain older payload shapes at rest even though current
reads filter them.

## 3. Gmail/OAuth governance

The add-on manifest scopes and the website mailbox scopes are separate. The
website's `gmail.readonly` scope is restricted; Google verification or a
security assessment may be required depending on publication and server-side
data use. The repository does not establish whether an exemption applies.

The known mailbox is a consumer `@gmail.com` account. A developer deployment
can be installed for that account, but a private organization-wide Marketplace
listing requires a Google Workspace organization. External test-mode OAuth
authorizations may expire on Google's test-mode schedule; recurring reconnects
must be diagnosed against the current consent-screen/publication status.

Record outside source control:

- Google Cloud project and primary/backup owner;
- website account that owns `GmailOAuthConnection` and a successor;
- add-on deployment ID and authorization service-account email;
- OAuth audience/publication/verification/security-assessment status;
- approved shared mailbox and authorized users;
- credential rotation and emergency reconnect procedure.

Deleting the website user that owns the shared Gmail connection currently
cascades deletion of that connection and related mailbox inventory. Do not
delete or deactivate that account without an ownership-transfer plan. Task 2.7
will harden designated-mailbox/ownership behavior behind available
configuration.

## 4. Secret and key management

- Store secrets only in the provider's secret manager/environment controls.
- Apply least privilege and require MFA on GitHub, Railway, database, Google,
  Cloudinary, and OpenAI accounts.
- Keep a named backup administrator; do not tie business credentials to one
  employee without succession access.
- Rotate provider credentials according to policy and after any suspected
  exposure.
- Treat `DJANGO_SECRET_KEY` as a data-encryption dependency, not just a cookie
  secret. Rotation invalidates saved Gmail token ciphertext. Schedule a shared
  mailbox reconnect immediately after a deliberate rotation.
- Never log OAuth tokens, API keys, signed handoff URLs, full environment dumps,
  or customer attachments.

## 5. Production verification checklist

Record operator, evidence link, and UTC time for every checked item.

- [ ] `DEBUG=0`; strong unique `DJANGO_SECRET_KEY`; intended hosts/origins only.
- [ ] HTTPS redirect, HSTS, secure cookies, and proxy SSL header behavior tested.
- [ ] Production `DATABASE_URL` points to the intended PostgreSQL database.
- [ ] Database users are least-privileged and TLS/backup/restore settings verified.
- [ ] Railway pre-deploy migration command is explicit and migration plan reviewed.
- [ ] Private quotation evidence has an accepted durability/recovery posture.
- [ ] Google scopes, mailbox, owners, publication/verification, and reconnect tested.
- [ ] OpenAI project/model, processor terms, retention, and privacy gates approved.
- [ ] Sentry/logging excludes unnecessary PII and access is restricted.
- [ ] Audit/history Django administration is read-only after Task 1.8 is deployed.
- [ ] Ambiguous-email reconciliation and no-blind-retry runbook tested.
- [ ] RPO/RTO and a successful restore drill are recorded.
- [ ] Incident contacts and credential revocation access are current.

## 6. Security incident runbook

### Suspected secret exposure

1. Preserve the alert, timestamps, affected systems, and minimal forensic
   evidence. Do not paste the secret into a ticket/chat.
2. Revoke or disable the exposed credential at its provider immediately.
3. Rotate the credential and update only approved secret stores.
4. If `DJANGO_SECRET_KEY` changed, invalidate sessions as expected and reconnect
   the shared Gmail mailbox.
5. Search repository history, build logs, Railway logs, browser bundles, and
   monitoring for the exposure without printing the value.
6. Assess data/customer impact, notify the designated owner, and follow legal
   or contractual notification rules.
7. Only then decide whether coordinated history rewriting is necessary.
   History rewriting is disruptive, does not revoke a secret, and must not be
   followed by an uncoordinated force push.

### Ambiguous customer email

1. Do not click send again and do not create another delivery record.
2. Use **Check Gmail status** / the reconciliation endpoint.
3. If Gmail is unavailable or returns no verified match, keep the delivery
   locked and inspect the shared Sent mailbox using the stable RFC Message-ID.
4. Escalate multiple matches or mismatched From/thread evidence; never mark sent
   based only on subject, recipient, or timing.
5. Reconciliation must never invoke Gmail send.

### Lost private evidence

1. Preserve the broken database reference and relevant logs.
2. Do not substitute a similarly named file without hash/evidence verification.
3. Recover from the approved durable store/backup, or re-fetch the canonical
   customer document with an audit note.
4. Confirm the affected quotation rows and evidence before further action.

## 7. Known gaps and scheduled hardening

- No production `lock_timeout` or `statement_timeout` is set by application
  configuration; Task 2.8 prepares bounded handling.
- The inspected Railway deployment had no pre-deploy command, health check,
  volume, or Sentry DSN.
- Default private quotation storage is ephemeral on Railway without a volume.
- There is no automated retention/deletion schedule, SLO/alert set, cost
  budget, or stuck-delivery sweeper.
- A sent-email preview has no stale-preview version guard yet (Task 2.1).
- The delivery ledger is mutable aggregate state, not immutable provider-attempt
  history (Task 2.2).
- Formal credential ownership transfer remains Task 2.7.

These are explicit risks, not permission to bypass the existing review,
evidence, blank-price, recipient-verification, or send-reconciliation controls.

## 8. References

- [Django security](https://docs.djangoproject.com/en/5.2/topics/security/)
- [Django deployment checklist](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/)
- [OWASP Web Security Testing Guide](https://owasp.org/www-project-web-security-testing-guide/)
- [Gmail API scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)
- [Google OAuth app audience](https://support.google.com/cloud/answer/15549945)
- [Architecture reference](GMAIL_QUOTATION_ARCHITECTURE_REVIEW.md)
- [Operations runbook](OPERATIONS.md)
