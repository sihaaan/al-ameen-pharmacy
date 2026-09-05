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

from .delivery import (
    accepted_quantity, cancel_delivery_note, confirm_delivery_note, issue_delivery_note,
    lock_note, order_progress,
)
from .models import Company, DeliveryNote, DeliveryNoteLine, Quotation, QuotationAuditLog, QuotationLine
from .permissions import IsQuotationStaff
from .services import audit_log


class DeliveryPagination(PageNumberPagination):
    page_size = 50


class DeliveryLineSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(max_length=2000, required=False, allow_blank=True)

    class Meta:
        model = DeliveryNoteLine
        fields = ["id", "quotation_line", "item_name", "description", "unit", "quantity", "received_quantity", "sort_order"]
        read_only_fields = ["id", "received_quantity"]
        validators = []  # The parent validates membership and duplicates together.


class DeliveryNoteSerializer(serializers.ModelSerializer):
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
            "quotation", "quotation_number", "delivery_date", "lpo_number", "invoice_number",
            "customer_name", "customer_address", "customer_trn", "delivery_address", "attention",
            "contact_phone", "notes", "lines", "received_by", "received_date", "receipt_reference",
            "receipt_notes", "cancellation_reason", "created_by_username", "issued_at", "confirmed_at",
            "cancelled_at", "created_at", "updated_at",
        ]
        read_only_fields = [
            "delivery_number", "status", "customer_name", "customer_address", "customer_trn",
            "received_by", "received_date", "receipt_reference", "receipt_notes", "cancellation_reason",
            "issued_at", "confirmed_at", "cancelled_at", "created_at", "updated_at",
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
        elif not company:
            raise serializers.ValidationError({"company": "Select a customer."})
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

    @transaction.atomic
    def create(self, validated_data):
        rows = validated_data.pop("lines", [])
        quote = validated_data.get("quotation")
        if quote:
            quote = Quotation.objects.select_for_update().get(pk=quote.pk)
            if quote.status not in {Quotation.STATUS_FINALIZED, Quotation.STATUS_SENT}:
                raise serializers.ValidationError("This quotation is no longer available for delivery.")
        company = validated_data["company"]
        validated_data.update(customer_name=company.name, customer_address=company.billing_address, customer_trn=company.trn)
        if quote:
            lpo = quote.lpos.filter(status="confirmed").order_by("-id").first()
            validated_data.setdefault("lpo_number", lpo.lpo_number if lpo else "")
            validated_data.setdefault("attention", quote.contact.name if quote.contact else "")
            validated_data.setdefault("contact_phone", quote.contact.phone if quote.contact else "")
        validated_data.setdefault("delivery_address", company.billing_address)
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


class DeliveryNoteViewSet(viewsets.ModelViewSet):
    permission_classes = [IsQuotationStaff]
    serializer_class = DeliveryNoteSerializer
    pagination_class = DeliveryPagination
    http_method_names = ["get", "post", "patch", "head", "options"]
    queryset = DeliveryNote.objects.select_related("company", "quotation", "created_by").prefetch_related("lines")

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
            queryset = queryset.filter(Q(delivery_number__icontains=term) | Q(customer_name__icontains=term) | Q(lpo_number__icontains=term) | Q(invoice_number__icontains=term))
        return queryset

    def handle_exception(self, exc):
        if isinstance(exc, DjangoValidationError):
            exc = serializers.ValidationError({"detail": " ".join(exc.messages)})
        return super().handle_exception(exc)

    @action(detail=True, methods=["post"])
    def issue(self, request, pk=None):
        note = issue_delivery_note(self.get_object(), request.user)
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
        if not note.lines.all():
            raise serializers.ValidationError("Add at least one delivery item before downloading the PDF.")
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
