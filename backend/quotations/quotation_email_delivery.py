"""Safe preview and Gmail delivery for finalized quotations."""

import base64
import hashlib
import re
import secrets
import urllib.error
from datetime import timedelta
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formataddr, getaddresses, parseaddr

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from .contract_intelligence import (
    can_manage_shared_gmail,
    gmail_fetch_reply_metadata,
    gmail_search_messages,
    gmail_send_raw_message,
    gmail_send_scope_granted,
    get_valid_access_token,
    resolve_gmail_connection,
)
from .models import (
    GmailInquiryImport,
    Quotation,
    QuotationAuditLog,
    QuotationEmailDelivery,
    QuotationEmailThreadSelection,
    QuotationSettings,
)
from .pdf import build_quotation_pdf
from .services import audit_log, finalize_quotation


THREAD_SELECTION_MAX_AGE = 30 * 60
MAX_TO_ADDRESSES = 10
MAX_CC_ADDRESSES = 10


class QuotationEmailError(ValidationError):
    def __init__(
        self,
        message,
        *,
        code="email_delivery_error",
        http_status=400,
        retryable=False,
        quote_finalized=False,
        delivery=None,
    ):
        super().__init__(message, code=code)
        self.message = str(message)
        self.code = code
        self.http_status = http_status
        self.retryable = bool(retryable)
        self.quote_finalized = bool(quote_finalized)
        self.delivery = delivery


def _require_staff(actor):
    if not actor or not getattr(actor, "is_authenticated", False) or not getattr(actor, "is_staff", False):
        raise QuotationEmailError(
            "Quotation staff access is required.",
            code="permission_denied",
            http_status=403,
        )


def _gmail_read_error(exc, *, operation):
    detail = str(exc or "").lower()
    reconnect = isinstance(exc, PermissionError) or any(
        marker in detail
        for marker in (
            "not connected",
            "reconnect gmail",
            "refresh token",
            "expired",
            "revoked",
            "http 401",
            "http 403",
        )
    )
    if reconnect:
        return QuotationEmailError(
            "Reconnect the shared Gmail mailbox before continuing.",
            code="gmail_reconnect_required",
            retryable=True,
        )
    return QuotationEmailError(
        f"Gmail could not {operation}. Try again.",
        code="gmail_read_unavailable",
        retryable=True,
    )


def _clean_addresses(values, *, field_name, maximum):
    if isinstance(values, str):
        values = [values]
    values = list(values or [])
    if len(values) > maximum:
        raise QuotationEmailError(f"Select no more than {maximum} {field_name} addresses.")
    cleaned = []
    seen = set()
    for value in values:
        _name, address = parseaddr(str(value or ""))
        address = address.strip().lower()
        try:
            validate_email(address)
        except ValidationError as exc:
            raise QuotationEmailError(f"Enter a valid {field_name} email address.") from exc
        if address not in seen:
            seen.add(address)
            cleaned.append(address)
    return cleaned


def _one_header_address(value, *, label):
    addresses = _clean_addresses(
        [address for _name, address in getaddresses([str(value or "")]) if address],
        field_name=label,
        maximum=2,
    )
    if len(addresses) != 1:
        raise QuotationEmailError(
            f"The source email has an ambiguous {label} address. Choose another source message.",
            code="ambiguous_source_recipient",
        )
    return addresses[0]


def _sender_addresses(value):
    result = []
    for _name, address in getaddresses([str(value or "")]):
        try:
            result.extend(_clean_addresses([address], field_name="sender", maximum=1))
        except QuotationEmailError:
            continue
    return result


def _reply_recipient(metadata):
    reply_to = str(metadata.get("reply_to") or "").strip()
    if reply_to:
        return _one_header_address(reply_to, label="Reply-To")
    return _one_header_address(metadata.get("sender"), label="From")


def _safe_download_part(value):
    return re.sub(r"[^A-Za-z0-9-]+", "_", str(value or "").upper()).strip("_-")[:80]


def quotation_attachment_filename(quotation):
    company = _safe_download_part(getattr(quotation.company, "name", ""))
    number = _safe_download_part(quotation.quotation_number) or "QUOTATION"
    return f"{company}-{number}.pdf" if company else f"{number}.pdf"


def _default_body(quotation):
    contact_name = str(getattr(quotation.contact, "name", "") or "").strip()
    salutation = contact_name.split()[0] if contact_name else "Sir or Madam"
    sender_name = str(QuotationSettings.get_solo().company_name or "Al Ameen Pharmacy LLC").strip()
    return (
        f"Dear {salutation},\n\n"
        "Greetings.\n\n"
        f"Thank you for your inquiry. Please find attached our quotation "
        f"{quotation.quotation_number} for your review.\n\n"
        "Should you require any clarification or revision, please feel free to contact us.\n\n"
        "Best regards,\n"
        f"{sender_name}"
    )


def _default_manual_recipient(quotation):
    contact_email = str(getattr(quotation.contact, "email", "") or "").strip()
    company_email = str(getattr(quotation.company, "email", "") or "").strip()
    candidate = contact_email or company_email
    if not candidate:
        return []
    return _clean_addresses([candidate], field_name="recipient", maximum=1)


def _gmail_import_for_quotation(quotation):
    query = GmailInquiryImport.objects.select_related("gmail_connection")
    direct = query.filter(quotation=quotation).first()
    if direct:
        return direct
    if quotation.inquiry_id:
        return query.filter(inquiry_id=quotation.inquiry_id).order_by("-confirmed_at", "-id").first()
    return None


def _manifest_sort_key(message):
    return (str(message.get("sent_at") or ""), str(message.get("gmail_message_id") or ""))


def _latest_relevant_inbound(gmail_import):
    inbound = [
        message
        for message in (gmail_import.message_manifest or [])
        if not message.get("is_outbound")
        and str(message.get("classification") or "") not in {"irrelevant", "our_reply"}
        and str(message.get("gmail_message_id") or "")
    ]
    relevant = [
        message
        for message in inbound
        if str(message.get("usage") or "") in {"used", "context"}
    ]
    candidates = relevant or inbound
    if not candidates:
        raise QuotationEmailError(
            "No verified inbound Gmail message is available for this quotation.",
            code="gmail_reply_source_missing",
        )
    return max(candidates, key=_manifest_sort_key)


def _validated_reply_source(
    connection,
    message_id,
    *,
    expected_thread_id="",
    scope_email="",
    metadata=None,
):
    if metadata is None:
        try:
            metadata = gmail_fetch_reply_metadata(connection, message_id)
        except Exception as exc:
            raise _gmail_read_error(exc, operation="verify the selected source message") from exc
    thread_id = str(metadata.get("gmail_thread_id") or "")
    if not thread_id or (expected_thread_id and thread_id != str(expected_thread_id)):
        raise QuotationEmailError(
            "The selected Gmail message no longer belongs to the expected thread.",
            code="gmail_thread_mismatch",
        )
    subject = str(metadata.get("subject") or "").strip()
    if not subject or "\r" in subject or "\n" in subject:
        raise QuotationEmailError(
            "The Gmail source has no safe matching subject.",
            code="gmail_subject_missing",
        )
    if len(subject) > 998:
        raise QuotationEmailError(
            "The Gmail source subject is longer than the safe 998-character email limit. Choose another source message.",
            code="gmail_subject_too_long",
        )
    rfc_message_id = str(metadata.get("rfc_message_id") or "").strip()
    if not re.fullmatch(r"<[^<>\r\n]+>", rfc_message_id):
        raise QuotationEmailError(
            "The Gmail source has no valid RFC Message-ID, so a safe threaded reply cannot be prepared. Choose another source message.",
            code="source_message_id_missing",
        )
    if scope_email and scope_email.lower() not in set(_sender_addresses(metadata.get("sender"))):
        raise QuotationEmailError(
            "The selected Gmail message is not from the confirmed email address.",
            code="gmail_source_sender_mismatch",
        )
    recipient = _reply_recipient(metadata)
    mailbox_email = str(getattr(connection, "email", "") or "").strip().lower()
    if recipient == mailbox_email:
        raise QuotationEmailError(
            "The reply recipient resolves to the shared mailbox itself.",
            code="gmail_self_recipient",
        )
    sender_name, sender_email = parseaddr(str(metadata.get("sender") or ""))
    received_at = metadata.get("sent_at")
    if hasattr(received_at, "isoformat"):
        received_at = received_at.isoformat()
    return {
        "metadata": metadata,
        "recipient": recipient,
        "subject": subject,
        "trusted_source": {
            "sender_name": sender_name,
            "sender_email": sender_email.lower(),
            "reply_to_email": recipient,
            "subject": subject,
            "received_at": received_at,
            "scope_email": scope_email.lower(),
        },
    }


def _thread_selection_token(
    *,
    quotation,
    connection,
    actor,
    message_id,
    thread_id,
    source_email,
):
    token = secrets.token_urlsafe(32)
    QuotationEmailThreadSelection.objects.create(
        quotation=quotation,
        gmail_connection=connection,
        created_by=actor,
        token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        source_email=str(source_email).lower(),
        gmail_message_id=str(message_id),
        gmail_thread_id=str(thread_id),
        expires_at=timezone.now() + timedelta(seconds=THREAD_SELECTION_MAX_AGE),
    )
    return token


def _read_thread_selection(token, quotation, actor):
    token_hash = hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()
    selection = QuotationEmailThreadSelection.objects.select_related(
        "gmail_connection"
    ).filter(
        quotation=quotation,
        created_by=actor,
        token_hash=token_hash,
    ).first()
    if not selection or selection.expires_at <= timezone.now():
        if selection:
            selection.delete()
        raise QuotationEmailError(
            "That Gmail thread selection expired. Search and select the message again.",
            code="thread_selection_expired",
        )
    source_email = _clean_addresses(
        [selection.source_email],
        field_name="source",
        maximum=1,
    )[0]
    if not selection.gmail_message_id:
        raise QuotationEmailError("That Gmail message selection is invalid.")
    selection.last_used_at = timezone.now()
    selection.save(update_fields=["last_used_at"])
    QuotationEmailThreadSelection.objects.filter(
        quotation=quotation,
        created_by=actor,
    ).exclude(pk=selection.pk).delete()
    return selection, source_email


def find_manual_thread_candidates(quotation, actor, recipient, *, limit=10):
    _require_staff(actor)
    QuotationEmailThreadSelection.objects.filter(
        expires_at__lte=timezone.now()
    ).delete()
    if _gmail_import_for_quotation(quotation):
        raise QuotationEmailError(
            "This quotation is already linked to its Gmail inquiry thread.",
            code="gmail_thread_already_linked",
        )
    recipient = _clean_addresses([recipient], field_name="recipient", maximum=1)[0]
    # A new search supersedes any earlier manual-thread chooser for this
    # quotation and staff user. This keeps abandoned searches bounded and
    # prevents an old browser tab from silently selecting stale evidence.
    QuotationEmailThreadSelection.objects.filter(
        quotation=quotation,
        created_by=actor,
    ).delete()
    connection = resolve_gmail_connection(actor, shared_only=True)
    if not connection:
        raise QuotationEmailError("Connect the shared Gmail mailbox before searching for a thread.")
    try:
        result = gmail_search_messages(
            connection,
            f"from:{recipient} newer_than:2y",
            max_messages=min(max(int(limit or 10), 1), 20),
        )
    except Exception as exc:
        raise _gmail_read_error(exc, operation="search recent inbound messages") from exc
    candidate_sources = []
    metadata_errors = []
    for row in result.get("messages") or []:
        message_id = str(row.get("id") or "")
        if not message_id:
            continue
        try:
            metadata = gmail_fetch_reply_metadata(connection, message_id)
        except Exception as exc:
            metadata_errors.append(exc)
            continue
        if recipient not in set(_sender_addresses(metadata.get("sender"))):
            continue
        try:
            source = _validated_reply_source(
                connection,
                message_id,
                expected_thread_id=metadata.get("gmail_thread_id"),
                scope_email=recipient,
                metadata=metadata,
            )
        except QuotationEmailError:
            continue
        candidate_sources.append(
            {
                "_gmail_message_id": message_id,
                "_gmail_thread_id": metadata.get("gmail_thread_id") or "",
                "sender_name": source["trusted_source"]["sender_name"],
                "sender_email": source["trusted_source"]["sender_email"],
                "reply_to_email": source["recipient"],
                "subject": source["subject"],
                "received_at": metadata.get("sent_at"),
                "snippet": str(metadata.get("snippet") or "")[:500],
            }
        )
    candidate_sources.sort(
        key=lambda row: str(row.get("received_at") or ""),
        reverse=True,
    )
    if not candidate_sources and metadata_errors and result.get("messages"):
        raise _gmail_read_error(
            metadata_errors[-1],
            operation="verify the matching inbound messages",
        )
    candidates = []
    # Serialize issuance for this quotation. Two simultaneous searches cannot
    # leave two independent batches of active opaque selection tokens behind.
    with transaction.atomic():
        Quotation.objects.select_for_update().get(pk=quotation.pk)
        QuotationEmailThreadSelection.objects.filter(
            quotation=quotation,
            created_by=actor,
        ).delete()
        for source in candidate_sources:
            candidates.append(
                {
                    "selection_token": _thread_selection_token(
                        quotation=quotation,
                        connection=connection,
                        actor=actor,
                        message_id=source.pop("_gmail_message_id"),
                        thread_id=source.pop("_gmail_thread_id"),
                        source_email=recipient,
                    ),
                    **source,
                }
            )
    return {"recipient": recipient, "candidates": candidates}


def _source_for_preview(quotation, actor, existing, thread_selection_token=""):
    gmail_import = _gmail_import_for_quotation(quotation)
    connection = (
        gmail_import.gmail_connection
        if gmail_import and gmail_import.gmail_connection_id
        else resolve_gmail_connection(actor, shared_only=True)
    )
    if gmail_import:
        if not connection or connection.status != connection.STATUS_CONNECTED:
            raise QuotationEmailError(
                "Reconnect the shared Gmail mailbox before preparing this reply.",
                code="gmail_reconnect_required",
                retryable=True,
            )
        manifest = _latest_relevant_inbound(gmail_import)
        source = _validated_reply_source(
            connection,
            manifest["gmail_message_id"],
            expected_thread_id=gmail_import.gmail_thread_id,
        )
        return {
            **source,
            "mode": QuotationEmailDelivery.MODE_GMAIL_REPLY,
            "connection": connection,
            "gmail_import": gmail_import,
        }

    if thread_selection_token:
        if not connection or connection.status != connection.STATUS_CONNECTED:
            raise QuotationEmailError(
                "Reconnect the shared Gmail mailbox before preparing this reply.",
                code="gmail_reconnect_required",
                retryable=True,
            )
        selection, source_email = _read_thread_selection(
            thread_selection_token,
            quotation,
            actor,
        )
        if selection.gmail_connection_id != connection.id:
            raise QuotationEmailError(
                "That Gmail thread selection belongs to a previous mailbox connection. Search again."
            )
        source = _validated_reply_source(
            connection,
            selection.gmail_message_id,
            expected_thread_id=selection.gmail_thread_id,
            scope_email=source_email,
        )
        return {
            **source,
            "mode": QuotationEmailDelivery.MODE_GMAIL_REPLY,
            "connection": connection,
            "gmail_import": None,
        }

    if (
        existing
        and existing.delivery_mode == QuotationEmailDelivery.MODE_GMAIL_REPLY
        and existing.source_gmail_message_id
        and existing.status in {
            QuotationEmailDelivery.STATUS_PREPARED,
            QuotationEmailDelivery.STATUS_FAILED,
        }
    ):
        if not connection or connection.status != connection.STATUS_CONNECTED:
            raise QuotationEmailError(
                "Reconnect the shared Gmail mailbox before preparing this reply.",
                code="gmail_reconnect_required",
                retryable=True,
            )
        source = _validated_reply_source(
            connection,
            existing.source_gmail_message_id,
            expected_thread_id=existing.gmail_thread_id,
            scope_email=str((existing.trusted_source or {}).get("scope_email") or ""),
        )
        return {
            **source,
            "mode": QuotationEmailDelivery.MODE_GMAIL_REPLY,
            "connection": connection,
            "gmail_import": None,
        }

    return {
        "mode": QuotationEmailDelivery.MODE_NEW_EMAIL,
        "connection": connection,
        "gmail_import": None,
        "metadata": {},
        "recipient": (_default_manual_recipient(quotation) or [""])[0],
        "subject": f"Quotation {quotation.quotation_number}",
        "trusted_source": {},
    }


def _set_source_fields(delivery, source):
    metadata = source.get("metadata") or {}
    delivery.delivery_mode = source["mode"]
    delivery.gmail_connection = source.get("connection")
    delivery.gmail_inquiry_import = source.get("gmail_import")
    delivery.trusted_source = source.get("trusted_source") or {}
    if source["mode"] == QuotationEmailDelivery.MODE_GMAIL_REPLY:
        delivery.gmail_thread_id = str(metadata.get("gmail_thread_id") or "")
        delivery.source_gmail_message_id = str(metadata.get("gmail_message_id") or "")
        delivery.source_rfc_message_id = str(metadata.get("rfc_message_id") or "")[:998]
        delivery.source_references = str(metadata.get("references") or "")[:10000]
    else:
        delivery.gmail_thread_id = ""
        delivery.source_gmail_message_id = ""
        delivery.source_rfc_message_id = ""
        delivery.source_references = ""


def _populate_preview_delivery(
    delivery,
    quotation,
    actor,
    source,
    *,
    created,
    replace_recipient_and_subject=False,
):
    if created:
        delivery.body = _default_body(quotation)
        delivery.attachment_filename = quotation_attachment_filename(quotation)
        delivery.status = QuotationEmailDelivery.STATUS_PREPARED
    _set_source_fields(delivery, source)
    delivery.actor = actor
    if created or replace_recipient_and_subject:
        delivery.to_addresses = [source["recipient"]] if source.get("recipient") else []
        delivery.subject = source["subject"]
    delivery.prepared_at = timezone.now()
    return delivery


def prepare_email_preview(
    quotation,
    actor,
    *,
    thread_selection_token="",
    persist=False,
):
    """Build a preview before finalization; persist only when sending starts."""

    _require_staff(actor)
    quotation = Quotation.objects.select_related("company", "contact", "inquiry").get(pk=quotation.pk)
    existing = QuotationEmailDelivery.objects.select_related(
        "gmail_connection", "gmail_inquiry_import"
    ).filter(quotation=quotation).first()
    if existing and existing.status in {
        QuotationEmailDelivery.STATUS_SENT,
        QuotationEmailDelivery.STATUS_UNKNOWN,
        QuotationEmailDelivery.STATUS_SENDING,
    }:
        return existing
    source = _source_for_preview(
        quotation,
        actor,
        existing,
        thread_selection_token=thread_selection_token,
    )
    if not persist:
        transient = existing or QuotationEmailDelivery(quotation=quotation)
        return _populate_preview_delivery(
            transient,
            quotation,
            actor,
            source,
            created=existing is None,
            replace_recipient_and_subject=bool(
                thread_selection_token or source.get("gmail_import")
            ),
        )

    with transaction.atomic():
        # Contact and inquiry are nullable, so their select_related joins are
        # LEFT OUTER JOINs. PostgreSQL rejects an unrestricted FOR UPDATE over
        # those joins; lock only the quotation row while retaining the related
        # data needed to build the preview.
        locked_quote = (
            Quotation.objects.select_for_update(of=("self",))
            .select_related("company", "contact", "inquiry")
            .get(pk=quotation.pk)
        )
        delivery = QuotationEmailDelivery.objects.select_for_update().filter(
            quotation=locked_quote
        ).first()
        if delivery and delivery.status in {
            QuotationEmailDelivery.STATUS_SENT,
            QuotationEmailDelivery.STATUS_UNKNOWN,
            QuotationEmailDelivery.STATUS_SENDING,
        }:
            return delivery
        created = delivery is None
        if created:
            delivery = QuotationEmailDelivery(quotation=locked_quote)
        _populate_preview_delivery(
            delivery,
            locked_quote,
            actor,
            source,
            created=created,
            replace_recipient_and_subject=bool(
                thread_selection_token or source.get("gmail_import")
            ),
        )
        delivery.save()
        if created:
            audit_log(
                actor,
                QuotationAuditLog.ACTION_EMAIL_PREPARED,
                delivery,
                message=f"Prepared customer email for {locked_quote.quotation_number}.",
                changes={"delivery_mode": delivery.delivery_mode},
            )
        return delivery


def delivery_preview_payload(delivery, actor=None):
    connection = delivery.gmail_connection
    connected = bool(connection and connection.status == connection.STATUS_CONNECTED)
    send_authorized = bool(connected and gmail_send_scope_granted(connection))
    warnings = []
    if not connected:
        warnings.append("The shared Gmail mailbox is not connected.")
    elif not send_authorized:
        warnings.append(
            "Reconnect the shared Gmail mailbox and approve Gmail send permission before sending."
        )
    if not delivery.to_addresses:
        warnings.append("Enter and confirm the recipient email address before sending.")
    if delivery.status == QuotationEmailDelivery.STATUS_UNKNOWN:
        warnings.append(
            "Gmail did not confirm the previous attempt. Do not resend until the shared mailbox is checked."
        )
    return {
        "delivery_id": delivery.id,
        "delivery_mode": delivery.delivery_mode,
        "can_reply_to_thread": delivery.delivery_mode == QuotationEmailDelivery.MODE_GMAIL_REPLY,
        "trusted_source": delivery.trusted_source or {},
        "thread": {
            "linked": delivery.delivery_mode == QuotationEmailDelivery.MODE_GMAIL_REPLY,
            "subject": delivery.subject if delivery.delivery_mode == QuotationEmailDelivery.MODE_GMAIL_REPLY else "",
        },
        "to": list(delivery.to_addresses or []),
        "cc": list(delivery.cc_addresses or []),
        "subject": delivery.subject,
        "body": delivery.body,
        "attachment_filename": delivery.attachment_filename,
        "attachment_sha256": delivery.attachment_sha256,
        "attachment_size": delivery.attachment_size,
        "status": delivery.status,
        "can_reconcile": delivery.status in {
            QuotationEmailDelivery.STATUS_SENDING,
            QuotationEmailDelivery.STATUS_UNKNOWN,
        },
        "attempt_count": delivery.attempt_count,
        "last_error": delivery.last_error,
        "warnings": warnings,
        "gmail_connected": connected,
        "gmail_send_authorized": send_authorized,
        "gmail_can_manage": can_manage_shared_gmail(actor, connection),
        "reconnect_required": bool(connected and not send_authorized),
    }


def delivery_payload(delivery):
    return {
        "id": delivery.id,
        "quotation": delivery.quotation_id,
        "delivery_mode": delivery.delivery_mode,
        "status": delivery.status,
        "can_reconcile": delivery.status in {
            QuotationEmailDelivery.STATUS_SENDING,
            QuotationEmailDelivery.STATUS_UNKNOWN,
        },
        "to": list(delivery.to_addresses or []),
        "cc": list(delivery.cc_addresses or []),
        "subject": delivery.subject,
        "body": delivery.body,
        "trusted_source": delivery.trusted_source or {},
        "attachment_filename": delivery.attachment_filename,
        "attachment_sha256": delivery.attachment_sha256,
        "attachment_size": delivery.attachment_size,
        "attempt_count": delivery.attempt_count,
        "last_error": delivery.last_error,
        "prepared_at": delivery.prepared_at,
        "sending_started_at": delivery.sending_started_at,
        "sent_at": delivery.sent_at,
        "failed_at": delivery.failed_at,
        "created_at": delivery.created_at,
        "updated_at": delivery.updated_at,
    }


def _validate_editable_fields(delivery, data):
    to_addresses = _clean_addresses(data.get("to"), field_name="recipient", maximum=MAX_TO_ADDRESSES)
    if not to_addresses:
        raise QuotationEmailError("Enter the recipient email address.")
    cc_addresses = _clean_addresses(data.get("cc") or [], field_name="CC", maximum=MAX_CC_ADDRESSES)
    subject = str(data.get("subject") or "").strip()
    if not subject or len(subject) > 998 or "\r" in subject or "\n" in subject:
        raise QuotationEmailError("Enter a valid single-line email subject.")
    body = str(data.get("body") or "").strip()
    if not body or len(body) > 100000:
        raise QuotationEmailError("Enter an email body of no more than 100,000 characters.")
    if data.get("confirm_recipient") is not True:
        raise QuotationEmailError("Confirm the recipient before sending the quotation.")
    if delivery.delivery_mode == QuotationEmailDelivery.MODE_GMAIL_REPLY:
        trusted_to = _reply_recipient(
            {
                "reply_to": (delivery.trusted_source or {}).get("reply_to_email") or "",
                "sender": (delivery.trusted_source or {}).get("sender_email") or "",
            }
        )
        if to_addresses != [trusted_to]:
            raise QuotationEmailError(
                "A Gmail-thread reply must use the verified Reply-To/From address shown in the preview.",
                code="gmail_recipient_mismatch",
            )
        if subject != delivery.subject:
            raise QuotationEmailError(
                "A Gmail-thread reply must keep the exact source subject so Gmail can preserve the thread.",
                code="gmail_subject_mismatch",
            )
    return to_addresses, cc_addresses, subject, body


def _references_header(delivery):
    values = re.findall(r"<[^<>\r\n]+>", str(delivery.source_references or ""))
    source_id = str(delivery.source_rfc_message_id or "").strip()
    if source_id and source_id not in values:
        values.append(source_id)
    return " ".join(dict.fromkeys(values))


def _build_raw_message(delivery, pdf_bytes):
    connection = delivery.gmail_connection
    from_email = str(getattr(connection, "email", "") or "").strip().lower()
    validate_email(from_email)
    sender_name = str(QuotationSettings.get_solo().company_name or "Al Ameen Pharmacy LLC").strip()
    message = EmailMessage(policy=SMTP)
    message["From"] = formataddr((sender_name, from_email))
    message["To"] = ", ".join(delivery.to_addresses or [])
    if delivery.cc_addresses:
        message["Cc"] = ", ".join(delivery.cc_addresses)
    message["Subject"] = delivery.subject
    message["Message-ID"] = delivery.outbound_rfc_message_id
    if delivery.delivery_mode == QuotationEmailDelivery.MODE_GMAIL_REPLY:
        if not delivery.source_rfc_message_id:
            raise QuotationEmailError(
                "The source email has no RFC Message-ID, so a safe threaded reply cannot be created.",
                code="source_message_id_missing",
            )
        message["In-Reply-To"] = delivery.source_rfc_message_id
        references = _references_header(delivery)
        if references:
            message["References"] = references
    message.set_content(delivery.body)
    message.add_attachment(
        pdf_bytes,
        maintype="application",
        subtype="pdf",
        filename=delivery.attachment_filename,
    )
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")


def _mark_delivery_failure(delivery_id, *, unknown, message, actor):
    now = timezone.now()
    quotation_id = QuotationEmailDelivery.objects.only("quotation_id").get(
        pk=delivery_id
    ).quotation_id
    with transaction.atomic():
        quotation = Quotation.objects.select_for_update().select_related("company").get(
            pk=quotation_id
        )
        delivery = QuotationEmailDelivery.objects.select_for_update().get(pk=delivery_id)
        # SENT is terminal. A reconciliation request can prove delivery while
        # the original request is unwinding from a timeout; that late failure
        # must never downgrade the reconciled delivery or emit a false failure
        # audit record.
        if delivery.status == QuotationEmailDelivery.STATUS_SENT:
            return delivery
        delivery.status = (
            QuotationEmailDelivery.STATUS_UNKNOWN
            if unknown
            else QuotationEmailDelivery.STATUS_FAILED
        )
        delivery.last_error = str(message)[:1000]
        delivery.failed_at = now
        delivery.save(update_fields=["status", "last_error", "failed_at", "updated_at"])
        audit_log(
            actor,
            QuotationAuditLog.ACTION_EMAIL_UNKNOWN if unknown else QuotationAuditLog.ACTION_EMAIL_FAILED,
            delivery,
            message=(
                f"Gmail delivery outcome is unknown for {delivery.quotation.quotation_number}."
                if unknown
                else f"Email delivery failed for {delivery.quotation.quotation_number}."
            ),
            changes={"attempt_count": delivery.attempt_count},
            company=quotation.company,
            quotation=quotation,
        )
        return delivery


def _record_successful_delivery(delivery_id, gmail_message_id, sent_thread_id, actor):
    now = timezone.now()
    quotation_id = QuotationEmailDelivery.objects.only("quotation_id").get(
        pk=delivery_id
    ).quotation_id
    with transaction.atomic():
        # Global workflow lock order is Quotation, then EmailDelivery. Keep it
        # identical to preview/send/cancel/revise paths to avoid PostgreSQL
        # deadlocks under simultaneous staff actions.
        sent_quote = Quotation.objects.select_for_update().get(pk=quotation_id)
        sent_delivery = QuotationEmailDelivery.objects.select_for_update().select_related(
            "quotation"
        ).get(pk=delivery_id)
        if sent_delivery.status == QuotationEmailDelivery.STATUS_SENT:
            return sent_quote, sent_delivery
        sent_delivery.status = QuotationEmailDelivery.STATUS_SENT
        sent_delivery.gmail_message_id = str(gmail_message_id)
        sent_delivery.sent_gmail_thread_id = str(sent_thread_id or "")
        sent_delivery.sent_at = now
        sent_delivery.failed_at = None
        sent_delivery.last_error = ""
        sent_delivery.save(
            update_fields=[
                "status",
                "gmail_message_id",
                "sent_gmail_thread_id",
                "sent_at",
                "failed_at",
                "last_error",
                "updated_at",
            ]
        )
        if sent_quote.status == Quotation.STATUS_FINALIZED:
            old_status = sent_quote.status
            sent_quote.status = Quotation.STATUS_SENT
            sent_quote.sent_at = now
            if not sent_quote.next_follow_up_date:
                sent_quote.next_follow_up_date = timezone.localdate() + timedelta(days=7)
            sent_quote.save(
                update_fields=["status", "sent_at", "next_follow_up_date", "updated_at"]
            )
            audit_log(
                actor,
                QuotationAuditLog.ACTION_STATUS_CHANGED,
                sent_quote,
                message=f"Quotation status changed from {old_status} to {sent_quote.status} after Gmail delivery.",
                changes={"old_status": old_status, "new_status": sent_quote.status},
            )
        audit_log(
            actor,
            QuotationAuditLog.ACTION_EMAIL_SENT,
            sent_delivery,
            message=f"Emailed quotation {sent_quote.quotation_number} through the shared Gmail mailbox.",
            changes={
                "delivery_mode": sent_delivery.delivery_mode,
                "attachment_sha256": sent_delivery.attachment_sha256,
            },
        )
        QuotationEmailThreadSelection.objects.filter(
            quotation=sent_quote
        ).delete()
    return sent_quote, sent_delivery


def _reconcile_delivery_with_gmail(delivery, actor):
    """Find a strictly verified sent message for an ambiguous delivery.

    ``None`` means Gmail was checked successfully and no qualifying sent
    message exists. Gmail/API failures raise a distinct error so callers never
    misreport an unavailable check as a genuine not-found result.
    """

    connection = delivery.gmail_connection
    if (
        not connection
        or not connection.is_shared
        or connection.status != connection.STATUS_CONNECTED
    ):
        raise QuotationEmailError(
            "Reconnect the shared Gmail mailbox before reconciling this delivery.",
            code="gmail_reconnect_required",
            retryable=False,
            quote_finalized=True,
            delivery=delivery,
        )
    mailbox_email = str(connection.email or "").strip().lower()
    if not mailbox_email:
        raise QuotationEmailError(
            "The connected shared Gmail mailbox has no verified email identity.",
            code="gmail_reconnect_required",
            retryable=False,
            quote_finalized=True,
            delivery=delivery,
        )
    outbound_message_id = str(delivery.outbound_rfc_message_id or "").strip()
    if not re.fullmatch(r"<[^<>\r\n]+>", outbound_message_id):
        raise QuotationEmailError(
            "This delivery has no valid stable Message-ID and cannot be reconciled safely.",
            code="delivery_reconciliation_invalid",
            retryable=False,
            quote_finalized=True,
            delivery=delivery,
        )
    message_id = outbound_message_id[1:-1]
    if (
        delivery.delivery_mode == QuotationEmailDelivery.MODE_GMAIL_REPLY
        and not str(delivery.gmail_thread_id or "").strip()
    ):
        raise QuotationEmailError(
            "This Gmail reply delivery has no expected source thread and cannot be reconciled safely.",
            code="delivery_reconciliation_invalid",
            retryable=False,
            quote_finalized=True,
            delivery=delivery,
        )
    try:
        result = gmail_search_messages(
            connection,
            f"in:sent from:{mailbox_email} rfc822msgid:{message_id}",
            max_messages=10,
        )
    except Exception as exc:
        base_error = _gmail_read_error(
            exc,
            operation="check the Sent mailbox for this delivery",
        )
        raise QuotationEmailError(
            base_error.message,
            code=(
                base_error.code
                if base_error.code == "gmail_reconnect_required"
                else "gmail_reconciliation_unavailable"
            ),
            http_status=(
                base_error.http_status
                if base_error.code == "gmail_reconnect_required"
                else 503
            ),
            retryable=False,
            quote_finalized=True,
            delivery=delivery,
        ) from exc

    metadata_errors = []
    verified_matches = []
    for candidate in result.get("messages") or []:
        candidate_id = str(candidate.get("id") or "")
        if not candidate_id:
            metadata_errors.append(ValueError("Gmail returned a message without an ID."))
            continue
        try:
            metadata = gmail_fetch_reply_metadata(connection, candidate_id)
        except Exception as exc:
            metadata_errors.append(exc)
            continue

        label_ids = {
            str(value or "").strip().upper()
            for value in (metadata.get("label_ids") or [])
        }
        if "SENT" not in label_ids:
            continue
        try:
            sender_email = _one_header_address(
                metadata.get("sender"),
                label="From",
            )
        except QuotationEmailError as exc:
            metadata_errors.append(exc)
            continue
        if sender_email != mailbox_email:
            continue
        if (
            str(metadata.get("rfc_message_id") or "").strip()
            != delivery.outbound_rfc_message_id
        ):
            continue
        thread_id = str(metadata.get("gmail_thread_id") or "").strip()
        if not thread_id:
            continue
        expected_thread_id = str(
            delivery.gmail_thread_id
            if delivery.delivery_mode == QuotationEmailDelivery.MODE_GMAIL_REPLY
            else delivery.sent_gmail_thread_id
            or ""
        ).strip()
        if expected_thread_id and thread_id != expected_thread_id:
            continue
        verified_matches.append((candidate_id, thread_id))

    if metadata_errors:
        base_error = _gmail_read_error(
            metadata_errors[-1],
            operation="verify the matching Sent message",
        )
        raise QuotationEmailError(
            base_error.message,
            code=(
                base_error.code
                if base_error.code == "gmail_reconnect_required"
                else "gmail_reconciliation_unavailable"
            ),
            http_status=(
                base_error.http_status
                if base_error.code == "gmail_reconnect_required"
                else 503
            ),
            retryable=False,
            quote_finalized=True,
            delivery=delivery,
        ) from metadata_errors[-1]
    if len(verified_matches) > 1:
        raise QuotationEmailError(
            "Gmail returned more than one fully verified sent message for this delivery.",
            code="delivery_reconciliation_ambiguous",
            http_status=409,
            retryable=False,
            quote_finalized=True,
            delivery=delivery,
        )
    if verified_matches:
        candidate_id, thread_id = verified_matches[0]
        return _record_successful_delivery(
            delivery.id,
            candidate_id,
            thread_id,
            actor,
        )
    return None


def _reconcile_after_ambiguous_outcome(delivery, actor):
    """Reconcile or preserve the delivery's non-retryable ambiguous state."""

    try:
        return _reconcile_delivery_with_gmail(delivery, actor)
    except QuotationEmailError as exc:
        current = QuotationEmailDelivery.objects.select_related(
            "quotation", "gmail_connection"
        ).get(pk=delivery.pk)
        if current.status == QuotationEmailDelivery.STATUS_SENT:
            return current.quotation, current
        if current.status == QuotationEmailDelivery.STATUS_SENDING:
            current = _mark_delivery_failure(
                current.id,
                unknown=True,
                message=(
                    "Gmail reconciliation could not be completed. The delivery remains unknown; "
                    "do not resend it blindly."
                ),
                actor=actor,
            )
            if current.status == QuotationEmailDelivery.STATUS_SENT:
                return current.quotation, current
        raise QuotationEmailError(
            f"{exc.message} The delivery remains locked as unknown; do not resend it.",
            code=exc.code,
            http_status=exc.http_status,
            retryable=False,
            quote_finalized=True,
            delivery=current,
        ) from exc


def _stale_sending(delivery):
    seconds = max(int(getattr(settings, "QUOTATION_EMAIL_SENDING_STALE_SECONDS", 300) or 300), 30)
    return bool(
        delivery.sending_started_at
        and delivery.sending_started_at <= timezone.now() - timedelta(seconds=seconds)
    )


def reconcile_quotation_email(quotation, actor):
    """Check Gmail by stable Message-ID without ever sending a message."""

    _require_staff(actor)
    quotation = Quotation.objects.get(pk=quotation.pk)
    delivery = QuotationEmailDelivery.objects.select_related(
        "quotation", "gmail_connection"
    ).filter(quotation=quotation).first()
    if not delivery:
        raise QuotationEmailError(
            "This quotation has no email delivery to reconcile.",
            code="email_delivery_missing",
        )
    if delivery.status == QuotationEmailDelivery.STATUS_SENT:
        return delivery.quotation, delivery, True
    if delivery.status == QuotationEmailDelivery.STATUS_SENDING and not _stale_sending(delivery):
        raise QuotationEmailError(
            "This quotation email is still being sent. Check again shortly.",
            code="delivery_in_progress",
            http_status=409,
            quote_finalized=quotation.status in {
                Quotation.STATUS_FINALIZED,
                Quotation.STATUS_SENT,
            },
            delivery=delivery,
        )
    if delivery.status not in {
        QuotationEmailDelivery.STATUS_UNKNOWN,
        QuotationEmailDelivery.STATUS_SENDING,
    }:
        raise QuotationEmailError(
            "Only an ambiguous Gmail delivery needs reconciliation.",
            code="delivery_not_ambiguous",
            delivery=delivery,
        )
    reconciled = _reconcile_after_ambiguous_outcome(delivery, actor)
    if reconciled:
        quote, sent_delivery = reconciled
        return quote, sent_delivery, True
    if delivery.status == QuotationEmailDelivery.STATUS_SENDING:
        delivery = _mark_delivery_failure(
            delivery.id,
            unknown=True,
            message=(
                "Gmail still does not confirm the delivery. Check the shared Sent mailbox; "
                "do not retry blindly."
            ),
            actor=actor,
        )
        if delivery.status == QuotationEmailDelivery.STATUS_SENT:
            return delivery.quotation, delivery, True
    else:
        delivery.refresh_from_db()
    quotation.refresh_from_db()
    return quotation, delivery, False


def send_quotation_email(
    quotation,
    actor,
    data,
    *,
    finalize_first,
    thread_selection_token="",
):
    """Finalize (when requested), commit, then send outside the transaction."""

    _require_staff(actor)
    preflight = prepare_email_preview(
        quotation,
        actor,
        thread_selection_token=thread_selection_token,
        persist=False,
    )
    prepared_access_token = None
    if preflight.status not in {
        QuotationEmailDelivery.STATUS_SENT,
        QuotationEmailDelivery.STATUS_SENDING,
        QuotationEmailDelivery.STATUS_UNKNOWN,
    }:
        _validate_editable_fields(preflight, data)
        if (
            not preflight.gmail_connection
            or preflight.gmail_connection.status
            != preflight.gmail_connection.STATUS_CONNECTED
        ):
            raise QuotationEmailError(
                "Connect the shared Gmail mailbox before finalizing and sending.",
                code="gmail_reconnect_required",
                retryable=True,
                quote_finalized=quotation.status in {
                    Quotation.STATUS_FINALIZED,
                    Quotation.STATUS_SENT,
                },
            )
        if not gmail_send_scope_granted(preflight.gmail_connection):
            raise QuotationEmailError(
                "Reconnect the shared Gmail mailbox and approve Gmail send permission before sending.",
                code="gmail_reconnect_required",
                retryable=True,
                quote_finalized=quotation.status in {
                    Quotation.STATUS_FINALIZED,
                    Quotation.STATUS_SENT,
                },
            )
        try:
            prepared_access_token = get_valid_access_token(
                preflight.gmail_connection
            )
        except Exception as exc:
            raise QuotationEmailError(
                "Reconnect the shared Gmail mailbox before finalizing and sending.",
                code="gmail_reconnect_required",
                retryable=True,
                quote_finalized=quotation.status in {
                    Quotation.STATUS_FINALIZED,
                    Quotation.STATUS_SENT,
                },
            ) from exc
        preview = prepare_email_preview(
            quotation,
            actor,
            thread_selection_token=thread_selection_token,
            persist=True,
        )
    else:
        preview = preflight
    if preview.status == QuotationEmailDelivery.STATUS_UNKNOWN:
        reconciled = _reconcile_after_ambiguous_outcome(preview, actor)
        if reconciled:
            reconciled_quote, reconciled_delivery = reconciled
            return reconciled_quote, reconciled_delivery, True
    if preview.status == QuotationEmailDelivery.STATUS_SENDING and _stale_sending(preview):
        reconciled = _reconcile_after_ambiguous_outcome(preview, actor)
        if reconciled:
            reconciled_quote, reconciled_delivery = reconciled
            return reconciled_quote, reconciled_delivery, True
        stale = _mark_delivery_failure(
            preview.id,
            unknown=True,
            message=(
                "The previous delivery process stopped before Gmail confirmed the result. "
                "Check the shared Sent mailbox; do not retry blindly."
            ),
            actor=actor,
        )
        if stale.status == QuotationEmailDelivery.STATUS_SENT:
            return stale.quotation, stale, True
        raise QuotationEmailError(
            stale.last_error,
            code="delivery_unknown",
            http_status=409,
            quote_finalized=stale.quotation.status in {
                Quotation.STATUS_FINALIZED,
                Quotation.STATUS_SENT,
            },
            delivery=stale,
        )
    with transaction.atomic():
        # See _record_successful_delivery: quote must always be locked first.
        locked_quote = Quotation.objects.select_for_update().get(pk=quotation.pk)
        locked_delivery = (
            QuotationEmailDelivery.objects.select_for_update(of=("self",))
            .select_related("quotation", "gmail_connection")
            .get(pk=preview.pk)
        )
        if locked_delivery.status == QuotationEmailDelivery.STATUS_SENT:
            return locked_quote, locked_delivery, True
        if locked_delivery.status == QuotationEmailDelivery.STATUS_UNKNOWN:
            raise QuotationEmailError(
                "Gmail did not confirm the previous delivery. Check the shared Sent mailbox before taking any further action.",
                code="delivery_unknown",
                http_status=409,
                quote_finalized=locked_quote.status in {Quotation.STATUS_FINALIZED, Quotation.STATUS_SENT},
                delivery=locked_delivery,
            )
        if locked_delivery.status == QuotationEmailDelivery.STATUS_SENDING:
            raise QuotationEmailError(
                "This quotation email is already being sent.",
                code="delivery_in_progress",
                http_status=409,
                quote_finalized=locked_quote.status in {Quotation.STATUS_FINALIZED, Quotation.STATUS_SENT},
                delivery=locked_delivery,
            )

        # Validate every user-controlled recipient/header before changing the
        # quotation state. Provider/configuration failures happen later and
        # deliberately leave a valid quotation finalized for retry.
        to_addresses, cc_addresses, subject, body = _validate_editable_fields(
            locked_delivery,
            data,
        )
        if (
            not locked_delivery.gmail_connection
            or locked_delivery.gmail_connection.status
            != locked_delivery.gmail_connection.STATUS_CONNECTED
        ):
            raise QuotationEmailError(
                "Connect the shared Gmail mailbox before finalizing and sending.",
                code="gmail_reconnect_required",
                retryable=True,
                quote_finalized=locked_quote.status in {
                    Quotation.STATUS_FINALIZED,
                    Quotation.STATUS_SENT,
                },
                delivery=locked_delivery,
            )
        if not gmail_send_scope_granted(locked_delivery.gmail_connection):
            raise QuotationEmailError(
                "Reconnect the shared Gmail mailbox and approve Gmail send permission before sending.",
                code="gmail_reconnect_required",
                retryable=True,
                quote_finalized=locked_quote.status in {
                    Quotation.STATUS_FINALIZED,
                    Quotation.STATUS_SENT,
                },
                delivery=locked_delivery,
            )

        if finalize_first:
            if locked_quote.status in Quotation.EDITABLE_STATUSES:
                try:
                    locked_quote = finalize_quotation(locked_quote, actor)
                except ValidationError as exc:
                    messages = getattr(exc, "messages", None) or [str(exc)]
                    raise QuotationEmailError(
                        str(messages[0]),
                        code="quotation_finalization_failed",
                        quote_finalized=False,
                    ) from exc
            elif locked_quote.status not in {Quotation.STATUS_FINALIZED, Quotation.STATUS_SENT}:
                raise QuotationEmailError("This quotation cannot be finalized and emailed from its current status.")
        elif locked_quote.status not in {Quotation.STATUS_FINALIZED, Quotation.STATUS_SENT}:
            raise QuotationEmailError(
                "Finalize the quotation before sending or retrying its email.",
                code="quotation_not_finalized",
            )

        locked_delivery.to_addresses = to_addresses
        locked_delivery.cc_addresses = cc_addresses
        locked_delivery.subject = subject
        locked_delivery.body = body
        locked_delivery.actor = actor
        locked_delivery.status = QuotationEmailDelivery.STATUS_SENDING
        locked_delivery.attempt_count += 1
        locked_delivery.sending_started_at = timezone.now()
        locked_delivery.failed_at = None
        locked_delivery.last_error = ""
        locked_delivery.save(
            update_fields=[
                "to_addresses",
                "cc_addresses",
                "subject",
                "body",
                "actor",
                "status",
                "attempt_count",
                "sending_started_at",
                "failed_at",
                "last_error",
                "updated_at",
            ]
        )
        delivery_id = locked_delivery.id

    # The quote finalization and sending marker are committed before PDF
    # generation or any Gmail request. A provider failure can never roll the
    # quotation back to an editable state.
    gmail_request_started = False
    try:
        delivery = QuotationEmailDelivery.objects.select_related(
            "quotation__company", "quotation__contact", "gmail_connection"
        ).get(pk=delivery_id)
        if not delivery.gmail_connection:
            raise PermissionError("The shared Gmail mailbox is not connected.")
        if not gmail_send_scope_granted(delivery.gmail_connection):
            raise PermissionError(
                "Gmail send permission is missing. Reconnect the shared Gmail mailbox and approve sending."
            )
        pdf_bytes = build_quotation_pdf(delivery.quotation)
        pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
        with transaction.atomic():
            attachment_row = QuotationEmailDelivery.objects.select_for_update().get(pk=delivery_id)
            if attachment_row.status != QuotationEmailDelivery.STATUS_SENDING:
                raise QuotationEmailError("The delivery state changed before Gmail could be called.")
            if (
                attachment_row.attachment_sha256
                and attachment_row.attachment_sha256 != pdf_sha256
            ):
                raise QuotationEmailError(
                    "The regenerated quotation PDF differs from the attachment recorded for the previous attempt. Start a reviewed revision instead of retrying with changed content.",
                    code="attachment_snapshot_mismatch",
                    retryable=False,
                )
            if not attachment_row.attachment_sha256:
                attachment_row.attachment_sha256 = pdf_sha256
                attachment_row.attachment_size = len(pdf_bytes)
                attachment_row.save(
                    update_fields=["attachment_sha256", "attachment_size", "updated_at"]
                )
        raw_message = _build_raw_message(delivery, pdf_bytes)
        # Refresh/validate OAuth before entering the ambiguous delivery window.
        # Only errors after this point could have occurred after Gmail received
        # the messages.send request.
        access_token = prepared_access_token or get_valid_access_token(
            delivery.gmail_connection
        )
        gmail_request_started = True
        response = gmail_send_raw_message(
            delivery.gmail_connection,
            raw_message,
            thread_id=(
                delivery.gmail_thread_id
                if delivery.delivery_mode == QuotationEmailDelivery.MODE_GMAIL_REPLY
                else ""
            ),
            access_token=access_token,
        )
    except QuotationEmailError as exc:
        failed = _mark_delivery_failure(
            delivery_id,
            unknown=False,
            message=exc.message,
            actor=actor,
        )
        if failed.status == QuotationEmailDelivery.STATUS_SENT:
            return failed.quotation, failed, True
        raise QuotationEmailError(
            f"The quotation is finalized, but the email was not sent. {exc.message}",
            code=exc.code,
            retryable=exc.retryable,
            quote_finalized=True,
            delivery=failed,
        )
    except PermissionError as exc:
        failed = _mark_delivery_failure(
            delivery_id,
            unknown=False,
            message=str(exc),
            actor=actor,
        )
        if failed.status == QuotationEmailDelivery.STATUS_SENT:
            return failed.quotation, failed, True
        raise QuotationEmailError(
            f"The quotation is finalized, but it was not emailed. {str(exc)}",
            code="gmail_reconnect_required",
            retryable=True,
            quote_finalized=True,
            delivery=failed,
        ) from exc
    except RuntimeError as exc:
        if not gmail_request_started:
            failed = _mark_delivery_failure(
                delivery_id,
                unknown=False,
                message="The quotation PDF or email could not be prepared.",
                actor=actor,
            )
            if failed.status == QuotationEmailDelivery.STATUS_SENT:
                return failed.quotation, failed, True
            raise QuotationEmailError(
                "The quotation is finalized, but its PDF/email could not be prepared.",
                code="email_prepare_failed",
                retryable=True,
                quote_finalized=True,
                delivery=failed,
            ) from exc
        code_match = re.search(r"HTTP\s+(\d{3})", str(exc))
        http_code = int(code_match.group(1)) if code_match else 0
        if http_code in {408, 425, 429} or http_code >= 500:
            current = QuotationEmailDelivery.objects.select_related(
                "gmail_connection", "quotation"
            ).get(pk=delivery_id)
            reconciled = _reconcile_after_ambiguous_outcome(current, actor)
            if reconciled:
                reconciled_quote, reconciled_delivery = reconciled
                return reconciled_quote, reconciled_delivery, False
            unknown = _mark_delivery_failure(
                delivery_id,
                unknown=True,
                message=(
                    "Gmail returned a temporary/ambiguous response and did not confirm whether the message was accepted. "
                    "Check the shared Sent mailbox before retrying."
                ),
                actor=actor,
            )
            if unknown.status == QuotationEmailDelivery.STATUS_SENT:
                return unknown.quotation, unknown, True
            raise QuotationEmailError(
                unknown.last_error,
                code="delivery_unknown",
                http_status=409,
                retryable=False,
                quote_finalized=True,
                delivery=unknown,
            ) from exc
        safe = (
            f"Gmail rejected the send request (HTTP {http_code})."
            if code_match
            else "Gmail rejected the send request."
        )
        failed = _mark_delivery_failure(
            delivery_id,
            unknown=False,
            message=safe,
            actor=actor,
        )
        if failed.status == QuotationEmailDelivery.STATUS_SENT:
            return failed.quotation, failed, True
        raise QuotationEmailError(
            f"The quotation is finalized, but Gmail did not send it. {safe}",
            code="gmail_send_failed",
            retryable=True,
            quote_finalized=True,
            delivery=failed,
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        if gmail_request_started:
            current = QuotationEmailDelivery.objects.select_related(
                "gmail_connection", "quotation"
            ).get(pk=delivery_id)
            reconciled = _reconcile_after_ambiguous_outcome(current, actor)
            if reconciled:
                reconciled_quote, reconciled_delivery = reconciled
                return reconciled_quote, reconciled_delivery, False
        result = _mark_delivery_failure(
            delivery_id,
            unknown=gmail_request_started,
            message=(
                "Gmail did not confirm whether the message was accepted. "
                "Check the shared Sent mailbox using the quotation number before any retry."
                if gmail_request_started
                else "The quotation PDF or email could not be prepared."
            ),
            actor=actor,
        )
        if result.status == QuotationEmailDelivery.STATUS_SENT:
            return result.quotation, result, True
        raise QuotationEmailError(
            result.last_error,
            code="delivery_unknown" if gmail_request_started else "email_prepare_failed",
            http_status=409 if gmail_request_started else 400,
            retryable=not gmail_request_started,
            quote_finalized=True,
            delivery=result,
        ) from exc
    except Exception as exc:
        if gmail_request_started:
            current = QuotationEmailDelivery.objects.select_related(
                "gmail_connection", "quotation"
            ).get(pk=delivery_id)
            reconciled = _reconcile_after_ambiguous_outcome(current, actor)
            if reconciled:
                reconciled_quote, reconciled_delivery = reconciled
                return reconciled_quote, reconciled_delivery, False
        result = _mark_delivery_failure(
            delivery_id,
            unknown=gmail_request_started,
            message=(
                "An unexpected error occurred after delivery started. "
                "Check the shared Sent mailbox before retrying."
                if gmail_request_started
                else "The quotation PDF or email could not be prepared."
            ),
            actor=actor,
        )
        if result.status == QuotationEmailDelivery.STATUS_SENT:
            return result.quotation, result, True
        raise QuotationEmailError(
            result.last_error,
            code="delivery_unknown" if gmail_request_started else "email_prepare_failed",
            http_status=409 if gmail_request_started else 400,
            retryable=not gmail_request_started,
            quote_finalized=True,
            delivery=result,
        ) from exc

    gmail_message_id = str((response or {}).get("id") or "").strip()
    sent_thread_id = str((response or {}).get("threadId") or "").strip()
    if not gmail_message_id or not sent_thread_id or (
        delivery.delivery_mode == QuotationEmailDelivery.MODE_GMAIL_REPLY
        and sent_thread_id != delivery.gmail_thread_id
    ):
        current = QuotationEmailDelivery.objects.select_related(
            "gmail_connection", "quotation"
        ).get(pk=delivery_id)
        reconciled = _reconcile_after_ambiguous_outcome(current, actor)
        if reconciled:
            reconciled_quote, reconciled_delivery = reconciled
            return reconciled_quote, reconciled_delivery, False
        unknown = _mark_delivery_failure(
            delivery_id,
            unknown=True,
            message=(
                "Gmail returned an incomplete or mismatched delivery receipt. "
                "Check the shared Sent mailbox before retrying."
            ),
            actor=actor,
        )
        if unknown.status == QuotationEmailDelivery.STATUS_SENT:
            return unknown.quotation, unknown, True
        raise QuotationEmailError(
            unknown.last_error,
            code="delivery_unknown",
            http_status=409,
            retryable=False,
            quote_finalized=True,
            delivery=unknown,
        )

    sent_quote, sent_delivery = _record_successful_delivery(
        delivery_id,
        gmail_message_id,
        sent_thread_id,
        actor,
    )
    return sent_quote, sent_delivery, False
