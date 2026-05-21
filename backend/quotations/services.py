from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from .models import (
    CompanyPriceHistory,
    Inquiry,
    InquiryLine,
    Quotation,
    QuotationAuditLog,
    QuotationLine,
)


def audit_log(actor, action, target, message="", changes=None, company=None, quotation=None):
    target_type = target.__class__.__name__ if target else ""
    target_id = getattr(target, "pk", None)
    if quotation is None:
        quotation = target if isinstance(target, Quotation) else getattr(target, "quotation", None)
    if company is None:
        company = getattr(target, "company", None) or getattr(quotation, "company", None)

    return QuotationAuditLog.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        target_type=target_type,
        target_id=target_id,
        company=company,
        quotation=quotation,
        message=message,
        changes=changes or {},
    )


def ensure_quotation_editable(quotation):
    if not quotation.is_editable:
        raise ValidationError("Finalized, sent, revised, and cancelled quotations cannot be edited directly.")


def recalculate_quotation_totals(quotation):
    totals = quotation.lines.aggregate(
        subtotal=Sum("line_subtotal"),
        vat_total=Sum("vat_amount"),
        total=Sum("line_total"),
    )
    quotation.subtotal = totals["subtotal"] or Decimal("0.00")
    quotation.vat_total = totals["vat_total"] or Decimal("0.00")
    quotation.total = totals["total"] or Decimal("0.00")
    quotation.save(update_fields=["subtotal", "vat_total", "total", "updated_at"])
    return quotation


@transaction.atomic
def create_imported_inquiry(validated_data, actor):
    lines_data = validated_data.pop("lines")
    inquiry = Inquiry.objects.create(
        source=Inquiry.SOURCE_IMPORTED,
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
        **validated_data,
    )
    for index, line_data in enumerate(lines_data):
        InquiryLine.objects.create(
            inquiry=inquiry,
            sort_order=index,
            raw_line=line_data.get("raw_line", ""),
            raw_name=line_data["raw_name"],
            quantity=line_data.get("quantity"),
            unit=line_data.get("unit", ""),
            notes=line_data.get("notes", ""),
            matched_quote_item=line_data.get("matched_quote_item"),
            match_status=line_data.get("match_status", InquiryLine.MATCH_UNRESOLVED),
            parse_status=line_data.get("parse_status", InquiryLine.PARSE_NEEDS_REVIEW),
            parse_confidence=line_data.get("parse_confidence", 0.0),
        )
    audit_log(
        actor,
        QuotationAuditLog.ACTION_IMPORTED,
        inquiry,
        message=f"Imported inquiry {inquiry.pk} with {len(lines_data)} reviewed line(s).",
        changes={
            "source_type": inquiry.source_type,
            "source_filename": inquiry.source_filename,
            "parse_method": inquiry.parse_method,
            "line_count": len(lines_data),
        },
    )
    return inquiry


@transaction.atomic
def create_quotation_from_inquiry(inquiry, actor):
    inquiry = Inquiry.objects.select_for_update().select_related("company").get(pk=inquiry.pk)
    existing = (
        Quotation.objects.filter(inquiry=inquiry)
        .select_related("company", "contact", "inquiry", "created_by", "finalized_by", "parent")
        .order_by("-version", "-created_at", "-pk")
        .first()
    )
    if existing:
        return existing, False

    quotation = Quotation.objects.create(
        company=inquiry.company,
        contact=inquiry.contact,
        inquiry=inquiry,
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
    )

    for index, line in enumerate(inquiry.lines.select_related("matched_quote_item").order_by("sort_order", "id")):
        quote_item = line.matched_quote_item
        item_name = quote_item.name if quote_item else line.raw_name
        QuotationLine.objects.create(
            quotation=quotation,
            inquiry_line=line,
            quote_item=quote_item,
            item_name_snapshot=item_name,
            quantity=line.quantity or Decimal("1.000"),
            unit=line.unit,
            match_status=line.match_status,
            sort_order=index,
            notes=line.notes,
        )

    inquiry.status = Inquiry.STATUS_QUOTED
    inquiry.save(update_fields=["status", "updated_at"])
    recalculate_quotation_totals(quotation)
    audit_log(
        actor,
        QuotationAuditLog.ACTION_CREATED,
        quotation,
        message=f"Created quotation {quotation.quotation_number} from inquiry {inquiry.pk}.",
    )
    return quotation, True


def _validate_line_for_finalization(line):
    if line.match_status == QuotationLine.MATCH_IGNORED:
        return
    if line.match_status != QuotationLine.MATCH_CONFIRMED:
        raise ValidationError(f"Line '{line.item_name_snapshot}' has an unresolved item match.")
    if not line.quote_item_id:
        raise ValidationError(f"Line '{line.item_name_snapshot}' must be linked to a quote item.")
    if line.quantity is None or line.quantity <= 0:
        raise ValidationError(f"Line '{line.item_name_snapshot}' must have a valid quantity.")
    if line.unit_price is None or line.unit_price <= 0:
        raise ValidationError(f"Line '{line.item_name_snapshot}' must have a valid unit price.")


@transaction.atomic
def finalize_quotation(quotation, actor):
    quotation = (
        Quotation.objects.select_for_update()
        .select_related("company")
        .prefetch_related("lines__quote_item")
        .get(pk=quotation.pk)
    )
    if quotation.status not in {
        Quotation.STATUS_DRAFT,
        Quotation.STATUS_PENDING_REVIEW,
        Quotation.STATUS_APPROVED,
    }:
        raise ValidationError("Only draft, pending review, or approved quotations can be finalized.")

    lines = list(quotation.lines.select_related("quote_item").order_by("sort_order", "id"))
    if not lines:
        raise ValidationError("A quotation must have at least one line before finalization.")

    for line in lines:
        _validate_line_for_finalization(line)

    recalculate_quotation_totals(quotation)
    quotation.status = Quotation.STATUS_FINALIZED
    quotation.finalized_by = actor if getattr(actor, "is_authenticated", False) else None
    quotation.finalized_at = timezone.now()
    quotation.save(update_fields=["status", "finalized_by", "finalized_at", "updated_at"])

    for line in lines:
        if line.match_status == QuotationLine.MATCH_IGNORED:
            continue
        CompanyPriceHistory.objects.get_or_create(
            quotation_line=line,
            defaults={
                "company": quotation.company,
                "quote_item": line.quote_item,
                "quotation": quotation,
                "unit_price": line.unit_price,
                "currency": quotation.currency,
                "quantity": line.quantity,
                "unit": line.unit,
                "quoted_at": quotation.finalized_at,
                "created_by": actor if getattr(actor, "is_authenticated", False) else None,
            },
        )

    audit_log(
        actor,
        QuotationAuditLog.ACTION_FINALIZED,
        quotation,
        message=f"Finalized quotation {quotation.quotation_number}.",
    )
    return quotation


@transaction.atomic
def revise_quotation(quotation, actor):
    source = (
        Quotation.objects.select_for_update()
        .select_related("company")
        .prefetch_related("lines")
        .get(pk=quotation.pk)
    )
    if source.status not in {Quotation.STATUS_FINALIZED, Quotation.STATUS_SENT}:
        raise ValidationError("Only finalized or sent quotations can be revised.")

    root = source.parent or source
    max_version = (
        Quotation.objects.filter(models_parent_or_self(root))
        .aggregate(max_version=Max("version"))
        .get("max_version")
        or root.version
    )
    revision = Quotation.objects.create(
        company=source.company,
        contact=source.contact,
        inquiry=source.inquiry,
        status=Quotation.STATUS_DRAFT,
        version=max_version + 1,
        parent=root,
        valid_until=source.valid_until,
        currency=source.currency,
        notes=source.notes,
        internal_notes=source.internal_notes,
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
    )
    for line in source.lines.order_by("sort_order", "id"):
        QuotationLine.objects.create(
            quotation=revision,
            inquiry_line=line.inquiry_line,
            quote_item=line.quote_item,
            item_name_snapshot=line.item_name_snapshot,
            description=line.description,
            quantity=line.quantity,
            unit=line.unit,
            unit_price=line.unit_price,
            vat_rate=line.vat_rate,
            match_status=line.match_status,
            sort_order=line.sort_order,
            notes=line.notes,
        )

    old_status = source.status
    source.status = Quotation.STATUS_REVISED
    source.save(update_fields=["status", "updated_at"])
    recalculate_quotation_totals(revision)
    audit_log(
        actor,
        QuotationAuditLog.ACTION_REVISED,
        source,
        message=f"Created revision {revision.quotation_number} from {source.quotation_number}.",
        changes={"old_status": old_status, "new_status": source.status, "revision_id": revision.pk},
    )
    audit_log(
        actor,
        QuotationAuditLog.ACTION_CREATED,
        revision,
        message=f"Created draft revision from {source.quotation_number}.",
    )
    return revision


def models_parent_or_self(root):
    from django.db.models import Q

    return Q(pk=root.pk) | Q(parent=root)


@transaction.atomic
def transition_quotation_status(quotation, actor, target_status):
    quotation = Quotation.objects.select_for_update().get(pk=quotation.pk)
    old_status = quotation.status

    allowed = {
        Quotation.STATUS_PENDING_REVIEW: {Quotation.STATUS_DRAFT},
        Quotation.STATUS_APPROVED: {Quotation.STATUS_DRAFT, Quotation.STATUS_PENDING_REVIEW},
        Quotation.STATUS_SENT: {Quotation.STATUS_FINALIZED},
        Quotation.STATUS_CANCELLED: {
            Quotation.STATUS_DRAFT,
            Quotation.STATUS_PENDING_REVIEW,
            Quotation.STATUS_APPROVED,
            Quotation.STATUS_FINALIZED,
            Quotation.STATUS_SENT,
        },
    }
    if old_status not in allowed.get(target_status, set()):
        raise ValidationError(f"Cannot move quotation from {old_status} to {target_status}.")

    quotation.status = target_status
    update_fields = ["status", "updated_at"]
    if target_status == Quotation.STATUS_SENT:
        quotation.sent_at = timezone.now()
        update_fields.append("sent_at")
    quotation.save(update_fields=update_fields)
    audit_log(
        actor,
        QuotationAuditLog.ACTION_STATUS_CHANGED,
        quotation,
        message=f"Quotation status changed from {old_status} to {target_status}.",
        changes={"old_status": old_status, "new_status": target_status},
    )
    return quotation
