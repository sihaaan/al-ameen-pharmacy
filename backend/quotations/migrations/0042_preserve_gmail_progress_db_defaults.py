import copy

from django.db import migrations
from django.db.models.fields import NOT_PROVIDED


PROGRESS_DEFAULT_SQL = {
    "analysis_progress_stage": "''",
    "analysis_progress_attempt": "0",
    "analysis_progress_generation": "''",
    "analysis_progress_error_category": "''",
}


def ensure_gmail_progress_database_defaults(apps, schema_editor):
    """Repair databases that applied the original 0040 without DB defaults."""

    model = apps.get_model("quotations", "GmailInquiryImport")
    table_name = schema_editor.quote_name(model._meta.db_table)
    vendor = schema_editor.connection.vendor

    if vendor == "postgresql":
        clauses = [
            (
                f"ALTER COLUMN {schema_editor.quote_name(column)} "
                f"SET DEFAULT {default_sql}"
            )
            for column, default_sql in PROGRESS_DEFAULT_SQL.items()
        ]
        # One catalog-only ALTER acquires the table lock once and never
        # rewrites existing inquiry/customer rows.
        schema_editor.execute(f"ALTER TABLE {table_name} " + ", ".join(clauses))
        return

    if vendor == "sqlite":
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(f"PRAGMA table_info({table_name})")
            defaults = {row[1]: row[4] for row in cursor.fetchall()}
        missing = [
            column
            for column in PROGRESS_DEFAULT_SQL
            if defaults.get(column) is None
        ]
        if not missing:
            return

        # Altering any missing field rebuilds SQLite's table. The corrected
        # migration state carries db_default on all four fields, so the single
        # rebuild repairs every missing default while preserving constraints,
        # indexes, and row values.
        field = model._meta.get_field(missing[0])
        old_field = copy.copy(field)
        old_field.db_default = NOT_PROVIDED
        old_field.__dict__.pop("_db_default_expression", None)
        schema_editor.alter_field(model, old_field, field, strict=True)
        return

    # Portable fallback for non-production development backends. Each field's
    # current migration state contains the persistent default.
    for column in PROGRESS_DEFAULT_SQL:
        field = model._meta.get_field(column)
        old_field = copy.copy(field)
        old_field.db_default = NOT_PROVIDED
        old_field.__dict__.pop("_db_default_expression", None)
        schema_editor.alter_field(model, old_field, field, strict=True)


class DatabaseDefaultRepair(migrations.RunPython):
    # The repair consists only of schema-editor operations, so sqlmigrate and
    # the PostgreSQL CI contract can render and inspect the exact DDL safely.
    reduces_to_sql = True


class Migration(migrations.Migration):
    dependencies = [
        ("quotations", "0041_gmailinquiryanalysisjob"),
    ]

    operations = [
        DatabaseDefaultRepair(
            ensure_gmail_progress_database_defaults,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
