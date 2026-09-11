from collections import Counter, defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from .delivery import (
    accepted_quantity, cancel_delivery_note, confirm_delivery_note, issue_delivery_note,
    lock_note, order_progress,
)
from .models import Company, DeliveryNote, DeliveryNoteLine, Quotation, QuotationAuditLog, QuotationLine
from .permissions import IsQuotationStaff
from .services import audit_log
from .lpo_parsing import (
    normalize_lpo_preview, delivery_lpo_preview, read_delivery_import_token, extract_delivery_details,
)
from .import_parsers import parse_file_preview, parse_text_preview
from .ai_parsing import AIParseError, clean_preview_with_ai, prefer_safe_ai_preview
from .company_matching import score_company_name


class DeliveryPagination(PageNumberPagination):
    page_size = 50


class DeliveryLineSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(max_length=2000, required=False, allow_blank=True)

    class Meta:
        model = DeliveryNoteLine
        fields = ["id", "quotation_line", "item_name", "description", "unit", "quantity", "deliver_later", "received_quantity", "sort_order"]
        read_only_fields = ["id", "received_quantity"]
        validators = []  # The parent validates membership and duplicates together.


class DeliveryNoteSerializer(serializers.ModelSerializer):
    lpo_import_token = serializers.CharField(write_only=True, required=False, max_length=500000)
    lpo_source = serializers.SerializerMethodField()
    later_deliveries = serializers.SerializerMethodField()
    continued_from_number = serializers.CharField(source="continued_from.delivery_number", read_only=True, default=None)
    lines = DeliveryLineSerializer(many=True, required=False)
    company_name = serializers.CharField(source="company.name", read_only=True)
    quotation_number = serializers.CharField(source="quotation.quotation_number", read_only=True, default=None)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    created_by_username = serializers.CharField(source="created_by.username", read_only=True, default=None)
    company = serializers.PrimaryKeyRelatedField(queryset=Company.objects.all(), required=False)
    notes = serializers.CharField(max_length=4000, required=False, allow_blank=True)
    delivery_address = serializers.CharField(max_length=2000, required=False, allow_blank=True)

    class Meta:
        model = DeliveryNote
        fields = [
            "id", "delivery_number", "status", "status_display", "company", "company_name",
            "quotation", "quotation_number", "quotation_reference", "delivery_date", "lpo_number", "invoice_number",
            "customer_name", "customer_address", "customer_trn", "delivery_address", "attention",
            "contact_phone", "notes", "lines", "received_by", "received_date", "receipt_reference",
            "receipt_notes", "cancellation_reason", "created_by_username", "issued_at", "confirmed_at",
            "cancelled_at", "created_at", "updated_at",
            "lpo_import_token", "lpo_source",
            "continued_from", "continued_from_number", "later_deliveries",
        ]
        read_only_fields = [
            "delivery_number", "status", "customer_name", "customer_address", "customer_trn",
            "received_by", "received_date", "receipt_reference", "receipt_notes", "cancellation_reason",
            "issued_at", "confirmed_at", "cancelled_at", "created_at", "updated_at",
            "continued_from",
        ]

    def validate(self, attrs):
        instance = self.instance
        quote = attrs.get("quotation", instance.quotation if instance else None)
        company = attrs.get("company", instance.company if instance else None)
        if instance:
            if instance.status != DeliveryNote.STATUS_DRAFT:
                raise serializers.ValidationError("Issued notes are locked. Cancel an incorrect note and create a replacement.")
            if quote != instance.quotation or company != instance.company:
                raise serializers.ValidationError("The source quotation and customer cannot be changed on an existing delivery note.")
        if quote:
            if quote.status not in {Quotation.STATUS_FINALIZED, Quotation.STATUS_SENT}:
                raise serializers.ValidationError("Select a finalized or sent quotation with accepted quantities.")
            if company and company.pk != quote.company_id:
                raise serializers.ValidationError("The customer must match the quotation.")
            attrs["company"] = quote.company
            attrs["quotation_reference"] = quote.quotation_number
        elif not company:
            raise serializers.ValidationError({"company": "Select a customer."})
        token = attrs.pop("lpo_import_token", None)
        if token:
            if quote:
                raise serializers.ValidationError("Review this source document through the quotation's Manage order workflow.")
            attrs["lpo_import"] = read_delivery_import_token(token, self.context["request"].user)
            if instance:
                attrs.update(self._customer_snapshot(company, attrs["lpo_import"]))
        rows = attrs.get("lines", [])
        if len(rows) > 300:
            raise serializers.ValidationError("A delivery note can contain at most 300 items.")
        seen = set()
        for row in rows:
            source = row.get("quotation_line")
            if quote:
                if not source or source.quotation_id != quote.pk or accepted_quantity(source) <= 0:
                    raise serializers.ValidationError("Select accepted items from this quotation only.")
                if source.pk in seen:
                    raise serializers.ValidationError("Each quotation item can appear only once in a delivery note.")
                seen.add(source.pk)
                row.update(item_name=source.item_name_snapshot, description=source.description, unit=source.unit)
            elif source:
                raise serializers.ValidationError("Standalone delivery notes cannot reference quotation items.")
            elif not row.get("item_name", "").strip():
                raise serializers.ValidationError("Enter a name for every delivery item.")
        return attrs

    def get_lpo_source(self, note):
        source = note.lpo_import or {}
        return {key: source.get(key) for key in ("source_filename", "document_type", "parse_method", "parsed_at", "details", "warnings")} if source else None

    def get_later_deliveries(self, note):
        return [{"id": later.pk, "delivery_number": later.delivery_number, "status": later.status}
                for later in note.later_deliveries.all()]

    @staticmethod
    def _customer_snapshot(company, source):
        fields = {"customer_name": company.name, "customer_address": company.billing_address, "customer_trn": company.trn}
        details = (source or {}).get("details") or {}
        if score_company_name(details.get("customer_name", ""), company.name)[0] >= 96:
            fields["customer_name"] = str(details["customer_name"])[:255]
            for field, limit in (("customer_address", 2000), ("customer_trn", 100)):
                if not fields[field]:
                    fields[field] = str(details.get(field) or "")[:limit]
        return fields

    @transaction.atomic
    def create(self, validated_data):
        rows = validated_data.pop("lines", [])
        quote = validated_data.get("quotation")
        if quote:
            quote = Quotation.objects.select_for_update().get(pk=quote.pk)
            if quote.status not in {Quotation.STATUS_FINALIZED, Quotation.STATUS_SENT}:
                raise serializers.ValidationError("This quotation is no longer available for delivery.")
        company = validated_data["company"]
        if quote:
            lpos = quote.lpos.filter(status="confirmed")
            if validated_data.get("lpo_number"):
                lpos = lpos.filter(lpo_number=validated_data["lpo_number"])
            lpo = lpos.order_by("-id").first()
            validated_data.setdefault("lpo_number", lpo.lpo_number if lpo else "")
            validated_data.setdefault("attention", quote.contact.name if quote.contact else "")
            validated_data.setdefault("contact_phone", quote.contact.phone if quote.contact else "")
            if lpo:
                details = (lpo.parsed_meta or {}).get("delivery_details") or extract_delivery_details({
                    "source_filename": lpo.source_filename, "source_file_ref": lpo.source_file_ref,
                    "source_sha256": lpo.source_sha256, "meta": lpo.parsed_meta,
                })
                validated_data["lpo_import"] = {
                    "lpo_id": lpo.pk, "source_filename": lpo.source_filename,
                    "source_file_ref": lpo.source_file_ref, "source_sha256": lpo.source_sha256,
                    "parse_method": lpo.parse_method, "details": details, "warnings": lpo.warnings,
                }
                for field in ("delivery_address", "attention", "contact_phone"):
                    if details.get(field) and not validated_data.get(field):
                        validated_data[field] = details[field]
        validated_data.setdefault("delivery_address", company.billing_address)
        validated_data.update(self._customer_snapshot(company, validated_data.get("lpo_import")))
        actor = self.context["request"].user
        note = DeliveryNote.objects.create(**validated_data, created_by=actor)
        self._save_lines(note, rows)
        audit_log(actor, QuotationAuditLog.ACTION_CREATED, note, message=f"Created delivery note {note.delivery_number}.")
        return note

    @transaction.atomic
    def update(self, instance, validated_data):
        note = lock_note(instance)
        if note.status != DeliveryNote.STATUS_DRAFT:
            raise serializers.ValidationError("This note has already been issued. Refresh before continuing.")
        rows = validated_data.pop("lines", None)
        for name, value in validated_data.items():
            setattr(note, name, value)
        note.save()
        if rows is not None:
            note.lines.all().delete()
            self._save_lines(note, rows)
        audit_log(self.context["request"].user, QuotationAuditLog.ACTION_UPDATED, note, message=f"Updated draft {note.delivery_number}.")
        return note

    @staticmethod
    def _save_lines(note, rows):
        for index, row in enumerate(rows):
            row["sort_order"] = index
            DeliveryNoteLine.objects.create(delivery_note=note, **row)


class ReceiptLineSerializer(serializers.Serializer):
    id = serializers.IntegerField(min_value=1)
    received_quantity = serializers.DecimalField(max_digits=12, decimal_places=3, min_value=Decimal("0"))


class ReceiptSerializer(serializers.Serializer):
    received_by = serializers.CharField(max_length=255)
    received_date = serializers.DateField()
    receipt_reference = serializers.CharField(max_length=255, required=False, allow_blank=True)
    receipt_notes = serializers.CharField(max_length=4000, required=False, allow_blank=True)
    lines = ReceiptLineSerializer(many=True, required=False)


class CancellationSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=4000)


class DeliveryReferencesSerializer(serializers.Serializer):
    lpo_number = serializers.CharField(max_length=120, required=False, allow_blank=True)
    invoice_number = serializers.CharField(max_length=120, required=False, allow_blank=True)
    quotation_reference = serializers.CharField(max_length=120, required=False, allow_blank=True)

    def validate(self, attrs):
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError("Only LPO, invoice and standalone quotation references can be corrected here.")
        if not attrs:
            raise serializers.ValidationError("Enter a reference to update.")
        return attrs


class DeliveryNoteViewSet(viewsets.ModelViewSet):
    permission_classes = [IsQuotationStaff]
    serializer_class = DeliveryNoteSerializer
    pagination_class = DeliveryPagination
    http_method_names = ["get", "post", "patch", "head", "options"]
    queryset = DeliveryNote.objects.select_related("company", "quotation", "created_by", "continued_from").prefetch_related("lines", "later_deliveries")

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action != "list":
            return queryset
        params = self.request.query_params
        if params.get("quotation"):
            if not params["quotation"].isdigit():
                raise serializers.ValidationError("Invalid quotation ID.")
            queryset = queryset.filter(quotation_id=params["quotation"])
        if params.get("status"):
            queryset = queryset.filter(status=params["status"])
        if params.get("search"):
            term = params["search"][:200]
            queryset = queryset.filter(Q(delivery_number__icontains=term) | Q(customer_name__icontains=term) | Q(lpo_number__icontains=term) | Q(invoice_number__icontains=term) | Q(quotation_reference__icontains=term) | Q(quotation__quotation_number__icontains=term))
        return queryset

    def handle_exception(self, exc):
        if isinstance(exc, DjangoValidationError):
            exc = serializers.ValidationError({"detail": " ".join(exc.messages)})
        return super().handle_exception(exc)

    @action(detail=False, methods=["post"], parser_classes=[MultiPartParser, FormParser, JSONParser])
    def parse_lpo(self, request):
        # Keep the original URL working for previously loaded clients.
        return self._parse_document(request)

    @action(detail=False, methods=["post"], parser_classes=[MultiPartParser, FormParser, JSONParser])
    def parse_document(self, request):
        return self._parse_document(request)

    def _parse_document(self, request):
        uploaded = request.FILES.get("file")
        text = str(request.data.get("text") or "")
        document_type = str(request.data.get("document_type") or "auto").lower()
        if document_type not in {"auto", "lpo", "quotation"}:
            raise serializers.ValidationError("Select LPO, quotation, or automatic document detection.")
        if not uploaded and not text.strip():
            raise serializers.ValidationError("Upload an LPO or quotation, or paste its text.")
        if len(text) > 200000:
            raise serializers.ValidationError("The pasted document is too large. Upload the file instead.")
        preview = normalize_lpo_preview(parse_file_preview(uploaded) if uploaded else parse_text_preview(text))
        original = preview
        warnings = list(preview.get("warnings") or [])
        if str(request.data.get("use_ai", "true")).lower() not in {"0", "false", "no"}:
            try:
                cleaned = clean_preview_with_ai(preview, actor=request.user, delivery_details=True)
                # Removing header rows must not discard the fields already read
                # from those rows when AI leaves a header value blank.
                source_details = (original.get("meta") or {}).get("delivery_details") or {}
                ai_details = (cleaned.get("meta") or {}).get("delivery_details") or {}
                cleaned = {**cleaned, "meta": {**(cleaned.get("meta") or {}), "delivery_details": {
                    key: ai_details.get(key) or value for key, value in source_details.items()
                }}}
                cleaned = normalize_lpo_preview(cleaned, read_pdf=False)
                preview = prefer_safe_ai_preview(original, cleaned, max_guard_rows=300, check_units=True)
            except AIParseError as exc:
                warnings.append(str(exc))
        preview["warnings"] = list(dict.fromkeys([*warnings, *(preview.get("warnings") or [])]))
        result = delivery_lpo_preview(preview, request.user, document_type=document_type)
        audit_log(request.user, QuotationAuditLog.ACTION_LPO_UPLOADED, None,
                  message="Parsed a source document for standalone delivery note review.",
                  changes={"source_filename": result["source_filename"], "document_type": result["document_type"], "line_count": len(result["lines"])})
        return Response(result)

    @action(detail=True, methods=["post"])
    def issue(self, request, pk=None):
        note = issue_delivery_note(self.get_object(), request.user)
        return Response(self.get_serializer(note).data)

    @action(detail=True, methods=["post"], url_path="update-references")
    @transaction.atomic
    def update_references(self, request, pk=None):
        serializer = DeliveryReferencesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        note = lock_note(self.get_object())
        if note.status == DeliveryNote.STATUS_CANCELLED:
            raise serializers.ValidationError("Cancelled notes keep their original references.")
        if note.quotation_id and "quotation_reference" in serializer.validated_data:
            if serializer.validated_data["quotation_reference"] != note.quotation.quotation_number:
                raise serializers.ValidationError("The linked quotation reference cannot be changed.")
        changes = {}
        for field, value in serializer.validated_data.items():
            previous = getattr(note, field)
            if previous != value:
                changes[field] = {"before": previous, "after": value}
                setattr(note, field, value)
        if changes:
            note.save(update_fields=[*changes, "updated_at"])
            audit_log(request.user, QuotationAuditLog.ACTION_UPDATED, note,
                      message=f"Corrected references on {note.delivery_number}.", changes=changes)
        return Response(self.get_serializer(note).data)

    @action(detail=True, methods=["post"], url_path="confirm-receipt")
    def confirm_receipt(self, request, pk=None):
        serializer = ReceiptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        note = confirm_delivery_note(self.get_object(), serializer.validated_data, request.user)
        return Response(self.get_serializer(note).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        serializer = CancellationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        note = cancel_delivery_note(self.get_object(), serializer.validated_data["reason"], request.user)
        return Response(self.get_serializer(note).data)

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        from .delivery_pdf import build_delivery_note_pdf
        note = self.get_object()
        if not any(not line.deliver_later for line in note.lines.all()):
            raise serializers.ValidationError("Choose at least one item to deliver now before downloading the PDF.")
        response = HttpResponse(build_delivery_note_pdf(note), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{note.delivery_number}.pdf"'
        response["Cache-Control"] = "private, no-store"
        return response


class DeliveryOrderViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsQuotationStaff]
    pagination_class = DeliveryPagination
    queryset = Quotation.objects.select_related("company").prefetch_related("lines", "lpos", "delivery_notes__lines")

    def get_queryset(self):
        return super().get_queryset().filter(
            Q(outcome_status__in=[Quotation.OUTCOME_WON, Quotation.OUTCOME_PARTIAL])
            | Q(lines__outcome_status__in=[QuotationLine.OUTCOME_ACCEPTED, QuotationLine.OUTCOME_QUANTITY_CHANGED], lines__accepted_quantity__gt=0)
            | Q(lpos__status="confirmed") | Q(delivery_notes__isnull=False)
        ).distinct()

    @staticmethod
    def _progress(quote):
        totals = defaultdict(lambda: {"issued": Decimal("0"), "delivered": Decimal("0")})
        for note in quote.delivery_notes.all():
            for line in note.lines.all():
                if note.status == DeliveryNote.STATUS_ISSUED:
                    totals[line.quotation_line_id]["issued"] += line.quantity
                elif note.status == DeliveryNote.STATUS_DELIVERED:
                    totals[line.quotation_line_id]["delivered"] += line.received_quantity or Decimal("0")
        return order_progress(quote, allocations=totals)

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        term = request.query_params.get("search", "")[:200]
        if term:
            queryset = queryset.filter(Q(company__name__icontains=term) | Q(quotation_number__icontains=term) | Q(lpos__lpo_number__icontains=term)).distinct()
        rows = [self._progress(quote) for quote in queryset]
        summary = dict(Counter(row["delivery_status"] for row in rows))
        state = request.query_params.get("status")
        if state:
            rows = [row for row in rows if row["delivery_status"] == state]
        page = self.paginate_queryset(rows)
        response = self.get_paginated_response(page)
        response.data["summary"] = summary
        return response

    def retrieve(self, request, *args, **kwargs):
        return Response(self._progress(self.get_object()))
