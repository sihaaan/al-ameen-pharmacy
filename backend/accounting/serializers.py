from django.conf import settings
from rest_framework import serializers

from .formatting import format_accounting_date, format_accounting_datetime, format_accounting_period
from .models import AccountCustomer, AccountingCategory, AccountingImport, AccountingImportCustomer, AccountingInvoiceRow
from .parsers import normalize_customer_name
from .services import email_preview_for_import_customer, statement_ledger


def money_string(value):
    return f"{value or 0:.2f}"


class AccountCustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccountCustomer
        fields = [
            "id",
            "customer_code",
            "name",
            "category",
            "email",
            "phone",
            "notes",
            "is_active",
            "is_ignored",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def update(self, instance, validated_data):
        if "name" in validated_data:
            validated_data["normalized_name"] = normalize_customer_name(validated_data["name"])
        return super().update(instance, validated_data)


class AccountingImportSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.CharField(source="uploaded_by.username", read_only=True, default="")
    duplicate = serializers.BooleanField(read_only=True, default=False)
    duplicate_message = serializers.CharField(read_only=True, default="")
    zip_sync_limit = serializers.SerializerMethodField()
    email_missing_count = serializers.SerializerMethodField()
    report_date_display = serializers.SerializerMethodField()
    created_at_display = serializers.SerializerMethodField()
    updated_at_display = serializers.SerializerMethodField()

    class Meta:
        model = AccountingImport
        fields = [
            "id",
            "source_filename",
            "source_sha256",
            "source_size",
            "category_filename",
            "report_date",
            "report_date_display",
            "uploaded_by",
            "uploaded_by_name",
            "status",
            "parsed_row_count",
            "skipped_row_count",
            "customer_count",
            "due_customer_count",
            "email_missing_count",
            "generated_statement_count",
            "warnings",
            "parse_meta",
            "duplicate",
            "duplicate_message",
            "zip_sync_limit",
            "created_at",
            "created_at_display",
            "updated_at",
            "updated_at_display",
        ]
        read_only_fields = fields

    def get_zip_sync_limit(self, obj):
        return int(getattr(settings, "ACCOUNTING_STATEMENT_ZIP_SYNC_LIMIT", 75))

    def get_email_missing_count(self, obj):
        return obj.customers.filter(email="").count() if obj.pk else 0

    def get_report_date_display(self, obj):
        return format_accounting_date(obj.report_date)

    def get_created_at_display(self, obj):
        return format_accounting_datetime(obj.created_at)

    def get_updated_at_display(self, obj):
        return format_accounting_datetime(obj.updated_at)


class AccountingInvoiceRowSerializer(serializers.ModelSerializer):
    invoice_date_display = serializers.SerializerMethodField()

    class Meta:
        model = AccountingInvoiceRow
        fields = [
            "id",
            "source_row_number",
            "customer_code",
            "customer_name",
            "place",
            "bill_number",
            "invoice_number",
            "lpo_reference",
            "invoice_date",
            "invoice_date_display",
            "amount",
            "bucket_0_30",
            "bucket_30_60",
            "bucket_60_90",
            "bucket_over_90",
            "total",
            "days",
            "warnings",
        ]
        read_only_fields = fields

    def get_invoice_date_display(self, obj):
        return format_accounting_date(obj.invoice_date)


class AccountingImportCustomerSerializer(serializers.ModelSerializer):
    customer_profile_id = serializers.IntegerField(source="customer_id", read_only=True)
    email_preview = serializers.SerializerMethodField()
    customer_notes = serializers.CharField(source="customer.notes", read_only=True, default="")

    class Meta:
        model = AccountingImportCustomer
        fields = [
            "id",
            "accounting_import",
            "customer_profile_id",
            "customer_code",
            "customer_name",
            "category",
            "email",
            "total_outstanding",
            "bucket_0_30",
            "bucket_30_60",
            "bucket_60_90",
            "bucket_over_90",
            "overdue_amount",
            "max_days",
            "invoice_count",
            "is_due",
            "is_ignored",
            "status",
            "warnings",
            "email_preview",
            "customer_notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "accounting_import",
            "customer_profile_id",
            "customer_code",
            "customer_name",
            "total_outstanding",
            "bucket_0_30",
            "bucket_30_60",
            "bucket_60_90",
            "bucket_over_90",
            "overdue_amount",
            "max_days",
            "invoice_count",
            "is_due",
            "status",
            "warnings",
            "email_preview",
            "customer_notes",
            "created_at",
            "updated_at",
        ]

    def get_email_preview(self, obj):
        return email_preview_for_import_customer(obj)

    def validate_category(self, value):
        valid = {choice.value for choice in AccountingCategory}
        if value not in valid:
            raise serializers.ValidationError("Invalid category.")
        return value

    def to_representation(self, instance):
        data = super().to_representation(instance)
        date_from = self.context.get("date_from")
        date_to = self.context.get("date_to")
        if date_from or date_to:
            ledger = statement_ledger(instance, date_from=date_from, date_to=date_to)
            data.update(
                {
                    "total_outstanding": money_string(ledger["total_outstanding"]),
                    "bucket_0_30": money_string(ledger["bucket_0_30"]),
                    "bucket_30_60": money_string(ledger["bucket_30_60"]),
                    "bucket_60_90": money_string(ledger["bucket_60_90"]),
                    "bucket_over_90": money_string(ledger["bucket_over_90"]),
                    "overdue_amount": money_string(ledger["overdue_amount"]),
                    "max_days": ledger["max_days"],
                    "invoice_count": ledger["invoice_count"],
                    "is_due": ledger["is_due"],
                    "status": ledger["status"],
                }
            )
        return data


class AccountingImportCustomerDetailSerializer(AccountingImportCustomerSerializer):
    invoice_rows = serializers.SerializerMethodField()
    ledger_rows = serializers.SerializerMethodField()
    statement_period = serializers.SerializerMethodField()

    class Meta(AccountingImportCustomerSerializer.Meta):
        fields = AccountingImportCustomerSerializer.Meta.fields + ["invoice_rows", "ledger_rows", "statement_period"]

    def get_invoice_rows(self, obj):
        date_from = self.context.get("date_from")
        date_to = self.context.get("date_to")
        rows = [line["row"] for line in statement_ledger(obj, date_from=date_from, date_to=date_to)["lines"]]
        return AccountingInvoiceRowSerializer(rows, many=True).data

    def get_ledger_rows(self, obj):
        date_from = self.context.get("date_from")
        date_to = self.context.get("date_to")
        lines = statement_ledger(obj, date_from=date_from, date_to=date_to)["lines"]
        return [
            {
                "id": line["row"].id,
                "invoice_date": line["row"].invoice_date.isoformat() if line["row"].invoice_date else "",
                "invoice_date_display": format_accounting_date(line["row"].invoice_date),
                "doc_type": line["doc_type"],
                "invoice_number": line["row"].invoice_number or line["row"].bill_number,
                "lpo_reference": line["row"].lpo_reference,
                "debit": money_string(line["debit"]),
                "credit": money_string(line["credit"]),
                "balance": money_string(line["balance"]),
                "days": line["days"],
            }
            for line in lines
        ]

    def get_statement_period(self, obj):
        date_from = self.context.get("date_from")
        date_to = self.context.get("date_to")
        ledger = statement_ledger(obj, date_from=date_from, date_to=date_to)
        period_start = ledger.get("period_start")
        period_end = ledger.get("period_end")
        return {
            "from": date_from.isoformat() if date_from else "",
            "to": date_to.isoformat() if date_to else "",
            "display_from": format_accounting_date(period_start),
            "display_to": format_accounting_date(period_end),
            "display": format_accounting_period(period_start, period_end),
        }
