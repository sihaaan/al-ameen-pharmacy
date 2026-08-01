import copy

from django.db import migrations
from django.db.models.fields import NOT_PROVIDED


def ensure_parsed_meta_database_default(apps, schema_editor):
    """Repair targets that applied the original 0036 without a DB default."""

    model = apps.get_model("quotations", "QuotationOutcomePOImport")
    field = model._meta.get_field("parsed_meta")
    table_name = schema_editor.quote_name(model._meta.db_table)
    column_name = schema_editor.quote_name(field.column)
    vendor = schema_editor.connection.vendor

    if vendor == "postgresql":
        # PostgreSQL JSONB needs an explicitly typed literal. SET DEFAULT is a
        # catalog-only change and does not rewrite existing customer rows.
        schema_editor.execute(
            f"ALTER TABLE {table_name} ALTER COLUMN {column_name} "
            "SET DEFAULT '{}'::jsonb"
        )
        return

    if vendor == "sqlite":
        # Fresh databases already received the persistent default from the
        # corrected 0036. Only rebuild an older local/test table when the
        # original 0036 was applied and subsequently recorded without it.
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(f"PRAGMA table_info({table_name})")
            column = next(
                (row for row in cursor.fetchall() if row[1] == field.column),
                None,
            )
        if column is None:
            raise RuntimeError(f"Missing expected column {model._meta.db_table}.{field.column}")
        if column[4] is not None:
            return

    # Django's schema editor supplies the portable repair for SQLite and any
    # non-PostgreSQL development backend. The logical row values are preserved.
    old_field = copy.copy(field)
    old_field.db_default = NOT_PROVIDED
    old_field.__dict__.pop("_db_default_expression", None)
    schema_editor.alter_field(model, old_field, field, strict=True)


class Migration(migrations.Migration):
    dependencies = [
        ("quotations", "0037_alter_gmailoauthconnection_user"),
    ]

    operations = [
        migrations.RunPython(
            ensure_parsed_meta_database_default,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
