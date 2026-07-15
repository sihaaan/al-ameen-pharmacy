from django.contrib import admin
from django.core.exceptions import PermissionDenied

from .models import (
    Company,
    CompanyContact,
    CompanyPriceHistory,
    AIParseCache,
    AIParseLog,
    HistoricalImportAISuggestion,
    HistoricalImportBatch,
    HistoricalPriceImport,
    HistoricalPriceImportLine,
    Inquiry,
    InquiryLine,
    MailboxPOAuditRun,
    MailboxPOAuditFailure,
    MailboxPOMatchRun,
    MailboxPOMessage,
    ProformaInvoice,
    ProformaInvoiceLine,
    Quotation,
    QuotationAuditLog,
    QuotationLine,
    QuotationLPO,
    QuotationPOEvidence,
    QuotationOutcomePOImport,
    QuotationSettings,
    UserQuotationProfile,
    ProductAlias,
    QuoteItem,
)


class CompanyContactInline(admin.TabularInline):
    model = CompanyContact
    extra = 0


class ReadOnlyHistoryAdminMixin:
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MailboxPOAuditRun)
class MailboxPOAuditRunAdmin(ReadOnlyHistoryAdminMixin, admin.ModelAdmin):
    list_display = [
        "id",
        "gmail_connection",
        "status",
        "messages_scanned",
        "relevant_messages",
        "incomplete_messages",
        "exhausted",
        "created_at",
    ]
    list_filter = ["status", "exhausted", "created_at"]
    search_fields = ["gmail_connection__email", "gmail_query"]
    readonly_fields = [field.name for field in MailboxPOAuditRun._meta.fields]


@admin.register(MailboxPOAuditFailure)
class MailboxPOAuditFailureAdmin(ReadOnlyHistoryAdminMixin, admin.ModelAdmin):
    list_display = ["audit_run", "gmail_message_id", "status", "attempts", "last_failed_at"]
    list_filter = ["status", "last_failed_at"]
    search_fields = ["gmail_message_id", "last_error", "audit_run__gmail_connection__email"]
    readonly_fields = [field.name for field in MailboxPOAuditFailure._meta.fields]


@admin.register(MailboxPOMatchRun)
class MailboxPOMatchRunAdmin(ReadOnlyHistoryAdminMixin, admin.ModelAdmin):
    list_display = ["id", "audit_run", "algorithm_version", "status", "completed_at", "created_at"]
    list_filter = ["status", "algorithm_version", "created_at"]
    readonly_fields = [field.name for field in MailboxPOMatchRun._meta.fields]


@admin.register(MailboxPOMessage)
class MailboxPOMessageAdmin(ReadOnlyHistoryAdminMixin, admin.ModelAdmin):
    list_display = ["subject", "sender", "sent_at", "classification", "is_relevant", "auto_link_eligible"]
    list_filter = ["classification", "is_relevant", "auto_link_eligible", "sent_at"]
    search_fields = ["gmail_message_id", "subject", "sender", "newest_body_text"]
    readonly_fields = [field.name for field in MailboxPOMessage._meta.fields]


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ["name", "email", "phone", "trn", "is_active", "updated_at"]
    list_filter = ["is_active"]
    search_fields = ["name", "email", "phone", "trn"]
    readonly_fields = ["normalized_name", "created_at", "updated_at"]
    inlines = [CompanyContactInline]


@admin.register(CompanyContact)
class CompanyContactAdmin(admin.ModelAdmin):
    list_display = ["name", "company", "department", "role", "email", "phone", "is_primary", "is_active"]
    list_filter = ["is_primary", "is_active"]
    search_fields = ["name", "company__name", "email", "phone", "role", "department"]
    autocomplete_fields = ["company"]


@admin.register(QuoteItem)
class QuoteItemAdmin(admin.ModelAdmin):
    list_display = ["name", "internal_code", "brand_text", "strength", "pack_size", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name", "internal_code", "brand_text", "generic_name", "product__name"]
    autocomplete_fields = ["product"]
    readonly_fields = ["normalized_name", "created_at", "updated_at"]


@admin.register(ProductAlias)
class ProductAliasAdmin(admin.ModelAdmin):
    list_display = ["alias", "company", "product", "is_active", "updated_at"]
    list_filter = ["is_active", "company"]
    search_fields = ["alias", "normalized_alias", "company__name", "product__name", "product__sku"]
    autocomplete_fields = ["company", "product", "created_by"]
    readonly_fields = ["normalized_alias", "created_at", "updated_at"]


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
        ("AI Parsing", {
            "fields": (
                "ai_parsing_enabled",
                "ai_auto_cleanup_enabled",
                "ai_pdf_vision_enabled",
            )
        }),
        ("Audit", {"fields": ("updated_by", "created_at", "updated_at")}),
    )
    readonly_fields = ["created_at", "updated_at"]

    def has_add_permission(self, request):
        return not QuotationSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UserQuotationProfile)
class UserQuotationProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "updated_at"]
    search_fields = ["user__username", "user__first_name", "user__last_name", "user__email"]
    autocomplete_fields = ["user"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(AIParseCache)
class AIParseCacheAdmin(admin.ModelAdmin):
    list_display = ["cache_key", "provider", "model", "mode", "source_sha256", "updated_at"]
    list_filter = ["provider", "model", "mode"]
    search_fields = ["cache_key", "source_sha256", "context_hash"]
    readonly_fields = ["cache_key", "source_sha256", "context_hash", "mode", "provider", "model", "result", "created_at", "updated_at"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AIParseLog)
class AIParseLogAdmin(admin.ModelAdmin):
    list_display = ["created_at", "provider", "model", "mode", "source_type", "cache_hit", "success", "actor"]
    list_filter = ["provider", "model", "mode", "cache_hit", "success"]
    search_fields = ["source_sha256", "context_hash", "error", "actor__username"]
    readonly_fields = [
        "actor",
        "provider",
        "model",
        "mode",
        "source_type",
        "source_sha256",
        "context_hash",
        "cache_hit",
        "text_length",
        "page_count",
        "image_count",
        "usage",
        "success",
        "error",
        "created_at",
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class InquiryLineInline(admin.TabularInline):
    model = InquiryLine
    extra = 0
    autocomplete_fields = ["matched_product", "matched_quote_item"]


@admin.register(Inquiry)
class InquiryAdmin(admin.ModelAdmin):
    list_display = ["subject", "company", "status", "source_type", "received_at", "created_by"]
    list_filter = ["status", "source", "source_type", "parse_method", "received_at"]
    search_fields = ["subject", "company__name", "original_text", "source_filename", "source_sha256", "source_file_ref"]
    autocomplete_fields = ["company", "contact", "created_by"]
    readonly_fields = ["created_at", "updated_at"]
    inlines = [InquiryLineInline]


@admin.register(InquiryLine)
class InquiryLineAdmin(admin.ModelAdmin):
    list_display = ["raw_name", "inquiry", "quantity", "unit", "match_status", "parse_status", "parse_confidence", "sort_order"]
    list_filter = ["match_status", "parse_status"]
    search_fields = ["raw_name", "raw_line", "inquiry__subject", "matched_product__name", "matched_quote_item__name"]
    autocomplete_fields = ["inquiry", "matched_product", "matched_quote_item"]
    readonly_fields = ["normalized_name", "created_at", "updated_at"]


class HistoricalPriceImportLineInline(admin.TabularInline):
    model = HistoricalPriceImportLine
    extra = 0
    autocomplete_fields = ["product", "quote_item"]
    readonly_fields = ["normalized_item_name", "duplicate_reason", "created_at", "updated_at"]


@admin.register(HistoricalImportBatch)
class HistoricalImportBatchAdmin(admin.ModelAdmin):
    list_display = ["name", "status", "created_by", "created_at", "updated_at"]
    list_filter = ["status", "created_at"]
    search_fields = ["name", "created_by__username"]
    autocomplete_fields = ["created_by"]
    readonly_fields = ["summary", "warnings", "created_at", "updated_at"]


@admin.register(HistoricalPriceImport)
class HistoricalPriceImportAdmin(admin.ModelAdmin):
    list_display = ["source_filename", "batch", "company", "document_number", "document_date", "status", "created_at"]
    list_filter = ["status", "source_type", "document_date", "batch", "created_at"]
    search_fields = ["source_filename", "source_sha256", "source_file_ref", "document_number", "suggested_company_name", "company__name", "batch__name"]
    autocomplete_fields = ["batch", "company", "created_quotation", "created_by", "committed_by"]
    readonly_fields = ["created_at", "updated_at", "committed_at"]
    inlines = [HistoricalPriceImportLineInline]


@admin.register(HistoricalImportAISuggestion)
class HistoricalImportAISuggestionAdmin(admin.ModelAdmin):
    list_display = ["action", "status", "historical_import", "line", "suggested_product", "suggested_company", "confidence", "updated_at"]
    list_filter = ["action", "status", "suggestion_type", "batch"]
    search_fields = ["historical_import__source_filename", "line__item_name", "alias_text", "proposed_product_name", "proposed_company_name", "reason"]
    autocomplete_fields = ["batch", "historical_import", "line", "suggested_company", "suggested_product", "created_by", "applied_by"]
    readonly_fields = ["candidate_companies", "candidate_products", "raw_ai_payload", "error_message", "created_at", "updated_at", "applied_at"]


@admin.register(HistoricalPriceImportLine)
class HistoricalPriceImportLineAdmin(admin.ModelAdmin):
    list_display = ["item_name", "historical_import", "product", "quote_item", "quantity", "unit_price", "status", "sort_order"]
    list_filter = ["status"]
    search_fields = ["item_name", "raw_line", "product__name", "quote_item__name", "historical_import__source_filename"]
    autocomplete_fields = ["historical_import", "product", "quote_item"]
    readonly_fields = ["normalized_item_name", "created_at", "updated_at"]


class QuotationLineInline(admin.TabularInline):
    model = QuotationLine
    extra = 0
    autocomplete_fields = ["product", "quote_item", "inquiry_line"]
    readonly_fields = ["line_subtotal", "vat_amount", "line_total"]


@admin.register(Quotation)
class QuotationAdmin(admin.ModelAdmin):
    list_display = ["quotation_number", "company", "status", "outcome_status", "version", "total", "is_historical_import", "created_at"]
    list_filter = ["status", "outcome_status", "follow_up_status", "is_historical_import", "created_at"]
    search_fields = ["quotation_number", "company__name", "inquiry__subject"]
    autocomplete_fields = ["company", "contact", "inquiry", "parent", "created_by", "finalized_by", "outcome_closed_by", "outcome_last_updated_by"]
    readonly_fields = [
        "quotation_number",
        "subtotal",
        "vat_total",
        "total",
        "finalized_at",
        "sent_at",
        "outcome_closed_at",
        "outcome_last_updated_at",
        "created_at",
        "updated_at",
    ]
    inlines = [QuotationLineInline]

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.lpos.filter(status=QuotationLPO.STATUS_CONFIRMED).exists():
            return False
        return super().has_delete_permission(request, obj)

    def delete_model(self, request, obj):
        if obj.lpos.filter(status=QuotationLPO.STATUS_CONFIRMED).exists():
            raise PermissionDenied(Quotation.CONFIRMED_LPO_DELETE_ERROR)
        return super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        if queryset.filter(lpos__status=QuotationLPO.STATUS_CONFIRMED).exists():
            raise PermissionDenied(Quotation.CONFIRMED_LPO_DELETE_ERROR)
        return super().delete_queryset(request, queryset)


@admin.register(QuotationLine)
class QuotationLineAdmin(admin.ModelAdmin):
    list_display = ["item_name_snapshot", "quotation", "quantity", "unit_price", "line_total", "match_status", "outcome_status"]
    list_filter = ["match_status", "outcome_status", "outcome_reason"]
    search_fields = ["item_name_snapshot", "quotation__quotation_number", "product__name", "quote_item__name"]
    autocomplete_fields = ["quotation", "product", "quote_item", "inquiry_line"]
    readonly_fields = ["line_subtotal", "vat_amount", "line_total", "accepted_total", "lost_value", "created_at", "updated_at"]


@admin.register(QuotationOutcomePOImport)
class QuotationOutcomePOImportAdmin(admin.ModelAdmin):
    list_display = ["quotation", "source_type", "source_filename", "status", "created_by", "created_at"]
    list_filter = ["source_type", "status", "created_at"]
    search_fields = ["quotation__quotation_number", "source_filename", "source_sha256", "gmail_evidence__subject"]
    autocomplete_fields = ["quotation", "gmail_evidence", "created_by"]
    readonly_fields = [
        "source_sha256",
        "source_file_ref",
        "parse_method",
        "parsed_rows",
        "suggestions",
        "unmatched_po_rows",
        "missing_quote_line_ids",
        "warnings",
        "created_at",
        "updated_at",
    ]


@admin.register(QuotationPOEvidence)
class QuotationPOEvidenceAdmin(admin.ModelAdmin):
    list_display = ["quotation", "subject", "sender", "sent_at", "confidence", "status", "updated_at"]
    list_filter = ["status", "sent_at", "updated_at"]
    search_fields = ["quotation__quotation_number", "subject", "sender", "gmail_message_id", "gmail_thread_id"]
    autocomplete_fields = ["quotation", "created_by"]
    readonly_fields = [
        "gmail_message_id",
        "gmail_thread_id",
        "recipients",
        "snippet",
        "extracted_text",
        "attachments",
        "source_sha256",
        "matching_reason",
        "error",
        "created_at",
        "updated_at",
    ]


@admin.register(QuotationLPO)
class QuotationLPOAdmin(admin.ModelAdmin):
    list_display = ["quotation", "lpo_number", "lpo_date", "status", "source_filename", "received_by", "received_at"]
    list_filter = ["status", "source_type", "lpo_date", "received_at"]
    search_fields = ["quotation__quotation_number", "quotation__company__name", "lpo_number", "source_filename", "source_sha256"]
    autocomplete_fields = ["quotation", "received_by"]
    readonly_fields = [
        "source_type",
        "source_filename",
        "source_sha256",
        "source_file_ref",
        "source_file_size",
        "parse_method",
        "parsed_meta",
        "parsed_rows",
        "warnings",
        "received_by",
        "received_at",
        "created_at",
        "updated_at",
    ]

    def has_change_permission(self, request, obj=None):
        # Confirmed reference corrections must use the audited application/API path.
        if obj is not None and obj.status == QuotationLPO.STATUS_CONFIRMED:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.status == QuotationLPO.STATUS_CONFIRMED:
            return False
        return super().has_delete_permission(request, obj)

    def delete_model(self, request, obj):
        if obj.status == QuotationLPO.STATUS_CONFIRMED:
            raise PermissionDenied(QuotationLPO.DELETE_ERROR)
        return super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        if queryset.filter(status=QuotationLPO.STATUS_CONFIRMED).exists():
            raise PermissionDenied(QuotationLPO.DELETE_ERROR)
        return super().delete_queryset(request, queryset)


class ProformaInvoiceLineInline(admin.TabularInline):
    model = ProformaInvoiceLine
    extra = 0
    autocomplete_fields = ["product"]
    readonly_fields = ["line_subtotal", "vat_amount", "line_total", "created_at", "updated_at"]


@admin.register(ProformaInvoice)
class ProformaInvoiceAdmin(admin.ModelAdmin):
    list_display = ["proforma_number", "company", "quotation", "lpo_number", "lpo_date", "status", "total", "created_by", "created_at"]
    list_filter = ["status", "proforma_date", "lpo_date", "created_at"]
    search_fields = ["proforma_number", "company__name", "quotation__quotation_number", "lpo_number", "source_filename"]
    autocomplete_fields = ["company", "contact", "quotation", "created_by", "issued_by"]
    inlines = [ProformaInvoiceLineInline]
    readonly_fields = [
        "proforma_number",
        "source_type",
        "source_filename",
        "source_sha256",
        "source_file_ref",
        "source_file_size",
        "parse_method",
        "parsed_meta",
        "parsed_rows",
        "warnings",
        "subtotal",
        "vat_total",
        "total",
        "created_at",
        "updated_at",
    ]


@admin.register(CompanyPriceHistory)
class CompanyPriceHistoryAdmin(admin.ModelAdmin):
    list_display = ["company", "product", "quote_item", "unit_price", "currency", "quoted_at", "quotation"]
    list_filter = ["currency", "quoted_at"]
    search_fields = ["company__name", "product__name", "quote_item__name", "quotation__quotation_number"]
    autocomplete_fields = ["company", "product", "quote_item", "quotation", "quotation_line", "created_by"]
    readonly_fields = ["created_at"]


@admin.register(QuotationAuditLog)
class QuotationAuditLogAdmin(admin.ModelAdmin):
    list_display = ["created_at", "actor", "action", "target_type", "target_id", "company", "quotation"]
    list_filter = ["action", "created_at"]
    search_fields = ["message", "actor__username", "company__name", "quotation__quotation_number"]
    readonly_fields = ["created_at"]
