import json
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.signing import TimestampSigner
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .contract_intelligence import (
    GMAIL_READONLY_SCOPE,
    GMAIL_SEND_SCOPE,
    analyze_contract_run,
    build_gmail_auth_url,
    discover_contract_sources,
    encrypt_token,
    exchange_gmail_code,
    get_valid_access_token,
    gmail_send_raw_message,
    gmail_oauth_configured,
    resolve_gmail_connection,
    transfer_shared_gmail_credential_owner,
)
from .models import (
    ContractIntelligenceRun,
    ContractIntelligenceSource,
    GmailInquiryImport,
    GmailOAuthConnection,
    QuotationAuditLog,
)


User = get_user_model()


OAUTH_SETTINGS = {
    "GOOGLE_OAUTH_CLIENT_ID": "oauth-client",
    "GOOGLE_OAUTH_CLIENT_SECRET": "oauth-secret",
    "GMAIL_ADDON_SHARED_MAILBOX_EMAIL": "shared@example.com",
}


class DesignatedMailboxEnforcementTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("gmail-owner", is_staff=True)
        self.other_staff = User.objects.create_user(
            "gmail-other-staff",
            is_staff=True,
        )
        self.superuser = User.objects.create_superuser(
            "gmail-admin",
            email="admin@example.com",
            password="pass",
        )
        self.connection = GmailOAuthConnection.objects.create(
            user=self.owner,
            email="shared@example.com",
            is_shared=True,
            access_token_encrypted="existing-access-ciphertext",
            refresh_token_encrypted="existing-refresh-ciphertext",
            scopes=[GMAIL_READONLY_SCOPE, GMAIL_SEND_SCOPE],
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )

    @override_settings(
        **OAUTH_SETTINGS,
        QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=False,
    )
    @patch("quotations.contract_intelligence._json_request")
    @patch("quotations.contract_intelligence._form_request")
    def test_disabled_flag_preserves_legacy_wrong_mailbox_replacement(
        self,
        form_request,
        json_request,
    ):
        form_request.return_value = {
            "access_token": "replacement-access-token",
            "refresh_token": "replacement-refresh-token",
            "expires_in": 3600,
            "scope": GMAIL_READONLY_SCOPE,
        }
        json_request.return_value = {"emailAddress": "other@example.com"}

        replacement = exchange_gmail_code(self.superuser, "oauth-code")

        self.connection.refresh_from_db()
        self.assertFalse(self.connection.is_shared)
        self.assertEqual(replacement.user_id, self.superuser.id)
        self.assertEqual(replacement.email, "other@example.com")
        self.assertTrue(replacement.is_shared)

    @override_settings(
        **OAUTH_SETTINGS,
        QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=False,
    )
    @patch("quotations.contract_intelligence._json_request")
    @patch("quotations.contract_intelligence._form_request")
    def test_disabled_flag_preserves_same_owner_mailbox_replacement(
        self,
        form_request,
        json_request,
    ):
        form_request.return_value = {
            "access_token": "replacement-access-token",
            "refresh_token": "replacement-refresh-token",
            "expires_in": 3600,
            "scope": GMAIL_READONLY_SCOPE,
        }
        json_request.return_value = {"emailAddress": "other@example.com"}

        replacement = exchange_gmail_code(self.owner, "oauth-code")

        self.assertEqual(replacement.pk, self.connection.pk)
        self.assertEqual(replacement.email, "other@example.com")
        self.assertTrue(replacement.is_shared)

    @override_settings(
        **OAUTH_SETTINGS,
        QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=True,
    )
    @patch("quotations.contract_intelligence._json_request")
    @patch("quotations.contract_intelligence._form_request")
    def test_mismatched_shared_row_never_reuses_an_old_mailbox_refresh_token(
        self,
        form_request,
        json_request,
    ):
        self.connection.email = "old-mailbox@example.com"
        self.connection.save(update_fields=["email"])
        form_request.return_value = {
            "access_token": "expected-mailbox-access-token",
            "expires_in": 3600,
            "scope": GMAIL_READONLY_SCOPE,
        }
        json_request.return_value = {"emailAddress": "shared@example.com"}

        with self.assertRaisesRegex(ValueError, "different mailbox"):
            exchange_gmail_code(self.owner, "oauth-code")

        self.connection.refresh_from_db()
        self.assertEqual(self.connection.email, "old-mailbox@example.com")
        self.assertTrue(self.connection.is_shared)
        self.assertEqual(
            self.connection.access_token_encrypted,
            "existing-access-ciphertext",
        )
        self.assertEqual(
            self.connection.refresh_token_encrypted,
            "existing-refresh-ciphertext",
        )

    @override_settings(
        **OAUTH_SETTINGS,
        QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=True,
    )
    @patch("quotations.contract_intelligence._json_request")
    @patch("quotations.contract_intelligence._form_request")
    def test_mismatched_legacy_owner_row_is_not_repurposed(
        self,
        form_request,
        json_request,
    ):
        self.connection.is_shared = False
        self.connection.email = "personal@example.com"
        self.connection.save(update_fields=["is_shared", "email"])
        form_request.return_value = {
            "access_token": "expected-mailbox-access-token",
            "expires_in": 3600,
            "scope": GMAIL_READONLY_SCOPE,
        }
        json_request.return_value = {"emailAddress": "shared@example.com"}

        with self.assertRaisesRegex(ValueError, "different mailbox"):
            exchange_gmail_code(self.owner, "oauth-code")

        self.connection.refresh_from_db()
        self.assertEqual(self.connection.email, "personal@example.com")
        self.assertFalse(self.connection.is_shared)
        self.assertEqual(
            self.connection.refresh_token_encrypted,
            "existing-refresh-ciphertext",
        )

    @override_settings(
        **OAUTH_SETTINGS,
        QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=True,
    )
    @patch("quotations.contract_intelligence._json_request")
    @patch("quotations.contract_intelligence._form_request")
    def test_conflict_free_successor_creates_distinct_row_and_preserves_provenance(
        self,
        form_request,
        json_request,
    ):
        self.connection.email = "old-mailbox@example.com"
        self.connection.save(update_fields=["email"])
        form_request.return_value = {
            "access_token": "expected-mailbox-access-token",
            "refresh_token": "fresh-expected-mailbox-refresh-token",
            "expires_in": 3600,
            "scope": f"{GMAIL_READONLY_SCOPE} {GMAIL_SEND_SCOPE}",
        }
        json_request.return_value = {"emailAddress": "shared@example.com"}

        gmail_import = GmailInquiryImport.objects.create(
            gmail_connection=self.connection,
            mailbox_email="old-mailbox@example.com",
            gmail_thread_id="legacy-thread",
            anchor_message_id="legacy-message",
        )

        repaired = exchange_gmail_code(self.superuser, "oauth-code")

        self.connection.refresh_from_db()
        gmail_import.refresh_from_db()
        self.assertNotEqual(repaired.pk, self.connection.pk)
        self.assertEqual(repaired.user_id, self.superuser.id)
        self.assertEqual(repaired.email, "shared@example.com")
        self.assertTrue(repaired.is_shared)
        self.assertEqual(self.connection.email, "old-mailbox@example.com")
        self.assertFalse(self.connection.is_shared)
        self.assertEqual(
            self.connection.access_token_encrypted,
            "existing-access-ciphertext",
        )
        self.assertEqual(
            self.connection.refresh_token_encrypted,
            "existing-refresh-ciphertext",
        )
        self.assertEqual(gmail_import.gmail_connection_id, self.connection.pk)
        self.assertEqual(
            get_valid_access_token(repaired),
            "expected-mailbox-access-token",
        )

    @override_settings(
        **OAUTH_SETTINGS,
        QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=True,
    )
    @patch("quotations.contract_intelligence._json_request")
    @patch("quotations.contract_intelligence._form_request")
    def test_new_connection_requires_google_to_issue_a_refresh_token(
        self,
        form_request,
        json_request,
    ):
        self.connection.email = "old-mailbox@example.com"
        self.connection.save(update_fields=["email"])
        form_request.return_value = {
            "access_token": "expected-mailbox-access-token",
            "expires_in": 3600,
            "scope": GMAIL_READONLY_SCOPE,
        }
        json_request.return_value = {"emailAddress": "shared@example.com"}

        with self.assertRaisesRegex(RuntimeError, "refresh token"):
            exchange_gmail_code(self.superuser, "oauth-code")

        self.connection.refresh_from_db()
        self.assertEqual(GmailOAuthConnection.objects.count(), 1)
        self.assertTrue(self.connection.is_shared)
        self.assertEqual(self.connection.email, "old-mailbox@example.com")

    @override_settings(
        **OAUTH_SETTINGS,
        QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=True,
    )
    @patch("quotations.contract_intelligence._json_request")
    @patch("quotations.contract_intelligence._form_request")
    def test_same_mailbox_reauthorization_can_reuse_saved_refresh_token(
        self,
        form_request,
        json_request,
    ):
        self.connection.refresh_token_encrypted = encrypt_token(
            "same-mailbox-refresh-token"
        )
        self.connection.save(update_fields=["refresh_token_encrypted"])
        form_request.return_value = {
            "access_token": "replacement-access-token",
            "expires_in": 3600,
            "scope": GMAIL_READONLY_SCOPE,
        }
        json_request.return_value = {"emailAddress": "shared@example.com"}

        refreshed = exchange_gmail_code(self.owner, "oauth-code")

        self.assertEqual(refreshed.pk, self.connection.pk)
        self.assertEqual(get_valid_access_token(refreshed), "replacement-access-token")

    @override_settings(
        **OAUTH_SETTINGS,
        QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=True,
    )
    @patch("quotations.contract_intelligence._json_request")
    @patch("quotations.contract_intelligence._form_request")
    def test_owner_reuses_expected_mailbox_row_that_predates_shared_designation(
        self,
        form_request,
        json_request,
    ):
        self.connection.is_shared = False
        self.connection.refresh_token_encrypted = encrypt_token("legacy-refresh")
        self.connection.save(
            update_fields=["is_shared", "refresh_token_encrypted"]
        )
        form_request.return_value = {
            "access_token": "replacement-access-token",
            "expires_in": 3600,
            "scope": GMAIL_READONLY_SCOPE,
        }
        json_request.return_value = {"emailAddress": "SHARED@EXAMPLE.COM"}

        refreshed = exchange_gmail_code(self.owner, "oauth-code")

        self.assertEqual(refreshed.pk, self.connection.pk)
        self.assertEqual(refreshed.user_id, self.owner.id)
        self.assertTrue(refreshed.is_shared)
        self.assertEqual(GmailOAuthConnection.objects.count(), 1)

    @override_settings(
        **OAUTH_SETTINGS,
        QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=True,
    )
    @patch("quotations.contract_intelligence._json_request")
    @patch("quotations.contract_intelligence._form_request")
    def test_superuser_reuses_legacy_expected_mailbox_row_without_rebinding_owner(
        self,
        form_request,
        json_request,
    ):
        self.connection.is_shared = False
        self.connection.save(update_fields=["is_shared"])
        form_request.return_value = {
            "access_token": "replacement-access-token",
            "refresh_token": "replacement-refresh-token",
            "expires_in": 3600,
            "scope": GMAIL_READONLY_SCOPE,
        }
        json_request.return_value = {"emailAddress": "shared@example.com"}

        refreshed = exchange_gmail_code(self.superuser, "oauth-code")

        self.assertEqual(refreshed.pk, self.connection.pk)
        self.assertEqual(refreshed.user_id, self.owner.id)
        self.assertTrue(refreshed.is_shared)
        self.assertEqual(GmailOAuthConnection.objects.count(), 1)

    @override_settings(
        **OAUTH_SETTINGS,
        QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=True,
    )
    @patch("quotations.contract_intelligence._json_request")
    @patch("quotations.contract_intelligence._form_request")
    def test_nonowner_cannot_claim_legacy_expected_mailbox_row(
        self,
        form_request,
        json_request,
    ):
        self.connection.is_shared = False
        self.connection.save(update_fields=["is_shared"])
        form_request.return_value = {
            "access_token": "replacement-access-token",
            "refresh_token": "replacement-refresh-token",
            "expires_in": 3600,
            "scope": GMAIL_READONLY_SCOPE,
        }
        json_request.return_value = {"emailAddress": "shared@example.com"}

        with self.assertRaisesRegex(PermissionError, "existing Gmail credential owner"):
            exchange_gmail_code(self.other_staff, "oauth-code")

        self.connection.refresh_from_db()
        self.assertFalse(self.connection.is_shared)
        self.assertEqual(self.connection.user_id, self.owner.id)
        self.assertEqual(GmailOAuthConnection.objects.count(), 1)

    @override_settings(
        **OAUTH_SETTINGS,
        QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=True,
    )
    @patch("quotations.contract_intelligence._json_request")
    @patch("quotations.contract_intelligence._form_request")
    def test_ambiguous_legacy_expected_mailbox_rows_fail_without_merging(
        self,
        form_request,
        json_request,
    ):
        self.connection.is_shared = False
        self.connection.save(update_fields=["is_shared"])
        duplicate_owner = User.objects.create_user(
            "legacy-duplicate-owner",
            is_staff=True,
        )
        duplicate = GmailOAuthConnection.objects.create(
            user=duplicate_owner,
            email=" SHARED@example.com ",
            is_shared=False,
            access_token_encrypted="duplicate-access",
            refresh_token_encrypted="duplicate-refresh",
        )
        form_request.return_value = {
            "access_token": "replacement-access-token",
            "refresh_token": "replacement-refresh-token",
            "expires_in": 3600,
            "scope": GMAIL_READONLY_SCOPE,
        }
        json_request.return_value = {"emailAddress": "shared@example.com"}

        with self.assertRaisesRegex(ValueError, "Multiple Gmail connection rows"):
            exchange_gmail_code(self.superuser, "oauth-code")

        self.connection.refresh_from_db()
        duplicate.refresh_from_db()
        self.assertFalse(self.connection.is_shared)
        self.assertFalse(duplicate.is_shared)
        self.assertEqual(GmailOAuthConnection.objects.count(), 2)

    @override_settings(
        **OAUTH_SETTINGS,
        QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=True,
    )
    @patch("quotations.contract_intelligence._json_request")
    @patch("quotations.contract_intelligence._form_request")
    def test_wrong_google_profile_is_rejected_before_any_connection_mutation(
        self,
        form_request,
        json_request,
    ):
        form_request.return_value = {
            "access_token": "replacement-access-token",
            "refresh_token": "replacement-refresh-token",
            "expires_in": 3600,
            "scope": GMAIL_READONLY_SCOPE,
        }
        json_request.return_value = {"emailAddress": "wrong@example.com"}
        before_updated_at = self.connection.updated_at

        with self.assertRaisesRegex(ValueError, "does not match"):
            exchange_gmail_code(self.superuser, "oauth-code")

        self.connection.refresh_from_db()
        self.assertEqual(GmailOAuthConnection.objects.count(), 1)
        self.assertTrue(self.connection.is_shared)
        self.assertEqual(self.connection.user_id, self.owner.id)
        self.assertEqual(
            self.connection.access_token_encrypted,
            "existing-access-ciphertext",
        )
        self.assertEqual(
            self.connection.refresh_token_encrypted,
            "existing-refresh-ciphertext",
        )
        self.assertEqual(self.connection.updated_at, before_updated_at)
        form_request.assert_called_once()
        json_request.assert_called_once()

    @override_settings(
        **OAUTH_SETTINGS,
        QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=True,
    )
    @patch("quotations.contract_intelligence._json_request")
    @patch("quotations.contract_intelligence._form_request")
    def test_expected_profile_is_accepted_using_canonical_email_identity(
        self,
        form_request,
        json_request,
    ):
        form_request.return_value = {
            "access_token": "replacement-access-token",
            "refresh_token": "replacement-refresh-token",
            "expires_in": 3600,
            "scope": f"{GMAIL_READONLY_SCOPE} {GMAIL_SEND_SCOPE}",
        }
        json_request.return_value = {"emailAddress": "SHARED@EXAMPLE.COM"}

        refreshed = exchange_gmail_code(self.superuser, "oauth-code")

        self.assertEqual(refreshed.pk, self.connection.pk)
        self.assertEqual(refreshed.user_id, self.owner.id)
        self.assertTrue(refreshed.is_shared)
        self.assertEqual(GmailOAuthConnection.objects.count(), 1)

    @override_settings(
        **OAUTH_SETTINGS,
        QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=True,
    )
    @patch("quotations.contract_intelligence._json_request")
    @patch("quotations.contract_intelligence._form_request")
    def test_owner_permission_is_rechecked_after_google_calls_before_persistence(
        self,
        form_request,
        json_request,
    ):
        successor = User.objects.create_user("successor", is_staff=True)
        form_request.return_value = {
            "access_token": "replacement-access-token",
            "refresh_token": "replacement-refresh-token",
            "expires_in": 3600,
            "scope": GMAIL_READONLY_SCOPE,
        }

        def transfer_ownership(_url, **_kwargs):
            GmailOAuthConnection.objects.filter(pk=self.connection.pk).update(
                user=successor
            )
            return {"emailAddress": "shared@example.com"}

        json_request.side_effect = transfer_ownership

        with self.assertRaisesRegex(PermissionError, "credential owner"):
            exchange_gmail_code(self.owner, "oauth-code")

        self.connection.refresh_from_db()
        # The mocked ownership change shares this test transaction and is
        # rolled back with the rejected exchange. The PermissionError proves
        # the post-Google locked recheck observed it before rollback.
        self.assertEqual(self.connection.user_id, self.owner.id)
        self.assertEqual(
            self.connection.access_token_encrypted,
            "existing-access-ciphertext",
        )
        self.assertEqual(
            self.connection.refresh_token_encrypted,
            "existing-refresh-ciphertext",
        )

    @override_settings(
        **OAUTH_SETTINGS,
        QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=True,
    )
    @patch("quotations.contract_intelligence._json_request")
    @patch("quotations.contract_intelligence._form_request")
    def test_actor_deactivation_during_google_calls_prevents_persistence(
        self,
        form_request,
        json_request,
    ):
        form_request.return_value = {
            "access_token": "replacement-access-token",
            "refresh_token": "replacement-refresh-token",
            "expires_in": 3600,
            "scope": GMAIL_READONLY_SCOPE,
        }

        def deactivate_actor(_url, **_kwargs):
            User.objects.filter(pk=self.owner.pk).update(is_active=False)
            return {"emailAddress": "shared@example.com"}

        json_request.side_effect = deactivate_actor

        with self.assertRaisesRegex(PermissionError, "active staff"):
            exchange_gmail_code(self.owner, "oauth-code")

        self.connection.refresh_from_db()
        self.assertEqual(
            self.connection.access_token_encrypted,
            "existing-access-ciphertext",
        )
        self.assertEqual(
            self.connection.refresh_token_encrypted,
            "existing-refresh-ciphertext",
        )

    @override_settings(
        **OAUTH_SETTINGS,
        QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=True,
    )
    @patch("quotations.contract_intelligence._json_request")
    @patch("quotations.contract_intelligence._form_request")
    def test_superuser_demotion_during_google_calls_revokes_replacement_authority(
        self,
        form_request,
        json_request,
    ):
        form_request.return_value = {
            "access_token": "replacement-access-token",
            "refresh_token": "replacement-refresh-token",
            "expires_in": 3600,
            "scope": GMAIL_READONLY_SCOPE,
        }

        def demote_actor(_url, **_kwargs):
            User.objects.filter(pk=self.superuser.pk).update(is_superuser=False)
            return {"emailAddress": "shared@example.com"}

        json_request.side_effect = demote_actor

        with self.assertRaisesRegex(PermissionError, "credential owner"):
            exchange_gmail_code(self.superuser, "oauth-code")

        self.connection.refresh_from_db()
        self.assertEqual(self.connection.user_id, self.owner.id)
        self.assertEqual(
            self.connection.access_token_encrypted,
            "existing-access-ciphertext",
        )

    @override_settings(FRONTEND_URL="https://frontend.example")
    @patch("quotations.views.exchange_gmail_code")
    def test_oauth_callback_rejects_an_inactive_staff_actor(self, exchange):
        self.owner.is_active = False
        self.owner.save(update_fields=["is_active"])
        state = TimestampSigner(salt="quotation-gmail-oauth").sign(
            str(self.owner.id)
        )

        response = APIClient().get(
            reverse("quotation-gmail-oauth-callback"),
            {"state": state, "code": "oauth-code"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("gmail=error", response["Location"])
        exchange.assert_not_called()

    @override_settings(
        GOOGLE_OAUTH_CLIENT_ID="oauth-client",
        GOOGLE_OAUTH_CLIENT_SECRET="oauth-secret",
        QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=True,
    )
    @patch("quotations.contract_intelligence._form_request")
    def test_enabled_enforcement_with_missing_or_invalid_mailbox_fails_closed(
        self,
        form_request,
    ):
        for configured_mailbox in ("", "not-an-email"):
            with self.subTest(configured_mailbox=configured_mailbox), override_settings(
                GMAIL_ADDON_SHARED_MAILBOX_EMAIL=configured_mailbox
            ):
                self.assertFalse(gmail_oauth_configured())
                with self.assertRaisesRegex(ValueError, "missing or invalid"):
                    build_gmail_auth_url(self.owner)
                with self.assertRaisesRegex(ValueError, "missing or invalid"):
                    exchange_gmail_code(self.owner, "oauth-code")
                self.assertIsNone(
                    resolve_gmail_connection(self.owner, shared_only=True)
                )
        form_request.assert_not_called()

    @override_settings(
        **OAUTH_SETTINGS,
        QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=True,
    )
    def test_shared_only_resolver_rejects_mismatch_but_keeps_management_visibility(self):
        self.connection.email = "different@example.com"
        self.connection.save(update_fields=["email"])

        self.assertIsNone(
            resolve_gmail_connection(self.owner, shared_only=True)
        )
        self.assertEqual(
            resolve_gmail_connection(self.owner, shared_only=False),
            self.connection,
        )

        self.connection.email = "SHARED@EXAMPLE.COM"
        self.connection.save(update_fields=["email"])
        self.assertEqual(
            resolve_gmail_connection(self.owner, shared_only=True),
            self.connection,
        )

    @override_settings(
        **OAUTH_SETTINGS,
        QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=True,
    )
    @patch("quotations.views.build_gmail_auth_url")
    def test_mismatched_row_does_not_bypass_existing_owner_gate(
        self,
        build_auth_url,
    ):
        self.connection.email = "different@example.com"
        self.connection.save(update_fields=["email"])
        client = APIClient()
        client.force_authenticate(self.other_staff)

        response = client.post(
            reverse("quotation-gmail-connection"),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        build_auth_url.assert_not_called()

    @override_settings(
        **OAUTH_SETTINGS,
        QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=True,
    )
    def test_status_marks_mismatched_mailbox_unavailable_and_reconnectable(self):
        self.connection.email = "different@example.com"
        self.connection.save(update_fields=["email"])
        client = APIClient()
        client.force_authenticate(self.owner)

        response = client.get(reverse("quotation-gmail-connection"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["configured"])
        self.assertTrue(response.data["can_manage"])
        self.assertFalse(response.data["can_reconnect"])
        self.assertFalse(response.data["connection"]["is_connected"])
        self.assertFalse(
            response.data["connection"]["mailbox_matches_designated"]
        )
        self.assertFalse(response.data["send_scope_granted"])
        self.assertTrue(response.data["reconnect_required"])
        self.assertIn(
            "does not match",
            response.data["connection_unavailable_reason"],
        )

    @override_settings(
        **OAUTH_SETTINGS,
        QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=True,
    )
    @patch("quotations.views.build_gmail_auth_url", return_value="https://google.example/oauth")
    def test_only_conflict_free_superuser_gets_mismatch_recovery_action(
        self,
        build_auth_url,
    ):
        self.connection.email = "different@example.com"
        self.connection.save(update_fields=["email"])

        owner_client = APIClient()
        owner_client.force_authenticate(self.owner)
        owner_response = owner_client.post(
            reverse("quotation-gmail-connection"),
            {},
            format="json",
        )

        self.assertEqual(owner_response.status_code, 409)
        build_auth_url.assert_not_called()

        admin_client = APIClient()
        admin_client.force_authenticate(self.superuser)
        admin_status = admin_client.get(reverse("quotation-gmail-connection"))
        admin_response = admin_client.post(
            reverse("quotation-gmail-connection"),
            {},
            format="json",
        )

        self.assertTrue(admin_status.data["can_reconnect"])
        self.assertEqual(admin_response.status_code, 200)
        self.assertEqual(admin_response.data["auth_url"], "https://google.example/oauth")
        build_auth_url.assert_called_once()

    @override_settings(
        **OAUTH_SETTINGS,
        QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=True,
    )
    @patch("quotations.views.build_gmail_auth_url")
    def test_superuser_with_own_mismatched_row_cannot_start_impossible_recovery(
        self,
        build_auth_url,
    ):
        self.connection.user = self.superuser
        self.connection.email = "different@example.com"
        self.connection.save(update_fields=["user", "email"])
        client = APIClient()
        client.force_authenticate(self.superuser)

        status_response = client.get(reverse("quotation-gmail-connection"))
        connect_response = client.post(
            reverse("quotation-gmail-connection"),
            {},
            format="json",
        )

        self.assertFalse(status_response.data["can_reconnect"])
        self.assertEqual(connect_response.status_code, 409)
        build_auth_url.assert_not_called()

    @override_settings(
        **OAUTH_SETTINGS,
        QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=True,
    )
    def test_disconnected_mismatch_still_requires_explicit_recovery(self):
        self.connection.email = "different@example.com"
        self.connection.status = GmailOAuthConnection.STATUS_DISCONNECTED
        self.connection.save(update_fields=["email", "status"])
        client = APIClient()
        client.force_authenticate(self.owner)

        response = client.get(reverse("quotation-gmail-connection"))

        self.assertTrue(response.data["reconnect_required"])
        self.assertFalse(response.data["connection"]["is_connected"])
        self.assertIn(
            "does not match",
            response.data["connection_unavailable_reason"],
        )

    @override_settings(
        GOOGLE_OAUTH_CLIENT_ID="oauth-client",
        GOOGLE_OAUTH_CLIENT_SECRET="oauth-secret",
        GMAIL_ADDON_SHARED_MAILBOX_EMAIL="",
        QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=True,
    )
    def test_status_identifies_missing_designated_mailbox_configuration(self):
        client = APIClient()
        client.force_authenticate(self.owner)

        response = client.get(reverse("quotation-gmail-connection"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["configured"])
        self.assertFalse(response.data["can_reconnect"])
        self.assertIn(
            "GMAIL_ADDON_SHARED_MAILBOX_EMAIL",
            response.data["configuration_error"],
        )

    @override_settings(
        **OAUTH_SETTINGS,
        QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=True,
    )
    @patch("quotations.contract_intelligence._json_request")
    @patch("quotations.contract_intelligence._form_request")
    def test_token_gate_rejects_wrong_connection_before_return_refresh_or_google_call(
        self,
        form_request,
        json_request,
    ):
        self.connection.email = "different@example.com"
        valid_access_ciphertext = encrypt_token("still-valid-token")
        self.connection.access_token_encrypted = valid_access_ciphertext
        self.connection.token_expiry = timezone.now() + timedelta(hours=1)
        self.connection.save(
            update_fields=["email", "access_token_encrypted", "token_expiry"]
        )
        before_updated_at = self.connection.updated_at

        with self.assertRaisesRegex(RuntimeError, "does not match"):
            get_valid_access_token(self.connection)

        self.connection.refresh_from_db()
        self.assertEqual(
            self.connection.access_token_encrypted,
            valid_access_ciphertext,
        )
        self.assertEqual(self.connection.updated_at, before_updated_at)
        form_request.assert_not_called()
        json_request.assert_not_called()

    @override_settings(
        **OAUTH_SETTINGS,
        QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=True,
    )
    @patch("quotations.contract_intelligence._json_request")
    def test_prepared_send_token_cannot_bypass_designated_mailbox_gate(
        self,
        json_request,
    ):
        self.connection.email = "different@example.com"
        self.connection.save(update_fields=["email"])

        with self.assertRaisesRegex(RuntimeError, "does not match"):
            gmail_send_raw_message(
                self.connection,
                "prepared-rfc-message",
                access_token="already-prepared-token",
            )

        json_request.assert_not_called()

    @override_settings(
        **OAUTH_SETTINGS,
        QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=False,
    )
    @patch("quotations.contract_intelligence.hydrate_contract_source")
    @patch("quotations.contract_intelligence.gmail_search_messages")
    def test_disabled_flag_preserves_legacy_operational_connection_fallback(
        self,
        gmail_search_messages,
        hydrate_contract_source,
    ):
        self.connection.is_shared = False
        self.connection.save(update_fields=["is_shared"])
        gmail_search_messages.return_value = {
            "messages": [],
            "next_page_token": "",
            "result_size_estimate": 0,
        }
        run = ContractIntelligenceRun.objects.create(
            target_company_name="Legacy Customer",
            created_by=self.owner,
        )

        discover_contract_sources(run, self.owner)

        gmail_search_messages.assert_called_once()
        self.assertEqual(
            gmail_search_messages.call_args.args[0],
            self.connection,
        )
        source = ContractIntelligenceSource.objects.create(
            run=run,
            gmail_message_id="legacy-fallback-message",
            subject="Legacy fallback source",
            body_text="",
            status="candidate",
        )

        def hydrated(candidate, connection, **_kwargs):
            self.assertEqual(connection, self.connection)
            candidate.body_text = "Pulse oximeter quantity 2 NOS"
            return candidate

        hydrate_contract_source.side_effect = hydrated

        result = analyze_contract_run(
            run,
            self.owner,
            use_ai=False,
            source_limit=1,
        )

        self.assertEqual(result["sources_failed"], 0)
        hydrate_contract_source.assert_called_once()

    @override_settings(
        **OAUTH_SETTINGS,
        QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=True,
    )
    @patch("quotations.contract_intelligence._json_request")
    @patch("quotations.contract_intelligence._form_request")
    def test_contract_discovery_and_legacy_source_analysis_make_no_google_call_on_mismatch(
        self,
        form_request,
        json_request,
    ):
        self.connection.email = "different@example.com"
        self.connection.access_token_encrypted = encrypt_token("still-valid-token")
        self.connection.token_expiry = timezone.now() + timedelta(hours=1)
        self.connection.save(
            update_fields=["email", "access_token_encrypted", "token_expiry"]
        )
        run = ContractIntelligenceRun.objects.create(
            target_company_name="Example Customer",
            created_by=self.owner,
        )

        with self.assertRaisesRegex(RuntimeError, "Connect Gmail read-only"):
            discover_contract_sources(run, self.owner)

        source = ContractIntelligenceSource.objects.create(
            run=run,
            gmail_connection=self.connection,
            mailbox_email=self.connection.email,
            gmail_message_id="legacy-message-1",
            subject="Legacy wrong-mailbox source",
            body_text="",
            status="candidate",
        )
        result = analyze_contract_run(run, self.owner, use_ai=False, source_limit=1)

        source.refresh_from_db()
        self.assertEqual(result["sources_failed"], 1)
        self.assertEqual(source.status, "failed")
        self.assertIn("Could not fetch Gmail source content", source.error)
        form_request.assert_not_called()
        json_request.assert_not_called()


@override_settings(
    GMAIL_ADDON_SHARED_MAILBOX_EMAIL="shared@example.com",
    QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=True,
)
class SharedMailboxOwnerTransferTests(TestCase):
    def setUp(self):
        self.initiator = User.objects.create_superuser(
            "transfer-admin",
            email="transfer-admin@example.com",
            password="pass",
        )
        self.owner = User.objects.create_user("old-owner", is_staff=True)
        self.new_owner = User.objects.create_user("new-owner", is_staff=True)
        self.connection = GmailOAuthConnection.objects.create(
            user=self.owner,
            email="shared@example.com",
            google_subject="google-subject-1",
            is_shared=True,
            access_token_encrypted="access-token-ciphertext",
            refresh_token_encrypted="refresh-token-ciphertext",
            token_expiry=timezone.now(),
            scopes=[GMAIL_READONLY_SCOPE, GMAIL_SEND_SCOPE],
            status=GmailOAuthConnection.STATUS_CONNECTED,
            last_error="",
        )
        self.gmail_import = GmailInquiryImport.objects.create(
            gmail_connection=self.connection,
            mailbox_email=self.connection.email,
            gmail_thread_id="thread-1",
            anchor_message_id="message-1",
        )

    def transfer(self, **overrides):
        params = {
            "initiated_by": self.initiator,
            "new_owner": self.new_owner,
            "confirmed_mailbox": "shared@example.com",
            "apply": True,
        }
        params.update(overrides)
        return transfer_shared_gmail_credential_owner(**params)

    def test_service_is_dry_run_by_default(self):
        result = transfer_shared_gmail_credential_owner(
            initiated_by=self.initiator,
            new_owner=self.new_owner,
            confirmed_mailbox="SHARED@EXAMPLE.COM",
        )

        self.connection.refresh_from_db()
        self.assertFalse(result["applied"])
        self.assertEqual(self.connection.user_id, self.owner.id)
        self.assertFalse(QuotationAuditLog.objects.exists())

    def test_current_credential_owner_cannot_be_deleted_before_transfer(self):
        with self.assertRaises(ProtectedError):
            self.owner.delete()

        self.owner.refresh_from_db()
        self.connection.refresh_from_db()
        self.gmail_import.refresh_from_db()
        self.assertEqual(self.connection.user_id, self.owner.id)
        self.assertEqual(
            self.gmail_import.gmail_connection_id,
            self.connection.pk,
        )

    def test_apply_changes_only_owner_and_preserves_provenance_then_old_owner_can_be_deleted(self):
        preserved = {
            "pk": self.connection.pk,
            "email": self.connection.email,
            "google_subject": self.connection.google_subject,
            "access_token_encrypted": self.connection.access_token_encrypted,
            "refresh_token_encrypted": self.connection.refresh_token_encrypted,
            "token_expiry": self.connection.token_expiry,
            "scopes": self.connection.scopes,
            "status": self.connection.status,
            "connected_at": self.connection.connected_at,
            "updated_at": self.connection.updated_at,
        }

        result = self.transfer()

        self.connection.refresh_from_db()
        self.gmail_import.refresh_from_db()
        self.assertTrue(result["applied"])
        self.assertEqual(self.connection.user_id, self.new_owner.id)
        self.assertEqual(self.gmail_import.gmail_connection_id, self.connection.pk)
        for field, expected in preserved.items():
            self.assertEqual(getattr(self.connection, field), expected)

        audit = QuotationAuditLog.objects.get(
            target_type="GmailOAuthConnection",
            target_id=self.connection.pk,
        )
        self.assertEqual(audit.actor_id, self.initiator.id)
        self.assertEqual(audit.action, QuotationAuditLog.ACTION_UPDATED)
        self.assertEqual(audit.changes["mailbox"], "shared@example.com")
        self.assertEqual(
            audit.changes["initiated_by"],
            {"id": self.initiator.id, "username": self.initiator.username},
        )
        self.assertEqual(audit.changes["previous_owner"]["id"], self.owner.id)
        self.assertEqual(audit.changes["new_owner"]["id"], self.new_owner.id)
        audit_payload = json.dumps(
            {"message": audit.message, "changes": audit.changes},
            sort_keys=True,
        )
        self.assertNotIn("access-token-ciphertext", audit_payload)
        self.assertNotIn("refresh-token-ciphertext", audit_payload)

        self.owner.delete()
        self.connection.refresh_from_db()
        self.gmail_import.refresh_from_db()
        self.assertEqual(self.connection.user_id, self.new_owner.id)
        self.assertEqual(self.gmail_import.gmail_connection_id, self.connection.pk)

        initiator_id = self.initiator.id
        initiator_username = self.initiator.username
        self.initiator.delete()
        audit.refresh_from_db()
        self.assertIsNone(audit.actor_id)
        self.assertEqual(
            audit.changes["initiated_by"],
            {"id": initiator_id, "username": initiator_username},
        )

    def test_active_superuser_and_active_staff_are_required(self):
        ordinary_staff = User.objects.create_user("ordinary", is_staff=True)
        inactive_admin = User.objects.create_superuser(
            "inactive-admin",
            email="inactive@example.com",
            password="pass",
        )
        inactive_admin.is_active = False
        inactive_admin.save(update_fields=["is_active"])
        inactive_target = User.objects.create_user(
            "inactive-target",
            is_staff=True,
            is_active=False,
        )
        nonstaff_target = User.objects.create_user("nonstaff-target")

        for initiator in (ordinary_staff, inactive_admin):
            with self.subTest(initiator=initiator.username):
                with self.assertRaisesRegex(PermissionError, "active superuser"):
                    self.transfer(initiated_by=initiator)
        for target in (inactive_target, nonstaff_target):
            with self.subTest(target=target.username):
                with self.assertRaisesRegex(ValueError, "active staff"):
                    self.transfer(new_owner=target)

        self.connection.refresh_from_db()
        self.assertEqual(self.connection.user_id, self.owner.id)
        self.assertFalse(QuotationAuditLog.objects.exists())

    def test_confirmation_and_connection_must_match_configured_mailbox(self):
        with self.assertRaisesRegex(ValueError, "confirmation"):
            self.transfer(confirmed_mailbox="other@example.com")

        self.connection.email = "other@example.com"
        self.connection.save(update_fields=["email"])
        with self.assertRaisesRegex(ValueError, "connection does not match"):
            self.transfer()

        self.connection.refresh_from_db()
        self.assertEqual(self.connection.user_id, self.owner.id)
        self.assertFalse(QuotationAuditLog.objects.exists())

    @override_settings(
        QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=False,
    )
    def test_transfer_is_unavailable_while_feature_flag_is_disabled(self):
        with self.assertRaisesRegex(ValueError, "owner transfer is disabled"):
            self.transfer()

        self.connection.refresh_from_db()
        self.assertEqual(self.connection.user_id, self.owner.id)

    def test_target_with_another_connection_fails_without_mutation(self):
        GmailOAuthConnection.objects.create(
            user=self.new_owner,
            email="personal@example.com",
            is_shared=False,
        )

        with self.assertRaisesRegex(ValueError, "already owns another"):
            self.transfer()

        self.connection.refresh_from_db()
        self.assertEqual(self.connection.user_id, self.owner.id)
        self.assertTrue(self.connection.is_shared)
        self.assertFalse(QuotationAuditLog.objects.exists())

    def test_management_command_is_dry_run_unless_apply_is_explicit(self):
        dry_run_stdout = StringIO()
        call_command(
            "transfer_shared_gmail_owner",
            initiated_by=self.initiator.username,
            new_owner=self.new_owner.username,
            confirm_mailbox="shared@example.com",
            stdout=dry_run_stdout,
        )

        self.connection.refresh_from_db()
        self.assertEqual(self.connection.user_id, self.owner.id)
        self.assertIn("DRY RUN ONLY", dry_run_stdout.getvalue())

        applied_stdout = StringIO()
        call_command(
            "transfer_shared_gmail_owner",
            initiated_by=self.initiator.username,
            new_owner=self.new_owner.username,
            confirm_mailbox="shared@example.com",
            apply=True,
            stdout=applied_stdout,
        )
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.user_id, self.new_owner.id)
        self.assertIn("ownership transferred", applied_stdout.getvalue())

    def test_management_command_rejects_non_superuser_attribution(self):
        with self.assertRaises(CommandError):
            call_command(
                "transfer_shared_gmail_owner",
                initiated_by=self.owner.username,
                new_owner=self.new_owner.username,
                confirm_mailbox="shared@example.com",
                apply=True,
            )

        self.connection.refresh_from_db()
        self.assertEqual(self.connection.user_id, self.owner.id)
