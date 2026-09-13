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
from app.database import init_db
from app.services.users_service import (
    create_user,
    get_user_by_username,
    set_user_password,
)


class SettingsUserManagementRouteTests(unittest.TestCase):
    """Benutzerverwaltung unter /einstellungen (loest die alten
    "Passwörter ändern"-Formulare pro Rolle ab): Benutzer anlegen, Rolle
    aendern, Passwort setzen, (de)aktivieren - siehe app/routes/settings.py
    und den "Benutzer"-Abschnitt in einstellungen.html."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "settings-routes-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_einstellungen_page_lists_seeded_users(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get("/einstellungen")
        self.assertEqual(response.status_code, 200)
        self.assertIn("ADMIN", response.text)
        self.assertIn("MOSA", response.text)
        # Rollenzuweisung als Radio-Buttons, nicht als Dropdown (nur der
        # Benutzer-Abschnitt zaehlt - andere Einstellungen wie das
        # Farbschema duerfen weiterhin ein <select> nutzen).
        start = response.text.index('id="benutzer"')
        end = response.text.index('id="aenderungsprotokoll"')
        benutzer_section = response.text[start:end]
        self.assertIn('type="radio"', benutzer_section)
        self.assertNotIn("<select", benutzer_section)

    def test_create_user_route_creates_user(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.post(
                "/einstellungen/benutzer/anlegen",
                data={"username": "wxyz", "password": "pw123456", "role": "referee"},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertIn("user_status=ok", response.headers["location"])
        self.assertIsNotNone(get_user_by_username("wxyz"))

    def test_create_user_route_rejects_duplicate_username(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.post(
                "/einstellungen/benutzer/anlegen",
                data={"username": "admin", "password": "pw123456", "role": "referee"},
                follow_redirects=False,
            )
        self.assertIn("user_status=duplicate_username", response.headers["location"])

    def test_update_user_role_route_changes_role(self):
        from app.main import app as fastapi_app

        create_user("wxyz", "pw123456", "referee")
        user = get_user_by_username("wxyz")

        with TestClient(fastapi_app) as client:
            response = client.post(
                f"/einstellungen/benutzer/{user['id']}/rolle",
                data={"role": "tournament_lead"},
                follow_redirects=False,
            )
        self.assertIn("user_status=ok", response.headers["location"])
        self.assertEqual(get_user_by_username("wxyz")["role"], "tournament_lead")

    def test_update_user_password_route_changes_password(self):
        from app.main import app as fastapi_app
        from app.services.users_service import verify_login

        create_user("wxyz", "altes-pw12", "referee")
        user = get_user_by_username("wxyz")

        with TestClient(fastapi_app) as client:
            response = client.post(
                f"/einstellungen/benutzer/{user['id']}/passwort",
                data={"new_password": "neues-pw12"},
                follow_redirects=False,
            )
        self.assertIn("user_status=ok", response.headers["location"])
        self.assertIsNotNone(verify_login("wxyz", "neues-pw12"))

    def test_deactivate_and_reactivate_user_route(self):
        from app.main import app as fastapi_app

        create_user("wxyz", "pw123456", "referee")
        user = get_user_by_username("wxyz")

        with TestClient(fastapi_app) as client:
            deactivate_response = client.post(
                f"/einstellungen/benutzer/{user['id']}/aktiv",
                data={"active": "false"},
                follow_redirects=False,
            )
            self.assertIn("user_status=ok", deactivate_response.headers["location"])
            self.assertIsNone(get_user_by_username("wxyz"))

            reactivate_response = client.post(
                f"/einstellungen/benutzer/{user['id']}/aktiv",
                data={"active": "true"},
                follow_redirects=False,
            )
            self.assertIn("user_status=ok", reactivate_response.headers["location"])
            self.assertIsNotNone(get_user_by_username("wxyz"))

    def test_deactivate_last_admin_is_blocked(self):
        from app.main import app as fastapi_app

        admin = get_user_by_username("ADMIN")
        with TestClient(fastapi_app) as client:
            response = client.post(
                f"/einstellungen/benutzer/{admin['id']}/aktiv",
                data={"active": "false"},
                follow_redirects=False,
            )
        self.assertIn("user_status=last_admin", response.headers["location"])
        self.assertIsNotNone(get_user_by_username("ADMIN"))


class SecurityToggleRouteTests(unittest.TestCase):
    """Der Sicherheits-Schalter verlangt eine Passwort-Re-Bestaetigung -
    frueher gegen das eine geteilte Admin-Passwort, jetzt gegen das
    Passwort des GERADE angemeldeten Admin-Benutzers (siehe
    verify_user_password() in users_service.py)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "security-toggle-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()
        set_user_password(get_user_by_username("ADMIN")["id"], "admin-pw-123")

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def _login_as_admin(self, client):
        return client.post(
            "/login",
            data={"username": "ADMIN", "password": "admin-pw-123", "next": "/"},
            headers={"CF-Connecting-IP": "10.1.1.1"},
            follow_redirects=False,
        )

    def test_enable_security_with_correct_own_password(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            self._login_as_admin(client)
            response = client.post(
                "/einstellungen/security",
                data={"security_enabled": "true", "admin_password": "admin-pw-123"},
                follow_redirects=False,
            )
        self.assertIn("security_status=enabled", response.headers["location"])

    def test_enable_security_rejects_wrong_password(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            self._login_as_admin(client)
            response = client.post(
                "/einstellungen/security",
                data={"security_enabled": "true", "admin_password": "falsches-pw"},
                follow_redirects=False,
            )
        self.assertIn("security_status=invalid_password", response.headers["location"])

    def test_enable_security_rejects_without_session(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.post(
                "/einstellungen/security",
                data={"security_enabled": "true", "admin_password": "admin-pw-123"},
                follow_redirects=False,
            )
        self.assertIn("security_status=invalid_password", response.headers["location"])


if __name__ == "__main__":
    unittest.main()
