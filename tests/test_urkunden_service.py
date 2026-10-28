import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import app.database as database
from app.services.sixkampf_service import calculate_sixkampf_team_ranking
from app.services.urkunden_service import (
    URKUNDE_EHREN,
    URKUNDE_SIEGER,
    URKUNDE_TEILNEHMER,
    bestimme_urkundentyp,
    eintraege_aus_disziplinen,
    eintraege_aus_gesamtwertung,
    eintraege_aus_sechskampf,
    eintraege_aus_turnier,
    filtere_eintraege,
    formatiere_datum,
    parse_schwellen,
    schwelle_fuer_gruppe,
    sortiere_eintraege,
    wende_anpassungen_an,
)


STANDARD = {"sieger_bis_platz": 3, "ehren_bis_platz": 0, "ehren_ab_punkte": None}


def _schwellen(**standard):
    schwellen = parse_schwellen("")
    schwellen["standard"].update(standard)
    return schwellen


class UrkundentypTests(unittest.TestCase):
    def test_platz_1_bis_3_bekommt_siegerurkunde(self):
        for platz in (1, 2, 3):
            self.assertEqual(bestimme_urkundentyp(platz, 0, STANDARD), URKUNDE_SIEGER)

    def test_ohne_ehren_schwelle_ab_platz_4_teilnehmerurkunde(self):
        self.assertEqual(bestimme_urkundentyp(4, 100, STANDARD), URKUNDE_TEILNEHMER)
        self.assertEqual(bestimme_urkundentyp(None, None, STANDARD), URKUNDE_TEILNEHMER)

    def test_ehrenurkunde_nach_platz(self):
        schwelle = dict(STANDARD, ehren_bis_platz=6)
        self.assertEqual(bestimme_urkundentyp(3, 0, schwelle), URKUNDE_SIEGER)
        self.assertEqual(bestimme_urkundentyp(4, 0, schwelle), URKUNDE_EHREN)
        self.assertEqual(bestimme_urkundentyp(6, 0, schwelle), URKUNDE_EHREN)
        self.assertEqual(bestimme_urkundentyp(7, 0, schwelle), URKUNDE_TEILNEHMER)

    def test_ehrenurkunde_ab_punktzahl(self):
        schwelle = dict(STANDARD, ehren_ab_punkte=30)
        self.assertEqual(bestimme_urkundentyp(8, 30, schwelle), URKUNDE_EHREN)
        self.assertEqual(bestimme_urkundentyp(8, 29.5, schwelle), URKUNDE_TEILNEHMER)
        # Sieger bleibt Sieger, auch mit wenig Punkten.
        self.assertEqual(bestimme_urkundentyp(1, 0, schwelle), URKUNDE_SIEGER)

    def test_konfigurierbare_siegerschwelle(self):
        schwelle = dict(STANDARD, sieger_bis_platz=1)
        self.assertEqual(bestimme_urkundentyp(1, 0, schwelle), URKUNDE_SIEGER)
        self.assertEqual(bestimme_urkundentyp(2, 0, schwelle), URKUNDE_TEILNEHMER)

    def test_jahrgangsgruppe_ueberschreibt_nur_gesetzte_felder(self):
        schwellen = parse_schwellen(
            '{"standard": {"sieger_bis_platz": 3, "ehren_bis_platz": 5},'
            ' "Oberstufe": {"sieger_bis_platz": "1", "ehren_ab_punkte": "40,5"}}'
        )
        oberstufe = schwelle_fuer_gruppe(schwellen, "Oberstufe")
        self.assertEqual(oberstufe["sieger_bis_platz"], 1)
        self.assertEqual(oberstufe["ehren_bis_platz"], 5)
        self.assertEqual(oberstufe["ehren_ab_punkte"], 40.5)
        self.assertEqual(schwelle_fuer_gruppe(schwellen, "Jahrgang 7")["sieger_bis_platz"], 3)
        self.assertEqual(schwelle_fuer_gruppe(schwellen, None)["sieger_bis_platz"], 3)

    def test_kaputte_schwellen_fallen_auf_standard_zurueck(self):
        schwellen = parse_schwellen("kein json")
        self.assertEqual(schwelle_fuer_gruppe(schwellen, "Jahrgang 8"), STANDARD)


def _team(team_id, name, jahrgang=7):
    return {"id": team_id, "name": name, "jahrgang": jahrgang}


class SechskampfPlaetzeTests(unittest.TestCase):
    """Plaetze kommen 1:1 aus der Sechskampf-Wertung; identische Werte
    teilen sich den Platz (z.B. zweimal Platz 2)."""

    def setUp(self):
        self.competition = {"id": 10, "name": "Sechskampf Jahrgang 7", "jahrgang": 7}
        self.teams = [_team(1, "7a"), _team(2, "7b"), _team(3, "7c"), _team(4, "7d")]
        self.disciplines = [
            {"id": 100, "name": "Weitsprung", "scoring_direction": "higher", "unit": "m"},
            {"id": 101, "name": "Sprint", "scoring_direction": "lower", "unit": "s"},
        ]
        weitsprung = {1: 4.5, 2: 4.2, 3: 4.2, 4: 3.9}
        sprint = {1: 8.0, 2: 8.5, 3: 8.5, 4: 9.0}
        self.results = [
            {"team_id": team_id, "discipline_id": 100, "value": value}
            for team_id, value in weitsprung.items()
        ] + [
            {"team_id": team_id, "discipline_id": 101, "value": value}
            for team_id, value in sprint.items()
        ]
        self.ranking, _, _ = calculate_sixkampf_team_ranking(
            self.teams, self.disciplines, self.results, points_first_place=4,
            require_result_entry=True,
        )

    def _by_team(self, eintraege):
        return {e["class_name"]: e for e in eintraege}

    def test_disziplin_gleichstand_ergibt_zweimal_platz_2(self):
        eintraege = eintraege_aus_disziplinen(self.competition, self.disciplines, self.ranking, "28.10.2026")
        weitsprung = self._by_team([e for e in eintraege if e["discipline_id"] == 100])
        self.assertEqual(weitsprung["7a"]["platz"], 1)
        self.assertEqual(weitsprung["7b"]["platz"], 2)
        self.assertEqual(weitsprung["7c"]["platz"], 2)
        self.assertEqual(weitsprung["7d"]["platz"], 3)
        self.assertEqual(weitsprung["7b"]["leistung"], "4,2 m")

    def test_disziplin_mit_niedriger_ist_besser(self):
        eintraege = eintraege_aus_disziplinen(self.competition, self.disciplines, self.ranking, "")
        sprint = self._by_team([e for e in eintraege if e["discipline_id"] == 101])
        self.assertEqual(sprint["7a"]["platz"], 1)
        self.assertEqual(sprint["7d"]["platz"], 3)
        self.assertEqual(sprint["7b"]["platz"], sprint["7c"]["platz"])

    def test_gleicher_platz_gleicher_urkundentyp(self):
        eintraege = eintraege_aus_disziplinen(self.competition, self.disciplines, self.ranking, "")
        wende_anpassungen_an(eintraege, {}, _schwellen(sieger_bis_platz=2))
        weitsprung = self._by_team([e for e in eintraege if e["discipline_id"] == 100])
        self.assertEqual(weitsprung["7b"]["urkundentyp"], URKUNDE_SIEGER)
        self.assertEqual(weitsprung["7c"]["urkundentyp"], URKUNDE_SIEGER)
        self.assertEqual(weitsprung["7d"]["urkundentyp"], URKUNDE_TEILNEHMER)

    def test_gesamturkunde_sechskampf_mit_gleichstand(self):
        eintraege = self._by_team(eintraege_aus_sechskampf(self.competition, self.ranking, ""))
        self.assertEqual(eintraege["7a"]["platz"], 1)
        self.assertEqual(eintraege["7b"]["platz"], eintraege["7c"]["platz"])
        self.assertEqual(eintraege["7b"]["platz"], 2)
        self.assertTrue(eintraege["7a"]["leistung"].endswith("Punkte"))
        self.assertEqual(len(eintraege), 4)

    def test_eintrag_traegt_jahrgangsgruppe_fuer_schwellen(self):
        eintraege = eintraege_aus_sechskampf(self.competition, self.ranking, "")
        self.assertTrue(all(e["jahrgangsgruppe"] == "Jahrgang 7" for e in eintraege))


class TurnierUndGesamtwertungTests(unittest.TestCase):
    def test_turnier_uebernimmt_plaetze_inkl_gleichstand(self):
        competition = {"id": 5, "name": "Fußball Jahrgang 8", "jahrgang": 8}
        rows = [
            {"team_id": 1, "team": "8a", "placement": 1, "pkt": 9.0, "pkt_display": "9", "plus": 7, "minus": 1},
            {"team_id": 2, "team": "8b", "placement": 2, "pkt": 4.0, "pkt_display": "4", "plus": 3, "minus": 3},
            {"team_id": 3, "team": "8c", "placement": 2, "pkt": 4.0, "pkt_display": "4", "plus": 3, "minus": 3},
            {"team_id": 4, "team": "8d", "placement": 4, "pkt": 0.0, "pkt_display": "0", "plus": 0, "minus": 6},
        ]
        eintraege = eintraege_aus_turnier(competition, rows, "01.07.2026", team_jahrgaenge={1: 8})
        wende_anpassungen_an(eintraege, {}, parse_schwellen(""))
        self.assertEqual([e["platz"] for e in eintraege], [1, 2, 2, 4])
        self.assertEqual(
            [e["urkundentyp"] for e in eintraege],
            [URKUNDE_SIEGER, URKUNDE_SIEGER, URKUNDE_SIEGER, URKUNDE_TEILNEHMER],
        )
        self.assertEqual(eintraege[0]["leistung"], "9 Punkte (7:1)")
        self.assertEqual(eintraege[0]["key"], "w:5:1")

    def test_gesamtwertung_gleichstand(self):
        groups = [{
            "year_group": "Gesamtwertung Jahrgang 9",
            "rows": [
                {"team_id": 1, "team": "9a", "jahrgang": 9, "place": 1, "total_points": 20.0, "total_points_display": "20"},
                {"team_id": 2, "team": "9b", "jahrgang": 9, "place": 2, "total_points": 15.0, "total_points_display": "15"},
                {"team_id": 3, "team": "9c", "jahrgang": 9, "place": 2, "total_points": 15.0, "total_points_display": "15"},
                {"team_id": 4, "team": "9d", "jahrgang": 9, "place": 4, "total_points": 12.0, "total_points_display": "12"},
            ],
        }]
        eintraege = eintraege_aus_gesamtwertung(groups, "")
        wende_anpassungen_an(eintraege, {}, _schwellen(ehren_ab_punkte=12))
        self.assertEqual([e["platz"] for e in eintraege], [1, 2, 2, 4])
        self.assertEqual(eintraege[3]["urkundentyp"], URKUNDE_EHREN)
        self.assertEqual(eintraege[0]["quelle_label"], "Gesamtwertung Jahrgang 9")


class AnpassungenTests(unittest.TestCase):
    def _eintraege(self):
        groups = [{
            "year_group": "Gesamtwertung Jahrgang 7",
            "rows": [
                {"team_id": 1, "team": "7a", "jahrgang": 7, "place": 1, "total_points": 10, "total_points_display": "10"},
                {"team_id": 2, "team": "7b", "jahrgang": 7, "place": 4, "total_points": 5, "total_points_display": "5"},
            ],
        }]
        return eintraege_aus_gesamtwertung(groups, "")

    def test_manueller_platz_bestimmt_typ_neu(self):
        eintraege = self._eintraege()
        key = eintraege[1]["key"]
        wende_anpassungen_an(eintraege, {key: {"platz": 3}}, parse_schwellen(""))
        self.assertEqual(eintraege[1]["platz"], 3)
        self.assertEqual(eintraege[1]["berechneter_platz"], 4)
        self.assertEqual(eintraege[1]["urkundentyp"], URKUNDE_SIEGER)
        self.assertEqual(eintraege[1]["berechneter_typ"], URKUNDE_TEILNEHMER)
        self.assertTrue(eintraege[1]["angepasst"])

    def test_manueller_typ_hat_vorrang(self):
        eintraege = self._eintraege()
        key = eintraege[0]["key"]
        wende_anpassungen_an(
            eintraege,
            {key: {"urkundentyp": URKUNDE_EHREN, "name": "Klasse 7a", "leistung": "toll"}},
            parse_schwellen(""),
        )
        self.assertEqual(eintraege[0]["urkundentyp"], URKUNDE_EHREN)
        self.assertTrue(eintraege[0]["typ_manuell"])
        self.assertEqual(eintraege[0]["recipient_name"], "Klasse 7a")
        self.assertEqual(eintraege[0]["class_name"], "7a")
        self.assertEqual(eintraege[0]["leistung"], "toll")

    def test_ausgeblendet_und_filter(self):
        eintraege = self._eintraege()
        wende_anpassungen_an(eintraege, {eintraege[1]["key"]: {"ausgeblendet": 1}}, parse_schwellen(""))
        self.assertEqual(len(filtere_eintraege(eintraege, nur_druck=True)), 1)
        self.assertEqual(len(filtere_eintraege(eintraege, platz_bis=3)), 1)
        self.assertEqual(len(filtere_eintraege(eintraege, team_id=2)), 1)
        self.assertEqual(len(filtere_eintraege(eintraege, urkundentyp=URKUNDE_SIEGER)), 1)
        self.assertEqual(len(filtere_eintraege(eintraege, jahrgangsgruppe="Jahrgang 8")), 0)

    def test_sortierung_nach_klasse(self):
        eintraege = [
            {"jahrgang": 7, "class_name": "7b", "quelle_id": "d:2", "quelle_label": "A", "platz": 1, "recipient_name": "7b"},
            {"jahrgang": 7, "class_name": "7a", "quelle_id": "d:1", "quelle_label": "B", "platz": 2, "recipient_name": "7a"},
            {"jahrgang": 7, "class_name": "7a", "quelle_id": "d:2", "quelle_label": "A", "platz": 3, "recipient_name": "7a"},
        ]
        nach_klasse = sortiere_eintraege(eintraege, "klasse")
        self.assertEqual([(e["class_name"], e["quelle_label"]) for e in nach_klasse],
                         [("7a", "A"), ("7a", "B"), ("7b", "A")])
        nach_wertung = sortiere_eintraege(eintraege, "wertung")
        self.assertEqual([e["platz"] for e in nach_wertung], [1, 3, 2])

    def test_datum_format(self):
        self.assertEqual(formatiere_datum("2026-06-30"), "30.06.2026")
        self.assertEqual(formatiere_datum(""), "")
        self.assertEqual(formatiere_datum("Sommer 2026"), "Sommer 2026")


class PersistenzTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "urkunden-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        database.init_db()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_anpassung_speichern_laden_loeschen(self):
        from app.services.urkunden_service import (
            lade_anpassungen, loesche_anpassung, speichere_anpassung,
        )
        speichere_anpassung(1, "w:5:1", platz="2", urkundentyp="Unsinn", name=" ", leistung="", ausgeblendet=True)
        anpassung = lade_anpassungen(1)["w:5:1"]
        self.assertEqual(anpassung["platz"], 2)
        self.assertIsNone(anpassung["urkundentyp"])
        self.assertIsNone(anpassung["name"])
        self.assertEqual(anpassung["ausgeblendet"], 1)

        speichere_anpassung(1, "w:5:1", platz="", urkundentyp=URKUNDE_EHREN)
        anpassung = lade_anpassungen(1)["w:5:1"]
        self.assertIsNone(anpassung["platz"])
        self.assertEqual(anpassung["urkundentyp"], URKUNDE_EHREN)
        self.assertEqual(lade_anpassungen(2), {})

        loesche_anpassung(1, "w:5:1")
        self.assertEqual(lade_anpassungen(1), {})

    def test_schwellen_speichern(self):
        from app.services.urkunden_service import lade_schwellen, speichere_schwellen
        speichere_schwellen({
            "standard": {"sieger_bis_platz": "3", "ehren_bis_platz": "6", "ehren_ab_punkte": ""},
            "Jahrgang 9": {"sieger_bis_platz": "", "ehren_bis_platz": "", "ehren_ab_punkte": "25"},
        })
        schwellen = lade_schwellen()
        jg9 = schwelle_fuer_gruppe(schwellen, "Jahrgang 9")
        self.assertEqual(jg9, {"sieger_bis_platz": 3, "ehren_bis_platz": 6, "ehren_ab_punkte": 25.0})
        self.assertIsNone(schwelle_fuer_gruppe(schwellen, "Jahrgang 7")["ehren_ab_punkte"])

    def test_logo_nur_png_oder_jpg(self):
        from PIL import Image
        from app.services.urkunden_service import lade_logo, loesche_logo, speichere_logo

        with self.assertRaises(ValueError):
            speichere_logo(b"GIF89a....")
        buffer = BytesIO()
        Image.new("RGB", (20, 10), (255, 0, 0)).save(buffer, "PNG")
        speichere_logo(buffer.getvalue())
        data, mime = lade_logo()
        self.assertEqual(mime, "image/png")
        self.assertEqual(data, buffer.getvalue())
        loesche_logo()
        self.assertEqual(lade_logo(), (None, None))


class PdfTests(unittest.TestCase):
    def test_lange_namen_werden_verkleinert(self):
        from app.services.urkunden_pdf_service import UrkundenPDF, passe_schriftgroesse

        pdf = UrkundenPDF()
        pdf.add_page()
        self.assertEqual(passe_schriftgroesse(pdf, "7a", 160, 34, 14), 34)
        lang = "Maximiliane-Franziska Müller-Lüdenscheidt-Großkopf"
        size = passe_schriftgroesse(pdf, lang, 160, 34, 14)
        self.assertLess(size, 34)
        self.assertGreaterEqual(size, 14)
        pdf.set_font("DejaVu", "B", size)
        self.assertLessEqual(pdf.get_string_width(lang), 160)

    def test_pdf_mit_umlauten_eine_seite_pro_urkunde(self):
        from app.services.urkunden_pdf_service import erzeuge_urkunden_pdf

        basis = {
            "quelle_praefix": "in der Disziplin", "quelle_label": "Weitsprung",
            "jahrgang_label": "Jahrgang 7", "leistung": "4,35 m", "datum": "28.10.2026",
            "veranstaltung": "Sportfest",
        }
        eintraege = [
            dict(basis, recipient_name="Jörg Dvořák-Ørsted", platz=1, urkundentyp=URKUNDE_SIEGER),
            dict(basis, recipient_name="Ein sehr, sehr langer Name " * 6, platz=5, urkundentyp=URKUNDE_EHREN),
            dict(basis, recipient_name="7c", platz=None, urkundentyp=URKUNDE_TEILNEHMER),
        ]
        layout = {"schulname": "Schule", "ort": "Köln", "unterschrift_links": "Sportlehrer:in",
                  "unterschrift_rechts": "Schulleitung"}
        pdf = erzeuge_urkunden_pdf(eintraege, layout)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertEqual(pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page "), 3)

    def test_leeres_pdf_hat_hinweisseite(self):
        from app.services.urkunden_pdf_service import erzeuge_urkunden_pdf

        self.assertTrue(erzeuge_urkunden_pdf([], {}).startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
