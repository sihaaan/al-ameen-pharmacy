import re
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from api.models import Product, ProductImage
from api.serializers import ProductListSerializer

from .company_matching import find_similar_companies
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
    ProformaInvoice,
    ProformaInvoiceLine,
    Quotation,
    QuotationAuditLog,
    QuotationLine,
    QuotationLPO,
    QuotationOutcomePOImport,
    QuotationSettings,
    UserQuotationProfile,
    QuoteItem,
    ProductAlias,
    normalize_label,
)


SAFE_BRANDING_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
SAFE_BRANDING_IMAGE_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp"}


def validate_branding_image_upload(image, label):
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
            "department",
            "is_primary",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "company_name", "created_at", "updated_at"]


class CompanySerializer(serializers.ModelSerializer):
    contacts = CompanyContactSerializer(many=True, read_only=True)
    allow_similar = serializers.BooleanField(write_only=True, required=False, default=False)

    class Meta:
        model = Company
        fields = [
            "id",
            "name",
            "allow_similar",
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

    def validate(self, attrs):
        attrs = super().validate(attrs)
        name = attrs.get("name") or getattr(self.instance, "name", "")
        allow_similar = attrs.pop("allow_similar", False)
        if not name:
            return attrs

        queryset = Company.objects.all()
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)

        normalized_name = normalize_label(name)
        exact_match = queryset.filter(normalized_name=normalized_name).first()
        if exact_match:
            raise serializers.ValidationError(
                {
                    "name": [f"Company already exists as {exact_match.name}."],
                    "similar_companies": [
                        {
                            "id": exact_match.id,
                            "name": exact_match.name,
                            "email": exact_match.email,
                            "phone": exact_match.phone,
                            "trn": exact_match.trn,
                            "score": 100,
                            "reason": "Exact company name match.",
                            "is_active": exact_match.is_active,
                        }
                    ],
                }
            )

        similar_companies = find_similar_companies(name, queryset=queryset, threshold=84)
        high_confidence_duplicate = next((company for company in similar_companies if company["score"] >= 92), None)
        if high_confidence_duplicate and not allow_similar:
            raise serializers.ValidationError(
                {
                    "name": [
                        (
                            f"This looks very similar to {high_confidence_duplicate['name']}. "
                            "Select the existing company or confirm that this is a different company."
                        )
                    ],
                    "similar_companies": similar_companies,
                    "requires_confirmation": True,
                }
            )
        return attrs


class CompanyListSerializer(serializers.ModelSerializer):
    contact_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Company
        fields = [
            "id",
            "name",
            "email",
            "phone",
            "trn",
            "is_active",
            "contact_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class GmailOAuthConnectionSerializer(serializers.ModelSerializer):
    is_connected = serializers.SerializerMethodField()

    class Meta:
        model = GmailOAuthConnection
        fields = [
            "id",
            "email",
            "google_subject",
            "status",
            "is_connected",
            "last_error",
            "scopes",
            "token_expiry",
            "connected_at",
            "disconnected_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_is_connected(self, obj):
        return obj.status == GmailOAuthConnection.STATUS_CONNECTED


class ContractIntelligenceItemSerializer(serializers.ModelSerializer):
    source_subject = serializers.CharField(source="source.subject", read_only=True, allow_null=True)
    source_sender = serializers.CharField(source="source.sender", read_only=True, allow_null=True)
    source_sent_at = serializers.DateTimeField(source="source.sent_at", read_only=True, allow_null=True)
    product_name = serializers.CharField(source="product.name", read_only=True, allow_null=True)

    class Meta:
        model = ContractIntelligenceItem
        fields = [
            "id",
            "run",
            "source",
            "source_subject",
            "source_sender",
            "source_sent_at",
            "product",
            "product_name",
            "original_item_name",
            "normalized_item_name",
            "suggested_item_name",
            "quantity",
            "unit",
            "unit_price",
            "currency",
            "requested_date",
            "project",
            "contact_text",
            "source_text",
            "source_filename",
            "source_page",
            "confidence",
            "ai_reason",
            "status",
            "review_notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "run",
            "source",
            "source_subject",
            "source_sender",
            "source_sent_at",
            "product_name",
            "normalized_item_name",
            "created_at",
            "updated_at",
        ]


class ContractIntelligenceSourceSerializer(serializers.ModelSerializer):
    item_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = ContractIntelligenceSource
        fields = [
            "id",
            "run",
            "gmail_message_id",
            "gmail_thread_id",
            "subject",
            "sender",
            "recipients",
            "sent_at",
            "snippet",
            "source_sha256",
            "attachments",
            "classification",
            "confidence",
            "status",
            "error",
            "item_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ContractIntelligenceRunSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True, allow_null=True)
    created_by_username = serializers.CharField(source="created_by.username", read_only=True, allow_null=True)
    source_count = serializers.SerializerMethodField()
    item_count = serializers.SerializerMethodField()

    class Meta:
        model = ContractIntelligenceRun
        fields = [
            "id",
            "company",
            "company_name",
            "target_company_name",
            "gmail_query",
            "date_from",
            "date_to",
            "max_messages",
            "include_attachments",
            "status",
            "ai_status",
            "summary",
            "warnings",
            "created_by",
            "created_by_username",
            "source_count",
            "item_count",
            "started_at",
            "completed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "company_name",
            "status",
            "ai_status",
            "summary",
            "warnings",
            "created_by",
            "created_by_username",
            "source_count",
            "item_count",
            "started_at",
            "completed_at",
            "created_at",
            "updated_at",
        ]

    def get_source_count(self, obj):
        return obj.sources.count() if obj.pk else 0

    def get_item_count(self, obj):
        return obj.items.count() if obj.pk else 0

    def validate(self, attrs):
        attrs = super().validate(attrs)
        company = attrs.get("company") or getattr(self.instance, "company", None)
        target_name = attrs.get("target_company_name") or getattr(self.instance, "target_company_name", "")
        if company and not target_name:
            attrs["target_company_name"] = company.name
        if not attrs.get("target_company_name") and not target_name:
            raise serializers.ValidationError({"target_company_name": "Enter the customer/company to search for."})
        max_messages = attrs.get("max_messages")
        if max_messages is not None:
            attrs["max_messages"] = min(max(int(max_messages), 1), 200)
        return attrs


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


class QuoteItemListSerializer(serializers.ModelSerializer):
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
            "brand_name",
            "category_name",
            "price",
            "dosage",
            "pack_size",
            "unit",
            "active_ingredient",
            "status",
            "is_active",
        ]
        read_only_fields = fields

    def get_is_active(self, obj):
        return obj.status != "archived"


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
    ai_available = serializers.SerializerMethodField()
    ai_unavailable_reason = serializers.SerializerMethodField()
    ai_provider = serializers.SerializerMethodField()
    ai_text_model = serializers.SerializerMethodField()
    ai_vision_model = serializers.SerializerMethodField()
    ai_global_enabled = serializers.SerializerMethodField()
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
            "ai_parsing_enabled",
            "ai_auto_cleanup_enabled",
            "ai_pdf_vision_enabled",
            "ai_available",
            "ai_unavailable_reason",
            "ai_provider",
            "ai_text_model",
            "ai_vision_model",
            "ai_global_enabled",
            "updated_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "logo_url",
            "signature_image_url",
            "stamp_image_url",
            "ai_available",
            "ai_unavailable_reason",
            "ai_provider",
            "ai_text_model",
            "ai_vision_model",
            "ai_global_enabled",
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

    def _ai_availability(self):
        from .ai_parsing import get_ai_parse_availability

        return get_ai_parse_availability()

    def get_ai_available(self, obj):
        return self._ai_availability()["available"]

    def get_ai_unavailable_reason(self, obj):
        return self._ai_availability()["reason"]

    def get_ai_provider(self, obj):
        return self._ai_availability()["provider"]

    def get_ai_text_model(self, obj):
        return self._ai_availability()["text_model"]

    def get_ai_vision_model(self, obj):
        return self._ai_availability()["vision_model"]

    def get_ai_global_enabled(self, obj):
        return self._ai_availability()["global_enabled"]

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
        return validate_branding_image_upload(image, label)

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


class UserQuotationProfileSerializer(serializers.ModelSerializer):
    signature_image_url = serializers.SerializerMethodField()
    clear_signature_image = serializers.BooleanField(write_only=True, required=False, default=False)
    username = serializers.CharField(source="user.username", read_only=True)
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = UserQuotationProfile
        fields = [
            "id",
            "user",
            "username",
            "display_name",
            "signature_image",
            "signature_image_url",
            "clear_signature_image",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "user", "username", "display_name", "signature_image_url", "created_at", "updated_at"]

    def get_signature_image_url(self, obj):
        if not obj.signature_image:
            return ""
        try:
            return obj.signature_image.url
        except ValueError:
            return ""

    def get_display_name(self, obj):
        full_name = obj.user.get_full_name() if obj.user_id else ""
        return full_name or obj.user.username

    def validate_signature_image(self, image):
        return validate_branding_image_upload(image, "Signature image")

    def update(self, instance, validated_data):
        should_clear = validated_data.pop("clear_signature_image", False)
        if should_clear and instance.signature_image:
            try:
                instance.signature_image.delete(save=False)
            except Exception:
                pass
            instance.signature_image = None
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
            "unit_price",
            "vat_rate",
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
    contact_role = serializers.CharField(source="contact.role", read_only=True, allow_null=True)
    contact_department = serializers.CharField(source="contact.department", read_only=True, allow_null=True)
    contact_phone = serializers.CharField(source="contact.phone", read_only=True, allow_null=True)
    contact_email = serializers.CharField(source="contact.email", read_only=True, allow_null=True)
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
            "contact_role",
            "contact_department",
            "contact_phone",
            "contact_email",
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
            "contact_role",
            "contact_department",
            "contact_phone",
            "contact_email",
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
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    vat_rate = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, default=Decimal("0.00"))
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
            if validated_data.get("product"):
                validated_data.setdefault("match_status", QuotationLine.MATCH_CONFIRMED)
            elif validated_data.get("match_status") == QuotationLine.MATCH_CONFIRMED:
                validated_data["match_status"] = QuotationLine.MATCH_UNRESOLVED
        return super().update(instance, validated_data)


class HistoricalPriceImportSerializer(serializers.ModelSerializer):
    batch_name = serializers.CharField(source="batch.name", read_only=True, allow_null=True)
    company_name = serializers.CharField(source="company.name", read_only=True, allow_null=True)
    created_by_username = serializers.CharField(source="created_by.username", read_only=True, allow_null=True)
    committed_by_username = serializers.CharField(source="committed_by.username", read_only=True, allow_null=True)
    created_quotation_number = serializers.CharField(source="created_quotation.quotation_number", read_only=True, allow_null=True)
    lines = HistoricalPriceImportLineSerializer(many=True, read_only=True)

    class Meta:
        model = HistoricalPriceImport
        fields = [
            "id",
            "batch",
            "batch_name",
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
            "batch",
            "batch_name",
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


class HistoricalImportBatchSerializer(serializers.ModelSerializer):
    created_by_username = serializers.CharField(source="created_by.username", read_only=True, allow_null=True)
    imports = HistoricalPriceImportSerializer(many=True, read_only=True)
    import_count = serializers.SerializerMethodField()
    pending_suggestion_count = serializers.SerializerMethodField()
    wizard_summary = serializers.SerializerMethodField()

    class Meta:
        model = HistoricalImportBatch
        fields = [
            "id",
            "name",
            "status",
            "summary",
            "warnings",
            "created_by",
            "created_by_username",
            "imports",
            "import_count",
            "pending_suggestion_count",
            "wizard_summary",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "summary",
            "warnings",
            "created_by",
            "created_by_username",
            "imports",
            "import_count",
            "pending_suggestion_count",
            "wizard_summary",
            "created_at",
            "updated_at",
        ]

    def get_import_count(self, obj):
        return obj.imports.count()

    def get_pending_suggestion_count(self, obj):
        return obj.ai_suggestions.filter(status=HistoricalImportAISuggestion.STATUS_PENDING).count()

    def get_wizard_summary(self, obj):
        imports = list(obj.imports.all())
        suggestions = list(obj.ai_suggestions.all())
        line_counts = {
            "total": 0,
            "ready": 0,
            "needs_review": 0,
            "skipped": 0,
            "committed": 0,
            "duplicate": 0,
        }
        missing_document_details = 0
        company_ready = 0
        for historical_import in imports:
            if historical_import.company_id:
                company_ready += 1
            if not historical_import.company_id or not historical_import.document_date:
                missing_document_details += 1
            for line in historical_import.lines.all():
                line_counts["total"] += 1
                if line.status in line_counts:
                    line_counts[line.status] += 1

        suggestion_counts = {}
        pending_action_counts = {}
        applied_action_counts = {}
        conflict_action_counts = {}
        high_confidence_pending = 0
        for suggestion in suggestions:
            suggestion_counts[suggestion.status] = suggestion_counts.get(suggestion.status, 0) + 1
            if suggestion.status == HistoricalImportAISuggestion.STATUS_PENDING:
                pending_action_counts[suggestion.action] = pending_action_counts.get(suggestion.action, 0) + 1
                if suggestion.confidence >= 0.85:
                    high_confidence_pending += 1
            elif suggestion.status == HistoricalImportAISuggestion.STATUS_APPLIED:
                applied_action_counts[suggestion.action] = applied_action_counts.get(suggestion.action, 0) + 1
            elif suggestion.status == HistoricalImportAISuggestion.STATUS_CONFLICT:
                conflict_action_counts[suggestion.action] = conflict_action_counts.get(suggestion.action, 0) + 1

        files = (obj.summary or {}).get("files", [])
        failed_files = len([entry for entry in files if entry.get("status") == "failed"])
        duplicate_files = len([entry for entry in files if entry.get("status") == "duplicate"])
        pending_count = suggestion_counts.get(HistoricalImportAISuggestion.STATUS_PENDING, 0)
        conflict_count = suggestion_counts.get(HistoricalImportAISuggestion.STATUS_CONFLICT, 0)
        commit_blockers = []
        for entry in imports:
            blockers = []
            if entry.status == HistoricalPriceImport.STATUS_COMMITTED:
                blockers.append("already committed")
            if entry.status == HistoricalPriceImport.STATUS_CANCELLED:
                blockers.append("cancelled import")
            if not entry.company_id:
                blockers.append("missing company")
            if not entry.document_date:
                blockers.append("missing document date")
            ready_rows = [line for line in entry.lines.all() if line.status == HistoricalPriceImportLine.STATUS_READY]
            unresolved_rows = [line for line in entry.lines.all() if line.status == HistoricalPriceImportLine.STATUS_NEEDS_REVIEW]
            if not ready_rows:
                blockers.append("no ready rows")
            commit_blockers.append(
                {
                    "import_id": entry.id,
                    "filename": entry.source_filename,
                    "company_name": entry.company.name if entry.company_id else "",
                    "document_number": entry.document_number,
                    "ready_row_count": len(ready_rows),
                    "unresolved_row_count": len(unresolved_rows),
                    "blockers": blockers,
                    "can_commit": not blockers,
                }
            )
        return {
            "file_count": len(files) or len(imports),
            "parsed_file_count": len([entry for entry in files if entry.get("status") == "parsed"]) or len(imports),
            "failed_file_count": failed_files,
            "duplicate_file_count": duplicate_files,
            "import_count": len(imports),
            "company_ready_count": company_ready,
            "documents_missing_details_count": missing_document_details,
            "line_counts": line_counts,
            "suggestion_status_counts": suggestion_counts,
            "pending_suggestion_action_counts": pending_action_counts,
            "applied_suggestion_action_counts": applied_action_counts,
            "conflict_suggestion_action_counts": conflict_action_counts,
            "high_confidence_pending_suggestion_count": high_confidence_pending,
            "unresolved_count": line_counts["needs_review"] + pending_count + conflict_count + missing_document_details,
            "commit_blockers": commit_blockers,
        }


class HistoricalImportAISuggestionSerializer(serializers.ModelSerializer):
    batch_name = serializers.CharField(source="batch.name", read_only=True, allow_null=True)
    historical_import_document = serializers.CharField(source="historical_import.document_number", read_only=True, allow_null=True)
    historical_import_document_date = serializers.DateField(source="historical_import.document_date", read_only=True, allow_null=True)
    historical_import_filename = serializers.CharField(source="historical_import.source_filename", read_only=True, allow_null=True)
    historical_import_company_name = serializers.CharField(source="historical_import.company.name", read_only=True, allow_null=True)
    line_item_name = serializers.CharField(source="line.item_name", read_only=True, allow_null=True)
    line_quantity = serializers.DecimalField(source="line.quantity", max_digits=12, decimal_places=3, read_only=True, allow_null=True)
    line_unit = serializers.CharField(source="line.unit", read_only=True, allow_null=True)
    line_unit_price = serializers.DecimalField(source="line.unit_price", max_digits=12, decimal_places=2, read_only=True, allow_null=True)
    line_amount = serializers.DecimalField(source="line.amount", max_digits=12, decimal_places=2, read_only=True, allow_null=True)
    line_vat_amount = serializers.DecimalField(source="line.vat_amount", max_digits=12, decimal_places=2, read_only=True, allow_null=True)
    line_vat_rate = serializers.DecimalField(source="line.vat_rate", max_digits=5, decimal_places=2, read_only=True, allow_null=True)
    line_total = serializers.DecimalField(source="line.line_total", max_digits=12, decimal_places=2, read_only=True, allow_null=True)
    line_status = serializers.CharField(source="line.status", read_only=True, allow_null=True)
    line_raw = serializers.CharField(source="line.raw_line", read_only=True, allow_null=True)
    line_source_page = serializers.IntegerField(source="line.source_page", read_only=True, allow_null=True)
    line_source_row = serializers.IntegerField(source="line.source_row", read_only=True, allow_null=True)
    suggested_company_name = serializers.CharField(source="suggested_company.name", read_only=True, allow_null=True)
    suggested_product_name = serializers.CharField(source="suggested_product.name", read_only=True, allow_null=True)
    price_history_summary = serializers.SerializerMethodField()
    source_context = serializers.SerializerMethodField()
    line_ready_blockers = serializers.SerializerMethodField()
    import_commit_blockers = serializers.SerializerMethodField()

    class Meta:
        model = HistoricalImportAISuggestion
        fields = [
            "id",
            "batch",
            "batch_name",
            "historical_import",
            "historical_import_document",
            "historical_import_document_date",
            "historical_import_filename",
            "historical_import_company_name",
            "line",
            "line_item_name",
            "line_quantity",
            "line_unit",
            "line_unit_price",
            "line_amount",
            "line_vat_amount",
            "line_vat_rate",
            "line_total",
            "line_status",
            "line_raw",
            "line_source_page",
            "line_source_row",
            "suggestion_type",
            "action",
            "status",
            "suggested_company",
            "suggested_company_name",
            "suggested_product",
            "suggested_product_name",
            "alias_text",
            "proposed_company_name",
            "proposed_product_name",
            "proposed_unit",
            "proposed_pack_size",
            "proposed_dosage",
            "confidence",
            "reason",
            "candidate_companies",
            "candidate_products",
            "raw_ai_payload",
            "price_history_summary",
            "source_context",
            "line_ready_blockers",
            "import_commit_blockers",
            "error_message",
            "created_by",
            "applied_by",
            "applied_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "batch",
            "batch_name",
            "historical_import",
            "historical_import_document",
            "historical_import_document_date",
            "historical_import_filename",
            "historical_import_company_name",
            "line",
            "line_item_name",
            "line_quantity",
            "line_unit",
            "line_unit_price",
            "line_amount",
            "line_vat_amount",
            "line_vat_rate",
            "line_total",
            "line_status",
            "line_raw",
            "line_source_page",
            "line_source_row",
            "suggestion_type",
            "status",
            "candidate_companies",
            "candidate_products",
            "raw_ai_payload",
            "price_history_summary",
            "source_context",
            "line_ready_blockers",
            "import_commit_blockers",
            "error_message",
            "created_by",
            "applied_by",
            "applied_at",
            "created_at",
            "updated_at",
        ]

    def get_price_history_summary(self, obj):
        line = obj.line
        product = obj.suggested_product or (line.product if line and line.product_id else None)
        historical_import = obj.historical_import
        imported_price = line.unit_price if line else None
        if not product:
            return {
                "available": False,
                "imported_unit_price": str(imported_price) if imported_price is not None else "",
                "message": "Select a target Product to see previous price context.",
            }
        summary = {
            "available": bool(historical_import and historical_import.company_id),
            "product_id": product.id,
            "product_name": product.name,
            "product_base_price": str(product.price) if product.price is not None else "",
            "imported_unit_price": str(imported_price) if imported_price is not None else "",
            "last_company_price": "",
            "last_company_price_date": "",
            "price_difference": "",
            "price_difference_percent": "",
            "recent_company_price_count": 0,
            "variance_warning": "",
        }
        if not historical_import or not historical_import.company_id:
            summary["message"] = "Select or approve the company to compare company-specific price history."
            return summary
        history = CompanyPriceHistory.objects.filter(
            company=historical_import.company,
            product=product,
        ).order_by("-quoted_at", "-id")
        summary["recent_company_price_count"] = history.count()
        last_price = history.first()
        if not last_price:
            summary["message"] = "No previous company-specific price history for this Product."
            return summary
        summary["last_company_price"] = str(last_price.unit_price)
        summary["last_company_price_date"] = last_price.quoted_at.date().isoformat()
        if imported_price is not None:
            difference = Decimal(imported_price) - Decimal(last_price.unit_price)
            summary["price_difference"] = str(difference.quantize(Decimal("0.01")))
            if last_price.unit_price:
                percent = (difference / Decimal(last_price.unit_price)) * Decimal("100")
                summary["price_difference_percent"] = str(percent.quantize(Decimal("0.01")))
                if abs(percent) >= Decimal("25"):
                    summary["variance_warning"] = "Large variance from last company price."
        return summary

    def get_source_context(self, obj):
        line = obj.line
        historical_import = obj.historical_import
        source_available = bool(historical_import and historical_import.source_file_ref)
        page_number = line.source_page if line else None
        return {
            "available": source_available,
            "filename": historical_import.source_filename if historical_import else "",
            "page_number": page_number,
            "source_row": line.source_row if line else None,
            "raw_line": line.raw_line if line else "",
            "preview_url": (
                f"/api/quotations/historical-imports/{historical_import.id}/preview_page/?page={page_number or 1}"
                if historical_import and source_available
                else ""
            ),
            "message": "" if source_available else "Source preview unavailable for this historical import.",
        }

    def get_line_ready_blockers(self, obj):
        line = obj.line
        if not line:
            return []
        blockers = []
        if line.status == HistoricalPriceImportLine.STATUS_COMMITTED:
            return ["already committed to price history"]
        if line.status == HistoricalPriceImportLine.STATUS_DUPLICATE:
            return ["duplicate row"]
        if line.status == HistoricalPriceImportLine.STATUS_SKIPPED:
            return ["skipped row"]
        if not line.product_id and not line.quote_item_id:
            blockers.append("missing Product")
        if line.quantity is None or line.quantity <= 0:
            blockers.append("missing quantity")
        if line.unit_price is None or line.unit_price < 0:
            blockers.append("missing unit price")
        return blockers

    def get_import_commit_blockers(self, obj):
        historical_import = obj.historical_import
        if not historical_import:
            return []
        blockers = []
        if historical_import.status == HistoricalPriceImport.STATUS_COMMITTED:
            blockers.append("already committed")
        if historical_import.status == HistoricalPriceImport.STATUS_CANCELLED:
            blockers.append("cancelled import")
        if not historical_import.company_id:
            blockers.append("missing company")
        if not historical_import.document_date:
            blockers.append("missing document date")
        return blockers


class QuotationLineSerializer(serializers.ModelSerializer):
    quote_item_name = serializers.CharField(source="quote_item.name", read_only=True, allow_null=True)
    product_name = serializers.CharField(source="product.name", read_only=True, allow_null=True)
    inquiry_line_raw_name = serializers.CharField(source="inquiry_line.raw_name", read_only=True, allow_null=True)
    product_image = serializers.PrimaryKeyRelatedField(
        queryset=ProductImage.objects.all(),
        required=False,
        allow_null=True,
    )
    product_image_url = serializers.SerializerMethodField()
    has_product_image = serializers.SerializerMethodField()
    outcome_status_display = serializers.CharField(source="get_outcome_status_display", read_only=True)
    outcome_reason_display = serializers.CharField(source="get_outcome_reason_display", read_only=True)

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
            "product_image",
            "product_image_url",
            "has_product_image",
            "include_product_image",
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
            "outcome_status",
            "outcome_status_display",
            "accepted_quantity",
            "accepted_unit_price",
            "accepted_total",
            "lost_value",
            "outcome_reason",
            "outcome_reason_display",
            "outcome_notes",
            "quoted_gross_profit",
            "accepted_gross_profit",
            "lost_gross_profit",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "inquiry_line_raw_name",
            "quote_item_name",
            "product_name",
            "product_image_url",
            "has_product_image",
            "line_subtotal",
            "vat_amount",
            "line_total",
            "outcome_status_display",
            "outcome_status",
            "accepted_quantity",
            "accepted_unit_price",
            "accepted_total",
            "lost_value",
            "outcome_reason",
            "outcome_reason_display",
            "outcome_notes",
            "quoted_gross_profit",
            "accepted_gross_profit",
            "lost_gross_profit",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        product = attrs.get("product") or getattr(self.instance, "product", None)
        product_image = attrs.get("product_image")
        if product_image is None and self.instance:
            product_image = self.instance.product_image
        quote_item = attrs.get("quote_item") or getattr(self.instance, "quote_item", None)
        item_name = attrs.get("item_name_snapshot") or getattr(self.instance, "item_name_snapshot", "")
        if product_image and product and product_image.product_id != product.id:
            raise serializers.ValidationError({"product_image": "Selected image must belong to the matched Product."})
        if not item_name and product:
            attrs["item_name_snapshot"] = product.name
        elif not item_name and quote_item:
            attrs["item_name_snapshot"] = quote_item.name
        if not attrs.get("item_name_snapshot") and not item_name:
            raise serializers.ValidationError({"item_name_snapshot": "This field is required."})
        return attrs

    def get_product_image_url(self, obj):
        image = obj.product_image or (obj.product.primary_image if obj.product_id else None)
        if not image or not image.image:
            return ""
        request = self.context.get("request")
        if request:
            return request.build_absolute_uri(image.image.url)
        return image.image.url

    def get_has_product_image(self, obj):
        return bool(obj.product_image_id or (obj.product_id and obj.product.primary_image))

    def update(self, instance, validated_data):
        if "product" in validated_data and validated_data.get("product") != instance.product:
            validated_data.setdefault("match_reason", "Selected manually by staff.")
            selected_image = validated_data.get("product_image", instance.product_image)
            next_product = validated_data.get("product")
            if selected_image and next_product and selected_image.product_id != next_product.id:
                validated_data["product_image"] = None
                validated_data["include_product_image"] = False
        return super().update(instance, validated_data)


class QuotationSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)
    contact_name = serializers.CharField(source="contact.name", read_only=True, allow_null=True)
    contact_role = serializers.CharField(source="contact.role", read_only=True, allow_null=True)
    contact_department = serializers.CharField(source="contact.department", read_only=True, allow_null=True)
    contact_phone = serializers.CharField(source="contact.phone", read_only=True, allow_null=True)
    contact_email = serializers.CharField(source="contact.email", read_only=True, allow_null=True)
    inquiry_subject = serializers.CharField(source="inquiry.subject", read_only=True, allow_null=True)
    created_by_username = serializers.CharField(source="created_by.username", read_only=True, allow_null=True)
    finalized_by_username = serializers.CharField(source="finalized_by.username", read_only=True, allow_null=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    payment_terms_display = serializers.CharField(source="get_payment_terms_display", read_only=True)
    outcome_status_display = serializers.CharField(source="get_outcome_status_display", read_only=True)
    follow_up_status_display = serializers.CharField(source="get_follow_up_status_display", read_only=True)
    follow_up_contact_method_display = serializers.CharField(source="get_follow_up_contact_method_display", read_only=True)
    outcome_closed_by_username = serializers.CharField(source="outcome_closed_by.username", read_only=True, allow_null=True)
    outcome_last_updated_by_username = serializers.CharField(source="outcome_last_updated_by.username", read_only=True, allow_null=True)
    latest_lpo = serializers.SerializerMethodField()
    lpo_count = serializers.SerializerMethodField()
    lines = QuotationLineSerializer(many=True, read_only=True)

    class Meta:
        model = Quotation
        fields = [
            "id",
            "company",
            "company_name",
            "contact",
            "contact_name",
            "contact_role",
            "contact_department",
            "contact_phone",
            "contact_email",
            "inquiry",
            "inquiry_subject",
            "quotation_number",
            "status",
            "status_display",
            "version",
            "parent",
            "valid_until",
            "currency",
            "payment_terms",
            "payment_terms_display",
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
            "outcome_status",
            "outcome_status_display",
            "outcome_status_is_manual",
            "outcome_date",
            "outcome_notes",
            "outcome_closed_by",
            "outcome_closed_by_username",
            "outcome_closed_at",
            "outcome_last_updated_by",
            "outcome_last_updated_by_username",
            "outcome_last_updated_at",
            "last_contacted_at",
            "next_follow_up_date",
            "follow_up_status",
            "follow_up_status_display",
            "follow_up_notes",
            "follow_up_contact_method",
            "follow_up_contact_method_display",
            "latest_lpo",
            "lpo_count",
            "is_historical_import",
            "lines",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "company_name",
            "contact_name",
            "contact_role",
            "contact_department",
            "contact_phone",
            "contact_email",
            "inquiry_subject",
            "quotation_number",
            "payment_terms_display",
            "subtotal",
            "vat_total",
            "total",
            "created_by",
            "created_by_username",
            "finalized_by",
            "finalized_by_username",
            "finalized_at",
            "sent_at",
            "outcome_status_display",
            "outcome_status",
            "outcome_status_is_manual",
            "outcome_date",
            "outcome_notes",
            "outcome_closed_by",
            "outcome_closed_by_username",
            "outcome_closed_at",
            "outcome_last_updated_by",
            "outcome_last_updated_by_username",
            "outcome_last_updated_at",
            "last_contacted_at",
            "next_follow_up_date",
            "follow_up_status",
            "follow_up_status_display",
            "follow_up_notes",
            "follow_up_contact_method",
            "follow_up_contact_method_display",
            "latest_lpo",
            "lpo_count",
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

    def get_latest_lpo(self, obj):
        lpo = obj.lpos.order_by("-received_at", "-id").first()
        if not lpo:
            return None
        return QuotationLPOSerializer(lpo, context=self.context).data

    def get_lpo_count(self, obj):
        return obj.lpos.count()


class QuotationListSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)
    contact_name = serializers.CharField(source="contact.name", read_only=True, allow_null=True)
    inquiry_subject = serializers.CharField(source="inquiry.subject", read_only=True, allow_null=True)
    created_by_username = serializers.CharField(source="created_by.username", read_only=True, allow_null=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    payment_terms_display = serializers.CharField(source="get_payment_terms_display", read_only=True)
    outcome_status_display = serializers.CharField(source="get_outcome_status_display", read_only=True)
    follow_up_status_display = serializers.CharField(source="get_follow_up_status_display", read_only=True)

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
            "valid_until",
            "currency",
            "payment_terms",
            "payment_terms_display",
            "outcome_status",
            "outcome_status_display",
            "outcome_date",
            "next_follow_up_date",
            "follow_up_status",
            "follow_up_status_display",
            "subtotal",
            "vat_total",
            "total",
            "is_historical_import",
            "created_by_username",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class QuotationOutcomePOImportSerializer(serializers.ModelSerializer):
    created_by_username = serializers.CharField(source="created_by.username", read_only=True, allow_null=True)

    class Meta:
        model = QuotationOutcomePOImport
        fields = [
            "id",
            "quotation",
            "source_type",
            "source_filename",
            "source_sha256",
            "parse_method",
            "status",
            "parsed_rows",
            "suggestions",
            "unmatched_po_rows",
            "missing_quote_line_ids",
            "warnings",
            "created_by",
            "created_by_username",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ProformaInvoiceLineSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True, allow_null=True)

    class Meta:
        model = ProformaInvoiceLine
        fields = [
            "id",
            "proforma",
            "product",
            "product_name",
            "item_name",
            "description",
            "quantity",
            "unit",
            "unit_price",
            "vat_rate",
            "line_subtotal",
            "vat_amount",
            "line_total",
            "sort_order",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "proforma",
            "product_name",
            "line_subtotal",
            "vat_amount",
            "line_total",
            "created_at",
            "updated_at",
        ]


class ProformaInvoiceSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)
    company_billing_address = serializers.CharField(source="company.billing_address", read_only=True, allow_blank=True)
    company_trn = serializers.CharField(source="company.trn", read_only=True, allow_blank=True)
    contact_name = serializers.CharField(source="contact.name", read_only=True, allow_null=True)
    contact_phone = serializers.CharField(source="contact.phone", read_only=True, allow_null=True)
    contact_email = serializers.CharField(source="contact.email", read_only=True, allow_null=True)
    quotation_number = serializers.CharField(source="quotation.quotation_number", read_only=True, allow_null=True)
    created_by_username = serializers.CharField(source="created_by.username", read_only=True, allow_null=True)
    issued_by_username = serializers.CharField(source="issued_by.username", read_only=True, allow_null=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    parsed_row_count = serializers.SerializerMethodField()
    lines = ProformaInvoiceLineSerializer(many=True, read_only=True)

    class Meta:
        model = ProformaInvoice
        fields = [
            "id",
            "company",
            "company_name",
            "company_billing_address",
            "company_trn",
            "contact",
            "contact_name",
            "contact_phone",
            "contact_email",
            "quotation",
            "quotation_number",
            "proforma_number",
            "status",
            "status_display",
            "proforma_date",
            "currency",
            "lpo_number",
            "lpo_date",
            "source_type",
            "source_filename",
            "source_sha256",
            "source_file_size",
            "parse_method",
            "parsed_meta",
            "parsed_rows",
            "parsed_row_count",
            "warnings",
            "subtotal",
            "vat_total",
            "total",
            "notes",
            "created_by",
            "created_by_username",
            "issued_by",
            "issued_by_username",
            "issued_at",
            "lines",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "company_name",
            "company_billing_address",
            "company_trn",
            "contact_name",
            "contact_phone",
            "contact_email",
            "quotation_number",
            "proforma_number",
            "status_display",
            "source_type",
            "source_filename",
            "source_sha256",
            "source_file_size",
            "parse_method",
            "parsed_meta",
            "parsed_rows",
            "parsed_row_count",
            "warnings",
            "subtotal",
            "vat_total",
            "total",
            "created_by",
            "created_by_username",
            "issued_by",
            "issued_by_username",
            "issued_at",
            "lines",
            "created_at",
            "updated_at",
        ]

    def get_parsed_row_count(self, obj):
        return len(obj.parsed_rows or [])

    def create(self, validated_data):
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            validated_data["created_by"] = request.user
        return super().create(validated_data)


class QuotationLPOSerializer(serializers.ModelSerializer):
    received_by_username = serializers.CharField(source="received_by.username", read_only=True, allow_null=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    source_type_display = serializers.CharField(source="get_source_type_display", read_only=True)
    parsed_row_count = serializers.SerializerMethodField()

    class Meta:
        model = QuotationLPO
        fields = [
            "id",
            "quotation",
            "source_type",
            "source_type_display",
            "source_filename",
            "source_sha256",
            "source_file_size",
            "parse_method",
            "lpo_number",
            "lpo_date",
            "status",
            "status_display",
            "parsed_meta",
            "parsed_rows",
            "parsed_row_count",
            "warnings",
            "notes",
            "received_by",
            "received_by_username",
            "received_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "quotation",
            "source_type",
            "source_type_display",
            "source_filename",
            "source_sha256",
            "source_file_size",
            "parse_method",
            "parsed_meta",
            "parsed_rows",
            "parsed_row_count",
            "warnings",
            "received_by",
            "received_by_username",
            "received_at",
            "created_at",
            "updated_at",
        ]

    def get_parsed_row_count(self, obj):
        return len(obj.parsed_rows or [])


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
    action_display = serializers.CharField(source="get_action_display", read_only=True)
    company_name = serializers.CharField(source="company.name", read_only=True, allow_null=True)
    quotation_number = serializers.CharField(source="quotation.quotation_number", read_only=True, allow_null=True)

    class Meta:
        model = QuotationAuditLog
        fields = [
            "id",
            "actor",
            "actor_username",
            "action",
            "action_display",
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
            "action_display",
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
