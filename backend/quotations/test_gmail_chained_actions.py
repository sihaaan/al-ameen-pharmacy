from copy import deepcopy
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api.models import Product

from .contract_intelligence import (
    GMAIL_READONLY_SCOPE,
    GMAIL_SEND_SCOPE,
    encrypt_token,
)
from .gmail_inquiry_import import (
    GMAIL_IDENTITY_MATCH_VERSION,
    approve_gmail_inquiry_company,
    claim_gmail_inquiry_handoff,
    issue_gmail_inquiry_handoff,
)
from .gmail_review_state import (
    gmail_identity_evidence_fingerprint,
    gmail_review_rows_fingerprint,
)
from .models import (
    Company,
    GmailInquiryImport,
    GmailOAuthConnection,
    Inquiry,
    Quotation,
    QuotationEmailDelivery,
    QuotationLine,
)
from .quotation_email_delivery import quotation_review_fingerprint
from .workflow_features import quotation_workflow_features


@override_settings(SECURE_SSL_REDIRECT=False)
class GmailChainedActionsTests(APITestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="gmail-chained-actions",
            password="unused",
            is_staff=True,
        )
        self.connection = GmailOAuthConnection.objects.create(
            user=self.staff,
            is_shared=True,
            email="shared-mailbox@example.test",
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )
        self.company = Company.objects.create(
            name="Chained Action Customer",
            email="buyer@chained-customer.example",
        )
        self.client.force_authenticate(self.staff)

    def analyzed_import(self, *, anchor="chained-message"):
        gmail_import, token = issue_gmail_inquiry_handoff(
            self.connection,
            anchor_message_id=anchor,
            gmail_thread_id=f"thread-{anchor}",
            mode=GmailInquiryImport.MODE_CURRENT_MESSAGE,
        )
        gmail_import = claim_gmail_inquiry_handoff(token, self.staff)
        gmail_import.status = GmailInquiryImport.STATUS_REVIEW_REQUIRED
        gmail_import.analysis_attempts = 2
        gmail_import.analysis = {
            "version": "gmail_inquiry_v2",
            "content_fingerprint": "c" * 64,
            "thread_analysis": {
                "customer_identity": {
                    "company_name": self.company.name,
                    "source_keys": ["body:identity"],
                    "confidence": 0.98,
                }
            },
            "preview": {
                "parse_method": "gmail_native_ai_v2",
                "original_text": "Private source body",
                "warnings": [],
                "meta": {},
                "lines": [
                    {
                        "row_key": "a" * 32,
                        "raw_name": "Gloves",
                        "raw_line": "Gloves | 10 | PCS",
                        "quantity": "10",
                        "unit": "PCS",
                        "unit_price": None,
                        "vat_rate": "0.00",
                        "operation": "added",
                        "parse_status": "parsed",
                        "parse_confidence": 0.99,
                        "included": True,
                        "reviewed_by_user": False,
                        "_source_keys": ["body:item"],
                    }
                ],
            },
        }
        gmail_import.candidates = {
            "identity_match_version": GMAIL_IDENTITY_MATCH_VERSION,
            "recommended_company_id": self.company.pk,
            "recommended_contact_id": None,
            "exact_company_match": False,
            "verified_identity_sender_emails": [
                "buyer@chained-customer.example"
            ],
            "companies": [
                {
                    "company_id": self.company.pk,
                    "company_name": self.company.name,
                    "confidence": 0.98,
                    "match_method": "verified_email_domain",
                    "evidence": [
                        {
                            "signal": "verified_email_domain",
                            "value": "chained-customer.example",
                            "message_ids": [anchor],
                        }
                    ],
                }
            ],
            "contacts": [],
            "ai_identity": {
                "company_name": self.company.name,
                "source_keys": ["body:identity"],
                "confidence": 0.98,
            },
        }
        gmail_import.message_manifest = [
            {
                "gmail_message_id": anchor,
                "subject": "Private RFQ",
                "sender": "Buyer <buyer@chained-customer.example>",
                "sent_at": timezone.now().isoformat(),
                "is_outbound": False,
            }
        ]
        gmail_import.save(
            update_fields=[
                "status",
                "analysis_attempts",
                "analysis",
                "candidates",
                "message_manifest",
                "updated_at",
            ]
        )
        return gmail_import

    def binding(self, gmail_import):
        gmail_import.refresh_from_db()
        return {
            "expected_source_fingerprint": gmail_import.source_fingerprint,
            "expected_analysis_attempt": gmail_import.analysis_attempts,
            "expected_review_rows_fingerprint": gmail_review_rows_fingerprint(
                gmail_import
            ),
            "identity_review_fingerprint": gmail_identity_evidence_fingerprint(
                gmail_import
            ),
        }

    def reviewed_row(self, *, raw_name="Gloves reviewed"):
        return {
            "row_key": "a" * 32,
            "raw_name": raw_name,
            "quantity": "10.000",
            "unit": "PCS",
            "included": True,
            "reviewed": True,
        }

    def approve(self, gmail_import):
        return approve_gmail_inquiry_company(
            gmail_import,
            self.staff,
            company=self.company,
            contact=None,
            suggested=True,
            identity_review_fingerprint=gmail_identity_evidence_fingerprint(
                gmail_import
            ),
        )

    @override_settings(
        QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True,
        QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED=True,
    )
    def test_bound_row_save_can_precede_approval_but_confirm_cannot(self):
        gmail_import = self.analyzed_import(anchor="save-before-approval")
        binding = self.binding(gmail_import)

        saved = self.client.patch(
            reverse("quotation-gmail-inquiry-import-detail", args=[gmail_import.pk]),
            {"review_lines": [self.reviewed_row()], **binding},
            format="json",
        )
        self.assertEqual(saved.status_code, status.HTTP_200_OK, saved.data)
        self.assertNotEqual(
            saved.data["review_rows_fingerprint"],
            binding["expected_review_rows_fingerprint"],
        )
        binding = self.binding(gmail_import)

        blocked = self.client.post(
            reverse("quotation-gmail-inquiry-import-confirm", args=[gmail_import.pk]),
            {"company": self.company.pk, "contact": None, **binding},
            format="json",
        )
        self.assertEqual(blocked.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Inquiry.objects.count(), 0)
        self.assertEqual(Quotation.objects.count(), 0)

    @override_settings(
        QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True,
        QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED=True,
    )
    def test_row_save_rejects_each_stale_binding_without_mutation(self):
        gmail_import = self.approve(self.analyzed_import(anchor="stale-save"))
        binding = self.binding(gmail_import)
        before = deepcopy(GmailInquiryImport.objects.get(pk=gmail_import.pk).analysis)
        stale_bindings = (
            {**binding, "expected_source_fingerprint": "f" * 64},
            {**binding, "expected_analysis_attempt": binding["expected_analysis_attempt"] + 1},
            {**binding, "expected_review_rows_fingerprint": "d" * 64},
            {**binding, "identity_review_fingerprint": "e" * 64},
        )

        for stale_binding in stale_bindings:
            with self.subTest(stale_binding=stale_binding):
                response = self.client.patch(
                    reverse(
                        "quotation-gmail-inquiry-import-detail",
                        args=[gmail_import.pk],
                    ),
                    {"review_lines": [self.reviewed_row()], **stale_binding},
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
                gmail_import.refresh_from_db()
                self.assertEqual(gmail_import.analysis, before)

    @override_settings(
        QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True,
        QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED=True,
    )
    def test_second_save_from_same_row_snapshot_cannot_overwrite_first(self):
        gmail_import = self.approve(
            self.analyzed_import(anchor="same-row-snapshot")
        )
        stale_binding = self.binding(gmail_import)
        url = reverse(
            "quotation-gmail-inquiry-import-detail",
            args=[gmail_import.pk],
        )

        first = self.client.patch(
            url,
            {"review_lines": [self.reviewed_row(raw_name="First review")], **stale_binding},
            format="json",
        )
        second = self.client.patch(
            url,
            {"review_lines": [self.reviewed_row(raw_name="Stale overwrite")], **stale_binding},
            format="json",
        )

        self.assertEqual(first.status_code, status.HTTP_200_OK, first.data)
        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT, second.data)
        gmail_import.refresh_from_db()
        line = gmail_import.analysis["preview"]["lines"][0]
        self.assertEqual(line["raw_name"], "First review")
        self.assertNotEqual(
            first.data["review_rows_fingerprint"],
            stale_binding["expected_review_rows_fingerprint"],
        )

    @override_settings(
        QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True,
        QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED=True,
    )
    def test_incomplete_binding_is_rejected_without_mutation(self):
        gmail_import = self.approve(self.analyzed_import(anchor="partial-binding"))
        before = deepcopy(GmailInquiryImport.objects.get(pk=gmail_import.pk).analysis)

        response = self.client.patch(
            reverse("quotation-gmail-inquiry-import-detail", args=[gmail_import.pk]),
            {
                "review_lines": [self.reviewed_row()],
                "expected_source_fingerprint": gmail_import.source_fingerprint,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        gmail_import.refresh_from_db()
        self.assertEqual(gmail_import.analysis, before)

    @override_settings(
        QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True,
        QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED=True,
    )
    def test_stale_confirm_returns_409_without_creating_records(self):
        gmail_import = self.approve(self.analyzed_import(anchor="stale-confirm"))
        binding = self.binding(gmail_import)

        response = self.client.post(
            reverse("quotation-gmail-inquiry-import-confirm", args=[gmail_import.pk]),
            {
                "company": self.company.pk,
                "contact": None,
                **binding,
                "expected_analysis_attempt": binding["expected_analysis_attempt"] + 1,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(Inquiry.objects.count(), 0)
        self.assertEqual(Quotation.objects.count(), 0)
        gmail_import.refresh_from_db()
        self.assertIsNone(gmail_import.inquiry_id)
        self.assertIsNone(gmail_import.quotation_id)

    @override_settings(
        QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True,
        QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED=True,
    )
    def test_post_save_confirm_requires_the_returned_rows_fingerprint(self):
        gmail_import = self.approve(
            self.analyzed_import(anchor="post-save-stale-confirm")
        )
        stale_binding = self.binding(gmail_import)
        saved = self.client.patch(
            reverse("quotation-gmail-inquiry-import-detail", args=[gmail_import.pk]),
            {"review_lines": [self.reviewed_row()], **stale_binding},
            format="json",
        )
        self.assertEqual(saved.status_code, status.HTTP_200_OK, saved.data)
        self.assertNotEqual(
            saved.data["review_rows_fingerprint"],
            stale_binding["expected_review_rows_fingerprint"],
        )

        stale_confirm = self.client.post(
            reverse("quotation-gmail-inquiry-import-confirm", args=[gmail_import.pk]),
            {
                "company": self.company.pk,
                "contact": None,
                **stale_binding,
            },
            format="json",
        )

        self.assertEqual(stale_confirm.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(Inquiry.objects.count(), 0)
        self.assertEqual(Quotation.objects.count(), 0)

    @override_settings(
        QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True,
        QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED=True,
    )
    def test_fresh_confirm_is_idempotent_and_keeps_selling_prices_blank(self):
        gmail_import = self.approve(self.analyzed_import(anchor="fresh-confirm"))
        binding = self.binding(gmail_import)
        saved = self.client.patch(
            reverse("quotation-gmail-inquiry-import-detail", args=[gmail_import.pk]),
            {"review_lines": [self.reviewed_row()], **binding},
            format="json",
        )
        self.assertEqual(saved.status_code, status.HTTP_200_OK, saved.data)
        binding["expected_review_rows_fingerprint"] = saved.data[
            "review_rows_fingerprint"
        ]

        url = reverse(
            "quotation-gmail-inquiry-import-confirm",
            args=[gmail_import.pk],
        )
        payload = {"company": self.company.pk, "contact": None, **binding}
        created = self.client.post(url, payload, format="json")
        repeated = self.client.post(url, payload, format="json")

        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)
        self.assertEqual(repeated.status_code, status.HTTP_200_OK, repeated.data)
        self.assertEqual(Inquiry.objects.count(), 1)
        self.assertEqual(Quotation.objects.count(), 1)
        quotation = Quotation.objects.get()
        self.assertEqual(quotation.status, Quotation.STATUS_DRAFT)
        self.assertIsNone(quotation.lines.get().unit_price)
        self.assertFalse(QuotationEmailDelivery.objects.exists())

    @override_settings(
        QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=False,
        QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED=False,
    )
    def test_flag_off_preserves_legacy_row_save_and_confirmation(self):
        gmail_import = self.analyzed_import(anchor="legacy-binding")
        stale = {
            "expected_source_fingerprint": "f" * 64,
            "expected_analysis_attempt": 999,
            "expected_review_rows_fingerprint": "d" * 64,
            "identity_review_fingerprint": "e" * 64,
        }
        saved = self.client.patch(
            reverse("quotation-gmail-inquiry-import-detail", args=[gmail_import.pk]),
            {
                "review_lines": [
                    {
                        key: value
                        for key, value in self.reviewed_row().items()
                        if key != "reviewed"
                    }
                ],
                **stale,
            },
            format="json",
        )
        created = self.client.post(
            reverse("quotation-gmail-inquiry-import-confirm", args=[gmail_import.pk]),
            {"company": self.company.pk, "contact": None, **stale},
            format="json",
        )

        self.assertEqual(saved.status_code, status.HTTP_200_OK, saved.data)
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)
        self.assertEqual(Quotation.objects.count(), 1)

    def test_feature_projection_requires_both_strict_flags(self):
        with override_settings(
            QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=False,
            QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED=True,
        ):
            self.assertFalse(
                quotation_workflow_features()["gmail_chained_actions"]
            )
        with override_settings(
            QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True,
            QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED="1",
        ):
            self.assertFalse(
                quotation_workflow_features()["gmail_chained_actions"]
            )
        with override_settings(
            QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True,
            QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED=True,
        ):
            self.assertTrue(
                quotation_workflow_features()["gmail_chained_actions"]
            )


@override_settings(SECURE_SSL_REDIRECT=False)
class QuotationChainedActionsTests(APITestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="quotation-chained-actions",
            password="unused",
            is_staff=True,
        )
        self.company = Company.objects.create(name="Quotation Chain Customer")
        self.product = Product.objects.create(
            name="Quotation Chain Product",
            price=Decimal("1.00"),
            status="draft",
        )
        self.quotation = Quotation.objects.create(
            company=self.company,
            created_by=self.staff,
        )
        self.line = QuotationLine.objects.create(
            quotation=self.quotation,
            product=self.product,
            item_name_snapshot="Customer wording",
            quantity=Decimal("2.000"),
            unit="PCS",
            unit_price=None,
            vat_rate=Decimal("5.00"),
            match_status=QuotationLine.MATCH_CONFIRMED,
        )
        self.connection = GmailOAuthConnection.objects.create(
            user=self.staff,
            is_shared=True,
            email="pharmacydxb@gmail.com",
            scopes=[GMAIL_READONLY_SCOPE, GMAIL_SEND_SCOPE],
            status=GmailOAuthConnection.STATUS_CONNECTED,
            access_token_encrypted=encrypt_token("access-token"),
            token_expiry=timezone.now() + timedelta(hours=1),
        )
        self.client.force_authenticate(self.staff)

    @override_settings(
        QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True,
        QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED=True,
    )
    def test_bound_line_save_returns_the_new_review_fingerprint(self):
        previous = quotation_review_fingerprint(self.quotation)

        response = self.client.post(
            reverse("quotation-bulk-update-lines", args=[self.quotation.pk]),
            {
                "lines": [{"id": self.line.pk, "unit_price": "12.50"}],
                "quotation_review_fingerprint": previous,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertTrue(
            response.data["quotation"]["workflow_features"][
                "gmail_chained_actions"
            ]
        )
        current = response.data["quotation"]["quotation_review_fingerprint"]
        self.assertRegex(current, r"^[0-9a-f]{64}$")
        self.assertNotEqual(current, previous)
        self.line.refresh_from_db()
        self.quotation.refresh_from_db()
        self.assertEqual(self.line.unit_price, Decimal("12.500"))
        self.assertEqual(current, quotation_review_fingerprint(self.quotation))
        self.assertEqual(self.quotation.status, Quotation.STATUS_DRAFT)
        self.assertFalse(QuotationEmailDelivery.objects.exists())

        preview = self.client.get(
            reverse("quotation-email-preview", args=[self.quotation.pk]),
            {"quotation_review_fingerprint": current},
        )
        self.assertEqual(preview.status_code, status.HTTP_200_OK, preview.data)
        self.assertTrue(preview.data["preview_fingerprint"])
        self.quotation.refresh_from_db()
        self.assertEqual(self.quotation.status, Quotation.STATUS_DRAFT)
        self.assertFalse(QuotationEmailDelivery.objects.exists())

    @override_settings(
        QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True,
        QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED=True,
    )
    def test_stale_line_save_returns_409_without_mutation(self):
        stale = quotation_review_fingerprint(self.quotation)
        QuotationLine.objects.filter(pk=self.line.pk).update(
            item_name_snapshot="Changed in another session"
        )

        response = self.client.post(
            reverse("quotation-bulk-update-lines", args=[self.quotation.pk]),
            {
                "lines": [{"id": self.line.pk, "quantity": "9.000"}],
                "quotation_review_fingerprint": stale,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, response.data)
        self.assertEqual(response.data["code"], "stale_quotation_review")
        self.line.refresh_from_db()
        self.assertEqual(self.line.quantity, Decimal("2.000"))
        self.assertEqual(self.line.item_name_snapshot, "Changed in another session")

    @override_settings(
        QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True,
        QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED=True,
    )
    def test_enabled_flag_keeps_unbound_legacy_line_save_available(self):
        response = self.client.post(
            reverse("quotation-bulk-update-lines", args=[self.quotation.pk]),
            {"lines": [{"id": self.line.pk, "quantity": "3.000"}]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.line.refresh_from_db()
        self.assertEqual(self.line.quantity, Decimal("3.000"))

    @override_settings(
        QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True,
        QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED=True,
    )
    def test_bound_validation_failure_rolls_back_every_submitted_row(self):
        second = QuotationLine.objects.create(
            quotation=self.quotation,
            product=self.product,
            item_name_snapshot="Second customer row",
            quantity=Decimal("1.000"),
            unit="BOX",
            unit_price=None,
            vat_rate=Decimal("0.00"),
            match_status=QuotationLine.MATCH_CONFIRMED,
        )
        current = quotation_review_fingerprint(self.quotation)

        response = self.client.post(
            reverse("quotation-bulk-update-lines", args=[self.quotation.pk]),
            {
                "lines": [
                    {"id": self.line.pk, "unit_price": "15.000"},
                    {"id": second.pk, "vat_rate": "7.00"},
                ],
                "quotation_review_fingerprint": current,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.line.refresh_from_db()
        second.refresh_from_db()
        self.assertIsNone(self.line.unit_price)
        self.assertEqual(second.vat_rate, Decimal("0.00"))

    @override_settings(
        QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=False,
        QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED=True,
    )
    def test_partial_rollout_disables_bound_path_and_preserves_legacy_save(self):
        response = self.client.post(
            reverse("quotation-bulk-update-lines", args=[self.quotation.pk]),
            {
                "lines": [{"id": self.line.pk, "quantity": "4.000"}],
                "quotation_review_fingerprint": "f" * 64,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.line.refresh_from_db()
        self.assertEqual(self.line.quantity, Decimal("4.000"))
