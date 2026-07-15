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

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.db.models import Min
from django.utils import timezone

from .ai_parsing import (
    AIParseError,
    AI_SOURCE_VISION,
    AI_STATUS_AVAILABLE,
    MAILBOX_PO_VISION_JSON_SCHEMA,
    clean_pdf_bytes_with_ai,
    settings_ai_status,
)
from .contract_intelligence import (
    GMAIL_API_BASE,
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
    QuotationSettings,
)


DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500
# The byte budget is authoritative.  This high secondary ceiling protects
# against pathological MIME-part floods without dropping legitimate messages
# that bundle many independent POs (a real mailbox message contained 17).
MAX_CANDIDATE_ATTACHMENTS = 50
# Mailbox evidence may legitimately be a little larger than a normal inquiry
# upload.  Keep this local to the read-only audit and below the existing 20 MiB
# per-message ceiling; normal uploads retain their 5 MiB limit.
MAILBOX_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAILBOX_AI_MAX_PDF_PAGES = 25
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
STRONG_ORDER_FILENAME_SIGNAL = re.compile(
    r"\b(?:call\s*off|award(?:ed)?|order\s+confirmation|accept(?:ed|ance)?)\b",
    re.IGNORECASE,
)
RFQ_REQUEST_SIGNAL = re.compile(
    r"\b(?:rfq|request(?:ing)?\s+(?:for\s+)?(?:a\s+)?(?:quotation|quote)|"
    r"(?:quotation|quote)\s+request|"
    r"(?:provide|send|share|submit|prepare|issue)(?:\s+us)?\s+(?:with\s+)?(?:(?:a|the)\s+)?(?:quotation|quote)|"
    r"(?:please|kindly)\s+quote)\b",
    re.IGNORECASE,
)
EXPLICIT_ORDER_CONTEXT_SIGNAL = re.compile(
    r"\b(?:order(?:ed)?|award(?:ed)?|approv(?:e|ed|al)|confirm(?:ed|ation)?|proceed|accept(?:ed|ance)?)\b",
    re.IGNORECASE,
)
GENERIC_INLINE_FILENAME_SIGNAL = re.compile(
    r"\b(?:image\d*|outlook|logo|icon|signature|banner|footer|header|spacer|pixel|social)\b",
    re.IGNORECASE,
)
UNSUPPORTED_DOCUMENT_EXTENSIONS = {".doc", ".docm", ".docx", ".odt", ".rtf"}
UNSUPPORTED_IMAGE_EXTENSIONS = {".bmp", ".gif", ".heic", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
LARGE_UNSUPPORTED_IMAGE_BYTES = 256 * 1024
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

MAILBOX_AI_REVIEW_WARNING = (
    "OCR rows were extracted with AI vision from a PDF that deterministic parsing could not read. "
    "Staff must inspect the exact attachment; these rows are review-only and cannot be auto-approved."
)
MAILBOX_AI_CLOUD_DISCLOSURE_WARNING = (
    "For this review-only extraction, bounded PDF page images were sent to the configured cloud AI "
    "vision provider with provider storage disabled; the Gmail source file was not copied into private media."
)
PDF_PAGE_LIMIT_ERROR = re.compile(
    r"(?:pdf\s+has\s+\d+\s+pages|maximum\s+supported\s+pages|(?:ai\s+cleanup\s+is\s+)?capped\s+at\s+\d+\s+pages)",
    re.IGNORECASE,
)
UNSUPPORTED_ORDER_REVIEW_WARNING = (
    "Unsupported or over-limit attachment; manual review of the exact Gmail source is required."
)
UNSUPPORTED_ATTACHMENT_TYPE_REVIEW_WARNING = (
    "Unsupported attachment type; manual review of the exact Gmail source is required."
)


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


def mailbox_max_attachment_bytes():
    """Return the mailbox-only per-file cap, never above 10 MiB."""

    configured = int(
        getattr(
            settings,
            "QUOTATION_MAILBOX_AUDIT_MAX_ATTACHMENT_BYTES",
            MAILBOX_MAX_ATTACHMENT_BYTES,
        )
    )
    return max(1, min(configured, MAILBOX_MAX_ATTACHMENT_BYTES))


def mailbox_ai_max_pdf_pages():
    """Allow ordinary multi-page POs while rejecting report-sized PDFs."""

    configured = int(
        getattr(settings, "QUOTATION_MAILBOX_AI_MAX_PDF_PAGES", MAILBOX_AI_MAX_PDF_PAGES)
    )
    # The in-memory renderer has its own hard cap as a second line of defence.
    return max(1, min(configured, MAILBOX_AI_MAX_PDF_PAGES))


def _is_pdf_attachment(attachment):
    filename = str((attachment or {}).get("filename") or "").lower()
    mime_type = str((attachment or {}).get("mime_type") or "").lower()
    return filename.endswith(".pdf") or mime_type == "application/pdf"


def _has_usable_attachment_rows(preview):
    for row in (preview or {}).get("lines") or []:
        if not isinstance(row, dict):
            continue
        status = str(row.get("parse_status") or row.get("status") or "").lower()
        name = str(
            row.get("requested_item_name")
            or row.get("raw_name")
            or row.get("item_name")
            or ""
        ).strip()
        if name and status != "ignored":
            return True
    return False


def _mailbox_auto_vision_enabled():
    return mailbox_vision_availability()["available"]


def mailbox_vision_availability():
    settings_obj = QuotationSettings.get_solo()
    if not bool(getattr(settings, "QUOTATION_MAILBOX_AI_VISION_ENABLED", False)):
        return {
            "available": False,
            "reason": "Cloud mailbox vision is not explicitly enabled by environment.",
        }
    if not settings_obj.ai_parsing_enabled or not settings_obj.ai_auto_cleanup_enabled:
        return {
            "available": False,
            "reason": "AI parsing and automatic cleanup are not both enabled in Quotation Settings.",
        }
    if not settings_obj.ai_pdf_vision_enabled:
        return {"available": False, "reason": "PDF vision is disabled in Quotation Settings."}
    status = settings_ai_status(settings_obj)
    if status.get("status") != AI_STATUS_AVAILABLE:
        return {"available": False, "reason": status.get("label") or "AI provider unavailable."}
    return {"available": True, "reason": ""}


def _preview_needs_pdf_vision(preview):
    if _has_usable_attachment_rows(preview):
        return False
    warnings = " ".join(str(value or "") for value in (preview or {}).get("warnings") or [])
    parse_method = str((preview or {}).get("parse_method") or "")
    original_text = str((preview or {}).get("original_text") or "").strip()
    return bool(
        not original_text
        or "no selectable text" in warnings.lower()
        or "ocr_required" in parse_method.lower()
    )


def _is_page_limit_error(error):
    return bool(PDF_PAGE_LIMIT_ERROR.search(str(error or "")))


def _page_count_from_error(error):
    match = re.search(r"pdf\s+has\s+(\d+)\s+pages", str(error or ""), re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _is_permanent_vision_rejection(error):
    value = str(error or "").lower()
    return any(
        marker in value
        for marker in (
            "ai cleanup is capped at",
            "in-memory ai cleanup is capped at",
            "encrypted pdf",
            "invalid pdf",
        )
    )


def _merge_mailbox_vision_preview(deterministic_preview, ai_preview):
    """Retain deterministic provenance and force every AI row to review."""

    deterministic_preview = deterministic_preview or {}
    ai_preview = ai_preview or {}
    rows = []
    for row in ai_preview.get("lines") or []:
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                **row,
                "parse_status": "needs_review",
                "status": "needs_review",
                "result_source": AI_SOURCE_VISION,
            }
        )
    warnings = list(
        dict.fromkeys(
            [
                *(deterministic_preview.get("warnings") or []),
                *(ai_preview.get("warnings") or []),
                MAILBOX_AI_REVIEW_WARNING,
                MAILBOX_AI_CLOUD_DISCLOSURE_WARNING,
            ]
        )
    )
    document_metadata = ai_preview.get("document_metadata") or (
        ai_preview.get("meta") or {}
    ).get("ai_document_metadata") or {}
    metadata_conflicted = bool(
        document_metadata.get("quotation_references_conflicted")
    )
    match_metadata_allowed = bool(
        document_metadata.get("document_type")
        in {"purchase_order", "local_purchase_order", "order_confirmation"}
        and float(document_metadata.get("confidence") or 0) >= 0.80
        and not metadata_conflicted
    )
    structured_text_lines = []
    for reference in document_metadata.get("po_references") or []:
        if (
            match_metadata_allowed
            and isinstance(reference, dict)
            and reference.get("reference")
            and float(reference.get("confidence") or 0) >= 0.80
        ):
            structured_text_lines.append(f"PO: {reference['reference']}")
    for reference in document_metadata.get("quotation_references") or []:
        if (
            match_metadata_allowed
            and isinstance(reference, dict)
            and reference.get("reference")
            and float(reference.get("confidence") or 0) >= 0.80
        ):
            structured_text_lines.append(f"Quotation: {reference['reference']}")
    for label, key in (
        ("Currency", "currency"),
        ("Subtotal", "subtotal"),
        ("VAT total", "vat_total"),
        ("Grand total", "grand_total"),
    ):
        if match_metadata_allowed and document_metadata.get(key):
            structured_text_lines.append(f"{label}: {document_metadata[key]}")
    structured_review_text = "\n".join(structured_text_lines)
    deterministic_totals = deterministic_preview.get("totals") or {}
    structured_totals = {}
    if match_metadata_allowed:
        structured_totals = {
            key: document_metadata.get(key) or ""
            for key in ("currency", "subtotal", "vat_total", "grand_total")
        }
        structured_totals.update({
            "page_number": document_metadata.get("totals_page_number") or "",
            "confidence": document_metadata.get("confidence", 0),
            "result_source": AI_SOURCE_VISION,
            "review_only": True,
        })
    if metadata_conflicted:
        warnings.append(
            "Conflicting AI vision quotation references and totals were kept display-only and excluded "
            "from quotation matching."
        )
    elif document_metadata and not match_metadata_allowed:
        warnings.append(
            "AI vision document metadata was kept display-only because its document type or confidence "
            "was not strong enough for quotation matching."
        )
    elif match_metadata_allowed and any(
        isinstance(reference, dict)
        and reference.get("reference")
        and float(reference.get("confidence") or 0) < 0.80
        for key in ("po_references", "quotation_references")
        for reference in (document_metadata.get(key) or [])
    ):
        warnings.append(
            "Low-confidence AI vision references were kept display-only and excluded from quotation matching."
        )
    return {
        **deterministic_preview,
        **ai_preview,
        "source_file_ref": "",
        # Only strict structured metadata is reference-extractable. Free-form
        # AI notes/raw rows remain in provenance fields and can never become an
        # attachment-authoritative quote/PO reference.
        "original_text": deterministic_preview.get("original_text") or structured_review_text,
        "totals": deterministic_totals or structured_totals,
        "lines": rows,
        "warnings": warnings,
        "meta": {
            **(deterministic_preview.get("meta") or {}),
            **(ai_preview.get("meta") or {}),
            "mailbox_ai_vision": {
                "provider": ai_preview.get("provider") or "",
                "model": ai_preview.get("model") or "",
                "cache_hit": bool(ai_preview.get("cache_hit")),
                "review_only": True,
                "source_persisted": False,
                "extracted_text_source": "ai_vision_structured_review" if structured_review_text else "",
                "document_metadata": document_metadata,
            },
        },
        "result_source": AI_SOURCE_VISION,
        "ai_status": ai_preview.get("ai_status") or "ai_vision_cleanup_used",
        "ai_review_required": True,
        "auto_approval_eligible": False,
        "vision_repair_status": "completed",
        "vision_repair_reason": "",
    }


def _run_mailbox_pdf_vision(
    content,
    attachment,
    deterministic_preview,
    *,
    actor=None,
    source_identity=None,
):
    return _merge_mailbox_vision_preview(
        deterministic_preview,
        clean_pdf_bytes_with_ai(
            content,
            {
                **(deterministic_preview or {}),
                "source_filename": str(attachment.get("filename") or "attachment.pdf"),
                "source_mime_type": attachment.get("mime_type") or "application/pdf",
                "source_sha256": (deterministic_preview or {}).get("source_sha256")
                or hashlib.sha256(content).hexdigest(),
                "source_file_ref": "",
                "source_file_size": len(content),
                "relevance_context": {
                    "workflow": "mailbox_po_review",
                    "review_only": True,
                    "capture_visible_po_and_quotation_references_in_document_notes": True,
                    "capture_visible_document_total_in_document_notes": True,
                },
                "ai_log_source_identity": _safe_json(source_identity or {}),
            },
            actor=actor,
            max_pages=mailbox_ai_max_pdf_pages(),
            max_pdf_bytes=mailbox_max_attachment_bytes(),
            json_schema=MAILBOX_PO_VISION_JSON_SCHEMA,
            schema_name="mailbox_po_vision_parse",
        ),
    )


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
    return 0 <= declared_size <= mailbox_max_attachment_bytes()


def _is_overlimit_supported_document_attachment(attachment):
    if not isinstance(attachment, dict):
        return False
    extension = os.path.splitext(str(attachment.get("filename") or ""))[1].lower()
    if extension not in SUPPORTED_ATTACHMENT_EXTENSIONS:
        return False
    try:
        return int(attachment.get("size") or 0) > mailbox_max_attachment_bytes()
    except (TypeError, ValueError):
        return False


def _message_order_context_text(message):
    """Return only author-controlled message text, excluding attachment names."""

    newest_body = str((message or {}).get("newest_body_text") or "").strip()
    body_or_snippet = newest_body or str((message or {}).get("snippet") or "")
    return "\n".join(
        [
            str((message or {}).get("subject") or ""),
            body_or_snippet,
        ]
    )


def _has_explicit_order_context(message):
    """Require positive order intent in the newest email and reject RFQ wording."""

    context_text = _message_order_context_text(message)
    context_references = extract_po_references(context_text)
    has_explicit_po = bool(
        PURCHASE_ORDER_SIGNAL.search(context_text)
        or any(reference.get("kind") == "po" for reference in context_references)
    )
    if has_explicit_po:
        return True
    if RFQ_REQUEST_SIGNAL.search(context_text):
        return False
    return bool(EXPLICIT_ORDER_CONTEXT_SIGNAL.search(context_text))


def _attachment_filename_parts(attachment):
    filename = os.path.basename(str((attachment or {}).get("filename") or ""))
    stem, extension = os.path.splitext(filename)
    normalized = re.sub(r"[_-]+", " ", filename)
    return filename, stem, extension.lower(), normalized


def _has_strong_order_filename(attachment):
    """Identify an unsupported file whose own name says it is order evidence."""

    filename, _stem, _extension, normalized = _attachment_filename_parts(attachment)
    if not filename:
        return False
    return bool(
        PURCHASE_ORDER_SIGNAL.search(normalized)
        or STRONG_ORDER_FILENAME_SIGNAL.search(normalized)
        or any(
            reference.get("kind") == "po"
            for reference in extract_po_references(filename)
        )
    )


def _is_large_non_inline_image(attachment):
    _filename, stem, extension, _normalized = _attachment_filename_parts(attachment)
    if extension not in UNSUPPORTED_IMAGE_EXTENSIONS:
        return False
    try:
        declared_size = int((attachment or {}).get("size") or 0)
    except (TypeError, ValueError):
        return False
    return bool(
        declared_size >= LARGE_UNSUPPORTED_IMAGE_BYTES
        and not GENERIC_INLINE_FILENAME_SIGNAL.search(
            re.sub(r"[_-]+", " ", stem.strip())
        )
    )


def _attachment_warning_values(attachment):
    raw = (attachment or {}).get("warnings") or []
    if isinstance(raw, (list, tuple)):
        return [str(value) for value in raw]
    return [str(raw)]


def _should_surface_unsupported_order_attachment(
    attachment,
    *,
    message_is_relevant,
    explicit_order_context,
):
    if not message_is_relevant or not isinstance(attachment, dict):
        return False
    _filename, _stem, extension, _normalized = _attachment_filename_parts(attachment)
    if extension in SUPPORTED_ATTACHMENT_EXTENSIONS:
        return bool(
            _is_overlimit_supported_document_attachment(attachment)
            and (explicit_order_context or _has_strong_order_filename(attachment))
        )
    if _has_strong_order_filename(attachment):
        return True
    if not explicit_order_context:
        return False
    # Office documents and substantial raster scans are evidence only
    # when the newest message itself expresses order intent. This prevents a
    # supported RFQ PDF from lending relevance to an unrelated Order.docx or
    # inline logo in the same email.
    return bool(
        extension in UNSUPPORTED_DOCUMENT_EXTENSIONS
        or _is_large_non_inline_image(attachment)
    )


def _remove_obsolete_broad_manual_surface(attachment):
    """Undo only untouched rows created by the former broad surfacing rule."""

    if not isinstance(attachment, dict):
        return attachment
    warning_values = _attachment_warning_values(attachment)
    owned_warnings = {
        UNSUPPORTED_ORDER_REVIEW_WARNING,
        UNSUPPORTED_ATTACHMENT_TYPE_REVIEW_WARNING,
    }
    warnings = [
        warning
        for warning in warning_values
        if warning not in owned_warnings
    ]
    was_our_generic_surface = bool(
        attachment.get("status") == "manual_review"
        and attachment.get("manual_review_required")
        and str(attachment.get("reason") or "") in owned_warnings
        and any(warning in owned_warnings for warning in warning_values)
        and all(warning in owned_warnings for warning in warning_values)
        and not attachment.get("candidate")
        and not attachment.get("content_fetched")
        and not attachment.get("manual_review_reason_code")
        and not attachment.get("vision_repair_status")
        and not attachment.get("vision_repair_reason")
        and not attachment.get("source_sha256")
        and not attachment.get("result_source")
        and not attachment.get("lines")
        and not attachment.get("line_count")
        and not attachment.get("ai_status")
        and not attachment.get("ai_review_required")
    )
    if not was_our_generic_surface:
        return attachment
    cleaned = {
        **attachment,
        "status": "metadata_only",
        "manual_review_required": False,
        "reason": "Unsupported document type for PO parsing.",
        "warnings": warnings,
    }
    cleaned.pop("manual_review_reason_code", None)
    return cleaned


def classify_mailbox_message(message):
    """Classify broadly enough for review without claiming a quote match."""

    context_text = _message_order_context_text(message)
    filenames = " ".join(
        str(attachment.get("filename") or "")
        for attachment in (message.get("attachment_manifest") or [])
        if isinstance(attachment, dict)
    )
    text = "\n".join(
        [
            context_text,
            filenames,
        ]
    )
    references = extract_po_references(text)
    plausible_documents = [
        attachment
        for attachment in (message.get("attachment_manifest") or [])
        if _is_plausible_document_attachment(attachment)
    ]
    explicit_order_context = _has_explicit_order_context(message)
    reviewable_overlimit_supported_documents = [
        attachment
        for attachment in (message.get("attachment_manifest") or [])
        if _is_overlimit_supported_document_attachment(attachment)
        and (explicit_order_context or _has_strong_order_filename(attachment))
    ]
    reviewable_unsupported_order_documents = [
        attachment
        for attachment in (message.get("attachment_manifest") or [])
        if isinstance(attachment, dict)
        and os.path.splitext(str(attachment.get("filename") or ""))[1].lower()
        not in SUPPORTED_ATTACHMENT_EXTENSIONS
        and _should_surface_unsupported_order_attachment(
            attachment,
            message_is_relevant=True,
            explicit_order_context=explicit_order_context,
        )
    ]
    has_po_signal = bool(PURCHASE_ORDER_SIGNAL.search(text))
    has_explicit_po_reference = any(
        reference.get("kind") == "po" for reference in references
    )
    has_quote_reference = any(reference["kind"] == "quotation" for reference in references)
    has_order_context = bool(POSSIBLE_ORDER_SIGNAL.search(text))
    restricted_labels = {"SPAM", "TRASH"}.intersection(
        str(label).upper() for label in (message.get("label_ids") or [])
    )

    if has_po_signal or has_explicit_po_reference:
        classification = MailboxPOMessage.CLASS_PURCHASE_ORDER
        reason = (
            "A credible explicit PO/LPO/MPO reference was found in the newest message, subject, or attachment filename."
            if has_explicit_po_reference
            else "PO/LPO language found in the newest message, subject, or attachment filename."
        )
    elif plausible_documents and (has_quote_reference or has_order_context):
        classification = MailboxPOMessage.CLASS_POSSIBLE_PO
        reason = "A supported document accompanies quotation/order context and needs review."
    elif plausible_documents:
        classification = MailboxPOMessage.CLASS_POSSIBLE_PO
        reason = "A size-bounded supported document has no PO keyword and must be inspected before exclusion."
    elif reviewable_overlimit_supported_documents:
        classification = MailboxPOMessage.CLASS_POSSIBLE_PO
        reason = "A supported document exceeds the safe parsing limit and requires exact-source review."
    elif reviewable_unsupported_order_documents:
        classification = MailboxPOMessage.CLASS_POSSIBLE_PO
        reason = "An unsupported attachment and the newest order context require exact-source review."
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


def _preview_attachment(
    connection,
    message_id,
    attachment,
    token,
    *,
    max_bytes=None,
    allow_ai_vision=True,
    actor=None,
    vision_source_identity=None,
):
    public = {key: value for key, value in attachment.items() if key != "_inline_data"}
    declared_size = int(attachment.get("size") or 0)
    per_file_limit = mailbox_max_attachment_bytes()
    if declared_size > per_file_limit:
        warning = (
            f"Attachment exceeds the {per_file_limit}-byte mailbox per-file audit limit; "
            "manual review of the exact Gmail source is required."
        )
        return {
            **public,
            "candidate": True,
            "content_fetched": False,
            "status": "skipped",
            "reason": warning,
            "warnings": [warning],
            "manual_review_required": True,
            "vision_repair_status": "rejected",
            "vision_repair_reason": warning,
        }, 0
    try:
        requested_budget = per_file_limit if max_bytes is None else int(max_bytes)
        remaining_budget = max(0, min(requested_budget, per_file_limit))
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

    content = b""
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
        if len(content) > per_file_limit:
            warning = (
                f"Decoded attachment exceeds the {per_file_limit}-byte mailbox per-file audit limit; "
                "manual review of the exact Gmail source is required."
            )
            return {
                **public,
                "candidate": True,
                "content_fetched": True,
                "fetched_bytes": len(content),
                "status": "skipped",
                "reason": warning,
                "warnings": [warning],
                "manual_review_required": True,
                "vision_repair_status": "rejected",
                "vision_repair_reason": warning,
            }, len(content)
        if len(content) > remaining_budget:
            return {
                **public,
                "candidate": True,
                "content_fetched": True,
                "fetched_bytes": len(content),
                "status": "skipped",
                "reason": "Decoded attachment exceeds the remaining per-message byte limit.",
            }, len(content)

        upload = SimpleUploadedFile(
            str(attachment.get("filename") or "attachment"),
            content,
            content_type=attachment.get("mime_type") or "application/octet-stream",
        )
        deterministic_error = ""
        try:
            preview = parse_file_preview(
                upload,
                store_source=False,
                max_bytes=per_file_limit,
                max_pdf_pages_override=mailbox_ai_max_pdf_pages(),
            )
        except Exception as exc:
            deterministic_error = str(exc)
            if not (
                _is_pdf_attachment(attachment)
                and _is_page_limit_error(exc)
                and _mailbox_auto_vision_enabled()
                and allow_ai_vision
            ):
                raise
            preview = {
                "source_type": "pdf",
                "source_filename": str(attachment.get("filename") or "attachment.pdf"),
                "source_mime_type": attachment.get("mime_type") or "application/pdf",
                "source_sha256": hashlib.sha256(content).hexdigest(),
                "source_file_ref": "",
                "source_file_size": len(content),
                "parse_method": "mailbox_pdf_preflight_fallback_v1",
                "original_text": "",
                "meta": {
                    "deterministic_parse_error": deterministic_error,
                    "page_count": _page_count_from_error(deterministic_error),
                },
                "totals": {},
                "lines": [],
                "warnings": [
                    f"Deterministic PDF parsing stopped before extraction: {deterministic_error}"
                ],
            }

        if (
            _is_pdf_attachment(attachment)
            and _mailbox_auto_vision_enabled()
            and _preview_needs_pdf_vision(preview)
            and not allow_ai_vision
        ):
            preview = {
                **preview,
                "warnings": list(
                    dict.fromkeys(
                        [
                            *(preview.get("warnings") or []),
                            "Cloud AI vision was deferred to the explicit bounded mailbox repair phase; staff review is required.",
                        ]
                    )
                ),
                "vision_repair_status": "pending",
                "vision_repair_reason": "Deferred from synchronous mailbox inventory.",
                "ai_review_required": True,
                "auto_approval_eligible": False,
            }

        if (
            _is_pdf_attachment(attachment)
            and _mailbox_auto_vision_enabled()
            and allow_ai_vision
            and (bool(deterministic_error) or _preview_needs_pdf_vision(preview))
        ):
            try:
                preview = _run_mailbox_pdf_vision(
                    content,
                    attachment,
                    preview,
                    actor=actor,
                    source_identity=vision_source_identity,
                )
            except Exception as exc:
                vision_error = str(exc)[:500]
                if deterministic_error:
                    return {
                        **public,
                        "candidate": True,
                        "content_fetched": True,
                        "fetched_bytes": len(content),
                        "status": "failed",
                        "reason": (
                            f"{deterministic_error} Mailbox AI vision cleanup also failed: {vision_error}"
                        )[:500],
                        "vision_repair_status": (
                            "rejected" if _is_permanent_vision_rejection(exc) else "retryable"
                        ),
                        "vision_repair_reason": vision_error,
                    }, len(content)
                preview = {
                    **preview,
                    "warnings": list(
                        dict.fromkeys(
                            [
                                *(preview.get("warnings") or []),
                                f"Mailbox AI vision cleanup failed; deterministic review evidence was kept. Detail: {vision_error}",
                            ]
                        )
                    ),
                    "vision_repair_status": (
                        "rejected" if _is_permanent_vision_rejection(exc) else "retryable"
                    ),
                    "vision_repair_reason": vision_error,
                    "ai_review_required": True,
                    "auto_approval_eligible": False,
                }
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
            "result_source": preview.get("result_source") or "deterministic_parse",
            "ai_status": preview.get("ai_status") or "",
            "ai_review_required": bool(preview.get("ai_review_required")),
            "auto_approval_eligible": preview.get("auto_approval_eligible", True),
            "vision_repair_status": preview.get("vision_repair_status") or "",
            "vision_repair_reason": preview.get("vision_repair_reason") or "",
        }, len(content)
    except Exception as exc:
        return {
            **public,
            "candidate": True,
            "content_fetched": bool(content),
            "fetched_bytes": len(content),
            "status": "failed",
            "reason": str(exc)[:500],
        }, len(content)


def hydrate_plausible_attachments(
    connection,
    message,
    *,
    is_relevant,
    heartbeat=None,
    allow_ai_vision=False,
    explicit_order_context=False,
):
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
        if (
            extension in SUPPORTED_ATTACHMENT_EXTENSIONS
            and not _is_overlimit_supported_document_attachment(attachment)
        ):
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
            manual_review = _should_surface_unsupported_order_attachment(
                attachment,
                message_is_relevant=is_relevant,
                explicit_order_context=explicit_order_context,
            )
            warning = UNSUPPORTED_ATTACHMENT_TYPE_REVIEW_WARNING
            manifest.append(
                {
                    **public,
                    "candidate": False,
                    "content_fetched": False,
                    "status": "manual_review" if manual_review else "metadata_only",
                    "reason": warning if manual_review else "Unsupported document type for PO parsing.",
                    "warnings": [warning] if manual_review else [],
                    "manual_review_required": manual_review,
                    **(
                        {"manual_review_reason_code": "unsupported_order_document"}
                        if manual_review
                        else {}
                    ),
                }
            )
            continue
        if _is_overlimit_supported_document_attachment(attachment):
            manual_review = _should_surface_unsupported_order_attachment(
                attachment,
                message_is_relevant=is_relevant,
                explicit_order_context=explicit_order_context,
            )
            if not manual_review:
                manifest.append(
                    {
                        **public,
                        "candidate": False,
                        "content_fetched": False,
                        "status": "metadata_only",
                        "reason": "Supported document exceeds the parsing limit without order evidence.",
                        "warnings": [],
                        "manual_review_required": False,
                    }
                )
                continue
            audited_candidates += 1
            if heartbeat:
                heartbeat()
            audited, _byte_count = _preview_attachment(
                connection,
                message.get("gmail_message_id"),
                attachment,
                "",
                allow_ai_vision=allow_ai_vision,
            )
            if heartbeat:
                heartbeat()
            manifest.append(audited)
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
        if heartbeat:
            heartbeat()
        audited, byte_count = _preview_attachment(
            connection,
            message.get("gmail_message_id"),
            attachment,
            token,
            max_bytes=remaining_budget,
            allow_ai_vision=allow_ai_vision,
        )
        if heartbeat:
            heartbeat()
        try:
            byte_count = max(0, int(byte_count))
        except (TypeError, ValueError):
            byte_count = 0
        if byte_count > remaining_budget:
            # The decoded byte count is authoritative when Gmail omitted or
            # understated its size metadata. Never persist parsed fields from
            # an attachment that crosses the cumulative processing budget.
            # Gmail has already returned those bytes, so provenance must
            # report the actual download even though parsing evidence is
            # discarded.
            warning = (
                "Decoded attachment exceeded the remaining per-message processing byte limit; "
                "the downloaded bytes were excluded and manual review is required."
            )
            manifest.append(
                {
                    **public,
                    "candidate": True,
                    "content_fetched": bool(byte_count),
                    "fetched_bytes": byte_count,
                    "status": "skipped",
                    "reason": warning,
                    "warnings": [warning],
                    "manual_review_required": True,
                }
            )
            # The oversized response has already crossed this message's byte
            # budget.  Do not fetch another attachment from the same message.
            fetched_bytes += byte_count
            continue
        manifest.append(audited)
        fetched_bytes += byte_count
    return manifest, audited_candidates, fetched_bytes


def _attachment_identity(attachment):
    # Gmail's opaque attachmentId is a download token, not a durable identity:
    # the API can return a different token on a later ``messages.get`` for the
    # same immutable MIME part.  The MIME part id and its public metadata are
    # stable within one Gmail message, so prefer that tuple for reconciliation.
    part_id = str((attachment or {}).get("part_id") or "")
    if part_id:
        try:
            size = int((attachment or {}).get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        return (
            "part",
            part_id,
            str((attachment or {}).get("filename") or ""),
            str((attachment or {}).get("mime_type") or "").lower(),
            size,
        )
    attachment_id = str(
        (attachment or {}).get("attachment_id")
        or (attachment or {}).get("source_gmail_attachment_id")
        or ""
    )
    if attachment_id:
        return ("attachment_id", attachment_id)
    filename = str((attachment or {}).get("filename") or "")
    # Filename alone is not exact source identity. Keep a deterministic key so
    # a terminal manual annotation can merge back into the stored manifest,
    # but never use this identity to select newly fetched Gmail bytes.
    return (
        "unidentifiable",
        filename,
        str((attachment or {}).get("mime_type") or "").lower(),
        str((attachment or {}).get("size") or ""),
    )


def _is_old_mailbox_size_skip(attachment):
    if str((attachment or {}).get("status") or "") != "skipped":
        return False
    reason = str((attachment or {}).get("reason") or "").lower()
    try:
        size = int((attachment or {}).get("size") or 0)
    except (TypeError, ValueError):
        return False
    return bool(
        _is_pdf_attachment(attachment)
        and 0 < size <= mailbox_max_attachment_bytes()
        and "per-file audit limit" in reason
    )


def _is_old_candidate_count_skip(attachment):
    if str((attachment or {}).get("status") or "") != "skipped":
        return False
    reason = str((attachment or {}).get("reason") or "").lower()
    try:
        size = int((attachment or {}).get("size") or 0)
    except (TypeError, ValueError):
        return False
    return bool(
        _is_pdf_attachment(attachment)
        and 0 <= size <= mailbox_max_attachment_bytes()
        and "per-message candidate attachment limit reached" in reason
    )


def _is_old_message_budget_skip(attachment):
    if str((attachment or {}).get("status") or "") != "skipped":
        return False
    reason = str((attachment or {}).get("reason") or "").lower()
    try:
        size = int((attachment or {}).get("size") or 0)
    except (TypeError, ValueError):
        return False
    return bool(
        _is_pdf_attachment(attachment)
        and 0 <= size <= mailbox_max_attachment_bytes()
        and any(
            marker in reason
            for marker in (
                "per-message total attachment byte limit reached",
                "decoded attachment exceeds the remaining per-message byte limit",
            )
        )
    )


def attachment_needs_mailbox_vision_repair(attachment):
    """Identify only a PDF that the old bounded audit could not inspect."""

    if not isinstance(attachment, dict) or not _is_pdf_attachment(attachment):
        return False
    repair_status = str(attachment.get("vision_repair_status") or "").lower()
    # Transient Gmail/provider failures are explicitly bounded by
    # ``vision_repair_attempts`` and must remain resumable even when a legacy
    # v1 identity failure had already changed the base status to manual_review.
    if repair_status == "retryable":
        return True
    # Five production rows were terminally marked by the v1 strategy before we
    # confirmed that Gmail rotates attachmentId download tokens.  Retry only
    # those legacy rows once when a stable MIME part id exists.  A v2 failure is
    # stamped below and remains terminal, preventing an infinite repair loop.
    if repair_status == "manual":
        reason = str(
            attachment.get("vision_repair_reason")
            or attachment.get("reason")
            or ""
        ).lower()
        return bool(
            attachment.get("part_id")
            and "exact gmail attachment part" in reason
            and not attachment.get("vision_identity_strategy")
        )
    if repair_status in {"completed", "rejected", "manual"}:
        return False
    if (
        _is_old_mailbox_size_skip(attachment)
        or _is_old_candidate_count_skip(attachment)
        or _is_old_message_budget_skip(attachment)
    ):
        return True
    status = str(attachment.get("status") or "").lower()
    if status == "metadata_only":
        try:
            return int(attachment.get("size") or 0) <= mailbox_max_attachment_bytes()
        except (TypeError, ValueError):
            return False
    if status == "failed" and _is_page_limit_error(attachment.get("reason")):
        return True
    if status != "parsed" or _has_usable_attachment_rows(attachment):
        return False
    warnings = " ".join(str(value or "") for value in attachment.get("warnings") or [])
    return bool(
        not str(attachment.get("original_text") or "").strip()
        or "no selectable text" in warnings.lower()
        or "ocr_required" in str(attachment.get("parse_method") or "").lower()
        or repair_status == "retryable"
    )


def _merge_repair_result(current_manifest, replacements):
    merged = []
    changed = False
    for attachment in current_manifest or []:
        replacement = replacements.get(_attachment_identity(attachment))
        if replacement is None or not attachment_needs_mailbox_vision_repair(attachment):
            merged.append(attachment)
            continue
        merged.append(replacement)
        changed = True
    return merged, changed


def assert_mailbox_po_audit_repairable(audit_run):
    latest_id = (
        MailboxPOAuditRun.objects.filter(gmail_connection=audit_run.gmail_connection)
        .order_by("-created_at", "-id")
        .values_list("id", flat=True)
        .first()
    )
    if latest_id != audit_run.id:
        raise ValueError(
            "Mailbox repair is allowed only for the latest audit run for this mailbox."
        )
    if audit_run.status != MailboxPOAuditRun.STATUS_COMPLETED or not audit_run.exhausted:
        raise ValueError(
            "Mailbox repair requires a completed, exhausted inventory; active scans cannot be changed."
        )
    if audit_run.match_runs.exists():
        raise ValueError(
            "Mailbox repair must run before reconciliation; matched evidence snapshots cannot be changed."
        )


def _claim_mailbox_po_repair_lease(audit_run):
    """Exclusively reserve a completed run before Gmail or AI work.

    Completed scans otherwise have no active use for ``scan_lease_*``. Reusing
    those existing columns keeps repair mutually exclusive without a schema
    migration. New audit creation and the final manifest merge lock the same
    mailbox connection row, so a newly-created run either precedes the final
    guard (and aborts it) or begins after the repair commit.
    """

    now = timezone.now()
    token = f"repair-{secrets.token_hex(24)}"
    with transaction.atomic():
        GmailOAuthConnection.objects.select_for_update().get(
            pk=audit_run.gmail_connection_id
        )
        stored = (
            MailboxPOAuditRun.objects.select_for_update()
            .select_related("gmail_connection")
            .get(pk=audit_run.pk)
        )
        assert_mailbox_po_audit_repairable(stored)
        if (
            stored.scan_lease_token
            and stored.scan_lease_expires_at
            and stored.scan_lease_expires_at > now
        ):
            raise RuntimeError(
                "This mailbox audit run is already being repaired. Try again shortly."
            )
        expires_at = now + timedelta(seconds=SCAN_LEASE_SECONDS)
        # Completed runs reject Model.save() by design. This guarded update is
        # deliberately limited to the two expiring coordination fields.
        MailboxPOAuditRun.objects.filter(pk=stored.pk).update(
            scan_lease_token=token,
            scan_lease_expires_at=expires_at,
            updated_at=now,
        )
        stored.scan_lease_token = token
        stored.scan_lease_expires_at = expires_at
        stored.updated_at = now
    return stored, token


def _renew_mailbox_po_repair_lease(audit_run_id, token):
    updated = MailboxPOAuditRun.objects.filter(
        pk=audit_run_id,
        scan_lease_token=token,
        status=MailboxPOAuditRun.STATUS_COMPLETED,
        exhausted=True,
    ).update(
        scan_lease_expires_at=timezone.now() + timedelta(seconds=SCAN_LEASE_SECONDS)
    )
    if not updated:
        raise RuntimeError(
            "This mailbox repair lease expired or was claimed by another worker."
        )


def _assert_mailbox_po_repair_lease_current(audit_run_id, token):
    """Re-check mutability and lease ownership inside the merge transaction."""

    stored = (
        MailboxPOAuditRun.objects.select_for_update()
        .select_related("gmail_connection")
        .get(pk=audit_run_id)
    )
    if (
        stored.scan_lease_token != token
        or not stored.scan_lease_expires_at
        or stored.scan_lease_expires_at <= timezone.now()
    ):
        raise RuntimeError(
            "This mailbox repair lease expired or was claimed by another worker."
        )
    assert_mailbox_po_audit_repairable(stored)
    return stored


def _release_mailbox_po_repair_lease(audit_run_id, token):
    MailboxPOAuditRun.objects.filter(
        pk=audit_run_id,
        scan_lease_token=token,
    ).update(
        scan_lease_token="",
        scan_lease_expires_at=None,
        updated_at=timezone.now(),
    )


def _persist_mailbox_po_repair_replacements(
    audit_run,
    message,
    replacements,
    *,
    lease_token,
):
    """Guard the only canonical evidence mutation against stale repair work."""

    if not replacements:
        return False
    with transaction.atomic():
        # Serialize against ``start_mailbox_po_audit`` before checking which
        # run is latest. This closes the rescan/repair TOCTOU window.
        GmailOAuthConnection.objects.select_for_update().get(
            pk=audit_run.gmail_connection_id
        )
        _assert_mailbox_po_repair_lease_current(audit_run.id, lease_token)
        locked = MailboxPOMessage.objects.select_for_update().get(pk=message.pk)
        merged, changed = _merge_repair_result(locked.attachment_manifest, replacements)
        if not changed:
            return False
        now = timezone.now()
        locked.attachment_manifest = _safe_json(merged)
        locked.audit_error = "; ".join(
            _database_text(attachment.get("reason"))
            for attachment in merged
            if isinstance(attachment, dict)
            and attachment.get("status") == "failed"
            and attachment.get("reason")
        )[:2000]
        locked.attachments_audited_at = now
        locked.last_audited_at = now
        locked.save(
            update_fields=[
                "attachment_manifest",
                "audit_error",
                "attachments_audited_at",
                "last_audited_at",
                "updated_at",
            ]
        )
    return True


def _mailbox_po_repair_retry_replacement(target, reason):
    """Return a persisted retry marker, terminally surfacing attempt three."""

    attempts = int((target or {}).get("vision_repair_attempts") or 0) + 1
    retry_reason = str(reason or "Mailbox PDF repair did not produce review evidence")[:500]
    if attempts >= 3:
        warning = (
            f"Mailbox PDF repair failed {attempts} times; automatic retries stopped and manual review "
            f"is required. Last error: {retry_reason}"
        )
        return {
            **target,
            "status": "manual_review",
            "manual_review_required": True,
            "vision_repair_status": "manual",
            "vision_repair_attempts": attempts,
            "vision_repair_reason": retry_reason,
            "warnings": list(dict.fromkeys([*(target.get("warnings") or []), warning])),
            "reason": warning,
        }, True
    return {
        **target,
        "vision_repair_status": "retryable",
        "vision_repair_attempts": attempts,
        "vision_repair_reason": retry_reason,
    }, False


def reclassify_mailbox_po_audit_messages(
    audit_run,
    *,
    apply=True,
    repair_lease_token=None,
):
    """Recompute stored relevance, optionally under the repair mutation guard."""

    if apply and repair_lease_token:
        with transaction.atomic():
            GmailOAuthConnection.objects.select_for_update().get(
                pk=audit_run.gmail_connection_id
            )
            _assert_mailbox_po_repair_lease_current(
                audit_run.id,
                repair_lease_token,
            )
            return _reclassify_mailbox_po_audit_messages(audit_run, apply=True)
    return _reclassify_mailbox_po_audit_messages(audit_run, apply=apply)


def _reclassify_mailbox_po_audit_messages(audit_run, *, apply=True):
    """Recompute deterministic relevance from stored data, without Gmail I/O."""

    changed = 0
    memberships = (
        MailboxPOAuditRunMessage.objects.filter(audit_run=audit_run)
        .select_related("message")
        .order_by("message_id")
    )
    for membership in memberships.iterator(chunk_size=200):
        message = membership.message
        message_payload = {
            "subject": message.subject,
            "newest_body_text": message.newest_body_text,
            "snippet": message.snippet,
            "attachment_manifest": message.attachment_manifest,
            "label_ids": message.label_ids,
        }
        result = classify_mailbox_message(message_payload)
        explicit_order_context = _has_explicit_order_context(message_payload)
        surfaced_manifest = []
        for attachment in message.attachment_manifest or []:
            if not isinstance(attachment, dict):
                surfaced_manifest.append(attachment)
                continue
            should_surface = _should_surface_unsupported_order_attachment(
                attachment,
                message_is_relevant=result["is_relevant"],
                explicit_order_context=explicit_order_context,
            )
            if not should_surface:
                surfaced_manifest.append(
                    _remove_obsolete_broad_manual_surface(attachment)
                )
                continue
            surfaced_manifest.append(
                {
                    **attachment,
                    "manual_review_required": True,
                    "warnings": list(
                        dict.fromkeys(
                            [
                                *_attachment_warning_values(attachment),
                                UNSUPPORTED_ORDER_REVIEW_WARNING,
                            ]
                        )
                    ),
                    "reason": UNSUPPORTED_ORDER_REVIEW_WARNING,
                    "manual_review_reason_code": "unsupported_order_document",
                    "status": (
                        attachment.get("status")
                        if attachment.get("status") == "parsed"
                        else "manual_review"
                    ),
                }
            )
        values = {
            "classification": result["classification"],
            "is_relevant": result["is_relevant"],
            "auto_link_eligible": result["auto_link_eligible"],
            "relevance_reason": result["relevance_reason"],
            "extracted_po_references": result["extracted_po_references"],
            "attachment_manifest": _safe_json(surfaced_manifest),
        }
        if all(getattr(message, field) == value for field, value in values.items()):
            continue
        if apply:
            MailboxPOMessage.objects.filter(pk=message.pk).update(
                **values,
                updated_at=timezone.now(),
            )
        changed += 1
    return changed


def mark_unavailable_mailbox_vision_for_manual_review(audit_run):
    """Terminally surface cloud-only targets when mailbox vision is unavailable."""

    availability = mailbox_vision_availability()
    if availability["available"]:
        return 0
    changed_messages = 0
    memberships = (
        MailboxPOAuditRunMessage.objects.filter(
            audit_run=audit_run,
            message__is_relevant=True,
        )
        .select_related("message")
        .order_by("message_id")
    )
    for membership in memberships.iterator(chunk_size=200):
        message = membership.message
        manifest = []
        changed = False
        for attachment in message.attachment_manifest or []:
            if not attachment_needs_mailbox_vision_repair(attachment):
                manifest.append(attachment)
                continue
            warning = (
                "Cloud AI vision was not used. This attachment requires manual review of the exact Gmail "
                f"source. Reason: {availability['reason']}"
            )
            manifest.append(
                {
                    **attachment,
                    "status": "manual_review",
                    "manual_review_required": True,
                    "vision_repair_status": "manual",
                    "vision_repair_reason": availability["reason"],
                    "warnings": list(
                        dict.fromkeys([*(attachment.get("warnings") or []), warning])
                    ),
                    "reason": warning,
                }
            )
            changed = True
        if not changed:
            continue
        MailboxPOMessage.objects.filter(pk=message.pk).update(
            attachment_manifest=_safe_json(manifest),
            updated_at=timezone.now(),
        )
        changed_messages += 1
    return changed_messages


def repair_mailbox_po_audit_pdf_vision(
    audit_run,
    *,
    message_ids=None,
    limit=None,
    dry_run=False,
    actor=None,
):
    """Repair a completed inventory under one exclusive, expiring lease."""

    if not isinstance(audit_run, MailboxPOAuditRun):
        audit_run = MailboxPOAuditRun.objects.select_related("gmail_connection").get(
            pk=audit_run
        )
    assert_mailbox_po_audit_repairable(audit_run)
    if not _mailbox_auto_vision_enabled():
        raise AIParseError(
            "Mailbox PDF repair requires AI parsing, automatic cleanup, PDF vision, and the explicit "
            "QUOTATION_MAILBOX_AI_VISION_ENABLED cloud-processing opt-in to be enabled."
        )
    if audit_run.gmail_connection.status != GmailOAuthConnection.STATUS_CONNECTED:
        raise RuntimeError("The audit run's Gmail mailbox is not connected.")

    claimed_run, lease_token = _claim_mailbox_po_repair_lease(audit_run)
    try:
        return _repair_mailbox_po_audit_pdf_vision_with_lease(
            claimed_run,
            message_ids=message_ids,
            limit=limit,
            dry_run=dry_run,
            repair_lease_token=lease_token,
            actor=actor,
        )
    finally:
        _release_mailbox_po_repair_lease(claimed_run.id, lease_token)


def _repair_mailbox_po_audit_pdf_vision_with_lease(
    audit_run,
    *,
    message_ids=None,
    limit=None,
    dry_run=False,
    repair_lease_token,
    actor=None,
):
    """Re-fetch and repair only OCR/page/legacy-size PDF manifests in a run.

    Gmail access remains read-only, network calls happen outside transactions,
    and only the canonical message's attachment manifest is updated.  No quote,
    order, evidence outcome or completed audit-run fields are touched.  A
    successful replacement stops being a target, making repeated invocations
    idempotent and naturally resumable.
    """

    connection = audit_run.gmail_connection

    reclassified_count = reclassify_mailbox_po_audit_messages(
        audit_run,
        apply=not dry_run,
        repair_lease_token=repair_lease_token if not dry_run else None,
    )
    memberships = (
        MailboxPOAuditRunMessage.objects.filter(audit_run=audit_run)
        .select_related("message")
        .order_by("message_id")
    )
    requested_message_ids = [str(value) for value in (message_ids or []) if str(value)]
    if requested_message_ids:
        memberships = memberships.filter(message__gmail_message_id__in=requested_message_ids)
    remaining_limit = None if limit is None else max(0, int(limit))
    summary = {
        "audit_run_id": audit_run.id,
        "messages_considered": 0,
        "messages_fetched": 0,
        "attachments_targeted": 0,
        "attachments_repaired": 0,
        "attachments_retryable": 0,
        "attachments_rejected": 0,
        "attachments_missing": 0,
        "messages_updated": 0,
        "messages_reclassified": reclassified_count,
        "dry_run": bool(dry_run),
        "errors": [],
    }

    for membership in memberships.iterator(chunk_size=100):
        message = membership.message
        targets = [
            attachment
            for attachment in (message.attachment_manifest or [])
            if message.is_relevant and attachment_needs_mailbox_vision_repair(attachment)
        ]
        if not targets:
            continue
        if remaining_limit is not None:
            if remaining_limit <= 0:
                break
            targets = targets[:remaining_limit]
            remaining_limit -= len(targets)
        summary["messages_considered"] += 1
        summary["attachments_targeted"] += len(targets)
        if dry_run:
            continue

        _renew_mailbox_po_repair_lease(audit_run.id, repair_lease_token)
        try:
            fetched_message = fetch_mailbox_message(connection, message.gmail_message_id)
            _renew_mailbox_po_repair_lease(audit_run.id, repair_lease_token)
            summary["messages_fetched"] += 1
        except Exception as exc:
            # A permanently deleted/forbidden Gmail message must not hold
            # reconciliation open forever. Persist the same bounded retry
            # state used for provider failures against every selected target.
            _renew_mailbox_po_repair_lease(audit_run.id, repair_lease_token)
            fetch_reason = f"Could not re-fetch Gmail message: {exc}"[:500]
            replacements = {}
            for target in targets:
                replacement, terminal = _mailbox_po_repair_retry_replacement(
                    target,
                    fetch_reason,
                )
                replacements[_attachment_identity(target)] = replacement
                if terminal:
                    summary["attachments_rejected"] += 1
                else:
                    summary["attachments_retryable"] += 1
            summary["errors"].append(
                {
                    "gmail_message_id": message.gmail_message_id,
                    "error": fetch_reason,
                }
            )
            if _persist_mailbox_po_repair_replacements(
                audit_run,
                message,
                replacements,
                lease_token=repair_lease_token,
            ):
                summary["messages_updated"] += 1
            continue

        fetched_refs = {}
        ambiguous_fetched_identities = set()
        for attachment in fetched_message.get("_attachment_refs") or []:
            identity = _attachment_identity(attachment)
            if identity[0] == "unidentifiable":
                continue
            if identity in fetched_refs:
                ambiguous_fetched_identities.add(identity)
            else:
                fetched_refs[identity] = attachment
        target_identity_counts = {}
        for target in targets:
            identity = _attachment_identity(target)
            target_identity_counts[identity] = target_identity_counts.get(identity, 0) + 1
        token = ""
        replacements = {}
        fetched_bytes = 0
        for target in targets:
            identity = _attachment_identity(target)
            source_ref = (
                fetched_refs.get(identity)
                if target_identity_counts.get(identity) == 1
                and identity not in ambiguous_fetched_identities
                else None
            )
            if source_ref is None:
                summary["attachments_missing"] += 1
                missing_warning = (
                    "The exact Gmail attachment part is missing or no longer unique; automatic repair stopped "
                    "and manual message review is required."
                )
                replacements[identity] = {
                    **target,
                    "status": "manual_review",
                    "manual_review_required": True,
                    "vision_repair_status": "manual",
                    "vision_identity_strategy": "mime_part_v2",
                    "vision_repair_reason": missing_warning,
                    "warnings": list(
                        dict.fromkeys([*(target.get("warnings") or []), missing_warning])
                    ),
                    "reason": missing_warning,
                }
                summary["errors"].append(
                    {
                        "gmail_message_id": message.gmail_message_id,
                        "attachment": str(target.get("filename") or identity),
                        "error": (
                            "The exact Gmail attachment part was missing or not unique; "
                            "the stored manifest was preserved."
                        ),
                    }
                )
                continue
            remaining_budget = MAX_TOTAL_ATTACHMENT_BYTES - fetched_bytes
            if remaining_budget <= 0:
                summary["attachments_retryable"] += 1
                continue
            if not token:
                token = get_valid_access_token(connection)
            _renew_mailbox_po_repair_lease(audit_run.id, repair_lease_token)
            repaired, byte_count = _preview_attachment(
                connection,
                message.gmail_message_id,
                source_ref,
                token,
                max_bytes=remaining_budget,
                actor=actor,
                vision_source_identity={
                    "audit_run_id": audit_run.id,
                    "gmail_message_id": message.gmail_message_id,
                    "attachment_id": source_ref.get("attachment_id") or "",
                    "part_id": source_ref.get("part_id") or "",
                },
            )
            _renew_mailbox_po_repair_lease(audit_run.id, repair_lease_token)
            fetched_bytes += max(0, int(byte_count or 0))
            if repaired.get("status") == "parsed" and (
                repaired.get("line_count", 0) or repaired.get("original_text")
                or repaired.get("vision_repair_status") == "completed"
            ):
                replacements[identity] = {
                    **repaired,
                    "vision_repaired_for_audit_run_id": audit_run.id,
                    "vision_identity_strategy": "mime_part_v2",
                }
                summary["attachments_repaired"] += 1
                continue

            repair_status = str(repaired.get("vision_repair_status") or "retryable")
            if repair_status == "rejected":
                # Preserve the original evidence fields and add only a bounded
                # terminal annotation so a 446-page report is not retried on
                # every resumable invocation.
                replacements[identity] = {
                    **target,
                    "vision_repair_status": "rejected",
                    "vision_repair_reason": str(
                        repaired.get("vision_repair_reason") or repaired.get("reason") or ""
                    )[:500],
                }
                summary["attachments_rejected"] += 1
            else:
                retry_reason = str(
                    repaired.get("reason")
                    or repaired.get("vision_repair_reason")
                    or "PDF repair did not produce review evidence"
                )[:500]
                replacement, terminal = _mailbox_po_repair_retry_replacement(
                    target,
                    retry_reason,
                )
                replacements[identity] = {
                    **replacement,
                    "vision_identity_strategy": "mime_part_v2",
                }
                if terminal:
                    summary["attachments_rejected"] += 1
                else:
                    summary["attachments_retryable"] += 1
                summary["errors"].append(
                    {
                        "gmail_message_id": message.gmail_message_id,
                        "attachment": str(target.get("filename") or identity),
                        "error": retry_reason,
                    }
                )

        if _persist_mailbox_po_repair_replacements(
            audit_run,
            message,
            replacements,
            lease_token=repair_lease_token,
        ):
            summary["messages_updated"] += 1
    summary["errors"] = summary["errors"][-MAX_RUN_ERRORS:]
    summary["repair_remaining"] = mailbox_po_audit_repair_remaining(audit_run)
    summary["repair_done"] = summary["repair_remaining"] == 0
    return summary


def mailbox_po_audit_repair_remaining(audit_run):
    remaining = 0
    memberships = (
        MailboxPOAuditRunMessage.objects.filter(audit_run=audit_run)
        .select_related("message")
        .order_by("message_id")
    )
    for membership in memberships.iterator(chunk_size=200):
        message = membership.message
        classification = classify_mailbox_message(
            {
                "subject": message.subject,
                "newest_body_text": message.newest_body_text,
                "snippet": message.snippet,
                "attachment_manifest": message.attachment_manifest,
                "label_ids": message.label_ids,
            }
        )
        classification_drift = any(
            getattr(message, field) != classification[field]
            for field in (
                "classification",
                "is_relevant",
                "auto_link_eligible",
                "relevance_reason",
                "extracted_po_references",
            )
        )
        remaining += int(classification_drift)
        if not classification["is_relevant"]:
            continue
        remaining += sum(
            1
            for attachment in (message.attachment_manifest or [])
            if attachment_needs_mailbox_vision_repair(attachment)
        )
    return remaining


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
    with transaction.atomic():
        # Repair's final canonical merge locks this same row. If a rescan is
        # started concurrently, either its new run becomes visible before the
        # repair guard (which aborts the stale merge), or it starts after the
        # completed repair commit.
        stored_connection = GmailOAuthConnection.objects.select_for_update().get(
            pk=connection.pk
        )
        if not stored_connection.is_shared:
            raise ValueError("Mailbox PO audits require the designated shared Gmail connection.")
        if stored_connection.status != GmailOAuthConnection.STATUS_CONNECTED:
            raise RuntimeError("The shared Gmail mailbox is not connected.")
        return MailboxPOAuditRun.objects.create(
            gmail_connection=stored_connection,
            requested_by=requested_by,
            earliest_quote_at=boundary,
            mailbox_cutoff_at=cutoff,
            gmail_query=build_mailbox_po_query(boundary, cutoff),
        )


def _persist_inventory_message(run, message, *, heartbeat=None):
    classification = classify_mailbox_message(message)
    manifest, candidate_count, fetched_bytes = hydrate_plausible_attachments(
        run.gmail_connection,
        message,
        is_relevant=classification["is_relevant"],
        heartbeat=heartbeat,
        allow_ai_vision=False,
        explicit_order_context=_has_explicit_order_context(message),
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
            inventory, _, candidates, fetched_bytes, attachment_errors = _persist_inventory_message(
                run,
                message,
                heartbeat=lambda: _renew_scan_lease(run.pk, lease_token),
            )
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
