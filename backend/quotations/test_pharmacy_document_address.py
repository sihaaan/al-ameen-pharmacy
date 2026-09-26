from importlib import import_module
from types import SimpleNamespace

from django.apps import apps
from django.db import connection
from django.test import TestCase

from .models import Company, QuotationSettings, TaxInvoice
from .pdf_config import get_quotation_pdf_config


address_migration = import_module("quotations.migrations.0054_pharmacy_document_address")


class PharmacyDocumentAddressTests(TestCase):
    def migrate_address(self):
        address_migration.update_placeholder_address(apps, SimpleNamespace(connection=connection))

    def test_migration_updates_placeholder_without_rewriting_issued_invoice(self):
        settings = QuotationSettings.objects.create(pk=1, address="Dubai, United Arab Emirates")
        invoice = TaxInvoice.objects.create(
            company=Company.objects.create(name="Customer"),
            invoice_number="ADDRESS-TEST", status="issued", customer_name="Customer",
            supplier_snapshot={"address": settings.address}, issued_pdf=b"original issued invoice",
        )
        for previous in ["Dubai, United Arab Emirates", "Dubai, UAE", "", "  DUBAI, UAE  "]:
            settings.address = previous
            settings.save(update_fields=["address"])
            self.migrate_address()
            settings.refresh_from_db()
            self.assertEqual(settings.address, address_migration.PHARMACY_ADDRESS)
            self.assertEqual(get_quotation_pdf_config().address, settings.address)
        invoice.refresh_from_db()
        self.assertEqual(invoice.supplier_snapshot, {"address": "Dubai, United Arab Emirates"})
        self.assertEqual(bytes(invoice.issued_pdf), b"original issued invoice")

    def test_migration_preserves_custom_address_and_new_settings_use_full_address(self):
        self.migrate_address()  # No settings record yet.
        self.assertEqual(get_quotation_pdf_config().address, address_migration.PHARMACY_ADDRESS)
        settings = QuotationSettings.objects.create(pk=1)
        self.assertEqual(settings.address, address_migration.PHARMACY_ADDRESS)
        settings.address = "Office 12, a separately maintained address"
        settings.save(update_fields=["address"])
        self.migrate_address()
        settings.refresh_from_db()
        self.assertEqual(settings.address, "Office 12, a separately maintained address")
