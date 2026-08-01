import re
from pathlib import Path

from django.test import SimpleTestCase

from quotations.ai_parsing import (
    AI_PARSE_OBSERVABILITY_VERSION,
    MAILBOX_PO_AI_PIPELINE_VERSION,
    MANUAL_AI_PIPELINE_VERSION,
)
from quotations.gmail_inquiry_import import (
    GMAIL_AI_PIPELINE_VERSION,
    GMAIL_AI_SCHEMA_NAME,
    GMAIL_IDENTITY_MATCH_VERSION,
)
from quotations.models import (
    QuotationEmailDeliveryAttemptEvent,
    QuotationEmailOutboundSnapshot,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PRIMARY_DOCUMENTS = (
    "ATTACHMENT_SECURITY_AND_SPREADSHEET_FIDELITY.md",
    "GMAIL_QUOTATION_ARCHITECTURE_REVIEW.md",
    "DEPLOYMENT.md",
    "SECURITY.md",
    "OPERATIONS.md",
    "RELEASE_CONFIGURATION_PACK.md",
    "RELEASE_HANDOFF.md",
    "gmail_addon/README.md",
)


def read_repository_file(relative_path):
    return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")


class DocumentationContractTests(SimpleTestCase):
    def test_primary_documents_have_versioned_provenance(self):
        for relative_path in PRIMARY_DOCUMENTS:
            with self.subTest(document=relative_path):
                content = read_repository_file(relative_path)
                self.assertRegex(content, r"(?im)^\| Document version \| [^|]+ \|$")
                self.assertRegex(content, r"(?im)^\| Status \| [^|]+ \|$")
                self.assertRegex(content, r"(?im)^\| Owner \| [^|]+ \|$")
                self.assertRegex(content, r"(?im)^\| Last verified \| 2026-08-01 \|$")
                self.assertRegex(content, r"(?im)^\| Reviewed code \| [^|]+ \|$")

    def test_task_2_8_documents_name_the_exact_pre_task_checkpoint(self):
        for relative_path in (
            "GMAIL_QUOTATION_ARCHITECTURE_REVIEW.md",
            "DEPLOYMENT.md",
            "SECURITY.md",
            "OPERATIONS.md",
        ):
            with self.subTest(document=relative_path):
                content = read_repository_file(relative_path)
                reviewed_row = re.search(
                    r"(?im)^\| Reviewed code \| ([^|]+) \|$",
                    content,
                )
                self.assertIsNotNone(reviewed_row)
                self.assertIn("`7bc7054`", reviewed_row.group(1))
                self.assertIn("Task 2.8", reviewed_row.group(1))

    def test_architecture_versions_match_runtime_contracts(self):
        content = read_repository_file("GMAIL_QUOTATION_ARCHITECTURE_REVIEW.md")
        expected_versions = (
            GMAIL_AI_PIPELINE_VERSION,
            GMAIL_AI_SCHEMA_NAME,
            GMAIL_IDENTITY_MATCH_VERSION,
            MANUAL_AI_PIPELINE_VERSION,
            MAILBOX_PO_AI_PIPELINE_VERSION,
            AI_PARSE_OBSERVABILITY_VERSION,
            QuotationEmailOutboundSnapshot.CONTRACT_VERSION,
        )
        for version in expected_versions:
            with self.subTest(version=version):
                self.assertIn(f"`{version}`", content)

        headings = re.findall(r"(?m)^## (.+)$", content)
        self.assertEqual(len(headings), len(set(headings)))
        self.assertIn("store=false", content)
        self.assertIn("backward-compatible one-to-one aggregate state", content)
        self.assertIn("`QuotationEmailDeliveryAttempt` child row", content)
        self.assertIn(
            f"append-only `{QuotationEmailDeliveryAttemptEvent.__name__}`",
            content,
        )
        self.assertIn("stale-preview", content)
        self.assertIn("30-case fully synthetic", content)

    def test_operations_document_records_known_live_gaps(self):
        content = read_repository_file("OPERATIONS.md")
        required_phrases = (
            "no Railway pre-deploy command",
            "no configured health check",
            "no Railway volume",
            "ephemeral",
            "Reconciliation must",
            "never sends email",
            "RPO, RTO",
            "no general scheduled purge",
            "no Redis/background worker",
            "`/backend/railway.json`",
            "`MIGRATION_DATABASE_URL`",
            "`database_request_interrupted`",
        )
        for phrase in required_phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, content)

    def test_release_configuration_pack_is_fail_closed_and_operator_ready(self):
        content = read_repository_file("RELEASE_CONFIGURATION_PACK.md")
        required_phrases = (
            "no external setting has been applied",
            "QUOTATION_EVIDENCE_STORAGE_BACKEND",
            "QUOTATION_EVIDENCE_STORAGE_OPTIONS_JSON",
            "exact-key",
            "SHA-256",
            "dual-read",
            "MIGRATION_DATABASE_URL",
            "/backend/railway.json",
            "quotations.0038",
            "GMAIL_ADDON_SHARED_MAILBOX_EMAIL",
            "pharmacydxb@gmail.com",
            "QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED",
            "Keep 0 during this remediation",
            "transfer_shared_gmail_owner",
            "command intentionally refuses both dry-run and",
            "Owner/successor read-only preflight",
            "There is no general scheduled purge",
            "no dedicated public liveness/readiness URL",
            "SENTRY_DSN",
            "There is no scheduler/worker",
            "existing reconciliation action, which searches and never",
            "currently unknown, so it is a merge blocker",
        )
        for phrase in required_phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, content)

        self.assertNotIn("--confirm-mailbox pharmacydxb@gmail.com --apply", content)
        self.assertIn("assert not settings.QUOTATION_GMAIL", content)
        self.assertIn(
            "Do not change the flag or run that",
            content,
        )

    def test_addon_document_separates_manifest_and_website_scopes(self):
        content = read_repository_file("gmail_addon/README.md")
        self.assertIn("`gmail.readonly`, `gmail.send`", content)
        self.assertIn("`gmail.addons.current.message.metadata`", content)
        self.assertIn("consumer Gmail account", content)
        self.assertIn("developer install", content)
        self.assertIn("deployment.template.json", content)
        self.assertNotIn("--deployment-file=deployment.json", content)

    def test_stale_operational_claims_are_not_reintroduced(self):
        joined = "\n".join(
            read_repository_file(path)
            for path in (
                "README.md",
                "DEPLOYMENT.md",
                "SECURITY.md",
                "GMAIL_QUOTATION_ARCHITECTURE_REVIEW.md",
            )
        ).lower()
        forbidden = (
            "smart migration runner",
            "all security measures are in place",
            "fully secured and ready",
            "7-day retention on free tier",
            "$10/month",
            "main production bottleneck",
            "git filter-branch",
        )
        for phrase in forbidden:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, joined)

    def test_environment_template_documents_critical_runtime_controls(self):
        content = read_repository_file("backend/.env.example")
        required_names = (
            "DATABASE_CONNECT_TIMEOUT_SECONDS",
            "DATABASE_DISABLE_SERVER_SIDE_CURSORS",
            "MIGRATION_DATABASE_URL",
            "MIGRATION_CONNECT_TIMEOUT_SECONDS",
            "MIGRATION_LOCK_TIMEOUT_MS",
            "MIGRATION_STATEMENT_TIMEOUT_MS",
            "QUOTATION_PRIVATE_STORAGE_ROOT",
            "QUOTATION_PRIVATE_EVIDENCE_MAX_BYTES",
            "QUOTATION_EVIDENCE_STORAGE_BACKEND",
            "QUOTATION_EVIDENCE_STORAGE_OPTIONS_JSON",
            "WEB_CONCURRENCY",
            "GUNICORN_THREADS",
            "SENTRY_DSN",
            "SENTRY_ENVIRONMENT",
            "SENTRY_TRACES_SAMPLE_RATE",
            "DJANGO_LOG_LEVEL",
            "DJANGO_REQUEST_LOG_LEVEL",
            "QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED",
            "QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED",
        )
        for name in required_names:
            with self.subTest(name=name):
                self.assertIn(name, content)
        self.assertRegex(content, r"(?m)^# CLOUDINARY_URL=cloudinary://")

        attachment_defaults = {
            "QUOTATION_IMPORT_MAX_UPLOAD_BYTES": "5242880",
            "QUOTATION_IMPORT_MAX_EXCEL_ROWS": "500",
            "QUOTATION_IMPORT_MAX_EXCEL_SHEETS": "10",
            "QUOTATION_IMPORT_MAX_EXCEL_COLUMNS": "100",
            "QUOTATION_IMPORT_MAX_PDF_PAGES": "10",
            "QUOTATION_IMPORT_MAX_PDF_OBJECTS": "20000",
            "QUOTATION_IMPORT_MAX_PDF_STREAMS": "5000",
            "QUOTATION_IMPORT_MAX_PDF_DECODED_STREAM_BYTES": "33554432",
            "QUOTATION_IMPORT_MAX_PDF_TOTAL_DECODED_STREAM_BYTES": "67108864",
            "QUOTATION_IMPORT_MAX_PDF_PAGE_DIMENSION_POINTS": "10000",
            "QUOTATION_IMPORT_MAX_PDF_PAGE_AREA_POINTS": "16000000",
            "QUOTATION_IMPORT_MAX_PDF_RENDER_PIXELS": "25000000",
            "QUOTATION_IMPORT_MAX_PDF_IMAGE_PIXELS": "25000000",
            "QUOTATION_IMPORT_MAX_PDF_TEXT_CHARS_PER_PAGE": "250000",
            "QUOTATION_IMPORT_MAX_PDF_TOTAL_TEXT_CHARS": "1000000",
            "QUOTATION_IMPORT_MAX_PDF_WORDS_PER_PAGE": "50000",
            "QUOTATION_IMPORT_MAX_PDF_TOTAL_WORDS": "250000",
            "QUOTATION_IMPORT_MAX_PDF_TABLE_ROWS": "20000",
            "QUOTATION_IMPORT_MAX_PDF_TABLE_CELLS": "100000",
            "QUOTATION_IMPORT_MAX_ARCHIVE_ENTRIES": "2048",
            "QUOTATION_IMPORT_MAX_ARCHIVE_UNCOMPRESSED_BYTES": "134217728",
            "QUOTATION_IMPORT_MAX_ARCHIVE_MEMBER_BYTES": "33554432",
            "QUOTATION_PRICE_REFERENCE_MAX_EXCEL_ROWS": "5000",
        }
        for name, value in attachment_defaults.items():
            with self.subTest(attachment_setting=name):
                self.assertRegex(
                    content,
                    rf"(?m)^# {re.escape(name)}={re.escape(value)}$",
                )

    def test_attachment_security_and_fidelity_contract_is_documented(self):
        relative_path = "ATTACHMENT_SECURITY_AND_SPREADSHEET_FIDELITY.md"
        content = read_repository_file(relative_path)
        lowered = content.lower()

        required_route_phrases = (
            "manual inquiry",
            "quotation lpo/outcome",
            "proforma lpo",
            "gmail native inquiry analysis",
            "price-reference upload",
            "historical quotation pdf",
            "product and quotation-branding images",
        )
        for phrase in required_route_phrases:
            with self.subTest(route=phrase):
                self.assertIn(phrase, lowered)

        required_safety_phrases = (
            "hard failure",
            "warning-only",
            "no malware scanner or antivirus",
            "no parser sandbox",
            "legacy `.xls`",
            "`.xlsb`",
            "blank selling prices",
            "0036_quotationoutcomepoimport_parsed_meta",
            "additive database migration",
            "no production deployment",
            "not item evidence",
        )
        for phrase in required_safety_phrases:
            with self.subTest(safety_claim=phrase):
                self.assertIn(phrase, lowered)

        for setting_name in (
            "QUOTATION_IMPORT_MAX_ARCHIVE_ENTRIES",
            "QUOTATION_IMPORT_MAX_ARCHIVE_UNCOMPRESSED_BYTES",
            "QUOTATION_IMPORT_MAX_ARCHIVE_MEMBER_BYTES",
            "QUOTATION_IMPORT_MAX_PDF_OBJECTS",
            "QUOTATION_IMPORT_MAX_PDF_TOTAL_DECODED_STREAM_BYTES",
            "QUOTATION_IMPORT_MAX_PDF_PAGE_DIMENSION_POINTS",
            "QUOTATION_IMPORT_MAX_PDF_RENDER_PIXELS",
            "QUOTATION_IMPORT_MAX_PDF_TOTAL_TEXT_CHARS",
            "QUOTATION_IMPORT_MAX_PDF_TOTAL_WORDS",
            "QUOTATION_IMPORT_MAX_PDF_TABLE_CELLS",
            "QUOTATION_PRICE_REFERENCE_MAX_EXCEL_ROWS",
            "PRODUCT_IMAGE_MAX_UPLOAD_BYTES",
            "QUOTATION_BRANDING_IMAGE_MAX_UPLOAD_BYTES",
        ):
            with self.subTest(documented_setting=setting_name):
                self.assertIn(f"`{setting_name}`", content)

        self.assertRegex(lowered, r"no\s+oauth scope")
        self.assertIn("ai model/prompt/schema change", lowered)
        self.assertIn("do not reverse `0036`", lowered)
        self.assertIn("0038_ensure_po_import_parsed_meta_db_default", lowered)
        self.assertIn("forward-only", lowered)
        self.assertIn("does not drop the database default", lowered)
        self.assertIn("local page traversal", lowered)
        self.assertIn("inline-image", lowered)
        self.assertIn("rollback", lowered)

        for document in (
            "SECURITY.md",
            "OPERATIONS.md",
            "DEPLOYMENT.md",
            "GMAIL_QUOTATION_ARCHITECTURE_REVIEW.md",
        ):
            with self.subTest(linked_from=document):
                self.assertIn(relative_path, read_repository_file(document))

    def test_legacy_status_documents_are_clearly_marked_historical(self):
        for relative_path in (
            "QUOTATION_MODULE.md",
            "TODO_QUOTATIONS.md",
            "current_status.md",
        ):
            with self.subTest(document=relative_path):
                first_lines = "\n".join(
                    read_repository_file(relative_path).splitlines()[:10]
                ).lower()
                self.assertIn("historical", first_lines)

    def test_relative_markdown_links_resolve(self):
        for relative_path in PRIMARY_DOCUMENTS + ("README.md",):
            content = read_repository_file(relative_path)
            parent = (REPOSITORY_ROOT / relative_path).parent
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", content):
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                clean_target = target.split("#", 1)[0]
                if not clean_target:
                    continue
                with self.subTest(document=relative_path, target=target):
                    self.assertTrue((parent / clean_target).resolve().exists())

    def test_markdown_code_fences_are_balanced(self):
        for relative_path in PRIMARY_DOCUMENTS + ("README.md",):
            with self.subTest(document=relative_path):
                content = read_repository_file(relative_path)
                self.assertEqual(content.count("```") % 2, 0)
