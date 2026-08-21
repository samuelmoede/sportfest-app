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


class ScrollRestoreTests(unittest.TestCase):
    """base.html merkt vor jedem POST-Formular-Submit die Scrollposition in
    sessionStorage und stellt sie nach dem Redirect-Reload auf derselben
    Seite wieder her (siehe Issue: Scroll-Position soll global erhalten
    bleiben, nicht nur pro Formular). Da es keine JS-Testinfrastruktur gibt
    (siehe CLAUDE.md - nur pytest fuer Python), prueft dieser Test auf
    Service-/Rendering-Ebene, dass die Restore-Logik tatsaechlich auf jeder
    Seite ausgeliefert wird und nicht versehentlich entfernt/pro Template
    dupliziert werden muss."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "scroll-restore-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_scroll_restore_script_present_on_public_pages(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            for path in ("/", "/einstellungen", "/tabellen"):
                response = client.get(path)
                self.assertEqual(response.status_code, 200, f"{path} -> {response.status_code}")
                self.assertIn("form-restore-state", response.text, path)
                self.assertIn("sessionStorage", response.text, path)
                self.assertIn("scrollTo", response.text, path)

    def test_theme_update_redirects_to_same_pathname(self):
        # Die Restore-Logik vergleicht window.location.pathname vor dem
        # Submit mit dem Pathname nach dem Redirect - Query-Parameter wie
        # ?theme_status=saved duerfen den Abgleich nicht stoeren.
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.post(
                "/einstellungen/theme",
                data={"site_theme": "dunkel"},
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)
            location = response.headers["location"]
            self.assertTrue(location.startswith("/einstellungen"))
            pathname = location.split("?", 1)[0]
            self.assertEqual(pathname, "/einstellungen")

    def test_submit_highlight_style_defined(self):
        css_path = ROOT_DIR / "app" / "static" / "css" / "results.css"
        css_text = css_path.read_text(encoding="utf-8")
        self.assertIn(".submit-restore-highlight", css_text)


if __name__ == "__main__":
    unittest.main()
