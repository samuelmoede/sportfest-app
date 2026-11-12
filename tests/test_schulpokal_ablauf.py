"""Schulpokal von Anfang bis Ende: Anlegen ueber die System-Vorlage des
Einrichtungsassistenten, Tischtennis getrennt nach Jungen/Maedchen ohne
Extra-Teams, Tagesplan, Gesamtwertung aus Spielpunkten, Jahrgangssieger und
Jahrgangsfinale sowie der Runden-Timer der Ergebniseingabe
(app/services/runden_timer_service.py).

Frueher lief das Anlegen ueber die Vorlage Schulpokal und den
Schulpokal-Gesamtplan (beide entfernt, ROADMAP Phase 7.5). Immer gegen eine
temporaere Datenbank."""
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
from app.services import assistent_entwurf_service as es
from app.services.assistent_planung_service import plane
from app.services.runden_timer_service import build_round_timer
from app.services.schedule_generator_service import _split_courts
from app.services.schulpokal_vorlage_service import parse_teilmannschaften


class SplitCourtsTests(unittest.TestCase):
    def test_split_courts_by_need(self):
        self.assertEqual(_split_courts([1, 2, 3, 4, 5, 6], [3, 3]), [[1, 2, 3], [4, 5, 6]])
        self.assertEqual(_split_courts([1, 2, 3], [3, 3]), [[1, 2], [3]])
        self.assertIsNone(_split_courts([1], [3, 3]))


class TeilmannschaftParsingTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_teilmannschaften("Jungen, Mädchen"), ["Jungen", "Mädchen"])
        self.assertEqual(parse_teilmannschaften(" jungen / Jungen "), ["jungen"])
        self.assertEqual(parse_teilmannschaften(""), [])


class SchulpokalAblaufTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "schulpokal-ablauf-test.db"
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
        self.event_id = self._schulpokal_anlegen()

    def tearDown(self):
        self.client.close()
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def _post(self, url, data=None):
        response = self.client.post(url, data=data or {}, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text[:500])
        return response.headers["location"]

    def _schulpokal_anlegen(self):
        """Schulpokal ueber die System-Vorlage des Einrichtungsassistenten:
        Entwurf anlegen, Grunddaten setzen, Warnungen bestaetigen,
        uebernehmen."""
        location = self._post("/assistent/entwurf/aus-vorlage", {"vorlage": es.SYSTEM_VORLAGE_SCHULPOKAL})
        entwurf_id = int(location.split("/")[3])
        self._post(location, {
            "name": "Schulpokal 2026", "datum": "2026-12-17",
            "jahrgaenge": ["7", "8", "9", "10"],
            "sportarten": ["Zweifelderball", "Volleyball", "Tischtennis"],
            "ort_gesendet_Turnhalle": "1", "ort_aktiv_Turnhalle": "1",
            "ort_gesendet_Mensa": "1", "ort_aktiv_Mensa": "1", "ziel": "grunddaten",
        })
        with get_conn() as conn:
            plan = plane(conn, es.lade_entwurf(conn, entwurf_id)["daten"], es.lade_stammdaten(conn))
        for hinweis in plan["hinweise"]:
            if hinweis["bestaetigbar"] and not hinweis["bestaetigt"]:
                self._post(f"/assistent/entwurf/{entwurf_id}/hinweis/bestaetigen", {"kennung": hinweis["id"]})
        with get_conn() as conn:
            entwurf = es.lade_entwurf(conn, entwurf_id)
        location = self._post(f"/assistent/entwurf/{entwurf_id}/uebernehmen", {"version": str(entwurf["version"])})
        self.assertIn("/fertig", location)
        with get_conn() as conn:
            return es.lade_entwurf(conn, entwurf_id)["event_id"]

    def _competition(self, name):
        with get_conn() as conn:
            return conn.execute("SELECT * FROM competitions WHERE name = ?", (name,)).fetchone()

    def test_assistant_creates_boys_and_girls_table_tennis_without_extra_teams(self):
        jungen = self._competition("Tischtennis Jungen Jahrgang 9")
        maedchen = self._competition("Tischtennis Mädchen Jahrgang 9")
        self.assertEqual(jungen["teilmannschaft"], "Jungen")
        self.assertEqual(maedchen["teilmannschaft"], "Mädchen")
        self.assertEqual((jungen["points_win"], jungen["points_draw"]), (1, 0))
        volleyball = self._competition("Volleyball Jahrgang 9")
        self.assertEqual((volleyball["points_win"], volleyball["points_draw"]), (2, 1))
        with get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM teams").fetchone()["n"], 25)

    def test_day_schedule_has_own_column_for_year_10(self):
        """Tagesplan beim Schulpokal: Jahrgang 10 bekommt eine eigene Spalte
        statt unter Oberstufe/GOST zu landen."""
        from app.main import get_day_schedule_for_event
        schedule = get_day_schedule_for_event(self.event_id)
        labels = [column["label"] for column in schedule["timeline"]["columns"]]
        self.assertIn("Jahrgang 10", labels)
        self.assertNotIn("Oberstufe", labels)
        page = self.client.get(f"/events/{self.event_id}")
        self.assertIn("JG 10", page.text)
        self.assertNotIn("OBERSTUFE", page.text.upper().split("TAGESPLAN", 1)[-1])

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

    def test_one_winner_per_jahrgang_and_final_with_jahrgang_winners(self):
        """Beim Schulpokal gibt es einen Sieger je Jahrgang aus allen
        Wettbewerben des Jahrgangs (Volleyball + Tischtennis); das
        Jahrgangsfinale zaehlt dort nicht mit und wird mit diesen
        Jahrgangssiegern besetzt."""
        from app.main import calculate_event_overall_ranking

        with get_conn() as conn:
            teams = {row["name"]: row["id"] for row in conn.execute("SELECT id, name FROM teams")}
            ids = {row["name"]: row["id"] for row in conn.execute("SELECT id, name FROM competitions")}
            gruppen = [
                ids[name] for name in ids
                if name.startswith(("Volleyball Jahrgang", "Tischtennis")) and "finale" not in name
            ]
            # Geplante Gruppenspiele durch wenige gespielte ersetzen, damit
            # die Gruppenphase abgeschlossen ist.
            conn.execute(
                f"DELETE FROM slots WHERE competition_id IN ({','.join('?' * len(gruppen))})", gruppen
            )

            def play(competition_name, results):
                for index, (team_a, team_b, score_a, score_b) in enumerate(results):
                    conn.execute(
                        """
                        INSERT INTO slots (competition_id, startzeit, slot_typ, phase,
                                           team_a_id, team_b_id, score_a, score_b, status)
                        VALUES (?, ?, 'Spiel', 'Gruppenphase', ?, ?, ?, ?, 'beendet')
                        """,
                        (ids[competition_name], f"11:{10 + index:02d}", teams[team_a],
                         teams[team_b], score_a, score_b),
                    )

            # Volleyball gewinnt jeweils "a", Tischtennis jeweils "b".
            for jahrgang in (9, 10):
                a, b, c = (f"{jahrgang}{letter}" for letter in "abc")
                play(f"Volleyball Jahrgang {jahrgang}", [(a, b, 2, 0), (a, c, 2, 0), (b, c, 2, 1)])
                play(f"Tischtennis Jungen Jahrgang {jahrgang}", [(b, a, 4, 2), (b, c, 4, 2), (c, a, 4, 2)])
                play(f"Tischtennis Mädchen Jahrgang {jahrgang}", [(b, a, 4, 2), (b, c, 4, 2), (c, a, 4, 2)])
            conn.commit()

        ranking = calculate_event_overall_ranking(self.event_id)
        gruppen_namen = [group["year_group"] for group in ranking]
        self.assertIn("Gesamtwertung Jahrgang 9", gruppen_namen)
        self.assertIn("Gesamtwertung Jahrgang 10", gruppen_namen)
        for jahrgang in (9, 10):
            group = next(g for g in ranking if g["year_group"] == f"Gesamtwertung Jahrgang {jahrgang}")
            self.assertEqual(group["rows"][0]["team"], f"{jahrgang}b")
            self.assertNotIn(
                "Volleyball Jahrgangsfinale 9/10",
                {competition["name"] for competition in group["competitions"]},
            )

        final_id = ids["Volleyball Jahrgangsfinale 9/10"]
        response = self.client.post(
            f"/competition/{final_id}/jahrgangsfinale/besetzen", follow_redirects=False
        )
        self.assertIn("finale_ok=besetzt", response.headers["location"])

        with get_conn() as conn:
            conn.execute("UPDATE events SET siegerehrung_public = 1 WHERE id = ?", (self.event_id,))
            conn.commit()
        page = self.client.get(f"/siegerehrung/{self.event_id}")
        self.assertIn("Gesamtwertung Jahrgang 10", page.text)
        self.assertNotIn("GOST", page.text)
        csv_export = self.client.get(f"/tabellen/csv?event_id={self.event_id}&jahrgang=10")
        self.assertIn("Gesamtwertung Jahrgang 10", csv_export.text)
        self.assertNotIn("Gesamtwertung Jahrgang 9", csv_export.text)

        with get_conn() as conn:
            slot = conn.execute(
                "SELECT team_a_id, team_b_id FROM slots WHERE competition_id = ? AND slot_typ = 'Spiel'",
                (final_id,),
            ).fetchone()
        self.assertEqual((slot["team_a_id"], slot["team_b_id"]), (teams["9b"], teams["10b"]))


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
        self.assertEqual(timer["next"], {"startzeit": "11:45", "slot_ids": [2], "count": 1, "competition_id": None})

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
