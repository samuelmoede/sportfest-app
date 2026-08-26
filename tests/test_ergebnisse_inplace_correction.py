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
from app.utils.formatting import format_score, parse_score


def _create_competition_and_teams(conn):
    conn.execute("INSERT INTO courts (name) VALUES ('Feld 1')")
    court_id = conn.execute(
        "SELECT id FROM courts WHERE name = 'Feld 1'"
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
    team_a_id = conn.execute(
        "SELECT id FROM teams WHERE name = 'Team A'"
    ).fetchone()["id"]
    team_b_id = conn.execute(
        "SELECT id FROM teams WHERE name = 'Team B'"
    ).fetchone()["id"]
    return competition_id, court_id, team_a_id, team_b_id


def _insert_slot(
    conn, *, competition_id, court_id, team_a_id, team_b_id,
    startzeit="10:00", score_a=None, score_b=None, status="geplant",
):
    conn.execute(
        """
        INSERT INTO slots (
            competition_id, court_id, startzeit, slot_typ, phase, gruppe,
            team_a_id, team_b_id, score_a, score_b, status
        ) VALUES (?, ?, ?, 'Spiel', 'Gruppenphase', 'A', ?, ?, ?, ?, ?)
        """,
        (
            competition_id, court_id, startzeit, team_a_id, team_b_id,
            score_a, score_b, status,
        ),
    )
    return conn.execute(
        "SELECT id FROM slots WHERE competition_id = ? AND startzeit = ?",
        (competition_id, startzeit),
    ).fetchone()["id"]


class ErgebnisseInPlaceCorrectionTests(unittest.TestCase):
    """Issue #50: beendete Spiele bleiben auf /ergebnisse an ihrer Position
    stehen (statt in ein separates Archiv zu wandern), sind aber gesperrt und
    nur ueber einen eigenen 'Bearbeiten'-Button korrigierbar."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "ergebnisse-inplace-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

        with get_conn() as conn:
            (
                self.competition_id, self.court_id,
                self.team_a_id, self.team_b_id,
            ) = _create_competition_and_teams(conn)

            self.active_slot_id = _insert_slot(
                conn,
                competition_id=self.competition_id,
                court_id=self.court_id,
                team_a_id=self.team_a_id,
                team_b_id=self.team_b_id,
                startzeit="10:00",
                status="geplant",
            )
            self.finished_slot_id = _insert_slot(
                conn,
                competition_id=self.competition_id,
                court_id=self.court_id,
                team_a_id=self.team_a_id,
                team_b_id=self.team_b_id,
                startzeit="09:00",
                score_a=3,
                score_b=1,
                status="beendet",
            )
            conn.commit()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_finished_slot_stays_in_same_results_section_as_active(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get(
                "/ergebnisse", params={"competition_id": self.competition_id}
            )

        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn('id="results-section"', html)
        self.assertNotIn('id="active-results-section"', html)
        self.assertNotIn('id="archived-results-section"', html)
        # Beide Spiele (aktiv und beendet) tauchen in derselben Sektion auf.
        section_start = html.index('id="results-section"')
        section_html = html[section_start:]
        self.assertIn(f'/slot/{self.finished_slot_id}/save', section_html)
        self.assertIn(f'/slot/{self.active_slot_id}/save', section_html)

    def test_finished_slot_card_is_locked_with_edit_button(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get(
                "/ergebnisse", params={"competition_id": self.competition_id}
            )

        html = response.text
        self.assertIn("Bearbeiten", html)
        self.assertIn('data-was-beendet="1"', html)
        # Score-Felder des beendeten Spiels sind standardmaessig disabled.
        finished_form_start = html.index(f'id="result-form-{self.finished_slot_id}"')
        finished_form_html = html[finished_form_start:finished_form_start + 700]
        self.assertIn("disabled", finished_form_html)

    def test_nur_aktive_filter_hides_finished_slots(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get(
                "/ergebnisse",
                params={"competition_id": self.competition_id, "nur_aktive": "1"},
            )

        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn(f'/slot/{self.active_slot_id}/save', html)
        self.assertNotIn(f'/slot/{self.finished_slot_id}/save', html)
        self.assertIn("checked", html.split('id="nur-aktive-checkbox"')[1][:40])

    def test_correction_of_finished_slot_logs_distinct_action(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.post(
                f"/slot/{self.finished_slot_id}/save",
                data={"score_a": "4", "score_b": "1", "finish": "1"},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)

        with get_conn() as conn:
            row = conn.execute(
                """
                SELECT action, old_value, new_value FROM change_log
                WHERE entity_type = 'slot_result' AND entity_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (self.finished_slot_id,),
            ).fetchone()

        self.assertEqual(row["action"], "Ergebnis nachträglich korrigiert")
        self.assertEqual(row["old_value"], "3:1")
        self.assertEqual(row["new_value"], "4:1")

    def test_undo_button_appears_only_after_a_correction(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            before = client.get(
                "/ergebnisse", params={"competition_id": self.competition_id}
            )
            self.assertNotIn(
                f"/slot/{self.finished_slot_id}/undo-correction", before.text
            )

            client.post(
                f"/slot/{self.finished_slot_id}/save",
                data={"score_a": "4", "score_b": "1", "finish": "1"},
            )

            after = client.get(
                "/ergebnisse", params={"competition_id": self.competition_id}
            )
        self.assertIn(
            f"/slot/{self.finished_slot_id}/undo-correction", after.text
        )
        self.assertIn("3:1", after.text)

    def test_undo_restores_previous_score_and_is_then_no_longer_offered(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            client.post(
                f"/slot/{self.finished_slot_id}/save",
                data={"score_a": "4", "score_b": "1", "finish": "1"},
            )

            undo_response = client.post(
                f"/slot/{self.finished_slot_id}/undo-correction",
                follow_redirects=False,
            )
            self.assertEqual(undo_response.status_code, 303)

            with get_conn() as conn:
                slot = conn.execute(
                    "SELECT score_a, score_b, status FROM slots WHERE id = ?",
                    (self.finished_slot_id,),
                ).fetchone()
            self.assertEqual(slot["score_a"], 3)
            self.assertEqual(slot["score_b"], 1)
            self.assertEqual(slot["status"], "beendet")

            after_undo = client.get(
                "/ergebnisse", params={"competition_id": self.competition_id}
            )
        self.assertNotIn(
            f"/slot/{self.finished_slot_id}/undo-correction", after_undo.text
        )

    def test_undo_is_a_noop_without_a_prior_correction(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.post(
                f"/slot/{self.finished_slot_id}/undo-correction",
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)

        with get_conn() as conn:
            slot = conn.execute(
                "SELECT score_a, score_b FROM slots WHERE id = ?",
                (self.finished_slot_id,),
            ).fetchone()
        self.assertEqual(slot["score_a"], 3)
        self.assertEqual(slot["score_b"], 1)


class ResultSaveButtonFormDataOrderingTests(unittest.TestCase):
    """Regression: 'Beenden & Speichern' loeste faelschlich den Start-Timer aus
    (Verhalten wie 'Starten'), weil der geklickte Submit-Button in
    attachResultSaveHandler() vor dem Aufbau der FormData deaktiviert wurde.
    Ein disabled Submit-Button wird beim Aufbau der Entry-List uebersprungen -
    auch wenn er explizit als `submitter` an FormData(form, submitter)
    uebergeben wird (HTML-Spec) - wodurch das name="finish" value="1"-Feld im
    Request fehlte und der Server (save_slot in app/main.py) mangels "finish"
    auf seinen Default "0" zurueckfiel (status wird 'laeuft' statt 'beendet')."""

    def test_formdata_is_built_before_submitter_is_disabled(self):
        html = (ROOT_DIR / "app" / "templates" / "ergebnisse.html").read_text(
            encoding="utf-8"
        )

        handler_start = html.index("function attachResultSaveHandler")
        handler_end = html.index("function attachCorrectionAreaHandler")
        handler_body = html[handler_start:handler_end]

        formdata_index = handler_body.index("new FormData(form, submitter")
        disable_index = handler_body.index("submitter.disabled = true")

        self.assertLess(
            formdata_index,
            disable_index,
            "submitter darf erst NACH dem FormData(form, submitter)-Aufruf "
            "disabled werden, sonst wird sein name/value-Paar (z.B. finish=1) "
            "beim Aufbau der Entry-List uebersprungen.",
        )


class UndoCorrectionRoleAccessTests(unittest.TestCase):
    """Die neue Undo-Route soll denselben Rollenschutz wie die uebrigen
    Ergebnis-Aktionen bekommen (siehe ACTION_ACCESS_RULES in app/main.py)."""

    def test_undo_correction_requires_referee_role(self):
        from app.main import get_required_role

        self.assertEqual(
            get_required_role("/slot/42/undo-correction"), "referee"
        )


class ParseScoreTests(unittest.TestCase):
    """parse_score() kehrt format_score() um - Grundlage fuer die
    Rueckgaengig-Funktion, die den im Aenderungsprotokoll gespeicherten
    'alt:neu'-String wieder in einzelne Werte zerlegen muss."""

    def test_round_trip_with_both_scores(self):
        self.assertEqual(parse_score(format_score(3, 1)), (3, 1))

    def test_round_trip_with_no_scores(self):
        self.assertEqual(parse_score(format_score(None, None)), (None, None))

    def test_parse_invalid_value_returns_none_tuple(self):
        self.assertEqual(parse_score("not-a-score"), (None, None))
        self.assertEqual(parse_score(""), (None, None))
        self.assertEqual(parse_score(None), (None, None))


if __name__ == "__main__":
    unittest.main()
