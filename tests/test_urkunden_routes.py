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
from app.database import get_conn, init_db


class UrkundenRouteTests(unittest.TestCase):
    """HTTP-Ebene des Urkunden-Generators (/urkunden): Vorschau-Seite, PDF,
    manuelle Anpassung und Zugriffsschutz (Turnierleitung/Admin)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "urkunden-routes.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()
        self._seed()

        from app.main import app
        self.client = TestClient(app)

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def _seed(self):
        with get_conn() as conn:
            for name in ("7a", "7b", "7c"):
                conn.execute("INSERT INTO teams (name, jahrgang) VALUES (?, 7)", (name,))
            team = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM teams")}
            conn.execute(
                "INSERT INTO events (name, event_date, status) VALUES ('Sportfest 2026', '2026-06-30', 'aktiv')"
            )
            self.event_id = conn.execute("SELECT id FROM events").fetchone()["id"]

            conn.execute("""
                INSERT INTO competitions (name, sportart, jahrgang, competition_type, event_id)
                VALUES ('Sechskampf Jahrgang 7', 'Leichtathletik', 7, 'Sechskampf', ?)
            """, (self.event_id,))
            sechskampf_id = conn.execute(
                "SELECT id FROM competitions WHERE competition_type = 'Sechskampf'"
            ).fetchone()["id"]
            conn.execute("""
                INSERT INTO competition_disciplines (competition_id, name, unit, scoring_direction)
                VALUES (?, 'Weitsprung', 'm', 'higher')
            """, (sechskampf_id,))
            discipline_id = conn.execute("SELECT id FROM competition_disciplines").fetchone()["id"]
            for name, value in (("7a", 4.5), ("7b", 4.1), ("7c", 4.1)):
                conn.execute("""
                    INSERT INTO sixkampf_team_results (competition_id, discipline_id, team_id, value_index, value)
                    VALUES (?, ?, ?, 0, ?)
                """, (sechskampf_id, discipline_id, team[name], value))

            conn.execute("""
                INSERT INTO competitions (name, sportart, jahrgang, competition_type, event_id)
                VALUES ('Völkerball Jahrgang 7', 'Völkerball', 7, 'Turnier', ?)
            """, (self.event_id,))
            turnier_id = conn.execute(
                "SELECT id FROM competitions WHERE competition_type = 'Turnier'"
            ).fetchone()["id"]
            for a, b, sa, sb in (("7a", "7b", 3, 1), ("7b", "7c", 2, 0), ("7a", "7c", 1, 1)):
                conn.execute("""
                    INSERT INTO slots (competition_id, startzeit, slot_typ, phase, team_a_id, team_b_id,
                                       score_a, score_b, status)
                    VALUES (?, '09:00', 'Spiel', 'Gruppenphase', ?, ?, ?, ?, 'beendet')
                """, (turnier_id, team[a], team[b], sa, sb))
            conn.commit()
        self.team = team
        self.discipline_id = discipline_id

    def test_vorschauseite_fuer_alle_wertungen(self):
        for wertung in ("wettbewerb", "disziplin", "gesamt"):
            response = self.client.get(f"/urkunden?event_id={self.event_id}&wertung={wertung}")
            self.assertEqual(response.status_code, 200, wertung)
            self.assertIn("7b", response.text)
            self.assertIn("Siegerurkunde", response.text)
        response = self.client.get(f"/urkunden?event_id={self.event_id}&wertung=disziplin")
        self.assertIn("Weitsprung (Sechskampf Jahrgang 7)", response.text)

    def test_seite_ohne_veranstaltung(self):
        with get_conn() as conn:
            conn.execute("DELETE FROM competitions")
            conn.execute("DELETE FROM events")
            conn.commit()
        response = self.client.get("/urkunden")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Noch keine Veranstaltung", response.text)

    def test_sammel_und_einzel_pdf(self):
        response = self.client.get(f"/urkunden/pdf?event_id={self.event_id}&wertung=disziplin&download=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertTrue(response.content.startswith(b"%PDF"))

        key = f"d:{self.discipline_id}:{self.team['7b']}"
        response = self.client.get(
            f"/urkunden/pdf", params={"event_id": self.event_id, "wertung": "disziplin", "key": key}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("inline", response.headers["content-disposition"])
        self.assertIn("7b", response.headers["content-disposition"])

    def test_vorschau_als_bild_fuer_handy(self):
        page = self.client.get(f"/urkunden?event_id={self.event_id}&wertung=disziplin").text
        self.assertIn("/urkunden/vorschau.png?", page)
        self.assertNotIn("<iframe", page)

        key = f"d:{self.discipline_id}:{self.team['7a']}"
        response = self.client.get(
            "/urkunden/vorschau.png", params={"event_id": self.event_id, "wertung": "disziplin", "key": key}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertTrue(response.content.startswith(b"\x89PNG"))

        response = self.client.get(
            "/urkunden/vorschau.png", params={"event_id": self.event_id, "key": "gibt-es-nicht"}
        )
        self.assertEqual(response.status_code, 404)

    def test_manuelle_platzkorrektur(self):
        key = f"d:{self.discipline_id}:{self.team['7c']}"
        response = self.client.post("/urkunden/anpassung", data={
            "event_id": self.event_id, "key": key, "platz": "3", "urkundentyp": "Ehrenurkunde",
            "name": "Klasse 7c", "leistung": "", "drucken": "1",
            "rueckweg": f"/urkunden?event_id={self.event_id}&wertung=disziplin",
        }, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers["location"].startswith("/urkunden?"))

        from app.services.urkunden_service import lade_anpassungen
        anpassung = lade_anpassungen(self.event_id)[key]
        self.assertEqual(anpassung["platz"], 3)
        self.assertEqual(anpassung["urkundentyp"], "Ehrenurkunde")

        page = self.client.get(f"/urkunden?event_id={self.event_id}&wertung=disziplin").text
        self.assertIn('value="Klasse 7c"', page)
        self.assertIn("Zurücksetzen", page)

        self.client.post("/urkunden/anpassung/zuruecksetzen", data={
            "event_id": self.event_id, "key": key, "rueckweg": "https://example.org/",
        }, follow_redirects=False)
        self.assertEqual(lade_anpassungen(self.event_id), {})

    def test_rueckweg_bleibt_in_urkunden(self):
        response = self.client.post("/urkunden/anpassung/zuruecksetzen", data={
            "event_id": self.event_id, "key": "x", "rueckweg": "//evil.example",
        }, follow_redirects=False)
        self.assertTrue(response.headers["location"].startswith("/urkunden"))

    def test_einstellungen_und_logo(self):
        response = self.client.post("/urkunden/einstellungen", data={
            "schulname": "Gymnasium Süd", "unterschrift_links": "Sportlehrerin",
            "standard_sieger_bis_platz": "1", "gruppe0_ehren_bis_platz": "2",
            "rueckweg": "/urkunden",
        }, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        from app.services.urkunden_service import lade_layout, lade_schwellen, schwelle_fuer_gruppe
        self.assertEqual(lade_layout()["schulname"], "Gymnasium Süd")
        self.assertEqual(schwelle_fuer_gruppe(lade_schwellen(), "Jahrgang 7")["ehren_bis_platz"], 2)

        page = self.client.get(f"/urkunden?event_id={self.event_id}&wertung=disziplin").text
        self.assertIn("Ehrenurkunde", page)

        response = self.client.post(
            "/urkunden/logo", data={"rueckweg": "/urkunden"},
            files={"logo": ("logo.gif", b"GIF89a", "image/gif")}, follow_redirects=False,
        )
        self.assertIn("fehler=", response.headers["location"])
        self.assertEqual(self.client.get("/urkunden/logo").status_code, 404)

    def test_nur_turnierleitung_und_admin_bei_aktiver_sicherheit(self):
        from app.main import get_required_role
        self.assertEqual(get_required_role("/urkunden"), "tournament_lead")
        self.assertEqual(get_required_role("/urkunden/pdf"), "tournament_lead")
        self.assertEqual(get_required_role("/urkunden/anpassung"), "tournament_lead")

        with patch("app.services.settings_service.is_security_enabled", return_value=True):
            response = self.client.get("/urkunden", follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["location"], "/")
            response = self.client.get(f"/urkunden/pdf?event_id={self.event_id}", follow_redirects=False)
            self.assertEqual(response.status_code, 303)


if __name__ == "__main__":
    unittest.main()
