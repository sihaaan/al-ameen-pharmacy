import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import Q

from api.models import Product

from .models import Company, CompanyPriceHistory, ProductAlias, normalize_label


AUTO_MATCH_CONFIDENCE = 0.88
FUZZY_CANDIDATE_THRESHOLD = 0.58
MAX_MATCH_CANDIDATES = 6
GLOBAL_PRODUCT_ALIAS_ADVISORY_LOCK = 1_884_115_049

_TOKEN_RE = re.compile(r"\d+(?:\.\d+)?|[a-z]+|%")
_MEASUREMENT_RE = re.compile(
    r"(?<![a-z0-9])(\d+(?:\.\d+)?)\s*(mcg|ug|mg|gm|g|kg|ml|ltr|lt|l|iu|units?|%)(?![a-z])",
    re.IGNORECASE,
)
_COUNT_FORM_RE = re.compile(
    r"(?<![a-z0-9])(\d+)\s*(tablets?|tabs?|capsules?|caps?|sachets?|ampoules?|amps?|vials?|"
    r"bottles?|boxes?|packs?|packets?|pkts?|pieces?|pcs?|nos?|strips?|rolls?|pairs?|tubes?|bags?)(?![a-z])",
    re.IGNORECASE,
)
_X_COUNT_RE = re.compile(r"(?<![a-z0-9])(?:pack|box|packet|pkt|strip)?\s*x\s*(\d+)(?![a-z0-9])", re.IGNORECASE)

_TOKEN_ALIASES = {
    "ug": "mcg",
    "gm": "g",
    "ltr": "l",
    "lt": "l",
    "tab": "tablet",
    "tabs": "tablet",
    "tablets": "tablet",
    "cap": "capsule",
    "caps": "capsule",
    "capsules": "capsule",
    "sachets": "sachet",
    "amp": "ampoule",
    "amps": "ampoule",
    "ampoules": "ampoule",
    "vials": "vial",
    "bottles": "bottle",
    "boxes": "box",
    "packs": "pack",
    "packet": "pack",
    "packets": "pack",
    "pkt": "pack",
    "pkts": "pack",
    "pieces": "piece",
    "pcs": "piece",
    "no": "piece",
    "nos": "piece",
    "strips": "strip",
    "rolls": "roll",
    "pairs": "pair",
    "tubes": "tube",
    "bags": "bag",
    "drops": "drop",
    "injections": "injection",
    "ointments": "ointment",
    "creams": "cream",
    "sprays": "spray",
}

_DOSAGE_FORMS = {
    "tablet",
    "capsule",
    "syrup",
    "cream",
    "ointment",
    "gel",
    "spray",
    "injection",
    "vial",
    "ampoule",
    "sachet",
    "suppository",
    "drop",
    "inhaler",
}
_PACK_FORMS = {"box", "pack", "bottle", "piece", "strip", "roll", "kit", "tube", "bag", "pair"}
_IDENTITY_NOISE = _DOSAGE_FORMS | _PACK_FORMS | {"of", "per", "each", "unit", "units"}


def normalize_item_text(value):
    """Normalize pharmacy item text without discarding strength or pack information."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("µ", "u").replace("μ", "u").replace("×", " x ").lower()
    tokens = [_TOKEN_ALIASES.get(token, token) for token in _TOKEN_RE.findall(text)]
    return " ".join(tokens)


def _canonical_number(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return str(value)
    rendered = format(number.normalize(), "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _measurement_key(number, unit):
    unit = _TOKEN_ALIASES.get(str(unit).lower(), str(unit).lower())
    try:
        value = Decimal(str(number))
    except (InvalidOperation, TypeError, ValueError):
        return f"{number}{unit}"
    if unit == "mcg":
        return f"{_canonical_number(value)}mcg"
    if unit == "mg":
        return f"{_canonical_number(value * 1000)}mcg"
    if unit == "g":
        return f"{_canonical_number(value * 1000000)}mcg"
    if unit == "kg":
        return f"{_canonical_number(value * 1000000000)}mcg"
    if unit == "l":
        return f"{_canonical_number(value * 1000)}ml"
    if unit == "ml":
        return f"{_canonical_number(value)}ml"
    if unit in {"unit", "units"}:
        unit = "iu"
    return f"{_canonical_number(value)}{unit}"


def _extract_measurements(value):
    return tuple(
        sorted({_measurement_key(number, unit) for number, unit in _MEASUREMENT_RE.findall(str(value or ""))})
    )


def _extract_pack_counts(value):
    counts = set()
    for number, raw_form in _COUNT_FORM_RE.findall(str(value or "")):
        form_tokens = normalize_item_text(raw_form).split()
        form = form_tokens[0] if form_tokens else ""
        counts.add((int(number), form))
    for number in _X_COUNT_RE.findall(str(value or "")):
        counts.add((int(number), ""))
    return tuple(sorted(counts))


def _forms(value):
    tokens = set(normalize_item_text(value).split())
    return tuple(sorted(tokens & (_DOSAGE_FORMS | _PACK_FORMS)))


def _core_name(value):
    text = _MEASUREMENT_RE.sub(" ", str(value or ""))
    text = _COUNT_FORM_RE.sub(" ", text)
    text = _X_COUNT_RE.sub(" ", text)
    tokens = [
        token
        for token in normalize_item_text(text).split()
        if token not in _IDENTITY_NOISE and token != "x"
    ]
    return " ".join(tokens)


@dataclass(frozen=True)
class ItemIdentity:
    normalized_text: str
    core_name: str
    core_tokens: tuple[str, ...]
    strengths: tuple[str, ...]
    pack_counts: tuple[tuple[int, str], ...]
    dosage_forms: tuple[str, ...]
    pack_forms: tuple[str, ...]

    @property
    def fingerprint(self):
        return (self.core_name, self.strengths, self.pack_counts, self.dosage_forms, self.pack_forms)


def item_identity(name, *, dosage="", pack_size="", unit=""):
    name = str(name or "")
    dosage = str(dosage or "")
    pack_size = str(pack_size or "")
    unit = str(unit or "")
    strong_forms = _forms(" ".join([name, dosage, pack_size]))
    unit_forms = _forms(unit)
    return ItemIdentity(
        normalized_text=normalize_item_text(" ".join(part for part in [name, dosage, pack_size] if part)),
        core_name=_core_name(name),
        core_tokens=tuple(_core_name(name).split()),
        strengths=_extract_measurements(" ".join(part for part in [name, dosage] if part)),
        pack_counts=_extract_pack_counts(" ".join(part for part in [name, pack_size] if part)),
        dosage_forms=tuple(sorted({form for form in (*strong_forms, *unit_forms) if form in _DOSAGE_FORMS})),
        pack_forms=tuple(form for form in strong_forms if form in _PACK_FORMS),
    )


def product_identity(product):
    return item_identity(
        product.name,
        dosage=getattr(product, "dosage", "") or "",
        pack_size=getattr(product, "pack_size", "") or "",
    )


def identities_compatible(requested, candidate):
    if requested.strengths and candidate.strengths and requested.strengths != candidate.strengths:
        return False
    if requested.dosage_forms and candidate.dosage_forms:
        if not set(requested.dosage_forms).intersection(candidate.dosage_forms):
            return False
    if requested.pack_counts and candidate.pack_counts:
        requested_by_form = {form: count for count, form in requested.pack_counts}
        candidate_by_form = {form: count for count, form in candidate.pack_counts}
        common_forms = (set(requested_by_form) & set(candidate_by_form)) - {""}
        if any(requested_by_form[form] != candidate_by_form[form] for form in common_forms):
            return False
        requested_unspecified = requested_by_form.get("")
        candidate_unspecified = candidate_by_form.get("")
        if requested_unspecified and candidate_unspecified and requested_unspecified != candidate_unspecified:
            return False
        if requested_unspecified and candidate_by_form and requested_unspecified not in set(candidate_by_form.values()):
            return False
        if candidate_unspecified and requested_by_form and candidate_unspecified not in set(requested_by_form.values()):
            return False
    if requested.pack_forms and candidate.pack_forms:
        if not set(requested.pack_forms).intersection(candidate.pack_forms):
            return False
    return True


@dataclass(frozen=True)
class ProductCandidate:
    product: Product
    score: float
    method: str
    reason: str

    def as_dict(self):
        return {
            "product": self.product.id,
            "product_id": self.product.id,
            "product_name": self.product.name,
            "sku": self.product.sku or "",
            "barcode": self.product.barcode or "",
            "dosage": self.product.dosage or "",
            "pack_size": self.product.pack_size or "",
            "status": self.product.status,
            "score": round(float(self.score), 3),
            "confidence": round(float(self.score), 3),
            "method": self.method,
            "reason": self.reason,
        }


@dataclass
class ProductMatch:
    product: Product | None
    confidence: float
    method: str
    reason: str
    candidates: list[ProductCandidate] = field(default_factory=list)
    requires_confirmation: bool = False

    @property
    def matched(self):
        return self.product is not None

    def as_preview(self):
        return {
            "matched_product": self.product.id if self.product else None,
            "matched_product_name": self.product.name if self.product else "",
            "match_confidence": round(float(self.confidence), 3),
            "match_method": self.method,
            "match_reason": self.reason,
            "match_candidates": [candidate.as_dict() for candidate in self.candidates],
            "requires_match_confirmation": bool(self.requires_confirmation),
        }


@dataclass
class ProductCreationResult:
    product: Product | None
    created: bool
    match: ProductMatch
    requires_confirmation: bool = False
    warning: str = ""
    override_used: bool = False
    creation_blocked: bool = False

    def as_dict(self):
        return {
            "product_id": self.product.id if self.product else None,
            "product_name": self.product.name if self.product else "",
            "created": self.created,
            "reused": bool(self.product and not self.created),
            "requires_confirmation": self.requires_confirmation,
            "warning": self.warning,
            "match_method": self.match.method,
            "match_confidence": round(float(self.match.confidence), 3),
            "match_reason": self.match.reason,
            "candidates": [candidate.as_dict() for candidate in self.match.candidates],
            "override_used": self.override_used,
            "creation_blocked": self.creation_blocked,
        }


def product_catalog_queryset():
    return Product.objects.exclude(status="archived").select_related("brand", "category")


def _candidate(product, score, method, reason):
    return ProductCandidate(product=product, score=score, method=method, reason=reason)


def _aliases_for_text(raw_text, company, *, for_update=False, include_inactive=False):
    simple = normalize_label(raw_text)
    domain = normalize_item_text(raw_text)
    queryset = ProductAlias.objects.filter(company=company).select_related("product")
    if not include_inactive:
        queryset = queryset.filter(is_active=True)
    if for_update:
        queryset = queryset.select_for_update()
    matches = []
    exact = list(queryset.filter(normalized_alias=simple).order_by("id")[:10])
    for alias in exact:
        if alias.product.status != "archived":
            matches.append(alias)
    first_term = domain.split()[0] if domain.split() else ""
    fallback = queryset
    if first_term and not for_update:
        raw_variants = {
            first_term,
            *(raw_token for raw_token, normalized_token in _TOKEN_ALIASES.items() if normalized_token == first_term),
        }
        token_filter = Q()
        for raw_variant in sorted(raw_variants):
            token_filter |= Q(alias__icontains=raw_variant)
        fallback = fallback.filter(token_filter)
    seen = {alias.id for alias in matches}
    ordered_fallback = fallback.order_by("id")
    for alias in ordered_fallback:
        if alias.id in seen or alias.product.status == "archived":
            continue
        if normalize_item_text(alias.alias) == domain:
            matches.append(alias)
            seen.add(alias.id)
    return matches


def _lock_product_alias_scopes(*companies):
    """Serialize alias decisions before locking individual spelling rows."""
    includes_global_scope = not companies or any(company is None for company in companies)
    if connection.vendor == "postgresql" and includes_global_scope:
        # The global (company=NULL) namespace has no row of its own to lock.
        # Managed moves take this first as well, then lock Company rows in order.
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [GLOBAL_PRODUCT_ALIAS_ADVISORY_LOCK])
    company_ids = sorted(
        {
            company.pk
            for company in companies
            if company and getattr(company, "pk", None)
        }
    )
    if company_ids:
        list(
            Company.objects.select_for_update()
            .filter(pk__in=company_ids)
            .order_by("pk")
            .values_list("pk", flat=True)
        )


def _lock_product_alias_scope(company):
    _lock_product_alias_scopes(company)


def _assert_managed_alias_available(*, alias_text, product, company, exclude_alias_id=None):
    cleaned_alias = str(alias_text or "").strip()
    identity = normalize_item_text(cleaned_alias)
    if not identity:
        raise ValidationError("Alias text is required.")
    queryset = (
        ProductAlias.objects.select_for_update()
        .select_related("product")
        .filter(company=company)
        .order_by("id")
    )
    if exclude_alias_id:
        queryset = queryset.exclude(pk=exclude_alias_id)
    for existing in queryset:
        if normalize_item_text(existing.alias) != identity:
            continue
        if existing.product_id != product.id:
            raise ValidationError(
                f"Alias '{cleaned_alias}' is equivalent to '{existing.alias}', which already points to "
                f"'{existing.product.name}' in this scope."
            )
        raise ValidationError(f"Equivalent alias '{existing.alias}' already exists in this scope.")
    return cleaned_alias


@transaction.atomic
def create_managed_product_alias(*, company, product, alias_text, notes="", is_active=True, actor=None):
    """Create an explicitly managed alias after an under-lock equivalence check."""
    _lock_product_alias_scopes(company)
    cleaned_alias = _assert_managed_alias_available(
        alias_text=alias_text,
        product=product,
        company=company,
    )
    return ProductAlias.objects.create(
        company=company,
        product=product,
        alias=cleaned_alias,
        notes=notes,
        is_active=is_active,
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
    )


@transaction.atomic
def update_managed_product_alias(*, alias_id, changes):
    """Update an explicitly managed alias without stale-instance overwrites."""
    _lock_product_alias_scopes()
    snapshot = ProductAlias.objects.select_related("company", "product").get(pk=alias_id)
    requested_company = changes.get("company", snapshot.company)
    _lock_product_alias_scopes(snapshot.company, requested_company)
    alias = (
        ProductAlias.objects.select_for_update()
        .select_related("company", "product")
        .get(pk=alias_id)
    )
    company = changes.get("company", alias.company)
    product = changes.get("product", alias.product)
    alias_text = changes.get("alias", alias.alias)
    identity_changed = (
        getattr(company, "pk", None) != alias.company_id
        or getattr(product, "pk", None) != alias.product_id
        or normalize_label(alias_text) != alias.normalized_alias
    )
    activating = not alias.is_active and bool(changes.get("is_active", alias.is_active))
    if identity_changed or activating:
        cleaned_alias = _assert_managed_alias_available(
            alias_text=alias_text,
            product=product,
            company=company,
            exclude_alias_id=alias.id,
        )
    else:
        cleaned_alias = str(alias_text or "").strip()
        if not cleaned_alias:
            raise ValidationError("Alias text is required.")
    alias.company = company
    alias.product = product
    alias.alias = cleaned_alias
    alias.notes = changes.get("notes", alias.notes)
    alias.is_active = changes.get("is_active", alias.is_active)
    alias.save(
        update_fields=[
            "company",
            "product",
            "alias",
            "normalized_alias",
            "notes",
            "is_active",
            "updated_at",
        ]
    )
    return alias


def _alias_match(raw_text, company, scope_label):
    aliases = _aliases_for_text(raw_text, company)
    if not aliases:
        return None
    products = {}
    for alias in aliases:
        products.setdefault(alias.product_id, alias)
    if len(products) > 1:
        candidates = [
            _candidate(
                alias.product,
                0.99,
                "alias_conflict",
                f"Equivalent {scope_label} aliases point to different Products.",
            )
            for alias in products.values()
        ]
        return ProductMatch(
            None,
            0.99,
            "alias_conflict",
            f"Equivalent {scope_label} aliases point to different Products; resolve the alias conflict before matching.",
            candidates,
            True,
        )
    alias = aliases[0]
    method = "company_alias" if company else "global_alias"
    label = "company" if company else "global"
    score = 0.99 if company else 0.97
    candidate = _candidate(alias.product, score, method, f"Matched {label} alias '{alias.alias}'.")
    return ProductMatch(alias.product, candidate.score, candidate.method, candidate.reason, [candidate])


def _company_history_product_match(raw_text, requested, company, *, sku="", barcode=""):
    if not company:
        return None
    identifiers = {str(value).strip().lower() for value in [raw_text, sku, barcode] if str(value or "").strip()}
    seen = set()
    for history in (
        CompanyPriceHistory.objects.filter(company=company)
        .select_related("product")
        .order_by("-quoted_at", "-id")[:1000]
    ):
        product = history.product
        if not product or product.id in seen or product.status == "archived":
            continue
        seen.add(product.id)
        if identifiers.intersection({(product.sku or "").strip().lower(), (product.barcode or "").strip().lower()}) - {""}:
            return product
        identity = product_identity(product)
        if requested.core_name and requested.core_name == identity.core_name and identities_compatible(requested, identity):
            return product
    return None


def _identifier_match(raw_text, *, sku="", barcode=""):
    values = []
    for value in [barcode, sku, raw_text]:
        cleaned = str(value or "").strip()
        if cleaned and cleaned.lower() not in {item.lower() for item in values}:
            values.append(cleaned)
    if not values:
        return [], ""
    query = Q()
    for value in values:
        query |= Q(sku__iexact=value) | Q(barcode__iexact=value)
    products = list(product_catalog_queryset().filter(query).order_by("id")[:20])
    method = "exact_sku_or_barcode" if products else ""
    return products, method


def _catalog_pool(requested, raw_text):
    query = Q(name__iexact=str(raw_text or "").strip())
    significant = [token for token in requested.core_tokens if len(token) >= 3][:4]
    for token in significant:
        prefix = token[:4]
        query |= (
            Q(name__icontains=token)
            | Q(active_ingredient__icontains=token)
            | Q(name__icontains=prefix)
            | Q(active_ingredient__icontains=prefix)
        )
    if not significant and not str(raw_text or "").strip():
        return []
    return list(product_catalog_queryset().filter(query).distinct().order_by("name", "id")[:300])


def _fuzzy_score(requested, candidate):
    if not requested.core_name or not candidate.core_name:
        return 0.0
    requested_tokens = set(requested.core_tokens)
    candidate_tokens = set(candidate.core_tokens)
    union = requested_tokens | candidate_tokens
    token_score = len(requested_tokens & candidate_tokens) / len(union) if union else 0.0
    sequence_score = SequenceMatcher(None, requested.core_name, candidate.core_name).ratio()
    score = (sequence_score * 0.62) + (token_score * 0.38)
    if requested.core_name in candidate.core_name or candidate.core_name in requested.core_name:
        score = max(score, 0.76 if min(len(requested.core_name), len(candidate.core_name)) >= 5 else score)
    if requested.strengths and candidate.strengths and requested.strengths == candidate.strengths:
        score += 0.04
    if requested.dosage_forms and candidate.dosage_forms and set(requested.dosage_forms) & set(candidate.dosage_forms):
        score += 0.02
    return min(score, 0.89)


def _rank_catalog_candidates(raw_text, requested, limit=MAX_MATCH_CANDIDATES):
    exact = []
    fuzzy = []
    for product in _catalog_pool(requested, raw_text):
        identity = product_identity(product)
        if not identities_compatible(requested, identity):
            continue
        if requested.core_name and requested.core_name == identity.core_name:
            exact.append(_candidate(product, 0.92, "canonical_name", "Matched canonical product identity."))
            continue
        score = _fuzzy_score(requested, identity)
        if score >= FUZZY_CANDIDATE_THRESHOLD:
            fuzzy.append(
                _candidate(
                    product,
                    score,
                    "fuzzy_name",
                    "Similar product name with compatible strength, dosage form, and pack details.",
                )
            )
    exact.sort(key=lambda item: (item.product.id, item.product.name.lower()))
    fuzzy.sort(key=lambda item: (-item.score, item.product.name.lower(), item.product.id))
    return exact[:limit], fuzzy[:limit]


def _select_canonical_match(exact_candidates, requested):
    if not exact_candidates:
        return None
    if len(exact_candidates) == 1:
        return exact_candidates[0]
    exact_fingerprint_matches = [
        candidate
        for candidate in exact_candidates
        if product_identity(candidate.product).fingerprint == requested.fingerprint
    ]
    if exact_fingerprint_matches:
        return min(exact_fingerprint_matches, key=lambda candidate: candidate.product.id)
    fingerprints = {product_identity(candidate.product).fingerprint for candidate in exact_candidates}
    if len(fingerprints) == 1:
        return min(exact_candidates, key=lambda candidate: candidate.product.id)
    fully_specified = bool(requested.strengths or requested.pack_counts or requested.dosage_forms or requested.pack_forms)
    if fully_specified:
        matching_fingerprints = [
            candidate
            for candidate in exact_candidates
            if all(
                [
                    not requested.strengths or requested.strengths == product_identity(candidate.product).strengths,
                    not requested.pack_counts or requested.pack_counts == product_identity(candidate.product).pack_counts,
                    not requested.dosage_forms or bool(set(requested.dosage_forms) & set(product_identity(candidate.product).dosage_forms)),
                    not requested.pack_forms or bool(set(requested.pack_forms) & set(product_identity(candidate.product).pack_forms)),
                ]
            )
        ]
        if len(matching_fingerprints) == 1:
            return matching_fingerprints[0]
    return None


def suggest_product_for_text(
    raw_text,
    company=None,
    *,
    company_only=False,
    sku="",
    barcode="",
    dosage="",
    pack_size="",
    unit="",
    limit=MAX_MATCH_CANDIDATES,
):
    # company_only is retained as a compatibility keyword, but deliberately no
    # longer short-circuits global aliases and the master catalog.
    del company_only
    raw_text = str(raw_text or "").strip()
    requested = item_identity(raw_text, dosage=dosage, pack_size=pack_size, unit=unit)
    if not requested.normalized_text and not str(sku or "").strip() and not str(barcode or "").strip():
        return ProductMatch(None, 0.0, "empty", "No item text or identifier to match.")

    if company:
        alias_match = _alias_match(raw_text, company, "company")
        if alias_match:
            return alias_match

        history_product = _company_history_product_match(
            raw_text,
            requested,
            company,
            sku=sku,
            barcode=barcode,
        )
        if history_product:
            reason = f"Matched Product previously quoted to {company.name}."
            candidate = _candidate(history_product, 0.96, "company_price_history", reason)
            return ProductMatch(history_product, candidate.score, candidate.method, reason, [candidate])

    alias_match = _alias_match(raw_text, None, "global")
    if alias_match:
        return alias_match

    identifier_products, identifier_method = _identifier_match(raw_text, sku=sku, barcode=barcode)
    if identifier_products:
        candidates = [
            _candidate(product, 0.99, identifier_method, "Matched exact SKU or barcode.")
            for product in identifier_products[:limit]
        ]
        if len(identifier_products) == 1:
            return ProductMatch(identifier_products[0], 0.99, identifier_method, candidates[0].reason, candidates)
        return ProductMatch(
            None,
            0.99,
            "identifier_conflict",
            "The same SKU/barcode points to multiple Products; select the intended Product.",
            candidates,
            True,
        )

    exact_candidates, fuzzy_candidates = _rank_catalog_candidates(raw_text, requested, limit=limit)
    selected = _select_canonical_match(exact_candidates, requested)
    if selected:
        return ProductMatch(
            selected.product,
            selected.score,
            selected.method,
            selected.reason,
            exact_candidates,
        )
    if exact_candidates:
        return ProductMatch(
            None,
            0.92,
            "canonical_name_conflict",
            "Several Products share this canonical name but differ in strength, dosage form, or pack; select one or confirm a new variant.",
            exact_candidates,
            True,
        )
    if fuzzy_candidates:
        return ProductMatch(
            None,
            fuzzy_candidates[0].score,
            "fuzzy_candidates",
            "Similar Products were found; select one or explicitly confirm creation of a new Product.",
            fuzzy_candidates,
            True,
        )

    return ProductMatch(None, 0.0, "unmatched", "No compatible Product candidate found.")


def apply_match_to_preview_line(line, company=None):
    match = suggest_product_for_text(
        line.get("raw_name") or line.get("item_name") or line.get("raw_line") or "",
        company,
        sku=line.get("sku") or "",
        barcode=line.get("barcode") or "",
        dosage=line.get("dosage") or line.get("strength") or "",
        pack_size=line.get("pack_size") or line.get("pack_info") or "",
        unit=line.get("unit") or "",
    )
    line.update(match.as_preview())
    if match.product and match.confidence >= AUTO_MATCH_CONFIDENCE:
        line["matched_product"] = match.product.id
        line["match_status"] = "confirmed"
    else:
        line["matched_product"] = None
        line["matched_product_name"] = ""
        line["match_status"] = "unresolved"
    return line


@transaction.atomic
def create_product_alias(*, alias_text, product, company=None, actor=None, notes=""):
    normalized = normalize_label(alias_text)
    if not normalized:
        raise ValidationError("Alias text is required.")
    _lock_product_alias_scope(company)
    exact_existing = list(
        ProductAlias.objects.select_for_update()
        .select_related("product")
        .filter(company=company, normalized_alias=normalized)
        .order_by("id")[:10]
    )
    equivalent = exact_existing + [
        alias
        for alias in _aliases_for_text(
            alias_text,
            company,
            for_update=True,
            include_inactive=True,
        )
        if alias.id not in {existing.id for existing in exact_existing}
    ]
    existing = equivalent[0] if equivalent else None
    conflicting_equivalent = next((alias for alias in equivalent if alias.product_id != product.id), None)
    if conflicting_equivalent:
        scope = company.name if company else "the global catalog"
        raise ValidationError(
            f"Alias '{alias_text.strip()}' is equivalent to '{conflicting_equivalent.alias}', which already points to "
            f"'{conflicting_equivalent.product.name}' for {scope}."
        )
    if existing and existing.product_id != product.id:
        scope = company.name if company else "the global catalog"
        raise ValidationError(
            f"Alias '{alias_text.strip()}' already points to '{existing.product.name}' for {scope}; "
            "change it explicitly in alias management instead of silently remapping it."
        )
    if existing:
        existing.alias = alias_text.strip()
        existing.is_active = True
        existing.notes = notes
        if not existing.created_by_id and getattr(actor, "is_authenticated", False):
            existing.created_by = actor
        existing.save(update_fields=["alias", "normalized_alias", "is_active", "notes", "created_by", "updated_at"])
        return existing, False
    try:
        with transaction.atomic():
            alias = ProductAlias.objects.create(
                company=company,
                alias=alias_text.strip(),
                product=product,
                is_active=True,
                created_by=actor if getattr(actor, "is_authenticated", False) else None,
                notes=notes,
            )
    except IntegrityError:
        existing = ProductAlias.objects.select_related("product").get(company=company, normalized_alias=normalized)
        if existing.product_id != product.id:
            raise ValidationError(
                f"Alias '{alias_text.strip()}' was assigned to '{existing.product.name}' by another request."
            )
        return existing, False
    return alias, True


@transaction.atomic
def learn_confirmed_product_alias(
    *,
    source_text,
    product,
    company,
    actor=None,
    notes="",
    explicit_confirmation=False,
):
    """Remember confirmed customer wording without changing the Product identity.

    Product matching previews are deliberately read-only. Persisted automatic
    matches may learn new wording, but only explicit staff confirmation may
    supersede a retired alias. Conflicts roll back the surrounding match instead
    of leaving the catalogue in a contradictory state.
    """
    cleaned_source = str(source_text or "").strip()
    if not cleaned_source or not product or not company:
        return None, False
    _lock_product_alias_scope(company)

    normalized = normalize_label(cleaned_source)
    exact_existing = list(
        ProductAlias.objects.select_for_update()
        .select_related("product")
        .filter(company=company, normalized_alias=normalized)
        .order_by("id")[:10]
    )
    equivalent = exact_existing + [
        alias
        for alias in _aliases_for_text(
            cleaned_source,
            company,
            for_update=True,
            include_inactive=True,
        )
        if alias.id not in {existing.id for existing in exact_existing}
    ]
    # Active cross-Product aliases remain authoritative. Staff must resolve
    # those explicitly instead of silently remapping live catalogue knowledge.
    if any(alias.is_active and alias.product_id != product.id for alias in equivalent):
        return create_product_alias(
            alias_text=cleaned_source,
            product=product,
            company=company,
            actor=actor,
            notes=notes,
        )

    exact_alias = exact_existing[0] if exact_existing else None
    if exact_alias and exact_alias.product_id == product.id and exact_alias.is_active:
        return exact_alias, False

    if not explicit_confirmation and any(not alias.is_active for alias in equivalent):
        # A retired equivalent is a human catalogue decision.  High-confidence
        # automatic matching can keep its Product selection, but cannot work
        # around that retirement by reviving or adding a spelling variant.
        return None, False

    if exact_alias:
        # The exact spelling key was retired. A new explicit staff confirmation
        # supersedes that retired mapping, so reactivate/reassign it and retain
        # the previous values on the instance for the service-layer audit log.
        exact_alias._automatic_learning_action = (
            "reactivated" if exact_alias.product_id == product.id else "reassigned"
        )
        exact_alias._automatic_learning_previous = {
            "alias": exact_alias.alias,
            "product_id": exact_alias.product_id,
            "product_name": exact_alias.product.name,
            "is_active": exact_alias.is_active,
            "notes": exact_alias.notes,
        }
        exact_alias.alias = cleaned_source
        exact_alias.product = product
        exact_alias.is_active = True
        exact_alias.notes = "\n".join(part for part in [exact_alias.notes.strip(), notes.strip()] if part)
        if not exact_alias.created_by_id and getattr(actor, "is_authenticated", False):
            exact_alias.created_by = actor
        exact_alias.save(
            update_fields=["alias", "normalized_alias", "product", "is_active", "notes", "created_by", "updated_at"]
        )
        return exact_alias, False

    # A canonical Product name does not need a duplicate alias, but only after
    # checking the company alias table.  An active alias owned by another
    # Product is authoritative, and an exact retired key must be restored and
    # audited even when the confirmed Product happens to use the same name.
    if cleaned_source == str(product.name or "").strip():
        return None, False

    # Domain-equivalent aliases may exist under other punctuation/spelling
    # keys. Preserve them, including retired mappings, and add the exact source
    # wording requested by staff as its own active alias.
    try:
        with transaction.atomic():
            alias = ProductAlias.objects.create(
                company=company,
                alias=cleaned_source,
                product=product,
                is_active=True,
                created_by=actor if getattr(actor, "is_authenticated", False) else None,
                notes=notes,
            )
    except IntegrityError:
        concurrent = (
            ProductAlias.objects.select_for_update()
            .select_related("product")
            .get(company=company, normalized_alias=normalized)
        )
        if concurrent.is_active and concurrent.product_id != product.id:
            raise ValidationError(
                f"Alias '{cleaned_source}' was assigned to '{concurrent.product.name}' by another request."
            )
        if concurrent.is_active and concurrent.product_id == product.id:
            return concurrent, False
        if not explicit_confirmation:
            return None, False
        concurrent._automatic_learning_action = (
            "reactivated" if concurrent.product_id == product.id else "reassigned"
        )
        concurrent._automatic_learning_previous = {
            "alias": concurrent.alias,
            "product_id": concurrent.product_id,
            "product_name": concurrent.product.name,
            "is_active": concurrent.is_active,
            "notes": concurrent.notes,
        }
        concurrent.alias = cleaned_source
        concurrent.product = product
        concurrent.is_active = True
        concurrent.notes = "\n".join(part for part in [concurrent.notes.strip(), notes.strip()] if part)
        if not concurrent.created_by_id and getattr(actor, "is_authenticated", False):
            concurrent.created_by = actor
        concurrent.save(
            update_fields=["alias", "normalized_alias", "product", "is_active", "notes", "created_by", "updated_at"]
        )
        return concurrent, False
    alias._automatic_learning_action = "created"
    alias._automatic_learning_previous = None
    return alias, True


@transaction.atomic
def create_or_reuse_product(
    *,
    name,
    company=None,
    sku="",
    barcode="",
    dosage="",
    pack_size="",
    unit="",
    defaults=None,
    confirm_create=False,
):
    cleaned_name = " ".join(str(name or "").split())[:200]
    if not cleaned_name:
        raise ValidationError("Enter a Product name before creating it.")
    defaults = dict(defaults or {})
    sku = str(sku or defaults.get("sku") or "").strip()[:100]
    barcode = str(barcode or defaults.get("barcode") or "").strip()[:50]
    dosage = str(dosage or defaults.get("dosage") or "").strip()[:100]
    requested_pack_size = str(pack_size or defaults.get("pack_size") or "").strip()[:100]
    stored_pack_size = requested_pack_size or str(unit or "").strip()[:100]
    match = suggest_product_for_text(
        cleaned_name,
        company,
        sku=sku,
        barcode=barcode,
        dosage=dosage,
        pack_size=requested_pack_size,
        unit=unit,
    )
    if match.product:
        return ProductCreationResult(product=match.product, created=False, match=match)
    if match.method in {"identifier_conflict", "alias_conflict"}:
        return ProductCreationResult(
            product=None,
            created=False,
            match=match,
            requires_confirmation=True,
            warning=match.reason,
            creation_blocked=True,
        )
    if match.candidates and not confirm_create:
        return ProductCreationResult(
            product=None,
            created=False,
            match=match,
            requires_confirmation=True,
            warning=match.reason,
        )

    product_values = {
        "name": cleaned_name,
        "price": Decimal("0.01"),
        "stock_quantity": 0,
        "status": "draft",
        "show_price": False,
        "requires_manual_review": True,
        **defaults,
    }
    product_values.update(
        {
            "name": cleaned_name,
            "sku": sku,
            "barcode": barcode,
            "dosage": dosage,
            "pack_size": stored_pack_size,
        }
    )
    product = Product.objects.create(**product_values)
    return ProductCreationResult(
        product=product,
        created=True,
        match=match,
        warning=match.reason if match.candidates else "",
        override_used=bool(confirm_create and match.candidates),
    )
