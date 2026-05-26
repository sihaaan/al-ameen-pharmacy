from django.contrib import admin
from django.contrib.admin.sites import NotRegistered
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm
from django.contrib.auth.models import User
from django import forms

from .models import AccountCustomer, AccountingImport, AccountingImportCustomer, AccountingInvoiceRow
from .permissions import set_user_accounting_access, user_has_accounting_access


class AccountingUserChangeForm(UserChangeForm):
    accounting_access = forms.BooleanField(
        label="Accounting access",
        required=False,
        help_text=(
            "Allows a staff user to open Admin -> Accounting and use protected "
            "accounting APIs. Superusers always have access."
        ),
    )

    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user = self.instance
        if user and user.pk:
            self.fields["accounting_access"].initial = user_has_accounting_access(user)
            if user.is_superuser:
                self.fields["accounting_access"].initial = True
                self.fields["accounting_access"].disabled = True


class AccountingAccessUserAdmin(UserAdmin):
    form = AccountingUserChangeForm
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "email")}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "accounting_access",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if "accounting_access" in form.cleaned_data and not form.instance.is_superuser:
            set_user_accounting_access(form.instance, form.cleaned_data["accounting_access"])


try:
    admin.site.unregister(User)
except NotRegistered:
    pass
admin.site.register(User, AccountingAccessUserAdmin)


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
        "invoice_number",
        "lpo_reference",
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
    list_display = ("customer_name", "invoice_number", "lpo_reference", "bill_number", "invoice_date", "total", "days")
    list_filter = ("invoice_date",)
    search_fields = ("customer_name", "customer_code", "bill_number", "invoice_number", "lpo_reference")
