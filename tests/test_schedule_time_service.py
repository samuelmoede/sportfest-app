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
from app.services.schedule_time_service import (
    find_overlapping_competitions,
    suggest_game_duration,
)


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



class SuggestGameDurationTests(unittest.TestCase):
    """Spielzeit-Vorschlag aus der verfuegbaren Zeit (Schulpokal 2026)."""

    @staticmethod
    def _slots(*starts):
        return [{"slot_typ": "Spiel", "startzeit": start} for start in starts]

    def test_ten_rounds_until_planned_end(self):
        # Zweifelderball J7/J8: 08:00-10:40, 10 Runden, 3 min Wechsel.
        starts = [f"{8 + (i * 16) // 60:02d}:{(i * 16) % 60:02d}" for i in range(10)]
        competition = {"end_time": "10:40", "game_duration_minutes": 7, "changeover_duration_minutes": 3}
        result = suggest_game_duration(self._slots(*starts), competition)
        self.assertEqual(result["rounds"], 10)
        self.assertEqual(result["available_minutes"], 160)
        # 10 * g + 9 * 3 <= 160 -> g = 13
        self.assertEqual(result["game_duration_minutes"], 13)
        self.assertEqual(result["current_game_duration_minutes"], 7)

    def test_parallel_games_count_as_one_round(self):
        competition = {"end_time": "09:00", "changeover_duration_minutes": 0}
        result = suggest_game_duration(self._slots("08:00", "08:00", "08:00", "08:10"), competition)
        self.assertEqual(result["rounds"], 2)
        self.assertEqual(result["game_duration_minutes"], 30)

    def test_without_end_time_there_is_no_suggestion(self):
        result = suggest_game_duration(self._slots("08:00", "08:10"), {})
        self.assertIsNone(result["game_duration_minutes"])
        self.assertIsNone(result["planned_end"])

    def test_too_little_time_gives_no_suggestion(self):
        competition = {"end_time": "08:05", "changeover_duration_minutes": 3}
        result = suggest_game_duration(self._slots("08:00", "08:10", "08:20"), competition)
        self.assertIsNone(result["game_duration_minutes"])
        self.assertEqual(result["planned_end"], "08:05")

    def test_breaks_are_ignored(self):
        self.assertIsNone(suggest_game_duration([{"slot_typ": "Pause", "startzeit": "08:00"}], {}))


if __name__ == "__main__":
    unittest.main()
