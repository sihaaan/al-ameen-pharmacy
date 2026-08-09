import json
import urllib.error
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from unittest.mock import ANY, patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import gmail_addon
from .contract_intelligence import encrypt_token
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
CANONICAL_MESSAGE_ID = "19fb2da13e1adcfa"
CANONICAL_THREAD_ID = "19fb2da13e1adcfb"
REQUIRED_SCOPES = list(gmail_addon.REQUIRED_GOOGLE_OAUTH_SCOPES)


def google_http_runtime_error(status_code, detail="provider failure"):
    provider_error = urllib.error.HTTPError(
        "https://gmail.googleapis.test/resource",
        status_code,
        "provider error",
        {},
        None,
    )
    error = RuntimeError(detail)
    error.__cause__ = provider_error
    return error


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
            access_token_encrypted=encrypt_token("stored-access-token"),
            token_expiry=timezone.now() + timedelta(hours=1),
        )
        self.system_email = SERVICE_ACCOUNT_EMAIL
        self.user_email = MAILBOX_EMAIL
        self.verified_tokens = []
        self.real_fetch_message_identity = gmail_addon._fetch_message_identity
        self.token_patcher = patch(
            "quotations.gmail_addon._verify_google_id_token",
            side_effect=self._verified_claims,
        )
        self.mock_verify_token = self.token_patcher.start()
        self.addCleanup(self.token_patcher.stop)
        self.identity_patcher = patch(
            "quotations.gmail_addon._fetch_message_identity",
            side_effect=self._same_id_identity,
        )
        self.mock_fetch_identity = self.identity_patcher.start()
        self.addCleanup(self.identity_patcher.stop)

    @staticmethod
    def _same_id_identity(_connection, message_id):
        thread_id = (
            "thread-f:2345678"
            if message_id == "msg-f:1234567"
            else "thread-f:one"
        )
        return message_id, thread_id

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
        authorized_scopes=None,
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
                "authorizedScopes": (
                    REQUIRED_SCOPES
                    if authorized_scopes is None
                    else authorized_scopes
                ),
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
        self.assertEqual(set(payload), {"action"})
        card = payload["action"]["navigations"][0]["pushCard"]
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
            required_message_id="msg-f:current",
            required_thread_id="thread-f:one",
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
            timeout=gmail_addon.GMAIL_READ_TIMEOUT_SECONDS,
        )

    @override_settings(GMAIL_ADDON_MAX_THREAD_MESSAGES=1000)
    @patch("quotations.gmail_addon._json_request")
    @patch(
        "quotations.gmail_addon.get_valid_access_token",
        return_value="stored-token",
    )
    def test_thread_summary_setting_is_hard_capped_at_one_hundred(
        self,
        _mock_access_token,
        mock_json_request,
    ):
        mock_json_request.return_value = {
            "id": "thread-limit",
            "messages": [
                {
                    "id": f"message-{index:03d}",
                    "threadId": "thread-limit",
                    "internalDate": str(1_000 + index),
                    "snippet": f"Message {index}",
                    "payload": {"headers": []},
                }
                for index in range(150)
            ],
        }

        summaries = gmail_addon._fetch_thread_message_summaries(
            self.connection,
            "thread-limit",
            required_message_id="message-000",
            required_thread_id="thread-limit",
        )

        self.assertEqual(len(summaries), 100)
        self.assertEqual(summaries[0]["message_id"], "message-000")
        self.assertEqual(summaries[1]["message_id"], "message-051")
        self.assertEqual(summaries[-1]["message_id"], "message-149")

    @override_settings(GMAIL_ADDON_MAX_THREAD_MESSAGES=3)
    @patch("quotations.gmail_addon._json_request")
    @patch(
        "quotations.gmail_addon.get_valid_access_token",
        return_value="stored-token",
    )
    def test_thread_summary_shuffled_payload_matches_backend_boundary(
        self,
        _mock_access_token,
        mock_json_request,
    ):
        chronological = [0, 1, 2, 3, 4]
        shuffled = [4, 1, 3, 0, 2]
        messages = {
            index: {
                "id": f"message-{index}",
                "threadId": "thread-shuffled",
                "internalDate": str(1_000 + index),
                "snippet": f"Message {index}",
                "payload": {"headers": []},
            }
            for index in chronological
        }
        mock_json_request.return_value = {
            "id": "thread-shuffled",
            "messages": [messages[index] for index in shuffled],
        }

        summaries = gmail_addon._fetch_thread_message_summaries(
            self.connection,
            "thread-shuffled",
            required_message_id="message-0",
            required_thread_id="thread-shuffled",
        )

        self.assertEqual(
            [summary["message_id"] for summary in summaries],
            ["message-0", "message-3", "message-4"],
        )

    @patch("quotations.gmail_addon._json_request")
    @patch("quotations.gmail_addon.get_valid_access_token", return_value="stored-token")
    def test_event_message_alias_resolves_to_canonical_gmail_identity(
        self,
        _mock_access_token,
        mock_json_request,
    ):
        mock_json_request.return_value = {
            "id": CANONICAL_MESSAGE_ID,
            "threadId": CANONICAL_THREAD_ID,
        }

        identity = self.real_fetch_message_identity(
            self.connection,
            "msg-f:14399576835632390395",
        )

        self.assertEqual(
            identity,
            (CANONICAL_MESSAGE_ID, CANONICAL_THREAD_ID),
        )
        request_url = mock_json_request.call_args.args[0]
        self.assertIn("msg-f%3A14399576835632390395", request_url)
        self.assertIn("fields=id%2CthreadId", request_url)

    @patch("quotations.gmail_addon.time.sleep")
    @patch("quotations.gmail_addon._json_request")
    @patch("quotations.gmail_addon.get_valid_access_token", return_value="stored-token")
    def test_message_identity_retries_each_transient_provider_status_once(
        self,
        _mock_access_token,
        mock_json_request,
        mock_sleep,
    ):
        for status_code in sorted(gmail_addon.TRANSIENT_GMAIL_READ_HTTP_STATUSES):
            with self.subTest(status_code=status_code):
                mock_json_request.reset_mock()
                mock_sleep.reset_mock()
                mock_json_request.side_effect = [
                    google_http_runtime_error(
                        status_code,
                        "secret msg-f:retry system-token",
                    ),
                    {
                        "id": CANONICAL_MESSAGE_ID,
                        "threadId": CANONICAL_THREAD_ID,
                    },
                ]

                with self.assertLogs("quotations.gmail_addon", level="WARNING") as logs:
                    identity = self.real_fetch_message_identity(
                        self.connection,
                        "msg-f:event-alias",
                    )

                self.assertEqual(
                    identity,
                    (CANONICAL_MESSAGE_ID, CANONICAL_THREAD_ID),
                )
                self.assertEqual(mock_json_request.call_count, 2)
                mock_sleep.assert_called_once_with(
                    gmail_addon.GMAIL_READ_RETRY_DELAY_SECONDS
                )
                log_text = " ".join(logs.output)
                self.assertIn("stage=message_identity", log_text)
                expected_category = (
                    "provider_rate_limited"
                    if status_code == 429
                    else "provider_unavailable"
                )
                self.assertIn(f"category={expected_category}", log_text)
                self.assertIn("outcome=retrying", log_text)
                self.assertNotIn("msg-f:retry", log_text)
                self.assertNotIn("system-token", log_text)

    @patch("quotations.gmail_addon.time.sleep")
    @patch("quotations.gmail_addon._json_request")
    @patch("quotations.gmail_addon.get_valid_access_token", return_value="stored-token")
    def test_thread_summary_retries_network_timeout_once(
        self,
        _mock_access_token,
        mock_json_request,
        mock_sleep,
    ):
        mock_json_request.side_effect = [
            TimeoutError("secret thread-f:timeout"),
            {
                "id": "thread-timeout",
                "messages": [],
            },
        ]

        summaries = gmail_addon._fetch_thread_message_summaries(
            self.connection,
            "thread-timeout",
        )

        self.assertEqual(summaries, [])
        self.assertEqual(mock_json_request.call_count, 2)
        mock_sleep.assert_called_once_with(
            gmail_addon.GMAIL_READ_RETRY_DELAY_SECONDS
        )

    @patch("quotations.gmail_addon.time.sleep")
    @patch("quotations.gmail_addon._json_request")
    @patch("quotations.gmail_addon.get_valid_access_token", return_value="stored-token")
    def test_non_transient_gmail_get_failure_is_not_retried(
        self,
        _mock_access_token,
        mock_json_request,
        mock_sleep,
    ):
        mock_json_request.side_effect = google_http_runtime_error(
            400,
            "secret rejected request",
        )

        with self.assertRaises(gmail_addon.GmailAddonReadFailure) as raised:
            self.real_fetch_message_identity(
                self.connection,
                "msg-f:event-alias",
            )

        self.assertEqual(raised.exception.safe_stage, "message_identity")
        self.assertEqual(
            raised.exception.safe_category,
            "provider_request_failed",
        )
        mock_json_request.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("quotations.gmail_addon._json_request")
    @patch("quotations.gmail_addon.get_valid_access_token", return_value="stored-token")
    def test_thread_summary_rejects_a_different_canonical_thread(
        self,
        _mock_access_token,
        mock_json_request,
    ):
        mock_json_request.return_value = {
            "id": "canonical-other-thread",
            "messages": [
                {
                    "id": "canonical-message",
                    "threadId": "canonical-other-thread",
                    "payload": {"headers": []},
                }
            ],
        }

        with self.assertRaises(gmail_addon.GmailAddonInputError):
            gmail_addon._fetch_thread_message_summaries(
                self.connection,
                "thread-f:event-alias",
                required_message_id="canonical-message",
                required_thread_id="canonical-expected-thread",
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
    def test_missing_scopes_are_requested_after_system_auth_before_user_auth(
        self,
        mock_fetch_summaries,
    ):
        authorized_scopes = [
            "https://www.googleapis.com/auth/gmail.addons.execute",
        ]

        response = self._post(
            "quotation-gmail-addon-contextual",
            self._event(
                authorized_scopes=authorized_scopes,
                user_token="invalid-token",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "requesting_google_scopes": {
                    "scopes": [
                        (
                            "https://www.googleapis.com/auth/"
                            "gmail.addons.current.message.metadata"
                        ),
                        "https://www.googleapis.com/auth/userinfo.email",
                    ]
                }
            },
        )
        self.assertEqual(
            self.verified_tokens,
            [("system-token", [CONTEXTUAL_URL])],
        )
        mock_fetch_summaries.assert_not_called()

    @patch("quotations.gmail_addon._issue_handoff")
    def test_action_requests_all_scopes_when_authorized_scopes_are_absent(
        self,
        mock_issue_handoff,
    ):
        event = self._event(
            mode="current_message",
            user_token="invalid-token",
        )
        event["authorizationEventObject"].pop("authorizedScopes")

        response = self._post("quotation-gmail-addon-action", event)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "requesting_google_scopes": {
                    "scopes": REQUIRED_SCOPES,
                }
            },
        )
        self.assertEqual(
            self.verified_tokens,
            [("system-token", [ACTION_URL])],
        )
        mock_issue_handoff.assert_not_called()

    @patch("quotations.gmail_addon._fetch_thread_message_summaries")
    def test_invalid_system_token_cannot_trigger_scope_consent(
        self,
        mock_fetch_summaries,
    ):
        response = self._post(
            "quotation-gmail-addon-contextual",
            self._event(
                system_token="invalid-token",
                authorized_scopes=[],
            ),
            bearer="invalid-token",
        )

        self.assertEqual(response.status_code, 401)
        self.assertNotIn("requesting_google_scopes", response.json())
        self.assertEqual(
            self.verified_tokens,
            [("invalid-token", [CONTEXTUAL_URL])],
        )
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

    @patch("quotations.gmail_addon._fetch_thread_message_summaries")
    def test_missing_shared_connection_returns_authenticated_reconnect_card(
        self,
        mock_fetch_summaries,
    ):
        self.connection.delete()

        response = self._post(
            "quotation-gmail-addon-contextual",
            self._event(),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(set(payload), {"action"})
        card = payload["action"]["navigations"][0]["pushCard"]
        self.assertEqual(card["header"]["title"], "Reconnect shared Gmail")
        widgets = card["sections"][0]["widgets"]
        self.assertIn(
            "Shared Gmail must be reconnected",
            widgets[0]["textParagraph"]["text"],
        )
        settings_link = widgets[1]["buttonList"]["buttons"][0]["onClick"]["openLink"]
        self.assertEqual(
            settings_link["url"],
            (
                "https://app.example.com/admin"
                "?admin_tab=quotations&quotation_tab=contract-intelligence"
            ),
        )
        self.assertEqual(settings_link["openAs"], "FULL_SIZE")
        self.assertEqual(settings_link["onClose"], "RELOAD")
        self.assertEqual(
            self.verified_tokens,
            [
                ("system-token", [CONTEXTUAL_URL]),
                ("user-token", [OAUTH_CLIENT_ID]),
            ],
        )
        mock_fetch_summaries.assert_not_called()

    @patch("quotations.gmail_addon._issue_handoff")
    def test_disconnected_shared_connection_returns_action_reconnect_card(
        self,
        mock_issue_handoff,
    ):
        self.connection.status = GmailOAuthConnection.STATUS_DISCONNECTED
        self.connection.save(update_fields=["status"])

        response = self._post(
            "quotation-gmail-addon-action",
            self._event(mode="current_message"),
        )

        self.assertEqual(response.status_code, 200)
        action = response.json()["renderActions"]["action"]
        self.assertIn("navigations", action)
        self.assertIn(
            "must be reconnected",
            action["notification"]["text"].lower(),
        )
        self.assertEqual(
            action["navigations"][0]["pushCard"]["header"]["title"],
            "Reconnect shared Gmail",
        )
        mock_issue_handoff.assert_not_called()

    @patch(
        "quotations.contract_intelligence._form_request",
        side_effect=RuntimeError(
            'Google OAuth request failed with HTTP 400: {"error":"invalid_grant"}'
        ),
    )
    def test_invalid_grant_returns_reconnect_card_and_revokes_saved_tokens(
        self,
        _mock_form_request,
    ):
        self.connection.access_token_encrypted = ""
        self.connection.refresh_token_encrypted = encrypt_token("expired-refresh-token")
        self.connection.token_expiry = None
        self.connection.save(
            update_fields=[
                "access_token_encrypted",
                "refresh_token_encrypted",
                "token_expiry",
            ]
        )

        response = self._post(
            "quotation-gmail-addon-contextual",
            self._event(),
        )

        self.assertEqual(response.status_code, 200)
        response_text = response.content.decode("utf-8")
        self.assertIn("Reconnect shared Gmail", response_text)
        self.assertNotIn("invalid_grant", response_text)
        self.assertNotIn("expired-refresh-token", response_text)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.status, GmailOAuthConnection.STATUS_ERROR)
        self.assertEqual(self.connection.access_token_encrypted, "")
        self.assertEqual(self.connection.refresh_token_encrypted, "")

    @patch(
        "quotations.gmail_addon.get_valid_access_token",
        side_effect=RuntimeError("token refresh failed: private provider detail"),
    )
    @patch("quotations.gmail_addon._issue_handoff")
    def test_action_token_refresh_failure_returns_sanitized_reconnect_card(
        self,
        mock_issue_handoff,
        _mock_access_token,
    ):
        self.mock_fetch_identity.side_effect = self.real_fetch_message_identity
        response = self._post(
            "quotation-gmail-addon-action",
            self._event(mode="ai_thread"),
        )

        self.assertEqual(response.status_code, 200)
        response_text = response.content.decode("utf-8")
        self.assertIn("Shared Gmail must be reconnected", response_text)
        self.assertNotIn("private provider detail", response_text)
        mock_issue_handoff.assert_not_called()

    @patch("quotations.gmail_addon._issue_handoff")
    @patch("quotations.gmail_addon._fetch_thread_message_summaries")
    def test_selected_action_issues_handoff_and_opens_only_opaque_url(
        self,
        mock_fetch_summaries,
        mock_issue_handoff,
    ):
        canonical_older_id = "19fb2da13e1adcf9"
        self.mock_fetch_identity.side_effect = None
        self.mock_fetch_identity.return_value = (
            CANONICAL_MESSAGE_ID,
            CANONICAL_THREAD_ID,
        )
        mock_fetch_summaries.return_value = [
            {"message_id": canonical_older_id, "label": "Older"},
            {"message_id": CANONICAL_MESSAGE_ID, "label": "Current"},
        ]
        mock_issue_handoff.return_value = (object(), "opaque-handoff-token")

        response = self._post(
            "quotation-gmail-addon-action",
            self._event(
                mode="selected_messages",
                selected=[
                    canonical_older_id,
                    CANONICAL_MESSAGE_ID,
                    canonical_older_id,
                ],
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
            anchor_message_id=CANONICAL_MESSAGE_ID,
            gmail_thread_id=CANONICAL_THREAD_ID,
            mode="selected_messages",
            selected_message_ids=[
                canonical_older_id,
                CANONICAL_MESSAGE_ID,
            ],
            ttl_seconds=1800,
        )
        mock_fetch_summaries.assert_called_once_with(
            self.connection,
            "thread-f:one",
            required_message_id=CANONICAL_MESSAGE_ID,
            required_thread_id=CANONICAL_THREAD_ID,
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
        self.mock_fetch_identity.side_effect = None
        self.mock_fetch_identity.return_value = (
            CANONICAL_MESSAGE_ID,
            CANONICAL_THREAD_ID,
        )
        mock_fetch_summaries.return_value = [
            {"message_id": CANONICAL_MESSAGE_ID, "label": "Current"}
        ]
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
                "anchor_message_id": CANONICAL_MESSAGE_ID,
                "gmail_thread_id": CANONICAL_THREAD_ID,
                "mode": "current_message",
                "selected_message_ids": [],
                "ttl_seconds": 1800,
            },
        )
        self.assertEqual(
            mock_issue_handoff.call_args_list[1].kwargs,
            {
                "anchor_message_id": CANONICAL_MESSAGE_ID,
                "gmail_thread_id": CANONICAL_THREAD_ID,
                "mode": "ai_thread",
                "selected_message_ids": [],
                "ttl_seconds": 1800,
            },
        )
        mock_fetch_summaries.assert_not_called()

    @patch("quotations.gmail_addon._fetch_thread_message_summaries")
    def test_event_aliases_render_as_one_canonical_message(
        self,
        mock_fetch_summaries,
    ):
        self.mock_fetch_identity.side_effect = None
        self.mock_fetch_identity.return_value = (
            CANONICAL_MESSAGE_ID,
            CANONICAL_THREAD_ID,
        )
        mock_fetch_summaries.return_value = [
            {
                "message_id": CANONICAL_MESSAGE_ID,
                "label": "30 Jul 2026 | Buyer | RFQ",
            }
        ]

        response = self._post(
            "quotation-gmail-addon-contextual",
            self._event(
                message_id="msg-f:14399576835632390395",
                thread_id="thread-f:14399576835632390395",
            ),
        )

        self.assertEqual(response.status_code, 200)
        card = response.json()["action"]["navigations"][0]["pushCard"]
        selection = card["sections"][0]["widgets"][1]["selectionInput"]
        self.assertEqual(
            [item["value"] for item in selection["items"]],
            [CANONICAL_MESSAGE_ID],
        )
        self.assertTrue(selection["items"][0]["selected"])

    @patch(
        "quotations.gmail_addon._issue_handoff",
        side_effect=RuntimeError("database secret msg-f:current system-token"),
    )
    @patch("quotations.gmail_addon.time.sleep")
    def test_unexpected_handoff_failure_is_sanitized_and_never_retried(
        self,
        mock_sleep,
        mock_issue_handoff,
    ):
        with self.assertLogs("quotations.gmail_addon", level="WARNING") as logs:
            response = self._post(
                "quotation-gmail-addon-action",
                self._event(mode="current_message"),
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(set(payload), {"renderActions"})
        notification = payload["renderActions"]["action"]["notification"]["text"]
        self.assertIn("temporarily unavailable", notification.lower())
        self.assertIn("try again", notification.lower())
        self.assertIn("close and reopen", notification.lower())
        self.assertNotIn("opening", notification.lower())
        mock_issue_handoff.assert_called_once()
        mock_sleep.assert_not_called()
        response_text = response.content.decode("utf-8")
        self.assertNotIn("database secret", response_text)
        self.assertNotIn("msg-f:current", response_text)
        self.assertNotIn("system-token", response_text)
        log_text = " ".join(logs.output)
        self.assertIn("stage=handoff", log_text)
        self.assertIn("category=unexpected", log_text)
        self.assertIn("outcome=failed", log_text)
        self.assertNotIn("database secret", log_text)
        self.assertNotIn("msg-f:current", log_text)
        self.assertNotIn("system-token", log_text)

    @patch("quotations.gmail_addon._issue_handoff")
    def test_authenticated_action_transient_read_failure_returns_notification(
        self,
        mock_issue_handoff,
    ):
        self.mock_fetch_identity.side_effect = gmail_addon.GmailAddonReadFailure(
            stage="message_identity",
            category="provider_unavailable",
        )

        with self.assertLogs("quotations.gmail_addon", level="WARNING") as logs:
            response = self._post(
                "quotation-gmail-addon-action",
                self._event(mode="current_message"),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {"renderActions"})
        notification = response.json()["renderActions"]["action"]["notification"]["text"]
        self.assertIn("temporarily unavailable", notification.lower())
        self.assertIn("try again", notification.lower())
        self.assertIn("close and reopen", notification.lower())
        mock_issue_handoff.assert_not_called()
        log_text = " ".join(logs.output)
        self.assertIn("stage=message_identity", log_text)
        self.assertIn("category=provider_unavailable", log_text)

    @patch(
        "quotations.gmail_addon._fetch_thread_message_summaries",
        side_effect=RuntimeError("provider secret thread-f:one user-token"),
    )
    def test_authenticated_contextual_failure_returns_recovery_card(
        self,
        _mock_fetch_summaries,
    ):
        with self.assertLogs("quotations.gmail_addon", level="WARNING") as logs:
            response = self._post(
                "quotation-gmail-addon-contextual",
                self._event(),
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(set(payload), {"action"})
        card = payload["action"]["navigations"][0]["pushCard"]
        self.assertEqual(
            card["header"]["title"],
            "Quotation import temporarily unavailable",
        )
        self.assertEqual(card["header"]["subtitle"], "No inquiry was imported")
        recovery_text = card["sections"][0]["widgets"][0]["textParagraph"]["text"]
        self.assertIn("opening the add-on again", recovery_text.lower())
        self.assertIn("close and reopen", recovery_text.lower())
        response_text = response.content.decode("utf-8")
        self.assertNotIn("provider secret", response_text)
        self.assertNotIn("thread-f:one", response_text)
        self.assertNotIn("user-token", response_text)
        log_text = " ".join(logs.output)
        self.assertIn("stage=message_context", log_text)
        self.assertIn("category=unexpected", log_text)
        self.assertNotIn("provider secret", log_text)
        self.assertNotIn("thread-f:one", log_text)
        self.assertNotIn("user-token", log_text)

    @patch("quotations.gmail_addon.time.sleep")
    @patch("quotations.gmail_addon._json_request")
    def test_permanent_transient_gmail_read_is_bounded_and_returns_recovery_card(
        self,
        mock_json_request,
        mock_sleep,
    ):
        self.mock_fetch_identity.side_effect = self.real_fetch_message_identity
        mock_json_request.side_effect = [
            google_http_runtime_error(503, "secret first provider failure"),
            google_http_runtime_error(503, "secret second provider failure"),
        ]

        with self.assertLogs("quotations.gmail_addon", level="WARNING") as logs:
            response = self._post(
                "quotation-gmail-addon-contextual",
                self._event(),
            )

        self.assertEqual(response.status_code, 200)
        card = response.json()["action"]["navigations"][0]["pushCard"]
        self.assertEqual(
            card["header"]["title"],
            "Quotation import temporarily unavailable",
        )
        self.assertEqual(
            mock_json_request.call_count,
            gmail_addon.GMAIL_READ_MAX_ATTEMPTS,
        )
        mock_sleep.assert_called_once_with(
            gmail_addon.GMAIL_READ_RETRY_DELAY_SECONDS
        )
        log_text = " ".join(logs.output)
        self.assertEqual(log_text.count("category=provider_unavailable"), 2)
        self.assertIn("outcome=retrying", log_text)
        self.assertIn("outcome=failed", log_text)
        self.assertNotIn("secret first", log_text)
        self.assertNotIn("secret second", log_text)

    @patch(
        "quotations.gmail_addon._parse_event",
        side_effect=RuntimeError("pre-auth secret system-token"),
    )
    def test_unexpected_failure_before_authentication_remains_fail_closed(
        self,
        _mock_parse_event,
    ):
        with self.assertLogs("quotations.gmail_addon", level="WARNING") as logs:
            response = self._post(
                "quotation-gmail-addon-contextual",
                self._event(),
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "The quotation Gmail add-on is temporarily unavailable."},
        )
        self.assertNotIn("action", response.json())
        log_text = " ".join(logs.output)
        self.assertIn("stage=request", log_text)
        self.assertIn("category=unexpected", log_text)
        self.assertNotIn("pre-auth secret", log_text)
        self.assertNotIn("system-token", log_text)

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
            required_message_id="msg-f:1234567",
            required_thread_id="thread-f:2345678",
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
