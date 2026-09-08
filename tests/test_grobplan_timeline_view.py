import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import app.database as database
from app.database import get_conn, init_db


class GrobplanTimelineViewTests(unittest.TestCase):
    """/events/{id} rendert die Tagesplan-Timeline im Admin-Kontext editierbar
    (Issue #80: Drag-to-move/Drag-to-resize direkt auf den bestehenden
    Wettbewerbs-Zeitbloecken aus build_day_timeline(), kein neues
    Datenmodell)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "grobplan-timeline-view-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

        with get_conn() as conn:
            conn.execute(
                "INSERT INTO events (name, event_date) VALUES ('Bewegungsfest', '2026-09-10')"
            )
            self.event_id = conn.execute(
                "SELECT id FROM events WHERE name = 'Bewegungsfest'"
            ).fetchone()["id"]
            conn.execute(
                """
                INSERT INTO competitions (
                    name, sportart, jahrgang, status, event_id,
                    location, start_time, end_time
                )
                VALUES ('Fussball Jg8', 'Fussball', 8, 'geplant', ?, 'Sportplatz', '09:00', '10:00')
                """,
                (self.event_id,),
            )
            self.competition_id = conn.execute(
                "SELECT id FROM competitions WHERE name = 'Fussball Jg8'"
            ).fetchone()["id"]
            conn.commit()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_event_detail_renders_editable_grobplan_block(self):
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get(f"/events/{self.event_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("data-grobplan-block", response.text)
        self.assertIn(f'data-competition-id="{self.competition_id}"', response.text)
        self.assertIn('data-grobplan-handle="start"', response.text)
        self.assertIn('data-grobplan-handle="end"', response.text)


if __name__ == "__main__":
    unittest.main()
