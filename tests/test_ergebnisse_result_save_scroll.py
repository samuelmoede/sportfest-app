import re
import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


class ErgebnisseResultSaveScrollTests(unittest.TestCase):
    """Regression fuer Issue #48: Beim Speichern eines Ergebnisses auf
    /ergebnisse werden active-results-section und archived-results-section
    per fetch() komplett ersetzt (das Spiel wandert dabei vom aktiven Bereich
    ins Archiv, die Spalten aendern ihre Hoehe). Anders als attachStartHandler
    und attachUnstartHandler (die die Scrollposition schon vor diesem Issue
    sichern und wiederherstellen) tat attachResultSaveHandler das nicht -
    nach dem Ersetzen springt der Browser dadurch manchmal, und fuer den
    Schiedsrichter verschwinden andere, eigentlich unveraenderte Spiele aus
    dem sichtbaren Bereich. Da es keine JS-Testinfrastruktur gibt (siehe
    CLAUDE.md), prueft dieser Test auf Quelltext-Ebene, dass der Save-Handler
    dasselbe Scroll-Restore-Muster wie die Start-/Unstart-Handler verwendet."""

    def setUp(self):
        template_path = ROOT_DIR / "app" / "templates" / "ergebnisse.html"
        self.template_text = template_path.read_text(encoding="utf-8")

    def _extract_function_body(self, function_name):
        match = re.search(
            r"function " + re.escape(function_name) + r"\(form\) \{(.*?)\n\}\n",
            self.template_text,
            re.DOTALL,
        )
        self.assertIsNotNone(
            match, f"Funktion {function_name} nicht in ergebnisse.html gefunden"
        )
        return match.group(1)

    def test_start_and_unstart_handlers_preserve_scroll_position(self):
        # Referenzverhalten, das der Save-Handler ebenfalls braucht.
        for function_name in ("attachStartHandler", "attachUnstartHandler"):
            body = self._extract_function_body(function_name)
            self.assertIn("window.scrollX", body, function_name)
            self.assertIn("window.scrollY", body, function_name)
            self.assertIn("window.scrollTo(scrollX, scrollY)", body, function_name)

    def test_result_save_handler_preserves_scroll_position(self):
        body = self._extract_function_body("attachResultSaveHandler")

        self.assertIn(
            "window.scrollX",
            body,
            "attachResultSaveHandler sichert die Scrollposition nicht vor dem "
            "Ersetzen von active-/archived-results-section",
        )
        self.assertIn(
            "window.scrollY",
            body,
            "attachResultSaveHandler sichert die Scrollposition nicht vor dem "
            "Ersetzen von active-/archived-results-section",
        )

        scroll_capture_index = body.find("window.scrollY")
        replace_index = body.find("current.replaceWith(updated)")
        restore_index = body.find("window.scrollTo(scrollX, scrollY)")

        self.assertNotEqual(restore_index, -1, "kein window.scrollTo(scrollX, scrollY)-Aufruf gefunden")
        self.assertLess(
            scroll_capture_index, replace_index,
            "Scrollposition muss VOR dem Ersetzen der Sections gesichert werden",
        )
        self.assertLess(
            replace_index, restore_index,
            "Scrollposition muss NACH dem Ersetzen der Sections wiederhergestellt werden",
        )


if __name__ == "__main__":
    unittest.main()
