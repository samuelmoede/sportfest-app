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


class GrobplanScheduleBlockTests(unittest.TestCase):
    """/competition/{id}/update-schedule-block ist der Schreib-Endpunkt fuer
    den Grobplan-Rotationsplaner (Issue #80): Drag-to-move/Drag-to-resize der
    Wettbewerbs-Zeitbloecke in der Tagesplan-Timeline (day_schedule.html)
    persistiert hier start_time/end_time direkt auf der competitions-Zeile -
    es gibt bewusst kein eigenes Datenmodell dafuer (siehe Issue-Diskussion)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "grobplan-schedule-block-test.db"
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
            self.blocking_competition_id = conn.execute(
                "SELECT id FROM competitions WHERE name = 'Fussball Jg8'"
            ).fetchone()["id"]

            conn.execute(
                """
                INSERT INTO competitions (
                    name, sportart, jahrgang, status, event_id,
                    location, start_time, end_time
                )
                VALUES ('Zweifelderball Jg7', 'Zweifelderball', 7, 'geplant', ?, 'Turnhalle', '09:00', '10:00')
                """,
                (self.event_id,),
            )
            self.competition_id = conn.execute(
                "SELECT id FROM competitions WHERE name = 'Zweifelderball Jg7'"
            ).fetchone()["id"]
            conn.commit()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def _get_times(self, competition_id):
        with get_conn() as conn:
            row = conn.execute(
                "SELECT start_time, end_time FROM competitions WHERE id = ?",
                (competition_id,),
            ).fetchone()
        return row["start_time"], row["end_time"]

    def test_moves_block_and_returns_no_warnings(self):
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.post(
                f"/competition/{self.competition_id}/update-schedule-block",
                data={"start_time": "10:15", "end_time": "11:15"},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["warnings"], [])
        self.assertEqual(self._get_times(self.competition_id), ("10:15", "11:15"))

    def test_reports_conflict_at_same_location_without_blocking_save(self):
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with get_conn() as conn:
            conn.execute(
                "UPDATE competitions SET location = 'Sportplatz' WHERE id = ?",
                (self.competition_id,),
            )
            conn.commit()

        with TestClient(fastapi_app) as client:
            response = client.post(
                f"/competition/{self.competition_id}/update-schedule-block",
                data={"start_time": "09:30", "end_time": "10:30"},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(len(body["warnings"]), 1)
        self.assertIn("Fussball Jg8", body["warnings"][0]["message"])
        # Trotz Konflikt wird gespeichert - die Warnung blockiert nicht,
        # analog zu /slot/{id}/move.
        self.assertEqual(self._get_times(self.competition_id), ("09:30", "10:30"))

    def test_rejects_end_before_start(self):
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.post(
                f"/competition/{self.competition_id}/update-schedule-block",
                data={"start_time": "11:00", "end_time": "10:00"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._get_times(self.competition_id), ("09:00", "10:00"))

    def test_unknown_competition_returns_404(self):
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.post(
                "/competition/999999/update-schedule-block",
                data={"start_time": "09:00", "end_time": "10:00"},
            )

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
