"""Review an existing company match without forgetting it or changing other companies."""
import hashlib
import json
from dataclasses import replace

from django.core.exceptions import ValidationError
from django.utils import timezone

from api.models import Product
from .models import ProductAlias, normalize_label


def _product_snapshot(product):
    return {"id": product.pk, **{field: getattr(product, field) for field in (
        "name", "dosage", "pack_size", "brand_id", "sku", "barcode", "status",
        "canonical_product_id", "identity_review_state",
    )}}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def confirmation_fingerprint(raw_text, product, company):
    from .matching import normalize_item_text
    snapshot = _product_snapshot(product)
    for field in ("name", "dosage", "pack_size"):
        snapshot[field] = normalize_item_text(snapshot[field])
    return _hash([1, company.pk if company else None, normalize_item_text(raw_text), snapshot])


def confirmation_is_current(alias, raw_text, product, company):
    return bool(company and alias.company_id == company.pk and alias.is_active
        and (alias.identity_confirmation or {}).get("fingerprint") == confirmation_fingerprint(raw_text, product, company))


def remember_identity_confirmation(alias, raw_text, product, company, actor):
    if not actor or not getattr(actor, "is_staff", False):
        return
    alias._automatic_learning_action = "identity_confirmed"
    alias._automatic_learning_previous = {"product_id": alias.product_id, "identity_confirmation": alias.identity_confirmation}
    alias.identity_confirmation = {"fingerprint": confirmation_fingerprint(raw_text, product, company),
        "actor_id": actor.pk, "confirmed_at": timezone.now().isoformat()}
    alias.save(update_fields=["identity_confirmation", "updated_at"])


def _differences(raw_text, product):
    from .matching import item_identity, product_identity, identities_compatible
    left, right = item_identity(raw_text), product_identity(product)
    reasons = []
    if product.status == "archived":
        reasons.append("The previously linked product is archived. Choose its replacement.")
    if left.dimensions and right.dimensions and left.dimensions != right.dimensions:
        reasons.append(f"Size differs: inquiry {', '.join(left.dimensions)}; saved product {', '.join(right.dimensions)}.")
    if left.name_roles != right.name_roles:
        reasons.append("Product type differs, such as the item itself versus its strips, kit or other accessory.")
    if left.strengths and right.strengths and left.strengths != right.strengths:
        reasons.append("The stated strengths differ.")
    if left.dosage_forms and right.dosage_forms and not set(left.dosage_forms) & set(right.dosage_forms):
        reasons.append("The dosage forms differ.")
    if left.pack_counts and right.pack_counts and not identities_compatible(left, replace(left, pack_counts=right.pack_counts)):
        reasons.append("The stated pack quantities differ.")
    if left.pack_forms and right.pack_forms and not set(left.pack_forms) & set(right.pack_forms):
        reasons.append("The packaging differs, such as a box versus a bottle.")
    for label, markers in [("Size", {"small", "medium", "large", "xl", "xs", "xxl"}),
                            ("Material", {"latex", "nitrile", "vinyl"})]:
        a, b = set(left.core_tokens) & markers, set(right.core_tokens) & markers
        if a and b and a != b:
            reasons.append(f"{label} differs: inquiry {', '.join(sorted(a))}; saved product {', '.join(sorted(b))}.")
    for marker in ("sterile", "powder"):
        a, b = set(left.core_tokens), set(right.core_tokens)
        if marker in a and marker in b and a & {"non", "free"} != b & {"non", "free"}:
            reasons.append(f"The {marker} / non-{marker} descriptions differ.")
    if not reasons and not identities_compatible(left, right):
        reasons.append("The combination or order of ingredient strengths differs.")
    return reasons


def saved_match_review(raw_text, company, aliases):
    from .matching import normalize_item_text
    products = {}
    snapshot = []
    for alias in aliases:
        product = alias.product.canonical_product if alias.product.canonical_product_id else alias.product
        products.setdefault(product.pk, {
            "product_id": product.pk, "product_name": product.name,
            "dosage": product.dosage, "pack_size": product.pack_size,
            "can_confirm": product.status != "archived",
            "differences": _differences(raw_text, product),
        })
        snapshot.append([alias.pk, alias.company_id, alias.alias, alias.is_active, alias.updated_at,
                         _product_snapshot(alias.product), _product_snapshot(product)])
    multiple = len(products) > 1
    reason = ("Equivalent saved names already point to different products. Choose the correct company match."
              if multiple else "This wording already has a saved product link. Review it before choosing a different product.")
    details = [reason] if multiple else [d for p in products.values() for d in p["differences"]]
    if not details and aliases and aliases[0].company_id is None and aliases[0].product.identity_review_state != "verified":
        details = ["This catalogue product is provisional. Confirm which product this company uses."]
    if not multiple and details:
        reason = f"Previously linked to '{next(iter(products.values()))['product_name']}'. " + " ".join(details)
    return {
        "company_id": company.pk if company else None,
        "company_name": company.name if company else "",
        "source_wording": raw_text,
        "scope": "company" if aliases and aliases[0].company_id else "catalogue",
        "products": list(products.values()), "reason": reason,
        "differences": details or ["The selected product differs from this saved link."],
        "token": _hash([1, company.pk if company else None, normalize_item_text(raw_text), sorted(snapshot, key=lambda a: a[0])]),
    }


class SavedProductMatchConflict(ValidationError):
    def __init__(self, review, *, line_id=None, selected_product_id=None, stale=False):
        self.saved_match_review = review
        self.line_id = line_id
        self.selected_product_id = selected_product_id
        self.http_status = 409 if stale else 400
        super().__init__("The saved match changed. Review the current details again." if stale else review["reason"])


def current_saved_aliases(raw_text, company, *, for_update=False):
    from .matching import _aliases_for_text
    aliases = _aliases_for_text(raw_text, company, for_update=for_update, include_archived=True)
    return aliases or _aliases_for_text(raw_text, None, for_update=for_update, include_archived=True)


def resolve_company_match(*, raw_text, company, product_id, token, resolution, actor):
    """Called under the quotation transaction; recheck the displayed alias snapshot."""
    from .matching import _aliases_for_text, _lock_product_alias_scopes, rejected_product_ids
    # Use the same scope locks as alias management, including a global alias
    # when it is being shadowed by a company-specific correction.
    _lock_product_alias_scopes(None, company)
    aliases = current_saved_aliases(raw_text, company, for_update=True)
    if not aliases:
        raise ValidationError("This saved link no longer exists. Refresh the quotation and match this row again.")
    # Catalogue edits cannot race the confirmation fingerprint.
    product_ids = {a.product_id for a in aliases} | {a.product.canonical_product_id for a in aliases if a.product.canonical_product_id}
    product_ids.add(product_id)
    locked = {p.pk: p for p in Product.objects.select_for_update().filter(pk__in=product_ids).order_by("pk")}
    for alias in aliases:
        alias.product = locked[alias.product_id]
        if alias.product.canonical_product_id:
            alias.product.canonical_product = locked[alias.product.canonical_product_id]
    review = saved_match_review(raw_text, company, aliases)
    if token != review["token"]:
        raise SavedProductMatchConflict(review, stale=True)
    if resolution not in {"confirm", "correct"}:
        raise ValidationError("Choose whether to confirm the saved product or correct the company match.")
    product = locked.get(product_id)
    if not product or product.status == "archived" or product.canonical_product_id:
        raise ValidationError("Choose an active product from the current catalogue.")
    if resolution == "confirm" and product_id not in {p["product_id"] for p in review["products"]}:
        raise ValidationError("The selected product is not one of the saved matches shown for review.")
    if product_id in rejected_product_ids(raw_text, company):
        raise ValidationError("This product has a recorded match concern for this wording. Resolve that concern or choose a different product.")

    affected = _aliases_for_text(raw_text, company, for_update=True, include_archived=True)
    # Reuse an exact retired key when replacing a catalogue-level alias.
    exact = ProductAlias.objects.select_for_update().filter(company=company, normalized_alias=normalize_label(raw_text)).first()
    if exact and exact.pk not in {a.pk for a in affected}:
        affected.append(exact)
    previous = [{"id": a.pk, "alias": a.alias, "product_id": a.product_id, "is_active": a.is_active,
                 "identity_confirmation": a.identity_confirmation} for a in affected]
    if not affected:
        affected = [ProductAlias(company=company, alias=raw_text, product=product, created_by=actor)]
    confirmed = {"fingerprint": confirmation_fingerprint(raw_text, product, company),
                 "actor_id": actor.pk, "confirmed_at": timezone.now().isoformat()}
    for alias in affected:
        alias.product = product
        alias.is_active = True
        alias.identity_confirmation = confirmed
        alias.save()
    return product, {"resolution": resolution, "source_wording": raw_text, "previous": previous,
                     "product_id": product.pk, "confirmation": confirmed, "alias_ids": [a.pk for a in affected]}
