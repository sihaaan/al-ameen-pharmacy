import json
from urllib.parse import parse_qs, urlsplit
from unittest.mock import ANY, patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from . import gmail_addon
from .models import GmailOAuthConnection


CONTEXTUAL_URL = "https://api.example.com/api/quotations/gmail/addon/contextual/"
ACTION_URL = "https://api.example.com/api/quotations/gmail/addon/action/"
HANDOFF_URL = (
    "https://app.example.com/admin"
    "?admin_tab=quotations&quotation_tab=inquiries"
)
OAUTH_CLIENT_ID = "workspace-addon.apps.googleusercontent.com"
SERVICE_ACCOUNT_EMAIL = "addon-sa@example-project.iam.gserviceaccount.com"
MAILBOX_EMAIL = "quotes@example.com"

ADDON_SETTINGS = {
    "GMAIL_ADDON_ENABLED": True,
    "GMAIL_ADDON_SERVICE_ACCOUNT_EMAIL": SERVICE_ACCOUNT_EMAIL,
    "GMAIL_ADDON_SHARED_MAILBOX_EMAIL": MAILBOX_EMAIL,
    "GMAIL_ADDON_OAUTH_CLIENT_ID": OAUTH_CLIENT_ID,
    "GMAIL_ADDON_ALLOWED_AUDIENCES": [CONTEXTUAL_URL, ACTION_URL],
    "GMAIL_ADDON_CONTEXTUAL_URL": CONTEXTUAL_URL,
    "GMAIL_ADDON_ACTION_URL": ACTION_URL,
    "GMAIL_ADDON_HANDOFF_URL": HANDOFF_URL,
    "GMAIL_ADDON_HANDOFF_TTL_SECONDS": 1800,
    "GMAIL_ADDON_MAX_THREAD_MESSAGES": 50,
}


class GoogleIdTokenVerificationTests(SimpleTestCase):
    @patch("quotations.gmail_addon.jwt.decode")
    @patch("quotations.gmail_addon._google_jwks_client")
    def test_google_token_verification_pins_algorithm_and_exact_audience(
        self,
        mock_jwks_client,
        mock_decode,
    ):
        mock_jwks_client.return_value.get_signing_key_from_jwt.return_value.key = (
            "public-key"
        )
        mock_decode.return_value = {
            "iss": "https://accounts.google.com",
            "sub": "google-subject",
        }

        claims = gmail_addon._verify_google_id_token(
            "signed-token",
            audiences=[CONTEXTUAL_URL],
        )

        self.assertEqual(claims["sub"], "google-subject")
        mock_jwks_client.return_value.get_signing_key_from_jwt.assert_called_once_with(
            "signed-token"
        )
        mock_decode.assert_called_once_with(
            "signed-token",
            "public-key",
            algorithms=["RS256"],
            audience=[CONTEXTUAL_URL],
            leeway=60,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )

    @patch("quotations.gmail_addon.jwt.decode")
    @patch("quotations.gmail_addon._google_jwks_client")
    def test_google_token_verification_rejects_non_google_issuer(
        self,
        mock_jwks_client,
        mock_decode,
    ):
        mock_jwks_client.return_value.get_signing_key_from_jwt.return_value.key = (
            "public-key"
        )
        mock_decode.return_value = {
            "iss": "https://attacker.example",
            "sub": "google-subject",
        }

        with self.assertRaises(gmail_addon.GmailAddonAuthenticationError):
            gmail_addon._verify_google_id_token(
                "signed-token",
                audiences=[CONTEXTUAL_URL],
            )


@override_settings(**ADDON_SETTINGS)
class GmailAddonEndpointTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="gmail_addon_owner",
            password="unused",
            is_staff=True,
        )
        self.connection = GmailOAuthConnection.objects.create(
            user=self.owner,
            is_shared=True,
            email=MAILBOX_EMAIL,
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )
        self.system_email = SERVICE_ACCOUNT_EMAIL
        self.user_email = MAILBOX_EMAIL
        self.verified_tokens = []
        self.token_patcher = patch(
            "quotations.gmail_addon._verify_google_id_token",
            side_effect=self._verified_claims,
        )
        self.mock_verify_token = self.token_patcher.start()
        self.addCleanup(self.token_patcher.stop)

    def _verified_claims(self, token, *, audiences):
        self.verified_tokens.append((token, list(audiences)))
        if token == "invalid-token":
            raise gmail_addon.GmailAddonAuthenticationError()
        if token == "system-token":
            email = self.system_email
            subject = "system-subject"
        elif token == "user-token":
            email = self.user_email
            subject = "user-subject"
        else:
            raise gmail_addon.GmailAddonAuthenticationError()
        return {
            "iss": "https://accounts.google.com",
            "sub": subject,
            "email": email,
            "email_verified": True,
        }

    def _event(
        self,
        *,
        message_id="msg-f:current",
        thread_id="thread-f:one",
        mode=None,
        selected=None,
        system_token="system-token",
        user_token="user-token",
    ):
        common = {
            "hostApp": "GMAIL",
            "platform": "WEB",
        }
        if mode is not None:
            common["parameters"] = {"selection_mode": mode}
        if selected is not None:
            common["formInputs"] = {
                "message_ids": {
                    "stringInputs": {
                        "value": selected,
                    }
                }
            }
        return {
            "authorizationEventObject": {
                "systemIdToken": system_token,
                "userIdToken": user_token,
            },
            "commonEventObject": common,
            "gmail": {
                "messageId": message_id,
                "threadId": thread_id,
            },
        }

    def _post(self, url_name, event, *, bearer="system-token"):
        return self.client.post(
            reverse(url_name),
            data=json.dumps(event),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {bearer}",
        )

    @override_settings(GMAIL_ADDON_ENABLED=False)
    def test_disabled_feature_returns_not_found_before_token_verification(self):
        response = self._post(
            "quotation-gmail-addon-contextual",
            self._event(),
        )

        self.assertEqual(response.status_code, 404)
        self.mock_verify_token.assert_not_called()

    @patch("quotations.gmail_addon._fetch_thread_message_summaries")
    def test_contextual_card_has_thread_selection_and_three_import_actions(
        self,
        mock_fetch_summaries,
    ):
        mock_fetch_summaries.return_value = [
            {
                "message_id": "msg-f:older",
                "label": (
                    "28 Jul 2026 | Buyer | RFQ | Please quote pulse oximeters "
                    "| Attachments: inquiry.pdf"
                ),
            },
            {
                "message_id": "msg-f:current",
                "label": "29 Jul 2026 | Buyer | Updated RFQ | Quantity changed to 12",
            },
        ]

        response = self._post(
            "quotation-gmail-addon-contextual",
            self._event(),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        card = payload["renderActions"]["action"]["navigations"][0]["pushCard"]
        widgets = card["sections"][0]["widgets"]
        selection = widgets[1]["selectionInput"]
        self.assertEqual(selection["name"], "message_ids")
        self.assertEqual(selection["type"], "CHECK_BOX")
        self.assertEqual(
            [item["value"] for item in selection["items"]],
            ["msg-f:older", "msg-f:current"],
        )
        self.assertFalse(selection["items"][0]["selected"])
        self.assertTrue(selection["items"][1]["selected"])
        self.assertIn("Attachments: inquiry.pdf", selection["items"][0]["text"])

        buttons = widgets[2]["buttonList"]["buttons"]
        self.assertEqual(
            [button["text"] for button in buttons],
            ["Let AI choose", "Import selected", "Current only"],
        )
        self.assertEqual(
            [
                button["onClick"]["action"]["parameters"][0]["value"]
                for button in buttons
            ],
            ["ai_thread", "selected_messages", "current_message"],
        )
        self.assertTrue(
            all(
                button["onClick"]["action"]["function"] == ACTION_URL
                for button in buttons
            )
        )
        mock_fetch_summaries.assert_called_once_with(
            self.connection,
            "thread-f:one",
        )
        self.assertEqual(
            self.verified_tokens,
            [
                ("system-token", [CONTEXTUAL_URL]),
                ("user-token", [OAUTH_CLIENT_ID]),
            ],
        )

    @patch("quotations.gmail_addon._json_request")
    @patch("quotations.gmail_addon.get_valid_access_token", return_value="stored-token")
    def test_thread_summary_uses_preview_and_attachment_names_without_bytes(
        self,
        _mock_access_token,
        mock_json_request,
    ):
        mock_json_request.return_value = {
            "messages": [
                {
                    "id": "msg-f:preview",
                    "internalDate": "1785283200000",
                    "snippet": "Please quote twelve first aid kits by tomorrow.",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "Buyer <buyer@example.com>"},
                            {"name": "Subject", "value": "Urgent medical RFQ"},
                        ],
                        "parts": [
                            {
                                "filename": "first-aid-inquiry.pdf",
                                "body": {"attachmentId": "attachment-secret"},
                            },
                            {
                                "filename": "product-photo.png",
                                "body": {"attachmentId": "attachment-secret-2"},
                            },
                        ],
                    },
                }
            ]
        }

        summaries = gmail_addon._fetch_thread_message_summaries(
            self.connection,
            "thread-f:preview",
        )

        self.assertEqual(summaries[0]["message_id"], "msg-f:preview")
        self.assertIn(
            "Please quote twelve first aid kits by tomorrow.",
            summaries[0]["label"],
        )
        self.assertIn("Attachments: first-aid-inquiry.pdf, product-photo.png", summaries[0]["label"])
        request_url = mock_json_request.call_args.args[0]
        self.assertIn("thread-f%3Apreview", request_url)
        self.assertIn("format=full", request_url)
        self.assertIn("fields=", request_url)
        self.assertNotIn("attachment-secret", json.dumps(summaries))
        mock_json_request.assert_called_once_with(
            ANY,
            token="stored-token",
            timeout=10,
        )

    @patch("quotations.gmail_addon._fetch_thread_message_summaries")
    def test_header_and_event_system_tokens_must_match(
        self,
        mock_fetch_summaries,
    ):
        response = self._post(
            "quotation-gmail-addon-contextual",
            self._event(system_token="invalid-token"),
            bearer="system-token",
        )

        self.assertEqual(response.status_code, 401)
        self.mock_verify_token.assert_not_called()
        mock_fetch_summaries.assert_not_called()

    @patch("quotations.gmail_addon._fetch_thread_message_summaries")
    def test_invalid_system_token_is_rejected(self, mock_fetch_summaries):
        response = self._post(
            "quotation-gmail-addon-contextual",
            self._event(system_token="invalid-token"),
            bearer="invalid-token",
        )

        self.assertEqual(response.status_code, 401)
        mock_fetch_summaries.assert_not_called()

    @patch("quotations.gmail_addon._fetch_thread_message_summaries")
    def test_unexpected_system_service_account_is_rejected(
        self,
        mock_fetch_summaries,
    ):
        self.system_email = "wrong-service-account@example.iam.gserviceaccount.com"

        response = self._post(
            "quotation-gmail-addon-contextual",
            self._event(),
        )

        self.assertEqual(response.status_code, 401)
        mock_fetch_summaries.assert_not_called()

    @patch("quotations.gmail_addon._fetch_thread_message_summaries")
    def test_other_gmail_user_is_forbidden(self, mock_fetch_summaries):
        self.user_email = "someone-else@example.com"

        response = self._post(
            "quotation-gmail-addon-contextual",
            self._event(),
        )

        self.assertEqual(response.status_code, 403)
        mock_fetch_summaries.assert_not_called()

    @patch("quotations.gmail_addon._fetch_thread_message_summaries")
    def test_connected_mailbox_must_match_configured_mailbox(
        self,
        mock_fetch_summaries,
    ):
        self.connection.email = "different-mailbox@example.com"
        self.connection.save(update_fields=["email"])

        response = self._post(
            "quotation-gmail-addon-contextual",
            self._event(),
        )

        self.assertEqual(response.status_code, 503)
        self.assertNotIn("different-mailbox", response.content.decode("utf-8"))
        mock_fetch_summaries.assert_not_called()

    @patch("quotations.gmail_addon._issue_handoff")
    @patch("quotations.gmail_addon._fetch_thread_message_summaries")
    def test_selected_action_issues_handoff_and_opens_only_opaque_url(
        self,
        mock_fetch_summaries,
        mock_issue_handoff,
    ):
        mock_fetch_summaries.return_value = [
            {"message_id": "msg-f:older", "label": "Older"},
            {"message_id": "msg-f:current", "label": "Current"},
        ]
        mock_issue_handoff.return_value = (object(), "opaque-handoff-token")

        response = self._post(
            "quotation-gmail-addon-action",
            self._event(
                mode="selected_messages",
                selected=["msg-f:older", "msg-f:current", "msg-f:older"],
            ),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        link = payload["renderActions"]["action"]["link"]
        self.assertEqual(link["openAs"], "FULL_SIZE")
        self.assertEqual(link["onClose"], "NOTHING")
        parsed_link = urlsplit(link["url"])
        self.assertEqual(parsed_link.scheme, "https")
        self.assertEqual(parsed_link.netloc, "app.example.com")
        query = parse_qs(parsed_link.query)
        self.assertEqual(query["admin_tab"], ["quotations"])
        self.assertEqual(query["quotation_tab"], ["inquiries"])
        self.assertEqual(query["gmail_import"], ["opaque-handoff-token"])
        self.assertNotIn("msg-f", link["url"])
        self.assertNotIn("thread-f", link["url"])
        self.assertNotIn("system-token", link["url"])
        self.assertNotIn("user-token", link["url"])
        mock_issue_handoff.assert_called_once_with(
            self.connection,
            anchor_message_id="msg-f:current",
            gmail_thread_id="thread-f:one",
            mode="selected_messages",
            selected_message_ids=["msg-f:older", "msg-f:current"],
            ttl_seconds=1800,
        )
        self.assertEqual(
            self.verified_tokens,
            [
                ("system-token", [ACTION_URL]),
                ("user-token", [OAUTH_CLIENT_ID]),
            ],
        )

    @patch("quotations.gmail_addon._issue_handoff")
    @patch("quotations.gmail_addon._fetch_thread_message_summaries")
    def test_selected_ids_must_belong_to_authenticated_thread(
        self,
        mock_fetch_summaries,
        mock_issue_handoff,
    ):
        mock_fetch_summaries.return_value = [
            {"message_id": "msg-f:current", "label": "Current"},
        ]

        response = self._post(
            "quotation-gmail-addon-action",
            self._event(
                mode="selected_messages",
                selected=["msg-f:forged"],
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "thread changed",
            response.json()["renderActions"]["action"]["notification"]["text"].lower(),
        )
        mock_issue_handoff.assert_not_called()

    @patch("quotations.gmail_addon._issue_handoff")
    def test_selected_action_requires_at_least_one_checkbox(
        self,
        mock_issue_handoff,
    ):
        response = self._post(
            "quotation-gmail-addon-action",
            self._event(mode="selected_messages", selected=[]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "select at least one",
            response.json()["renderActions"]["action"]["notification"]["text"].lower(),
        )
        mock_issue_handoff.assert_not_called()

    @patch("quotations.gmail_addon._issue_handoff")
    def test_selected_action_enforces_core_message_limit(
        self,
        mock_issue_handoff,
    ):
        response = self._post(
            "quotation-gmail-addon-action",
            self._event(
                mode="selected_messages",
                selected=[f"msg-f:{index}" for index in range(26)],
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "no more than 25",
            response.json()["renderActions"]["action"]["notification"]["text"].lower(),
        )
        mock_issue_handoff.assert_not_called()

    @patch("quotations.gmail_addon._fetch_thread_message_summaries")
    @patch("quotations.gmail_addon._issue_handoff")
    def test_current_and_ai_modes_map_to_core_service_without_callback_ai(
        self,
        mock_issue_handoff,
        mock_fetch_summaries,
    ):
        mock_issue_handoff.side_effect = [
            (object(), "current-handoff"),
            (object(), "ai-handoff"),
        ]

        current_response = self._post(
            "quotation-gmail-addon-action",
            self._event(mode="current_message"),
        )
        ai_response = self._post(
            "quotation-gmail-addon-action",
            self._event(mode="ai_thread"),
        )

        self.assertEqual(current_response.status_code, 200)
        self.assertEqual(ai_response.status_code, 200)
        self.assertEqual(mock_issue_handoff.call_count, 2)
        self.assertEqual(
            mock_issue_handoff.call_args_list[0].kwargs,
            {
                "anchor_message_id": "msg-f:current",
                "gmail_thread_id": "thread-f:one",
                "mode": "current_message",
                "selected_message_ids": [],
                "ttl_seconds": 1800,
            },
        )
        self.assertEqual(
            mock_issue_handoff.call_args_list[1].kwargs,
            {
                "anchor_message_id": "msg-f:current",
                "gmail_thread_id": "thread-f:one",
                "mode": "ai_thread",
                "selected_message_ids": [],
                "ttl_seconds": 1800,
            },
        )
        mock_fetch_summaries.assert_not_called()

    @patch(
        "quotations.gmail_addon._issue_handoff",
        side_effect=RuntimeError("database secret msg-f:current system-token"),
    )
    def test_unexpected_handoff_failure_is_sanitized(self, _mock_issue_handoff):
        response = self._post(
            "quotation-gmail-addon-action",
            self._event(mode="current_message"),
        )

        self.assertEqual(response.status_code, 503)
        response_text = response.content.decode("utf-8")
        self.assertIn("temporarily unavailable", response_text)
        self.assertNotIn("database secret", response_text)
        self.assertNotIn("msg-f:current", response_text)
        self.assertNotIn("system-token", response_text)

    @patch("quotations.gmail_addon._fetch_thread_message_summaries")
    def test_ids_are_opaque_but_reject_path_or_whitespace_characters(
        self,
        mock_fetch_summaries,
    ):
        colon_response = self._post(
            "quotation-gmail-addon-contextual",
            self._event(
                message_id="msg-f:1234567",
                thread_id="thread-f:2345678",
            ),
        )
        slash_response = self._post(
            "quotation-gmail-addon-contextual",
            self._event(thread_id="../other-thread"),
        )
        whitespace_response = self._post(
            "quotation-gmail-addon-contextual",
            self._event(message_id="message id"),
        )
        surrounding_whitespace_response = self._post(
            "quotation-gmail-addon-contextual",
            self._event(message_id=" msg-f:current"),
        )

        self.assertEqual(colon_response.status_code, 200)
        self.assertEqual(slash_response.status_code, 400)
        self.assertEqual(whitespace_response.status_code, 400)
        self.assertEqual(surrounding_whitespace_response.status_code, 400)
        mock_fetch_summaries.assert_called_once_with(
            self.connection,
            "thread-f:2345678",
        )

    @override_settings(
        GMAIL_ADDON_ALLOWED_AUDIENCES=[CONTEXTUAL_URL],
    )
    def test_both_exact_endpoint_audiences_are_required_in_configuration(self):
        response = self._post(
            "quotation-gmail-addon-contextual",
            self._event(),
        )

        self.assertEqual(response.status_code, 503)
        self.mock_verify_token.assert_not_called()
