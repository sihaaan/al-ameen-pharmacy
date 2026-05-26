from django.contrib import admin

from .models import AccountCustomer, AccountingImport, AccountingImportCustomer, AccountingInvoiceRow


@admin.register(AccountCustomer)
class AccountCustomerAdmin(admin.ModelAdmin):
    list_display = ("customer_code", "name", "category", "email", "is_ignored", "updated_at")
    list_filter = ("category", "is_ignored", "is_active")
    search_fields = ("customer_code", "name", "email")
    readonly_fields = ("normalized_name", "created_at", "updated_at")


class AccountingInvoiceRowInline(admin.TabularInline):
    model = AccountingInvoiceRow
    extra = 0
    readonly_fields = (
        "source_row_number",
        "bill_number",
        "invoice_date",
        "amount",
        "bucket_0_30",
        "bucket_30_60",
        "bucket_60_90",
        "bucket_over_90",
        "total",
        "days",
    )
    can_delete = False
    max_num = 0


@admin.register(AccountingImportCustomer)
class AccountingImportCustomerAdmin(admin.ModelAdmin):
    list_display = (
        "customer_name",
        "customer_code",
        "category",
        "total_outstanding",
        "overdue_amount",
        "max_days",
        "invoice_count",
        "status",
    )
    list_filter = ("status", "category", "is_due", "is_ignored")
    search_fields = ("customer_name", "customer_code", "email")
    readonly_fields = ("created_at", "updated_at")
    inlines = [AccountingInvoiceRowInline]


@admin.register(AccountingImport)
class AccountingImportAdmin(admin.ModelAdmin):
    list_display = (
        "source_filename",
        "report_date",
        "status",
        "parsed_row_count",
        "customer_count",
        "due_customer_count",
        "uploaded_by",
        "created_at",
    )
    list_filter = ("status", "report_date", "created_at")
    search_fields = ("source_filename", "source_sha256")
    readonly_fields = ("created_at", "updated_at")


@admin.register(AccountingInvoiceRow)
class AccountingInvoiceRowAdmin(admin.ModelAdmin):
    list_display = ("customer_name", "bill_number", "invoice_date", "total", "days")
    list_filter = ("invoice_date",)
    search_fields = ("customer_name", "customer_code", "bill_number")
