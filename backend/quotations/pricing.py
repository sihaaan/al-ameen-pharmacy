"""Company selling-price recommendations and auditable staff corrections.

Customer acceptance is deliberately owned by the outcome workflow, never by
these draft-edit operations. Historical rows and issued documents are immutable.
"""
from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from .matching import identities_compatible, item_identity, normalize_item_text, product_identity
from .models import (CompanyPriceHistory, PriceRecommendationRetirement, ProductAlias,
                     QuotationLine, QuotationPriceFeedback)


def pricing_unit(value):
    # Equivalent spelling only; never infer a conversion between packs/pieces.
    normalized = normalize_item_text(value)
    return {"each": "piece", "ea": "piece", "pc": "piece", "unit": "piece", "units": "piece", "btl": "bottle", "btls": "bottle"}.get(normalized, normalized)


def _unavailable(reason, unit, currency):
    return {"eligible": False, "reason": reason, "unit": unit, "currency": currency}


def recommendation_context(quotation, product_ids):
    from api.models import Product
    related = dict(Product.objects.filter(canonical_product_id__in=product_ids).values_list("pk", "canonical_product_id"))
    source_ids = set(product_ids) | set(related)
    entries = defaultdict(list)
    for entry in (CompanyPriceHistory.objects.filter(company_id=quotation.company_id,
            product_id__in=source_ids).exclude(quotation_id=quotation.pk)
            .select_related("quotation", "quotation_line").order_by("-quoted_at", "-id")):
        entries[related.get(entry.product_id, entry.product_id)].append(entry)
    retirements = {(r.product_id, r.unit, r.currency): r.retired_before for r in
        PriceRecommendationRetirement.objects.filter(company_id=quotation.company_id, product_id__in=product_ids)}
    rejected = defaultdict(set)
    for row in QuotationPriceFeedback.objects.filter(company_id=quotation.company_id, kind="wrong_product", product_id__in=source_ids).values("product_id", "source_wording"):
        rejected[normalize_item_text(row["source_wording"])].add(related.get(row["product_id"], row["product_id"]))
    return entries, retirements, rejected


def recommend_price(quotation, product, unit, *, context=None, source_wording=""):
    currency = quotation.currency.upper()
    key = pricing_unit(unit)
    if not key:
        return _unavailable("Select a pricing unit", unit, currency)
    if product.identity_review_state != "verified":
        return _unavailable("Product identity needs catalogue review; enter a price manually", unit, currency)
    entries, retirements, rejected = context or recommendation_context(quotation, [product.pk])
    if source_wording and product.pk in rejected.get(normalize_item_text(source_wording), set()):
        return _unavailable("This wording has a recorded wrong-product correction; review and enter the price manually", unit, currency)
    history = entries.get(product.pk, [])
    if not history:
        return _unavailable("No previous company price", unit, currency)
    retired = retirements.get((product.pk, key, currency))
    eligible = []
    for entry in history:
        if entry.quotation.status not in {"finalized", "sent"}:
            continue
        if entry.currency.upper() != currency or pricing_unit(entry.unit) != key:
            continue
        if retired and entry.quoted_at <= retired:
            continue
        historical_brand = normalize_item_text(entry.quotation_line.brand_name_snapshot)
        current_brand = normalize_item_text(product.brand.name) if product.brand_id else ""
        if historical_brand and current_brand and historical_brand != current_brand:
            continue
        historical_identity = item_identity(entry.quotation_line.item_name_snapshot, unit=entry.unit)
        if not identities_compatible(historical_identity, product_identity(product)):
            continue
        if source_wording and not identities_compatible(item_identity(source_wording, unit=unit), historical_identity):
            continue
        if entry.quotation_line.price_review_required:
            continue
        eligible.append(entry)
    if not eligible:
        return _unavailable("No eligible price for this unit/currency; older or conflicting sources need review", unit, currency)
    accepted = [e for e in eligible if e.quotation_line.outcome_status in {"accepted", "quantity_changed"}
                and e.quotation_line.accepted_unit_price is not None
                and e.quotation_line.accepted_unit_price > 0]
    if accepted:
        entry = max(accepted, key=lambda e: (
            str(e.quotation.outcome_date or (e.quotation.outcome_last_updated_at or e.quotation_line.updated_at).date()),
            str(e.quotation.outcome_last_updated_at or e.quotation_line.updated_at), e.pk))
        amount = entry.quotation_line.accepted_unit_price
        basis = "accepted"
    else:
        entry = max(eligible, key=lambda e: (e.quoted_at, e.pk))
        amount = entry.unit_price
        basis = "quoted"
    if amount is None or amount <= 0:
        return _unavailable("Historical price is not valid", unit, currency)
    return {"eligible": True, "amount": str(amount), "basis": basis,
            "history_id": entry.pk, "quotation_id": entry.quotation_id,
            "quotation_number": entry.quotation.quotation_number,
            "date": str(entry.quotation.outcome_date or entry.quoted_at.date()) if basis == "accepted" else str(entry.quoted_at.date()),
            "unit": entry.unit, "currency": entry.currency, "quantity": str(entry.quantity),
            "company_id": quotation.company_id, "product_id": product.pk}


def line_price_snapshot(line):
    return {"line_id": line.pk, "quotation_id": line.quotation_id, "product_id": line.product_id, "unit": line.unit, "price": str(line.unit_price) if line.unit_price is not None else None,
            "provenance": dict(line.price_provenance or {}), "review": line.price_review_required,
            "wording": line.item_name_snapshot}


def update_line_pricing(line, before, payload, actor, *, context=None):
    """Run inside the quotation lock after applying editable line fields."""
    is_new = before is None
    before = before or {"product_id": None, "unit": "", "price": None, "provenance": {}, "review": False}
    previous = before["provenance"]
    original_id = payload.get("price_original_history")
    if original_id and not previous:
        if not line.product_id:
            raise ValidationError("A historical suggestion requires a linked product.")
        original = recommend_price(line.quotation, line.product, line.unit, source_wording=line.item_name_snapshot, context=context)
        if not original["eligible"] or str(original["history_id"]) != str(original_id):
            raise ValidationError("The original suggestion is no longer eligible. Refresh the company price before saving this correction.")
        previous = {"kind": "history", **original}
        before = {**before, "provenance": previous}
    feedback = payload.get("price_feedback", "")
    if feedback not in {"", "one_off", "outdated", "wrong_product"}:
        raise ValidationError("Unknown price-change reason.")
    variant_changed = bool(before.get("wording") and before["wording"] != line.item_name_snapshot
        and not identities_compatible(item_identity(before["wording"]), item_identity(line.item_name_snapshot)))
    context_changed = not is_new and (variant_changed or before["product_id"] != line.product_id or pricing_unit(before["unit"]) != pricing_unit(line.unit))
    changed = before["price"] != (str(line.unit_price) if line.unit_price is not None else None)
    if before["price"] is not None and line.unit_price is not None:
        changed = Decimal(before["price"]) != line.unit_price
    if context_changed or feedback == "wrong_product":
        line.price_provenance = {}
        if previous.get("kind") == "history" and not changed:
            line.unit_price = None
        elif line.unit_price is not None:
            line.price_review_required = True
        if feedback == "wrong_product" and before["product_id"]:
            ProductAlias.objects.filter(company_id=line.quotation.company_id,
                product_id=before["product_id"], normalized_alias=normalize_item_text(before.get("wording", "")),
                is_active=True).update(is_active=False)
        if feedback == "wrong_product" and line.product_id == before["product_id"]:
            line.product = None
            line.match_status = QuotationLine.MATCH_UNRESOLVED
            line.product_image = None
            line.include_product_image = False
            line.brand_name_snapshot = ""
        line.price_review_required = bool(before["review"] or variant_changed or feedback == "wrong_product" or
            (before["price"] is not None and previous.get("kind") != "history" and line.unit_price is not None))
    if payload.get("price_context_changed") is True:
        line.price_review_required = True
    source_id = payload.get("price_source_history")
    if source_id:
        if not line.product_id or line.match_status != QuotationLine.MATCH_CONFIRMED:
            raise ValidationError("Confirm the product before using a historical price.")
        recommended = recommend_price(line.quotation, line.product, line.unit, source_wording=line.item_name_snapshot, context=context)
        if (not recommended["eligible"] or str(recommended["history_id"]) != str(source_id)
                or line.unit_price is None or Decimal(recommended["amount"]) != line.unit_price):
            raise ValidationError("This historical price has changed or is no longer eligible. Refresh its history or enter a reviewed price.")
        line.price_provenance = {"kind": "history", **recommended}
        line.price_review_required = False
    elif changed or context_changed or feedback == "wrong_product":
        if line.unit_price is not None:
            line.price_provenance = {"kind": "manual", "company_id": line.quotation.company_id,
                "product_id": line.product_id, "unit": line.unit, "currency": line.quotation.currency,
                "previous_source": previous.get("previous_source", previous) if previous else {}}
        else:
            line.price_provenance = {}
    if payload.get("price_reviewed") is True:
        if not line.product_id or line.match_status != QuotationLine.MATCH_CONFIRMED or not line.unit_price or line.unit_price <= 0:
            raise ValidationError("Confirm the product and enter a valid price before marking it reviewed.")
        line.price_review_required = False
        if line.price_provenance.get("kind") == "history":
            line.price_provenance = {"kind": "manual", "previous_source": line.price_provenance}
    if feedback == "outdated":
        source = previous if previous.get("kind") == "history" else previous.get("previous_source", {})
        if (not source.get("history_id") or not line.product_id or source.get("product_id") != line.product_id
                or source.get("company_id") != line.quotation.company_id
                or pricing_unit(source.get("unit")) != pricing_unit(line.unit)
                or source.get("currency") != line.quotation.currency):
            raise ValidationError("Choose a historical recommendation before retiring an outdated price.")
        # Locking the company also serializes creation of a previously absent context.
        from .models import Company
        Company.objects.select_for_update().get(pk=line.quotation.company_id)
        PriceRecommendationRetirement.objects.update_or_create(
            company_id=line.quotation.company_id, product_id=line.product_id,
            unit=pricing_unit(line.unit), currency=line.quotation.currency.upper(),
            defaults={"retired_before": timezone.now(), "actor": actor})
        line.price_provenance = {"kind": "manual", "previous_source": source}
    if line.pk and (changed or context_changed or feedback or payload.get("price_reviewed")):
        from .services import audit_log
        from .models import QuotationAuditLog
        audit_log(actor, QuotationAuditLog.ACTION_UPDATED, line, quotation=line.quotation,
            message="Reviewed quotation pricing." if payload.get("price_reviewed") else "Recorded a quotation price decision.",
            changes={"pricing": {"before": before, "entered_price": str(line.unit_price) if line.unit_price is not None else None,
                                  "reason": feedback or "price_edit", "review_required": line.price_review_required}})

        QuotationPriceFeedback.objects.create(line=line, company_id=line.quotation.company_id,
            product_id=before["product_id"] or line.product_id,
            replacement_product_id=line.product_id if context_changed else None,
            source_wording=before.get("wording", line.item_name_snapshot),
            kind=feedback or ("product_correction" if context_changed and before["product_id"] else "price_edit"),
            previous=before, entered_price=line.unit_price, actor=actor)


def invalidate_quotation_prices(quotation, actor):
    for line in quotation.lines.select_for_update():
        before = line_price_snapshot(line)
        if line.price_provenance.get("kind") == "history":
            line.unit_price = None
        elif line.unit_price is not None:
            line.price_review_required = True
        line.price_provenance = {}
        line.save()
        QuotationPriceFeedback.objects.create(line=line, company_id=quotation.company_id,
            product_id=line.product_id, kind="customer_currency_changed", previous=before,
            entered_price=line.unit_price, actor=actor)


def validate_stored_price_source(line):
    source = line.price_provenance or {}
    if source.get("kind") != "history":
        return
    if not line.product_id:
        raise ValidationError("Review the product for this historical price.")
    current = recommend_price(line.quotation, line.product, line.unit, source_wording=line.item_name_snapshot)
    if not current["eligible"] or current["history_id"] != source.get("history_id") or Decimal(current["amount"]) != line.unit_price:
        raise ValidationError(f"The historical price for '{line.item_name_snapshot}' has changed or is no longer eligible. Review it before finalizing.")
