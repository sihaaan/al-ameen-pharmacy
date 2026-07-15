from datetime import date

from django.contrib import admin
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import RequestFactory
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .admin import (
    MailboxPOAuditFailureAdmin,
    MailboxPOAuditRunAdmin,
    MailboxPOMatchRunAdmin,
    MailboxPOMessageAdmin,
    QuotationAdmin,
    QuotationLPOAdmin,
)
from .models import (
    Company,
    MailboxPOAuditFailure,
    MailboxPOAuditRun,
    MailboxPOMatchRun,
    MailboxPOMessage,
    Quotation,
    QuotationAuditLog,
    QuotationLPO,
)


class QuotationLPOHistoryGuardTests(APITestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="lpo-auditor", password="pass", is_staff=True)
        self.admin_user = User.objects.create_superuser(username="lpo-admin", password="pass")
        self.company = Company.objects.create(name="LPO History Customer")
        self.quotation = Quotation.objects.create(company=self.company, created_by=self.staff)
        self.client.force_authenticate(self.staff)
        self.admin_request = RequestFactory().get("/admin/quotations/")
        self.admin_request.user = self.admin_user

    def create_lpo(self, *, lpo_status=QuotationLPO.STATUS_CONFIRMED):
        return QuotationLPO.objects.create(
            quotation=self.quotation,
            source_type=QuotationLPO.SOURCE_PASTED_TEXT,
            source_filename="customer-po.pdf",
            source_sha256="a" * 64,
            lpo_number="LPO-ORIGINAL",
            lpo_date=date(2026, 7, 1),
            status=lpo_status,
            parsed_rows=[{"item": "Gloves", "quantity": "10"}],
            notes="Original review",
            received_by=self.staff,
        )

    def test_confirmed_lpo_cannot_be_deleted(self):
        lpo = self.create_lpo()

        response = self.client.delete(reverse("quotation-lpo-detail", args=[lpo.id]))

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("Confirmed LPO records cannot be deleted", response.data["detail"])
        self.assertTrue(QuotationLPO.objects.filter(pk=lpo.pk).exists())
        self.assertFalse(
            QuotationAuditLog.objects.filter(
                action=QuotationAuditLog.ACTION_DELETED,
                target_type="QuotationLPO",
                target_id=lpo.id,
            ).exists()
        )

    def test_patch_and_put_cannot_revert_confirmed_lpo(self):
        lpo = self.create_lpo()
        url = reverse("quotation-lpo-detail", args=[lpo.id])

        for method in (self.client.patch, self.client.put):
            with self.subTest(method=method.__name__):
                response = method(
                    url,
                    {
                        "lpo_number": "LPO-CHANGED-IN-BLOCKED-REQUEST",
                        "status": QuotationLPO.STATUS_NEEDS_REVIEW,
                    },
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("cannot be moved back", str(response.data["status"][0]))
                lpo.refresh_from_db()
                self.assertEqual(lpo.status, QuotationLPO.STATUS_CONFIRMED)
                self.assertEqual(lpo.lpo_number, "LPO-ORIGINAL")

        self.assertFalse(
            QuotationAuditLog.objects.filter(
                action=QuotationAuditLog.ACTION_UPDATED,
                target_type="QuotationLPO",
                target_id=lpo.id,
            ).exists()
        )

    def test_confirmed_reference_correction_keeps_status_and_audits_before_and_after(self):
        lpo = self.create_lpo()

        response = self.client.patch(
            reverse("quotation-lpo-detail", args=[lpo.id]),
            {
                "lpo_number": "LPO-CORRECTED",
                "lpo_date": "2026-07-02",
                "notes": "Corrected against the signed attachment",
                "status": QuotationLPO.STATUS_CONFIRMED,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        lpo.refresh_from_db()
        self.assertEqual(lpo.status, QuotationLPO.STATUS_CONFIRMED)
        self.assertEqual(lpo.lpo_number, "LPO-CORRECTED")
        self.assertEqual(lpo.parsed_rows, [{"item": "Gloves", "quantity": "10"}])

        audit = QuotationAuditLog.objects.get(
            action=QuotationAuditLog.ACTION_UPDATED,
            target_type="QuotationLPO",
            target_id=lpo.id,
        )
        self.assertEqual(
            audit.changes["before"],
            {
                "lpo_number": "LPO-ORIGINAL",
                "lpo_date": "2026-07-01",
                "status": QuotationLPO.STATUS_CONFIRMED,
                "notes": "Original review",
            },
        )
        self.assertEqual(
            audit.changes["after"],
            {
                "lpo_number": "LPO-CORRECTED",
                "lpo_date": "2026-07-02",
                "status": QuotationLPO.STATUS_CONFIRMED,
                "notes": "Corrected against the signed attachment",
            },
        )
        self.assertEqual(audit.changes["changed_fields"], ["lpo_number", "lpo_date", "notes"])

    def test_unconfirmed_lpo_can_still_be_deleted(self):
        lpo = self.create_lpo(lpo_status=QuotationLPO.STATUS_NEEDS_REVIEW)

        response = self.client.delete(reverse("quotation-lpo-detail", args=[lpo.id]))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(QuotationLPO.objects.filter(pk=lpo.pk).exists())

    def test_confirmed_lpo_cannot_be_cascade_deleted_through_quotation(self):
        lpo = self.create_lpo()

        response = self.client.delete(reverse("quotation-detail", args=[self.quotation.id]))

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("confirmed LPO", response.data["detail"])
        self.assertTrue(Quotation.objects.filter(pk=self.quotation.pk).exists())
        self.assertTrue(QuotationLPO.objects.filter(pk=lpo.pk).exists())

    def test_model_save_cannot_downgrade_confirmed_lpo(self):
        lpo = self.create_lpo()
        lpo.status = QuotationLPO.STATUS_NEEDS_REVIEW

        with self.assertRaisesMessage(ValidationError, "cannot be moved back"):
            lpo.save()

        lpo.refresh_from_db()
        self.assertEqual(lpo.status, QuotationLPO.STATUS_CONFIRMED)

    def test_model_delete_cannot_remove_confirmed_lpo_even_with_stale_status(self):
        lpo = self.create_lpo()
        lpo.status = QuotationLPO.STATUS_NEEDS_REVIEW

        with self.assertRaisesMessage(ValidationError, "Confirmed LPO records cannot be deleted"):
            lpo.delete()

        self.assertTrue(QuotationLPO.objects.filter(pk=lpo.pk).exists())

    def test_model_save_cannot_change_confirmed_source_or_commercial_history(self):
        lpo = self.create_lpo()
        original_rows = list(lpo.parsed_rows)
        lpo.parsed_rows = [{"item": "Different item", "quantity": "99"}]
        lpo.source_sha256 = "b" * 64

        with self.assertRaisesMessage(ValidationError, "source and commercial history is immutable"):
            lpo.save()

        lpo.refresh_from_db()
        self.assertEqual(lpo.parsed_rows, original_rows)
        self.assertEqual(lpo.source_sha256, "a" * 64)

    def test_queryset_status_update_is_all_or_nothing_when_confirmed_lpo_is_selected(self):
        confirmed = self.create_lpo()
        review = self.create_lpo(lpo_status=QuotationLPO.STATUS_PARSED)

        with self.assertRaisesMessage(ValidationError, "cannot be moved back"):
            QuotationLPO.objects.filter(pk__in=[confirmed.pk, review.pk]).update(
                status=QuotationLPO.STATUS_RECEIVED
            )

        confirmed.refresh_from_db()
        review.refresh_from_db()
        self.assertEqual(confirmed.status, QuotationLPO.STATUS_CONFIRMED)
        self.assertEqual(review.status, QuotationLPO.STATUS_PARSED)

    def test_queryset_update_cannot_change_confirmed_source_or_commercial_history(self):
        confirmed = self.create_lpo()
        review = self.create_lpo(lpo_status=QuotationLPO.STATUS_PARSED)

        with self.assertRaisesMessage(ValidationError, "source and commercial history is immutable"):
            QuotationLPO.objects.filter(pk__in=[confirmed.pk, review.pk]).update(
                parsed_rows=[{"item": "Replacement", "quantity": "1"}],
                source_filename="replacement.pdf",
            )

        confirmed.refresh_from_db()
        review.refresh_from_db()
        self.assertEqual(confirmed.parsed_rows, [{"item": "Gloves", "quantity": "10"}])
        self.assertEqual(review.parsed_rows, [{"item": "Gloves", "quantity": "10"}])
        self.assertEqual(confirmed.source_filename, "customer-po.pdf")
        self.assertEqual(review.source_filename, "customer-po.pdf")

    def test_queryset_update_allows_confirmed_reference_correction_only(self):
        lpo = self.create_lpo()

        updated = QuotationLPO.objects.filter(pk=lpo.pk).update(
            lpo_number="LPO-QUERYSET-CORRECTED",
            lpo_date=date(2026, 7, 3),
            notes="Reference checked against original attachment",
            status=QuotationLPO.STATUS_CONFIRMED,
        )

        self.assertEqual(updated, 1)
        lpo.refresh_from_db()
        self.assertEqual(lpo.lpo_number, "LPO-QUERYSET-CORRECTED")
        self.assertEqual(lpo.lpo_date, date(2026, 7, 3))
        self.assertEqual(lpo.notes, "Reference checked against original attachment")
        self.assertEqual(lpo.status, QuotationLPO.STATUS_CONFIRMED)

    def test_bulk_update_cannot_downgrade_confirmed_lpo(self):
        lpo = self.create_lpo()
        lpo.status = QuotationLPO.STATUS_NEEDS_REVIEW

        with self.assertRaisesMessage(ValidationError, "cannot be moved back"):
            QuotationLPO.objects.bulk_update([lpo], ["status"])

        lpo.refresh_from_db()
        self.assertEqual(lpo.status, QuotationLPO.STATUS_CONFIRMED)

    def test_bulk_update_cannot_change_confirmed_source_or_commercial_history(self):
        lpo = self.create_lpo()
        lpo.parsed_meta = {"document_total": "999.00"}
        lpo.received_by = self.admin_user

        with self.assertRaisesMessage(ValidationError, "source and commercial history is immutable"):
            QuotationLPO.objects.bulk_update([lpo], ["parsed_meta", "received_by"])

        lpo.refresh_from_db()
        self.assertEqual(lpo.parsed_meta, {})
        self.assertEqual(lpo.received_by, self.staff)

    def test_bulk_update_allows_status_preserving_reference_correction(self):
        lpo = self.create_lpo()
        lpo.lpo_number = "LPO-BULK-CORRECTED"
        lpo.notes = "Bulk reference correction"

        updated = QuotationLPO.objects.bulk_update(
            [lpo],
            ["lpo_number", "notes", "status"],
        )

        self.assertEqual(updated, 1)
        lpo.refresh_from_db()
        self.assertEqual(lpo.lpo_number, "LPO-BULK-CORRECTED")
        self.assertEqual(lpo.notes, "Bulk reference correction")
        self.assertEqual(lpo.status, QuotationLPO.STATUS_CONFIRMED)

    def test_queryset_delete_is_all_or_nothing_when_confirmed_lpo_is_selected(self):
        confirmed = self.create_lpo()
        review = self.create_lpo(lpo_status=QuotationLPO.STATUS_PARSED)

        with self.assertRaisesMessage(ValidationError, "Confirmed LPO records cannot be deleted"):
            QuotationLPO.objects.filter(pk__in=[confirmed.pk, review.pk]).delete()

        self.assertTrue(QuotationLPO.objects.filter(pk=confirmed.pk).exists())
        self.assertTrue(QuotationLPO.objects.filter(pk=review.pk).exists())

    def test_quotation_model_and_queryset_deletes_cannot_cascade_confirmed_lpo(self):
        lpo = self.create_lpo()

        with self.assertRaisesMessage(ValidationError, "confirmed LPO"):
            self.quotation.delete()
        with self.assertRaisesMessage(ValidationError, "confirmed LPO"):
            Quotation.objects.filter(pk=self.quotation.pk).delete()

        self.assertTrue(Quotation.objects.filter(pk=self.quotation.pk).exists())
        self.assertTrue(QuotationLPO.objects.filter(pk=lpo.pk).exists())

    def test_admin_locks_confirmed_lpo_and_quotation_history(self):
        confirmed = self.create_lpo()
        review = self.create_lpo(lpo_status=QuotationLPO.STATUS_PARSED)
        lpo_admin = QuotationLPOAdmin(QuotationLPO, admin.site)
        quotation_admin = QuotationAdmin(Quotation, admin.site)

        self.assertFalse(lpo_admin.has_change_permission(self.admin_request, confirmed))
        self.assertFalse(lpo_admin.has_delete_permission(self.admin_request, confirmed))
        self.assertTrue(lpo_admin.has_change_permission(self.admin_request, review))
        self.assertTrue(lpo_admin.has_delete_permission(self.admin_request, review))
        self.assertFalse(quotation_admin.has_delete_permission(self.admin_request, self.quotation))

        with self.assertRaisesMessage(PermissionDenied, "Confirmed LPO records cannot be deleted"):
            lpo_admin.delete_queryset(
                self.admin_request,
                QuotationLPO.objects.filter(pk__in=[confirmed.pk, review.pk]),
            )
        with self.assertRaisesMessage(PermissionDenied, "confirmed LPO"):
            quotation_admin.delete_queryset(
                self.admin_request,
                Quotation.objects.filter(pk=self.quotation.pk),
            )

        self.assertTrue(QuotationLPO.objects.filter(pk=confirmed.pk).exists())
        self.assertTrue(QuotationLPO.objects.filter(pk=review.pk).exists())
        self.assertTrue(Quotation.objects.filter(pk=self.quotation.pk).exists())

    def test_mailbox_history_admins_are_view_only_without_bulk_delete(self):
        history_admins = [
            (MailboxPOAuditRunAdmin, MailboxPOAuditRun),
            (MailboxPOAuditFailureAdmin, MailboxPOAuditFailure),
            (MailboxPOMatchRunAdmin, MailboxPOMatchRun),
            (MailboxPOMessageAdmin, MailboxPOMessage),
        ]

        for admin_class, model in history_admins:
            with self.subTest(model=model.__name__):
                model_admin = admin_class(model, admin.site)
                self.assertFalse(model_admin.has_add_permission(self.admin_request))
                self.assertFalse(model_admin.has_change_permission(self.admin_request))
                self.assertFalse(model_admin.has_delete_permission(self.admin_request))
                self.assertNotIn("delete_selected", model_admin.get_actions(self.admin_request))
