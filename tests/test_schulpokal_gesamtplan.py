"""Tests fuer den Gesamtplan des Schulpokals (alle Bloecke auf einmal,
app/services/schulpokal_gesamtplan_service.py), Tischtennis getrennt nach
Jungen/Maedchen ohne Extra-Teams, die Gesamtwertung aus Spielpunkten und den
Runden-Timer der Ergebniseingabe (app/services/runden_timer_service.py)."""
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
from app.services.runden_timer_service import build_round_timer
from app.services.schedule_generator_service import _split_courts, plan_schulpokal_block
from app.services.schulpokal_vorlage_service import (
    DEFAULT_SCHULPOKAL_BLOECKE,
    parse_points,
    parse_teilmannschaften,
)


def _entry(competition_id, jahrgang, count, name=None):
    teams = [f"{jahrgang}{letter}" for letter in "abcdefg"[:count]]
    return {
        "competition_id": competition_id,
        "competition_name": name or f"Wettbewerb {competition_id}",
        "jahrgang": jahrgang,
        "team_names": teams,
        "team_ids": {team: f"{competition_id}-{team}" for team in teams},
    }


class PlanSchulpokalBlockTests(unittest.TestCase):
    def test_split_courts_by_need(self):
        self.assertEqual(_split_courts([1, 2, 3, 4, 5, 6], [3, 3]), [[1, 2, 3], [4, 5, 6]])
        self.assertEqual(_split_courts([1, 2, 3], [3, 3]), [[1, 2], [3]])
        self.assertIsNone(_split_courts([1], [3, 3]))

    def test_alternating_years_share_courts_and_only_play_own_year(self):
        slots, steps = plan_schulpokal_block(
            [_entry(1, 7, 7), _entry(2, 8, 6)], [11, 12, 13], 8 * 60, 10
        )
        times = sorted({slot["startzeit"] for slot in slots})
        self.assertEqual(times[:4], ["08:00", "08:10", "08:20", "08:30"])
        by_time = {}
        for slot in slots:
            by_time.setdefault(slot["startzeit"], set()).add(slot["competition_id"])
            self.assertTrue(slot["team_a"].startswith(str(slot["competition_id"] + 6)))
            self.assertTrue(slot["team_b"].startswith(str(slot["competition_id"] + 6)))
        # je Zeitschritt nur ein Jahrgang, abwechselnd
        self.assertTrue(all(len(ids) == 1 for ids in by_time.values()))
        self.assertEqual([by_time[t].pop() for t in times[:4]], [1, 2, 1, 2])
        self.assertEqual(len(slots), 21 + 15)
        self.assertEqual(steps, len(times))

    def test_boys_and_girls_of_same_year_play_simultaneously_on_own_tables(self):
        entries = [_entry(1, 9, 6), _entry(2, 9, 6), _entry(3, 10, 6), _entry(4, 10, 6)]
        slots, steps = plan_schulpokal_block(entries, list(range(1, 13)), 11 * 60 + 30, 15)
        self.assertEqual(steps, 10)  # 5 Runden J9 und 5 Runden J10 im Wechsel
        first = [slot for slot in slots if slot["startzeit"] == "11:30"]
        self.assertEqual({slot["competition_id"] for slot in first}, {1, 2})
        courts_1 = {slot["court_id"] for slot in slots if slot["competition_id"] == 1}
        courts_2 = {slot["court_id"] for slot in slots if slot["competition_id"] == 2}
        self.assertFalse(courts_1 & courts_2)

        slots, steps = plan_schulpokal_block(
            entries, list(range(1, 13)), 11 * 60 + 30, 15, gleichzeitig=True
        )
        self.assertEqual(steps, 5)
        self.assertEqual(len([s for s in slots if s["startzeit"] == "11:30"]), 12)

    def test_too_few_courts_for_parallel_competitions(self):
        with self.assertRaises(ValueError):
            plan_schulpokal_block(
                [_entry(1, 9, 6), _entry(2, 9, 6)], [1], 600, 10
            )


class TeilmannschaftParsingTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_teilmannschaften("Jungen, Mädchen"), ["Jungen", "Mädchen"])
        self.assertEqual(parse_teilmannschaften(" jungen / Jungen "), ["jungen"])
        self.assertEqual(parse_teilmannschaften(""), [])
        self.assertEqual(parse_points("", ""), (2, 1))
        self.assertEqual(parse_points("1", "0"), (1, 0))
        self.assertEqual(parse_points("", "", "Turnier"), (3, 1))


def _vorlage_form(name="Schulpokal 2026"):
    data = {"name": name, "event_date": "2026-12-17", "siegerehrung": "1"}
    keys = (
        "sportart", "jahrgaenge", "location", "start_time", "end_time", "finale_time",
        "teilmannschaften", "points_win", "points_draw",
    )
    for key in keys:
        data[f"block_{key}"] = [str(block[key]) for block in DEFAULT_SCHULPOKAL_BLOECKE]
    return data


class SchulpokalGesamtplanTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "schulpokal-gesamtplan-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()
        with get_conn() as conn:
            for jahrgang, letters in ((7, "abcdefg"), (8, "abcdef"), (9, "abcdef"), (10, "abcdef")):
                for letter in letters:
                    conn.execute(
                        "INSERT INTO teams (name, jahrgang, active) VALUES (?, ?, 1)",
                        (f"{jahrgang}{letter}", jahrgang),
                    )
            for number in range(1, 4):
                conn.execute(
                    "INSERT INTO courts (name, location, active) VALUES (?, 'Turnhalle', 1)",
                    (f"Feld {number}",),
                )
            for number in range(1, 13):
                conn.execute(
                    "INSERT INTO courts (name, location, active) VALUES (?, 'Mensa', 1)",
                    (f"Platte {number}",),
                )
            conn.commit()
        from app.main import app as fastapi_app
        self.client = TestClient(fastapi_app)
        response = self.client.post(
            "/assistent/vorlage/schulpokal", data=_vorlage_form(), follow_redirects=False
        )
        self.event_id = int(re.search(r"/assistent/(\d+)", response.headers["location"]).group(1))

    def tearDown(self):
        self.client.close()
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def _competition(self, name):
        with get_conn() as conn:
            return conn.execute("SELECT * FROM competitions WHERE name = ?", (name,)).fetchone()

    def _block_keys(self):
        page = self.client.get(f"/assistent/{self.event_id}/schulpokal-plan")
        self.assertEqual(page.status_code, 200)
        return re.findall(r'name="ablauf_(\d+)"', page.text)

    def _all_blocks_form(self, **extra):
        form = {"gesendet": "1"}
        for key in self._block_keys():
            form[f"planen_{key}"] = "1"
        form.update(extra)
        return form

    def test_template_creates_boys_and_girls_table_tennis_without_extra_teams(self):
        jungen = self._competition("Tischtennis Jungen Jahrgang 9")
        maedchen = self._competition("Tischtennis Mädchen Jahrgang 9")
        self.assertEqual(jungen["teilmannschaft"], "Jungen")
        self.assertEqual(maedchen["teilmannschaft"], "Mädchen")
        self.assertEqual((jungen["points_win"], jungen["points_draw"]), (1, 0))
        volleyball = self._competition("Volleyball Jahrgang 9")
        self.assertEqual((volleyball["points_win"], volleyball["points_draw"]), (2, 1))
        with get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM teams").fetchone()["n"], 25)

    def test_assistant_offers_whole_plan_button(self):
        page = self.client.get(f"/assistent/{self.event_id}")
        self.assertIn(f"/assistent/{self.event_id}/schulpokal-plan", page.text)
        self.assertEqual(len(self._block_keys()), 3)

    def test_preview_and_apply_plan_everything_at_once(self):
        preview = self.client.post(
            f"/assistent/{self.event_id}/schulpokal-plan/vorschau", data=self._all_blocks_form()
        )
        self.assertEqual(preview.status_code, 200)
        self.assertIn("Alles übernehmen", preview.text)
        self.assertIn("zwei Sportarten", preview.text)
        self.assertNotIn("gleichzeitig für mehrere Spiele", preview.text)

        response = self.client.post(
            f"/assistent/{self.event_id}/schulpokal-plan/uebernehmen",
            data=self._all_blocks_form(), follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertIn("gesamtplan_ok=126", response.headers["location"])
        with get_conn() as conn:
            counts = {
                row["name"]: row["n"] for row in conn.execute(
                    """
                    SELECT c.name, COUNT(*) AS n FROM slots s
                    JOIN competitions c ON c.id = s.competition_id
                    WHERE s.slot_typ = 'Spiel' AND s.phase = 'Gruppenphase'
                    GROUP BY c.name
                    """
                )
            }
            final_slot = conn.execute(
                """
                SELECT s.* FROM slots s JOIN competitions c ON c.id = s.competition_id
                WHERE c.name = 'Zweifelderball Jahrgangsfinale 7/8'
                """
            ).fetchone()
            tt_times = conn.execute(
                """
                SELECT DISTINCT s.startzeit FROM slots s JOIN competitions c ON c.id = s.competition_id
                WHERE c.sportart = 'Tischtennis' ORDER BY s.startzeit
                """
            ).fetchall()
        self.assertEqual(counts["Zweifelderball Jahrgang 7"], 21)
        self.assertEqual(counts["Tischtennis Mädchen Jahrgang 10"], 15)
        self.assertIsNotNone(final_slot["court_id"])
        # 12 Platten reichen fuer alle: Tischtennis spielt standardmaessig gleichzeitig
        self.assertEqual(len(tt_times), 5)

        # Ein zweites Uebernehmen ersetzt die ungespielten Spiele statt sie zu verdoppeln
        self.client.post(
            f"/assistent/{self.event_id}/schulpokal-plan/uebernehmen", data=self._all_blocks_form()
        )
        with get_conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM slots WHERE slot_typ = 'Spiel' AND phase = 'Gruppenphase'"
            ).fetchone()["n"]
        self.assertEqual(total, 126)

    def test_table_tennis_tables_and_alternation_are_selectable(self):
        tt_key = str(self._competition("Tischtennis Jungen Jahrgang 9")["id"])
        form = self._all_blocks_form(**{
            f"anzahl_felder_{tt_key}": "6",
            f"ablauf_{tt_key}": "abwechselnd",
            f"startzeit_{tt_key}": "11:30",
            f"spielzeit_{tt_key}": "10",
            f"wechselzeit_{tt_key}": "5",
        })
        self.client.post(f"/assistent/{self.event_id}/schulpokal-plan/uebernehmen", data=form)
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT s.startzeit, c.jahrgang, co.name AS court FROM slots s
                JOIN competitions c ON c.id = s.competition_id
                JOIN courts co ON co.id = s.court_id
                WHERE c.sportart = 'Tischtennis'
                """
            ).fetchall()
            competition = self._competition("Tischtennis Jungen Jahrgang 9")
        self.assertEqual({row["court"] for row in rows}, {f"Platte {n}" for n in range(1, 7)})
        jahrgang_by_time = {}
        for row in rows:
            jahrgang_by_time.setdefault(row["startzeit"], set()).add(row["jahrgang"])
        self.assertEqual(sorted(jahrgang_by_time)[:2], ["11:30", "11:45"])
        self.assertEqual(jahrgang_by_time["11:30"], {9})
        self.assertEqual(jahrgang_by_time["11:45"], {10})
        self.assertEqual(competition["game_duration_minutes"], 10)

    def test_block_with_played_games_is_left_alone(self):
        self.client.post(
            f"/assistent/{self.event_id}/schulpokal-plan/uebernehmen", data=self._all_blocks_form()
        )
        volleyball = self._competition("Volleyball Jahrgang 9")
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE slots SET status = 'beendet', score_a = 25, score_b = 20
                WHERE id = (SELECT MIN(id) FROM slots WHERE competition_id = ? AND slot_typ = 'Spiel')
                """,
                (volleyball["id"],),
            )
            conn.commit()
            before = conn.execute(
                "SELECT COUNT(*) AS n FROM slots WHERE competition_id = ?", (volleyball["id"],)
            ).fetchone()["n"]
        preview = self.client.post(
            f"/assistent/{self.event_id}/schulpokal-plan/vorschau", data=self._all_blocks_form()
        )
        self.assertIn("nicht neu geplant", preview.text)
        self.client.post(
            f"/assistent/{self.event_id}/schulpokal-plan/uebernehmen", data=self._all_blocks_form()
        )
        with get_conn() as conn:
            after = conn.execute(
                "SELECT COUNT(*) AS n FROM slots WHERE competition_id = ?", (volleyball["id"],)
            ).fetchone()["n"]
            finished = conn.execute(
                "SELECT COUNT(*) AS n FROM slots WHERE status = 'beendet'"
            ).fetchone()["n"]
        self.assertEqual(before, after)
        self.assertEqual(finished, 1)

    def test_assistant_competition_with_boys_and_girls(self):
        response = self.client.post(f"/assistent/{self.event_id}/wettbewerb/anlegen", data={
            "sportart": "Badminton",
            "jahrgang": "8",
            "competition_type": "Schulpokal",
            "teilmannschaften": "Jungen, Mädchen",
            "points_win": "1",
            "points_draw": "0",
            "game_duration_minutes": "8",
            "changeover_duration_minutes": "2",
        }, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        jungen = self._competition("Badminton Jungen Jahrgang 8")
        maedchen = self._competition("Badminton Mädchen Jahrgang 8")
        self.assertEqual(jungen["teilmannschaft"], "Jungen")
        self.assertEqual(maedchen["teilmannschaft"], "Mädchen")
        self.assertEqual(maedchen["points_win"], 1)

    def test_overall_ranking_sums_game_points_like_the_sportfachschaft(self):
        """9a gewinnt Volleyball (2 Punkte) und ein Tischtennisspiel der
        Jungen (1 Punkt), 9b gewinnt ein Tischtennisspiel der Maedchen."""
        from app.main import calculate_event_overall_ranking
        teams = {}
        with get_conn() as conn:
            for row in conn.execute("SELECT id, name FROM teams"):
                teams[row["name"]] = row["id"]

            def game(competition_name, team_a, team_b, score_a, score_b):
                conn.execute(
                    """
                    INSERT INTO slots (competition_id, startzeit, slot_typ, phase, team_a_id,
                                       team_b_id, score_a, score_b, status)
                    VALUES (?, '11:10', 'Spiel', 'Gruppenphase', ?, ?, ?, ?, 'beendet')
                    """,
                    (self._competition(competition_name)["id"], teams[team_a], teams[team_b],
                     score_a, score_b),
                )

            game("Volleyball Jahrgang 9", "9a", "9b", 25, 20)
            game("Tischtennis Jungen Jahrgang 9", "9a", "9b", 2, 0)
            game("Tischtennis Mädchen Jahrgang 9", "9a", "9b", 0, 2)
            conn.commit()

        group = next(
            g for g in calculate_event_overall_ranking(self.event_id)
            if g["year_group"] == "Gesamtwertung Jahrgang 9"
        )
        points = {row["team"]: row["total_points"] for row in group["rows"]}
        self.assertEqual(points["9a"], 3)
        self.assertEqual(points["9b"], 1)
        self.assertEqual(group["rows"][0]["team"], "9a")
        self.assertEqual(len(group["competitions"]), 3)


class RundenTimerTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "runden-timer-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()
        with get_conn() as conn:
            event_id = conn.execute(
                "INSERT INTO events (name, event_date) VALUES ('Schulpokal', '2026-12-17')"
            ).lastrowid
            competition_id = conn.execute(
                """
                INSERT INTO competitions (name, sportart, jahrgang, status, event_id,
                                          competition_type, location, game_duration_minutes)
                VALUES ('Tischtennis Jungen Jahrgang 9', 'Tischtennis', 9, 'läuft', ?,
                        'Schulpokal', 'Mensa', 10)
                """,
                (event_id,),
            ).lastrowid
            team_ids = [
                conn.execute(
                    "INSERT INTO teams (name, jahrgang, active) VALUES (?, 9, 1)", (f"9{letter}",)
                ).lastrowid
                for letter in "abcd"
            ]
            court_ids = [
                conn.execute(
                    "INSERT INTO courts (name, location, active) VALUES (?, 'Mensa', 1)",
                    (f"Platte {n}",),
                ).lastrowid
                for n in (1, 2)
            ]
            self.slot_ids = []
            for startzeit, pairs in (("11:30", ((0, 1), (2, 3))), ("11:45", ((0, 2), (1, 3)))):
                for court_id, (a, b) in zip(court_ids, pairs):
                    self.slot_ids.append(conn.execute(
                        """
                        INSERT INTO slots (competition_id, court_id, startzeit, slot_typ, phase,
                                           team_a_id, team_b_id, status)
                        VALUES (?, ?, ?, 'Spiel', 'Gruppenphase', ?, ?, 'geplant')
                        """,
                        (competition_id, court_id, startzeit, team_ids[a], team_ids[b]),
                    ).lastrowid)
            conn.commit()
        from app.main import app as fastapi_app
        self.client = TestClient(fastapi_app)

    def tearDown(self):
        self.client.close()
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_build_round_timer(self):
        slots = [
            {"id": 1, "slot_typ": "Spiel", "team_a_id": 1, "team_b_id": 2, "status": "läuft",
             "started_at": "2026-12-17 11:30:00", "startzeit": "11:30", "planned_duration_seconds": 600},
            {"id": 2, "slot_typ": "Spiel", "team_a_id": 3, "team_b_id": 4, "status": "geplant",
             "startzeit": "11:45"},
            {"id": 3, "slot_typ": "Spiel", "team_a_id": None, "team_b_id": None, "status": "geplant",
             "startzeit": "11:40"},
        ]
        timer = build_round_timer(slots)
        self.assertEqual(timer["running"]["slot_ids"], [1])
        self.assertEqual(timer["running"]["planned_seconds"], 600)
        self.assertIsNone(timer["running"]["stopped_at"])
        self.assertEqual(timer["next"], {"startzeit": "11:45", "slot_ids": [2], "count": 1})

    def test_start_all_simultaneous_games_and_stop_timer(self):
        page = self.client.get("/ergebnisse?ort=Mensa")
        self.assertIn("Alle 2 Spiele um 11:30 gleichzeitig starten", page.text)

        self.client.post("/slots/runde/starten", data={"slot_ids": self.slot_ids[:2]})
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT status, started_at FROM slots WHERE id IN (?, ?)", self.slot_ids[:2]
            ).fetchall()
        self.assertEqual({row["status"] for row in rows}, {"läuft"})
        self.assertEqual(len({row["started_at"] for row in rows}), 1)

        page = self.client.get("/ergebnisse?ort=Mensa")
        self.assertIn("Timer stoppen", page.text)
        self.assertIn('class="round-timer-display"', page.text)
        self.assertIn("Alle 2 Spiele um 11:45 gleichzeitig starten", page.text)

        self.client.post("/slots/runde/timer-stoppen", data={"slot_ids": self.slot_ids[:2]})
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT status, timer_stopped_at FROM slots WHERE id IN (?, ?)", self.slot_ids[:2]
            ).fetchall()
        self.assertEqual({row["status"] for row in rows}, {"läuft"})
        self.assertTrue(all(row["timer_stopped_at"] for row in rows))
        page = self.client.get("/ergebnisse?ort=Mensa")
        self.assertNotIn("Timer stoppen", page.text)

        # Einzelnes Spiel zuruecksetzen loescht auch den gestoppten Timer
        self.client.post(f"/slot/{self.slot_ids[0]}/unstart")
        with get_conn() as conn:
            row = conn.execute(
                "SELECT status, timer_stopped_at FROM slots WHERE id = ?", (self.slot_ids[0],)
            ).fetchone()
        self.assertEqual((row["status"], row["timer_stopped_at"]), ("geplant", None))

    def test_start_round_ignores_games_that_already_run(self):
        self.client.post(f"/slot/{self.slot_ids[0]}/start", data={"started_at": "2026-12-17 11:00:00"})
        self.client.post("/slots/runde/starten", data={"slot_ids": self.slot_ids[:2]})
        with get_conn() as conn:
            started = conn.execute(
                "SELECT started_at FROM slots WHERE id = ?", (self.slot_ids[0],)
            ).fetchone()["started_at"]
        self.assertEqual(started, "2026-12-17 11:00:00")


if __name__ == "__main__":
    unittest.main()
