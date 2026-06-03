import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.db.models import Q
from django.http import HttpResponse
from rest_framework import status, viewsets
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from api.models import Product

from .ai_parsing import (
    AIParseError,
    apply_ai_rows_to_historical_import,
    clean_historical_import_with_ai,
    clean_preview_with_ai,
    maybe_attach_auto_ai_candidate,
)
from .ai_learning import (
    append_batch_file_result,
    apply_historical_ai_suggestions,
    commit_ready_imports_for_batch,
    generate_batch_learning_suggestions,
    generate_historical_import_learning_suggestions,
    refresh_historical_import_batch_summary,
)
from .historical_import_parsers import parse_historical_pdf_upload
from .import_parsers import parse_file_preview, parse_text_preview
from .matching import apply_match_to_preview_line
from .models import (
    Company,
    CompanyContact,
    CompanyPriceHistory,
    HistoricalImportAISuggestion,
    HistoricalImportBatch,
    HistoricalPriceImport,
    HistoricalPriceImportLine,
    Inquiry,
    InquiryLine,
    Quotation,
    QuotationAuditLog,
    QuotationLine,
    QuotationSettings,
    ProductAlias,
)
from .pdf import build_quotation_pdf
from .permissions import IsQuotationStaff
from .serializers import (
    CompanyContactSerializer,
    CompanyPriceHistorySerializer,
    CompanySerializer,
    HistoricalPriceImportLineSerializer,
    HistoricalPriceImportSerializer,
    HistoricalImportAISuggestionSerializer,
    HistoricalImportBatchSerializer,
    ImportedInquiryCreateSerializer,
    InquiryLineSerializer,
    InquirySerializer,
    QuotationAuditLogSerializer,
    QuotationLineSerializer,
    QuotationSettingsSerializer,
    QuotationSerializer,
    ProductAliasSerializer,
    QuoteItemSerializer,
    serializer_error_from_django_validation,
)
from .services import (
    audit_log,
    apply_product_matches_to_historical_import,
    bulk_create_quote_items_for_historical_import,
    bulk_create_products_from_quotation_lines,
    bulk_update_quotation_lines,
    bulk_update_historical_import_rows,
    commit_historical_price_import,
    create_historical_price_import,
    create_imported_inquiry,
    create_product_from_quotation_line,
    create_quotation_from_inquiry,
    ensure_quotation_editable,
    find_historical_import_duplicates,
    finalize_quotation,
    remember_historical_import_line_alias,
    remember_inquiry_line_alias,
    remember_quotation_line_alias,
    recalculate_quotation_totals,
    revise_quotation,
    transition_quotation_status,
)
from .private_storage import read_private_ref

try:
    import fitz
except Exception:  # pragma: no cover
    fitz = None


logger = logging.getLogger(__name__)


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


class CompanyViewSet(QuotationBaseViewSet, viewsets.ModelViewSet):
    serializer_class = CompanySerializer
    queryset = Company.objects.prefetch_related("contacts")

    def get_queryset(self):
        queryset = super().get_queryset()
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
        item_id = request.query_params.get("item")
        if item_id:
            queryset = queryset.filter(Q(product_id=item_id) | Q(quote_item_id=item_id))
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
    queryset = Product.objects.select_related("brand", "category").prefetch_related("images")

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
        return queryset.order_by("name")

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
        alias = serializer.save(created_by=self.request.user)
        audit_log(self.request.user, QuotationAuditLog.ACTION_CREATED, alias, message="Created product alias.")

    def perform_update(self, serializer):
        alias = serializer.save()
        audit_log(self.request.user, QuotationAuditLog.ACTION_UPDATED, alias, message="Updated product alias.")


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
        inquiry = serializer.save()
        audit_log(self.request.user, QuotationAuditLog.ACTION_CREATED, inquiry, message="Created inquiry.")

    def perform_update(self, serializer):
        inquiry = serializer.save()
        audit_log(self.request.user, QuotationAuditLog.ACTION_UPDATED, inquiry, message="Updated inquiry.")

    @action(detail=False, methods=["post"])
    def parse_text(self, request):
        raw_text = request.data.get("raw_text") or request.data.get("text") or ""
        raw_html = request.data.get("raw_html") or request.data.get("html") or ""
        if not str(raw_text).strip():
            return Response({"detail": "Paste inquiry text before extracting lines."}, status=status.HTTP_400_BAD_REQUEST)
        preview = parse_text_preview(raw_text, raw_html=raw_html)
        self._apply_product_matches(preview, request.data.get("company"))
        maybe_attach_auto_ai_candidate(preview, actor=request.user, allow_vision=False)
        return Response(preview)

    @action(detail=False, methods=["post"], parser_classes=[MultiPartParser, FormParser])
    def parse_file(self, request):
        try:
            preview = parse_file_preview(request.FILES.get("file"))
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        self._apply_product_matches(preview, request.data.get("company"))
        maybe_attach_auto_ai_candidate(preview, actor=request.user, allow_vision=True)
        return Response(preview)

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
        inquiry = create_imported_inquiry(serializer.validated_data, request.user)
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
        line = serializer.save()
        audit_log(self.request.user, QuotationAuditLog.ACTION_CREATED, line, message="Created inquiry line.")

    def perform_update(self, serializer):
        line = serializer.save()
        audit_log(self.request.user, QuotationAuditLog.ACTION_UPDATED, line, message="Updated inquiry line.")

    @action(detail=True, methods=["post"])
    def remember_alias(self, request, pk=None):
        try:
            alias = remember_inquiry_line_alias(self.get_object(), request.user)
        except (DjangoValidationError, ValueError) as exc:
            return self.handle_workflow_error(exc)
        return Response(ProductAliasSerializer(alias, context={"request": request}).data, status=status.HTTP_201_CREATED)


class QuotationViewSet(QuotationBaseViewSet, viewsets.ModelViewSet):
    serializer_class = QuotationSerializer
    queryset = Quotation.objects.select_related(
        "company", "contact", "inquiry", "created_by", "finalized_by", "parent"
    ).prefetch_related("lines", "lines__quote_item", "lines__product")

    def get_queryset(self):
        queryset = super().get_queryset()
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
        return queryset

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
        try:
            ensure_quotation_editable(quotation)
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        audit_log(request.user, QuotationAuditLog.ACTION_DELETED, quotation, message="Deleted quotation.")
        return super().destroy(request, *args, **kwargs)

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
        response["Content-Disposition"] = f'attachment; filename="{quotation.quotation_number}.pdf"'
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

        history = CompanyPriceHistory.objects.filter(
            company=quotation.company,
            product=product,
        ).order_by("-quoted_at", "-id").first()
        if history:
            return Response(
                {
                    "product": product.id,
                    "product_name": product.name,
                    "unit_price": str(history.unit_price),
                    "unit": history.unit or "",
                    "currency": history.currency,
                    "source": "company_price_history",
                    "source_label": f"Latest {quotation.company.name} price",
                    "quoted_at": history.quoted_at.date().isoformat(),
                }
            )

        return Response(
            {
                "product": product.id,
                "product_name": product.name,
                "unit_price": str(product.price) if product.price is not None else "",
                "unit": "",
                "currency": quotation.currency,
                "source": "product_base_price",
                "source_label": "Product base price",
                "quoted_at": "",
            }
        )

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
                "message": (
                    f"Linked {len(summary['updated_lines'])} line(s) to "
                    f"{summary['unique_products']} Product(s)."
                ),
            }
        )


class QuotationLineViewSet(QuotationBaseViewSet, viewsets.ModelViewSet):
    serializer_class = QuotationLineSerializer
    queryset = QuotationLine.objects.select_related("quotation", "quotation__company", "quote_item", "product", "inquiry_line")

    def get_queryset(self):
        queryset = super().get_queryset()
        quotation_id = self.request.query_params.get("quotation")
        if quotation_id:
            queryset = queryset.filter(quotation_id=quotation_id)
        return queryset

    def perform_create(self, serializer):
        quotation = serializer.validated_data["quotation"]
        ensure_quotation_editable(quotation)
        line = serializer.save()
        recalculate_quotation_totals(quotation)
        audit_log(self.request.user, QuotationAuditLog.ACTION_CREATED, line, message="Created quotation line.")

    def perform_update(self, serializer):
        ensure_quotation_editable(serializer.instance.quotation)
        line = serializer.save()
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
            line, product, created = create_product_from_quotation_line(
                self.get_object(),
                request.user,
                product_name=request.data.get("product_name") or "",
            )
        except Exception as exc:
            return self.handle_safe_workflow_exception(exc, "Create Product from quote line failed. Check the Product name and line status.")
        return Response(
            {
                "line": QuotationLineSerializer(line, context={"request": request}).data,
                "product": QuoteItemSerializer(product, context={"request": request}).data,
                "created": created,
                "message": (
                    f"Created draft/internal Product '{product.name}' and linked the row."
                    if created
                    else f"Linked the row to existing Product '{product.name}'."
                ),
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
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
        suggestion = self.get_object()
        if suggestion.status != HistoricalImportAISuggestion.STATUS_PENDING:
            return Response({"detail": "Only pending suggestions can be rejected."}, status=status.HTTP_400_BAD_REQUEST)
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
        item_id = self.request.query_params.get("item")
        if company_id:
            queryset = queryset.filter(company_id=company_id)
        if item_id:
            queryset = queryset.filter(Q(product_id=item_id) | Q(quote_item_id=item_id))
        return queryset


class QuotationAuditLogViewSet(QuotationBaseViewSet, viewsets.ReadOnlyModelViewSet):
    serializer_class = QuotationAuditLogSerializer
    queryset = QuotationAuditLog.objects.select_related("actor", "company", "quotation")

    def get_queryset(self):
        queryset = super().get_queryset()
        quotation_id = self.request.query_params.get("quotation")
        company_id = self.request.query_params.get("company")
        if quotation_id:
            queryset = queryset.filter(quotation_id=quotation_id)
        if company_id:
            queryset = queryset.filter(company_id=company_id)
        return queryset
