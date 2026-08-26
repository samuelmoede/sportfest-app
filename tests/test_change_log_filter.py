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
from app.services.settings_service import (
    get_change_log_filter_options,
    get_recent_change_log,
)


def _insert_competition(conn, name, jahrgang=5):
    cursor = conn.execute(
        "INSERT INTO competitions (name, sportart, jahrgang) VALUES (?, ?, ?)",
        (name, "Fußball", jahrgang),
    )
    return cursor.lastrowid


def _insert_change_log_entry(
    conn, *, actor_role, competition_id, created_at="2026-08-26 12:00:00"
):
    conn.execute(
        """
        INSERT INTO change_log (
            created_at, actor_role, action, entity_type, entity_id,
            competition_id, discipline_id, team_id, old_value, new_value
        )
        VALUES (?, ?, 'update', 'slot_result', 1, ?, NULL, NULL, '0:0', '1:0')
        """,
        (created_at, actor_role, competition_id),
    )


class ChangeLogFilterOptionsTests(unittest.TestCase):
    """Issue #45: die Filterauswahl im Aenderungsprotokoll (Einstellungen)
    soll nur tatsaechlich im Protokoll vorkommende Rollen/Wettbewerbe
    anbieten, keine statische Liste - siehe get_change_log_filter_options()
    in settings_service.py."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "change-log-filter-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_no_options_without_entries(self):
        options = get_change_log_filter_options()
        self.assertEqual(options["roles"], [])
        self.assertEqual(options["competitions"], [])

    def test_options_only_contain_actually_used_roles_and_competitions(self):
        with get_conn() as conn:
            comp_a = _insert_competition(conn, "Fußballturnier A")
            _insert_competition(conn, "Fußballturnier B (ungenutzt)")
            _insert_change_log_entry(conn, actor_role="referee", competition_id=comp_a)
            _insert_change_log_entry(conn, actor_role="admin", competition_id=comp_a)
            conn.commit()

        options = get_change_log_filter_options()
        self.assertEqual(
            {role["key"] for role in options["roles"]}, {"referee", "admin"}
        )
        self.assertEqual(len(options["competitions"]), 1)
        self.assertEqual(options["competitions"][0]["name"], "Fußballturnier A")

    def test_role_labels_are_human_readable(self):
        with get_conn() as conn:
            comp_a = _insert_competition(conn, "Fußballturnier A")
            _insert_change_log_entry(conn, actor_role="tournament_lead", competition_id=comp_a)
            conn.commit()

        options = get_change_log_filter_options()
        self.assertEqual(options["roles"][0]["label"], "Turnierleitung")


class ChangeLogFilterByRoleAndCompetitionTests(unittest.TestCase):
    """Beide Filter (Rolle, Wettbewerb) sollen kombinierbar sein - siehe
    get_recent_change_log() in settings_service.py."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "change-log-filter-combo-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

        with get_conn() as conn:
            self.comp_a = _insert_competition(conn, "Fußballturnier A")
            self.comp_b = _insert_competition(conn, "Fußballturnier B")
            _insert_change_log_entry(
                conn, actor_role="referee", competition_id=self.comp_a
            )
            _insert_change_log_entry(
                conn, actor_role="admin", competition_id=self.comp_a
            )
            _insert_change_log_entry(
                conn, actor_role="referee", competition_id=self.comp_b
            )
            conn.commit()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_no_filter_returns_all_entries(self):
        self.assertEqual(len(get_recent_change_log()), 3)

    def test_filter_by_role_only(self):
        rows = get_recent_change_log(role="referee")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["actor_role"] == "referee" for row in rows))

    def test_filter_by_competition_only(self):
        rows = get_recent_change_log(competition_id=self.comp_a)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["competition_id"] == self.comp_a for row in rows))

    def test_combined_filter_role_and_competition(self):
        rows = get_recent_change_log(role="referee", competition_id=self.comp_a)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["actor_role"], "referee")
        self.assertEqual(rows[0]["competition_id"], self.comp_a)

    def test_combined_filter_with_no_matches_returns_empty(self):
        rows = get_recent_change_log(role="admin", competition_id=self.comp_b)
        self.assertEqual(rows, [])


class ChangeLogFilterRouteTests(unittest.TestCase):
    """/einstellungen: Filterauswahl im Formular sowie automatischer Reset
    der Filterauswahl beim Zuruecksetzen des Aenderungszaehlers (Issue #45)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "change-log-filter-route-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

        with get_conn() as conn:
            self.comp_a = _insert_competition(conn, "Fußballturnier A")
            _insert_change_log_entry(
                conn, actor_role="referee", competition_id=self.comp_a
            )
            _insert_change_log_entry(conn, actor_role="admin", competition_id=None)
            conn.commit()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_filter_form_preselects_chosen_role(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get(
                "/einstellungen", params={"change_log_role": "referee"}
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn(
                '<option value="referee" selected>', response.text
            )
            self.assertNotIn('<option value="admin" selected>', response.text)

    def test_filter_by_role_reduces_displayed_entries(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get(
                "/einstellungen", params={"change_log_role": "admin"}
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn("Angezeigt werden die letzten 1 Einträge.", response.text)

    def test_reset_change_counter_clears_filter_selection(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.post(
                "/einstellungen/reset-aenderungszaehler",
                follow_redirects=True,
            )
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("change_log_role=", str(response.url))
            self.assertNotIn("change_log_competition_id=", str(response.url))
            self.assertNotIn('<option value="referee" selected>', response.text)
            self.assertNotIn('<option value="admin" selected>', response.text)


if __name__ == "__main__":
    unittest.main()
