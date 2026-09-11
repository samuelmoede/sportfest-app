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


class ScheduleTimingUpdateTests(unittest.TestCase):
    """/competition/{id}/update-timing erlaubt es, Spiel-/Wechselzeit direkt
    auf /spielplan-bearbeiten zu aendern, ohne den Umweg ueber /wettbewerbe
    (siehe Issue #76). Validierung muss dabei identisch zu
    /competition/{id}/update in app/routes/competitions.py bleiben."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "schedule-timing-update-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO competitions (
                    name, sportart, jahrgang, status, competition_type,
                    game_duration_minutes, changeover_duration_minutes
                )
                VALUES (?, 'Fussball', 7, 'geplant', 'Turnier', 7, 3)
                """,
                ("Testturnier",),
            )
            self.competition_id = conn.execute(
                "SELECT id FROM competitions WHERE name = ?", ("Testturnier",)
            ).fetchone()["id"]
            conn.commit()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def _get_timing(self):
        with get_conn() as conn:
            row = conn.execute(
                "SELECT game_duration_minutes, changeover_duration_minutes "
                "FROM competitions WHERE id = ?",
                (self.competition_id,),
            ).fetchone()
        return row["game_duration_minutes"], row["changeover_duration_minutes"]

    def test_updates_timing_and_redirects_to_editor(self):
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.post(
                f"/competition/{self.competition_id}/update-timing",
                data={"game_duration_minutes": "10", "changeover_duration_minutes": "5"},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            response.headers["location"],
            f"/spielplan-bearbeiten?competition_id={self.competition_id}",
        )
        self.assertEqual(self._get_timing(), (10, 5))

    def test_rejects_game_duration_below_one(self):
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.post(
                f"/competition/{self.competition_id}/update-timing",
                data={"game_duration_minutes": "0", "changeover_duration_minutes": "5"},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(self._get_timing(), (7, 3))

    def test_rejects_negative_changeover_duration(self):
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.post(
                f"/competition/{self.competition_id}/update-timing",
                data={"game_duration_minutes": "10", "changeover_duration_minutes": "-1"},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(self._get_timing(), (7, 3))

    def test_editor_page_shows_editable_timing_form(self):
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get(
                f"/spielplan-bearbeiten?competition_id={self.competition_id}"
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            f'action="/competition/{self.competition_id}/update-timing"',
            response.text,
        )
        self.assertIn('name="game_duration_minutes"', response.text)
        self.assertIn('name="changeover_duration_minutes"', response.text)


if __name__ == "__main__":
    unittest.main()
