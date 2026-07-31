from datetime import timedelta
from decimal import Decimal
from queue import Queue
from threading import Barrier, Event, Thread
from unittest import expectedFailure, skipUnless
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import (
    OperationalError,
    close_old_connections,
    connection,
    connections,
    transaction,
)
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient
from django.test import TransactionTestCase

from api.models import Product

from .contract_intelligence import (
    GMAIL_READONLY_SCOPE,
    GMAIL_SEND_SCOPE,
    encrypt_token,
)
from .models import (
    Company,
    CompanyContact,
    GmailOAuthConnection,
    Quotation,
    QuotationAuditLog,
    QuotationEmailDelivery,
    QuotationLine,
)
from .quotation_email_delivery import (
    _mark_delivery_failure,
    _record_successful_delivery,
)
from .serializers import QuotationSerializer


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
            results.put(
                (
                    label,
                    "response",
                    response.status_code,
                    str(getattr(response, "data", {}).get("code") or ""),
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

    @expectedFailure
    def test_quotation_patch_holds_quote_lock_until_update_commits(self):
        """Characterizes the missing PATCH lock fixed by hardening task 1.2."""

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

    @expectedFailure
    def test_quotation_line_delete_holds_quote_lock_until_delete_commits(self):
        """Characterizes the missing DELETE lock fixed by hardening task 1.2."""

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

    @patch(
        "quotations.quotation_email_delivery.build_quotation_pdf",
        return_value=b"%PDF-concurrency-test",
    )
    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    @expectedFailure
    def test_concurrent_send_invokes_gmail_once(self, gmail_send, _build_pdf):
        """Also captures PostgreSQL's rejection of the current preview lock join.

        Task 1.2 narrows that lock to the quotation table, then this same test
        exercises the concurrent provider-call invariant and loses the xfail.
        """

        quotation, _line = self.create_quote()
        provider_entered = Event()
        release_provider = Event()
        results = Queue()

        def paused_provider(*_args, **_kwargs):
            provider_entered.set()
            if not release_provider.wait(timeout=10):
                raise AssertionError("Timed out waiting to release the Gmail provider call.")
            return {"id": "gmail-concurrent-sent", "threadId": "gmail-concurrent-thread"}

        gmail_send.side_effect = paused_provider
        url = reverse("quotation-finalize-and-send", args=[quotation.id])
        payload = {
            "to": ["buyer@example.com"],
            "cc": [],
            "subject": "Quotation preview",
            "body": "Please find the quotation attached.",
            "confirm_recipient": True,
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
        delivery = QuotationEmailDelivery.objects.get(quotation=quotation)
        self.assertEqual(delivery.status, QuotationEmailDelivery.STATUS_SENT)
        self.assertEqual(delivery.attempt_count, 1)
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
