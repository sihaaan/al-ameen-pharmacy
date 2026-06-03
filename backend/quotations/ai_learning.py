import hashlib
import json
import re
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from api.models import Product

from .ai_parsing import (
    AIParseCache,
    AIParseError,
    AIProviderUnavailable,
    _limit_text,
    _log_ai_parse,
    _render_pdf_images,
    _select_mode,
    get_ai_parse_availability,
    get_ai_parse_provider,
    settings_ai_status,
)
from .matching import create_product_alias, product_catalog_queryset, suggest_product_for_text
from .models import (
    Company,
    CompanyPriceHistory,
    HistoricalImportAISuggestion,
    HistoricalImportBatch,
    HistoricalPriceImport,
    HistoricalPriceImportLine,
    ProductAlias,
    QuotationAuditLog,
    QuotationSettings,
    normalize_label,
)
from .services import (
    _historical_ready_errors,
    apply_product_matches_to_historical_import,
    audit_log,
    commit_historical_price_import,
)


MAX_LEARNING_ROWS = 120
MAX_CANDIDATES_PER_ROW = 6
MAX_COMPANY_CANDIDATES = 6


HISTORICAL_NOISE_PATTERNS = [
    re.compile(r"^\s*$"),
    re.compile(r"^\s*(item|items|item description|material description|description|particulars)\s*$", re.IGNORECASE),
    re.compile(r"^\s*(qty|quantity|req quantity|unit|uom|price|rate|unit price|u price|amount|vat|total)\s*$", re.IGNORECASE),
    re.compile(
        r"\b(item description|material description|req quantity|unit price|u price)\b.*\b(total|amount|qty|quantity)\b",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*(sub[\s-]?total|total|grand total|net total|vat total|vat|amount)\b", re.IGNORECASE),
    re.compile(r"^\s*(quotation|quote|invoice|lpo|local purchase order|tender no|tender number)\b", re.IGNORECASE),
    re.compile(r"^\s*(date|from|seller|to|buyer|kind attn|attn|attention)\b", re.IGNORECASE),
    re.compile(r"^\s*(tel|fax|email|e-mail|website|www\.|https?://|p\s*o\s*box)\b", re.IGNORECASE),
    re.compile(r"^\s*(terms|conditions|payment terms|validity|delivery|prepared by|approved by|signature|stamp|yours truly|for al ameen)\b", re.IGNORECASE),
    re.compile(r"^\s*page\s+\d+(\s+of\s+\d+)?\s*$", re.IGNORECASE),
]

LINE_ACTIONS = {
    HistoricalImportAISuggestion.ACTION_MATCH_EXISTING_PRODUCT,
    HistoricalImportAISuggestion.ACTION_CREATE_COMPANY_ALIAS,
    HistoricalImportAISuggestion.ACTION_CREATE_NEW_PRODUCT,
    HistoricalImportAISuggestion.ACTION_NEEDS_MANUAL_REVIEW,
    HistoricalImportAISuggestion.ACTION_SKIP,
}
COMPANY_ACTIONS = {
    HistoricalImportAISuggestion.ACTION_MATCH_EXISTING_COMPANY,
    HistoricalImportAISuggestion.ACTION_CREATE_NEW_COMPANY,
    HistoricalImportAISuggestion.ACTION_NEEDS_MANUAL_REVIEW,
}


AI_LEARNING_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "company": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "action": {"type": "string", "enum": list(COMPANY_ACTIONS)},
                "company_id": {"type": "string"},
                "proposed_company_name": {"type": "string"},
                "confidence": {"type": "number"},
                "reason": {"type": "string"},
                "candidate_company_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "action",
                "company_id",
                "proposed_company_name",
                "confidence",
                "reason",
                "candidate_company_ids",
            ],
        },
        "rows": {
            "type": "array",
            "maxItems": MAX_LEARNING_ROWS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "line_id": {"type": "string"},
                    "action": {"type": "string", "enum": list(LINE_ACTIONS)},
                    "product_id": {"type": "string"},
                    "alias_text": {"type": "string"},
                    "new_product_name": {"type": "string"},
                    "new_product_unit": {"type": "string"},
                    "new_product_pack_size": {"type": "string"},
                    "new_product_dosage": {"type": "string"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                    "candidate_product_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "line_id",
                    "action",
                    "product_id",
                    "alias_text",
                    "new_product_name",
                    "new_product_unit",
                    "new_product_pack_size",
                    "new_product_dosage",
                    "confidence",
                    "reason",
                    "candidate_product_ids",
                ],
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
        "document_notes": {"type": "string"},
    },
    "required": ["company", "rows", "warnings", "document_notes"],
}


def _clean_text(value, max_length=None):
    text = " ".join(str(value or "").strip().split())
    return text[:max_length] if max_length else text


def _safe_int(value):
    try:
        if value in (None, ""):
            return None
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _confidence(value):
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if score > 1:
        score = score / 100
    return max(0.0, min(score, 1.0))


def _product_payload(product):
    return {
        "id": product.id,
        "name": product.name,
        "sku": product.sku or "",
        "barcode": product.barcode or "",
        "dosage": product.dosage or "",
        "pack_size": product.pack_size or "",
        "status": product.status,
    }


def _product_price_context(product, company=None):
    context = {
        "base_price": str(product.price) if getattr(product, "price", None) is not None else "",
        "last_company_price": "",
        "last_company_price_date": "",
        "recent_company_price_count": 0,
    }
    if not company:
        return context
    history = CompanyPriceHistory.objects.filter(company=company, product=product).order_by("-quoted_at", "-id")
    context["recent_company_price_count"] = history[:10].count()
    last_price = history.first()
    if last_price:
        context["last_company_price"] = str(last_price.unit_price)
        context["last_company_price_date"] = last_price.quoted_at.date().isoformat()
    return context


def _company_payload(company):
    return {
        "id": company.id,
        "name": company.name,
        "email": company.email or "",
        "phone": company.phone or "",
    }


def _historical_noise_reason(line):
    text = _clean_text(" ".join([line.item_name or "", line.raw_line or ""]))
    normalized = normalize_label(text)
    if not normalized:
        return "Blank or empty extracted row."

    compact_item = normalize_label(line.item_name or "")
    header_tokens = {
        "item",
        "items",
        "item description",
        "material description",
        "description",
        "particulars",
        "qty",
        "quantity",
        "req quantity",
        "unit",
        "uom",
        "unit price",
        "u price",
        "amount",
        "vat",
        "total",
    }
    if compact_item in header_tokens:
        return "Looks like a table header, not an item."

    for pattern in HISTORICAL_NOISE_PATTERNS:
        if pattern.search(text):
            return "Looks like document header, footer, total, or table-label noise."

    has_item_words = len([token for token in normalized.split() if len(token) >= 3])
    has_price_or_qty = any([line.quantity, line.unit_price, line.amount, line.line_total])
    if has_item_words == 0 and not has_price_or_qty:
        return "No usable product text or price data was extracted."
    return ""


def _candidate_products_for_line(line, company=None):
    seen = {}
    match = suggest_product_for_text(line.item_name, company)
    if match.product:
        seen[match.product.id] = match.product

    terms = [token for token in normalize_label(line.item_name).split() if len(token) >= 4][:5]
    query = Q()
    for token in terms:
        query |= Q(name__icontains=token) | Q(sku__icontains=token) | Q(barcode__icontains=token)
    if query:
        for product in product_catalog_queryset().filter(query).order_by("name")[:12]:
            seen.setdefault(product.id, product)
            if len(seen) >= MAX_CANDIDATES_PER_ROW:
                break

    return [
        {**_product_payload(product), "price_context": _product_price_context(product, company)}
        for product in list(seen.values())[:MAX_CANDIDATES_PER_ROW]
    ]


def _candidate_companies_for_import(historical_import):
    seen = {}
    if historical_import.company_id:
        seen[historical_import.company_id] = historical_import.company

    source = " ".join(
        [
            historical_import.suggested_company_name or "",
            historical_import.source_filename or "",
            historical_import.document_number or "",
        ]
    )
    terms = [token for token in normalize_label(source).split() if len(token) >= 4][:6]
    query = Q()
    for token in terms:
        query |= Q(name__icontains=token)
    if query:
        for company in Company.objects.filter(query, is_active=True).order_by("name")[:12]:
            seen.setdefault(company.id, company)
            if len(seen) >= MAX_COMPANY_CANDIDATES:
                break

    return [_company_payload(company) for company in list(seen.values())[:MAX_COMPANY_CANDIDATES]]


def _existing_alias_context(historical_import):
    if not historical_import.company_id:
        return []
    aliases = (
        ProductAlias.objects.filter(Q(company=historical_import.company) | Q(company__isnull=True), is_active=True)
        .select_related("company", "product")
        .order_by("company_id", "alias")[:40]
    )
    return [
        {
            "scope": alias.company.name if alias.company_id else "global",
            "alias": alias.alias,
            "product_id": alias.product_id,
            "product_name": alias.product.name,
        }
        for alias in aliases
    ]


def build_learning_context(historical_import):
    candidate_companies = _candidate_companies_for_import(historical_import)
    lines = []
    noise_rows = []
    for line in historical_import.lines.order_by("sort_order", "id")[:MAX_LEARNING_ROWS]:
        noise_reason = _historical_noise_reason(line)
        if noise_reason:
            noise_rows.append({"line": line, "reason": noise_reason})
            continue
        lines.append(
            {
                "line_id": line.id,
                "item_name": line.item_name,
                "quantity": str(line.quantity or ""),
                "unit": line.unit or "",
                "unit_price": str(line.unit_price or ""),
                "line_total": str(line.line_total or ""),
                "raw_line": line.raw_line or "",
                "parse_confidence": line.parse_confidence,
                "current_product_id": line.product_id or "",
                "current_product_name": line.product.name if line.product_id else "",
                "candidate_products": _candidate_products_for_line(line, historical_import.company),
            }
        )

    payload = {
        "task": "Suggest review-only company, product, alias, new product, or skip decisions for old finalized quotation import rows.",
        "rules": [
            "Return suggestions only. Do not create, commit, overwrite, or delete anything.",
            "Use only candidate Product IDs for existing-product or alias targets.",
            "Prefer company-specific alias when imported item is just the customer's wording for an existing Product.",
            "Suggest a new Product only when candidates are unsuitable and the imported item is a real product.",
            "Mark ambiguous rows needs_manual_review.",
            "Missing Product match must not reduce parsing confidence; this is a separate product decision.",
        ],
        "document": {
            "historical_import_id": historical_import.id,
            "filename": historical_import.source_filename,
            "document_number": historical_import.document_number,
            "document_date": historical_import.document_date.isoformat() if historical_import.document_date else "",
            "suggested_company_name": historical_import.suggested_company_name,
            "selected_company_id": historical_import.company_id or "",
            "selected_company_name": historical_import.company.name if historical_import.company_id else "",
            "currency": historical_import.currency,
            "total": str(historical_import.total or ""),
        },
        "candidate_companies": candidate_companies,
        "existing_aliases": _existing_alias_context(historical_import),
        "rows": lines,
    }
    return (
        json.dumps(payload, ensure_ascii=True, indent=2),
        candidate_companies,
        {row["line_id"]: row["candidate_products"] for row in lines},
        noise_rows,
    )


def _learning_instructions(mode):
    return (
        "You are helping pharmacy staff review old finalized quotations/invoices. "
        "Return strict JSON review suggestions only. Do not invent IDs. "
        "For existing Product or alias suggestions, use one of the provided candidate Product IDs. "
        "For company match suggestions, use one of the provided candidate Company IDs. "
        "Use create_new_product only when no candidate is suitable. "
        "Use create_company_alias when the imported name is customer-specific wording for a candidate Product. "
        "Use needs_manual_review when uncertain. Use skip only for non-item rows. "
        f"Mode: {mode}."
    )


def _normalize_learning_result(raw_result, historical_import, candidate_companies, line_candidate_products):
    if not isinstance(raw_result, dict):
        raise AIParseError("AI learning provider returned an unsupported response shape.")
    if not isinstance(raw_result.get("rows"), list):
        raise AIParseError("AI learning provider response did not include row suggestions.")
    company_result = raw_result.get("company") if isinstance(raw_result.get("company"), dict) else {}
    warnings = [_clean_text(warning) for warning in raw_result.get("warnings", []) if _clean_text(warning)]

    company_candidate_ids = {candidate["id"] for candidate in candidate_companies}
    normalized = {"company": None, "rows": [], "warnings": warnings, "document_notes": _clean_text(raw_result.get("document_notes"))}

    company_action = company_result.get("action") or HistoricalImportAISuggestion.ACTION_NEEDS_MANUAL_REVIEW
    if company_action not in COMPANY_ACTIONS:
        company_action = HistoricalImportAISuggestion.ACTION_NEEDS_MANUAL_REVIEW
    company_id = _safe_int(company_result.get("company_id"))
    if company_id and company_id not in company_candidate_ids:
        warnings.append(f"Ignored invalid company candidate id {company_id}.")
        company_id = None
        company_action = HistoricalImportAISuggestion.ACTION_NEEDS_MANUAL_REVIEW
    normalized["company"] = {
        "action": company_action,
        "company_id": company_id,
        "proposed_company_name": _clean_text(company_result.get("proposed_company_name"), 255),
        "confidence": _confidence(company_result.get("confidence")),
        "reason": _clean_text(company_result.get("reason")),
        "candidate_company_ids": [str(candidate_id) for candidate_id in company_result.get("candidate_company_ids", [])],
        "raw": company_result,
    }

    valid_line_ids = set(line_candidate_products.keys())
    for row in raw_result.get("rows", [])[:MAX_LEARNING_ROWS]:
        if not isinstance(row, dict):
            warnings.append("Skipped invalid AI row suggestion.")
            continue
        line_id = _safe_int(row.get("line_id"))
        if line_id not in valid_line_ids:
            warnings.append(f"Skipped AI suggestion for unknown line id {row.get('line_id')}.")
            continue
        action = row.get("action") or HistoricalImportAISuggestion.ACTION_NEEDS_MANUAL_REVIEW
        if action not in LINE_ACTIONS:
            action = HistoricalImportAISuggestion.ACTION_NEEDS_MANUAL_REVIEW
        product_id = _safe_int(row.get("product_id"))
        allowed_product_ids = {candidate["id"] for candidate in line_candidate_products.get(line_id, [])}
        if product_id and product_id not in allowed_product_ids:
            warnings.append(f"Line {line_id}: ignored invalid product candidate id {product_id}.")
            product_id = None
            action = HistoricalImportAISuggestion.ACTION_NEEDS_MANUAL_REVIEW
        if action in {
            HistoricalImportAISuggestion.ACTION_MATCH_EXISTING_PRODUCT,
            HistoricalImportAISuggestion.ACTION_CREATE_COMPANY_ALIAS,
        } and not product_id:
            action = HistoricalImportAISuggestion.ACTION_NEEDS_MANUAL_REVIEW
        normalized["rows"].append(
            {
                "line_id": line_id,
                "action": action,
                "product_id": product_id,
                "alias_text": _clean_text(row.get("alias_text"), 255),
                "new_product_name": _clean_text(row.get("new_product_name"), 255),
                "new_product_unit": _clean_text(row.get("new_product_unit"), 80),
                "new_product_pack_size": _clean_text(row.get("new_product_pack_size"), 120),
                "new_product_dosage": _clean_text(row.get("new_product_dosage"), 120),
                "confidence": _confidence(row.get("confidence")),
                "reason": _clean_text(row.get("reason")),
                "candidate_product_ids": [str(candidate_id) for candidate_id in row.get("candidate_product_ids", [])],
                "raw": row,
            }
        )
    return normalized


def _store_learning_suggestions(historical_import, normalized_result, candidate_companies, line_candidate_products, actor, noise_rows=None):
    HistoricalImportAISuggestion.objects.filter(
        historical_import=historical_import,
        status__in=[
            HistoricalImportAISuggestion.STATUS_PENDING,
            HistoricalImportAISuggestion.STATUS_FAILED,
            HistoricalImportAISuggestion.STATUS_CONFLICT,
            HistoricalImportAISuggestion.STATUS_REJECTED,
        ],
    ).delete()

    created = []
    company = normalized_result.get("company") or {}
    if company:
        created.append(
            HistoricalImportAISuggestion.objects.create(
                batch=historical_import.batch,
                historical_import=historical_import,
                suggestion_type=HistoricalImportAISuggestion.TYPE_COMPANY,
                action=company["action"],
                suggested_company_id=company.get("company_id"),
                proposed_company_name=company.get("proposed_company_name", ""),
                confidence=company.get("confidence", 0.0),
                reason=company.get("reason", ""),
                candidate_companies=candidate_companies,
                raw_ai_payload=company.get("raw", {}),
                created_by=actor if getattr(actor, "is_authenticated", False) else None,
            )
        )

    lines_by_id = {
        line.id: line
        for line in historical_import.lines.select_related("product").order_by("sort_order", "id")
    }
    for row in normalized_result.get("rows", []):
        line = lines_by_id.get(row["line_id"])
        if not line:
            continue
        created.append(
            HistoricalImportAISuggestion.objects.create(
                batch=historical_import.batch,
                historical_import=historical_import,
                line=line,
                suggestion_type=HistoricalImportAISuggestion.TYPE_LINE,
                action=row["action"],
                suggested_product_id=row.get("product_id"),
                alias_text=row.get("alias_text") or line.item_name,
                proposed_product_name=row.get("new_product_name") or line.item_name,
                proposed_unit=row.get("new_product_unit") or line.unit,
                proposed_pack_size=row.get("new_product_pack_size") or line.unit,
                proposed_dosage=row.get("new_product_dosage", ""),
                confidence=row.get("confidence", 0.0),
                reason=row.get("reason", ""),
                candidate_products=line_candidate_products.get(line.id, []),
                raw_ai_payload=row.get("raw", {}),
                created_by=actor if getattr(actor, "is_authenticated", False) else None,
            )
        )
    for noise in noise_rows or []:
        line = noise["line"]
        created.append(
            HistoricalImportAISuggestion.objects.create(
                batch=historical_import.batch,
                historical_import=historical_import,
                line=line,
                suggestion_type=HistoricalImportAISuggestion.TYPE_LINE,
                action=HistoricalImportAISuggestion.ACTION_SKIP,
                alias_text=line.item_name,
                proposed_product_name=line.item_name,
                confidence=1.0,
                reason=noise["reason"],
                raw_ai_payload={"deterministic_noise_gate": True},
                created_by=actor if getattr(actor, "is_authenticated", False) else None,
            )
        )
    return created


def generate_historical_import_learning_suggestions(historical_import, actor=None, *, requested_mode="auto"):
    historical_import = (
        HistoricalPriceImport.objects.select_related("company", "batch")
        .prefetch_related("lines__product")
        .get(pk=historical_import.pk)
    )
    if historical_import.status in {HistoricalPriceImport.STATUS_COMMITTED, HistoricalPriceImport.STATUS_CANCELLED}:
        raise AIParseError("Committed or cancelled historical imports cannot be AI-reviewed.")

    settings_obj = QuotationSettings.get_solo()
    status_info = settings_ai_status(settings_obj)
    if status_info["status"] != "ai_available":
        raise AIProviderUnavailable(status_info["label"])

    context, candidate_companies, line_candidate_products, noise_rows = build_learning_context(historical_import)
    preview = {
        "source_type": historical_import.source_type,
        "source_sha256": historical_import.source_sha256,
        "source_file_ref": historical_import.source_file_ref,
        "meta": historical_import.parse_meta or {},
    }
    mode = _select_mode(preview, requested_mode=requested_mode, allow_vision=True, settings_obj=settings_obj)
    images = []
    page_count = int((historical_import.parse_meta or {}).get("page_count") or 0)
    if mode == AIParseCache.MODE_VISION:
        images, rendered_page_count = _render_pdf_images(historical_import.source_file_ref)
        page_count = page_count or rendered_page_count

    availability = get_ai_parse_availability()
    provider_name = availability["provider"]
    model = availability["vision_model"] if mode == AIParseCache.MODE_VISION else availability["text_model"]
    if not model:
        raise AIProviderUnavailable("AI unavailable: no model configured for learning suggestions.")
    context = _limit_text(context)
    image_hashes = [hashlib.sha256(image.encode("utf-8")).hexdigest() for image in images]
    context_hash = hashlib.sha256((context + "".join(image_hashes)).encode("utf-8")).hexdigest()
    cache_key = hashlib.sha256(
        f"learning:{historical_import.source_sha256}:{provider_name}:{model}:{mode}:{context_hash}".encode("utf-8")
    ).hexdigest()
    cached = AIParseCache.objects.filter(cache_key=cache_key).first()
    if cached:
        normalized = cached.result
        cache_hit = True
        _log_ai_parse(
            actor=actor,
            provider=provider_name,
            model=model,
            mode=mode,
            preview=preview,
            context_hash=context_hash,
            cache_hit=True,
            text_length=len(context),
            page_count=page_count,
            image_count=len(images),
            success=True,
        )
    else:
        provider = get_ai_parse_provider(provider_name)
        try:
            raw_result, usage = provider.clean_rows(
                mode=mode,
                model=model,
                instructions=_learning_instructions(mode),
                text_context=context,
                image_data_urls=images,
                json_schema=AI_LEARNING_JSON_SCHEMA,
                schema_name="quotation_historical_learning",
            )
            normalized = _normalize_learning_result(raw_result, historical_import, candidate_companies, line_candidate_products)
            AIParseCache.objects.update_or_create(
                cache_key=cache_key,
                defaults={
                    "source_sha256": historical_import.source_sha256,
                    "context_hash": context_hash,
                    "mode": mode,
                    "provider": provider_name,
                    "model": model,
                    "result": normalized,
                },
            )
            cache_hit = False
            _log_ai_parse(
                actor=actor,
                provider=provider_name,
                model=model,
                mode=mode,
                preview=preview,
                context_hash=context_hash,
                cache_hit=False,
                text_length=len(context),
                page_count=page_count,
                image_count=len(images),
                usage=usage,
                success=True,
            )
        except Exception as exc:
            _log_ai_parse(
                actor=actor,
                provider=provider_name,
                model=model,
                mode=mode,
                preview=preview,
                context_hash=context_hash,
                cache_hit=False,
                text_length=len(context),
                page_count=page_count,
                image_count=len(images),
                success=False,
                error=str(exc),
            )
            if isinstance(exc, AIParseError):
                raise
            raise AIParseError(str(exc)) from exc

    suggestions = _store_learning_suggestions(historical_import, normalized, candidate_companies, line_candidate_products, actor, noise_rows)
    historical_import.parse_meta = {
        **(historical_import.parse_meta or {}),
        "ai_learning_last_run_at": timezone.now().isoformat(),
        "ai_learning_last_mode": mode,
        "ai_learning_cache_hit": cache_hit,
        "ai_learning_warning_count": len(normalized.get("warnings", [])),
        "ai_learning_noise_skip_count": len(noise_rows),
    }
    historical_import.save(update_fields=["parse_meta", "updated_at"])
    audit_log(
        actor,
        QuotationAuditLog.ACTION_UPDATED,
        historical_import,
        message=f"Generated {len(suggestions)} AI learning suggestion(s) for historical import.",
        changes={"suggestion_ids": [suggestion.id for suggestion in suggestions], "cache_hit": cache_hit, "mode": mode},
        company=historical_import.company,
    )
    return suggestions, {"cache_hit": cache_hit, "mode": mode, "warnings": normalized.get("warnings", [])}


def generate_batch_learning_suggestions(batch, import_ids=None, actor=None, requested_mode="auto"):
    queryset = batch.imports.exclude(status__in=[HistoricalPriceImport.STATUS_COMMITTED, HistoricalPriceImport.STATUS_CANCELLED])
    if import_ids:
        queryset = queryset.filter(id__in=import_ids)
    results = []
    for historical_import in queryset.order_by("created_at", "id"):
        try:
            suggestions, meta = generate_historical_import_learning_suggestions(
                historical_import,
                actor=actor,
                requested_mode=requested_mode,
            )
            results.append(
                {
                    "import_id": historical_import.id,
                    "status": "suggested",
                    "suggestion_count": len(suggestions),
                    **meta,
                }
            )
        except AIParseError as exc:
            existing_count = HistoricalImportAISuggestion.objects.filter(
                historical_import=historical_import,
                status=HistoricalImportAISuggestion.STATUS_PENDING,
            ).count()
            results.append(
                {
                    "import_id": historical_import.id,
                    "filename": historical_import.source_filename,
                    "status": "failed",
                    "message": _friendly_ai_failure_message(str(exc)),
                    "raw_message": str(exc),
                    "previous_suggestion_count": existing_count,
                    "showing_previous_suggestions": existing_count > 0,
                }
            )
    refresh_historical_import_batch_summary(batch)
    return _summary_from_results(results), results


def _friendly_ai_failure_message(message):
    text = str(message or "").strip()
    lowered = text.lower()
    if "missing api key" in lowered or "api key" in lowered:
        return "AI unavailable: missing API key. Check OPENAI_API_KEY on the Railway backend service."
    if "disabled" in lowered and "vision" in lowered:
        return "AI vision cleanup is disabled in Quotation Settings."
    if "disabled" in lowered:
        return "AI parsing is disabled in Quotation Settings or by the global environment switch."
    if "render" in lowered and "pdf" in lowered:
        return "AI could not render the source PDF for vision analysis."
    if "source pdf" in lowered or "source file" in lowered or "private storage" in lowered:
        return "Source file is unavailable for AI analysis. Re-upload the PDF if source preview is missing."
    if "timeout" in lowered:
        return "AI request timed out. Retry failed files or reduce the batch size."
    if "rate" in lowered and "limit" in lowered:
        return "AI rate limit reached. Wait briefly and retry failed files."
    if not text:
        return "AI request failed with no provider details. Retry failed files and check backend logs."
    return text


def _summary_from_results(results):
    counts = {}
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    auto_applied = sum(int(result.get("auto_applied_similar") or 0) for result in results)
    return {
        **counts,
        "suggested": counts.get("suggested", 0),
        "applied": counts.get("applied", 0),
        "applied_similar": counts.get("applied_similar", 0),
        "auto_applied_similar": auto_applied,
        "conflict": counts.get("conflict", 0),
        "committed": counts.get("committed", 0),
        "blocked": counts.get("blocked", 0),
        "failed": counts.get("failed", 0),
        "results": results,
    }


def _find_existing_product_by_normalized_name(name, *, pack_size="", dosage="", unit=""):
    normalized = normalize_label(name)
    if not normalized:
        return None
    candidates = list(Product.objects.exclude(status="archived").filter(name__iexact=name.strip())[:10])
    if not candidates:
        candidates = [
            product
            for product in Product.objects.exclude(status="archived").filter(name__icontains=name.strip()[:80])[:50]
            if normalize_label(product.name) == normalized
        ]
    if not candidates:
        return None
    desired_pack = normalize_label(pack_size or unit)
    desired_dosage = normalize_label(dosage)
    if desired_pack or desired_dosage:
        for product in candidates:
            product_pack = normalize_label(getattr(product, "pack_size", "") or "")
            product_dosage = normalize_label(getattr(product, "dosage", "") or "")
            if desired_pack and product_pack and desired_pack != product_pack:
                continue
            if desired_dosage and product_dosage and desired_dosage != product_dosage:
                continue
            return product
    return candidates[0]


def _line_units_are_compatible(source_line, target_line):
    source_unit = normalize_label(source_line.unit or "")
    target_unit = normalize_label(target_line.unit or "")
    return not source_unit or not target_unit or source_unit == target_unit


def _matching_pending_line_suggestions(source_suggestion):
    line = source_suggestion.line
    batch = source_suggestion.batch
    if not line or not batch or not line.normalized_item_name:
        return HistoricalImportAISuggestion.objects.none()
    return (
        HistoricalImportAISuggestion.objects.select_for_update()
        .select_related("historical_import__company", "batch", "line", "suggested_company", "suggested_product")
        .filter(
            batch=batch,
            suggestion_type=HistoricalImportAISuggestion.TYPE_LINE,
            status=HistoricalImportAISuggestion.STATUS_PENDING,
            line__normalized_item_name=line.normalized_item_name,
        )
        .exclude(id=source_suggestion.id)
        .order_by("historical_import_id", "line__sort_order", "id")
    )


def _ready_or_review(line):
    return (
        HistoricalPriceImportLine.STATUS_READY
        if not _historical_ready_errors(line.historical_import, line)
        else HistoricalPriceImportLine.STATUS_NEEDS_REVIEW
    )


def _safe_create_company_alias(suggestion, actor):
    historical_import = suggestion.historical_import
    line = suggestion.line
    if not historical_import.company_id:
        raise ValidationError("Select or approve a company before creating company aliases.")
    if not suggestion.suggested_product_id:
        raise ValidationError("Select a target Product before creating an alias.")
    alias_text = suggestion.alias_text or line.item_name
    normalized = normalize_label(alias_text)
    existing = ProductAlias.objects.filter(company=historical_import.company, normalized_alias=normalized).first()
    if existing and existing.product_id != suggestion.suggested_product_id:
        raise ValidationError(
            f"Alias '{alias_text}' already points to '{existing.product.name}' for this company."
        )
    alias, _ = create_product_alias(
        alias_text=alias_text,
        product=suggestion.suggested_product,
        company=historical_import.company,
        actor=actor,
        notes=f"Approved from historical import AI suggestion {suggestion.pk}.",
    )
    line.product = suggestion.suggested_product
    line.match_reason = f"Approved AI alias '{alias.alias}'."
    line.status = _ready_or_review(line)
    line.save(update_fields=["product", "match_reason", "status", "updated_at"])
    return {
        "alias_id": alias.id,
        "alias_text": alias.alias,
        "product_id": suggestion.suggested_product_id,
        "product_name": suggestion.suggested_product.name,
        "row_status": line.status,
        "message": f"Alias approved: '{alias.alias}' -> {suggestion.suggested_product.name}.",
    }


def _apply_exact_matching_line_suggestions(source_suggestion, actor, source_result):
    action = source_suggestion.action
    if action not in {
        HistoricalImportAISuggestion.ACTION_MATCH_EXISTING_PRODUCT,
        HistoricalImportAISuggestion.ACTION_CREATE_COMPANY_ALIAS,
        HistoricalImportAISuggestion.ACTION_CREATE_NEW_PRODUCT,
        HistoricalImportAISuggestion.ACTION_SKIP,
    }:
        return []
    line = source_suggestion.line
    if not line:
        return []

    results = []
    product_id = source_result.get("product_id")
    propagated_product = Product.objects.filter(pk=product_id).first() if product_id else source_suggestion.suggested_product
    for target in _matching_pending_line_suggestions(source_suggestion):
        if not target.line or not _line_units_are_compatible(line, target.line):
            continue
        if action == HistoricalImportAISuggestion.ACTION_CREATE_COMPANY_ALIAS:
            if target.historical_import.company_id != source_suggestion.historical_import.company_id:
                continue
            target.suggested_product_id = product_id or source_suggestion.suggested_product_id
            if propagated_product:
                target.suggested_product = propagated_product
            target.alias_text = target.alias_text or target.line.item_name
        elif product_id and action in {
            HistoricalImportAISuggestion.ACTION_MATCH_EXISTING_PRODUCT,
            HistoricalImportAISuggestion.ACTION_CREATE_NEW_PRODUCT,
        }:
            target.suggested_product_id = product_id
            if propagated_product:
                target.suggested_product = propagated_product
            target.action = HistoricalImportAISuggestion.ACTION_MATCH_EXISTING_PRODUCT
            target.reason = (
                f"Reused staff-approved mapping from exact same imported item '{line.item_name}'. "
                f"Original AI reason: {target.reason}"
            )[:1000]
        elif action == HistoricalImportAISuggestion.ACTION_SKIP:
            target.action = HistoricalImportAISuggestion.ACTION_SKIP
        try:
            result = _apply_one_suggestion(target, actor)
            target.status = HistoricalImportAISuggestion.STATUS_APPLIED
            target.error_message = ""
            target.applied_by = actor if getattr(actor, "is_authenticated", False) else None
            target.applied_at = timezone.now()
            target.save(
                update_fields=[
                    "action",
                    "suggested_product",
                    "alias_text",
                    "reason",
                    "status",
                    "error_message",
                    "applied_by",
                    "applied_at",
                    "updated_at",
                ]
            )
            results.append(
                {
                    "suggestion_id": target.id,
                    "historical_import_id": target.historical_import_id,
                    "status": "applied_similar",
                    "message": f"Applied the same exact-item decision to '{target.line.item_name}'.",
                    **result,
                }
            )
        except ValidationError as exc:
            target.status = HistoricalImportAISuggestion.STATUS_CONFLICT
            target.error_message = " ".join(str(part) for part in getattr(exc, "messages", [str(exc)]))
            target.save(update_fields=["status", "error_message", "updated_at"])
            results.append({"suggestion_id": target.id, "status": "conflict", "message": target.error_message})
    return results


@transaction.atomic
def apply_historical_ai_suggestions(suggestion_ids, actor):
    if not suggestion_ids:
        raise ValidationError("Select at least one AI suggestion to apply.")
    suggestions = list(
        HistoricalImportAISuggestion.objects.select_for_update()
        .select_related("historical_import__company", "batch", "line", "suggested_company", "suggested_product")
        .filter(id__in=suggestion_ids)
        .order_by("historical_import_id", "line__sort_order", "id")
    )
    found_ids = {suggestion.id for suggestion in suggestions}
    missing = [suggestion_id for suggestion_id in suggestion_ids if suggestion_id not in found_ids]
    if missing:
        raise ValidationError(f"AI suggestions were not found: {missing}")

    results = []
    touched_import_ids = set()
    auto_applied_ids = set()
    for suggestion in suggestions:
        if suggestion.id in auto_applied_ids:
            continue
        if suggestion.status != HistoricalImportAISuggestion.STATUS_PENDING:
            results.append(
                {
                    "suggestion_id": suggestion.id,
                    "status": "already_applied" if suggestion.status == HistoricalImportAISuggestion.STATUS_APPLIED else "failed",
                    "message": f"Suggestion is already {suggestion.status}.",
                }
            )
            continue
        try:
            result = _apply_one_suggestion(suggestion, actor)
            suggestion.status = HistoricalImportAISuggestion.STATUS_APPLIED
            suggestion.error_message = ""
            suggestion.applied_by = actor if getattr(actor, "is_authenticated", False) else None
            suggestion.applied_at = timezone.now()
            suggestion.save(update_fields=["status", "error_message", "applied_by", "applied_at", "updated_at"])
            similar_results = _apply_exact_matching_line_suggestions(suggestion, actor, result)
            results.append(
                {
                    "suggestion_id": suggestion.id,
                    "status": "applied",
                    "auto_applied_similar": len([item for item in similar_results if item.get("status") == "applied_similar"]),
                    **result,
                }
            )
            results.extend(similar_results)
            auto_applied_ids.update(
                item.get("suggestion_id")
                for item in similar_results
                if item.get("status") == "applied_similar"
            )
            touched_import_ids.add(suggestion.historical_import_id)
            touched_import_ids.update(
                item.get("historical_import_id")
                for item in similar_results
                if item.get("historical_import_id")
            )
        except ValidationError as exc:
            suggestion.status = HistoricalImportAISuggestion.STATUS_CONFLICT
            suggestion.error_message = " ".join(str(part) for part in getattr(exc, "messages", [str(exc)]))
            suggestion.save(update_fields=["status", "error_message", "updated_at"])
            results.append({"suggestion_id": suggestion.id, "status": "conflict", "message": suggestion.error_message})

    for historical_import in HistoricalPriceImport.objects.filter(id__in=touched_import_ids).select_related("company"):
        apply_product_matches_to_historical_import(historical_import, actor)

    return _summary_from_results(results), results


def _apply_one_suggestion(suggestion, actor):
    historical_import = suggestion.historical_import
    if historical_import.status in {HistoricalPriceImport.STATUS_COMMITTED, HistoricalPriceImport.STATUS_CANCELLED}:
        raise ValidationError("Committed or cancelled imports cannot be changed.")

    if suggestion.suggestion_type == HistoricalImportAISuggestion.TYPE_COMPANY:
        if suggestion.action == HistoricalImportAISuggestion.ACTION_MATCH_EXISTING_COMPANY:
            if not suggestion.suggested_company_id:
                raise ValidationError("Select a company before applying this suggestion.")
            historical_import.company = suggestion.suggested_company
            historical_import.save(update_fields=["company", "updated_at"])
            return {
                "company_id": suggestion.suggested_company_id,
                "company_name": suggestion.suggested_company.name,
                "message": f"Company linked: {suggestion.suggested_company.name}.",
            }
        if suggestion.action == HistoricalImportAISuggestion.ACTION_CREATE_NEW_COMPANY:
            name = suggestion.proposed_company_name or historical_import.suggested_company_name
            if not name:
                raise ValidationError("Enter a company name before creating a company.")
            company = Company.objects.filter(normalized_name=normalize_label(name)).first()
            if not company:
                company = Company.objects.create(name=name[:255])
            historical_import.company = company
            historical_import.save(update_fields=["company", "updated_at"])
            return {"company_id": company.id, "company_name": company.name, "message": f"Company linked: {company.name}."}
        historical_import.status = HistoricalPriceImport.STATUS_REVIEWED
        historical_import.save(update_fields=["status", "updated_at"])
        return {"message": "Company left for manual review."}

    line = suggestion.line
    if not line:
        raise ValidationError("Line suggestion is missing its source row.")
    if line.status in {HistoricalPriceImportLine.STATUS_COMMITTED, HistoricalPriceImportLine.STATUS_DUPLICATE}:
        raise ValidationError("Committed or duplicate rows cannot be changed.")

    if suggestion.action == HistoricalImportAISuggestion.ACTION_MATCH_EXISTING_PRODUCT:
        if not suggestion.suggested_product_id:
            raise ValidationError("Select a Product before applying this suggestion.")
        line.product = suggestion.suggested_product
        line.match_reason = f"Approved AI Product match: {suggestion.reason}"[:255]
        line.status = _ready_or_review(line)
        line.save(update_fields=["product", "match_reason", "status", "updated_at"])
        return {
            "product_id": suggestion.suggested_product_id,
            "product_name": suggestion.suggested_product.name,
            "row_status": line.status,
            "message": f"Product linked: {suggestion.suggested_product.name}. Row is {line.status}.",
        }

    if suggestion.action == HistoricalImportAISuggestion.ACTION_CREATE_COMPANY_ALIAS:
        return _safe_create_company_alias(suggestion, actor)

    if suggestion.action == HistoricalImportAISuggestion.ACTION_CREATE_NEW_PRODUCT:
        name = suggestion.proposed_product_name or line.item_name
        product = _find_existing_product_by_normalized_name(
            name,
            pack_size=suggestion.proposed_pack_size,
            dosage=suggestion.proposed_dosage,
            unit=suggestion.proposed_unit or line.unit,
        )
        created = False
        if not product:
            product = Product.objects.create(
                name=name[:200],
                price=Decimal("0.01"),
                stock_quantity=0,
                status="draft",
                show_price=False,
                requires_manual_review=True,
                pack_size=(suggestion.proposed_pack_size or suggestion.proposed_unit or line.unit)[:100],
                dosage=suggestion.proposed_dosage[:100],
                short_description=f"Internal quotation item approved from {historical_import.source_filename}".strip(),
            )
            created = True
        line.product = product
        line.match_reason = "Approved AI new draft Product." if created else "Approved AI suggestion linked existing exact Product."
        line.status = _ready_or_review(line)
        line.save(update_fields=["product", "match_reason", "status", "updated_at"])
        return {
            "product_id": product.id,
            "product_name": product.name,
            "created": created,
            "row_status": line.status,
            "message": (
                f"Draft Product created: {product.name}. Row is {line.status}."
                if created
                else f"Existing exact Product reused: {product.name}. Row is {line.status}."
            ),
        }

    if suggestion.action == HistoricalImportAISuggestion.ACTION_SKIP:
        line.status = HistoricalPriceImportLine.STATUS_SKIPPED
        line.match_reason = f"Skipped by approved AI suggestion: {suggestion.reason}"[:255]
        line.save(update_fields=["status", "match_reason", "updated_at"])
        return {"row_status": line.status, "message": "Row skipped and will not be committed."}

    line.status = HistoricalPriceImportLine.STATUS_NEEDS_REVIEW
    line.match_reason = f"AI needs manual review: {suggestion.reason}"[:255]
    line.save(update_fields=["status", "match_reason", "updated_at"])
    return {"row_status": line.status, "message": "Row left in manual review."}


def refresh_historical_import_batch_summary(batch):
    batch = HistoricalImportBatch.objects.get(pk=batch.pk)
    _close_stale_batch_ai_suggestions(batch)
    imports = list(batch.imports.prefetch_related("lines").all())
    import_count = len(imports)
    committed = sum(1 for entry in imports if entry.status == HistoricalPriceImport.STATUS_COMMITTED)
    failed_files = len([item for item in batch.summary.get("files", []) if item.get("status") == "failed"])
    duplicate_files = len([item for item in batch.summary.get("files", []) if item.get("status") == "duplicate"])
    ready_rows = 0
    needs_review_rows = 0
    skipped_rows = 0
    committed_rows = 0
    duplicate_rows = 0
    total_rows = 0
    company_ready = 0
    documents_missing_details = 0
    for entry in imports:
        if entry.company_id:
            company_ready += 1
        if not entry.company_id or not entry.document_date:
            documents_missing_details += 1
        for line in entry.lines.all():
            total_rows += 1
            if line.status == HistoricalPriceImportLine.STATUS_READY:
                ready_rows += 1
            elif line.status == HistoricalPriceImportLine.STATUS_NEEDS_REVIEW:
                needs_review_rows += 1
            elif line.status == HistoricalPriceImportLine.STATUS_SKIPPED:
                skipped_rows += 1
            elif line.status == HistoricalPriceImportLine.STATUS_COMMITTED:
                committed_rows += 1
            elif line.status == HistoricalPriceImportLine.STATUS_DUPLICATE:
                duplicate_rows += 1
    suggestions = list(batch.ai_suggestions.all())
    pending_suggestions = [suggestion for suggestion in suggestions if suggestion.status == HistoricalImportAISuggestion.STATUS_PENDING]
    suggestion_counts = {}
    pending_by_action = {}
    applied_by_action = {}
    conflict_by_action = {}
    high_confidence_pending = 0
    for suggestion in suggestions:
        suggestion_counts[suggestion.status] = suggestion_counts.get(suggestion.status, 0) + 1
        if suggestion.status == HistoricalImportAISuggestion.STATUS_PENDING:
            pending_by_action[suggestion.action] = pending_by_action.get(suggestion.action, 0) + 1
            if suggestion.confidence >= 0.85:
                high_confidence_pending += 1
        elif suggestion.status == HistoricalImportAISuggestion.STATUS_APPLIED:
            applied_by_action[suggestion.action] = applied_by_action.get(suggestion.action, 0) + 1
        elif suggestion.status == HistoricalImportAISuggestion.STATUS_CONFLICT:
            conflict_by_action[suggestion.action] = conflict_by_action.get(suggestion.action, 0) + 1
    batch.summary = {
        **(batch.summary or {}),
        "import_count": import_count,
        "committed_import_count": committed,
        "failed_file_count": failed_files,
        "duplicate_file_count": duplicate_files,
        "company_ready_count": company_ready,
        "documents_missing_details_count": documents_missing_details,
        "total_row_count": total_rows,
        "ready_row_count": ready_rows,
        "needs_review_row_count": needs_review_rows,
        "skipped_row_count": skipped_rows,
        "committed_row_count": committed_rows,
        "duplicate_row_count": duplicate_rows,
        "pending_suggestion_count": len(pending_suggestions),
        "suggestion_status_counts": suggestion_counts,
        "pending_suggestion_action_counts": pending_by_action,
        "applied_suggestion_action_counts": applied_by_action,
        "conflict_suggestion_action_counts": conflict_by_action,
        "high_confidence_pending_suggestion_count": high_confidence_pending,
        "unresolved_count": needs_review_rows + len(pending_suggestions) + sum(conflict_by_action.values()) + documents_missing_details,
    }
    if failed_files and not import_count:
        batch.status = HistoricalImportBatch.STATUS_FAILED
    elif import_count and committed == import_count:
        batch.status = HistoricalImportBatch.STATUS_COMMITTED
    elif ready_rows or needs_review_rows or batch.summary["pending_suggestion_count"]:
        batch.status = HistoricalImportBatch.STATUS_NEEDS_REVIEW
    elif import_count:
        batch.status = HistoricalImportBatch.STATUS_PARSED
    batch.save(update_fields=["summary", "status", "updated_at"])
    return batch


def _close_stale_batch_ai_suggestions(batch):
    now = timezone.now()
    stale_suggestions = []
    for suggestion in (
        batch.ai_suggestions.select_related("historical_import", "line", "suggested_company")
        .filter(status=HistoricalImportAISuggestion.STATUS_PENDING)
    ):
        close_message = ""
        close_as = ""
        historical_import = suggestion.historical_import
        line = suggestion.line

        if historical_import.status == HistoricalPriceImport.STATUS_COMMITTED:
            close_as = HistoricalImportAISuggestion.STATUS_APPLIED
            close_message = "Closed because this historical import has already been committed."
        elif historical_import.status == HistoricalPriceImport.STATUS_CANCELLED:
            close_as = HistoricalImportAISuggestion.STATUS_REJECTED
            close_message = "Closed because this historical import was cancelled or removed from the batch."
        elif line and line.status == HistoricalPriceImportLine.STATUS_COMMITTED:
            close_as = HistoricalImportAISuggestion.STATUS_APPLIED
            close_message = "Closed because this source row has already been committed."
        elif (
            suggestion.suggestion_type == HistoricalImportAISuggestion.TYPE_COMPANY
            and suggestion.action == HistoricalImportAISuggestion.ACTION_MATCH_EXISTING_COMPANY
            and suggestion.suggested_company_id
            and historical_import.company_id == suggestion.suggested_company_id
        ):
            close_as = HistoricalImportAISuggestion.STATUS_APPLIED
            close_message = f"Closed because company is already linked: {suggestion.suggested_company.name}."

        if close_as:
            suggestion.status = close_as
            suggestion.error_message = close_message
            suggestion.updated_at = now
            if close_as == HistoricalImportAISuggestion.STATUS_APPLIED:
                suggestion.applied_at = suggestion.applied_at or now
            stale_suggestions.append(suggestion)

    if stale_suggestions:
        HistoricalImportAISuggestion.objects.bulk_update(
            stale_suggestions,
            ["status", "error_message", "applied_at", "updated_at"],
        )


def append_batch_file_result(batch, result):
    summary = batch.summary or {}
    files = list(summary.get("files", []))
    files.append(result)
    batch.summary = {**summary, "files": files}
    batch.save(update_fields=["summary", "updated_at"])
    return refresh_historical_import_batch_summary(batch)


def _commit_blockers_for_import(historical_import):
    blockers = []
    if historical_import.status == HistoricalPriceImport.STATUS_COMMITTED:
        blockers.append("already committed")
    if historical_import.status == HistoricalPriceImport.STATUS_CANCELLED:
        blockers.append("cancelled import")
    if not historical_import.company_id:
        blockers.append("missing company")
    if not historical_import.document_number:
        blockers.append("missing document number")
    if not historical_import.document_date:
        blockers.append("missing document date")
    ready_count = historical_import.lines.filter(status=HistoricalPriceImportLine.STATUS_READY).count()
    if ready_count <= 0:
        blockers.append("no ready rows")
    unresolved_count = historical_import.lines.filter(status=HistoricalPriceImportLine.STATUS_NEEDS_REVIEW).count()
    return blockers, ready_count, unresolved_count


def commit_ready_imports_for_batch(batch, import_ids, actor):
    queryset = batch.imports.all()
    if import_ids:
        queryset = queryset.filter(id__in=import_ids)
    results = []
    for historical_import in queryset.order_by("created_at", "id"):
        blockers, ready_count, unresolved_count = _commit_blockers_for_import(historical_import)
        base_result = {
            "import_id": historical_import.id,
            "filename": historical_import.source_filename,
            "company_name": historical_import.company.name if historical_import.company_id else "",
            "document_number": historical_import.document_number,
            "ready_row_count": ready_count,
            "unresolved_row_count": unresolved_count,
        }
        if blockers:
            results.append(
                {
                    **base_result,
                    "status": "blocked",
                    "message": "; ".join(blockers),
                    "blockers": blockers,
                }
            )
            continue
        try:
            committed = commit_historical_price_import(historical_import, actor)
            results.append(
                {
                    **base_result,
                    "status": "committed",
                    "quotation": committed.created_quotation_id,
                    "message": f"Committed {ready_count} ready row(s) to price history.",
                }
            )
        except ValidationError as exc:
            results.append(
                {
                    **base_result,
                    "status": "failed",
                    "message": " ".join(getattr(exc, "messages", [str(exc)])),
                }
            )
    refresh_historical_import_batch_summary(batch)
    return _summary_from_results(results), results
