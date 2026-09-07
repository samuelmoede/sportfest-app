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


def _make_competition_with_finished_round_robin(conn):
    """5 Teams, jeder gegen jeden bereits gespielt (kein Halbfinale), Finale/
    Spiel um Platz 3 als Platzhalter angelegt - wie generate_group_plan es
    fuer eine einzelne Runde ohne Gruppen-Split erzeugt (siehe Issue #75)."""
    # init_db() seedet bereits Standard-Spielfelder - hier eigene, eindeutig
    # benannte Felder anlegen statt fixer IDs, die mit den Seed-Feldern
    # kollidieren koennten.
    conn.execute("INSERT INTO courts (name) VALUES ('Feld 1'), ('Feld 2')")
    court_1 = conn.execute("SELECT id FROM courts WHERE name = 'Feld 1'").fetchone()["id"]
    court_2 = conn.execute("SELECT id FROM courts WHERE name = 'Feld 2'").fetchone()["id"]
    conn.execute("""INSERT INTO competitions
        (id, name, sportart, jahrgang, competition_type, status)
        VALUES (1, 'Schnellturnier', 'Zweifelderball', 8, 'Turnier', 'geplant')""")

    # Rangfolge nach Punkten: A(4 Siege) > B(3) > C(2) > D(1) > E(0)
    teams = {}
    for name in ("A", "B", "C", "D", "E"):
        conn.execute("INSERT INTO teams (name, jahrgang) VALUES (?, 8)", (name,))
        teams[name] = conn.execute(
            "SELECT id FROM teams WHERE name = ?", (name,)
        ).fetchone()["id"]

    results = [
        ("A", "B", 5, 2), ("A", "C", 5, 1), ("A", "D", 5, 1), ("A", "E", 5, 0),
        ("B", "C", 4, 2), ("B", "D", 4, 2), ("B", "E", 4, 0),
        ("C", "D", 3, 1), ("C", "E", 4, 1),
        ("D", "E", 4, 1),
    ]
    for team_a, team_b, score_a, score_b in results:
        conn.execute("""
            INSERT INTO slots (
                competition_id, court_id, startzeit, slot_typ, phase, gruppe,
                team_a_id, team_b_id, score_a, score_b, status
            ) VALUES (1, ?, '10:00', 'Spiel', 'Gruppenphase', '', ?, ?, ?, ?, 'beendet')
        """, (court_1, teams[team_a], teams[team_b], score_a, score_b))

    conn.execute("""
        INSERT INTO slots (
            competition_id, court_id, startzeit, slot_typ, phase, gruppe, status, note
        ) VALUES (1, ?, '11:00', 'Spiel', 'Finale', '', 'geplant', '')
    """, (court_1,))
    conn.execute("""
        INSERT INTO slots (
            competition_id, court_id, startzeit, slot_typ, phase, gruppe, status, note
        ) VALUES (1, ?, '11:00', 'Spiel', 'Spiel um Platz 3', '', 'geplant', '')
    """, (court_2,))
    conn.commit()
    return teams


class GenerateFinalsFromTableRouteTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "finals-from-table-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_fills_finale_and_platz3_from_table_ranking(self):
        with get_conn() as conn:
            teams = _make_competition_with_finished_round_robin(conn)

        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            response = client.post(
                "/competition/1/generate-finals-from-table", follow_redirects=False
            )
        self.assertEqual(response.status_code, 303)

        with get_conn() as conn:
            final_slot = conn.execute(
                "SELECT * FROM slots WHERE competition_id = 1 AND phase = 'Finale'"
            ).fetchone()
            platz3_slot = conn.execute(
                "SELECT * FROM slots WHERE competition_id = 1 AND phase = 'Spiel um Platz 3'"
            ).fetchone()

        self.assertEqual(
            {final_slot["team_a_id"], final_slot["team_b_id"]},
            {teams["A"], teams["B"]},
        )
        self.assertEqual(
            {platz3_slot["team_a_id"], platz3_slot["team_b_id"]},
            {teams["C"], teams["D"]},
        )
        self.assertEqual(final_slot["status"], "geplant")
        self.assertEqual(final_slot["note"], "Finale: Platz 1 gegen Platz 2 der Tabelle")

    def test_overwrite_of_already_played_final_requires_confirmation(self):
        with get_conn() as conn:
            teams = _make_competition_with_finished_round_robin(conn)
            conn.execute("""
                UPDATE slots
                SET team_a_id = ?, team_b_id = ?, score_a = 3, score_b = 1, status = 'beendet'
                WHERE competition_id = 1 AND phase = 'Finale'
            """, (teams["A"], teams["B"]))
            conn.commit()

        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            response = client.post(
                "/competition/1/generate-finals-from-table", follow_redirects=False
            )
            self.assertEqual(response.status_code, 303)
            self.assertIn("overwrite_warning=Finale", response.headers["location"])

            # Ohne Bestaetigung bleibt das gespeicherte Ergebnis unangetastet.
            with get_conn() as conn:
                final_slot = conn.execute(
                    "SELECT * FROM slots WHERE competition_id = 1 AND phase = 'Finale'"
                ).fetchone()
            self.assertEqual(final_slot["status"], "beendet")
            self.assertEqual(final_slot["score_a"], 3)

            response = client.post(
                "/competition/1/generate-finals-from-table",
                data={"confirm_overwrite": "1"},
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)

        with get_conn() as conn:
            final_slot = conn.execute(
                "SELECT * FROM slots WHERE competition_id = 1 AND phase = 'Finale'"
            ).fetchone()
        self.assertEqual(final_slot["status"], "geplant")
        self.assertIsNone(final_slot["score_a"])


if __name__ == "__main__":
    unittest.main()
