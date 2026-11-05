import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi.testclient import TestClient

import app.database as database
from app.database import get_conn, init_db


class QuickstartRouteTests(unittest.TestCase):
    """Issue #75: /turnier-schnellstart soll in einem Schritt ein Turnier mit
    ausgewaehlten Teams anlegen und sofort einen vollstaendigen "jeder gegen
    jeden"-Rundenplan inkl. Finale/Spiel um Platz 3 speichern - ohne die
    separaten Vorschau-/Uebernehmen-Schritte des normalen Plan-Generators."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "quickstart-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

        with get_conn() as conn:
            for name in ("8a", "8b", "8c", "8d", "8f"):
                conn.execute("INSERT INTO teams (name, jahrgang) VALUES (?, 8)", (name,))
            self.team_ids = [
                row["id"] for row in conn.execute(
                    "SELECT id FROM teams ORDER BY id"
                ).fetchall()
            ]
            # Turnhalle ist der Standard-Ort einer neu angelegten Wettbewerbs-
            # zeile (kein location gesetzt) - siehe DEFAULT_COMPETITION_LOCATION.
            conn.execute("INSERT INTO courts (name, location) VALUES ('Feld 1', 'Turnhalle')")
            conn.execute("INSERT INTO courts (name, location) VALUES ('Feld 2', 'Turnhalle')")
            conn.commit()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_get_form_lists_teams_and_turnhalle_courts(self):
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            response = client.get("/turnier-schnellstart")
        self.assertEqual(response.status_code, 200)
        self.assertIn("8a", response.text)
        self.assertIn("Feld 1", response.text)

    def test_post_creates_competition_with_full_round_robin_and_direct_finals(self):
        from app.main import app as fastapi_app
        with get_conn() as conn:
            court_ids = [
                row["id"] for row in conn.execute(
                    "SELECT id FROM courts WHERE name IN ('Feld 1', 'Feld 2') ORDER BY name"
                ).fetchall()
            ]
        self.assertEqual(len(court_ids), 2)

        with TestClient(fastapi_app) as client:
            response = client.post(
                "/turnier-schnellstart",
                data={
                    "name": "Pausenturnier",
                    "sportart": "Zweifelderball",
                    "team_ids": [str(tid) for tid in self.team_ids],
                    "court_ids": [str(cid) for cid in court_ids],
                    "startzeit": "10:00",
                },
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertIn("/spielplan-bearbeiten?competition_id=", response.headers["location"])

        with get_conn() as conn:
            competition = conn.execute(
                "SELECT * FROM competitions WHERE name = 'Pausenturnier'"
            ).fetchone()
            self.assertIsNotNone(competition)
            self.assertEqual(competition["competition_type"], "Turnier")

            slots = conn.execute(
                "SELECT * FROM slots WHERE competition_id = ?", (competition["id"],)
            ).fetchall()

        group_slots = [s for s in slots if s["phase"] == "Gruppenphase"]
        # 5 Teams jeder gegen jeden = 10 Spiele.
        self.assertEqual(len(group_slots), 10)
        games_per_team = {}
        for slot in group_slots:
            games_per_team[slot["team_a_id"]] = games_per_team.get(slot["team_a_id"], 0) + 1
            games_per_team[slot["team_b_id"]] = games_per_team.get(slot["team_b_id"], 0) + 1
        self.assertEqual(set(games_per_team.values()), {4})

        phases = {s["phase"] for s in slots}
        self.assertNotIn("Halbfinale", phases)
        self.assertIn("Finale", phases)
        self.assertIn("Spiel um Platz 3", phases)

    def test_get_form_offers_turnier_modes_and_timing_fields(self):
        """Issue #114: der Turnier-Schnellstart fragt Spielzeit/Wechselzeit
        und Turniermodus (aus TURNIER_MODES) ab, statt sie fest zu verdrahten."""
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            response = client.get("/turnier-schnellstart")
        self.assertEqual(response.status_code, 200)
        self.assertIn('name="game_duration_minutes"', response.text)
        self.assertIn('name="changeover_duration_minutes"', response.text)
        self.assertIn("Gruppenphase mit KO-Runde", response.text)
        self.assertIn("Reine KO-Runde", response.text)
        self.assertIn("Punkterunde (Jeder gegen Jeden)", response.text)

    def test_post_uses_submitted_timing_values(self):
        from app.main import app as fastapi_app
        with get_conn() as conn:
            court_ids = [
                row["id"] for row in conn.execute(
                    "SELECT id FROM courts WHERE name IN ('Feld 1', 'Feld 2') ORDER BY name"
                ).fetchall()
            ]
        with TestClient(fastapi_app) as client:
            client.post(
                "/turnier-schnellstart",
                data={
                    "name": "Zeit-Turnier",
                    "team_ids": [str(tid) for tid in self.team_ids],
                    "court_ids": [str(cid) for cid in court_ids],
                    "startzeit": "10:00",
                    "game_duration_minutes": "8",
                    "changeover_duration_minutes": "3",
                },
                follow_redirects=False,
            )
        with get_conn() as conn:
            competition = conn.execute(
                "SELECT * FROM competitions WHERE name = 'Zeit-Turnier'"
            ).fetchone()
        self.assertIsNotNone(competition)
        self.assertEqual(competition["game_duration_minutes"], 8)
        self.assertEqual(competition["changeover_duration_minutes"], 3)

    def test_post_rejects_invalid_timing(self):
        from app.main import app as fastapi_app
        with get_conn() as conn:
            court_ids = [
                row["id"] for row in conn.execute(
                    "SELECT id FROM courts WHERE name IN ('Feld 1', 'Feld 2') ORDER BY name"
                ).fetchall()
            ]
        with TestClient(fastapi_app) as client:
            response = client.post(
                "/turnier-schnellstart",
                data={
                    "team_ids": [str(tid) for tid in self.team_ids],
                    "court_ids": [str(cid) for cid in court_ids],
                    "startzeit": "10:00",
                    "game_duration_minutes": "0",
                },
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        with get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM competitions").fetchone()["n"]
        self.assertEqual(count, 0)

    def test_post_with_ko_runde_mode_generates_ko_bracket_instead_of_group_plan(self):
        """Issue #114: turnier_schnellstart_create() ruft je nach gewaehltem
        Turniermodus die passende Generator-Funktion auf, statt immer
        generate_group_plan(..., include_ko=True) fest zu verdrahten."""
        from app.main import app as fastapi_app
        with get_conn() as conn:
            court_ids = [
                row["id"] for row in conn.execute(
                    "SELECT id FROM courts WHERE name IN ('Feld 1', 'Feld 2') ORDER BY name"
                ).fetchall()
            ]

        with TestClient(fastapi_app) as client:
            response = client.post(
                "/turnier-schnellstart",
                data={
                    "name": "KO-Turnier",
                    "team_ids": [str(tid) for tid in self.team_ids],
                    "court_ids": [str(cid) for cid in court_ids],
                    "startzeit": "10:00",
                    "tournament_mode": "ko_runde",
                },
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)

        with get_conn() as conn:
            competition = conn.execute(
                "SELECT * FROM competitions WHERE name = 'KO-Turnier'"
            ).fetchone()
            self.assertEqual(competition["tournament_mode"], "ko_runde")
            slots = conn.execute(
                "SELECT * FROM slots WHERE competition_id = ?", (competition["id"],)
            ).fetchall()

        phases = {s["phase"] for s in slots}
        self.assertNotIn("Gruppenphase", phases)
        self.assertIn("Halbfinale", phases)
        self.assertIn("Finale", phases)

    def test_post_with_punkterunde_mode_generates_round_robin_without_finals(self):
        from app.main import app as fastapi_app
        with get_conn() as conn:
            court_ids = [
                row["id"] for row in conn.execute(
                    "SELECT id FROM courts WHERE name IN ('Feld 1', 'Feld 2') ORDER BY name"
                ).fetchall()
            ]

        with TestClient(fastapi_app) as client:
            response = client.post(
                "/turnier-schnellstart",
                data={
                    "name": "Punkte-Turnier",
                    "team_ids": [str(tid) for tid in self.team_ids],
                    "court_ids": [str(cid) for cid in court_ids],
                    "startzeit": "10:00",
                    "tournament_mode": "punkterunde",
                },
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)

        with get_conn() as conn:
            competition = conn.execute(
                "SELECT * FROM competitions WHERE name = 'Punkte-Turnier'"
            ).fetchone()
            self.assertEqual(competition["tournament_mode"], "punkterunde")
            slots = conn.execute(
                "SELECT * FROM slots WHERE competition_id = ?", (competition["id"],)
            ).fetchall()

        phases = {s["phase"] for s in slots}
        self.assertEqual(phases, {"Gruppenphase"})
        # 5 Teams jeder gegen jeden = 10 Spiele, keine KO-Runde.
        self.assertEqual(len(slots), 10)

    def test_post_with_punkterunde_finals_flags_persists_and_creates_placeholders(self):
        """Issue #125: grosses/kleines Finale sind ueber den Schnellstart
        waehlbar und werden sowohl auf dem Wettbewerb persistiert als auch
        vom Punkterunde-Generator als Platzhalter-Slots angelegt."""
        from app.main import app as fastapi_app
        with get_conn() as conn:
            court_ids = [
                row["id"] for row in conn.execute(
                    "SELECT id FROM courts WHERE name IN ('Feld 1', 'Feld 2') ORDER BY name"
                ).fetchall()
            ]

        with TestClient(fastapi_app) as client:
            response = client.post(
                "/turnier-schnellstart",
                data={
                    "name": "Punkte-Turnier-mit-Finale",
                    "team_ids": [str(tid) for tid in self.team_ids],
                    "court_ids": [str(cid) for cid in court_ids],
                    "startzeit": "10:00",
                    "tournament_mode": "punkterunde",
                    "punkterunde_grosses_finale": "1",
                    "punkterunde_kleines_finale": "1",
                },
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)

        with get_conn() as conn:
            competition = conn.execute(
                "SELECT * FROM competitions WHERE name = 'Punkte-Turnier-mit-Finale'"
            ).fetchone()
            self.assertEqual(competition["punkterunde_grosses_finale"], 1)
            self.assertEqual(competition["punkterunde_kleines_finale"], 1)
            slots = conn.execute(
                "SELECT * FROM slots WHERE competition_id = ?", (competition["id"],)
            ).fetchall()

        phases = {s["phase"] for s in slots}
        self.assertIn("Finale", phases)
        self.assertIn("Spiel um Platz 3", phases)

    def test_post_without_finals_flags_defaults_to_zero(self):
        from app.main import app as fastapi_app
        with get_conn() as conn:
            court_ids = [
                row["id"] for row in conn.execute(
                    "SELECT id FROM courts WHERE name IN ('Feld 1', 'Feld 2') ORDER BY name"
                ).fetchall()
            ]

        with TestClient(fastapi_app) as client:
            client.post(
                "/turnier-schnellstart",
                data={
                    "name": "Punkte-Turnier-ohne-Finale",
                    "team_ids": [str(tid) for tid in self.team_ids],
                    "court_ids": [str(cid) for cid in court_ids],
                    "startzeit": "10:00",
                    "tournament_mode": "punkterunde",
                },
                follow_redirects=False,
            )

        with get_conn() as conn:
            competition = conn.execute(
                "SELECT * FROM competitions WHERE name = 'Punkte-Turnier-ohne-Finale'"
            ).fetchone()
        self.assertEqual(competition["punkterunde_grosses_finale"], 0)
        self.assertEqual(competition["punkterunde_kleines_finale"], 0)

    def test_post_with_too_few_teams_does_not_create_competition(self):
        from app.main import app as fastapi_app
        with get_conn() as conn:
            court_id = conn.execute(
                "SELECT id FROM courts WHERE name = 'Feld 1'"
            ).fetchone()["id"]

        with TestClient(fastapi_app) as client:
            client.post(
                "/turnier-schnellstart",
                data={
                    "team_ids": [str(self.team_ids[0])],
                    "court_ids": [str(court_id)],
                    "startzeit": "10:00",
                },
                follow_redirects=False,
            )

        with get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM competitions").fetchone()["n"]
        self.assertEqual(count, 0)

    def _court_ids(self):
        with get_conn() as conn:
            return [
                row["id"] for row in conn.execute(
                    "SELECT id FROM courts WHERE name IN ('Feld 1', 'Feld 2') ORDER BY name"
                ).fetchall()
            ]

    def _add_existing_game(self, court_id, startzeit="10:00"):
        """Ein bereits gespeichertes Spiel eines anderen Wettbewerbs auf Feld 1."""
        with get_conn() as conn:
            cursor = conn.execute(
                "INSERT INTO competitions (name, sportart, jahrgang, competition_type) "
                "VALUES ('Fussball 8', 'Fussball', 8, 'Turnier')"
            )
            conn.execute(
                "INSERT INTO slots (competition_id, court_id, startzeit, slot_typ, phase, "
                "team_a_id, team_b_id, status) VALUES (?, ?, ?, 'Spiel', 'Gruppenphase', ?, ?, 'geplant')",
                (cursor.lastrowid, court_id, startzeit, self.team_ids[0], self.team_ids[1]),
            )
            conn.commit()

    def _post_quickstart(self, client, **extra):
        data = {
            "name": "Konflikt-Turnier",
            "team_ids": [str(tid) for tid in self.team_ids],
            "court_ids": [str(cid) for cid in self._court_ids()],
            "startzeit": "10:00",
        }
        data.update(extra)
        return client.post("/turnier-schnellstart", data=data, follow_redirects=False)

    def test_post_with_overlap_shows_conflicts_and_creates_nothing(self):
        """Wie beim Uebernehmen im Plan-Generator (#145) wird gegen den
        bestehenden Spielplan geprueft. Der Schnellstart hat kein Datum,
        Ueberschneidungen sind deshalb Hinweise, die bestaetigt werden muessen."""
        from app.main import app as fastapi_app
        self._add_existing_game(self._court_ids()[0])

        with TestClient(fastapi_app) as client:
            response = self._post_quickstart(client)

        self.assertEqual(response.status_code, 409)
        self.assertIn("Mögliche Überschneidungen", response.text)
        self.assertIn("Feld 1", response.text)
        self.assertIn("Trotzdem anlegen", response.text)
        self.assertIn('name="confirm_conflicts" value="1"', response.text)
        with get_conn() as conn:
            self.assertIsNone(conn.execute(
                "SELECT id FROM competitions WHERE name = 'Konflikt-Turnier'"
            ).fetchone())
            self.assertEqual(
                conn.execute("SELECT COUNT(*) AS n FROM competition_teams").fetchone()["n"], 0
            )
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM slots").fetchone()["n"], 1)

    def test_post_with_confirmed_overlap_creates_plan(self):
        from app.main import app as fastapi_app
        self._add_existing_game(self._court_ids()[0])

        with TestClient(fastapi_app) as client:
            response = self._post_quickstart(client, confirm_conflicts="1")

        self.assertEqual(response.status_code, 303)
        with get_conn() as conn:
            competition = conn.execute(
                "SELECT id FROM competitions WHERE name = 'Konflikt-Turnier'"
            ).fetchone()
            self.assertIsNotNone(competition)
            self.assertGreater(conn.execute(
                "SELECT COUNT(*) AS n FROM slots WHERE competition_id = ?", (competition["id"],)
            ).fetchone()["n"], 0)

    def test_post_with_internal_conflict_is_blocked_even_when_confirmed(self):
        """Fehler (hier: Plan in sich doppelt belegt) blockieren immer."""
        from app.main import app as fastapi_app
        broken_plan = [
            {"competition_id": None, "court_id": self._court_ids()[0], "startzeit": "10:00",
             "slot_typ": "Spiel", "phase": "Gruppenphase", "gruppe": "",
             "team_a_id": self.team_ids[0], "team_b_id": self.team_ids[1], "note": ""},
            {"competition_id": None, "court_id": self._court_ids()[0], "startzeit": "10:00",
             "slot_typ": "Spiel", "phase": "Gruppenphase", "gruppe": "",
             "team_a_id": self.team_ids[2], "team_b_id": self.team_ids[3], "note": ""},
        ]

        def fake_generate(competition_id, **kwargs):
            return [dict(slot, competition_id=competition_id) for slot in broken_plan]

        with patch("app.routes.quickstart.generate_group_plan", side_effect=fake_generate):
            with TestClient(fastapi_app) as client:
                response = self._post_quickstart(client, confirm_conflicts="1")

        self.assertEqual(response.status_code, 409)
        self.assertIn("doppelt belegt", response.text)
        self.assertNotIn("Trotzdem anlegen", response.text)
        with get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM competitions").fetchone()["n"], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM slots").fetchone()["n"], 0)


if __name__ == "__main__":
    unittest.main()
