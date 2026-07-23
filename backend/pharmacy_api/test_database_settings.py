import os
import json
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase


BACKEND_ROOT = Path(__file__).resolve().parent.parent


class PostgreSQLPoolerSettingsTests(SimpleTestCase):
    def settings_values(self, **overrides):
        environment = os.environ.copy()
        environment.update(
            {
                "DATABASE_URL": "postgresql://user:pass@localhost:5432/pharmacy",
                "DJANGO_SECRET_KEY": "pooler-settings-test-key",
                "DEBUG": "False",
            }
        )
        setting_names = (
            "DATABASE_CONN_HEALTH_CHECKS",
            "DATABASE_CONN_MAX_AGE_SECONDS",
            "DATABASE_DISABLE_SERVER_SIDE_CURSORS",
        )
        for name in setting_names:
            environment.pop(name, None)
        environment.update(overrides)
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import json; "
                    "from pharmacy_api.settings import DATABASES; "
                    "db = DATABASES['default']; "
                    "print(json.dumps({"
                    "'health_checks': db.get('CONN_HEALTH_CHECKS'), "
                    "'max_age': db.get('CONN_MAX_AGE'), "
                    "'server_side_cursors_disabled': db.get('DISABLE_SERVER_SIDE_CURSORS')"
                    "}))"
                ),
            ],
            cwd=BACKEND_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return json.loads(completed.stdout.strip().splitlines()[-1])

    def test_postgresql_uses_pooler_safe_connection_defaults(self):
        self.assertEqual(
            self.settings_values(),
            {
                "health_checks": True,
                "max_age": 60,
                "server_side_cursors_disabled": True,
            },
        )

    def test_pooler_connection_settings_can_be_explicitly_overridden(self):
        self.assertEqual(
            self.settings_values(
                DATABASE_CONN_HEALTH_CHECKS="0",
                DATABASE_CONN_MAX_AGE_SECONDS="0",
                DATABASE_DISABLE_SERVER_SIDE_CURSORS="0",
            ),
            {
                "health_checks": False,
                "max_age": 0,
                "server_side_cursors_disabled": False,
            },
        )
