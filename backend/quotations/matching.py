from dataclasses import dataclass

from django.db.models import Q

from api.models import Product

from .models import CompanyPriceHistory, ProductAlias, normalize_label


@dataclass
class ProductMatch:
    product: Product | None
    confidence: float
    method: str
    reason: str

    @property
    def matched(self):
        return self.product is not None

    def as_preview(self):
        if not self.product:
            return {
                "matched_product": None,
                "matched_product_name": "",
                "match_confidence": self.confidence,
                "match_method": self.method,
                "match_reason": self.reason,
            }
        return {
            "matched_product": self.product.id,
            "matched_product_name": self.product.name,
            "match_confidence": self.confidence,
            "match_method": self.method,
            "match_reason": self.reason,
        }


def product_catalog_queryset():
    return Product.objects.exclude(status="archived").select_related("brand", "category")


def _company_history_product_match(raw_text, normalized, company):
    if not company:
        return None
    lookup_text = (raw_text or "").strip()
    for history in (
        CompanyPriceHistory.objects.filter(company=company)
        .select_related("product")
        .order_by("-quoted_at", "-id")[:1000]
    ):
        product = history.product
        if not product or product.status == "archived":
            continue
        if normalize_label(product.name) == normalized:
            return product
        if lookup_text and (product.sku == lookup_text or product.barcode == lookup_text):
            return product
    return None


def suggest_product_for_text(raw_text, company=None, *, company_only=False):
    normalized = normalize_label(raw_text)
    if not normalized:
        return ProductMatch(None, 0.0, "empty", "No item text to match.")

    if company:
        alias = (
            ProductAlias.objects.filter(
                company=company,
                normalized_alias=normalized,
                is_active=True,
            )
            .select_related("product")
            .first()
        )
        if alias and alias.product.status != "archived":
            return ProductMatch(
                alias.product,
                0.98,
                "company_alias",
                f"Matched company alias '{alias.alias}'.",
            )

        history_product = _company_history_product_match(raw_text, normalized, company)
        if history_product:
            return ProductMatch(
                history_product,
                0.92,
                "company_price_history",
                f"Matched Product previously quoted to {company.name}.",
            )

        if company_only:
            return ProductMatch(
                None,
                0.0,
                "company_unmatched",
                "No company-specific alias or previous company price history matched this item.",
            )

    alias = (
        ProductAlias.objects.filter(
            company__isnull=True,
            normalized_alias=normalized,
            is_active=True,
        )
        .select_related("product")
        .first()
    )
    if alias and alias.product.status != "archived":
        return ProductMatch(
            alias.product,
            0.94,
            "global_alias",
            f"Matched global alias '{alias.alias}'.",
        )

    exact_products = list(product_catalog_queryset().filter(name__iexact=raw_text.strip())[:2])
    if len(exact_products) == 1:
        return ProductMatch(
            exact_products[0],
            0.90,
            "exact_product_name",
            "Matched exact product name.",
        )

    normalized_candidates = [
        product
        for product in product_catalog_queryset().filter(name__icontains=raw_text.strip()[:80])[:10]
        if normalize_label(product.name) == normalized
    ]
    if len(normalized_candidates) == 1:
        return ProductMatch(
            normalized_candidates[0],
            0.88,
            "normalized_product_name",
            "Matched normalized product name.",
        )

    if len(normalized) >= 5:
        contains_candidates = list(
            product_catalog_queryset()
            .filter(Q(name__icontains=raw_text.strip()) | Q(sku__iexact=raw_text.strip()) | Q(barcode__iexact=raw_text.strip()))
            .order_by("name")[:2]
        )
        if len(contains_candidates) == 1:
            return ProductMatch(
                contains_candidates[0],
                0.72,
                "unique_product_search",
                "Found one conservative product-name/SKU candidate; review before finalizing.",
            )

    return ProductMatch(None, 0.0, "unmatched", "No safe deterministic match found.")


def apply_match_to_preview_line(line, company=None):
    match = suggest_product_for_text(
        line.get("raw_name") or line.get("item_name") or line.get("raw_line") or "",
        company,
        company_only=bool(company),
    )
    line.update(match.as_preview())
    if match.confidence >= 0.88:
        line["matched_product"] = match.product.id
        line["match_status"] = "confirmed"
    else:
        line.setdefault("matched_product", None)
        line.setdefault("match_status", "unresolved")
    return line


def create_product_alias(*, alias_text, product, company=None, actor=None, notes=""):
    normalized = normalize_label(alias_text)
    if not normalized:
        raise ValueError("Alias text is required.")
    alias, created = ProductAlias.objects.update_or_create(
        company=company,
        normalized_alias=normalized,
        defaults={
            "alias": alias_text.strip(),
            "product": product,
            "is_active": True,
            "created_by": actor if getattr(actor, "is_authenticated", False) else None,
            "notes": notes,
        },
    )
    return alias, created
