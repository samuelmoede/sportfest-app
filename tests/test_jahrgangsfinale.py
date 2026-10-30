"""Tests fuer das Jahrgangsfinale beim Schulpokal (Sieger J7 gegen Sieger J8),
siehe app/services/jahrgangsfinale_service.py."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi.testclient import TestClient

import app.database as database
from app.database import get_conn, init_db


class JahrgangsfinaleTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "jahrgangsfinale-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()
        with get_conn() as conn:
            self.event_id = conn.execute(
                "INSERT INTO events (name, event_date) VALUES ('Schulpokal', '2026-12-17')"
            ).lastrowid
            self.court_id = conn.execute(
                "INSERT INTO courts (name, location, active) VALUES ('Feld 1', 'Turnhalle', 1)"
            ).lastrowid
            self.teams = {}
            for name, jahrgang in (("7a", 7), ("7b", 7), ("8a", 8), ("8b", 8)):
                self.teams[name] = conn.execute(
                    "INSERT INTO teams (name, jahrgang, active) VALUES (?, ?, 1)", (name, jahrgang)
                ).lastrowid
            self.comp_7 = self._competition(conn, "Zweifelderball Jahrgang 7", 7)
            self.comp_8 = self._competition(conn, "Zweifelderball Jahrgang 8", 8)
            self.game_7 = self._game(conn, self.comp_7, "08:00", "7a", "7b")
            self.game_8 = self._game(conn, self.comp_8, "08:10", "8a", "8b")
            conn.commit()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def _competition(self, conn, name, jahrgang):
        return conn.execute(
            """
            INSERT INTO competitions (name, sportart, jahrgang, status, event_id,
                                      competition_type, tournament_mode, location)
            VALUES (?, 'Zweifelderball', ?, 'geplant', ?, 'Schulpokal', 'jeder_gegen_jeden', 'Turnhalle')
            """,
            (name, jahrgang, self.event_id),
        ).lastrowid

    def _game(self, conn, competition_id, startzeit, team_a, team_b):
        return conn.execute(
            """
            INSERT INTO slots (competition_id, court_id, startzeit, slot_typ, phase,
                               team_a_id, team_b_id, status)
            VALUES (?, ?, ?, 'Spiel', 'Gruppenphase', ?, ?, 'geplant')
            """,
            (competition_id, self.court_id, startzeit, self.teams[team_a], self.teams[team_b]),
        ).lastrowid

    def _finish(self, slot_id, score_a, score_b):
        with get_conn() as conn:
            conn.execute(
                "UPDATE slots SET score_a = ?, score_b = ?, status = 'beendet' WHERE id = ?",
                (score_a, score_b, slot_id),
            )
            conn.commit()

    def _create_final(self, client):
        response = client.post(
            f"/competition/{self.comp_7}/jahrgangsfinale/anlegen",
            data={
                "partner_competition_id": str(self.comp_8),
                "startzeit": "10:45",
                "court_id": str(self.court_id),
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        return int(parse_qs(urlsplit(response.headers["location"]).query)["competition_id"][0])

    def test_editor_offers_final_for_schulpokal(self):
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            page = client.get(f"/spielplan-bearbeiten?competition_id={self.comp_7}")
        self.assertIn("Jahrgangsfinale anlegen", page.text)
        self.assertIn("Zweifelderball Jahrgang 8", page.text)

    def test_create_final_with_placeholder_game(self):
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            final_id = self._create_final(client)
            page = client.get(f"/spielplan-bearbeiten?competition_id={final_id}")
        self.assertIn("Sieger ins Finale eintragen", page.text)
        with get_conn() as conn:
            final = conn.execute("SELECT * FROM competitions WHERE id = ?", (final_id,)).fetchone()
            slot = conn.execute("SELECT * FROM slots WHERE competition_id = ?", (final_id,)).fetchone()
        self.assertEqual(final["name"], "Zweifelderball Jahrgangsfinale 7/8")
        self.assertEqual(final["event_id"], self.event_id)
        self.assertEqual(final["final_source_ids"], f"{self.comp_7},{self.comp_8}")
        self.assertEqual(slot["phase"], "Finale")
        self.assertEqual(slot["startzeit"], "10:45")
        self.assertEqual(slot["court_id"], self.court_id)
        self.assertIsNone(slot["team_a_id"])

    def test_second_final_for_same_competition_is_refused(self):
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            self._create_final(client)
            response = client.post(
                f"/competition/{self.comp_8}/jahrgangsfinale/anlegen",
                data={"partner_competition_id": str(self.comp_7), "startzeit": "11:00"},
                follow_redirects=False,
            )
        self.assertIn("finale_hinweis", response.headers["location"])
        with get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM competitions WHERE final_source_ids IS NOT NULL"
            ).fetchone()["n"]
        self.assertEqual(count, 1)

    def test_fill_waits_for_open_games_then_takes_table_winners(self):
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            final_id = self._create_final(client)
            self._finish(self.game_7, 3, 1)
            response = client.post(
                f"/competition/{final_id}/jahrgangsfinale/besetzen", follow_redirects=False
            )
            self.assertIn("offen", parse_qs(urlsplit(response.headers["location"]).query)["finale_hinweis"][0])

            self._finish(self.game_8, 0, 2)
            response = client.post(
                f"/competition/{final_id}/jahrgangsfinale/besetzen", follow_redirects=False
            )
            self.assertIn("finale_ok=besetzt", response.headers["location"])
            tables = client.get(f"/tabellen?event_id={self.event_id}")
        self.assertEqual(tables.status_code, 200)
        with get_conn() as conn:
            slot = conn.execute("SELECT * FROM slots WHERE competition_id = ?", (final_id,)).fetchone()
            team_ids = {
                row["team_id"] for row in conn.execute(
                    "SELECT team_id FROM competition_teams WHERE competition_id = ?", (final_id,)
                )
            }
        self.assertEqual((slot["team_a_id"], slot["team_b_id"]), (self.teams["7a"], self.teams["8b"]))
        self.assertEqual(team_ids, {self.teams["7a"], self.teams["8b"]})

    def test_finished_final_is_not_overwritten(self):
        from app.main import app as fastapi_app
        self._finish(self.game_7, 3, 1)
        self._finish(self.game_8, 0, 2)
        with TestClient(fastapi_app) as client:
            final_id = self._create_final(client)
            client.post(f"/competition/{final_id}/jahrgangsfinale/besetzen")
            with get_conn() as conn:
                conn.execute(
                    "UPDATE slots SET score_a = 1, score_b = 0, status = 'beendet' WHERE competition_id = ?",
                    (final_id,),
                )
                conn.commit()
            response = client.post(
                f"/competition/{final_id}/jahrgangsfinale/besetzen", follow_redirects=False
            )
        self.assertIn("finale_hinweis", response.headers["location"])
        with get_conn() as conn:
            slot = conn.execute("SELECT * FROM slots WHERE competition_id = ?", (final_id,)).fetchone()
        self.assertEqual(slot["status"], "beendet")


    def test_manual_slot_on_busy_court_is_saved_with_hint(self):
        from app.main import app as fastapi_app
        with get_conn() as conn:
            final_id = conn.execute(
                """
                INSERT INTO competitions (name, sportart, jahrgang, status, event_id, competition_type)
                VALUES ('Manuell', 'Zweifelderball', 'mixed', 'geplant', ?, 'Turnier')
                """,
                (self.event_id,),
            ).lastrowid
            conn.commit()
        with TestClient(fastapi_app) as client:
            response = client.post("/slot/create", data={
                "competition_id": str(final_id),
                "startzeit": "08:00",
                "slot_typ": "Spiel",
                "court_id": str(self.court_id),
                "phase": "Finale",
                "team_a_id": str(self.teams["7a"]),
                "team_b_id": str(self.teams["8a"]),
            }, follow_redirects=False)
            hint = parse_qs(urlsplit(response.headers["location"]).query)["slot_hinweis"][0]
            page = client.get(response.headers["location"])
        self.assertIn("Feld 1", hint)
        self.assertIn("Gespeichert, bitte prüfen", page.text)
        with get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM slots WHERE competition_id = ?", (final_id,)
            ).fetchone()["n"]
        self.assertEqual(count, 1)

    def test_editor_preselects_same_sport_partner(self):
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            page = client.get(f"/spielplan-bearbeiten?competition_id={self.comp_7}")
        self.assertRegex(
            page.text,
            rf'name="partner_competition_ids" value="{self.comp_8}"\s+checked',
        )


if __name__ == "__main__":
    unittest.main()
