import json

from django.contrib.auth.models import User
from django.test import TestCase

from .models import (
    Company,
    CompanyContact,
    GmailInquiryImport,
    Inquiry,
    Quotation,
)
from .serializers import QuotationListSerializer, QuotationSerializer


class QuotationGmailSourceSerializerTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="gmail-source-staff",
            is_staff=True,
        )
        self.company = Company.objects.create(name="Confirmed Customer")
        self.contact = CompanyContact.objects.create(
            company=self.company,
            name="Confirmed Purchaser",
        )
        self.inquiry = Inquiry.objects.create(
            company=self.company,
            contact=self.contact,
            source=Inquiry.SOURCE_IMPORTED,
            source_type=Inquiry.SOURCE_TYPE_GMAIL,
            subject="Medical supplies RFQ",
            created_by=self.staff,
        )
        self.quotation = Quotation.objects.create(
            company=self.company,
            contact=self.contact,
            inquiry=self.inquiry,
            created_by=self.staff,
        )
        self.gmail_import = GmailInquiryImport.objects.create(
            mailbox_email="shared-mailbox@example.com",
            gmail_thread_id="private-thread-id",
            anchor_message_id="private-message-id",
            selected_message_ids=["private-message-id"],
            status=GmailInquiryImport.STATUS_CONFIRMED,
            selected_company=self.company,
            selected_contact=self.contact,
            inquiry=self.inquiry,
            quotation=self.quotation,
        )

    def test_detail_exposes_only_internal_confirmed_source_summary(self):
        payload = QuotationSerializer(self.quotation).data

        self.assertEqual(
            payload["gmail_source"],
            {
                "import_id": self.gmail_import.pk,
                "inquiry_subject": "Medical supplies RFQ",
                "confirmed_company_name": "Confirmed Customer",
                "confirmed_contact_name": "Confirmed Purchaser",
            },
        )
        serialized = json.dumps(payload["gmail_source"])
        self.assertNotIn("shared-mailbox@example.com", serialized)
        self.assertNotIn("private-thread-id", serialized)
        self.assertNotIn("private-message-id", serialized)

    def test_detail_returns_none_without_a_confirmed_linked_import(self):
        self.gmail_import.status = GmailInquiryImport.STATUS_REVIEW_REQUIRED
        self.gmail_import.save(update_fields=["status", "updated_at"])

        self.assertIsNone(QuotationSerializer(self.quotation).data["gmail_source"])

    def test_list_payload_does_not_add_source_queries_or_private_summary(self):
        self.quotation.po_evidence_count = 0
        self.quotation.po_evidence_candidate_count = 0
        self.quotation.po_evidence_ambiguous_count = 0
        self.quotation.po_evidence_parsed_count = 0
        with self.assertNumQueries(0):
            payload = QuotationListSerializer(self.quotation).data

        self.assertNotIn("gmail_source", payload)
