# Security Configuration and Operations Guide

| Field | Value |
|---|---|
| Document version | 2.7.0 |
| Status | Repository control reference and operator checklist; not a certification |
| Owner | Al Ameen platform maintainers and designated production operators |
| Last verified | 2026-08-01 |
| Reviewed code | Hardening baseline `d88b767` through Task 2.6 checkpoint `3da9b5c`, plus the Task 2.7 worktree checkpoint |
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
- The shipped editor verifies its displayed quotation revision before opening
  an email preview. New/retry sends then require the employee's keyed email-
  preview fingerprint. A missing or changed quotation/PDF/source review is
  blocked under the authoritative render locks until the employee explicitly
  refreshes, reviews, and clicks Send again.
- PDF and raw MIME bytes are built while their database dependencies are
  locked. Before Gmail is called, the exact MIME (including the PDF), complete
  metadata digest, one provider-attempt row, and aggregate `sending` state are
  committed atomically. A known-safe retry verifies and reuses those persisted
  bytes rather than rebuilding customer content.
- Outbound snapshot/attempt/event models reject model and bulk mutation, are
  view-only in administration, and never expose raw MIME through the
  application API or admin form. Provider results and reconciliation proof are
  append-only event rows; later proof never overwrites the original ambiguous
  network/HTTP fact.
- One aggregate delivery record per quotation revision, database locking, and
  delivery-state checks prevent ordinary double sends.
- An ambiguous result becomes `unknown`; blind retry is blocked. Reconciliation
  verifies Sent/From/RFC Message-ID/thread evidence and never sends email.
- Forwarded inquiry bodies are transient, bounded, unverified evidence. Strict
  Gmail/Outlook structure is required before preservation; embedded forwarding
  headers never replace the physical Gmail sender or participate in exact
  identity, contact selection, or reply routing. A forwarded transport sender
  is also excluded from deterministic customer recommendation.
- Customer matching canonicalizes domains with pinned non-transitional IDNA
  2008/UTS #46 rules, rejects malformed/IP/single-label identities, preserves
  local-part dots and `+tags`, and blocks regional public-mail domains from
  private-domain inference. Company/domain-name, acronym, and a different
  sender on the same domain remain review-only for automatic LPO linking.
  Automatic identity requires an exact saved sender, exact quotation
  reference, or customer identity in the selected attachment.
  Multiple/duplicate physical `From` fields or addresses fail closed in Gmail
  intake, mailbox-PO matching, reply preparation, and Sent reconciliation.
  A singleton `Reply-To` is routing-only and is considered only after the
  physical `From` check; cross-domain intake use remains visibly warned.
- Newly prepared Gmail replies record the strict sender-validation contract.
  A frozen failed reply without that contract cannot be retried, preventing a
  historical weakly parsed sender or `Reply-To` from reaching Gmail.
- AI-provided company/contact identity must cite at least one current evidence
  source. Unconfirmed identity results from any pre-v4 or unversioned matcher
  are quarantined until Gmail evidence is reanalyzed; confirmed history is not
  rewritten.

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

### Attachment and image validation

- Task 2.4 inspects supported PDF and Excel containers before normal parser
  use. Definite malformed/encrypted/unsafe containers and hard archive limits
  fail closed; MIME mismatches and business-fidelity features such as formulas,
  hidden/merged cells, external links, and PDF active-content markers remain
  warning-only after byte validation.
- PDF cross-reference/object-stream structure is bounded before `PdfReader`;
  object, stream, geometry, embedded-image, render, text, word, and table output
  limits then apply. Unsupported non-image content filters skip local page
  traversal, while an xref/object stream that cannot be decoded under the
  preflight limits fails closed. Local AI rendering repeats the inspection
  immediately before opening the renderer. Reachable inline images in page,
  Form, Pattern, Type3, soft-mask, or annotation-appearance content also block
  local rendering because their geometry is not interpreted speculatively.
- Gmail native analysis blocks the complete provider call for the selected
  source set if any selected supported document fails inspection, cannot be
  fetched/prepared, or exceeds a file-count/byte boundary. The rejection reason
  and digest remain bounded evidence, prepared siblings are marked skipped, and
  no failed attachment becomes item evidence or produces rows. More than 100
  attachment metadata entries on a selected inbound message use the same
  fail-closed path; only a Gmail `SENT` message whose sole parsed `From` address
  is the exact connected mailbox is exempt as outbound context.
- Product and quotation-branding images must agree across extension, declared
  MIME when supplied, and decoded PNG/JPEG/WebP format, and pass byte,
  dimension, pixel, complete-decode, and single-frame limits before persistence.
  This includes company brand logos as well as product, line, quotation logo,
  signature, and stamp uploads.
- These are bounded validation controls, not malware/antivirus scanning or a
  parser sandbox. Legacy `.xls` and binary `.xlsb` content receive limited
  fidelity inspection. Native PDF image-codec complexity and upstream Gmail
  JSON/MIME-tree materialization remain in-process availability risks. See
  [ATTACHMENT_SECURITY_AND_SPREADSHEET_FIDELITY.md](ATTACHMENT_SECURITY_AND_SPREADSHEET_FIDELITY.md).

### Database and file access

- Confidential quotation sources use a dedicated Django storage alias, never
  the public/default media storage. New references are content-addressed,
  omit customer filenames, and are checked against SHA-256 before use.
- Unsafe, absolute, URL, Gmail pseudo-, traversal, and unknown-version refs are
  rejected before any backend access. A versioned ref may use the previous
  local copy only after definite active-backend absence and full embedded-hash
  verification; a backend outage never permits fallback.
- Existing unversioned refs remain local-first during migration. Provider URLs
  are never generated by the evidence abstraction; authenticated endpoints
  return bounded derived content with private/no-store browser headers. Stored
  objects are bounded before parser use, and new writes are read back and
  integrity checked before their refs are returned.

- PostgreSQL connections support health checks, bounded connect timeout, and
  disabled server-side cursors.
- Private source references are path-confined and served only through
  authenticated application paths.
- API audit/history viewsets are read-only where defined. The hardening branch
  also makes AI parse logs, generated company price history, and quotation
  audit logs view-only in Django administration; verify the deployed commit
  before relying on that production control.

## 2. Data processors and retained categories

| Boundary | Data that may cross it | Current control | Operator decision still required |
|---|---|---|---|
| Railway application | accounts, quotations, evidence, audit and delivery state | authenticated APIs, TLS at platform edge, environment secrets | access review, log retention, region/contract |
| PostgreSQL provider | application records, encrypted Gmail credentials, and exact outbound quotation MIME/PDF snapshots | database authentication/TLS configuration; raw MIME excluded from application APIs/admin forms | backup, restore window, access review, RPO/RTO, deletion policy |
| Google/Gmail | mailbox contents and outgoing quotation | OAuth scopes, canonical re-fetch, verified reply/send | publication/verification, owners, retention/legal basis |
| OpenAI API | bounded bodies/documents or parsed rows | explicit feature gates, strict schema, `store=false` | approved project, retention/residency/DPA/security assessment |
| Cloudinary | configured catalog/branding media | provider credentials and Django storage | account controls and backup requirements |
| Private evidence storage alias | manual/import/contract-intelligence source files | opaque content-addressed keys, integrity verification, confined legacy reads, no public URLs | approved durable provider/volume, access policy, migration and recovery; current Railway snapshot has no volume |

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

Migration `0037` protects the shared Gmail connection from deletion with its
current website owner. Ownership must be transferred first; deleting the
current owner now fails instead of cascading into the credential or mailbox
provenance. Task 2.7 also adds a disabled-by-default designated-mailbox gate and
an operator-only, audited transfer command. Enable the gate only after the
deployed expected address and the physical Google profile are verified. With
the gate enabled, invalid/missing configuration and a different Google account
fail closed before token/designation persistence; every operational Gmail token
read also rejects a mismatched stored connection. OAuth persistence rechecks
the actor's current active/staff/owner-or-superuser authority after Google
returns, refuses cross-mailbox credential-row reuse, reuses one unambiguous
legacy row for the same physical mailbox, and serializes PostgreSQL first
connects for that mailbox. The transfer command changes only the owner FK,
rejects conflicting destination connections, and never logs token material.
Its immutable audit payload snapshots the initiating superuser identity. Shell
access is the command's authentication boundary; the named active superuser is
an authorization precondition and audit attribution, not proof of the human
shell operator's identity.

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
- [ ] Representative valid, warning-only, and hard-failure attachments were smoke-tested without bypassing employee review or blank inquiry selling prices.
- [ ] Sentry/logging excludes unnecessary PII and access is restricted.
- [ ] The deployed commit includes and verifies Task 1.8 read-only audit/history administration.
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
- The private-evidence abstraction and dual reader are implemented, but its
  default local backend remains ephemeral on Railway without a volume. No live
  durable provider, credentials, legacy copy, backup, or restore drill is
  configured by Task 2.3.
- Abandoned manual previews and price-reference processing can leave
  unreferenced private objects. Contract-intelligence may retain supported
  Gmail attachments. No destructive cleanup was added without an approved
  retention/legal-hold policy.
- There is no automated retention/deletion schedule, SLO/alert set, cost
  budget, or stuck-delivery sweeper.
- The stale-preview guard fingerprints database/config asset identities but
  cannot detect remote bytes replaced out of band at the same storage key
  before the first outbound snapshot is created. Once an attempt begins, exact
  MIME bytes are persisted and verified for every retry.
- Exact outbound MIME duplicates customer email/PDF data in PostgreSQL. It is
  capped at 35 MiB per delivery and hidden from normal API/admin views, but an
  approved retention/deletion policy, database access review, backup scope,
  and storage-growth budget remain operator decisions. Application-level
  immutability does not prevent a privileged database administrator or raw SQL
  from changing rows; the snapshot digest detects corruption before retry.
- A crash in the commit-before-Gmail-call gap leaves a durable attempt without
  a result event even if the network call was never reached. It remains locked
  for reconciliation; the system does not trade duplicate-send safety for an
  automatic retry.
- Reverse foreign-key deletion paths can acquire a dependency before a
  quotation/line lock, while reviewed rendering uses quotation-first order.
  PostgreSQL safely aborts a deadlock participant, but explicit timeout/deadlock
  normalization remains Task 2.8 availability work.
- Production activation and an actual credential ownership transfer remain
  operator actions; repository defaults keep Task 2.7 enforcement disabled.
- Attachment checks do not provide malware/AV detection or parser isolation.
  PDF marker inspection is not exhaustive, and legacy `.xls`/`.xlsb` formula,
  hidden-content, external-link, macro, encryption, and embedded-object coverage
  is limited. Passing validation is not proof that business data is trustworthy
  or that extraction is accurate.

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
- [Attachment security and spreadsheet fidelity](ATTACHMENT_SECURITY_AND_SPREADSHEET_FIDELITY.md)
