import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase


BACKEND_ROOT = Path(__file__).resolve().parent.parent


class PostgreSQLPoolerSettingsTests(SimpleTestCase):
    def settings_value(self, override=None):
        environment = os.environ.copy()
        environment.update(
            {
                "DATABASE_URL": "postgresql://user:pass@localhost:5432/pharmacy",
                "DJANGO_SECRET_KEY": "pooler-settings-test-key",
                "DEBUG": "False",
            }
        )
        if override is None:
            environment.pop("DATABASE_DISABLE_SERVER_SIDE_CURSORS", None)
        else:
            environment["DATABASE_DISABLE_SERVER_SIDE_CURSORS"] = override
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from pharmacy_api.settings import DATABASES; "
                    "print(DATABASES['default'].get('DISABLE_SERVER_SIDE_CURSORS'))"
                ),
            ],
            cwd=BACKEND_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return completed.stdout.strip().splitlines()[-1]

    def test_postgresql_disables_server_side_cursors_by_default(self):
        self.assertEqual(self.settings_value(), "True")

    def test_server_side_cursor_setting_can_be_explicitly_overridden(self):
        self.assertEqual(self.settings_value("0"), "False")
