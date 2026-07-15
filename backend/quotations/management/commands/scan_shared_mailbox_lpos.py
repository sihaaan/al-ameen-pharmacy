"""Backward-compatible name for the mailbox-wide LPO audit command."""

import argparse

from .audit_shared_mailbox_lpos import Command as MailboxAuditCommand


class Command(MailboxAuditCommand):
    help = (
        "Compatibility alias for audit_shared_mailbox_lpos. The old capped per-quotation "
        "search has been replaced by a resumable mailbox-wide inventory."
    )

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument("--quote-limit", type=int, help=argparse.SUPPRESS)
        parser.add_argument("--message-limit", type=int, help=argparse.SUPPRESS)
        parser.add_argument("--rescan-hours", type=float, help=argparse.SUPPRESS)
