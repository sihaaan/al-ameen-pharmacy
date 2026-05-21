from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from api.serializers import ProductListSerializer

from .models import (
    Company,
    CompanyContact,
    CompanyPriceHistory,
    Inquiry,
    InquiryLine,
    Quotation,
    QuotationAuditLog,
    QuotationLine,
    QuoteItem,
)


class CompanyContactSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = CompanyContact
        fields = [
            "id",
            "company",
            "company_name",
            "name",
            "email",
            "phone",
            "role",
            "is_primary",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "company_name", "created_at", "updated_at"]


class CompanySerializer(serializers.ModelSerializer):
    contacts = CompanyContactSerializer(many=True, read_only=True)

    class Meta:
        model = Company
        fields = [
            "id",
            "name",
            "normalized_name",
            "email",
            "phone",
            "billing_address",
            "trn",
            "notes",
            "is_active",
            "contacts",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "normalized_name", "contacts", "created_at", "updated_at"]


class QuoteItemSerializer(serializers.ModelSerializer):
    product_detail = ProductListSerializer(source="product", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True, allow_null=True)

    class Meta:
        model = QuoteItem
        fields = [
            "id",
            "product",
            "product_name",
            "product_detail",
            "name",
            "normalized_name",
            "internal_code",
            "brand_text",
            "generic_name",
            "strength",
            "dosage_form",
            "pack_size",
            "unit",
            "notes",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "normalized_name", "product_name", "product_detail", "created_at", "updated_at"]


class InquiryLineSerializer(serializers.ModelSerializer):
    matched_quote_item_name = serializers.CharField(source="matched_quote_item.name", read_only=True, allow_null=True)

    class Meta:
        model = InquiryLine
        fields = [
            "id",
            "inquiry",
            "raw_name",
            "normalized_name",
            "quantity",
            "unit",
            "notes",
            "matched_quote_item",
            "matched_quote_item_name",
            "match_status",
            "sort_order",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "normalized_name", "matched_quote_item_name", "created_at", "updated_at"]
        extra_kwargs = {
            "inquiry": {"required": False},
        }


class InquirySerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)
    contact_name = serializers.CharField(source="contact.name", read_only=True, allow_null=True)
    created_by_username = serializers.CharField(source="created_by.username", read_only=True, allow_null=True)
    lines = InquiryLineSerializer(many=True, required=False)

    class Meta:
        model = Inquiry
        fields = [
            "id",
            "company",
            "company_name",
            "contact",
            "contact_name",
            "source",
            "subject",
            "original_text",
            "received_at",
            "status",
            "created_by",
            "created_by_username",
            "lines",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "source",
            "created_by",
            "created_by_username",
            "company_name",
            "contact_name",
            "created_at",
            "updated_at",
        ]

    def create(self, validated_data):
        lines_data = validated_data.pop("lines", [])
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            validated_data["created_by"] = request.user
        inquiry = Inquiry.objects.create(**validated_data)
        for index, line_data in enumerate(lines_data):
            line_data.pop("inquiry", None)
            sort_order = line_data.pop("sort_order", index)
            InquiryLine.objects.create(inquiry=inquiry, sort_order=sort_order, **line_data)
        return inquiry


class QuotationLineSerializer(serializers.ModelSerializer):
    quote_item_name = serializers.CharField(source="quote_item.name", read_only=True, allow_null=True)
    inquiry_line_raw_name = serializers.CharField(source="inquiry_line.raw_name", read_only=True, allow_null=True)

    class Meta:
        model = QuotationLine
        fields = [
            "id",
            "quotation",
            "inquiry_line",
            "inquiry_line_raw_name",
            "quote_item",
            "quote_item_name",
            "item_name_snapshot",
            "description",
            "quantity",
            "unit",
            "unit_price",
            "vat_rate",
            "line_subtotal",
            "vat_amount",
            "line_total",
            "match_status",
            "sort_order",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "inquiry_line_raw_name",
            "quote_item_name",
            "line_subtotal",
            "vat_amount",
            "line_total",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        quote_item = attrs.get("quote_item") or getattr(self.instance, "quote_item", None)
        item_name = attrs.get("item_name_snapshot") or getattr(self.instance, "item_name_snapshot", "")
        if not item_name and quote_item:
            attrs["item_name_snapshot"] = quote_item.name
        if not attrs.get("item_name_snapshot") and not item_name:
            raise serializers.ValidationError({"item_name_snapshot": "This field is required."})
        return attrs


class QuotationSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)
    contact_name = serializers.CharField(source="contact.name", read_only=True, allow_null=True)
    inquiry_subject = serializers.CharField(source="inquiry.subject", read_only=True, allow_null=True)
    created_by_username = serializers.CharField(source="created_by.username", read_only=True, allow_null=True)
    finalized_by_username = serializers.CharField(source="finalized_by.username", read_only=True, allow_null=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    lines = QuotationLineSerializer(many=True, read_only=True)

    class Meta:
        model = Quotation
        fields = [
            "id",
            "company",
            "company_name",
            "contact",
            "contact_name",
            "inquiry",
            "inquiry_subject",
            "quotation_number",
            "status",
            "status_display",
            "version",
            "parent",
            "valid_until",
            "currency",
            "subtotal",
            "vat_total",
            "total",
            "notes",
            "internal_notes",
            "created_by",
            "created_by_username",
            "finalized_by",
            "finalized_by_username",
            "finalized_at",
            "sent_at",
            "lines",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "company_name",
            "contact_name",
            "inquiry_subject",
            "quotation_number",
            "subtotal",
            "vat_total",
            "total",
            "created_by",
            "created_by_username",
            "finalized_by",
            "finalized_by_username",
            "finalized_at",
            "sent_at",
            "lines",
            "created_at",
            "updated_at",
        ]

    def create(self, validated_data):
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            validated_data["created_by"] = request.user
        return super().create(validated_data)


class CompanyPriceHistorySerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)
    quote_item_name = serializers.CharField(source="quote_item.name", read_only=True)
    quotation_number = serializers.CharField(source="quotation.quotation_number", read_only=True)
    created_by_username = serializers.CharField(source="created_by.username", read_only=True, allow_null=True)

    class Meta:
        model = CompanyPriceHistory
        fields = [
            "id",
            "company",
            "company_name",
            "quote_item",
            "quote_item_name",
            "quotation",
            "quotation_number",
            "quotation_line",
            "unit_price",
            "currency",
            "quantity",
            "unit",
            "quoted_at",
            "created_by",
            "created_by_username",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "company",
            "company_name",
            "quote_item",
            "quote_item_name",
            "quotation",
            "quotation_number",
            "quotation_line",
            "unit_price",
            "currency",
            "quantity",
            "unit",
            "quoted_at",
            "created_by",
            "created_by_username",
            "created_at",
        ]


class QuotationAuditLogSerializer(serializers.ModelSerializer):
    actor_username = serializers.CharField(source="actor.username", read_only=True, allow_null=True)
    company_name = serializers.CharField(source="company.name", read_only=True, allow_null=True)
    quotation_number = serializers.CharField(source="quotation.quotation_number", read_only=True, allow_null=True)

    class Meta:
        model = QuotationAuditLog
        fields = [
            "id",
            "actor",
            "actor_username",
            "action",
            "target_type",
            "target_id",
            "company",
            "company_name",
            "quotation",
            "quotation_number",
            "message",
            "changes",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "actor",
            "actor_username",
            "action",
            "target_type",
            "target_id",
            "company",
            "company_name",
            "quotation",
            "quotation_number",
            "message",
            "changes",
            "created_at",
        ]


def serializer_error_from_django_validation(exc):
    if hasattr(exc, "message_dict"):
        return exc.message_dict
    if isinstance(exc, DjangoValidationError):
        return {"detail": exc.messages}
    return {"detail": str(exc)}
