import re
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from api.models import Product
from api.serializers import ProductListSerializer

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
    QuoteItem,
    ProductAlias,
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
    """Product-backed item serializer for the staff quotation item catalog."""
    brand_name = serializers.CharField(source="brand.name", read_only=True, allow_null=True)
    category_name = serializers.CharField(source="category.name", read_only=True, allow_null=True)
    unit = serializers.CharField(source="pack_size", read_only=True)
    is_active = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "slug",
            "sku",
            "barcode",
            "brand",
            "brand_name",
            "category",
            "category_name",
            "short_description",
            "detailed_description",
            "price",
            "stock_quantity",
            "dosage",
            "pack_size",
            "unit",
            "active_ingredient",
            "status",
            "show_price",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "slug", "brand_name", "category_name", "is_active", "created_at", "updated_at"]
        extra_kwargs = {
            "price": {"required": False},
            "stock_quantity": {"required": False},
            "status": {"required": False},
            "show_price": {"required": False},
        }

    def get_is_active(self, obj):
        return obj.status != "archived"

    def validate(self, attrs):
        if self.instance is None:
            attrs.setdefault("price", Decimal("0.01"))
            attrs.setdefault("stock_quantity", 0)
            attrs.setdefault("status", "draft")
            attrs.setdefault("show_price", False)
        return attrs


class ProductAliasSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True, allow_null=True)
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = ProductAlias
        fields = [
            "id",
            "company",
            "company_name",
            "product",
            "product_name",
            "alias",
            "normalized_alias",
            "notes",
            "is_active",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "normalized_alias", "company_name", "product_name", "created_by", "created_at", "updated_at"]


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
    matched_product_name = serializers.CharField(source="matched_product.name", read_only=True, allow_null=True)

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
            "matched_product",
            "matched_product_name",
            "match_reason",
            "match_status",
            "parse_status",
            "parse_confidence",
            "sort_order",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "normalized_name", "matched_quote_item_name", "matched_product_name", "created_at", "updated_at"]
        extra_kwargs = {
            "inquiry": {"required": False},
        }

    def update(self, instance, validated_data):
        if "matched_product" in validated_data and validated_data.get("matched_product") != instance.matched_product:
            validated_data.setdefault("match_reason", "Selected manually by staff.")
        return super().update(instance, validated_data)


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
            "source_file_ref",
            "source_file_size",
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
            "source_file_ref",
            "source_file_size",
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
    matched_product = serializers.PrimaryKeyRelatedField(
        queryset=Product.objects.all(),
        required=False,
        allow_null=True,
    )
    match_reason = serializers.CharField(required=False, allow_blank=True)
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
    source_file_ref = serializers.CharField(max_length=500, required=False, allow_blank=True)
    source_file_size = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    parse_method = serializers.CharField(max_length=80, required=False, allow_blank=True)
    parse_meta = serializers.JSONField(required=False, default=dict)
    lines = ImportedInquiryLineSerializer(many=True, allow_empty=False)

    def validate_source_file_ref(self, value):
        if value and (".." in value.replace("\\", "/").split("/") or value.startswith(("/", "\\"))):
            raise serializers.ValidationError("Invalid private source file reference.")
        return value

    def validate(self, attrs):
        contact = attrs.get("contact")
        company = attrs.get("company")
        if contact and company and contact.company_id != company.id:
            raise serializers.ValidationError({"contact": "Contact must belong to the selected company."})
        return attrs


class HistoricalPriceImportLineSerializer(serializers.ModelSerializer):
    quote_item_name = serializers.CharField(source="quote_item.name", read_only=True, allow_null=True)
    product_name = serializers.CharField(source="product.name", read_only=True, allow_null=True)

    class Meta:
        model = HistoricalPriceImportLine
        fields = [
            "id",
            "historical_import",
            "quote_item",
            "quote_item_name",
            "product",
            "product_name",
            "match_reason",
            "raw_line",
            "item_name",
            "quantity",
            "unit",
            "unit_price",
            "amount",
            "vat_amount",
            "vat_rate",
            "line_total",
            "serial_no",
            "source_page",
            "source_row",
            "parse_confidence",
            "status",
            "duplicate_reason",
            "notes",
            "sort_order",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "historical_import",
            "quote_item_name",
            "product_name",
            "raw_line",
            "serial_no",
            "source_page",
            "source_row",
            "parse_confidence",
            "duplicate_reason",
            "sort_order",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        status_value = attrs.get("status") or getattr(self.instance, "status", "")
        product = attrs.get("product") if "product" in attrs else getattr(self.instance, "product", None)
        quote_item = attrs.get("quote_item") if "quote_item" in attrs else getattr(self.instance, "quote_item", None)
        quantity = attrs.get("quantity") if "quantity" in attrs else getattr(self.instance, "quantity", None)
        unit_price = attrs.get("unit_price") if "unit_price" in attrs else getattr(self.instance, "unit_price", None)
        if status_value == HistoricalPriceImportLine.STATUS_READY:
            errors = {}
            historical_import = getattr(self.instance, "historical_import", None)
            if historical_import and not historical_import.company_id:
                errors["historical_import"] = "Select the company before marking this row ready."
            if historical_import and not historical_import.document_date:
                errors["document_date"] = "Enter the quotation date before marking this row ready."
            if not product and not quote_item:
                errors["product"] = "Select a product/item before marking this row ready."
            if quantity is None or quantity <= 0:
                errors["quantity"] = "Enter a valid quantity before marking this row ready."
            if unit_price is None or unit_price < 0:
                errors["unit_price"] = "Enter a valid unit price before marking this row ready."
            if errors:
                raise serializers.ValidationError(errors)
        return attrs

    def update(self, instance, validated_data):
        if "product" in validated_data and validated_data.get("product") != instance.product:
            validated_data.setdefault("match_reason", "Selected manually by staff.")
        return super().update(instance, validated_data)


class HistoricalPriceImportSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True, allow_null=True)
    created_by_username = serializers.CharField(source="created_by.username", read_only=True, allow_null=True)
    committed_by_username = serializers.CharField(source="committed_by.username", read_only=True, allow_null=True)
    created_quotation_number = serializers.CharField(source="created_quotation.quotation_number", read_only=True, allow_null=True)
    lines = HistoricalPriceImportLineSerializer(many=True, read_only=True)

    class Meta:
        model = HistoricalPriceImport
        fields = [
            "id",
            "company",
            "company_name",
            "suggested_company_name",
            "source_type",
            "source_filename",
            "source_mime_type",
            "source_sha256",
            "source_file_ref",
            "source_file_size",
            "parse_method",
            "parse_meta",
            "document_number",
            "document_date",
            "currency",
            "subtotal",
            "vat_total",
            "total",
            "status",
            "created_quotation",
            "created_quotation_number",
            "created_by",
            "created_by_username",
            "committed_by",
            "committed_by_username",
            "committed_at",
            "lines",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "source_type",
            "source_filename",
            "source_mime_type",
            "source_sha256",
            "source_file_ref",
            "source_file_size",
            "parse_method",
            "parse_meta",
            "status",
            "created_quotation",
            "created_quotation_number",
            "created_by",
            "created_by_username",
            "committed_by",
            "committed_by_username",
            "committed_at",
            "lines",
            "created_at",
            "updated_at",
        ]

    def validate_source_file_ref(self, value):
        if value and (".." in value.replace("\\", "/").split("/") or value.startswith(("/", "\\"))):
            raise serializers.ValidationError("Invalid private source file reference.")
        return value


class QuotationLineSerializer(serializers.ModelSerializer):
    quote_item_name = serializers.CharField(source="quote_item.name", read_only=True, allow_null=True)
    product_name = serializers.CharField(source="product.name", read_only=True, allow_null=True)
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
            "product",
            "product_name",
            "match_reason",
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
            "product_name",
            "line_subtotal",
            "vat_amount",
            "line_total",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        product = attrs.get("product") or getattr(self.instance, "product", None)
        quote_item = attrs.get("quote_item") or getattr(self.instance, "quote_item", None)
        item_name = attrs.get("item_name_snapshot") or getattr(self.instance, "item_name_snapshot", "")
        if not item_name and product:
            attrs["item_name_snapshot"] = product.name
        elif not item_name and quote_item:
            attrs["item_name_snapshot"] = quote_item.name
        if not attrs.get("item_name_snapshot") and not item_name:
            raise serializers.ValidationError({"item_name_snapshot": "This field is required."})
        return attrs

    def update(self, instance, validated_data):
        if "product" in validated_data and validated_data.get("product") != instance.product:
            validated_data.setdefault("match_reason", "Selected manually by staff.")
        return super().update(instance, validated_data)


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
            "is_historical_import",
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
            "is_historical_import",
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
    quote_item_name = serializers.CharField(source="quote_item.name", read_only=True, allow_null=True)
    product_name = serializers.CharField(source="product.name", read_only=True, allow_null=True)
    quotation_number = serializers.CharField(source="quotation.quotation_number", read_only=True)
    quotation_is_historical_import = serializers.BooleanField(source="quotation.is_historical_import", read_only=True)
    created_by_username = serializers.CharField(source="created_by.username", read_only=True, allow_null=True)

    class Meta:
        model = CompanyPriceHistory
        fields = [
            "id",
            "company",
            "company_name",
            "quote_item",
            "quote_item_name",
            "product",
            "product_name",
            "quotation",
            "quotation_number",
            "quotation_is_historical_import",
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
            "product",
            "product_name",
            "quotation",
            "quotation_number",
            "quotation_is_historical_import",
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
