from datetime import datetime, time
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone
from django.utils.text import slugify

from api.models import Product, ProductImage

from .matching import create_product_alias, suggest_product_for_text
from .models import (
    Company,
    CompanyPriceHistory,
    HistoricalPriceImport,
    HistoricalPriceImportLine,
    Inquiry,
    InquiryLine,
    normalize_label,
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
    totals = quotation.lines.exclude(match_status=QuotationLine.MATCH_IGNORED).aggregate(
        subtotal=Sum("line_subtotal"),
        vat_total=Sum("vat_amount"),
        total=Sum("line_total"),
    )
    quotation.subtotal = totals["subtotal"] or Decimal("0.00")
    quotation.vat_total = totals["vat_total"] or Decimal("0.00")
    quotation.total = totals["total"] or Decimal("0.00")
    quotation.save(update_fields=["subtotal", "vat_total", "total", "updated_at"])
    return quotation


def _snapshot_decimal(value):
    return str(value) if value is not None else None


def _snapshot_date(value):
    return value.isoformat() if value else None


def build_quotation_delete_snapshot(quotation):
    lines = quotation.lines.select_related("product", "quote_item").order_by("sort_order", "id")
    return {
        "quotation": {
            "id": quotation.id,
            "quotation_number": quotation.quotation_number,
            "status": quotation.status,
            "version": quotation.version,
            "company_id": quotation.company_id,
            "company_name": quotation.company.name if quotation.company_id else "",
            "contact_id": quotation.contact_id,
            "contact_name": quotation.contact.name if quotation.contact_id else "",
            "inquiry_id": quotation.inquiry_id,
            "valid_until": _snapshot_date(quotation.valid_until),
            "currency": quotation.currency,
            "payment_terms": quotation.payment_terms,
            "subtotal": _snapshot_decimal(quotation.subtotal),
            "vat_total": _snapshot_decimal(quotation.vat_total),
            "total": _snapshot_decimal(quotation.total),
        },
        "lines": [
            {
                "id": line.id,
                "sort_order": line.sort_order,
                "item_name_snapshot": line.item_name_snapshot,
                "product_id": line.product_id,
                "product_name": line.product.name if line.product_id else "",
                "quote_item_id": line.quote_item_id,
                "quote_item_name": line.quote_item.name if line.quote_item_id else "",
                "quantity": _snapshot_decimal(line.quantity),
                "unit": line.unit,
                "unit_price": _snapshot_decimal(line.unit_price),
                "vat_rate": _snapshot_decimal(line.vat_rate),
                "line_subtotal": _snapshot_decimal(line.line_subtotal),
                "vat_amount": _snapshot_decimal(line.vat_amount),
                "line_total": _snapshot_decimal(line.line_total),
                "match_status": line.match_status,
                "notes": line.notes,
            }
            for line in lines
        ],
    }


def _product_name_from_line(line, override_name=""):
    name = (override_name or "").strip()
    if not name:
        name = (line.item_name_snapshot or "").strip()
    if not name and line.inquiry_line_id:
        name = (line.inquiry_line.raw_name or "").strip()
    name = " ".join(name.split())
    if not name:
        raise ValidationError("Enter a Product name before creating it from this line.")
    return name[:200]


def _find_product_by_normalized_name(name):
    key = normalize_label(name)
    if not key:
        return None
    slug_key = slugify(name)
    exact = Product.objects.filter(name__iexact=name).first()
    if exact:
        return exact
    if slug_key:
        slug_match = Product.objects.filter(slug=slug_key).first()
        if slug_match:
            return slug_match
    first_token = key.split()[0] if key.split() else ""
    candidates = Product.objects.all()
    if first_token:
        candidates = candidates.filter(name__icontains=first_token)
    for product in candidates[:100]:
        if normalize_label(product.name) == key or (slug_key and slugify(product.name) == slug_key):
            return product
    return None


def _get_or_create_internal_product_from_line(line, actor, override_name=""):
    product_name = _product_name_from_line(line, override_name)
    existing = _find_product_by_normalized_name(product_name)
    if existing:
        return existing, False
    product = Product.objects.create(
        name=product_name,
        price=Decimal("0.01"),
        pack_size=(line.unit or "")[:100],
        status="draft",
        show_price=False,
        requires_manual_review=True,
    )
    audit_log(
        actor,
        QuotationAuditLog.ACTION_CREATED,
        product,
        message=f"Created draft/internal Product '{product.name}' from quotation line {line.pk}.",
        quotation=line.quotation,
        company=line.quotation.company,
    )
    return product, True


def _link_quotation_line_to_product(line, product, actor, *, reason="Created/linked from quotation line."):
    line.product = product
    line.quote_item = None
    line.item_name_snapshot = product.name
    line.match_status = QuotationLine.MATCH_CONFIRMED
    line.match_reason = reason
    line.save(update_fields=["product", "quote_item", "item_name_snapshot", "match_status", "match_reason", "line_subtotal", "vat_amount", "line_total", "updated_at"])
    audit_log(
        actor,
        QuotationAuditLog.ACTION_UPDATED,
        line,
        message=f"Linked quotation line to Product '{product.name}'.",
        quotation=line.quotation,
        company=line.quotation.company,
    )
    return line


def _date_for_audit(value):
    if not value:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _historical_import_summary(historical_import):
    company_name = historical_import.company.name if historical_import.company_id else historical_import.suggested_company_name
    return {
        "id": historical_import.id,
        "source_filename": historical_import.source_filename,
        "company": historical_import.company_id,
        "company_name": company_name or "",
        "suggested_company_name": historical_import.suggested_company_name,
        "document_number": historical_import.document_number,
        "document_date": _date_for_audit(historical_import.document_date),
        "status": historical_import.status,
        "created_at": _date_for_audit(historical_import.created_at),
        "committed_at": _date_for_audit(historical_import.committed_at),
        "created_quotation": historical_import.created_quotation_id,
        "created_quotation_number": (
            historical_import.created_quotation.quotation_number
            if historical_import.created_quotation_id
            else ""
        ),
        "line_count": historical_import.lines.count(),
        "subtotal": str(historical_import.subtotal or ""),
        "vat_total": str(historical_import.vat_total or ""),
        "total": str(historical_import.total or ""),
    }


def _preview_company_key(preview_data):
    return normalize_label(preview_data.get("suggested_company_name") or "")


def _match_existing_company_from_preview(preview_data):
    company_key = _preview_company_key(preview_data)
    if not company_key:
        return None

    exact = Company.objects.filter(normalized_name=company_key, is_active=True).first()
    if exact:
        return exact

    company_tokens = {token for token in company_key.split() if len(token) >= 3}
    if not company_tokens:
        return None

    candidates = Company.objects.filter(is_active=True)
    for token in list(company_tokens)[:5]:
        candidates = candidates.filter(normalized_name__icontains=token)

    for company in candidates.order_by("name")[:10]:
        candidate_key = normalize_label(company.name)
        candidate_tokens = {token for token in candidate_key.split() if len(token) >= 3}
        if not candidate_tokens:
            continue
        overlap = company_tokens & candidate_tokens
        if candidate_key in company_key or company_key in candidate_key:
            return company
        if overlap and len(overlap) == min(len(company_tokens), len(candidate_tokens)):
            return company
    return None


def _historical_import_company_key(historical_import):
    if historical_import.company_id:
        return normalize_label(historical_import.company.name)
    return normalize_label(historical_import.suggested_company_name or "")


def _company_keys_match(left, right):
    if not left or not right:
        return False
    if left == right or left in right or right in left:
        return True
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return False
    overlap = len(left_tokens & right_tokens)
    return overlap >= 2 and overlap / min(len(left_tokens), len(right_tokens)) >= 0.67


def _decimal_key(value):
    if value in (None, ""):
        return ""
    try:
        return str(Decimal(str(value)).quantize(Decimal("0.01")))
    except Exception:
        return str(value)


def _quantity_key(value):
    if value in (None, ""):
        return ""
    try:
        return str(Decimal(str(value)).quantize(Decimal("0.001")))
    except Exception:
        return str(value)


def _preview_line_fingerprints(lines):
    fingerprints = set()
    for line in lines or []:
        item_name = normalize_label(line.get("item_name") or line.get("raw_line") or "")
        if not item_name:
            continue
        fingerprints.add(
            "|".join(
                [
                    item_name,
                    _quantity_key(line.get("quantity")),
                    normalize_label(line.get("unit") or ""),
                    _decimal_key(line.get("unit_price")),
                    _decimal_key(line.get("vat_amount")),
                    _decimal_key(line.get("line_total")),
                ]
            )
        )
    return fingerprints


def _import_line_fingerprints(historical_import):
    fingerprints = set()
    for line in historical_import.lines.all():
        item_name = normalize_label(line.item_name or line.raw_line or "")
        if not item_name:
            continue
        fingerprints.add(
            "|".join(
                [
                    item_name,
                    _quantity_key(line.quantity),
                    normalize_label(line.unit or ""),
                    _decimal_key(line.unit_price),
                    _decimal_key(line.vat_amount),
                    _decimal_key(line.line_total),
                ]
            )
        )
    return fingerprints


def _same_company_identity(preview_data, historical_import):
    preview_company = _preview_company_key(preview_data)
    import_keys = [
        _historical_import_company_key(historical_import),
        normalize_label(historical_import.suggested_company_name or ""),
    ]
    return any(_company_keys_match(preview_company, key) for key in import_keys)


def find_historical_import_duplicates(preview_data):
    """Find existing historical imports that look like the same source document.

    Exact file hashes and same-company document numbers are blocking by default,
    because creating a second staged import for those cases is usually a mistake.
    Date/totals/row similarity is advisory so staff can still review edge cases.
    """
    duplicate_check = {
        "is_duplicate": False,
        "blocking": False,
        "duplicate_type": "",
        "message": "",
        "recommended_action": "",
        "matches": [],
    }
    matches_by_id = {}

    def add_match(kind, historical_import, message, *, blocking=False, similarity=None):
        existing = matches_by_id.get(historical_import.id)
        if existing:
            if kind not in existing.get("kinds", []):
                existing["kinds"].append(kind)
            if message not in existing.get("messages", []):
                existing["messages"].append(message)
            existing["blocking"] = existing.get("blocking") or blocking
            if similarity is not None:
                existing["similarity"] = max(existing.get("similarity") or 0, similarity)
            return
        summary = _historical_import_summary(historical_import)
        summary.update(
            {
                "kinds": [kind],
                "messages": [message],
                "blocking": blocking,
                "similarity": similarity,
            }
        )
        matches_by_id[historical_import.id] = summary

    source_sha256 = (preview_data.get("source_sha256") or "").strip()
    if source_sha256:
        for historical_import in (
            HistoricalPriceImport.objects.filter(source_sha256=source_sha256)
            .select_related("company", "created_quotation")
            .order_by("-updated_at", "-id")
        ):
            add_match(
                "exact_file_hash",
                historical_import,
                "This PDF has already been added before.",
                blocking=True,
            )

    document_number = (preview_data.get("document_number") or "").strip()
    if document_number:
        for historical_import in (
            HistoricalPriceImport.objects.filter(document_number__iexact=document_number)
            .select_related("company", "created_quotation")
            .order_by("-updated_at", "-id")
        ):
            same_company = _same_company_identity(preview_data, historical_import)
            add_match(
                "same_document_number",
                historical_import,
                (
                    "This quotation already exists for this company."
                    if same_company
                    else "This quotation number already exists on another historical import."
                ),
                blocking=same_company,
            )

    document_date = preview_data.get("document_date")
    subtotal = preview_data.get("subtotal")
    vat_total = preview_data.get("vat_total")
    total = preview_data.get("total")
    preview_fingerprints = _preview_line_fingerprints(preview_data.get("lines", []))
    if document_date and preview_fingerprints:
        candidates = HistoricalPriceImport.objects.filter(document_date=document_date).select_related("company", "created_quotation")
        if total not in (None, ""):
            candidates = candidates.filter(total=total)
        elif subtotal not in (None, "") or vat_total not in (None, ""):
            if subtotal not in (None, ""):
                candidates = candidates.filter(subtotal=subtotal)
            if vat_total not in (None, ""):
                candidates = candidates.filter(vat_total=vat_total)
        for historical_import in candidates.prefetch_related("lines").order_by("-updated_at", "-id"):
            existing_fingerprints = _import_line_fingerprints(historical_import)
            if not existing_fingerprints:
                continue
            overlap = len(preview_fingerprints & existing_fingerprints)
            denominator = max(1, min(len(preview_fingerprints), len(existing_fingerprints)))
            similarity = overlap / denominator
            if overlap >= 2 and similarity >= 0.60:
                add_match(
                    "similar_rows_totals",
                    historical_import,
                    "This looks similar to a previous import.",
                    blocking=False,
                    similarity=round(similarity, 2),
                )

    matches = list(matches_by_id.values())
    if not matches:
        return duplicate_check

    matches.sort(key=lambda match: (not match.get("blocking", False), match["id"]))
    primary = matches[0]
    duplicate_check.update(
        {
            "is_duplicate": True,
            "blocking": any(match.get("blocking") for match in matches),
            "duplicate_type": primary["kinds"][0],
            "message": primary["messages"][0],
            "recommended_action": "open_existing_import" if primary.get("blocking") else "review_before_commit",
            "primary_match": primary,
            "matches": matches,
        }
    )
    return duplicate_check


@transaction.atomic
def create_historical_price_import(preview_data, actor, batch=None):
    lines_data = preview_data.pop("lines", [])
    warnings = preview_data.pop("warnings", [])
    meta = preview_data.pop("meta", {})
    matched_company = _match_existing_company_from_preview(preview_data)
    if matched_company:
        preview_data["company"] = matched_company
        meta = {
            **meta,
            "company_match": {
                "source": "filename_or_document_hint",
                "company_id": matched_company.id,
                "company_name": matched_company.name,
                "reason": "Matched cleaned historical import company hint to an existing company.",
            },
        }
    historical_import = HistoricalPriceImport.objects.create(
        batch=batch,
        parse_meta={**meta, "warnings": warnings},
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
        **preview_data,
    )
    for index, line_data in enumerate(lines_data):
        match = suggest_product_for_text(line_data.get("item_name", ""))
        HistoricalPriceImportLine.objects.create(
            historical_import=historical_import,
            sort_order=index,
            product=match.product,
            match_reason=match.reason if match.product else "",
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
    filters = {
        "company": historical_import.company,
        "quoted_at__date": historical_import.document_date,
        "unit_price": line.unit_price,
        "quantity": line.quantity,
        "unit__iexact": line.unit,
    }
    if line.product_id:
        filters["product"] = line.product
    else:
        filters["quote_item"] = line.quote_item
    return CompanyPriceHistory.objects.filter(
        **filters,
    ).exists()


def _historical_ready_errors(historical_import, line):
    errors = []
    if not historical_import.company_id:
        errors.append("Select the company before marking rows ready.")
    if not historical_import.document_date:
        errors.append("Enter the quotation date before marking rows ready.")
    if not line.product_id and not line.quote_item_id:
        errors.append("Link a product/item.")
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

        match = suggest_product_for_text(item_name, historical_import.company)
        product = match.product
        result_status = "linked_existing"
        message = match.reason if product else "No matching product found."

        if not product:
            product = Product.objects.create(
                name=item_name[:255],
                pack_size=line.unit or "",
                price=Decimal("0.01"),
                status="draft",
                show_price=False,
                requires_manual_review=True,
                short_description=f"Internal quotation item created from {historical_import.source_filename}".strip(),
            )
            created_items.append(product.id)
            result_status = "created"
            message = "Created internal draft Product and linked row."

        line.product = product
        line.match_reason = message
        line.status = (
            HistoricalPriceImportLine.STATUS_READY
            if not _historical_ready_errors(historical_import, line)
            else HistoricalPriceImportLine.STATUS_NEEDS_REVIEW
        )
        line.duplicate_reason = ""
        line.save(update_fields=["product", "match_reason", "status", "duplicate_reason", "updated_at"])
        results.append(
            {
                "row_id": line.id,
                "status": result_status,
                "product_id": product.id,
                "product_name": product.name,
                "row_status": line.status,
                "message": message,
            }
        )

    audit_log(
        actor,
        QuotationAuditLog.ACTION_UPDATED,
        historical_import,
        message=f"Bulk linked products for {len(lines)} historical import row(s).",
        changes={"row_ids": row_ids, "created_product_ids": created_items, "results": results},
    )
    return _historical_bulk_summary(results), historical_import


def apply_product_match_to_historical_line(line, company=None):
    if line.product_id:
        return line
    match = suggest_product_for_text(line.item_name, company)
    if match.product:
        line.product = match.product
        line.match_reason = match.reason
        if match.confidence >= 0.88 and not _historical_ready_errors(line.historical_import, line):
            line.status = HistoricalPriceImportLine.STATUS_READY
        line.save(update_fields=["product", "match_reason", "status", "updated_at"])
    return line


@transaction.atomic
def apply_product_matches_to_historical_import(historical_import, actor=None):
    historical_import = (
        HistoricalPriceImport.objects.select_for_update()
        .select_related("company")
        .get(pk=historical_import.pk)
    )
    if historical_import.status in {
        HistoricalPriceImport.STATUS_COMMITTED,
        HistoricalPriceImport.STATUS_CANCELLED,
    }:
        return historical_import

    updated_line_ids = []
    auto_match_prefixes = (
        "Matched exact product name.",
        "Matched normalized product name.",
        "Matched global alias",
        "Found one conservative",
        "No safe deterministic",
        "No matching product",
    )
    candidate_lines = historical_import.lines.select_for_update().exclude(
        status__in=[
            HistoricalPriceImportLine.STATUS_COMMITTED,
            HistoricalPriceImportLine.STATUS_DUPLICATE,
            HistoricalPriceImportLine.STATUS_SKIPPED,
        ]
    ).order_by("sort_order", "id")
    for line in candidate_lines:
        before_status = line.status
        match = suggest_product_for_text(line.item_name, historical_import.company)
        if not match.product:
            continue
        can_override = (
            not line.product_id
            or (
                match.method == "company_alias"
                and line.product_id != match.product.id
                and (not line.match_reason or line.match_reason.startswith(auto_match_prefixes))
            )
        )
        if not can_override:
            continue
        line.product = match.product
        line.match_reason = match.reason
        if match.confidence >= 0.88 and not _historical_ready_errors(historical_import, line):
            line.status = HistoricalPriceImportLine.STATUS_READY
        line.save(update_fields=["product", "match_reason", "status", "updated_at"])
        if line.product_id or line.status != before_status:
            updated_line_ids.append(line.id)

    if updated_line_ids:
        audit_log(
            actor,
            QuotationAuditLog.ACTION_UPDATED,
            historical_import,
            message=f"Applied product matching to {len(updated_line_ids)} historical import row(s).",
            changes={"line_ids": updated_line_ids},
            company=historical_import.company,
        )
    return historical_import


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
        if not line.product_id and not line.quote_item_id:
            raise ValidationError(f"Line '{line.item_name}' must be linked to a product/item before committing.")
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
            product=line.product,
            item_name_snapshot=line.product.name if line.product_id else line.quote_item.name,
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
            product=line.product,
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
            unit_price=line_data.get("unit_price"),
            vat_rate=line_data.get("vat_rate") or Decimal("0.00"),
            notes=line_data.get("notes", ""),
            matched_quote_item=line_data.get("matched_quote_item"),
            matched_product=line_data.get("matched_product"),
            match_reason=line_data.get("match_reason", ""),
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
def remember_inquiry_line_alias(line, actor):
    line = InquiryLine.objects.select_for_update().select_related("inquiry__company").get(pk=line.pk)
    if not line.matched_product_id:
        raise ValidationError("Select a product before remembering this alias.")
    alias, created = create_product_alias(
        alias_text=line.raw_name,
        product=line.matched_product,
        company=line.inquiry.company,
        actor=actor,
        notes=f"Remembered from inquiry line {line.pk}.",
    )
    line.match_reason = f"Matched company alias '{alias.alias}'."
    line.save(update_fields=["match_reason", "updated_at"])
    audit_log(
        actor,
        QuotationAuditLog.ACTION_UPDATED,
        alias,
        message=("Created" if created else "Updated") + " product alias from inquiry line.",
        company=line.inquiry.company,
    )
    return alias


@transaction.atomic
def remember_historical_import_line_alias(line, actor):
    line = (
        HistoricalPriceImportLine.objects.select_for_update()
        .select_related("historical_import__company")
        .get(pk=line.pk)
    )
    if not line.historical_import.company_id:
        raise ValidationError("Select the company before remembering this alias.")
    if not line.product_id:
        raise ValidationError("Select a product before remembering this alias.")
    alias, created = create_product_alias(
        alias_text=line.item_name,
        product=line.product,
        company=line.historical_import.company,
        actor=actor,
        notes=f"Remembered from historical import line {line.pk}.",
    )
    line.match_reason = f"Matched company alias '{alias.alias}'."
    line.save(update_fields=["match_reason", "updated_at"])
    audit_log(
        actor,
        QuotationAuditLog.ACTION_UPDATED,
        alias,
        message=("Created" if created else "Updated") + " product alias from historical import line.",
        company=line.historical_import.company,
    )
    return alias


@transaction.atomic
def remember_quotation_line_alias(line, actor):
    line = QuotationLine.objects.select_for_update().select_related("quotation__company").get(pk=line.pk)
    if not line.product_id:
        raise ValidationError("Select a product before remembering this alias.")
    alias_text = line.inquiry_line.raw_name if line.inquiry_line_id else line.item_name_snapshot
    alias, created = create_product_alias(
        alias_text=alias_text,
        product=line.product,
        company=line.quotation.company,
        actor=actor,
        notes=f"Remembered from quotation line {line.pk}.",
    )
    line.match_reason = f"Matched company alias '{alias.alias}'."
    line.save(update_fields=["match_reason", "updated_at"])
    audit_log(
        actor,
        QuotationAuditLog.ACTION_UPDATED,
        alias,
        message=("Created" if created else "Updated") + " product alias from quotation line.",
        company=line.quotation.company,
        quotation=line.quotation,
    )
    return alias


@transaction.atomic
def create_product_from_quotation_line(line, actor, product_name=""):
    line = (
        QuotationLine.objects.select_for_update()
        .select_related("quotation__company")
        .get(pk=line.pk)
    )
    ensure_quotation_editable(line.quotation)
    product, created = _get_or_create_internal_product_from_line(line, actor, product_name)
    line = _link_quotation_line_to_product(
        line,
        product,
        actor,
        reason="Created draft/internal Product from quotation line." if created else "Linked to existing Product with the same name.",
    )
    recalculate_quotation_totals(line.quotation)
    return line, product, created


@transaction.atomic
def bulk_create_products_from_quotation_lines(quotation, line_ids, actor, names_by_id=None):
    quotation = Quotation.objects.select_for_update().select_related("company").get(pk=quotation.pk)
    ensure_quotation_editable(quotation)
    names_by_id = {int(key): value for key, value in (names_by_id or {}).items() if str(key).isdigit()}
    lines = list(
        quotation.lines.select_for_update()
        .select_related("quotation__company")
        .filter(id__in=line_ids)
        .order_by("sort_order", "id")
    )
    if not lines:
        raise ValidationError(f"No selected quotation lines were found for this quotation. Selected ids: {line_ids or 'none'}.")

    products_by_key = {}
    updated_lines = []
    created_count = 0
    reused_count = 0
    for line in lines:
        product_name = _product_name_from_line(line, names_by_id.get(line.id, ""))
        key = normalize_label(product_name)
        if key in products_by_key:
            product, created = products_by_key[key]
        else:
            product, created = _get_or_create_internal_product_from_line(line, actor, product_name)
            products_by_key[key] = (product, created)
            if created:
                created_count += 1
            else:
                reused_count += 1
        updated_lines.append(
            _link_quotation_line_to_product(
                line,
                product,
                actor,
                reason="Bulk created/linked draft/internal Product from quotation line." if created else "Bulk linked to existing Product with the same name.",
            )
        )
    recalculate_quotation_totals(quotation)
    return {
        "updated_lines": updated_lines,
        "created_products": created_count,
        "reused_products": reused_count,
        "unique_products": len(products_by_key),
    }


@transaction.atomic
def bulk_update_quotation_lines(quotation, rows, actor):
    quotation = Quotation.objects.select_for_update().get(pk=quotation.pk)
    ensure_quotation_editable(quotation)
    rows_by_id = {}
    for row in rows or []:
        try:
            rows_by_id[int(row.get("id"))] = row
        except (TypeError, ValueError):
            raise ValidationError("Each line update must include a valid id.")
    if not rows_by_id:
        raise ValidationError("No line changes were provided.")

    lines = {
        line.id: line
        for line in quotation.lines.select_for_update().filter(id__in=rows_by_id.keys())
    }
    updated = []
    allowed_fields = {
        "product",
        "quote_item",
        "product_image",
        "include_product_image",
        "item_name_snapshot",
        "description",
        "quantity",
        "unit",
        "unit_price",
        "vat_rate",
        "match_status",
        "notes",
    }

    def decimal_value(value, label, *, allow_null=False):
        if value in ("", None):
            if allow_null:
                return None
            raise ValidationError(f"{label} is required.")
        try:
            return Decimal(str(value))
        except Exception as exc:
            raise ValidationError(f"{label} must be a valid number.") from exc

    for line_id, payload in rows_by_id.items():
        line = lines.get(line_id)
        if not line:
            raise ValidationError(f"Line {line_id} does not belong to this quotation.")
        for field in allowed_fields:
            if field not in payload:
                continue
            value = payload[field]
            if field in {"product", "quote_item", "product_image"}:
                setattr(line, f"{field}_id", value or None)
            elif field == "include_product_image":
                if isinstance(value, str):
                    line.include_product_image = value.strip().lower() in {"1", "true", "yes", "on"}
                else:
                    line.include_product_image = bool(value)
            elif field == "quantity":
                line.quantity = decimal_value(value, "Quantity")
            elif field == "unit_price":
                line.unit_price = decimal_value(value, "Unit price", allow_null=True)
            elif field == "vat_rate":
                vat_rate = decimal_value(value, "VAT")
                if vat_rate not in {Decimal("0"), Decimal("0.00"), Decimal("5"), Decimal("5.00")}:
                    raise ValidationError("VAT must be 0% or 5% in the quotation line review workflow.")
                line.vat_rate = vat_rate
            else:
                setattr(line, field, value if value != "" else "")
        if line.product_image_id:
            image_product_id = ProductImage.objects.filter(pk=line.product_image_id).values_list("product_id", flat=True).first()
            if not image_product_id:
                raise ValidationError(f"Selected Product image for line {line_id} was not found.")
            if line.product_id and image_product_id != line.product_id:
                raise ValidationError(f"Selected Product image for line {line_id} does not belong to the matched Product.")
        if line.include_product_image and not line.product_image_id and line.product_id:
            primary_image = Product.objects.get(pk=line.product_id).primary_image
            if primary_image:
                line.product_image = primary_image
        if line.product_id and line.match_status == QuotationLine.MATCH_UNRESOLVED:
            line.match_status = QuotationLine.MATCH_CONFIRMED
        if not line.product_id and not line.quote_item_id and line.match_status == QuotationLine.MATCH_CONFIRMED:
            line.match_status = QuotationLine.MATCH_UNRESOLVED
        line.save()
        updated.append(line)
    recalculate_quotation_totals(quotation)
    audit_log(
        actor,
        QuotationAuditLog.ACTION_UPDATED,
        quotation,
        message=f"Saved {len(updated)} quotation line(s).",
    )
    return quotation, updated


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

    for index, line in enumerate(inquiry.lines.select_related("matched_quote_item", "matched_product").order_by("sort_order", "id")):
        quote_item = line.matched_quote_item
        product = line.matched_product
        item_name = product.name if product else quote_item.name if quote_item else line.raw_name
        QuotationLine.objects.create(
            quotation=quotation,
            inquiry_line=line,
            quote_item=quote_item,
            product=product,
            match_reason=line.match_reason,
            item_name_snapshot=item_name,
            quantity=line.quantity or Decimal("1.000"),
            unit=line.unit,
            unit_price=line.unit_price,
            vat_rate=line.vat_rate,
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
    if not line.product_id and not line.quote_item_id:
        raise ValidationError(f"Line '{line.item_name_snapshot}' must be linked to a product/item.")
    if line.quantity is None or line.quantity <= 0:
        raise ValidationError(f"Line '{line.item_name_snapshot}' must have a valid quantity.")
    if line.unit_price is None or line.unit_price <= 0:
        raise ValidationError(f"Line '{line.item_name_snapshot}' must have a valid unit price.")


@transaction.atomic
def finalize_quotation(quotation, actor):
    quotation = (
        Quotation.objects.select_for_update()
        .select_related("company")
        .prefetch_related("lines__quote_item", "lines__product")
        .get(pk=quotation.pk)
    )
    if quotation.status not in {
        Quotation.STATUS_DRAFT,
        Quotation.STATUS_PENDING_REVIEW,
        Quotation.STATUS_APPROVED,
    }:
        raise ValidationError("Only draft, pending review, or approved quotations can be finalized.")

    lines = list(quotation.lines.select_related("quote_item", "product").order_by("sort_order", "id"))
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
                "product": line.product,
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
        payment_terms=source.payment_terms,
        notes=source.notes,
        internal_notes=source.internal_notes,
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
    )
    for line in source.lines.order_by("sort_order", "id"):
        QuotationLine.objects.create(
            quotation=revision,
            inquiry_line=line.inquiry_line,
            quote_item=line.quote_item,
            product=line.product,
            match_reason=line.match_reason,
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
