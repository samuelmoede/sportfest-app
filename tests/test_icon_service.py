import re
import unittest
from pathlib import Path

from app.services.icon_service import ICONS, icon_svg
from app.services.navigation_service import NAV_ICONS

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "app" / "templates"

# Bunte Bildzeichen, die je nach Geraet anders aussehen: Emoji-Bloecke,
# Sonstige Symbole/Dingbats (z.B. ⚠ ✔ ✖ ⚡ ☰), technische Zeichen (⏯ ⌂) und
# geometrische Formen (◀ ▶ ▾ ○ ▦). Typografische Pfeile wie ← → bleiben erlaubt.
EMOJI_MUSTER = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿⌀-⏿■-◿ℹ️]"
)


class IconServiceTests(unittest.TestCase):
    def test_svg_ist_dekorativ_und_erbt_farbe(self):
        svg = icon_svg("map-pin")
        self.assertTrue(svg.startswith('<svg class="icon"'))
        self.assertIn('stroke="currentColor"', svg)
        self.assertIn('aria-hidden="true"', svg)
        self.assertIn(ICONS["map-pin"], svg)

    def test_eigene_klasse(self):
        self.assertIn('class="logo-icon"', icon_svg("zap", "logo-icon"))

    def test_navigation_nutzt_denselben_satz(self):
        self.assertIs(NAV_ICONS, ICONS)

    def test_unbekanntes_symbol_faellt_auf(self):
        with self.assertRaises(KeyError):
            icon_svg("gibt-es-nicht")

    def test_vorlagen_verwenden_nur_bekannte_symbole(self):
        for pfad in TEMPLATE_DIR.rglob("*.html"):
            for name in re.findall(r'icon\("([^"]+)"', pfad.read_text(encoding="utf-8")):
                self.assertIn(name, ICONS, f"{pfad.name}: {name}")

    def test_vorlagen_ohne_emojis(self):
        for pfad in TEMPLATE_DIR.rglob("*.html"):
            for nr, zeile in enumerate(pfad.read_text(encoding="utf-8").splitlines(), 1):
                treffer = EMOJI_MUSTER.findall(zeile)
                self.assertFalse(treffer, f"{pfad.name}:{nr}: {treffer} -> icon(...) verwenden")


if __name__ == "__main__":
    unittest.main()
