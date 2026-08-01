from unittest import skipUnless

from django.db import connection, models
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase


APP_LABEL = "quotations"
MIGRATION_0035 = (APP_LABEL, "0035_quotationemailoutboundsnapshot_and_more")
MIGRATION_0036 = (APP_LABEL, "0036_quotationoutcomepoimport_parsed_meta")
MIGRATION_0037 = (APP_LABEL, "0037_alter_gmailoauthconnection_user")
MIGRATION_0038 = (APP_LABEL, "0038_ensure_po_import_parsed_meta_db_default")


class ParsedMetaMigrationCompatibilityTests(TransactionTestCase):
    """Exercise both the corrected 0036 and the already-applied repair path."""

    reset_sequences = True

    def setUp(self):
        super().setUp()
        self.addCleanup(self._restore_latest_schema)
        executor = MigrationExecutor(connection)
        executor.migrate([MIGRATION_0035])
        self.old_apps = executor.loader.project_state([MIGRATION_0035]).apps
        self.company_id, self.quotation_id, self.existing_import_id = self._create_old_rows(
            suffix=self._testMethodName
        )

    @staticmethod
    def _restore_latest_schema():
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def _create_old_rows(self, *, suffix):
        company_model = self.old_apps.get_model(APP_LABEL, "Company")
        quotation_model = self.old_apps.get_model(APP_LABEL, "Quotation")
        outcome_import_model = self.old_apps.get_model(
            APP_LABEL, "QuotationOutcomePOImport"
        )
        company = company_model.objects.create(
            name=f"Migration compatibility {suffix}",
            normalized_name=f"migration compatibility {suffix}",
        )
        quotation = quotation_model.objects.create(
            company_id=company.pk,
            quotation_number=f"MIG-{suffix}"[:50],
        )
        po_import = outcome_import_model.objects.create(
            quotation_id=quotation.pk,
            source_type="file",
            source_filename="existing.pdf",
        )
        return company.pk, quotation.pk, po_import.pk

    @staticmethod
    def _database_default():
        table = "quotations_quotationoutcomepoimport"
        column = "parsed_meta"
        with connection.cursor() as cursor:
            if connection.vendor == "sqlite":
                cursor.execute(f'PRAGMA table_info("{table}")')
                row = next(item for item in cursor.fetchall() if item[1] == column)
                return row[4]
            if connection.vendor == "postgresql":
                cursor.execute(
                    """
                    SELECT pg_get_expr(default_value.adbin, default_value.adrelid)
                    FROM pg_attribute AS attribute
                    JOIN pg_class AS relation ON relation.oid = attribute.attrelid
                    JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                    LEFT JOIN pg_attrdef AS default_value
                      ON default_value.adrelid = relation.oid
                     AND default_value.adnum = attribute.attnum
                    WHERE namespace.nspname = current_schema()
                      AND relation.relname = %s
                      AND attribute.attname = %s
                    """,
                    [table, column],
                )
                return cursor.fetchone()[0]
        return None

    def _assert_old_and_new_writes(self, apps):
        old_import_model = self.old_apps.get_model(
            APP_LABEL, "QuotationOutcomePOImport"
        )
        new_import_model = apps.get_model(APP_LABEL, "QuotationOutcomePOImport")

        existing = new_import_model.objects.get(pk=self.existing_import_id)
        self.assertEqual(existing.parsed_meta, {})
        self.assertEqual(existing.source_filename, "existing.pdf")
        self.assertEqual(existing.quotation_id, self.quotation_id)

        # This historical model has no parsed_meta field and therefore emits
        # the same INSERT shape as the pre-hardening application.
        old_insert = old_import_model.objects.create(
            quotation_id=self.quotation_id,
            source_type="file",
            source_filename="old-code.pdf",
        )
        self.assertEqual(
            new_import_model.objects.get(pk=old_insert.pk).parsed_meta,
            {},
        )

        new_insert = new_import_model.objects.create(
            quotation_id=self.quotation_id,
            source_type="file",
            source_filename="new-code.pdf",
            parsed_meta={"attachment_safety": {"validated": True}},
        )
        self.assertEqual(
            new_import_model.objects.get(pk=new_insert.pk).parsed_meta,
            {"attachment_safety": {"validated": True}},
        )
        new_import_model.objects.filter(pk=new_insert.pk).update(
            parsed_meta={"attachment_safety": {"validated": False}}
        )
        self.assertEqual(
            new_import_model.objects.get(pk=new_insert.pk).parsed_meta,
            {"attachment_safety": {"validated": False}},
        )

        first = new_import_model(quotation_id=self.quotation_id, source_type="file")
        second = new_import_model(quotation_id=self.quotation_id, source_type="file")
        self.assertEqual(first.parsed_meta, {})
        self.assertIsNot(first.parsed_meta, second.parsed_meta)

    def test_corrected_0036_retains_default_for_rolling_deployment(self):
        executor = MigrationExecutor(connection)
        executor.migrate([MIGRATION_0036])
        apps = executor.loader.project_state([MIGRATION_0036]).apps

        database_default = self._database_default()
        self.assertIsNotNone(database_default)
        self.assertIn("{}", str(database_default))
        self._assert_old_and_new_writes(apps)

    def test_0038_repairs_a_previously_applied_original_0036(self):
        outcome_import_model = self.old_apps.get_model(
            APP_LABEL, "QuotationOutcomePOImport"
        )
        old_field = models.JSONField(blank=True, default=dict)
        old_field.set_attributes_from_name("parsed_meta")
        old_field.model = outcome_import_model
        with connection.schema_editor() as schema_editor:
            schema_editor.add_field(outcome_import_model, old_field)

        self.assertIsNone(self._database_default())
        recorder = MigrationRecorder(connection)
        recorder.record_applied(*MIGRATION_0036)
        recorder.record_applied(*MIGRATION_0037)

        executor = MigrationExecutor(connection)
        executor.migrate([MIGRATION_0038])
        apps = executor.loader.project_state([MIGRATION_0038]).apps

        database_default = self._database_default()
        self.assertIsNotNone(database_default)
        self.assertIn("{}", str(database_default))
        self._assert_old_and_new_writes(apps)


@skipUnless(connection.vendor == "postgresql", "PostgreSQL SQL contract")
class ParsedMetaPostgreSQLMigrationSQLTests(TransactionTestCase):
    def test_0036_generated_sql_keeps_the_jsonb_default(self):
        executor = MigrationExecutor(connection)
        migration = executor.loader.get_migration(*MIGRATION_0036)
        state = executor.loader.project_state([MIGRATION_0035])

        with connection.schema_editor(collect_sql=True) as schema_editor:
            migration.apply(state, schema_editor, collect_sql=True)
        generated_sql = "\n".join(schema_editor.collected_sql)

        self.assertIn(
            'ADD COLUMN "parsed_meta" jsonb DEFAULT \'{}\'::jsonb NOT NULL',
            generated_sql,
        )
        self.assertNotIn('ALTER COLUMN "parsed_meta" DROP DEFAULT', generated_sql)
