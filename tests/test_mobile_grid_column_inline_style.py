import re
import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


class MobileGridColumnInlineStyleTests(unittest.TestCase):
    """Regression fuer Issue #109: ein Inline-Style `style="grid-column: 2;"`
    kann von keiner externen Stylesheet-Regel ueberschrieben werden - auch
    nicht vom Mobile-Reset auf `grid-column: auto` in responsive.css. Dadurch
    behielt das jeweilige Grid auf Mobile zwei Spalten statt einer, die
    Label-Spalte wurde auf wenige Pixel gequetscht und Labels brachen
    buchstabenweise um. Betroffene Elemente muessen ihre Grid-Platzierung
    stattdessen ueber CSS-Klassen bekommen, die per Media Query zuruecksetzbar
    sind (siehe app/static/css/responsive.css, @media (max-width: 860px))."""

    def setUp(self):
        self.wettbewerbe_html = (
            ROOT_DIR / "app" / "templates" / "wettbewerbe.html"
        ).read_text(encoding="utf-8")
        self.spielplan_html = (
            ROOT_DIR / "app" / "templates" / "spielplan_bearbeiten.html"
        ).read_text(encoding="utf-8")
        self.competitions_css = (
            ROOT_DIR / "app" / "static" / "css" / "competitions.css"
        ).read_text(encoding="utf-8")
        self.responsive_css = (
            ROOT_DIR / "app" / "static" / "css" / "responsive.css"
        ).read_text(encoding="utf-8")

    def test_no_inline_grid_column_style_in_wettbewerbe(self):
        self.assertNotIn('style="grid-column: 2', self.wettbewerbe_html)

    def test_no_inline_grid_column_style_in_spielplan_bearbeiten(self):
        # Der dynamische `grid-column: {{ loop.index + 1 }}` im Zeitraster
        # (variable Spaltenanzahl je nach Feldern/Bereichen) ist absichtlich
        # kein Fall des hier gefixten Bugs (immer fixe Spalte 2) und bleibt
        # unangetastet.
        self.assertNotIn('style="grid-column: 2', self.spielplan_html)

    def test_save_feedback_span_uses_resettable_class(self):
        self.assertIn(
            'class="save-feedback-inline competition-save-feedback"',
            self.wettbewerbe_html,
        )
        self.assertIn(".competition-save-feedback {", self.competitions_css)

    def test_generator_hint_paragraphs_use_resettable_class(self):
        hint_count = self.spielplan_html.count('class="muted generator-hint"')
        self.assertEqual(
            hint_count,
            4,
            "Erwarte 4 Hinweistexte mit der Klasse 'generator-hint' im "
            "Plan-Generator-Formular",
        )
        self.assertIn(".generator-hint {", self.spielplan_html)

    def test_mobile_media_query_resets_grid_column_to_auto(self):
        media_block_match = re.search(
            r"@media \(max-width: 860px\) \{(.*)", self.responsive_css, re.DOTALL
        )
        self.assertIsNotNone(
            media_block_match, "@media (max-width: 860px)-Block nicht gefunden"
        )
        media_block = media_block_match.group(1)

        self.assertIn(
            ".competition-save-feedback",
            media_block,
            "Mobile-Reset fuer .competition-save-feedback fehlt",
        )
        self.assertIn(
            ".schedule-editor-page .generator-hint {\n        grid-column: auto;",
            media_block,
            "Mobile-Reset fuer .generator-hint fehlt",
        )


if __name__ == "__main__":
    unittest.main()
