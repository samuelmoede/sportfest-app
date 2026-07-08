import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.sixkampf_service import (
    calculate_sixkampf_station_rotation,
    calculate_sixkampf_team_ranking,
)


def team(team_id, name, jahrgang=7):
    return {"id": team_id, "name": name, "jahrgang": jahrgang}


def discipline(discipline_id, name, scoring_direction="higher", unit="", location=None):
    return {
        "id": discipline_id,
        "name": name,
        "scoring_direction": scoring_direction,
        "unit": unit,
        "location": location,
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
            CREATE TABLE competition_teams (
                competition_id INTEGER,
                team_id INTEGER
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
        # 1. Platz erhaelt so viele Punkte wie tatsaechlich teilnehmende Teams (3),
        # nicht die bei Anlage der Veranstaltung konfigurierte points_first_place (7).
        self.assertEqual(rows["7a"]["points_by_competition"][1], 3)
        self.assertEqual(rows["7a"]["total_points"], 3)
        self.assertEqual(rows["7b"]["points_by_competition"][1], 2)
        self.assertEqual(rows["7c"]["points_by_competition"][1], 1)

    def test_tournament_points_still_use_placement_points(self):
        main = import_main_for_tests()

        class FakeCursor:
            def __init__(self, rows):
                self._rows = rows

            def fetchall(self):
                return self._rows

        class FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def execute(self, sql, *_args, **_kwargs):
                # Queries against the K.-o.-phase (Finale/Halbfinale/Platz 3)
                # filter by "phase" and should report "no such slots" here,
                # so calculate_tournament_points falls back to the flat table.
                if "phase" in sql:
                    return FakeCursor([])
                return FakeCursor([object()])

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

        # 1. Platz erhaelt so viele Punkte wie tatsaechlich teilnehmende Teams (3),
        # nicht die bei Anlage der Veranstaltung konfigurierte points_first_place (7).
        self.assertEqual([row["competition_points"] for row in rows], [3, 2, 1])

    def test_ko_bracket_placement_overrides_flat_table_order(self):
        main = import_main_for_tests()

        def make_slot(phase, team_a_id, team_b_id, score_a, score_b):
            return {
                "phase": phase,
                "status": "beendet",
                "team_a_id": team_a_id,
                "team_b_id": team_b_id,
                "score_a": score_a,
                "score_b": score_b,
            }

        # Team 1 dominated the group phase (most table points), but lost the
        # final to team 2 - the bracket result must decide places 1-4, not
        # the aggregated group-phase table.
        finale = make_slot("Finale", 2, 1, 3, 1)
        platz3 = make_slot("Spiel um Platz 3", 3, 4, 2, 0)

        def fake_get_ko_phase_slots(competition_id):
            return [finale], [platz3], []

        table_rows = [
            {"team_id": 1, "team": "7a", "pkt": 9, "diff": 6, "plus": 10},
            {"team_id": 2, "team": "7b", "pkt": 6, "diff": 3, "plus": 7},
            {"team_id": 3, "team": "7c", "pkt": 3, "diff": 1, "plus": 4},
            {"team_id": 4, "team": "7d", "pkt": 0, "diff": -10, "plus": 0},
            {"team_id": 5, "team": "7e", "pkt": 0, "diff": -8, "plus": 1},
        ]
        for index, row in enumerate(table_rows, start=1):
            row["placement"] = index

        with patch.object(main, "get_ko_phase_slots", side_effect=fake_get_ko_phase_slots):
            rows = main.apply_ko_bracket_placements(table_rows, competition_id=1)

        placements_by_team = {row["team_id"]: row["placement"] for row in rows}
        self.assertEqual(placements_by_team[2], 1)  # Finalsieger
        self.assertEqual(placements_by_team[1], 2)  # Finalverlierer
        self.assertEqual(placements_by_team[3], 3)  # Sieger Spiel um Platz 3
        self.assertEqual(placements_by_team[4], 4)  # Verlierer Spiel um Platz 3
        self.assertEqual(placements_by_team[5], 5)  # in Gruppenphase ausgeschieden
        self.assertEqual([row["team_id"] for row in rows], [2, 1, 3, 4, 5])

    def test_ko_bracket_placement_shares_third_place_without_platz3_game(self):
        main = import_main_for_tests()

        def make_slot(phase, team_a_id, team_b_id, score_a, score_b):
            return {
                "phase": phase,
                "status": "beendet",
                "team_a_id": team_a_id,
                "team_b_id": team_b_id,
                "score_a": score_a,
                "score_b": score_b,
            }

        finale = make_slot("Finale", 2, 1, 3, 1)
        halbfinale_1 = make_slot("Halbfinale", 1, 3, 2, 0)
        halbfinale_2 = make_slot("Halbfinale", 2, 4, 2, 1)

        def fake_get_ko_phase_slots(competition_id):
            return [finale], [], [halbfinale_1, halbfinale_2]

        table_rows = [
            {"team_id": 1, "team": "7a", "pkt": 9, "diff": 6, "plus": 10, "placement": 1},
            {"team_id": 2, "team": "7b", "pkt": 6, "diff": 3, "plus": 7, "placement": 2},
            {"team_id": 3, "team": "7c", "pkt": 3, "diff": 1, "plus": 4, "placement": 3},
            {"team_id": 4, "team": "7d", "pkt": 0, "diff": -10, "plus": 0, "placement": 4},
        ]

        with patch.object(main, "get_ko_phase_slots", side_effect=fake_get_ko_phase_slots):
            rows = main.apply_ko_bracket_placements(table_rows, competition_id=1)

        placements_by_team = {row["team_id"]: row["placement"] for row in rows}
        self.assertEqual(placements_by_team[2], 1)  # Finalsieger
        self.assertEqual(placements_by_team[1], 2)  # Finalverlierer
        self.assertEqual(placements_by_team[3], 3)  # Halbfinal-Verlierer, geteilt
        self.assertEqual(placements_by_team[4], 3)  # Halbfinal-Verlierer, geteilt


class StationRotationTests(unittest.TestCase):
    def test_team_count_equals_station_count_no_pause(self):
        teams = [team(1, "7a"), team(2, "7b"), team(3, "7c"), team(4, "7d")]
        disciplines = [
            discipline(10, "Station1"), discipline(20, "Station2"),
            discipline(30, "Station3"), discipline(40, "Station4"),
        ]
        rotation = calculate_sixkampf_station_rotation(teams, disciplines)
        starts = [entry["rounds"][0]["name"] for entry in rotation]
        self.assertEqual(starts, ["Station1", "Station2", "Station3", "Station4"])
        for entry in rotation:
            self.assertNotIn("Pause", [station["name"] for station in entry["rounds"]])

    def test_more_teams_than_stations_inserts_pause(self):
        teams = [team(i, name) for i, name in enumerate(
            ["7a", "7b", "7c", "7d", "7e", "7f", "7g"], start=1
        )]
        disciplines = [
            discipline(10, "Station1"), discipline(20, "Station2"),
            discipline(30, "Station3"), discipline(40, "Station4"),
        ]
        rotation = calculate_sixkampf_station_rotation(teams, disciplines)

        # Jede Klasse bis zur Stationsanzahl startet in Runde 1 an ihrer
        # eigenen Station (7a->Station1, 7b->Station2 usw.); ueberzaehlige
        # Klassen (7e, 7f, 7g) starten mit Pause.
        starts = [entry["rounds"][0]["name"] for entry in rotation]
        self.assertEqual(
            starts,
            ["Station1", "Station2", "Station3", "Station4", "Pause", "Pause", "Pause"],
        )

        # Jede Klasse bekommt ueber den vollen Zyklus (7 Runden) gleich oft
        # (3x) Pause - reihum, nicht dauerhaft nur die Ueberzaehligen.
        for entry in rotation:
            names = [station["name"] for station in entry["rounds"]]
            self.assertEqual(names.count("Pause"), 3)
            self.assertEqual(len(entry["rounds"]), 7)

        # In jeder Runde steht weiterhin genau eine Klasse je Station.
        for round_index in range(7):
            stations_this_round = [
                entry["rounds"][round_index]["name"] for entry in rotation
                if entry["rounds"][round_index]["name"] != "Pause"
            ]
            self.assertEqual(sorted(stations_this_round), ["Station1", "Station2", "Station3", "Station4"])

    def test_no_disciplines_or_no_teams_returns_empty(self):
        self.assertEqual(calculate_sixkampf_station_rotation([], [discipline(10, "S1")]), [])
        self.assertEqual(calculate_sixkampf_station_rotation([team(1, "7a")], []), [])

    def test_station_location_is_carried_through(self):
        teams = [team(1, "7a"), team(2, "7b")]
        disciplines = [
            discipline(10, "Station1", location="Foyer 3. Etage"),
            discipline(20, "Station2", location=None),
        ]
        rotation = calculate_sixkampf_station_rotation(teams, disciplines)
        self.assertEqual(rotation[0]["rounds"][0], {"name": "Station1", "location": "Foyer 3. Etage"})
        self.assertEqual(rotation[0]["rounds"][1], {"name": "Station2", "location": None})


if __name__ == "__main__":
    unittest.main()
