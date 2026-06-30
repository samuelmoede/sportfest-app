import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.sixkampf_service import calculate_sixkampf_team_ranking


def team(team_id, name, jahrgang=7):
    return {"id": team_id, "name": name, "jahrgang": jahrgang}


def discipline(discipline_id, name, scoring_direction="higher", unit=""):
    return {
        "id": discipline_id,
        "name": name,
        "scoring_direction": scoring_direction,
        "unit": unit,
    }


def result(team_id, discipline_id, value, value_index=1):
    return {
        "team_id": team_id,
        "discipline_id": discipline_id,
        "value": value,
        "value_index": value_index,
    }


def rows_by_team_name(ranking):
    rows = {}
    for row in ranking:
        team_value = row["team"]
        team_name = team_value["name"] if isinstance(team_value, dict) else team_value
        rows[team_name] = row
    return rows


def import_main_for_tests():
    import importlib
    import zoneinfo
    from datetime import timezone

    if "app.main" in sys.modules:
        return sys.modules["app.main"]

    with patch.object(zoneinfo, "ZoneInfo", return_value=timezone.utc):
        return importlib.import_module("app.main")


class SixkampfServiceTests(unittest.TestCase):
    def test_discipline_points_follow_unique_order(self):
        teams = [team(1, "7a"), team(2, "7b"), team(3, "7c")]
        disciplines = [discipline(10, "Sprint")]
        ranking, _, _ = calculate_sixkampf_team_ranking(
            teams,
            disciplines,
            [
                result(1, 10, 30),
                result(2, 10, 20),
                result(3, 10, 10),
            ],
            require_result_entry=True,
            placement_points=[7, 6, 5, 4, 3, 2, 1],
        )

        by_name = rows_by_team_name(ranking)
        self.assertEqual(by_name["7a"]["discipline_points"][10], 7)
        self.assertEqual(by_name["7b"]["discipline_points"][10], 6)
        self.assertEqual(by_name["7c"]["discipline_points"][10], 5)

    def test_discipline_ties_get_same_points_and_next_group_one_less(self):
        teams = [team(1, "7a"), team(2, "7b"), team(3, "7c")]
        disciplines = [discipline(10, "Sprint")]
        ranking, _, _ = calculate_sixkampf_team_ranking(
            teams,
            disciplines,
            [
                result(1, 10, 30),
                result(2, 10, 30),
                result(3, 10, 20),
            ],
            require_result_entry=True,
            placement_points=[7, 6, 5, 4, 3, 2, 1],
        )

        by_name = rows_by_team_name(ranking)
        self.assertEqual(by_name["7a"]["discipline_points"][10], 7)
        self.assertEqual(by_name["7b"]["discipline_points"][10], 7)
        self.assertEqual(by_name["7c"]["discipline_points"][10], 6)

    def test_missing_discipline_value_is_not_scored_as_zero(self):
        teams = [team(1, "7a"), team(2, "7b"), team(3, "7c")]
        disciplines = [discipline(10, "Sprint"), discipline(20, "Weit")]
        ranking, totals_by_team_discipline, _ = calculate_sixkampf_team_ranking(
            teams,
            disciplines,
            [
                result(1, 10, 30),
                result(2, 10, 20),
                result(2, 20, 4),
            ],
            require_result_entry=True,
            placement_points=[7, 6, 5, 4, 3, 2, 1],
        )

        by_name = rows_by_team_name(ranking)
        self.assertNotIn("7c", by_name)
        self.assertNotIn(20, by_name["7a"]["discipline_points"])
        self.assertNotIn((1, 20), totals_by_team_discipline)
        self.assertEqual(by_name["7a"]["intermediate_total"], 7)
        self.assertEqual(by_name["7b"]["intermediate_total"], 13)

    def test_discipline_result_display_shows_existing_totals_with_units(self):
        teams = [team(1, "7a"), team(2, "7b"), team(3, "7c")]
        disciplines = [
            discipline(10, "Medizinball", unit="m"),
            discipline(20, "Sprint", scoring_direction="lower", unit="s"),
            discipline(30, "Zielwerfen", unit="Treffer"),
        ]
        ranking, totals_by_team_discipline, _ = calculate_sixkampf_team_ranking(
            teams,
            disciplines,
            [
                result(1, 10, 20),
                result(1, 10, 18.42, value_index=2),
                result(1, 20, 40),
                result(1, 20, 38.42, value_index=2),
                result(1, 30, 20),
                result(1, 30, 11, value_index=2),
                result(2, 10, 35),
                result(2, 10, 1.5, value_index=2),
                result(2, 20, 65),
                result(2, 30, 28),
                result(3, 10, 12),
            ],
            require_result_entry=True,
            placement_points=[7, 6, 5, 4, 3, 2, 1],
        )

        by_name = rows_by_team_name(ranking)
        self.assertEqual(totals_by_team_discipline[(1, 10)], 38.42)
        self.assertEqual(by_name["7a"]["discipline_results_display"][10], "38,42 m")
        self.assertEqual(by_name["7a"]["discipline_results_display"][20], "78,42 s")
        self.assertEqual(by_name["7a"]["discipline_results_display"][30], "31 Treffer")
        self.assertEqual(by_name["7b"]["discipline_results_display"][10], "36,5 m")
        self.assertEqual(by_name["7b"]["discipline_results_display"][20], "65 s")
        self.assertEqual(by_name["7b"]["discipline_results_display"][30], "28 Treffer")
        self.assertNotIn(20, by_name["7c"]["discipline_results_display"])
        self.assertNotIn(30, by_name["7c"]["discipline_results_display"])

        self.assertEqual(by_name["7a"]["discipline_points"], {10: 7, 20: 6, 30: 7})
        self.assertEqual(by_name["7b"]["discipline_points"], {10: 6, 20: 7, 30: 6})
        self.assertEqual(by_name["7c"]["discipline_points"], {10: 5})
        self.assertEqual(by_name["7a"]["intermediate_total"], 20)
        self.assertEqual(by_name["7b"]["intermediate_total"], 19)
        self.assertEqual(by_name["7a"]["placement"], 1)
        self.assertEqual(by_name["7a"]["scoring_points"], 7)
        self.assertEqual(by_name["7b"]["placement"], 2)
        self.assertEqual(by_name["7b"]["scoring_points"], 6)
    def test_intermediate_total_can_reach_42_with_six_disciplines(self):
        teams = [team(1, "7a"), team(2, "7b")]
        disciplines = [discipline(discipline_id, f"D{discipline_id}") for discipline_id in range(1, 7)]
        results = []
        for item in disciplines:
            results.append(result(1, item["id"], 20))
            results.append(result(2, item["id"], 10))

        ranking, _, _ = calculate_sixkampf_team_ranking(
            teams,
            disciplines,
            results,
            require_result_entry=True,
            placement_points=[7, 6, 5, 4, 3, 2, 1],
        )

        winner = rows_by_team_name(ranking)["7a"]
        self.assertEqual(winner["intermediate_total"], 42)
        self.assertEqual(winner["max_intermediate_total"], 42)

    def test_final_sixkampf_scoring_points_are_based_on_intermediate_ranking(self):
        teams = [team(1, "7a"), team(2, "7b"), team(3, "7c")]
        disciplines = [discipline(discipline_id, f"D{discipline_id}") for discipline_id in range(1, 7)]
        results = []
        for item in disciplines:
            results.append(result(1, item["id"], 30))
            results.append(result(2, item["id"], 20))
            results.append(result(3, item["id"], 10))

        ranking, _, _ = calculate_sixkampf_team_ranking(
            teams,
            disciplines,
            results,
            require_result_entry=True,
            placement_points=[7, 6, 5, 4, 3, 2, 1],
        )

        by_name = rows_by_team_name(ranking)
        self.assertEqual(by_name["7a"]["placement"], 1)
        self.assertEqual(by_name["7a"]["scoring_points"], 7)
        self.assertEqual(by_name["7b"]["placement"], 2)
        self.assertEqual(by_name["7b"]["scoring_points"], 6)
        self.assertEqual(by_name["7c"]["placement"], 3)
        self.assertEqual(by_name["7c"]["scoring_points"], 5)


class EventOverallAndTournamentTests(unittest.TestCase):
    def test_event_overall_uses_final_sixkampf_scoring_points(self):
        main = import_main_for_tests()

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE competitions (
                id INTEGER PRIMARY KEY,
                event_id INTEGER,
                name TEXT,
                jahrgang INTEGER,
                competition_type TEXT,
                points_first_place INTEGER,
                placement_points TEXT
            );
            CREATE TABLE teams (
                id INTEGER PRIMARY KEY,
                name TEXT,
                jahrgang INTEGER,
                active INTEGER
            );
            CREATE TABLE competition_disciplines (
                id INTEGER PRIMARY KEY,
                competition_id INTEGER,
                name TEXT,
                sort_order INTEGER,
                scoring_direction TEXT
            );
            CREATE TABLE sixkampf_team_results (
                competition_id INTEGER,
                discipline_id INTEGER,
                team_id INTEGER,
                value_index INTEGER,
                value REAL
            );
            """
        )
        conn.execute(
            "INSERT INTO competitions VALUES (1, 100, 'Sechskampf 7', 7, 'Sechskampf', 7, NULL)"
        )
        conn.executemany(
            "INSERT INTO teams VALUES (?, ?, 7, 1)",
            [(1, "7a"), (2, "7b"), (3, "7c")],
        )
        conn.executemany(
            "INSERT INTO competition_disciplines VALUES (?, 1, ?, ?, 'higher')",
            [(discipline_id, f"D{discipline_id}", discipline_id) for discipline_id in range(1, 7)],
        )
        conn.executemany(
            "INSERT INTO sixkampf_team_results VALUES (1, ?, ?, 1, ?)",
            [
                (discipline_id, team_id, value)
                for discipline_id in range(1, 7)
                for team_id, value in [(1, 30), (2, 20), (3, 10)]
            ],
        )

        with patch.object(main, "get_conn", return_value=conn):
            groups = main.calculate_event_overall_ranking(100)

        rows = rows_by_team_name(groups[0]["rows"])
        self.assertEqual(rows["7a"]["points_by_competition"][1], 7)
        self.assertEqual(rows["7a"]["total_points"], 7)
        self.assertEqual(rows["7b"]["points_by_competition"][1], 6)
        self.assertEqual(rows["7c"]["points_by_competition"][1], 5)

    def test_tournament_points_still_use_placement_points(self):
        main = import_main_for_tests()

        class FakeCursor:
            def fetchall(self):
                return [object()]

        class FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def execute(self, *_args, **_kwargs):
                return FakeCursor()

        table_rows = [
            {"team_id": 1, "team": "7a", "pkt": 9, "diff": 6, "plus": 10},
            {"team_id": 2, "team": "7b", "pkt": 6, "diff": 3, "plus": 7},
            {"team_id": 3, "team": "7c", "pkt": 3, "diff": 1, "plus": 4},
        ]
        competition = {
            "id": 1,
            "points_first_place": 7,
            "placement_points": None,
        }

        with patch.object(main, "get_conn", return_value=FakeConn()):
            with patch.object(main, "calculate_table", return_value=table_rows):
                rows = main.calculate_tournament_points(competition)

        self.assertEqual([row["competition_points"] for row in rows], [7, 6, 5])


if __name__ == "__main__":
    unittest.main()
