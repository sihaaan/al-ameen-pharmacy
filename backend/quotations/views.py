from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.http import HttpResponse
from rest_framework import status, viewsets
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from api.models import Product

from .historical_import_parsers import parse_historical_pdf_upload
from .import_parsers import parse_file_preview, parse_text_preview
from .matching import apply_match_to_preview_line
from .models import (
    Company,
    CompanyContact,
    CompanyPriceHistory,
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
    bulk_update_historical_import_rows,
    commit_historical_price_import,
    create_historical_price_import,
    create_imported_inquiry,
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


class QuotationBaseViewSet:
    permission_classes = [IsQuotationStaff]

    def handle_workflow_error(self, exc):
        return Response(serializer_error_from_django_validation(exc), status=status.HTTP_400_BAD_REQUEST)


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
        if not str(raw_text).strip():
            return Response({"detail": "Paste inquiry text before extracting lines."}, status=status.HTTP_400_BAD_REQUEST)
        preview = parse_text_preview(raw_text)
        self._apply_product_matches(preview, request.data.get("company"))
        return Response(preview)

    @action(detail=False, methods=["post"], parser_classes=[MultiPartParser, FormParser])
    def parse_file(self, request):
        try:
            preview = parse_file_preview(request.FILES.get("file"))
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        self._apply_product_matches(preview, request.data.get("company"))
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
        except DjangoValidationError as exc:
            return self.handle_workflow_error(exc)
        serializer = self.get_serializer(historical_import)
        data = dict(serializer.data)
        if duplicate_check.get("is_duplicate"):
            data["duplicate_check"] = duplicate_check
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
                page = document[0]
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
