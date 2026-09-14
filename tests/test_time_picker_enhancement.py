import re
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

# Templates, die laut Issue #113 native <input type="time">-Felder enthalten.
# Neue Zeitfelder muessen NICHT hier ergaenzt werden, damit sie die Auswahl
# bekommen (die Enhancement-Logik in base.html greift appweit generisch ueber
# querySelectorAll('input[type="time"]')) - diese Liste ist nur eine
# Regressionspruefung, dass die bekannten Felder nicht verschwinden.
TEMPLATES_WITH_TIME_FIELDS = {
    "assistent_start.html": 1,
    "spielplan_bearbeiten.html": 6,
    "turnier_schnellstart.html": 1,
    "wettbewerbe.html": 4,
}


class TimePickerEnhancementStructureTests(unittest.TestCase):
    """Issue #113/#117/#120: einheitliche, appweite Uhrzeit-Auswahl fuer alle
    input[type="time"]-Felder statt browserabhaengiger nativer Steuerelemente
    (bzw. der fehlerhaften Scroll-Wheel-Variante aus PR #116). Es gibt keine
    Browser-Testinfrastruktur (siehe CLAUDE.md / Issues #109, #111, #119) -
    diese Tests pruefen deshalb auf Quelltext-/Struktur-Ebene, dass die
    Enhancement-Logik zentral in base.html liegt, appweit generisch alle
    Zeitfelder erfasst, als Android/Material-typisches 24h-Zifferblatt mit
    Doppelring (aeusserer Ring 1-12, innerer Ring 13-23/0, siehe Issue #120)
    ohne scrollbare Container umgesetzt ist, die bestehende HH:MM-
    Formularuebermittlung nicht veraendert und keine externe Bibliothek
    einbindet."""

    @classmethod
    def setUpClass(cls):
        cls.base_html_text = (
            ROOT_DIR / "app" / "templates" / "base.html"
        ).read_text(encoding="utf-8")
        cls.style_css_text = (
            ROOT_DIR / "app" / "static" / "style.css"
        ).read_text(encoding="utf-8")
        cls.time_picker_css_path = (
            ROOT_DIR / "app" / "static" / "css" / "time-picker.css"
        )
        cls.time_picker_css_text = cls.time_picker_css_path.read_text(encoding="utf-8")

    def test_known_time_fields_still_present_in_templates(self):
        templates_dir = ROOT_DIR / "app" / "templates"
        for template_name, expected_count in TEMPLATES_WITH_TIME_FIELDS.items():
            text = (templates_dir / template_name).read_text(encoding="utf-8")
            actual_count = len(re.findall(r'type="time"', text))
            self.assertEqual(
                actual_count,
                expected_count,
                f"{template_name}: erwartete {expected_count} type=\"time\"-Felder, "
                f"gefunden {actual_count} - falls sich das absichtlich geaendert hat, "
                "TEMPLATES_WITH_TIME_FIELDS aktualisieren",
            )

    def test_enhancement_script_uses_generic_selector_in_base_html(self):
        self.assertIn(
            'querySelectorAll(\'input[type="time"]\')',
            self.base_html_text,
            "base.html muss alle Zeitfelder appweit generisch ueber einen "
            "querySelectorAll('input[type=\"time\"]')-Selektor erfassen, "
            "nicht pro Template einzeln",
        )

    def test_enhancement_is_idempotent(self):
        # Ein Guard-Flag verhindert doppeltes Enhancement, falls das Skript
        # z.B. durch dynamisch nachgeladene Modal-Inhalte mehrfach greifen
        # koennte.
        self.assertIn("dataset.timeEnhanced", self.base_html_text)

    def test_enhancement_does_not_replace_native_input_type(self):
        # Die Formularverarbeitung (Name/Value als HH:MM) darf sich nicht
        # aendern - das native <input type="time"> muss erhalten bleiben,
        # es darf insbesondere nicht auf type="text" umgestellt werden.
        self.assertNotIn('input.type = "text"', self.base_html_text)
        self.assertNotIn("input.type = 'text'", self.base_html_text)

    def test_enhancement_sets_value_in_hh_mm_format(self):
        self.assertIn("const setValue = (hh, mm) =>", self.base_html_text)
        self.assertIn("input.value = `${hh}:${mm}`;", self.base_html_text)

    def test_enhancement_dispatches_input_and_change_events(self):
        # Ohne dispatchEvent bleibt der native Formular-Wert zwar korrekt,
        # aber andere auf "input"/"change" lauschende Skripte wuerden die
        # per Picker gesetzte Uhrzeit nicht mitbekommen.
        self.assertIn('new Event("input"', self.base_html_text)
        self.assertIn('new Event("change"', self.base_html_text)

    def test_no_new_external_dependency_or_cdn_introduced(self):
        for forbidden in ("cdn.", "unpkg.com", "jsdelivr", "googleapis.com/ajax"):
            self.assertNotIn(
                forbidden,
                self.base_html_text.lower(),
                f"base.html darf keine externe Bibliothek/CDN ({forbidden}) einbinden",
            )

    def test_time_picker_css_file_is_imported(self):
        self.assertIn('@import url("css/time-picker.css");', self.style_css_text)
        self.assertTrue(
            self.time_picker_css_path.is_file(),
            "app/static/css/time-picker.css sollte existieren",
        )

    def test_time_picker_css_uses_theme_variables(self):
        # Damit die Auswahl im "Dunkel"-Theme (siehe theme.css
        # [data-theme="dunkel"]) automatisch mitzieht, duerfen Farben nur
        # ueber CSS-Variablen gesetzt werden, nicht hart codiert.
        for expected_var in ("var(--surface", "var(--border", "var(--text)", "var(--muted)"):
            self.assertIn(expected_var, self.time_picker_css_text)

    def test_time_picker_css_defines_key_classes(self):
        for selector in (
            ".time-field-wrap",
            ".time-picker-toggle",
            ".time-picker-panel",
            ".time-picker-display",
            ".time-clock",
            ".time-clock-face",
            ".time-clock-numbers",
            ".time-clock-number",
            ".time-clock-hand",
            ".time-clock-center",
            ".is-selected",
        ):
            self.assertIn(selector, self.time_picker_css_text)

    def test_time_picker_css_defines_double_ring_styling(self):
        # Issue #120: Android/Material-Doppelring statt Einzelring - der
        # innere Ring (Stunden 13-23/0) muss sich optisch (Groesse/Farbe)
        # vom aeusseren Ring absetzen, und der Zeiger braucht eine kuerzere
        # Variante fuer Ziffern auf dem inneren Ring.
        self.assertIn(".time-clock-number.is-inner", self.time_picker_css_text)
        self.assertIn(".time-clock-hand.is-inner", self.time_picker_css_text)

    def test_time_picker_css_avoids_native_scrollable_lists(self):
        # Issue #117: die vorherige Scroll-Wheel-Umsetzung (zwei Listen mit
        # overflow-y: auto) rendert auf manchen Geraeten/Browsern fehlerhaft
        # (unsichtbare Zahlen, nur native Scrollbar-Pfeile sichtbar). Das
        # Zifferblatt darf sich deshalb nicht auf overflow-y: auto verlassen.
        self.assertNotIn("overflow-y: auto", self.time_picker_css_text)
        self.assertNotIn(".time-picker-col", self.time_picker_css_text)
        self.assertNotIn(".time-picker-option", self.time_picker_css_text)

    def test_enhancement_uses_pointer_events_not_scroll_container(self):
        # Die Auswahl laeuft ueber Drag/Tap direkt auf dem Zifferblatt-Kreis
        # (Pointer Events), nicht ueber scrollbare Listen.
        for expected in ("pointerdown", "pointermove", "pointerup"):
            self.assertIn(expected, self.base_html_text)
        self.assertNotIn("time-picker-col", self.base_html_text)
        self.assertNotIn("time-picker-option", self.base_html_text)

    def test_enhancement_builds_24_hour_double_ring_and_5_minute_dial_numbers(self):
        # Issue #120: Android/Material-Doppelring statt Einzelring - alle
        # 24 Stunden (0-23) werden auf zwei konzentrischen Ringen verteilt
        # (aeusserer Ring 1-12, innerer Ring 13-23/0), Minuten bleiben ein
        # einzelner Ring mit Fuenf-Minuten-Ziffern (0/5/.../55), mit
        # stufenlosem Ziehen dazwischen (siehe Issue #117).
        self.assertIn("for (let hour = 0; hour < 24; hour += 1)", self.base_html_text)
        self.assertIn("for (let value = 0; value < 60; value += 5)", self.base_html_text)
        self.assertIn("OUTER_NUMBER_RADIUS_PERCENT", self.base_html_text)
        self.assertIn("INNER_NUMBER_RADIUS_PERCENT", self.base_html_text)

    def test_enhancement_assigns_hours_to_inner_and_outer_ring(self):
        # Aeusserer Ring traegt die Stunden 1-12, innerer Ring 13-23 sowie 0
        # (0/12 teilen sich dieselbe Winkelposition oben, wie im Android/
        # Material-Vorbild aus Issue #120).
        self.assertIn("const isOuterHour = (hour) => hour >= 1 && hour <= 12;", self.base_html_text)
        self.assertIn('"is-outer"', self.base_html_text)
        self.assertIn('"is-inner"', self.base_html_text)

    def test_enhancement_uses_pointer_distance_to_disambiguate_ring(self):
        # Da sich beide Ringe dieselben 12 Winkelpositionen teilen, muss
        # zusaetzlich der Abstand zum Mittelpunkt ausgewertet werden, um
        # beim Antippen/Ziehen zwischen aeusserer und innerer Stunde zu
        # unterscheiden.
        self.assertIn("distancePercent", self.base_html_text)
        self.assertIn("RING_THRESHOLD_PERCENT", self.base_html_text)

    def test_enhancement_switches_from_hours_to_minutes_after_selection(self):
        self.assertIn('switchMode("minutes")', self.base_html_text)


class TimePickerFormSubmissionCompatibilityTests(unittest.TestCase):
    """Serverseitig darf sich am gerenderten Markup nichts aendern, das die
    Formularverarbeitung betrifft (name/value der type="time"-Felder) - die
    Auswahl wird ausschliesslich client-seitig per JS ergaenzt."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "time-picker-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_time_inputs_keep_native_type_and_name_on_rendered_pages(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            for path in ("/wettbewerbe", "/spielplan-bearbeiten"):
                response = client.get(path)
                self.assertEqual(response.status_code, 200, f"{path} -> {response.status_code}")
                self.assertIn('type="time"', response.text, path)
                self.assertIn('name="start_time"' if path == "/wettbewerbe" else 'name="startzeit"', response.text)

    def test_time_picker_stylesheet_is_served(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get("/static/css/time-picker.css")
            self.assertEqual(response.status_code, 200)
            self.assertIn(".time-picker-panel", response.text)


if __name__ == "__main__":
    unittest.main()
