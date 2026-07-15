import base64
from datetime import timedelta
from email.message import EmailMessage
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .contract_intelligence import extract_nested_email_document, gmail_fetch_attachment_content
from .models import Company, GmailOAuthConnection, Quotation, QuotationPOEvidence


def gmail_data(value):
    raw = value if isinstance(value, bytes) else str(value).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class GmailAttachmentContentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("mail-owner", is_staff=True)
        self.connection = GmailOAuthConnection.objects.create(
            user=self.user,
            email="orders@example.com",
            is_shared=True,
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )

    @patch("quotations.contract_intelligence.get_valid_access_token", return_value="token")
    @patch("quotations.contract_intelligence._json_request")
    def test_fetches_the_exact_manifest_attachment_bytes(self, mock_json, _mock_token):
        mock_json.side_effect = [
            {
                "id": "message-1",
                "payload": {
                    "mimeType": "multipart/mixed",
                    "parts": [
                        {
                            "partId": "1",
                            "filename": "customer-lpo.pdf",
                            "mimeType": "application/pdf",
                            "body": {"attachmentId": "gmail-att-1", "size": 13},
                        }
                    ],
                },
            },
            {"data": gmail_data(b"%PDF-source")},
        ]

        result = gmail_fetch_attachment_content(
            self.connection,
            "message-1",
            attachment_id="gmail-att-1",
        )

        self.assertEqual(result["content"], b"%PDF-source")
        self.assertEqual(result["filename"], "customer-lpo.pdf")
        self.assertEqual(result["mime_type"], "application/pdf")
        self.assertIn("/attachments/gmail-att-1", mock_json.call_args_list[1].args[0])

    @patch("quotations.contract_intelligence.get_valid_access_token", return_value="token")
    @patch("quotations.contract_intelligence._json_request")
    def test_rejects_a_part_that_is_not_in_the_gmail_message(self, mock_json, _mock_token):
        mock_json.return_value = {"id": "message-1", "payload": {"parts": []}}

        with self.assertRaisesMessage(ValueError, "no longer present"):
            gmail_fetch_attachment_content(self.connection, "message-1", attachment_id="wrong")

    @patch("quotations.contract_intelligence.get_valid_access_token", return_value="token")
    @patch("quotations.contract_intelligence._json_request")
    def test_fetches_exact_nested_pdf_from_attached_email_without_returning_wrapper(self, mock_json, _mock_token):
        nested_pdf = b"%PDF-1.4\nreal nested purchase order"
        attached_email = EmailMessage()
        attached_email["Subject"] = "Implemented purchase order"
        attached_email.set_content("The implemented PO is attached.")
        attached_email.add_attachment(
            nested_pdf,
            maintype="application",
            subtype="octet-stream",
            filename="PO_PO26IMD32175_0.pdf",
        )
        wrapper = attached_email.as_bytes()
        mock_json.side_effect = [
            {
                "id": "message-1",
                "payload": {
                    "mimeType": "multipart/mixed",
                    "parts": [
                        {
                            "partId": "2",
                            "filename": "Implemented Purchase Order.eml",
                            "mimeType": "message/rfc822",
                            "body": {"attachmentId": "gmail-eml-1", "size": len(wrapper)},
                        }
                    ],
                },
            },
            {"data": gmail_data(wrapper)},
        ]

        result = gmail_fetch_attachment_content(
            self.connection,
            "message-1",
            attachment_id="gmail-eml-1",
            nested_filename="PO_PO26IMD32175_0.pdf",
        )

        self.assertEqual(result["content"], nested_pdf)
        self.assertEqual(result["filename"], "PO_PO26IMD32175_0.pdf")
        self.assertEqual(result["mime_type"], "application/pdf")
        self.assertEqual(result["container_filename"], "Implemented Purchase Order.eml")
        self.assertEqual(result["attachment_id"], "gmail-eml-1")
        self.assertEqual(result["part_id"], "2")

    def test_attached_email_selects_unique_order_document_among_supporting_files(self):
        attached_email = EmailMessage()
        attached_email.set_content("Please see the attached documents.")
        attached_email.add_attachment(
            b"%PDF-1.4\napproved product datasheet",
            maintype="application",
            subtype="pdf",
            filename="Approved Datasheet.pdf",
        )
        order_pdf = b"%PDF-1.4\nreal purchase order"
        attached_email.add_attachment(
            order_pdf,
            maintype="application",
            subtype="pdf",
            filename="PO_PO26IMD32175_0.pdf",
        )

        result = extract_nested_email_document(attached_email.as_bytes())

        self.assertEqual(result["filename"], "PO_PO26IMD32175_0.pdf")
        self.assertEqual(result["content"], order_pdf)

    def test_attached_email_with_multiple_order_documents_fails_closed(self):
        attached_email = EmailMessage()
        attached_email.set_content("Two orders are attached.")
        for filename in ("PO-1001.pdf", "LPO-1002.pdf"):
            attached_email.add_attachment(
                b"%PDF-1.4\norder",
                maintype="application",
                subtype="pdf",
                filename=filename,
            )

        with self.assertRaisesMessage(
            ValueError,
            "multiple supported documents",
        ):
            extract_nested_email_document(attached_email.as_bytes())

    def test_attached_email_recognizes_an_mpo_named_document(self):
        attached_email = EmailMessage()
        attached_email.set_content("The material purchase order is attached.")
        mpo_pdf = b"%PDF-1.4\nmaterial purchase order"
        attached_email.add_attachment(
            mpo_pdf,
            maintype="application",
            subtype="pdf",
            filename="MPO-294676.pdf",
        )
        attached_email.add_attachment(
            b"%PDF-1.4\nsupporting quotation",
            maintype="application",
            subtype="pdf",
            filename="Quotation.pdf",
        )

        result = extract_nested_email_document(attached_email.as_bytes())

        self.assertEqual(result["filename"], "MPO-294676.pdf")
        self.assertEqual(result["content"], mpo_pdf)


class QuotationPOEvidenceAttachmentAPITests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("reviewer", password="test", is_staff=True)
        self.connection = GmailOAuthConnection.objects.create(
            user=self.staff,
            email="orders@example.com",
            is_shared=True,
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )
        company = Company.objects.create(name="Attachment Customer")
        quote = Quotation.objects.create(
            company=company,
            quotation_number="QT-20260715-0001",
            status=Quotation.STATUS_SENT,
            sent_at=timezone.now() - timedelta(hours=1),
            created_by=self.staff,
        )
        self.evidence = QuotationPOEvidence.objects.create(
            quotation=quote,
            gmail_connection=self.connection,
            gmail_message_id="message-1",
            subject="LPO attached",
            attachments=[
                {
                    "attachment_id": "gmail-att-1",
                    "part_id": "1",
                    "filename": "customer-lpo.pdf",
                    "mime_type": "application/pdf",
                    "size": 13,
                }
            ],
            created_by=self.staff,
        )
        self.url = reverse("quotation-po-evidence-attachment", args=[self.evidence.pk])
        self.client = APIClient()

    @patch("quotations.views.gmail_fetch_attachment_content")
    def test_staff_can_open_the_exact_source_attachment_inline(self, mock_fetch):
        self.client.force_authenticate(self.staff)
        mock_fetch.return_value = {
            "filename": "customer-lpo.pdf",
            "mime_type": "application/pdf",
            "size": 13,
            "content": b"%PDF-source",
        }

        response = self.client.get(self.url, {"attachment_id": "gmail-att-1"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"%PDF-source")
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("inline", response["Content-Disposition"])
        self.assertEqual(response["Cache-Control"], "private, no-store, max-age=0")
        mock_fetch.assert_called_once_with(
            self.connection,
            "message-1",
            attachment_id="gmail-att-1",
            part_id="1",
            max_bytes=20 * 1024 * 1024,
        )

    @patch("quotations.views.gmail_fetch_attachment_content")
    def test_staff_opens_nested_pdf_bytes_instead_of_attached_email_wrapper(self, mock_fetch):
        self.client.force_authenticate(self.staff)
        self.evidence.attachments = [
            {
                "attachment_id": "gmail-eml-1",
                "part_id": "2",
                "filename": "PO_PO26IMD32175_0.pdf",
                "mime_type": "application/pdf",
                "container_filename": "Implemented Purchase Order.eml",
                "container_mime_type": "message/rfc822",
                "nested_filename": "PO_PO26IMD32175_0.pdf",
                "nested_mime_type": "application/pdf",
                "size": 32,
            }
        ]
        self.evidence.save(update_fields=["attachments", "updated_at"])
        mock_fetch.return_value = {
            "filename": "PO_PO26IMD32175_0.pdf",
            "mime_type": "application/pdf",
            "size": 32,
            "content": b"%PDF-1.4\nnested purchase order",
        }

        response = self.client.get(self.url, {"attachment_id": "gmail-eml-1"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("PO_PO26IMD32175_0.pdf", response["Content-Disposition"])
        mock_fetch.assert_called_once_with(
            self.connection,
            "message-1",
            attachment_id="gmail-eml-1",
            part_id="2",
            max_bytes=20 * 1024 * 1024,
            nested_filename="PO_PO26IMD32175_0.pdf",
        )

    @patch("quotations.views.gmail_fetch_attachment_content")
    def test_cannot_request_an_attachment_outside_the_evidence_manifest(self, mock_fetch):
        self.client.force_authenticate(self.staff)

        response = self.client.get(self.url, {"attachment_id": "another-message-attachment"})

        self.assertEqual(response.status_code, 404)
        mock_fetch.assert_not_called()

    def test_anonymous_user_cannot_read_evidence_attachments(self):
        response = self.client.get(self.url, {"attachment_id": "gmail-att-1"})

        self.assertIn(response.status_code, {401, 403})
