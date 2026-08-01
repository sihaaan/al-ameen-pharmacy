# Al Ameen Gmail Quotation Add-on

| Field | Value |
|---|---|
| Document version | 1.3.0 |
| Status | Developer-deployment guide for the configured consumer Gmail mailbox |
| Owner | Must be assigned in the Google/Railway operations record |
| Last verified | 2026-08-01 |
| Reviewed code | Hardening baseline `d88b767` through Task 2.6 checkpoint `3da9b5c`, plus the Task 2.7 worktree checkpoint |
| Add-on deployment ID/publication status | Unknown; verify in Google Cloud |

This directory contains the Google Workspace HTTP add-on manifest template.
The add-on displays the currently open thread, lets an employee use the current
message, select several messages, or let AI classify messages inside that one
thread, then opens the website review. It never creates a quotation or sends a
customer email by itself.

The configured mailbox is `pharmacydxb@gmail.com`, a consumer Gmail account.
It can use an unpublished developer install. A private organization-wide
Marketplace listing requires a Google Workspace organization; do not describe
the consumer-account deployment as a private Workspace release.

## 1. Two separate Google permission sets

| Component | Permissions | Purpose |
|---|---|---|
| HTTP add-on manifest | `gmail.addons.execute`, `gmail.addons.current.message.metadata`, `userinfo.email` | Open-message/thread context and verified user identity |
| Website mailbox OAuth | `gmail.readonly`, `gmail.send` | Canonical shared-mailbox evidence and explicit reviewed delivery |

The add-on manifest does not grant mailbox-wide reading or sending. The
Railway backend separately uses the connected website mailbox after verifying
the add-on request. An older website connection that lacks `gmail.send` must be
reconnected before reviewed delivery.

`gmail.readonly` is a restricted scope. Depending on publication and server-
side data processing, Google verification and possibly a security assessment
may be required. Source control cannot prove the project's current status.
External OAuth apps left in test mode can have short-lived test-user
authorizations (commonly seven days); verify the current consent-screen status
when recurring reconnects occur.

## 2. Prepare the Google Cloud project

1. Sign in with an account authorized to administer the project associated
   with `pharmacydxb@gmail.com`.
2. Enable Gmail API, Google Workspace Marketplace SDK, and Google Workspace
   Add-ons API.
3. Configure the OAuth consent audience. While unpublished, add
   `pharmacydxb@gmail.com` as a test user and record the test-mode expiry risk.
4. Review the exact scopes in `deployment.template.json`.
5. Confirm the template's production URLs and logo before deployment. The
   checked-in template contains no secrets.
6. Record a primary and backup Google Cloud owner, deployment ID,
   authorization service account, OAuth audience/status, and the website user
   who owns the shared Gmail connection.

Endpoints currently referenced by the implementation:

```text
Contextual: https://al-ameen-pharmacy-production.up.railway.app/api/quotations/gmail/addon/contextual/
Action:     https://al-ameen-pharmacy-production.up.railway.app/api/quotations/gmail/addon/action/
Website:    https://www.ameenpharmacy.ae/admin?admin_tab=quotations&quotation_tab=inquiries
```

## 3. Create and install a developer deployment

Run from the repository root with a current Google Cloud CLI. Use the checked-
in template directly; there is no required `deployment.json` file.

```text
gcloud auth login
gcloud config set project YOUR_GOOGLE_CLOUD_PROJECT_ID
gcloud workspace-add-ons deployments create al-ameen-quotation \
  --deployment-file=gmail_addon/deployment.template.json
gcloud workspace-add-ons deployments describe al-ameen-quotation
gcloud workspace-add-ons deployments install al-ameen-quotation
gcloud workspace-add-ons get-authorization
```

The final command returns the deployment authorization service-account
identity required by the backend. Do not commit its tokens or a filled secret
file. The Cloud Console alternative is **Google Workspace Marketplace SDK >
HTTP Deployments**; install while signed in as the intended mailbox.

On first use, approve every required granular permission. Missing manifest
permissions should produce a permission card rather than mailbox access.

## 4. Railway environment

Keep the integration disabled until every non-secret identity and URL matches
the Google deployment:

```text
GMAIL_ADDON_ENABLED=0
GMAIL_ADDON_SERVICE_ACCOUNT_EMAIL=service-account@project.iam.gserviceaccount.com
GMAIL_ADDON_SHARED_MAILBOX_EMAIL=pharmacydxb@gmail.com
GMAIL_ADDON_OAUTH_CLIENT_ID=authorization-resource-client-id.apps.googleusercontent.com
GMAIL_ADDON_ALLOWED_AUDIENCES=https://al-ameen-pharmacy-production.up.railway.app/api/quotations/gmail/addon/contextual/,https://al-ameen-pharmacy-production.up.railway.app/api/quotations/gmail/addon/action/
GMAIL_ADDON_CONTEXTUAL_URL=https://al-ameen-pharmacy-production.up.railway.app/api/quotations/gmail/addon/contextual/
GMAIL_ADDON_ACTION_URL=https://al-ameen-pharmacy-production.up.railway.app/api/quotations/gmail/addon/action/
GMAIL_ADDON_HANDOFF_URL=https://www.ameenpharmacy.ae/admin?admin_tab=quotations&quotation_tab=inquiries
GMAIL_ADDON_HANDOFF_TTL_SECONDS=1800
GMAIL_ADDON_MAX_THREAD_MESSAGES=50
GMAIL_INQUIRY_ATTACHMENT_VIEW_MAX_BYTES=20971520
```

`GMAIL_ADDON_MAX_THREAD_MESSAGES` is clamped to the safe range `1..100` by
both the add-on and the website analyzer. If the open message is older than the
newest configured window, it occupies one slot and the other slots contain the
newest thread messages. Current-message and AI-thread action clicks reuse the
canonical open-message identity without re-fetching sidebar summaries;
selected-message imports always re-fetch membership before issuing a handoff.

`GMAIL_ADDON_ALLOWED_AUDIENCES` must contain both exact endpoint URLs; do not
use a wildcard. `GMAIL_ADDON_OAUTH_CLIENT_ID` is the client ID in the add-on
deployment's **Authorization Resource**, not the website Gmail OAuth client.
The backend intentionally does not fall back between them.

The website Gmail connection also needs the canonical `GOOGLE_OAUTH_*`
variables from [backend/.env.example](../backend/.env.example). AI analysis
additionally requires the explicitly approved provider/model/timeout/privacy
settings. Enable `GMAIL_ADDON_ENABLED=1` only after the verification below.

## 5. Verification

Automated local checks use mocked Google/Gmail calls:

```text
cd backend
python manage.py test quotations.test_gmail_addon quotations.test_gmail_inquiry_import --noinput
```

For the developer deployment, record evidence for each item:

- [ ] Deployment `describe` output matches the intended project/template.
- [ ] Service-account email and both exact audiences match Railway.
- [ ] Signed callback for `pharmacydxb@gmail.com` opens the sidebar.
- [ ] A different mailbox is rejected.
- [ ] Current, selected, and AI-thread modes open an opaque website handoff.
- [ ] Login returns to the exact import review.
- [ ] Company, rows, uncertainty, and evidence require employee confirmation.
- [ ] Selling prices remain blank.
- [ ] Repeated clicks reuse the import and confirmed threads reopen the quote.
- [ ] The add-on itself cannot send email.
- [ ] Missing/revoked/disconnected or failed-refresh website Gmail credentials
      show a reconnect path instead of raw credentials/errors.

The application analysis is synchronous and can take tens of seconds. The
website shows/polls resumable state; this is not a background worker.

## 6. Rotation, reconnect, and ownership

- Reconnect the website mailbox when scopes change, Google revokes the grant,
  refresh fails, external test authorization expires, or `DJANGO_SECRET_KEY`
  is rotated.
- Website designated-mailbox enforcement is separately guarded by
  `QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=0`. Leave it disabled
  until the deployed `GMAIL_ADDON_SHARED_MAILBOX_EMAIL` and the connected
  Google profile have been independently verified as the same mailbox. When
  enabled, missing/invalid configuration and a different Google profile fail
  closed before any credential or shared designation is persisted.
- Do not delete the website user that owns `GmailOAuthConnection` until an
  ownership transfer has been applied and verified. Migration `0037` makes an
  attempted current-owner deletion fail with protected-related-data behavior
  instead of cascading through the Gmail credential and provenance. The
  operator command is a dry run unless `--apply` is supplied:

  ```text
  python manage.py transfer_shared_gmail_owner \
    --initiated-by ACTIVE_SUPERUSER_USERNAME \
    --new-owner ACTIVE_STAFF_USERNAME \
    --confirm-mailbox exact-shared-mailbox@example.com

  python manage.py transfer_shared_gmail_owner \
    --initiated-by ACTIVE_SUPERUSER_USERNAME \
    --new-owner ACTIVE_STAFF_USERNAME \
    --confirm-mailbox exact-shared-mailbox@example.com \
    --apply
  ```

  Shell access remains the trusted boundary; `--initiated-by` is checked and
  recorded for audit attribution, not used as independent authentication. The
  destination must not already own another Gmail connection. The command
  changes only the owner FK and preserves the connection ID, encrypted tokens,
  scopes, status, and Gmail-derived provenance. The token-free audit payload
  also snapshots the initiating superuser so attribution remains after account
  deletion. Run the dry run again in the reverse direction before using
  `--apply` for rollback; do not reverse migration `0037` as a routine rollback.
- Rotate the add-on authorization resource/service account only with a staged
  Railway audience/identity update and developer smoke test.
- Maintain a backup administrator who can access Google Cloud, Railway, and
  the website without sharing one person's credentials.

Never commit OAuth credentials, ID/access/refresh tokens, raw Gmail message or
thread IDs, generated handoff URLs, or an environment dump.

## 7. Related references

- [Architecture reference](../GMAIL_QUOTATION_ARCHITECTURE_REVIEW.md)
- [Deployment guide](../DEPLOYMENT.md)
- [Security guide](../SECURITY.md)
- [Operations runbook](../OPERATIONS.md)
- [Gmail API scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)
