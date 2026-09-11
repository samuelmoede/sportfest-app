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
from app.services.users_service import create_user


class LoginRouteTests(unittest.TestCase):
    """HTTP-Ebene des benutzerbasierten Logins (POST /login): loest das
    alte geteilte Rollen-Passwort-System ab (drei separate Formulare mit
    target_role) durch ein einzelnes Benutzername+Passwort-Formular.

    Die Rate-Limit-Zaehler in app/main.py (_login_failures/_login_lockouts)
    sind Prozess-globale Dicts, die pro Client-IP ueber die gesamte
    Test-Laufzeit hinweg bestehen bleiben (siehe Kommentar dort - die App
    laeuft als Einzelprozess). Damit sich Login-Tests nicht gegenseitig
    ueber ihre Fehlversuche sperren, bekommt jede Testmethode ueber den
    CF-Connecting-IP-Header ihre eigene, eindeutige Absender-"IP"."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "login-route-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()
        create_user("wxyz", "GeheimesPw1", "tournament_lead")

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def _client_headers(self):
        return {"CF-Connecting-IP": f"10.0.0.{abs(hash(self.id())) % 250 + 1}"}

    def test_login_page_loads(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Login", response.text)
        self.assertNotIn("Rechte erweitern", response.text)

    def test_successful_login_sets_session_role(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.post(
                "/login",
                data={"username": "wxyz", "password": "GeheimesPw1", "next": "/"},
                headers=self._client_headers(),
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)
            # Folgeanfrage im selben Client traegt das Sitzungscookie.
            settings_response = client.get("/tabellen")
            self.assertEqual(settings_response.status_code, 200)

    def test_login_is_case_insensitive_for_username(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.post(
                "/login",
                data={"username": "WxYz", "password": "GeheimesPw1", "next": "/"},
                headers=self._client_headers(),
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)

    def test_login_is_case_sensitive_for_password(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.post(
                "/login",
                data={"username": "wxyz", "password": "geheimespw1", "next": "/"},
                headers=self._client_headers(),
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 401)
        self.assertIn("nicht korrekt", response.text)

    def test_login_with_unknown_username_fails_generically(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.post(
                "/login",
                data={"username": "UNBEKANNT", "password": "irgendwas", "next": "/"},
                headers=self._client_headers(),
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 401)
        # Fehlermeldung verraet nicht, ob der Benutzername existiert.
        self.assertIn("Benutzername oder Passwort", response.text)

    def test_deactivated_user_cannot_log_in(self):
        from app.main import app as fastapi_app
        from app.services.users_service import get_user_by_username, set_user_active

        user = get_user_by_username("wxyz")
        set_user_active(user["id"], False)

        with TestClient(fastapi_app) as client:
            response = client.post(
                "/login",
                data={"username": "wxyz", "password": "GeheimesPw1", "next": "/"},
                headers=self._client_headers(),
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 401)

    def test_rate_limiting_locks_out_after_repeated_failures(self):
        from app.main import app as fastapi_app

        headers = self._client_headers()
        with TestClient(fastapi_app) as client:
            for _ in range(5):
                client.post(
                    "/login",
                    data={"username": "wxyz", "password": "falsch", "next": "/"},
                    headers=headers,
                    follow_redirects=False,
                )
            locked_response = client.post(
                "/login",
                data={"username": "wxyz", "password": "GeheimesPw1", "next": "/"},
                headers=headers,
                follow_redirects=False,
            )
        self.assertEqual(locked_response.status_code, 429)


if __name__ == "__main__":
    unittest.main()
