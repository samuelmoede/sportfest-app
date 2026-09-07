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


if __name__ == "__main__":
    unittest.main()
