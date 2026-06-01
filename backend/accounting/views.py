from io import BytesIO
from datetime import datetime
from decimal import Decimal
from zipfile import ZIP_STORED, ZipFile

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count, DecimalField, Max, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.utils.dateparse import parse_date
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AccountCustomer, AccountingImport, AccountingImportCustomer
from .excel import build_statement_workbook, statement_excel_filename
from .pdf import build_statement_pdf
from .permissions import IsAccountingUser
from .serializers import (
    AccountCustomerSerializer,
    AccountingImportCustomerDetailSerializer,
    AccountingImportCustomerSerializer,
    AccountingImportSerializer,
)
from .services import (
    apply_category_upload_to_import,
    category_update_message,
    create_accounting_import,
    statement_filename,
    statement_ledger,
    update_import_customer,
)


def parse_accounting_date_range(request):
    date_from = parse_accounting_date(request.query_params.get("date_from", "") or "")
    date_to = parse_accounting_date(request.query_params.get("date_to", "") or "")
    return date_from, date_to


def parse_accounting_date(value):
    value = (value or "").strip()
    if not value:
        return None
    parsed = parse_date(value)
    if parsed:
        return parsed
    for date_format in ("%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            continue
    return None


def money_string(value):
    return f"{value or Decimal('0.00'):.2f}"


def chunked(items, size):
    size = max(int(size or 1), 1)
    for index in range(0, len(items), size):
        yield index // size + 1, items[index : index + size]


def write_statement_zip(archive, customers, style, *, date_from=None, date_to=None):
    for customer in customers:
        archive.writestr(
            statement_filename(customer, style=style),
            build_statement_pdf(customer, style=style, date_from=date_from, date_to=date_to),
        )


def write_statement_excel_zip(archive, customers, *, date_from=None, date_to=None):
    for customer in customers:
        archive.writestr(
            statement_excel_filename(customer),
            build_statement_workbook(customer, date_from=date_from, date_to=date_to),
        )


def validation_error_response(exc):
    message = getattr(exc, "messages", None)
    if message:
        return {"detail": message[0] if len(message) == 1 else message}
    return {"detail": str(exc)}


class AccountingBaseViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAccountingUser]


class AccountingDashboardView(APIView):
    permission_classes = [IsAccountingUser]

    def get(self, request):
        latest_import = AccountingImport.objects.order_by("-created_at").first()
        customers = AccountCustomer.objects.all()
        return Response(
            {
                "latest_import": AccountingImportSerializer(latest_import).data if latest_import else None,
                "customer_count": customers.count(),
                "email_missing_count": customers.filter(email="").count(),
                "ignored_count": customers.filter(is_ignored=True).count(),
                "import_count": AccountingImport.objects.count(),
            }
        )


class AccountCustomerViewSet(AccountingBaseViewSet):
    serializer_class = AccountCustomerSerializer
    queryset = AccountCustomer.objects.all()
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        queryset = super().get_queryset()
        search = self.request.query_params.get("search", "").strip()
        category = self.request.query_params.get("category", "").strip()
        if search:
            queryset = queryset.filter(Q(name__icontains=search) | Q(customer_code__icontains=search) | Q(email__icontains=search))
        if category:
            queryset = queryset.filter(category=category)
        return queryset.order_by("name")


class AccountingImportViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAccountingUser]
    serializer_class = AccountingImportSerializer
    queryset = AccountingImport.objects.select_related("uploaded_by").all()

    @action(detail=False, methods=["post"], parser_classes=[MultiPartParser, FormParser])
    def upload(self, request):
        outstanding_file = request.FILES.get("file")
        category_file = request.FILES.get("category_file")
        try:
            import_record, meta = create_accounting_import(
                outstanding_file=outstanding_file,
                category_file=category_file,
                actor=request.user,
            )
        except DjangoValidationError as exc:
            return Response(validation_error_response(exc), status=status.HTTP_400_BAD_REQUEST)
        serializer = self.get_serializer(import_record)
        data = dict(serializer.data)
        data["duplicate"] = bool(meta.get("duplicate"))
        data["duplicate_message"] = meta.get("message", "")
        data["previous_import_id"] = meta.get("previous_import_id")
        data["category_update"] = meta.get("category_update", {})
        data["category_update_message"] = meta.get("category_update_message", "")
        return Response(data, status=status.HTTP_200_OK if meta.get("duplicate") else status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], parser_classes=[MultiPartParser, FormParser])
    def apply_categories(self, request, pk=None):
        import_record = self.get_object()
        category_file = request.FILES.get("category_file")
        try:
            result = apply_category_upload_to_import(import_record=import_record, category_file=category_file)
        except DjangoValidationError as exc:
            return Response(validation_error_response(exc), status=status.HTTP_400_BAD_REQUEST)
        serializer = self.get_serializer(import_record)
        data = dict(serializer.data)
        data["category_update"] = result
        data["category_update_message"] = category_update_message(result)
        data["message"] = data["category_update_message"]
        return Response(data)

    @action(detail=True, methods=["get"])
    def statements_zip(self, request, pk=None):
        import_record = self.get_object()
        style = request.query_params.get("style", "professional")
        date_from, date_to = parse_accounting_date_range(request)
        customer_ids = [
            int(item)
            for item in request.query_params.get("customer_ids", "").replace(" ", "").split(",")
            if item.isdigit()
        ]
        customers = (
            import_record.customers.filter(is_due=True, is_ignored=False)
            .prefetch_related("invoice_rows")
            .order_by("customer_name")
        )
        if customer_ids:
            customers = customers.filter(id__in=customer_ids)
        customers = list(customers)
        if date_from or date_to:
            filtered_customers = []
            for customer in customers:
                ledger = statement_ledger(customer, date_from=date_from, date_to=date_to)
                if ledger["invoice_count"] > 0 and ledger["is_due"]:
                    filtered_customers.append(customer)
            customers = filtered_customers
        customer_count = len(customers)
        batch_size = int(getattr(settings, "ACCOUNTING_STATEMENT_ZIP_SYNC_LIMIT", 75))
        if customer_count == 0:
            return Response({"detail": "No due, non-ignored customers are available for this ZIP."}, status=status.HTTP_400_BAD_REQUEST)
        buffer = BytesIO()
        is_batched = not customer_ids and customer_count > batch_size
        with ZipFile(buffer, "w", ZIP_STORED) as archive:
            if is_batched:
                for part_number, batch in chunked(customers, batch_size):
                    part_buffer = BytesIO()
                    with ZipFile(part_buffer, "w", ZIP_STORED) as part_archive:
                        write_statement_zip(part_archive, batch, style, date_from=date_from, date_to=date_to)
                    archive.writestr(
                        f"accounting-statements-{import_record.id}-part-{part_number:03d}.zip",
                        part_buffer.getvalue(),
                    )
            else:
                write_statement_zip(archive, customers, style, date_from=date_from, date_to=date_to)
        response = HttpResponse(buffer.getvalue(), content_type="application/zip")
        suffix = "batched" if is_batched else "selected" if customer_ids else "all"
        response["Content-Disposition"] = f'attachment; filename="accounting-statements-{import_record.id}-{suffix}.zip"'
        response["X-Accounting-Zip-Batched"] = "true" if is_batched else "false"
        response["X-Accounting-Statement-Count"] = str(customer_count)
        response["X-Accounting-Zip-Batch-Size"] = str(batch_size)
        return response

    @action(detail=True, methods=["get"])
    def statements_excel_zip(self, request, pk=None):
        import_record = self.get_object()
        date_from, date_to = parse_accounting_date_range(request)
        customer_ids = [
            int(item)
            for item in request.query_params.get("customer_ids", "").replace(" ", "").split(",")
            if item.isdigit()
        ]
        customers = (
            import_record.customers.filter(is_due=True, is_ignored=False)
            .prefetch_related("invoice_rows")
            .order_by("customer_name")
        )
        if customer_ids:
            customers = customers.filter(id__in=customer_ids)
        customers = list(customers)
        if date_from or date_to:
            customers = [
                customer
                for customer in customers
                if (lambda ledger: ledger["invoice_count"] > 0 and ledger["is_due"])(
                    statement_ledger(customer, date_from=date_from, date_to=date_to)
                )
            ]
        customer_count = len(customers)
        batch_size = int(getattr(settings, "ACCOUNTING_STATEMENT_ZIP_SYNC_LIMIT", 75))
        if customer_count == 0:
            return Response({"detail": "No due, non-ignored customers are available for this Excel ZIP."}, status=status.HTTP_400_BAD_REQUEST)
        buffer = BytesIO()
        is_batched = not customer_ids and customer_count > batch_size
        with ZipFile(buffer, "w", ZIP_STORED) as archive:
            if is_batched:
                for part_number, batch in chunked(customers, batch_size):
                    part_buffer = BytesIO()
                    with ZipFile(part_buffer, "w", ZIP_STORED) as part_archive:
                        write_statement_excel_zip(part_archive, batch, date_from=date_from, date_to=date_to)
                    archive.writestr(
                        f"accounting-excel-statements-{import_record.id}-part-{part_number:03d}.zip",
                        part_buffer.getvalue(),
                    )
            else:
                write_statement_excel_zip(archive, customers, date_from=date_from, date_to=date_to)
        response = HttpResponse(buffer.getvalue(), content_type="application/zip")
        suffix = "batched" if is_batched else "selected" if customer_ids else "all"
        response["Content-Disposition"] = f'attachment; filename="accounting-excel-statements-{import_record.id}-{suffix}.zip"'
        response["X-Accounting-Zip-Batched"] = "true" if is_batched else "false"
        response["X-Accounting-Statement-Count"] = str(customer_count)
        response["X-Accounting-Zip-Batch-Size"] = str(batch_size)
        return response


class AccountingImportCustomerViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAccountingUser]
    serializer_class = AccountingImportCustomerSerializer
    queryset = AccountingImportCustomer.objects.select_related("accounting_import", "customer").prefetch_related("invoice_rows")
    http_method_names = ["get", "patch", "head", "options"]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return AccountingImportCustomerDetailSerializer
        return AccountingImportCustomerSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        date_from, date_to = parse_accounting_date_range(self.request)
        context.update({"date_from": date_from, "date_to": date_to})
        return context

    def get_queryset(self):
        queryset = super().get_queryset()
        import_id = self.request.query_params.get("import_id")
        status_filter = self.request.query_params.get("status", "").strip()
        category = self.request.query_params.get("category", "").strip()
        search = self.request.query_params.get("search", "").strip()
        email_missing = self.request.query_params.get("email_missing", "").strip().lower()
        due_only = self.request.query_params.get("due_only", "").strip().lower()
        ageing_filter = self.request.query_params.get("ageing", "").strip().lower()
        ordering = self.request.query_params.get("ordering", "-overdue_amount").strip()
        date_from, date_to = parse_accounting_date_range(self.request)
        has_date_range = bool(date_from or date_to)
        if import_id:
            queryset = queryset.filter(accounting_import_id=import_id)
        if status_filter and not has_date_range:
            queryset = queryset.filter(status=status_filter)
        if category:
            queryset = queryset.filter(category=category)
        if email_missing in {"1", "true", "yes"}:
            queryset = queryset.filter(email="")
        if search:
            queryset = queryset.filter(Q(customer_name__icontains=search) | Q(customer_code__icontains=search) | Q(email__icontains=search))
        if due_only in {"1", "true", "yes"} and not has_date_range:
            queryset = queryset.filter(is_due=True, is_ignored=False)
        if has_date_range:
            return queryset.order_by("customer_name")
        if ageing_filter == "over_30":
            queryset = queryset.filter(max_days__gt=30)
        elif ageing_filter == "over_60":
            queryset = queryset.filter(max_days__gt=60)
        elif ageing_filter == "over_90":
            queryset = queryset.filter(max_days__gt=90)
        elif ageing_filter == "has_30_60":
            queryset = queryset.filter(bucket_30_60__gt=0)
        elif ageing_filter == "has_60_90":
            queryset = queryset.filter(bucket_60_90__gt=0)
        elif ageing_filter == "has_over_90":
            queryset = queryset.filter(bucket_over_90__gt=0)
        allowed_ordering = {
            "company": "customer_name",
            "-total_outstanding": "-total_outstanding",
            "-overdue_amount": "-overdue_amount",
            "-max_days": "-max_days",
            "-invoice_count": "-invoice_count",
        }
        return queryset.order_by(allowed_ordering.get(ordering, "-overdue_amount"), "customer_name")

    def list(self, request, *args, **kwargs):
        date_from, date_to = parse_accounting_date_range(request)
        if not (date_from or date_to):
            return super().list(request, *args, **kwargs)
        queryset = self.filter_queryset(self.get_queryset()).prefetch_related(None)
        status_filter = request.query_params.get("status", "").strip()
        due_only = request.query_params.get("due_only", "").strip().lower()
        ageing_filter = request.query_params.get("ageing", "").strip().lower()
        ordering = request.query_params.get("ordering", "-overdue_amount").strip()
        date_q = Q(invoice_rows__invoice_date__isnull=False)
        if date_from:
            date_q &= Q(invoice_rows__invoice_date__gte=date_from)
        if date_to:
            date_q &= Q(invoice_rows__invoice_date__lte=date_to)
        decimal_field = DecimalField(max_digits=14, decimal_places=2)
        queryset = queryset.annotate(
            filtered_total=Coalesce(Sum("invoice_rows__total", filter=date_q), Value(Decimal("0.00")), output_field=decimal_field),
            filtered_0_30=Coalesce(Sum("invoice_rows__bucket_0_30", filter=date_q), Value(Decimal("0.00")), output_field=decimal_field),
            filtered_30_60=Coalesce(Sum("invoice_rows__bucket_30_60", filter=date_q), Value(Decimal("0.00")), output_field=decimal_field),
            filtered_60_90=Coalesce(Sum("invoice_rows__bucket_60_90", filter=date_q), Value(Decimal("0.00")), output_field=decimal_field),
            filtered_over_90=Coalesce(Sum("invoice_rows__bucket_over_90", filter=date_q), Value(Decimal("0.00")), output_field=decimal_field),
            filtered_max_days=Coalesce(Max("invoice_rows__days", filter=date_q), Value(0)),
            filtered_invoice_count=Count("invoice_rows", filter=date_q),
        )
        items = []
        for item in queryset:
            overdue_amount = item.filtered_30_60 + item.filtered_60_90 + item.filtered_over_90
            is_due = bool((overdue_amount != 0 or item.filtered_max_days > 30) and not item.is_ignored)
            status = AccountingImportCustomer.STATUS_IGNORED if item.is_ignored else (
                AccountingImportCustomer.STATUS_DUE if is_due else AccountingImportCustomer.STATUS_NOT_DUE
            )
            items.append(
                {
                    "id": item.id,
                    "accounting_import": item.accounting_import_id,
                    "customer_profile_id": item.customer_id,
                    "customer_code": item.customer_code,
                    "customer_name": item.customer_name,
                    "category": item.category,
                    "email": item.email,
                    "total_outstanding": money_string(item.filtered_total),
                    "bucket_0_30": money_string(item.filtered_0_30),
                    "bucket_30_60": money_string(item.filtered_30_60),
                    "bucket_60_90": money_string(item.filtered_60_90),
                    "bucket_over_90": money_string(item.filtered_over_90),
                    "overdue_amount": money_string(overdue_amount),
                    "max_days": item.filtered_max_days,
                    "invoice_count": item.filtered_invoice_count,
                    "is_due": is_due,
                    "is_ignored": item.is_ignored,
                    "status": status,
                    "warnings": item.warnings,
                    "customer_notes": getattr(item.customer, "notes", ""),
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                }
            )
        items = [item for item in items if item["invoice_count"] > 0]

        def as_float(item, key):
            try:
                return float(item.get(key) or 0)
            except (TypeError, ValueError):
                return 0

        if due_only in {"1", "true", "yes"}:
            items = [item for item in items if item.get("is_due") and not item.get("is_ignored")]
        if status_filter:
            items = [item for item in items if item.get("status") == status_filter]
        if ageing_filter == "over_30":
            items = [item for item in items if item.get("max_days", 0) > 30]
        elif ageing_filter == "over_60":
            items = [item for item in items if item.get("max_days", 0) > 60]
        elif ageing_filter == "over_90":
            items = [item for item in items if item.get("max_days", 0) > 90]
        elif ageing_filter == "has_30_60":
            items = [item for item in items if as_float(item, "bucket_30_60") > 0]
        elif ageing_filter == "has_60_90":
            items = [item for item in items if as_float(item, "bucket_60_90") > 0]
        elif ageing_filter == "has_over_90":
            items = [item for item in items if as_float(item, "bucket_over_90") > 0]

        reverse = ordering.startswith("-")
        sort_key = ordering[1:] if reverse else ordering
        sort_fields = {
            "overdue_amount": lambda item: as_float(item, "overdue_amount"),
            "total_outstanding": lambda item: as_float(item, "total_outstanding"),
            "max_days": lambda item: item.get("max_days", 0),
            "invoice_count": lambda item: item.get("invoice_count", 0),
            "company": lambda item: (item.get("customer_name") or "").lower(),
        }
        key_func = sort_fields.get(sort_key, sort_fields["overdue_amount"])
        items.sort(key=lambda item: (key_func(item), (item.get("customer_name") or "").lower()), reverse=reverse)
        return Response(items)

    def partial_update(self, request, *args, **kwargs):
        import_customer = self.get_object()
        try:
            updated = update_import_customer(
                import_customer,
                email=request.data.get("email") if "email" in request.data else None,
                category=request.data.get("category") if "category" in request.data else None,
                is_ignored=request.data.get("is_ignored") if "is_ignored" in request.data else None,
                notes=request.data.get("notes") if "notes" in request.data else None,
            )
        except DjangoValidationError as exc:
            return Response(validation_error_response(exc), status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(updated).data)

    @action(detail=True, methods=["get"])
    def statement_pdf(self, request, pk=None):
        import_customer = self.get_object()
        style = request.query_params.get("style", "professional")
        date_from, date_to = parse_accounting_date_range(request)
        pdf_bytes = build_statement_pdf(import_customer, style=style, date_from=date_from, date_to=date_to)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{statement_filename(import_customer, style=style)}"'
        return response

    @action(detail=True, methods=["get"])
    def statement_excel(self, request, pk=None):
        import_customer = self.get_object()
        date_from, date_to = parse_accounting_date_range(request)
        workbook_bytes = build_statement_workbook(import_customer, date_from=date_from, date_to=date_to)
        response = HttpResponse(
            workbook_bytes,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{statement_excel_filename(import_customer)}"'
        return response
