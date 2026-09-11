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
from app.services.schedule_grid_service import build_editor_time_grid


def _slot(court_id, startzeit, slot_id):
    return {"court_id": court_id, "startzeit": startzeit, "slot_typ": "Spiel", "id": slot_id}


def _court(court_id, name):
    return {"id": court_id, "name": name}


class GroupSlotsByCourtTimeGridTests(unittest.TestCase):
    """Issue #53: auf /ergebnisse sollen zeitgleiche Spiele ueber die
    Feld-Spalten hinweg in derselben Zeile stehen, statt lose je Feld
    gestapelt zu werden. _group_slots_by_court() gruppiert Slots weiterhin
    je Feld - build_editor_time_grid() (bereits fuer den Spielplan-Editor
    genutzt) baut daraus zusaetzlich das gemeinsame Zeit-Raster."""

    def test_same_startzeit_gets_same_grid_row_across_courts(self):
        from app.main import _group_slots_by_court

        courts = [_court(1, "Feld 1"), _court(2, "Feld 2")]
        slots = [
            _slot(1, "10:00", 101),
            _slot(2, "10:00", 102),
            _slot(1, "10:10", 103),
        ]

        columns = _group_slots_by_court(slots, courts)
        build_editor_time_grid(columns)

        feld1 = next(c for c in columns if c["court_id"] == 1)
        feld2 = next(c for c in columns if c["court_id"] == 2)

        row_feld1 = next(cell for cell in feld1["time_cells"] if cell["startzeit"] == "10:00")
        row_feld2 = next(cell for cell in feld2["time_cells"] if cell["startzeit"] == "10:00")

        self.assertEqual(row_feld1["editor_grid_row"], row_feld2["editor_grid_row"])
        self.assertEqual([s["id"] for s in row_feld1["slots"]], [101])
        self.assertEqual([s["id"] for s in row_feld2["slots"]], [102])

    def test_missing_time_on_one_court_becomes_empty_placeholder_row(self):
        from app.main import _group_slots_by_court

        courts = [_court(1, "Feld 1"), _court(2, "Feld 2")]
        slots = [
            _slot(1, "10:00", 101),
            _slot(2, "10:00", 102),
            _slot(1, "10:10", 103),
        ]

        columns = _group_slots_by_court(slots, courts)
        build_editor_time_grid(columns)

        feld1 = next(c for c in columns if c["court_id"] == 1)
        feld2 = next(c for c in columns if c["court_id"] == 2)

        row_feld1_1010 = next(cell for cell in feld1["time_cells"] if cell["startzeit"] == "10:10")
        row_feld2_1010 = next(cell for cell in feld2["time_cells"] if cell["startzeit"] == "10:10")

        self.assertEqual([s["id"] for s in row_feld1_1010["slots"]], [103])
        self.assertEqual(row_feld2_1010["slots"], [])
        # beide Felder bekommen trotzdem dieselbe Zeile fuer 10:10 zugewiesen
        self.assertEqual(row_feld1_1010["editor_grid_row"], row_feld2_1010["editor_grid_row"])

    def test_time_marks_are_ordered_chronologically(self):
        from app.main import _group_slots_by_court

        columns = _group_slots_by_court(
            [_slot(1, "10:10", 1), _slot(1, "10:00", 2)],
            [_court(1, "Feld 1")],
        )

        time_marks = build_editor_time_grid(columns)

        self.assertEqual(time_marks, ["10:00", "10:10"])


def _create_competition_and_teams(conn):
    conn.execute("INSERT INTO courts (name) VALUES ('Feld 1')")
    conn.execute("INSERT INTO courts (name) VALUES ('Feld 2')")
    court_1_id = conn.execute(
        "SELECT id FROM courts WHERE name = 'Feld 1'"
    ).fetchone()["id"]
    court_2_id = conn.execute(
        "SELECT id FROM courts WHERE name = 'Feld 2'"
    ).fetchone()["id"]

    conn.execute(
        """
        INSERT INTO competitions (name, sportart, jahrgang, status, competition_type)
        VALUES ('Testturnier', 'Fußball', 5, 'geplant', 'Turnier')
        """
    )
    competition_id = conn.execute(
        "SELECT id FROM competitions WHERE name = 'Testturnier'"
    ).fetchone()["id"]

    conn.execute("INSERT INTO teams (name, jahrgang) VALUES ('Team A', 5)")
    conn.execute("INSERT INTO teams (name, jahrgang) VALUES ('Team B', 5)")
    conn.execute("INSERT INTO teams (name, jahrgang) VALUES ('Team C', 5)")
    team_a_id = conn.execute("SELECT id FROM teams WHERE name = 'Team A'").fetchone()["id"]
    team_b_id = conn.execute("SELECT id FROM teams WHERE name = 'Team B'").fetchone()["id"]
    team_c_id = conn.execute("SELECT id FROM teams WHERE name = 'Team C'").fetchone()["id"]
    return competition_id, court_1_id, court_2_id, team_a_id, team_b_id, team_c_id


def _insert_slot(conn, *, competition_id, court_id, team_a_id, team_b_id, startzeit):
    conn.execute(
        """
        INSERT INTO slots (
            competition_id, court_id, startzeit, slot_typ, phase, gruppe,
            team_a_id, team_b_id, status
        ) VALUES (?, ?, ?, 'Spiel', 'Gruppenphase', 'A', ?, ?, 'geplant')
        """,
        (competition_id, court_id, startzeit, team_a_id, team_b_id),
    )


class ErgebnisseTimeAlignedColumnsRouteTests(unittest.TestCase):
    """HTTP-Ebene: prueft, dass /ergebnisse das Zeit-Raster tatsaechlich
    rendert (Zeit-Beschriftungen je eindeutiger Startzeit, Platzhalter-Zelle
    fuer das Feld ohne Spiel zu dieser Zeit)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "ergebnisse-time-grid-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

        with get_conn() as conn:
            (
                self.competition_id, self.court_1_id, self.court_2_id,
                self.team_a_id, self.team_b_id, self.team_c_id,
            ) = _create_competition_and_teams(conn)

            _insert_slot(
                conn, competition_id=self.competition_id, court_id=self.court_1_id,
                team_a_id=self.team_a_id, team_b_id=self.team_b_id, startzeit="10:00",
            )
            _insert_slot(
                conn, competition_id=self.competition_id, court_id=self.court_2_id,
                team_a_id=self.team_a_id, team_b_id=self.team_b_id, startzeit="10:00",
            )
            _insert_slot(
                conn, competition_id=self.competition_id, court_id=self.court_1_id,
                team_a_id=self.team_c_id, team_b_id=self.team_b_id, startzeit="10:10",
            )
            conn.commit()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_results_board_renders_shared_time_grid_with_placeholder(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get(
                "/ergebnisse", params={"competition_id": self.competition_id}
            )

        self.assertEqual(response.status_code, 200)
        html = response.text

        self.assertIn('class="editor-time-grid results-time-grid"', html)
        # zwei eindeutige Startzeiten (10:00, 10:10) -> zwei Zeit-Beschriftungen
        self.assertEqual(html.count('class="editor-time-label"'), 2)
        # Feld 2 hat um 10:10 kein Spiel -> Platzhalter-Zelle
        self.assertIn("is-empty", html)


if __name__ == "__main__":
    unittest.main()
