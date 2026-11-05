"""Tests fuer ROADMAP Phase 4.7 (kritisch): sichtbare Fehlermeldung statt
stillem Redirect beim automatischen Besetzen von Halbfinale/Finale sowie der
dauerhafte Besetzen-Button in der Ergebniseingabe
(app/services/ko_phase_service.py)."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi.testclient import TestClient

import app.database as database
from app.database import get_conn, init_db
from app.services.ko_phase_service import KO_ERROR_MESSAGES, get_next_phase_action

INSERT_SLOT = """
    INSERT INTO slots (
        competition_id, court_id, startzeit, slot_typ, phase, gruppe,
        team_a_id, team_b_id, score_a, score_b, status
    ) VALUES (1, ?, ?, 'Spiel', ?, ?, ?, ?, ?, ?, ?)
"""


class KoPhaseTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "ko-phase-actions-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def _make_competition(self, tournament_mode=None, competition_type="Turnier"):
        with get_conn() as conn:
            conn.execute("INSERT INTO courts (name) VALUES ('Testfeld')")
            self.court = conn.execute(
                "SELECT id FROM courts WHERE name = 'Testfeld'"
            ).fetchone()["id"]
            conn.execute("""
                INSERT INTO competitions
                    (id, name, sportart, jahrgang, competition_type, tournament_mode, status)
                VALUES (1, 'Testturnier', 'Fußball', 8, ?, ?, 'geplant')
            """, (competition_type, tournament_mode))
            self.teams = {}
            for name in ("A1", "A2", "B1", "B2"):
                conn.execute("INSERT INTO teams (name, jahrgang) VALUES (?, 8)", (name,))
                self.teams[name] = conn.execute(
                    "SELECT id FROM teams WHERE name = ?", (name,)
                ).fetchone()["id"]
            conn.commit()

    def _slot(self, phase, gruppe=None, team_a=None, team_b=None,
              score_a=None, score_b=None, status="geplant", startzeit="10:00"):
        with get_conn() as conn:
            cursor = conn.execute(INSERT_SLOT, (
                self.court, startzeit, phase, gruppe,
                self.teams.get(team_a), self.teams.get(team_b),
                score_a, score_b, status,
            ))
            conn.commit()
            return cursor.lastrowid

    def _set_status(self, slot_id, status, score_a=None, score_b=None):
        with get_conn() as conn:
            conn.execute(
                "UPDATE slots SET status = ?, score_a = ?, score_b = ? WHERE id = ?",
                (status, score_a, score_b, slot_id),
            )
            conn.commit()

    def _competition(self):
        with get_conn() as conn:
            return conn.execute("SELECT * FROM competitions WHERE id = 1").fetchone()

    def _two_group_plan(self, groups_finished=True):
        status = "beendet" if groups_finished else "geplant"
        score = (2, 1) if groups_finished else (None, None)
        self._slot("Gruppenphase", "A", "A1", "A2", *score, status=status, startzeit="09:00")
        self._slot("Gruppenphase", "B", "B1", "B2", *score, status=status, startzeit="09:00")
        semis = [
            self._slot("Halbfinale", startzeit="10:00"),
            self._slot("Halbfinale", startzeit="10:00"),
        ]
        final = self._slot("Finale", startzeit="11:00")
        small_final = self._slot("Spiel um Platz 3", startzeit="11:00")
        return semis, final, small_final


class NextPhaseActionTests(KoPhaseTestCase):
    def test_nothing_while_group_phase_is_running(self):
        self._make_competition()
        self._two_group_plan(groups_finished=False)
        self.assertIsNone(get_next_phase_action(self._competition()))

    def test_semifinal_offered_after_group_phase(self):
        self._make_competition()
        self._two_group_plan()
        action = get_next_phase_action(self._competition())
        self.assertEqual(action["action"], "/competition/1/generate-semifinals")

    def test_semifinal_no_longer_offered_once_started(self):
        self._make_competition()
        semis, _, _ = self._two_group_plan()
        self._set_status(semis[0], "läuft")
        self.assertIsNone(get_next_phase_action(self._competition()))

    def test_final_offered_after_semifinals_until_final_starts(self):
        self._make_competition()
        semis, final, _ = self._two_group_plan()
        self._set_status(semis[0], "beendet", 3, 1)
        self._set_status(semis[1], "beendet", 0, 2)
        action = get_next_phase_action(self._competition())
        self.assertEqual(action["action"], "/competition/1/generate-finals")

        self._set_status(final, "läuft")
        self.assertIsNone(get_next_phase_action(self._competition()))

    def test_finals_from_table_without_semifinals(self):
        self._make_competition()
        self._slot("Gruppenphase", None, "A1", "A2", 1, 0, status="beendet")
        self._slot("Finale", startzeit="11:00")
        action = get_next_phase_action(self._competition())
        self.assertEqual(action["action"], "/competition/1/generate-finals-from-table")

    def test_no_action_without_final_placeholders(self):
        self._make_competition()
        self._slot("Gruppenphase", None, "A1", "A2", 1, 0, status="beendet")
        self.assertIsNone(get_next_phase_action(self._competition()))

    def test_ko_runde_offers_advance(self):
        self._make_competition(tournament_mode="ko_runde")
        self._slot("Halbfinale", None, "A1", "B2", 2, 0, status="beendet")
        self._slot("Halbfinale", None, "A2", "B1")
        self._slot("Finale", startzeit="11:00")
        action = get_next_phase_action(self._competition())
        self.assertEqual(action["action"], "/competition/1/advance-ko-round")

    def test_punkterunde_without_finals_and_sechskampf_get_nothing(self):
        self._make_competition(tournament_mode="punkterunde")
        self._slot("Gruppenphase", None, "A1", "A2", 1, 0, status="beendet")
        self._slot("Finale", startzeit="11:00")
        self.assertIsNone(get_next_phase_action(self._competition()))
        self.assertIsNone(get_next_phase_action(None))


class KoErrorRedirectTests(KoPhaseTestCase):
    def _post(self, client, path, **data):
        response = client.post(path, data=data, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        location = response.headers["location"]
        return location, parse_qs(urlsplit(location).query)

    def test_semifinal_without_groups_reports_error_in_editor(self):
        from app.main import app as fastapi_app

        self._make_competition()
        self._slot("Gruppenphase", None, "A1", "A2", 1, 0, status="beendet")
        self._slot("Halbfinale")
        with TestClient(fastapi_app) as client:
            location, query = self._post(client, "/competition/1/generate-semifinals")
            self.assertTrue(location.startswith("/spielplan-bearbeiten?"))
            self.assertEqual(query["ko_error"], ["keine_gruppen"])

            page = client.get(location)
            self.assertIn(KO_ERROR_MESSAGES["keine_gruppen"], page.text)

    def test_drawn_semifinal_reports_error_on_results_page(self):
        from app.main import app as fastapi_app

        self._make_competition()
        semis, final, _ = self._two_group_plan()
        with get_conn() as conn:
            conn.execute("UPDATE slots SET team_a_id = ?, team_b_id = ? WHERE id = ?",
                         (self.teams["A1"], self.teams["B2"], semis[0]))
            conn.execute("UPDATE slots SET team_a_id = ?, team_b_id = ? WHERE id = ?",
                         (self.teams["B1"], self.teams["A2"], semis[1]))
            conn.commit()
        self._set_status(semis[0], "beendet", 1, 1)
        self._set_status(semis[1], "beendet", 0, 2)
        with TestClient(fastapi_app) as client:
            location, query = self._post(
                client, "/competition/1/generate-finals",
                results_return_to="/ergebnisse?competition_id=1",
            )
            self.assertTrue(location.startswith("/ergebnisse?"))
            self.assertEqual(query["ko_error"], ["halbfinale_unentschieden"])

            page = client.get(location)
            self.assertIn("ein Halbfinale endete", page.text.replace("Ein", "ein"))

        with get_conn() as conn:
            row = conn.execute("SELECT team_a_id FROM slots WHERE id = ?", (final,)).fetchone()
        self.assertIsNone(row["team_a_id"])

    def test_open_semifinal_reports_error(self):
        from app.main import app as fastapi_app

        self._make_competition()
        self._two_group_plan()
        with TestClient(fastapi_app) as client:
            _, query = self._post(client, "/competition/1/generate-finals")
        self.assertEqual(query["ko_error"], ["halbfinale_offen"])

    def test_empty_table_reports_error(self):
        from app.main import app as fastapi_app

        self._make_competition()
        self._slot("Finale", startzeit="11:00")
        with get_conn() as conn:
            conn.execute("UPDATE teams SET jahrgang = 5")
            conn.commit()
        with TestClient(fastapi_app) as client:
            _, query = self._post(client, "/competition/1/generate-finals-from-table")
        self.assertEqual(query["ko_error"], ["tabelle_zu_klein"])

    def test_error_is_not_carried_over_to_next_results_redirect(self):
        from app.services.results_filter_service import build_results_redirect_url

        url = build_results_redirect_url("/ergebnisse?competition_id=1&ko_error=keine_gruppen")
        self.assertNotIn("ko_error", url)


class ResultsPagePermanentButtonTests(KoPhaseTestCase):
    def test_results_page_shows_permanent_button(self):
        from app.main import app as fastapi_app

        self._make_competition()
        self._two_group_plan()
        with TestClient(fastapi_app) as client:
            page = client.get("/ergebnisse?competition_id=1")
        self.assertEqual(page.status_code, 200)
        self.assertIn('action="/competition/1/generate-semifinals"', page.text)
        self.assertIn("Halbfinale besetzen", page.text)

    def test_results_page_hides_button_while_group_phase_runs(self):
        from app.main import app as fastapi_app

        self._make_competition()
        self._two_group_plan(groups_finished=False)
        with TestClient(fastapi_app) as client:
            page = client.get("/ergebnisse?competition_id=1")
        self.assertNotIn('action="/competition/1/generate-semifinals"', page.text)


class AdvanceKoRoundAccessTests(unittest.TestCase):
    def test_advance_route_needs_referee_like_other_fill_actions(self):
        from app.main import get_required_role

        self.assertEqual(get_required_role("/competition/7/advance-ko-round"), "referee")


if __name__ == "__main__":
    unittest.main()
