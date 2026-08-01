import contextlib
import io
import json
import os
from pathlib import Path
from unittest import mock

import psycopg
from django.test import SimpleTestCase

from run_deploy_migrations import (
    BACKEND_ROOT,
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_LOCK_TIMEOUT_MS,
    DEFAULT_STATEMENT_TIMEOUT_MS,
    MIGRATION_ADVISORY_LOCK_ID,
    MigrationConfigurationError,
    load_migration_configuration,
    main,
    migration_advisory_lock,
    run_migrations,
)


APP_URL = (
    "postgresql://application:app-secret@"
    "ep-example-pooler.eu-central-1.aws.neon.tech/pharmacy?sslmode=require"
)
MIGRATION_URL = (
    "postgresql://migration:migration-secret@"
    "ep-example.eu-central-1.aws.neon.tech/pharmacy?sslmode=require"
)


class DeployMigrationConfigurationTests(SimpleTestCase):
    def environment(self, **overrides):
        environment = {
            "DATABASE_URL": APP_URL,
            "MIGRATION_DATABASE_URL": MIGRATION_URL,
        }
        environment.update(overrides)
        return environment

    def test_accepts_matching_neon_pooler_and_direct_host_lineage(self):
        configuration = load_migration_configuration(self.environment())

        self.assertEqual(configuration.database_url, MIGRATION_URL)
        self.assertEqual(
            configuration.connect_timeout_seconds,
            DEFAULT_CONNECT_TIMEOUT_SECONDS,
        )
        self.assertEqual(configuration.lock_timeout_ms, DEFAULT_LOCK_TIMEOUT_MS)
        self.assertEqual(
            configuration.statement_timeout_ms,
            DEFAULT_STATEMENT_TIMEOUT_MS,
        )

    def test_railway_predeploy_invokes_the_guarded_runner(self):
        railway_configuration = json.loads(
            (BACKEND_ROOT / "railway.json").read_text(encoding="utf-8")
        )

        self.assertEqual(
            railway_configuration["$schema"],
            "https://railway.com/railway.schema.json",
        )
        self.assertEqual(
            railway_configuration["deploy"]["preDeployCommand"],
            "python run_deploy_migrations.py",
        )
        procfile = (BACKEND_ROOT / "Procfile").read_text(encoding="utf-8")
        self.assertNotIn("release:", procfile)
        self.assertNotIn("manage.py migrate", procfile)

    def test_rejects_missing_migration_url(self):
        with self.assertRaisesRegex(
            MigrationConfigurationError,
            "MIGRATION_DATABASE_URL is required",
        ):
            load_migration_configuration({"DATABASE_URL": APP_URL})

    def test_rejects_non_postgresql_targets(self):
        with self.assertRaisesRegex(MigrationConfigurationError, "use PostgreSQL"):
            load_migration_configuration(
                self.environment(MIGRATION_DATABASE_URL="sqlite:///db.sqlite3")
            )

    def test_rejects_pooler_migration_host(self):
        with self.assertRaisesRegex(MigrationConfigurationError, "direct, unpooled"):
            load_migration_configuration(
                self.environment(MIGRATION_DATABASE_URL=APP_URL)
            )

    def test_rejects_pooler_query_marker(self):
        with self.assertRaisesRegex(MigrationConfigurationError, "direct, unpooled"):
            load_migration_configuration(
                self.environment(
                    MIGRATION_DATABASE_URL=(
                        "postgresql://migration:migration-secret@"
                        "ep-example.eu-central-1.aws.neon.tech/pharmacy?pgbouncer=true"
                    )
                )
            )

    def test_requires_encrypted_tls_for_non_local_migration_hosts(self):
        base_url = (
            "postgresql://migration:migration-secret@"
            "ep-example.eu-central-1.aws.neon.tech/pharmacy"
        )
        for suffix in (
            "",
            "?sslmode=disable",
            "?sslmode=prefer",
            "?sslmode=require&sslmode=disable",
        ):
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(MigrationConfigurationError, "require TLS"):
                    load_migration_configuration(
                        self.environment(MIGRATION_DATABASE_URL=f"{base_url}{suffix}")
                    )

    def test_accepts_each_supported_encrypted_tls_mode(self):
        for sslmode in ("require", "verify-ca", "verify-full"):
            with self.subTest(sslmode=sslmode):
                configuration = load_migration_configuration(
                    self.environment(
                        MIGRATION_DATABASE_URL=(
                            "postgresql://migration:migration-secret@"
                            "ep-example.eu-central-1.aws.neon.tech/pharmacy"
                            f"?sslmode={sslmode}"
                        )
                    )
                )
                self.assertIn(f"sslmode={sslmode}", configuration.database_url)

    def test_allows_unencrypted_loopback_for_disposable_local_validation(self):
        configuration = load_migration_configuration(
            {
                "DATABASE_URL": "postgresql://postgres@127.0.0.1:55432/pharmacy",
                "MIGRATION_DATABASE_URL": (
                    "postgresql://postgres@127.0.0.1:55432/pharmacy"
                ),
            }
        )

        self.assertEqual(
            configuration.database_url.split("@", 1)[1],
            "127.0.0.1:55432/pharmacy",
        )

    def test_rejects_query_options_that_override_the_validated_target(self):
        for key in (
            "connect_timeout",
            "host",
            "hostaddr",
            "dbname",
            "options",
            "service",
        ):
            with self.subTest(key=key):
                with self.assertRaisesRegex(
                    MigrationConfigurationError,
                    "must not override",
                ):
                    load_migration_configuration(
                        self.environment(
                            MIGRATION_DATABASE_URL=f"{MIGRATION_URL}&{key}=other"
                        )
                    )

    def test_decodes_host_before_single_tcp_host_and_pooler_validation(self):
        invalid_urls = (
            "postgresql://migration:secret@%2Fvar%2Frun%2Fpostgresql/pharmacy",
            "postgresql://migration:secret@ep-example%2Cep-other.example/pharmacy",
            (
                "postgresql://migration:secret@"
                "ep-example%2Dpooler.eu-central-1.aws.neon.tech/pharmacy"
                "?sslmode=require"
            ),
        )

        for migration_url in invalid_urls:
            with self.subTest(migration_url=migration_url):
                with self.assertRaises(MigrationConfigurationError):
                    load_migration_configuration(
                        self.environment(MIGRATION_DATABASE_URL=migration_url)
                    )

    def test_rejects_multiple_hosts_fragments_and_different_ports(self):
        invalid_urls = (
            "postgresql://migration:secret@ep-example.eu-central-1.aws.neon.tech,ep-other.example/pharmacy",
            "postgresql://migration:secret@ep-example.eu-central-1.aws.neon.tech/pharmacy#other",
            "postgresql://migration:secret@ep-example.eu-central-1.aws.neon.tech:6543/pharmacy",
        )

        for migration_url in invalid_urls:
            with self.subTest(migration_url=migration_url):
                with self.assertRaises(MigrationConfigurationError):
                    load_migration_configuration(
                        self.environment(MIGRATION_DATABASE_URL=migration_url)
                    )

    def test_rejects_different_database(self):
        with self.assertRaisesRegex(MigrationConfigurationError, "same database"):
            load_migration_configuration(
                self.environment(
                    MIGRATION_DATABASE_URL=(
                        "postgresql://migration:migration-secret@"
                        "ep-example.eu-central-1.aws.neon.tech/other?sslmode=require"
                    )
                )
            )

    def test_rejects_unrelated_host(self):
        with self.assertRaisesRegex(MigrationConfigurationError, "host lineage"):
            load_migration_configuration(
                self.environment(
                    MIGRATION_DATABASE_URL=(
                        "postgresql://migration:migration-secret@"
                        "ep-unrelated.eu-central-1.aws.neon.tech/pharmacy?sslmode=require"
                    )
                )
            )

    def test_rejects_invalid_or_unbounded_timeouts(self):
        invalid_values = (
            {"MIGRATION_CONNECT_TIMEOUT_SECONDS": "0"},
            {"MIGRATION_CONNECT_TIMEOUT_SECONDS": "61"},
            {"MIGRATION_LOCK_TIMEOUT_MS": "0"},
            {"MIGRATION_LOCK_TIMEOUT_MS": "five seconds"},
            {"MIGRATION_LOCK_TIMEOUT_MS": "300001"},
            {"MIGRATION_STATEMENT_TIMEOUT_MS": "3600001"},
            {
                "MIGRATION_LOCK_TIMEOUT_MS": "20000",
                "MIGRATION_STATEMENT_TIMEOUT_MS": "10000",
            },
        )

        for values in invalid_values:
            with self.subTest(values=values):
                with self.assertRaises(MigrationConfigurationError):
                    load_migration_configuration(self.environment(**values))


class DeployMigrationExecutionTests(SimpleTestCase):
    def configuration(self):
        return load_migration_configuration(
            {
                "DATABASE_URL": APP_URL,
                "MIGRATION_DATABASE_URL": MIGRATION_URL,
                "MIGRATION_CONNECT_TIMEOUT_SECONDS": "4",
                "MIGRATION_LOCK_TIMEOUT_MS": "5000",
                "MIGRATION_STATEMENT_TIMEOUT_MS": "30000",
            }
        )

    @mock.patch("run_deploy_migrations.migration_advisory_lock")
    @mock.patch("run_deploy_migrations.subprocess.run")
    def test_runs_migrate_with_direct_url_and_bounded_session_options(
        self,
        run,
        advisory_lock,
    ):
        run.return_value.returncode = 0
        parent_environment = {
            "DATABASE_URL": APP_URL,
            "MIGRATION_DATABASE_URL": MIGRATION_URL,
            "PGOPTIONS": "-c unsafe_inherited_setting=1",
            "PGHOSTADDR": "203.0.113.10",
            "PGHOST": "attacker.example",
            "PGPORT": "6543",
            "PGDATABASE": "other",
            "PGSERVICE": "other-service",
            "PGSERVICEFILE": "private-service-file",
            "PGSSLMODE": "disable",
            "PGPASSWORD": "inherited-secret",
            "DJANGO_SETTINGS_MODULE": "untrusted.settings",
            "UNCHANGED": "value",
        }

        result = run_migrations(self.configuration(), parent_environment)

        self.assertEqual(result, 0)
        advisory_lock.assert_called_once_with(self.configuration())
        run.assert_called_once()
        args, kwargs = run.call_args
        self.assertEqual(
            args[0],
            [os.sys.executable, "manage.py", "migrate", "--noinput"],
        )
        self.assertEqual(kwargs["cwd"], Path(BACKEND_ROOT))
        self.assertFalse(kwargs["check"])
        child_environment = kwargs["env"]
        self.assertEqual(child_environment["DATABASE_URL"], MIGRATION_URL)
        self.assertNotIn("MIGRATION_DATABASE_URL", child_environment)
        self.assertEqual(child_environment["DATABASE_CONN_MAX_AGE_SECONDS"], "0")
        self.assertEqual(child_environment["DATABASE_CONNECT_TIMEOUT_SECONDS"], "4")
        self.assertEqual(
            child_environment["DJANGO_SETTINGS_MODULE"],
            "pharmacy_api.settings",
        )
        self.assertEqual(
            child_environment["PGOPTIONS"],
            "-c lock_timeout=5000ms -c statement_timeout=30000ms",
        )
        self.assertEqual(child_environment["UNCHANGED"], "value")
        self.assertFalse(
            any(
                name.upper().startswith("PG") and name != "PGOPTIONS"
                for name in child_environment
            )
        )
        self.assertEqual(parent_environment["DATABASE_URL"], APP_URL)
        self.assertEqual(parent_environment["PGOPTIONS"], "-c unsafe_inherited_setting=1")

    @mock.patch("run_deploy_migrations.migration_advisory_lock")
    @mock.patch("run_deploy_migrations.subprocess.run")
    def test_returns_django_migration_failure_code(self, run, advisory_lock):
        run.return_value.returncode = 7

        self.assertEqual(run_migrations(self.configuration(), {}), 7)
        advisory_lock.assert_called_once()

    def test_validation_failure_does_not_log_database_credentials(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = main(
                {
                    "DATABASE_URL": APP_URL,
                    "MIGRATION_DATABASE_URL": (
                        "postgresql://migration:do-not-log-this@"
                        "ep-attacker.example/pharmacy"
                    ),
                }
            )

        self.assertEqual(result, 2)
        output = stderr.getvalue()
        self.assertNotIn("app-secret", output)
        self.assertNotIn("do-not-log-this", output)
        self.assertNotIn("ep-attacker.example", output)

    @mock.patch("run_deploy_migrations.migration_advisory_lock")
    @mock.patch("run_deploy_migrations.subprocess.run")
    def test_main_propagates_nonzero_migration_status(self, run, advisory_lock):
        run.return_value.returncode = 9
        stdout = io.StringIO()
        stderr = io.StringIO()

        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = main(
                {
                    "DATABASE_URL": APP_URL,
                    "MIGRATION_DATABASE_URL": MIGRATION_URL,
                }
            )

        self.assertEqual(result, 9)
        self.assertIn("Guarded Django migration step failed", stderr.getvalue())
        self.assertNotIn("migration-secret", stdout.getvalue() + stderr.getvalue())
        advisory_lock.assert_called_once()

    @mock.patch("run_deploy_migrations.subprocess.run")
    @mock.patch(
        "run_deploy_migrations.psycopg.connect",
        side_effect=psycopg.OperationalError("private connection detail"),
    )
    def test_advisory_lock_failure_is_generic_and_never_runs_migrate(
        self,
        connect,
        run,
    ):
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            result = run_migrations(self.configuration(), {})

        self.assertEqual(result, 1)
        connect.assert_called_once()
        run.assert_not_called()
        self.assertIn("Unable to establish or lock", stderr.getvalue())
        self.assertNotIn("private connection detail", stderr.getvalue())


class MigrationAdvisoryLockTests(SimpleTestCase):
    @mock.patch("run_deploy_migrations.psycopg.connect")
    def test_lock_uses_validated_target_and_clears_process_libpq_overrides(
        self,
        connect,
    ):
        configuration = load_migration_configuration(
            {
                "DATABASE_URL": APP_URL,
                "MIGRATION_DATABASE_URL": MIGRATION_URL,
                "MIGRATION_CONNECT_TIMEOUT_SECONDS": "4",
                "MIGRATION_LOCK_TIMEOUT_MS": "5000",
                "MIGRATION_STATEMENT_TIMEOUT_MS": "30000",
            }
        )
        connection = connect.return_value.__enter__.return_value
        cursor = connection.cursor.return_value.__enter__.return_value

        with mock.patch.dict(
            os.environ,
            {"PGHOSTADDR": "203.0.113.10", "PGSSLMODE": "disable"},
            clear=False,
        ):
            with migration_advisory_lock(configuration):
                self.assertNotIn("PGHOSTADDR", os.environ)
                self.assertNotIn("PGSSLMODE", os.environ)
            self.assertEqual(os.environ["PGHOSTADDR"], "203.0.113.10")
            self.assertEqual(os.environ["PGSSLMODE"], "disable")

        connect.assert_called_once_with(
            MIGRATION_URL,
            autocommit=True,
            connect_timeout=4,
            options="-c lock_timeout=5000ms -c statement_timeout=30000ms",
        )
        cursor.execute.assert_called_once_with(
            "SELECT pg_advisory_lock(%s)",
            (MIGRATION_ADVISORY_LOCK_ID,),
        )
