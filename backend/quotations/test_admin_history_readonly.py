from django.contrib import admin
from django.contrib.auth.models import Permission, User
from django.contrib.contenttypes.models import ContentType
from django.test import RequestFactory, TestCase
from django.urls import reverse

from .models import (
    AIParseLog,
    AIParseCache,
    CompanyPriceHistory,
    GmailInquiryImport,
    GmailOAuthConnection,
    MailboxPOAuditFailure,
    MailboxPOAuditRun,
    MailboxPOMatchRun,
    MailboxPOMessage,
    Quotation,
    QuotationAuditLog,
    QuotationEmailDelivery,
    QuotationEmailDeliveryAttempt,
    QuotationEmailDeliveryAttemptEvent,
    QuotationEmailOutboundSnapshot,
    QuotationPOEvidence,
)


READ_ONLY_ADMIN_MODELS = (
    GmailInquiryImport,
    MailboxPOAuditRun,
    MailboxPOAuditFailure,
    MailboxPOMatchRun,
    MailboxPOMessage,
    AIParseLog,
    CompanyPriceHistory,
    QuotationAuditLog,
    QuotationPOEvidence,
    QuotationEmailDelivery,
    QuotationEmailOutboundSnapshot,
    QuotationEmailDeliveryAttempt,
    QuotationEmailDeliveryAttemptEvent,
)


class AuditHistoryAdminReadOnlyTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="history-admin",
            email="history-admin@example.test",
            password="test-password",
        )
        self.request = RequestFactory().get("/admin/quotations/")
        self.request.user = self.superuser

    def test_registered_audit_and_history_admins_are_view_only(self):
        for model in READ_ONLY_ADMIN_MODELS:
            with self.subTest(model=model.__name__):
                model_admin = admin.site._registry[model]
                self.assertTrue(model_admin.has_view_permission(self.request))
                self.assertFalse(model_admin.has_add_permission(self.request))
                self.assertFalse(model_admin.has_change_permission(self.request))
                self.assertFalse(model_admin.has_delete_permission(self.request))
                self.assertFalse(model_admin.has_change_permission(self.request, model()))
                self.assertFalse(model_admin.has_delete_permission(self.request, model()))
                self.assertNotIn("delete_selected", model_admin.get_actions(self.request))

                concrete_fields = {field.name for field in model._meta.fields}
                readonly_fields = set(model_admin.get_readonly_fields(self.request))
                excluded_fields = set(model_admin.exclude or ())
                self.assertTrue(
                    (concrete_fields - excluded_fields).issubset(readonly_fields)
                )

    def test_audit_log_change_page_is_viewable_but_mutations_are_forbidden(self):
        entry = QuotationAuditLog.objects.create(
            actor=self.superuser,
            action=QuotationAuditLog.ACTION_CREATED,
            target_type="Quotation",
            target_id=123,
            message="Immutable audit entry",
        )
        self.client.force_login(self.superuser)

        changelist_url = reverse("admin:quotations_quotationauditlog_changelist")
        change_url = reverse("admin:quotations_quotationauditlog_change", args=[entry.pk])
        add_url = reverse("admin:quotations_quotationauditlog_add")
        delete_url = reverse("admin:quotations_quotationauditlog_delete", args=[entry.pk])

        self.assertEqual(self.client.get(changelist_url).status_code, 200)
        self.assertEqual(self.client.get(change_url).status_code, 200)
        self.assertEqual(self.client.get(add_url).status_code, 403)
        self.assertEqual(self.client.post(add_url, {}).status_code, 403)
        self.assertEqual(self.client.post(change_url, {"message": "tampered"}).status_code, 403)
        self.assertEqual(self.client.get(delete_url).status_code, 403)
        self.assertEqual(self.client.post(delete_url, {"post": "yes"}).status_code, 403)

        entry.refresh_from_db()
        self.assertEqual(entry.message, "Immutable audit entry")

    def test_view_only_staff_cannot_inspect_owner_audit_history(self):
        viewer = User.objects.create_user(
            username="history-viewer",
            password="test-password",
            is_staff=True,
        )
        content_type = ContentType.objects.get_for_model(QuotationAuditLog)
        viewer.user_permissions.add(
            Permission.objects.get(
                content_type=content_type,
                codename="view_quotationauditlog",
            )
        )
        request = RequestFactory().get("/admin/quotations/quotationauditlog/")
        request.user = viewer
        model_admin = admin.site._registry[QuotationAuditLog]

        self.assertFalse(model_admin.has_module_permission(request))
        self.assertFalse(model_admin.has_view_permission(request))
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))

    def test_unprivileged_staff_cannot_view_or_mutate_audit_history(self):
        staff = User.objects.create_user(
            username="history-no-access",
            password="test-password",
            is_staff=True,
        )
        request = RequestFactory().get("/admin/quotations/quotationauditlog/")
        request.user = staff
        model_admin = admin.site._registry[QuotationAuditLog]

        self.assertFalse(model_admin.has_view_permission(request))
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))

    def test_mailbox_audit_admin_is_limited_to_superusers(self):
        owner = User.objects.create_user(
            username="mailbox-admin-owner",
            password="test-password",
            is_staff=True,
        )
        employee = User.objects.create_user(
            username="mailbox-admin-employee",
            password="test-password",
            is_staff=True,
        )
        GmailOAuthConnection.objects.create(
            user=owner,
            is_shared=True,
            email="shared-mailbox@example.test",
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )

        mailbox_models = (
            MailboxPOAuditRun,
            MailboxPOAuditFailure,
            MailboxPOMatchRun,
            MailboxPOMessage,
            QuotationPOEvidence,
            QuotationAuditLog,
        )
        for model in mailbox_models:
            content_type = ContentType.objects.get_for_model(model)
            view_permission = Permission.objects.get(
                content_type=content_type,
                codename=f"view_{model._meta.model_name}",
            )
            owner.user_permissions.add(view_permission)
            employee.user_permissions.add(view_permission)

        # Refresh both users once after assigning every permission so Django's
        # per-instance permission cache cannot retain a partial loop result.
        owner = User.objects.get(pk=owner.pk)
        employee = User.objects.get(pk=employee.pk)
        for model in mailbox_models:
            model_admin = admin.site._registry[model]
            superuser_request = RequestFactory().get("/admin/quotations/")
            superuser_request.user = self.superuser
            owner_request = RequestFactory().get("/admin/quotations/")
            owner_request.user = owner
            employee_request = RequestFactory().get("/admin/quotations/")
            employee_request.user = employee

            with self.subTest(model=model.__name__):
                self.assertTrue(model_admin.has_module_permission(superuser_request))
                self.assertTrue(model_admin.has_view_permission(superuser_request))
                self.assertFalse(model_admin.has_module_permission(owner_request))
                self.assertFalse(model_admin.has_view_permission(owner_request))
                self.assertFalse(model_admin.has_module_permission(employee_request))
                self.assertFalse(model_admin.has_view_permission(employee_request))

    def test_quotation_admin_hides_mailbox_scan_fields_from_non_superusers(self):
        employee = User.objects.create_user(
            username="quotation-admin-employee",
            password="test-password",
            is_staff=True,
        )
        model_admin = admin.site._registry[Quotation]
        operator_request = RequestFactory().get("/admin/quotations/quotation/")
        operator_request.user = self.superuser
        employee_request = RequestFactory().get("/admin/quotations/quotation/")
        employee_request.user = employee
        audit_fields = {
            "po_evidence_last_scanned_at",
            "po_evidence_last_scan_count",
            "po_evidence_last_scan_error",
        }

        self.assertTrue(audit_fields.issubset(set(model_admin.get_fields(operator_request))))
        self.assertTrue(audit_fields.isdisjoint(set(model_admin.get_fields(employee_request))))

    def test_ai_parse_cache_keeps_superuser_delete_for_cache_invalidation(self):
        model_admin = admin.site._registry[AIParseCache]

        self.assertTrue(model_admin.has_view_permission(self.request))
        self.assertFalse(model_admin.has_add_permission(self.request))
        self.assertFalse(model_admin.has_change_permission(self.request))
        self.assertTrue(model_admin.has_delete_permission(self.request))
        self.assertIn("delete_selected", model_admin.get_actions(self.request))

    def test_exact_outbound_mime_is_not_rendered_in_admin(self):
        model_admin = admin.site._registry[QuotationEmailOutboundSnapshot]

        self.assertIn("raw_mime", model_admin.exclude)
        self.assertNotIn("raw_mime", model_admin.get_fields(self.request))
        deferred_fields, is_deferred = model_admin.get_queryset(
            self.request
        ).query.deferred_loading
        self.assertTrue(is_deferred)
        self.assertIn("raw_mime", deferred_fields)
