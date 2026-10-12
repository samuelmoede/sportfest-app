"""Tests fuer den CSRF-Schutz per Herkunftspruefung
(app/services/csrf_service.py und CsrfOriginMiddleware in app/main.py)."""
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
from app.services.csrf_service import get_trusted_hosts, is_request_origin_allowed

HOST = {"host": "sportfest.example.org"}


class OriginCheckTests(unittest.TestCase):
    def allowed(self, method="POST", extra="", **headers):
        return is_request_origin_allowed(method, {**HOST, **headers}, extra_origins=extra)

    def test_reading_requests_are_never_checked(self):
        self.assertTrue(self.allowed("GET", origin="https://evil.example"))

    def test_post_without_origin_and_referer_is_allowed(self):
        self.assertTrue(self.allowed())

    def test_same_origin_is_allowed_regardless_of_scheme(self):
        self.assertTrue(self.allowed(origin="https://sportfest.example.org"))
        self.assertTrue(self.allowed(origin="http://SPORTFEST.example.org"))

    def test_foreign_and_null_origin_are_rejected(self):
        self.assertFalse(self.allowed(origin="https://evil.example"))
        self.assertFalse(self.allowed(origin="null"))
        self.assertFalse(self.allowed(origin="https://sportfest.example.org.evil.example"))

    def test_other_port_is_another_origin(self):
        self.assertFalse(self.allowed(origin="http://sportfest.example.org:8502"))

    def test_referer_is_used_when_origin_is_missing(self):
        self.assertTrue(self.allowed(referer="https://sportfest.example.org/ergebnisse?x=1"))
        self.assertFalse(self.allowed(referer="https://evil.example/page"))

    def test_origin_wins_over_referer(self):
        self.assertFalse(self.allowed(
            origin="https://evil.example",
            referer="https://sportfest.example.org/",
        ))

    def test_proxy_forwarded_host_and_trusted_origins(self):
        internal = {"host": "192.168.178.20:8500"}
        self.assertFalse(is_request_origin_allowed(
            "POST", {**internal, "origin": "https://sportfest.example.org"}, extra_origins=""
        ))
        self.assertTrue(is_request_origin_allowed(
            "POST",
            {**internal, "origin": "https://sportfest.example.org",
             "x-forwarded-host": "sportfest.example.org"},
            extra_origins="",
        ))
        self.assertTrue(is_request_origin_allowed(
            "POST", {**internal, "origin": "https://sportfest.example.org"},
            extra_origins="https://other.example, https://sportfest.example.org",
        ))

    def test_trusted_hosts_read_environment_by_default(self):
        with patch.dict("os.environ", {"SPORTFEST_TRUSTED_ORIGINS": "https://a.example:8443"}):
            self.assertIn("a.example:8443", get_trusted_hosts({"host": "b.example"}))


class CsrfMiddlewareTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "csrf-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def _post_login(self, security_enabled, origin):
        from app.main import app as fastapi_app

        with patch("app.main.is_security_enabled", return_value=security_enabled):
            with TestClient(fastapi_app) as client:
                return client.post(
                    "/login",
                    data={"username": "NIEMAND", "password": "falsch"},
                    headers={"origin": origin},
                    follow_redirects=False,
                )

    def test_foreign_origin_is_rejected_when_security_is_enabled(self):
        response = self._post_login(True, "https://evil.example")
        self.assertEqual(response.status_code, 403)
        self.assertIn("CSRF", response.text)

    def test_same_origin_passes_when_security_is_enabled(self):
        response = self._post_login(True, "http://testserver")
        self.assertNotEqual(response.status_code, 403)

    def test_disabled_security_stays_unchanged(self):
        response = self._post_login(False, "https://evil.example")
        self.assertNotEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
