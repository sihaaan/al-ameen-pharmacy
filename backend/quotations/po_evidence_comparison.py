"""Read-only commercial comparison for one quotation/PO evidence source.

The comparison deliberately reuses the guarded outcome matcher, but it never
updates quotation outcomes.  Values labelled as accepted come only from the
selected PO source; quotation fallbacks used by outcome suggestions are not
reported as customer-accepted quantities or prices.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from .import_parsers import parse_text_preview
from .mailbox_po_reconciliation import document_variants
from .models import QuotationLine, QuotationOutcomePOImport, normalize_label
from .services import build_po_outcome_suggestions


MATERIAL_WARNING_TERMS = (
    "aggregate",
    "ambiguous",
    "arithmetic",
    "could not",
    "failed",
    "fallback",
    "incomplete",
    "manual review",
    "no clear header",
    "no commercial",
    "no item",
    "not parsed",
    "ocr",
    "requires staff",
    "stopped",
    "total",
    "unsupported",
)
SAFE_UNMATCHED_REASON_CODES = {
    "non_item_metadata",
    "numeric_only_metadata",
}

logger = logging.getLogger(__name__)
AMBIGUOUS_DOCUMENT_VARIANT = object()
AMBIGUOUS_STORED_ATTACHMENT = object()
LATEST_PO_IMPORT_UNSET = object()


def latest_relevant_po_import(evidence):
    """Return the newest successful import for this exact evidence/quote pair."""

    cached = getattr(evidence, "_latest_relevant_po_import_cache", LATEST_PO_IMPORT_UNSET)
    if cached is not LATEST_PO_IMPORT_UNSET:
        return cached

    prefetched = getattr(evidence, "_prefetched_objects_cache", {}).get("po_imports")
    if prefetched is not None:
        candidates = [
            po_import
            for po_import in prefetched
            if po_import.quotation_id == evidence.quotation_id
            and po_import.status == QuotationOutcomePOImport.STATUS_PARSED
        ]
        latest = max(
            candidates,
            key=lambda po_import: (po_import.created_at, po_import.pk),
            default=None,
        )
        evidence._latest_relevant_po_import_cache = latest
        return latest

    latest = (
        QuotationOutcomePOImport.objects.select_related(
            "created_by",
            "gmail_evidence__canonical_lpo",
        ).filter(
            gmail_evidence_id=evidence.pk,
            quotation_id=evidence.quotation_id,
            status=QuotationOutcomePOImport.STATUS_PARSED,
        )
        .order_by("-created_at", "-id")
        .first()
    )
    evidence._latest_relevant_po_import_cache = latest
    return latest


def _decimal(value):
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _decimal_text(value):
    parsed = _decimal(value)
    if parsed is None:
        return None
    return format(parsed, "f")


def _confidence(value):
    parsed = _decimal(value)
    if parsed is None:
        return 0.0
    return max(0.0, min(float(parsed), 100.0))


def _row_name(row):
    if not isinstance(row, dict):
        return ""
    return str(
        row.get("name")
        or row.get("item_name")
        or row.get("raw_name")
        or row.get("requested_item_name")
        or row.get("product_name")
        or ""
    ).strip()


def _canonical_row(row, index):
    if not isinstance(row, dict):
        return {}
    return {
        **row,
        "line_id": row.get("line_id") or row.get("row_number") or index,
        "raw_name": _row_name(row),
        "description": str(row.get("description") or row.get("notes") or ""),
        "quantity": _decimal_text(
            row.get("quantity") if row.get("quantity") is not None else row.get("qty")
        ),
        "unit_price": _decimal_text(
            row.get("unit_price") if row.get("unit_price") is not None else row.get("price")
        ),
        "line_total": _decimal_text(
            row.get("line_total") if row.get("line_total") is not None else row.get("amount")
        ),
        "unit": str(row.get("unit") or row.get("uom") or ""),
    }


def _variant_rows(variant):
    return [
        {
            "line_id": row.line_id or index,
            "raw_name": row.name,
            "description": row.description,
            "quantity": _decimal_text(row.quantity),
            "unit_price": _decimal_text(row.unit_price),
            "line_total": _decimal_text(row.line_total),
            "unit": row.unit,
        }
        for index, row in enumerate(variant.message.parsed_rows, start=1)
    ]


def _attachment_identifiers(attachment):
    if not isinstance(attachment, dict):
        return set()
    return {
        str(value).strip()
        for value in (
            attachment.get("attachment_id"),
            attachment.get("source_gmail_attachment_id"),
            attachment.get("part_id"),
        )
        if str(value or "").strip()
    }


def _selected_attachment(evidence):
    selected_id = str(evidence.selected_attachment_id or "").strip()
    selected_filename = str(evidence.selected_attachment_filename or "").strip()
    source = ((evidence.match_signals or {}).get("source") or {})
    source_id = str(source.get("attachment_id") or "").strip()
    source_filename = str(source.get("filename") or "").strip()
    attachments = [row for row in (evidence.attachments or []) if isinstance(row, dict)]
    if not attachments:
        return None

    def resolve_indexes(indexes):
        unique = set(indexes)
        if len(unique) == 1:
            return attachments[unique.pop()]
        if unique:
            return AMBIGUOUS_STORED_ATTACHMENT
        return None

    # A content hash is the strongest stored identity and is allowed to
    # disambiguate stale duplicate selection flags or filenames.
    selected_hash = str(evidence.source_sha256 or "").strip().lower()
    if selected_hash:
        resolved = resolve_indexes(
            index
            for index, attachment in enumerate(attachments)
            if str(attachment.get("source_sha256") or "").strip().lower()
            == selected_hash
        )
        if resolved is not None:
            return resolved

    identifier_indexes = []
    for identifier in dict.fromkeys(value for value in (selected_id, source_id) if value):
        matches = [
            index
            for index, attachment in enumerate(attachments)
            if identifier in _attachment_identifiers(attachment)
        ]
        if len(matches) > 1:
            return AMBIGUOUS_STORED_ATTACHMENT
        identifier_indexes.extend(matches)
    resolved = resolve_indexes(identifier_indexes)
    if resolved is not None:
        return resolved

    resolved = resolve_indexes(
        index
        for index, attachment in enumerate(attachments)
        if attachment.get("is_selected") is True
    )
    if resolved is not None:
        return resolved

    filename_indexes = []
    for filename in dict.fromkeys(
        value for value in (selected_filename, source_filename) if value
    ):
        matches = [
            index
            for index, attachment in enumerate(attachments)
            if str(attachment.get("filename") or "").strip() == filename
        ]
        if len(matches) > 1:
            return AMBIGUOUS_STORED_ATTACHMENT
        filename_indexes.extend(matches)
    resolved = resolve_indexes(filename_indexes)
    if resolved is not None:
        return resolved
    return None


def _selected_variant(evidence):
    inventory = evidence.mailbox_message
    if not inventory:
        return None
    variants = list(document_variants(inventory))
    if not variants:
        return None

    signals = evidence.match_signals or {}
    source = signals.get("source") or {}
    source_kind = str(source.get("kind") or "").strip()
    selected_id = str(
        evidence.selected_attachment_id or source.get("attachment_id") or ""
    ).strip()
    selected_filename = str(
        evidence.selected_attachment_filename or source.get("filename") or ""
    ).strip()
    selected_hash = str(evidence.source_sha256 or "").strip().lower()
    if selected_hash:
        hash_matches = [
            variant
            for variant in variants
            if str(variant.source_sha256 or "").strip().lower() == selected_hash
        ]
        if len(hash_matches) == 1:
            return hash_matches[0]
        if len(hash_matches) > 1:
            return AMBIGUOUS_DOCUMENT_VARIANT
    attachment = _selected_attachment(evidence)
    if attachment is AMBIGUOUS_STORED_ATTACHMENT:
        return AMBIGUOUS_DOCUMENT_VARIANT
    attachment_ids = _attachment_identifiers(attachment)
    attachment_hash = str((attachment or {}).get("source_sha256") or "").strip().lower()

    def score(variant):
        value = 0
        if source_kind and variant.source_kind == source_kind:
            value += 20
        if selected_hash and str(variant.source_sha256 or "").lower() == selected_hash:
            value += 200
        if attachment_hash and str(variant.source_sha256 or "").lower() == attachment_hash:
            value += 180
        if selected_id and variant.attachment_id == selected_id:
            value += 160
        if variant.attachment_id and variant.attachment_id in attachment_ids:
            value += 140
        if selected_filename and variant.filename == selected_filename:
            value += 80
        return value

    ranked = sorted(variants, key=score, reverse=True)
    best_score = score(ranked[0])
    if best_score > 0:
        best = [variant for variant in ranked if score(variant) == best_score]
        # Never borrow rows from the first of several equally plausible
        # sources. Evidence review must retain exact attachment provenance.
        return best[0] if len(best) == 1 else AMBIGUOUS_DOCUMENT_VARIANT
    if len(variants) == 1:
        return variants[0]
    return AMBIGUOUS_DOCUMENT_VARIANT


def _total_from_mapping(value):
    if not isinstance(value, dict):
        return None
    for key in (
        "grand_total",
        "document_total",
        "net_total",
        "total_amount",
        "total",
    ):
        parsed = _decimal(value.get(key))
        if parsed is not None:
            return parsed
    totals = value.get("totals")
    if isinstance(totals, dict):
        return _total_from_mapping(totals)
    return None


def _lpo_reference(evidence, variant=None, canonical_lpo=None):
    if canonical_lpo and canonical_lpo.lpo_number:
        return canonical_lpo.lpo_number
    signals = evidence.match_signals or {}
    references = signals.get("lpo_references") or []
    if len(references) == 1:
        return str(references[0])[:120]
    if (
        variant is not None
        and variant is not AMBIGUOUS_DOCUMENT_VARIANT
        and len(variant.lpo_references) == 1
    ):
        return str(variant.lpo_references[0])[:120]
    return ""


def _warning_is_material(warning):
    lowered = str(warning or "").casefold()
    return any(term in lowered for term in MATERIAL_WARNING_TERMS)


def _money_result(left, right):
    left_value = _decimal(left)
    right_value = _decimal(right)
    if left_value is None or right_value is None:
        return "unknown"
    return "exact" if abs(left_value - right_value) <= Decimal("0.02") else "conflict"


def _quantity_result(accepted, quoted):
    accepted_value = _decimal(accepted)
    quoted_value = _decimal(quoted)
    if accepted_value is None or quoted_value is None:
        return "unknown"
    tolerance = max(Decimal("0.001"), abs(quoted_value) * Decimal("0.005"))
    if abs(accepted_value - quoted_value) <= tolerance:
        return "exact"
    if accepted_value > 0 and quoted_value > 0 and accepted_value < quoted_value:
        return "reduced"
    return "conflict"


def _units_conflict(accepted, quoted):
    accepted_unit = normalize_label(accepted)
    quoted_unit = normalize_label(quoted)
    if not accepted_unit or not quoted_unit:
        return False
    aliases = {
        "bottles": "bottle",
        "boxes": "box",
        "ea": "piece",
        "each": "piece",
        "no": "piece",
        "nos": "piece",
        "packet": "pack",
        "packets": "pack",
        "packs": "pack",
        "pairs": "pair",
        "pc": "piece",
        "pcs": "piece",
        "pieces": "piece",
        "pkt": "pack",
        "pkts": "pack",
        "rolls": "roll",
        "strips": "strip",
        "tubes": "tube",
        "unit": "piece",
        "units": "piece",
    }
    return aliases.get(accepted_unit, accepted_unit) != aliases.get(quoted_unit, quoted_unit)


def _commercial_status(*, confidence, accepted_quantity, quoted_quantity, accepted_price, quoted_price, accepted_unit, quoted_unit):
    if confidence < 85:
        return "uncertain", "Item match needs staff confirmation because its confidence is below 85%."
    quantity_result = _quantity_result(accepted_quantity, quoted_quantity)
    if quantity_result == "unknown":
        return "uncertain", "The LPO does not state a usable accepted quantity."
    if quantity_result == "conflict":
        return "uncertain", "The LPO quantity is higher than or conflicts with the quoted quantity."
    if _units_conflict(accepted_unit, quoted_unit):
        return "uncertain", "The LPO unit conflicts with the quotation unit."
    if _decimal(accepted_price) is None:
        if quantity_result == "reduced":
            return (
                "reduced_price_not_stated",
                "The LPO accepts a lower quantity, but does not state a usable unit price.",
            )
        return (
            "accepted_price_not_stated",
            "The LPO accepts the quoted quantity, but does not state a usable unit price.",
        )
    if _decimal(quoted_price) is None:
        return "uncertain", "The quotation does not contain a usable unit price for comparison."
    price_result = _money_result(accepted_price, quoted_price)
    if quantity_result == "reduced" and price_result == "conflict":
        return "reduced_repriced", "The LPO accepts a lower quantity at a different unit price."
    if quantity_result == "reduced":
        return "reduced", "The LPO accepts a lower quantity at the quoted unit price."
    if price_result == "conflict":
        return "repriced", "The LPO quantity matches, but the accepted unit price differs."
    return "accepted", "The parsed LPO quantity and unit price match this quotation line."


def _parsed_source(evidence, variant, latest_import, canonical_lpo):
    """Return rows/suggestions/warnings without mutating evidence or outcomes."""

    if latest_import and latest_import.status == latest_import.STATUS_PARSED:
        rows = [
            _canonical_row(row, index)
            for index, row in enumerate(latest_import.parsed_rows or [], start=1)
            if isinstance(row, dict)
        ]
        suggestions = [
            value
            for value in (latest_import.suggestions or [])
            if isinstance(value, dict) and not value.get("provenance_only_after_reparse")
        ]
        unmatched = [
            value for value in (latest_import.unmatched_po_rows or []) if isinstance(value, dict)
        ]
        warnings = [str(value) for value in (latest_import.warnings or []) if str(value).strip()]
        return {
            "rows": rows,
            "suggestions": suggestions,
            "unmatched": unmatched,
            "missing_line_ids": list(latest_import.missing_quote_line_ids or []),
            "warnings": warnings,
            "source_kind": "attachment" if evidence.selected_attachment_id else "email_body",
            "source_filename": latest_import.source_filename or evidence.selected_attachment_filename,
            "parse_source": "approved_po_import",
        }

    if variant is AMBIGUOUS_DOCUMENT_VARIANT:
        return {
            "rows": [],
            "suggestions": [],
            "unmatched": [],
            "missing_line_ids": [line.id for line in evidence.quotation.lines.all()],
            "warnings": [
                "The exact LPO source is ambiguous between multiple attachments; "
                "review the source document manually."
            ],
            "source_kind": str(
                ((evidence.match_signals or {}).get("source") or {}).get("kind") or ""
            ),
            "source_filename": evidence.selected_attachment_filename,
            "parse_source": "unavailable",
        }

    if variant:
        rows = _variant_rows(variant)
        warnings = list(
            dict.fromkeys(
                str(value)
                for value in (
                    *(variant.message.parser_warnings or ()),
                    *(variant.message.material_warnings or ()),
                )
                if str(value).strip()
            )
        )
        preview = {
            "lines": rows,
            "warnings": warnings,
            "meta": {
                "aggregate_po_summary_detected": any(
                    "aggregate" in str(value).casefold()
                    for value in (variant.message.material_warnings or [])
                )
            },
        }
        suggestions, unmatched, missing = build_po_outcome_suggestions(
            evidence.quotation,
            preview,
        )
        return {
            "rows": rows,
            "suggestions": suggestions,
            "unmatched": unmatched,
            "missing_line_ids": missing,
            "warnings": warnings,
            "source_kind": variant.source_kind,
            "source_filename": variant.filename,
            "parse_source": "mailbox_deterministic",
        }

    attachment = _selected_attachment(evidence)
    if attachment is AMBIGUOUS_STORED_ATTACHMENT:
        return {
            "rows": [],
            "suggestions": [],
            "unmatched": [],
            "missing_line_ids": [line.id for line in evidence.quotation.lines.all()],
            "warnings": [
                "The stored evidence identifies more than one possible LPO attachment; "
                "review the source document manually."
            ],
            "source_kind": "attachment",
            "source_filename": evidence.selected_attachment_filename,
            "parse_source": "unavailable",
        }
    if attachment:
        rows = [
            _canonical_row(row, index)
            for index, row in enumerate(attachment.get("lines") or [], start=1)
            if isinstance(row, dict)
        ]
        raw_warnings = attachment.get("warnings") or []
        if not isinstance(raw_warnings, (list, tuple, set)):
            raw_warnings = [raw_warnings]
        material_warnings = attachment.get("material_warnings") or []
        if not isinstance(material_warnings, (list, tuple, set)):
            material_warnings = [material_warnings]
        warnings = list(
            dict.fromkeys(
                str(value)
                for value in (
                    *raw_warnings,
                    *material_warnings,
                    *([attachment.get("reason")] if attachment.get("reason") else []),
                )
                if str(value).strip()
            )
        )
        preview = {"lines": rows, "warnings": warnings, "meta": attachment.get("meta") or {}}
        suggestions, unmatched, missing = build_po_outcome_suggestions(evidence.quotation, preview)
        return {
            "rows": rows,
            "suggestions": suggestions,
            "unmatched": unmatched,
            "missing_line_ids": missing,
            "warnings": warnings,
            "source_kind": "attachment",
            "source_filename": str(attachment.get("filename") or ""),
            "parse_source": "stored_attachment",
        }

    source_kind = str(((evidence.match_signals or {}).get("source") or {}).get("kind") or "")
    if source_kind == "email_body" and str(evidence.extracted_text or "").strip():
        preview = parse_text_preview(evidence.extracted_text)
        rows = [
            _canonical_row(row, index)
            for index, row in enumerate(preview.get("lines") or [], start=1)
            if isinstance(row, dict)
        ]
        warnings = [str(value) for value in (preview.get("warnings") or []) if str(value).strip()]
        preview["lines"] = rows
        suggestions, unmatched, missing = build_po_outcome_suggestions(evidence.quotation, preview)
        return {
            "rows": rows,
            "suggestions": suggestions,
            "unmatched": unmatched,
            "missing_line_ids": missing,
            "warnings": warnings,
            "source_kind": "email_body",
            "source_filename": "",
            "parse_source": "stored_body_deterministic",
        }

    return {
        "rows": [],
        "suggestions": [],
        "unmatched": [],
        "missing_line_ids": [line.id for line in evidence.quotation.lines.all()],
        "warnings": ["No structured LPO rows are available for automatic comparison."],
        "source_kind": source_kind,
        "source_filename": evidence.selected_attachment_filename,
        "parse_source": "unavailable",
    }


def _suggestion_line_id(suggestion):
    raw = suggestion.get("quotation_line_id", suggestion.get("quotation_line"))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _row_value(row, *keys):
    if not isinstance(row, dict):
        return None
    for key in keys:
        if row.get(key) not in (None, ""):
            return row.get(key)
    return None


def _embedded_suggestion_row(suggestion):
    row = suggestion.get("po_row")
    embedded = dict(row) if isinstance(row, dict) else {}
    if _row_value(embedded, "quantity", "qty") is None and suggestion.get(
        "po_quantity"
    ) not in (None, ""):
        embedded["quantity"] = suggestion.get("po_quantity")
    if _row_value(embedded, "unit_price", "price") is None and suggestion.get(
        "po_unit_price"
    ) not in (None, ""):
        embedded["unit_price"] = suggestion.get("po_unit_price")
    if not _row_name(embedded) and suggestion.get("po_item_name"):
        embedded["raw_name"] = suggestion.get("po_item_name")
    return embedded


def _rows_disagree(authoritative, embedded):
    if not embedded:
        return ""
    disagreements = []
    for label, keys, tolerance in (
        ("quantity", ("quantity", "qty"), Decimal("0.001")),
        ("unit price", ("unit_price", "price"), Decimal("0.01")),
        ("line total", ("line_total", "amount"), Decimal("0.01")),
    ):
        embedded_raw = _row_value(embedded, *keys)
        if embedded_raw is None:
            continue
        embedded_value = _decimal(embedded_raw)
        authoritative_value = _decimal(_row_value(authoritative, *keys))
        if (
            embedded_value is None
            or authoritative_value is None
            or abs(embedded_value - authoritative_value) > tolerance
        ):
            disagreements.append(label)

    embedded_unit = str(_row_value(embedded, "unit", "uom") or "").strip()
    authoritative_unit = str(_row_value(authoritative, "unit", "uom") or "").strip()
    if embedded_unit and (
        not authoritative_unit or _units_conflict(embedded_unit, authoritative_unit)
    ):
        disagreements.append("unit")

    embedded_name = _row_name(embedded)
    authoritative_name = _row_name(authoritative)
    if embedded_name and (
        not authoritative_name
        or normalize_label(embedded_name) != normalize_label(authoritative_name)
    ):
        disagreements.append("item name")

    if not disagreements:
        return ""
    return (
        "The stored match suggestion disagrees with the authoritative parsed LPO row "
        f"for {', '.join(disagreements)}; staff confirmation is required."
    )


def _suggestion_row(suggestion, rows_by_number):
    embedded = _embedded_suggestion_row(suggestion)
    raw_number = suggestion.get("po_row_number", suggestion.get("po_row_index"))
    if raw_number in (None, ""):
        raw_number = _row_value(embedded, "row_number", "line_id")
    try:
        row_number = int(raw_number)
    except (TypeError, ValueError):
        return {}, "The stored match suggestion has no valid parsed LPO row number."
    authoritative = rows_by_number.get(row_number)
    if not authoritative:
        return {}, "The stored match suggestion does not resolve to a parsed LPO row."
    return authoritative, _rows_disagree(authoritative, embedded)


def _comparison_line(line, suggestion, rows_by_number, *, complete_for_missing_lines):
    if not suggestion:
        if line.match_status == QuotationLine.MATCH_IGNORED:
            status = "uncertain"
            reason = "This quotation line is excluded from automatic item matching."
        elif complete_for_missing_lines:
            status = "not_ordered"
            reason = (
                "No matching row appears on this parsed LPO. This means not ordered on this LPO; "
                "it is not an explicit customer rejection."
            )
        else:
            status = "uncertain"
            reason = "A complete LPO row comparison is unavailable, so absence cannot be treated as not ordered."
        return {
            "quotation_line_id": line.id,
            "quote_item_name": line.item_name_snapshot,
            "lpo_item_name": "",
            "quoted_quantity": _decimal_text(line.quantity),
            "accepted_quantity": None,
            "quoted_unit": line.unit,
            "accepted_unit": "",
            "quoted_unit_price": _decimal_text(line.unit_price),
            "accepted_unit_price": None,
            "quoted_line_subtotal": _decimal_text(line.line_subtotal),
            "quoted_vat_amount": _decimal_text(line.vat_amount),
            "quoted_line_total": _decimal_text(line.line_total),
            "accepted_line_total": None,
            "accepted_line_total_derived": False,
            "accepted_arithmetic_result": "not_checkable",
            "status": status,
            "not_on_lpo": status == "not_ordered",
            "confidence": None,
            "reason": reason,
            "review_required": status == "uncertain",
        }

    row, row_disagreement = _suggestion_row(suggestion, rows_by_number)
    accepted_quantity = _decimal(
        row.get("quantity") if row.get("quantity") is not None else row.get("qty")
    )
    accepted_price = _decimal(
        row.get("unit_price") if row.get("unit_price") is not None else row.get("price")
    )
    accepted_total = _decimal(
        row.get("line_total") if row.get("line_total") is not None else row.get("amount")
    )
    accepted_total_derived = False
    arithmetic_result = "not_checkable"
    if accepted_total is None and accepted_quantity is not None and accepted_price is not None:
        accepted_total = accepted_quantity * accepted_price
        accepted_total_derived = True
        arithmetic_result = "derived"
    elif accepted_total is not None and accepted_quantity is not None and accepted_price is not None:
        arithmetic_result = (
            "exact"
            if abs((accepted_quantity * accepted_price) - accepted_total)
            <= Decimal("0.01")
            else "conflict"
        )
    confidence = _confidence(suggestion.get("confidence"))
    status, reason = _commercial_status(
        confidence=confidence,
        accepted_quantity=accepted_quantity,
        quoted_quantity=line.quantity,
        accepted_price=accepted_price,
        quoted_price=line.unit_price,
        accepted_unit=str(row.get("unit") or row.get("uom") or ""),
        quoted_unit=line.unit,
    )
    if row_disagreement:
        status = "uncertain"
        reason = row_disagreement
    elif arithmetic_result == "conflict":
        status = "uncertain"
        reason = (
            "The parsed LPO arithmetic conflicts: accepted quantity multiplied by unit price "
            "does not equal the stated line total."
        )
    return {
        "quotation_line_id": line.id,
        "quote_item_name": line.item_name_snapshot,
        "lpo_item_name": _row_name(row) or str(suggestion.get("po_item_name") or ""),
        "quoted_quantity": _decimal_text(line.quantity),
        "accepted_quantity": _decimal_text(accepted_quantity),
        "quoted_unit": line.unit,
        "accepted_unit": str(row.get("unit") or row.get("uom") or ""),
        "quoted_unit_price": _decimal_text(line.unit_price),
        "accepted_unit_price": _decimal_text(accepted_price),
        "quoted_line_subtotal": _decimal_text(line.line_subtotal),
        "quoted_vat_amount": _decimal_text(line.vat_amount),
        "quoted_line_total": _decimal_text(line.line_total),
        "accepted_line_total": _decimal_text(accepted_total),
        "accepted_line_total_derived": accepted_total_derived,
        "accepted_arithmetic_result": arithmetic_result,
        "status": status,
        "not_on_lpo": False,
        "confidence": round(confidence),
        "reason": reason,
        "review_required": status in {
            "uncertain",
            "accepted_price_not_stated",
            "reduced_price_not_stated",
        },
    }


def _unmatched_rows(unmatched, rows_by_number):
    results = []
    for value in unmatched:
        if not isinstance(value, dict):
            continue
        raw_number = value.get("po_row_number", value.get("po_row_index"))
        try:
            row_number = int(raw_number)
        except (TypeError, ValueError):
            row_number = None
        row = rows_by_number.get(row_number, {}) if row_number is not None else {}
        results.append(
            {
                "po_row_number": row_number,
                "lpo_item_name": _row_name(row) or str(value.get("po_item_name") or ""),
                "accepted_quantity": _decimal_text(
                    row.get("quantity") if row.get("quantity") is not None else row.get("qty")
                ),
                "accepted_unit": str(row.get("unit") or row.get("uom") or ""),
                "accepted_unit_price": _decimal_text(
                    row.get("unit_price") if row.get("unit_price") is not None else row.get("price")
                ),
                "accepted_line_total": _decimal_text(
                    row.get("line_total") if row.get("line_total") is not None else row.get("amount")
                ),
                "reason_code": str(value.get("reason_code") or "unmatched"),
                "reason": str(value.get("reason") or "This LPO row was not matched to a quotation line."),
                "review_required": True,
            }
        )
    return results


def build_po_evidence_commercial_comparison(evidence):
    """Build a bounded, deterministic, read-only commercial comparison."""

    quotation = evidence.quotation
    quote_lines = list(quotation.lines.all())
    variant = _selected_variant(evidence)
    latest_import = latest_relevant_po_import(evidence)
    try:
        canonical_lpo = evidence.canonical_lpo
    except Exception:
        canonical_lpo = None

    parsed = _parsed_source(evidence, variant, latest_import, canonical_lpo)
    rows = parsed["rows"]
    rows_by_number = {index: row for index, row in enumerate(rows, start=1)}
    for index, row in enumerate(rows, start=1):
        raw_number = row.get("row_number") or row.get("line_id") or index
        try:
            rows_by_number.setdefault(int(raw_number), row)
        except (TypeError, ValueError):
            pass

    warnings = list(parsed["warnings"])
    if variant is not None and variant is not AMBIGUOUS_DOCUMENT_VARIANT:
        warnings.extend(
            str(value)
            for value in (variant.message.material_warnings or ())
            if str(value).strip()
        )
    candidate = ((evidence.match_signals or {}).get("candidate") or {})
    warnings.extend(
        str(value)
        for value in (candidate.get("material_warnings") or [])
        if str(value).strip()
    )
    warnings = list(dict.fromkeys(warnings))[:30]
    unsafe_unmatched = [
        value
        for value in parsed["unmatched"]
        if str(value.get("reason_code") or "") not in SAFE_UNMATCHED_REASON_CODES
    ]
    safe_unmatched = [
        value
        for value in parsed["unmatched"]
        if str(value.get("reason_code") or "") in SAFE_UNMATCHED_REASON_CODES
    ]
    suggestion_line_ids = [
        line_id
        for value in parsed["suggestions"]
        if (line_id := _suggestion_line_id(value)) is not None
    ]
    eligible_quote_line_ids = {
        line.id
        for line in quote_lines
        if line.match_status != QuotationLine.MATCH_IGNORED
    }
    raw_missing_line_ids = list(parsed.get("missing_line_ids") or [])
    missing_line_ids = []
    missing_line_ids_valid = True
    for value in raw_missing_line_ids:
        try:
            missing_line_ids.append(int(value))
        except (TypeError, ValueError):
            missing_line_ids_valid = False
    suggestion_rows_reconcile = all(
        bool(row) and not disagreement
        for row, disagreement in (
            _suggestion_row(value, rows_by_number)
            for value in parsed["suggestions"]
        )
    )
    suggestion_ids_reconcile = bool(suggestion_line_ids) and (
        len(suggestion_line_ids) == len(parsed["suggestions"])
        and len(suggestion_line_ids) == len(set(suggestion_line_ids))
        and set(suggestion_line_ids).issubset(eligible_quote_line_ids)
    )
    missing_ids_reconcile = (
        missing_line_ids_valid
        and len(missing_line_ids) == len(set(missing_line_ids))
        and set(missing_line_ids).issubset(eligible_quote_line_ids)
        and set(missing_line_ids)
        == eligible_quote_line_ids.difference(suggestion_line_ids)
    )
    complete_for_missing_lines = bool(rows) and not any(
        _warning_is_material(value) for value in warnings
    ) and not unsafe_unmatched and (
        len(parsed["suggestions"]) + len(safe_unmatched) == len(rows)
    ) and suggestion_ids_reconcile and missing_ids_reconcile and suggestion_rows_reconcile and all(
        _confidence(value.get("confidence")) >= 85
        for value in parsed["suggestions"]
    )

    suggestions_by_line = {
        line_id: suggestion
        for suggestion in parsed["suggestions"]
        if (line_id := _suggestion_line_id(suggestion)) is not None
    }
    lines = [
        _comparison_line(
            line,
            suggestions_by_line.get(line.id),
            rows_by_number,
            complete_for_missing_lines=complete_for_missing_lines,
        )
        for line in quote_lines
    ]
    unmatched_rows = _unmatched_rows(parsed["unmatched"], rows_by_number)

    lpo_total = None
    if parsed["parse_source"] == "approved_po_import" and canonical_lpo:
        lpo_total = _total_from_mapping(canonical_lpo.parsed_meta or {})
    if (
        lpo_total is None
        and variant is not None
        and variant is not AMBIGUOUS_DOCUMENT_VARIANT
    ):
        lpo_total = variant.message.document_total
    if lpo_total is None and canonical_lpo:
        lpo_total = _total_from_mapping(canonical_lpo.parsed_meta or {})
    if lpo_total is None and parsed["parse_source"] != "unavailable":
        lpo_total = _total_from_mapping(_selected_attachment(evidence) or {})
    total_result = (
        str(candidate.get("document_total_result") or "unknown")
        if lpo_total is not None
        else "unknown"
    )
    total_basis = "unknown"
    if lpo_total is not None:
        if _money_result(lpo_total, quotation.total) == "exact":
            total_result = "exact"
            total_basis = "quotation_total_incl_vat"
        elif _money_result(lpo_total, quotation.subtotal) == "exact":
            total_result = "exact"
            total_basis = "quotation_subtotal_ex_vat"
        else:
            total_result = "conflict"
            total_basis = "conflict"

    summary = {
        "accepted_count": sum(
            line["status"] in {"accepted", "accepted_price_not_stated"}
            for line in lines
        ),
        "repriced_count": sum(line["status"] in {"repriced", "reduced_repriced"} for line in lines),
        "reduced_count": sum(
            line["status"] in {"reduced", "reduced_repriced", "reduced_price_not_stated"}
            for line in lines
        ),
        "price_not_stated_count": sum(
            line["status"] in {"accepted_price_not_stated", "reduced_price_not_stated"}
            for line in lines
        ),
        "not_ordered_count": sum(line["status"] == "not_ordered" for line in lines),
        "uncertain_count": sum(line["status"] == "uncertain" for line in lines),
        "unmatched_lpo_count": len(unmatched_rows),
    }
    return {
        "company_id": quotation.company_id,
        "company_name": quotation.company.name,
        "quotation_id": quotation.id,
        "quotation_number": quotation.quotation_number,
        "currency": quotation.currency,
        "quotation_subtotal": _decimal_text(quotation.subtotal),
        "quotation_vat_total": _decimal_text(quotation.vat_total),
        "quotation_total": _decimal_text(quotation.total),
        "lpo_number": _lpo_reference(evidence, variant, canonical_lpo),
        "lpo_total": _decimal_text(lpo_total),
        "total_result": total_result,
        "total_basis": total_basis,
        "source_kind": parsed["source_kind"],
        "source_filename": parsed["source_filename"],
        "parse_source": parsed["parse_source"],
        "review_only": True,
        "complete_for_missing_lines": complete_for_missing_lines,
        "warnings": warnings,
        "summary": summary,
        "lines": lines,
        "unmatched_lpo_rows": unmatched_rows,
    }


def unavailable_po_evidence_commercial_comparison(
    evidence,
    warning="Commercial line comparison is unavailable; review the source document manually.",
):
    """Return a fail-closed payload while keeping source evidence viewable."""

    quotation = evidence.quotation
    lines = [
        _comparison_line(
            line,
            None,
            {},
            complete_for_missing_lines=False,
        )
        for line in quotation.lines.all()
    ]
    return {
        "company_id": quotation.company_id,
        "company_name": quotation.company.name,
        "quotation_id": quotation.id,
        "quotation_number": quotation.quotation_number,
        "currency": quotation.currency,
        "quotation_subtotal": _decimal_text(quotation.subtotal),
        "quotation_vat_total": _decimal_text(quotation.vat_total),
        "quotation_total": _decimal_text(quotation.total),
        "lpo_number": _lpo_reference(evidence),
        "lpo_total": None,
        "total_result": "unknown",
        "total_basis": "unknown",
        "source_kind": str(
            ((evidence.match_signals or {}).get("source") or {}).get("kind") or ""
        ),
        "source_filename": evidence.selected_attachment_filename,
        "parse_source": "unavailable",
        "review_only": True,
        "complete_for_missing_lines": False,
        "warnings": [warning],
        "summary": {
            "accepted_count": 0,
            "repriced_count": 0,
            "reduced_count": 0,
            "price_not_stated_count": 0,
            "not_ordered_count": 0,
            "uncertain_count": len(lines),
            "unmatched_lpo_count": 0,
        },
        "lines": lines,
        "unmatched_lpo_rows": [],
    }


def safe_build_po_evidence_commercial_comparison(evidence):
    try:
        return build_po_evidence_commercial_comparison(evidence)
    except Exception:
        logger.exception(
            "Could not build commercial comparison for quotation PO evidence %s",
            evidence.pk,
        )
        return unavailable_po_evidence_commercial_comparison(evidence)
