import re

from django.conf import settings
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
    QuotationSettings,
    QuoteItem,
)


SAFE_BRANDING_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
SAFE_BRANDING_IMAGE_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp"}


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


class QuotationSettingsSerializer(serializers.ModelSerializer):
    logo_url = serializers.SerializerMethodField()
    signature_image_url = serializers.SerializerMethodField()
    stamp_image_url = serializers.SerializerMethodField()
    clear_logo = serializers.BooleanField(write_only=True, required=False, default=False)
    clear_signature_image = serializers.BooleanField(write_only=True, required=False, default=False)
    clear_stamp_image = serializers.BooleanField(write_only=True, required=False, default=False)

    class Meta:
        model = QuotationSettings
        fields = [
            "id",
            "company_name",
            "company_name_ar",
            "address",
            "phone",
            "email",
            "trn",
            "license_number",
            "logo",
            "logo_url",
            "signature_image",
            "signature_image_url",
            "stamp_image",
            "stamp_image_url",
            "clear_logo",
            "clear_signature_image",
            "clear_stamp_image",
            "logo_layout",
            "footer_note",
            "default_terms",
            "payment_terms",
            "validity_days",
            "prepared_by_default",
            "signature_label",
            "stamp_label",
            "pdf_template_style",
            "primary_color",
            "accent_color",
            "show_arabic_name",
            "show_trn",
            "show_license_number",
            "show_signature_area",
            "show_stamp_area",
            "updated_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "logo_url",
            "signature_image_url",
            "stamp_image_url",
            "updated_by",
            "created_at",
            "updated_at",
        ]

    def get_logo_url(self, obj):
        return self._get_image_url(obj.logo)

    def get_signature_image_url(self, obj):
        return self._get_image_url(obj.signature_image)

    def get_stamp_image_url(self, obj):
        return self._get_image_url(obj.stamp_image)

    def _get_image_url(self, image):
        if not image:
            return ""
        try:
            return image.url
        except ValueError:
            return ""

    def validate_logo(self, logo):
        return self._validate_branding_image(logo, "Logo")

    def validate_signature_image(self, image):
        return self._validate_branding_image(image, "Signature image")

    def validate_stamp_image(self, image):
        return self._validate_branding_image(image, "Stamp image")

    def _validate_branding_image(self, image, label):
        if not image:
            return image
        max_bytes = int(
            getattr(
                settings,
                "QUOTATION_BRANDING_IMAGE_MAX_UPLOAD_BYTES",
                getattr(settings, "QUOTATION_LOGO_MAX_UPLOAD_BYTES", 2 * 1024 * 1024),
            )
        )
        if image.size > max_bytes:
            raise serializers.ValidationError(f"{label} file is too large. Maximum size is {max_bytes // (1024 * 1024)} MB.")
        extension = image.name.rsplit(".", 1)[-1].lower() if "." in image.name else ""
        if extension not in SAFE_BRANDING_IMAGE_EXTENSIONS:
            raise serializers.ValidationError(f"Unsupported {label.lower()} type. Upload png, jpg, jpeg, or webp only.")
        if getattr(image, "content_type", "") and image.content_type not in SAFE_BRANDING_IMAGE_CONTENT_TYPES:
            raise serializers.ValidationError(f"Unsupported {label.lower()} content type.")
        header = image.read(512)
        image.seek(0)
        if extension == "webp":
            if not (header.startswith(b"RIFF") and b"WEBP" in header[:16]):
                raise serializers.ValidationError("Uploaded file does not look like a valid WebP image.")
        elif extension == "png" and not header.startswith(b"\x89PNG\r\n\x1a\n"):
            raise serializers.ValidationError("Uploaded file does not look like a valid PNG image.")
        elif extension in {"jpg", "jpeg"} and not header.startswith(b"\xff\xd8\xff"):
            raise serializers.ValidationError("Uploaded file does not look like a valid image.")
        return image

    def validate_primary_color(self, value):
        return self._validate_hex_color(value, "primary_color")

    def validate_accent_color(self, value):
        return self._validate_hex_color(value, "accent_color")

    def _validate_hex_color(self, value, field_name):
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value or ""):
            raise serializers.ValidationError(f"{field_name} must be a 6-digit hex color such as #0F766E.")
        return value

    def validate_validity_days(self, value):
        if value < 1 or value > 365:
            raise serializers.ValidationError("Validity days must be between 1 and 365.")
        return value

    def update(self, instance, validated_data):
        clear_map = {
            "clear_logo": "logo",
            "clear_signature_image": "signature_image",
            "clear_stamp_image": "stamp_image",
        }
        for clear_field, image_field in clear_map.items():
            should_clear = validated_data.pop(clear_field, False)
            if should_clear:
                image = getattr(instance, image_field)
                if image:
                    try:
                        image.delete(save=False)
                    except Exception:
                        pass
                setattr(instance, image_field, None)
        return super().update(instance, validated_data)


class InquiryLineSerializer(serializers.ModelSerializer):
    matched_quote_item_name = serializers.CharField(source="matched_quote_item.name", read_only=True, allow_null=True)

    class Meta:
        model = InquiryLine
        fields = [
            "id",
            "inquiry",
            "raw_name",
            "raw_line",
            "normalized_name",
            "quantity",
            "unit",
            "notes",
            "matched_quote_item",
            "matched_quote_item_name",
            "match_status",
            "parse_status",
            "parse_confidence",
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
    quotation_id = serializers.SerializerMethodField()
    quotation_number = serializers.SerializerMethodField()

    class Meta:
        model = Inquiry
        fields = [
            "id",
            "company",
            "company_name",
            "contact",
            "contact_name",
            "source",
            "source_type",
            "source_filename",
            "source_mime_type",
            "source_sha256",
            "parse_method",
            "parse_meta",
            "subject",
            "original_text",
            "received_at",
            "status",
            "created_by",
            "created_by_username",
            "lines",
            "quotation_id",
            "quotation_number",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "source",
            "source_type",
            "source_filename",
            "source_mime_type",
            "source_sha256",
            "parse_method",
            "parse_meta",
            "created_by",
            "created_by_username",
            "quotation_id",
            "quotation_number",
            "company_name",
            "contact_name",
            "created_at",
            "updated_at",
        ]

    def get_quotation_id(self, obj):
        quotation = self._get_existing_quotation(obj)
        return quotation.id if quotation else None

    def get_quotation_number(self, obj):
        quotation = self._get_existing_quotation(obj)
        return quotation.quotation_number if quotation else ""

    def _get_existing_quotation(self, obj):
        quotations = getattr(obj, "_prefetched_objects_cache", {}).get("quotations")
        if quotations is not None:
            return sorted(quotations, key=lambda quote: (quote.version, quote.created_at, quote.pk), reverse=True)[0] if quotations else None
        return obj.quotations.order_by("-version", "-created_at", "-pk").first()

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


class ImportedInquiryLineSerializer(serializers.Serializer):
    raw_name = serializers.CharField(max_length=255)
    raw_line = serializers.CharField(required=False, allow_blank=True)
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3, required=False, allow_null=True)
    unit = serializers.CharField(max_length=50, required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    matched_quote_item = serializers.PrimaryKeyRelatedField(
        queryset=QuoteItem.objects.all(),
        required=False,
        allow_null=True,
    )
    match_status = serializers.ChoiceField(
        choices=InquiryLine.MATCH_STATUS_CHOICES,
        default=InquiryLine.MATCH_UNRESOLVED,
    )
    parse_status = serializers.ChoiceField(
        choices=InquiryLine.PARSE_STATUS_CHOICES,
        default=InquiryLine.PARSE_NEEDS_REVIEW,
    )
    parse_confidence = serializers.FloatField(min_value=0, max_value=1, required=False, default=0.0)


class ImportedInquiryCreateSerializer(serializers.Serializer):
    company = serializers.PrimaryKeyRelatedField(queryset=Company.objects.all())
    contact = serializers.PrimaryKeyRelatedField(
        queryset=CompanyContact.objects.all(),
        required=False,
        allow_null=True,
    )
    subject = serializers.CharField(max_length=255, required=False, allow_blank=True)
    original_text = serializers.CharField(required=False, allow_blank=True)
    source_type = serializers.ChoiceField(
        choices=[
            Inquiry.SOURCE_TYPE_PASTED_TEXT,
            Inquiry.SOURCE_TYPE_EXCEL,
            Inquiry.SOURCE_TYPE_PDF,
        ]
    )
    source_filename = serializers.CharField(max_length=255, required=False, allow_blank=True)
    source_mime_type = serializers.CharField(max_length=120, required=False, allow_blank=True)
    source_sha256 = serializers.CharField(max_length=64, required=False, allow_blank=True)
    parse_method = serializers.CharField(max_length=80, required=False, allow_blank=True)
    parse_meta = serializers.JSONField(required=False, default=dict)
    lines = ImportedInquiryLineSerializer(many=True, allow_empty=False)

    def validate(self, attrs):
        contact = attrs.get("contact")
        company = attrs.get("company")
        if contact and company and contact.company_id != company.id:
            raise serializers.ValidationError({"contact": "Contact must belong to the selected company."})
        return attrs


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
