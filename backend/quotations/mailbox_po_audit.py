"""Canonical, mailbox-wide Gmail purchase-order inventory.

This module deliberately does not import or update quotation outcome services.
It inventories inbound mail once, with Gmail pagination, so quote matching can
operate against a complete and auditable source set instead of repeatedly
searching a capped candidate window per quotation.
"""

import hashlib
import os
import re
import secrets
import urllib.parse
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.db.models import Min
from django.utils import timezone

from .contract_intelligence import (
    GMAIL_API_BASE,
    MAX_ATTACHMENT_BYTES,
    SUPPORTED_ATTACHMENT_EXTENSIONS,
    _attachment_refs,
    _decode_gmail_data,
    _header,
    _json_request,
    _message_body_parts,
    _message_datetime,
    _trim_quoted_reply,
    _walk_parts,
    get_valid_access_token,
)
from .import_parsers import parse_file_preview
from .models import (
    GmailOAuthConnection,
    MailboxPOAuditFailure,
    MailboxPOAuditRun,
    MailboxPOAuditRunMessage,
    MailboxPOMessage,
    Quotation,
)


DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500
MAX_CANDIDATE_ATTACHMENTS = 10
MAX_TOTAL_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_RUN_ERRORS = 500
MAX_MESSAGE_FETCH_ATTEMPTS = 3
SCAN_LEASE_SECONDS = 10 * 60

PURCHASE_ORDER_SIGNAL = re.compile(
    r"\b(?:local\s+purchase\s+order|purchase\s+order|l\.?\s*p\.?\s*o\.?|p\.?\s*o\.?)\b",
    re.IGNORECASE,
)
POSSIBLE_ORDER_SIGNAL = re.compile(
    r"\b(?:order|ordered|award(?:ed)?|approv(?:e|ed|al)|confirm(?:ed|ation)?|proceed|quotation|quote)\b",
    re.IGNORECASE,
)
QUOTATION_REFERENCE = re.compile(r"\bQT[-_\s]?\d{8}[-_]\d{4}\b", re.IGNORECASE)
LABELLED_PO_REFERENCE = re.compile(
    r"\b(?P<label>L\.?\s*P\.?\s*O\.?|P(?:URCHASE)?\.?\s*O(?:RDER)?\.?)"
    r"\s*(?:NO\.?|NUMBER|#)?\s*[:#-]?\s*(?P<value>[A-Z0-9][A-Z0-9_./-]{2,})",
    re.IGNORECASE,
)
PREFIXED_PO_REFERENCE = re.compile(r"\b(?P<value>(?:LPO|MPO|PO)[-_]?[A-Z0-9][A-Z0-9_./-]{3,})\b", re.IGNORECASE)
REFERENCE_STOP_WORDS = {
    "ATTACHED",
    "CONFIRM",
    "CONFIRMED",
    "DETAILS",
    "DOCUMENT",
    "IS",
    "NO",
    "NUMBER",
    "ORDER",
    "PLEASE",
}


def _bounded_errors(existing, additions):
    return [*(existing or []), *(additions or [])][-MAX_RUN_ERRORS:]


def _database_text(value):
    # PostgreSQL text/JSONB cannot store the NUL character. Corrupt MIME input
    # must not make an otherwise resumable page permanently unscannable.
    return str(value or "").replace("\x00", "")


def _safe_json(value):
    if isinstance(value, dict):
        return {_database_text(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value]
    if isinstance(value, str):
        return _database_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _database_text(value)


def earliest_eligible_quote_boundary():
    """Return the literal first non-historical quotation creation time."""

    return (
        Quotation.objects.filter(is_historical_import=False)
        .aggregate(boundary=Min("created_at"))
        .get("boundary")
    )


def build_mailbox_po_query(boundary, cutoff):
    """Build a fixed, bounded global incoming-mail snapshot query.

    ``in:anywhere`` plus ``includeSpamTrash=true`` on the API call makes the
    inventory explicit about including Spam and Trash. ``-from:me`` removes
    outbound mail; replies received from other senders remain included. The
    exclusive ``before`` bound is frozen when the run is created, so mail that
    arrives while Gmail pages are being consumed cannot reorder the snapshot.
    """

    if not boundary:
        raise ValueError("A first quotation date is required for mailbox audit.")
    if not cutoff:
        raise ValueError("A fixed mailbox cutoff is required for mailbox audit.")
    # Gmail's after operator is exclusive, so step back one second to include
    # a message whose timestamp exactly equals the first quote's creation.
    epoch = max(int(boundary.timestamp()) - 1, 0)
    before_epoch = int(cutoff.timestamp())
    if before_epoch <= epoch:
        raise ValueError("The mailbox cutoff must be after the first quotation boundary.")
    return f"in:anywhere after:{epoch} before:{before_epoch} -from:me"


def gmail_list_mailbox_messages(
    connection,
    query,
    *,
    page_size=DEFAULT_PAGE_SIZE,
    page_token="",
    include_spam_trash=True,
):
    """Return one Gmail page without imposing a run-wide result cap."""

    token = get_valid_access_token(connection)
    page_size = min(max(int(page_size or DEFAULT_PAGE_SIZE), 1), MAX_PAGE_SIZE)
    params = {
        "q": query,
        "maxResults": page_size,
        "includeSpamTrash": "true" if include_spam_trash else "false",
    }
    if page_token:
        params["pageToken"] = page_token
    payload = _json_request(
        f"{GMAIL_API_BASE}/messages?{urllib.parse.urlencode(params)}",
        token=token,
    )
    return {
        "messages": payload.get("messages") or [],
        "next_page_token": payload.get("nextPageToken") or "",
        "result_size_estimate": payload.get("resultSizeEstimate"),
    }


def _public_headers(headers):
    return [
        {"name": _database_text(header.get("name")), "value": _database_text(header.get("value"))}
        for header in (headers or [])
        if isinstance(header, dict)
    ]


def fetch_mailbox_message(connection, message_id):
    """Fetch one full Gmail message, including every header and newest body.

    MIME attachment bytes are not fetched here. Inline bytes present in the
    full Gmail response are kept only in a private return key for the bounded
    candidate hydration step and are never persisted directly.
    """

    token = get_valid_access_token(connection)
    payload = _json_request(
        f"{GMAIL_API_BASE}/messages/{urllib.parse.quote(str(message_id))}?format=full",
        token=token,
    )
    message_id = payload.get("id") or str(message_id)
    mime_payload = payload.get("payload") or {}
    # Gmail may move unusually large text/plain or text/html bodies behind an
    # attachmentId even though they are the email body, not a user attachment.
    # Hydrate those parts so "full body fetched" remains literally true.
    for part in _walk_parts(mime_payload):
        mime_type = str(part.get("mimeType") or "").lower()
        body = part.get("body") or {}
        detached_body_id = body.get("attachmentId") or ""
        if (
            mime_type in {"text/plain", "text/html"}
            and not part.get("filename")
            and detached_body_id
            and not body.get("data")
        ):
            detached = _json_request(
                f"{GMAIL_API_BASE}/messages/{urllib.parse.quote(str(message_id))}/attachments/"
                f"{urllib.parse.quote(str(detached_body_id))}",
                token=token,
            )
            body["data"] = detached.get("data") or ""
            part["body"] = body
    headers = mime_payload.get("headers") or []
    body_parts = _message_body_parts(mime_payload)
    newest_body = _trim_quoted_reply("\n".join(value for value in body_parts if value))
    private_attachment_refs = _attachment_refs(mime_payload, message_id, include_inline_data=True)
    public_attachment_refs = [
        {key: value for key, value in attachment.items() if key != "_inline_data"}
        for attachment in private_attachment_refs
    ]
    return {
        "gmail_message_id": message_id,
        "gmail_thread_id": payload.get("threadId") or "",
        "label_ids": [_database_text(label) for label in (payload.get("labelIds") or [])],
        "full_headers": _public_headers(headers),
        "subject": _header(headers, "Subject"),
        "sender": _header(headers, "From"),
        "recipients": _header(headers, "To"),
        "cc": _header(headers, "Cc"),
        "reply_to": _header(headers, "Reply-To"),
        "sent_at": _message_datetime(payload),
        "snippet": payload.get("snippet") or "",
        "newest_body_text": newest_body,
        "attachment_manifest": public_attachment_refs,
        "_attachment_refs": private_attachment_refs,
    }


def extract_po_references(text):
    """Extract auditable quotation/PO reference candidates from message text."""

    text = str(text or "")
    references = []
    seen = set()

    def add(kind, value):
        value = re.sub(r"\s+", "", str(value or "")).strip(" .,:;#").upper()
        if len(value) < 3 or value in REFERENCE_STOP_WORDS or (kind == "po" and not re.search(r"\d", value)):
            return
        key = (kind, value)
        if key not in seen:
            seen.add(key)
            references.append({"kind": kind, "value": value})

    for match in QUOTATION_REFERENCE.finditer(text):
        add("quotation", match.group(0).replace("_", "-"))
    for match in LABELLED_PO_REFERENCE.finditer(text):
        add("po", match.group("value"))
    for match in PREFIXED_PO_REFERENCE.finditer(text):
        add("po", match.group("value"))
    return references


def _is_plausible_document_attachment(attachment):
    if not isinstance(attachment, dict):
        return False
    extension = os.path.splitext(str(attachment.get("filename") or ""))[1].lower()
    if extension not in SUPPORTED_ATTACHMENT_EXTENSIONS:
        return False
    try:
        declared_size = int(attachment.get("size") or 0)
    except (TypeError, ValueError):
        return False
    # A zero size means Gmail did not supply metadata; decoded bytes remain
    # subject to the hard cap in ``_preview_attachment``.
    return 0 <= declared_size <= MAX_ATTACHMENT_BYTES


def classify_mailbox_message(message):
    """Classify broadly enough for review without claiming a quote match."""

    filenames = " ".join(
        str(attachment.get("filename") or "")
        for attachment in (message.get("attachment_manifest") or [])
        if isinstance(attachment, dict)
    )
    text = "\n".join(
        [
            str(message.get("subject") or ""),
            str(message.get("newest_body_text") or ""),
            str(message.get("snippet") or ""),
            filenames,
        ]
    )
    references = extract_po_references(text)
    plausible_documents = [
        attachment
        for attachment in (message.get("attachment_manifest") or [])
        if _is_plausible_document_attachment(attachment)
    ]
    has_po_signal = bool(PURCHASE_ORDER_SIGNAL.search(text))
    has_quote_reference = any(reference["kind"] == "quotation" for reference in references)
    has_order_context = bool(POSSIBLE_ORDER_SIGNAL.search(text))
    restricted_labels = {"SPAM", "TRASH"}.intersection(
        str(label).upper() for label in (message.get("label_ids") or [])
    )

    if has_po_signal:
        classification = MailboxPOMessage.CLASS_PURCHASE_ORDER
        reason = "PO/LPO language found in the newest message, subject, or attachment filename."
    elif plausible_documents and (has_quote_reference or has_order_context):
        classification = MailboxPOMessage.CLASS_POSSIBLE_PO
        reason = "A supported document accompanies quotation/order context and needs review."
    elif plausible_documents:
        classification = MailboxPOMessage.CLASS_POSSIBLE_PO
        reason = "A size-bounded supported document has no PO keyword and must be inspected before exclusion."
    else:
        classification = MailboxPOMessage.CLASS_OTHER
        reason = "No PO/LPO signal or safely inspectable supported document was found."
    if restricted_labels:
        reason = f"{reason} Auto-link blocked because Gmail labels include {', '.join(sorted(restricted_labels))}."
    return {
        "classification": classification,
        "is_relevant": classification != MailboxPOMessage.CLASS_OTHER,
        "auto_link_eligible": not bool(restricted_labels),
        "relevance_reason": reason,
        "extracted_po_references": references,
    }


def _preview_attachment(connection, message_id, attachment, token, *, max_bytes=MAX_ATTACHMENT_BYTES):
    public = {key: value for key, value in attachment.items() if key != "_inline_data"}
    declared_size = int(attachment.get("size") or 0)
    if declared_size > MAX_ATTACHMENT_BYTES:
        return {
            **public,
            "candidate": True,
            "content_fetched": False,
            "status": "skipped",
            "reason": "Attachment exceeds the per-file audit limit.",
        }, 0
    try:
        remaining_budget = max(0, min(int(max_bytes), MAX_ATTACHMENT_BYTES))
    except (TypeError, ValueError):
        remaining_budget = 0
    if declared_size > remaining_budget:
        return {
            **public,
            "candidate": True,
            "content_fetched": False,
            "status": "skipped",
            "reason": "Per-message total attachment byte limit reached.",
        }, 0

    try:
        if attachment.get("_inline_data"):
            content = _decode_gmail_data(attachment["_inline_data"])
        else:
            attachment_id = attachment.get("attachment_id")
            if not attachment_id:
                raise ValueError("Gmail attachment id is missing.")
            payload = _json_request(
                f"{GMAIL_API_BASE}/messages/{urllib.parse.quote(str(message_id))}/attachments/"
                f"{urllib.parse.quote(str(attachment_id))}",
                token=token,
            )
            content = _decode_gmail_data(payload.get("data") or "")
        if len(content) > MAX_ATTACHMENT_BYTES:
            return {
                **public,
                "candidate": True,
                "content_fetched": False,
                "status": "skipped",
                "reason": "Decoded attachment exceeds the per-file audit limit.",
            }, 0
        if len(content) > remaining_budget:
            return {
                **public,
                "candidate": True,
                "content_fetched": False,
                "status": "skipped",
                "reason": "Decoded attachment exceeds the remaining per-message byte limit.",
            }, 0

        upload = SimpleUploadedFile(
            str(attachment.get("filename") or "attachment"),
            content,
            content_type=attachment.get("mime_type") or "application/octet-stream",
        )
        preview = parse_file_preview(upload, store_source=False)
        source_sha256 = preview.get("source_sha256") or hashlib.sha256(content).hexdigest()
        rows = preview.get("lines") or []
        return {
            **public,
            "candidate": True,
            "content_fetched": True,
            "fetched_bytes": len(content),
            "status": "parsed",
            "source_sha256": source_sha256,
            "source_file_ref": preview.get("source_file_ref") or "",
            "source_mime_type": preview.get("source_mime_type") or attachment.get("mime_type") or "",
            "parse_method": preview.get("parse_method") or "",
            "original_text": _database_text(preview.get("original_text"))[:120000],
            "meta": _safe_json(preview.get("meta") or {}),
            "totals": _safe_json(preview.get("totals") or {}),
            "line_count": len(rows),
            "lines": _safe_json(rows),
            "warnings": _safe_json(preview.get("warnings") or []),
        }, len(content)
    except Exception as exc:
        return {
            **public,
            "candidate": True,
            "content_fetched": False,
            "status": "failed",
            "reason": str(exc)[:500],
        }, 0


def hydrate_plausible_attachments(connection, message, *, is_relevant):
    """Fetch/parse bounded document bytes only for a plausible PO message."""

    refs = message.get("_attachment_refs") or message.get("attachment_manifest") or []
    public_refs = [{key: value for key, value in ref.items() if key != "_inline_data"} for ref in refs]
    if not is_relevant:
        return [
            {**ref, "candidate": False, "content_fetched": False, "status": "metadata_only"}
            for ref in public_refs
        ], 0, 0

    candidates = []
    for index, attachment in enumerate(refs):
        extension = os.path.splitext(str(attachment.get("filename") or ""))[1].lower()
        if extension in SUPPORTED_ATTACHMENT_EXTENSIONS:
            candidates.append(index)
    candidate_indexes = set(candidates[:MAX_CANDIDATE_ATTACHMENTS])
    token = get_valid_access_token(connection) if candidate_indexes else ""
    manifest = []
    fetched_bytes = 0
    audited_candidates = 0
    for index, attachment in enumerate(refs):
        public = {key: value for key, value in attachment.items() if key != "_inline_data"}
        extension = os.path.splitext(str(attachment.get("filename") or ""))[1].lower()
        if extension not in SUPPORTED_ATTACHMENT_EXTENSIONS:
            manifest.append(
                {
                    **public,
                    "candidate": False,
                    "content_fetched": False,
                    "status": "metadata_only",
                    "reason": "Unsupported document type for PO parsing.",
                }
            )
            continue
        if index not in candidate_indexes:
            manifest.append(
                {
                    **public,
                    "candidate": True,
                    "content_fetched": False,
                    "status": "skipped",
                    "reason": "Per-message candidate attachment limit reached.",
                }
            )
            continue
        audited_candidates += 1
        remaining_budget = MAX_TOTAL_ATTACHMENT_BYTES - fetched_bytes
        if remaining_budget <= 0 or int(attachment.get("size") or 0) > remaining_budget:
            manifest.append(
                {
                    **public,
                    "candidate": True,
                    "content_fetched": False,
                    "status": "skipped",
                    "reason": "Per-message total attachment byte limit reached.",
                }
            )
            continue
        audited, byte_count = _preview_attachment(
            connection,
            message.get("gmail_message_id"),
            attachment,
            token,
            max_bytes=remaining_budget,
        )
        try:
            byte_count = max(0, int(byte_count))
        except (TypeError, ValueError):
            byte_count = 0
        if byte_count > remaining_budget:
            # The decoded byte count is authoritative when Gmail omitted or
            # understated its size metadata. Never persist parsed fields from
            # an attachment that crosses the cumulative message budget.
            manifest.append(
                {
                    **public,
                    "candidate": True,
                    "content_fetched": False,
                    "status": "skipped",
                    "reason": "Decoded attachment exceeds the remaining per-message byte limit.",
                }
            )
            continue
        manifest.append(audited)
        fetched_bytes += byte_count
    return manifest, audited_candidates, fetched_bytes


def start_mailbox_po_audit(connection, *, requested_by=None, earliest_quote_at=None):
    """Create a new immutable-ledger scan against the shared Gmail mailbox."""

    if not connection or not connection.is_shared:
        raise ValueError("Mailbox PO audits require the designated shared Gmail connection.")
    if connection.status != GmailOAuthConnection.STATUS_CONNECTED:
        raise RuntimeError("The shared Gmail mailbox is not connected.")
    boundary = earliest_quote_at or earliest_eligible_quote_boundary()
    if not boundary:
        raise ValueError("No non-historical quotation exists to establish the mailbox audit boundary.")
    # Gmail's query grammar is second-granular. Flooring is conservative: mail
    # from the current partial second is picked up by the next immutable run.
    cutoff = timezone.now().replace(microsecond=0)
    return MailboxPOAuditRun.objects.create(
        gmail_connection=connection,
        requested_by=requested_by,
        earliest_quote_at=boundary,
        mailbox_cutoff_at=cutoff,
        gmail_query=build_mailbox_po_query(boundary, cutoff),
    )


def _persist_inventory_message(run, message):
    classification = classify_mailbox_message(message)
    manifest, candidate_count, fetched_bytes = hydrate_plausible_attachments(
        run.gmail_connection,
        message,
        is_relevant=classification["is_relevant"],
    )
    attachment_errors = [
        _database_text(attachment.get("reason"))
        for attachment in manifest
        if attachment.get("status") == "failed" and attachment.get("reason")
    ]
    now = timezone.now()
    defaults = {
        "gmail_thread_id": _database_text(message.get("gmail_thread_id"))[:255],
        "mailbox_email": run.gmail_connection.email or "",
        "label_ids": _safe_json(message.get("label_ids") or []),
        "full_headers": _safe_json(message.get("full_headers") or []),
        "subject": _database_text(message.get("subject"))[:500],
        "sender": _database_text(message.get("sender"))[:500],
        "recipients": _database_text(message.get("recipients")),
        "cc": _database_text(message.get("cc")),
        "reply_to": _database_text(message.get("reply_to"))[:500],
        "sent_at": message.get("sent_at"),
        "snippet": _database_text(message.get("snippet")),
        "newest_body_text": _database_text(message.get("newest_body_text")),
        "attachment_manifest": _safe_json(manifest),
        "classification": classification["classification"],
        "is_relevant": classification["is_relevant"],
        "auto_link_eligible": classification["auto_link_eligible"],
        "relevance_reason": classification["relevance_reason"],
        "extracted_po_references": classification["extracted_po_references"],
        "audit_error": "; ".join(attachment_errors)[:2000],
        "last_seen_run": run,
        "last_seen_at": now,
        "full_message_fetched_at": now,
        "attachments_audited_at": now,
        "last_audited_at": now,
    }
    with transaction.atomic():
        inventory, created = MailboxPOMessage.objects.select_for_update().get_or_create(
            gmail_connection=run.gmail_connection,
            gmail_message_id=_database_text(message.get("gmail_message_id"))[:255],
            defaults={**defaults, "first_seen_run": run, "first_seen_at": now},
        )
        if not created:
            for field, value in defaults.items():
                setattr(inventory, field, value)
            inventory.save(update_fields=[*defaults.keys(), "updated_at"])
        MailboxPOAuditRunMessage.objects.get_or_create(
            audit_run=run,
            message=inventory,
        )
    return inventory, created, candidate_count, fetched_bytes, attachment_errors


def _claim_scan_lease(run_id):
    """Claim a page with a short database lock, then release it before I/O."""

    now = timezone.now()
    token = secrets.token_hex(24)
    with transaction.atomic():
        run = (
            MailboxPOAuditRun.objects.select_for_update()
            .select_related("gmail_connection")
            .get(pk=run_id)
        )
        if run.status == MailboxPOAuditRun.STATUS_COMPLETED or run.exhausted:
            raise ValueError("Completed mailbox PO audit runs are immutable; start a new run.")
        if run.scan_lease_token and run.scan_lease_expires_at and run.scan_lease_expires_at > now:
            raise RuntimeError("This mailbox audit page is already being scanned. Try again shortly.")
        if run.gmail_connection.status != GmailOAuthConnection.STATUS_CONNECTED:
            raise RuntimeError("The shared Gmail mailbox is not connected.")
        run.scan_lease_token = token
        run.scan_lease_expires_at = now + timedelta(seconds=SCAN_LEASE_SECONDS)
        run.status = MailboxPOAuditRun.STATUS_RUNNING
        if not run.started_at:
            run.started_at = now
        run.save(
            update_fields=[
                "scan_lease_token",
                "scan_lease_expires_at",
                "status",
                "started_at",
                "updated_at",
            ]
        )
    return run, token


def _renew_scan_lease(run_id, token):
    updated = MailboxPOAuditRun.objects.filter(pk=run_id, scan_lease_token=token).update(
        scan_lease_expires_at=timezone.now() + timedelta(seconds=SCAN_LEASE_SECONDS)
    )
    if not updated:
        raise RuntimeError("This mailbox audit page lease expired and was claimed by another worker.")


def _release_scan_lease(run, token, update_fields):
    """Commit cursor/counters only if this worker still owns the page lease."""

    with transaction.atomic():
        stored = MailboxPOAuditRun.objects.select_for_update().get(pk=run.pk)
        if stored.scan_lease_token != token:
            raise RuntimeError("This mailbox audit page lease expired and was claimed by another worker.")
        for field in update_fields:
            setattr(stored, field, getattr(run, field))
        stored.scan_lease_token = ""
        stored.scan_lease_expires_at = None
        stored.save(
            update_fields=[
                *update_fields,
                "scan_lease_token",
                "scan_lease_expires_at",
                "updated_at",
            ]
        )
    return stored


def _record_message_failure(run, message_id, error):
    now = timezone.now()
    with transaction.atomic():
        failure, _ = MailboxPOAuditFailure.objects.select_for_update().get_or_create(
            audit_run=run,
            gmail_message_id=message_id,
            defaults={
                "page_token": run.page_token,
                "first_failed_at": now,
                "last_failed_at": now,
            },
        )
        if failure.status == MailboxPOAuditFailure.STATUS_TOMBSTONED:
            return failure
        failure.page_token = run.page_token
        failure.attempts += 1
        failure.last_error = _database_text(error)[:2000]
        failure.last_failed_at = now
        failure.resolved_at = None
        if failure.attempts >= MAX_MESSAGE_FETCH_ATTEMPTS:
            failure.status = MailboxPOAuditFailure.STATUS_TOMBSTONED
            failure.tombstoned_at = now
        else:
            failure.status = MailboxPOAuditFailure.STATUS_RETRYING
            failure.tombstoned_at = None
        failure.save(
            update_fields=[
                "page_token",
                "attempts",
                "status",
                "last_error",
                "last_failed_at",
                "tombstoned_at",
                "resolved_at",
            ]
        )
    return failure


def _resolve_message_failure(run, message_id):
    MailboxPOAuditFailure.objects.filter(
        audit_run=run,
        gmail_message_id=message_id,
        status=MailboxPOAuditFailure.STATUS_RETRYING,
    ).update(
        status=MailboxPOAuditFailure.STATUS_RESOLVED,
        resolved_at=timezone.now(),
        tombstoned_at=None,
    )


def _failed_page(run, token, entry):
    run.status = MailboxPOAuditRun.STATUS_FAILED
    run.errors = _bounded_errors(run.errors, [entry])
    run.incomplete_messages = MailboxPOAuditFailure.objects.filter(
        audit_run=run,
        status=MailboxPOAuditFailure.STATUS_TOMBSTONED,
    ).count()
    run.last_page_at = timezone.now()
    return _release_scan_lease(
        run,
        token,
        ["status", "errors", "incomplete_messages", "last_page_at"],
    )


def scan_mailbox_po_audit_page(run, *, page_size=DEFAULT_PAGE_SIZE):
    """Process one Gmail page without holding a database lock across I/O.

    A message that persistently fails is retried across resumptions and then
    recorded as a tombstone. That makes the omission explicit while allowing
    the immutable Gmail page cursor to advance.
    """

    run, lease_token = _claim_scan_lease(run.pk)
    if run.status == MailboxPOAuditRun.STATUS_COMPLETED or run.exhausted:
        raise ValueError("Completed mailbox PO audit runs are immutable; start a new run.")

    try:
        page = gmail_list_mailbox_messages(
            run.gmail_connection,
            run.gmail_query,
            page_size=page_size,
            page_token=run.page_token,
            include_spam_trash=True,
        )
    except Exception as exc:
        return _failed_page(
            run,
            lease_token,
            {"page_token": run.page_token, "error": str(exc)[:1000]},
        )
    _renew_scan_lease(run.pk, lease_token)

    page_relevant = 0
    page_candidates = 0
    page_bytes = 0
    page_errors = []
    messages = page.get("messages") or []
    for index, message_ref in enumerate(messages):
        message_id = str((message_ref or {}).get("id") or "")
        if not message_id:
            missing_digest = hashlib.sha256(
                f"{run.page_token}:{index}".encode("utf-8", errors="ignore")
            ).hexdigest()[:32]
            message_id = f"__missing_gmail_id__:{missing_digest}"
            _renew_scan_lease(run.pk, lease_token)
            failure = _record_message_failure(run, message_id, "Gmail returned a message without an id.")
            entry = {
                "page_token": run.page_token,
                "gmail_message_id": message_id,
                "attempts": failure.attempts,
                "tombstoned": failure.status == MailboxPOAuditFailure.STATUS_TOMBSTONED,
                "error": failure.last_error,
            }
            if failure.status != MailboxPOAuditFailure.STATUS_TOMBSTONED:
                return _failed_page(run, lease_token, entry)
            page_errors.append(entry)
            continue

        existing_failure = MailboxPOAuditFailure.objects.filter(
            audit_run=run,
            gmail_message_id=message_id,
            status=MailboxPOAuditFailure.STATUS_TOMBSTONED,
        ).first()
        if existing_failure:
            page_errors.append(
                {
                    "page_token": run.page_token,
                    "gmail_message_id": message_id,
                    "attempts": existing_failure.attempts,
                    "tombstoned": True,
                    "error": existing_failure.last_error,
                }
            )
            continue
        try:
            _renew_scan_lease(run.pk, lease_token)
            message = fetch_mailbox_message(run.gmail_connection, message_id)
            _renew_scan_lease(run.pk, lease_token)
            inventory, _, candidates, fetched_bytes, attachment_errors = _persist_inventory_message(run, message)
            _renew_scan_lease(run.pk, lease_token)
        except Exception as exc:
            _renew_scan_lease(run.pk, lease_token)
            failure = _record_message_failure(run, message_id, exc)
            entry = {
                "page_token": run.page_token,
                "gmail_message_id": message_id,
                "attempts": failure.attempts,
                "tombstoned": failure.status == MailboxPOAuditFailure.STATUS_TOMBSTONED,
                "error": failure.last_error,
            }
            if failure.status != MailboxPOAuditFailure.STATUS_TOMBSTONED:
                return _failed_page(run, lease_token, entry)
            page_errors.append(entry)
            continue
        _resolve_message_failure(run, message_id)
        page_relevant += int(inventory.is_relevant)
        page_candidates += candidates
        page_bytes += fetched_bytes
        page_errors.extend(
            {
                "page_token": run.page_token,
                "gmail_message_id": message_id,
                "error": error,
            }
            for error in attachment_errors
        )

    next_page_token = page.get("next_page_token") or ""
    exhausted = not bool(next_page_token)
    result_size_estimate = page.get("result_size_estimate")
    try:
        result_size_estimate = max(int(result_size_estimate), 0) if result_size_estimate is not None else None
    except (TypeError, ValueError):
        result_size_estimate = None
    now = timezone.now()
    run.page_token = next_page_token
    run.exhausted = exhausted
    run.result_size_estimate = result_size_estimate
    run.pages_scanned += 1
    run.messages_scanned += len(messages)
    run.messages_created = MailboxPOMessage.objects.filter(first_seen_run=run).count()
    run.relevant_messages += page_relevant
    run.incomplete_messages = MailboxPOAuditFailure.objects.filter(
        audit_run=run,
        status=MailboxPOAuditFailure.STATUS_TOMBSTONED,
    ).count()
    run.attachment_candidates += page_candidates
    run.attachment_bytes_fetched += page_bytes
    run.errors = _bounded_errors(run.errors, page_errors)
    run.last_page_at = now
    run.status = MailboxPOAuditRun.STATUS_COMPLETED if exhausted else MailboxPOAuditRun.STATUS_RUNNING
    run.completed_at = now if exhausted else None
    return _release_scan_lease(
        run,
        lease_token,
        [
            "page_token",
            "exhausted",
            "result_size_estimate",
            "pages_scanned",
            "messages_scanned",
            "messages_created",
            "relevant_messages",
            "incomplete_messages",
            "attachment_candidates",
            "attachment_bytes_fetched",
            "errors",
            "last_page_at",
            "status",
            "completed_at",
        ],
    )


def run_mailbox_po_audit(run, *, page_size=DEFAULT_PAGE_SIZE, max_pages=None):
    """Drain Gmail pages until exhausted, failed, or an optional page limit."""

    pages = 0
    while max_pages is None or pages < max(int(max_pages), 0):
        run = scan_mailbox_po_audit_page(run, page_size=page_size)
        pages += 1
        if run.status in {MailboxPOAuditRun.STATUS_COMPLETED, MailboxPOAuditRun.STATUS_FAILED}:
            break
    return run
