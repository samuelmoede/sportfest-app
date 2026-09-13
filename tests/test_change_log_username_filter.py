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
    get_current_username,
    get_recent_change_log,
)
from app.services.users_service import create_user


def _insert_competition(conn, name, jahrgang=5):
    cursor = conn.execute(
        "INSERT INTO competitions (name, sportart, jahrgang) VALUES (?, ?, ?)",
        (name, "Fußball", jahrgang),
    )
    return cursor.lastrowid


def _insert_change_log_entry(
    conn, *, actor_role, competition_id, actor_username=None,
    created_at="2026-08-26 12:00:00",
):
    conn.execute(
        """
        INSERT INTO change_log (
            created_at, actor_role, actor_username, action, entity_type,
            entity_id, competition_id, discipline_id, team_id, old_value,
            new_value
        )
        VALUES (?, ?, ?, 'update', 'slot_result', 1, ?, NULL, NULL, '0:0', '1:0')
        """,
        (created_at, actor_role, actor_username, competition_id),
    )


class ChangeLogActorUsernameMigrationTests(unittest.TestCase):
    """Issue #98: change_log braucht eine (nullable) actor_username-Spalte,
    damit bestehende Datenbanken beim Upgrade automatisch nachziehen
    (siehe die Migrationsbloecke am Ende von database.py)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._tmp_db_path = Path(self._tmpdir.name) / "change-log-username-migration-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", self._tmp_db_path)
        self._db_path_patcher.start()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_fresh_database_has_actor_username_column(self):
        init_db()
        with get_conn() as conn:
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(change_log)").fetchall()
            }
        self.assertIn("actor_username", columns)

    def test_upgrading_a_database_without_the_column_adds_it(self):
        # Schema vor Issue #98 nachstellen: change_log ohne actor_username.
        conn = database.sqlite3.connect(self._tmp_db_path)
        conn.execute(
            """
            CREATE TABLE change_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                actor_role TEXT NOT NULL,
                action TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id INTEGER,
                competition_id INTEGER,
                discipline_id INTEGER,
                team_id INTEGER,
                old_value TEXT,
                new_value TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO change_log (
                created_at, actor_role, action, entity_type, entity_id
            ) VALUES ('2026-01-01 10:00:00', 'admin', 'update', 'slot_result', 1)
            """
        )
        conn.commit()
        conn.close()

        init_db()

        with get_conn() as conn:
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(change_log)").fetchall()
            }
            self.assertIn("actor_username", columns)
            # Bestehende Eintraege bleiben NULL, sind aber weiterhin lesbar.
            row = conn.execute(
                "SELECT actor_username FROM change_log WHERE entity_id = 1"
            ).fetchone()
            self.assertIsNone(row["actor_username"])


class ChangeLogUsernameFilterServiceTests(unittest.TestCase):
    """Der Benutzer-Filter soll unabhaengig vom Rollen-Filter nutzbar sein,
    aber auch kombiniert funktionieren (Issue #98)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "change-log-username-service-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

        with get_conn() as conn:
            self.comp_a = _insert_competition(conn, "Fußballturnier A")
            self.comp_b = _insert_competition(conn, "Fußballturnier B")
            _insert_change_log_entry(
                conn, actor_role="referee", actor_username="MOSA",
                competition_id=self.comp_a,
            )
            _insert_change_log_entry(
                conn, actor_role="admin", actor_username="ADMIN",
                competition_id=self.comp_a,
            )
            _insert_change_log_entry(
                conn, actor_role="referee", actor_username="MOSA",
                competition_id=self.comp_b,
            )
            # Alteintrag ohne Benutzer (vor der Migration protokolliert).
            _insert_change_log_entry(
                conn, actor_role="referee", actor_username=None,
                competition_id=self.comp_b,
            )
            conn.commit()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_filter_by_username_only(self):
        rows = get_recent_change_log(username="MOSA")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["actor_username"] == "MOSA" for row in rows))

    def test_filter_by_username_and_role_are_independent(self):
        # Rolle allein: 3 referee-Eintraege (inkl. dem ohne Benutzer).
        self.assertEqual(len(get_recent_change_log(role="referee")), 3)
        # Benutzer allein: 2 MOSA-Eintraege (unabhaengig vom Wettbewerb).
        self.assertEqual(len(get_recent_change_log(username="MOSA")), 2)

    def test_combined_username_and_competition_filter(self):
        rows = get_recent_change_log(username="MOSA", competition_id=self.comp_a)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["competition_id"], self.comp_a)
        self.assertEqual(rows[0]["actor_username"], "MOSA")

    def test_combined_username_and_role_with_no_matches_returns_empty(self):
        rows = get_recent_change_log(username="ADMIN", role="referee")
        self.assertEqual(rows, [])

    def test_filter_options_list_distinct_usernames_excluding_null(self):
        options = get_change_log_filter_options()
        self.assertEqual(options["usernames"], ["ADMIN", "MOSA"])


class GetCurrentUsernameTests(unittest.TestCase):
    """get_current_username() liest den angemeldeten Benutzer aus der Session
    (siehe session["username"] in /login) - fehlt er (kein Login bzw. der
    alte geteilte Admin-Login), liefert die Funktion None."""

    class _FakeRequest:
        def __init__(self, session):
            self.session = session

    def test_returns_username_from_session(self):
        request = self._FakeRequest({"username": "MOSA"})
        self.assertEqual(get_current_username(request), "MOSA")

    def test_returns_none_without_session_username(self):
        request = self._FakeRequest({})
        self.assertIsNone(get_current_username(request))

    def test_returns_none_for_legacy_admin_login(self):
        request = self._FakeRequest({"admin_logged_in": True, "role": "admin"})
        self.assertIsNone(get_current_username(request))


class ChangeLogUsernameRouteTests(unittest.TestCase):
    """End-to-End: ein angemeldeter Benutzer, der ein Ergebnis korrigiert,
    landet mit seinem Benutzernamen im Aenderungsprotokoll - und dieses
    laesst sich in /einstellungen nach genau diesem Benutzer filtern."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "change-log-username-route-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()
        create_user("wxyz", "GeheimesPw1", "referee")

        with get_conn() as conn:
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
            conn.execute(
                """
                INSERT INTO slots (
                    competition_id, court_id, startzeit, slot_typ, phase, gruppe,
                    team_a_id, team_b_id, score_a, score_b, status
                ) VALUES (?, ?, '09:00', 'Spiel', 'Gruppenphase', 'A', ?, ?, 3, 1, 'beendet')
                """,
                (competition_id, court_id, team_a_id, team_b_id),
            )
            self.slot_id = conn.execute(
                "SELECT id FROM slots WHERE competition_id = ? AND startzeit = '09:00'",
                (competition_id,),
            ).fetchone()["id"]
            conn.commit()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def _client_headers(self):
        return {"CF-Connecting-IP": f"10.0.1.{abs(hash(self.id())) % 250 + 1}"}

    def test_logged_in_correction_records_actor_username(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            login_response = client.post(
                "/login",
                data={"username": "wxyz", "password": "GeheimesPw1", "next": "/"},
                headers=self._client_headers(),
                follow_redirects=False,
            )
            self.assertEqual(login_response.status_code, 303)

            client.post(
                f"/slot/{self.slot_id}/save",
                data={"score_a": "4", "score_b": "1", "finish": "1"},
            )

        with get_conn() as conn:
            row = conn.execute(
                """
                SELECT actor_username FROM change_log
                WHERE entity_type = 'slot_result' AND entity_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (self.slot_id,),
            ).fetchone()
        self.assertEqual(row["actor_username"], "WXYZ")

    def test_settings_page_can_filter_by_the_recorded_username(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            client.post(
                "/login",
                data={"username": "wxyz", "password": "GeheimesPw1", "next": "/"},
                headers=self._client_headers(),
                follow_redirects=False,
            )
            client.post(
                f"/slot/{self.slot_id}/save",
                data={"score_a": "4", "score_b": "1", "finish": "1"},
            )

            matching = client.get(
                "/einstellungen", params={"change_log_username": "WXYZ"}
            )
            self.assertEqual(matching.status_code, 200)
            self.assertIn("Ergebnis nachträglich korrigiert", matching.text)
            self.assertIn('<option value="WXYZ" selected>', matching.text)

            other_user = client.get(
                "/einstellungen", params={"change_log_username": "ADMIN"}
            )
            self.assertEqual(other_user.status_code, 200)
            self.assertIn("Keine Einträge für die gewählte Filterauswahl.", other_user.text)

    def test_ajax_fragment_route_applies_username_filter(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            client.post(
                "/login",
                data={"username": "wxyz", "password": "GeheimesPw1", "next": "/"},
                headers=self._client_headers(),
                follow_redirects=False,
            )
            client.post(
                f"/slot/{self.slot_id}/save",
                data={"score_a": "4", "score_b": "1", "finish": "1"},
            )

            response = client.get(
                "/einstellungen/aenderungsprotokoll",
                params={"change_log_username": "WXYZ"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn('id="change-log-content"', response.text)
            self.assertIn("Ergebnis nachträglich korrigiert", response.text)


if __name__ == "__main__":
    unittest.main()
