import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.schedule_grid_service import _insert_group_phase_gaps


def spiel(competition_id, court_id, startzeit, phase="Gruppenphase", competition_name="Fussball 7"):
    return {
        "competition_id": competition_id,
        "competition_name": competition_name,
        "court_id": court_id,
        "startzeit": startzeit,
        "slot_typ": "Spiel",
        "phase": phase,
    }


class InsertGroupPhaseGapsTests(unittest.TestCase):
    def test_fills_missing_time_on_the_shorter_court(self):
        slots = [
            spiel(24, 1, "10:20"),
            spiel(24, 2, "10:20"),
            spiel(24, 1, "10:30"),
            spiel(24, 2, "10:30"),
            spiel(24, 1, "10:40"),
            spiel(24, 2, "10:40"),
            spiel(24, 1, "10:50"),
            spiel(24, 1, "11:00", phase="Halbfinale"),
            spiel(24, 2, "11:00", phase="Halbfinale"),
        ]

        result = _insert_group_phase_gaps(slots)
        gaps = [slot for slot in result if slot.get("is_gap")]

        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["court_id"], 2)
        self.assertEqual(gaps[0]["startzeit"], "10:50")
        self.assertEqual(gaps[0]["competition_id"], 24)

    def test_no_gap_when_single_court_competition(self):
        slots = [spiel(9, 1, "08:00"), spiel(9, 1, "08:10")]

        result = _insert_group_phase_gaps(slots)

        self.assertEqual(result, slots)

    def test_no_duplicate_gap_when_time_already_covered_by_other_slot_typ(self):
        slots = [
            spiel(24, 1, "10:20"),
            spiel(24, 2, "10:20"),
            {
                "competition_id": 24,
                "competition_name": "Fussball 7",
                "court_id": 2,
                "startzeit": "10:30",
                "slot_typ": "Leer",
                "phase": "Gruppenphase",
            },
            spiel(24, 1, "10:30"),
        ]

        result = _insert_group_phase_gaps(slots)
        gaps = [slot for slot in result if slot.get("is_gap")]

        self.assertEqual(gaps, [])


if __name__ == "__main__":
    unittest.main()
