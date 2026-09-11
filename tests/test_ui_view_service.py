import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.ui_view_service import build_day_timeline, classify_yeargang


class ClassifyYeargangTests(unittest.TestCase):
    """Issue #85: die Grobplan-Spalten duerfen nicht mehr auf eine feste
    Liste (Jahrgang 7-9 + Oberstufe) beschraenkt sein - u.a. Jahrgang 5/6
    fielen bisher stillschweigend unter den Tisch."""

    def test_returns_jahrgang_label_for_5_and_6(self):
        self.assertEqual(classify_yeargang(5), "Jahrgang 5")
        self.assertEqual(classify_yeargang(6), "Jahrgang 6")

    def test_still_returns_jahrgang_label_for_7_8_9(self):
        self.assertEqual(classify_yeargang(7), "Jahrgang 7")
        self.assertEqual(classify_yeargang(8), "Jahrgang 8")
        self.assertEqual(classify_yeargang(9), "Jahrgang 9")

    def test_returns_oberstufe_for_10_to_13(self):
        for year in (10, 11, 12, 13):
            self.assertEqual(classify_yeargang(year), "Oberstufe")

    def test_accepts_numeric_strings(self):
        self.assertEqual(classify_yeargang("5"), "Jahrgang 5")

    def test_returns_oberstufe_for_text_variants(self):
        self.assertEqual(classify_yeargang("GOST"), "Oberstufe")
        self.assertEqual(classify_yeargang("11b"), "Oberstufe")

    def test_returns_none_for_mixed_and_unclassifiable_values(self):
        # 'mixed' wird von /competition/create fuer rein explizite
        # Team-Auswahl ohne Jahrgang verwendet (siehe app/routes/competitions.py).
        # Bewusst weiterhin ausgeschlossen, bis geklaert ist, ob eine eigene
        # Grobplan-Spalte "Sonstiges/Mixed" gewuenscht ist (Issue #85, Punkt 3a).
        self.assertIsNone(classify_yeargang("mixed"))
        self.assertIsNone(classify_yeargang("Lehrer"))
        self.assertIsNone(classify_yeargang(None))


class BuildDayTimelineColumnTests(unittest.TestCase):
    """build_day_timeline() selbst kannte schon immer beliebige Spalten -
    sie muessen ihm nur von main.build_day_schedule() (das die tatsaechlich
    vorhandenen Jahrgaenge einer Veranstaltung ermittelt) mitgegeben werden."""

    def test_includes_entries_for_dynamic_year_5_6_columns(self):
        competitions = [
            {
                "id": 1,
                "name": "Zweifelderball Jg5",
                "sportart": "Zweifelderball",
                "jahrgang": 5,
                "start_time": "09:00",
                "end_time": "10:00",
            },
            {
                "id": 2,
                "name": "Fussball Jg6",
                "sportart": "Fussball",
                "jahrgang": 6,
                "start_time": "09:00",
                "end_time": "10:00",
            },
        ]
        timeline = build_day_timeline(["Jahrgang 5", "Jahrgang 6"], competitions)
        self.assertIsNotNone(timeline)
        labels = [col["label"] for col in timeline["columns"]]
        self.assertEqual(labels, ["Jahrgang 5", "Jahrgang 6"])
        self.assertEqual(len(timeline["columns"][0]["items"]), 1)
        self.assertEqual(len(timeline["columns"][1]["items"]), 1)


if __name__ == "__main__":
    unittest.main()
