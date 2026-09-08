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


class NavAssistentVisibilityTests(unittest.TestCase):
    """Issue #83: der Assistent-Nav-Eintrag (Gruppe "Verwaltung") fehlte im
    Desktop-Sidebar-Nav-Block von base.html, obwohl er im mobilen Drawer-Block
    vorhanden war (siehe PR #81). Ausserdem soll Turnier-Schnellstart kein
    eigener Nav-Eintrag mehr sein, da er jetzt in den Assistenten eingebettet
    ist (siehe test_wizard_route.py::WizardQuickstartIntegrationTests)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "nav-visibility-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_assistent_link_appears_in_mobile_and_desktop_nav(self):
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            response = client.get("/")
        self.assertEqual(response.status_code, 200)
        # Ein Link im mobilen Drawer-Block, einer im Desktop-Sidebar-Block.
        self.assertEqual(response.text.count('href="/assistent"'), 2)

    def test_turnier_schnellstart_no_longer_in_nav(self):
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            response = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('href="/turnier-schnellstart"', response.text)

    def test_turnier_schnellstart_route_still_reachable_directly(self):
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            response = client.get("/turnier-schnellstart")
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
