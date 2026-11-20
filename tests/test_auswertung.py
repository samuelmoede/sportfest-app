import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pypdfium2
from fastapi.testclient import TestClient

import app.database as database
from app.database import init_db
from app.services.urkunden_service import (
    BLANKO_MAX_ANZAHL,
    URKUNDE_EHREN,
    URKUNDE_SIEGER,
    URKUNDE_TEILNEHMER,
    blanko_eintraege,
)
from tests import test_urkunden_routes


def pdf_texte(pdf_bytes):
    document = pypdfium2.PdfDocument(pdf_bytes)
    try:
        return [document[i].get_textpage().get_text_range() for i in range(len(document))]
    finally:
        document.close()


class BlankoServiceTests(unittest.TestCase):
    def test_blanko_ohne_klasse_mit_veranstaltung(self):
        eintraege = blanko_eintraege(
            urkundentyp=URKUNDE_SIEGER, anzahl=3, platz="2",
            quelle_label="Völkerball", quelle_praefix="im Wettbewerb",
            veranstaltung="Schulpokal", datum="21.12.2026",
        )
        self.assertEqual(len(eintraege), 3)
        for eintrag in eintraege:
            self.assertTrue(eintrag["blanko"])
            self.assertEqual(eintrag["recipient_name"], "")
            self.assertEqual(eintrag["platz"], 2)
            self.assertEqual(eintrag["veranstaltung"], "Schulpokal")

    def test_anzahl_wird_begrenzt_und_teilnehmer_ohne_platz(self):
        self.assertEqual(len(blanko_eintraege(urkundentyp=URKUNDE_EHREN, anzahl="0")), 1)
        self.assertEqual(len(blanko_eintraege(urkundentyp=URKUNDE_EHREN, anzahl="abc")), 1)
        self.assertEqual(
            len(blanko_eintraege(urkundentyp=URKUNDE_EHREN, anzahl=10_000)), BLANKO_MAX_ANZAHL
        )
        eintrag = blanko_eintraege(urkundentyp=URKUNDE_TEILNEHMER, anzahl=1, platz="1")[0]
        self.assertIsNone(eintrag["platz"])
        # Unbekannter Typ faellt auf Teilnehmerurkunde zurueck, leerer Platz bleibt leer.
        self.assertEqual(blanko_eintraege(urkundentyp="x", anzahl=1)[0]["urkundentyp"], URKUNDE_TEILNEHMER)
        self.assertIsNone(blanko_eintraege(urkundentyp=URKUNDE_SIEGER, anzahl=1, platz="")[0]["platz"])

    def test_ohne_wettbewerb_kein_praefix(self):
        eintrag = blanko_eintraege(urkundentyp=URKUNDE_SIEGER, anzahl=1, quelle_praefix="im Wettbewerb")[0]
        self.assertEqual(eintrag["quelle_praefix"], "")

    def test_blanko_pdf_hat_schreiblinien_statt_klasse(self):
        from app.services.urkunden_pdf_service import erzeuge_urkunden_pdf

        pdf = erzeuge_urkunden_pdf(
            blanko_eintraege(urkundentyp=URKUNDE_SIEGER, anzahl=1, veranstaltung="Schulpokal"),
            {"unterschrift_links": "Sportlehrer:in"},
        )
        text = pdf_texte(pdf)[0]
        self.assertIn("SIEGERURKUNDE", text)
        self.assertIn("Schulpokal", text)
        self.assertIn(". Platz", text)
        self.assertNotIn("im Wettbewerb", text)


class AuswertungRouteTests(unittest.TestCase):
    """Eigene Auswertungsseite (/auswertung) und Blanko-PDF."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._db_path_patcher = patch.object(database, "DB_PATH", Path(self._tmpdir.name) / "auswertung.db")
        self._db_path_patcher.start()
        init_db()
        test_urkunden_routes.UrkundenRouteTests._seed(self)

        from app.main import app
        self.client = TestClient(app)

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_seite_zeigt_gesamtwertung_podest_und_druckwege(self):
        response = self.client.get(f"/auswertung?event_id={self.event_id}")
        self.assertEqual(response.status_code, 200)
        text = response.text
        self.assertIn("Gesamtwertung", text)
        self.assertIn("Einzelne Wettbewerbe nachschauen", text)
        # Gesamtwertung steht vor den Einzelwettbewerben.
        self.assertLess(text.index('id="gesamtwertung"'), text.index('id="wettbewerbe"'))
        self.assertIn("Völkerball Jahrgang 7", text)
        self.assertIn("Urkunden drucken", text)
        self.assertIn("Blanko", text)
        self.assertIn("/urkunden/blanko.pdf", text)
        self.assertIn(f"/urkunden/pdf?event_id={self.event_id}&amp;wertung=gesamt", text)
        # Sechskampf vorhanden: auch der Druck je Disziplin wird angeboten.
        self.assertIn("wertung=disziplin", text)

    def test_druckersymbol_je_jahrgang_und_wettbewerb(self):
        text = self.client.get(f"/auswertung?event_id={self.event_id}").text
        self.assertIn("quelle=g%3AGesamtwertung%20Jahrgang%207", text)
        self.assertIn("quelle=w%3A", text)
        self.assertIn('class="druck-symbol"', text)
        # Die Veranstaltungsseite nutzt dieselbe Tabelle, aber ohne Druckersymbol.
        event_text = self.client.get(f"/events/{self.event_id}").text
        self.assertNotIn('class="druck-symbol"', event_text)

    def test_dashboard_verlinkt_auf_auswertung(self):
        text = self.client.get("/").text
        self.assertIn(f'href="/auswertung?event_id={self.event_id}"', text)
        self.assertNotIn("#gesamtwertung", text)

    def test_seite_ohne_veranstaltung(self):
        from app.database import get_conn
        with get_conn() as conn:
            conn.execute("DELETE FROM competitions")
            conn.execute("DELETE FROM events")
            conn.commit()
        response = self.client.get("/auswertung")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Noch keine Veranstaltung", response.text)

    def test_blanko_pdf_mit_wettbewerb_und_satz(self):
        from app.database import get_conn
        with get_conn() as conn:
            turnier_id = conn.execute(
                "SELECT id FROM competitions WHERE competition_type = 'Turnier'"
            ).fetchone()["id"]

        response = self.client.get("/urkunden/blanko.pdf", params={
            "event_id": self.event_id, "typ": URKUNDE_SIEGER, "platz": "satz",
            "quelle": f"w:{turnier_id}", "anzahl": "2",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertIn("blanko", response.headers["content-disposition"])
        seiten = pdf_texte(response.content)
        self.assertEqual(len(seiten), 6)
        self.assertIn("1. Platz", seiten[0])
        self.assertIn("2. Platz", seiten[1])
        self.assertIn("3. Platz", seiten[2])
        for seite in seiten:
            self.assertIn("Sportfest 2026", seite)
            self.assertIn("Völkerball Jahrgang 7", seite)
            self.assertIn("30.06.2026", seite)
            self.assertNotIn("7a", seite)

    def test_blanko_teilnehmer_ignoriert_platz(self):
        response = self.client.get("/urkunden/blanko.pdf", params={
            "event_id": self.event_id, "typ": URKUNDE_TEILNEHMER, "platz": "satz", "anzahl": "3",
        })
        seiten = pdf_texte(response.content)
        self.assertEqual(len(seiten), 3)
        self.assertIn("TEILNEHMERURKUNDE", seiten[0])
        self.assertNotIn("Platz", seiten[0])

    def test_unbekannter_wettbewerb_bleibt_leer(self):
        response = self.client.get("/urkunden/blanko.pdf", params={
            "event_id": self.event_id, "quelle": "w:99999",
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("im Wettbewerb", pdf_texte(response.content)[0])

    def test_nur_turnierleitung_und_admin(self):
        from app.main import get_required_role
        self.assertEqual(get_required_role("/auswertung"), "tournament_lead")
        self.assertEqual(get_required_role("/urkunden/blanko.pdf"), "tournament_lead")
        with patch("app.services.settings_service.is_security_enabled", return_value=True):
            response = self.client.get("/auswertung", follow_redirects=False)
            self.assertEqual(response.status_code, 303)


if __name__ == "__main__":
    unittest.main()
