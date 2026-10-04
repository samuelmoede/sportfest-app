import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi.testclient import TestClient

import app.database as database
from app.database import get_conn, init_db
from app.services.users_service import (
    SESSION_MAX_AGE_SECONDS,
    create_user,
    end_user_session,
    get_user_by_username,
    list_logged_in_sessions,
    start_user_session,
    touch_user_session,
)


class _TempDbTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "user-sessions-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()
        create_user("tsta", "GeheimesPw1", "referee")
        self.user_id = get_user_by_username("TSTA")["id"]

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def _set_last_seen(self, token, value):
        with get_conn() as conn:
            conn.execute(
                "UPDATE user_sessions SET last_seen_at = ? WHERE token = ?",
                (value.strftime("%Y-%m-%d %H:%M:%S"), token),
            )
            conn.commit()


class UserSessionServiceTests(_TempDbTestCase):
    def test_started_session_is_listed_as_online(self):
        start_user_session(self.user_id)

        sessions = list_logged_in_sessions()

        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["username"], "TSTA")
        self.assertEqual(sessions[0]["role"], "referee")
        self.assertTrue(sessions[0]["online"])

    def test_ended_session_is_not_listed(self):
        token = start_user_session(self.user_id)
        end_user_session(token)

        self.assertEqual(list_logged_in_sessions(), [])

    def test_idle_session_is_listed_but_not_online(self):
        token = start_user_session(self.user_id)
        self._set_last_seen(token, datetime.now(timezone.utc) - timedelta(hours=2))

        sessions = list_logged_in_sessions()

        self.assertEqual(len(sessions), 1)
        self.assertFalse(sessions[0]["online"])

    def test_session_older_than_cookie_lifetime_is_expired(self):
        token = start_user_session(self.user_id)
        self._set_last_seen(
            token,
            datetime.now(timezone.utc) - timedelta(seconds=SESSION_MAX_AGE_SECONDS + 60),
        )

        self.assertEqual(list_logged_in_sessions(), [])

    def test_touch_updates_last_activity(self):
        token = start_user_session(self.user_id)
        self._set_last_seen(token, datetime.now(timezone.utc) - timedelta(hours=2))

        touch_user_session(token, self.user_id, force=True)

        self.assertTrue(list_logged_in_sessions()[0]["online"])

    def test_touch_creates_missing_session_row(self):
        # Login von vor Einfuehrung der Tabelle: Token existiert nur im Cookie.
        touch_user_session("altes-token", self.user_id, force=True)

        sessions = list_logged_in_sessions()
        self.assertEqual([entry["username"] for entry in sessions], ["TSTA"])

    def test_touch_does_not_revive_ended_session(self):
        token = start_user_session(self.user_id)
        end_user_session(token)

        touch_user_session(token, self.user_id, force=True)

        self.assertEqual(list_logged_in_sessions(), [])

    def test_most_recently_active_session_comes_first(self):
        create_user("abcd", "Pw", "admin")
        other_id = get_user_by_username("ABCD")["id"]
        older = start_user_session(self.user_id)
        start_user_session(other_id)
        self._set_last_seen(older, datetime.now(timezone.utc) - timedelta(minutes=30))

        sessions = list_logged_in_sessions()

        self.assertEqual([entry["username"] for entry in sessions], ["ABCD", "TSTA"])


class UserSessionRouteTests(_TempDbTestCase):
    def _login(self, client):
        response = client.post(
            "/login",
            data={"username": "tsta", "password": "GeheimesPw1", "next": "/"},
            headers={"CF-Connecting-IP": "10.9.8.7"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)

    def test_sidebar_shows_username_instead_of_role_when_logged_in(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            before = client.get("/tabellen").text
            self._login(client)
            after = client.get("/tabellen").text

        self.assertIn("Rolle:</span>", before)
        self.assertIn('<span class="role-badge-label">TSTA</span>', after)
        self.assertNotIn('<span class="role-badge-label">Schiedsrichter</span>', after)

    def test_settings_lists_logged_in_user_until_logout(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            self._login(client)
            settings_page = client.get("/einstellungen").text
            self.assertIn("Derzeit angemeldet", settings_page)
            self.assertIn("gerade aktiv", settings_page)
            self.assertEqual(len(list_logged_in_sessions()), 1)

            client.get("/logout", follow_redirects=False)

        self.assertEqual(list_logged_in_sessions(), [])


if __name__ == "__main__":
    unittest.main()
