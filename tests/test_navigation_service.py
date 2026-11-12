"""Tests fuer die Hauptnavigation (Seitenleiste und mobiles Menue)."""
import unittest

from app.services.navigation_service import (
    NAV_GRUPPEN,
    NAV_ICONS,
    build_navigation,
    nav_icon_svg,
)


def _labels(gruppen):
    return [(g["titel"], [e["label"] for e in g["eintraege"]]) for g in gruppen]


class NavigationAufbauTests(unittest.TestCase):
    def test_admin_sieht_alle_gruppen_in_fester_reihenfolge(self):
        gruppen = build_navigation("/", lambda rolle: True)
        self.assertEqual(
            _labels(gruppen),
            [
                (None, ["Dashboard"]),
                ("Wettkampftag", ["Spielplan", "Ergebnisse eintragen"]),
                ("Auswertung", ["Tabellen", "Urkunden"]),
                (
                    "Vorbereitung",
                    [
                        "Einrichtungsassistent",
                        "Veranstaltungen",
                        "Teams",
                        "Spielfelder",
                        "Wettbewerbe",
                        "Spielplan bearbeiten",
                    ],
                ),
                ("System", ["Einstellungen", "Dokumentation", "Roadmap"]),
            ],
        )

    def test_viewer_sieht_nur_oeffentliche_eintraege_ohne_leere_gruppen(self):
        gruppen = build_navigation("/", lambda rolle: False)
        self.assertEqual(
            _labels(gruppen),
            [
                (None, ["Dashboard"]),
                ("Wettkampftag", ["Spielplan"]),
                ("Auswertung", ["Tabellen"]),
            ],
        )

    def test_schiedsrichter_sieht_ergebnisse_aber_keine_verwaltung(self):
        gruppen = build_navigation("/", lambda rolle: rolle == "referee")
        self.assertEqual(
            _labels(gruppen),
            [
                (None, ["Dashboard"]),
                ("Wettkampftag", ["Spielplan", "Ergebnisse eintragen"]),
                ("Auswertung", ["Tabellen"]),
            ],
        )

    def test_turnierleitung_sieht_zusaetzlich_urkunden(self):
        gruppen = build_navigation(
            "/", lambda rolle: rolle in {"referee", "tournament_lead"}
        )
        self.assertEqual(_labels(gruppen)[2], ("Auswertung", ["Tabellen", "Urkunden"]))
        self.assertEqual(len(gruppen), 3)

    def test_oeffentliche_eintraege_stehen_fuer_jede_rolle_an_derselben_stelle(self):
        def reihenfolge(darf):
            return [
                e["label"]
                for g in build_navigation("/", darf)
                for e in g["eintraege"]
            ]

        viewer = reihenfolge(lambda rolle: False)
        admin = reihenfolge(lambda rolle: True)
        self.assertEqual([label for label in admin if label in viewer], viewer)
        self.assertEqual(admin[:2], viewer[:2])

    def _aktive(self, pfad):
        return [
            e["label"]
            for g in build_navigation(pfad, lambda rolle: True)
            for e in g["eintraege"]
            if e["aktiv"]
        ]

    def test_genau_ein_eintrag_ist_aktiv(self):
        self.assertEqual(self._aktive("/"), ["Dashboard"])
        self.assertEqual(self._aktive("/spielplan"), ["Spielplan"])
        self.assertEqual(self._aktive("/spielplan-bearbeiten"), ["Spielplan bearbeiten"])

    def test_unterseiten_markieren_veranstaltungen_und_assistent(self):
        self.assertEqual(self._aktive("/events/3"), ["Veranstaltungen"])
        self.assertEqual(self._aktive("/assistent/entwurf/5"), ["Einrichtungsassistent"])

    def test_unbekannter_pfad_markiert_nichts(self):
        self.assertEqual(self._aktive("/impressum"), [])


class NavigationSymbolTests(unittest.TestCase):
    def test_jeder_eintrag_hat_ein_eigenes_vorhandenes_symbol(self):
        icons = [icon for _, eintraege in NAV_GRUPPEN for _, _, icon, _, _ in eintraege]
        for icon in icons:
            self.assertIn(icon, NAV_ICONS)
        self.assertEqual(len(icons), len(set(icons)), "Symbole muessen eindeutig sein")

    def test_svg_ist_dekorativ_und_erbt_die_schriftfarbe(self):
        svg = nav_icon_svg("trophy")
        self.assertTrue(svg.startswith('<svg class="nav-icon"'))
        self.assertIn('aria-hidden="true"', svg)
        self.assertIn('stroke="currentColor"', svg)
        self.assertIn('class="beamer-icon"', nav_icon_svg("projector", "beamer-icon"))


class NavigationRechteTests(unittest.TestCase):
    def test_rolle_je_eintrag_passt_zum_zentralen_bereichsschutz(self):
        from app.main import get_required_role

        for _, eintraege in NAV_GRUPPEN:
            for label, href, _, rolle, _ in eintraege:
                self.assertEqual(
                    rolle, get_required_role(href),
                    f"{label}: Sichtbarkeit weicht vom Bereichsschutz ab",
                )


if __name__ == "__main__":
    unittest.main()
