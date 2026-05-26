from rest_framework import serializers

from .models import AccountCustomer, AccountingCategory, AccountingImport, AccountingImportCustomer, AccountingInvoiceRow
from .parsers import normalize_customer_name
from .services import email_preview_for_import_customer


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

    class Meta:
        model = AccountingImport
        fields = [
            "id",
            "source_filename",
            "source_sha256",
            "source_size",
            "category_filename",
            "report_date",
            "uploaded_by",
            "uploaded_by_name",
            "status",
            "parsed_row_count",
            "skipped_row_count",
            "customer_count",
            "due_customer_count",
            "generated_statement_count",
            "warnings",
            "parse_meta",
            "duplicate",
            "duplicate_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class AccountingInvoiceRowSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccountingInvoiceRow
        fields = [
            "id",
            "source_row_number",
            "customer_code",
            "customer_name",
            "place",
            "bill_number",
            "invoice_date",
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


class AccountingImportCustomerSerializer(serializers.ModelSerializer):
    customer_profile_id = serializers.IntegerField(source="customer_id", read_only=True)
    email_preview = serializers.SerializerMethodField()

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


class AccountingImportCustomerDetailSerializer(AccountingImportCustomerSerializer):
    invoice_rows = AccountingInvoiceRowSerializer(many=True, read_only=True)

    class Meta(AccountingImportCustomerSerializer.Meta):
        fields = AccountingImportCustomerSerializer.Meta.fields + ["invoice_rows"]
