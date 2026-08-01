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
    "GMAIL_QUOTATION_ARCHITECTURE_REVIEW.md",
    "DEPLOYMENT.md",
    "SECURITY.md",
    "OPERATIONS.md",
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
                self.assertIn("`d88b767`", content)

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
        )
        for phrase in required_phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, content)

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
            "QUOTATION_PRIVATE_STORAGE_ROOT",
            "WEB_CONCURRENCY",
            "GUNICORN_THREADS",
            "SENTRY_DSN",
            "SENTRY_ENVIRONMENT",
            "SENTRY_TRACES_SAMPLE_RATE",
            "DJANGO_LOG_LEVEL",
            "DJANGO_REQUEST_LOG_LEVEL",
        )
        for name in required_names:
            with self.subTest(name=name):
                self.assertIn(name, content)
        self.assertRegex(content, r"(?m)^# CLOUDINARY_URL=cloudinary://")

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
