from django.contrib import admin

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


class CompanyContactInline(admin.TabularInline):
    model = CompanyContact
    extra = 0


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ["name", "email", "phone", "trn", "is_active", "updated_at"]
    list_filter = ["is_active"]
    search_fields = ["name", "email", "phone", "trn"]
    readonly_fields = ["normalized_name", "created_at", "updated_at"]
    inlines = [CompanyContactInline]


@admin.register(CompanyContact)
class CompanyContactAdmin(admin.ModelAdmin):
    list_display = ["name", "company", "email", "phone", "role", "is_primary", "is_active"]
    list_filter = ["is_primary", "is_active"]
    search_fields = ["name", "company__name", "email", "phone"]
    autocomplete_fields = ["company"]


@admin.register(QuoteItem)
class QuoteItemAdmin(admin.ModelAdmin):
    list_display = ["name", "internal_code", "brand_text", "strength", "pack_size", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name", "internal_code", "brand_text", "generic_name", "product__name"]
    autocomplete_fields = ["product"]
    readonly_fields = ["normalized_name", "created_at", "updated_at"]


@admin.register(QuotationSettings)
class QuotationSettingsAdmin(admin.ModelAdmin):
    fieldsets = (
        ("Company Branding", {
            "fields": (
                "company_name",
                "company_name_ar",
                "logo",
                "signature_image",
                "stamp_image",
                "logo_layout",
                "address",
                "phone",
                "email",
                "trn",
                "license_number",
            )
        }),
        ("PDF Defaults", {
            "fields": (
                "default_terms",
                "payment_terms",
                "validity_days",
                "footer_note",
                "prepared_by_default",
                "signature_label",
                "stamp_label",
                "pdf_template_style",
            )
        }),
        ("Style", {
            "fields": (
                "primary_color",
                "accent_color",
                "show_arabic_name",
                "show_trn",
                "show_license_number",
                "show_signature_area",
                "show_stamp_area",
            )
        }),
        ("Audit", {"fields": ("updated_by", "created_at", "updated_at")}),
    )
    readonly_fields = ["created_at", "updated_at"]

    def has_add_permission(self, request):
        return not QuotationSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


class InquiryLineInline(admin.TabularInline):
    model = InquiryLine
    extra = 0
    autocomplete_fields = ["matched_quote_item"]


@admin.register(Inquiry)
class InquiryAdmin(admin.ModelAdmin):
    list_display = ["subject", "company", "status", "source_type", "received_at", "created_by"]
    list_filter = ["status", "source", "source_type", "parse_method", "received_at"]
    search_fields = ["subject", "company__name", "original_text", "source_filename", "source_sha256"]
    autocomplete_fields = ["company", "contact", "created_by"]
    readonly_fields = ["created_at", "updated_at"]
    inlines = [InquiryLineInline]


@admin.register(InquiryLine)
class InquiryLineAdmin(admin.ModelAdmin):
    list_display = ["raw_name", "inquiry", "quantity", "unit", "match_status", "parse_status", "parse_confidence", "sort_order"]
    list_filter = ["match_status", "parse_status"]
    search_fields = ["raw_name", "raw_line", "inquiry__subject", "matched_quote_item__name"]
    autocomplete_fields = ["inquiry", "matched_quote_item"]
    readonly_fields = ["normalized_name", "created_at", "updated_at"]


class QuotationLineInline(admin.TabularInline):
    model = QuotationLine
    extra = 0
    autocomplete_fields = ["quote_item", "inquiry_line"]
    readonly_fields = ["line_subtotal", "vat_amount", "line_total"]


@admin.register(Quotation)
class QuotationAdmin(admin.ModelAdmin):
    list_display = ["quotation_number", "company", "status", "version", "total", "created_at"]
    list_filter = ["status", "created_at"]
    search_fields = ["quotation_number", "company__name", "inquiry__subject"]
    autocomplete_fields = ["company", "contact", "inquiry", "parent", "created_by", "finalized_by"]
    readonly_fields = ["quotation_number", "subtotal", "vat_total", "total", "finalized_at", "sent_at", "created_at", "updated_at"]
    inlines = [QuotationLineInline]


@admin.register(QuotationLine)
class QuotationLineAdmin(admin.ModelAdmin):
    list_display = ["item_name_snapshot", "quotation", "quantity", "unit_price", "line_total", "match_status"]
    list_filter = ["match_status"]
    search_fields = ["item_name_snapshot", "quotation__quotation_number", "quote_item__name"]
    autocomplete_fields = ["quotation", "quote_item", "inquiry_line"]
    readonly_fields = ["line_subtotal", "vat_amount", "line_total", "created_at", "updated_at"]


@admin.register(CompanyPriceHistory)
class CompanyPriceHistoryAdmin(admin.ModelAdmin):
    list_display = ["company", "quote_item", "unit_price", "currency", "quoted_at", "quotation"]
    list_filter = ["currency", "quoted_at"]
    search_fields = ["company__name", "quote_item__name", "quotation__quotation_number"]
    autocomplete_fields = ["company", "quote_item", "quotation", "quotation_line", "created_by"]
    readonly_fields = ["created_at"]


@admin.register(QuotationAuditLog)
class QuotationAuditLogAdmin(admin.ModelAdmin):
    list_display = ["created_at", "actor", "action", "target_type", "target_id", "company", "quotation"]
    list_filter = ["action", "created_at"]
    search_fields = ["message", "actor__username", "company__name", "quotation__quotation_number"]
    readonly_fields = ["created_at"]
