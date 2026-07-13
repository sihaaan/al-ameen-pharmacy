from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api.models import Product

from .models import (
    Company,
    CompanyPriceHistory,
    Quotation,
    QuotationLine,
    QuotationLPO,
    QuoteItem,
)
from .services import finalize_quotation, update_quotation_outcome


class ProductPriceContextTests(APITestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="price-context-staff", password="pass", is_staff=True)
        self.company = Company.objects.create(name="Price Context Company")
        self.other_company = Company.objects.create(name="Other Price Context Company")
        self.product = Product.objects.create(
            name="Context Bandage",
            price=Decimal("1.00"),
            pack_size="box",
            status="draft",
        )
        self.client.force_authenticate(self.staff)

    def create_finalized_price(
        self,
        *,
        company,
        unit_price,
        quoted_at,
        product=None,
        quote_item=None,
        quantity=Decimal("5.000"),
    ):
        quotation = Quotation.objects.create(company=company, created_by=self.staff)
        item = product or quote_item
        line = QuotationLine.objects.create(
            quotation=quotation,
            product=product,
            quote_item=quote_item,
            item_name_snapshot=item.name,
            quantity=quantity,
            unit="box",
            unit_price=Decimal(unit_price),
            match_status=QuotationLine.MATCH_CONFIRMED,
        )
        finalize_quotation(quotation, self.staff)
        history = CompanyPriceHistory.objects.get(quotation_line=line)
        CompanyPriceHistory.objects.filter(pk=history.pk).update(quoted_at=quoted_at)
        history.refresh_from_db()
        return quotation, line, history

    def current_quote(self, company=None):
        return Quotation.objects.create(company=company or self.company, created_by=self.staff)

    def product_price(self, quotation, product=None, **params):
        product = product or self.product
        return self.client.get(
            reverse("quotation-product-price", args=[quotation.id]),
            {"product": product.id, **params},
        )

    def test_context_is_company_scoped_newest_first_and_capped(self):
        now = timezone.now()
        oldest = self.create_finalized_price(
            company=self.company,
            product=self.product,
            unit_price="10.00",
            quoted_at=now - timedelta(days=3),
        )[0]
        middle = self.create_finalized_price(
            company=self.company,
            product=self.product,
            unit_price="11.25",
            quoted_at=now - timedelta(days=2),
        )[0]
        newest = self.create_finalized_price(
            company=self.company,
            product=self.product,
            unit_price="13.50",
            quoted_at=now - timedelta(days=1),
        )[0]
        other_company_quote = self.create_finalized_price(
            company=self.other_company,
            product=self.product,
            unit_price="99.00",
            quoted_at=now,
        )[0]

        response = self.product_price(self.current_quote(), history_limit=2)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["unit_price"], "13.50")
        self.assertEqual(response.data["latest_quoted"]["quotation"], newest.id)
        self.assertEqual(response.data["latest_accepted"], None)
        self.assertEqual(
            [row["quotation"] for row in response.data["history"]],
            [newest.id, middle.id],
        )
        self.assertNotIn(oldest.id, [row["quotation"] for row in response.data["history"]])
        self.assertNotIn(other_company_quote.id, [row["quotation"] for row in response.data["history"]])

    def test_context_is_product_scoped_within_the_same_company(self):
        now = timezone.now()
        other_product = Product.objects.create(
            name="Other Context Product",
            price=Decimal("2.00"),
            status="draft",
        )
        requested_quote = self.create_finalized_price(
            company=self.company,
            product=self.product,
            unit_price="10.00",
            quoted_at=now - timedelta(days=1),
        )[0]
        other_product_quote = self.create_finalized_price(
            company=self.company,
            product=other_product,
            unit_price="99.00",
            quoted_at=now,
        )[0]
        current = self.current_quote()

        requested_response = self.product_price(current)
        other_response = self.product_price(current, product=other_product)

        self.assertEqual(requested_response.status_code, status.HTTP_200_OK)
        self.assertEqual(other_response.status_code, status.HTTP_200_OK)
        self.assertEqual(requested_response.data["unit_price"], "10.00")
        self.assertEqual(
            [row["quotation"] for row in requested_response.data["history"]],
            [requested_quote.id],
        )
        self.assertNotIn(
            other_product_quote.id,
            [row["quotation"] for row in requested_response.data["history"]],
        )
        self.assertEqual(other_response.data["product"], other_product.id)
        self.assertEqual(other_response.data["unit_price"], "99.00")
        self.assertEqual(other_response.data["history"][0]["quotation"], other_product_quote.id)

    def test_context_excludes_the_quotation_being_queried(self):
        now = timezone.now()
        prior_quote = self.create_finalized_price(
            company=self.company,
            product=self.product,
            unit_price="9.00",
            quoted_at=now - timedelta(days=2),
        )[0]
        current_finalized_quote = self.create_finalized_price(
            company=self.company,
            product=self.product,
            unit_price="15.00",
            quoted_at=now - timedelta(days=1),
        )[0]

        response = self.product_price(current_finalized_quote)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["unit_price"], "9.00")
        self.assertEqual(response.data["latest_quoted"]["quotation"], prior_quote.id)
        self.assertEqual(
            [row["quotation"] for row in response.data["history"]],
            [prior_quote.id],
        )
        self.assertNotIn(
            current_finalized_quote.id,
            [row["quotation"] for row in response.data["history"]],
        )

    def test_accepted_context_uses_confirmed_outcome_values_and_only_confirmed_lpo(self):
        now = timezone.now()
        accepted_quote, accepted_line, _ = self.create_finalized_price(
            company=self.company,
            product=self.product,
            unit_price="10.00",
            quantity=Decimal("5.000"),
            quoted_at=now - timedelta(days=10),
        )
        update_quotation_outcome(
            accepted_quote,
            {
                "line_updates": [
                    {
                        "id": accepted_line.id,
                        "outcome_status": QuotationLine.OUTCOME_QUANTITY_CHANGED,
                        "accepted_quantity": "3.000",
                        "accepted_unit_price": "8.75",
                    }
                ]
            },
            self.staff,
        )
        accepted_quote.outcome_date = date(2026, 6, 20)
        accepted_quote.save(update_fields=["outcome_date", "updated_at"])
        QuotationLPO.objects.create(
            quotation=accepted_quote,
            lpo_number="LPO-CONFIRMED-8",
            status=QuotationLPO.STATUS_CONFIRMED,
            received_by=self.staff,
        )
        QuotationLPO.objects.create(
            quotation=accepted_quote,
            lpo_number="LPO-UNCONFIRMED-9",
            status=QuotationLPO.STATUS_PARSED,
            received_by=self.staff,
            received_at=now + timedelta(hours=1),
        )

        incomplete_quote, incomplete_line, _ = self.create_finalized_price(
            company=self.company,
            product=self.product,
            unit_price="11.00",
            quoted_at=now - timedelta(days=2),
        )
        QuotationLine.objects.filter(pk=incomplete_line.pk).update(
            outcome_status=QuotationLine.OUTCOME_ACCEPTED,
            accepted_unit_price=None,
            accepted_quantity=None,
        )
        pending_quote = self.create_finalized_price(
            company=self.company,
            product=self.product,
            unit_price="12.00",
            quoted_at=now - timedelta(days=1),
        )[0]

        response = self.product_price(self.current_quote())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["latest_quoted"]["quotation"], pending_quote.id)
        accepted = response.data["latest_accepted"]
        self.assertEqual(accepted["quotation"], accepted_quote.id)
        self.assertEqual(accepted["outcome_status"], QuotationLine.OUTCOME_QUANTITY_CHANGED)
        self.assertEqual(accepted["accepted_unit_price"], "8.75")
        self.assertEqual(accepted["accepted_quantity"], "3.000")
        self.assertEqual(accepted["accepted_at"], "2026-06-20")
        self.assertEqual(accepted["lpo_number"], "LPO-CONFIRMED-8")

        rows = {row["quotation"]: row for row in response.data["history"]}
        self.assertEqual(rows[accepted_quote.id]["quoted_unit_price"], "10.00")
        self.assertEqual(rows[accepted_quote.id]["accepted_unit_price"], "8.75")
        self.assertEqual(rows[pending_quote.id]["accepted_unit_price"], None)
        self.assertEqual(rows[pending_quote.id]["lpo_number"], "")
        self.assertEqual(rows[incomplete_quote.id]["outcome_status"], QuotationLine.OUTCOME_ACCEPTED)
        self.assertEqual(rows[incomplete_quote.id]["accepted_unit_price"], None)
        self.assertEqual(rows[incomplete_quote.id]["accepted_at"], None)

    def test_latest_quoted_and_latest_accepted_use_their_own_chronology(self):
        now = timezone.now()
        older_quote, older_line, _ = self.create_finalized_price(
            company=self.company,
            product=self.product,
            unit_price="9.00",
            quoted_at=now - timedelta(days=10),
        )
        newer_quote, newer_line, _ = self.create_finalized_price(
            company=self.company,
            product=self.product,
            unit_price="12.00",
            quoted_at=now - timedelta(days=2),
        )
        update_quotation_outcome(
            older_quote,
            {
                "line_updates": [
                    {
                        "id": older_line.id,
                        "outcome_status": QuotationLine.OUTCOME_ACCEPTED,
                        "accepted_unit_price": "8.00",
                    }
                ]
            },
            self.staff,
        )
        update_quotation_outcome(
            newer_quote,
            {
                "line_updates": [
                    {
                        "id": newer_line.id,
                        "outcome_status": QuotationLine.OUTCOME_ACCEPTED,
                        "accepted_unit_price": "11.00",
                    }
                ]
            },
            self.staff,
        )
        older_quote.outcome_date = date(2026, 7, 10)
        older_quote.save(update_fields=["outcome_date", "updated_at"])
        newer_quote.outcome_date = date(2026, 7, 5)
        newer_quote.save(update_fields=["outcome_date", "updated_at"])

        response = self.product_price(self.current_quote())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["latest_quoted"]["quotation"], newer_quote.id)
        self.assertEqual(response.data["latest_quoted"]["quoted_unit_price"], "12.00")
        self.assertEqual(response.data["latest_accepted"]["quotation"], older_quote.id)
        self.assertEqual(response.data["latest_accepted"]["accepted_unit_price"], "8.00")
        self.assertEqual(response.data["latest_accepted"]["accepted_at"], "2026-07-10")
        self.assertEqual(
            [row["quotation"] for row in response.data["history"]],
            [newer_quote.id, older_quote.id],
        )

    def test_no_history_payload_is_complete_and_backward_compatible(self):
        current = self.current_quote()

        response = self.product_price(current)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data,
            {
                "product": self.product.id,
                "product_name": self.product.name,
                "unit_price": "",
                "unit": "",
                "currency": current.currency,
                "source": "no_company_price_history",
                "source_label": f"No previous {self.company.name} price",
                "quoted_at": "",
                "latest_quoted": None,
                "latest_accepted": None,
                "history": [],
            },
        )

    def test_price_history_filters_do_not_collide_product_and_legacy_quote_item_ids(self):
        now = timezone.now()
        legacy_item = QuoteItem.objects.create(id=self.product.id, name="Legacy Collision Item", unit="box")
        product_quote = self.create_finalized_price(
            company=self.company,
            product=self.product,
            unit_price="10.00",
            quoted_at=now - timedelta(days=1),
        )[0]
        legacy_quote = self.create_finalized_price(
            company=self.company,
            quote_item=legacy_item,
            unit_price="20.00",
            quoted_at=now,
        )[0]
        self.assertEqual(self.product.id, legacy_item.id)

        list_url = reverse("quotation-price-history-list")
        product_response = self.client.get(list_url, {"company": self.company.id, "product": self.product.id})
        compatible_response = self.client.get(list_url, {"company": self.company.id, "item": self.product.id})
        legacy_response = self.client.get(list_url, {"company": self.company.id, "quote_item": legacy_item.id})
        explicit_legacy_response = self.client.get(
            list_url,
            {"company": self.company.id, "item": legacy_item.id, "item_type": "quote_item"},
        )
        company_response = self.client.get(
            reverse("quotation-company-price-history", args=[self.company.id]),
            {"item": self.product.id},
        )
        company_legacy_response = self.client.get(
            reverse("quotation-company-price-history", args=[self.company.id]),
            {"quote_item": legacy_item.id},
        )

        for response in [
            product_response,
            compatible_response,
            legacy_response,
            explicit_legacy_response,
            company_response,
            company_legacy_response,
        ]:
            self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["quotation"] for row in product_response.data], [product_quote.id])
        self.assertEqual([row["quotation"] for row in compatible_response.data], [product_quote.id])
        self.assertEqual([row["quotation"] for row in company_response.data], [product_quote.id])
        self.assertEqual([row["quotation"] for row in legacy_response.data], [legacy_quote.id])
        self.assertEqual([row["quotation"] for row in explicit_legacy_response.data], [legacy_quote.id])
        self.assertEqual([row["quotation"] for row in company_legacy_response.data], [legacy_quote.id])

    def test_batch_product_prices_returns_same_context_and_enforces_cap(self):
        now = timezone.now()
        second_product = Product.objects.create(name="Second Context Product", price=Decimal("2.00"), status="draft")
        accepted_quote, accepted_line, _ = self.create_finalized_price(
            company=self.company,
            product=self.product,
            unit_price="7.00",
            quoted_at=now,
        )
        update_quotation_outcome(
            accepted_quote,
            {
                "line_updates": [
                    {
                        "id": accepted_line.id,
                        "outcome_status": QuotationLine.OUTCOME_ACCEPTED,
                        "accepted_unit_price": "6.50",
                    }
                ]
            },
            self.staff,
        )
        second_quote = self.create_finalized_price(
            company=self.company,
            product=second_product,
            unit_price="17.25",
            quoted_at=now - timedelta(hours=1),
        )[0]
        current_quote = self.current_quote()

        response = self.client.get(
            reverse("quotation-product-prices", args=[current_quote.id]),
            {"products": f"{self.product.id},{second_product.id},{self.product.id}"},
        )
        cap_response = self.client.get(
            reverse("quotation-product-prices", args=[current_quote.id]),
            {"products": ",".join(str(value) for value in range(1, 102))},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(list(response.data["results"]), [str(self.product.id), str(second_product.id)])
        self.assertEqual(response.data["results"][str(self.product.id)]["unit_price"], "7.00")
        self.assertEqual(
            response.data["results"][str(self.product.id)]["latest_accepted"]["accepted_unit_price"],
            "6.50",
        )
        self.assertEqual(response.data["results"][str(second_product.id)]["unit_price"], "17.25")
        self.assertEqual(
            [row["quotation"] for row in response.data["results"][str(second_product.id)]["history"]],
            [second_quote.id],
        )
        self.assertEqual(response.data["results"][str(second_product.id)]["latest_accepted"], None)
        self.assertEqual(cap_response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_batch_product_prices_rejects_missing_ids_without_partial_results(self):
        missing_product_id = self.product.id + 10000
        current_quote = self.current_quote()

        response = self.client.get(
            reverse("quotation-product-prices", args=[current_quote.id]),
            {"products": f"{self.product.id},{missing_product_id}"},
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["missing_product_ids"], [missing_product_id])
        self.assertNotIn("results", response.data)
