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


class TeamSelectorDropdownTests(unittest.TestCase):
    """Die Team-Auswahl in /wettbewerbe (Anlegen- und Bearbeiten-Formular)
    wird als eingeklapptes <details>-Dropdown dargestellt statt als
    permanent sichtbare Checkbox-Liste (Issue #65). Die zugrunde liegende
    Auswahl-Logik (Feldname team_ids, Speicherung in competition_teams,
    Gruppen-Checkbox schaltet alle Klassen eines Jahrgangs) bleibt
    unveraendert - hier wird nur die Dropdown-Verpackung geprueft."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "team-selector-dropdown-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

        with get_conn() as conn:
            for name, jahrgang in (("7a", 7), ("7b", 7), ("8a", 8)):
                conn.execute(
                    "INSERT INTO teams (name, jahrgang) VALUES (?, ?)", (name, jahrgang)
                )
            conn.execute(
                """
                INSERT INTO competitions (name, sportart, jahrgang, status, competition_type)
                VALUES ('Zweifelderball', 'Zweifelderball', 7, 'geplant', 'Turnier')
                """
            )
            self.competition_id = conn.execute(
                "SELECT id FROM competitions WHERE name = 'Zweifelderball'"
            ).fetchone()["id"]

            team_7a_id = conn.execute(
                "SELECT id FROM teams WHERE name = '7a'"
            ).fetchone()["id"]
            conn.execute(
                "INSERT INTO competition_teams (competition_id, team_id) VALUES (?, ?)",
                (self.competition_id, team_7a_id),
            )
            conn.commit()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_create_form_wraps_team_selector_in_collapsed_details(self):
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get("/wettbewerbe")

        self.assertEqual(response.status_code, 200)
        html = response.text

        details_start = html.index('<details class="team-selector-details">')
        panel_start = html.index('<div class="team-selector-panel">', details_start)
        self.assertLess(
            details_start,
            panel_start,
            "das Team-Auswahl-Panel muss innerhalb des <details>-Elements liegen",
        )

        details_snippet = html[details_start:panel_start]
        self.assertNotIn(
            "open",
            details_snippet.split(">", 1)[0],
            "das Dropdown muss standardmaessig eingeklappt sein (kein open-Attribut)",
        )
        self.assertIn("gruppe-all-create", html)
        self.assertIn('name="team_ids"', html)

    def test_edit_form_wraps_team_selector_in_collapsed_details_and_keeps_selection(self):
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get("/wettbewerbe")

        self.assertEqual(response.status_code, 200)
        html = response.text

        panel_start = html.index("team-selector-panel team-selector-compact")
        details_start = html.rindex(
            '<details class="team-selector-details">', 0, panel_start
        )
        self.assertLess(details_start, panel_start)

        details_snippet = html[details_start:panel_start]
        self.assertNotIn("open", details_snippet.split(">", 1)[0])

        # Bereits explizit ausgewaehltes Team (7a) bleibt weiterhin angehakt,
        # auch wenn das Panel standardmaessig eingeklappt ist.
        checked_snippet = html[panel_start : panel_start + 3000]
        self.assertIn('value="', checked_snippet)
        self.assertIn("checked", checked_snippet)

    def test_team_selector_toggle_css_defined(self):
        css_path = ROOT_DIR / "app" / "static" / "css" / "theme.css"
        css_text = css_path.read_text(encoding="utf-8")
        self.assertIn(".team-selector-details", css_text)
        self.assertIn(".team-selector-toggle", css_text)


if __name__ == "__main__":
    unittest.main()
