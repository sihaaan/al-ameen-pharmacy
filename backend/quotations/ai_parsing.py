import base64
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from .import_rules import preserve_specific_item_details, standardize_item_display_name, summarize_lines
from .models import AIParseCache, AIParseLog, HistoricalPriceImport, Inquiry, QuotationSettings
from .private_storage import read_private_ref

try:
    import fitz
except Exception:  # pragma: no cover - optional runtime dependency guard
    fitz = None


AI_SOURCE_DETERMINISTIC = "deterministic_parse"
AI_SOURCE_TEXT = "ai_text_cleanup"
AI_SOURCE_VISION = "ai_vision_cleanup"
AI_STATUS_DISABLED = "ai_disabled_in_settings"
AI_STATUS_UNAVAILABLE = "ai_unavailable_missing_api_key"
AI_STATUS_FAILED = "ai_failed_using_original_parse"
AI_STATUS_AVAILABLE = "ai_available"

MAX_ROWS = 250
VALID_PARSE_STATUSES = {"parsed", "needs_review", "ignored"}


AI_PARSE_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "rows": {
            "type": "array",
            "maxItems": MAX_ROWS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "item_name": {"type": "string"},
                    "quantity": {"type": "string"},
                    "unit": {"type": "string"},
                    "unit_price": {"type": "string"},
                    "vat_rate": {"type": "string"},
                    "vat_amount": {"type": "string"},
                    "line_total": {"type": "string"},
                    "pack_info": {"type": "string"},
                    "notes": {"type": "string"},
                    "raw_source_text": {"type": "string"},
                    "page_number": {"type": "string"},
                    "confidence": {"type": "number"},
                    "parse_status": {"type": "string", "enum": ["parsed", "needs_review", "ignored"]},
                    "reason": {"type": "string"},
                },
                "required": [
                    "item_name",
                    "quantity",
                    "unit",
                    "unit_price",
                    "vat_rate",
                    "vat_amount",
                    "line_total",
                    "pack_info",
                    "notes",
                    "raw_source_text",
                    "page_number",
                    "confidence",
                    "parse_status",
                    "reason",
                ],
            },
        },
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
        },
        "document_notes": {"type": "string"},
    },
    "required": ["rows", "warnings", "document_notes"],
}


class AIParseError(Exception):
    """Raised when an AI cleanup request cannot produce validated rows."""


class AIProviderUnavailable(AIParseError):
    """Raised when settings or environment make AI cleanup unavailable."""


class AIParseProvider:
    def clean_rows(self, *, mode, model, instructions, text_context, image_data_urls=None, json_schema=None, schema_name="quotation_import_parse"):
        raise NotImplementedError


class OpenAIResponsesParseProvider(AIParseProvider):
    endpoint = "https://api.openai.com/v1/responses"

    def __init__(self, api_key=None):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")

    def clean_rows(self, *, mode, model, instructions, text_context, image_data_urls=None, json_schema=None, schema_name="quotation_import_parse"):
        if not self.api_key:
            raise AIProviderUnavailable("AI unavailable: missing OpenAI API key.")
        if not model:
            raise AIProviderUnavailable("AI unavailable: no OpenAI model is configured.")

        user_content = [{"type": "input_text", "text": text_context}]
        for image_url in image_data_urls or []:
            user_content.append({"type": "input_image", "image_url": image_url})

        payload = {
            "model": model,
            "input": [
                {"role": "developer", "content": [{"type": "input_text", "text": instructions}]},
                {"role": "user", "content": user_content},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": json_schema or AI_PARSE_JSON_SCHEMA,
                    "strict": True,
                }
            },
        }
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        timeout = int(getattr(settings, "QUOTATION_AI_PARSE_TIMEOUT_SECONDS", 60))
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw_response = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise AIParseError(f"AI provider request failed with HTTP {exc.code}: {detail[:500]}") from exc
        except Exception as exc:
            raise AIParseError(f"AI provider request failed: {exc}") from exc

        output_text = _extract_openai_output_text(raw_response)
        if not output_text:
            raise AIParseError("AI provider returned no structured text output.")
        try:
            parsed = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise AIParseError("AI provider returned invalid JSON.") from exc
        return parsed, raw_response.get("usage") or {}


class AnthropicParseProvider(AIParseProvider):
    def clean_rows(self, *, mode, model, instructions, text_context, image_data_urls=None, json_schema=None, schema_name="quotation_import_parse"):
        raise AIProviderUnavailable("Anthropic AI parsing is not implemented in this release. Use the OpenAI provider.")


def get_ai_parse_provider(provider_name=None):
    provider_name = (provider_name or getattr(settings, "QUOTATION_AI_PARSE_PROVIDER", "openai") or "").lower()
    if provider_name == "openai":
        return OpenAIResponsesParseProvider()
    if provider_name == "anthropic":
        return AnthropicParseProvider()
    raise AIProviderUnavailable(f"AI unavailable: unsupported provider '{provider_name or 'unknown'}'.")


def get_ai_parse_availability():
    provider = (getattr(settings, "QUOTATION_AI_PARSE_PROVIDER", "openai") or "openai").lower()
    text_model = getattr(settings, "QUOTATION_AI_PARSE_TEXT_MODEL", "")
    vision_model = getattr(settings, "QUOTATION_AI_PARSE_VISION_MODEL", "")
    global_enabled = bool(getattr(settings, "QUOTATION_AI_PARSE_GLOBAL_ENABLED", True))
    if not global_enabled:
        reason = "AI parsing is globally disabled by environment."
        return _availability(False, reason, provider, text_model, vision_model, global_enabled)
    if provider == "openai":
        if not os.environ.get("OPENAI_API_KEY"):
            return _availability(False, "AI unavailable: missing API key.", provider, text_model, vision_model, global_enabled)
    elif provider == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return _availability(False, "AI unavailable: missing API key.", provider, text_model, vision_model, global_enabled)
    else:
        return _availability(False, f"AI unavailable: unsupported provider '{provider}'.", provider, text_model, vision_model, global_enabled)
    if not text_model:
        return _availability(False, "AI unavailable: no text model configured.", provider, text_model, vision_model, global_enabled)
    return _availability(True, "", provider, text_model, vision_model, global_enabled)


def _availability(available, reason, provider, text_model, vision_model, global_enabled):
    return {
        "available": available,
        "reason": reason,
        "provider": provider,
        "text_model": text_model,
        "vision_model": vision_model,
        "global_enabled": global_enabled,
    }


def settings_ai_status(settings_obj=None):
    settings_obj = settings_obj or QuotationSettings.get_solo()
    availability = get_ai_parse_availability()
    if not availability["available"]:
        return {
            "status": AI_STATUS_UNAVAILABLE,
            "label": availability["reason"] or "AI unavailable.",
            "availability": availability,
        }
    if not settings_obj.ai_parsing_enabled:
        return {
            "status": AI_STATUS_DISABLED,
            "label": "AI disabled in settings.",
            "availability": availability,
        }
    return {
        "status": AI_STATUS_AVAILABLE,
        "label": "AI parsing is available.",
        "availability": availability,
    }


def maybe_attach_auto_ai_candidate(preview, actor=None, *, allow_vision=True):
    preview["result_source"] = preview.get("result_source") or AI_SOURCE_DETERMINISTIC
    settings_obj = QuotationSettings.get_solo()
    status_info = settings_ai_status(settings_obj)
    preview["ai_status"] = status_info["status"]
    preview["ai_status_label"] = status_info["label"]
    preview["ai_available"] = status_info["availability"]["available"]
    preview["ai_auto_cleanup_enabled"] = bool(settings_obj.ai_auto_cleanup_enabled)
    preview["ai_pdf_vision_enabled"] = bool(settings_obj.ai_pdf_vision_enabled)

    if (
        status_info["status"] != AI_STATUS_AVAILABLE
        or not settings_obj.ai_auto_cleanup_enabled
        or not is_parse_quality_poor(preview)
    ):
        return preview

    try:
        candidate = clean_preview_with_ai(preview, actor=actor, requested_mode="auto", allow_vision=allow_vision)
        preview["ai_candidate"] = candidate
        preview["ai_status"] = "ai_candidate_ready"
        preview["ai_status_label"] = _result_source_label(candidate["result_source"])
    except AIParseError as exc:
        preview["ai_status"] = AI_STATUS_FAILED
        preview["ai_status_label"] = "AI failed, using original parse."
        preview.setdefault("warnings", []).append(str(exc))
    return preview


def is_parse_quality_poor(preview):
    """Heuristic for extraction quality only; product matching fields are ignored."""
    lines = preview.get("lines") or []
    if not lines:
        return True
    statuses = [line.get("parse_status") or line.get("status") or "" for line in lines]
    confidences = [_safe_float(line.get("parse_confidence"), default=0.0) for line in lines]
    if confidences and sum(confidences) / len(confidences) < 0.62:
        return True
    weak_count = sum(1 for status in statuses if status in {"needs_review", "unparsed", ""})
    if len(lines) >= 2 and weak_count / len(lines) > 0.55:
        return True
    warnings = " ".join(str(warning).lower() for warning in preview.get("warnings") or [])
    if "no selectable text" in warnings or "no item lines" in warnings:
        return True
    return False


def clean_preview_with_ai(preview, actor=None, *, requested_mode="auto", allow_vision=True):
    settings_obj = QuotationSettings.get_solo()
    _assert_ai_allowed(settings_obj)
    mode = _select_mode(preview, requested_mode=requested_mode, allow_vision=allow_vision, settings_obj=settings_obj)
    context = _build_preview_text_context(preview)
    images = []
    page_count = _safe_int(preview.get("meta", {}).get("page_count"), default=0)
    if mode == AIParseCache.MODE_VISION:
        images, rendered_page_count = _render_pdf_images(preview.get("source_file_ref", ""))
        if not images:
            raise AIParseError("AI vision cleanup could not render the source PDF. Use text cleanup or review manually.")
        page_count = page_count or rendered_page_count

    return _run_ai_cleanup(
        preview=preview,
        actor=actor,
        mode=mode,
        context=context,
        images=images,
        page_count=page_count,
        output_style="inquiry",
    )


def clean_historical_import_with_ai(historical_import, actor=None, *, requested_mode="auto"):
    settings_obj = QuotationSettings.get_solo()
    _assert_ai_allowed(settings_obj)
    if historical_import.status in {HistoricalPriceImport.STATUS_COMMITTED, HistoricalPriceImport.STATUS_CANCELLED}:
        raise AIParseError("Committed or cancelled historical imports cannot be AI-cleaned.")
    preview = _historical_import_to_preview(historical_import)
    mode = _select_mode(preview, requested_mode=requested_mode, allow_vision=True, settings_obj=settings_obj)
    context = _build_historical_import_text_context(historical_import)
    images = []
    page_count = _safe_int(historical_import.parse_meta.get("page_count"), default=0)
    if mode == AIParseCache.MODE_VISION:
        images, rendered_page_count = _render_pdf_images(historical_import.source_file_ref)
        if not images:
            raise AIParseError("AI vision cleanup could not render the source PDF. Use text cleanup or review manually.")
        page_count = page_count or rendered_page_count
    return _run_ai_cleanup(
        preview=preview,
        actor=actor,
        mode=mode,
        context=context,
        images=images,
        page_count=page_count,
        output_style="historical",
    )


def apply_ai_rows_to_historical_import(historical_import, rows, actor=None, ai_meta=None):
    from django.db import transaction
    from .services import apply_product_matches_to_historical_import, audit_log
    from .models import HistoricalPriceImportLine, QuotationAuditLog

    if not isinstance(rows, list) or not rows:
        raise ValidationError("AI cleaned rows are required before applying.")

    with transaction.atomic():
        historical_import = HistoricalPriceImport.objects.select_for_update().get(pk=historical_import.pk)
        if historical_import.status in {HistoricalPriceImport.STATUS_COMMITTED, HistoricalPriceImport.STATUS_CANCELLED}:
            raise ValidationError("Committed or cancelled historical imports cannot be replaced with AI cleaned rows.")
        historical_import.lines.all().delete()
        for index, row in enumerate(rows[:MAX_ROWS]):
            item_name = _clean_text(row.get("item_name") or row.get("raw_name"))
            if not item_name:
                continue
            HistoricalPriceImportLine.objects.create(
                historical_import=historical_import,
                raw_line=_clean_text(row.get("raw_line") or row.get("raw_source_text") or item_name),
                item_name=item_name[:255],
                quantity=_decimal_or_none(row.get("quantity")),
                unit=_clean_text(row.get("unit"))[:50],
                unit_price=_money_or_none(row.get("unit_price")),
                line_total=_money_or_none(row.get("line_total")),
                source_page=_safe_int(row.get("page_number") or row.get("source_page"), default=None),
                parse_confidence=_safe_float(row.get("parse_confidence") or row.get("confidence"), default=0.0),
                status=HistoricalPriceImportLine.STATUS_NEEDS_REVIEW,
                notes=_clean_text(row.get("notes") or row.get("pack_info") or row.get("reason")),
                sort_order=index,
            )
        historical_import.parse_method = _append_parse_method(historical_import.parse_method, "ai_cleaned")
        historical_import.parse_meta = {
            **(historical_import.parse_meta or {}),
            "ai_last_applied_at": timezone.now().isoformat(),
            "ai_last_result": ai_meta or {},
            "ai_review_required": True,
        }
        historical_import.save(update_fields=["parse_method", "parse_meta", "updated_at"])
        audit_log(
            actor,
            QuotationAuditLog.ACTION_UPDATED,
            historical_import,
            message="Applied AI cleaned rows to historical import for staff review.",
            changes={"line_count": historical_import.lines.count(), "ai_meta": ai_meta or {}},
            company=historical_import.company,
        )
    return apply_product_matches_to_historical_import(historical_import, actor)


def _assert_ai_allowed(settings_obj):
    status_info = settings_ai_status(settings_obj)
    if status_info["status"] != AI_STATUS_AVAILABLE:
        raise AIProviderUnavailable(status_info["label"])


def _select_mode(preview, *, requested_mode, allow_vision, settings_obj):
    source_type = (preview.get("source_type") or "").lower()
    if requested_mode == AIParseCache.MODE_TEXT:
        return AIParseCache.MODE_TEXT
    wants_vision = requested_mode == AIParseCache.MODE_VISION or (
        requested_mode == "auto" and source_type == Inquiry.SOURCE_TYPE_PDF
    )
    if wants_vision:
        if not allow_vision or not settings_obj.ai_pdf_vision_enabled:
            if requested_mode == AIParseCache.MODE_VISION:
                raise AIProviderUnavailable("AI vision cleanup is disabled in Quotation Settings.")
            return AIParseCache.MODE_TEXT
        if not getattr(settings, "QUOTATION_AI_PARSE_VISION_MODEL", ""):
            if requested_mode == AIParseCache.MODE_VISION:
                raise AIProviderUnavailable("AI vision cleanup is unavailable because no vision model is configured.")
            return AIParseCache.MODE_TEXT
        return AIParseCache.MODE_VISION
    return AIParseCache.MODE_TEXT


def _run_ai_cleanup(*, preview, actor, mode, context, images, page_count, output_style):
    availability = get_ai_parse_availability()
    provider_name = availability["provider"]
    model = availability["vision_model"] if mode == AIParseCache.MODE_VISION else availability["text_model"]
    if not model:
        raise AIProviderUnavailable("AI unavailable: no model configured for this cleanup mode.")
    context = _limit_text(context)
    image_hashes = [hashlib.sha256(image.encode("utf-8")).hexdigest() for image in images]
    context_hash = hashlib.sha256((context + "".join(image_hashes)).encode("utf-8")).hexdigest()
    source_sha256 = preview.get("source_sha256") or ""
    cache_key = hashlib.sha256(
        f"{source_sha256}:{provider_name}:{model}:{mode}:{context_hash}".encode("utf-8")
    ).hexdigest()
    cached = AIParseCache.objects.filter(cache_key=cache_key).first()
    if cached:
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
        return {**cached.result, "cache_hit": True}

    provider = get_ai_parse_provider(provider_name)
    try:
        raw_result, usage = provider.clean_rows(
            mode=mode,
            model=model,
            instructions=_ai_instructions(output_style=output_style, mode=mode),
            text_context=context,
            image_data_urls=images,
        )
        result = _normalize_ai_result(
            raw_result,
            preview=preview,
            mode=mode,
            provider=provider_name,
            model=model,
            output_style=output_style,
            usage=usage,
        )
        AIParseCache.objects.update_or_create(
            cache_key=cache_key,
            defaults={
                "source_sha256": source_sha256,
                "context_hash": context_hash,
                "mode": mode,
                "provider": provider_name,
                "model": model,
                "result": result,
            },
        )
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
        return result
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


def _normalize_ai_result(raw_result, *, preview, mode, provider, model, output_style, usage=None):
    if not isinstance(raw_result, dict):
        raise AIParseError("AI provider returned an object with an unsupported shape.")
    raw_rows = raw_result.get("rows")
    if not isinstance(raw_rows, list):
        raise AIParseError("AI provider response did not include a rows array.")

    rows = []
    ignored_count = 0
    warnings = [_clean_text(warning) for warning in raw_result.get("warnings", []) if _clean_text(warning)]
    for index, row in enumerate(raw_rows[:MAX_ROWS]):
        if not isinstance(row, dict):
            warnings.append(f"Skipped AI row {index + 1}: invalid row shape.")
            continue
        parse_status = (row.get("parse_status") or "needs_review").strip().lower()
        if parse_status not in VALID_PARSE_STATUSES:
            parse_status = "needs_review"
        item_name = _clean_text(row.get("item_name"))
        original_item_hint = _clean_text(
            row.get("original_item_name")
            or row.get("source_item_name")
            or row.get("raw_name")
            or row.get("raw_source_text")
            or row.get("raw_line")
            or item_name
        )
        item_name = preserve_specific_item_details(item_name, original_item_hint)
        raw_line = _clean_text(row.get("raw_source_text") or row.get("raw_line") or item_name)
        if parse_status == "ignored":
            ignored_count += 1
            continue
        if not item_name:
            ignored_count += 1
            warnings.append("Skipped an AI row because it had no item name.")
            continue
        confidence = _normalize_confidence(row.get("confidence"))
        if confidence < 0.80 and parse_status == "parsed":
            parse_status = "needs_review"

        common = {
            "quantity": _clean_quantity(row.get("quantity")),
            "unit": _clean_text(row.get("unit"))[:50],
            "unit_price": _clean_money(row.get("unit_price")),
            "vat_rate": _clean_money(row.get("vat_rate")),
            "vat_amount": _clean_money(row.get("vat_amount")),
            "line_total": _clean_money(row.get("line_total")),
            "notes": _join_notes(row.get("pack_info"), row.get("notes"), row.get("reason")),
            "raw_line": raw_line,
            "page_number": _clean_text(row.get("page_number")),
            "parse_confidence": confidence,
            "parse_status": parse_status,
            "result_source": AI_SOURCE_VISION if mode == AIParseCache.MODE_VISION else AI_SOURCE_TEXT,
        }
        if output_style == "historical":
            rows.append(
                {
                    **common,
                    "item_name": standardize_item_display_name(item_name)[:255],
                    "status": "needs_review",
                    "source_page": _safe_int(row.get("page_number"), default=None),
                }
            )
        else:
            rows.append(
                {
                    **common,
                    "raw_name": standardize_item_display_name(item_name)[:255],
                    "matched_product": "",
                    "match_reason": "",
                    "match_status": "unresolved",
                }
            )

    if not rows:
        raise AIParseError("AI cleanup did not return any item rows for review.")

    result_source = AI_SOURCE_VISION if mode == AIParseCache.MODE_VISION else AI_SOURCE_TEXT
    result = {
        "source_type": preview.get("source_type", ""),
        "source_filename": preview.get("source_filename", ""),
        "source_mime_type": preview.get("source_mime_type", ""),
        "source_sha256": preview.get("source_sha256", ""),
        "source_file_ref": preview.get("source_file_ref", ""),
        "source_file_size": preview.get("source_file_size"),
        "parse_method": _append_parse_method(preview.get("parse_method", ""), result_source),
        "original_text": preview.get("original_text", ""),
        "lines": rows,
        "warnings": warnings,
        "summary": summarize_lines(rows, skipped_count=ignored_count),
        "meta": {
            **(preview.get("meta") or {}),
            "ai_provider": provider,
            "ai_model": model,
            "ai_mode": mode,
            "ai_document_notes": _clean_text(raw_result.get("document_notes")),
            "ai_ignored_count": ignored_count,
            "ai_usage": usage or {},
        },
        "result_source": result_source,
        "ai_status": f"{result_source}_used",
        "ai_status_label": _result_source_label(result_source),
        "provider": provider,
        "model": model,
        "cache_hit": False,
    }
    return result


def _build_preview_text_context(preview):
    lines = preview.get("lines") or []
    context_lines = [
        "Clean these extracted pharmacy inquiry/quotation rows into strict structured rows for staff review.",
        f"Source type: {preview.get('source_type') or '-'}",
        f"Filename: {preview.get('source_filename') or '-'}",
        f"Parse method: {preview.get('parse_method') or '-'}",
        "",
    ]
    relevance_context = preview.get("relevance_context")
    if relevance_context:
        context_lines.extend(
            [
                "Workflow relevance context (authoritative; do not broaden beyond it):",
                json.dumps(relevance_context, ensure_ascii=False, default=str),
                "",
            ]
        )
    original_text = preview.get("original_text") or ""
    if original_text:
        context_lines.extend(["Original pasted/extracted text:", original_text, ""])
    if lines:
        context_lines.append("Deterministic parser rows:")
        for index, line in enumerate(lines[:MAX_ROWS], start=1):
            context_lines.append(
                json.dumps(
                    {
                        "row": index,
                        "item_name": line.get("raw_name") or line.get("item_name"),
                        "quantity": line.get("quantity"),
                        "unit": line.get("unit"),
                        "unit_price": line.get("unit_price"),
                        "vat_rate": line.get("vat_rate"),
                        "vat_amount": line.get("vat_amount"),
                        "line_total": line.get("line_total"),
                        "raw_source_text": line.get("raw_line") or line.get("raw_source_line"),
                        "parse_status": line.get("parse_status"),
                        "confidence": line.get("parse_confidence"),
                    },
                    ensure_ascii=False,
                )
            )
    warnings = preview.get("warnings") or []
    if warnings:
        context_lines.extend(["", "Parser warnings:", "\n".join(f"- {warning}" for warning in warnings)])
    return "\n".join(context_lines)


def _build_historical_import_text_context(historical_import):
    context_lines = [
        "Clean these staged historical finalized quotation rows into strict structured price rows for staff review.",
        f"Filename: {historical_import.source_filename or '-'}",
        f"Document number: {historical_import.document_number or '-'}",
        f"Document date: {historical_import.document_date or '-'}",
        f"Suggested company: {historical_import.suggested_company_name or '-'}",
        f"Parse method: {historical_import.parse_method or '-'}",
        "",
        "Current staged rows:",
    ]
    for index, line in enumerate(historical_import.lines.order_by("sort_order", "id")[:MAX_ROWS], start=1):
        context_lines.append(
            json.dumps(
                {
                    "row": index,
                    "item_name": line.item_name,
                    "quantity": str(line.quantity or ""),
                    "unit": line.unit,
                    "unit_price": str(line.unit_price or ""),
                    "line_total": str(line.line_total or ""),
                    "raw_source_text": line.raw_line,
                    "source_page": line.source_page,
                    "parse_confidence": line.parse_confidence,
                    "status": line.status,
                },
                ensure_ascii=False,
            )
        )
    warnings = historical_import.parse_meta.get("warnings") or []
    if warnings:
        context_lines.extend(["", "Parser warnings:", "\n".join(f"- {warning}" for warning in warnings)])
    return "\n".join(context_lines)


def _historical_import_to_preview(historical_import):
    return {
        "source_type": historical_import.source_type,
        "source_filename": historical_import.source_filename,
        "source_mime_type": historical_import.source_mime_type,
        "source_sha256": historical_import.source_sha256,
        "source_file_ref": historical_import.source_file_ref,
        "source_file_size": historical_import.source_file_size,
        "parse_method": historical_import.parse_method,
        "original_text": "",
        "warnings": historical_import.parse_meta.get("warnings") or [],
        "meta": historical_import.parse_meta or {},
        "lines": [
            {
                "item_name": line.item_name,
                "quantity": str(line.quantity or ""),
                "unit": line.unit,
                "unit_price": str(line.unit_price or ""),
                "line_total": str(line.line_total or ""),
                "raw_line": line.raw_line,
                "parse_status": "parsed" if line.parse_confidence >= 0.8 else "needs_review",
                "parse_confidence": line.parse_confidence,
            }
            for line in historical_import.lines.order_by("sort_order", "id")[:MAX_ROWS]
        ],
    }


def _render_pdf_images(source_file_ref):
    if fitz is None:
        raise AIProviderUnavailable("AI vision cleanup is unavailable because PDF rendering is not installed.")
    data = read_private_ref(source_file_ref)
    if not data:
        raise AIParseError("Source PDF is not available in private storage.")
    max_pages = int(getattr(settings, "QUOTATION_AI_PARSE_MAX_PDF_PAGES", 10))
    max_rendered_pages = int(getattr(settings, "QUOTATION_AI_PARSE_MAX_RENDERED_PAGES", 3))
    max_dimension = int(getattr(settings, "QUOTATION_AI_PARSE_IMAGE_MAX_DIMENSION", 1400))
    configured_scale = float(getattr(settings, "QUOTATION_AI_PARSE_IMAGE_SCALE", 1.4))
    images = []
    with fitz.open(stream=data, filetype="pdf") as document:
        if len(document) > max_pages:
            raise AIParseError(f"PDF has {len(document)} pages. AI cleanup is capped at {max_pages} pages.")
        for page_index in range(min(len(document), max_rendered_pages)):
            page = document[page_index]
            page_max_points = max(float(page.rect.width), float(page.rect.height)) or 1
            scale = min(configured_scale, max_dimension / page_max_points)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            png_bytes = pixmap.tobytes("png")
            images.append(f"data:image/png;base64,{base64.b64encode(png_bytes).decode('ascii')}")
    return images, min(len(images), max_rendered_pages)


def _ai_instructions(*, output_style, mode):
    return (
        "You clean messy pharmacy inquiry, LPO, and finalized quotation extraction into JSON rows for human review. "
        "Do not match products, do not create items, do not invent prices or quantities, and do not commit anything. "
        "Only extract what is visible or clearly present. Preserve product-identifying sizes, dimensions, strengths, variants, and pack counts in item_name, for example Adhesive Tape 1/2\" x 10 yds, Gauze Bandage - 2\", Gauze Pads - 3\" x 3\", or Ammonia Inhalant - pack of 5. Put order quantities, units, unit prices, and totals in their own fields. "
        "Preserve VAT percentage/rate in vat_rate and VAT money amount in vat_amount when visible. Do not convert a visible VAT rate such as 5% into a VAT amount. "
        "For structured Excel rows, keep every real item row unless it is clearly a header, footer, subtotal, metadata, or duplicate noise row. "
        "Skip obvious document metadata such as dates, seller/buyer addresses, tender numbers, quotation headings, table headers, totals, footers, contact/signature text, and email addresses by setting parse_status='ignored'. "
        "If quantity is unclear, leave quantity blank and set parse_status='needs_review'. "
        "If price is clear, extract unit_price. Preserve item-like uncertain rows as needs_review. "
        "Use confidence 0-100 for extraction quality only. Missing product matches are irrelevant and must not reduce confidence. "
        f"Return rows suitable for {'historical finalized quotation price review' if output_style == 'historical' else 'new inquiry review'}. "
        f"Mode: {mode}."
    )


def _extract_openai_output_text(response):
    if isinstance(response, dict) and response.get("output_text"):
        return response["output_text"]
    for output_item in response.get("output", []) if isinstance(response, dict) else []:
        for content in output_item.get("content", []) if isinstance(output_item, dict) else []:
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                return content["text"]
    return ""


def _log_ai_parse(*, actor, provider, model, mode, preview, context_hash, cache_hit, text_length, page_count, image_count, usage=None, success=False, error=""):
    AIParseLog.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        provider=provider,
        model=model,
        mode=mode,
        source_type=preview.get("source_type", ""),
        source_sha256=preview.get("source_sha256", ""),
        context_hash=context_hash,
        cache_hit=cache_hit,
        text_length=text_length,
        page_count=page_count or 0,
        image_count=image_count,
        usage=usage or {},
        success=success,
        error=error[:1000],
    )


def _limit_text(value):
    value = value or ""
    max_chars = int(getattr(settings, "QUOTATION_AI_PARSE_MAX_TEXT_CHARS", 20000))
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "\n[Truncated by AI parsing safety limit.]"


def _result_source_label(result_source):
    if result_source == AI_SOURCE_VISION:
        return "AI vision cleanup used."
    if result_source == AI_SOURCE_TEXT:
        return "AI text cleanup used."
    return "Deterministic parse."


def _append_parse_method(current, suffix):
    current = (current or "manual_review").strip()
    return current if suffix in current else f"{current}+{suffix}"


def _clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _join_notes(*values):
    notes = []
    for value in values:
        text = _clean_text(value)
        if text and text not in notes:
            notes.append(text)
    return " | ".join(notes)


def _normalize_confidence(value):
    confidence = _safe_float(value, default=0.0)
    if confidence > 1:
        confidence = confidence / 100
    return max(0.0, min(1.0, confidence))


def _clean_quantity(value):
    text = _clean_text(value)
    if not text:
        return None
    try:
        normalized = format(Decimal(text.replace(",", "")).normalize(), "f")
        return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized
    except (InvalidOperation, ValueError):
        return text


def _clean_money(value):
    text = _clean_text(value)
    if not text:
        return ""
    match = re.search(r"-?\d+(?:,\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?", text)
    return match.group(0).replace(",", "") if match else text


def _decimal_or_none(value):
    value = _clean_quantity(value)
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.001"))
    except (InvalidOperation, ValueError):
        return None


def _money_or_none(value):
    value = _clean_money(value)
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default=0):
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
