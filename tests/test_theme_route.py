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


class SiteThemeRouteTests(unittest.TestCase):
    """POST /einstellungen/theme setzt das site-weite Farbschema, das dann in
    base.html als data-theme-Attribut auf jeder oeffentlichen Seite landet -
    ohne dass Besucher sich einloggen muessen (security_enabled ist per
    Default aus, siehe CLAUDE.md 'Caveats')."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "theme-route-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_default_theme_is_rendered_on_public_page(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get("/")
            self.assertEqual(response.status_code, 200)
            self.assertIn('data-theme="standard"', response.text)

    def test_updating_theme_reflects_on_public_pages(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            update_response = client.post(
                "/einstellungen/theme",
                data={"site_theme": "dunkel"},
                follow_redirects=False,
            )
            self.assertEqual(update_response.status_code, 303)
            self.assertIn("theme_status=saved", update_response.headers["location"])

            dashboard_response = client.get("/")
            self.assertIn('data-theme="dunkel"', dashboard_response.text)

            beamer_response = client.get("/beamer")
            self.assertEqual(beamer_response.status_code, 200)

    def test_invalid_theme_is_rejected(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.post(
                "/einstellungen/theme",
                data={"site_theme": "does-not-exist"},
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)
            self.assertIn("theme_status=invalid", response.headers["location"])

            dashboard_response = client.get("/")
            self.assertIn('data-theme="standard"', dashboard_response.text)

    def test_settings_page_offers_theme_selection(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get("/einstellungen")
            self.assertEqual(response.status_code, 200)
            self.assertIn('name="site_theme"', response.text)
            self.assertIn("Dunkel", response.text)


if __name__ == "__main__":
    unittest.main()
