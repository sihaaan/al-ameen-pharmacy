from django.conf import settings
from django.db import models


class AccountingCategory(models.TextChoices):
    CREDIT = "credit", "Credit"
    INSURANCE = "insurance", "Insurance"
    CLINIC = "clinic", "Clinic"
    BRANCH = "branch", "Branch"
    CARD = "card", "Card"
    MISC = "misc", "Misc"
    UNKNOWN = "unknown", "Unknown"


class AccountCustomer(models.Model):
    customer_code = models.CharField(max_length=50, blank=True, db_index=True)
    name = models.CharField(max_length=255, db_index=True)
    normalized_name = models.CharField(max_length=255, db_index=True)
    category = models.CharField(
        max_length=30,
        choices=AccountingCategory.choices,
        default=AccountingCategory.UNKNOWN,
        db_index=True,
    )
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    is_ignored = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["customer_code", "normalized_name"]),
            models.Index(fields=["category", "is_ignored"]),
        ]
        permissions = [
            ("view_accounting_module", "Can view Accounting module"),
            ("upload_accounting_statement", "Can upload accounting statements"),
            ("generate_accounting_statement", "Can generate accounting statements"),
            ("edit_accounting_customer", "Can edit accounting customers"),
            ("download_accounting_statement", "Can download accounting statements"),
        ]

    def __str__(self):
        return f"{self.customer_code} - {self.name}" if self.customer_code else self.name


class AccountingImport(models.Model):
    STATUS_PARSED = "parsed"
    STATUS_REVIEWED = "reviewed"
    STATUS_GENERATED = "generated"
    STATUS_ARCHIVED = "archived"

    STATUS_CHOICES = [
        (STATUS_PARSED, "Parsed"),
        (STATUS_REVIEWED, "Reviewed"),
        (STATUS_GENERATED, "Generated"),
        (STATUS_ARCHIVED, "Archived"),
    ]

    source_filename = models.CharField(max_length=255)
    source_sha256 = models.CharField(max_length=64, db_index=True)
    source_size = models.PositiveIntegerField(default=0)
    category_filename = models.CharField(max_length=255, blank=True)
    category_sha256 = models.CharField(max_length=64, blank=True)
    report_date = models.DateField(null=True, blank=True, db_index=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="accounting_imports",
    )
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_PARSED, db_index=True)
    parsed_row_count = models.PositiveIntegerField(default=0)
    skipped_row_count = models.PositiveIntegerField(default=0)
    customer_count = models.PositiveIntegerField(default=0)
    due_customer_count = models.PositiveIntegerField(default=0)
    generated_statement_count = models.PositiveIntegerField(default=0)
    warnings = models.JSONField(default=list, blank=True)
    parse_meta = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["source_sha256", "report_date"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        return f"{self.source_filename} ({self.report_date or 'no date'})"


class AccountingImportCustomer(models.Model):
    STATUS_DUE = "due"
    STATUS_NOT_DUE = "not_due"
    STATUS_IGNORED = "ignored"
    STATUS_NEEDS_REVIEW = "needs_review"

    STATUS_CHOICES = [
        (STATUS_DUE, "Due"),
        (STATUS_NOT_DUE, "Not Due"),
        (STATUS_IGNORED, "Ignored"),
        (STATUS_NEEDS_REVIEW, "Needs Review"),
    ]

    accounting_import = models.ForeignKey(
        AccountingImport,
        on_delete=models.CASCADE,
        related_name="customers",
    )
    customer = models.ForeignKey(
        AccountCustomer,
        on_delete=models.PROTECT,
        related_name="import_summaries",
    )
    customer_code = models.CharField(max_length=50, blank=True, db_index=True)
    customer_name = models.CharField(max_length=255, db_index=True)
    category = models.CharField(
        max_length=30,
        choices=AccountingCategory.choices,
        default=AccountingCategory.UNKNOWN,
        db_index=True,
    )
    email = models.EmailField(blank=True)
    total_outstanding = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    bucket_0_30 = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    bucket_30_60 = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    bucket_60_90 = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    bucket_over_90 = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    overdue_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    max_days = models.IntegerField(default=0)
    invoice_count = models.PositiveIntegerField(default=0)
    is_due = models.BooleanField(default=False, db_index=True)
    is_ignored = models.BooleanField(default=False, db_index=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_NOT_DUE, db_index=True)
    warnings = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["customer_name"]
        unique_together = ["accounting_import", "customer"]
        indexes = [
            models.Index(fields=["accounting_import", "status"]),
            models.Index(fields=["accounting_import", "is_due", "is_ignored"]),
            models.Index(fields=["category", "email"]),
        ]

    def __str__(self):
        return f"{self.customer_name} - {self.accounting_import.source_filename}"


class AccountingInvoiceRow(models.Model):
    import_customer = models.ForeignKey(
        AccountingImportCustomer,
        on_delete=models.CASCADE,
        related_name="invoice_rows",
    )
    source_row_number = models.PositiveIntegerField()
    customer_code = models.CharField(max_length=50, blank=True)
    customer_name = models.CharField(max_length=255)
    place = models.CharField(max_length=255, blank=True)
    bill_number = models.CharField(max_length=120, blank=True, db_index=True)
    invoice_date = models.DateField(null=True, blank=True, db_index=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    bucket_0_30 = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    bucket_30_60 = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    bucket_60_90 = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    bucket_over_90 = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    days = models.IntegerField(default=0)
    raw_data = models.JSONField(default=dict, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["invoice_date", "bill_number", "id"]
        indexes = [
            models.Index(fields=["import_customer", "invoice_date"]),
            models.Index(fields=["days"]),
        ]

    def __str__(self):
        return f"{self.customer_name} {self.bill_number}"
