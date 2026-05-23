from decimal import Decimal
from datetime import datetime, time

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from .models import (
    CompanyPriceHistory,
    HistoricalPriceImport,
    HistoricalPriceImportLine,
    Inquiry,
    InquiryLine,
    Quotation,
    QuotationAuditLog,
    QuotationLine,
    QuoteItem,
    normalize_label,
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


def _date_for_audit(value):
    if not value:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


@transaction.atomic
def create_historical_price_import(preview_data, actor):
    lines_data = preview_data.pop("lines", [])
    warnings = preview_data.pop("warnings", [])
    meta = preview_data.pop("meta", {})
    historical_import = HistoricalPriceImport.objects.create(
        parse_meta={**meta, "warnings": warnings},
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
        **preview_data,
    )
    for index, line_data in enumerate(lines_data):
        HistoricalPriceImportLine.objects.create(
            historical_import=historical_import,
            sort_order=index,
            raw_line=line_data.get("raw_line", ""),
            item_name=line_data.get("item_name", "")[:255],
            quantity=line_data.get("quantity"),
            unit=line_data.get("unit", ""),
            unit_price=line_data.get("unit_price"),
            amount=line_data.get("amount"),
            vat_amount=line_data.get("vat_amount"),
            vat_rate=line_data.get("vat_rate") or Decimal("0.00"),
            line_total=line_data.get("line_total"),
            serial_no=line_data.get("serial_no", ""),
            source_page=line_data.get("source_page"),
            source_row=line_data.get("source_row"),
            parse_confidence=line_data.get("parse_confidence", 0.0),
            status=line_data.get("status", HistoricalPriceImportLine.STATUS_NEEDS_REVIEW),
        )
    audit_log(
        actor,
        QuotationAuditLog.ACTION_IMPORTED,
        historical_import,
        message=f"Parsed historical price import {historical_import.pk} with {len(lines_data)} line(s).",
        changes={
            "source_filename": historical_import.source_filename,
            "source_sha256": historical_import.source_sha256,
            "document_number": historical_import.document_number,
            "document_date": _date_for_audit(historical_import.document_date),
            "line_count": len(lines_data),
        },
    )
    return historical_import


def _historical_quote_number(historical_import):
    base = (historical_import.document_number or "").strip()
    if base and not Quotation.objects.filter(quotation_number=base).exists():
        return base
    fallback = f"HIST-{historical_import.pk:06d}"
    if not Quotation.objects.filter(quotation_number=fallback).exists():
        return fallback
    suffix = 1
    while Quotation.objects.filter(quotation_number=f"{fallback}-{suffix}").exists():
        suffix += 1
    return f"{fallback}-{suffix}"


def _quoted_at_for_historical_import(document_date):
    return timezone.make_aware(datetime.combine(document_date, time(hour=12)))


def _historical_line_is_duplicate(historical_import, line):
    return CompanyPriceHistory.objects.filter(
        company=historical_import.company,
        quote_item=line.quote_item,
        quoted_at__date=historical_import.document_date,
        unit_price=line.unit_price,
        quantity=line.quantity,
        unit__iexact=line.unit,
    ).exists()


def _historical_ready_errors(historical_import, line):
    errors = []
    if not historical_import.company_id:
        errors.append("Select the company before marking rows ready.")
    if not historical_import.document_date:
        errors.append("Enter the quotation date before marking rows ready.")
    if not line.quote_item_id:
        errors.append("Link a Quote Item.")
    if line.quantity is None or line.quantity <= 0:
        errors.append("Enter a quantity greater than zero.")
    if line.unit_price is None or line.unit_price < 0:
        errors.append("Enter a unit price of zero or more.")
    return errors


def _historical_bulk_summary(results):
    summary = {}
    for result in results:
        status_value = result.get("status", "unknown")
        summary[status_value] = summary.get(status_value, 0) + 1
    return {
        "created": summary.get("created", 0),
        "linked_existing": summary.get("linked_existing", 0),
        "updated": summary.get("updated", 0),
        "skipped": summary.get("skipped", 0),
        "failed": summary.get("failed", 0),
        "results": results,
    }


def _get_historical_lines_for_bulk(historical_import, row_ids):
    row_ids = [row_id for row_id in row_ids if row_id]
    if not row_ids:
        raise ValidationError("Select at least one row.")
    historical_import = HistoricalPriceImport.objects.select_for_update().get(pk=historical_import.pk)
    if historical_import.status == HistoricalPriceImport.STATUS_COMMITTED:
        raise ValidationError("Committed historical imports cannot be edited.")
    if historical_import.status == HistoricalPriceImport.STATUS_CANCELLED:
        raise ValidationError("Cancelled historical imports cannot be edited.")
    lines = list(
        historical_import.lines.select_for_update()
        .filter(id__in=row_ids)
        .order_by("sort_order", "id")
    )
    found_ids = {line.id for line in lines}
    missing_ids = [row_id for row_id in row_ids if row_id not in found_ids]
    if missing_ids:
        raise ValidationError(f"Rows do not belong to this import or no longer exist: {missing_ids}")
    return historical_import, lines


@transaction.atomic
def bulk_create_quote_items_for_historical_import(historical_import, row_ids, actor):
    historical_import, lines = _get_historical_lines_for_bulk(historical_import, row_ids)
    results = []
    created_items = []

    for line in lines:
        if line.status in {HistoricalPriceImportLine.STATUS_COMMITTED, HistoricalPriceImportLine.STATUS_DUPLICATE}:
            results.append({"row_id": line.id, "status": "failed", "message": "Committed or duplicate rows cannot be changed."})
            continue

        item_name = (line.item_name or "").strip()
        if not item_name:
            results.append({"row_id": line.id, "status": "failed", "message": "Row has no item name."})
            continue

        normalized_name = normalize_label(item_name)
        quote_item = QuoteItem.objects.filter(normalized_name=normalized_name).order_by("id").first()
        result_status = "linked_existing"
        message = "Linked existing QuoteItem."

        if not quote_item:
            quote_item = QuoteItem.objects.create(
                name=item_name[:255],
                unit=line.unit or "",
                notes=f"Created from historical import {historical_import.source_filename}".strip(),
            )
            created_items.append(quote_item.id)
            result_status = "created"
            message = "Created QuoteItem and linked row."

        line.quote_item = quote_item
        line.status = (
            HistoricalPriceImportLine.STATUS_READY
            if not _historical_ready_errors(historical_import, line)
            else HistoricalPriceImportLine.STATUS_NEEDS_REVIEW
        )
        line.duplicate_reason = ""
        line.save(update_fields=["quote_item", "status", "duplicate_reason", "updated_at"])
        results.append(
            {
                "row_id": line.id,
                "status": result_status,
                "quote_item_id": quote_item.id,
                "quote_item_name": quote_item.name,
                "row_status": line.status,
                "message": message,
            }
        )

    audit_log(
        actor,
        QuotationAuditLog.ACTION_UPDATED,
        historical_import,
        message=f"Bulk linked QuoteItems for {len(lines)} historical import row(s).",
        changes={"row_ids": row_ids, "created_quote_item_ids": created_items, "results": results},
    )
    return _historical_bulk_summary(results), historical_import


@transaction.atomic
def bulk_update_historical_import_rows(historical_import, row_ids, status_value, actor):
    if status_value not in {
        HistoricalPriceImportLine.STATUS_READY,
        HistoricalPriceImportLine.STATUS_NEEDS_REVIEW,
        HistoricalPriceImportLine.STATUS_SKIPPED,
    }:
        raise ValidationError("Unsupported bulk row status.")

    historical_import, lines = _get_historical_lines_for_bulk(historical_import, row_ids)
    results = []

    for line in lines:
        if line.status in {HistoricalPriceImportLine.STATUS_COMMITTED, HistoricalPriceImportLine.STATUS_DUPLICATE}:
            results.append({"row_id": line.id, "status": "failed", "message": "Committed or duplicate rows cannot be changed."})
            continue

        if status_value == HistoricalPriceImportLine.STATUS_READY:
            errors = _historical_ready_errors(historical_import, line)
            if errors:
                results.append({"row_id": line.id, "status": "failed", "message": " ".join(errors)})
                continue

        line.status = status_value
        line.duplicate_reason = ""
        line.save(update_fields=["status", "duplicate_reason", "updated_at"])
        results.append({"row_id": line.id, "status": "updated", "row_status": line.status, "message": f"Marked {status_value}."})

    audit_log(
        actor,
        QuotationAuditLog.ACTION_UPDATED,
        historical_import,
        message=f"Bulk updated {len(lines)} historical import row(s) to {status_value}.",
        changes={"row_ids": row_ids, "status": status_value, "results": results},
    )
    return _historical_bulk_summary(results), historical_import


@transaction.atomic
def commit_historical_price_import(historical_import, actor):
    historical_import = HistoricalPriceImport.objects.select_for_update().get(pk=historical_import.pk)
    if historical_import.status == HistoricalPriceImport.STATUS_COMMITTED:
        raise ValidationError("This historical import has already been committed.")
    if historical_import.status == HistoricalPriceImport.STATUS_CANCELLED:
        raise ValidationError("Cancelled historical imports cannot be committed.")
    if not historical_import.company_id:
        raise ValidationError("Select the company before committing historical prices.")
    if not historical_import.document_date:
        raise ValidationError("Enter the quotation date before committing historical prices.")

    ready_lines = list(
        historical_import.lines.select_for_update()
        .filter(status=HistoricalPriceImportLine.STATUS_READY)
        .order_by("sort_order", "id")
    )
    if not ready_lines:
        raise ValidationError("Mark at least one reviewed line as ready before committing.")

    for line in ready_lines:
        if not line.quote_item_id:
            raise ValidationError(f"Line '{line.item_name}' must be linked to a Quote Item before committing.")
        if line.quantity is None or line.quantity <= 0:
            raise ValidationError(f"Line '{line.item_name}' must have a valid quantity.")
        if line.unit_price is None or line.unit_price < 0:
            raise ValidationError(f"Line '{line.item_name}' must have a valid unit price.")

    quotation = Quotation.objects.create(
        company=historical_import.company,
        quotation_number=_historical_quote_number(historical_import),
        status=Quotation.STATUS_FINALIZED,
        currency=historical_import.currency or "AED",
        subtotal=historical_import.subtotal or Decimal("0.00"),
        vat_total=historical_import.vat_total or Decimal("0.00"),
        total=historical_import.total or Decimal("0.00"),
        internal_notes=f"Historical price import from {historical_import.source_filename}",
        is_historical_import=True,
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
        finalized_by=actor if getattr(actor, "is_authenticated", False) else None,
        finalized_at=timezone.now(),
    )

    created_count = 0
    duplicate_count = 0
    quoted_at = _quoted_at_for_historical_import(historical_import.document_date)

    for index, line in enumerate(ready_lines):
        if _historical_line_is_duplicate(historical_import, line):
            line.status = HistoricalPriceImportLine.STATUS_DUPLICATE
            line.duplicate_reason = "Matching company, item, date, unit price, quantity, and unit already exists in price history."
            line.save(update_fields=["status", "duplicate_reason", "updated_at"])
            duplicate_count += 1
            continue

        quotation_line = QuotationLine.objects.create(
            quotation=quotation,
            quote_item=line.quote_item,
            item_name_snapshot=line.quote_item.name,
            description=line.item_name,
            quantity=line.quantity,
            unit=line.unit,
            unit_price=line.unit_price,
            vat_rate=line.vat_rate or Decimal("0.00"),
            match_status=QuotationLine.MATCH_CONFIRMED,
            sort_order=index,
            notes=f"Imported from historical source line {line.serial_no or line.source_row or line.pk}.",
        )
        CompanyPriceHistory.objects.create(
            company=historical_import.company,
            quote_item=line.quote_item,
            quotation=quotation,
            quotation_line=quotation_line,
            unit_price=line.unit_price,
            currency=quotation.currency,
            quantity=line.quantity,
            unit=line.unit,
            quoted_at=quoted_at,
            created_by=actor if getattr(actor, "is_authenticated", False) else None,
        )
        line.status = HistoricalPriceImportLine.STATUS_COMMITTED
        line.duplicate_reason = ""
        line.save(update_fields=["status", "duplicate_reason", "updated_at"])
        created_count += 1

    if created_count == 0:
        quotation.delete()
        raise ValidationError("No new price history rows were committed because all ready rows were duplicates.")

    recalculate_quotation_totals(quotation)
    historical_import.status = HistoricalPriceImport.STATUS_COMMITTED
    historical_import.created_quotation = quotation
    historical_import.committed_by = actor if getattr(actor, "is_authenticated", False) else None
    historical_import.committed_at = timezone.now()
    historical_import.save(update_fields=["status", "created_quotation", "committed_by", "committed_at", "updated_at"])
    audit_log(
        actor,
        QuotationAuditLog.ACTION_IMPORTED,
        historical_import,
        message=f"Committed {created_count} historical price row(s) from import {historical_import.pk}.",
        changes={
            "quotation_id": quotation.pk,
            "quotation_number": quotation.quotation_number,
            "created_count": created_count,
            "duplicate_count": duplicate_count,
        },
        company=historical_import.company,
        quotation=quotation,
    )
    return historical_import


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
            "source_file_ref": inquiry.source_file_ref,
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
