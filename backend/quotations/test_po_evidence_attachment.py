import base64
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .contract_intelligence import gmail_fetch_attachment_content
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
    def test_cannot_request_an_attachment_outside_the_evidence_manifest(self, mock_fetch):
        self.client.force_authenticate(self.staff)

        response = self.client.get(self.url, {"attachment_id": "another-message-attachment"})

        self.assertEqual(response.status_code, 404)
        mock_fetch.assert_not_called()

    def test_anonymous_user_cannot_read_evidence_attachments(self):
        response = self.client.get(self.url, {"attachment_id": "gmail-att-1"})

        self.assertIn(response.status_code, {401, 403})
