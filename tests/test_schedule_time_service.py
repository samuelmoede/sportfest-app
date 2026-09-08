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
from app.services.schedule_time_service import find_overlapping_competitions


class FindOverlappingCompetitionsTests(unittest.TestCase):
    """Konfliktpruefung fuer den Grobplan (Issue #80): zwei Wettbewerbe
    derselben Veranstaltung am selben Ort duerfen sich zeitlich nicht
    ueberschneiden, ohne dass eine Warnung ausgegeben wird."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "schedule-time-service-test.db"
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
                    location, location_subarea, start_time, end_time
                )
                VALUES (?, 'Fussball', 8, 'geplant', ?, 'Sportplatz', NULL, '09:00', '10:00')
                """,
                ("Fussball Jg8", self.event_id),
            )
            self.other_competition_id = conn.execute(
                "SELECT id FROM competitions WHERE name = 'Fussball Jg8'"
            ).fetchone()["id"]

            conn.execute(
                """
                INSERT INTO competitions (
                    name, sportart, jahrgang, status, event_id,
                    location, location_subarea, start_time, end_time
                )
                VALUES (?, 'Zweifelderball', 7, 'geplant', ?, 'Turnhalle', NULL, '09:00', '10:00')
                """,
                ("Zweifelderball Jg7", self.event_id),
            )
            self.competition_id = conn.execute(
                "SELECT id FROM competitions WHERE name = 'Zweifelderball Jg7'"
            ).fetchone()["id"]
            conn.commit()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def _conflicts(self, location, location_subarea, start_time, end_time):
        with get_conn() as conn:
            return find_overlapping_competitions(
                conn,
                competition_id=self.competition_id,
                event_id=self.event_id,
                location=location,
                location_subarea=location_subarea,
                start_time=start_time,
                end_time=end_time,
            )

    def test_no_conflict_at_different_location(self):
        self.assertEqual(self._conflicts("Turnhalle", None, "09:30", "10:30"), [])

    def test_conflict_when_overlapping_at_same_location(self):
        conflicts = self._conflicts("Sportplatz", None, "09:30", "10:30")
        self.assertEqual(conflicts, ["Fussball Jg8"])

    def test_no_conflict_when_adjacent_not_overlapping(self):
        self.assertEqual(self._conflicts("Sportplatz", None, "10:00", "11:00"), [])

    def test_no_conflict_without_location(self):
        self.assertEqual(self._conflicts(None, None, "09:30", "10:30"), [])

    def test_no_conflict_when_subareas_differ(self):
        with get_conn() as conn:
            conn.execute(
                "UPDATE competitions SET location_subarea = 'Halle 1' WHERE id = ?",
                (self.other_competition_id,),
            )
            conn.commit()
        conflicts = self._conflicts("Sportplatz", "Halle 2", "09:30", "10:30")
        self.assertEqual(conflicts, [])


if __name__ == "__main__":
    unittest.main()
