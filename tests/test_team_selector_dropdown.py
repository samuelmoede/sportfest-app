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

    def test_gruppe_checkbox_has_fixed_small_size_css(self):
        """Issue #67: die Gruppen-Checkbox darf nicht die globale
        input/select/textarea-Groesse (min-height: 43px) erben, sonst bricht
        das Zeilenlayout ("Gruppe 7" umbricht mitten im Text)."""
        css_path = ROOT_DIR / "app" / "static" / "css" / "theme.css"
        css_text = css_path.read_text(encoding="utf-8")

        selector = '.team-selector-gruppe-label input[type="checkbox"]'
        self.assertIn(selector, css_text)

        rule_start = css_text.index(selector)
        rule_body_start = css_text.index("{", rule_start)
        rule_body_end = css_text.index("}", rule_body_start)
        rule_body = css_text[rule_body_start:rule_body_end]

        self.assertIn("width: 16px", rule_body)
        self.assertIn("height: 16px", rule_body)
        self.assertIn("min-height: 0", rule_body)

    def test_jahrgang_field_has_explanatory_hint(self):
        """Das 'Jahrgang'-Auswahlfeld (jahrgang) muss sich klar von der
        darunterliegenden Klassen-Auswahl abgrenzen - beide Formulare
        (Anlegen und Bearbeiten) bekommen einen kurzen Hinweistext."""
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get("/wettbewerbe")

        self.assertEqual(response.status_code, 200)
        html = response.text

        self.assertGreaterEqual(
            html.count('class="muted field-hint"'),
            2,
            "sowohl Anlegen- als auch Bearbeiten-Formular brauchen den Hinweistext",
        )
        self.assertIn("automatisch verwendet", html)

    def test_team_selector_hint_references_jahrgang_field(self):
        """Issue #69: Dropdown-Titel/Hinweis sollen explizit auf das
        Ueberschreiben des 'Jahrgang'-Felds hinweisen, damit der Unterschied
        zwischen beiden Wegen (Jahrgang vs. explizite Klassenauswahl) klar
        bleibt - und die frueher uneinheitliche 'Gruppe'-Formulierung ist
        vollstaendig durch 'Jahrgang' ersetzt (sichtbare Texte, siehe
        Issue #69 Punkt 3)."""
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get("/wettbewerbe")

        self.assertEqual(response.status_code, 200)
        html = response.text

        self.assertGreaterEqual(html.count("überschreibt"), 2)
        self.assertGreaterEqual(html.count("eingetragenen Jahrgang"), 2)
        self.assertNotIn("Gruppe-Feld", html)
        self.assertNotIn(">Gruppe<", html)

    def test_jahrgang_field_and_team_selector_share_one_control(self):
        """Issue #69 Punkt 2: das freie 'Jahrgang'-Textfeld und die
        Klassen-Auswahl sind kein eigenstaendiges, gleichrangiges Feldpaar
        mehr, sondern zusammen EIN Bedienelement (gemeinsamer Wrapper unter
        einem einzigen 'Jahrgang'-Label) statt zwei separater Grid-Zeilen."""
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get("/wettbewerbe")

        self.assertEqual(response.status_code, 200)
        html = response.text

        self.assertGreaterEqual(html.count(">Jahrgang<"), 2)

        wrapper_start = html.index('<div class="jahrgang-selector-field">')
        jahrgang_input_pos = html.index('name="jahrgang"', wrapper_start)
        details_pos = html.index('<details class="team-selector-details">', wrapper_start)

        self.assertLess(
            wrapper_start,
            jahrgang_input_pos,
            "das Jahrgang-Textfeld muss innerhalb des gemeinsamen Wrappers liegen",
        )
        self.assertLess(
            jahrgang_input_pos,
            details_pos,
            "die Klassen-Auswahl muss im selben Bedienelement wie das Jahrgang-Feld stecken",
        )
        self.assertLess(
            details_pos - jahrgang_input_pos,
            2000,
            "Jahrgang-Feld und Klassen-Auswahl sollten eng im selben Wrapper beieinanderliegen",
        )

    def test_team_checkbox_chip_renders_for_concrete_team(self):
        """Issue #69 Punkt 1: im aufgeklappten Dropdown muss fuer ein
        konkretes Team (z.B. Klasse '7a') tatsaechlich ein Checkbox-Chip
        innerhalb von .team-selector-items im gerenderten HTML vorkommen -
        nicht nur die Jahrgangs-Zeile mit Anzahl."""
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get("/wettbewerbe")

        self.assertEqual(response.status_code, 200)
        html = response.text

        items_start = html.index('<div class="team-selector-items">')
        items_end = html.index("</div>", items_start)
        items_snippet = html[items_start:items_end]

        self.assertIn('class="team-selector-item"', items_snippet)
        self.assertIn('type="checkbox" name="team_ids"', items_snippet)
        self.assertIn("7a", items_snippet)

    def test_team_selector_item_checkbox_not_hidden_via_clip(self):
        """Regression-Schutz: die Checkbox je Klassen-Chip darf nicht mehr
        per position:absolute + clip visuell versteckt werden (fragiles
        Muster, moegliche Ursache fuer Issue #69 Punkt 1) - stattdessen eine
        kleine, aber sichtbare Checkbox wie beim bereits erprobten
        Jahrgangs-Checkbox-Fix aus Issue #67."""
        css_path = ROOT_DIR / "app" / "static" / "css" / "theme.css"
        css_text = css_path.read_text(encoding="utf-8")

        selector = '.team-selector-item input[type="checkbox"]'
        self.assertIn(selector, css_text)

        rule_start = css_text.index(selector)
        rule_body_start = css_text.index("{", rule_start)
        rule_body_end = css_text.index("}", rule_body_start)
        rule_body = css_text[rule_body_start:rule_body_end]

        self.assertNotIn("position: absolute", rule_body)
        self.assertNotIn("clip:", rule_body)

    def test_jahrgang_field_is_select_without_free_text_fallback(self):
        """Issue #71: das Jahrgang-Bedienelement darf kein Freitextfeld mehr
        sein (mit Datalist-Vorschlaegen), sondern nur noch eine Auswahl aus
        den tatsaechlich vorhandenen Jahrgaengen/Gruppen - Tippfehler duerfen
        keinen nicht existierenden Jahrgang mehr setzen koennen."""
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get("/wettbewerbe")

        self.assertEqual(response.status_code, 200)
        html = response.text

        self.assertNotIn('<input type="text" name="jahrgang"', html)
        self.assertNotIn("gruppe-suggestions-create", html)
        self.assertNotIn("gruppe-suggestions-edit", html)
        self.assertNotIn("<datalist", html)

        create_select_start = html.index('<select name="jahrgang" id="create-jahrgang-input">')
        create_select_end = html.index("</select>", create_select_start)
        create_select_html = html[create_select_start:create_select_end]
        self.assertIn('<option value="7">7</option>', create_select_html)
        self.assertIn('<option value="8">8</option>', create_select_html)

    def test_edit_form_jahrgang_select_preselects_current_value(self):
        """Das Bearbeiten-Formular muss den bereits gespeicherten Jahrgang
        des Wettbewerbs (hier 7) als vorausgewaehlte Option im Dropdown
        zeigen, statt stillschweigend auf eine andere Option umzuspringen."""
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get("/wettbewerbe")

        self.assertEqual(response.status_code, 200)
        html = response.text

        edit_select_start = html.index('<select name="jahrgang">')
        edit_select_end = html.index("</select>", edit_select_start)
        edit_select_html = html[edit_select_start:edit_select_end]
        self.assertIn('<option value="7" selected>7</option>', edit_select_html)

    def test_edit_form_preserves_jahrgang_value_without_matching_teams(self):
        """Randfall: ein Wettbewerb kann einen Jahrgang tragen, fuer den
        aktuell keine aktiven Teams (mehr) existieren (z.B. nachtraeglich
        deaktiviert). Die Auswahl muss diesen Wert trotzdem als eigene,
        vorausgewaehlte Option anbieten - sonst wuerde ein Speichern ohne
        bewusste Aenderung den Jahrgang stillschweigend auf die erste
        Options-Liste verspringen lassen."""
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with get_conn() as conn:
            conn.execute(
                "UPDATE competitions SET jahrgang = ? WHERE id = ?",
                ("9", self.competition_id),
            )
            conn.commit()

        with TestClient(fastapi_app) as client:
            response = client.get("/wettbewerbe")

        self.assertEqual(response.status_code, 200)
        html = response.text

        edit_select_start = html.index('<select name="jahrgang">')
        edit_select_end = html.index("</select>", edit_select_start)
        edit_select_html = html[edit_select_start:edit_select_end]
        self.assertIn('<option value="9" selected>9</option>', edit_select_html)


if __name__ == "__main__":
    unittest.main()
