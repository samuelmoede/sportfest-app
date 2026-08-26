import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi.testclient import TestClient

import app.database as database
from app.database import init_db


class ChangeLogScrollWrapperTests(unittest.TestCase):
    """Das Aenderungsprotokoll unter /einstellungen bekommt zusaetzlich zur
    generischen '.table-scroll'-Klasse (horizontales Scrollen, projektweit
    genutzt) einen eigenen '.change-log-scroll'-Wrapper fuer eine maximale
    Hoehe mit vertikalem Scrollbereich (siehe Issue #43)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "change-log-scroll-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_change_log_wrapper_has_no_scroll_class_without_entries(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get("/einstellungen")
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("change-log-scroll", response.text)

    def test_change_log_wrapper_has_scroll_class_with_entries(self):
        from app.main import app as fastapi_app

        with database.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO change_log (
                    created_at, actor_role, action, entity_type, entity_id,
                    competition_id, discipline_id, team_id, old_value, new_value
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "2026-08-26 12:00:00",
                    "admin",
                    "update",
                    "slot_result",
                    1,
                    None,
                    None,
                    None,
                    "0:0",
                    "1:0",
                ),
            )
            conn.commit()

        with TestClient(fastapi_app) as client:
            response = client.get("/einstellungen")
            self.assertEqual(response.status_code, 200)
            self.assertIn(
                '<div class="table-scroll change-log-scroll">', response.text
            )
            self.assertIn('class="change-log-table"', response.text)


if __name__ == "__main__":
    unittest.main()
