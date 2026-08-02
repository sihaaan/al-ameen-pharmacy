from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api.models import Product

from .gmail_inquiry_import import _unified_prepared_rows
from .gmail_review_state import (
    GMAIL_IDENTITY_MATCH_VERSION,
    build_gmail_identity_approval,
    gmail_analysis_generation,
    gmail_identity_evidence_fingerprint,
    gmail_review_rows_fingerprint,
)
from .models import (
    Company,
    GmailInquiryImport,
    GmailOAuthConnection,
    Inquiry,
    Quotation,
    QuotationLine,
    ProductAlias,
)
from .serializers import GmailInquiryConfirmAndPrepareSerializer
from .workflow_features import quotation_workflow_features


@override_settings(
    SECURE_SSL_REDIRECT=False,
    QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True,
    QUOTATION_GMAIL_UNIFIED_WORKSPACE_ENABLED=True,
)
class GmailUnifiedWorkspaceTests(APITestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="gmail-unified-staff",
            password="unused",
            is_staff=True,
        )
        self.nonstaff = get_user_model().objects.create_user(
            username="gmail-unified-customer",
            password="unused",
        )
        self.connection = GmailOAuthConnection.objects.create(
            user=self.staff,
            is_shared=True,
            email="shared-unified@example.test",
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )
        self.company = Company.objects.create(
            name="Unified Customer",
            email="buyer@unified.example",
        )
        self.product = Product.objects.create(
            name="Approved Gloves",
            price=Decimal("1.00"),
            status="draft",
        )
        self.other_product = Product.objects.create(
            name="Approved Masks",
            price=Decimal("2.00"),
            status="draft",
        )
        self.client.force_authenticate(self.staff)

    def make_import(
        self,
        *,
        anchor="unified-anchor",
        thread="unified-thread",
        source_character="a",
        uncertain=False,
        suggested_product=None,
        customer_budget="999.00",
    ):
        suggested_product = suggested_product or self.product
        operation = "uncertain" if uncertain else "added"
        parse_status = "needs_review" if uncertain else "parsed"
        gmail_import = GmailInquiryImport.objects.create(
            gmail_connection=self.connection,
            mailbox_email=self.connection.email,
            gmail_thread_id=thread,
            anchor_message_id=anchor,
            selected_message_ids=[anchor],
            mode=GmailInquiryImport.MODE_CURRENT_MESSAGE,
            source_fingerprint=source_character * 64,
            status=GmailInquiryImport.STATUS_REVIEW_REQUIRED,
            claimed_by=self.staff,
            claimed_at=timezone.now(),
            selected_company=self.company,
            analysis_attempts=2,
            analysis_progress_generation="1" * 32,
            analyzed_at=timezone.now(),
            message_manifest=[
                {
                    "gmail_message_id": anchor,
                    "subject": "Private RFQ",
                    "sender": "Buyer <buyer@unified.example>",
                    "sent_at": timezone.now().isoformat(),
                    "is_outbound": False,
                }
            ],
            evidence=[
                {
                    "source_key": "body:item",
                    "kind": "email_body",
                    "rows": [],
                }
            ],
            candidates={
                "identity_match_version": GMAIL_IDENTITY_MATCH_VERSION,
                "recommended_company_id": self.company.pk,
                "recommended_contact_id": None,
                "identity_conflict": False,
                "identity_reanalysis_required": False,
                "companies": [],
                "contacts": [],
            },
            analysis={
                "version": "gmail_inquiry_v2",
                "content_fingerprint": "c" * 64,
                "preview": {
                    "parse_method": "gmail_native_ai_v2",
                    "original_text": "Private request body",
                    "warnings": [],
                    "meta": {},
                    "lines": [
                        {
                            "row_key": "b" * 32,
                            "raw_name": "Customer wording gloves",
                            "raw_line": "Customer wording gloves | 10 | PCS",
                            "quantity": "10",
                            "unit": "PCS",
                            # Customer evidence must never become selling price.
                            "unit_price": customer_budget,
                            "customer_price": customer_budget,
                            "vat_rate": "0.00",
                            "matched_product": suggested_product.pk,
                            "matched_quote_item": None,
                            "match_status": "unresolved",
                            "operation": operation,
                            "parse_status": parse_status,
                            "parse_confidence": 0.9,
                            "included": True,
                            "reviewed_by_user": False,
                            "_source_keys": ["body:item"],
                        }
                    ],
                },
            },
        )
        analysis = deepcopy(gmail_import.analysis)
        analysis["identity_approval"] = build_gmail_identity_approval(
            gmail_import,
            self.staff,
            suggested=False,
            request_fingerprint=gmail_identity_evidence_fingerprint(
                gmail_import
            ),
        )
        gmail_import.analysis = analysis
        gmail_import.save(update_fields=["analysis", "updated_at"])
        return gmail_import

    def binding(self, gmail_import):
        gmail_import.refresh_from_db()
        return {
            "expected_source_fingerprint": gmail_import.source_fingerprint,
            "expected_analysis_attempt": gmail_import.analysis_attempts,
            "expected_analysis_generation": gmail_analysis_generation(
                gmail_import
            ),
            "expected_review_rows_fingerprint": gmail_review_rows_fingerprint(
                gmail_import
            ),
            "identity_review_fingerprint": gmail_identity_evidence_fingerprint(
                gmail_import
            ),
        }

    def row(
        self,
        *,
        product=None,
        product_decision="approve",
        price=None,
        uncertainty_decision=None,
        raw_name="Customer wording gloves",
        quantity="10.000",
        included=True,
    ):
        payload = {
            "row_key": "b" * 32,
            "raw_name": raw_name if included else "Customer wording gloves",
            "quantity": quantity if included else None,
            "unit": "PCS" if included else "",
            "included": included,
            "product": (product or self.product).pk if included else None,
            "quote_item": None,
            "product_decision": product_decision if included else "exclude",
            "match_status": "confirmed" if included else "ignored",
            "unit_price": price,
            "vat_rate": "5.00",
        }
        if uncertainty_decision is not None:
            payload["uncertainty_decision"] = uncertainty_decision
        return payload

    def url(self, gmail_import):
        return reverse(
            "quotation-gmail-inquiry-import-confirm-and-prepare-quotation",
            args=[gmail_import.pk],
        )

    def post(self, gmail_import, *, row=None, binding=None):
        return self.client.post(
            self.url(gmail_import),
            {
                **(binding or self.binding(gmail_import)),
                "rows": [row or self.row()],
            },
            format="json",
        )

    def test_flag_is_strict_default_off_and_depends_on_review_ui(self):
        with override_settings(
            QUOTATION_GMAIL_UNIFIED_WORKSPACE_ENABLED=False,
            QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True,
        ):
            self.assertFalse(
                quotation_workflow_features()["gmail_unified_workspace"]
            )

    def test_analysis_generation_excludes_mutable_review_and_preparation_state(self):
        gmail_import = self.make_import(anchor="generation-contract")
        original = gmail_analysis_generation(gmail_import)
        analysis = deepcopy(gmail_import.analysis)
        analysis["preview"]["lines"][0]["raw_name"] = "Employee correction"
        analysis["identity_approval"]["approved_at"] = timezone.now().isoformat()
        analysis["unified_preparation"] = {"fingerprint": "f" * 64}
        gmail_import.analysis = analysis
        self.assertEqual(gmail_analysis_generation(gmail_import), original)
        gmail_import.analysis_attempts += 1
        self.assertNotEqual(gmail_analysis_generation(gmail_import), original)
        with override_settings(
            QUOTATION_GMAIL_UNIFIED_WORKSPACE_ENABLED=True,
            QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=False,
        ):
            self.assertFalse(
                quotation_workflow_features()["gmail_unified_workspace"]
            )

    def test_endpoint_is_404_when_flag_is_off(self):
        gmail_import = self.make_import()
        with override_settings(
            QUOTATION_GMAIL_UNIFIED_WORKSPACE_ENABLED=False
        ):
            response = self.post(gmail_import)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(Inquiry.objects.count(), 0)

        legacy = self.client.post(
            reverse(
                "quotation-gmail-inquiry-import-confirm",
                args=[gmail_import.pk],
            ),
            {
                "company": self.company.pk,
                "contact": None,
                "identity_review_fingerprint": gmail_identity_evidence_fingerprint(
                    gmail_import
                ),
            },
            format="json",
        )
        self.assertEqual(legacy.status_code, status.HTTP_201_CREATED, legacy.data)
        self.assertEqual(Inquiry.objects.count(), 1)

    def test_staff_ownership_permissions_are_preserved(self):
        gmail_import = self.make_import()
        payload = {**self.binding(gmail_import), "rows": [self.row()]}
        self.client.force_authenticate(self.nonstaff)
        response = self.client.post(self.url(gmail_import), payload, format="json")
        self.assertIn(
            response.status_code,
            {status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND},
        )
        self.assertEqual(Inquiry.objects.count(), 0)

    def test_archived_product_cannot_be_prepared(self):
        gmail_import = self.make_import(anchor="archived-product")
        self.product.status = "archived"
        self.product.save(update_fields=["status", "updated_at"])

        response = self.post(gmail_import)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Inquiry.objects.count(), 0)

    def test_missing_catalogue_id_cannot_be_prepared(self):
        gmail_import = self.make_import(anchor="missing-product")
        row = self.row(product_decision="correct")
        row["product"] = max(self.product.pk, self.other_product.pk) + 999999

        response = self.post(gmail_import, row=row)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Inquiry.objects.count(), 0)

    def test_catalogue_availability_is_bulk_resolved_for_included_rows(self):
        gmail_import = self.make_import(anchor="bulk-catalogue-validation")
        analysis = deepcopy(gmail_import.analysis)
        analysis["preview"]["lines"].append(
            {
                "row_key": "d" * 32,
                "raw_name": "Customer wording masks",
                "raw_line": "Customer wording masks | 4 | BOX",
                "quantity": "4",
                "unit": "BOX",
                "matched_product": self.other_product.pk,
                "matched_quote_item": None,
                "match_status": "unresolved",
                "operation": "added",
                "parse_status": "parsed",
                "parse_confidence": 0.9,
                "included": True,
                "reviewed_by_user": False,
                "_source_keys": ["body:item"],
            }
        )
        gmail_import.analysis = analysis
        second_row = {
            **self.row(product=self.other_product),
            "row_key": "d" * 32,
            "raw_name": "Customer wording masks",
            "quantity": "4.000",
            "unit": "BOX",
        }

        with CaptureQueriesContext(connection) as captured:
            prepared = _unified_prepared_rows(
                gmail_import,
                [self.row(), second_row],
                self.staff,
            )

        product_availability_queries = [
            query["sql"]
            for query in captured.captured_queries
            if 'FROM "api_product"' in query["sql"]
        ]
        self.assertEqual(len(prepared), 2)
        self.assertEqual(len(product_availability_queries), 1)

    def test_request_serializer_validates_catalogue_ids_without_queries(self):
        rows = []
        for index in range(20):
            row = self.row()
            row["row_key"] = f"{index + 1:032x}"
            rows.append(row)
        payload = {
            "expected_source_fingerprint": "1" * 64,
            "expected_analysis_attempt": 1,
            "expected_analysis_generation": "2" * 64,
            "expected_review_rows_fingerprint": "3" * 64,
            "identity_review_fingerprint": "4" * 64,
            "rows": rows,
        }

        with CaptureQueriesContext(connection) as captured:
            serializer = GmailInquiryConfirmAndPrepareSerializer(data=payload)
            self.assertTrue(serializer.is_valid(), serializer.errors)

        self.assertEqual(len(captured.captured_queries), 0)
        self.assertEqual(
            {row["product"] for row in serializer.validated_data["rows"]},
            {self.product.pk},
        )

    def test_creates_prepared_quote_with_blank_employee_price_and_no_alias(self):
        gmail_import = self.make_import(customer_budget="987.65")
        before_aliases = ProductAlias.objects.count()
        before_products = Product.objects.count()

        response = self.post(gmail_import)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertTrue(response.data["created"])
        self.assertTrue(response.data["prepared"])
        self.assertTrue(response.data["prepared_for_preview"])
        self.assertFalse(response.data["preparation_reused"])
        self.assertEqual(response.data["reused_reason"], "")
        self.assertEqual(
            response.data["quotation_review_fingerprint"],
            response.data["quotation"]["quotation_review_fingerprint"],
        )
        line = QuotationLine.objects.get()
        self.assertEqual(line.product_id, self.product.pk)
        self.assertEqual(line.match_status, QuotationLine.MATCH_CONFIRMED)
        self.assertIsNone(line.unit_price)
        self.assertEqual(line.vat_rate, Decimal("5.00"))
        self.assertEqual(ProductAlias.objects.count(), before_aliases)
        self.assertEqual(Product.objects.count(), before_products)
        gmail_import.refresh_from_db()
        marker = gmail_import.analysis["unified_preparation"]
        self.assertEqual(set(marker), {
            "version",
            "fingerprint",
            "source_fingerprint",
            "analysis_attempt",
            "analysis_generation",
            "review_rows_fingerprint",
            "identity_review_fingerprint",
        })
        self.assertNotIn("987.65", str(marker))

    def test_employee_entered_price_is_saved_but_budget_fields_are_rejected(self):
        gmail_import = self.make_import(anchor="price-input")
        priced = self.post(gmail_import, row=self.row(price="12.50"))
        self.assertEqual(priced.status_code, status.HTTP_201_CREATED, priced.data)
        self.assertEqual(QuotationLine.objects.get().unit_price, Decimal("12.500"))

        other = self.make_import(
            anchor="budget-rejected",
            thread="budget-rejected-thread",
            source_character="d",
        )
        invalid_row = self.row()
        invalid_row["customer_budget"] = "1.00"
        rejected = self.post(other, row=invalid_row)
        self.assertEqual(rejected.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Inquiry.objects.count(), 1)

    def test_each_stale_binding_rejects_without_creation(self):
        gmail_import = self.make_import(anchor="stale")
        mutations = {
            "expected_source_fingerprint": "e" * 64,
            "expected_analysis_attempt": gmail_import.analysis_attempts + 1,
            "expected_analysis_generation": "e" * 64,
            "expected_review_rows_fingerprint": "e" * 64,
            "identity_review_fingerprint": "e" * 64,
        }

        for field, stale_value in mutations.items():
            with self.subTest(field=field):
                binding = self.binding(gmail_import)
                binding[field] = stale_value
                response = self.post(gmail_import, binding=binding)
                self.assertEqual(
                    response.status_code,
                    status.HTTP_409_CONFLICT,
                    response.data,
                )
                self.assertEqual(response.data["code"], "stale_gmail_review")
                self.assertEqual(Inquiry.objects.count(), 0)

        self.assertEqual(Inquiry.objects.count(), 0)

    def test_duplicate_server_row_keys_fail_closed(self):
        gmail_import = self.make_import(anchor="duplicate-row-key")
        analysis = deepcopy(gmail_import.analysis)
        duplicate = deepcopy(analysis["preview"]["lines"][0])
        duplicate["raw_name"] = "Different row with duplicated identifier"
        analysis["preview"]["lines"].append(duplicate)
        gmail_import.analysis = analysis
        gmail_import.save(update_fields=["analysis", "updated_at"])

        response = self.post(gmail_import)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Inquiry.objects.count(), 0)

    def test_company_change_requires_explicit_product_correction(self):
        gmail_import = self.make_import(anchor="company-context")
        corrected_company = Company.objects.create(
            name="Corrected Customer",
            email="buyer@corrected.example",
        )
        analysis = deepcopy(gmail_import.analysis)
        analysis.pop("identity_approval", None)
        analysis["preview"]["lines"][0]["match_reason"] = (
            "Old company-specific history suggested this Product."
        )
        gmail_import.selected_company = corrected_company
        gmail_import.analysis = analysis
        gmail_import.save(
            update_fields=["selected_company", "analysis", "updated_at"]
        )
        analysis = deepcopy(gmail_import.analysis)
        analysis["identity_approval"] = build_gmail_identity_approval(
            gmail_import,
            self.staff,
            suggested=False,
            request_fingerprint=gmail_identity_evidence_fingerprint(
                gmail_import
            ),
        )
        gmail_import.analysis = analysis
        gmail_import.save(update_fields=["analysis", "updated_at"])

        stale_approval = self.post(gmail_import, row=self.row())
        self.assertEqual(
            stale_approval.status_code,
            status.HTTP_400_BAD_REQUEST,
            stale_approval.data,
        )
        self.assertEqual(Inquiry.objects.count(), 0)

        corrected = self.post(
            gmail_import,
            row=self.row(product_decision="correct"),
        )
        self.assertEqual(corrected.status_code, status.HTTP_201_CREATED, corrected.data)
        self.assertEqual(Quotation.objects.get().company_id, corrected_company.pk)
        saved_reason = QuotationLine.objects.get().match_reason
        self.assertIn("Staff corrected the Product suggestion", saved_reason)
        self.assertNotIn("Old company-specific history", saved_reason)
        gmail_import.refresh_from_db()
        self.assertEqual(
            gmail_import.analysis["preview"]["lines"][0]["match_company_id"],
            corrected_company.pk,
        )

    def test_uncertainty_and_product_decisions_are_explicit(self):
        gmail_import = self.make_import(anchor="uncertain", uncertain=True)
        missing_uncertainty = self.post(gmail_import)
        self.assertEqual(
            missing_uncertainty.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(Inquiry.objects.count(), 0)

        approved = self.post(
            gmail_import,
            row=self.row(uncertainty_decision="approve"),
        )
        self.assertEqual(approved.status_code, status.HTTP_201_CREATED, approved.data)

        other = self.make_import(
            anchor="wrong-product-decision",
            thread="wrong-product-decision-thread",
            source_character="e",
        )
        wrong = self.post(
            other,
            row=self.row(
                product=self.other_product,
                product_decision="approve",
            ),
        )
        self.assertEqual(wrong.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Quotation.objects.count(), 1)

        corrected_import = self.make_import(
            anchor="corrected-product",
            thread="corrected-product-thread",
            source_character="3",
        )
        corrected = self.post(
            corrected_import,
            row=self.row(
                product=self.other_product,
                product_decision="correct",
            ),
        )
        self.assertEqual(
            corrected.status_code,
            status.HTTP_201_CREATED,
            corrected.data,
        )
        self.assertEqual(
            QuotationLine.objects.order_by("-pk").first().product_id,
            self.other_product.pk,
        )

        unchanged_import = self.make_import(
            anchor="unchanged-correction",
            thread="unchanged-correction-thread",
            source_character="4",
        )
        unchanged = self.post(
            unchanged_import,
            row=self.row(product_decision="correct"),
        )
        self.assertEqual(unchanged.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Quotation.objects.count(), 2)

    def test_missing_or_unknown_server_evidence_fails_closed(self):
        gmail_import = self.make_import(anchor="missing-evidence")
        gmail_import.evidence = []
        gmail_import.save(update_fields=["evidence", "updated_at"])

        response = self.post(gmail_import)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Inquiry.objects.count(), 0)

    def test_explicitly_excluded_row_is_saved_as_reviewed_but_not_quoted(self):
        gmail_import = self.make_import(anchor="excluded-row")
        analysis = deepcopy(gmail_import.analysis)
        analysis["preview"]["lines"].append(
            {
                "row_key": "c" * 32,
                "raw_name": "Duplicate masks",
                "quantity": "5",
                "unit": "PCS",
                "operation": "duplicate",
                "parse_status": "ignored",
                "included": False,
                "matched_product": self.other_product.pk,
                "_source_keys": ["body:item"],
            }
        )
        gmail_import.analysis = analysis
        gmail_import.save(update_fields=["analysis", "updated_at"])
        excluded = {
            "row_key": "c" * 32,
            "raw_name": "Browser attempted rewrite",
            "quantity": None,
            "unit": "",
            "included": False,
            "uncertainty_decision": "exclude",
            "product": None,
            "quote_item": None,
            "product_decision": "exclude",
            "match_status": "ignored",
            "unit_price": None,
            "vat_rate": "0.00",
        }
        response = self.client.post(
            self.url(gmail_import),
            {
                **self.binding(gmail_import),
                "rows": [self.row(), excluded],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(QuotationLine.objects.count(), 1)
        gmail_import.refresh_from_db()
        excluded_state = gmail_import.analysis["preview"]["lines"][1]
        self.assertFalse(excluded_state["included"])
        self.assertTrue(excluded_state["reviewed_by_user"])
        self.assertEqual(excluded_state["match_status"], "ignored")
        self.assertEqual(excluded_state["raw_name"], "Duplicate masks")
        self.assertEqual(excluded_state["quantity"], "5")
        self.assertEqual(excluded_state["unit"], "PCS")
        self.assertEqual(excluded_state["_source_keys"], ["body:item"])

    def test_exact_double_click_does_not_reapply_or_auto_preview(self):
        gmail_import = self.make_import(anchor="double-click")
        binding = self.binding(gmail_import)
        row = self.row(price="12.50")

        first = self.post(gmail_import, row=row, binding=binding)
        # A lost-response retry must still reach the keyed idempotency marker
        # even if the selected catalogue row is archived after commit.
        self.product.status = "archived"
        self.product.save(update_fields=["status", "updated_at"])
        second = self.post(gmail_import, row=row, binding=binding)

        self.assertEqual(first.status_code, status.HTTP_201_CREATED, first.data)
        self.assertEqual(second.status_code, status.HTTP_200_OK, second.data)
        self.assertEqual(Inquiry.objects.count(), 1)
        self.assertEqual(Quotation.objects.count(), 1)
        self.assertEqual(QuotationLine.objects.count(), 1)
        self.assertFalse(second.data["created"])
        self.assertFalse(second.data["prepared"])
        self.assertFalse(second.data["prepared_for_preview"])
        self.assertTrue(second.data["preparation_reused"])
        self.assertEqual(second.data["reused_reason"], "same_preparation")

    def test_same_thread_import_returns_existing_quote_without_applying_rows(self):
        first_import = self.make_import(anchor="thread-first")
        first = self.post(first_import, row=self.row(price="10.00"))
        self.assertEqual(first.status_code, status.HTTP_201_CREATED, first.data)
        quotation_id = first.data["quotation"]["id"]

        second_import = self.make_import(
            anchor="thread-second",
            thread="unified-thread",
            source_character="2",
            suggested_product=self.other_product,
        )
        second = self.post(
            second_import,
            row=self.row(
                product=self.other_product,
                product_decision="approve",
                price="77.00",
            ),
        )

        self.assertEqual(second.status_code, status.HTTP_200_OK, second.data)
        self.assertEqual(second.data["quotation"]["id"], quotation_id)
        self.assertFalse(second.data["prepared"])
        self.assertFalse(second.data["prepared_for_preview"])
        self.assertTrue(second.data["reused_existing_thread"])
        self.assertEqual(second.data["reused_reason"], "thread_already_confirmed")
        self.assertEqual(Inquiry.objects.count(), 1)
        line = QuotationLine.objects.get()
        self.assertEqual(line.product_id, self.product.pk)
        self.assertEqual(line.unit_price, Decimal("10.000"))
        second_import.refresh_from_db()
        self.assertIsNone(second_import.quotation_id)

    @patch("quotations.views.record_gmail_workflow_metric")
    def test_new_preparation_emits_safe_quote_and_pricing_metrics(self, record):
        gmail_import = self.make_import(anchor="metric-preparation")
        binding = self.binding(gmail_import)
        response = self.post(
            gmail_import,
            row=self.row(price="8.50"),
            binding=binding,
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        events = [call.args[1] for call in record.call_args_list]
        self.assertIn("quotation_created_or_reused", events)
        self.assertIn("pricing_saved", events)
        pricing = next(
            call
            for call in record.call_args_list
            if call.args[1] == "pricing_saved"
        )
        self.assertEqual(pricing.kwargs["counts"]["priced_row_count"], 1)
        self.assertNotIn("8.50", str(pricing))

        record.reset_mock()
        repeated = self.post(
            gmail_import,
            row=self.row(price="8.50"),
            binding=binding,
        )
        self.assertEqual(repeated.status_code, status.HTTP_200_OK)
        self.assertNotIn(
            "pricing_saved",
            [call.args[1] for call in record.call_args_list],
        )
