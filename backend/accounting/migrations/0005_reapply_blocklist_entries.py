from django.db import migrations
from django.utils import timezone
import re


GENERIC_SINGLE_TOKENS = {
    "branch",
    "card",
    "cash",
    "clinic",
    "company",
    "credit",
    "customer",
    "hospital",
    "insurance",
    "misc",
    "pharmacy",
    "school",
}
BUSINESS_SUFFIX_TOKENS = {
    "co",
    "company",
    "est",
    "establishment",
    "llc",
    "limited",
    "ltd",
}


def normalized_tokens(value):
    return re.findall(r"[a-z0-9]+", value or "")


def blocklist_match_tokens(normalized_name):
    tokens = normalized_tokens(normalized_name)
    while tokens and tokens[-1] in BUSINESS_SUFFIX_TOKENS:
        tokens.pop()
    return tokens


def blocklist_name_matches(customer_normalized_name, blocklist_normalized_name):
    if not customer_normalized_name or not blocklist_normalized_name:
        return False
    if customer_normalized_name == blocklist_normalized_name:
        return True

    customer_tokens = normalized_tokens(customer_normalized_name)
    blocklist_tokens = blocklist_match_tokens(blocklist_normalized_name)
    if not customer_tokens or not blocklist_tokens:
        return False

    if len(blocklist_tokens) == 1:
        token = blocklist_tokens[0]
        return len(token) >= 5 and token not in GENERIC_SINGLE_TOKENS and token in customer_tokens

    return " ".join(blocklist_tokens) in " ".join(customer_tokens)


def reapply_blocklist_entries(apps, schema_editor):
    AccountCustomer = apps.get_model("accounting", "AccountCustomer")
    AccountingBlocklistedCustomer = apps.get_model("accounting", "AccountingBlocklistedCustomer")
    AccountingImport = apps.get_model("accounting", "AccountingImport")
    AccountingImportCustomer = apps.get_model("accounting", "AccountingImportCustomer")

    active_blocklist_names = list(
        AccountingBlocklistedCustomer.objects.filter(is_active=True).values_list("normalized_name", flat=True)
    )
    if not active_blocklist_names:
        return

    matched_customer_ids = [
        customer.id
        for customer in AccountCustomer.objects.filter(is_active=True).only("id", "normalized_name")
        if any(blocklist_name_matches(customer.normalized_name, blocklist_name) for blocklist_name in active_blocklist_names)
    ]
    if not matched_customer_ids:
        return

    now = timezone.now()
    AccountCustomer.objects.filter(id__in=matched_customer_ids).update(is_ignored=True, updated_at=now)
    summaries = AccountingImportCustomer.objects.filter(customer_id__in=matched_customer_ids)
    affected_import_ids = list(summaries.values_list("accounting_import_id", flat=True).distinct())
    summaries.update(is_ignored=True, is_due=False, status="ignored", updated_at=now)

    for import_record in AccountingImport.objects.filter(id__in=affected_import_ids):
        summaries_for_import = AccountingImportCustomer.objects.filter(accounting_import=import_record)
        due_count = summaries_for_import.filter(is_due=True, is_ignored=False).count()
        import_record.customer_count = summaries_for_import.count()
        import_record.due_customer_count = due_count
        import_record.generated_statement_count = due_count
        import_record.updated_at = now
        import_record.save(update_fields=["customer_count", "due_customer_count", "generated_statement_count", "updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0004_accountingblocklistedcustomer"),
    ]

    operations = [
        migrations.RunPython(reapply_blocklist_entries, migrations.RunPython.noop),
    ]
