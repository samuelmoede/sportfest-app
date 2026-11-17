"""Automatisches Zwischenspeichern im Ergebnis-Raster und in der Feldansicht
(Feedback 2026-11-17): kein Speichern-Knopf mehr, der Stand wird beim Tippen
ueber /slot/<id>/zwischenstand gesichert.

Geprueft wird vor allem, was bei vielen gleichzeitigen Geraeten schiefgehen
koennte: nur der Stand wird geschrieben (Status/Uhr bleiben), ein beendetes
Spiel wird nie automatisch geaendert, und ein Geraet mit veraltetem Stand
ueberschreibt nicht, was ein anderes Geraet inzwischen gespeichert hat.
"""
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
from app.services import zwischenstand_service as zs


def _anlegen(conn, status="geplant", score_a=None, score_b=None, started_at=None):
    court_id = conn.execute("INSERT INTO courts (name) VALUES ('Feld 1')").lastrowid
    comp_id = conn.execute(
        """
        INSERT INTO competitions (name, sportart, jahrgang, status, competition_type)
        VALUES ('Zwischenstand-Test', 'Zweifelderball', 7, 'geplant', 'Turnier')
        """
    ).lastrowid
    team_a = conn.execute("INSERT INTO teams (name, jahrgang) VALUES ('7b', 7)").lastrowid
    team_b = conn.execute("INSERT INTO teams (name, jahrgang) VALUES ('7g', 7)").lastrowid
    slot_id = conn.execute(
        """
        INSERT INTO slots (competition_id, court_id, startzeit, slot_typ, phase, gruppe,
                           team_a_id, team_b_id, score_a, score_b, status, started_at)
        VALUES (?, ?, '08:10', 'Spiel', 'Gruppenphase', 'A', ?, ?, ?, ?, ?, ?)
        """,
        (comp_id, court_id, team_a, team_b, score_a, score_b, status, started_at),
    ).lastrowid
    conn.commit()
    return comp_id, slot_id


class _MitTempDb(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._patcher = patch.object(database, "DB_PATH", Path(self._tmpdir.name) / "zwischenstand.db")
        self._patcher.start()
        init_db()

    def tearDown(self):
        self._patcher.stop()
        self._tmpdir.cleanup()

    def slot(self, slot_id):
        with get_conn() as conn:
            return conn.execute("SELECT * FROM slots WHERE id = ?", (slot_id,)).fetchone()


class LiesStandTests(unittest.TestCase):
    def test_zahlen_und_leer(self):
        self.assertEqual(zs.lies_stand("2", "1"), (2, 1))
        self.assertEqual(zs.lies_stand(" 0 ", "12"), (0, 12))
        self.assertEqual(zs.lies_stand("", ""), (None, None))

    def test_ungueltig(self):
        for a, b in (("2", ""), ("", "1"), ("-1", "2"), ("x", "1"), ("1.5", "1"), ("99999", "1")):
            with self.subTest(a=a, b=b), self.assertRaises(ValueError):
                zs.lies_stand(a, b)


class SpeichereZwischenstandTests(_MitTempDb):
    def test_speichert_nur_den_stand_status_und_uhr_bleiben(self):
        with get_conn() as conn:
            _, geplant = _anlegen(conn)
        with get_conn() as conn:
            ergebnis = zs.speichere_zwischenstand(conn, geplant, "2", "1")
            conn.commit()
        self.assertEqual(ergebnis["ergebnis"], zs.GESPEICHERT)
        slot = self.slot(geplant)
        self.assertEqual((slot["score_a"], slot["score_b"]), (2, 1))
        # Tippen in ein geplantes Spiel startet es nicht (anders als /save).
        self.assertEqual(slot["status"], "geplant")
        self.assertIsNone(slot["started_at"])

    def test_leere_felder_loeschen_den_stand_nach_start_rueckgaengig(self):
        """Der gemeldete Fehler: 2:1 gespeichert, Start rueckgaengig - der
        Stand liess sich am Desktop nicht mehr entfernen."""
        with get_conn() as conn:
            _, slot_id = _anlegen(conn, status="geplant", score_a=2, score_b=1)
        protokoll = []
        with get_conn() as conn:
            ergebnis = zs.speichere_zwischenstand(
                conn, slot_id, "", "", basis=("2", "1"),
                protokolliere=lambda action, slot, alt, neu: protokoll.append((action, alt, neu)),
            )
            conn.commit()
        self.assertEqual(ergebnis["ergebnis"], zs.GESPEICHERT)
        slot = self.slot(slot_id)
        self.assertIsNone(slot["score_a"])
        self.assertIsNone(slot["score_b"])
        self.assertEqual(protokoll, [("Ergebnis gelöscht", (2, 1), (None, None))])

    def test_beendetes_spiel_wird_nie_automatisch_geaendert(self):
        with get_conn() as conn:
            _, slot_id = _anlegen(conn, status="beendet", score_a=3, score_b=1)
        with get_conn() as conn:
            ergebnis = zs.speichere_zwischenstand(conn, slot_id, "0", "0")
            conn.commit()
        self.assertEqual(ergebnis["ergebnis"], zs.BEENDET)
        slot = self.slot(slot_id)
        self.assertEqual((slot["score_a"], slot["score_b"], slot["status"]), (3, 1, "beendet"))

    def test_veralteter_stand_ueberschreibt_anderes_geraet_nicht(self):
        with get_conn() as conn:
            _, slot_id = _anlegen(conn, status="läuft", score_a=1, score_b=0)
        # Geraet A kannte 1:0 und speichert 2:0.
        with get_conn() as conn:
            self.assertEqual(
                zs.speichere_zwischenstand(conn, slot_id, "2", "0", basis=("1", "0"))["ergebnis"],
                zs.GESPEICHERT,
            )
            conn.commit()
        # Geraet B kannte auch noch 1:0 und will 1:1 speichern -> Konflikt.
        with get_conn() as conn:
            ergebnis = zs.speichere_zwischenstand(conn, slot_id, "1", "1", basis=("1", "0"))
            conn.commit()
        self.assertEqual(ergebnis["ergebnis"], zs.KONFLIKT)
        self.assertEqual((ergebnis["score_a"], ergebnis["score_b"]), (2, 0))
        slot = self.slot(slot_id)
        self.assertEqual((slot["score_a"], slot["score_b"]), (2, 0))

    def test_gleicher_stand_von_zwei_geraeten_ist_kein_konflikt(self):
        with get_conn() as conn:
            _, slot_id = _anlegen(conn, status="läuft", score_a=2, score_b=0)
        with get_conn() as conn:
            ergebnis = zs.speichere_zwischenstand(conn, slot_id, "2", "0", basis=("1", "0"))
        self.assertEqual(ergebnis["ergebnis"], zs.UNVERAENDERT)

    def test_ohne_aenderung_kein_protokolleintrag(self):
        with get_conn() as conn:
            _, slot_id = _anlegen(conn, status="läuft", score_a=2, score_b=0)
        protokoll = []
        with get_conn() as conn:
            zs.speichere_zwischenstand(
                conn, slot_id, "2", "0",
                protokolliere=lambda *args: protokoll.append(args),
            )
        self.assertEqual(protokoll, [])

    def test_nur_ein_feld_ausgefuellt_speichert_nichts(self):
        with get_conn() as conn:
            _, slot_id = _anlegen(conn, status="läuft", score_a=2, score_b=1)
        with get_conn() as conn:
            ergebnis = zs.speichere_zwischenstand(conn, slot_id, "3", "")
            conn.commit()
        self.assertEqual(ergebnis["ergebnis"], zs.UNGUELTIG)
        self.assertEqual((self.slot(slot_id)["score_a"], self.slot(slot_id)["score_b"]), (2, 1))


class ZwischenstandRouteTests(_MitTempDb):
    def setUp(self):
        super().setUp()
        from app.main import app
        self.client = TestClient(app)
        with get_conn() as conn:
            self.comp_id, self.slot_id = _anlegen(
                conn, status="läuft", score_a=None, score_b=None, started_at="2026-11-17 08:10:00",
            )

    def test_antwortet_mit_json_statt_ganzer_seite(self):
        antwort = self.client.post(
            f"/slot/{self.slot_id}/zwischenstand",
            data={"score_a": "2", "score_b": "1", "basis_a": "", "basis_b": "", "basis_pruefen": "1"},
            follow_redirects=False,
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(antwort.headers["cache-control"], "no-store")
        daten = antwort.json()
        self.assertEqual(daten["ergebnis"], "gespeichert")
        self.assertEqual((daten["score_a"], daten["score_b"]), (2, 1))
        self.assertEqual(daten["status"], "läuft")
        slot = self.slot(self.slot_id)
        self.assertEqual(slot["started_at"], "2026-11-17 08:10:00")
        with get_conn() as conn:
            eintrag = conn.execute(
                "SELECT action, old_value, new_value FROM change_log WHERE entity_id = ?",
                (self.slot_id,),
            ).fetchone()
        self.assertEqual(tuple(eintrag), ("Ergebnis erfasst", "–", "2:1"))

    def test_konflikt_liefert_409_mit_aktuellem_stand(self):
        self.client.post(f"/slot/{self.slot_id}/zwischenstand", data={"score_a": "2", "score_b": "1"})
        antwort = self.client.post(
            f"/slot/{self.slot_id}/zwischenstand",
            data={"score_a": "0", "score_b": "1", "basis_a": "", "basis_b": "", "basis_pruefen": "1"},
        )
        self.assertEqual(antwort.status_code, 409)
        self.assertEqual(antwort.json()["ergebnis"], "konflikt")
        self.assertEqual((antwort.json()["score_a"], antwort.json()["score_b"]), (2, 1))

    def test_ungueltig_und_unbekannt(self):
        antwort = self.client.post(f"/slot/{self.slot_id}/zwischenstand", data={"score_a": "2", "score_b": ""})
        self.assertEqual(antwort.status_code, 400)
        antwort = self.client.post("/slot/999999/zwischenstand", data={"score_a": "1", "score_b": "1"})
        self.assertEqual(antwort.status_code, 404)

    def test_braucht_dieselbe_rolle_wie_speichern(self):
        from app.main import get_required_role
        self.assertEqual(get_required_role(f"/slot/{self.slot_id}/zwischenstand"), "referee")
        self.assertEqual(
            get_required_role(f"/slot/{self.slot_id}/zwischenstand"),
            get_required_role(f"/slot/{self.slot_id}/save"),
        )


class RasterOhneSpeichernKnopfTests(_MitTempDb):
    def test_offene_karte_hat_autospeichern_statt_speichern_knopf(self):
        from app.main import app
        with get_conn() as conn:
            comp_id, slot_id = _anlegen(conn, status="geplant", score_a=2, score_b=1)
        html = TestClient(app).get(f"/ergebnisse?competition_id={comp_id}&ansicht=raster").text
        section = html[html.index('id="results-section"'):]
        self.assertIn(f'data-slot-id="{slot_id}"', section)
        self.assertIn('data-autospeichern', section)
        self.assertIn('data-basis-a="2"', section)
        self.assertIn('data-basis-b="1"', section)
        self.assertIn('data-speicherstand', section)
        self.assertNotIn('value="0">Speichern</button>', section)
        self.assertIn('name="finish" value="1">Beenden</button>', section)

    def test_beendete_karte_bleibt_ohne_autospeichern(self):
        from app.main import app
        with get_conn() as conn:
            comp_id, slot_id = _anlegen(conn, status="beendet", score_a=2, score_b=1)
        html = TestClient(app).get(f"/ergebnisse?competition_id={comp_id}&ansicht=raster").text
        section = html[html.index('id="results-section"'):]
        self.assertNotIn(f'data-slot-id="{slot_id}"', section)
        self.assertIn("Bearbeiten", section)

    def test_enter_beendet_das_spiel_nicht(self):
        """Ohne Speichern-Knopf waere "Beenden" der erste Absende-Knopf des
        Formulars - Enter im Eingabefeld darf es nicht ausloesen."""
        html = (ROOT_DIR / "app" / "templates" / "ergebnisse.html").read_text(encoding="utf-8")
        handler = html[html.index("function attachAutospeichern"):html.index("window.addEventListener('beforeunload'")]
        self.assertIn("event.key === 'Enter'", handler)
        self.assertIn("event.preventDefault()", handler)

    def test_ergebnisse_vorlage_behaelt_crlf(self):
        roh = (ROOT_DIR / "app" / "templates" / "ergebnisse.html").read_bytes()
        self.assertEqual(roh.count(b"\n"), roh.count(b"\r\n"))


if __name__ == "__main__":
    unittest.main()
