import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


class AdminFormMobileLayoutTests(unittest.TestCase):
    """Issue #111: .admin-form kollabierte auf Mobile nicht auf eine Spalte.

    Ursache war eine Reihenfolge-/Spezifitaetsfalle innerhalb von theme.css:
    ein unconditional ".admin-form { grid-template-columns: 160px 1fr; }"
    stand im Quelltext spaeter als der beabsichtigte Mobile-Reset
    "@media (max-width: 700px) { .admin-form, .points-form { grid-template-
    columns: 1fr; } }". Bei gleicher Spezifitaet (je eine Klasse) gewinnt die
    spaetere Regel unabhaengig davon, ob sie in einer Media Query steht -
    der Mobile-Reset griff dadurch auf KEINER Bildschirmbreite. Fix: die
    160px-1fr-Regel in @media (min-width: 701px) huellen, damit sie den
    Mobile-Reset nicht mehr aushebeln kann - unabhaengig von der Position
    im Quelltext."""

    def setUp(self):
        css_path = ROOT_DIR / "app" / "static" / "css" / "theme.css"
        self.css_text = css_path.read_text(encoding="utf-8")

    def test_mobile_reset_to_one_column_still_exists(self):
        self.assertIn(
            "@media (max-width: 700px) {",
            self.css_text,
            "der Mobile-Reset-Media-Query darf nicht entfernt werden",
        )
        mobile_media_start = self.css_text.index("@media (max-width: 700px) {")
        mobile_media_body_start = self.css_text.index("{", mobile_media_start)
        mobile_media_body_end = self.css_text.index("\n}\n", mobile_media_body_start)
        mobile_media_body = self.css_text[mobile_media_body_start:mobile_media_body_end]

        self.assertIn(".admin-form,", mobile_media_body)
        self.assertIn("grid-template-columns: 1fr;", mobile_media_body)

    def test_desktop_label_column_rule_is_gated_behind_min_width_media_query(self):
        marker = "grid-template-columns: 160px 1fr;"
        self.assertIn(
            marker,
            self.css_text,
            "die 160px-Label-Spalten-Regel fuer .admin-form sollte weiterhin existieren",
        )
        marker_pos = self.css_text.index(marker)

        enclosing_media_start = self.css_text.rindex("@media", 0, marker_pos)
        enclosing_media_condition_end = self.css_text.index("{", enclosing_media_start)
        enclosing_media_condition = self.css_text[
            enclosing_media_start:enclosing_media_condition_end
        ]

        # Zwischen dem @media-Start und der Regel darf keine schliessende
        # geschweifte Klammer auf oberster Ebene liegen - sonst waere die
        # Regel bereits wieder ausserhalb des gefundenen Media-Blocks.
        body_between = self.css_text[enclosing_media_condition_end + 1 : marker_pos]
        self.assertNotIn(
            "}",
            body_between,
            "die 160px-1fr-Regel muss innerhalb des zuletzt geoeffneten @media-Blocks liegen",
        )

        self.assertIn(
            "min-width",
            enclosing_media_condition,
            (
                "die unconditional .admin-form-Regel (160px 1fr) muss hinter einer "
                "min-width-Media-Query stehen, damit sie den Mobile-Reset auf 1fr "
                "nicht mehr unabhaengig von der Quelltext-Reihenfolge aushebelt"
            ),
        )


if __name__ == "__main__":
    unittest.main()
