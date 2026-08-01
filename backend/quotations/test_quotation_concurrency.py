from datetime import timedelta
from decimal import Decimal
from queue import Queue
from threading import Barrier, Event, Thread, current_thread
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import (
    OperationalError,
    close_old_connections,
    connection,
    connections,
    transaction,
)
from django.db.models.deletion import ProtectedError
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from api.models import Product

from .contract_intelligence import (
    GMAIL_READONLY_SCOPE,
    GMAIL_SEND_SCOPE,
    _lock_designated_gmail_mailbox,
    encrypt_token,
    exchange_gmail_code,
    transfer_shared_gmail_credential_owner,
)
from .models import (
    Company,
    CompanyContact,
    GmailOAuthConnection,
    Quotation,
    QuotationAuditLog,
    QuotationEmailDelivery,
    QuotationEmailDeliveryAttempt,
    QuotationEmailOutboundSnapshot,
    QuotationLine,
)
from .quotation_email_delivery import (
    _mark_delivery_failure,
    _record_successful_delivery,
    _validate_editable_fields,
)
from .serializers import QuotationSerializer
from .views import QuotationLineViewSet, QuotationViewSet


@skipUnless(
    connection.vendor == "postgresql",
    "PostgreSQL row-lock semantics are required.",
)
@override_settings(
    GMAIL_ADDON_SHARED_MAILBOX_EMAIL="shared@example.com",
    QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=True,
)
class GmailCredentialOwnerConcurrencyTests(TransactionTestCase):
    """Production-database checks for owner transfer/delete serialization."""

    reset_sequences = True

    def setUp(self):
        self.initiator = User.objects.create_superuser(
            username="gmail-transfer-concurrency-admin",
            email="gmail-transfer-concurrency-admin@example.com",
            password="pass",
        )
        self.old_owner = User.objects.create_user(
            username="gmail-transfer-concurrency-old-owner",
            is_staff=True,
        )
        self.new_owner = User.objects.create_user(
            username="gmail-transfer-concurrency-new-owner",
            is_staff=True,
        )
        self.gmail_connection = GmailOAuthConnection.objects.create(
            user=self.old_owner,
            is_shared=True,
            email="shared@example.com",
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )

    def test_old_owner_delete_cannot_collect_connection_during_transfer(self):
        transfer_mutated_owner = Event()
        release_transfer = Event()
        results = Queue()

        def paused_audit_log(*_args, **_kwargs):
            transfer_mutated_owner.set()
            if not release_transfer.wait(timeout=10):
                raise AssertionError("Timed out waiting to complete owner transfer.")

        def transfer_owner():
            close_old_connections()
            try:
                result = transfer_shared_gmail_credential_owner(
                    initiated_by=User.objects.get(pk=self.initiator.pk),
                    new_owner=User.objects.get(pk=self.new_owner.pk),
                    confirmed_mailbox="shared@example.com",
                    apply=True,
                )
                results.put(("transfer", result["applied"]))
            except Exception as exc:  # pragma: no cover - PostgreSQL diagnostic
                results.put(("transfer", "error", repr(exc)))
            finally:
                connections.close_all()

        worker = Thread(target=transfer_owner, daemon=True)
        with patch(
            "quotations.contract_intelligence.audit_log",
            side_effect=paused_audit_log,
        ):
            try:
                worker.start()
                self.assertTrue(
                    transfer_mutated_owner.wait(timeout=10),
                    "Owner transfer never reached its mutation boundary.",
                )
                # The transfer is uncommitted, so this connection still sees
                # the old owner relationship. PROTECT must reject collection
                # instead of scheduling the credential row for a stale-PK
                # cascade delete after the transfer commits.
                with self.assertRaises(ProtectedError):
                    User.objects.get(pk=self.old_owner.pk).delete()
            finally:
                release_transfer.set()
                worker.join(timeout=10)

        self.assertFalse(worker.is_alive(), "Owner transfer deadlocked.")
        self.assertEqual(results.get_nowait(), ("transfer", True))
        self.gmail_connection.refresh_from_db()
        self.assertEqual(self.gmail_connection.user_id, self.new_owner.pk)
        self.assertTrue(User.objects.filter(pk=self.old_owner.pk).exists())

    def test_concurrent_first_connects_reuse_one_physical_mailbox_row(self):
        GmailOAuthConnection.objects.all().delete()
        second_admin = User.objects.create_superuser(
            username="gmail-first-connect-concurrency-admin-2",
            email="gmail-first-connect-concurrency-admin-2@example.com",
            password="pass",
        )
        both_ready = Barrier(2)
        results = Queue()

        def synchronized_mailbox_lock(mailbox):
            both_ready.wait(timeout=10)
            return _lock_designated_gmail_mailbox(mailbox)

        def token_response(_url, data, **_kwargs):
            code = str(data["code"])
            return {
                "access_token": f"access-{code}",
                "refresh_token": f"refresh-{code}",
                "expires_in": 3600,
                "scope": GMAIL_READONLY_SCOPE,
            }

        def connect(actor_id, code):
            close_old_connections()
            try:
                gmail = exchange_gmail_code(
                    User.objects.get(pk=actor_id),
                    code,
                )
                results.put(("connected", gmail.pk))
            except Exception as exc:  # pragma: no cover - PostgreSQL diagnostic
                results.put(("error", repr(exc)))
            finally:
                connections.close_all()

        workers = [
            Thread(
                target=connect,
                args=(self.initiator.pk, "first"),
                daemon=True,
            ),
            Thread(
                target=connect,
                args=(second_admin.pk, "second"),
                daemon=True,
            ),
        ]
        with (
            patch(
                "quotations.contract_intelligence._lock_designated_gmail_mailbox",
                side_effect=synchronized_mailbox_lock,
            ),
            patch(
                "quotations.contract_intelligence._form_request",
                side_effect=token_response,
            ),
            patch(
                "quotations.contract_intelligence._json_request",
                return_value={"emailAddress": "shared@example.com"},
            ),
        ):
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=15)

        self.assertTrue(all(not worker.is_alive() for worker in workers))
        outcomes = [results.get_nowait(), results.get_nowait()]
        self.assertTrue(
            all(outcome[0] == "connected" for outcome in outcomes),
            outcomes,
        )
        self.assertEqual({outcome[1] for outcome in outcomes}, {
            GmailOAuthConnection.objects.get().pk
        })
        self.assertEqual(GmailOAuthConnection.objects.count(), 1)
        self.assertTrue(GmailOAuthConnection.objects.get().is_shared)


@skipUnless(
    connection.vendor == "postgresql",
    "PostgreSQL row-lock semantics are required.",
)
class QuotationConcurrencyTests(TransactionTestCase):
    """Production-database checks for quotation workflow serialization.

    Each worker opens its own database connection. Synchronization events pause
    at mutation boundaries so the tests do not depend on scheduler timing.
    """

    reset_sequences = True

    def setUp(self):
        self.staff = User.objects.create_user(
            username="quotation-concurrency-staff",
            password="pass",
            is_staff=True,
        )
        self.company = Company.objects.create(
            name="Quotation Concurrency Customer",
            email="accounts@example.com",
        )
        self.contact = CompanyContact.objects.create(
            company=self.company,
            name="Concurrency Buyer",
            email="buyer@example.com",
            is_primary=True,
        )
        self.product = Product.objects.create(
            name="Quotation Concurrency Product",
            price=Decimal("10.00"),
            status="draft",
        )
        self.gmail_connection = GmailOAuthConnection.objects.create(
            user=self.staff,
            is_shared=True,
            email="pharmacydxb@gmail.com",
            scopes=[GMAIL_READONLY_SCOPE, GMAIL_SEND_SCOPE],
            status=GmailOAuthConnection.STATUS_CONNECTED,
            access_token_encrypted=encrypt_token("access-token"),
            token_expiry=timezone.now() + timedelta(hours=1),
        )

    def create_quote(self, *, quote_status=Quotation.STATUS_DRAFT):
        quotation = Quotation.objects.create(
            company=self.company,
            contact=self.contact,
            created_by=self.staff,
            status=quote_status,
            notes="Original notes",
        )
        line = QuotationLine.objects.create(
            quotation=quotation,
            product=self.product,
            item_name_snapshot=self.product.name,
            quantity=Decimal("2.000"),
            unit="PCS",
            unit_price=Decimal("10.000"),
            vat_rate=Decimal("5.00"),
            match_status=QuotationLine.MATCH_CONFIRMED,
        )
        return quotation, line

    def _api_worker(self, results, label, method, url, payload=None):
        close_old_connections()
        try:
            staff = User.objects.get(pk=self.staff.pk)
            client = APIClient()
            client.force_authenticate(staff)
            response = getattr(client, method)(url, payload or {}, format="json")
            response_data = getattr(response, "data", None) or {}
            results.put(
                (
                    label,
                    "response",
                    response.status_code,
                    str(response_data.get("code") or ""),
                )
            )
        except Exception as exc:  # pragma: no cover - diagnostic for PostgreSQL CI
            results.put((label, "error", repr(exc)))
        finally:
            connections.close_all()

    def _quote_lock_is_held_elsewhere(self, quotation_id):
        try:
            with transaction.atomic():
                Quotation.objects.select_for_update(nowait=True).get(pk=quotation_id)
        except OperationalError:
            return True
        return False

    def test_quotation_patch_holds_quote_lock_until_update_commits(self):
        """PATCH holds the workflow lock until its quotation mutation commits."""

        quotation, _line = self.create_quote()
        mutation_entered = Event()
        release_mutation = Event()
        results = Queue()
        original_update = QuotationSerializer.update

        def paused_update(serializer, instance, validated_data):
            mutation_entered.set()
            if not release_mutation.wait(timeout=10):
                raise AssertionError("Timed out waiting to release quotation PATCH.")
            return original_update(serializer, instance, validated_data)

        with patch.object(QuotationSerializer, "update", paused_update):
            worker = Thread(
                target=self._api_worker,
                args=(
                    results,
                    "patch",
                    "patch",
                    reverse("quotation-detail", args=[quotation.id]),
                    {"notes": "Updated safely before finalization"},
                ),
                daemon=True,
            )
            worker.start()
            self.assertTrue(
                mutation_entered.wait(timeout=10),
                "Quotation PATCH never reached its database mutation boundary.",
            )
            lock_was_held = self._quote_lock_is_held_elsewhere(quotation.id)
            release_mutation.set()
            worker.join(timeout=10)

        self.assertFalse(worker.is_alive(), "Quotation PATCH deadlocked.")
        self.assertEqual(
            results.get_nowait(),
            ("patch", "response", status.HTTP_200_OK, ""),
        )
        self.assertTrue(
            lock_was_held,
            "Quotation PATCH must lock the quotation before saving mutable fields.",
        )

    def test_quotation_line_delete_holds_quote_lock_until_delete_commits(self):
        """Line DELETE follows the quotation-first workflow lock order."""

        quotation, line = self.create_quote()
        mutation_entered = Event()
        release_mutation = Event()
        results = Queue()
        original_delete = QuotationLine.delete

        def paused_delete(instance, *args, **kwargs):
            mutation_entered.set()
            if not release_mutation.wait(timeout=10):
                raise AssertionError("Timed out waiting to release quotation-line DELETE.")
            return original_delete(instance, *args, **kwargs)

        with patch.object(QuotationLine, "delete", paused_delete):
            worker = Thread(
                target=self._api_worker,
                args=(
                    results,
                    "delete",
                    "delete",
                    reverse("quotation-line-detail", args=[line.id]),
                ),
                daemon=True,
            )
            worker.start()
            self.assertTrue(
                mutation_entered.wait(timeout=10),
                "Quotation-line DELETE never reached its database mutation boundary.",
            )
            lock_was_held = self._quote_lock_is_held_elsewhere(quotation.id)
            release_mutation.set()
            worker.join(timeout=10)

        self.assertFalse(worker.is_alive(), "Quotation-line DELETE deadlocked.")
        self.assertEqual(
            results.get_nowait(),
            ("delete", "response", status.HTTP_204_NO_CONTENT, ""),
        )
        self.assertTrue(
            lock_was_held,
            "Quotation-line DELETE must lock its quotation before deletion.",
        )

    def test_concurrent_line_deletes_return_204_then_404_without_server_error(self):
        quotation, line = self.create_quote()
        first_delete_entered = Event()
        second_read_line = Event()
        release_first_delete = Event()
        results = Queue()
        original_delete = QuotationLine.delete
        original_get_object = QuotationLineViewSet.get_object

        def paused_first_delete(instance, *args, **kwargs):
            first_delete_entered.set()
            if not release_first_delete.wait(timeout=10):
                raise AssertionError("Timed out waiting to release the first line DELETE.")
            return original_delete(instance, *args, **kwargs)

        def observed_get_object(view):
            value = original_get_object(view)
            if first_delete_entered.is_set():
                second_read_line.set()
            return value

        url = reverse("quotation-line-detail", args=[line.id])
        with patch.object(QuotationLine, "delete", paused_first_delete), patch.object(
            QuotationLineViewSet,
            "get_object",
            observed_get_object,
        ):
            first = Thread(
                target=self._api_worker,
                args=(results, "first", "delete", url),
                daemon=True,
            )
            second = Thread(
                target=self._api_worker,
                args=(results, "second", "delete", url),
                daemon=True,
            )
            try:
                first.start()
                self.assertTrue(first_delete_entered.wait(timeout=10))
                second.start()
                self.assertTrue(
                    second_read_line.wait(timeout=10),
                    "The second DELETE did not read the line before waiting on the quote lock.",
                )
            finally:
                release_first_delete.set()
                first.join(timeout=10)
                if second.ident is not None:
                    second.join(timeout=10)

        self.assertFalse(first.is_alive() or second.is_alive(), "Concurrent line DELETE deadlocked.")
        self.assertEqual(results.qsize(), 2)
        outcomes = [results.get_nowait() for _ in range(2)]
        self.assertFalse([outcome for outcome in outcomes if outcome[1] == "error"], outcomes)
        self.assertCountEqual(
            [(outcome[0], outcome[2]) for outcome in outcomes],
            [
                ("first", status.HTTP_204_NO_CONTENT),
                ("second", status.HTTP_404_NOT_FOUND),
            ],
        )
        self.assertFalse(QuotationLine.objects.filter(pk=line.pk).exists())
        quotation.refresh_from_db()
        self.assertEqual(quotation.total, Decimal("0.00"))

    def test_concurrent_company_contact_patch_revalidates_after_quote_lock(self):
        quotation, _line = self.create_quote()
        next_company = Company.objects.create(name="Next Concurrent Customer")
        next_contact = CompanyContact.objects.create(
            company=next_company,
            name="Next Buyer",
            email="next@example.com",
        )
        first_update_entered = Event()
        second_validation_seen = Event()
        release_first_update = Event()
        results = Queue()
        original_update = QuotationSerializer.update
        original_validate = QuotationSerializer.validate

        def paused_first_update(serializer, instance, validated_data):
            next_value = validated_data.get("company")
            if getattr(next_value, "pk", None) == next_company.pk:
                first_update_entered.set()
                if not release_first_update.wait(timeout=10):
                    raise AssertionError("Timed out waiting to release the first quotation PATCH.")
            return original_update(serializer, instance, validated_data)

        def observed_validate(serializer, attrs):
            contact = attrs.get("contact")
            if "company" not in attrs and getattr(contact, "pk", None) == self.contact.pk:
                second_validation_seen.set()
            return original_validate(serializer, attrs)

        url = reverse("quotation-detail", args=[quotation.id])
        first = Thread(
            target=self._api_worker,
            args=(
                results,
                "first",
                "patch",
                url,
                {"company": next_company.pk, "contact": next_contact.pk},
            ),
            daemon=True,
        )
        second = Thread(
            target=self._api_worker,
            args=(
                results,
                "second",
                "patch",
                url,
                {"contact": self.contact.pk},
            ),
            daemon=True,
        )

        with patch.object(QuotationSerializer, "update", paused_first_update), patch.object(
            QuotationSerializer,
            "validate",
            observed_validate,
        ):
            try:
                first.start()
                self.assertTrue(first_update_entered.wait(timeout=10))
                second.start()
                # With validation inside the quotation lock, this event cannot
                # fire until the first mutation commits and releases its lock.
                self.assertFalse(second_validation_seen.wait(timeout=1))
            finally:
                release_first_update.set()
                first.join(timeout=10)
                if second.ident is not None:
                    second.join(timeout=10)

        self.assertFalse(first.is_alive() or second.is_alive(), "Concurrent quotation PATCH deadlocked.")
        self.assertTrue(second_validation_seen.is_set())
        self.assertEqual(results.qsize(), 2)
        outcomes = [results.get_nowait() for _ in range(2)]
        self.assertFalse([outcome for outcome in outcomes if outcome[1] == "error"], outcomes)
        self.assertCountEqual(
            [(outcome[0], outcome[2]) for outcome in outcomes],
            [
                ("first", status.HTTP_200_OK),
                ("second", status.HTTP_400_BAD_REQUEST),
            ],
        )
        quotation.refresh_from_db()
        self.assertEqual(quotation.company_id, next_company.pk)
        self.assertEqual(quotation.contact_id, next_contact.pk)

    def test_quotation_deleted_before_patch_lock_returns_not_found(self):
        quotation, _line = self.create_quote()
        patch_read_quote = Event()
        release_patch_lookup = Event()
        results = Queue()
        original_get_object = QuotationViewSet.get_object

        def paused_patch_get_object(view):
            value = original_get_object(view)
            if current_thread().name == "stale-quotation-patch":
                patch_read_quote.set()
                if not release_patch_lookup.wait(timeout=10):
                    raise AssertionError("Timed out waiting to release quotation PATCH lookup.")
            return value

        url = reverse("quotation-detail", args=[quotation.id])
        patch_worker = Thread(
            target=self._api_worker,
            args=(results, "patch", "patch", url, {"notes": "Stale patch"}),
            name="stale-quotation-patch",
            daemon=True,
        )
        delete_worker = Thread(
            target=self._api_worker,
            args=(results, "delete", "delete", url),
            name="concurrent-quotation-delete",
            daemon=True,
        )

        with patch.object(QuotationViewSet, "get_object", paused_patch_get_object):
            try:
                patch_worker.start()
                self.assertTrue(
                    patch_read_quote.wait(timeout=10),
                    "Quotation PATCH did not read the row before deletion.",
                )
                delete_worker.start()
                delete_worker.join(timeout=10)
                self.assertFalse(delete_worker.is_alive(), "Quotation DELETE deadlocked.")
            finally:
                release_patch_lookup.set()
                patch_worker.join(timeout=10)
                if delete_worker.ident is not None:
                    delete_worker.join(timeout=10)

        self.assertFalse(patch_worker.is_alive(), "Quotation PATCH deadlocked after deletion.")
        self.assertEqual(results.qsize(), 2)
        outcomes = [results.get_nowait() for _ in range(2)]
        self.assertFalse([outcome for outcome in outcomes if outcome[1] == "error"], outcomes)
        self.assertCountEqual(
            [(outcome[0], outcome[2]) for outcome in outcomes],
            [
                ("delete", status.HTTP_204_NO_CONTENT),
                ("patch", status.HTTP_404_NOT_FOUND),
            ],
        )
        self.assertFalse(Quotation.objects.filter(pk=quotation.pk).exists())

    @patch("quotations.quotation_email_delivery.build_quotation_pdf")
    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    def test_send_waiting_on_quote_mutation_rejects_old_preview(self, gmail_send, build_pdf):
        quotation, _line = self.create_quote()
        preview_client = APIClient()
        preview_client.force_authenticate(self.staff)
        preview = preview_client.get(
            reverse("quotation-email-preview", args=[quotation.id])
        )
        self.assertEqual(preview.status_code, status.HTTP_200_OK)

        mutation_entered = Event()
        release_mutation = Event()
        send_preflight_complete = Event()
        results = Queue()
        original_update = QuotationSerializer.update

        def paused_update(serializer, instance, validated_data):
            mutation_entered.set()
            if not release_mutation.wait(timeout=10):
                raise AssertionError("Timed out waiting to release quotation mutation.")
            return original_update(serializer, instance, validated_data)

        validate_calls = {"count": 0}

        def observed_validation(*args, **kwargs):
            result = _validate_editable_fields(*args, **kwargs)
            validate_calls["count"] += 1
            if validate_calls["count"] == 1:
                send_preflight_complete.set()
            return result

        payload = {
            "to": ["buyer@example.com"],
            "cc": [],
            "subject": "Quotation preview",
            "body": "Please find the quotation attached.",
            "confirm_recipient": True,
            "preview_fingerprint": preview.data["preview_fingerprint"],
        }
        patch_worker = Thread(
            target=self._api_worker,
            args=(
                results,
                "patch",
                "patch",
                reverse("quotation-detail", args=[quotation.id]),
                {"notes": "Changed while the old preview remained open"},
            ),
            daemon=True,
        )
        send_worker = Thread(
            target=self._api_worker,
            args=(
                results,
                "send",
                "post",
                reverse("quotation-finalize-and-send", args=[quotation.id]),
                payload,
            ),
            daemon=True,
        )

        with patch.object(QuotationSerializer, "update", paused_update), patch(
            "quotations.quotation_email_delivery._validate_editable_fields",
            side_effect=observed_validation,
        ):
            try:
                patch_worker.start()
                self.assertTrue(mutation_entered.wait(timeout=10))
                send_worker.start()
                self.assertTrue(send_preflight_complete.wait(timeout=10))
            finally:
                release_mutation.set()
                patch_worker.join(timeout=10)
                send_worker.join(timeout=10)

        self.assertFalse(patch_worker.is_alive(), "Quotation mutation deadlocked.")
        self.assertFalse(send_worker.is_alive(), "Stale-preview send deadlocked.")
        self.assertEqual(results.qsize(), 2)
        outcomes = [results.get_nowait() for _ in range(2)]
        self.assertFalse([outcome for outcome in outcomes if outcome[1] == "error"], outcomes)
        self.assertCountEqual(
            [(outcome[0], outcome[2], outcome[3]) for outcome in outcomes],
            [
                ("patch", status.HTTP_200_OK, ""),
                ("send", status.HTTP_409_CONFLICT, "stale_email_preview"),
            ],
        )
        quotation.refresh_from_db()
        self.assertEqual(quotation.status, Quotation.STATUS_DRAFT)
        self.assertFalse(QuotationEmailDelivery.objects.filter(quotation=quotation).exists())
        build_pdf.assert_not_called()
        gmail_send.assert_not_called()

    def test_quotation_detail_payload_and_review_token_are_one_locked_snapshot(self):
        quotation, line = self.create_quote()
        fingerprint_started = Event()
        release_retrieve = Event()
        mutation_finished = Event()
        results = Queue()
        original_fingerprint = QuotationSerializer.get_quotation_review_fingerprint

        def paused_fingerprint(serializer, instance):
            if current_thread().name == "quotation-review-retrieve":
                fingerprint_started.set()
                if not release_retrieve.wait(timeout=10):
                    raise AssertionError("Timed out waiting to release quotation review.")
            return original_fingerprint(serializer, instance)

        def retrieve_quote():
            close_old_connections()
            try:
                staff = User.objects.get(pk=self.staff.pk)
                client = APIClient()
                client.force_authenticate(staff)
                response = client.get(
                    reverse("quotation-detail", args=[quotation.id])
                )
                results.put(("retrieve", response.status_code, response.data))
            except Exception as exc:  # pragma: no cover - PostgreSQL diagnostic
                results.put(("retrieve", "error", repr(exc)))
            finally:
                connections.close_all()

        def update_line():
            close_old_connections()
            try:
                QuotationLine.objects.filter(pk=line.pk).update(
                    item_name_snapshot="Changed after locked review"
                )
                results.put(("line", "updated"))
            except Exception as exc:  # pragma: no cover - PostgreSQL diagnostic
                results.put(("line", "error", repr(exc)))
            finally:
                mutation_finished.set()
                connections.close_all()

        retrieve_worker = Thread(
            target=retrieve_quote,
            name="quotation-review-retrieve",
            daemon=True,
        )
        mutation_worker = Thread(target=update_line, daemon=True)
        with patch.object(
            QuotationSerializer,
            "get_quotation_review_fingerprint",
            paused_fingerprint,
        ):
            try:
                retrieve_worker.start()
                self.assertTrue(fingerprint_started.wait(timeout=10))
                mutation_worker.start()
                self.assertFalse(
                    mutation_finished.wait(timeout=1),
                    "A PDF-affecting line changed while its review response was being serialized.",
                )
            finally:
                release_retrieve.set()
                retrieve_worker.join(timeout=10)
                if mutation_worker.ident is not None:
                    mutation_worker.join(timeout=10)

        self.assertFalse(retrieve_worker.is_alive() or mutation_worker.is_alive())
        self.assertEqual(results.qsize(), 2)
        outcomes = [results.get_nowait() for _ in range(2)]
        self.assertFalse(
            [outcome for outcome in outcomes if outcome[1] == "error"],
            outcomes,
        )
        retrieve_outcome = next(row for row in outcomes if row[0] == "retrieve")
        self.assertEqual(retrieve_outcome[1], status.HTTP_200_OK)
        review_payload = retrieve_outcome[2]
        self.assertEqual(
            review_payload["lines"][0]["item_name_snapshot"],
            self.product.name,
        )
        self.assertTrue(review_payload["quotation_review_fingerprint"])
        self.assertIn(("line", "updated"), outcomes)

        preview_client = APIClient()
        preview_client.force_authenticate(self.staff)
        stale = preview_client.get(
            reverse("quotation-email-preview", args=[quotation.id]),
            {
                "quotation_review_fingerprint": review_payload[
                    "quotation_review_fingerprint"
                ]
            },
        )
        self.assertEqual(stale.status_code, status.HTTP_409_CONFLICT, stale.data)
        self.assertEqual(stale.data["code"], "stale_quotation_review")

    def test_email_bytes_are_built_while_customer_dependency_lock_is_held(self):
        quotation, _line = self.create_quote()
        self.company.billing_address = "Reviewed customer address"
        self.company.save(update_fields=["billing_address", "updated_at"])
        preview_client = APIClient()
        preview_client.force_authenticate(self.staff)
        preview = preview_client.get(
            reverse("quotation-email-preview", args=[quotation.id])
        )
        self.assertEqual(preview.status_code, status.HTTP_200_OK, preview.data)

        renderer_entered = Event()
        release_renderer = Event()
        mutation_finished = Event()
        results = Queue()
        rendered = {}

        def paused_renderer(current, *, config=None):
            rendered["billing_address"] = current.company.billing_address
            renderer_entered.set()
            if not release_renderer.wait(timeout=10):
                raise AssertionError("Timed out waiting to release PDF preparation.")
            return b"%PDF-locked-customer-state"

        def update_company():
            close_old_connections()
            try:
                Company.objects.filter(pk=self.company.pk).update(
                    billing_address="Address changed after preparation"
                )
                results.put(("company", "updated"))
            except Exception as exc:  # pragma: no cover - PostgreSQL diagnostic
                results.put(("company", "error", repr(exc)))
            finally:
                mutation_finished.set()
                connections.close_all()

        send_payload = {
            "to": ["buyer@example.com"],
            "cc": [],
            "subject": "Quotation preview",
            "body": "Please find the quotation attached.",
            "confirm_recipient": True,
            "preview_fingerprint": preview.data["preview_fingerprint"],
        }
        send_worker = Thread(
            target=self._api_worker,
            args=(
                results,
                "send",
                "post",
                reverse("quotation-finalize-and-send", args=[quotation.id]),
                send_payload,
            ),
            daemon=True,
        )
        mutation_worker = Thread(target=update_company, daemon=True)

        with patch(
            "quotations.quotation_email_delivery.build_quotation_pdf",
            side_effect=paused_renderer,
        ), patch(
            "quotations.quotation_email_delivery.gmail_send_raw_message",
            return_value={"id": "gmail-frozen", "threadId": "thread-frozen"},
        ):
            try:
                send_worker.start()
                self.assertTrue(renderer_entered.wait(timeout=10))
                mutation_worker.start()
                self.assertFalse(
                    mutation_finished.wait(timeout=1),
                    "Customer data changed while the reviewed PDF was being built.",
                )
            finally:
                release_renderer.set()
                send_worker.join(timeout=10)
                if mutation_worker.ident is not None:
                    mutation_worker.join(timeout=10)

        self.assertFalse(send_worker.is_alive() or mutation_worker.is_alive())
        self.assertEqual(rendered["billing_address"], "Reviewed customer address")
        self.assertEqual(results.qsize(), 2)
        outcomes = [results.get_nowait() for _ in range(2)]
        self.assertFalse([outcome for outcome in outcomes if outcome[1] == "error"], outcomes)
        self.assertIn(("send", "response", status.HTTP_200_OK, ""), outcomes)
        self.assertIn(("company", "updated"), outcomes)
        self.company.refresh_from_db()
        self.assertEqual(
            self.company.billing_address,
            "Address changed after preparation",
        )

    def test_image_upload_deleted_before_lock_returns_not_found(self):
        quotation, line = self.create_quote()
        initial_lookup_finished = Event()
        release_upload = Event()
        results = Queue()
        original_get_object = QuotationLineViewSet.get_object

        def paused_get_object(view):
            value = original_get_object(view)
            if current_thread().name == "stale-image-upload":
                initial_lookup_finished.set()
                if not release_upload.wait(timeout=10):
                    raise AssertionError("Timed out waiting to release image upload.")
            return value

        def upload_image():
            close_old_connections()
            try:
                staff = User.objects.get(pk=self.staff.pk)
                client = APIClient()
                client.force_authenticate(staff)
                response = client.post(
                    reverse("quotation-line-upload-product-image", args=[line.id]),
                    {
                        "image": SimpleUploadedFile(
                            "item.png",
                            b"\x89PNG\r\n\x1a\nnot-read-before-lock",
                            content_type="image/png",
                        )
                    },
                    format="multipart",
                )
                results.put(("upload", response.status_code))
            except Exception as exc:  # pragma: no cover - PostgreSQL diagnostic
                results.put(("upload", "error", repr(exc)))
            finally:
                connections.close_all()

        worker = Thread(target=upload_image, name="stale-image-upload", daemon=True)
        with patch.object(QuotationLineViewSet, "get_object", paused_get_object):
            try:
                worker.start()
                self.assertTrue(initial_lookup_finished.wait(timeout=10))
                quotation.delete()
            finally:
                release_upload.set()
                worker.join(timeout=10)

        self.assertFalse(worker.is_alive(), "Image upload deadlocked after deletion.")
        self.assertEqual(results.get_nowait(), ("upload", status.HTTP_404_NOT_FOUND))

    @patch(
        "quotations.quotation_email_delivery.build_quotation_pdf",
        return_value=b"%PDF-concurrency-test",
    )
    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    def test_concurrent_send_invokes_gmail_once(self, gmail_send, _build_pdf):
        """One concurrent confirmation reaches Gmail; the other is rejected."""

        quotation, _line = self.create_quote()
        provider_entered = Event()
        release_provider = Event()
        results = Queue()
        durable_state_seen = []

        def paused_provider(*_args, **_kwargs):
            provider_entered.set()
            if not release_provider.wait(timeout=10):
                raise AssertionError("Timed out waiting to release the Gmail provider call.")
            return {"id": "gmail-concurrent-sent", "threadId": "gmail-concurrent-thread"}

        gmail_send.side_effect = paused_provider
        url = reverse("quotation-finalize-and-send", args=[quotation.id])
        preview_client = APIClient()
        preview_client.force_authenticate(self.staff)
        preview = preview_client.get(
            reverse("quotation-email-preview", args=[quotation.id])
        )
        self.assertEqual(preview.status_code, status.HTTP_200_OK)
        payload = {
            "to": ["buyer@example.com"],
            "cc": [],
            "subject": "Quotation preview",
            "body": "Please find the quotation attached.",
            "confirm_recipient": True,
            "preview_fingerprint": preview.data["preview_fingerprint"],
        }
        first = Thread(
            target=self._api_worker,
            args=(results, "first", "post", url, payload),
            daemon=True,
        )
        second = Thread(
            target=self._api_worker,
            args=(results, "second", "post", url, payload),
            daemon=True,
        )

        try:
            first.start()
            self.assertTrue(
                provider_entered.wait(timeout=10),
                "The first send never reached Gmail.",
            )
            # A separate connection can observe both rows before the provider
            # is released, proving the pre-call transaction has committed.
            durable_state_seen.append(
                (
                    QuotationEmailOutboundSnapshot.objects.filter(
                        delivery__quotation_id=quotation.id
                    ).count(),
                    QuotationEmailDeliveryAttempt.objects.filter(
                        delivery__quotation_id=quotation.id,
                    ).count(),
                )
            )
            second.start()
            second.join(timeout=10)
            self.assertFalse(
                second.is_alive(),
                "The idempotent second send blocked behind the external Gmail call.",
            )
        finally:
            release_provider.set()
            first.join(timeout=10)
            if second.ident is not None:
                second.join(timeout=10)

        self.assertFalse(first.is_alive(), "The first Gmail send deadlocked.")
        self.assertEqual(results.qsize(), 2)
        outcomes = [results.get_nowait() for _ in range(2)]
        self.assertFalse([outcome for outcome in outcomes if outcome[1] == "error"], outcomes)
        self.assertCountEqual(
            [(outcome[0], outcome[2], outcome[3]) for outcome in outcomes],
            [
                ("first", status.HTTP_200_OK, ""),
                ("second", status.HTTP_409_CONFLICT, "delivery_in_progress"),
            ],
        )
        self.assertEqual(gmail_send.call_count, 1)
        self.assertEqual(durable_state_seen, [(1, 1)])
        delivery = QuotationEmailDelivery.objects.get(quotation=quotation)
        self.assertEqual(delivery.status, QuotationEmailDelivery.STATUS_SENT)
        self.assertEqual(delivery.attempt_count, 1)
        self.assertEqual(
            QuotationEmailOutboundSnapshot.objects.filter(delivery=delivery).count(),
            1,
        )
        self.assertEqual(delivery.provider_attempts.count(), 1)
        quotation.refresh_from_db()
        self.assertEqual(quotation.status, Quotation.STATUS_SENT)

    def test_concurrent_success_and_late_failure_cannot_downgrade_sent(self):
        quotation, _line = self.create_quote(quote_status=Quotation.STATUS_FINALIZED)
        delivery = QuotationEmailDelivery.objects.create(
            quotation=quotation,
            gmail_connection=self.gmail_connection,
            actor=self.staff,
            delivery_mode=QuotationEmailDelivery.MODE_NEW_EMAIL,
            status=QuotationEmailDelivery.STATUS_SENDING,
            to_addresses=["buyer@example.com"],
            subject="Quotation",
            body="Attached.",
            attachment_filename="quotation.pdf",
            attempt_count=1,
            sending_started_at=timezone.now(),
        )
        barrier = Barrier(2)
        results = Queue()

        def record_success():
            close_old_connections()
            try:
                actor = User.objects.get(pk=self.staff.pk)
                barrier.wait(timeout=10)
                _record_successful_delivery(
                    delivery.id,
                    "gmail-success",
                    "gmail-thread",
                    actor,
                )
                results.put(("success", "ok"))
            except Exception as exc:  # pragma: no cover - diagnostic for PostgreSQL CI
                results.put(("success", "error", repr(exc)))
            finally:
                connections.close_all()

        def record_late_failure():
            close_old_connections()
            try:
                actor = User.objects.get(pk=self.staff.pk)
                barrier.wait(timeout=10)
                _mark_delivery_failure(
                    delivery.id,
                    unknown=True,
                    message="Late ambiguous provider response.",
                    actor=actor,
                )
                results.put(("failure", "ok"))
            except Exception as exc:  # pragma: no cover - diagnostic for PostgreSQL CI
                results.put(("failure", "error", repr(exc)))
            finally:
                connections.close_all()

        workers = [
            Thread(target=record_success, daemon=True),
            Thread(target=record_late_failure, daemon=True),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=15)

        self.assertFalse(any(worker.is_alive() for worker in workers), "Delivery completion deadlocked.")
        self.assertEqual(results.qsize(), 2)
        outcomes = [results.get_nowait() for _ in range(2)]
        self.assertCountEqual(outcomes, [("success", "ok"), ("failure", "ok")])
        delivery.refresh_from_db()
        quotation.refresh_from_db()
        self.assertEqual(delivery.status, QuotationEmailDelivery.STATUS_SENT)
        self.assertEqual(delivery.gmail_message_id, "gmail-success")
        self.assertEqual(quotation.status, Quotation.STATUS_SENT)
        self.assertEqual(
            QuotationAuditLog.objects.filter(
                quotation=quotation,
                action=QuotationAuditLog.ACTION_EMAIL_SENT,
            ).count(),
            1,
        )
