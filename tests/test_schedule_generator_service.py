import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.schedule_generator_service import (
    _assign_courts_for_round,
    generate_group_plan,
)


def make_in_memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    schema_sql = (ROOT_DIR / "app" / "database.py").read_text(encoding="utf-8")
    start = schema_sql.index('conn.executescript("""') + len('conn.executescript("""')
    end = schema_sql.index('""")', start)
    conn.executescript(schema_sql[start:end])
    return conn


class AssignCourtsForRoundTests(unittest.TestCase):
    def test_prefers_court_a_team_already_played_on(self):
        round_selections = [
            ("7f", "7g", "B", 0),
            ("7a", "7c", "A", 1),
        ]
        last_court_by_team = {"7a": 10, "7b": 10, "7d": 20, "7e": 20}

        assignment = _assign_courts_for_round(round_selections, [10, 20], last_court_by_team)

        court_by_selection = {selection: court for court, selection in assignment}
        self.assertEqual(court_by_selection[("7a", "7c", "A", 1)], 10)
        self.assertEqual(court_by_selection[("7f", "7g", "B", 0)], 20)

    def test_falls_back_to_original_order_when_no_history(self):
        round_selections = [("7a", "7b", "A", 0), ("7c", "7d", "B", 1)]

        assignment = _assign_courts_for_round(round_selections, [10, 20], {})

        self.assertEqual(assignment, [(10, round_selections[0]), (20, round_selections[1])])


class GenerateGroupPlanCourtContinuityTests(unittest.TestCase):
    def test_seven_teams_never_switch_courts_within_group_phase(self):
        conn = make_in_memory_db()
        conn.execute("""INSERT INTO courts (id, name, sportart, location) VALUES
            (1, 'Rasenplatz', 'Fussball', 'Fußballplatz'),
            (2, 'Kaefig', 'Fussball', 'Fußballplatz')""")
        conn.execute("""INSERT INTO teams (id, name, jahrgang) VALUES
            (1,'7a',7),(2,'7b',7),(3,'7c',7),(4,'7d',7),(5,'7e',7),(6,'7f',7),(7,'7g',7)""")
        conn.execute("""INSERT INTO competitions
            (id, name, sportart, jahrgang, competition_type, game_duration_minutes,
             changeover_duration_minutes, location)
            VALUES (1, 'Fussball Jahrgang 7', 'Fussball', 7, 'Turnier', 7, 3, 'Fußballplatz')""")
        conn.commit()

        import app.services.schedule_generator_service as svc
        with patch.object(svc, "get_conn", return_value=conn):
            slots = generate_group_plan(
                competition_id=1,
                court_ids=[1, 2],
                startzeit="10:20",
                games_per_team=2,
                include_ko=True,
            )

        group_slots = [slot for slot in slots if slot["phase"] == "Gruppenphase"]
        courts_used_by_team = {}
        for slot in group_slots:
            for team in (slot["team_a"], slot["team_b"]):
                courts_used_by_team.setdefault(team, set()).add(slot["court_id"])

        switching_teams = {
            team: courts for team, courts in courts_used_by_team.items() if len(courts) > 1
        }
        self.assertEqual(switching_teams, {}, "no team should need to switch courts mid group phase")


if __name__ == "__main__":
    unittest.main()
