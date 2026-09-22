"""Reviewed tax invoices. Draft edits, numbering and issuance are atomic."""
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from .ai_parsing import AIParseError, clean_preview_with_ai, prefer_safe_ai_preview
from .import_parsers import parse_file_preview, parse_text_preview
from .lpo_parsing import delivery_lpo_preview, normalize_lpo_preview
from .models import QuotationAuditLog, TaxInvoice, TaxInvoiceLine
from .pdf_config import get_quotation_pdf_config
from .permissions import IsQuotationStaff
from .services import audit_log

CENT = Decimal(".01")
IMPORT_SALT = "tax-invoice-source-v1"


def money(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


class TaxInvoiceLineSerializer(serializers.ModelSerializer):
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3, min_value=Decimal(".001"))
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=3, min_value=0, allow_null=True)
    vat_rate = serializers.DecimalField(max_digits=5, decimal_places=2, allow_null=True)
    discount = serializers.DecimalField(max_digits=16, decimal_places=2, min_value=0, default=Decimal(0))

    class Meta:
        model = TaxInvoiceLine
        fields = ["id", "item_name", "description", "quantity", "unit", "unit_price", "vat_rate",
                  "discount", "line_subtotal", "vat_amount", "line_total"]
        read_only_fields = ["id", "line_subtotal", "vat_amount", "line_total"]
        extra_kwargs = {"description": {"max_length": 2000}}

    def validate(self, attrs):
        missing = {key: "This field is required on every replacement line." for key in ("item_name", "quantity", "unit_price", "vat_rate") if key not in attrs}
        if missing:
            raise ValidationError(missing)
        if attrs.get("vat_rate") not in (None, Decimal(0), Decimal(5)):
            raise ValidationError({"vat_rate": "Choose 0% or 5% VAT."})
        price = attrs.get("unit_price")
        gross = money(attrs["quantity"] * price) if price is not None else Decimal(0)
        if gross >= Decimal("100000000000000"):
            raise ValidationError("Line value is too large. Check the quantity and unit price.")
        discount = attrs.setdefault("discount", Decimal(0))
        if discount > gross:
            raise ValidationError({"discount": "Discount cannot exceed the line value before VAT."})
        subtotal = gross - discount
        vat = money(subtotal * (attrs.get("vat_rate") or Decimal(0)) / 100)
        attrs.update(line_subtotal=subtotal, vat_amount=vat, line_total=subtotal + vat)
        return attrs


class TaxInvoiceSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(max_length=50, required=False, allow_blank=True, allow_null=True)
    lines = TaxInvoiceLineSerializer(many=True, allow_empty=False)
    company_name = serializers.CharField(source="company.name", read_only=True)
    source_filename = serializers.SerializerMethodField()
    import_token = serializers.CharField(write_only=True, required=False)
    expected_revision = serializers.IntegerField(write_only=True, required=False, min_value=1)

    class Meta:
        model = TaxInvoice
        fields = ["id", "company", "company_name", "invoice_number", "status", "invoice_date", "supply_date",
                  "currency", "customer_name", "customer_address", "customer_trn", "attention", "quotation_reference",
                  "lpo_number", "notes", "source_filename", "subtotal", "discount_total", "vat_total", "total", "revision",
                  "issued_at", "created_at", "updated_at", "lines", "import_token", "expected_revision"]
        read_only_fields = ["status", "subtotal", "discount_total", "vat_total", "total", "revision",
                            "issued_at", "created_at", "updated_at"]
        extra_kwargs = {"customer_address": {"max_length": 2000}, "notes": {"max_length": 5000}}

    def get_source_filename(self, obj):
        return obj.source.get("source_filename", "")

    def validate_invoice_number(self, value):
        value = (value or "").strip()
        if not value:
            return None
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ./_-]*", value):
            raise ValidationError("Use letters, numbers, spaces, hyphens, underscores, dots or slashes.")
        query = TaxInvoice.objects.filter(invoice_number__iexact=value)
        if self.instance:
            query = query.exclude(pk=self.instance.pk)
        if query.exists():
            raise ValidationError("This invoice number is already used. Enter a different number.")
        return value

    def validate_currency(self, value):
        if value != "AED":
            raise ValidationError("Tax invoices currently support AED only. Convert and verify other currencies before entry.")
        return value

    def validate_customer_trn(self, value):
        if value and not re.fullmatch(r"[0-9]{15}", value):
            raise ValidationError("Enter a 15-digit customer TRN. Incomplete details can be saved as a draft.")
        return value

    def validate_lines(self, value):
        if len(value) > 300:
            raise ValidationError("Use at most 300 items per invoice.")
        return value

    def validate(self, attrs):
        if self.instance:
            if self.instance.status != "draft":
                raise ValidationError("Issued invoices are locked. Their original record and PDF must be retained.")
            if attrs.pop("expected_revision", None) != self.instance.revision:
                raise ValidationError("This draft changed in another window. Reopen it before saving.")
        else:
            attrs.pop("expected_revision", None)
        token = attrs.pop("import_token", None)
        if token:
            try:
                source = signing.loads(token, salt=IMPORT_SALT, max_age=86400)
                if source.get("parsed_by") != self.context["request"].user.pk:
                    raise signing.BadSignature()
            except signing.BadSignature:
                raise ValidationError("This scan preview expired or belongs to another user. Scan the document again.")
            attrs["source"] = source
        return attrs

    def _save_lines(self, instance, rows):
        instance.lines.all().delete()
        TaxInvoiceLine.objects.bulk_create([TaxInvoiceLine(invoice=instance, sort_order=i, **row) for i, row in enumerate(rows)])
        for field, key in [("subtotal", "line_subtotal"), ("discount_total", "discount"), ("vat_total", "vat_amount"), ("total", "line_total")]:
            setattr(instance, field, sum((row[key] for row in rows), Decimal(0)))
        instance.save()
        return instance

    def create(self, validated_data):
        rows = validated_data.pop("lines")
        obj = TaxInvoice.objects.create(created_by=self.context["request"].user, **validated_data)
        return self._save_lines(obj, rows)

    def update(self, instance, validated_data):
        rows = validated_data.pop("lines", None)
        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.revision += 1
        instance.save()
        if rows is not None:
            self._save_lines(instance, rows)
        return instance


class TaxInvoiceListSerializer(TaxInvoiceSerializer):
    class Meta(TaxInvoiceSerializer.Meta):
        fields = [field for field in TaxInvoiceSerializer.Meta.fields if field not in {"lines", "import_token", "expected_revision"}]


class InvoicePagination(PageNumberPagination):
    page_size = 30


class TaxInvoiceViewSet(viewsets.ModelViewSet):
    permission_classes = [IsQuotationStaff]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    pagination_class = InvoicePagination
    serializer_class = TaxInvoiceSerializer
    # Issued records are retained; drafts can be reopened rather than deleted.
    http_method_names = ["get", "post", "put", "patch", "head", "options"]

    def get_queryset(self):
        query = TaxInvoice.objects.select_related("company").defer("issued_pdf", "supplier_snapshot")
        if self.action in {"update", "partial_update", "issue"}:
            query = query.select_for_update(of=("self",))
        if self.action == "list":
            search = self.request.query_params.get("search", "").strip()[:200]
            if search:
                query = query.filter(Q(invoice_number__icontains=search) | Q(customer_name__icontains=search)
                                     | Q(quotation_reference__icontains=search) | Q(lpo_number__icontains=search))
            status = self.request.query_params.get("status")
            if status in {"draft", "issued"}:
                query = query.filter(status=status)
        return query

    def get_serializer_class(self):
        return TaxInvoiceListSerializer if self.action == "list" else TaxInvoiceSerializer

    def handle_exception(self, exc):
        if isinstance(exc, DjangoValidationError):
            exc = ValidationError(exc.messages)
        return super().handle_exception(exc)

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        obj = self._save_draft(serializer)
        audit_log(self.request.user, QuotationAuditLog.ACTION_CREATED, obj, message="Created tax invoice draft.")

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    def perform_update(self, serializer):
        obj = self._save_draft(serializer)
        audit_log(self.request.user, QuotationAuditLog.ACTION_UPDATED, obj, message="Updated tax invoice draft.", changes={"revision": obj.revision})

    @staticmethod
    def _save_draft(serializer):
        try:
            # A savepoint makes a simultaneous reservation of the same manual
            # number a validation error, without leaving partial header/line edits.
            with transaction.atomic():
                return serializer.save()
        except IntegrityError:
            number = serializer.validated_data.get("invoice_number")
            query = TaxInvoice.objects.filter(invoice_number__iexact=number) if number else TaxInvoice.objects.none()
            if serializer.instance:
                query = query.exclude(pk=serializer.instance.pk)
            if query.exists():
                raise ValidationError({"invoice_number": "This invoice number is already used. Enter a different number."})
            raise

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def issue(self, request, pk=None):
        invoice = self.get_object()
        if invoice.status == "issued":
            return Response(self.get_serializer(invoice).data)
        if request.data.get("expected_revision") != invoice.revision:
            raise ValidationError("Save and review the latest draft before issuing it.")
        if not invoice.invoice_number:
            raise ValidationError({"invoice_number": "Enter the invoice number before issuing."})
        if not invoice.customer_name.strip():
            raise ValidationError({"customer_name": "Enter the customer's full legal name before issuing."})
        if not invoice.customer_address.strip():
            raise ValidationError("Enter the customer billing address before issuing.")
        if not re.fullmatch(r"[0-9]{15}", invoice.customer_trn):
            raise ValidationError({"customer_trn": "Enter the customer's 15-digit TRN before issuing."})
        rows = list(invoice.lines.all())
        if not rows or any(row.unit_price is None or row.vat_rate is None for row in rows):
            raise ValidationError("Every item needs a unit price and a confirmed VAT rate before issuing.")
        config = get_quotation_pdf_config(include_hidden_trn=True)
        if not re.fullmatch(r"\d{15}", config.trn or "") or not config.company_name or not config.address:
            raise ValidationError("Add the pharmacy name, address and 15-digit TRN in quotation Settings before issuing.")
        invoice.status = "issued"
        invoice.issued_by = request.user
        invoice.issued_at = timezone.now()
        invoice.revision += 1
        invoice.supplier_snapshot = {key: getattr(config, key) for key in ("company_name", "address", "trn", "phone", "email")}
        from .tax_invoice_pdf import BANK_DETAILS
        invoice.supplier_snapshot["bank_details"] = dict(BANK_DETAILS)
        from .tax_invoice_pdf import build_tax_invoice_pdf
        invoice.issued_pdf = build_tax_invoice_pdf(invoice, config=config)
        invoice.save()
        audit_log(request.user, QuotationAuditLog.ACTION_UPDATED, invoice, message=f"Issued tax invoice {invoice.invoice_number}.")
        return Response(self.get_serializer(invoice).data)

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        invoice = self.get_object()
        from .tax_invoice_pdf import build_tax_invoice_pdf
        data = bytes(invoice.issued_pdf) if invoice.status == "issued" else build_tax_invoice_pdf(invoice)
        response = HttpResponse(data, content_type="application/pdf")
        filename = re.sub(r"[^A-Za-z0-9._-]", "_", invoice.invoice_number or f"DRAFT-{invoice.pk}")
        response["Content-Disposition"] = f'attachment; filename="{filename}.pdf"'
        response["Cache-Control"] = "private, no-store"
        return response

    @action(detail=False, methods=["post"], url_path="parse-document")
    def parse_document(self, request):
        uploaded = request.FILES.get("file")
        text = str(request.data.get("text") or "").strip()
        if not uploaded and not text:
            raise ValidationError("Upload a quotation or paste its text.")
        if len(text) > 200000:
            raise ValidationError("Use at most 200,000 characters.")
        original = normalize_lpo_preview(parse_file_preview(uploaded) if uploaded else parse_text_preview(text))
        preview = original
        warnings = list(original.get("warnings") or [])
        if str(request.data.get("use_ai", "true")).lower() not in {"false", "0", "no"}:
            try:
                cleaned = clean_preview_with_ai(original, actor=request.user, delivery_details=True)
                original_details = original.get("meta", {}).get("delivery_details", {})
                ai_details = cleaned.get("meta", {}).get("delivery_details", {})
                cleaned.setdefault("meta", {})["delivery_details"] = {key: ai_details.get(key) or value for key, value in original_details.items()}
                preview = prefer_safe_ai_preview(original, normalize_lpo_preview(cleaned, read_pdf=False), max_guard_rows=300, check_units=True)
            except AIParseError as exc:
                warnings.append(str(exc))
        result = delivery_lpo_preview(preview, request.user, document_type="quotation")
        rows = [row for row in preview.get("lines", []) if row.get("parse_status") != "ignored"
                and str(row.get("requested_item_name") or row.get("raw_name") or row.get("item_name") or "").strip()]
        for line, row in zip(result["lines"], rows):
            line.pop("quotation_line", None)
            # Never interpret a line total as its unit price or guess a missing VAT rate.
            line["unit_price"] = self._parsed_decimal(row.get("unit_price"), places=3)
            line["vat_rate"] = self._parsed_decimal(row.get("vat_rate"), places=2, allowed={Decimal(0), Decimal(5)})
            line["discount"] = "0.00"
        warnings.extend(result["warnings"])
        warnings.append("Review every quantity, unit price, VAT rate and discount against the quotation. Missing prices and VAT must be entered before issuing. Amounts must be in AED.")
        result["warnings"] = list(dict.fromkeys(warnings))
        source = {key: preview.get(key, "") for key in ("source_filename", "source_sha256", "source_file_ref", "parse_method")}
        source.update(parsed_by=request.user.pk, parsed_at=timezone.now().isoformat(), details=result["details"], lines=result["lines"], warnings=result["warnings"])
        result["import_token"] = signing.dumps(source, salt=IMPORT_SALT, compress=True)
        return Response(result)

    @staticmethod
    def _parsed_decimal(value, *, places, allowed=None):
        try:
            number = Decimal(str(value))
            if not number.is_finite() or number < 0 or number >= Decimal("1000000000"):
                return None
            if allowed is not None and number not in allowed:
                return None
            # Leave excessive precision for review rather than silently altering the source price.
            if number != number.quantize(Decimal(10) ** -places):
                return None
            return str(number)
        except (InvalidOperation, ValueError, TypeError):
            return None
