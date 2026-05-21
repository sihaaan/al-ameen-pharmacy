from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
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


class Inquiry(models.Model):
    SOURCE_MANUAL = "manual"
    SOURCE_CHOICES = [
        (SOURCE_MANUAL, "Manual"),
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

    inquiry = models.ForeignKey(Inquiry, on_delete=models.CASCADE, related_name="lines")
    raw_name = models.CharField(max_length=255)
    normalized_name = models.CharField(max_length=255, db_index=True, editable=False)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    unit = models.CharField(max_length=50, blank=True)
    notes = models.TextField(blank=True)
    matched_quote_item = models.ForeignKey(
        QuoteItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inquiry_lines",
    )
    match_status = models.CharField(
        max_length=30,
        choices=MATCH_STATUS_CHOICES,
        default=MATCH_UNRESOLVED,
    )
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "id"]
        indexes = [
            models.Index(fields=["inquiry", "sort_order"]),
            models.Index(fields=["match_status"]),
            models.Index(fields=["matched_quote_item"]),
        ]

    def __str__(self):
        return self.raw_name

    def save(self, *args, **kwargs):
        self.normalized_name = normalize_label(self.raw_name)
        super().save(*args, **kwargs)


class Quotation(models.Model):
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
        ]

    def __str__(self):
        return self.quotation_number or f"Quotation #{self.pk}"

    @property
    def is_editable(self):
        return self.status in self.EDITABLE_STATUSES

    def save(self, *args, **kwargs):
        if not self.quotation_number:
            self.quotation_number = self._generate_quotation_number()
        super().save(*args, **kwargs)

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
        decimal_places=2,
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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "id"]
        indexes = [
            models.Index(fields=["quotation", "sort_order"]),
            models.Index(fields=["quote_item"]),
            models.Index(fields=["match_status"]),
        ]

    def __str__(self):
        return f"{self.quotation} - {self.item_name_snapshot}"

    def save(self, *args, **kwargs):
        if self.quote_item and not self.item_name_snapshot:
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


class CompanyPriceHistory(models.Model):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="price_history")
    quote_item = models.ForeignKey(QuoteItem, on_delete=models.PROTECT, related_name="company_price_history")
    quotation = models.ForeignKey(Quotation, on_delete=models.PROTECT, related_name="price_history_entries")
    quotation_line = models.OneToOneField(
        QuotationLine,
        on_delete=models.PROTECT,
        related_name="price_history_entry",
    )
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
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
            models.Index(fields=["quotation"]),
        ]

    def __str__(self):
        return f"{self.company.name} - {self.quote_item.name} - {self.unit_price}"


class QuotationAuditLog(models.Model):
    ACTION_CREATED = "created"
    ACTION_UPDATED = "updated"
    ACTION_DELETED = "deleted"
    ACTION_STATUS_CHANGED = "status_changed"
    ACTION_FINALIZED = "finalized"
    ACTION_REVISED = "revised"
    ACTION_PDF_DOWNLOADED = "pdf_downloaded"
    ACTION_CHOICES = [
        (ACTION_CREATED, "Created"),
        (ACTION_UPDATED, "Updated"),
        (ACTION_DELETED, "Deleted"),
        (ACTION_STATUS_CHANGED, "Status Changed"),
        (ACTION_FINALIZED, "Finalized"),
        (ACTION_REVISED, "Revised"),
        (ACTION_PDF_DOWNLOADED, "PDF Downloaded"),
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
