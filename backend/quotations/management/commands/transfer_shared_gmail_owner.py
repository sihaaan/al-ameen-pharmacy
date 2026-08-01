import json

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from quotations.contract_intelligence import (
    transfer_shared_gmail_credential_owner,
)


class Command(BaseCommand):
    help = (
        "Dry-run a transfer of the designated Gmail credential owner. "
        "Use --apply only after verifying the configured mailbox and both users. "
        "Shell access is the trusted security boundary; --initiated-by is "
        "validated as an active superuser and recorded for audit attribution."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--initiated-by",
            required=True,
            metavar="USERNAME",
            help="Existing active superuser recorded as the operator.",
        )
        parser.add_argument(
            "--new-owner",
            required=True,
            metavar="USERNAME",
            help="Existing active staff user who will own the credential row.",
        )
        parser.add_argument(
            "--confirm-mailbox",
            required=True,
            metavar="EMAIL",
            help="Exact configured designated mailbox address.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply the transfer. Without this switch the command is read-only.",
        )

    def handle(self, *args, **options):
        User = get_user_model()
        username_field = User.USERNAME_FIELD
        try:
            initiated_by = User.objects.get(
                **{username_field: options["initiated_by"]}
            )
        except User.DoesNotExist as exc:
            raise CommandError("The requested initiating user does not exist.") from exc
        try:
            new_owner = User.objects.get(
                **{username_field: options["new_owner"]}
            )
        except User.DoesNotExist as exc:
            raise CommandError("The requested destination owner does not exist.") from exc

        try:
            result = transfer_shared_gmail_credential_owner(
                initiated_by=initiated_by,
                new_owner=new_owner,
                confirmed_mailbox=options["confirm_mailbox"],
                apply=bool(options["apply"]),
            )
        except (PermissionError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(json.dumps(result, sort_keys=True))
        if result["applied"]:
            self.stdout.write(
                self.style.SUCCESS(
                    "Designated Gmail credential ownership transferred."
                )
            )
        elif result["previous_owner"]["id"] == result["new_owner"]["id"]:
            self.stdout.write(
                self.style.WARNING(
                    "No change: the requested destination already owns the credential."
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "DRY RUN ONLY: no database row changed. Re-run with --apply after verification."
                )
            )
