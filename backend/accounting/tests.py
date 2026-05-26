import csv
from io import BytesIO, StringIO
from zipfile import ZipFile

from django.contrib.auth.models import Group, Permission, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook
from rest_framework.test import APITestCase

from .models import AccountCustomer, AccountingImport, AccountingImportCustomer
from .parsers import parse_outstanding_upload


def make_agewise_row(code, party, bill_no, invoice_date, amount, b0, b30, b60, b90, total, days):
    return [
        "AL AMEEN PHARMACY",
        "Frij Murar, Somali St, Deira, Dubai.",
        "Agewise Outstanding as on 25/05/26",
        "Page -1 of 1",
        "Code",
        "Party",
        "Place",
        "Bill No.",
        "Date",
        "Amount",
        "0-30",
        "30 - 60",
        "60 - 90",
        "Over 90",
        "TOTAL",
        "Days",
        code,
        party,
        "",
        bill_no,
        invoice_date,
        amount,
        b0,
        b30,
        b60,
        b90,
        total,
        days,
        "37071549.94",
        "586650.57",
    ]


def make_agewise_upload(name="ageoutcode test.csv", marker=""):
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(make_agewise_row("083", "MILLENNIUM AIRPORT HOTEL", "571920-UA3IJ2-", "03/02/2026", "1557.70", "0.00", "0.00", "1557.70", "0.00", "1557.70", "80"))
    writer.writerow(make_agewise_row("084", "CARD CUSTOMER", f"571921-{marker}", "20/05/2026", "100.00", "100.00", "0.00", "0.00", "0.00", "100.00", "5"))
    writer.writerow(make_agewise_row("085", "CREDIT NOTE CUSTOMER", "CR-1", "03/02/2026", "(126.00", "0.00", "0.00", "(126.00", "0.00", "(126.00", "80"))
    return SimpleUploadedFile(name, buffer.getvalue().encode("utf-8"), content_type="text/csv")


def make_category_upload():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "cat"
    sheet.append([None, None, None])
    sheet.append([None, None, None])
    sheet.append([None, "Cust Name", "Cat"])
    sheet.append([None, "MILLENNIUM AIRPORT HOTEL", "Credit"])
    sheet.append([None, "CARD CUSTOMER", "Card"])
    buffer = BytesIO()
    workbook.save(buffer)
    return SimpleUploadedFile("ageoutcode may 2026.xlsx", buffer.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


class AccountingParserTests(TestCase):
    def test_parse_sample_style_csv_and_accounting_negatives(self):
        parsed = parse_outstanding_upload(make_agewise_upload())
        self.assertEqual(parsed.report_date.isoformat(), "2026-05-25")
        self.assertEqual(len(parsed.rows), 3)
        first = parsed.rows[0]
        self.assertEqual(first.customer_code, "083")
        self.assertEqual(first.customer_name, "MILLENNIUM AIRPORT HOTEL")
        self.assertEqual(first.days, 80)
        self.assertEqual(str(first.bucket_60_90), "1557.70")
        credit = parsed.rows[2]
        self.assertEqual(str(credit.total), "-126.00")


class AccountingAPITests(APITestCase):
    def setUp(self):
        self.customer = User.objects.create_user(username="customer", password="pass")
        self.staff = User.objects.create_user(username="staff", password="pass", is_staff=True)
        self.accountant = User.objects.create_user(username="accountant", password="pass", is_staff=True)
        self.superuser = User.objects.create_superuser(username="owner", password="pass", email="owner@example.com")
        perms = Permission.objects.filter(
            content_type__app_label="accounting",
            codename__in=[
                "view_accounting_module",
                "upload_accounting_statement",
                "generate_accounting_statement",
                "edit_accounting_customer",
                "download_accounting_statement",
            ],
        )
        self.accountant.user_permissions.add(*perms)

    def upload_import(self):
        self.client.force_authenticate(self.accountant)
        return self.client.post(
            reverse("accounting-import-upload"),
            {"file": make_agewise_upload(), "category_file": make_category_upload()},
            format="multipart",
        )

    def test_accounting_permissions(self):
        dashboard_url = reverse("accounting-dashboard")
        anon = self.client.get(dashboard_url)
        self.assertEqual(anon.status_code, 401)

        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.get(dashboard_url).status_code, 403)

        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get(dashboard_url).status_code, 403)

        self.client.force_authenticate(self.accountant)
        self.assertEqual(self.client.get(dashboard_url).status_code, 200)

        self.client.force_authenticate(self.superuser)
        self.assertEqual(self.client.get(dashboard_url).status_code, 200)

    def test_accounting_group_staff_user_allowed(self):
        group_user = User.objects.create_user(username="group_accountant", password="pass", is_staff=True)
        group = Group.objects.create(name="Accounting")
        group_user.groups.add(group)
        self.client.force_authenticate(group_user)
        self.assertEqual(self.client.get(reverse("accounting-dashboard")).status_code, 200)

    def test_upload_groups_customers_matches_categories_and_persists_email(self):
        response = self.upload_import()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["parsed_row_count"], 3)
        self.assertEqual(response.data["customer_count"], 3)
        self.assertEqual(response.data["due_customer_count"], 2)

        customer = AccountCustomer.objects.get(customer_code="083")
        self.assertEqual(customer.category, "credit")

        summary = AccountingImportCustomer.objects.get(customer=customer)
        patch = self.client.patch(
            reverse("accounting-import-customer-detail", args=[summary.id]),
            {"email": "accounts@hotel.example", "category": "clinic", "is_ignored": False},
            format="json",
        )
        self.assertEqual(patch.status_code, 200)
        customer.refresh_from_db()
        self.assertEqual(customer.email, "accounts@hotel.example")
        self.assertEqual(customer.category, "clinic")

        second = self.client.post(
            reverse("accounting-import-upload"),
            {"file": make_agewise_upload("new_month.csv", marker="new")},
            format="multipart",
        )
        self.assertEqual(second.status_code, 201)
        self.assertEqual(
            AccountingImportCustomer.objects.filter(customer__customer_code="083").order_by("-id").first().email,
            "accounts@hotel.example",
        )

    def test_same_customer_name_with_different_codes_stays_separate(self):
        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(make_agewise_row("A01", "DUPLICATE NAME LLC", "INV-1", "03/02/2026", "10.00", "0.00", "10.00", "0.00", "0.00", "10.00", "80"))
        writer.writerow(make_agewise_row("B02", "DUPLICATE NAME LLC", "INV-2", "03/02/2026", "20.00", "0.00", "20.00", "0.00", "0.00", "20.00", "80"))
        upload = SimpleUploadedFile("duplicate-names.csv", buffer.getvalue().encode("utf-8"), content_type="text/csv")

        self.client.force_authenticate(self.accountant)
        response = self.client.post(reverse("accounting-import-upload"), {"file": upload}, format="multipart")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["customer_count"], 2)
        self.assertEqual(AccountCustomer.objects.filter(name="DUPLICATE NAME LLC").count(), 2)
        self.assertEqual(AccountingImportCustomer.objects.count(), 2)

    def test_duplicate_upload_returns_previous_import_without_creating_another(self):
        first = self.upload_import()
        self.assertEqual(first.status_code, 201)
        second = self.client.post(reverse("accounting-import-upload"), {"file": make_agewise_upload()}, format="multipart")
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.data["duplicate"])
        self.assertEqual(AccountingImport.objects.count(), 1)

    def test_statement_pdf_and_zip_download_are_staff_only_and_do_not_send_email(self):
        response = self.upload_import()
        import_id = response.data["id"]
        summary = AccountingImportCustomer.objects.get(customer__customer_code="083")

        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get(reverse("accounting-import-customer-statement-pdf", args=[summary.id])).status_code, 403)

        self.client.force_authenticate(self.accountant)
        pdf = self.client.get(reverse("accounting-import-customer-statement-pdf", args=[summary.id]))
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertGreater(len(pdf.content), 500)

        zip_response = self.client.get(reverse("accounting-import-statements-zip", args=[import_id]))
        self.assertEqual(zip_response.status_code, 200)
        self.assertEqual(zip_response["Content-Type"], "application/zip")
        with ZipFile(BytesIO(zip_response.content)) as archive:
            self.assertGreaterEqual(len(archive.namelist()), 1)
