import re
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


class PunkterundeFinalsFromTableIntegrationTests(unittest.TestCase):
    """Issue #125: generate_punkterunde_plan() legt bei aktivierten Flags
    Finale-/Spiel-um-Platz-3-Platzhalter an (bisher tote Platzhalter, siehe
    Issue) - dieser Test faehrt den kompletten Weg von der Plan-Erzeugung
    ueber das Bespielen der Punkterunde bis zur automatischen Besetzung aus
    der Tabelle durch /generate-finals-from-table, genau wie es fuer den
    Standardmodus bereits in GenerateFinalsFromTableRouteTests getestet ist."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "punkterunde-finals-from-table-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_punkterunde_placeholders_are_filled_after_round_robin_finishes(self):
        from app.services.tournament_modes.punkterunde import generate_punkterunde_plan

        with get_conn() as conn:
            conn.execute("INSERT INTO courts (name) VALUES ('Feld 1'), ('Feld 2')")
            court_ids = [
                row["id"] for row in conn.execute(
                    "SELECT id FROM courts WHERE name IN ('Feld 1', 'Feld 2') ORDER BY name"
                ).fetchall()
            ]
            # Rangfolge nach Punkten soll A > B > C > D > E ergeben (siehe
            # rank-basierte Ergebnisvergabe unten).
            for name in ("A", "B", "C", "D", "E"):
                conn.execute("INSERT INTO teams (name, jahrgang) VALUES (?, 8)", (name,))
            conn.execute("""
                INSERT INTO competitions (
                    id, name, sportart, jahrgang, competition_type, tournament_mode,
                    status, punkterunde_grosses_finale, punkterunde_kleines_finale
                ) VALUES (1, 'Punkterunde-Finale-Test', 'Zweifelderball', 8, 'Turnier',
                          'punkterunde', 'geplant', 1, 1)
            """)
            conn.commit()

            proposed_slots = generate_punkterunde_plan(
                competition_id=1, court_ids=court_ids, startzeit="09:00"
            )

            rank = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4}
            for slot in proposed_slots:
                if slot["phase"] == "Gruppenphase":
                    if rank[slot["team_a"]] < rank[slot["team_b"]]:
                        score_a, score_b, status = 5, 1, "beendet"
                    else:
                        score_a, score_b, status = 1, 5, "beendet"
                else:
                    score_a = score_b = None
                    status = "geplant"
                conn.execute("""
                    INSERT INTO slots (
                        competition_id, court_id, startzeit, slot_typ, phase, gruppe,
                        team_a_id, team_b_id, score_a, score_b, status, note
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    slot["competition_id"], slot["court_id"], slot["startzeit"], slot["slot_typ"],
                    slot["phase"], slot["gruppe"] or None,
                    slot["team_a_id"] or None, slot["team_b_id"] or None,
                    score_a, score_b, status, slot["note"] or None,
                ))
            conn.commit()

            team_ids = {
                row["name"]: row["id"]
                for row in conn.execute("SELECT id, name FROM teams").fetchall()
            }

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

        self.assertIsNotNone(final_slot)
        self.assertIsNotNone(platz3_slot)
        self.assertEqual(
            {final_slot["team_a_id"], final_slot["team_b_id"]},
            {team_ids["A"], team_ids["B"]},
        )
        self.assertEqual(
            {platz3_slot["team_a_id"], platz3_slot["team_b_id"]},
            {team_ids["C"], team_ids["D"]},
        )
        self.assertEqual(final_slot["status"], "geplant")


class PunkterundeKoBoxVisibilityTests(unittest.TestCase):
    """Issue #125: der Bereich "Nächste Phase automatisch besetzen" (inkl.
    Button "Endplatzierung aus Tabelle besetzen") war fuer Punkterunde-
    Wettbewerbe zuvor pauschal ausgeblendet (can_generate_finals_from_table
    wurde in app/routes/schedule.py explizit fuer is_punkterunde
    unterdrueckt) - er soll nur erscheinen, wenn mindestens ein Finale-Flag
    aktiviert ist, und dann analog zum Standardmodus (ohne Gruppen-Split)
    aktiv/inaktiv je nach group_phase_finished() sein."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "punkterunde-ko-box-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def _make_finished_punkterunde_competition(self, conn, **finals_flags):
        conn.execute("""
            INSERT INTO competitions (
                id, name, sportart, jahrgang, competition_type, tournament_mode, status,
                punkterunde_grosses_finale, punkterunde_kleines_finale
            ) VALUES (1, 'Punkterunde', 'Zweifelderball', 8, 'Turnier', 'punkterunde', 'geplant', ?, ?)
        """, (
            finals_flags.get("grosses_finale", 0),
            finals_flags.get("kleines_finale", 0),
        ))
        for name in ("A", "B"):
            conn.execute("INSERT INTO teams (name, jahrgang) VALUES (?, 8)", (name,))
        team_ids = {
            row["name"]: row["id"]
            for row in conn.execute("SELECT id, name FROM teams").fetchall()
        }
        conn.execute("""
            INSERT INTO slots (
                competition_id, court_id, startzeit, slot_typ, phase, gruppe,
                team_a_id, team_b_id, score_a, score_b, status
            ) VALUES (1, NULL, '10:00', 'Spiel', 'Gruppenphase', '', ?, ?, 3, 1, 'beendet')
        """, (team_ids["A"], team_ids["B"]))
        conn.commit()

    def test_box_hidden_without_finals_flags(self):
        with get_conn() as conn:
            self._make_finished_punkterunde_competition(conn)

        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            response = client.get("/spielplan-bearbeiten?competition_id=1")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Nächste Phase automatisch besetzen", response.text)

    def test_box_enabled_once_round_robin_finished_with_flags(self):
        with get_conn() as conn:
            self._make_finished_punkterunde_competition(conn, grosses_finale=1, kleines_finale=1)

        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            response = client.get("/spielplan-bearbeiten?competition_id=1")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Nächste Phase automatisch besetzen", response.text)
        self.assertIn("Endplatzierung aus Tabelle besetzen", response.text)
        button_match = re.search(
            r'generate-finals-from-table.*?<button class="green" type="submit"[^>]*>',
            response.text, re.DOTALL,
        )
        self.assertIsNotNone(button_match)
        self.assertNotIn("disabled", button_match.group(0))


if __name__ == "__main__":
    unittest.main()
