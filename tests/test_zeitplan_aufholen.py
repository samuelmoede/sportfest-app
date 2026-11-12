"""Tests fuer "Zeitplan aufholen" (app/services/zeitplan_aufholen_service.py),
die Spielzeit in Sekunden und die Durchsage an alle
(app/services/durchsage_service.py, app/routes/zeitplan.py)."""
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
from app.main import get_required_role
from app.services.durchsage_service import aktuelle_durchsage, durchsage_senden
from app.services.programmpunkte_service import create_programmpunkt
from app.services.schedule_grid_service import get_all_slots
from app.services.schedule_time_service import format_duration_seconds, get_competition_timing
from app.services.zeitplan_aufholen_service import (
    berechne_aufholen,
    durchsage_text,
    load_block_competitions,
    uebernehmen_aufholen,
)


class SpielzeitSekundenTests(unittest.TestCase):
    def test_seconds_override_minutes(self):
        timing = get_competition_timing({"game_duration_minutes": 7, "game_duration_seconds": 390})
        self.assertEqual(timing["game_duration_seconds"], 390)
        self.assertEqual(get_competition_timing({"game_duration_minutes": 7})["game_duration_seconds"], 420)
        self.assertEqual(format_duration_seconds(390), "6:30")
        self.assertEqual(format_duration_seconds(420), "7")


class ZeitplanAufholenTests(unittest.TestCase):
    """Zweifelderball 7 und 8 abwechselnd: 16 Runden alle 10 Minuten ab 08:10
    (7 Min. Spiel + 3 Min. Wechsel), letzte Runde 10:40 und damit schon laut
    Plan 7 Minuten zu knapp fuer die Siegerehrung um 10:40."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._patcher = patch.object(database, "DB_PATH", Path(self._tmpdir.name) / "aufholen.db")
        self._patcher.start()
        init_db()
        with get_conn() as conn:
            self.event_id = conn.execute(
                "INSERT INTO events (name, event_date, event_type) VALUES ('Schulpokal', '2026-12-17', 'Schulpokal')"
            ).lastrowid
            court = conn.execute("INSERT INTO courts (name, location, active) VALUES ('Feld 1', 'Turnhalle', 1)").lastrowid
            self.ids = {}
            for jahrgang in (7, 8):
                team_a = conn.execute("INSERT INTO teams (name, jahrgang, active) VALUES (?, ?, 1)", (f"{jahrgang}a", jahrgang)).lastrowid
                team_b = conn.execute("INSERT INTO teams (name, jahrgang, active) VALUES (?, ?, 1)", (f"{jahrgang}b", jahrgang)).lastrowid
                self.ids[jahrgang] = conn.execute(
                    """
                    INSERT INTO competitions (name, sportart, jahrgang, status, event_id, competition_type,
                                              location, start_time, end_time,
                                              game_duration_minutes, changeover_duration_minutes)
                    VALUES (?, 'Zweifelderball', ?, 'geplant', ?, 'Schulpokal', 'Turnhalle', '08:00', '10:40', 7, 3)
                    """,
                    (f"Zweifelderball Jahrgang {jahrgang}", jahrgang, self.event_id),
                ).lastrowid
                self.teams = (team_a, team_b)
                for index in range(8):
                    minute = 8 * 60 + 10 + (index * 2 + (jahrgang - 7)) * 10
                    conn.execute(
                        """
                        INSERT INTO slots (competition_id, court_id, startzeit, slot_typ, phase,
                                           team_a_id, team_b_id, status)
                        VALUES (?, ?, ?, 'Spiel', 'Gruppenphase', ?, ?, 'geplant')
                        """,
                        (self.ids[jahrgang], court, f"{minute // 60:02d}:{minute % 60:02d}", team_a, team_b),
                    )
            # anderer Block (Volleyball), darf nicht mitverschoben werden
            conn.execute(
                """
                INSERT INTO competitions (name, sportart, jahrgang, status, event_id, competition_type,
                                          location, start_time, end_time)
                VALUES ('Volleyball Jahrgang 9', 'Volleyball', 9, 'geplant', ?, 'Schulpokal', 'Turnhalle', '11:10', '13:40')
                """,
                (self.event_id,),
            )
            create_programmpunkt(conn, self.event_id, "Siegerehrung", "10:40", "10:45", ["7", "8"])
            conn.commit()

    def tearDown(self):
        self._patcher.stop()
        self._tmpdir.cleanup()

    def test_block_contains_both_jahrgaenge_only(self):
        with get_conn() as conn:
            names = [c["name"] for c in load_block_competitions(conn, self.ids[7])]
        self.assertEqual(names, ["Zweifelderball Jahrgang 7", "Zweifelderball Jahrgang 8"])

    def test_preview_without_delay_is_on_plan(self):
        with get_conn() as conn:
            v = berechne_aufholen(conn, self.ids[7])
        self.assertEqual(len(v["rounds"]), 16)
        self.assertEqual((v["ende_plan"], v["ende_ohne"], v["ende_neu"]), ("10:47", "10:47", "10:47"))
        self.assertEqual(v["ziel"]["label"], "Siegerehrung")
        self.assertEqual(v["puffer_ohne"], -7)

    def test_delay_and_shorter_games(self):
        # Um 09:00 laeuft die Runde von 08:30 erst seit 08:50: 20 Minuten
        # hinter dem Plan, 13 Runden stehen noch aus.
        with get_conn() as conn:
            conn.execute(
                "UPDATE slots SET status = 'beendet', started_at = '2026-12-17 08:12:00' WHERE startzeit < '08:30'"
            )
            conn.execute(
                "UPDATE slots SET status = 'läuft', started_at = '2026-12-17 08:50:00' WHERE startzeit = '08:30'"
            )
            now = datetime(2026, 12, 17, 9, 0)
            ohne = berechne_aufholen(conn, self.ids[7], now=now)
            self.assertEqual(ohne["naechste_runde"], "09:00")
            self.assertEqual(ohne["ende_ohne"], "11:07")
            self.assertEqual(ohne["puffer_ohne"], -27)
            neu = berechne_aufholen(conn, self.ids[7], spielzeit_sekunden=330, wechselzeit_minuten=2, now=now)
            # 13 Runden alle 7:30 ab 09:00 -> letzte Runde 10:30, Ende 10:35:30
            self.assertEqual(len(neu["rounds"]), 13)
            self.assertEqual(neu["rounds"][1]["neu"], "09:08")
            self.assertEqual(neu["ende_neu"], "10:36")
            self.assertEqual(neu["puffer_neu"], 4)
            self.assertIn("5:30 statt 7 Minuten", durchsage_text(neu))
            moved = uebernehmen_aufholen(conn, neu)
            conn.commit()
            self.assertEqual(moved, 13)
            competition = conn.execute("SELECT * FROM competitions WHERE id = ?", (self.ids[8],)).fetchone()
            self.assertEqual(
                (competition["game_duration_seconds"], competition["game_duration_minutes"],
                 competition["changeover_duration_minutes"]),
                (330, 6, 2),
            )
            running = conn.execute("SELECT startzeit FROM slots WHERE status = 'läuft'").fetchall()
            self.assertEqual({row["startzeit"] for row in running}, {"08:30"})
        slots = get_all_slots(competition_ids=[self.ids[7]])
        self.assertTrue(all(slot["planned_duration_seconds"] == 330 for slot in slots))

    def test_routes_preview_apply_and_announcement(self):
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            page = client.get(f"/zeitplan-aufholen?competition_id={self.ids[7]}&spielzeit_min=6&spielzeit_sek=30")
            self.assertEqual(page.status_code, 200)
            self.assertIn("Zeitplan aufholen", page.text)
            self.assertIn("Spielzeit ab sofort 6:30 statt 7 Minuten", page.text)
            response = client.post(
                "/zeitplan-aufholen/uebernehmen",
                data={
                    "competition_id": str(self.ids[7]), "spielzeit_min": "6", "spielzeit_sek": "30",
                    "wechselzeit": "3", "naechste_runde": "08:10",
                    "durchsage_senden": "1", "durchsage_text": "Ab sofort 6:30 Minuten Spielzeit",
                },
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)
            self.assertIn("gesendet=1", response.headers["location"])
            self.assertEqual(client.get("/durchsage/aktuell").json()["text"], "Ab sofort 6:30 Minuten Spielzeit")
            self.assertIn('id="durchsage"', client.get("/").text)
            editor = client.get(f"/spielplan-bearbeiten?competition_id={self.ids[7]}")
            self.assertIn(f"/zeitplan-aufholen?competition_id={self.ids[7]}", editor.text)
            durchsage_id = client.get("/durchsage/aktuell").json()["id"]
            client.post(f"/durchsage/{durchsage_id}/beenden", data={"zurueck": "/"})
            self.assertEqual(client.get("/durchsage/aktuell").json(), {})
        with get_conn() as conn:
            starts = [row["startzeit"] for row in conn.execute(
                "SELECT DISTINCT startzeit FROM slots WHERE competition_id IN (?, ?) ORDER BY startzeit",
                (self.ids[7], self.ids[8]),
            )]
        self.assertEqual(starts[:3], ["08:10", "08:20", "08:29"])

    def test_access_rules(self):
        self.assertEqual(get_required_role("/zeitplan-aufholen"), "tournament_lead")
        self.assertEqual(get_required_role("/zeitplan-aufholen/uebernehmen"), "tournament_lead")
        self.assertEqual(get_required_role("/durchsage/senden"), "tournament_lead")
        self.assertEqual(get_required_role("/durchsage/4/beenden"), "tournament_lead")
        self.assertIsNone(get_required_role("/durchsage/aktuell"))


class DurchsageServiceTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._patcher = patch.object(database, "DB_PATH", Path(self._tmpdir.name) / "durchsage.db")
        self._patcher.start()
        init_db()

    def tearDown(self):
        self._patcher.stop()
        self._tmpdir.cleanup()

    def test_new_announcement_replaces_old_and_empty_is_refused(self):
        with get_conn() as conn:
            durchsage_senden(conn, "Erste", "2026-12-17 09:00:00")
            second = durchsage_senden(conn, "  Zweite\n Zeile ", "2026-12-17 09:05:00")
            self.assertEqual(aktuelle_durchsage(conn)["id"], second)
            self.assertEqual(aktuelle_durchsage(conn)["text"], "Zweite Zeile")
            with self.assertRaises(ValueError):
                durchsage_senden(conn, "   ", "2026-12-17 09:06:00")


if __name__ == "__main__":
    unittest.main()
