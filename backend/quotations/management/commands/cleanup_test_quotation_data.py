from collections import OrderedDict

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Q

from quotations.models import (
    Company,
    CompanyPriceHistory,
    HistoricalPriceImport,
    HistoricalPriceImportLine,
    Inquiry,
    InquiryLine,
    Quotation,
    QuotationAuditLog,
    QuotationLine,
    QuotationSettings,
    QuoteItem,
)


TEST_COMPANY_IDS = [15, 23]
TEST_QUOTE_ITEM_IDS = [31, 38, 39, 40, 41, 42, 43, 44]
TEST_INQUIRY_IDS = [13, 17, 18]
TEST_QUOTATION_IDS = list(range(15, 29)) + [37, 38]
TEST_PRICE_HISTORY_IDS = [7, 8]
TEST_HISTORICAL_IMPORT_IDS = [1]

PROTECTED_MODEL_NAMES = [
    "api.Product",
    "api.ProductImage",
    "api.Order",
    "auth.User",
    "quotations.QuotationSettings",
]


class Command(BaseCommand):
    help = (
        "Dry-run or delete the accidental production quotation test data created "
        "during local development. The command only targets a fixed allowlist of IDs."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be deleted. This is the default unless --confirm is passed.",
        )
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Actually delete the allowlisted test quotation data in one transaction.",
        )
        parser.add_argument(
            "--i-understand-this-targets-production-test-data",
            action="store_true",
            help="Required with --confirm to acknowledge this deletes allowlisted production test data.",
        )
        parser.add_argument(
            "--allow-non-production-target",
            action="store_true",
            help="Allow --confirm on a non-Neon/non-production-looking database target.",
        )

    def handle(self, *args, **options):
        if options["dry_run"] and options["confirm"]:
            raise CommandError("Use either --dry-run or --confirm, not both.")

        confirmed = options["confirm"]
        explicit_ack = options["i_understand_this_targets_production_test_data"]
        db_info = self.database_info()
        before_counts = self.model_counts()
        plan = self.build_plan()

        self.print_header(db_info, confirmed)
        self.print_counts("Before counts", before_counts)
        self.print_plan(plan)
        self.print_kept_records()
        self.print_missing_ids(plan)

        if not confirmed:
            self.stdout.write(self.style.WARNING("Dry-run only. No rows were deleted."))
            self.print_projected_counts(before_counts, plan)
            return

        if not explicit_ack:
            raise CommandError(
                "--confirm requires --i-understand-this-targets-production-test-data."
            )

        self.validate_confirm_target(db_info, options["allow_non_production_target"])

        with transaction.atomic():
            deleted_summary = self.delete_plan(plan)

        after_counts = self.model_counts()
        self.stdout.write(self.style.SUCCESS("Cleanup confirmed. Rows were deleted transactionally."))
        self.print_delete_summary(deleted_summary)
        self.print_counts("After counts", after_counts)

    def database_info(self):
        db = settings.DATABASES["default"]
        return {
            "engine": db.get("ENGINE", ""),
            "host": db.get("HOST") or "local-file/sqlite",
            "name": str(db.get("NAME") or ""),
            "debug": settings.DEBUG,
        }

    def validate_confirm_target(self, db_info, allow_non_production_target):
        host = (db_info["host"] or "").lower()
        name = (db_info["name"] or "").lower()
        looks_like_neon = host.endswith(".neon.tech")
        looks_like_expected_db = name == "neondb"
        if looks_like_neon and looks_like_expected_db:
            return
        if allow_non_production_target:
            self.stdout.write(
                self.style.WARNING(
                    "--allow-non-production-target was supplied; proceeding against this explicit target."
                )
            )
            return
        raise CommandError(
            "--confirm refused because this database target does not look like the intended Neon production "
            "database. Re-run with --allow-non-production-target only if you intentionally want this target."
        )

    def model_counts(self):
        return OrderedDict(
            [
                ("companies", Company.objects.count()),
                ("quote_items", QuoteItem.objects.count()),
                ("inquiries", Inquiry.objects.count()),
                ("inquiry_lines", InquiryLine.objects.count()),
                ("quotations", Quotation.objects.count()),
                ("quotation_lines", QuotationLine.objects.count()),
                ("historical_imports", HistoricalPriceImport.objects.count()),
                ("historical_import_lines", HistoricalPriceImportLine.objects.count()),
                ("price_history", CompanyPriceHistory.objects.count()),
                ("audit_logs", QuotationAuditLog.objects.count()),
                ("quotation_settings", QuotationSettings.objects.count()),
            ]
        )

    def build_plan(self):
        companies = Company.objects.filter(id__in=TEST_COMPANY_IDS).order_by("id")
        quote_items = QuoteItem.objects.filter(id__in=TEST_QUOTE_ITEM_IDS).order_by("id")
        inquiries = Inquiry.objects.filter(id__in=TEST_INQUIRY_IDS).order_by("id")
        quotations = Quotation.objects.filter(id__in=TEST_QUOTATION_IDS).order_by("id")
        price_history = CompanyPriceHistory.objects.filter(id__in=TEST_PRICE_HISTORY_IDS).order_by("id")
        historical_imports = HistoricalPriceImport.objects.filter(id__in=TEST_HISTORICAL_IMPORT_IDS).order_by("id")

        inquiry_line_ids = list(
            InquiryLine.objects.filter(inquiry_id__in=TEST_INQUIRY_IDS)
            .order_by("id")
            .values_list("id", flat=True)
        )
        quotation_line_ids = list(
            QuotationLine.objects.filter(quotation_id__in=TEST_QUOTATION_IDS)
            .order_by("id")
            .values_list("id", flat=True)
        )
        historical_import_line_ids = list(
            HistoricalPriceImportLine.objects.filter(historical_import_id__in=TEST_HISTORICAL_IMPORT_IDS)
            .order_by("id")
            .values_list("id", flat=True)
        )

        audit_filter = (
            Q(target_type="Company", target_id__in=TEST_COMPANY_IDS)
            | Q(target_type="QuoteItem", target_id__in=TEST_QUOTE_ITEM_IDS)
            | Q(target_type="Inquiry", target_id__in=TEST_INQUIRY_IDS)
            | Q(target_type="InquiryLine", target_id__in=inquiry_line_ids)
            | Q(target_type="Quotation", target_id__in=TEST_QUOTATION_IDS)
            | Q(target_type="QuotationLine", target_id__in=quotation_line_ids)
            | Q(target_type="HistoricalPriceImport", target_id__in=TEST_HISTORICAL_IMPORT_IDS)
            | Q(target_type="HistoricalPriceImportLine", target_id__in=historical_import_line_ids)
            | Q(company_id__in=TEST_COMPANY_IDS)
            | Q(quotation_id__in=TEST_QUOTATION_IDS)
        )
        audit_logs = QuotationAuditLog.objects.filter(audit_filter).order_by("id")

        return OrderedDict(
            [
                ("price_history", price_history),
                ("audit_logs", audit_logs),
                ("historical_import_lines", HistoricalPriceImportLine.objects.filter(id__in=historical_import_line_ids).order_by("id")),
                ("historical_imports", historical_imports),
                ("quotation_lines", QuotationLine.objects.filter(id__in=quotation_line_ids).order_by("id")),
                ("quotations", quotations),
                ("inquiry_lines", InquiryLine.objects.filter(id__in=inquiry_line_ids).order_by("id")),
                ("inquiries", inquiries),
                ("quote_items", quote_items),
                ("companies", companies),
            ]
        )

    def print_header(self, db_info, confirmed):
        mode = "CONFIRM DELETE" if confirmed else "DRY RUN"
        self.stdout.write(self.style.WARNING(f"Mode: {mode}"))
        self.stdout.write(
            "Database target: "
            f"engine={db_info['engine']} host={db_info['host']} name={db_info['name']} DEBUG={db_info['debug']}"
        )

    def print_counts(self, title, counts):
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(title))
        for label, count in counts.items():
            self.stdout.write(f"  {label}: {count}")

    def print_plan(self, plan):
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Allowlisted records targeted for deletion"))
        for label, queryset in plan.items():
            rows = list(queryset)
            self.stdout.write(f"  {label}: {len(rows)}")
            for row in rows:
                self.stdout.write(f"    - {self.describe_row(row)}")

    def print_kept_records(self):
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Records explicitly kept"))
        for model_name in PROTECTED_MODEL_NAMES:
            self.stdout.write(f"  - {model_name}")
        self.stdout.write("  - Any quotation records outside the fixed test ID allowlist")

    def print_missing_ids(self, plan):
        present = {
            "companies": set(plan["companies"].values_list("id", flat=True)),
            "quote_items": set(plan["quote_items"].values_list("id", flat=True)),
            "inquiries": set(plan["inquiries"].values_list("id", flat=True)),
            "quotations": set(plan["quotations"].values_list("id", flat=True)),
            "price_history": set(plan["price_history"].values_list("id", flat=True)),
            "historical_imports": set(plan["historical_imports"].values_list("id", flat=True)),
        }
        expected = {
            "companies": set(TEST_COMPANY_IDS),
            "quote_items": set(TEST_QUOTE_ITEM_IDS),
            "inquiries": set(TEST_INQUIRY_IDS),
            "quotations": set(TEST_QUOTATION_IDS),
            "price_history": set(TEST_PRICE_HISTORY_IDS),
            "historical_imports": set(TEST_HISTORICAL_IMPORT_IDS),
        }
        missing = {label: sorted(expected[label] - present[label]) for label in expected}
        missing = {label: ids for label, ids in missing.items() if ids}
        if not missing:
            self.stdout.write("")
            self.stdout.write("Missing allowlisted IDs: none")
            return
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("Missing allowlisted IDs"))
        for label, ids in missing.items():
            self.stdout.write(f"  {label}: {ids}")

    def print_projected_counts(self, before_counts, plan):
        projected = before_counts.copy()
        for label, queryset in plan.items():
            if label in projected:
                projected[label] = max(0, projected[label] - queryset.count())
        self.print_counts("Projected counts after --confirm", projected)

    def delete_plan(self, plan):
        deleted_summary = OrderedDict()
        for label, queryset in plan.items():
            count = queryset.count()
            queryset.delete()
            deleted_summary[label] = count
        return deleted_summary

    def print_delete_summary(self, deleted_summary):
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Deleted rows"))
        for label, count in deleted_summary.items():
            self.stdout.write(f"  {label}: {count}")

    def describe_row(self, row):
        if isinstance(row, Company):
            return f"Company id={row.id} name={row.name}"
        if isinstance(row, QuoteItem):
            return f"QuoteItem id={row.id} name={row.name}"
        if isinstance(row, Inquiry):
            return (
                f"Inquiry id={row.id} company_id={row.company_id} source_type={row.source_type} "
                f"filename={row.source_filename or '-'}"
            )
        if isinstance(row, InquiryLine):
            return f"InquiryLine id={row.id} inquiry_id={row.inquiry_id} raw_name={row.raw_name}"
        if isinstance(row, Quotation):
            return f"Quotation id={row.id} number={row.quotation_number} company_id={row.company_id} status={row.status}"
        if isinstance(row, QuotationLine):
            return f"QuotationLine id={row.id} quotation_id={row.quotation_id} item={row.item_name_snapshot}"
        if isinstance(row, CompanyPriceHistory):
            return (
                f"CompanyPriceHistory id={row.id} company_id={row.company_id} "
                f"quote_item_id={row.quote_item_id} quotation_id={row.quotation_id}"
            )
        if isinstance(row, HistoricalPriceImport):
            return f"HistoricalPriceImport id={row.id} filename={row.source_filename} status={row.status}"
        if isinstance(row, HistoricalPriceImportLine):
            return (
                f"HistoricalPriceImportLine id={row.id} import_id={row.historical_import_id} "
                f"item={row.item_name} status={row.status}"
            )
        if isinstance(row, QuotationAuditLog):
            return (
                f"QuotationAuditLog id={row.id} action={row.action} target={row.target_type}:{row.target_id} "
                f"company_id={row.company_id or '-'} quotation_id={row.quotation_id or '-'}"
            )
        return f"{row.__class__.__name__} id={row.pk}"
