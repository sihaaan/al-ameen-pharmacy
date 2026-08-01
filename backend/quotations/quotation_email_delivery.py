"""Safe preview and Gmail delivery for finalized quotations."""

import base64
import hashlib
import json
import re
import secrets
import urllib.error
from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formataddr, getaddresses, parseaddr

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import salted_hmac

from api.models import Brand, Product, ProductImage

from .contract_intelligence import (
    can_manage_shared_gmail,
    gmail_fetch_reply_metadata,
    gmail_search_messages,
    gmail_send_raw_message,
    gmail_send_scope_granted,
    get_valid_access_token,
    resolve_gmail_connection,
)
from .email_identity import (
    canonical_singleton_header_address,
    canonical_singleton_from_address,
    canonicalize_email_address,
)
from .models import (
    Company,
    CompanyContact,
    GmailInquiryImport,
    GmailOAuthConnection,
    QuoteItem,
    Quotation,
    QuotationAuditLog,
    QuotationEmailDelivery,
    QuotationEmailDeliveryAttempt,
    QuotationEmailDeliveryAttemptEvent,
    QuotationEmailOutboundSnapshot,
    QuotationEmailThreadSelection,
    QuotationLine,
    QuotationSettings,
    UserQuotationProfile,
)
from .pdf import build_quotation_pdf
from .pdf_config import get_quotation_pdf_config
from .services import (
    audit_log,
    finalize_quotation,
    quotation_brand_name_for_selection,
)


THREAD_SELECTION_MAX_AGE = 30 * 60
MAX_TO_ADDRESSES = 10
MAX_CC_ADDRESSES = 10
EMAIL_PREVIEW_FINGERPRINT_CONTRACT = "quotation_email_preview_v1"
EMAIL_PREVIEW_FINGERPRINT_SALT = "quotations.email-preview-fingerprint.v1"
QUOTATION_REVIEW_FINGERPRINT_CONTRACT = "quotation_editor_review_v1"
QUOTATION_REVIEW_FINGERPRINT_SALT = "quotations.editor-review-fingerprint.v1"
GMAIL_REPLY_SENDER_VALIDATION_CONTRACT = "gmail_reply_sender_identity_v1"
MAX_OUTBOUND_MIME_BYTES = 35 * 1024 * 1024


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


def _image_identity(image):
    if not image:
        return None
    image_field = getattr(image, "image", None)
    return {
        "id": getattr(image, "pk", None),
        "name": str(getattr(image_field, "name", "") or ""),
        "updated_at": getattr(image, "updated_at", None),
    }


def _file_field_name(instance, field_name):
    field = getattr(instance, field_name, None) if instance else None
    return str(getattr(field, "name", "") or "")


def _preview_source_identity(delivery):
    trusted_source = delivery.trusted_source or {}
    return {
        "delivery_mode": delivery.delivery_mode,
        "gmail_connection_id": delivery.gmail_connection_id,
        "gmail_connection_email": str(
            getattr(delivery.gmail_connection, "email", "") or ""
        ).strip().lower(),
        "gmail_inquiry_import_id": delivery.gmail_inquiry_import_id,
        "gmail_thread_id": str(delivery.gmail_thread_id or ""),
        "source_gmail_message_id": str(delivery.source_gmail_message_id or ""),
        "source_rfc_message_id": str(delivery.source_rfc_message_id or ""),
        "source_references": str(delivery.source_references or ""),
        "verified_recipient": str(
            trusted_source.get("reply_to_email")
            or trusted_source.get("sender_email")
            or ""
        ).strip().lower(),
        "verified_subject": str(trusted_source.get("subject") or ""),
        "verified_sender": str(trusted_source.get("sender_email") or "")
        .strip()
        .lower(),
        "scope_email": str(trusted_source.get("scope_email") or "")
        .strip()
        .lower(),
        "sender_validation_contract": str(
            trusted_source.get("sender_validation_contract") or ""
        ),
    }


def _delivery_snapshot(delivery, *, include_raw=False, for_update=False):
    """Fetch snapshot metadata without pulling the potentially large MIME blob."""

    delivery_id = getattr(delivery, "pk", None)
    if not delivery_id:
        return None
    queryset = QuotationEmailOutboundSnapshot.objects.filter(delivery_id=delivery_id)
    if not include_raw:
        queryset = queryset.defer("raw_mime")
    if for_update:
        queryset = queryset.select_for_update()
    return queryset.first()


def _quotation_customer_state(quotation, *, pdf_config=None, project_for_send):
    """Return canonical customer-facing state without exposing it to clients.

    ``project_for_send`` represents the deterministic changes made by
    finalization (status, effective brand snapshots, and aggregate totals).
    This lets one reviewed token remain valid across the draft -> finalized
    transition while still changing for every independently editable value.
    """

    quotation = Quotation.objects.select_related(
        "company",
        "contact",
        "created_by",
    ).get(pk=quotation.pk)
    company = quotation.company
    contact = quotation.contact
    created_by = quotation.created_by
    try:
        creator_profile = getattr(created_by, "quotation_profile", None)
    except Exception:
        creator_profile = None

    lines = list(
        quotation.lines.select_related(
            "product__brand",
            "product_image",
            "quote_item__product__brand",
        )
        .prefetch_related("product__images")
        .order_by("sort_order", "id")
    )
    line_state = []
    projected_subtotal = Decimal("0.00")
    projected_vat_total = Decimal("0.00")
    projected_total = Decimal("0.00")
    for line in lines:
        selected_image = None
        if line.include_product_image:
            selected_image = line.product_image or (
                line.product.primary_image if line.product_id else None
            )
        effective_brand = (
            line.brand_name_snapshot
            or quotation_brand_name_for_selection(
                product=line.product,
                quote_item=line.quote_item,
            )
        )
        if line.match_status != QuotationLine.MATCH_IGNORED:
            projected_subtotal += line.line_subtotal or Decimal("0.00")
            projected_vat_total += line.vat_amount or Decimal("0.00")
            projected_total += line.line_total or Decimal("0.00")
        line_state.append(
            {
                "id": line.id,
                "sort_order": line.sort_order,
                "match_status": line.match_status,
                "product_id": line.product_id,
                "quote_item_id": line.quote_item_id,
                "product_image_id": line.product_image_id,
                "include_product_image": line.include_product_image,
                "selected_image": _image_identity(selected_image),
                "item_name_snapshot": line.item_name_snapshot,
                "brand_name_snapshot": (
                    effective_brand if project_for_send else line.brand_name_snapshot
                ),
                "effective_finalized_brand": effective_brand,
                "description": line.description,
                "quantity": line.quantity,
                "unit": line.unit,
                "unit_price": line.unit_price,
                "vat_rate": line.vat_rate,
                "line_subtotal": line.line_subtotal,
                "vat_amount": line.vat_amount,
                "line_total": line.line_total,
            }
        )

    resolved_pdf_config = pdf_config or get_quotation_pdf_config(quotation=quotation)
    pdf_settings = QuotationSettings.objects.filter(pk=1).first()
    rendered_status = quotation.status
    if project_for_send and quotation.status in {
        *Quotation.EDITABLE_STATUSES,
        Quotation.STATUS_FINALIZED,
    }:
        rendered_status = Quotation.STATUS_FINALIZED
    return {
        "quotation": {
            "id": quotation.id,
            "quotation_number": quotation.quotation_number,
            "status": rendered_status,
            "version": quotation.version,
            "company_id": quotation.company_id,
            "contact_id": quotation.contact_id,
            "created_by_id": quotation.created_by_id,
            "created_by_username": str(getattr(created_by, "username", "") or ""),
            "created_at": quotation.created_at,
            "valid_until": quotation.valid_until,
            "currency": quotation.currency,
            "payment_terms": quotation.payment_terms,
            "show_brand_column": quotation.show_brand_column,
            "subtotal": projected_subtotal if project_for_send else quotation.subtotal,
            "vat_total": projected_vat_total if project_for_send else quotation.vat_total,
            "total": projected_total if project_for_send else quotation.total,
            "notes": quotation.notes,
        },
        "company": {
            "name": company.name,
            "email": company.email,
            "billing_address": company.billing_address,
            "trn": company.trn,
        },
        "contact": (
            {
                "name": contact.name,
                "email": contact.email,
                "phone": contact.phone,
            }
            if contact
            else None
        ),
        "creator_profile": {
            "updated_at": getattr(creator_profile, "updated_at", None),
            "signature_name": str(
                getattr(getattr(creator_profile, "signature_image", None), "name", "")
                or ""
            ),
        },
        "pdf_config": asdict(resolved_pdf_config),
        "pdf_settings_identity": (
            {
                "updated_at": pdf_settings.updated_at,
                "logo_name": _file_field_name(pdf_settings, "logo"),
                "signature_name": _file_field_name(
                    pdf_settings,
                    "signature_image",
                ),
                "stamp_name": _file_field_name(pdf_settings, "stamp_image"),
            }
            if pdf_settings
            else None
        ),
        "lines": line_state,
    }


def _quotation_preview_state(quotation, delivery, actor, *, pdf_config=None):
    snapshot = _delivery_snapshot(delivery) if getattr(delivery, "pk", None) else None
    return {
        "contract": EMAIL_PREVIEW_FINGERPRINT_CONTRACT,
        "actor_id": getattr(actor, "pk", None),
        "customer": _quotation_customer_state(
            quotation,
            pdf_config=pdf_config,
            project_for_send=True,
        ),
        "attachment": {
            "filename": str(delivery.attachment_filename or ""),
            "sha256": str(delivery.attachment_sha256 or ""),
            "size": delivery.attachment_size,
            "outbound_snapshot_sha256": (
                str(snapshot.snapshot_sha256) if snapshot else ""
            ),
        },
        "source": _preview_source_identity(delivery),
    }


def _state_fingerprint(state, *, salt):
    canonical = json.dumps(
        state,
        cls=DjangoJSONEncoder,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return salted_hmac(
        salt,
        canonical,
        algorithm="sha256",
    ).hexdigest()


def quotation_review_fingerprint(quotation):
    return _state_fingerprint(
        {
            "contract": QUOTATION_REVIEW_FINGERPRINT_CONTRACT,
            "customer": _quotation_customer_state(
                quotation,
                project_for_send=False,
            ),
        },
        salt=QUOTATION_REVIEW_FINGERPRINT_SALT,
    )


def quotation_email_preview_fingerprint(
    quotation,
    delivery,
    actor,
    *,
    pdf_config=None,
):
    return _state_fingerprint(
        _quotation_preview_state(
            quotation,
            delivery,
            actor,
            pdf_config=pdf_config,
        ),
        salt=EMAIL_PREVIEW_FINGERPRINT_SALT,
    )


def require_current_quotation_review(quotation, fingerprint):
    supplied = str(fingerprint or "").strip()
    if not supplied:
        raise QuotationEmailError(
            "Reload and review the quotation before opening its email preview.",
            code="quotation_review_required",
            http_status=400,
            retryable=False,
            quote_finalized=quotation.status in {
                Quotation.STATUS_FINALIZED,
                Quotation.STATUS_SENT,
            },
        )
    expected = quotation_review_fingerprint(quotation)
    if not secrets.compare_digest(supplied, expected):
        raise QuotationEmailError(
            "The quotation changed in another session. The editor has been refreshed; review the latest quotation before opening its email preview.",
            code="stale_quotation_review",
            http_status=409,
            retryable=False,
            quote_finalized=quotation.status in {
                Quotation.STATUS_FINALIZED,
                Quotation.STATUS_SENT,
            },
        )


def _require_current_email_preview(
    quotation,
    delivery,
    actor,
    fingerprint,
    *,
    pdf_config=None,
):
    supplied = str(fingerprint or "").strip()
    if not supplied:
        raise QuotationEmailError(
            "Open and review the latest email preview before sending this quotation.",
            code="email_preview_required",
            http_status=400,
            retryable=False,
            quote_finalized=quotation.status in {
                Quotation.STATUS_FINALIZED,
                Quotation.STATUS_SENT,
            },
            delivery=delivery if getattr(delivery, "pk", None) else None,
        )
    expected = quotation_email_preview_fingerprint(
        quotation,
        delivery,
        actor,
        pdf_config=pdf_config,
    )
    if not secrets.compare_digest(supplied, expected):
        raise QuotationEmailError(
            "The quotation or verified reply source changed after this preview. Reload and review the latest preview before sending.",
            code="stale_email_preview",
            http_status=409,
            retryable=False,
            quote_finalized=quotation.status in {
                Quotation.STATUS_FINALIZED,
                Quotation.STATUS_SENT,
            },
            delivery=delivery if getattr(delivery, "pk", None) else None,
        )


def _lock_email_render_dependencies(quotation, delivery=None):
    """Lock every database row that can affect the reviewed PDF or MIME source.

    The caller must already hold the quotation and delivery locks. Keeping the
    shared quote -> delivery -> line order makes concurrent quotation writes
    serialize, while these additional locks close changes to related customer,
    branding, catalogue, image, creator, and Gmail identity rows until the
    exact outbound MIME bytes have been prepared.
    """

    line_refs = list(
        QuotationLine.objects.select_for_update(of=("self",))
        .filter(quotation_id=quotation.pk)
        .order_by("pk")
        .values("product_id", "quote_item_id", "product_image_id")
    )
    list(
        Company.objects.select_for_update()
        .filter(pk=quotation.company_id)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    if quotation.contact_id:
        list(
            CompanyContact.objects.select_for_update()
            .filter(pk=quotation.contact_id)
            .order_by("pk")
            .values_list("pk", flat=True)
        )

    creator_model = Quotation._meta.get_field("created_by").remote_field.model
    if quotation.created_by_id:
        list(
            creator_model.objects.select_for_update()
            .filter(pk=quotation.created_by_id)
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        list(
            UserQuotationProfile.objects.select_for_update()
            .filter(user_id=quotation.created_by_id)
            .order_by("pk")
            .values_list("pk", flat=True)
        )

    # The singleton may legitimately be absent in an older/local database.
    # In that case the resolved config is frozen and passed to the PDF builder
    # below, so a concurrent first-time settings insert cannot alter the bytes.
    list(
        QuotationSettings.objects.select_for_update()
        .filter(pk=1)
        .order_by("pk")
        .values_list("pk", flat=True)
    )

    quote_item_ids = {
        row["quote_item_id"] for row in line_refs if row["quote_item_id"]
    }
    quote_item_rows = list(
        QuoteItem.objects.select_for_update()
        .filter(pk__in=quote_item_ids)
        .order_by("pk")
        .values("product_id")
    )
    product_ids = {
        row["product_id"] for row in line_refs if row["product_id"]
    }
    product_ids.update(
        row["product_id"] for row in quote_item_rows if row["product_id"]
    )
    product_rows = list(
        Product.objects.select_for_update()
        .filter(pk__in=product_ids)
        .order_by("pk")
        .values("brand_id")
    )
    brand_ids = {row["brand_id"] for row in product_rows if row["brand_id"]}
    list(
        Brand.objects.select_for_update()
        .filter(pk__in=brand_ids)
        .order_by("pk")
        .values_list("pk", flat=True)
    )

    explicit_image_ids = {
        row["product_image_id"]
        for row in line_refs
        if row["product_image_id"]
    }
    list(
        ProductImage.objects.select_for_update()
        .filter(Q(pk__in=explicit_image_ids) | Q(product_id__in=product_ids))
        .order_by("pk")
        .values_list("pk", flat=True)
    )

    locked_connection = None
    if delivery is not None and delivery.gmail_connection_id:
        locked_connection = GmailOAuthConnection.objects.select_for_update().get(
            pk=delivery.gmail_connection_id
        )
        delivery.gmail_connection = locked_connection
    return locked_connection


def lock_quotation_review_dependencies(quotation):
    """Lock rows used by the customer-facing quotation review representation."""

    _lock_email_render_dependencies(quotation)


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


def _physical_from_address(metadata):
    header_values = (
        metadata.get("from_header_values")
        if "from_header_values" in metadata
        else None
    )
    address = canonical_singleton_from_address(
        metadata.get("sender"),
        from_header_values=header_values,
    )
    if not address:
        raise QuotationEmailError(
            "The source email has an ambiguous From address. Choose another source message.",
            code="ambiguous_source_recipient",
        )
    return address


def _reply_recipient(metadata, *, physical_sender=""):
    reply_to_values = (
        metadata.get("reply_to_header_values")
        if "reply_to_header_values" in metadata
        else None
    )
    if reply_to_values is not None:
        reply_to_values = list(reply_to_values or [])
        reply_to = str(reply_to_values[0] if reply_to_values else "").strip()
    else:
        reply_to = str(metadata.get("reply_to") or "").strip()
    if reply_to:
        reply_to_address = canonical_singleton_header_address(
            reply_to,
            header_values=reply_to_values,
        )
        if not reply_to_address:
            raise QuotationEmailError(
                "The source email has an ambiguous Reply-To address. Choose another source message.",
                code="ambiguous_source_recipient",
            )
        return reply_to_address
    if physical_sender:
        return physical_sender
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
    # Preview is a read operation. Do not create the singleton settings row
    # here: doing so changes the quotation review fingerprint between the
    # editor GET and the preview response. Resolve the same fallback config
    # that the PDF renderer uses when settings have not been saved yet.
    sender_name = str(
        get_quotation_pdf_config(quotation=quotation).company_name
        or "Al Ameen Pharmacy LLC"
    ).strip()
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
    physical_sender = _physical_from_address(metadata)
    canonical_scope_email = canonicalize_email_address(scope_email)
    if scope_email and canonical_scope_email != physical_sender:
        raise QuotationEmailError(
            "The selected Gmail message is not from the confirmed email address.",
            code="gmail_source_sender_mismatch",
        )
    recipient = _reply_recipient(
        metadata,
        physical_sender=physical_sender,
    )
    mailbox_email = canonicalize_email_address(
        getattr(connection, "email", "")
    )
    if canonicalize_email_address(recipient) == mailbox_email:
        raise QuotationEmailError(
            "The reply recipient resolves to the shared mailbox itself.",
            code="gmail_self_recipient",
        )
    sender_name, _sender_email = parseaddr(str(metadata.get("sender") or ""))
    received_at = metadata.get("sent_at")
    if hasattr(received_at, "isoformat"):
        received_at = received_at.isoformat()
    return {
        "metadata": metadata,
        "recipient": recipient,
        "subject": subject,
        "trusted_source": {
            "sender_validation_contract": GMAIL_REPLY_SENDER_VALIDATION_CONTRACT,
            "sender_name": sender_name,
            "sender_email": physical_sender,
            "reply_to_email": recipient,
            "subject": subject,
            "received_at": received_at,
            "scope_email": canonical_scope_email,
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


def _apply_outbound_snapshot(delivery, snapshot):
    delivery.delivery_mode = snapshot.delivery_mode
    delivery.to_addresses = list(snapshot.to_addresses or [])
    delivery.cc_addresses = list(snapshot.cc_addresses or [])
    delivery.subject = snapshot.subject
    delivery.body = snapshot.body
    delivery.gmail_thread_id = snapshot.gmail_api_thread_id
    delivery.source_gmail_message_id = snapshot.source_gmail_message_id
    delivery.source_rfc_message_id = snapshot.source_rfc_message_id
    delivery.source_references = snapshot.source_references
    delivery.outbound_rfc_message_id = snapshot.outbound_rfc_message_id
    delivery.attachment_filename = snapshot.attachment_filename
    delivery.attachment_sha256 = snapshot.attachment_sha256
    delivery.attachment_size = snapshot.attachment_size
    return delivery


def _require_current_gmail_reply_sender_contract(delivery):
    if delivery.delivery_mode != QuotationEmailDelivery.MODE_GMAIL_REPLY:
        return
    contract = str(
        (delivery.trusted_source or {}).get("sender_validation_contract") or ""
    )
    if contract != GMAIL_REPLY_SENDER_VALIDATION_CONTRACT:
        raise QuotationEmailError(
            "This saved Gmail reply predates the current sender verification rules and cannot be retried safely. Create and review a quotation revision before sending.",
            code="gmail_reply_source_reverification_required",
            http_status=409,
            retryable=False,
            quote_finalized=delivery.quotation.status
            in {Quotation.STATUS_FINALIZED, Quotation.STATUS_SENT},
            delivery=delivery,
        )


def prepare_email_preview(
    quotation,
    actor,
    *,
    thread_selection_token="",
    persist=False,
    expected_preview_fingerprint="",
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
    existing_snapshot = _delivery_snapshot(existing) if existing else None
    if (
        existing
        and existing_snapshot
        and existing.status == QuotationEmailDelivery.STATUS_FAILED
    ):
        # A known-safe retry is a review of the exact persisted request. Do not
        # refetch a newer source message or rewrite any customer-facing field.
        existing.actor = actor
        _apply_outbound_snapshot(existing, existing_snapshot)
        _require_current_gmail_reply_sender_contract(existing)
        if expected_preview_fingerprint:
            _require_current_email_preview(
                quotation,
                existing,
                actor,
                expected_preview_fingerprint,
            )
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
        delivery = (
            QuotationEmailDelivery.objects.select_for_update(of=("self",))
            .filter(quotation=locked_quote)
            .first()
        )
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
        if expected_preview_fingerprint:
            _require_current_email_preview(
                locked_quote,
                delivery,
                actor,
                expected_preview_fingerprint,
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


def delivery_preview_payload(delivery, actor=None, *, preview_fingerprint=None):
    snapshot = _delivery_snapshot(delivery) if getattr(delivery, "pk", None) else None
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
    if delivery.status == QuotationEmailDelivery.STATUS_UNKNOWN:
        warnings.append(
            "Gmail did not confirm the previous attempt. Do not resend until the shared mailbox is checked."
        )
    if snapshot and delivery.status == QuotationEmailDelivery.STATUS_FAILED:
        warnings.append(
            "This is an exact frozen retry. Recipient, CC, subject, message, thread headers, and PDF cannot be changed; create a quotation revision for different content."
        )
    to_addresses = list(snapshot.to_addresses or []) if snapshot else list(delivery.to_addresses or [])
    cc_addresses = list(snapshot.cc_addresses or []) if snapshot else list(delivery.cc_addresses or [])
    subject = snapshot.subject if snapshot else delivery.subject
    body = snapshot.body if snapshot else delivery.body
    attachment_filename = snapshot.attachment_filename if snapshot else delivery.attachment_filename
    attachment_sha256 = snapshot.attachment_sha256 if snapshot else delivery.attachment_sha256
    attachment_size = snapshot.attachment_size if snapshot else delivery.attachment_size
    delivery_mode = snapshot.delivery_mode if snapshot else delivery.delivery_mode
    if not to_addresses:
        warnings.append("Enter and confirm the recipient email address before sending.")
    payload = {
        "delivery_id": delivery.id,
        "delivery_mode": delivery_mode,
        "can_reply_to_thread": delivery_mode == QuotationEmailDelivery.MODE_GMAIL_REPLY,
        "trusted_source": delivery.trusted_source or {},
        "thread": {
            "linked": delivery_mode == QuotationEmailDelivery.MODE_GMAIL_REPLY,
            "subject": subject if delivery_mode == QuotationEmailDelivery.MODE_GMAIL_REPLY else "",
        },
        "to": to_addresses,
        "cc": cc_addresses,
        "subject": subject,
        "body": body,
        "attachment_filename": attachment_filename,
        "attachment_sha256": attachment_sha256,
        "attachment_size": attachment_size,
        "outbound_snapshot_frozen": bool(snapshot),
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
    if actor is not None:
        payload["preview_fingerprint"] = (
            preview_fingerprint
            or quotation_email_preview_fingerprint(
                delivery.quotation,
                delivery,
                actor,
            )
        )
    return payload


def reviewed_delivery_preview_payload(
    quotation,
    delivery,
    actor,
    *,
    quotation_review_fingerprint_value="",
):
    """Bind a preview response to the quotation revision displayed by staff.

    The parameter remains optional for compatibility with older internal API
    clients. The shipped editor always supplies it. When present, the final
    comparison and email-token construction happen under the same dependency
    locks, so a change during Gmail source discovery cannot mint a sendable
    token for unseen quotation state.
    """

    supplied = str(quotation_review_fingerprint_value or "").strip()
    if not supplied:
        return delivery_preview_payload(delivery, actor)

    with transaction.atomic():
        locked_quote = Quotation.objects.select_for_update().get(pk=quotation.pk)
        if getattr(delivery, "pk", None):
            delivery = QuotationEmailDelivery.objects.select_for_update(
                of=("self",)
            ).select_related("quotation", "gmail_connection").get(
                pk=delivery.pk
            )
            QuotationEmailOutboundSnapshot.objects.select_for_update().only("id").filter(
                delivery_id=delivery.pk
            ).first()
        _lock_email_render_dependencies(locked_quote, delivery)
        pdf_config = get_quotation_pdf_config(quotation=locked_quote)
        require_current_quotation_review(locked_quote, supplied)
        email_fingerprint = quotation_email_preview_fingerprint(
            locked_quote,
            delivery,
            actor,
            pdf_config=pdf_config,
        )
        return delivery_preview_payload(
            delivery,
            actor,
            preview_fingerprint=email_fingerprint,
        )


def delivery_payload(delivery):
    snapshot = _delivery_snapshot(delivery) if getattr(delivery, "pk", None) else None
    delivery_mode = snapshot.delivery_mode if snapshot else delivery.delivery_mode
    return {
        "id": delivery.id,
        "quotation": delivery.quotation_id,
        "delivery_mode": delivery_mode,
        "status": delivery.status,
        "outbound_snapshot_frozen": bool(snapshot),
        "can_reconcile": delivery.status in {
            QuotationEmailDelivery.STATUS_SENDING,
            QuotationEmailDelivery.STATUS_UNKNOWN,
        },
        "to": list(snapshot.to_addresses or []) if snapshot else list(delivery.to_addresses or []),
        "cc": list(snapshot.cc_addresses or []) if snapshot else list(delivery.cc_addresses or []),
        "subject": snapshot.subject if snapshot else delivery.subject,
        "body": snapshot.body if snapshot else delivery.body,
        "trusted_source": delivery.trusted_source or {},
        "attachment_filename": snapshot.attachment_filename if snapshot else delivery.attachment_filename,
        "attachment_sha256": snapshot.attachment_sha256 if snapshot else delivery.attachment_sha256,
        "attachment_size": snapshot.attachment_size if snapshot else delivery.attachment_size,
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
        _require_current_gmail_reply_sender_contract(delivery)
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


def _build_raw_message(delivery, pdf_bytes, *, sender_name=None):
    connection = delivery.gmail_connection
    from_email = str(getattr(connection, "email", "") or "").strip().lower()
    validate_email(from_email)
    sender_name = str(
        sender_name
        or QuotationSettings.get_solo().company_name
        or "Al Ameen Pharmacy LLC"
    ).strip()
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


def _decode_gmail_raw_message(raw_message):
    value = str(raw_message or "")
    padded = value + ("=" * (-len(value) % 4))
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except Exception as exc:
        raise QuotationEmailError(
            "The prepared email bytes could not be frozen safely.",
            code="outbound_snapshot_invalid",
            retryable=False,
        ) from exc


def _encode_gmail_raw_message(raw_mime):
    return base64.urlsafe_b64encode(bytes(raw_mime)).decode("ascii").rstrip("=")


def _snapshot_metadata(snapshot):
    return {
        "contract_version": str(snapshot.contract_version or ""),
        "delivery_id": snapshot.delivery_id,
        "gmail_connection_id_snapshot": snapshot.gmail_connection_id_snapshot,
        "mailbox_email": str(snapshot.mailbox_email or "").strip().lower(),
        "sender_name": str(snapshot.sender_name or ""),
        "delivery_mode": str(snapshot.delivery_mode or ""),
        "to_addresses": list(snapshot.to_addresses or []),
        "cc_addresses": list(snapshot.cc_addresses or []),
        "subject": str(snapshot.subject or ""),
        "body": str(snapshot.body or ""),
        "gmail_api_thread_id": str(snapshot.gmail_api_thread_id or ""),
        "source_gmail_message_id": str(snapshot.source_gmail_message_id or ""),
        "source_rfc_message_id": str(snapshot.source_rfc_message_id or ""),
        "source_references": str(snapshot.source_references or ""),
        "outbound_rfc_message_id": str(snapshot.outbound_rfc_message_id or ""),
        "attachment_filename": str(snapshot.attachment_filename or ""),
        "attachment_sha256": str(snapshot.attachment_sha256 or ""),
        "attachment_size": snapshot.attachment_size,
        "raw_mime_sha256": str(snapshot.raw_mime_sha256 or ""),
        "raw_mime_size": snapshot.raw_mime_size,
    }


def _outbound_snapshot_digest(metadata, raw_mime):
    canonical = json.dumps(
        metadata,
        cls=DjangoJSONEncoder,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical + b"\0" + bytes(raw_mime)).hexdigest()


def _create_outbound_snapshot(delivery, actor, *, sender_name, pdf_bytes, raw_message):
    raw_mime = _decode_gmail_raw_message(raw_message)
    if not raw_mime or len(raw_mime) > MAX_OUTBOUND_MIME_BYTES:
        raise QuotationEmailError(
            "The prepared quotation email is too large to freeze and send safely.",
            code="outbound_snapshot_too_large",
            retryable=False,
        )
    mailbox_email = str(getattr(delivery.gmail_connection, "email", "") or "").strip().lower()
    validate_email(mailbox_email)
    raw_mime_sha256 = hashlib.sha256(raw_mime).hexdigest()
    snapshot = QuotationEmailOutboundSnapshot(
        delivery=delivery,
        created_by=actor,
        created_by_username=str(getattr(actor, "get_username", lambda: "")() or "")[:150],
        gmail_connection_id_snapshot=delivery.gmail_connection_id,
        mailbox_email=mailbox_email,
        sender_name=str(sender_name or "")[:255],
        delivery_mode=delivery.delivery_mode,
        to_addresses=list(delivery.to_addresses or []),
        cc_addresses=list(delivery.cc_addresses or []),
        subject=delivery.subject,
        body=delivery.body,
        gmail_api_thread_id=(
            delivery.gmail_thread_id
            if delivery.delivery_mode == QuotationEmailDelivery.MODE_GMAIL_REPLY
            else ""
        ),
        source_gmail_message_id=delivery.source_gmail_message_id,
        source_rfc_message_id=delivery.source_rfc_message_id,
        source_references=delivery.source_references,
        outbound_rfc_message_id=delivery.outbound_rfc_message_id,
        attachment_filename=delivery.attachment_filename,
        attachment_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
        attachment_size=len(pdf_bytes),
        raw_mime=raw_mime,
        raw_mime_sha256=raw_mime_sha256,
        raw_mime_size=len(raw_mime),
        snapshot_sha256="",
    )
    snapshot.snapshot_sha256 = _outbound_snapshot_digest(
        _snapshot_metadata(snapshot),
        raw_mime,
    )
    snapshot.save(force_insert=True)
    return snapshot


def _verify_outbound_snapshot(snapshot):
    raw_mime = bytes(snapshot.raw_mime or b"")
    raw_digest = hashlib.sha256(raw_mime).hexdigest()
    expected_snapshot_digest = _outbound_snapshot_digest(
        _snapshot_metadata(snapshot),
        raw_mime,
    )
    valid = (
        snapshot.contract_version == QuotationEmailOutboundSnapshot.CONTRACT_VERSION
        and 0 < len(raw_mime) <= MAX_OUTBOUND_MIME_BYTES
        and snapshot.raw_mime_size == len(raw_mime)
        and secrets.compare_digest(str(snapshot.raw_mime_sha256 or ""), raw_digest)
        and secrets.compare_digest(
            str(snapshot.snapshot_sha256 or ""),
            expected_snapshot_digest,
        )
    )
    if not valid:
        raise QuotationEmailError(
            "The frozen quotation email failed its integrity check. It was not sent.",
            code="outbound_snapshot_corrupt",
            retryable=False,
        )
    return raw_mime


def _require_snapshot_matches_delivery(delivery, snapshot):
    connection_email = str(
        getattr(delivery.gmail_connection, "email", "") or ""
    ).strip().lower()
    current = {
        "delivery_mode": delivery.delivery_mode,
        "to_addresses": list(delivery.to_addresses or []),
        "cc_addresses": list(delivery.cc_addresses or []),
        "subject": str(delivery.subject or ""),
        "body": str(delivery.body or ""),
        "gmail_api_thread_id": (
            str(delivery.gmail_thread_id or "")
            if delivery.delivery_mode == QuotationEmailDelivery.MODE_GMAIL_REPLY
            else ""
        ),
        "source_gmail_message_id": str(delivery.source_gmail_message_id or ""),
        "source_rfc_message_id": str(delivery.source_rfc_message_id or ""),
        "source_references": str(delivery.source_references or ""),
        "outbound_rfc_message_id": str(delivery.outbound_rfc_message_id or ""),
        "attachment_filename": str(delivery.attachment_filename or ""),
        "attachment_sha256": str(delivery.attachment_sha256 or ""),
        "attachment_size": delivery.attachment_size,
        "mailbox_email": connection_email,
    }
    frozen = {
        key: value
        for key, value in _snapshot_metadata(snapshot).items()
        if key in current
    }
    if current != frozen:
        raise QuotationEmailError(
            "The saved delivery no longer matches its frozen outbound email. Create a reviewed quotation revision instead of changing a retry.",
            code="outbound_snapshot_mismatch",
            http_status=409,
            retryable=False,
        )


def _require_payload_matches_snapshot(snapshot, *, to_addresses, cc_addresses, subject, body):
    if (
        list(to_addresses) != list(snapshot.to_addresses or [])
        or list(cc_addresses) != list(snapshot.cc_addresses or [])
        or str(subject) != str(snapshot.subject)
        or str(body) != str(snapshot.body)
    ):
        raise QuotationEmailError(
            "A retry must use the exact recipient, CC, subject, and message that were previously reviewed. Create a quotation revision to change them.",
            code="outbound_snapshot_mismatch",
            http_status=409,
            retryable=False,
        )


def _create_provider_attempt(delivery, snapshot, actor):
    return QuotationEmailDeliveryAttempt.objects.create(
        delivery=delivery,
        snapshot=snapshot,
        sequence=delivery.attempt_count,
        actor=actor,
        actor_username=str(getattr(actor, "get_username", lambda: "")() or "")[:150],
        gmail_connection_id_snapshot=delivery.gmail_connection_id,
        mailbox_email=snapshot.mailbox_email,
        raw_mime_sha256=snapshot.raw_mime_sha256,
        expected_thread_id=snapshot.gmail_api_thread_id,
    )


def _gmail_send_credential_generation(connection):
    """Return an in-memory identity for the exact credential used to send.

    The value is never persisted or logged. Comparing it again under the
    connection row lock prevents a concurrent OAuth replacement from pairing
    frozen mailbox attribution with an access token obtained from a different
    credential generation.
    """

    if not connection:
        return None
    return (
        connection.pk,
        connection.user_id,
        bool(connection.is_shared),
        str(connection.status or ""),
        str(connection.email or "").strip().lower(),
        tuple(sorted(str(scope or "") for scope in (connection.scopes or []))),
        str(connection.access_token_encrypted or ""),
        connection.token_expiry,
    )


def _latest_provider_attempt(delivery):
    return delivery.provider_attempts.order_by("-sequence", "-id").first()


def _append_provider_attempt_event(
    attempt,
    *,
    event_type,
    actor,
    event_key="",
    provider_message_id="",
    provider_thread_id="",
    provider_http_status=None,
    error_category="",
    error_code="",
    error_class="",
    error_summary="",
):
    if not attempt:
        return None
    attempt = QuotationEmailDeliveryAttempt.objects.select_for_update().get(pk=attempt.pk)
    event_key = str(event_key or event_type)
    existing = attempt.events.filter(event_key=event_key).first()
    if existing:
        return existing
    return QuotationEmailDeliveryAttemptEvent.objects.create(
        attempt=attempt,
        event_key=event_key,
        event_type=event_type,
        actor=actor,
        actor_username=str(getattr(actor, "get_username", lambda: "")() or "")[:150],
        provider_message_id=str(provider_message_id or "")[:255],
        provider_thread_id=str(provider_thread_id or "")[:255],
        provider_http_status=provider_http_status,
        error_category=str(error_category or "")[:50],
        error_code=str(error_code or "")[:100],
        error_class=str(error_class or "")[:255],
        error_summary=str(error_summary or "")[:1000],
    )


def _mark_delivery_failure(
    delivery_id,
    *,
    unknown,
    message,
    actor,
    attempt_id=None,
    error_code="",
    error_class="",
    provider_http_status=None,
):
    now = timezone.now()
    quotation_id = QuotationEmailDelivery.objects.only("quotation_id").get(
        pk=delivery_id
    ).quotation_id
    with transaction.atomic():
        quotation = Quotation.objects.select_for_update().select_related("company").get(
            pk=quotation_id
        )
        delivery = QuotationEmailDelivery.objects.select_for_update().get(pk=delivery_id)
        attempt = None
        if attempt_id:
            attempt = QuotationEmailDeliveryAttempt.objects.filter(
                pk=attempt_id,
                delivery=delivery,
            ).first()
        elif unknown:
            attempt = _latest_provider_attempt(delivery)
        _append_provider_attempt_event(
            attempt,
            event_type=(
                QuotationEmailDeliveryAttemptEvent.EVENT_PROVIDER_UNKNOWN
                if unknown
                else QuotationEmailDeliveryAttemptEvent.EVENT_PROVIDER_FAILED
            ),
            actor=actor,
            provider_http_status=provider_http_status,
            error_category="ambiguous" if unknown else "rejected",
            error_code=error_code,
            error_class=error_class,
            error_summary=str(message),
        )
        # SENT is terminal. A reconciliation request can prove delivery while
        # the original request is unwinding from a timeout. Preserve that late
        # provider fact above, but never downgrade the reconciled delivery or
        # emit a false failure audit record.
        if delivery.status == QuotationEmailDelivery.STATUS_SENT:
            return delivery
        # Only the latest committed provider call may change the mutable
        # non-success aggregate. A delayed handler from an older retry remains
        # useful append-only history but cannot overwrite a newer SENDING state.
        if attempt_id and (
            attempt is None or attempt.sequence != delivery.attempt_count
        ):
            return delivery
        # UNKNOWN means Gmail may already have accepted the message. It
        # dominates every later failure classification so blind retry can never
        # be re-enabled by completion-order races.
        if delivery.status == QuotationEmailDelivery.STATUS_UNKNOWN:
            return delivery
        if (
            delivery.status == QuotationEmailDelivery.STATUS_FAILED
            and not unknown
        ):
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


def _record_successful_delivery(
    delivery_id,
    gmail_message_id,
    sent_thread_id,
    actor,
    *,
    attempt_id=None,
    reconciled=False,
):
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
        attempt = None
        if attempt_id:
            attempt = QuotationEmailDeliveryAttempt.objects.filter(
                pk=attempt_id,
                delivery=sent_delivery,
            ).first()
        elif reconciled:
            attempt = _latest_provider_attempt(sent_delivery)
        if reconciled and attempt and not attempt.events.filter(
            event_type__in=[
                QuotationEmailDeliveryAttemptEvent.EVENT_PROVIDER_SENT,
                QuotationEmailDeliveryAttemptEvent.EVENT_PROVIDER_FAILED,
                QuotationEmailDeliveryAttemptEvent.EVENT_PROVIDER_UNKNOWN,
            ]
        ).exists():
            _append_provider_attempt_event(
                attempt,
                event_type=QuotationEmailDeliveryAttemptEvent.EVENT_PROVIDER_UNKNOWN,
                actor=actor,
                event_key="provider_unknown:reconciliation_pending",
                error_category="ambiguous",
                error_code="provider_outcome_missing",
                error_summary=(
                    "The provider call had no durable result before strict Gmail reconciliation."
                ),
            )
        _append_provider_attempt_event(
            attempt,
            event_type=(
                QuotationEmailDeliveryAttemptEvent.EVENT_RECONCILED_SENT
                if reconciled
                else QuotationEmailDeliveryAttemptEvent.EVENT_PROVIDER_SENT
            ),
            actor=actor,
            provider_message_id=gmail_message_id,
            provider_thread_id=sent_thread_id,
            error_category="reconciliation" if reconciled else "",
            error_code="delivery_reconciled" if reconciled else "",
            error_summary=(
                "The sent message was proven by strict Gmail reconciliation."
                if reconciled
                else ""
            ),
        )
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


def _reconcile_delivery_with_gmail(delivery, actor, *, attempt_id=None):
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
    snapshot = _delivery_snapshot(delivery)
    connected_mailbox_email = str(connection.email or "").strip().lower()
    mailbox_email = (
        str(snapshot.mailbox_email or "").strip().lower()
        if snapshot
        else connected_mailbox_email
    )
    if not mailbox_email:
        raise QuotationEmailError(
            "The connected shared Gmail mailbox has no verified email identity.",
            code="gmail_reconnect_required",
            retryable=False,
            quote_finalized=True,
            delivery=delivery,
        )
    if snapshot and connected_mailbox_email != mailbox_email:
        raise QuotationEmailError(
            "The connected shared mailbox does not match the mailbox frozen for this delivery.",
            code="delivery_reconciliation_invalid",
            retryable=False,
            quote_finalized=True,
            delivery=delivery,
        )
    outbound_message_id = str(
        snapshot.outbound_rfc_message_id
        if snapshot
        else delivery.outbound_rfc_message_id
        or ""
    ).strip()
    delivery_mode = snapshot.delivery_mode if snapshot else delivery.delivery_mode
    expected_thread_id = str(
        snapshot.gmail_api_thread_id
        if snapshot
        else delivery.gmail_thread_id
        if delivery_mode == QuotationEmailDelivery.MODE_GMAIL_REPLY
        else delivery.sent_gmail_thread_id
        or ""
    ).strip()
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
        delivery_mode == QuotationEmailDelivery.MODE_GMAIL_REPLY
        and not expected_thread_id
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
            sender_email = _physical_from_address(metadata)
        except QuotationEmailError as exc:
            metadata_errors.append(exc)
            continue
        if sender_email != mailbox_email:
            continue
        if (
            str(metadata.get("rfc_message_id") or "").strip()
            != outbound_message_id
        ):
            continue
        thread_id = str(metadata.get("gmail_thread_id") or "").strip()
        if not thread_id:
            continue
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
            attempt_id=attempt_id,
            reconciled=True,
        )
    return None


def _reconcile_after_ambiguous_outcome(delivery, actor, *, attempt_id=None):
    """Reconcile or preserve the delivery's non-retryable ambiguous state."""

    try:
        return _reconcile_delivery_with_gmail(
            delivery,
            actor,
            attempt_id=attempt_id,
        )
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
                attempt_id=attempt_id,
                error_code="gmail_reconciliation_unavailable",
                error_class=type(exc).__name__,
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
    preview_fingerprint = str(data.get("preview_fingerprint") or "").strip()
    preflight = prepare_email_preview(
        quotation,
        actor,
        thread_selection_token=thread_selection_token,
        persist=False,
    )
    prepared_access_token = None
    prepared_credential_generation = None
    if preflight.status not in {
        QuotationEmailDelivery.STATUS_SENT,
        QuotationEmailDelivery.STATUS_SENDING,
        QuotationEmailDelivery.STATUS_UNKNOWN,
    }:
        _require_current_email_preview(
            quotation,
            preflight,
            actor,
            preview_fingerprint,
        )
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
            prepared_credential_generation = _gmail_send_credential_generation(
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
            expected_preview_fingerprint=preview_fingerprint,
        )
    else:
        preview = preflight
    if preview.status == QuotationEmailDelivery.STATUS_SENT:
        # SENT is terminal and idempotent. A later OAuth disconnect must not
        # turn a harmless repeated click into a reconnect error or provider call.
        return preview.quotation, preview, True
    if preview.status == QuotationEmailDelivery.STATUS_UNKNOWN:
        reconciled = _reconcile_after_ambiguous_outcome(preview, actor)
        if reconciled:
            reconciled_quote, reconciled_delivery = reconciled
            return reconciled_quote, reconciled_delivery, True
        preview = QuotationEmailDelivery.objects.select_related(
            "quotation", "gmail_connection"
        ).get(pk=preview.pk)
        if preview.status == QuotationEmailDelivery.STATUS_SENT:
            return preview.quotation, preview, True
        if preview.status == QuotationEmailDelivery.STATUS_UNKNOWN:
            raise QuotationEmailError(
                "Gmail still does not confirm the previous delivery. Check the shared Sent mailbox before taking any further action.",
                code="delivery_unknown",
                http_status=409,
                retryable=False,
                quote_finalized=preview.quotation.status in {
                    Quotation.STATUS_FINALIZED,
                    Quotation.STATUS_SENT,
                },
                delivery=preview,
            )
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
    if preview.status == QuotationEmailDelivery.STATUS_SENDING:
        preview = QuotationEmailDelivery.objects.select_related(
            "quotation", "gmail_connection"
        ).get(pk=preview.pk)
        if preview.status == QuotationEmailDelivery.STATUS_SENT:
            return preview.quotation, preview, True
        if preview.status == QuotationEmailDelivery.STATUS_UNKNOWN:
            raise QuotationEmailError(
                "Gmail did not confirm the previous delivery. Check the shared Sent mailbox before taking any further action.",
                code="delivery_unknown",
                http_status=409,
                retryable=False,
                quote_finalized=preview.quotation.status in {
                    Quotation.STATUS_FINALIZED,
                    Quotation.STATUS_SENT,
                },
                delivery=preview,
            )
        if preview.status == QuotationEmailDelivery.STATUS_SENDING:
            raise QuotationEmailError(
                "This quotation email is already being sent.",
                code="delivery_in_progress",
                http_status=409,
                retryable=False,
                quote_finalized=preview.quotation.status in {
                    Quotation.STATUS_FINALIZED,
                    Quotation.STATUS_SENT,
                },
                delivery=preview,
            )
    preparation_failure = None
    raw_message = None
    send_connection = None
    send_thread_id = ""
    send_is_reply = False
    provider_attempt_id = None
    if prepared_access_token is None:
        try:
            prepared_access_token = get_valid_access_token(preview.gmail_connection)
            prepared_credential_generation = _gmail_send_credential_generation(
                preview.gmail_connection
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
                delivery=preview,
            ) from exc
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

        send_connection = _lock_email_render_dependencies(
            locked_quote,
            locked_delivery,
        )
        if (
            _gmail_send_credential_generation(send_connection)
            != prepared_credential_generation
        ):
            raise QuotationEmailError(
                "The shared Gmail connection changed while this send was being prepared. Review the current email preview and try again.",
                code="gmail_connection_changed",
                http_status=409,
                retryable=True,
                quote_finalized=locked_quote.status in {
                    Quotation.STATUS_FINALIZED,
                    Quotation.STATUS_SENT,
                },
                delivery=locked_delivery,
            )
        pdf_config = get_quotation_pdf_config(quotation=locked_quote)
        _require_current_email_preview(
            locked_quote,
            locked_delivery,
            actor,
            preview_fingerprint,
            pdf_config=pdf_config,
        )

        # Validate every user-controlled recipient/header before changing the
        # quotation state. Provider/configuration failures happen later and
        # deliberately leave a valid quotation finalized for retry.
        to_addresses, cc_addresses, subject, body = _validate_editable_fields(
            locked_delivery,
            data,
        )
        outbound_snapshot = _delivery_snapshot(
            locked_delivery,
            include_raw=True,
            for_update=True,
        )
        if outbound_snapshot:
            _require_snapshot_matches_delivery(locked_delivery, outbound_snapshot)
            _require_payload_matches_snapshot(
                outbound_snapshot,
                to_addresses=to_addresses,
                cc_addresses=cc_addresses,
                subject=subject,
                body=body,
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
        locked_delivery.attempt_count += 1
        locked_delivery.sending_started_at = timezone.now()
        locked_delivery.failed_at = None
        locked_delivery.last_error = ""

        # Finalization deliberately mutates status, blank brand snapshots, and
        # aggregate totals. The projected fingerprint must remain identical;
        # this second assertion prevents a future finalization change from
        # silently escaping the reviewed contract.
        _require_current_email_preview(
            locked_quote,
            locked_delivery,
            actor,
            preview_fingerprint,
            pdf_config=pdf_config,
        )

        try:
            # A savepoint keeps the outer transaction usable if PDF/MIME
            # preparation fails. The outer transaction can then commit both
            # the finalized quotation and a durable FAILED delivery state.
            with transaction.atomic():
                if outbound_snapshot:
                    raw_mime = _verify_outbound_snapshot(outbound_snapshot)
                    raw_message = _encode_gmail_raw_message(raw_mime)
                    pdf_sha256 = outbound_snapshot.attachment_sha256
                    pdf_size = outbound_snapshot.attachment_size
                else:
                    pdf_bytes = build_quotation_pdf(
                        locked_quote,
                        config=pdf_config,
                    )
                    pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
                    pdf_size = len(pdf_bytes)
                    if (
                        locked_delivery.attachment_sha256
                        and locked_delivery.attachment_sha256 != pdf_sha256
                    ):
                        raise QuotationEmailError(
                            "The regenerated quotation PDF differs from the attachment recorded for the previous attempt. Start a reviewed revision instead of retrying with changed content.",
                            code="attachment_snapshot_mismatch",
                            retryable=False,
                        )
                    if (
                        locked_delivery.attachment_size is not None
                        and locked_delivery.attachment_size != pdf_size
                    ):
                        raise QuotationEmailError(
                            "The regenerated quotation PDF size differs from the attachment recorded for the previous attempt. Start a reviewed revision instead of retrying with changed content.",
                            code="attachment_snapshot_mismatch",
                            retryable=False,
                        )
                    raw_message = _build_raw_message(
                        locked_delivery,
                        pdf_bytes,
                        sender_name=pdf_config.company_name,
                    )
                    outbound_snapshot = _create_outbound_snapshot(
                        locked_delivery,
                        actor,
                        sender_name=pdf_config.company_name,
                        pdf_bytes=pdf_bytes,
                        raw_message=raw_message,
                    )
                    # Gmail always receives bytes read back from the durable
                    # snapshot, never a separate in-memory construction.
                    outbound_snapshot = QuotationEmailOutboundSnapshot.objects.get(
                        pk=outbound_snapshot.pk
                    )
                    raw_message = _encode_gmail_raw_message(
                        _verify_outbound_snapshot(outbound_snapshot)
                    )
        except Exception as exc:  # normalized after the finalized state commits
            preparation_failure = exc
            safe_failure = (
                exc.message
                if isinstance(exc, QuotationEmailError)
                else "The quotation PDF or email could not be prepared."
            )
            locked_delivery.status = QuotationEmailDelivery.STATUS_FAILED
            locked_delivery.last_error = str(safe_failure)[:1000]
            locked_delivery.failed_at = timezone.now()
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
            audit_log(
                actor,
                QuotationAuditLog.ACTION_EMAIL_FAILED,
                locked_delivery,
                message=f"Email delivery failed for {locked_quote.quotation_number}.",
                changes={"attempt_count": locked_delivery.attempt_count},
                company=locked_quote.company,
                quotation=locked_quote,
            )
        else:
            if (
                not locked_delivery.attachment_sha256
                or locked_delivery.attachment_size is None
            ):
                locked_delivery.attachment_sha256 = pdf_sha256
                locked_delivery.attachment_size = pdf_size
            locked_delivery.status = QuotationEmailDelivery.STATUS_SENDING
            locked_delivery.save(
                update_fields=[
                    "to_addresses",
                    "cc_addresses",
                    "subject",
                    "body",
                    "actor",
                    "status",
                    "attachment_sha256",
                    "attachment_size",
                    "attempt_count",
                    "sending_started_at",
                    "failed_at",
                    "last_error",
                    "updated_at",
                ]
            )
            provider_attempt = _create_provider_attempt(
                locked_delivery,
                outbound_snapshot,
                actor,
            )
            provider_attempt_id = provider_attempt.id
            send_thread_id = outbound_snapshot.gmail_api_thread_id
            send_is_reply = (
                outbound_snapshot.delivery_mode
                == QuotationEmailDelivery.MODE_GMAIL_REPLY
            )
        delivery_id = locked_delivery.id

    if preparation_failure is not None:
        failed = QuotationEmailDelivery.objects.select_related("quotation").get(
            pk=delivery_id
        )
        if isinstance(preparation_failure, QuotationEmailError):
            raise QuotationEmailError(
                f"The quotation is finalized, but the email was not sent. {preparation_failure.message}",
                code=preparation_failure.code,
                retryable=preparation_failure.retryable,
                quote_finalized=True,
                delivery=failed,
            ) from preparation_failure
        raise QuotationEmailError(
            "The quotation is finalized, but its PDF/email could not be prepared.",
            code="email_prepare_failed",
            retryable=True,
            quote_finalized=True,
            delivery=failed,
        ) from preparation_failure

    # All customer-facing bytes and thread headers are frozen before locks are
    # released. Only the Gmail network call occurs in the ambiguous window.
    gmail_request_started = False
    try:
        access_token = prepared_access_token
        gmail_request_started = True
        response = gmail_send_raw_message(
            send_connection,
            raw_message,
            thread_id=send_thread_id,
            access_token=access_token,
        )
    except QuotationEmailError as exc:
        failed = _mark_delivery_failure(
            delivery_id,
            unknown=False,
            message=exc.message,
            actor=actor,
            attempt_id=provider_attempt_id,
            error_code=exc.code,
            error_class=type(exc).__name__,
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
            attempt_id=provider_attempt_id,
            error_code="gmail_reconnect_required",
            error_class=type(exc).__name__,
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
                attempt_id=provider_attempt_id,
                error_code="email_prepare_failed",
                error_class=type(exc).__name__,
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
            unknown = _mark_delivery_failure(
                delivery_id,
                unknown=True,
                message=(
                    "Gmail returned a temporary/ambiguous response and did not confirm whether the message was accepted. "
                    "Check the shared Sent mailbox before retrying."
                ),
                actor=actor,
                attempt_id=provider_attempt_id,
                error_code="delivery_unknown",
                error_class=type(exc).__name__,
                provider_http_status=http_code or None,
            )
            if unknown.status == QuotationEmailDelivery.STATUS_SENT:
                return unknown.quotation, unknown, True
            reconciled = _reconcile_after_ambiguous_outcome(
                unknown,
                actor,
                attempt_id=provider_attempt_id,
            )
            if reconciled:
                reconciled_quote, reconciled_delivery = reconciled
                return reconciled_quote, reconciled_delivery, False
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
            attempt_id=provider_attempt_id,
            error_code="gmail_send_failed",
            error_class=type(exc).__name__,
            provider_http_status=http_code or None,
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
            result = _mark_delivery_failure(
                delivery_id,
                unknown=True,
                message=(
                    "Gmail did not confirm whether the message was accepted. "
                    "Check the shared Sent mailbox using the quotation number before any retry."
                ),
                actor=actor,
                attempt_id=provider_attempt_id,
                error_code="delivery_unknown",
                error_class=type(exc).__name__,
            )
            if result.status == QuotationEmailDelivery.STATUS_SENT:
                return result.quotation, result, True
            reconciled = _reconcile_after_ambiguous_outcome(
                result,
                actor,
                attempt_id=provider_attempt_id,
            )
            if reconciled:
                reconciled_quote, reconciled_delivery = reconciled
                return reconciled_quote, reconciled_delivery, False
        else:
            result = _mark_delivery_failure(
                delivery_id,
                unknown=False,
                message="The quotation PDF or email could not be prepared.",
                actor=actor,
                attempt_id=provider_attempt_id,
                error_code="email_prepare_failed",
                error_class=type(exc).__name__,
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
            result = _mark_delivery_failure(
                delivery_id,
                unknown=True,
                message=(
                    "An unexpected error occurred after delivery started. "
                    "Check the shared Sent mailbox before retrying."
                ),
                actor=actor,
                attempt_id=provider_attempt_id,
                error_code="delivery_unknown",
                error_class=type(exc).__name__,
            )
            if result.status == QuotationEmailDelivery.STATUS_SENT:
                return result.quotation, result, True
            reconciled = _reconcile_after_ambiguous_outcome(
                result,
                actor,
                attempt_id=provider_attempt_id,
            )
            if reconciled:
                reconciled_quote, reconciled_delivery = reconciled
                return reconciled_quote, reconciled_delivery, False
        else:
            result = _mark_delivery_failure(
                delivery_id,
                unknown=False,
                message="The quotation PDF or email could not be prepared.",
                actor=actor,
                attempt_id=provider_attempt_id,
                error_code="email_prepare_failed",
                error_class=type(exc).__name__,
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
        send_is_reply
        and sent_thread_id != send_thread_id
    ):
        unknown = _mark_delivery_failure(
            delivery_id,
            unknown=True,
            message=(
                "Gmail returned an incomplete or mismatched delivery receipt. "
                "Check the shared Sent mailbox before retrying."
            ),
            actor=actor,
            attempt_id=provider_attempt_id,
            error_code="delivery_unknown",
            error_class="IncompleteGmailReceipt",
        )
        if unknown.status == QuotationEmailDelivery.STATUS_SENT:
            return unknown.quotation, unknown, True
        reconciled = _reconcile_after_ambiguous_outcome(
            unknown,
            actor,
            attempt_id=provider_attempt_id,
        )
        if reconciled:
            reconciled_quote, reconciled_delivery = reconciled
            return reconciled_quote, reconciled_delivery, False
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
        attempt_id=provider_attempt_id,
    )
    return sent_quote, sent_delivery, False
