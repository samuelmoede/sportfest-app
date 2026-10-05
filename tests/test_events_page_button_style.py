import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import app.database as database
from app.database import init_db


class EventsPageCreateButtonStyleTests(unittest.TestCase):
    """Der '+ Veranstaltung anlegen'-Button auf /events soll optisch dem
    primaeren/akzentfarbenen '+ Wettbewerb anlegen'-Button auf /wettbewerbe
    entsprechen (statt outlined/weiss zu sein)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "events-button-style-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_events_page_create_button_uses_primary_style(self):
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get("/events")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            '<a class="button primary" href="/events/new">+ Veranstaltung anlegen</a>',
            response.text,
        )
        self.assertNotIn(
            '<a class="button secondary" href="/events/new">+ Veranstaltung anlegen</a>',
            response.text,
        )


if __name__ == "__main__":
    unittest.main()
