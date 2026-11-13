"""Tests fuer den Zwischenspeicher oeffentlicher Leseseiten
(app/services/public_cache_service.py und PublicCacheMiddleware in app/main.py)."""
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
from app.services.public_cache_service import (
    get_public_cache_seconds,
    is_public_cache_path,
    public_cache_control,
)
from app.services.users_service import create_user


class PublicCacheControlTests(unittest.TestCase):
    def test_anonymous_public_page_is_shared_for_a_few_seconds(self):
        self.assertEqual(
            public_cache_control("GET", "/spielplan", 200, False, seconds=15),
            "public, max-age=0, s-maxage=15",
        )

    def test_all_reading_pages_for_viewers_are_covered(self):
        for path in ("/", "/spielplan", "/spielplan/aushang", "/tabellen", "/beamer",
                     "/durchsage/aktuell", "/siegerehrung/3"):
            self.assertTrue(is_public_cache_path(path), path)

    def test_protected_and_writing_paths_are_never_shared(self):
        for path in ("/ergebnisse", "/einstellungen", "/teams", "/login", "/logout",
                     "/tabellen/csv", "/spielplan-bearbeiten", "/urkunden", "/events/1"):
            self.assertIsNone(public_cache_control("GET", path, 200, False, seconds=15), path)
        self.assertIsNone(public_cache_control("POST", "/spielplan", 200, False, seconds=15))

    def test_session_makes_the_page_private(self):
        self.assertEqual(public_cache_control("GET", "/", 200, True, seconds=15), "private")

    def test_errors_and_redirects_are_not_shared(self):
        self.assertIsNone(public_cache_control("GET", "/", 303, False, seconds=15))
        self.assertIsNone(public_cache_control("GET", "/siegerehrung/9", 404, False, seconds=15))

    def test_zero_seconds_switches_everything_off(self):
        self.assertIsNone(public_cache_control("GET", "/", 200, False, seconds=0))
        self.assertIsNone(public_cache_control("GET", "/", 200, True, seconds=0))

    def test_seconds_from_environment(self):
        self.assertEqual(get_public_cache_seconds(""), 15)
        self.assertEqual(get_public_cache_seconds("abc"), 15)
        self.assertEqual(get_public_cache_seconds("0"), 0)
        self.assertEqual(get_public_cache_seconds("-5"), 0)
        self.assertEqual(get_public_cache_seconds("30"), 30)
        self.assertEqual(get_public_cache_seconds("9999"), 300)
        with patch.dict("os.environ", {"SPORTFEST_PUBLIC_CACHE_SECONDS": "20"}):
            self.assertEqual(get_public_cache_seconds(), 20)


class PublicCacheMiddlewareTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "public-cache-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()
        create_user("wxyz", "GeheimesPw1", "tournament_lead")

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_anonymous_visitor_gets_shared_cache_header(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            for path in ("/", "/spielplan", "/tabellen", "/beamer", "/durchsage/aktuell"):
                response = client.get(path)
                self.assertEqual(response.status_code, 200, path)
                self.assertNotIn("set-cookie", response.headers, path)
                self.assertEqual(
                    response.headers.get("cache-control"),
                    "public, max-age=0, s-maxage=15", path,
                )

    def test_logged_in_user_gets_private_pages(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            login = client.post(
                "/login", data={"username": "wxyz", "password": "GeheimesPw1"},
                follow_redirects=False,
            )
            self.assertIn("set-cookie", login.headers)
            for path in ("/", "/spielplan", "/tabellen", "/durchsage/aktuell"):
                response = client.get(path)
                self.assertEqual(response.headers.get("cache-control"), "private", path)

    def test_other_pages_keep_their_headers(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            self.assertNotIn("cache-control", client.get("/login").headers)
            self.assertNotIn("cache-control", client.get("/impressum").headers)

    def test_environment_can_switch_it_off(self):
        from app.main import app as fastapi_app

        with patch.dict("os.environ", {"SPORTFEST_PUBLIC_CACHE_SECONDS": "0"}):
            with TestClient(fastapi_app) as client:
                self.assertNotIn("cache-control", client.get("/spielplan").headers)
                self.assertEqual(
                    client.get("/durchsage/aktuell").headers.get("cache-control"), "no-store"
                )


if __name__ == "__main__":
    unittest.main()
