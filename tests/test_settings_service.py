import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import app.database as database
from app.database import get_conn, init_db
from app.services.settings_service import (
    DEFAULT_SITE_THEME,
    SITE_THEMES,
    get_site_theme,
    set_setting,
    set_site_theme,
)


class SiteThemeSettingTests(unittest.TestCase):
    """Site-weite Theme-Einstellung: Admin waehlt in /einstellungen ein Design,
    das dann fuer alle Besucher (ohne Login) gilt - siehe get_setting/set_setting
    Muster in settings_service.py."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "settings-theme-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_default_theme_is_standard(self):
        self.assertEqual(get_site_theme(), DEFAULT_SITE_THEME)
        self.assertEqual(get_site_theme(), "standard")

    def test_set_site_theme_valid_persists(self):
        self.assertTrue(set_site_theme("dunkel"))
        self.assertEqual(get_site_theme(), "dunkel")

    def test_set_site_theme_invalid_is_rejected(self):
        self.assertTrue(set_site_theme("dunkel"))
        self.assertFalse(set_site_theme("nicht-vorhanden"))
        # Der zuvor gueltig gesetzte Wert bleibt unveraendert.
        self.assertEqual(get_site_theme(), "dunkel")

    def test_unknown_stored_value_falls_back_to_default(self):
        # Simuliert einen veralteten/manuell manipulierten Datenbankwert.
        set_setting("site_theme", "irgendein-altes-theme")
        self.assertEqual(get_site_theme(), DEFAULT_SITE_THEME)

    def test_available_themes_include_standard_and_dunkel(self):
        self.assertIn("standard", SITE_THEMES)
        self.assertIn("dunkel", SITE_THEMES)

    def test_conn_roundtrip_via_settings_table(self):
        set_site_theme("dunkel")
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = 'site_theme'"
            ).fetchone()
        self.assertEqual(row["value"], "dunkel")


if __name__ == "__main__":
    unittest.main()
