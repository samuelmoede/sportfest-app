"""Tests fuer die automatische Weiterbesetzung der KO-Runden im Turniermodus
"Reine KO-Runde" (Issue #101, Teilschritt 3): plan_ko_advancement() /
advance_ko_rounds() in app/services/tournament_modes/ko_runde.py sowie die
Route /competition/{id}/advance-ko-round.

Kernpruefung: Fuer jede Teamzahl von 2 bis 32 wird ein kompletter
KO-Baum erzeugt, gespeichert und Runde fuer Runde durchgespielt - am Ende
muss das Finale (und ggf. das Spiel um Platz 3) mit den richtigen Teams
besetzt sein, ohne dass jemand Teams manuell eintraegt."""
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
import app.services.tournament_modes.ko_runde as ko_service
from app.database import init_db
from app.services.tournament_modes.ko_runde import (
    advance_ko_rounds,
    generate_ko_plan,
    plan_ko_advancement,
)
from tests.test_ko_runde_service import _make_ko_competition, make_in_memory_db

INSERT_SLOT_SQL = """
    INSERT INTO slots (
        competition_id, court_id, startzeit, slot_typ, phase, gruppe,
        team_a_id, team_b_id, status, note
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'geplant', ?)
"""


def _persist_plan(conn, slots):
    """Speichert den Vorschlag in derselben Reihenfolge wie
    /plan-generator/apply (eine Zeile pro Slot, IDs aufsteigend)."""
    for slot in slots:
        conn.execute(INSERT_SLOT_SQL, (
            slot["competition_id"], slot["court_id"], slot["startzeit"],
            slot["slot_typ"], slot["phase"], slot["gruppe"] or None,
            slot["team_a_id"] or None, slot["team_b_id"] or None,
            slot["note"] or None,
        ))
    conn.commit()


def _build_tournament(team_count, court_count=2):
    conn = make_in_memory_db()
    court_ids, teams = _make_ko_competition(conn, team_count, court_count)
    with patch.object(ko_service, "get_conn", return_value=conn):
        slots = generate_ko_plan(competition_id=1, court_ids=court_ids, startzeit="09:00")
    _persist_plan(conn, slots)
    return conn, teams


def _finish(conn, slot_id, score_a, score_b):
    conn.execute(
        "UPDATE slots SET score_a = ?, score_b = ?, status = 'beendet' WHERE id = ?",
        (score_a, score_b, slot_id),
    )
    conn.commit()


def _ko_slots(conn, phase=None):
    if phase is None:
        return conn.execute(
            "SELECT * FROM slots WHERE competition_id = 1 ORDER BY id"
        ).fetchall()
    return conn.execute(
        "SELECT * FROM slots WHERE competition_id = 1 AND phase = ? ORDER BY id",
        (phase,),
    ).fetchall()


class KoAdvancementSimulationTests(unittest.TestCase):
    def test_full_bracket_is_filled_round_by_round(self):
        """Das Team mit der kleineren ID gewinnt immer. Damit muss Team 1
        (Setzplatz 1) Turniersieger werden und jedes Spiel, sobald es an der
        Reihe ist, zwei Teams haben."""
        for team_count in range(2, 33):
            with self.subTest(team_count=team_count):
                conn, teams = _build_tournament(team_count)
                with patch.object(ko_service, "get_conn", return_value=conn):
                    for _ in range(10):
                        open_games = [
                            slot for slot in _ko_slots(conn)
                            if slot["status"] == "geplant"
                            and slot["team_a_id"] is not None
                            and slot["team_b_id"] is not None
                        ]
                        if not open_games:
                            break
                        for slot in open_games:
                            if slot["team_a_id"] < slot["team_b_id"]:
                                _finish(conn, slot["id"], 2, 1)
                            else:
                                _finish(conn, slot["id"], 1, 2)
                        result = advance_ko_rounds(1)
                        self.assertEqual(result["warnings"], [])

                remaining = [slot for slot in _ko_slots(conn) if slot["status"] != "beendet"]
                self.assertEqual(
                    remaining, [],
                    f"Nicht alle Spiele wurden besetzt/gespielt: "
                    f"{[(s['phase'], s['team_a_id'], s['team_b_id']) for s in remaining]}",
                )

                final = _ko_slots(conn, "Finale")[0]
                winner = final["team_a_id"] if final["score_a"] > final["score_b"] else final["team_b_id"]
                self.assertEqual(winner, teams[0]["id"])

                # Jedes Team scheidet genau einmal aus: N - 1 KO-Spiele.
                main_games = [s for s in _ko_slots(conn) if s["phase"] != "Spiel um Platz 3"]
                self.assertEqual(len(main_games), team_count - 1)

    def test_small_final_gets_semifinal_losers(self):
        conn, teams = _build_tournament(4)
        with patch.object(ko_service, "get_conn", return_value=conn):
            semifinals = _ko_slots(conn, "Halbfinale")
            _finish(conn, semifinals[0]["id"], 3, 0)
            _finish(conn, semifinals[1]["id"], 0, 3)
            advance_ko_rounds(1)

        final = _ko_slots(conn, "Finale")[0]
        small_final = _ko_slots(conn, "Spiel um Platz 3")[0]
        self.assertEqual(
            (final["team_a_id"], final["team_b_id"]),
            (semifinals[0]["team_a_id"], semifinals[1]["team_b_id"]),
        )
        self.assertEqual(
            (small_final["team_a_id"], small_final["team_b_id"]),
            (semifinals[0]["team_b_id"], semifinals[1]["team_a_id"]),
        )

    def test_single_decided_game_is_advanced_immediately(self):
        """Nicht erst warten, bis die ganze Runde beendet ist."""
        conn, _ = _build_tournament(8)
        quarterfinals = _ko_slots(conn, "Viertelfinale")
        _finish(conn, quarterfinals[0]["id"], 4, 1)

        plan = plan_ko_advancement(conn, 1)
        semifinal_1 = _ko_slots(conn, "Halbfinale")[0]
        self.assertEqual(plan["updates"], [{
            "slot_id": semifinal_1["id"],
            "column": "team_a_id",
            "team_id": quarterfinals[0]["team_a_id"],
        }])

    def test_dragged_slots_keep_their_bracket_position(self):
        """Drag & Drop aendert Uhrzeit/Feld, nicht die ID - die Zuordnung zum
        Baum darf sich dadurch nicht verschieben."""
        conn, _ = _build_tournament(8)
        quarterfinals = _ko_slots(conn, "Viertelfinale")
        conn.execute("UPDATE slots SET startzeit = '23:00' WHERE id = ?", (quarterfinals[0]["id"],))
        _finish(conn, quarterfinals[1]["id"], 0, 2)

        plan = plan_ko_advancement(conn, 1)
        semifinal_1 = _ko_slots(conn, "Halbfinale")[0]
        self.assertEqual(plan["updates"], [{
            "slot_id": semifinal_1["id"],
            "column": "team_b_id",
            "team_id": quarterfinals[1]["team_b_id"],
        }])

    def test_corrected_result_replaces_not_yet_started_follow_up(self):
        conn, _ = _build_tournament(4)
        with patch.object(ko_service, "get_conn", return_value=conn):
            semifinal = _ko_slots(conn, "Halbfinale")[0]
            _finish(conn, semifinal["id"], 2, 0)
            advance_ko_rounds(1)
            _finish(conn, semifinal["id"], 0, 2)
            advance_ko_rounds(1)

        final = _ko_slots(conn, "Finale")[0]
        self.assertEqual(final["team_a_id"], semifinal["team_b_id"])

    def test_started_follow_up_game_is_never_changed(self):
        conn, _ = _build_tournament(4)
        with patch.object(ko_service, "get_conn", return_value=conn):
            semifinal = _ko_slots(conn, "Halbfinale")[0]
            _finish(conn, semifinal["id"], 2, 0)
            advance_ko_rounds(1)
            final = _ko_slots(conn, "Finale")[0]
            conn.execute("UPDATE slots SET status = 'läuft' WHERE id = ?", (final["id"],))
            _finish(conn, semifinal["id"], 0, 2)
            result = advance_ko_rounds(1)

        self.assertEqual(_ko_slots(conn, "Finale")[0]["team_a_id"], semifinal["team_a_id"])
        self.assertFalse(any(u["slot_id"] == final["id"] for u in result["updates"]))

    def test_draw_is_reported_and_not_advanced(self):
        conn, _ = _build_tournament(4)
        semifinal = _ko_slots(conn, "Halbfinale")[0]
        _finish(conn, semifinal["id"], 1, 1)

        plan = plan_ko_advancement(conn, 1)
        self.assertEqual(plan["updates"], [])
        self.assertEqual(len(plan["warnings"]), 1)
        self.assertIn("unentschieden", plan["warnings"][0])

    def test_deleted_slot_is_reported_instead_of_guessing(self):
        conn, _ = _build_tournament(8)
        quarterfinals = _ko_slots(conn, "Viertelfinale")
        _finish(conn, quarterfinals[0]["id"], 2, 0)
        conn.execute("DELETE FROM slots WHERE id = ?", (quarterfinals[3]["id"],))

        plan = plan_ko_advancement(conn, 1)
        self.assertEqual(plan["updates"], [])
        self.assertEqual(len(plan["warnings"]), 1)

    def test_competition_without_ko_slots_has_nothing_to_do(self):
        conn = make_in_memory_db()
        _make_ko_competition(conn, 4, 2)
        self.assertEqual(plan_ko_advancement(conn, 1), {"updates": [], "warnings": []})


class AdvanceKoRoundRouteTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "ko-advance-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def _setup(self, tournament_mode):
        with database.get_conn() as conn:
            conn.execute("DELETE FROM courts")
            court_ids, _ = _make_ko_competition(conn, 4, 2, tournament_mode=tournament_mode)
        slots = generate_ko_plan(competition_id=1, court_ids=court_ids, startzeit="09:00")
        with database.get_conn() as conn:
            _persist_plan(conn, slots)
            semifinals = _ko_slots(conn, "Halbfinale")
            _finish(conn, semifinals[0]["id"], 5, 2)
            _finish(conn, semifinals[1]["id"], 1, 3)
        return semifinals

    def test_route_fills_final_and_editor_shows_button(self):
        from app.main import app as fastapi_app

        semifinals = self._setup("ko_runde")
        with TestClient(fastapi_app) as client:
            page = client.get("/spielplan-bearbeiten?competition_id=1")
            self.assertEqual(page.status_code, 200)
            self.assertIn("Nächste KO-Runde automatisch besetzen", page.text)

            response = client.post("/competition/1/advance-ko-round", follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertIn("/spielplan-bearbeiten?competition_id=1", response.headers["location"])

        with database.get_conn() as conn:
            final = _ko_slots(conn, "Finale")[0]
        self.assertEqual(
            (final["team_a_id"], final["team_b_id"]),
            (semifinals[0]["team_a_id"], semifinals[1]["team_b_id"]),
        )

    def test_route_ignores_other_tournament_modes(self):
        from app.main import app as fastapi_app

        self._setup("gruppenphase_ko")
        with TestClient(fastapi_app) as client:
            client.post("/competition/1/advance-ko-round", follow_redirects=False)

        with database.get_conn() as conn:
            final = _ko_slots(conn, "Finale")[0]
        self.assertIsNone(final["team_a_id"])
        self.assertIsNone(final["team_b_id"])


if __name__ == "__main__":
    unittest.main()
