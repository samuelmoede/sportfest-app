"""Feldansicht der Ergebniseingabe (/ergebnisse/feld): Auswahl von Ort und
Feld, Aufteilung in beendet / laufend / geplant und die Route selbst."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi.testclient import TestClient

import app.database as database
from app.database import get_conn, init_db
from app.services.feldansicht_service import SICHTBARE_BEENDETE, build_feldansicht
from app.services.results_filter_service import build_results_redirect_url

COURTS = [
    {"id": 1, "name": "Feld 1", "location": "Turnhalle"},
    {"id": 2, "name": "Feld 2", "location": "Turnhalle"},
    {"id": 3, "name": "Feld 10", "location": "Turnhalle"},
    {"id": 4, "name": "Platte 1", "location": "Mensa"},
    {"id": 5, "name": "Platte 2", "location": "Mensa"},
]


def _slot(slot_id, court_id, startzeit, status="geplant"):
    return {
        "id": slot_id, "court_id": court_id, "startzeit": startzeit,
        "status": status, "slot_typ": "Spiel",
    }


class BuildFeldansichtTests(unittest.TestCase):
    def test_ohne_auswahl_erster_ort_und_erstes_feld_mit_spielen(self):
        slots = [_slot(1, 2, "09:00"), _slot(2, 4, "09:00")]
        ansicht = build_feldansicht(slots, COURTS)
        self.assertEqual(ansicht["ort"], "Turnhalle")
        self.assertEqual(ansicht["feld"]["id"], 2)
        # Feld 1 und Feld 10 haben keine Spiele und tauchen nicht auf.
        self.assertEqual([f["name"] for f in ansicht["felder"]], ["Feld 2"])
        self.assertEqual([o["name"] for o in ansicht["orte"]], ["Turnhalle", "Mensa"])

    def test_feld_bestimmt_den_ort(self):
        slots = [_slot(1, 1, "09:00"), _slot(2, 5, "09:00")]
        ansicht = build_feldansicht(slots, COURTS, court_id=5)
        self.assertEqual(ansicht["ort"], "Mensa")
        self.assertEqual(ansicht["feld"]["name"], "Platte 2")
        self.assertTrue(ansicht["feld"]["gewaehlt"])

    def test_ort_ohne_feld_nimmt_erstes_feld_des_orts(self):
        slots = [_slot(1, 1, "09:00"), _slot(2, 5, "09:00"), _slot(3, 4, "09:00")]
        ansicht = build_feldansicht(slots, COURTS, ort="Mensa")
        self.assertEqual(ansicht["feld"]["name"], "Platte 1")
        self.assertEqual([f["name"] for f in ansicht["felder"]], ["Platte 1", "Platte 2"])

    def test_feld_aus_anderem_ort_wird_bei_ortswahl_verworfen(self):
        slots = [_slot(1, 1, "09:00"), _slot(2, 4, "09:00")]
        ansicht = build_feldansicht(slots, COURTS, ort="Mensa", court_id=1)
        self.assertEqual(ansicht["ort"], "Mensa")
        self.assertEqual(ansicht["feld"]["id"], 4)

    def test_unbekanntes_feld_faellt_auf_standard_zurueck(self):
        ansicht = build_feldansicht([_slot(1, 1, "09:00")], COURTS, court_id=99)
        self.assertEqual(ansicht["feld"]["id"], 1)

    def test_felder_natuerlich_sortiert(self):
        slots = [_slot(1, 3, "09:00"), _slot(2, 2, "09:00"), _slot(3, 1, "09:00")]
        ansicht = build_feldansicht(slots, COURTS)
        self.assertEqual([f["name"] for f in ansicht["felder"]], ["Feld 1", "Feld 2", "Feld 10"])

    def test_laufendes_spiel_und_reihenfolge(self):
        slots = [
            _slot(4, 1, "09:30"), _slot(1, 1, "09:00", "beendet"),
            _slot(3, 1, "09:20", "läuft"), _slot(2, 1, "09:10", "beendet"),
            _slot(5, 2, "09:00", "läuft"),
        ]
        ansicht = build_feldansicht(slots, COURTS, court_id=1)
        self.assertEqual([s["id"] for s in ansicht["beendet"]], [1, 2])
        self.assertEqual([s["id"] for s in ansicht["laufend"]], [3])
        self.assertIsNone(ansicht["dran"])
        self.assertEqual([s["id"] for s in ansicht["geplant"]], [4])
        self.assertTrue(ansicht["feld"]["laeuft"])
        self.assertEqual(ansicht["feld"]["offen"], 2)

    def test_ohne_laufendes_spiel_ist_das_naechste_dran(self):
        slots = [_slot(1, 1, "09:00", "beendet"), _slot(2, 1, "09:10"), _slot(3, 1, "09:20")]
        ansicht = build_feldansicht(slots, COURTS)
        self.assertEqual(ansicht["dran"]["id"], 2)
        self.assertEqual([s["id"] for s in ansicht["geplant"]], [3])
        self.assertFalse(ansicht["feld"]["laeuft"])

    def test_aeltere_beendete_spiele_klappen_ein(self):
        slots = [_slot(i, 1, f"09:{i:02d}", "beendet") for i in range(1, 6)]
        ansicht = build_feldansicht(slots, COURTS)
        self.assertEqual(len(ansicht["beendet"]), SICHTBARE_BEENDETE)
        self.assertEqual([s["id"] for s in ansicht["beendet"]], [4, 5])
        self.assertEqual([s["id"] for s in ansicht["beendet_frueher"]], [1, 2, 3])

    def test_ohne_spiele(self):
        ansicht = build_feldansicht([], COURTS)
        self.assertIsNone(ansicht["feld"])
        self.assertIsNone(ansicht["ort"])
        self.assertFalse(ansicht["hat_spiele"])


class FeldansichtRedirectTests(unittest.TestCase):
    def test_rueckweg_in_die_feldansicht_bleibt_erhalten(self):
        url = build_results_redirect_url("/ergebnisse/feld?court_id=3", correction_blocked="1")
        self.assertEqual(url, "/ergebnisse/feld?court_id=3&correction_blocked=1")

    def test_fremde_pfade_fallen_weiter_auf_ergebnisse_zurueck(self):
        self.assertEqual(build_results_redirect_url("/ergebnisse/feldx?court_id=3"), "/ergebnisse")
        self.assertEqual(build_results_redirect_url("//boese.example/ergebnisse/feld"), "/ergebnisse")
        self.assertEqual(build_results_redirect_url("/ergebnisse?competition_id=1"), "/ergebnisse?competition_id=1")


class FeldansichtRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_db_path = database.DB_PATH
        database.DB_PATH = Path(self.tmpdir.name) / "feldansicht-test.db"
        init_db()
        with get_conn() as conn:
            self.feld1 = conn.execute(
                "INSERT INTO courts (name, location) VALUES ('Feld 1', 'Turnhalle')"
            ).lastrowid
            self.platte1 = conn.execute(
                "INSERT INTO courts (name, location) VALUES ('Platte 1', 'Mensa')"
            ).lastrowid
            self.comp = conn.execute(
                """
                INSERT INTO competitions (name, sportart, jahrgang, status, competition_type, location)
                VALUES ('Zweifelderball 7', 'Zweifelderball', 7, 'aktiv', 'Turnier', 'Turnhalle')
                """
            ).lastrowid
            self.team_a = conn.execute("INSERT INTO teams (name, jahrgang) VALUES ('7a', 7)").lastrowid
            self.team_b = conn.execute("INSERT INTO teams (name, jahrgang) VALUES ('7b', 7)").lastrowid
            self.beendet = self._slot(conn, self.feld1, "09:00", "beendet", 3, 1)
            self.laeuft = self._slot(conn, self.feld1, "09:10", "läuft", 1, 0)
            self._slot(conn, self.feld1, "09:20", "geplant")
            self._slot(conn, self.platte1, "09:00", "geplant")
            conn.commit()
        from app.main import app
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        database.DB_PATH = self.original_db_path
        self.tmpdir.cleanup()

    def _slot(self, conn, court_id, startzeit, status, score_a=None, score_b=None):
        return conn.execute(
            """
            INSERT INTO slots (competition_id, court_id, startzeit, slot_typ, phase, gruppe,
                               team_a_id, team_b_id, score_a, score_b, status)
            VALUES (?, ?, ?, 'Spiel', 'Gruppenphase', 'A', ?, ?, ?, ?, ?)
            """,
            (self.comp, court_id, startzeit, self.team_a, self.team_b, score_a, score_b, status),
        ).lastrowid

    def test_seite_zeigt_feld_mit_beendet_laufend_geplant(self):
        response = self.client.get("/ergebnisse/feld")
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("<h1>Feld 1</h1>", html)
        self.assertIn("3 : 1", html)
        self.assertIn(f'data-slot="{self.laeuft}" data-autospeichern="1"', html)
        self.assertIn("Spiel beenden", html)
        self.assertIn(f'data-url="/slot/', html)
        self.assertIn("/ergebnisse/feld?ort=Mensa", html)

    def test_gewaehltes_feld_wird_gemerkt(self):
        response = self.client.get(f"/ergebnisse/feld?court_id={self.platte1}")
        self.assertIn("<h1>Platte 1</h1>", response.text)
        self.assertIn(f"feldansicht_feld={self.platte1}", response.headers.get("set-cookie", ""))
        # Der naechste Aufruf ohne Auswahl landet wieder dort.
        self.assertIn("<h1>Platte 1</h1>", self.client.get("/ergebnisse/feld").text)
        # Eine ausdrueckliche Ortswahl geht vor.
        self.assertIn("<h1>Feld 1</h1>", self.client.get("/ergebnisse/feld?ort=Turnhalle").text)

    def test_speichern_fuehrt_zurueck_in_die_feldansicht(self):
        response = self.client.post(
            f"/slot/{self.laeuft}/save",
            data={
                "score_a": "2", "score_b": "0", "finish": "1",
                "results_return_to": f"/ergebnisse/feld?court_id={self.feld1}",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers["location"].startswith("/ergebnisse/feld?court_id="))
        with get_conn() as conn:
            slot = conn.execute("SELECT * FROM slots WHERE id = ?", (self.laeuft,)).fetchone()
        self.assertEqual((slot["status"], slot["score_a"], slot["score_b"]), ("beendet", 2, 0))

    def test_ohne_spiele_verweist_auf_das_raster(self):
        with get_conn() as conn:
            conn.execute("DELETE FROM slots")
            conn.commit()
        response = self.client.get("/ergebnisse/feld")
        self.assertEqual(response.status_code, 200)
        self.assertIn("/ergebnisse?ansicht=raster", response.text)
        self.assertNotIn("set-cookie", response.headers)

    def test_raster_wechselt_am_handy_nur_ohne_filter(self):
        html = self.client.get("/ergebnisse").text
        self.assertIn("window.location.replace('/ergebnisse/feld')", html)
        self.assertIn("!window.location.search", html)


if __name__ == "__main__":
    unittest.main()
