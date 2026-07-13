from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from django.utils.text import slugify


def normalize_label(value):
    return " ".join((value or "").strip().lower().split())


class Company(models.Model):
    name = models.CharField(max_length=255, unique=True)
    normalized_name = models.CharField(max_length=255, unique=True, editable=False)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    billing_address = models.TextField(blank=True)
    trn = models.CharField(max_length=50, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["normalized_name"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.normalized_name = normalize_label(self.name)
        super().save(*args, **kwargs)


class CompanyContact(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="contacts")
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    role = models.CharField(max_length=100, blank=True)
    department = models.CharField(max_length=120, blank=True)
    is_primary = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["company__name", "-is_primary", "name"]
        indexes = [
            models.Index(fields=["company", "is_primary"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return f"{self.name} - {self.company.name}"

    def save(self, *args, **kwargs):
        if self.is_primary:
            CompanyContact.objects.filter(
                company=self.company,
                is_primary=True,
            ).exclude(pk=self.pk).update(is_primary=False)
        super().save(*args, **kwargs)


class QuoteItem(models.Model):
    """
    Deprecated compatibility model.

    New quotation workflows use api.Product as the master item catalog. This
    model/table is kept temporarily so older migrations and rollback paths stay
    stable while production moves to product-backed quotations.
    """
    product = models.ForeignKey(
        "api.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_items",
    )
    name = models.CharField(max_length=255)
    normalized_name = models.CharField(max_length=255, db_index=True, editable=False)
    internal_code = models.CharField(max_length=100, blank=True, db_index=True)
    brand_text = models.CharField(max_length=200, blank=True)
    generic_name = models.CharField(max_length=200, blank=True)
    strength = models.CharField(max_length=100, blank=True)
    dosage_form = models.CharField(max_length=100, blank=True)
    pack_size = models.CharField(max_length=100, blank=True)
    unit = models.CharField(max_length=50, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["normalized_name"]),
            models.Index(fields=["internal_code"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["product"]),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.normalized_name = normalize_label(self.name)
        super().save(*args, **kwargs)


class ProductAlias(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="product_aliases",
        help_text="Blank means a global alias. Company-specific aliases override global aliases.",
    )
    product = models.ForeignKey(
        "api.Product",
        on_delete=models.CASCADE,
        related_name="quotation_aliases",
    )
    alias = models.CharField(max_length=255)
    normalized_alias = models.CharField(max_length=255, editable=False, db_index=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_product_aliases",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["company__name", "alias"]
        indexes = [
            models.Index(fields=["company", "normalized_alias"]),
            models.Index(fields=["product"]),
            models.Index(fields=["is_active"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "normalized_alias"],
                condition=models.Q(company__isnull=False),
                name="uniq_company_product_alias",
            ),
            models.UniqueConstraint(
                fields=["normalized_alias"],
                condition=models.Q(company__isnull=True),
                name="uniq_global_product_alias",
            ),
        ]

    def __str__(self):
        scope = self.company.name if self.company_id else "Global"
        return f"{scope}: {self.alias} -> {self.product.name}"

    def save(self, *args, **kwargs):
        self.normalized_alias = normalize_label(self.alias)
        super().save(*args, **kwargs)


class HistoricalImportBatch(models.Model):
    STATUS_CREATED = "created"
    STATUS_PROCESSING = "processing"
    STATUS_PARSED = "parsed"
    STATUS_NEEDS_REVIEW = "needs_review"
    STATUS_COMMITTED = "committed"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_CREATED, "Created"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_PARSED, "Parsed"),
        (STATUS_NEEDS_REVIEW, "Needs Review"),
        (STATUS_COMMITTED, "Committed"),
        (STATUS_FAILED, "Failed"),
    ]

    name = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_CREATED)
    summary = models.JSONField(default=dict, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_historical_import_batches",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["created_by"]),
        ]

    def __str__(self):
        return self.name or f"Historical import batch #{self.pk}"


class QuotationSettings(models.Model):
    STYLE_CLASSIC = "classic"
    STYLE_MODERN = "modern"
    STYLE_COMPACT = "compact"
    LOGO_LAYOUT_FULL = "full_logo_only"
    LOGO_LAYOUT_LOGO_TEXT = "logo_plus_company_text"
    LOGO_LAYOUT_ICON_TEXT = "icon_left_company_text"
    LOGO_LAYOUT_NONE = "no_logo"
    PDF_TEMPLATE_STYLE_CHOICES = [
        (STYLE_CLASSIC, "Classic"),
        (STYLE_MODERN, "Modern"),
        (STYLE_COMPACT, "Compact"),
    ]
    LOGO_LAYOUT_CHOICES = [
        (LOGO_LAYOUT_FULL, "Full logo only"),
        (LOGO_LAYOUT_LOGO_TEXT, "Logo plus company text"),
        (LOGO_LAYOUT_ICON_TEXT, "Icon left, company text"),
        (LOGO_LAYOUT_NONE, "No logo"),
    ]

    company_name = models.CharField(max_length=255, default="Al Ameen Pharmacy")
    company_name_ar = models.CharField(max_length=255, blank=True)
    address = models.TextField(default="Dubai, United Arab Emirates", blank=True)
    phone = models.CharField(max_length=80, default="+971 50 545 6388", blank=True)
    email = models.EmailField(default="alameenpharmacyllc@gmail.com", blank=True)
    trn = models.CharField(max_length=80, blank=True)
    license_number = models.CharField(max_length=80, blank=True)
    logo = models.ImageField(upload_to="quotations/logos/", blank=True, null=True)
    signature_image = models.ImageField(upload_to="quotations/signatures/", blank=True, null=True)
    stamp_image = models.ImageField(upload_to="quotations/stamps/", blank=True, null=True)
    logo_layout = models.CharField(
        max_length=40,
        choices=LOGO_LAYOUT_CHOICES,
        default=LOGO_LAYOUT_FULL,
    )
    footer_note = models.TextField(blank=True)
    default_terms = models.TextField(
        default="Prices are subject to stock availability and final confirmation. This quotation is confidential and intended for the named customer only.",
        blank=True,
    )
    payment_terms = models.TextField(default="As per mutually agreed terms.", blank=True)
    validity_days = models.PositiveIntegerField(default=30)
    prepared_by_default = models.CharField(max_length=255, blank=True)
    signature_label = models.CharField(max_length=120, default="Signature", blank=True)
    stamp_label = models.CharField(max_length=120, default="Stamp", blank=True)
    pdf_template_style = models.CharField(
        max_length=30,
        choices=PDF_TEMPLATE_STYLE_CHOICES,
        default=STYLE_CLASSIC,
    )
    primary_color = models.CharField(max_length=7, default="#0F766E")
    accent_color = models.CharField(max_length=7, default="#ECFDF5")
    show_arabic_name = models.BooleanField(default=True)
    show_trn = models.BooleanField(default=True)
    show_license_number = models.BooleanField(default=True)
    show_signature_area = models.BooleanField(default=True)
    show_stamp_area = models.BooleanField(default=True)
    ai_parsing_enabled = models.BooleanField(default=False)
    ai_auto_cleanup_enabled = models.BooleanField(default=False)
    ai_pdf_vision_enabled = models.BooleanField(default=False)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_quotation_settings",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Quotation Settings"
        verbose_name_plural = "Quotation Settings"

    def __str__(self):
        return "Quotation Settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        settings_obj, _ = cls.objects.get_or_create(pk=1)
        return settings_obj


class UserQuotationProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="quotation_profile",
    )
    signature_image = models.ImageField(upload_to="quotations/user-signatures/", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User Quotation Profile"
        verbose_name_plural = "User Quotation Profiles"

    def __str__(self):
        return f"Quotation profile for {self.user}"


class AIParseCache(models.Model):
    MODE_TEXT = "text"
    MODE_VISION = "vision"
    MODE_CHOICES = [
        (MODE_TEXT, "Text cleanup"),
        (MODE_VISION, "Vision cleanup"),
    ]

    cache_key = models.CharField(max_length=64, unique=True, db_index=True)
    source_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    context_hash = models.CharField(max_length=64, db_index=True)
    mode = models.CharField(max_length=20, choices=MODE_CHOICES)
    provider = models.CharField(max_length=40)
    model = models.CharField(max_length=120)
    result = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["source_sha256", "mode"]),
            models.Index(fields=["provider", "model"]),
        ]

    def __str__(self):
        return f"{self.provider}:{self.model}:{self.mode}:{self.cache_key[:8]}"


class AIParseLog(models.Model):
    MODE_TEXT = AIParseCache.MODE_TEXT
    MODE_VISION = AIParseCache.MODE_VISION
    MODE_CHOICES = AIParseCache.MODE_CHOICES

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quotation_ai_parse_logs",
    )
    provider = models.CharField(max_length=40)
    model = models.CharField(max_length=120)
    mode = models.CharField(max_length=20, choices=MODE_CHOICES)
    source_type = models.CharField(max_length=40, blank=True)
    source_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    context_hash = models.CharField(max_length=64, blank=True)
    cache_hit = models.BooleanField(default=False)
    text_length = models.PositiveIntegerField(default=0)
    page_count = models.PositiveIntegerField(default=0)
    image_count = models.PositiveIntegerField(default=0)
    usage = models.JSONField(default=dict, blank=True)
    success = models.BooleanField(default=False)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["provider", "model", "mode"]),
            models.Index(fields=["success", "created_at"]),
        ]

    def __str__(self):
        status = "success" if self.success else "failed"
        return f"{self.provider}:{self.mode}:{status}:{self.created_at:%Y-%m-%d %H:%M}"


class GmailOAuthConnection(models.Model):
    STATUS_CONNECTED = "connected"
    STATUS_ERROR = "error"
    STATUS_DISCONNECTED = "disconnected"
    STATUS_CHOICES = [
        (STATUS_CONNECTED, "Connected"),
        (STATUS_ERROR, "Error"),
        (STATUS_DISCONNECTED, "Disconnected"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="quotation_gmail_connection",
    )
    # The quotation workflow uses one company mailbox. The user remains the
    # OAuth credential owner/audit principal; ``is_shared`` identifies the
    # connection all quotation staff should resolve for read-only discovery.
    is_shared = models.BooleanField(default=False, db_index=True)
    email = models.EmailField(blank=True)
    google_subject = models.CharField(max_length=255, blank=True)
    access_token_encrypted = models.TextField(blank=True)
    refresh_token_encrypted = models.TextField(blank=True)
    token_expiry = models.DateTimeField(null=True, blank=True)
    scopes = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_CONNECTED)
    last_error = models.TextField(blank=True)
    connected_at = models.DateTimeField(default=timezone.now)
    disconnected_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["email"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["is_shared"],
                condition=models.Q(is_shared=True),
                name="unique_shared_gmail_connection",
            ),
        ]

    def __str__(self):
        return self.email or f"Gmail connection for {self.user}"


class ContractIntelligenceRun(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_DISCOVERING = "discovering"
    STATUS_READY = "ready"
    STATUS_ANALYZING = "analyzing"
    STATUS_REVIEW = "review"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_DISCOVERING, "Discovering"),
        (STATUS_READY, "Ready"),
        (STATUS_ANALYZING, "Analyzing"),
        (STATUS_REVIEW, "Review"),
        (STATUS_FAILED, "Failed"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contract_intelligence_runs",
    )
    target_company_name = models.CharField(max_length=255)
    gmail_query = models.TextField(blank=True)
    sender_domain_hint = models.CharField(max_length=255, blank=True)
    date_from = models.DateField(null=True, blank=True)
    date_to = models.DateField(null=True, blank=True)
    max_messages = models.PositiveIntegerField(default=100)
    discovery_batch_size = models.PositiveIntegerField(default=25)
    discovery_page_token = models.TextField(blank=True)
    discovery_exhausted = models.BooleanField(default=False)
    discovery_result_estimate = models.PositiveIntegerField(null=True, blank=True)
    include_attachments = models.BooleanField(default=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    ai_status = models.CharField(max_length=80, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_contract_intelligence_runs",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "created_at"]),
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["created_by", "created_at"]),
        ]

    def __str__(self):
        return f"{self.target_company_name} contract intelligence #{self.pk}"


class ContractIntelligenceSource(models.Model):
    CLASS_INQUIRY = "inquiry"
    CLASS_QUOTATION = "quotation"
    CLASS_LPO = "lpo"
    CLASS_FOLLOWUP = "followup"
    CLASS_IRRELEVANT = "irrelevant"
    CLASS_UNKNOWN = "unknown"
    CLASSIFICATION_CHOICES = [
        (CLASS_INQUIRY, "Inquiry / RFQ"),
        (CLASS_QUOTATION, "Quotation sent"),
        (CLASS_LPO, "LPO / order"),
        (CLASS_FOLLOWUP, "Follow-up"),
        (CLASS_IRRELEVANT, "Irrelevant"),
        (CLASS_UNKNOWN, "Unknown"),
    ]

    run = models.ForeignKey(ContractIntelligenceRun, on_delete=models.CASCADE, related_name="sources")
    gmail_connection = models.ForeignKey(
        GmailOAuthConnection,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contract_sources",
    )
    mailbox_email = models.EmailField(blank=True)
    gmail_message_id = models.CharField(max_length=255, blank=True)
    gmail_thread_id = models.CharField(max_length=255, blank=True)
    subject = models.CharField(max_length=500, blank=True)
    sender = models.CharField(max_length=500, blank=True)
    recipients = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    snippet = models.TextField(blank=True)
    body_text = models.TextField(blank=True)
    source_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    attachments = models.JSONField(default=list, blank=True)
    classification = models.CharField(max_length=30, choices=CLASSIFICATION_CHOICES, default=CLASS_UNKNOWN)
    confidence = models.FloatField(default=0.0)
    status = models.CharField(max_length=30, default="fetched")
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-sent_at", "-created_at"]
        indexes = [
            models.Index(fields=["run", "classification"]),
            models.Index(fields=["gmail_message_id"]),
            models.Index(fields=["sent_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "gmail_message_id"],
                condition=models.Q(gmail_message_id__gt=""),
                name="uniq_contract_run_gmail_message",
            ),
        ]

    def __str__(self):
        return self.subject or f"Contract source #{self.pk}"


class ContractIntelligenceItem(models.Model):
    STATUS_SUGGESTED = "suggested"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_NEEDS_REVIEW = "needs_review"
    STATUS_CHOICES = [
        (STATUS_SUGGESTED, "Suggested"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_NEEDS_REVIEW, "Needs Review"),
    ]

    run = models.ForeignKey(ContractIntelligenceRun, on_delete=models.CASCADE, related_name="items")
    source = models.ForeignKey(
        ContractIntelligenceSource,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="items",
    )
    product = models.ForeignKey(
        "api.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contract_intelligence_items",
    )
    original_item_name = models.CharField(max_length=500)
    normalized_item_name = models.CharField(max_length=500, db_index=True)
    suggested_item_name = models.CharField(max_length=500, blank=True)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    unit = models.CharField(max_length=80, blank=True)
    unit_price = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    currency = models.CharField(max_length=3, default="AED")
    requested_date = models.DateField(null=True, blank=True)
    project = models.CharField(max_length=255, blank=True)
    contact_text = models.CharField(max_length=255, blank=True)
    source_text = models.TextField(blank=True)
    source_filename = models.CharField(max_length=255, blank=True)
    source_page = models.CharField(max_length=30, blank=True)
    confidence = models.FloatField(default=0.0)
    ai_reason = models.TextField(blank=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_SUGGESTED)
    review_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["normalized_item_name", "-requested_date", "-created_at"]
        indexes = [
            models.Index(fields=["run", "status"]),
            models.Index(fields=["normalized_item_name"]),
            models.Index(fields=["requested_date"]),
            models.Index(fields=["product"]),
        ]

    def __str__(self):
        return self.suggested_item_name or self.original_item_name

    def save(self, *args, **kwargs):
        self.normalized_item_name = normalize_label(self.suggested_item_name or self.original_item_name)
        super().save(*args, **kwargs)


class Inquiry(models.Model):
    SOURCE_MANUAL = "manual"
    SOURCE_IMPORTED = "imported"
    SOURCE_CHOICES = [
        (SOURCE_MANUAL, "Manual"),
        (SOURCE_IMPORTED, "Imported"),
    ]

    SOURCE_TYPE_MANUAL = "manual"
    SOURCE_TYPE_PASTED_TEXT = "pasted_text"
    SOURCE_TYPE_EXCEL = "excel"
    SOURCE_TYPE_PDF = "pdf"
    SOURCE_TYPE_CHOICES = [
        (SOURCE_TYPE_MANUAL, "Manual"),
        (SOURCE_TYPE_PASTED_TEXT, "Pasted Text"),
        (SOURCE_TYPE_EXCEL, "Excel"),
        (SOURCE_TYPE_PDF, "PDF"),
    ]

    STATUS_DRAFT = "draft"
    STATUS_QUOTED = "quoted"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_QUOTED, "Quoted"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="inquiries")
    contact = models.ForeignKey(
        CompanyContact,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inquiries",
    )
    source = models.CharField(max_length=30, choices=SOURCE_CHOICES, default=SOURCE_MANUAL)
    source_type = models.CharField(max_length=30, choices=SOURCE_TYPE_CHOICES, default=SOURCE_TYPE_MANUAL)
    source_filename = models.CharField(max_length=255, blank=True)
    source_mime_type = models.CharField(max_length=120, blank=True)
    source_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    source_file_ref = models.CharField(max_length=500, blank=True)
    source_file_size = models.PositiveIntegerField(null=True, blank=True)
    parse_method = models.CharField(max_length=80, blank=True)
    parse_meta = models.JSONField(default=dict, blank=True)
    subject = models.CharField(max_length=255, blank=True)
    original_text = models.TextField(blank=True)
    received_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_inquiries",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-received_at", "-created_at"]
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["received_at"]),
            models.Index(fields=["created_by"]),
        ]

    def __str__(self):
        return self.subject or f"Inquiry #{self.pk}"


class InquiryLine(models.Model):
    MATCH_UNRESOLVED = "unresolved"
    MATCH_CONFIRMED = "confirmed"
    MATCH_IGNORED = "ignored"
    MATCH_STATUS_CHOICES = [
        (MATCH_UNRESOLVED, "Unresolved"),
        (MATCH_CONFIRMED, "Confirmed"),
        (MATCH_IGNORED, "Ignored"),
    ]
    PARSE_MANUAL = "manual"
    PARSE_PARSED = "parsed"
    PARSE_NEEDS_REVIEW = "needs_review"
    PARSE_UNPARSED = "unparsed"
    PARSE_STATUS_CHOICES = [
        (PARSE_MANUAL, "Manual"),
        (PARSE_PARSED, "Parsed"),
        (PARSE_NEEDS_REVIEW, "Needs Review"),
        (PARSE_UNPARSED, "Unparsed"),
    ]

    inquiry = models.ForeignKey(Inquiry, on_delete=models.CASCADE, related_name="lines")
    raw_name = models.CharField(max_length=255)
    raw_line = models.TextField(blank=True)
    normalized_name = models.CharField(max_length=255, db_index=True, editable=False)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    unit = models.CharField(max_length=50, blank=True)
    unit_price = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    notes = models.TextField(blank=True)
    matched_quote_item = models.ForeignKey(
        QuoteItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inquiry_lines",
    )
    matched_product = models.ForeignKey(
        "api.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quotation_inquiry_lines",
    )
    match_reason = models.CharField(max_length=255, blank=True)
    match_status = models.CharField(
        max_length=30,
        choices=MATCH_STATUS_CHOICES,
        default=MATCH_UNRESOLVED,
    )
    parse_status = models.CharField(max_length=30, choices=PARSE_STATUS_CHOICES, default=PARSE_MANUAL)
    parse_confidence = models.FloatField(default=1.0)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "id"]
        indexes = [
            models.Index(fields=["inquiry", "sort_order"]),
            models.Index(fields=["match_status"]),
            models.Index(fields=["matched_quote_item"]),
            models.Index(fields=["matched_product"]),
        ]

    def __str__(self):
        return self.raw_name

    def save(self, *args, **kwargs):
        self.normalized_name = normalize_label(self.raw_name)
        super().save(*args, **kwargs)


class HistoricalPriceImport(models.Model):
    STATUS_PARSED = "parsed"
    STATUS_REVIEWED = "reviewed"
    STATUS_COMMITTED = "committed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_PARSED, "Parsed"),
        (STATUS_REVIEWED, "Reviewed"),
        (STATUS_COMMITTED, "Committed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    SOURCE_TYPE_PDF = "pdf"
    SOURCE_TYPE_CHOICES = [
        (SOURCE_TYPE_PDF, "PDF"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="historical_price_imports",
    )
    batch = models.ForeignKey(
        HistoricalImportBatch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="imports",
    )
    suggested_company_name = models.CharField(max_length=255, blank=True)
    source_type = models.CharField(max_length=30, choices=SOURCE_TYPE_CHOICES, default=SOURCE_TYPE_PDF)
    source_filename = models.CharField(max_length=255, blank=True)
    source_mime_type = models.CharField(max_length=120, blank=True)
    source_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    source_file_ref = models.CharField(max_length=500, blank=True)
    source_file_size = models.PositiveIntegerField(null=True, blank=True)
    parse_method = models.CharField(max_length=80, blank=True)
    parse_meta = models.JSONField(default=dict, blank=True)
    document_number = models.CharField(max_length=80, blank=True, db_index=True)
    document_date = models.DateField(null=True, blank=True, db_index=True)
    currency = models.CharField(max_length=3, default="AED")
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    vat_total = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    total = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_PARSED)
    created_quotation = models.OneToOneField(
        "Quotation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="historical_price_import",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_historical_price_imports",
    )
    committed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="committed_historical_price_imports",
    )
    committed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["batch", "status"]),
            models.Index(fields=["source_sha256"]),
            models.Index(fields=["company", "document_date"]),
            models.Index(fields=["document_number"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return self.document_number or self.source_filename or f"Historical import #{self.pk}"


class HistoricalPriceImportLine(models.Model):
    STATUS_NEEDS_REVIEW = "needs_review"
    STATUS_READY = "ready"
    STATUS_SKIPPED = "skipped"
    STATUS_DUPLICATE = "duplicate"
    STATUS_COMMITTED = "committed"
    STATUS_CHOICES = [
        (STATUS_NEEDS_REVIEW, "Needs Review"),
        (STATUS_READY, "Ready"),
        (STATUS_SKIPPED, "Skipped"),
        (STATUS_DUPLICATE, "Duplicate"),
        (STATUS_COMMITTED, "Committed"),
    ]

    historical_import = models.ForeignKey(HistoricalPriceImport, on_delete=models.CASCADE, related_name="lines")
    quote_item = models.ForeignKey(
        QuoteItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="historical_import_lines",
    )
    product = models.ForeignKey(
        "api.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="historical_import_lines",
    )
    match_reason = models.CharField(max_length=255, blank=True)
    raw_line = models.TextField(blank=True)
    item_name = models.CharField(max_length=255)
    normalized_item_name = models.CharField(max_length=255, db_index=True, editable=False)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    unit = models.CharField(max_length=50, blank=True)
    unit_price = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    vat_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    line_total = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    serial_no = models.CharField(max_length=30, blank=True)
    source_page = models.PositiveIntegerField(null=True, blank=True)
    source_row = models.PositiveIntegerField(null=True, blank=True)
    parse_confidence = models.FloatField(default=0.0)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_NEEDS_REVIEW)
    duplicate_reason = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "id"]
        indexes = [
            models.Index(fields=["historical_import", "sort_order"]),
            models.Index(fields=["quote_item"]),
            models.Index(fields=["product"]),
            models.Index(fields=["status"]),
            models.Index(fields=["normalized_item_name"]),
        ]

    def __str__(self):
        return self.item_name

    def save(self, *args, **kwargs):
        self.normalized_item_name = normalize_label(self.item_name)
        super().save(*args, **kwargs)


class HistoricalImportAISuggestion(models.Model):
    TYPE_COMPANY = "company"
    TYPE_LINE = "line"
    TYPE_CHOICES = [
        (TYPE_COMPANY, "Company"),
        (TYPE_LINE, "Line"),
    ]

    ACTION_MATCH_EXISTING_PRODUCT = "match_existing_product"
    ACTION_CREATE_COMPANY_ALIAS = "create_company_alias"
    ACTION_CREATE_NEW_PRODUCT = "create_new_product"
    ACTION_NEEDS_MANUAL_REVIEW = "needs_manual_review"
    ACTION_SKIP = "skip"
    ACTION_MATCH_EXISTING_COMPANY = "match_existing_company"
    ACTION_CREATE_NEW_COMPANY = "create_new_company"
    ACTION_CHOICES = [
        (ACTION_MATCH_EXISTING_PRODUCT, "Match Existing Product"),
        (ACTION_CREATE_COMPANY_ALIAS, "Create Company Alias"),
        (ACTION_CREATE_NEW_PRODUCT, "Create New Product"),
        (ACTION_NEEDS_MANUAL_REVIEW, "Needs Manual Review"),
        (ACTION_SKIP, "Skip"),
        (ACTION_MATCH_EXISTING_COMPANY, "Match Existing Company"),
        (ACTION_CREATE_NEW_COMPANY, "Create New Company"),
    ]

    STATUS_PENDING = "pending"
    STATUS_APPLIED = "applied"
    STATUS_REJECTED = "rejected"
    STATUS_CONFLICT = "conflict"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPLIED, "Applied"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_CONFLICT, "Conflict"),
        (STATUS_FAILED, "Failed"),
    ]

    batch = models.ForeignKey(
        HistoricalImportBatch,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="ai_suggestions",
    )
    historical_import = models.ForeignKey(
        HistoricalPriceImport,
        on_delete=models.CASCADE,
        related_name="ai_suggestions",
    )
    line = models.ForeignKey(
        HistoricalPriceImportLine,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="ai_suggestions",
    )
    suggestion_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default=TYPE_LINE)
    action = models.CharField(max_length=40, choices=ACTION_CHOICES)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_PENDING)
    suggested_company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="historical_ai_company_suggestions",
    )
    suggested_product = models.ForeignKey(
        "api.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="historical_ai_product_suggestions",
    )
    alias_text = models.CharField(max_length=255, blank=True)
    proposed_company_name = models.CharField(max_length=255, blank=True)
    proposed_product_name = models.CharField(max_length=255, blank=True)
    proposed_unit = models.CharField(max_length=80, blank=True)
    proposed_pack_size = models.CharField(max_length=120, blank=True)
    proposed_dosage = models.CharField(max_length=120, blank=True)
    confidence = models.FloatField(default=0.0)
    reason = models.TextField(blank=True)
    candidate_companies = models.JSONField(default=list, blank=True)
    candidate_products = models.JSONField(default=list, blank=True)
    raw_ai_payload = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_historical_ai_suggestions",
    )
    applied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="applied_historical_ai_suggestions",
    )
    applied_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["historical_import", "line__sort_order", "suggestion_type", "id"]
        indexes = [
            models.Index(fields=["batch", "status"]),
            models.Index(fields=["historical_import", "status"]),
            models.Index(fields=["line", "status"]),
            models.Index(fields=["action"]),
            models.Index(fields=["confidence"]),
        ]

    def __str__(self):
        target = self.line.item_name if self.line_id else self.historical_import
        return f"{self.action} for {target}"


class Quotation(models.Model):
    PAYMENT_CREDIT_30 = "credit_30_days"
    PAYMENT_CREDIT_60 = "credit_60_days"
    PAYMENT_ADVANCE_100 = "advance_100"
    PAYMENT_PDC_30 = "pdc_30_days"
    PAYMENT_CASH = "cash"
    PAYMENT_PDC_60 = "pdc_60_days"
    PAYMENT_AS_PER_AGREEMENT = "as_per_agreement"
    PAYMENT_TERM_CHOICES = [
        (PAYMENT_CREDIT_30, "Credit 30 days"),
        (PAYMENT_CREDIT_60, "Credit 60 days"),
        (PAYMENT_ADVANCE_100, "100% advance"),
        (PAYMENT_PDC_30, "PDC 30 days"),
        (PAYMENT_CASH, "Cash"),
        (PAYMENT_PDC_60, "PDC 60 days"),
        (PAYMENT_AS_PER_AGREEMENT, "As per agreement"),
    ]

    STATUS_DRAFT = "draft"
    STATUS_PENDING_REVIEW = "pending_review"
    STATUS_APPROVED = "approved"
    STATUS_FINALIZED = "finalized"
    STATUS_SENT = "sent"
    STATUS_REVISED = "revised"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_PENDING_REVIEW, "Pending Review"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_FINALIZED, "Finalized"),
        (STATUS_SENT, "Sent"),
        (STATUS_REVISED, "Revised"),
        (STATUS_CANCELLED, "Cancelled"),
    ]
    EDITABLE_STATUSES = {STATUS_DRAFT, STATUS_PENDING_REVIEW, STATUS_APPROVED}

    OUTCOME_PENDING = "pending"
    OUTCOME_WON = "won"
    OUTCOME_LOST = "lost"
    OUTCOME_PARTIAL = "partial"
    OUTCOME_EXPIRED = "expired"
    OUTCOME_CANCELLED = "cancelled"
    OUTCOME_STATUS_CHOICES = [
        (OUTCOME_PENDING, "Pending"),
        (OUTCOME_WON, "Won"),
        (OUTCOME_LOST, "Lost"),
        (OUTCOME_PARTIAL, "Partial"),
        (OUTCOME_EXPIRED, "Expired"),
        (OUTCOME_CANCELLED, "Cancelled"),
    ]

    FOLLOWUP_OPEN = "open"
    FOLLOWUP_DUE = "due"
    FOLLOWUP_OVERDUE = "overdue"
    FOLLOWUP_DONE = "done"
    FOLLOWUP_NOT_REQUIRED = "not_required"
    FOLLOWUP_STATUS_CHOICES = [
        (FOLLOWUP_OPEN, "Open"),
        (FOLLOWUP_DUE, "Due"),
        (FOLLOWUP_OVERDUE, "Overdue"),
        (FOLLOWUP_DONE, "Done"),
        (FOLLOWUP_NOT_REQUIRED, "Not required"),
    ]

    CONTACT_CALL = "call"
    CONTACT_WHATSAPP = "whatsapp"
    CONTACT_EMAIL = "email"
    CONTACT_VISIT = "visit"
    CONTACT_OTHER = "other"
    FOLLOWUP_CONTACT_METHOD_CHOICES = [
        (CONTACT_CALL, "Call"),
        (CONTACT_WHATSAPP, "WhatsApp"),
        (CONTACT_EMAIL, "Email"),
        (CONTACT_VISIT, "Visit"),
        (CONTACT_OTHER, "Other"),
    ]

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="quotations")
    contact = models.ForeignKey(
        CompanyContact,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quotations",
    )
    inquiry = models.ForeignKey(
        Inquiry,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quotations",
    )
    quotation_number = models.CharField(max_length=50, unique=True, blank=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    version = models.PositiveIntegerField(default=1)
    parent = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="revisions",
    )
    valid_until = models.DateField(null=True, blank=True)
    currency = models.CharField(max_length=3, default="AED")
    payment_terms = models.CharField(
        max_length=40,
        choices=PAYMENT_TERM_CHOICES,
        default=PAYMENT_AS_PER_AGREEMENT,
    )
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    vat_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    notes = models.TextField(blank=True)
    internal_notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_quotations",
    )
    finalized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="finalized_quotations",
    )
    finalized_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    outcome_status = models.CharField(
        max_length=30,
        choices=OUTCOME_STATUS_CHOICES,
        default=OUTCOME_PENDING,
        db_index=True,
    )
    outcome_status_is_manual = models.BooleanField(default=False)
    outcome_date = models.DateField(null=True, blank=True)
    outcome_notes = models.TextField(blank=True)
    outcome_closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="closed_quotation_outcomes",
    )
    outcome_closed_at = models.DateTimeField(null=True, blank=True)
    outcome_last_updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_quotation_outcomes",
    )
    outcome_last_updated_at = models.DateTimeField(null=True, blank=True)
    last_contacted_at = models.DateTimeField(null=True, blank=True)
    next_follow_up_date = models.DateField(null=True, blank=True, db_index=True)
    follow_up_status = models.CharField(
        max_length=30,
        choices=FOLLOWUP_STATUS_CHOICES,
        default=FOLLOWUP_OPEN,
        db_index=True,
    )
    follow_up_notes = models.TextField(blank=True)
    follow_up_contact_method = models.CharField(
        max_length=30,
        choices=FOLLOWUP_CONTACT_METHOD_CHOICES,
        blank=True,
    )
    po_evidence_last_scanned_at = models.DateTimeField(null=True, blank=True)
    po_evidence_last_scan_count = models.PositiveIntegerField(default=0)
    po_evidence_last_scan_error = models.TextField(blank=True)
    is_historical_import = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["quotation_number"]),
            models.Index(fields=["company", "status"]),
            models.Index(fields=["status"]),
            models.Index(fields=["created_by"]),
            models.Index(fields=["parent", "version"]),
            models.Index(fields=["is_historical_import"]),
            models.Index(fields=["outcome_status", "created_at"]),
            models.Index(fields=["follow_up_status", "next_follow_up_date"]),
        ]

    def __str__(self):
        return self.quotation_number or f"Quotation #{self.pk}"

    @property
    def is_editable(self):
        return self.status in self.EDITABLE_STATUSES

    def save(self, *args, **kwargs):
        generated_number = not self.quotation_number
        if generated_number:
            self.quotation_number = self._generate_quotation_number()
        try:
            with transaction.atomic():
                super().save(*args, **kwargs)
        except IntegrityError:
            if not generated_number or not self._state.adding:
                raise
            for _ in range(3):
                self.quotation_number = self._generate_quotation_number()
                try:
                    with transaction.atomic():
                        super().save(*args, **kwargs)
                    return
                except IntegrityError:
                    continue
            raise

    @classmethod
    def _generate_quotation_number(cls):
        date_part = timezone.now().strftime("%Y%m%d")
        base = f"QT-{date_part}"
        last = (
            cls.objects.filter(quotation_number__startswith=base)
            .order_by("-quotation_number")
            .values_list("quotation_number", flat=True)
            .first()
        )
        if not last:
            return f"{base}-0001"
        try:
            next_number = int(last.rsplit("-", 1)[1]) + 1
        except (IndexError, ValueError):
            next_number = cls.objects.filter(quotation_number__startswith=base).count() + 1
        candidate = f"{base}-{next_number:04d}"
        while cls.objects.filter(quotation_number=candidate).exists():
            next_number += 1
            candidate = f"{base}-{next_number:04d}"
        return candidate


class QuotationLine(models.Model):
    MATCH_UNRESOLVED = InquiryLine.MATCH_UNRESOLVED
    MATCH_CONFIRMED = InquiryLine.MATCH_CONFIRMED
    MATCH_IGNORED = InquiryLine.MATCH_IGNORED
    MATCH_STATUS_CHOICES = InquiryLine.MATCH_STATUS_CHOICES

    OUTCOME_PENDING = "pending"
    OUTCOME_ACCEPTED = "accepted"
    OUTCOME_REJECTED = "rejected"
    OUTCOME_UNAVAILABLE_MISSING = "unavailable_missing"
    OUTCOME_SUBSTITUTED = "substituted"
    OUTCOME_QUANTITY_CHANGED = "quantity_changed"
    OUTCOME_STATUS_CHOICES = [
        (OUTCOME_PENDING, "Pending"),
        (OUTCOME_ACCEPTED, "Accepted"),
        (OUTCOME_REJECTED, "Rejected"),
        (OUTCOME_UNAVAILABLE_MISSING, "Unavailable / missing"),
        (OUTCOME_SUBSTITUTED, "Substituted"),
        (OUTCOME_QUANTITY_CHANGED, "Quantity changed"),
    ]

    REASON_PRICE_TOO_HIGH = "price_too_high"
    REASON_NOT_AVAILABLE = "not_available"
    REASON_NO_LONGER_REQUIRED = "customer_no_longer_required"
    REASON_COMPETITOR_SELECTED = "competitor_selected"
    REASON_ALTERNATE_BRAND = "alternate_brand_selected"
    REASON_QUANTITY_CHANGED = "quantity_changed"
    REASON_DELIVERY_TIME = "delivery_time_issue"
    REASON_CUSTOMER_CANCELLED = "customer_cancelled"
    REASON_NO_RESPONSE = "no_response"
    REASON_UNKNOWN = "unknown"
    OUTCOME_REASON_CHOICES = [
        (REASON_PRICE_TOO_HIGH, "Price too high"),
        (REASON_NOT_AVAILABLE, "Not available"),
        (REASON_NO_LONGER_REQUIRED, "Customer no longer required"),
        (REASON_COMPETITOR_SELECTED, "Competitor selected"),
        (REASON_ALTERNATE_BRAND, "Alternate brand selected"),
        (REASON_QUANTITY_CHANGED, "Quantity changed"),
        (REASON_DELIVERY_TIME, "Delivery time issue"),
        (REASON_CUSTOMER_CANCELLED, "Customer cancelled"),
        (REASON_NO_RESPONSE, "No response"),
        (REASON_UNKNOWN, "Unknown"),
    ]

    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE, related_name="lines")
    inquiry_line = models.ForeignKey(
        InquiryLine,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quotation_lines",
    )
    quote_item = models.ForeignKey(
        QuoteItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quotation_lines",
    )
    product = models.ForeignKey(
        "api.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quotation_lines",
    )
    product_image = models.ForeignKey(
        "api.ProductImage",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quotation_lines",
    )
    include_product_image = models.BooleanField(default=False)
    match_reason = models.CharField(max_length=255, blank=True)
    item_name_snapshot = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal("1.000"),
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    unit = models.CharField(max_length=50, blank=True)
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    line_subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    vat_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    line_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    match_status = models.CharField(
        max_length=30,
        choices=MATCH_STATUS_CHOICES,
        default=MATCH_UNRESOLVED,
    )
    sort_order = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)
    outcome_status = models.CharField(
        max_length=30,
        choices=OUTCOME_STATUS_CHOICES,
        default=OUTCOME_PENDING,
        db_index=True,
    )
    accepted_quantity = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    accepted_unit_price = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    accepted_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    lost_value = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    outcome_reason = models.CharField(max_length=40, choices=OUTCOME_REASON_CHOICES, blank=True)
    outcome_notes = models.TextField(blank=True)
    quoted_gross_profit = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    accepted_gross_profit = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    lost_gross_profit = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "id"]
        indexes = [
            models.Index(fields=["quotation", "sort_order"]),
            models.Index(fields=["quote_item"]),
            models.Index(fields=["product"]),
            models.Index(fields=["match_status"]),
            models.Index(fields=["outcome_status"]),
            models.Index(fields=["outcome_reason"]),
        ]

    def __str__(self):
        return f"{self.quotation} - {self.item_name_snapshot}"

    def save(self, *args, **kwargs):
        if self.product and not self.item_name_snapshot:
            self.item_name_snapshot = self.product.name
        elif self.quote_item and not self.item_name_snapshot:
            self.item_name_snapshot = self.quote_item.name
        if self.unit_price is None:
            self.line_subtotal = Decimal("0.00")
            self.vat_amount = Decimal("0.00")
            self.line_total = Decimal("0.00")
        else:
            subtotal = Decimal(self.quantity) * Decimal(self.unit_price)
            vat = subtotal * (Decimal(self.vat_rate) / Decimal("100"))
            self.line_subtotal = subtotal.quantize(Decimal("0.01"))
            self.vat_amount = vat.quantize(Decimal("0.01"))
            self.line_total = (self.line_subtotal + self.vat_amount).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)


class QuotationPOEvidence(models.Model):
    STATUS_CANDIDATE = "candidate"
    STATUS_AMBIGUOUS = "ambiguous"
    STATUS_SUPERSEDED = "superseded"
    STATUS_PARSED = "parsed"
    STATUS_NOT_RELEVANT = "not_relevant"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_CANDIDATE, "Candidate"),
        (STATUS_AMBIGUOUS, "Ambiguous"),
        (STATUS_SUPERSEDED, "Superseded"),
        (STATUS_PARSED, "Parsed"),
        (STATUS_NOT_RELEVANT, "Not relevant"),
        (STATUS_FAILED, "Failed"),
    ]

    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE, related_name="po_evidence")
    gmail_connection = models.ForeignKey(
        GmailOAuthConnection,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="po_evidence",
    )
    mailbox_email = models.EmailField(blank=True)
    gmail_message_id = models.CharField(max_length=255, blank=True, db_index=True)
    gmail_thread_id = models.CharField(max_length=255, blank=True, db_index=True)
    sender = models.CharField(max_length=500, blank=True)
    recipients = models.TextField(blank=True)
    subject = models.CharField(max_length=500, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    snippet = models.TextField(blank=True)
    extracted_text = models.TextField(blank=True)
    attachments = models.JSONField(default=list, blank=True)
    source_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    matching_reason = models.TextField(blank=True)
    confidence = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_CANDIDATE)
    error = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_quote_po_evidence",
    )
    link_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_quote_po_evidence",
    )
    link_approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-confidence", "-sent_at", "-created_at"]
        indexes = [
            models.Index(fields=["quotation", "status"]),
            models.Index(fields=["quotation", "confidence"]),
            models.Index(fields=["sent_at"]),
            models.Index(fields=["source_sha256"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["quotation", "gmail_message_id"],
                condition=~models.Q(gmail_message_id=""),
                name="unique_quote_po_evidence_message",
            )
        ]

    def __str__(self):
        return self.subject or f"PO evidence for {self.quotation}"


class QuotationOutcomePOImport(models.Model):
    SOURCE_PASTED_TEXT = "pasted_text"
    SOURCE_FILE = "file"
    SOURCE_GMAIL = "gmail"
    SOURCE_TYPE_CHOICES = [
        (SOURCE_PASTED_TEXT, "Pasted text"),
        (SOURCE_FILE, "File"),
        (SOURCE_GMAIL, "Gmail evidence"),
    ]

    STATUS_PARSED = "parsed"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PARSED, "Parsed"),
        (STATUS_FAILED, "Failed"),
    ]

    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE, related_name="outcome_po_imports")
    gmail_evidence = models.ForeignKey(
        QuotationPOEvidence,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="po_imports",
    )
    source_type = models.CharField(max_length=30, choices=SOURCE_TYPE_CHOICES)
    source_filename = models.CharField(max_length=255, blank=True)
    source_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    source_file_ref = models.CharField(max_length=500, blank=True)
    parse_method = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_PARSED)
    parsed_rows = models.JSONField(default=list, blank=True)
    suggestions = models.JSONField(default=list, blank=True)
    unmatched_po_rows = models.JSONField(default=list, blank=True)
    missing_quote_line_ids = models.JSONField(default=list, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_outcome_po_imports",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["quotation", "created_at"]),
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["source_sha256"]),
        ]

    def __str__(self):
        return f"PO outcome import for {self.quotation}"


class QuotationLPO(models.Model):
    STATUS_RECEIVED = "received"
    STATUS_PARSED = "parsed"
    STATUS_NEEDS_REVIEW = "needs_review"
    STATUS_CONFIRMED = "confirmed"
    STATUS_CHOICES = [
        (STATUS_RECEIVED, "Received"),
        (STATUS_PARSED, "Parsed"),
        (STATUS_NEEDS_REVIEW, "Needs review"),
        (STATUS_CONFIRMED, "Confirmed"),
    ]

    SOURCE_FILE = "file"
    SOURCE_PASTED_TEXT = "pasted_text"
    SOURCE_GMAIL = "gmail"
    SOURCE_TYPE_CHOICES = [
        (SOURCE_FILE, "File"),
        (SOURCE_PASTED_TEXT, "Pasted text"),
        (SOURCE_GMAIL, "Gmail evidence"),
    ]

    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE, related_name="lpos")
    gmail_evidence = models.OneToOneField(
        QuotationPOEvidence,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="canonical_lpo",
    )
    gmail_message_id = models.CharField(max_length=255, blank=True, db_index=True)
    mailbox_email = models.EmailField(blank=True)
    source_type = models.CharField(max_length=30, choices=SOURCE_TYPE_CHOICES, default=SOURCE_FILE)
    source_filename = models.CharField(max_length=255, blank=True)
    source_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    source_file_ref = models.CharField(max_length=500, blank=True)
    source_file_size = models.PositiveIntegerField(default=0)
    parse_method = models.CharField(max_length=100, blank=True)
    lpo_number = models.CharField(max_length=120, blank=True, db_index=True)
    lpo_date = models.DateField(null=True, blank=True, db_index=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_RECEIVED, db_index=True)
    parsed_meta = models.JSONField(default=dict, blank=True)
    parsed_rows = models.JSONField(default=list, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="received_quotation_lpos",
    )
    received_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-received_at", "-id"]
        indexes = [
            models.Index(fields=["quotation", "received_at"]),
            models.Index(fields=["quotation", "status"]),
            models.Index(fields=["source_sha256"]),
            models.Index(fields=["lpo_date"]),
        ]

    def __str__(self):
        label = self.lpo_number or self.source_filename or f"LPO #{self.pk}"
        return f"{label} for {self.quotation}"


class ProformaInvoice(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_ISSUED = "issued"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_ISSUED, "Issued"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    SOURCE_FILE = "file"
    SOURCE_PASTED_TEXT = "pasted_text"
    SOURCE_TYPE_CHOICES = [
        (SOURCE_FILE, "File"),
        (SOURCE_PASTED_TEXT, "Pasted text"),
    ]

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="proforma_invoices")
    contact = models.ForeignKey(
        CompanyContact,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="proforma_invoices",
    )
    quotation = models.ForeignKey(
        Quotation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="standalone_proformas",
    )
    proforma_number = models.CharField(max_length=50, unique=True, blank=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True)
    proforma_date = models.DateField(default=timezone.localdate, db_index=True)
    currency = models.CharField(max_length=3, default="AED")
    lpo_number = models.CharField(max_length=120, blank=True, db_index=True)
    lpo_date = models.DateField(null=True, blank=True, db_index=True)
    source_type = models.CharField(max_length=30, choices=SOURCE_TYPE_CHOICES, blank=True)
    source_filename = models.CharField(max_length=255, blank=True)
    source_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    source_file_ref = models.CharField(max_length=500, blank=True)
    source_file_size = models.PositiveIntegerField(default=0)
    parse_method = models.CharField(max_length=100, blank=True)
    parsed_meta = models.JSONField(default=dict, blank=True)
    parsed_rows = models.JSONField(default=list, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    vat_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_proforma_invoices",
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_proforma_invoices",
    )
    issued_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["proforma_number"]),
            models.Index(fields=["company", "status"]),
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["lpo_number"]),
            models.Index(fields=["lpo_date"]),
            models.Index(fields=["source_sha256"]),
        ]

    def __str__(self):
        return self.proforma_number or f"Proforma #{self.pk}"

    def save(self, *args, **kwargs):
        generated_number = not self.proforma_number
        if generated_number:
            self.proforma_number = self._generate_proforma_number()
        try:
            with transaction.atomic():
                super().save(*args, **kwargs)
        except IntegrityError:
            if not generated_number or not self._state.adding:
                raise
            for _ in range(3):
                self.proforma_number = self._generate_proforma_number()
                try:
                    with transaction.atomic():
                        super().save(*args, **kwargs)
                    return
                except IntegrityError:
                    continue
            raise

    @classmethod
    def _generate_proforma_number(cls):
        date_part = timezone.now().strftime("%Y%m%d")
        base = f"PI-{date_part}"
        last = (
            cls.objects.filter(proforma_number__startswith=base)
            .order_by("-proforma_number")
            .values_list("proforma_number", flat=True)
            .first()
        )
        if not last:
            return f"{base}-0001"
        try:
            next_number = int(last.rsplit("-", 1)[1]) + 1
        except (IndexError, ValueError):
            next_number = cls.objects.filter(proforma_number__startswith=base).count() + 1
        candidate = f"{base}-{next_number:04d}"
        while cls.objects.filter(proforma_number=candidate).exists():
            next_number += 1
            candidate = f"{base}-{next_number:04d}"
        return candidate


class ProformaInvoiceLine(models.Model):
    proforma = models.ForeignKey(ProformaInvoice, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(
        "api.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="proforma_invoice_lines",
    )
    item_name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal("1.000"),
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    unit = models.CharField(max_length=50, blank=True)
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    line_subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    vat_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    line_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    sort_order = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "id"]
        indexes = [
            models.Index(fields=["proforma", "sort_order"]),
            models.Index(fields=["product"]),
        ]

    def __str__(self):
        return f"{self.proforma} - {self.item_name}"

    def save(self, *args, **kwargs):
        if self.product and not self.item_name:
            self.item_name = self.product.name
        if self.unit_price is None:
            self.line_subtotal = Decimal("0.00")
            self.vat_amount = Decimal("0.00")
            self.line_total = Decimal("0.00")
        else:
            subtotal = Decimal(self.quantity) * Decimal(self.unit_price)
            vat = subtotal * (Decimal(self.vat_rate) / Decimal("100"))
            self.line_subtotal = subtotal.quantize(Decimal("0.01"))
            self.vat_amount = vat.quantize(Decimal("0.01"))
            self.line_total = (self.line_subtotal + self.vat_amount).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)


class CompanyPriceHistory(models.Model):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="price_history")
    quote_item = models.ForeignKey(
        QuoteItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="company_price_history",
    )
    product = models.ForeignKey(
        "api.Product",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="company_price_history",
    )
    quotation = models.ForeignKey(Quotation, on_delete=models.PROTECT, related_name="price_history_entries")
    quotation_line = models.OneToOneField(
        QuotationLine,
        on_delete=models.PROTECT,
        related_name="price_history_entry",
    )
    unit_price = models.DecimalField(max_digits=12, decimal_places=3)
    currency = models.CharField(max_length=3, default="AED")
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("1.000"))
    unit = models.CharField(max_length=50, blank=True)
    quoted_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_price_history",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-quoted_at", "-created_at"]
        indexes = [
            models.Index(fields=["company", "quote_item", "quoted_at"]),
            models.Index(fields=["company", "product", "quoted_at"]),
            models.Index(fields=["quotation"]),
        ]

    def __str__(self):
        item_name = self.product.name if self.product_id else (self.quote_item.name if self.quote_item_id else "Unknown item")
        return f"{self.company.name} - {item_name} - {self.unit_price}"


class QuotationAuditLog(models.Model):
    ACTION_CREATED = "created"
    ACTION_UPDATED = "updated"
    ACTION_DELETED = "deleted"
    ACTION_STATUS_CHANGED = "status_changed"
    ACTION_FINALIZED = "finalized"
    ACTION_REVISED = "revised"
    ACTION_PDF_DOWNLOADED = "pdf_downloaded"
    ACTION_IMPORTED = "imported"
    ACTION_LPO_UPLOADED = "lpo_uploaded"
    ACTION_PROFORMA_DOWNLOADED = "proforma_downloaded"
    ACTION_OUTCOME_UPDATED = "outcome_updated"
    ACTION_FOLLOWUP_UPDATED = "followup_updated"
    ACTION_CHOICES = [
        (ACTION_CREATED, "Created"),
        (ACTION_UPDATED, "Updated"),
        (ACTION_DELETED, "Deleted"),
        (ACTION_STATUS_CHANGED, "Status Changed"),
        (ACTION_FINALIZED, "Finalized"),
        (ACTION_REVISED, "Revised"),
        (ACTION_PDF_DOWNLOADED, "PDF Downloaded"),
        (ACTION_IMPORTED, "Imported"),
        (ACTION_LPO_UPLOADED, "LPO Uploaded"),
        (ACTION_PROFORMA_DOWNLOADED, "Proforma Downloaded"),
        (ACTION_OUTCOME_UPDATED, "Outcome Updated"),
        (ACTION_FOLLOWUP_UPDATED, "Follow-up Updated"),
    ]

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quotation_audit_logs",
    )
    action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    target_type = models.CharField(max_length=100)
    target_id = models.PositiveIntegerField(null=True, blank=True)
    company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    quotation = models.ForeignKey(
        Quotation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    message = models.TextField(blank=True)
    changes = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["action", "created_at"]),
            models.Index(fields=["target_type", "target_id"]),
            models.Index(fields=["company", "created_at"]),
            models.Index(fields=["quotation", "created_at"]),
        ]

    def __str__(self):
        actor_name = self.actor.username if self.actor else "System"
        return f"{actor_name} {self.action} {self.target_type}:{self.target_id}"
