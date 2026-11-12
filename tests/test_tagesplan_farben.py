"""Tests fuer das Farbkonzept des Tagesplans: Farbe je Sportart
(assign_sport_colors in app/services/ui_view_service.py)."""
import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.ui_view_service import SPORT_FARBEN, assign_sport_colors, build_day_timeline


def _block(sportart, column, start, name=None):
    return {"sportart": sportart, "name": name or sportart, "column": column,
            "start_minutes": start, "end_minutes": start + 60}


class AssignSportColorsTests(unittest.TestCase):
    def test_same_sport_same_color_across_years(self):
        entries = [
            _block("Zweifelderball", "Jahrgang 7", 480),
            _block("Zweifelderball", "Jahrgang 8", 480),
            _block("Tischtennis", "Jahrgang 9", 670),
            _block("Volleyball", "Jahrgang 9", 670),
            _block("tischtennis ", "Jahrgang 10", 670),
        ]
        assign_sport_colors(entries, ["Jahrgang 7", "Jahrgang 8", "Jahrgang 9", "Jahrgang 10"])
        self.assertEqual([e["farbe"] for e in entries], [1, 1, 2, 3, 2])

    def test_order_follows_time_then_column(self):
        entries = [_block("B", "Jahrgang 8", 480), _block("A", "Jahrgang 7", 480), _block("C", "Jahrgang 7", 420)]
        assign_sport_colors(entries, ["Jahrgang 7", "Jahrgang 8"])
        self.assertEqual({e["sportart"]: e["farbe"] for e in entries}, {"C": 1, "A": 2, "B": 3})

    def test_programm_entries_get_no_color_and_palette_wraps(self):
        entries = [_block(f"Sport {i}", "Jahrgang 7", 480 + i) for i in range(SPORT_FARBEN + 1)]
        entries.append({"is_programm": True, "name": "Siegerehrung", "column": "Jahrgang 7",
                        "start_minutes": 600, "end_minutes": 610})
        assign_sport_colors(entries, ["Jahrgang 7"])
        self.assertEqual(entries[SPORT_FARBEN]["farbe"], 1)
        self.assertNotIn("farbe", entries[-1])

    def test_timeline_blocks_carry_color(self):
        competitions = [
            {"id": 1, "name": "Fußball JG 7", "sportart": "Fußball", "jahrgang": 7,
             "start_time": "08:00", "end_time": "09:00"},
            {"id": 2, "name": "Fußball JG 8", "sportart": "Fußball", "jahrgang": 8,
             "start_time": "08:00", "end_time": "09:00"},
        ]
        timeline = build_day_timeline(["Jahrgang 7", "Jahrgang 8"], competitions)
        farben = [item["farbe"] for col in timeline["columns"] for item in col["items"]]
        self.assertEqual(farben, [1, 1])


if __name__ == "__main__":
    unittest.main()
