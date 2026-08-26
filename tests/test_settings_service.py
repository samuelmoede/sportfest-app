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
    can_access_role,
    get_site_theme,
    get_tournament_lead_password,
    is_logged_in,
    set_setting,
    set_site_theme,
)


class FakeRequest:
    """Minimaler Ersatz fuer fastapi.Request in Tests: can_access_role/
    get_current_role greifen ausschliesslich auf request.session zu."""

    def __init__(self, role=None):
        self.session = {"role": role} if role else {}


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


class TournamentLeadRoleTests(unittest.TestCase):
    """Turnierleitung: wie Schiedsrichter (Ergebnisse einsehen/erfassen),
    zusaetzlich Zugriff auf explizit dafuer geoeffnete Ansichten (z.B.
    Gesamtwertung/Siegerehrung), aber ohne die admin-only Verwaltung
    (Spielfelder/Events/Wettbewerbe anlegen) - siehe can_access_role()."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "settings-roles-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()
        # can_access_role() gibt bei deaktivierter Sicherheit immer True zurueck;
        # fuer diese Tests soll die Rollenlogik selbst greifen.
        self._security_patcher = patch(
            "app.services.settings_service.is_security_enabled",
            return_value=True,
        )
        self._security_patcher.start()

    def tearDown(self):
        self._security_patcher.stop()
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_tournament_lead_has_referee_level_access(self):
        request = FakeRequest("tournament_lead")
        self.assertTrue(can_access_role(request, "referee"))
        self.assertTrue(can_access_role(request, "viewer"))

    def test_tournament_lead_cannot_access_admin_only(self):
        request = FakeRequest("tournament_lead")
        self.assertFalse(can_access_role(request, "admin"))

    def test_tournament_lead_can_access_tournament_lead_required_view(self):
        request = FakeRequest("tournament_lead")
        self.assertTrue(can_access_role(request, "tournament_lead"))

    def test_admin_can_access_tournament_lead_required_view(self):
        request = FakeRequest("admin")
        self.assertTrue(can_access_role(request, "tournament_lead"))

    def test_referee_cannot_access_tournament_lead_required_view(self):
        request = FakeRequest("referee")
        self.assertFalse(can_access_role(request, "tournament_lead"))

    def test_viewer_cannot_access_tournament_lead_required_view(self):
        request = FakeRequest("viewer")
        self.assertFalse(can_access_role(request, "tournament_lead"))

    def test_station_helper_cannot_access_tournament_lead_required_view(self):
        request = FakeRequest("station_helper")
        self.assertFalse(can_access_role(request, "tournament_lead"))

    def test_tournament_lead_counts_as_logged_in(self):
        self.assertTrue(is_logged_in(FakeRequest("tournament_lead")))

    def test_security_disabled_allows_everything_regardless_of_role(self):
        # Ueberschreibt den in setUp() aktiven is_security_enabled-Patch
        # temporaer; nach dem with-Block gilt wieder return_value=True.
        with patch(
            "app.services.settings_service.is_security_enabled",
            return_value=False,
        ):
            request = FakeRequest("viewer")
            self.assertTrue(can_access_role(request, "admin"))


class TournamentLeadPasswordTests(unittest.TestCase):
    """Eigenes Passwort fuer die Turnierleitung, analog zu
    get_referee_password(): Umgebungsvariable hat Vorrang vor dem
    Settings-Key tournament_lead_password."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "settings-tournament-lead-password-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_default_password_is_empty(self):
        self.assertEqual(get_tournament_lead_password(), "")

    def test_password_from_settings_table(self):
        set_setting("tournament_lead_password", "geheim123")
        self.assertEqual(get_tournament_lead_password(), "geheim123")

    def test_environment_variable_takes_precedence(self):
        set_setting("tournament_lead_password", "geheim123")
        with patch.dict(
            "os.environ",
            {"SPORTFEST_TOURNAMENT_LEAD_PASSWORD": "aus-umgebung"},
        ):
            self.assertEqual(get_tournament_lead_password(), "aus-umgebung")


if __name__ == "__main__":
    unittest.main()
