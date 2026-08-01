from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from .gmail_inquiry_import import (
    GMAIL_IDENTITY_MATCH_VERSION,
    GmailInquiryImportError,
    analyze_gmail_inquiry_import,
    approve_gmail_inquiry_company,
    claim_gmail_inquiry_handoff,
    confirm_gmail_inquiry_import,
    issue_gmail_inquiry_handoff,
    update_gmail_inquiry_identity,
    update_gmail_inquiry_review_lines,
    update_gmail_inquiry_selection,
)
from .gmail_review_state import (
    gmail_identity_approval_is_current,
    gmail_identity_evidence_fingerprint,
    gmail_identity_review_projection,
)
from .models import (
    Company,
    CompanyContact,
    GmailInquiryImport,
    GmailOAuthConnection,
    GmailWorkflowMetric,
)
from .serializers import (
    GmailInquiryImportSerializer,
    GmailInquiryReviewLineUpdateSerializer,
)


@override_settings(SECURE_SSL_REDIRECT=False)
class GmailReviewUIV2Tests(TransactionTestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="gmail-review-v2",
            password="unused",
            is_staff=True,
        )
        self.other_staff = get_user_model().objects.create_user(
            username="gmail-review-other",
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
            name="Verified Customer",
            email="buyer@verified-customer.example",
        )
        self.other_company = Company.objects.create(name="Manual Customer")
        self.contact = CompanyContact.objects.create(
            company=self.company,
            name="Buyer",
            email="buyer@verified-customer.example",
            is_active=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.staff)

    def analyzed_import(self, *, anchor="review-message", uncertain=True):
        gmail_import, token = issue_gmail_inquiry_handoff(
            self.connection,
            anchor_message_id=anchor,
            gmail_thread_id=f"thread-{anchor}",
            mode=GmailInquiryImport.MODE_CURRENT_MESSAGE,
        )
        gmail_import = claim_gmail_inquiry_handoff(token, self.staff)
        first_operation = "uncertain" if uncertain else "added"
        first_status = "needs_review" if uncertain else "parsed"
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
                        "operation": first_operation,
                        "parse_status": first_status,
                        "parse_confidence": 0.8,
                        "included": True,
                        "reviewed_by_user": False,
                        "_source_keys": ["body:item"],
                    },
                    {
                        "row_key": "b" * 32,
                        "raw_name": "Masks",
                        "raw_line": "Masks | 5 | BOX",
                        "quantity": "5",
                        "unit": "BOX",
                        "unit_price": None,
                        "vat_rate": "0.00",
                        "operation": "added",
                        "parse_status": "parsed",
                        "parse_confidence": 0.99,
                        "included": True,
                        "reviewed_by_user": False,
                        "_source_keys": ["body:item"],
                    },
                ],
            },
        }
        gmail_import.candidates = {
            "identity_match_version": GMAIL_IDENTITY_MATCH_VERSION,
            "recommended_company_id": self.company.pk,
            "recommended_contact_id": self.contact.pk,
            "exact_company_match": False,
            "verified_identity_sender_emails": [
                "buyer@verified-customer.example"
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
                            "value": "verified-customer.example",
                            "message_ids": [anchor],
                        }
                    ],
                }
            ],
            "contacts": [
                {
                    "contact_id": self.contact.pk,
                    "company_id": self.company.pk,
                    "contact_name": self.contact.name,
                }
            ],
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
                "sender": "Buyer <buyer@verified-customer.example>",
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

    def approve(self, gmail_import, *, company=None, contact=None, suggested=True):
        return approve_gmail_inquiry_company(
            gmail_import,
            self.staff,
            company=company or self.company,
            contact=contact,
            suggested=suggested,
            identity_review_fingerprint=gmail_identity_evidence_fingerprint(
                gmail_import
            ),
        )

    @override_settings(
        QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True,
        QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=True,
    )
    def test_suggested_approval_is_explicit_idempotent_and_never_selects_contact(self):
        gmail_import = self.analyzed_import()
        request_fingerprint = gmail_identity_evidence_fingerprint(gmail_import)

        approved = approve_gmail_inquiry_company(
            gmail_import,
            self.staff,
            company=self.company,
            contact=None,
            suggested=True,
            identity_review_fingerprint=request_fingerprint,
        )
        repeated = approve_gmail_inquiry_company(
            approved,
            self.staff,
            company=self.company,
            contact=None,
            suggested=True,
            identity_review_fingerprint=request_fingerprint,
        )

        self.assertEqual(repeated.selected_company_id, self.company.pk)
        self.assertIsNone(repeated.selected_contact_id)
        self.assertTrue(gmail_identity_approval_is_current(repeated))
        approval = repeated.analysis["identity_approval"]
        self.assertEqual(approval["approved_by_user_id"], self.staff.pk)
        self.assertEqual(approval["analysis_attempt"], 2)
        self.assertTrue(approval["suggested"])
        self.assertEqual(
            GmailWorkflowMetric.objects.filter(
                gmail_import=gmail_import,
                event_name="company_approved",
            ).count(),
            1,
        )
        reviewed = update_gmail_inquiry_review_lines(
            repeated,
            self.staff,
            review_lines=[
                {
                    "row_key": "a" * 32,
                    "raw_name": "Gloves",
                    "quantity": "10",
                    "unit": "PCS",
                    "included": True,
                    "reviewed": True,
                }
            ],
        )
        response = self.client.post(
            reverse(
                "quotation-gmail-inquiry-import-confirm",
                args=[reviewed.pk],
            ),
            {
                "company": self.company.pk,
                "contact": None,
                "identity_review_fingerprint": gmail_identity_evidence_fingerprint(
                    reviewed
                ),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(
            GmailWorkflowMetric.objects.filter(
                gmail_import=gmail_import,
                event_name="company_approved",
            ).count(),
            1,
        )

    @override_settings(QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True)
    def test_forwarded_only_or_conflicting_suggestion_requires_manual_approval(self):
        gmail_import = self.analyzed_import(anchor="forwarded")
        gmail_import.candidates["ai_identity_unverified_forwarded"] = True
        gmail_import.candidates["companies"][0]["evidence"] = [
            {"signal": "ai_saved_company_name", "source_keys": ["forwarded:1"]}
        ]
        gmail_import.save(update_fields=["candidates", "updated_at"])
        fingerprint = gmail_identity_evidence_fingerprint(gmail_import)

        with self.assertRaises(GmailInquiryImportError):
            approve_gmail_inquiry_company(
                gmail_import,
                self.staff,
                company=self.company,
                suggested=True,
                identity_review_fingerprint=fingerprint,
            )
        gmail_import.refresh_from_db()
        self.assertIsNone(gmail_import.selected_company_id)

        manually_approved = approve_gmail_inquiry_company(
            gmail_import,
            self.staff,
            company=self.company,
            suggested=False,
            identity_review_fingerprint=fingerprint,
        )
        self.assertTrue(gmail_identity_approval_is_current(manually_approved))
        self.assertFalse(manually_approved.analysis["identity_approval"]["suggested"])

        conflicting = self.analyzed_import(anchor="conflicting")
        conflicting.candidates["identity_conflict"] = {"reason": "conflict"}
        conflicting.candidates["recommended_company_id"] = None
        conflicting.save(update_fields=["candidates", "updated_at"])
        with self.assertRaises(GmailInquiryImportError):
            self.approve(conflicting, suggested=True)

        missing = self.analyzed_import(anchor="missing")
        missing.candidates["recommended_company_id"] = None
        missing.save(update_fields=["candidates", "updated_at"])
        with self.assertRaises(GmailInquiryImportError):
            self.approve(missing, suggested=True)

    @override_settings(QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True)
    def test_forwarded_suggestion_needs_independent_physical_corroboration(self):
        gmail_import = self.analyzed_import(anchor="corroborated-forward")
        gmail_import.candidates["ai_identity_unverified_forwarded"] = True
        gmail_import.save(update_fields=["candidates", "updated_at"])

        approved = self.approve(gmail_import, suggested=True)

        self.assertTrue(gmail_identity_approval_is_current(approved))
        self.assertIsNone(approved.selected_contact_id)

    @override_settings(QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True)
    def test_api_rejects_stale_identity_fingerprint_with_409(self):
        gmail_import = self.analyzed_import(anchor="stale")
        stale = gmail_identity_evidence_fingerprint(gmail_import)
        gmail_import.candidates["identity_warnings"] = ["Evidence changed"]
        gmail_import.save(update_fields=["candidates", "updated_at"])

        response = self.client.post(
            reverse(
                "quotation-gmail-inquiry-import-approve-company",
                args=[gmail_import.pk],
            ),
            {
                "company": self.company.pk,
                "contact": None,
                "suggested": True,
                "identity_review_fingerprint": stale,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        gmail_import.refresh_from_db()
        self.assertIsNone(gmail_import.selected_company_id)
        self.assertNotIn("identity_approval", gmail_import.analysis)

    @override_settings(QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True)
    def test_row_only_review_preserves_approval_and_only_marks_submitted_row(self):
        gmail_import = self.approve(self.analyzed_import(anchor="rows"))
        approval = dict(gmail_import.analysis["identity_approval"])
        identity_fingerprint = gmail_identity_evidence_fingerprint(gmail_import)

        updated = update_gmail_inquiry_review_lines(
            gmail_import,
            self.staff,
            review_lines=[
                {
                    "row_key": "a" * 32,
                    "raw_name": "Gloves",
                    "quantity": "10",
                    "unit": "PCS",
                    "included": True,
                    "reviewed": True,
                }
            ],
        )

        rows = updated.analysis["preview"]["lines"]
        self.assertTrue(rows[0]["reviewed_by_user"])
        self.assertFalse(rows[1]["reviewed_by_user"])
        self.assertEqual(updated.analysis["identity_approval"], approval)
        self.assertEqual(
            gmail_identity_evidence_fingerprint(updated),
            identity_fingerprint,
        )
        self.assertTrue(gmail_identity_approval_is_current(updated))

    @override_settings(QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True)
    def test_wording_quantity_unit_and_exclusion_edits_preserve_approval(self):
        gmail_import = self.approve(self.analyzed_import(anchor="row-edits"))
        fingerprint = gmail_identity_evidence_fingerprint(gmail_import)

        corrected = update_gmail_inquiry_review_lines(
            gmail_import,
            self.staff,
            review_lines=[
                {
                    "row_key": "a" * 32,
                    "raw_name": "Sterile Gloves",
                    "quantity": "12",
                    "unit": "BOX",
                    "included": True,
                    "reviewed": True,
                }
            ],
        )
        self.assertEqual(
            gmail_identity_evidence_fingerprint(corrected),
            fingerprint,
        )
        self.assertTrue(gmail_identity_approval_is_current(corrected))

        excluded = update_gmail_inquiry_review_lines(
            corrected,
            self.staff,
            review_lines=[
                {
                    "row_key": "a" * 32,
                    "raw_name": "Sterile Gloves",
                    "quantity": "12",
                    "unit": "BOX",
                    "included": False,
                    "reviewed": True,
                }
            ],
        )
        self.assertEqual(
            gmail_identity_evidence_fingerprint(excluded),
            fingerprint,
        )
        self.assertTrue(gmail_identity_approval_is_current(excluded))

    @override_settings(QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True)
    def test_reincluded_row_is_not_silently_omitted_from_confirmation(self):
        gmail_import = self.approve(
            self.analyzed_import(anchor="reinclude", uncertain=False)
        )
        excluded = update_gmail_inquiry_review_lines(
            gmail_import,
            self.staff,
            review_lines=[
                {
                    "row_key": "a" * 32,
                    "raw_name": "Gloves",
                    "quantity": "10",
                    "unit": "PCS",
                    "included": False,
                    "reviewed": True,
                }
            ],
        )
        reincluded = update_gmail_inquiry_review_lines(
            excluded,
            self.staff,
            review_lines=[
                {
                    "row_key": "a" * 32,
                    "raw_name": "Gloves",
                    "quantity": "10",
                    "unit": "PCS",
                    "included": True,
                    "reviewed": True,
                }
            ],
        )

        self.assertEqual(
            reincluded.analysis["preview"]["lines"][0]["parse_status"],
            "parsed",
        )
        result = confirm_gmail_inquiry_import(
            reincluded,
            self.staff,
            company=self.company,
            contact=None,
            identity_review_fingerprint=gmail_identity_evidence_fingerprint(
                reincluded
            ),
        )
        self.assertEqual(result.quotation.lines.count(), 2)

    @override_settings(QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True)
    def test_identity_or_source_changes_invalidate_approval(self):
        gmail_import = self.approve(self.analyzed_import(anchor="invalidate"))

        same = update_gmail_inquiry_identity(
            gmail_import,
            self.staff,
            company=self.company,
            contact=None,
        )
        self.assertTrue(gmail_identity_approval_is_current(same))
        changed = update_gmail_inquiry_identity(
            same,
            self.staff,
            company=self.other_company,
            contact=None,
        )
        self.assertNotIn("identity_approval", changed.analysis)

        second = self.approve(self.analyzed_import(anchor="selection"))
        selected = update_gmail_inquiry_selection(
            second,
            self.staff,
            selected_message_ids=[second.anchor_message_id],
            mode=GmailInquiryImport.MODE_SELECTED_MESSAGES,
        )
        self.assertEqual(selected.analysis, {})

        contact_approved = self.approve(
            self.analyzed_import(anchor="contact-change"),
            contact=self.contact,
            suggested=False,
        )
        self.assertTrue(gmail_identity_approval_is_current(contact_approved))
        contact_removed = update_gmail_inquiry_identity(
            contact_approved,
            self.staff,
            company=self.company,
            contact=None,
        )
        self.assertNotIn("identity_approval", contact_removed.analysis)

    @override_settings(QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True)
    def test_approval_revalidates_active_company_inside_transaction(self):
        gmail_import = self.analyzed_import(anchor="inactive-company")
        fingerprint = gmail_identity_evidence_fingerprint(gmail_import)
        self.company.is_active = False
        self.company.save(update_fields=["is_active"])

        with self.assertRaises(GmailInquiryImportError):
            approve_gmail_inquiry_company(
                gmail_import,
                self.staff,
                company=self.company,
                contact=None,
                suggested=False,
                identity_review_fingerprint=fingerprint,
            )

        gmail_import.refresh_from_db()
        self.assertIsNone(gmail_import.selected_company_id)
        self.assertNotIn("identity_approval", gmail_import.analysis)

    @override_settings(QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True)
    def test_old_identity_contract_cannot_replay_an_existing_approval(self):
        gmail_import = self.analyzed_import(anchor="identity-version")
        request_fingerprint = gmail_identity_evidence_fingerprint(gmail_import)
        approved = approve_gmail_inquiry_company(
            gmail_import,
            self.staff,
            company=self.company,
            contact=None,
            suggested=True,
            identity_review_fingerprint=request_fingerprint,
        )
        approved.candidates["identity_match_version"] = "gmail_identity_old"
        approved.save(update_fields=["candidates", "updated_at"])

        with self.assertRaises(GmailInquiryImportError):
            approve_gmail_inquiry_company(
                approved,
                self.staff,
                company=self.company,
                contact=None,
                suggested=True,
                identity_review_fingerprint=request_fingerprint,
            )

        projection = gmail_identity_review_projection(approved)
        self.assertFalse(projection["approved"])
        self.assertFalse(projection["suggestion_approvable"])

    @override_settings(QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True)
    @patch("quotations.gmail_inquiry_import._fetch_analysis_messages")
    @patch("quotations.gmail_inquiry_import._connected_mailbox_for_import")
    def test_failed_reanalysis_clears_prior_approval(
        self,
        mock_connection,
        mock_fetch,
    ):
        gmail_import = self.approve(self.analyzed_import(anchor="reanalyze"))
        mock_connection.return_value = self.connection
        mock_fetch.side_effect = GmailInquiryImportError("safe failure")

        with self.assertRaises(GmailInquiryImportError):
            analyze_gmail_inquiry_import(
                gmail_import,
                self.staff,
                force=True,
                reanalyze=True,
            )

        gmail_import.refresh_from_db()
        self.assertNotIn("identity_approval", gmail_import.analysis)

    @override_settings(QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True)
    def test_confirmation_requires_current_approval_and_keeps_selling_prices_blank(self):
        gmail_import = self.analyzed_import(anchor="confirm", uncertain=False)
        with self.assertRaises(GmailInquiryImportError):
            confirm_gmail_inquiry_import(
                gmail_import,
                self.staff,
                company=self.company,
            )

        approved = self.approve(gmail_import)
        confirm_payload = {
            "company": self.company.pk,
            "contact": None,
            "identity_review_fingerprint": gmail_identity_evidence_fingerprint(
                approved
            ),
        }
        confirm_url = reverse(
            "quotation-gmail-inquiry-import-confirm",
            args=[approved.pk],
        )
        response = self.client.post(confirm_url, confirm_payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        approved.refresh_from_db()
        quotation = approved.quotation
        self.assertTrue(
            all(line.unit_price is None for line in quotation.lines.all())
        )
        repeated = self.client.post(confirm_url, confirm_payload, format="json")
        self.assertEqual(repeated.status_code, status.HTTP_200_OK, repeated.data)
        self.assertEqual(repeated.data["quotation_id"], quotation.pk)

    @override_settings(QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True)
    def test_confirm_rejects_a_stale_identity_fingerprint_before_creation(self):
        gmail_import = self.approve(
            self.analyzed_import(anchor="stale-confirm", uncertain=False)
        )
        stale = gmail_identity_evidence_fingerprint(gmail_import)
        gmail_import.candidates["identity_warnings"] = ["Evidence changed"]
        gmail_import.save(update_fields=["candidates", "updated_at"])

        response = self.client.post(
            reverse(
                "quotation-gmail-inquiry-import-confirm",
                args=[gmail_import.pk],
            ),
            {
                "company": self.company.pk,
                "contact": None,
                "identity_review_fingerprint": stale,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        gmail_import.refresh_from_db()
        self.assertIsNone(gmail_import.inquiry_id)
        self.assertIsNone(gmail_import.quotation_id)

    @override_settings(QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=False)
    def test_flag_off_preserves_legacy_confirmation_and_row_payload_contract(self):
        gmail_import = self.analyzed_import(anchor="legacy", uncertain=False)
        result = confirm_gmail_inquiry_import(
            gmail_import,
            self.staff,
            company=self.company,
        )
        self.assertTrue(result.created)
        serializer = GmailInquiryReviewLineUpdateSerializer(
            data={
                "row_key": "a" * 32,
                "raw_name": "Gloves",
                "quantity": "10",
                "unit": "PCS",
                "included": True,
                "reviewed": True,
            }
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("reviewed", serializer.errors)

    @override_settings(QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=False)
    def test_flag_off_preserves_legacy_inactive_instance_identity_update(self):
        gmail_import = self.analyzed_import(anchor="legacy-inactive")
        gmail_import.selected_company = self.company
        gmail_import.selected_contact = self.contact
        gmail_import.save(
            update_fields=["selected_company", "selected_contact", "updated_at"]
        )
        self.company.is_active = False
        self.company.save(update_fields=["is_active"])
        self.contact.is_active = False
        self.contact.save(update_fields=["is_active"])

        updated = update_gmail_inquiry_identity(
            gmail_import,
            self.staff,
            company=self.company,
            contact=None,
        )

        self.assertEqual(updated.selected_company_id, self.company.pk)
        self.assertIsNone(updated.selected_contact_id)

    @override_settings(QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True)
    def test_serializer_exposes_strict_server_feature_and_safe_review_projection(self):
        gmail_import = self.analyzed_import(anchor="projection")
        payload = GmailInquiryImportSerializer(gmail_import).data

        self.assertIs(payload["workflow_features"]["gmail_review_ui_v2"], True)
        self.assertFalse(payload["identity_review_approved"])
        self.assertRegex(payload["identity_review_fingerprint"], r"^[0-9a-f]{64}$")
        self.assertTrue(payload["identity_review"]["suggestion_approvable"])
        self.assertNotIn("buyer@verified-customer.example", str(payload["identity_review"]))
