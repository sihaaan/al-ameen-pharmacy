import json
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from quotations.contract_intelligence import resolve_gmail_connection
from quotations.quote_po_intelligence import scan_quote_po_evidence_batch


class Command(BaseCommand):
    help = (
        "Discover review-only PO/LPO email candidates from the designated shared Gmail mailbox. "
        "This command never creates outcomes or confirms LPOs."
    )

    def add_arguments(self, parser):
        parser.add_argument("--quote-limit", type=int, default=10)
        parser.add_argument("--message-limit", type=int, default=10)
        parser.add_argument(
            "--rescan-hours",
            type=float,
            default=6.0,
            help="Only rescan quotations whose previous scan is at least this many hours old.",
        )

    def handle(self, *args, **options):
        connection = resolve_gmail_connection(None, shared_only=True)
        if not connection:
            raise CommandError("No connected shared Gmail mailbox is configured.")
        hours = max(float(options["rescan_hours"] or 0), 0.25)
        cutoff = timezone.now() - timedelta(hours=hours)
        result = scan_quote_po_evidence_batch(
            connection.user,
            quote_limit=options["quote_limit"],
            message_limit=options["message_limit"],
            rescan=True,
            rescan_before=cutoff,
        )
        self.stdout.write(json.dumps(result, default=str, sort_keys=True))
        if result.get("errors"):
            self.stderr.write(
                self.style.WARNING(
                    f"Completed with {len(result['errors'])} quotation scan error(s); candidates remain review-only."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Scanned {result['processed']} quotation(s); found {result['candidates_found']} review candidate(s)."
                )
            )
