from io import BytesIO
from zipfile import ZIP_STORED, ZipFile

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.http import HttpResponse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AccountCustomer, AccountingImport, AccountingImportCustomer
from .pdf import build_statement_pdf
from .permissions import IsAccountingUser
from .serializers import (
    AccountCustomerSerializer,
    AccountingImportCustomerDetailSerializer,
    AccountingImportCustomerSerializer,
    AccountingImportSerializer,
)
from .services import create_accounting_import, statement_filename, update_import_customer


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
        return Response(data, status=status.HTTP_200_OK if meta.get("duplicate") else status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def statements_zip(self, request, pk=None):
        import_record = self.get_object()
        customers = (
            import_record.customers.filter(is_due=True, is_ignored=False)
            .prefetch_related("invoice_rows")
            .order_by("customer_name")
        )
        buffer = BytesIO()
        with ZipFile(buffer, "w", ZIP_STORED) as archive:
            for customer in customers:
                archive.writestr(statement_filename(customer), build_statement_pdf(customer))
        response = HttpResponse(buffer.getvalue(), content_type="application/zip")
        response["Content-Disposition"] = f'attachment; filename="accounting-statements-{import_record.id}.zip"'
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

    def get_queryset(self):
        queryset = super().get_queryset()
        import_id = self.request.query_params.get("import_id")
        status_filter = self.request.query_params.get("status", "").strip()
        category = self.request.query_params.get("category", "").strip()
        search = self.request.query_params.get("search", "").strip()
        email_missing = self.request.query_params.get("email_missing", "").strip().lower()
        due_only = self.request.query_params.get("due_only", "").strip().lower()
        if import_id:
            queryset = queryset.filter(accounting_import_id=import_id)
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if category:
            queryset = queryset.filter(category=category)
        if email_missing in {"1", "true", "yes"}:
            queryset = queryset.filter(email="")
        if due_only in {"1", "true", "yes"}:
            queryset = queryset.filter(is_due=True, is_ignored=False)
        if search:
            queryset = queryset.filter(Q(customer_name__icontains=search) | Q(customer_code__icontains=search) | Q(email__icontains=search))
        return queryset.order_by("-overdue_amount", "customer_name")

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
        pdf_bytes = build_statement_pdf(import_customer)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{statement_filename(import_customer)}"'
        return response
