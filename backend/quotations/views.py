import json
import logging
import re
from pathlib import Path
from datetime import datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Prefetch, Q, Window
from django.db.models.functions import RowNumber
from django.http import HttpResponse, HttpResponseRedirect
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import status, viewsets
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from api.models import Product, ProductImage

from .ai_parsing import (
    AIParseError,
    apply_ai_rows_to_historical_import,
    clean_historical_import_with_ai,
    clean_preview_with_ai,
    maybe_attach_auto_ai_candidate,
    prefer_safe_ai_preview,
)
from .ai_learning import (
    append_batch_file_result,
    apply_historical_ai_suggestions,
    commit_ready_imports_for_batch,
    generate_batch_learning_suggestions,
    generate_historical_import_learning_suggestions,
    refresh_historical_import_batch_summary,
)
from .company_matching import find_similar_companies
from .contract_intelligence import (
    build_contract_intelligence_export,
    build_gmail_auth_url,
    can_manage_shared_gmail,
    clean_contract_run_items,
    discover_contract_sources,
    disconnect_gmail,
    exchange_gmail_code,
    gmail_frontend_redirect_url,
    gmail_fetch_attachment_content,
    gmail_connection_lineage_q,
    gmail_oauth_configured,
    parse_gmail_oauth_state,
    resolve_gmail_connection,
    refresh_contract_run_summary,
    analyze_contract_run,
)
from .historical_import_parsers import parse_historical_pdf_upload
from .import_parsers import parse_file_preview, parse_text_preview
from .mailbox_po_audit import (
    assert_mailbox_po_audit_repairable,
    mailbox_po_audit_repair_remaining,
    mailbox_vision_availability,
    mark_unavailable_mailbox_vision_for_manual_review,
    reclassify_mailbox_po_audit_messages,
    repair_mailbox_po_audit_pdf_vision,
    scan_mailbox_po_audit_page,
    start_mailbox_po_audit,
)
from .mailbox_po_reconciliation import (
    ALGORITHM_VERSION,
    MailboxPOMatchBusy,
    reconcile_mailbox_po_audit_page,
)
from .matching import (
    apply_match_to_preview_line,
    create_managed_product_alias,
    create_or_reuse_product,
    update_managed_product_alias,
)
from .models import (
    Company,
    CompanyContact,
    CompanyPriceHistory,
    ContractIntelligenceItem,
    ContractIntelligenceRun,
    ContractIntelligenceSource,
    GmailOAuthConnection,
    HistoricalImportAISuggestion,
    HistoricalImportBatch,
    HistoricalPriceImport,
    HistoricalPriceImportLine,
    Inquiry,
    InquiryLine,
    MailboxPOAuditRun,
    MailboxPOMatchRun,
    ProformaInvoice,
    ProformaInvoiceLine,
    Quotation,
    QuotationAuditLog,
    QuotationLine,
    QuotationLPO,
    QuotationOutcomePOImport,
    QuotationPOEvidence,
    QuotationSettings,
    UserQuotationProfile,
    ProductAlias,
    normalize_label,
)
from .excel import build_quotation_excel
from .pdf import build_proforma_invoice_pdf, build_standalone_proforma_invoice_pdf, build_quotation_pdf
from .permissions import IsQuotationStaff
from .price_reference import apply_price_reference_to_preview, parse_price_reference_source
from .po_evidence_comparison import (
    latest_relevant_po_import,
    safe_build_po_evidence_commercial_comparison,
    unavailable_po_evidence_commercial_comparison,
)
from .quote_po_intelligence import find_quote_po_evidence, parse_quote_po_evidence, scan_quote_po_evidence_batch
from .serializers import (
    CompanyContactSerializer,
    CompanyListSerializer,
    CompanyPriceHistorySerializer,
    CompanySerializer,
    ContractIntelligenceItemSerializer,
    ContractIntelligenceRunSerializer,
    ContractIntelligenceSourceSerializer,
    GmailOAuthConnectionSerializer,
    HistoricalPriceImportLineSerializer,
    HistoricalPriceImportSerializer,
    HistoricalImportAISuggestionSerializer,
    HistoricalImportBatchSerializer,
    ImportedInquiryCreateSerializer,
    InquiryLineSerializer,
    InquirySerializer,
    MailboxPOAuditRunSerializer,
    MailboxPOMatchRunSerializer,
    QuotationAuditLogSerializer,
    QuotationLineSerializer,
    QuotationLPOSerializer,
    QuotationPOEvidenceSerializer,
    QuotationListSerializer,
    QuotationOutcomePOImportSerializer,
    ProformaInvoiceLineSerializer,
    ProformaInvoiceSerializer,
    QuotationSettingsSerializer,
    QuotationSerializer,
    UserQuotationProfileSerializer,
    ProductAliasSerializer,
    QuoteItemListSerializer,
    QuoteItemSerializer,
    format_unit_price_value,
    serializer_error_from_django_validation,
)
from .services import (
    audit_log,
    apply_product_matches_to_historical_import,
    build_quotation_delete_snapshot,
    build_guarded_po_outcome_suggestions,
    bulk_create_quote_items_for_historical_import,
    bulk_create_products_from_quotation_lines,
    bulk_update_quotation_lines,
    bulk_update_historical_import_rows,
    commit_historical_price_import,
    create_historical_price_import,
    create_imported_inquiry,
    create_product_from_quotation_line,
    create_quotation_from_inquiry,
    ensure_outcome_reviewable,
    ensure_quotation_editable,
    find_historical_import_duplicates,
    finalize_quotation,
    learn_confirmed_inquiry_line_alias,
    learn_confirmed_quotation_line_alias,
    outcome_summary_for_quotation,
    _quotation_lines_for_update,
    _quotations_for_update,
    quotation_line_source_wording,
    remember_historical_import_line_alias,
    remember_inquiry_line_alias,
    remember_quotation_line_alias,
    recalculate_quotation_totals,
    revise_quotation,
    transition_quotation_status,
    update_quotation_outcome,
)
from .private_storage import read_private_ref

try:
    import fitz
except Exception:  # pragma: no cover
    fitz = None


logger = logging.getLogger(__name__)


def _safe_download_name_part(value):
    cleaned = re.sub(r"[^A-Za-z0-9-]+", "_", str(value or "").upper()).strip("_-")
    return cleaned[:80] or ""


def _quotation_download_filename(quotation, extension):
    company_part = _safe_download_name_part(getattr(quotation.company, "name", ""))
    quote_part = _safe_download_name_part(quotation.quotation_number) or "QUOTATION"
    basename = f"{company_part}-{quote_part}" if company_part else quote_part
    return f"{basename}.{extension}"


def _proforma_download_filename(quotation):
    company_part = _safe_download_name_part(getattr(quotation.company, "name", ""))
    quote_part = _safe_download_name_part(quotation.quotation_number) or "QUOTATION"
    basename = f"{company_part}-PROFORMA-{quote_part}" if company_part else f"PROFORMA-{quote_part}"
    return f"{basename}.pdf"


def _standalone_proforma_download_filename(proforma):
    company_part = _safe_download_name_part(getattr(proforma.company, "name", ""))
    proforma_part = _safe_download_name_part(proforma.proforma_number) or "PROFORMA"
    basename = f"{company_part}-{proforma_part}" if company_part else proforma_part
    return f"{basename}.pdf"


def _preview_text_blob(preview):
    chunks = [
        str(preview.get("original_text") or ""),
        str(preview.get("source_filename") or ""),
        json.dumps(preview.get("meta") or {}, default=str),
    ]
    for row in preview.get("lines") or []:
        chunks.extend(
            str(row.get(key) or "")
            for key in ["raw_line", "raw_name", "requested_item_name", "description", "item_name"]
        )
    return "\n".join(part for part in chunks if part)


def _parse_lpo_business_date(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    parsed = parse_date(raw)
    if parsed:
        return parsed
    for pattern in (
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d.%m.%Y",
        "%d%m%Y",
        "%Y%m%d",
        "%d-%b-%Y",
        "%d %b %Y",
        "%d.%b.%Y",
        "%d/%b/%Y",
        "%d-%B-%Y",
        "%d %B %Y",
        "%d.%B.%Y",
        "%d/%B/%Y",
    ):
        try:
            return datetime.strptime(raw, pattern).date()
        except ValueError:
            continue
    return None


def _clean_lpo_number_candidate(value):
    candidate = str(value or "").strip().strip(" .:-#")
    candidate = re.sub(r"\s+", "", candidate)
    candidate = candidate.upper()
    candidate = re.sub(r"\.(?:PDF|XLSX?|XLSB)$", "", candidate)
    if not candidate or candidate in {"BOX", "P.O.BOX", "POBOX", "PBOX"}:
        return ""
    if not re.search(r"\d", candidate):
        return ""
    if len(candidate) < 3 or len(candidate) > 120:
        return ""
    return candidate


def _extract_lpo_details(preview):
    text = _preview_text_blob(preview)
    meta = dict(preview.get("meta") or {})
    lpo_number = ""
    lpo_date = None

    for key in ["lpo_number", "po_number", "purchase_order_number", "document_number"]:
        if meta.get(key):
            lpo_number = _clean_lpo_number_candidate(meta.get(key))
            if lpo_number:
                break

    if not lpo_number:
        number_patterns = [
            r"\b(?:LPO|PO|P\.O\.|PURCHASE\s+ORDER)\s*(?:NO\.?|NUMBER|#)\s*[:\-]?\s*(?:\r?\n\s*)?([A-Z0-9][A-Z0-9\/\-.]{2,})",
            r"\bPURCHASE\s+ORDER\s*#\s*[:\-]?\s*(?:\r?\n\s*)?([A-Z0-9][A-Z0-9\/\-.]{2,})",
            r"\b(LPO[-\/.]?[A-Z0-9][A-Z0-9\/\-.]{2,})\b",
            r"\b(?:PO[_-])?(PO\d{3}_\d{5,})(?!\d)",
            r"\b(?:LPO|MPO|PO|P\.O\.|PURCHASE\s+ORDER)\s*[-#:]?\s*(\d[A-Z0-9\/_.-]{2,})",
        ]
        for pattern in number_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                lpo_number = _clean_lpo_number_candidate(match.group(1))
                if lpo_number:
                    break

    for key in ["lpo_date", "po_date", "purchase_order_date", "document_date", "date"]:
        lpo_date = _parse_lpo_business_date(meta.get(key))
        if lpo_date:
            break

    if not lpo_date:
        date_token = r"(\d{4}-\d{2}-\d{2}|\d{1,2}[\/\-.]\d{1,2}[\/\-.]\d{2,4}|\d{1,2}[\/\-. ](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[\/\-. ]\d{2,4})"
        labelled_date_match = re.search(
            rf"\b(?:LPO|PO|P\.O\.|PURCHASE\s+ORDER)\s*DATE\s*[:\-]?\s*(?:\r?\n\s*)?{date_token}",
            text,
            re.IGNORECASE,
        )
        date_match = labelled_date_match or re.search(rf"\b{date_token}\b", text, re.IGNORECASE)
        if date_match:
            lpo_date = _parse_lpo_business_date(date_match.group(1))

    if not lpo_date:
        filename = str(preview.get("source_filename") or "")
        compact_match = re.search(r"(?<!\d)(\d{8})(?!\d)", filename)
        if compact_match:
            lpo_date = _parse_lpo_business_date(compact_match.group(1))

    return {
        "lpo_number": lpo_number[:120],
        "lpo_date": lpo_date,
        "parsed_meta": {
            **meta,
            "detected_lpo_number": lpo_number,
            "detected_lpo_date": lpo_date.isoformat() if lpo_date else "",
        },
    }


def _preview_decimal(value, default=None):
    if value in (None, ""):
        return default
    cleaned = re.sub(r"[^0-9.\-]", "", str(value))
    if cleaned in {"", "-", ".", "-."}:
        return default
    try:
        return Decimal(cleaned)
    except Exception:
        return default


def _preview_line_text(row):
    for key in ("requested_item_name", "raw_name", "item_name", "description", "raw_line"):
        value = str(row.get(key) or "").strip()
        if value:
            return value[:255]
    return ""


def _proforma_line_from_preview(row, index):
    item_name = _preview_line_text(row)
    if not item_name:
        return None
    quantity = _preview_decimal(row.get("quantity") or row.get("qty"), Decimal("1.000"))
    if quantity is None or quantity <= 0:
        quantity = Decimal("1.000")
    unit_price = _preview_decimal(
        row.get("unit_price")
        or row.get("price")
        or row.get("rate")
        or row.get("amount")
        or row.get("total")
    )
    total = _preview_decimal(row.get("line_total") or row.get("total") or row.get("amount"))
    if unit_price is None and total is not None and quantity:
        unit_price = (total / quantity).quantize(Decimal("0.001"))
    vat_rate = _preview_decimal(row.get("vat_rate") or row.get("vat") or row.get("tax"), Decimal("0.00"))
    if vat_rate is None:
        vat_rate = Decimal("0.00")
    if vat_rate > 100:
        vat_rate = Decimal("0.00")
    return {
        "item_name": item_name,
        "description": str(row.get("description") or "").strip(),
        "quantity": quantity,
        "unit": str(row.get("unit") or row.get("uom") or "").strip()[:50],
        "unit_price": unit_price,
        "vat_rate": vat_rate,
        "sort_order": index,
    }


def _recalculate_proforma_totals(proforma):
    subtotal = Decimal("0.00")
    vat_total = Decimal("0.00")
    total = Decimal("0.00")
    for line in ProformaInvoiceLine.objects.filter(proforma=proforma):
        subtotal += Decimal(line.line_subtotal or 0)
        vat_total += Decimal(line.vat_amount or 0)
        total += Decimal(line.line_total or 0)
    proforma.subtotal = subtotal.quantize(Decimal("0.01"))
    proforma.vat_total = vat_total.quantize(Decimal("0.01"))
    proforma.total = total.quantize(Decimal("0.01"))
    proforma.save(update_fields=["subtotal", "vat_total", "total", "updated_at"])
    return proforma


class QuotationBaseViewSet:
    permission_classes = [IsQuotationStaff]

    def handle_workflow_error(self, exc):
        return Response(serializer_error_from_django_validation(exc), status=status.HTTP_400_BAD_REQUEST)

    def handle_safe_workflow_exception(self, exc, fallback_message="Quotation workflow action failed."):
        if isinstance(exc, DjangoValidationError):
            return self.handle_workflow_error(exc)
        if isinstance(exc, IntegrityError):
            logger.exception("%s Database conflict while running quotation workflow action.", fallback_message)
            return Response({"detail": "A duplicate or conflicting database value blocked this action. Link the existing Product or edit the Product name."}, status=status.HTTP_400_BAD_REQUEST)
        logger.exception("%s Unexpected quotation workflow error.", fallback_message)
        detail = (
            f"{fallback_message} Please refresh and retry. "
            "If this is an older historical-import batch, re-run AI Analyze or create a fresh batch."
        )
        return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)


class QuotationSettingsView(APIView):
    permission_classes = [IsQuotationStaff]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_object(self):
        return QuotationSettings.get_solo()

    def get(self, request):
        serializer = QuotationSettingsSerializer(self.get_object(), context={"request": request})
        return Response(serializer.data)

    def patch(self, request):
        settings_obj = self.get_object()
        serializer = QuotationSettingsSerializer(settings_obj, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        settings_obj = serializer.save(updated_by=request.user)
        audit_log(
            request.user,
            QuotationAuditLog.ACTION_UPDATED,
            settings_obj,
            message="Updated quotation settings.",
        )
        return Response(QuotationSettingsSerializer(settings_obj, context={"request": request}).data)


class UserQuotationProfileView(APIView):
    permission_classes = [IsQuotationStaff]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_object(self, request):
        profile, _ = UserQuotationProfile.objects.get_or_create(user=request.user)
        return profile

    def get(self, request):
        serializer = UserQuotationProfileSerializer(self.get_object(request), context={"request": request})
        return Response(serializer.data)

    def patch(self, request):
        profile = self.get_object(request)
        serializer = UserQuotationProfileSerializer(profile, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        profile = serializer.save(user=request.user)
        audit_log(
            request.user,
            QuotationAuditLog.ACTION_UPDATED,
            profile,
            message="Updated user quotation signature.",
        )
        return Response(UserQuotationProfileSerializer(profile, context={"request": request}).data)


class GmailConnectionView(APIView):
    permission_classes = [IsQuotationStaff]

    def get(self, request):
        connection = resolve_gmail_connection(request.user, connected_only=False)
        return Response(
            {
                "configured": gmail_oauth_configured(),
                "scope": "https://www.googleapis.com/auth/gmail.readonly",
                "connection": GmailOAuthConnectionSerializer(connection).data if connection else None,
                "can_manage": can_manage_shared_gmail(request.user, connection),
                "railway_env_vars": [
                    "GOOGLE_OAUTH_CLIENT_ID",
                    "GOOGLE_OAUTH_CLIENT_SECRET",
                    "GOOGLE_OAUTH_REDIRECT_URI",
                ],
            }
        )

    def post(self, request):
        connection = resolve_gmail_connection(request.user, connected_only=False, shared_only=True)
        if not can_manage_shared_gmail(request.user, connection):
            return Response(
                {"detail": "Only the shared Gmail credential owner or a superuser can replace the mailbox."},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            return Response({"auth_url": build_gmail_auth_url(request.user, request)})
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request):
        connection = resolve_gmail_connection(request.user, connected_only=False)
        if not connection:
            return Response({"detail": "Gmail is not connected."}, status=status.HTTP_400_BAD_REQUEST)
        if not can_manage_shared_gmail(request.user, connection):
            return Response(
                {"detail": "Only the shared Gmail credential owner or a superuser can disconnect the mailbox."},
                status=status.HTTP_403_FORBIDDEN,
            )
        disconnect_gmail(connection)
        return Response({"detail": "Gmail disconnected."})


class GmailOAuthCallbackView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        error = request.query_params.get("error")
        if error:
            return HttpResponseRedirect(gmail_frontend_redirect_url(f"error:{error}"))
        user_id = parse_gmail_oauth_state(request.query_params.get("state"))
        if not user_id:
            return HttpResponseRedirect(gmail_frontend_redirect_url("invalid-state"))
        code = request.query_params.get("code")
        if not code:
            return HttpResponseRedirect(gmail_frontend_redirect_url("missing-code"))
        User = get_user_model()
        try:
            user = User.objects.get(pk=user_id, is_staff=True)
            exchange_gmail_code(user, code, request)
        except Exception as exc:
            logger.exception("Gmail OAuth callback failed.")
            return HttpResponseRedirect(gmail_frontend_redirect_url(f"error:{str(exc)[:80]}"))
        return HttpResponseRedirect(gmail_frontend_redirect_url("connected"))


class ContractIntelligenceRunViewSet(QuotationBaseViewSet, viewsets.ModelViewSet):
    serializer_class = ContractIntelligenceRunSerializer
    queryset = ContractIntelligenceRun.objects.select_related("company", "created_by")
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        queryset = super().get_queryset()
        if getattr(self, "action", None) != "list":
            return queryset
        company_id = self.request.query_params.get("company")
        status_param = self.request.query_params.get("status")
        search = (self.request.query_params.get("search") or "").strip()
        if company_id:
            queryset = queryset.filter(company_id=company_id)
        if status_param:
            queryset = queryset.filter(status=status_param)
        if search:
            queryset = queryset.filter(
                Q(target_company_name__icontains=search)
                | Q(company__name__icontains=search)
                | Q(gmail_query__icontains=search)
            )
        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def _refresh_summary_safely(self, run):
        try:
            refresh_contract_run_summary(run)
            run.save(update_fields=["summary", "updated_at"])
        except Exception as exc:
            logger.exception("Contract intelligence summary refresh failed for run %s.", run.pk)
            existing_warnings = run.warnings if isinstance(run.warnings, list) else []
            warning = "Summary refresh failed. Run data is still available."
            if str(exc):
                warning = f"{warning} {str(exc)[:160]}"
            if warning not in existing_warnings:
                run.warnings = [*existing_warnings[-9:], warning]
                run.save(update_fields=["warnings", "updated_at"])

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        self._refresh_summary_safely(instance)
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def sources(self, request, pk=None):
        run = self.get_object()
        queryset = run.sources.annotate(item_count=Count("items")).order_by("-sent_at", "-created_at")
        return Response(ContractIntelligenceSourceSerializer(queryset, many=True).data)

    @action(detail=True, methods=["get"])
    def items(self, request, pk=None):
        run = self.get_object()
        queryset = run.items.select_related("source", "product").order_by("normalized_item_name", "-requested_date", "-id")
        status_param = request.query_params.get("status")
        include_rejected = str(request.query_params.get("include_rejected", "")).lower() in {"1", "true", "yes", "on"}
        dedupe = str(request.query_params.get("dedupe", "")).lower() in {"1", "true", "yes", "on"}
        if status_param:
            queryset = queryset.filter(status=status_param)
        elif not include_rejected:
            queryset = queryset.exclude(status=ContractIntelligenceItem.STATUS_REJECTED)
        if not dedupe:
            return Response(ContractIntelligenceItemSerializer(queryset[:1000], many=True).data)

        grouped = {}
        for item in queryset:
            key = item.normalized_item_name or normalize_label(item.suggested_item_name or item.original_item_name)
            if not key:
                key = f"item-{item.pk}"
            bucket = grouped.setdefault(
                key,
                {
                    "representative": item,
                    "mention_count": 0,
                    "source_ids": set(),
                },
            )
            bucket["mention_count"] += 1
            if item.source_id:
                bucket["source_ids"].add(item.source_id)

        representatives = [bucket["representative"] for bucket in grouped.values()]
        data = ContractIntelligenceItemSerializer(representatives[:1000], many=True).data
        for row in data:
            key = row.get("normalized_item_name") or normalize_label(row.get("suggested_item_name") or row.get("original_item_name") or "")
            bucket = grouped.get(key)
            if bucket:
                row["unique_key"] = key
                row["mention_count"] = bucket["mention_count"]
                row["source_count"] = len(bucket["source_ids"])
        return Response(data)

    @action(detail=True, methods=["post"])
    def discover(self, request, pk=None):
        run = self.get_object()
        try:
            result = discover_contract_sources(
                run,
                request.user,
                batch_size=request.data.get("batch_size"),
                reset_cursor=bool(request.data.get("reset_cursor")),
            )
        except RuntimeError as exc:
            run.status = ContractIntelligenceRun.STATUS_FAILED
            run.warnings = [str(exc)]
            run.save(update_fields=["status", "warnings", "updated_at"])
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            logger.exception("Contract intelligence Gmail discovery failed.")
            run.status = ContractIntelligenceRun.STATUS_FAILED
            run.warnings = [str(exc)]
            run.save(update_fields=["status", "warnings", "updated_at"])
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"run": ContractIntelligenceRunSerializer(run).data, "result": result})

    @action(detail=True, methods=["post"])
    def analyze(self, request, pk=None):
        run = self.get_object()
        use_ai = str(request.data.get("use_ai", "true")).lower() not in {"0", "false", "no", "off"}
        reanalyze = str(request.data.get("reanalyze", "false")).lower() in {"1", "true", "yes", "on"}
        try:
            if reanalyze:
                ContractIntelligenceItem.objects.filter(run=run).delete()
                ContractIntelligenceSource.objects.filter(run=run).update(status="candidate", error="")
                run.status = ContractIntelligenceRun.STATUS_READY
                run.ai_status = "queued"
                run.warnings = []
                run.completed_at = None
                self._refresh_summary_safely(run)
                run.save(update_fields=["status", "ai_status", "warnings", "completed_at", "updated_at"])
            result = analyze_contract_run(
                run,
                request.user,
                use_ai=use_ai,
                source_limit=request.data.get("source_limit"),
            )
        except RuntimeError as exc:
            run.status = ContractIntelligenceRun.STATUS_FAILED
            run.warnings = [str(exc)]
            run.save(update_fields=["status", "warnings", "updated_at"])
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            logger.exception("Contract intelligence analysis failed.")
            run.status = ContractIntelligenceRun.STATUS_FAILED
            run.warnings = [str(exc)]
            run.save(update_fields=["status", "warnings", "updated_at"])
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"run": ContractIntelligenceRunSerializer(run).data, "result": result})

    @staticmethod
    def _positive_int(value, default, minimum=1, maximum=100):
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return min(max(parsed, minimum), maximum)

    @action(detail=True, methods=["post"])
    def discover_all(self, request, pk=None):
        run = self.get_object()
        batch_size = self._positive_int(
            request.data.get("batch_size"),
            run.discovery_batch_size or 25,
            minimum=1,
            maximum=100,
        )
        max_batches = self._positive_int(request.data.get("max_batches"), 50, minimum=1, maximum=100)
        reset_cursor = bool(request.data.get("reset_cursor"))
        totals = {
            "batches": 0,
            "created": 0,
            "reused": 0,
            "failed": 0,
            "warnings": [],
            "discovery_exhausted": False,
            "result_size_estimate": None,
        }

        try:
            for batch_index in range(max_batches):
                result = discover_contract_sources(
                    run,
                    request.user,
                    batch_size=batch_size,
                    reset_cursor=reset_cursor and batch_index == 0,
                )
                totals["batches"] += 1
                totals["created"] += int(result.get("created") or 0)
                totals["reused"] += int(result.get("reused") or 0)
                totals["failed"] += int(result.get("failed") or 0)
                totals["warnings"].extend(result.get("warnings") or [])
                totals["discovery_exhausted"] = bool(result.get("discovery_exhausted"))
                totals["result_size_estimate"] = result.get("result_size_estimate")
                run.refresh_from_db()
                if result.get("discovery_exhausted") or run.discovery_exhausted:
                    break
                if not result.get("next_page_token"):
                    break
        except RuntimeError as exc:
            run.status = ContractIntelligenceRun.STATUS_FAILED
            run.warnings = [str(exc)]
            run.save(update_fields=["status", "warnings", "updated_at"])
            return Response({"detail": str(exc), "result": totals}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            logger.exception("Contract intelligence full Gmail discovery failed.")
            run.status = ContractIntelligenceRun.STATUS_FAILED
            run.warnings = [str(exc)]
            run.save(update_fields=["status", "warnings", "updated_at"])
            return Response({"detail": str(exc), "result": totals}, status=status.HTTP_400_BAD_REQUEST)

        refresh_contract_run_summary(run)
        run.save(update_fields=["summary", "updated_at"])
        return Response({"run": ContractIntelligenceRunSerializer(run).data, "result": totals})

    @action(detail=True, methods=["post"])
    def analyze_all(self, request, pk=None):
        run = self.get_object()
        use_ai = str(request.data.get("use_ai", "true")).lower() not in {"0", "false", "no", "off"}
        source_limit = self._positive_int(
            request.data.get("source_limit"),
            run.discovery_batch_size or 25,
            minimum=1,
            maximum=100,
        )
        max_batches = self._positive_int(request.data.get("max_batches"), 50, minimum=1, maximum=100)
        totals = {
            "batches": 0,
            "sources_analyzed": 0,
            "sources_processed": 0,
            "items_created": 0,
            "warnings": [],
            "pending_sources": None,
        }

        try:
            for _ in range(max_batches):
                result = analyze_contract_run(
                    run,
                    request.user,
                    use_ai=use_ai,
                    source_limit=source_limit,
                )
                sources_analyzed = int(result.get("sources_analyzed") or 0)
                sources_processed = int(result.get("sources_processed") or sources_analyzed)
                totals["batches"] += 1
                totals["sources_analyzed"] += sources_analyzed
                totals["sources_processed"] += sources_processed
                totals["items_created"] += int(result.get("items_created") or 0)
                totals["warnings"].extend(result.get("warnings") or [])
                totals["pending_sources"] = int(result.get("pending_sources") or 0)
                run.refresh_from_db()
                if totals["pending_sources"] <= 0 or sources_processed <= 0:
                    break
        except RuntimeError as exc:
            run.status = ContractIntelligenceRun.STATUS_FAILED
            run.warnings = [str(exc)]
            run.save(update_fields=["status", "warnings", "updated_at"])
            return Response({"detail": str(exc), "result": totals}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            logger.exception("Contract intelligence full analysis failed.")
            run.status = ContractIntelligenceRun.STATUS_FAILED
            run.warnings = [str(exc)]
            run.save(update_fields=["status", "warnings", "updated_at"])
            return Response({"detail": str(exc), "result": totals}, status=status.HTTP_400_BAD_REQUEST)

        refresh_contract_run_summary(run)
        run.save(update_fields=["summary", "updated_at"])
        return Response({"run": ContractIntelligenceRunSerializer(run).data, "result": totals})

    @action(detail=True, methods=["post"])
    def clean_items(self, request, pk=None):
        run = self.get_object()
        batch_size = self._positive_int(request.data.get("batch_size"), 500, minimum=1, maximum=2000)
        try:
            cursor = int(request.data.get("cursor") or 0)
        except (TypeError, ValueError):
            cursor = 0
        try:
            result = clean_contract_run_items(run, limit=batch_size, cursor=cursor, save_summary=False)
        except Exception as exc:
            logger.exception("Contract intelligence item cleanup failed.")
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        items = ContractIntelligenceItem.objects.filter(run=run)
        total_items = items.count()
        result_cursor = result.get("cursor")
        remaining = items.filter(id__gt=result_cursor).count() if result_cursor else 0
        done = not result_cursor or remaining == 0
        if done:
            refresh_contract_run_summary(run)
            run.save(update_fields=["summary", "updated_at"])
        run.refresh_from_db()
        result.update(
            {
                "processed": result.get("total", 0),
                "total_items": total_items,
                "remaining": remaining,
                "done": done,
                "batch_size": batch_size,
            }
        )
        return Response({"run": ContractIntelligenceRunSerializer(run).data, "result": result})

    @action(detail=True, methods=["get"])
    def export(self, request, pk=None):
        run = self.get_object()
        content = build_contract_intelligence_export(run)
        filename = _safe_download_name_part(run.target_company_name or "CONTRACT") or "CONTRACT"
        response = HttpResponse(
            content,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}_contract_intelligence.xlsx"'
        return response


class ContractIntelligenceSourceViewSet(QuotationBaseViewSet, viewsets.ReadOnlyModelViewSet):
    serializer_class = ContractIntelligenceSourceSerializer
    queryset = ContractIntelligenceSource.objects.select_related("run").annotate(item_count=Count("items"))

    def get_queryset(self):
        queryset = super().get_queryset()
        run_id = self.request.query_params.get("run")
        if run_id:
            queryset = queryset.filter(run_id=run_id)
        return queryset


class ContractIntelligenceItemViewSet(QuotationBaseViewSet, viewsets.ModelViewSet):
    serializer_class = ContractIntelligenceItemSerializer
    queryset = ContractIntelligenceItem.objects.select_related("run", "source", "product")
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        queryset = super().get_queryset()
        run_id = self.request.query_params.get("run")
        status_param = self.request.query_params.get("status")
        search = (self.request.query_params.get("search") or "").strip()
        if run_id:
            queryset = queryset.filter(run_id=run_id)
        if status_param:
            queryset = queryset.filter(status=status_param)
        if search:
            queryset = queryset.filter(
                Q(original_item_name__icontains=search)
                | Q(suggested_item_name__icontains=search)
                | Q(normalized_item_name__icontains=search)
                | Q(source__subject__icontains=search)
            )
        return queryset


class QuotationDashboardView(APIView):
    permission_classes = [IsQuotationStaff]

    def get(self, request):
        quote_queryset = Quotation.objects.filter(is_historical_import=False)
        return Response(
            {
                "companies": Company.objects.filter(is_active=True).count(),
                "items": Product.objects.exclude(status="archived").count(),
                "inquiries": Inquiry.objects.count(),
                "quotes": quote_queryset.count(),
                "pending": quote_queryset.filter(status__in=["draft", "pending_review", "approved"]).count(),
                "finalized": quote_queryset.filter(status__in=["finalized", "sent"]).count(),
            }
        )


def _decimal_response(value):
    if value is None:
        return None
    return str(Decimal(value or 0).quantize(Decimal("0.01")))


def _percentage(numerator, denominator):
    denominator = Decimal(denominator or 0)
    if denominator <= 0:
        return 0
    return round(float((Decimal(numerator or 0) / denominator) * Decimal("100")), 2)


def _analysis_base_queryset(request):
    queryset = Quotation.objects.filter(
        is_historical_import=False,
        status__in=[Quotation.STATUS_FINALIZED, Quotation.STATUS_SENT],
    ).select_related("company", "created_by")
    start = request.query_params.get("start") or request.query_params.get("date_from")
    end = request.query_params.get("end") or request.query_params.get("date_to")
    if start:
        queryset = queryset.filter(created_at__date__gte=start)
    if end:
        queryset = queryset.filter(created_at__date__lte=end)
    company = request.query_params.get("company")
    if company:
        queryset = queryset.filter(company_id=company)
    prepared_by = request.query_params.get("prepared_by")
    if prepared_by:
        queryset = queryset.filter(created_by_id=prepared_by)
    outcome_status = request.query_params.get("outcome_status")
    if outcome_status:
        queryset = queryset.filter(outcome_status=outcome_status)
    return queryset


class QuotationAnalysisDashboardView(APIView):
    permission_classes = [IsQuotationStaff]

    def get(self, request):
        queryset = _analysis_base_queryset(request)
        line_queryset = QuotationLine.objects.filter(quotation__in=queryset).exclude(match_status=QuotationLine.MATCH_IGNORED)
        product = request.query_params.get("product")
        if product:
            line_queryset = line_queryset.filter(Q(product_id=product) | Q(quote_item_id=product))
        reason = request.query_params.get("reason")
        if reason:
            line_queryset = line_queryset.filter(outcome_reason=reason)

        lines = list(
            line_queryset.select_related("quotation", "quotation__company", "quotation__created_by", "product", "quote_item")
        )
        quoted_value = sum((line.line_total or Decimal("0.00")) for line in lines)
        accepted_value = sum((line.accepted_total or Decimal("0.00")) for line in lines)
        lost_value = sum((line.lost_value or Decimal("0.00")) for line in lines)
        pending_value = sum(
            (quote.total or Decimal("0.00"))
            for quote in queryset
            if quote.outcome_status == Quotation.OUTCOME_PENDING
        )
        accepted_lines = sum(1 for line in lines if (line.accepted_total or 0) > 0)
        closed_quotes = [quote for quote in queryset if quote.outcome_closed_at]
        avg_days_to_close = None
        close_durations = []
        for quote in closed_quotes:
            start_date = quote.sent_at or quote.finalized_at or quote.created_at
            if start_date:
                close_durations.append((quote.outcome_closed_at - start_date).days)
        if close_durations:
            avg_days_to_close = round(sum(close_durations) / len(close_durations), 1)

        today = timezone.localdate()
        overdue_followups = queryset.filter(
            outcome_status=Quotation.OUTCOME_PENDING,
            next_follow_up_date__lt=today,
        ).count()

        def item_label(line):
            if line.product_id:
                return line.product.name
            if line.quote_item_id:
                return line.quote_item.name
            return line.item_name_snapshot

        def grouped_lines(filter_fn, label_fn, value_fn=lambda line: line.lost_value or Decimal("0.00"), limit=10):
            buckets = {}
            for line in lines:
                if not filter_fn(line):
                    continue
                label = label_fn(line) or "Unknown"
                entry = buckets.setdefault(label, {"label": label, "count": 0, "value": Decimal("0.00")})
                entry["count"] += 1
                entry["value"] += Decimal(value_fn(line) or 0)
            return [
                {**entry, "value": _decimal_response(entry["value"])}
                for entry in sorted(buckets.values(), key=lambda item: (item["value"], item["count"]), reverse=True)[:limit]
            ]

        customers = {}
        staff = {}
        for quote in queryset:
            summary = outcome_summary_for_quotation(quote)
            customer_entry = customers.setdefault(
                quote.company.name,
                {"label": quote.company.name, "quoted": Decimal("0.00"), "accepted": Decimal("0.00"), "lost": Decimal("0.00")},
            )
            staff_name = quote.created_by.username if quote.created_by_id else "Unassigned"
            staff_entry = staff.setdefault(
                staff_name,
                {"label": staff_name, "quoted": Decimal("0.00"), "accepted": Decimal("0.00"), "lost": Decimal("0.00")},
            )
            for entry in [customer_entry, staff_entry]:
                entry["quoted"] += summary["quoted_value"]
                entry["accepted"] += summary["accepted_value"]
                entry["lost"] += summary["lost_value"]

        def score_rows(rows, reverse=True):
            result = []
            for entry in rows.values():
                result.append(
                    {
                        "label": entry["label"],
                        "quoted": _decimal_response(entry["quoted"]),
                        "accepted": _decimal_response(entry["accepted"]),
                        "lost": _decimal_response(entry["lost"]),
                        "value_win_rate": _percentage(entry["accepted"], entry["quoted"]),
                    }
                )
            return sorted(result, key=lambda item: item["value_win_rate"], reverse=reverse)[:10]

        reason_labels = dict(QuotationLine.OUTCOME_REASON_CHOICES)
        reason_buckets = {}
        for line in lines:
            if not line.outcome_reason:
                continue
            entry = reason_buckets.setdefault(
                line.outcome_reason,
                {
                    "reason": line.outcome_reason,
                    "reason_display": reason_labels.get(line.outcome_reason, line.outcome_reason),
                    "lines": 0,
                    "lost_value": Decimal("0.00"),
                },
            )
            entry["lines"] += 1
            entry["lost_value"] += Decimal(line.lost_value or 0)
        lost_by_reason = [
            {**entry, "lost_value": _decimal_response(entry["lost_value"])}
            for entry in sorted(reason_buckets.values(), key=lambda item: (item["lost_value"], item["lines"]), reverse=True)[:10]
        ]
        pending_by_customer = {}
        for quote in queryset.filter(outcome_status=Quotation.OUTCOME_PENDING):
            entry = pending_by_customer.setdefault(quote.company.name, {"label": quote.company.name, "value": Decimal("0.00"), "count": 0})
            entry["value"] += Decimal(quote.total or 0)
            entry["count"] += 1

        return Response(
            {
                "cards": {
                    "total_quoted_value": _decimal_response(quoted_value),
                    "accepted_value": _decimal_response(accepted_value),
                    "lost_value": _decimal_response(lost_value),
                    "value_win_rate": _percentage(accepted_value, quoted_value),
                    "line_win_rate": round((accepted_lines / len(lines)) * 100, 2) if lines else 0,
                    "pending_quotation_value": _decimal_response(pending_value),
                    "overdue_followups": overdue_followups,
                    "average_days_to_close": avg_days_to_close,
                },
                "tables": {
                    "top_rejected_products": grouped_lines(
                        lambda line: line.outcome_status == QuotationLine.OUTCOME_REJECTED,
                        item_label,
                    ),
                    "top_unavailable_products": grouped_lines(
                        lambda line: line.outcome_status == QuotationLine.OUTCOME_UNAVAILABLE_MISSING,
                        item_label,
                    ),
                    "top_substituted_products": grouped_lines(
                        lambda line: line.outcome_status == QuotationLine.OUTCOME_SUBSTITUTED,
                        item_label,
                    ),
                    "best_converting_customers": score_rows(customers, reverse=True),
                    "worst_converting_customers": score_rows(customers, reverse=False),
                    "staff_performance": score_rows(staff, reverse=True),
                    "lost_value_by_reason": lost_by_reason,
                    "pending_value_by_customer": [
                        {**entry, "value": _decimal_response(entry["value"])}
                        for entry in sorted(pending_by_customer.values(), key=lambda item: item["value"], reverse=True)[:10]
                    ],
                },
            }
        )


class QuotationFollowupsView(APIView):
    permission_classes = [IsQuotationStaff]

    def get(self, request):
        today = timezone.localdate()
        stale_cutoff = timezone.now() - timedelta(days=7)
        base = Quotation.objects.filter(
            is_historical_import=False,
            status__in=[Quotation.STATUS_FINALIZED, Quotation.STATUS_SENT],
            outcome_status=Quotation.OUTCOME_PENDING,
        ).select_related("company", "created_by")
        due_today = base.filter(next_follow_up_date=today)
        overdue = base.filter(next_follow_up_date__lt=today)
        no_outcome = base.filter(Q(sent_at__lt=stale_cutoff) | Q(finalized_at__lt=stale_cutoff))
        high_value = base.order_by("-total")[:10]
        serializer_context = {"request": request}
        return Response(
            {
                "due_today": QuotationListSerializer(due_today, many=True, context=serializer_context).data,
                "overdue": QuotationListSerializer(overdue, many=True, context=serializer_context).data,
                "sent_no_outcome_after_7_days": QuotationListSerializer(no_outcome, many=True, context=serializer_context).data,
                "high_value_pending": QuotationListSerializer(high_value, many=True, context=serializer_context).data,
            }
        )


PRICE_CONTEXT_HISTORY_DEFAULT = 10
PRICE_CONTEXT_HISTORY_MAX = 50


def _positive_pk(value):
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _filter_price_history_item(queryset, params):
    """Apply Product-first, typed filters without mixing Product and QuoteItem IDs."""
    product_value = params.get("product")
    quote_item_value = params.get("quote_item")
    compatibility_value = params.get("item")

    if product_value not in (None, ""):
        product_id = _positive_pk(product_value)
        queryset = queryset.filter(product_id=product_id) if product_id else queryset.none()
    if quote_item_value not in (None, ""):
        quote_item_id = _positive_pk(quote_item_value)
        queryset = queryset.filter(quote_item_id=quote_item_id) if quote_item_id else queryset.none()

    # `item` used to OR together two unrelated ID namespaces. Runtime item
    # selectors now contain Products, so keep `item` compatible as Product-only.
    # Old callers can still request QuoteItem rows explicitly with
    # `item_type=quote_item` (or the typed `quote_item` parameter above).
    if (
        product_value in (None, "")
        and quote_item_value in (None, "")
        and compatibility_value not in (None, "")
    ):
        item_id = _positive_pk(compatibility_value)
        if not item_id:
            return queryset.none()
        if params.get("item_type") == "quote_item":
            return queryset.filter(quote_item_id=item_id)
        return queryset.filter(product_id=item_id)
    return queryset


def _price_context_history_limit(value):
    try:
        requested = int(value)
    except (TypeError, ValueError):
        requested = PRICE_CONTEXT_HISTORY_DEFAULT
    return min(max(requested, 1), PRICE_CONTEXT_HISTORY_MAX)


def _price_context_date(value):
    if not value:
        return None
    if isinstance(value, datetime) and timezone.is_aware(value):
        value = timezone.localtime(value)
    if isinstance(value, datetime):
        value = value.date()
    return value.isoformat()


def _price_context_queryset(quotation):
    price_context_po_imports = QuotationOutcomePOImport.objects.only(
        "id",
        "gmail_evidence_id",
        "suggestions",
    ).order_by("-created_at", "-id")
    price_context_gmail_evidence = QuotationPOEvidence.objects.only("id").prefetch_related(
        Prefetch(
            "po_imports",
            queryset=price_context_po_imports,
            to_attr="price_context_po_imports",
        )
    )
    confirmed_lpos = (
        QuotationLPO.objects.filter(status=QuotationLPO.STATUS_CONFIRMED)
        .exclude(lpo_number="")
        .only(
            "id",
            "quotation_id",
            "gmail_evidence_id",
            "lpo_number",
            "status",
            "received_at",
            "parsed_meta",
        )
        .prefetch_related(
            Prefetch(
                "gmail_evidence",
                queryset=price_context_gmail_evidence,
                to_attr="price_context_gmail_evidence",
            )
        )
        .order_by("-received_at", "-id")
    )
    return (
        CompanyPriceHistory.objects.filter(company=quotation.company, product__isnull=False)
        .exclude(quotation=quotation)
        .select_related("quotation", "quotation_line")
        .prefetch_related(
            Prefetch(
                "quotation__lpos",
                queryset=confirmed_lpos,
                to_attr="confirmed_lpos_for_price_context",
            )
        )
    )


def _po_suggestion_line_ids(suggestions):
    line_ids = set()
    for suggestion in suggestions or []:
        if not isinstance(suggestion, dict):
            continue
        value = suggestion.get("quotation_line_id", suggestion.get("quotation_line"))
        try:
            line_ids.add(int(value))
        except (TypeError, ValueError):
            continue
    return line_ids


def _applied_po_suggestion_line_ids(suggestions):
    return {
        line_id
        for suggestion in (suggestions or [])
        if isinstance(suggestion, dict) and suggestion.get("outcome_applied") is True
        for line_id in [_positive_pk(suggestion.get("quotation_line_id", suggestion.get("quotation_line")))]
        if line_id
    }


def _applied_lpo_line_ids(parsed_meta):
    values = (parsed_meta or {}).get("applied_outcome_line_ids") or []
    return {line_id for value in values for line_id in [_positive_pk(value)] if line_id}


def _price_context_lpo_for_line(quotation, line):
    confirmed_lpos = getattr(quotation, "confirmed_lpos_for_price_context", [])
    explicit_provenance_found = False
    parser_hint_found = False
    for lpo in confirmed_lpos:
        parsed_meta = lpo.parsed_meta or {}
        applied_line_ids = _applied_lpo_line_ids(parsed_meta)
        parser_line_ids = _po_suggestion_line_ids(parsed_meta.get("outcome_suggestions"))
        evidence = getattr(lpo, "price_context_gmail_evidence", None)
        po_imports = getattr(evidence, "price_context_po_imports", [])
        if po_imports:
            # A Gmail evidence row has one canonical import today, but merge
            # all prefetched imports defensively. Parser suggestions are only
            # hints; only staff-applied markers establish positive provenance.
            for po_import in po_imports:
                applied_line_ids.update(
                    _applied_po_suggestion_line_ids(po_import.suggestions)
                )
                parser_line_ids.update(_po_suggestion_line_ids(po_import.suggestions))
        explicit_provenance_found = explicit_provenance_found or bool(applied_line_ids)
        parser_hint_found = parser_hint_found or bool(parser_line_ids)
        if line.id in applied_line_ids:
            # Confirmed LPOs are already newest-first. If the same line was
            # ordered repeatedly, expose the newest source that actually
            # contains that line rather than an unrelated quotation-level LPO.
            return lpo
    if len(confirmed_lpos) == 1 and not explicit_provenance_found and not parser_hint_found:
        # Legacy and manually recorded orders may predate line-level source
        # metadata. A sole confirmed LPO is unambiguous only when it does not
        # contain parser hints or explicit provenance that need staff review.
        return confirmed_lpos[0]
    return None


def _price_context_row(entry):
    quotation = entry.quotation
    line = entry.quotation_line
    is_accepted = (
        line.outcome_status
        in {QuotationLine.OUTCOME_ACCEPTED, QuotationLine.OUTCOME_QUANTITY_CHANGED}
        and line.accepted_unit_price is not None
    )
    confirmed_lpo = _price_context_lpo_for_line(quotation, line) if is_accepted else None
    accepted_at = None
    if is_accepted:
        accepted_at = quotation.outcome_date or quotation.outcome_last_updated_at or line.updated_at
    return {
        "quotation": quotation.id,
        "quotation_number": quotation.quotation_number,
        "quoted_at": _price_context_date(entry.quoted_at),
        "quoted_unit_price": format_unit_price_value(entry.unit_price),
        "quantity": str(entry.quantity),
        "unit": entry.unit or "",
        "currency": entry.currency,
        "outcome_status": line.outcome_status,
        "accepted_unit_price": format_unit_price_value(line.accepted_unit_price) if is_accepted else None,
        "accepted_quantity": str(line.accepted_quantity) if is_accepted and line.accepted_quantity is not None else None,
        "accepted_at": _price_context_date(accepted_at),
        "lpo_number": confirmed_lpo.lpo_number if confirmed_lpo else "",
    }


def _accepted_price_ordering():
    return [
        F("quotation__outcome_date").desc(nulls_last=True),
        F("quotation__outcome_last_updated_at").desc(nulls_last=True),
        F("quotation_line__updated_at").desc(nulls_last=True),
        F("quoted_at").desc(),
        F("id").desc(),
    ]


def _product_price_context_payload(quotation, product, history_entries, latest_accepted_entry):
    history_rows = [_price_context_row(entry) for entry in history_entries]
    latest_quoted = history_rows[0] if history_rows else None
    latest_accepted = _price_context_row(latest_accepted_entry) if latest_accepted_entry else None

    if latest_quoted:
        return {
            "product": product.id,
            "product_name": product.name,
            "unit_price": latest_quoted["quoted_unit_price"],
            "unit": latest_quoted["unit"],
            "currency": latest_quoted["currency"],
            "source": "company_price_history",
            "source_label": f"Latest {quotation.company.name} price",
            "quoted_at": latest_quoted["quoted_at"],
            "latest_quoted": latest_quoted,
            "latest_accepted": latest_accepted,
            "history": history_rows,
        }

    return {
        "product": product.id,
        "product_name": product.name,
        "unit_price": "",
        "unit": "",
        "currency": quotation.currency,
        "source": "no_company_price_history",
        "source_label": f"No previous {quotation.company.name} price",
        "quoted_at": "",
        "latest_quoted": None,
        "latest_accepted": None,
        "history": [],
    }


def _build_product_price_context(quotation, product, history_limit):
    history_queryset = _price_context_queryset(quotation).filter(product=product)
    history_entries = list(history_queryset.order_by("-quoted_at", "-id")[:history_limit])
    latest_accepted_entry = (
        history_queryset.filter(
            quotation_line__outcome_status__in=[
                QuotationLine.OUTCOME_ACCEPTED,
                QuotationLine.OUTCOME_QUANTITY_CHANGED,
            ],
            quotation_line__accepted_unit_price__isnull=False,
        )
        .order_by(*_accepted_price_ordering())
        .first()
    )
    return _product_price_context_payload(
        quotation,
        product,
        history_entries,
        latest_accepted_entry,
    )


def _build_product_price_contexts(quotation, products_by_id, product_ids, history_limit):
    history_queryset = _price_context_queryset(quotation).filter(product_id__in=product_ids)
    history_entries = list(
        history_queryset.annotate(
            price_context_rank=Window(
                expression=RowNumber(),
                partition_by=[F("product_id")],
                order_by=[F("quoted_at").desc(), F("id").desc()],
            )
        )
        .filter(price_context_rank__lte=history_limit)
        .order_by("product_id", "-quoted_at", "-id")
    )
    latest_accepted_entries = list(
        history_queryset.filter(
            quotation_line__outcome_status__in=[
                QuotationLine.OUTCOME_ACCEPTED,
                QuotationLine.OUTCOME_QUANTITY_CHANGED,
            ],
            quotation_line__accepted_unit_price__isnull=False,
        )
        .annotate(
            accepted_price_rank=Window(
                expression=RowNumber(),
                partition_by=[F("product_id")],
                order_by=_accepted_price_ordering(),
            )
        )
        .filter(accepted_price_rank=1)
        .order_by("product_id")
    )

    history_by_product = {product_id: [] for product_id in product_ids}
    for entry in history_entries:
        history_by_product[entry.product_id].append(entry)
    latest_accepted_by_product = {entry.product_id: entry for entry in latest_accepted_entries}

    return {
        str(product_id): _product_price_context_payload(
            quotation,
            products_by_id[product_id],
            history_by_product[product_id],
            latest_accepted_by_product.get(product_id),
        )
        for product_id in product_ids
    }


class CompanyViewSet(QuotationBaseViewSet, viewsets.ModelViewSet):
    serializer_class = CompanySerializer
    queryset = Company.objects.all()

    def get_serializer_class(self):
        if self.action == "list" and self.request.query_params.get("include_contacts") != "true":
            return CompanyListSerializer
        return CompanySerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == "list" and self.request.query_params.get("include_contacts") != "true":
            queryset = queryset.annotate(contact_count=Count("contacts", distinct=True))
        else:
            queryset = queryset.prefetch_related("contacts")
        search = self.request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(email__icontains=search)
                | Q(phone__icontains=search)
                | Q(trn__icontains=search)
            )
        if self.request.query_params.get("active") == "true":
            queryset = queryset.filter(is_active=True)
        return queryset

    def perform_create(self, serializer):
        company = serializer.save()
        audit_log(self.request.user, QuotationAuditLog.ACTION_CREATED, company, message="Created company.")

    def perform_update(self, serializer):
        company = serializer.save()
        audit_log(self.request.user, QuotationAuditLog.ACTION_UPDATED, company, message="Updated company.")

    @action(detail=False, methods=["get"])
    def similar(self, request):
        name = (request.query_params.get("name") or "").strip()
        if len(name) < 3:
            return Response({"suggestions": []})
        queryset = self.get_queryset()
        if request.query_params.get("active") != "false":
            queryset = queryset.filter(is_active=True)
        suggestions = find_similar_companies(name, queryset=queryset, threshold=70, limit=8)
        return Response({"suggestions": suggestions})

    def destroy(self, request, *args, **kwargs):
        company = self.get_object()
        has_references = (
            company.quotations.exists()
            or company.inquiries.exists()
            or company.price_history.exists()
            or company.historical_price_imports.exists()
            or company.product_aliases.exists()
        )
        if has_references:
            company.is_active = False
            company.save(update_fields=["is_active", "updated_at"])
            audit_log(request.user, QuotationAuditLog.ACTION_UPDATED, company, message="Deactivated referenced company.")
            return Response(self.get_serializer(company).data)
        audit_log(request.user, QuotationAuditLog.ACTION_DELETED, company, message="Deleted unused company.")
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["get"])
    def price_history(self, request, pk=None):
        company = self.get_object()
        queryset = CompanyPriceHistory.objects.filter(company=company).select_related(
            "company", "product", "quote_item", "quotation", "created_by"
        )
        queryset = _filter_price_history_item(queryset, request.query_params)
        serializer = CompanyPriceHistorySerializer(queryset, many=True)
        return Response(serializer.data)


class CompanyContactViewSet(QuotationBaseViewSet, viewsets.ModelViewSet):
    serializer_class = CompanyContactSerializer
    queryset = CompanyContact.objects.select_related("company")

    def get_queryset(self):
        queryset = super().get_queryset()
        company_id = self.request.query_params.get("company")
        if company_id:
            queryset = queryset.filter(company_id=company_id)
        if self.request.query_params.get("active") == "true":
            queryset = queryset.filter(is_active=True)
        return queryset

    def perform_create(self, serializer):
        contact = serializer.save()
        audit_log(self.request.user, QuotationAuditLog.ACTION_CREATED, contact, message="Created company contact.")

    def perform_update(self, serializer):
        contact = serializer.save()
        audit_log(self.request.user, QuotationAuditLog.ACTION_UPDATED, contact, message="Updated company contact.")


class QuoteItemViewSet(QuotationBaseViewSet, viewsets.ModelViewSet):
    serializer_class = QuoteItemSerializer
    queryset = Product.objects.select_related("brand", "category")

    def get_serializer_class(self):
        if self.action == "list":
            return QuoteItemListSerializer
        return QuoteItemSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        search = self.request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(sku__icontains=search)
                | Q(barcode__icontains=search)
                | Q(brand__name__icontains=search)
                | Q(active_ingredient__icontains=search)
            )
        if self.request.query_params.get("active") == "true":
            queryset = queryset.exclude(status="archived")
        company_used = self.request.query_params.get("company_used")
        if company_used:
            queryset = queryset.filter(
                Q(company_price_history__company_id=company_used)
                | Q(quotation_aliases__company_id=company_used, quotation_aliases__is_active=True)
                | Q(quotation_lines__quotation__company_id=company_used)
            ).distinct()
        return queryset.order_by("name")

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        name = values.pop("name")
        resolution = create_or_reuse_product(
            name=name,
            sku=values.get("sku") or "",
            barcode=values.get("barcode") or "",
            dosage=values.get("dosage") or "",
            pack_size=values.get("pack_size") or "",
            defaults=values,
            confirm_create=str(request.data.get("confirm_create") or "").lower() in {"1", "true", "yes", "on"},
        )
        if resolution.requires_confirmation:
            return Response(
                {"detail": resolution.warning, **resolution.as_dict()},
                status=status.HTTP_409_CONFLICT,
            )
        item = resolution.product
        if resolution.created:
            audit_log(self.request.user, QuotationAuditLog.ACTION_CREATED, item, message="Created quotation product.")
        payload = dict(QuoteItemSerializer(item, context={"request": request}).data)
        payload.update(resolution.as_dict())
        return Response(payload, status=status.HTTP_201_CREATED if resolution.created else status.HTTP_200_OK)

    def perform_create(self, serializer):
        item = serializer.save()
        audit_log(self.request.user, QuotationAuditLog.ACTION_CREATED, item, message="Created quotation product.")

    def perform_update(self, serializer):
        item = serializer.save()
        audit_log(self.request.user, QuotationAuditLog.ACTION_UPDATED, item, message="Updated quotation product.")

    def destroy(self, request, *args, **kwargs):
        product = self.get_object()
        has_references = (
            product.quotation_lines.exists()
            or product.quotation_inquiry_lines.exists()
            or product.historical_import_lines.exists()
            or product.company_price_history.exists()
        )
        if has_references:
            product.status = "archived"
            product.save(update_fields=["status", "updated_at"])
            audit_log(request.user, QuotationAuditLog.ACTION_UPDATED, product, message="Archived referenced quotation product.")
            return Response(self.get_serializer(product).data)
        audit_log(request.user, QuotationAuditLog.ACTION_DELETED, product, message="Deleted unused quotation product.")
        return super().destroy(request, *args, **kwargs)


class ProductAliasViewSet(QuotationBaseViewSet, viewsets.ModelViewSet):
    serializer_class = ProductAliasSerializer
    queryset = ProductAlias.objects.select_related("company", "product", "created_by")

    def get_queryset(self):
        queryset = super().get_queryset()
        company_id = self.request.query_params.get("company")
        product_id = self.request.query_params.get("product")
        search = self.request.query_params.get("search", "").strip()
        if company_id:
            queryset = queryset.filter(company_id=company_id)
        if product_id:
            queryset = queryset.filter(product_id=product_id)
        if search:
            queryset = queryset.filter(Q(alias__icontains=search) | Q(product__name__icontains=search))
        return queryset

    def perform_create(self, serializer):
        with transaction.atomic():
            alias = create_managed_product_alias(
                company=serializer.validated_data.get("company"),
                product=serializer.validated_data["product"],
                alias_text=serializer.validated_data["alias"],
                notes=serializer.validated_data.get("notes", ""),
                is_active=serializer.validated_data.get("is_active", True),
                actor=self.request.user,
            )
            serializer.instance = alias
            audit_log(self.request.user, QuotationAuditLog.ACTION_CREATED, alias, message="Created product alias.")

    def perform_update(self, serializer):
        with transaction.atomic():
            alias = update_managed_product_alias(
                alias_id=serializer.instance.pk,
                changes=dict(serializer.validated_data),
            )
            serializer.instance = alias
            audit_log(self.request.user, QuotationAuditLog.ACTION_UPDATED, alias, message="Updated product alias.")

    def create(self, request, *args, **kwargs):
        try:
            return super().create(request, *args, **kwargs)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        except IntegrityError as exc:
            return self.handle_safe_workflow_exception(exc, "Create product alias failed.")

    def update(self, request, *args, **kwargs):
        try:
            return super().update(request, *args, **kwargs)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        except IntegrityError as exc:
            return self.handle_safe_workflow_exception(exc, "Update product alias failed.")

    def partial_update(self, request, *args, **kwargs):
        try:
            return super().partial_update(request, *args, **kwargs)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        except IntegrityError as exc:
            return self.handle_safe_workflow_exception(exc, "Update product alias failed.")


class InquiryViewSet(QuotationBaseViewSet, viewsets.ModelViewSet):
    serializer_class = InquirySerializer
    queryset = Inquiry.objects.select_related("company", "contact", "created_by").prefetch_related("lines", "quotations")

    def get_queryset(self):
        queryset = super().get_queryset()
        company_id = self.request.query_params.get("company")
        status_filter = self.request.query_params.get("status")
        search = self.request.query_params.get("search", "").strip()
        if company_id:
            queryset = queryset.filter(company_id=company_id)
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if search:
            queryset = queryset.filter(Q(subject__icontains=search) | Q(original_text__icontains=search))
        return queryset

    def perform_create(self, serializer):
        with transaction.atomic():
            inquiry = serializer.save()
            audit_log(self.request.user, QuotationAuditLog.ACTION_CREATED, inquiry, message="Created inquiry.")

    def perform_update(self, serializer):
        inquiry = serializer.save()
        audit_log(self.request.user, QuotationAuditLog.ACTION_UPDATED, inquiry, message="Updated inquiry.")

    def create(self, request, *args, **kwargs):
        try:
            return super().create(request, *args, **kwargs)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)

    @action(detail=False, methods=["post"])
    def parse_text(self, request):
        raw_text = request.data.get("raw_text") or request.data.get("text") or ""
        raw_html = request.data.get("raw_html") or request.data.get("html") or ""
        if not str(raw_text).strip():
            return Response({"detail": "Paste inquiry text before extracting lines."}, status=status.HTTP_400_BAD_REQUEST)
        preview = parse_text_preview(raw_text, raw_html=raw_html)
        self._apply_product_matches(preview, request.data.get("company"))
        maybe_attach_auto_ai_candidate(preview, actor=request.user, allow_vision=False)
        if preview.get("ai_candidate"):
            self._apply_product_matches(preview["ai_candidate"], request.data.get("company"))
        return Response(preview)

    @action(detail=False, methods=["post"], parser_classes=[MultiPartParser, FormParser])
    def parse_file(self, request):
        try:
            preview = parse_file_preview(request.FILES.get("file"))
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        self._apply_product_matches(preview, request.data.get("company"))
        maybe_attach_auto_ai_candidate(preview, actor=request.user, allow_vision=True)
        if preview.get("ai_candidate"):
            self._apply_product_matches(preview["ai_candidate"], request.data.get("company"))
        return Response(preview)

    @action(detail=False, methods=["post"], parser_classes=[MultiPartParser, FormParser])
    def apply_price_reference(self, request):
        raw_preview = request.data.get("preview") or "{}"
        try:
            preview = json.loads(raw_preview) if isinstance(raw_preview, str) else raw_preview
        except json.JSONDecodeError:
            return Response({"detail": "Send the current inquiry preview as valid JSON."}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(preview, dict) or not isinstance(preview.get("lines"), list):
            return Response({"detail": "A parsed inquiry preview with lines is required before applying price references."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            reference_rows, reference_meta = parse_price_reference_source(
                request.FILES.get("file"),
                raw_text=request.data.get("raw_text") or "",
                raw_html=request.data.get("raw_html") or "",
                use_ai=str(request.data.get("use_ai") or "").lower() in {"1", "true", "yes", "on"},
                actor=request.user,
            )
            updated_preview = apply_price_reference_to_preview(preview, reference_rows)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        updated_preview["price_reference"] = reference_meta
        updated_preview.setdefault("warnings", [])
        updated_preview["warnings"] = [
            *(updated_preview.get("warnings") or []),
            *(reference_meta.get("warnings") or []),
        ]
        return Response(updated_preview)

    def _apply_product_matches(self, preview, company_id):
        company = None
        if company_id:
            company = Company.objects.filter(pk=company_id).first()
        for line in preview.get("lines", []):
            apply_match_to_preview_line(line, company)

    @action(detail=False, methods=["post"])
    def create_imported(self, request):
        serializer = ImportedInquiryCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            inquiry = create_imported_inquiry(serializer.validated_data, request.user)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        response_serializer = InquirySerializer(inquiry, context={"request": request})
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"])
    def ai_clean_parse(self, request):
        preview = request.data.get("preview") or {}
        if not isinstance(preview, dict):
            return Response({"detail": "A deterministic preview object is required."}, status=status.HTTP_400_BAD_REQUEST)
        requested_mode = request.data.get("mode") or "auto"
        try:
            candidate = clean_preview_with_ai(
                preview,
                actor=request.user,
                requested_mode=requested_mode,
                allow_vision=True,
            )
        except AIParseError as exc:
            return Response(
                {
                    "detail": str(exc),
                    "ai_status": "ai_failed_using_original_parse",
                    "ai_status_label": "AI failed, using original parse.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        self._apply_product_matches(candidate, request.data.get("company"))
        return Response(candidate)

    @action(detail=True, methods=["post"])
    def create_quote(self, request, pk=None):
        inquiry = self.get_object()
        try:
            quotation, created = create_quotation_from_inquiry(inquiry, request.user)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        serializer = QuotationSerializer(quotation, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class InquiryLineViewSet(QuotationBaseViewSet, viewsets.ModelViewSet):
    serializer_class = InquiryLineSerializer
    queryset = InquiryLine.objects.select_related("inquiry", "inquiry__company", "matched_quote_item", "matched_product")

    def get_queryset(self):
        queryset = super().get_queryset()
        inquiry_id = self.request.query_params.get("inquiry")
        if inquiry_id:
            queryset = queryset.filter(inquiry_id=inquiry_id)
        return queryset

    def perform_create(self, serializer):
        with transaction.atomic():
            line = serializer.save()
            learn_confirmed_inquiry_line_alias(
                line,
                self.request.user,
                explicit_confirmation=True,
            )
            audit_log(self.request.user, QuotationAuditLog.ACTION_CREATED, line, message="Created inquiry line.")

    def perform_update(self, serializer):
        with transaction.atomic():
            line = (
                InquiryLine.objects.select_for_update(of=("self",))
                .select_related("inquiry__company", "matched_product")
                .get(pk=serializer.instance.pk)
            )
            serializer.instance = line
            next_product = serializer.validated_data.get("matched_product", line.matched_product)
            product_changed = (
                "matched_product" in serializer.validated_data
                and getattr(next_product, "pk", None) != line.matched_product_id
            )
            raw_name_changed = (
                "raw_name" in serializer.validated_data
                and str(serializer.validated_data.get("raw_name") or "").strip()
                != str(line.raw_name or "").strip()
            )
            match_status_changed = (
                "match_status" in serializer.validated_data
                and str(serializer.validated_data.get("match_status") or "")
                != str(line.match_status or "")
            )
            match_fields_changed = product_changed or raw_name_changed or match_status_changed
            line = serializer.save()
            if match_fields_changed:
                learn_confirmed_inquiry_line_alias(
                    line,
                    self.request.user,
                    explicit_confirmation=True,
                )
            audit_log(self.request.user, QuotationAuditLog.ACTION_UPDATED, line, message="Updated inquiry line.")

    def create(self, request, *args, **kwargs):
        try:
            return super().create(request, *args, **kwargs)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)

    def update(self, request, *args, **kwargs):
        try:
            return super().update(request, *args, **kwargs)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)

    def partial_update(self, request, *args, **kwargs):
        try:
            return super().partial_update(request, *args, **kwargs)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)

    @action(detail=True, methods=["post"])
    def remember_alias(self, request, pk=None):
        try:
            alias = remember_inquiry_line_alias(self.get_object(), request.user)
        except (DjangoValidationError, ValueError) as exc:
            return self.handle_workflow_error(exc)
        return Response(ProductAliasSerializer(alias, context={"request": request}).data, status=status.HTTP_201_CREATED)


PO_EVIDENCE_ACTIVE_STATUSES = [
    QuotationPOEvidence.STATUS_CANDIDATE,
    QuotationPOEvidence.STATUS_AMBIGUOUS,
    QuotationPOEvidence.STATUS_PARSED,
    QuotationPOEvidence.STATUS_FAILED,
]
PO_EVIDENCE_ARCHIVE_PAGE_SIZE = 20
PO_EVIDENCE_ARCHIVE_MAX_PAGE_SIZE = 50


def _bounded_int(value, *, default, minimum=0, maximum=50):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _quotation_po_evidence_page(quotation, request):
    connection = resolve_gmail_connection(request.user, shared_only=True)
    queryset = quotation.po_evidence.none()
    if connection:
        queryset = quotation.po_evidence.filter(
            gmail_connection_lineage_q(connection)
        ).select_related(
            "mailbox_message", "mailbox_match_run"
        )
    order = ("-confidence", "-sent_at", "-created_at")
    active = list(queryset.filter(status__in=PO_EVIDENCE_ACTIVE_STATUSES).order_by(*order))
    archived_queryset = queryset.exclude(status__in=PO_EVIDENCE_ACTIVE_STATUSES).order_by(*order)
    archived_total = archived_queryset.count()
    archived_offset = _bounded_int(
        request.query_params.get("archived_offset"),
        default=0,
        minimum=0,
        maximum=max(archived_total, 0),
    )
    archived_limit = _bounded_int(
        request.query_params.get("archived_limit"),
        default=PO_EVIDENCE_ARCHIVE_PAGE_SIZE,
        minimum=1,
        maximum=PO_EVIDENCE_ARCHIVE_MAX_PAGE_SIZE,
    )
    archived = list(archived_queryset[archived_offset:archived_offset + archived_limit])
    next_offset = archived_offset + len(archived)
    return active + archived, {
        "active_count": len(active),
        "archived_count": archived_total,
        "archived_offset": archived_offset,
        "archived_limit": archived_limit,
        "archived_returned": len(archived),
        "archived_has_more": next_offset < archived_total,
        "archived_next_offset": next_offset if next_offset < archived_total else None,
    }


class MailboxPOAuditRunViewSet(QuotationBaseViewSet, viewsets.ReadOnlyModelViewSet):
    serializer_class = MailboxPOAuditRunSerializer
    queryset = MailboxPOAuditRun.objects.select_related("gmail_connection", "requested_by")

    def get_queryset(self):
        connection = resolve_gmail_connection(self.request.user, shared_only=True)
        if not connection:
            return super().get_queryset().none()
        return super().get_queryset().filter(gmail_connection=connection)

    def _response(self, run, *, http_status=status.HTTP_200_OK, repair_summary=None):
        # Only the current algorithm can complete the current client workflow.
        # Returning a historical match here makes a version rollover look done
        # and prevents the browser from requesting reconciliation with the new
        # matcher.
        latest_match = (
            run.match_runs.filter(algorithm_version=ALGORITHM_VERSION)
            .select_related("requested_by")
            .first()
        )
        has_any_match = bool(latest_match) or run.match_runs.exists()
        inventory_done = bool(run.status == MailboxPOAuditRun.STATUS_COMPLETED and run.exhausted)
        vision_availability = mailbox_vision_availability()
        repair_remaining = (
            mailbox_po_audit_repair_remaining(run)
            if inventory_done and not has_any_match and vision_availability["available"]
            else 0
        )
        return Response(
            {
                "run": MailboxPOAuditRunSerializer(run, context={"request": self.request}).data,
                "match_run": (
                    MailboxPOMatchRunSerializer(latest_match, context={"request": self.request}).data
                    if latest_match
                    else None
                ),
                "inventory_done": inventory_done,
                "inventory_complete": bool(
                    run.status == MailboxPOAuditRun.STATUS_COMPLETED
                    and run.exhausted
                    and run.incomplete_messages == 0
                ),
                "repair_done": bool(inventory_done and repair_remaining == 0),
                "repair_remaining": repair_remaining,
                "repair_summary": repair_summary,
                "mailbox_vision_available": vision_availability["available"],
                "mailbox_vision_reason": vision_availability["reason"],
                "done": bool(
                    run.status == MailboxPOAuditRun.STATUS_COMPLETED
                    and run.exhausted
                    and latest_match
                    and latest_match.status == MailboxPOMatchRun.STATUS_COMPLETED
                ),
            },
            status=http_status,
        )

    def create(self, request, *args, **kwargs):
        connection = resolve_gmail_connection(request.user, shared_only=True)
        if not connection or connection.status != GmailOAuthConnection.STATUS_CONNECTED:
            return Response(
                {"detail": "Connect the shared Gmail mailbox before starting a mailbox-wide PO audit."},
                status=status.HTTP_409_CONFLICT,
            )
        restart = str(request.data.get("restart", "false")).lower() in {"1", "true", "yes", "on"}
        if not restart:
            resumable = (
                MailboxPOAuditRun.objects.filter(
                    gmail_connection=connection,
                    status__in=[
                        MailboxPOAuditRun.STATUS_PENDING,
                        MailboxPOAuditRun.STATUS_RUNNING,
                        MailboxPOAuditRun.STATUS_FAILED,
                    ],
                )
                .order_by("-created_at")
                .first()
            )
            if resumable:
                return self._response(resumable)
            # Inventory and reconciliation are deliberately separate phases.
            # Return a completed snapshot that has not yet been reconciled so
            # the client continues that job instead of silently starting over.
            awaiting_reconciliation = (
                MailboxPOAuditRun.objects.filter(
                    gmail_connection=connection,
                    status=MailboxPOAuditRun.STATUS_COMPLETED,
                    exhausted=True,
                )
                .exclude(
                    match_runs__algorithm_version=ALGORITHM_VERSION,
                    match_runs__status=MailboxPOMatchRun.STATUS_COMPLETED,
                )
                .order_by("-created_at")
                .first()
            )
            if awaiting_reconciliation:
                return self._response(awaiting_reconciliation)
        try:
            run = start_mailbox_po_audit(connection, requested_by=request.user)
        except (ValueError, RuntimeError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return self._response(run, http_status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"])
    def latest(self, request):
        connection = resolve_gmail_connection(request.user, shared_only=True)
        run = (
            self.get_queryset().filter(gmail_connection=connection).first()
            if connection
            else None
        )
        if not run:
            vision_availability = mailbox_vision_availability()
            return Response(
                {
                    "run": None,
                    "match_run": None,
                    "inventory_done": False,
                    "inventory_complete": False,
                    "repair_done": False,
                    "repair_remaining": 0,
                    "repair_summary": None,
                    "mailbox_vision_available": vision_availability["available"],
                    "mailbox_vision_reason": vision_availability["reason"],
                    "done": False,
                }
            )
        return self._response(run)

    @action(detail=True, methods=["post"], parser_classes=[JSONParser])
    def scan_page(self, request, pk=None):
        run = self.get_object()
        if run.status == MailboxPOAuditRun.STATUS_COMPLETED or run.exhausted:
            return self._response(run)
        try:
            page_size = max(1, min(int(request.data.get("page_size") or 10), 25))
            run = scan_mailbox_po_audit_page(run, page_size=page_size)
        except (TypeError, ValueError, RuntimeError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return self._response(run)

    @action(detail=True, methods=["post"], parser_classes=[JSONParser])
    def repair_page(self, request, pk=None):
        run = self.get_object()
        try:
            # Keep API requests comfortably below Gunicorn's timeout. The
            # management command may use a larger explicit batch off-request.
            limit = 1
            summary = repair_mailbox_po_audit_pdf_vision(
                run,
                message_ids=request.data.get("message_ids") or [],
                limit=limit,
                actor=request.user,
            )
        except (AIParseError, RuntimeError, TypeError, ValueError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        run.refresh_from_db()
        return self._response(run, repair_summary=summary)

    @action(detail=True, methods=["post"], parser_classes=[JSONParser])
    def reconcile(self, request, pk=None):
        run = self.get_object()
        if run.status != MailboxPOAuditRun.STATUS_COMPLETED or not run.exhausted:
            return Response(
                {"detail": "Finish the mailbox inventory before matching quotations."},
                status=status.HTTP_409_CONFLICT,
            )
        force = str(request.data.get("force", "false")).lower() in {"1", "true", "yes", "on"}
        current = run.match_runs.filter(
            algorithm_version=ALGORITHM_VERSION,
            status=MailboxPOMatchRun.STATUS_COMPLETED,
        ).first()
        if current and not force:
            return self._response(run)
        if not run.match_runs.exists():
            try:
                assert_mailbox_po_audit_repairable(run)
                reclassify_mailbox_po_audit_messages(run)
                mark_unavailable_mailbox_vision_for_manual_review(run)
            except ValueError as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
            repair_remaining = mailbox_po_audit_repair_remaining(run)
            if repair_remaining:
                return Response(
                    {
                        "detail": (
                            "Finish the bounded mailbox PDF repair phase before matching quotations."
                        ),
                        "repair_done": False,
                        "repair_remaining": repair_remaining,
                    },
                    status=status.HTTP_409_CONFLICT,
                )
        try:
            page_size = max(1, min(int(request.data.get("page_size") or 5), 10))
            reconcile_mailbox_po_audit_page(
                run,
                requested_by=request.user,
                page_size=page_size,
                force=force,
            )
        except MailboxPOMatchBusy as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except (TypeError, ValueError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            logger.exception("Mailbox PO reconciliation failed for audit %s", run.pk)
            return Response(
                {"detail": f"Mailbox inventory completed, but quotation matching failed. {str(exc)[:250]}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        run.refresh_from_db()
        return self._response(run)


class QuotationPOEvidenceViewSet(QuotationBaseViewSet, viewsets.GenericViewSet):
    """Narrow evidence-source endpoints used by the manual review screen."""

    serializer_class = QuotationPOEvidenceSerializer
    queryset = (
        QuotationPOEvidence.objects.select_related(
            "gmail_connection",
            "quotation__company",
            "mailbox_message",
            "canonical_lpo",
        )
        .prefetch_related(
            "quotation__lines",
            "quotation__lines__product",
            "quotation__lines__quote_item",
        )
    )

    def get_queryset(self):
        connection = resolve_gmail_connection(self.request.user, shared_only=True)
        if not connection:
            return super().get_queryset().none()
        return super().get_queryset().filter(gmail_connection_lineage_q(connection))

    @action(detail=True, methods=["get"])
    def source(self, request, pk=None):
        evidence = self.get_object()
        message = evidence.mailbox_message
        latest_po_import = latest_relevant_po_import(evidence)
        max_chars = _bounded_int(
            getattr(settings, "PO_EVIDENCE_SOURCE_VIEW_MAX_CHARS", 100000),
            default=100000,
            minimum=1000,
            maximum=250000,
        )
        extracted_text = str(evidence.extracted_text or "")
        email_body = str(getattr(message, "newest_body_text", "") or "")
        try:
            commercial_comparison = safe_build_po_evidence_commercial_comparison(evidence)
        except Exception:
            # Source text and attachments remain primary review evidence. A
            # malformed legacy parser row must not make that evidence
            # unviewable merely because the convenience comparison failed.
            logger.exception(
                "Commercial comparison failed while viewing PO evidence %s",
                evidence.pk,
            )
            commercial_comparison = unavailable_po_evidence_commercial_comparison(evidence)
        latest_po_import_payload = None
        if latest_po_import:
            latest_po_import_payload = dict(
                QuotationOutcomePOImportSerializer(
                    latest_po_import,
                    context={
                        "request": request,
                        "omit_commercial_comparison": True,
                    },
                ).data
            )
            # The authoritative comparison is already returned once at the
            # top level. The client combines it with this reload-safe import
            # metadata instead of downloading every line twice.
            latest_po_import_payload.pop("commercial_comparison", None)

        response = Response(
            {
                "id": evidence.id,
                "selected_source_kind": str(((evidence.match_signals or {}).get("source") or {}).get("kind") or ""),
                "extracted_text": extracted_text[:max_chars],
                "extracted_text_truncated": len(extracted_text) > max_chars,
                "email_body_text": email_body[:max_chars],
                "email_body_text_truncated": len(email_body) > max_chars,
                "commercial_comparison": commercial_comparison,
                "latest_po_import": latest_po_import_payload,
            }
        )
        response["Cache-Control"] = "private, no-store, max-age=0"
        response["Pragma"] = "no-cache"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    @action(detail=True, methods=["get"])
    def attachment(self, request, pk=None):
        evidence = self.get_object()
        requested_id = str(request.query_params.get("attachment_id") or "").strip()
        if not requested_id:
            return Response({"detail": "Choose an attachment to view."}, status=status.HTTP_400_BAD_REQUEST)

        manifest_entry = next(
            (
                attachment
                for attachment in (evidence.attachments or [])
                if isinstance(attachment, dict)
                and requested_id
                in {
                    str(attachment.get("attachment_id") or ""),
                    str(attachment.get("source_gmail_attachment_id") or ""),
                    str(attachment.get("part_id") or ""),
                }
            ),
            None,
        )
        if not manifest_entry:
            return Response(
                {"detail": "That attachment does not belong to this Gmail evidence record."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not evidence.gmail_message_id:
            return Response(
                {"detail": "This historical evidence record has no Gmail message to open."},
                status=status.HTTP_409_CONFLICT,
            )

        connection = resolve_gmail_connection(request.user, shared_only=True)
        if not connection or connection.status != GmailOAuthConnection.STATUS_CONNECTED:
            return Response(
                {"detail": "Reconnect the shared Gmail mailbox before opening source attachments."},
                status=status.HTTP_409_CONFLICT,
            )
        evidence_mailbox = str(
            evidence.mailbox_email
            or getattr(getattr(evidence, "gmail_connection", None), "email", "")
            or ""
        ).strip().lower()
        if evidence_mailbox and evidence_mailbox != str(connection.email or "").strip().lower():
            return Response(
                {"detail": "This evidence is not from the designated shared Gmail mailbox."},
                status=status.HTTP_404_NOT_FOUND,
            )

        attachment_id = str(manifest_entry.get("attachment_id") or "")
        part_id = str(manifest_entry.get("part_id") or "")
        if not attachment_id and str(manifest_entry.get("source_gmail_attachment_id") or "") == requested_id:
            # Older evidence stored a Gmail attachment id only under the
            # source-provenance key; some legacy rows used the MIME part id.
            attachment_id = requested_id
            if not part_id:
                part_id = requested_id
        max_bytes = int(getattr(settings, "PO_EVIDENCE_ATTACHMENT_VIEW_MAX_BYTES", 20 * 1024 * 1024))
        try:
            fetch_kwargs = {
                "attachment_id": attachment_id,
                "part_id": part_id,
                "max_bytes": max_bytes,
            }
            nested_filename = str(manifest_entry.get("nested_filename") or "").strip()
            if nested_filename:
                fetch_kwargs["nested_filename"] = nested_filename
            source = gmail_fetch_attachment_content(
                connection,
                evidence.gmail_message_id,
                **fetch_kwargs,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            logger.exception("Could not fetch Gmail attachment for PO evidence %s", evidence.pk)
            return Response(
                {"detail": f"Could not open the Gmail attachment. {str(exc)[:250]}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        filename = Path(str(source.get("filename") or "gmail-attachment")).name
        filename = re.sub(r"[\r\n\x00-\x1f\x7f\"\\]+", "_", filename)[:240] or "gmail-attachment"
        mime_type = str(source.get("mime_type") or "application/octet-stream").split(";", 1)[0].strip().lower()
        inline_types = {
            "application/pdf",
            "image/gif",
            "image/jpeg",
            "image/png",
            "image/webp",
            "text/plain",
        }
        disposition = "inline" if mime_type in inline_types else "attachment"
        response = HttpResponse(source["content"], content_type=mime_type)
        response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
        response["Content-Length"] = str(source.get("size") or len(source["content"]))
        response["Cache-Control"] = "private, no-store, max-age=0"
        response["Pragma"] = "no-cache"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src data: blob:"
        return response


class QuotationViewSet(QuotationBaseViewSet, viewsets.ModelViewSet):
    serializer_class = QuotationSerializer
    queryset = Quotation.objects.select_related(
        "company", "contact", "inquiry", "created_by", "finalized_by", "parent"
    )

    def get_serializer_class(self):
        if self.action == "list":
            return QuotationListSerializer
        return QuotationSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == "list":
            connection = resolve_gmail_connection(self.request.user, shared_only=True)
            evidence_scope = gmail_connection_lineage_q(connection, prefix="po_evidence")
            queryset = queryset.annotate(
                po_evidence_count=Count("po_evidence", filter=evidence_scope, distinct=True),
                po_evidence_candidate_count=Count(
                    "po_evidence",
                    filter=evidence_scope
                    & Q(po_evidence__status__in=["candidate", "parsed"]),
                    distinct=True,
                ),
                po_evidence_ambiguous_count=Count(
                    "po_evidence",
                    filter=evidence_scope & Q(po_evidence__status="ambiguous"),
                    distinct=True,
                ),
                po_evidence_parsed_count=Count(
                    "po_evidence",
                    filter=evidence_scope & Q(po_evidence__status="parsed"),
                    distinct=True,
                ),
            )
        if self.action != "list":
            queryset = queryset.prefetch_related(
                "lines",
                "lines__quote_item",
                "lines__product",
                "lines__product__images",
            )
        if self.request.query_params.get("include_historical") != "true":
            queryset = queryset.filter(is_historical_import=False)
        company_id = self.request.query_params.get("company")
        status_filter = self.request.query_params.get("status")
        search = self.request.query_params.get("search", "").strip()
        if company_id:
            queryset = queryset.filter(company_id=company_id)
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if search:
            queryset = queryset.filter(
                Q(quotation_number__icontains=search)
                | Q(company__name__icontains=search)
                | Q(inquiry__subject__icontains=search)
            )
        return queryset.order_by("-updated_at", "-id")

    def perform_create(self, serializer):
        quotation = serializer.save()
        audit_log(self.request.user, QuotationAuditLog.ACTION_CREATED, quotation, message="Created quotation.")

    def perform_update(self, serializer):
        ensure_quotation_editable(serializer.instance)
        quotation = serializer.save()
        recalculate_quotation_totals(quotation)
        audit_log(self.request.user, QuotationAuditLog.ACTION_UPDATED, quotation, message="Updated quotation.")

    def update(self, request, *args, **kwargs):
        try:
            return super().update(request, *args, **kwargs)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)

    def partial_update(self, request, *args, **kwargs):
        try:
            return super().partial_update(request, *args, **kwargs)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)

    def destroy(self, request, *args, **kwargs):
        quotation = self.get_object()
        if quotation.lpos.filter(status=QuotationLPO.STATUS_CONFIRMED).exists():
            return Response(
                {
                    "detail": (
                        "This quotation has a confirmed LPO and cannot be deleted because that would "
                        "remove accepted-price and order history. Correct the quotation or LPO instead."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )
        try:
            ensure_quotation_editable(quotation)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        with transaction.atomic():
            snapshot = build_quotation_delete_snapshot(quotation)
            inquiry = quotation.inquiry
            should_reset_inquiry = (
                inquiry is not None
                and inquiry.status == Inquiry.STATUS_QUOTED
                and not inquiry.quotations.exclude(pk=quotation.pk).exists()
            )
            audit_log(
                request.user,
                QuotationAuditLog.ACTION_DELETED,
                quotation,
                message="Deleted draft quotation with snapshot.",
                changes={"snapshot": snapshot},
            )
            quotation.delete()
            if should_reset_inquiry:
                inquiry.status = Inquiry.STATUS_DRAFT
                inquiry.save(update_fields=["status", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def submit_review(self, request, pk=None):
        return self._transition(request, Quotation.STATUS_PENDING_REVIEW)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        return self._transition(request, Quotation.STATUS_APPROVED)

    @action(detail=True, methods=["post"])
    def mark_sent(self, request, pk=None):
        return self._transition(request, Quotation.STATUS_SENT)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return self._transition(request, Quotation.STATUS_CANCELLED)

    def _transition(self, request, target_status):
        quotation = self.get_object()
        try:
            quotation = transition_quotation_status(quotation, request.user, target_status)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        serializer = self.get_serializer(quotation)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def finalize(self, request, pk=None):
        quotation = self.get_object()
        try:
            quotation = finalize_quotation(quotation, request.user)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        serializer = self.get_serializer(quotation)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def revise(self, request, pk=None):
        quotation = self.get_object()
        try:
            revision = revise_quotation(quotation, request.user)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        serializer = self.get_serializer(revision)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        quotation = self.get_object()
        pdf_bytes = build_quotation_pdf(quotation)
        audit_log(
            request.user,
            QuotationAuditLog.ACTION_PDF_DOWNLOADED,
            quotation,
            message=f"Downloaded PDF for {quotation.quotation_number}.",
        )
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{_quotation_download_filename(quotation, "pdf")}"'
        return response

    @action(detail=True, methods=["get"])
    def excel(self, request, pk=None):
        quotation = self.get_object()
        workbook_bytes = build_quotation_excel(quotation)
        audit_log(
            request.user,
            QuotationAuditLog.ACTION_PDF_DOWNLOADED,
            quotation,
            message=f"Downloaded Excel for {quotation.quotation_number}.",
        )
        response = HttpResponse(
            workbook_bytes,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{_quotation_download_filename(quotation, "xlsx")}"'
        return response

    @action(detail=True, methods=["get"])
    def product_price(self, request, pk=None):
        quotation = self.get_object()
        product_id = request.query_params.get("product")
        if not product_id:
            return Response({"detail": "Select a Product before requesting a price."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            product = Product.objects.get(pk=product_id)
        except (Product.DoesNotExist, ValueError):
            return Response({"detail": "Selected Product was not found."}, status=status.HTTP_404_NOT_FOUND)

        history_limit = _price_context_history_limit(request.query_params.get("history_limit"))
        return Response(_build_product_price_context(quotation, product, history_limit))

    @action(detail=True, methods=["get"])
    def product_prices(self, request, pk=None):
        quotation = self.get_object()
        raw_values = request.query_params.getlist("products")
        tokens = [token.strip() for raw in raw_values for token in raw.split(",") if token.strip()]
        product_ids = []
        seen = set()
        for token in tokens:
            product_id = _positive_pk(token)
            if not product_id:
                return Response(
                    {"detail": "Products must be a comma-separated list of positive IDs."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if product_id not in seen:
                product_ids.append(product_id)
                seen.add(product_id)

        if not product_ids:
            return Response(
                {"detail": "Select at least one Product before requesting prices."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(product_ids) > 100:
            return Response(
                {"detail": "Request price context for at most 100 Products at a time."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        products_by_id = Product.objects.in_bulk(product_ids)
        missing_product_ids = [product_id for product_id in product_ids if product_id not in products_by_id]
        if missing_product_ids:
            return Response(
                {
                    "detail": "One or more selected Products were not found.",
                    "missing_product_ids": missing_product_ids,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        history_limit = _price_context_history_limit(request.query_params.get("history_limit"))
        return Response(
            {
                "results": _build_product_price_contexts(
                    quotation,
                    products_by_id,
                    product_ids,
                    history_limit,
                )
            }
        )

    @action(detail=True, methods=["get", "patch"], parser_classes=[JSONParser])
    def outcome(self, request, pk=None):
        quotation = self.get_object()
        try:
            ensure_outcome_reviewable(quotation)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        if request.method.lower() == "patch":
            try:
                quotation = update_quotation_outcome(quotation, request.data or {}, request.user)
            except DjangoValidationError as exc:
                return self.handle_workflow_error(exc)
            except Exception as exc:
                logger.exception("Quotation outcome save failed for quote %s", quotation.pk)
                return Response(
                    {"detail": f"Save quotation outcome failed. {str(exc)[:250]}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            quotation.refresh_from_db()
        quotation = (
            Quotation.objects.select_related(
                "company",
                "contact",
                "inquiry",
                "created_by",
                "finalized_by",
                "outcome_closed_by",
                "outcome_last_updated_by",
            )
            .prefetch_related("lines", "lines__quote_item", "lines__product", "lines__product__images")
            .get(pk=quotation.pk)
        )
        serializer = self.get_serializer(quotation)
        evidence, evidence_pagination = _quotation_po_evidence_page(quotation, request)
        return Response(
            {
                "quotation": serializer.data,
                "summary": outcome_summary_for_quotation(quotation),
                "po_evidence": QuotationPOEvidenceSerializer(evidence, many=True, context={"request": request}).data,
                "po_evidence_pagination": evidence_pagination,
                "line_outcome_statuses": [
                    {"value": value, "label": label} for value, label in QuotationLine.OUTCOME_STATUS_CHOICES
                ],
                "outcome_statuses": [
                    {"value": value, "label": label} for value, label in Quotation.OUTCOME_STATUS_CHOICES
                ],
                "reasons": [
                    {"value": value, "label": label} for value, label in QuotationLine.OUTCOME_REASON_CHOICES
                ],
                "contact_methods": [
                    {"value": value, "label": label} for value, label in Quotation.FOLLOWUP_CONTACT_METHOD_CHOICES
                ],
                "follow_up_statuses": [
                    {"value": value, "label": label} for value, label in Quotation.FOLLOWUP_STATUS_CHOICES
                ],
            }
        )

    @action(detail=True, methods=["post"], parser_classes=[JSONParser, MultiPartParser, FormParser])
    def parse_outcome_po(self, request, pk=None):
        quotation = self.get_object()
        try:
            if quotation.status not in {Quotation.STATUS_FINALIZED, Quotation.STATUS_SENT}:
                raise DjangoValidationError("Only finalized or sent quotations can use PO outcome parsing.")
            uploaded = request.FILES.get("file")
            raw_text = request.data.get("text") or request.data.get("raw_text") or ""
            raw_html = request.data.get("html") or request.data.get("raw_html") or ""
            if uploaded:
                filename = (uploaded.name or "").lower()
                if filename.endswith((".png", ".jpg", ".jpeg", ".webp")):
                    return Response(
                        {
                            "detail": "Image PO parsing is not available in this environment yet. Upload PDF/Excel or paste PO text.",
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                preview = parse_file_preview(uploaded)
                source_type = QuotationOutcomePOImport.SOURCE_FILE
            elif raw_text or raw_html:
                preview = parse_text_preview(raw_text, raw_html=raw_html)
                source_type = QuotationOutcomePOImport.SOURCE_PASTED_TEXT
            else:
                return Response({"detail": "Upload a PO file or paste PO text."}, status=status.HTTP_400_BAD_REQUEST)

            deterministic_preview = preview
            warnings = list(preview.get("warnings") or [])
            use_ai = str(request.data.get("use_ai", "true")).lower() not in {"0", "false", "no"}
            if use_ai:
                try:
                    ai_preview = clean_preview_with_ai(
                        deterministic_preview,
                        actor=request.user,
                        requested_mode="auto",
                        allow_vision=True,
                    )
                    preview = prefer_safe_ai_preview(deterministic_preview, ai_preview)
                except AIParseError as exc:
                    warnings.append(str(exc))
            warnings = list(dict.fromkeys([*warnings, *(preview.get("warnings") or [])]))
            preview["warnings"] = warnings

            preview, suggestions, unmatched, missing_line_ids = build_guarded_po_outcome_suggestions(
                quotation,
                deterministic_preview,
                preview,
            )
            warnings = list(dict.fromkeys([*warnings, *(preview.get("warnings") or [])]))
            preview["warnings"] = warnings
            po_import = QuotationOutcomePOImport.objects.create(
                quotation=quotation,
                source_type=source_type,
                source_filename=preview.get("source_filename", ""),
                source_sha256=preview.get("source_sha256", ""),
                source_file_ref=preview.get("source_file_ref", ""),
                parse_method=preview.get("parse_method", ""),
                parsed_rows=preview.get("lines") or [],
                suggestions=suggestions,
                unmatched_po_rows=unmatched,
                missing_quote_line_ids=missing_line_ids,
                warnings=warnings,
                created_by=request.user if request.user.is_authenticated else None,
            )
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        serializer = QuotationOutcomePOImportSerializer(po_import, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], parser_classes=[JSONParser])
    def po_evidence(self, request, pk=None):
        quotation = self.get_object()
        try:
            ensure_outcome_reviewable(quotation)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        evidence, evidence_pagination = _quotation_po_evidence_page(quotation, request)
        serializer = QuotationPOEvidenceSerializer(evidence, many=True, context={"request": request})
        return Response(
            {
                "results": serializer.data,
                "count": len(serializer.data),
                "pagination": evidence_pagination,
            }
        )

    @action(detail=True, methods=["post"], parser_classes=[JSONParser])
    def find_po_evidence(self, request, pk=None):
        quotation = self.get_object()
        latest_audit = (
            MailboxPOAuditRun.objects.filter(
                gmail_connection=resolve_gmail_connection(request.user, shared_only=True),
                status=MailboxPOAuditRun.STATUS_COMPLETED,
                exhausted=True,
                match_runs__status=MailboxPOMatchRun.STATUS_COMPLETED,
                match_runs__algorithm_version=ALGORITHM_VERSION,
            )
            .distinct()
            .first()
        )
        if latest_audit:
            # Once the canonical mailbox audit exists, never fall back to the
            # old capped per-quotation Gmail search: it was the source of the
            # historical candidate explosion. Return the complete reconciled
            # evidence trail instead.
            connection = resolve_gmail_connection(request.user, shared_only=True)
            evidence_queryset = quotation.po_evidence.filter(
                gmail_connection_lineage_q(connection)
            )
            active_count = evidence_queryset.filter(
                status__in=[QuotationPOEvidence.STATUS_CANDIDATE, QuotationPOEvidence.STATUS_PARSED]
            ).count()
            ambiguous_count = evidence_queryset.filter(status=QuotationPOEvidence.STATUS_AMBIGUOUS).count()
            evidence, evidence_pagination = _quotation_po_evidence_page(quotation, request)
            serializer = QuotationPOEvidenceSerializer(evidence, many=True, context={"request": request})
            return Response(
                {
                    "count": active_count,
                    "ambiguous_count": ambiguous_count,
                    "evidence_count": len(serializer.data),
                    "scan_complete": True,
                    "incomplete_queries": [],
                    "scan_warning": "",
                    "queries": [latest_audit.gmail_query],
                    "results": serializer.data,
                    "pagination": evidence_pagination,
                    "mailbox_audit_run": latest_audit.id,
                }
            )
        try:
            result = find_quote_po_evidence(
                quotation,
                request.user,
                limit=request.data.get("limit", 25),
            )
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        except Exception as exc:
            logger.exception("Quotation PO evidence search failed for quote %s", quotation.pk)
            return Response(
                {"detail": f"Gmail PO evidence search failed. {str(exc)[:250]}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = QuotationPOEvidenceSerializer(result["evidence"], many=True, context={"request": request})
        return Response(
            {
                "count": result["count"],
                "ambiguous_count": result["ambiguous_count"],
                "evidence_count": result["evidence_count"],
                "scan_complete": result["scan_complete"],
                "incomplete_queries": result["incomplete_queries"],
                "scan_warning": result["scan_warning"],
                "queries": result["queries"],
                "results": serializer.data,
            }
        )

    @action(detail=False, methods=["post"], parser_classes=[JSONParser])
    def scan_po_evidence(self, request):
        try:
            result = scan_quote_po_evidence_batch(
                request.user,
                quote_limit=request.data.get("quote_limit", 5),
                message_limit=request.data.get("message_limit", 10),
                rescan=str(request.data.get("rescan", "false")).lower() in {"1", "true", "yes"},
                rescan_before=request.data.get("rescan_before"),
            )
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        except Exception as exc:
            logger.exception("Batch quotation PO evidence scan failed")
            return Response(
                {"detail": f"Batch PO/LPO scan failed. {str(exc)[:250]}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(result)

    @action(detail=True, methods=["post"], parser_classes=[JSONParser])
    def parse_po_evidence(self, request, pk=None):
        quotation = self.get_object()
        evidence_id = request.data.get("evidence_id")
        try:
            connection = resolve_gmail_connection(request.user, shared_only=True)
            if not connection:
                raise quotation.po_evidence.model.DoesNotExist
            evidence = quotation.po_evidence.get(
                gmail_connection_lineage_q(connection),
                pk=evidence_id,
            )
            po_import = parse_quote_po_evidence(
                evidence,
                request.user,
                use_ai=str(request.data.get("use_ai", "true")).lower() not in {"0", "false", "no"},
                link_approved=str(request.data.get("approve_link", "false")).lower()
                in {"1", "true", "yes", "on"},
            )
        except quotation.po_evidence.model.DoesNotExist:
            return Response({"detail": "Select a valid Gmail evidence candidate."}, status=status.HTTP_400_BAD_REQUEST)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        except Exception as exc:
            logger.exception("Quotation PO evidence parsing failed for quote %s", quotation.pk)
            return Response(
                {"detail": f"Gmail PO evidence parsing failed. {str(exc)[:250]}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = QuotationOutcomePOImportSerializer(po_import, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], parser_classes=[JSONParser])
    def mark_po_evidence_not_relevant(self, request, pk=None):
        quotation = self.get_object()
        evidence_id = request.data.get("evidence_id")
        with transaction.atomic():
            try:
                # Serialize this decision against approval/parsing. Without a
                # row lock, a stale candidate instance could overwrite a
                # concurrently completed approval back to not_relevant.
                connection = resolve_gmail_connection(request.user, shared_only=True)
                if not connection:
                    raise quotation.po_evidence.model.DoesNotExist
                evidence = quotation.po_evidence.select_for_update().get(
                    gmail_connection_lineage_q(connection),
                    pk=evidence_id,
                )
            except quotation.po_evidence.model.DoesNotExist:
                return Response(
                    {"detail": "Select a valid Gmail evidence candidate."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if evidence.status == evidence.STATUS_PARSED or evidence.link_approved_at:
                return Response(
                    {"detail": "Approved or parsed Gmail evidence cannot be marked not relevant."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            evidence.status = evidence.STATUS_NOT_RELEVANT
            evidence.error = ""
            evidence.save(update_fields=["status", "error", "updated_at"])
        serializer = QuotationPOEvidenceSerializer(evidence, context={"request": request})
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def lpos(self, request, pk=None):
        quotation = self.get_object()
        lpos = quotation.lpos.select_related("received_by").order_by("-received_at", "-id")
        serializer = QuotationLPOSerializer(lpos, many=True, context={"request": request})
        return Response(serializer.data)

    @action(detail=True, methods=["post"], parser_classes=[JSONParser, MultiPartParser, FormParser])
    def upload_lpo(self, request, pk=None):
        quotation = self.get_object()
        if quotation.status not in {Quotation.STATUS_APPROVED, Quotation.STATUS_FINALIZED, Quotation.STATUS_SENT}:
            return Response(
                {"detail": "Approve or finalize this quotation before recording an LPO."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            uploaded = request.FILES.get("file")
            raw_text = request.data.get("text") or request.data.get("raw_text") or ""
            raw_html = request.data.get("html") or request.data.get("raw_html") or ""
            source_file_size = 0
            if uploaded:
                filename = (uploaded.name or "").lower()
                if filename.endswith((".png", ".jpg", ".jpeg", ".webp")):
                    return Response(
                        {
                            "detail": "Image LPO parsing is not available here yet. Upload PDF/Excel or paste the LPO text.",
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                source_file_size = int(getattr(uploaded, "size", 0) or 0)
                preview = parse_file_preview(uploaded)
                source_type = QuotationLPO.SOURCE_FILE
            elif raw_text or raw_html:
                preview = parse_text_preview(raw_text, raw_html=raw_html)
                source_file_size = len(str(raw_text or raw_html).encode("utf-8"))
                source_type = QuotationLPO.SOURCE_PASTED_TEXT
            else:
                return Response({"detail": "Upload an LPO file or paste LPO text."}, status=status.HTTP_400_BAD_REQUEST)

            source_context = {
                "original_text": preview.get("original_text") or "",
                "source_filename": preview.get("source_filename") or "",
                "source_sha256": preview.get("source_sha256") or "",
                "source_file_ref": preview.get("source_file_ref") or "",
            }
            deterministic_preview = preview
            warnings = list(preview.get("warnings") or [])
            use_ai = str(request.data.get("use_ai", "true")).lower() not in {"0", "false", "no"}
            if use_ai:
                try:
                    ai_preview = clean_preview_with_ai(
                        deterministic_preview,
                        actor=request.user,
                        requested_mode="auto",
                        allow_vision=True,
                    )
                    preview = prefer_safe_ai_preview(deterministic_preview, ai_preview)
                except AIParseError as exc:
                    warnings.append(str(exc))
            warnings = list(dict.fromkeys([*warnings, *(preview.get("warnings") or [])]))
            preview["warnings"] = warnings
            for key, value in source_context.items():
                if value and not preview.get(key):
                    preview[key] = value

            preview, suggestions, unmatched, missing_line_ids = build_guarded_po_outcome_suggestions(
                quotation,
                deterministic_preview,
                preview,
            )
            warnings = list(dict.fromkeys([*warnings, *(preview.get("warnings") or [])]))
            preview["warnings"] = warnings

            details = _extract_lpo_details(preview)
            if not details["lpo_number"]:
                warnings.append("LPO number was not detected. Enter it manually if the customer provided one.")
            if not details["lpo_date"]:
                warnings.append("LPO date was not detected. Enter it manually if needed.")

            parsed_rows = preview.get("lines") or []
            lpo_status = QuotationLPO.STATUS_PARSED if parsed_rows or details["lpo_number"] else QuotationLPO.STATUS_NEEDS_REVIEW
            lpo = QuotationLPO.objects.create(
                quotation=quotation,
                source_type=source_type,
                source_filename=preview.get("source_filename", ""),
                source_sha256=preview.get("source_sha256", ""),
                source_file_ref=preview.get("source_file_ref", ""),
                source_file_size=source_file_size,
                parse_method=preview.get("parse_method", ""),
                lpo_number=details["lpo_number"],
                lpo_date=details["lpo_date"],
                status=lpo_status,
                parsed_meta={
                    **details["parsed_meta"],
                    "outcome_suggestions": suggestions,
                },
                parsed_rows=parsed_rows,
                warnings=warnings,
                received_by=request.user if request.user.is_authenticated else None,
            )
            audit_log(
                request.user,
                QuotationAuditLog.ACTION_LPO_UPLOADED,
                lpo,
                message=f"Recorded LPO for {quotation.quotation_number}.",
                changes={
                    "quotation": quotation.quotation_number,
                    "lpo_number": lpo.lpo_number,
                    "lpo_date": lpo.lpo_date.isoformat() if lpo.lpo_date else "",
                    "source_filename": lpo.source_filename,
                },
            )
        except (DjangoValidationError, ValueError) as exc:
            return self.handle_workflow_error(exc)

        serializer = QuotationLPOSerializer(lpo, context={"request": request})
        return Response(
            {
                "lpo": serializer.data,
                "outcome_suggestions": suggestions,
                "unmatched_lpo_rows": unmatched,
                "missing_quote_line_ids": missing_line_ids,
                "message": "LPO recorded. Review the detected details, then download the Proforma Tax Invoice.",
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get"])
    def proforma_pdf(self, request, pk=None):
        quotation = self.get_object()
        if quotation.status not in {Quotation.STATUS_APPROVED, Quotation.STATUS_FINALIZED, Quotation.STATUS_SENT}:
            return Response(
                {"detail": "Approve or finalize this quotation before downloading a Proforma Tax Invoice."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        lpo_id = request.query_params.get("lpo")
        lpos = quotation.lpos.select_related("received_by").order_by("-received_at", "-id")
        lpo = None
        if lpo_id:
            try:
                lpo = lpos.get(pk=lpo_id)
            except (QuotationLPO.DoesNotExist, ValueError):
                return Response({"detail": "Selected LPO was not found for this quotation."}, status=status.HTTP_404_NOT_FOUND)
        else:
            lpo = lpos.first()
        if not lpo:
            return Response(
                {"detail": "Record the customer's LPO before downloading a Proforma Tax Invoice."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        pdf_bytes = build_proforma_invoice_pdf(quotation, lpo=lpo)
        audit_log(
            request.user,
            QuotationAuditLog.ACTION_PROFORMA_DOWNLOADED,
            quotation,
            message=f"Downloaded Proforma Tax Invoice for {quotation.quotation_number}.",
            changes={"lpo_id": lpo.id, "lpo_number": lpo.lpo_number},
        )
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{_proforma_download_filename(quotation)}"'
        return response

    @action(detail=True, methods=["post"])
    def bulk_update_lines(self, request, pk=None):
        quotation = self.get_object()
        try:
            quotation, updated_lines = bulk_update_quotation_lines(
                quotation,
                request.data.get("lines") or [],
                request.user,
            )
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        quotation.refresh_from_db()
        serializer = self.get_serializer(quotation)
        return Response(
            {
                "quotation": serializer.data,
                "updated_line_ids": [line.id for line in updated_lines],
                "message": f"Saved {len(updated_lines)} line(s).",
            }
        )

    @action(detail=True, methods=["post"])
    def bulk_create_products_for_lines(self, request, pk=None):
        quotation = self.get_object()
        try:
            line_ids = _request_int_list(request.data, "line_ids")
            summary = bulk_create_products_from_quotation_lines(
                quotation,
                line_ids,
                request.user,
                names_by_id=request.data.get("names") or {},
                confirm_create_line_ids=_request_int_list(request.data, "confirm_create_line_ids"),
            )
        except Exception as exc:
            return self.handle_safe_workflow_exception(exc, "Create Products from quote lines failed. Check selected line IDs and Product names.")
        line_serializer = QuotationLineSerializer(summary["updated_lines"], many=True, context={"request": request})
        return Response(
            {
                "updated_lines": line_serializer.data,
                "created_products": summary["created_products"],
                "reused_products": summary["reused_products"],
                "unique_products": summary["unique_products"],
                "confirmation_required": summary["confirmation_required"],
                "resolutions": summary["resolutions"],
                "message": (
                    f"Linked {len(summary['updated_lines'])} line(s) to "
                    f"{summary['unique_products']} Product(s)."
                    + (
                        f" {len(summary['confirmation_required'])} line(s) need confirmation before creating a new Product."
                        if summary["confirmation_required"]
                        else ""
                    )
                ),
            }
        )


class QuotationLPOViewSet(QuotationBaseViewSet, viewsets.ModelViewSet):
    serializer_class = QuotationLPOSerializer
    queryset = QuotationLPO.objects.select_related("quotation", "quotation__company", "received_by")
    http_method_names = ["get", "put", "patch", "delete", "head", "options"]

    @staticmethod
    def _editable_audit_snapshot(lpo):
        snapshot = {
            "lpo_number": lpo.lpo_number,
            "lpo_date": lpo.lpo_date.isoformat() if lpo.lpo_date else "",
            "status": lpo.status,
            "notes": lpo.notes,
        }
        if "applied_outcome_line_ids" in (lpo.parsed_meta or {}):
            snapshot["applied_outcome_line_ids"] = sorted(
                _po_suggestion_line_ids(
                    [
                        {"quotation_line_id": value}
                        for value in (
                            (lpo.parsed_meta or {}).get("applied_outcome_line_ids") or []
                        )
                    ]
                )
            )
        return snapshot

    def get_queryset(self):
        queryset = super().get_queryset()
        quotation_id = self.request.query_params.get("quotation")
        if quotation_id:
            queryset = queryset.filter(quotation_id=quotation_id)
        return queryset.order_by("-received_at", "-id")

    def perform_update(self, serializer):
        before = self._editable_audit_snapshot(serializer.instance)
        lpo = serializer.save()
        after = self._editable_audit_snapshot(lpo)
        audit_log(
            self.request.user,
            QuotationAuditLog.ACTION_UPDATED,
            lpo,
            message=f"Updated LPO details for {lpo.quotation.quotation_number}.",
            changes={
                "before": before,
                "after": after,
                "changed_fields": [field for field in before if before[field] != after[field]],
            },
        )

    def destroy(self, request, *args, **kwargs):
        lpo = self.get_object()
        if lpo.status == QuotationLPO.STATUS_CONFIRMED:
            return Response(
                {
                    "detail": (
                        "Confirmed LPO records cannot be deleted because they are part of the quotation history. "
                        "Correct the reference details instead."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )
        audit_log(
            request.user,
            QuotationAuditLog.ACTION_DELETED,
            lpo,
            message=f"Deleted LPO record for {lpo.quotation.quotation_number}.",
            changes={"lpo_number": lpo.lpo_number, "source_filename": lpo.source_filename},
        )
        return super().destroy(request, *args, **kwargs)


class ProformaInvoiceViewSet(QuotationBaseViewSet, viewsets.ModelViewSet):
    serializer_class = ProformaInvoiceSerializer
    queryset = ProformaInvoice.objects.select_related(
        "company", "contact", "quotation", "created_by", "issued_by"
    ).prefetch_related("lines", "lines__product")

    def get_queryset(self):
        queryset = super().get_queryset()
        company_id = self.request.query_params.get("company")
        status_filter = self.request.query_params.get("status")
        search = self.request.query_params.get("search", "").strip()
        if company_id:
            queryset = queryset.filter(company_id=company_id)
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if search:
            queryset = queryset.filter(
                Q(proforma_number__icontains=search)
                | Q(company__name__icontains=search)
                | Q(quotation__quotation_number__icontains=search)
                | Q(lpo_number__icontains=search)
                | Q(source_filename__icontains=search)
            )
        return queryset.order_by("-updated_at", "-id")

    def perform_create(self, serializer):
        proforma = serializer.save()
        audit_log(
            self.request.user,
            QuotationAuditLog.ACTION_CREATED,
            proforma,
            company=proforma.company,
            quotation=proforma.quotation,
            message=f"Created Proforma Tax Invoice {proforma.proforma_number}.",
        )

    def perform_update(self, serializer):
        proforma = serializer.save()
        audit_log(
            self.request.user,
            QuotationAuditLog.ACTION_UPDATED,
            proforma,
            company=proforma.company,
            quotation=proforma.quotation,
            message=f"Updated Proforma Tax Invoice {proforma.proforma_number}.",
        )

    def destroy(self, request, *args, **kwargs):
        proforma = self.get_object()
        if proforma.status != ProformaInvoice.STATUS_DRAFT:
            return Response(
                {"detail": "Only draft Proforma Tax Invoices can be deleted. Issued documents are kept for audit history."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        proforma_number = proforma.proforma_number
        company = proforma.company
        quotation = proforma.quotation
        response = super().destroy(request, *args, **kwargs)
        audit_log(
            request.user,
            QuotationAuditLog.ACTION_DELETED,
            None,
            company=company,
            quotation=quotation,
            message=f"Deleted draft Proforma Tax Invoice {proforma_number}.",
            changes={"proforma": proforma_number},
        )
        return response

    @action(detail=True, methods=["post"], parser_classes=[JSONParser, MultiPartParser, FormParser])
    def upload_lpo(self, request, pk=None):
        proforma = self.get_object()
        try:
            uploaded = request.FILES.get("file")
            raw_text = request.data.get("text") or request.data.get("raw_text") or ""
            raw_html = request.data.get("html") or request.data.get("raw_html") or ""
            source_file_size = 0
            if uploaded:
                filename = (uploaded.name or "").lower()
                if filename.endswith((".png", ".jpg", ".jpeg", ".webp")):
                    return Response(
                        {"detail": "Image LPO parsing is not available here yet. Upload PDF/Excel or paste the LPO text."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                source_file_size = int(getattr(uploaded, "size", 0) or 0)
                preview = parse_file_preview(uploaded)
                source_type = ProformaInvoice.SOURCE_FILE
            elif raw_text or raw_html:
                preview = parse_text_preview(raw_text, raw_html=raw_html)
                source_file_size = len(str(raw_text or raw_html).encode("utf-8"))
                source_type = ProformaInvoice.SOURCE_PASTED_TEXT
            else:
                return Response({"detail": "Upload an LPO file or paste LPO text."}, status=status.HTTP_400_BAD_REQUEST)

            source_context = {
                "original_text": preview.get("original_text") or "",
                "source_filename": preview.get("source_filename") or "",
                "source_sha256": preview.get("source_sha256") or "",
                "source_file_ref": preview.get("source_file_ref") or "",
            }
            warnings = list(preview.get("warnings") or [])
            use_ai = str(request.data.get("use_ai", "true")).lower() not in {"0", "false", "no"}
            if use_ai:
                try:
                    deterministic_preview = preview
                    ai_preview = clean_preview_with_ai(
                        deterministic_preview,
                        actor=request.user,
                        requested_mode="auto",
                        allow_vision=True,
                    )
                    preview = prefer_safe_ai_preview(deterministic_preview, ai_preview)
                except AIParseError as exc:
                    warnings.append(str(exc))
            warnings = list(dict.fromkeys([*warnings, *(preview.get("warnings") or [])]))
            preview["warnings"] = warnings
            for key, value in source_context.items():
                if value and not preview.get(key):
                    preview[key] = value

            details = _extract_lpo_details(preview)
            if not details["lpo_number"]:
                warnings.append("LPO number was not detected. Enter it manually if the customer provided one.")
            if not details["lpo_date"]:
                warnings.append("LPO date was not detected. Enter it manually if needed.")

            parsed_rows = preview.get("lines") or []
            with transaction.atomic():
                proforma.source_type = source_type
                proforma.source_filename = preview.get("source_filename", "")
                proforma.source_sha256 = preview.get("source_sha256", "")
                proforma.source_file_ref = preview.get("source_file_ref", "")
                proforma.source_file_size = source_file_size
                proforma.parse_method = preview.get("parse_method", "")
                proforma.lpo_number = details["lpo_number"]
                proforma.lpo_date = details["lpo_date"]
                proforma.parsed_meta = details["parsed_meta"]
                proforma.parsed_rows = parsed_rows
                proforma.warnings = warnings
                proforma.save(
                    update_fields=[
                        "source_type",
                        "source_filename",
                        "source_sha256",
                        "source_file_ref",
                        "source_file_size",
                        "parse_method",
                        "lpo_number",
                        "lpo_date",
                        "parsed_meta",
                        "parsed_rows",
                        "warnings",
                        "updated_at",
                    ]
                )
                proforma.lines.all().delete()
                created_lines = []
                for index, row in enumerate(parsed_rows, start=1):
                    line_data = _proforma_line_from_preview(row, index)
                    if not line_data:
                        continue
                    created_lines.append(ProformaInvoiceLine.objects.create(proforma=proforma, **line_data))
                _recalculate_proforma_totals(proforma)
                proforma.refresh_from_db()

            audit_log(
                request.user,
                QuotationAuditLog.ACTION_LPO_UPLOADED,
                proforma,
                company=proforma.company,
                quotation=proforma.quotation,
                message=f"Parsed LPO for Proforma Tax Invoice {proforma.proforma_number}.",
                changes={
                    "proforma": proforma.proforma_number,
                    "lpo_number": proforma.lpo_number,
                    "lpo_date": proforma.lpo_date.isoformat() if proforma.lpo_date else "",
                    "source_filename": proforma.source_filename,
                    "line_count": len(created_lines),
                },
            )
        except (DjangoValidationError, ValueError) as exc:
            return self.handle_workflow_error(exc)

        serializer = self.get_serializer(proforma)
        return Response(
            {
                "proforma": serializer.data,
                "message": f"LPO parsed. {proforma.lines.count()} line(s) are ready for review.",
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], parser_classes=[JSONParser])
    def bulk_update_lines(self, request, pk=None):
        proforma = self.get_object()
        payload = request.data.get("lines") or []
        if not isinstance(payload, list):
            return Response({"detail": "Send a list of lines to update."}, status=status.HTTP_400_BAD_REQUEST)
        updated = []
        created = []
        deleted = 0
        line_map = {line.id: line for line in proforma.lines.all()}
        try:
            with transaction.atomic():
                for line_data in payload:
                    line_id = line_data.get("id")
                    line = None
                    if line_id:
                        try:
                            line = line_map.get(int(line_id))
                        except (TypeError, ValueError):
                            line = None
                    if line and line_data.get("_delete"):
                        line.delete()
                        deleted += 1
                        continue
                    if not line and line_data.get("_delete"):
                        continue
                    if line:
                        serializer = ProformaInvoiceLineSerializer(line, data=line_data, partial=True, context={"request": request})
                        serializer.is_valid(raise_exception=True)
                        updated.append(serializer.save())
                    else:
                        serializer = ProformaInvoiceLineSerializer(data=line_data, context={"request": request})
                        serializer.is_valid(raise_exception=True)
                        created.append(serializer.save(proforma=proforma))
                _recalculate_proforma_totals(proforma)
        except (DjangoValidationError, ValueError) as exc:
            return self.handle_workflow_error(exc)

        audit_log(
            request.user,
            QuotationAuditLog.ACTION_UPDATED,
            proforma,
            company=proforma.company,
            quotation=proforma.quotation,
            message=f"Saved Proforma Tax Invoice lines: {len(updated)} updated, {len(created)} added, {deleted} removed.",
            changes={"updated": len(updated), "created": len(created), "deleted": deleted},
        )
        proforma.refresh_from_db()
        return Response(self.get_serializer(proforma).data)

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        proforma = self.get_object()
        if not proforma.lines.exists():
            return Response({"detail": "Add or parse at least one line before downloading the Proforma Tax Invoice."}, status=status.HTTP_400_BAD_REQUEST)
        pdf_bytes = build_standalone_proforma_invoice_pdf(proforma)
        if proforma.status == ProformaInvoice.STATUS_DRAFT:
            proforma.status = ProformaInvoice.STATUS_ISSUED
            proforma.issued_by = request.user if request.user.is_authenticated else None
            proforma.issued_at = timezone.now()
            proforma.save(update_fields=["status", "issued_by", "issued_at", "updated_at"])
        audit_log(
            request.user,
            QuotationAuditLog.ACTION_PROFORMA_DOWNLOADED,
            proforma,
            company=proforma.company,
            quotation=proforma.quotation,
            message=f"Downloaded standalone Proforma Tax Invoice {proforma.proforma_number}.",
            changes={"proforma": proforma.proforma_number, "lpo_number": proforma.lpo_number},
        )
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{_standalone_proforma_download_filename(proforma)}"'
        return response


class QuotationLineViewSet(QuotationBaseViewSet, viewsets.ModelViewSet):
    serializer_class = QuotationLineSerializer
    queryset = QuotationLine.objects.select_related("quotation", "quotation__company", "quote_item", "product", "product_image", "inquiry_line")

    def get_queryset(self):
        queryset = super().get_queryset()
        quotation_id = self.request.query_params.get("quotation")
        if quotation_id:
            queryset = queryset.filter(quotation_id=quotation_id)
        return queryset

    def perform_create(self, serializer):
        with transaction.atomic():
            quotation = (
                _quotations_for_update()
                .select_related("company")
                .get(pk=serializer.validated_data["quotation"].pk)
            )
            ensure_quotation_editable(quotation)
            line = serializer.save(quotation=quotation)
            source_wording = quotation_line_source_wording(line)
            if source_wording and line.item_name_snapshot != source_wording:
                line.item_name_snapshot = source_wording
                line.save(update_fields=["item_name_snapshot", "updated_at"])
            learn_confirmed_quotation_line_alias(
                line,
                self.request.user,
                explicit_confirmation=True,
            )
            recalculate_quotation_totals(quotation)
            audit_log(self.request.user, QuotationAuditLog.ACTION_CREATED, line, message="Created quotation line.")

    def perform_update(self, serializer):
        with transaction.atomic():
            quotation = _quotations_for_update().get(pk=serializer.instance.quotation_id)
            ensure_quotation_editable(quotation)
            line = (
                _quotation_lines_for_update()
                .select_related("quotation__company", "inquiry_line", "product")
                .get(pk=serializer.instance.pk, quotation=quotation)
            )
            serializer.instance = line
            next_inquiry_line = serializer.validated_data.get("inquiry_line", line.inquiry_line)
            source_wording = (
                next_inquiry_line.raw_name
                if next_inquiry_line and str(next_inquiry_line.raw_name or "").strip()
                else line.item_name_snapshot
            )
            next_product = serializer.validated_data.get("product", line.product)
            product_changed = (
                "product" in serializer.validated_data
                and getattr(next_product, "pk", None) != line.product_id
            )
            inquiry_changed = (
                "inquiry_line" in serializer.validated_data
                and getattr(next_inquiry_line, "pk", None) != line.inquiry_line_id
            )
            snapshot_changed = (
                "item_name_snapshot" in serializer.validated_data
                and str(serializer.validated_data.get("item_name_snapshot") or "").strip()
                != str(line.item_name_snapshot or "").strip()
            )
            match_status_changed = (
                "match_status" in serializer.validated_data
                and str(serializer.validated_data.get("match_status") or "")
                != str(line.match_status or "")
            )
            match_fields_changed = product_changed or inquiry_changed or snapshot_changed or match_status_changed
            restore_source_snapshot = bool(
                source_wording
                and next_product
                and (next_inquiry_line or product_changed or inquiry_changed)
            )
            if restore_source_snapshot:
                serializer.validated_data["item_name_snapshot"] = source_wording
            line = serializer.save()
            if restore_source_snapshot and line.item_name_snapshot != source_wording:
                line.item_name_snapshot = source_wording
                line.save(update_fields=["item_name_snapshot", "updated_at"])
            if match_fields_changed:
                learn_confirmed_quotation_line_alias(
                    line,
                    self.request.user,
                    source_wording=source_wording if restore_source_snapshot else "",
                    explicit_confirmation=True,
                )
            recalculate_quotation_totals(line.quotation)
            audit_log(self.request.user, QuotationAuditLog.ACTION_UPDATED, line, message="Updated quotation line.")

    def create(self, request, *args, **kwargs):
        try:
            return super().create(request, *args, **kwargs)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)

    def update(self, request, *args, **kwargs):
        try:
            return super().update(request, *args, **kwargs)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)

    def partial_update(self, request, *args, **kwargs):
        try:
            return super().partial_update(request, *args, **kwargs)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)

    def destroy(self, request, *args, **kwargs):
        line = self.get_object()
        try:
            ensure_quotation_editable(line.quotation)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        quotation = line.quotation
        audit_log(request.user, QuotationAuditLog.ACTION_DELETED, line, message="Deleted quotation line.")
        response = super().destroy(request, *args, **kwargs)
        recalculate_quotation_totals(quotation)
        return response

    @action(detail=True, methods=["post"])
    def remember_alias(self, request, pk=None):
        try:
            alias = remember_quotation_line_alias(self.get_object(), request.user)
        except (DjangoValidationError, ValueError) as exc:
            return self.handle_workflow_error(exc)
        return Response(ProductAliasSerializer(alias, context={"request": request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def create_product(self, request, pk=None):
        try:
            line, resolution = create_product_from_quotation_line(
                self.get_object(),
                request.user,
                product_name=request.data.get("product_name") or "",
                confirm_create=str(request.data.get("confirm_create") or "").lower() in {"1", "true", "yes", "on"},
            )
        except Exception as exc:
            return self.handle_safe_workflow_exception(exc, "Create Product from quote line failed. Check the Product name and line status.")
        if resolution.requires_confirmation:
            return Response(
                {
                    "detail": resolution.warning,
                    **resolution.as_dict(),
                    "line": QuotationLineSerializer(line, context={"request": request}).data,
                },
                status=status.HTTP_409_CONFLICT,
            )
        product = resolution.product
        created = resolution.created
        return Response(
            {
                "line": QuotationLineSerializer(line, context={"request": request}).data,
                "product": QuoteItemSerializer(product, context={"request": request}).data,
                "created": created,
                **resolution.as_dict(),
                "message": (
                    f"Created draft/internal Product '{product.name}' and linked the row."
                    if created
                    else f"Linked the row to existing Product '{product.name}'."
                ),
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], parser_classes=[MultiPartParser, FormParser])
    def upload_product_image(self, request, pk=None):
        line = self.get_object()
        try:
            ensure_quotation_editable(line.quotation)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        if not line.product_id:
            return Response({"detail": "Match this line to a Product before uploading an item image."}, status=status.HTTP_400_BAD_REQUEST)
        image_file = request.FILES.get("image")
        if not image_file:
            return Response({"detail": "Choose an image file."}, status=status.HTTP_400_BAD_REQUEST)
        has_primary = ProductImage.objects.filter(product=line.product, is_primary=True).exists()
        product_image = ProductImage.objects.create(
            product=line.product,
            image=image_file,
            alt_text=line.item_name_snapshot or line.product.name,
            is_primary=not has_primary,
            display_order=ProductImage.objects.filter(product=line.product).count(),
            source_type="manual_upload",
        )
        line.product_image = product_image
        line.include_product_image = True
        line.save(update_fields=["product_image", "include_product_image", "updated_at"])
        audit_log(request.user, QuotationAuditLog.ACTION_UPDATED, line, message="Uploaded Product image from quotation line.")
        return Response(
            {
                "line": QuotationLineSerializer(line, context={"request": request}).data,
                "image": {
                    "id": product_image.id,
                    "image_url": request.build_absolute_uri(product_image.image.url),
                    "is_primary": product_image.is_primary,
                },
                "message": "Image saved to the Product and enabled for this quotation line.",
            },
            status=status.HTTP_201_CREATED,
        )


def _request_int_list(data, key):
    values = data.get(key, [])
    if values in (None, ""):
        return []
    if not isinstance(values, list):
        raise DjangoValidationError(f"{key} must be a list.")
    try:
        return [int(value) for value in values if value not in (None, "")]
    except (TypeError, ValueError) as exc:
        raise DjangoValidationError(f"{key} must contain only ids.") from exc


def _serialized_ai_suggestions_for_results(batch, results, request):
    suggestion_ids = [result.get("suggestion_id") for result in results if result.get("suggestion_id")]
    if not suggestion_ids:
        return []
    suggestions = (
        HistoricalImportAISuggestion.objects.select_related(
            "batch",
            "historical_import",
            "historical_import__company",
            "line",
            "suggested_company",
            "suggested_product",
            "created_by",
            "applied_by",
        )
        .filter(batch=batch, id__in=suggestion_ids)
        .order_by("historical_import_id", "line__sort_order", "id")
    )
    return HistoricalImportAISuggestionSerializer(suggestions, many=True, context={"request": request}).data


class HistoricalImportBatchViewSet(QuotationBaseViewSet, viewsets.ModelViewSet):
    serializer_class = HistoricalImportBatchSerializer
    queryset = HistoricalImportBatch.objects.select_related("created_by").prefetch_related(
        "imports",
        "imports__company",
        "imports__created_by",
        "imports__committed_by",
        "imports__created_quotation",
        "imports__lines",
        "imports__lines__product",
        "imports__lines__quote_item",
        "ai_suggestions",
    )
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def retrieve(self, request, *args, **kwargs):
        batch = refresh_historical_import_batch_summary(self.get_object())
        return Response(self.get_serializer(batch).data)

    def perform_create(self, serializer):
        batch = serializer.save(created_by=self.request.user)
        audit_log(
            self.request.user,
            QuotationAuditLog.ACTION_CREATED,
            batch,
            message="Created historical import batch.",
        )

    @action(detail=True, methods=["post"], parser_classes=[MultiPartParser, FormParser])
    def upload_file(self, request, pk=None):
        batch = self.get_object()
        upload = request.FILES.get("file")
        try:
            preview = parse_historical_pdf_upload(upload)
            duplicate_check = find_historical_import_duplicates(preview)
            force_new_import = str(request.data.get("force_new_import", "")).lower() in {"1", "true", "yes"}
            if duplicate_check.get("is_duplicate"):
                preview.setdefault("meta", {})["duplicate_check"] = duplicate_check
                preview.setdefault("warnings", []).append(duplicate_check["message"])
            if duplicate_check.get("blocking") and not force_new_import:
                existing_import = HistoricalPriceImport.objects.get(pk=duplicate_check["primary_match"]["id"])
                append_batch_file_result(
                    batch,
                    {
                        "filename": preview.get("source_filename", ""),
                        "status": "duplicate",
                        "existing_import_id": existing_import.id,
                        "message": duplicate_check.get("message", ""),
                        "duplicate_type": duplicate_check.get("duplicate_type", ""),
                        "duplicate_match": duplicate_check.get("primary_match", {}),
                        "duplicate_matches": duplicate_check.get("matches", []),
                    },
                )
                data = HistoricalPriceImportSerializer(existing_import, context={"request": request}).data
                return Response(
                    {
                        "status": "duplicate",
                        "import": data,
                        "duplicate_check": {
                            **duplicate_check,
                            "blocked_new_import": True,
                        },
                        "batch": self.get_serializer(refresh_historical_import_batch_summary(batch)).data,
                    },
                    status=status.HTTP_200_OK,
                )
            historical_import = create_historical_price_import(preview, request.user, batch=batch)
            maybe_attach_auto_ai_candidate(preview, actor=request.user, allow_vision=True)
            append_batch_file_result(
                batch,
                {
                    "filename": historical_import.source_filename,
                    "status": "parsed",
                    "import_id": historical_import.id,
                    "line_count": historical_import.lines.count(),
                    "duplicate": bool(duplicate_check.get("is_duplicate")),
                    "duplicate_type": duplicate_check.get("duplicate_type", ""),
                    "duplicate_match": duplicate_check.get("primary_match", {}),
                    "duplicate_matches": duplicate_check.get("matches", []),
                },
            )
        except DjangoValidationError as exc:
            filename = getattr(upload, "name", "") or request.data.get("filename", "")
            append_batch_file_result(
                batch,
                {
                    "filename": filename,
                    "status": "failed",
                    "message": " ".join(getattr(exc, "messages", [str(exc)])),
                },
            )
            return self.handle_workflow_error(exc)
        data = HistoricalPriceImportSerializer(historical_import, context={"request": request}).data
        if duplicate_check.get("is_duplicate"):
            data["duplicate_check"] = duplicate_check
        if preview.get("ai_candidate"):
            data["ai_candidate"] = preview["ai_candidate"]
            data["ai_status"] = preview.get("ai_status")
            data["ai_status_label"] = preview.get("ai_status_label")
        return Response(
            {
                "status": "parsed",
                "import": data,
                "batch": self.get_serializer(refresh_historical_import_batch_summary(batch)).data,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    def run_ai_suggestions(self, request, pk=None):
        batch = self.get_object()
        try:
            import_ids = _request_int_list(request.data, "import_ids")
            summary, results = generate_batch_learning_suggestions(
                batch,
                import_ids=import_ids,
                actor=request.user,
                requested_mode=request.data.get("mode") or "auto",
            )
        except (DjangoValidationError, AIParseError) as exc:
            return self.handle_workflow_error(exc)
        return Response(
            {
                "summary": summary,
                "results": results,
                "batch": self.get_serializer(refresh_historical_import_batch_summary(batch)).data,
            }
        )

    @action(detail=True, methods=["post"])
    def apply_ai_suggestions(self, request, pk=None):
        try:
            batch = self.get_object()
            suggestion_ids = _request_int_list(request.data, "suggestion_ids")
            summary, results = apply_historical_ai_suggestions(suggestion_ids, request.user)
            refreshed_batch = refresh_historical_import_batch_summary(batch)
            updated_suggestions = _serialized_ai_suggestions_for_results(batch, results, request)
            serialized_batch = self.get_serializer(refreshed_batch).data
        except Exception as exc:
            return self.handle_safe_workflow_exception(exc, "Apply AI suggestions failed.")
        return Response(
            {
                "summary": summary,
                "results": results,
                "batch": serialized_batch,
                "updated_suggestions": updated_suggestions,
            }
        )

    @action(detail=True, methods=["post"])
    def commit_ready_imports(self, request, pk=None):
        batch = self.get_object()
        try:
            import_ids = _request_int_list(request.data, "import_ids")
            summary, results = commit_ready_imports_for_batch(batch, import_ids, request.user)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        return Response(
            {
                "summary": summary,
                "results": results,
                "batch": self.get_serializer(refresh_historical_import_batch_summary(batch)).data,
            }
        )


class HistoricalImportAISuggestionViewSet(QuotationBaseViewSet, viewsets.ModelViewSet):
    serializer_class = HistoricalImportAISuggestionSerializer
    queryset = HistoricalImportAISuggestion.objects.select_related(
        "batch",
        "historical_import",
        "historical_import__company",
        "line",
        "suggested_company",
        "suggested_product",
        "created_by",
        "applied_by",
    )
    http_method_names = ["get", "patch", "post", "head", "options"]

    def get_queryset(self):
        queryset = super().get_queryset()
        batch_id = self.request.query_params.get("batch")
        import_id = self.request.query_params.get("historical_import")
        status_filter = self.request.query_params.get("status")
        action_filter = self.request.query_params.get("action")
        suggestion_type = self.request.query_params.get("suggestion_type")
        if batch_id:
            queryset = queryset.filter(batch_id=batch_id)
        if import_id:
            queryset = queryset.filter(historical_import_id=import_id)
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if action_filter:
            queryset = queryset.filter(action=action_filter)
        if suggestion_type:
            queryset = queryset.filter(suggestion_type=suggestion_type)
        return queryset

    @transaction.atomic
    def perform_update(self, serializer):
        suggestion = (
            HistoricalImportAISuggestion.objects.select_for_update(of=("self",))
            .get(pk=serializer.instance.pk)
        )
        if suggestion.status != HistoricalImportAISuggestion.STATUS_PENDING:
            raise DRFValidationError({"detail": "Only pending suggestions can be edited."})
        serializer.instance = suggestion
        serializer.save()

    @action(detail=False, methods=["post"])
    def apply(self, request):
        try:
            suggestion_ids = _request_int_list(request.data, "suggestion_ids")
            summary, results = apply_historical_ai_suggestions(suggestion_ids, request.user)
            updated_ids = [result.get("suggestion_id") for result in results if result.get("suggestion_id")]
            suggestions = self.get_queryset().filter(id__in=updated_ids)
            updated_suggestions = self.get_serializer(suggestions, many=True).data
        except Exception as exc:
            return self.handle_safe_workflow_exception(exc, "Apply AI suggestions failed.")
        return Response(
            {
                "summary": summary,
                "results": results,
                "updated_suggestions": updated_suggestions,
            }
        )

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        suggestion_id = self.get_object().pk
        with transaction.atomic():
            suggestion = (
                HistoricalImportAISuggestion.objects.select_for_update(of=("self",))
                .get(pk=suggestion_id)
            )
            self.check_object_permissions(request, suggestion)
            if suggestion.status != HistoricalImportAISuggestion.STATUS_PENDING:
                return Response(
                    {"detail": "Only pending suggestions can be rejected."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            suggestion.status = HistoricalImportAISuggestion.STATUS_REJECTED
            suggestion.error_message = request.data.get("reason", "")
            suggestion.save(update_fields=["status", "error_message", "updated_at"])
        serializer = self.get_serializer(suggestion)
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def source_context(self, request, pk=None):
        suggestion = self.get_object()
        serializer = self.get_serializer(suggestion)
        return Response(serializer.data.get("source_context") or {})


class HistoricalPriceImportViewSet(QuotationBaseViewSet, viewsets.ModelViewSet):
    serializer_class = HistoricalPriceImportSerializer
    queryset = HistoricalPriceImport.objects.select_related(
        "company", "created_by", "committed_by", "created_quotation"
    ).prefetch_related("lines", "lines__quote_item", "lines__product")
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    http_method_names = ["get", "patch", "post", "head", "options"]

    def get_queryset(self):
        queryset = super().get_queryset()
        company_id = self.request.query_params.get("company")
        status_filter = self.request.query_params.get("status")
        search = self.request.query_params.get("search", "").strip()
        if company_id:
            queryset = queryset.filter(company_id=company_id)
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if search:
            queryset = queryset.filter(
                Q(source_filename__icontains=search)
                | Q(document_number__icontains=search)
                | Q(suggested_company_name__icontains=search)
                | Q(company__name__icontains=search)
            )
        return queryset

    def perform_update(self, serializer):
        historical_import = serializer.instance
        if historical_import.status == HistoricalPriceImport.STATUS_COMMITTED:
            raise DjangoValidationError("Committed historical imports cannot be edited.")
        historical_import = serializer.save()
        apply_product_matches_to_historical_import(historical_import, self.request.user)
        audit_log(
            self.request.user,
            QuotationAuditLog.ACTION_UPDATED,
            historical_import,
            message="Updated historical price import.",
        )

    def partial_update(self, request, *args, **kwargs):
        try:
            return super().partial_update(request, *args, **kwargs)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)

    @action(detail=True, methods=["post"])
    def remove_from_batch(self, request, pk=None):
        historical_import = self.get_object()
        if historical_import.status == HistoricalPriceImport.STATUS_COMMITTED:
            return Response(
                {"detail": "Committed historical imports cannot be removed from a batch."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        batch = historical_import.batch
        if not batch:
            return Response(
                {"detail": "This historical import is not attached to a batch."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if HistoricalImportAISuggestion.objects.filter(
            historical_import=historical_import,
            suggestion_type=HistoricalImportAISuggestion.TYPE_LINE,
            status=HistoricalImportAISuggestion.STATUS_APPLIED,
        ).exists():
            return Response(
                {
                    "detail": (
                        "This import has already applied Product/alias decisions. "
                        "It cannot be removed from the batch without leaving durable review changes behind."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        files = []
        for file_result in batch.summary.get("files", []):
            if file_result.get("import_id") == historical_import.id:
                files.append(
                    {
                        **file_result,
                        "status": "removed",
                        "message": "Removed from this batch by staff.",
                    }
                )
            else:
                files.append(file_result)
        batch.summary = {**(batch.summary or {}), "files": files}
        batch.save(update_fields=["summary", "updated_at"])
        HistoricalImportAISuggestion.objects.filter(
            historical_import=historical_import,
            batch=batch,
        ).update(
            batch=None,
            status=HistoricalImportAISuggestion.STATUS_REJECTED,
            error_message="Removed from batch by staff.",
        )
        historical_import.status = HistoricalPriceImport.STATUS_CANCELLED
        historical_import.batch = None
        historical_import.save(update_fields=["status", "batch", "updated_at"])
        audit_log(
            request.user,
            QuotationAuditLog.ACTION_UPDATED,
            historical_import,
            message="Removed historical import from batch.",
        )
        return Response(
            {
                "status": "removed",
                "import": self.get_serializer(historical_import).data,
                "batch": HistoricalImportBatchSerializer(
                    refresh_historical_import_batch_summary(batch),
                    context={"request": request},
                ).data,
            }
        )

    @action(detail=False, methods=["post"], parser_classes=[MultiPartParser, FormParser])
    def parse_file(self, request):
        try:
            preview = parse_historical_pdf_upload(request.FILES.get("file"))
            duplicate_check = find_historical_import_duplicates(preview)
            force_new_import = str(request.data.get("force_new_import", "")).lower() in {"1", "true", "yes"}
            if duplicate_check.get("is_duplicate"):
                preview.setdefault("meta", {})["duplicate_check"] = duplicate_check
                preview.setdefault("warnings", []).append(duplicate_check["message"])
            if duplicate_check.get("blocking") and not force_new_import:
                existing_id = duplicate_check["primary_match"]["id"]
                existing_import = self.get_queryset().get(pk=existing_id)
                serializer = self.get_serializer(existing_import)
                data = dict(serializer.data)
                data["duplicate_check"] = {
                    **duplicate_check,
                    "blocked_new_import": True,
                    "opened_existing_import": True,
                }
                return Response(data, status=status.HTTP_200_OK)
            historical_import = create_historical_price_import(preview, request.user)
            maybe_attach_auto_ai_candidate(preview, actor=request.user, allow_vision=True)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        serializer = self.get_serializer(historical_import)
        data = dict(serializer.data)
        if duplicate_check.get("is_duplicate"):
            data["duplicate_check"] = duplicate_check
        if preview.get("ai_candidate"):
            data["ai_candidate"] = preview["ai_candidate"]
            data["ai_status"] = preview.get("ai_status")
            data["ai_status_label"] = preview.get("ai_status_label")
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def commit(self, request, pk=None):
        historical_import = self.get_object()
        try:
            historical_import = commit_historical_price_import(historical_import, request.user)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        serializer = self.get_serializer(historical_import)
        return Response(serializer.data)

    def _bulk_row_ids(self, request):
        row_ids = request.data.get("row_ids", [])
        if not isinstance(row_ids, list):
            raise DjangoValidationError("row_ids must be a list.")
        try:
            return [int(row_id) for row_id in row_ids]
        except (TypeError, ValueError) as exc:
            raise DjangoValidationError("row_ids must contain only row ids.") from exc

    def _bulk_response(self, historical_import, summary):
        historical_import.refresh_from_db()
        serializer = self.get_serializer(historical_import)
        return Response({"summary": summary, "import": serializer.data})

    @action(detail=True, methods=["post"])
    def bulk_create_quote_items(self, request, pk=None):
        historical_import = self.get_object()
        try:
            summary, historical_import = bulk_create_quote_items_for_historical_import(
                historical_import,
                self._bulk_row_ids(request),
                request.user,
                confirm_create_row_ids=_request_int_list(request.data, "confirm_create_row_ids"),
            )
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        return self._bulk_response(historical_import, summary)

    @action(detail=True, methods=["post"])
    def bulk_update_rows(self, request, pk=None):
        historical_import = self.get_object()
        try:
            summary, historical_import = bulk_update_historical_import_rows(
                historical_import,
                self._bulk_row_ids(request),
                request.data.get("status", ""),
                request.user,
            )
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        return self._bulk_response(historical_import, summary)

    @action(detail=True, methods=["post"])
    def bulk_skip_rows(self, request, pk=None):
        historical_import = self.get_object()
        try:
            summary, historical_import = bulk_update_historical_import_rows(
                historical_import,
                self._bulk_row_ids(request),
                HistoricalPriceImportLine.STATUS_SKIPPED,
                request.user,
            )
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        return self._bulk_response(historical_import, summary)

    @action(detail=True, methods=["post"])
    def ai_clean_rows(self, request, pk=None):
        historical_import = self.get_object()
        requested_mode = request.data.get("mode") or "auto"
        try:
            candidate = clean_historical_import_with_ai(
                historical_import,
                actor=request.user,
                requested_mode=requested_mode,
            )
        except AIParseError as exc:
            return Response(
                {
                    "detail": str(exc),
                    "ai_status": "ai_failed_using_original_parse",
                    "ai_status_label": "AI failed, using original parse.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(candidate)

    @action(detail=True, methods=["post"])
    def run_ai_suggestions(self, request, pk=None):
        historical_import = self.get_object()
        try:
            suggestions, meta = generate_historical_import_learning_suggestions(
                historical_import,
                actor=request.user,
                requested_mode=request.data.get("mode") or "auto",
            )
        except AIParseError as exc:
            return self.handle_workflow_error(exc)
        return Response(
            {
                "summary": {"suggested": len(suggestions), "failed": 0},
                "meta": meta,
                "suggestions": HistoricalImportAISuggestionSerializer(suggestions, many=True, context={"request": request}).data,
            }
        )

    @action(detail=True, methods=["post"])
    def apply_ai_clean_rows(self, request, pk=None):
        historical_import = self.get_object()
        try:
            historical_import = apply_ai_rows_to_historical_import(
                historical_import,
                request.data.get("lines") or [],
                actor=request.user,
                ai_meta={
                    "result_source": request.data.get("result_source", ""),
                    "provider": request.data.get("provider", ""),
                    "model": request.data.get("model", ""),
                    "cache_hit": bool(request.data.get("cache_hit", False)),
                },
            )
        except (DjangoValidationError, AIParseError) as exc:
            return self.handle_workflow_error(exc)
        serializer = self.get_serializer(historical_import)
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def preview_page(self, request, pk=None):
        historical_import = self.get_object()
        if fitz is None:
            return Response({"detail": "PDF preview rendering is not available in this environment."}, status=status.HTTP_400_BAD_REQUEST)
        data = read_private_ref(historical_import.source_file_ref)
        if not data:
            return Response({"detail": "Source PDF is not available in private storage."}, status=status.HTTP_404_NOT_FOUND)
        try:
            with fitz.open(stream=data, filetype="pdf") as document:
                try:
                    requested_page = int(request.query_params.get("page") or 1)
                except (TypeError, ValueError):
                    requested_page = 1
                page_index = max(0, min(requested_page - 1, len(document) - 1))
                page = document[page_index]
                pixmap = page.get_pixmap(matrix=fitz.Matrix(1.2, 1.2), alpha=False)
                png_bytes = pixmap.tobytes("png")
        except Exception as exc:
            return Response({"detail": f"Could not render source PDF preview: {exc}"}, status=status.HTTP_400_BAD_REQUEST)
        return HttpResponse(png_bytes, content_type="image/png")


class HistoricalPriceImportLineViewSet(QuotationBaseViewSet, viewsets.ModelViewSet):
    serializer_class = HistoricalPriceImportLineSerializer
    queryset = HistoricalPriceImportLine.objects.select_related("historical_import", "historical_import__company", "quote_item", "product")
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        queryset = super().get_queryset()
        historical_import_id = self.request.query_params.get("historical_import")
        if historical_import_id:
            queryset = queryset.filter(historical_import_id=historical_import_id)
        return queryset

    def perform_update(self, serializer):
        historical_import = serializer.instance.historical_import
        if historical_import.status == HistoricalPriceImport.STATUS_COMMITTED:
            raise DjangoValidationError("Committed historical import lines cannot be edited.")
        line = serializer.save()
        audit_log(
            self.request.user,
            QuotationAuditLog.ACTION_UPDATED,
            line,
            message="Updated historical import line.",
        )

    def partial_update(self, request, *args, **kwargs):
        try:
            return super().partial_update(request, *args, **kwargs)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)

    @action(detail=True, methods=["post"])
    def remember_alias(self, request, pk=None):
        try:
            alias = remember_historical_import_line_alias(self.get_object(), request.user)
        except (DjangoValidationError, ValueError) as exc:
            return self.handle_workflow_error(exc)
        return Response(ProductAliasSerializer(alias, context={"request": request}).data, status=status.HTTP_201_CREATED)


class CompanyPriceHistoryViewSet(QuotationBaseViewSet, viewsets.ReadOnlyModelViewSet):
    serializer_class = CompanyPriceHistorySerializer
    queryset = CompanyPriceHistory.objects.select_related("company", "product", "quote_item", "quotation", "created_by")

    def get_queryset(self):
        queryset = super().get_queryset()
        company_id = self.request.query_params.get("company")
        if company_id:
            queryset = queryset.filter(company_id=company_id)
        return _filter_price_history_item(queryset, self.request.query_params)


class QuotationAuditLogViewSet(QuotationBaseViewSet, viewsets.ReadOnlyModelViewSet):
    serializer_class = QuotationAuditLogSerializer
    queryset = QuotationAuditLog.objects.select_related("actor", "company", "quotation")
    noisy_target_types = {
        "InquiryLine",
        "QuotationLine",
        "HistoricalPriceImportLine",
        "HistoricalImportAISuggestion",
    }
    noisy_actions = {
        QuotationAuditLog.ACTION_PDF_DOWNLOADED,
    }

    @staticmethod
    def _truthy_param(value):
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        quotation_id = params.get("quotation")
        company_id = params.get("company")
        action = params.get("action")
        actor_id = params.get("actor")
        target_type = params.get("target_type")
        search = (params.get("search") or "").strip()

        if quotation_id:
            queryset = queryset.filter(quotation_id=quotation_id)
        if company_id:
            queryset = queryset.filter(company_id=company_id)
        if action:
            queryset = queryset.filter(action=action)
        if actor_id:
            queryset = queryset.filter(actor_id=actor_id)
        if target_type:
            queryset = queryset.filter(target_type=target_type)
        if self._truthy_param(params.get("important")):
            queryset = queryset.exclude(target_type__in=self.noisy_target_types).exclude(action__in=self.noisy_actions)
        if search:
            queryset = queryset.filter(
                Q(message__icontains=search)
                | Q(actor__username__icontains=search)
                | Q(company__name__icontains=search)
                | Q(quotation__quotation_number__icontains=search)
                | Q(target_type__icontains=search)
            )

        try:
            limit = min(max(int(params.get("limit", 150)), 1), 500)
        except (TypeError, ValueError):
            limit = 150
        return queryset[:limit]
