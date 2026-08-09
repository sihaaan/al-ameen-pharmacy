import copy
from unittest import skipUnless

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models.fields import NOT_PROVIDED
from django.test import TransactionTestCase


APP_LABEL = "quotations"
MIGRATION_0039 = (APP_LABEL, "0039_gmailworkflowmetric")
MIGRATION_0040 = (
    APP_LABEL,
    "0040_gmailinquiryimport_analysis_progress",
)
MIGRATION_0041 = (APP_LABEL, "0041_gmailinquiryanalysisjob")
MIGRATION_0042 = (
    APP_LABEL,
    "0042_preserve_gmail_progress_db_defaults",
)
TABLE_NAME = "quotations_gmailinquiryimport"
PROGRESS_DEFAULTS = {
    "analysis_progress_stage": "",
    "analysis_progress_attempt": 0,
    "analysis_progress_generation": "",
    "analysis_progress_error_category": "",
}


class GmailProgressMigrationCompatibilityTests(TransactionTestCase):
    """Verify fresh, already-applied, and reverse compatibility paths."""

    reset_sequences = True

    def setUp(self):
        super().setUp()
        self.addCleanup(self._restore_latest_schema)
        executor = MigrationExecutor(connection)
        executor.migrate([MIGRATION_0039])
        self.old_apps = executor.loader.project_state([MIGRATION_0039]).apps
        old_import_model = self.old_apps.get_model(APP_LABEL, "GmailInquiryImport")
        self.existing_import_id = old_import_model.objects.create(
            anchor_message_id=f"existing-{self._testMethodName}",
        ).pk

    @staticmethod
    def _restore_latest_schema():
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    @staticmethod
    def _database_defaults():
        columns = tuple(PROGRESS_DEFAULTS)
        with connection.cursor() as cursor:
            if connection.vendor == "sqlite":
                cursor.execute(f'PRAGMA table_info("{TABLE_NAME}")')
                rows = {row[1]: row[4] for row in cursor.fetchall()}
                return {column: rows[column] for column in columns}
            if connection.vendor == "postgresql":
                cursor.execute(
                    """
                    SELECT attribute.attname,
                           pg_get_expr(default_value.adbin, default_value.adrelid)
                    FROM pg_attribute AS attribute
                    JOIN pg_class AS relation ON relation.oid = attribute.attrelid
                    JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                    LEFT JOIN pg_attrdef AS default_value
                      ON default_value.adrelid = relation.oid
                     AND default_value.adnum = attribute.attnum
                    WHERE namespace.nspname = current_schema()
                      AND relation.relname = %s
                      AND attribute.attname = ANY(%s)
                    ORDER BY attribute.attname
                    """,
                    [TABLE_NAME, list(columns)],
                )
                rows = dict(cursor.fetchall())
                return {column: rows[column] for column in columns}
        return {column: None for column in columns}

    @staticmethod
    def _remove_database_defaults(apps):
        model = apps.get_model(APP_LABEL, "GmailInquiryImport")
        table_name = connection.ops.quote_name(model._meta.db_table)
        fields = [model._meta.get_field(name) for name in PROGRESS_DEFAULTS]

        if connection.vendor == "postgresql":
            clauses = [
                f"ALTER COLUMN {connection.ops.quote_name(field.column)} DROP DEFAULT"
                for field in fields
            ]
            with connection.cursor() as cursor:
                cursor.execute(f"ALTER TABLE {table_name} " + ", ".join(clauses))
            return

        if connection.vendor == "sqlite":
            alter_fields = []
            for field in fields:
                field_without_default = copy.copy(field)
                field_without_default.db_default = NOT_PROVIDED
                field_without_default.__dict__.pop("_db_default_expression", None)
                alter_fields.append((field, field_without_default))
            with connection.schema_editor() as schema_editor:
                schema_editor._remake_table(model, alter_fields=alter_fields)
            return

        with connection.schema_editor() as schema_editor:
            for field in fields:
                field_without_default = copy.copy(field)
                field_without_default.db_default = NOT_PROVIDED
                field_without_default.__dict__.pop("_db_default_expression", None)
                schema_editor.alter_field(
                    model,
                    field,
                    field_without_default,
                    strict=True,
                )

    def _assert_persistent_defaults(self):
        defaults = self._database_defaults()
        for column in PROGRESS_DEFAULTS:
            self.assertIsNotNone(defaults[column], column)
        self.assertIn("0", str(defaults["analysis_progress_attempt"]))

    def _assert_old_and_new_writes(self, apps):
        old_import_model = self.old_apps.get_model(APP_LABEL, "GmailInquiryImport")
        new_import_model = apps.get_model(APP_LABEL, "GmailInquiryImport")

        existing = new_import_model.objects.get(pk=self.existing_import_id)
        for field_name, expected in PROGRESS_DEFAULTS.items():
            self.assertEqual(getattr(existing, field_name), expected)

        # The historical model has no progress fields and therefore emits the
        # same INSERT shape as the application deployed before PR #2.
        old_insert = old_import_model.objects.create(
            anchor_message_id=f"old-code-{self._testMethodName}",
        )
        stored_old_insert = new_import_model.objects.get(pk=old_insert.pk)
        for field_name, expected in PROGRESS_DEFAULTS.items():
            self.assertEqual(getattr(stored_old_insert, field_name), expected)

        new_insert = new_import_model.objects.create(
            anchor_message_id=f"new-code-{self._testMethodName}",
            analysis_progress_stage="completed",
            analysis_progress_attempt=7,
            analysis_progress_generation="a" * 32,
            analysis_progress_error_category="",
        )
        stored_new_insert = new_import_model.objects.get(pk=new_insert.pk)
        self.assertEqual(stored_new_insert.analysis_progress_stage, "completed")
        self.assertEqual(stored_new_insert.analysis_progress_attempt, 7)
        self.assertEqual(stored_new_insert.analysis_progress_generation, "a" * 32)

        defaulted_insert = new_import_model.objects.create(
            anchor_message_id=f"new-defaults-{self._testMethodName}",
        )
        stored_defaulted_insert = new_import_model.objects.get(pk=defaulted_insert.pk)
        for field_name, expected in PROGRESS_DEFAULTS.items():
            self.assertEqual(getattr(stored_defaulted_insert, field_name), expected)

    def test_fresh_corrected_0040_has_defaults_before_0042(self):
        executor = MigrationExecutor(connection)
        executor.migrate([MIGRATION_0040])
        apps = executor.loader.project_state([MIGRATION_0040]).apps

        self._assert_persistent_defaults()
        self._assert_old_and_new_writes(apps)

        executor = MigrationExecutor(connection)
        executor.migrate([MIGRATION_0041])
        apps = executor.loader.project_state([MIGRATION_0041]).apps

        self._assert_persistent_defaults()
        self._assert_old_and_new_writes(apps)

    def test_0042_repairs_an_already_applied_original_0040(self):
        executor = MigrationExecutor(connection)
        executor.migrate([MIGRATION_0041])
        apps = executor.loader.project_state([MIGRATION_0041]).apps
        self._remove_database_defaults(apps)
        self.assertTrue(
            all(value is None for value in self._database_defaults().values())
        )

        executor = MigrationExecutor(connection)
        executor.migrate([MIGRATION_0042])
        apps = executor.loader.project_state([MIGRATION_0042]).apps

        self._assert_persistent_defaults()
        self._assert_old_and_new_writes(apps)

    def test_reversing_0042_preserves_database_defaults_for_old_code(self):
        executor = MigrationExecutor(connection)
        executor.migrate([MIGRATION_0042])
        self._assert_persistent_defaults()

        executor = MigrationExecutor(connection)
        executor.migrate([MIGRATION_0041])
        apps = executor.loader.project_state([MIGRATION_0041]).apps

        self._assert_persistent_defaults()
        self._assert_old_and_new_writes(apps)


@skipUnless(connection.vendor == "postgresql", "PostgreSQL SQL contract")
class GmailProgressPostgreSQLMigrationSQLTests(TransactionTestCase):
    def test_corrected_0040_adds_defaults_without_dropping_them(self):
        executor = MigrationExecutor(connection)
        migration = executor.loader.get_migration(*MIGRATION_0040)
        before_state = executor.loader.project_state([MIGRATION_0039])

        with connection.schema_editor(collect_sql=True) as schema_editor:
            migration.apply(before_state, schema_editor, collect_sql=True)
        generated_sql = "\n".join(schema_editor.collected_sql)

        expected_fragments = {
            "analysis_progress_stage": "DEFAULT '' NOT NULL",
            "analysis_progress_attempt": "DEFAULT 0 NOT NULL",
            "analysis_progress_generation": "DEFAULT '' NOT NULL",
            "analysis_progress_error_category": "DEFAULT '' NOT NULL",
        }
        for column, default_sql in expected_fragments.items():
            self.assertIn(f'ADD COLUMN "{column}"', generated_sql)
            column_sql = next(
                statement
                for statement in schema_editor.collected_sql
                if f'ADD COLUMN "{column}"' in statement
            )
            self.assertIn(default_sql, column_sql)
        self.assertNotIn("DROP DEFAULT", generated_sql)

    def test_0042_repairs_all_defaults_and_reverse_never_drops_them(self):
        executor = MigrationExecutor(connection)
        migration = executor.loader.get_migration(*MIGRATION_0042)
        before_state = executor.loader.project_state([MIGRATION_0041])

        with connection.schema_editor(collect_sql=True) as schema_editor:
            after_state = migration.apply(
                before_state.clone(),
                schema_editor,
                collect_sql=True,
            )
        generated_sql = "\n".join(schema_editor.collected_sql)

        for column in PROGRESS_DEFAULTS:
            self.assertIn(
                f'ALTER COLUMN "{column}" SET DEFAULT',
                generated_sql,
            )
        self.assertNotIn("DROP DEFAULT", generated_sql)

        with connection.schema_editor(collect_sql=True) as schema_editor:
            migration.unapply(
                after_state,
                schema_editor,
                collect_sql=True,
            )
        reverse_sql = "\n".join(schema_editor.collected_sql)
        self.assertNotIn("DROP DEFAULT", reverse_sql)
