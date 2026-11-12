import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi.testclient import TestClient

import app.database as database
from app.database import get_conn, init_db
from app.services.beamer_service import (
    baue_beamer_seiten,
    baue_tabellen_seiten,
    naechster_programmpunkt,
)

JETZT = datetime(2026, 12, 17, 9, 24, 0)


def spiel(slot_id, feld, zeit, status, a, b, *, sportart="Zweifelderball", ort="Turnhalle",
          score=(None, None), started_at=None, name=None):
    return {
        "id": slot_id, "slot_typ": "Spiel", "status": status, "startzeit": zeit,
        "court_id": feld, "court_name": f"Feld {feld}", "team_a_id": 1, "team_b_id": 2,
        "team_a": a, "team_b": b, "score_a": score[0], "score_b": score[1],
        "sportart": sportart, "competition_name": name or f"{sportart} Jahrgang 7",
        "competition_start_time": "11:10" if ort == "Mensa" else "08:00",
        "effective_competition_location": ort,
        "phase": "Gruppenphase", "started_at": started_at, "planned_duration_seconds": 420,
        "competition_id": 1,
    }


class BeamerSeitenTests(unittest.TestCase):
    def test_ort_page_shows_running_games_and_next_game_per_court(self):
        slots = [
            spiel(1, 1, "09:00", "beendet", "7a", "7b", score=(2, 1)),
            spiel(2, 1, "09:20", "läuft", "7c", "7d", score=(1, 0), started_at="2026-12-17 09:20:00"),
            spiel(3, 1, "09:30", "geplant", "7a", "7c"),
            spiel(4, 1, "09:40", "geplant", "7b", "7d"),
            spiel(5, 2, "09:30", "geplant", "7e", "7f"),
        ]
        seiten = baue_beamer_seiten(slots, JETZT)["seiten"]
        ort = seiten[0]
        self.assertEqual((ort["typ"], ort["titel"]), ("ort", "Turnhalle"))
        self.assertEqual([k["team_a"] for k in ort["laufend"]], ["7c"])
        # Je Feld nur das naechste Spiel, nicht der ganze Tag.
        self.assertEqual([(k["feld"], k["zeit"]) for k in ort["naechste"]], [("Feld 1", "09:30"), ("Feld 2", "09:30")])
        self.assertEqual(ort["timer"], {"rest_sekunden": 180, "angehalten": False})
        self.assertEqual(seiten[-1]["typ"], "ergebnisse")
        self.assertEqual(seiten[-1]["karten"][0]["score_a"], 2)

    def test_no_shared_timer_when_games_were_started_separately(self):
        slots = [
            spiel(1, 1, "09:20", "läuft", "7a", "7b", started_at="2026-12-17 09:20:00"),
            spiel(2, 2, "09:20", "läuft", "7c", "7d", started_at="2026-12-17 09:22:00"),
        ]
        self.assertIsNone(baue_beamer_seiten(slots, JETZT)["seiten"][0]["timer"])

    def test_ort_filter_and_sport_colors(self):
        slots = [
            spiel(1, 1, "09:30", "geplant", "7a", "7b"),
            spiel(2, 5, "09:30", "geplant", "9a", "9b", sportart="Tischtennis", ort="Mensa",
                  name="Tischtennis Jungen Jahrgang 9"),
        ]
        alle = baue_beamer_seiten(slots, JETZT)
        self.assertEqual([s["titel"] for s in alle["seiten"]], ["Turnhalle", "Mensa"])
        self.assertEqual([e["farbe"] for e in alle["legende"]], [1, 2])
        mensa = baue_beamer_seiten(slots, JETZT, ort="Mensa")["seiten"]
        self.assertEqual([s["titel"] for s in mensa], ["Mensa"])
        karte = mensa[0]["naechste"][0]
        self.assertEqual((karte["farbe"], karte["wettbewerb"]), (2, "Tischtennis Jungen Jg. 9"))

    def test_tables_skip_empty_and_duplicate_overall_ranking(self):
        gesamt = [
            {"year_group": "Gesamtwertung Jahrgang 7", "competitions": [{"id": 1}],
             "rows": [{"place": 1, "team": "7a", "total_points": 3.0}]},
            {"year_group": "Gesamtwertung Jahrgang 9", "competitions": [{"id": 2}, {"id": 3}],
             "rows": [{"place": 1, "team": "9a", "total_points": 4.5}, {"place": 2, "team": "9b", "total_points": 0}]},
        ]
        tabellen = [
            {"name": "Zweifelderball Jahrgang 7", "rows": [
                {"team": "7a", "sp": 1, "pkt": 3.0, "plus": 2, "minus": 1},
                {"team": "7b", "sp": 0, "pkt": 0, "plus": 0, "minus": 0}]},
            {"name": "Volleyball Jahrgang 9", "rows": [{"team": "9a", "sp": 0, "pkt": 0}]},
        ]
        seiten = baue_tabellen_seiten(gesamt, tabellen)
        self.assertEqual(len(seiten), 1)
        self.assertEqual([k["titel"] for k in seiten[0]["karten"]],
                         ["Gesamtwertung Jahrgang 9", "Zweifelderball Jahrgang 7"])
        self.assertEqual(seiten[0]["karten"][0]["zeilen"][0], {"platz": 1, "name": "9a", "wert": "4,5"})
        self.assertEqual(len(seiten[0]["karten"][1]["zeilen"]), 2)
        self.assertEqual(seiten[0]["karten"][1]["zeilen"][0]["detail"], "2:1")

    def test_tables_can_be_switched_off(self):
        slots = [spiel(1, 1, "09:00", "beendet", "7a", "7b", score=(2, 1))]
        tabellen = [{"name": "ZB", "sportart": "Zweifelderball", "rows": [{"team": "7a", "sp": 1, "pkt": 3}]}]
        typen = lambda **kw: [s["typ"] for s in baue_beamer_seiten(slots, JETZT, tabellen=tabellen, **kw)["seiten"]]
        self.assertEqual(typen(), ["tabellen", "ergebnisse"])
        self.assertEqual(typen(mit_tabellen=False), ["ergebnisse"])

    def test_next_programmpunkt(self):
        punkte = [
            {"titel": "Eröffnung", "start_time": "08:00", "end_time": "08:10"},
            {"titel": "Siegerehrung", "start_time": "10:40", "end_time": "10:45"},
        ]
        self.assertEqual(naechster_programmpunkt(punkte, JETZT)["titel"], "Siegerehrung")
        laeuft = naechster_programmpunkt(punkte, datetime(2026, 12, 17, 8, 5))
        self.assertEqual((laeuft["titel"], laeuft["laeuft"]), ("Eröffnung", True))
        self.assertIsNone(naechster_programmpunkt(punkte, datetime(2026, 12, 17, 11, 0)))


class BeamerRouteTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._db_path_patcher = patch.object(database, "DB_PATH", Path(self._tmpdir.name) / "beamer-test.db")
        self._db_path_patcher.start()
        init_db()
        with get_conn() as conn:
            conn.execute("INSERT INTO courts (name, location, active) VALUES ('Feld 1', 'Turnhalle', 1)")
            for name in ("7a", "7b", "7c"):
                conn.execute("INSERT INTO teams (name, jahrgang, active) VALUES (?, 7, 1)", (name,))
            conn.execute("""
                INSERT INTO competitions (name, sportart, jahrgang, status, competition_type)
                VALUES ('Zweifelderball Jahrgang 7', 'Zweifelderball', 7, 'läuft', 'Turnier')
            """)
            for zeit, a, b, status, sa, sb in (
                ("09:00", 1, 2, "beendet", 3, 1), ("09:10", 1, 3, "läuft", 1, 0), ("09:20", 2, 3, "geplant", None, None),
            ):
                conn.execute("""
                    INSERT INTO slots (competition_id, court_id, startzeit, slot_typ, team_a_id, team_b_id,
                                       phase, status, score_a, score_b)
                    VALUES (1, 1, ?, 'Spiel', ?, ?, 'Gruppenphase', ?, ?, ?)
                """, (zeit, a, b, status, sa, sb))
            conn.commit()
        from app.main import app as fastapi_app
        self.client = TestClient(fastapi_app)

    def tearDown(self):
        self.client.close()
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_beamer_shows_pages_without_delay_or_confirm_button(self):
        text = self.client.get("/beamer").text
        for erwartet in ("Turnhalle", "Als Nächstes", "Zwischenstand", "Letzte Ergebnisse", "sport-1"):
            self.assertIn(erwartet, text)
        # Verspaetung nur fuer die Turnierleitung, Durchsage ohne Klick.
        self.assertNotIn("Verspätung", text)
        self.assertNotIn("Verstanden", text)

    def test_beamer_options(self):
        self.assertNotIn("Zwischenstand", self.client.get("/beamer?tabellen=aus").text)
        mensa = self.client.get("/beamer?ort=Mensa&hell=1&sekunden=3").text
        self.assertIn("Zurzeit kein Spiel", mensa)
        self.assertIn('class="hell"', mensa)
        self.assertIn('data-sekunden="5"', mensa)
        self.assertEqual(self.client.get("/beamer?ui_theme=modern").status_code, 200)


if __name__ == "__main__":
    unittest.main()
