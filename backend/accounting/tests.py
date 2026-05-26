import csv
from io import BytesIO, StringIO
from zipfile import ZipFile

from django.contrib.auth.models import Group, Permission, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook
from pypdf import PdfReader
from rest_framework.test import APITestCase

from .models import AccountCustomer, AccountingImport, AccountingImportCustomer
from .parsers import parse_outstanding_upload
from .permissions import accounting_permissions_queryset, set_user_accounting_access


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


def make_code_category_upload():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "cat"
    sheet.append(["Customer Code", "Cust Name", "Cat"])
    sheet.append(["A01", "DUPLICATE NAME LLC", "Credit"])
    sheet.append(["B02", "DUPLICATE NAME LLC", "Card"])
    buffer = BytesIO()
    workbook.save(buffer)
    return SimpleUploadedFile("categories-by-code.xlsx", buffer.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


class AccountingParserTests(TestCase):
    def test_parse_sample_style_csv_and_accounting_negatives(self):
        parsed = parse_outstanding_upload(make_agewise_upload())
        self.assertEqual(parsed.report_date.isoformat(), "2026-05-25")
        self.assertEqual(len(parsed.rows), 3)
        first = parsed.rows[0]
        self.assertEqual(first.customer_code, "083")
        self.assertEqual(first.customer_name, "MILLENNIUM AIRPORT HOTEL")
        self.assertEqual(first.bill_number, "571920-UA3IJ2-")
        self.assertEqual(first.invoice_number, "571920")
        self.assertEqual(first.lpo_reference, "UA3IJ2-")
        self.assertEqual(first.days, 80)
        self.assertEqual(str(first.bucket_60_90), "1557.70")
        second = parsed.rows[1]
        self.assertEqual(second.invoice_number, "571921")
        self.assertEqual(second.lpo_reference, "")
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

    def test_accounting_access_toggle_manages_only_accounting_access(self):
        user = User.objects.create_user(username="toggle_user", password="pass", is_staff=True)
        unrelated_group = Group.objects.create(name="Inventory")
        unrelated_permission = Permission.objects.filter(codename="view_user").first()
        user.groups.add(unrelated_group)
        if unrelated_permission:
            user.user_permissions.add(unrelated_permission)

        self.client.force_authenticate(user)
        self.assertEqual(self.client.get(reverse("accounting-dashboard")).status_code, 403)

        set_user_accounting_access(user, True)
        user = User.objects.get(pk=user.pk)
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get(reverse("accounting-dashboard")).status_code, 200)

        set_user_accounting_access(user, False)
        user = User.objects.get(pk=user.pk)
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get(reverse("accounting-dashboard")).status_code, 403)
        self.assertTrue(user.groups.filter(name="Inventory").exists())
        if unrelated_permission:
            self.assertTrue(user.user_permissions.filter(pk=unrelated_permission.pk).exists())
        self.assertFalse(user.groups.filter(name="Accounting").exists())
        self.assertFalse(user.user_permissions.filter(pk__in=accounting_permissions_queryset().values("pk")).exists())

    def test_existing_permission_group_still_allows_accounting(self):
        user = User.objects.create_user(username="custom_group_user", password="pass", is_staff=True)
        group = Group.objects.create(name="Finance Team")
        group.permissions.add(Permission.objects.get(codename="view_accounting_module", content_type__app_label="accounting"))
        user.groups.add(group)
        self.client.force_authenticate(User.objects.get(pk=user.pk))
        self.assertEqual(self.client.get(reverse("accounting-dashboard")).status_code, 200)

    def test_accounting_access_still_requires_staff_unless_superuser(self):
        user = User.objects.create_user(username="nonstaff_accounting", password="pass", is_staff=False)
        set_user_accounting_access(user, True)
        self.client.force_authenticate(User.objects.get(pk=user.pk))
        self.assertEqual(self.client.get(reverse("accounting-dashboard")).status_code, 403)

        self.client.force_authenticate(self.superuser)
        self.assertEqual(self.client.get(reverse("accounting-dashboard")).status_code, 200)

    def test_django_user_admin_shows_accounting_access_checkbox(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse("admin:auth_user_change", args=[self.staff.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Accounting access")

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

    def test_duplicate_upload_can_apply_category_workbook_to_existing_import(self):
        self.client.force_authenticate(self.accountant)
        first = self.client.post(reverse("accounting-import-upload"), {"file": make_agewise_upload()}, format="multipart")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(AccountCustomer.objects.get(customer_code="083").category, "unknown")

        second = self.client.post(
            reverse("accounting-import-upload"),
            {"file": make_agewise_upload(), "category_file": make_category_upload()},
            format="multipart",
        )

        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.data["duplicate"])
        self.assertEqual(AccountingImport.objects.count(), 1)
        self.assertGreaterEqual(second.data["category_update"]["matched"], 2)
        self.assertIn("unchanged", second.data["category_update"])
        self.assertEqual(AccountCustomer.objects.get(customer_code="083").category, "credit")
        self.assertEqual(AccountingImportCustomer.objects.get(customer__customer_code="084").category, "card")

        third = self.client.post(
            reverse("accounting-import-upload"),
            {"file": make_agewise_upload(), "category_file": make_category_upload()},
            format="multipart",
        )
        self.assertEqual(third.status_code, 200)
        self.assertEqual(third.data["category_update"]["updated"], 0)
        self.assertGreaterEqual(third.data["category_update"]["unchanged"], 2)
        self.assertIn("already up to date", third.data["category_update_message"])

    def test_category_workbook_can_be_applied_to_existing_import(self):
        self.client.force_authenticate(self.accountant)
        response = self.client.post(reverse("accounting-import-upload"), {"file": make_agewise_upload()}, format="multipart")
        self.assertEqual(response.status_code, 201)

        apply_response = self.client.post(
            reverse("accounting-import-apply-categories", args=[response.data["id"]]),
            {"category_file": make_category_upload()},
            format="multipart",
        )

        self.assertEqual(apply_response.status_code, 200)
        self.assertEqual(AccountCustomer.objects.get(customer_code="083").category, "credit")

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

    def test_category_matching_uses_customer_code_before_name(self):
        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(make_agewise_row("A01", "DUPLICATE NAME LLC", "INV-1", "03/02/2026", "10.00", "0.00", "10.00", "0.00", "0.00", "10.00", "80"))
        writer.writerow(make_agewise_row("B02", "DUPLICATE NAME LLC", "INV-2", "03/02/2026", "20.00", "0.00", "20.00", "0.00", "0.00", "20.00", "80"))
        upload = SimpleUploadedFile("duplicate-names.csv", buffer.getvalue().encode("utf-8"), content_type="text/csv")

        self.client.force_authenticate(self.accountant)
        response = self.client.post(
            reverse("accounting-import-upload"),
            {"file": upload, "category_file": make_code_category_upload()},
            format="multipart",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(AccountCustomer.objects.get(customer_code="A01").category, "credit")
        self.assertEqual(AccountCustomer.objects.get(customer_code="B02").category, "card")

    def test_duplicate_upload_returns_previous_import_without_creating_another(self):
        first = self.upload_import()
        self.assertEqual(first.status_code, 201)
        second = self.client.post(reverse("accounting-import-upload"), {"file": make_agewise_upload()}, format="multipart")
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.data["duplicate"])
        self.assertEqual(AccountingImport.objects.count(), 1)

    def test_statement_pdfs_and_zip_download_are_staff_only_and_customer_facing(self):
        response = self.upload_import()
        import_id = response.data["id"]
        summary = AccountingImportCustomer.objects.get(customer__customer_code="083")

        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get(reverse("accounting-import-customer-statement-pdf", args=[summary.id])).status_code, 403)

        self.client.force_authenticate(self.accountant)
        for style in ("classic", "professional"):
            pdf = self.client.get(reverse("accounting-import-customer-statement-pdf", args=[summary.id]), {"style": style})
            self.assertEqual(pdf.status_code, 200)
            self.assertEqual(pdf["Content-Type"], "application/pdf")
            self.assertGreater(len(pdf.content), 500)
            text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(pdf.content)).pages)
            self.assertIn("Invoice No.", text)
            self.assertIn("LPO / Reference No.", text)
            self.assertIn("571920", text)
            self.assertIn("UA3IJ2-", text)
            self.assertNotIn("Email missing", text)
            self.assertNotIn("Category", text)

        zip_response = self.client.get(reverse("accounting-import-statements-zip", args=[import_id]))
        self.assertEqual(zip_response.status_code, 200)
        self.assertEqual(zip_response["Content-Type"], "application/zip")
        with ZipFile(BytesIO(zip_response.content)) as archive:
            self.assertGreaterEqual(len(archive.namelist()), 1)

    def test_ignored_customers_are_excluded_from_statement_zip(self):
        response = self.upload_import()
        import_id = response.data["id"]
        summary = AccountingImportCustomer.objects.get(customer__customer_code="083")
        self.client.force_authenticate(self.accountant)
        self.client.patch(
            reverse("accounting-import-customer-detail", args=[summary.id]),
            {"is_ignored": True},
            format="json",
        )

        zip_response = self.client.get(reverse("accounting-import-statements-zip", args=[import_id]))
        self.assertEqual(zip_response.status_code, 200)
        with ZipFile(BytesIO(zip_response.content)) as archive:
            names = archive.namelist()
        self.assertFalse(any("MILLENNIUM" in name for name in names))

    @override_settings(ACCOUNTING_STATEMENT_ZIP_SYNC_LIMIT=1)
    def test_large_statement_zip_is_guarded_and_selected_zip_still_works(self):
        response = self.upload_import()
        import_id = response.data["id"]
        summary = AccountingImportCustomer.objects.get(customer__customer_code="083")
        self.client.force_authenticate(self.accountant)

        guarded = self.client.get(reverse("accounting-import-statements-zip", args=[import_id]))
        self.assertEqual(guarded.status_code, 400)
        self.assertIn("may take too long", guarded.data["detail"])

        selected = self.client.get(
            reverse("accounting-import-statements-zip", args=[import_id]),
            {"customer_ids": str(summary.id), "style": "professional"},
        )
        self.assertEqual(selected.status_code, 200)
        self.assertEqual(selected["Content-Type"], "application/zip")
        with ZipFile(BytesIO(selected.content)) as archive:
            names = archive.namelist()
        self.assertEqual(len(names), 1)
        self.assertTrue(any("MILLENNIUM" in name for name in names))

    def test_ageing_filters_and_ordering(self):
        self.upload_import()
        self.client.force_authenticate(self.accountant)
        list_url = reverse("accounting-import-customer-list")

        over_60 = self.client.get(list_url, {"ageing": "over_60"})
        self.assertEqual(over_60.status_code, 200)
        self.assertGreaterEqual(len(over_60.data), 1)
        self.assertTrue(all(item["max_days"] > 60 for item in over_60.data))

        over_90 = self.client.get(list_url, {"ageing": "over_90"})
        self.assertEqual(over_90.status_code, 200)
        self.assertTrue(all(item["max_days"] > 90 for item in over_90.data))

        has_60_90 = self.client.get(list_url, {"ageing": "has_60_90"})
        self.assertEqual(has_60_90.status_code, 200)
        self.assertTrue(all(float(item["bucket_60_90"]) > 0 for item in has_60_90.data))

        ordered = self.client.get(list_url, {"ordering": "-max_days"})
        self.assertEqual(ordered.status_code, 200)
        max_days = [item["max_days"] for item in ordered.data]
        self.assertEqual(max_days, sorted(max_days, reverse=True))
