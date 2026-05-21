from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Company, CompanyPriceHistory, Quotation, QuotationLine, QuoteItem


class QuotationPermissionTests(APITestCase):
    list_route_names = [
        "quotation-company-list",
        "quotation-contact-list",
        "quotation-item-list",
        "quotation-inquiry-list",
        "quotation-inquiry-line-list",
        "quotation-list",
        "quotation-line-list",
        "quotation-price-history-list",
        "quotation-audit-log-list",
    ]

    def setUp(self):
        self.company = Company.objects.create(name="Blocked Test Company")
        self.staff = User.objects.create_user(username="staff", password="pass", is_staff=True)
        self.customer = User.objects.create_user(username="customer", password="pass")

    def test_anonymous_users_are_blocked_from_all_list_endpoints(self):
        for route_name in self.list_route_names:
            with self.subTest(route=route_name):
                response = self.client.get(reverse(route_name))
                self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_non_staff_users_are_blocked_from_all_list_endpoints(self):
        self.client.force_authenticate(self.customer)
        for route_name in self.list_route_names:
            with self.subTest(route=route_name):
                response = self.client.get(reverse(route_name))
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_users_are_allowed_on_all_list_endpoints(self):
        self.client.force_authenticate(self.staff)
        for route_name in self.list_route_names:
            with self.subTest(route=route_name):
                response = self.client.get(reverse(route_name))
                self.assertEqual(response.status_code, status.HTTP_200_OK)


class QuotationWorkflowTests(APITestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="staff", password="pass", is_staff=True)
        self.customer = User.objects.create_user(username="customer", password="pass")
        self.company = Company.objects.create(name="Workflow Company")
        self.quote_item = QuoteItem.objects.create(name="Bandage Pack", unit="box")
        self.client.force_authenticate(self.staff)

    def create_quote(self):
        return Quotation.objects.create(company=self.company, created_by=self.staff)

    def create_valid_line(self, quotation):
        return QuotationLine.objects.create(
            quotation=quotation,
            quote_item=self.quote_item,
            item_name_snapshot="Bandage Pack",
            quantity=Decimal("2.000"),
            unit="box",
            unit_price=Decimal("10.00"),
            match_status=QuotationLine.MATCH_CONFIRMED,
        )

    def test_cannot_finalize_invalid_quote(self):
        quotation = self.create_quote()
        QuotationLine.objects.create(
            quotation=quotation,
            item_name_snapshot="Unknown Item",
            quantity=Decimal("1.000"),
            unit="box",
            match_status=QuotationLine.MATCH_UNRESOLVED,
        )

        response = self.client.post(reverse("quotation-finalize", args=[quotation.id]))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        quotation.refresh_from_db()
        self.assertEqual(quotation.status, Quotation.STATUS_DRAFT)
        self.assertEqual(CompanyPriceHistory.objects.count(), 0)

    def test_finalized_quote_appends_price_history_once(self):
        quotation = self.create_quote()
        self.create_valid_line(quotation)

        response = self.client.post(reverse("quotation-finalize", args=[quotation.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        quotation.refresh_from_db()
        self.assertEqual(quotation.status, Quotation.STATUS_FINALIZED)
        self.assertEqual(CompanyPriceHistory.objects.count(), 1)
        history = CompanyPriceHistory.objects.get()
        self.assertEqual(history.company, self.company)
        self.assertEqual(history.quote_item, self.quote_item)
        self.assertEqual(history.unit_price, Decimal("10.00"))

        second_response = self.client.post(reverse("quotation-finalize", args=[quotation.id]))
        self.assertEqual(second_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(CompanyPriceHistory.objects.count(), 1)

    def test_finalized_quote_cannot_be_edited(self):
        quotation = self.create_quote()
        line = self.create_valid_line(quotation)
        self.client.post(reverse("quotation-finalize", args=[quotation.id]))

        quote_response = self.client.patch(
            reverse("quotation-detail", args=[quotation.id]),
            {"notes": "Should fail"},
            format="json",
        )
        line_response = self.client.patch(
            reverse("quotation-line-detail", args=[line.id]),
            {"unit_price": "12.00"},
            format="json",
        )

        self.assertEqual(quote_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(line_response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_revision_creates_new_draft(self):
        quotation = self.create_quote()
        self.create_valid_line(quotation)
        self.client.post(reverse("quotation-finalize", args=[quotation.id]))

        response = self.client.post(reverse("quotation-revise", args=[quotation.id]))

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        quotation.refresh_from_db()
        self.assertEqual(quotation.status, Quotation.STATUS_REVISED)
        revision = Quotation.objects.get(id=response.data["id"])
        self.assertEqual(revision.status, Quotation.STATUS_DRAFT)
        self.assertEqual(revision.version, 2)
        self.assertEqual(revision.parent, quotation)
        self.assertEqual(revision.lines.count(), 1)

    def test_pdf_endpoint_is_staff_only(self):
        quotation = self.create_quote()
        self.create_valid_line(quotation)

        self.client.force_authenticate(self.customer)
        blocked = self.client.get(reverse("quotation-pdf", args=[quotation.id]))
        self.assertEqual(blocked.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.staff)
        allowed = self.client.get(reverse("quotation-pdf", args=[quotation.id]))
        self.assertEqual(allowed.status_code, status.HTTP_200_OK)
        self.assertEqual(allowed["Content-Type"], "application/pdf")
