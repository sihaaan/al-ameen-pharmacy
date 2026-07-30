# Al Ameen Gmail quotation add-on

This directory contains the Google Workspace HTTP add-on deployment template.
The add-on is intended for a **private Google Workspace organization**. It
shows a contextual card for the message open in Gmail and lets an authorized
shared-mailbox user import the current message, selected thread messages, or
let the existing inquiry workflow choose relevant thread messages.

The add-on callback only issues a short-lived website handoff. Email parsing
and AI work happen after the browser opens the quotation inquiry page, keeping
Google's card callback below its execution limit.

## 1. Prepare the Google Cloud project

1. Use a standard Google Cloud project owned by the same Workspace
   organization as the shared mailbox.
2. Enable the Gmail API, Google Workspace Marketplace SDK, and Google
   Workspace Add-ons API.
3. Configure the OAuth audience as **Internal**.
4. Add the exact scopes from `deployment.template.json`. The
   `gmail.addons.current.message.metadata` scope provides the open-message
   context for the unconditional trigger. The add-on itself does not request
   mailbox-wide Gmail read access; the backend reads the already-connected
   shared mailbox with its existing read-only OAuth connection.
5. Copy `deployment.template.json` to the untracked `deployment.json`. The
   checked-in template already points to the Al Ameen production frontend,
   Railway backend, and publicly readable brand icon.

The contextual endpoint is:

`https://al-ameen-pharmacy-production.up.railway.app/api/quotations/gmail/addon/contextual/`

The action endpoint, configured in Railway rather than in the manifest, is:

`https://al-ameen-pharmacy-production.up.railway.app/api/quotations/gmail/addon/action/`

## 2. Create and test the HTTP deployment

Create the deployment with the Google Cloud CLI:

```text
gcloud workspace-add-ons deployments create al-ameen-quotation \
  --deployment-file=deployment.json
```

Install the unpublished deployment for a test account:

```text
gcloud workspace-add-ons deployments install al-ameen-quotation
```

Use this command to obtain the deployment service-account email:

```text
gcloud workspace-add-ons get-authorization
```

The backend verifies that service account, the exact callback audience, the
Google user ID token, and the configured shared mailbox before it touches any
mail or quotation data.

## 3. Railway environment

Set all of these on the backend service:

```text
GMAIL_ADDON_ENABLED=1
GMAIL_ADDON_SERVICE_ACCOUNT_EMAIL=service-account@project.iam.gserviceaccount.com
GMAIL_ADDON_SHARED_MAILBOX_EMAIL=shared-mailbox@example.com
GMAIL_ADDON_OAUTH_CLIENT_ID=oauth-client-id.apps.googleusercontent.com
GMAIL_ADDON_ALLOWED_AUDIENCES=https://al-ameen-pharmacy-production.up.railway.app/api/quotations/gmail/addon/contextual/,https://al-ameen-pharmacy-production.up.railway.app/api/quotations/gmail/addon/action/
GMAIL_ADDON_CONTEXTUAL_URL=https://al-ameen-pharmacy-production.up.railway.app/api/quotations/gmail/addon/contextual/
GMAIL_ADDON_ACTION_URL=https://al-ameen-pharmacy-production.up.railway.app/api/quotations/gmail/addon/action/
GMAIL_ADDON_HANDOFF_URL=https://www.ameenpharmacy.ae/admin?admin_tab=quotations&quotation_tab=inquiries
GMAIL_ADDON_HANDOFF_TTL_SECONDS=1800
GMAIL_ADDON_MAX_THREAD_MESSAGES=50
GMAIL_INQUIRY_ATTACHMENT_VIEW_MAX_BYTES=20971520
```

`GMAIL_ADDON_ALLOWED_AUDIENCES` must contain both exact endpoint URLs. Do not
use a host wildcard. The shared mailbox must also be the connected shared
Gmail account in the website's quotation settings. Attachment evidence opened
during staff review is clamped to 1-25 MiB; the default above is 20 MiB.

`GMAIL_ADDON_OAUTH_CLIENT_ID` must be the OAuth client ID shown in the
Marketplace SDK HTTP deployment's **Authorization Resource**. It is not the
website Gmail OAuth client ID, and the backend intentionally has no fallback
between the two.

Keep `GMAIL_ADDON_ENABLED=0` until the deployment and environment values match.

## 4. Private release

After testing, configure a private Google Workspace Marketplace listing and
let an administrator install it for the quotation team. A private listing
remains inside the Workspace organization; publishing for consumer or
cross-domain Gmail accounts requires Google's external-app publication flow.

Never commit a filled deployment file, ID token, OAuth credential, Gmail
message identifier, or generated handoff URL.
