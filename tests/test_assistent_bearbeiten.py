"""Bestehende Veranstaltung im Einrichtungsassistenten bearbeiten und
„Vorjahr übernehmen“ (ROADMAP Phase 7.5).

Geprüft wird: Entwurf aus der Veranstaltung behält Name, Datum und Klassen;
unverändertes Übernehmen ändert nichts an Wettbewerben und Spielanzahl;
Änderungen ersetzen nur ungespielte Spiele; Blöcke mit Ergebnissen bleiben
unangetastet; entfernte Wettbewerbe ohne Ergebnisse werden gelöscht, mit
Ergebnissen blockiert; ein zweiter Klick öffnet denselben Entwurf.

Immer gegen eine temporäre Datenbank."""

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
from app.services import assistent_entwurf_service as es
from app.services.assistent_planung_service import plane


class AssistentBearbeitenTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._patcher = patch.object(database, "DB_PATH", Path(self._tmpdir.name) / "test.db")
        self._patcher.start()
        from app.main import app as fastapi_app
        self._client_cm = TestClient(fastapi_app)
        self.client = self._client_cm.__enter__()
        with database.get_conn() as conn:
            for jahrgang, anzahl in ((7, 5), (8, 4), (9, 4), (10, 4)):
                for i in range(anzahl):
                    conn.execute("INSERT INTO teams (name, jahrgang, active) VALUES (?, ?, 1)",
                                 (f"{jahrgang}{'abcdef'[i]}", jahrgang))
            for i in range(1, 4):
                conn.execute("INSERT INTO courts (name, location, active) VALUES (?, 'Turnhalle', 1)",
                             (f"Feld {i}",))
            for i in range(1, 13):
                conn.execute("INSERT INTO courts (name, location, active) VALUES (?, 'Mensa', 1)",
                             (f"Platte {i}",))
            conn.commit()
        self.event_id = self._schulpokal_anlegen()

    def tearDown(self):
        self._client_cm.__exit__(None, None, None)
        self._patcher.stop()
        self._tmpdir.cleanup()

    # Hilfen ----------------------------------------------------------------

    def _post(self, url, data=None):
        response = self.client.post(url, data=data or {}, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text[:500])
        return response.headers["location"]

    def _entwurf(self, entwurf_id):
        with database.get_conn() as conn:
            return es.lade_entwurf(conn, entwurf_id)

    def _plan(self, entwurf_id):
        with database.get_conn() as conn:
            entwurf = es.lade_entwurf(conn, entwurf_id)
            return plane(conn, entwurf["daten"], es.lade_stammdaten(conn))

    def _speichere(self, entwurf_id, daten):
        with database.get_conn() as conn:
            es.speichere_entwurf(conn, entwurf_id, daten)
            conn.commit()

    def _bestaetige_warnungen(self, entwurf_id):
        for hinweis in self._plan(entwurf_id)["hinweise"]:
            if hinweis["bestaetigbar"] and not hinweis["bestaetigt"]:
                self._post(f"/assistent/entwurf/{entwurf_id}/hinweis/bestaetigen", {"kennung": hinweis["id"]})

    def _uebernehmen(self, entwurf_id):
        self._bestaetige_warnungen(entwurf_id)
        plan = self._plan(entwurf_id)
        self.assertTrue(plan["uebernahme_moeglich"], [h["titel"] for h in plan["blockierend"]])
        version = self._entwurf(entwurf_id)["version"]
        location = self._post(f"/assistent/entwurf/{entwurf_id}/uebernehmen", {"version": str(version)})
        self.assertIn("/fertig", location)
        return location

    def _rows(self, sql, params=()):
        with database.get_conn() as conn:
            return conn.execute(sql, params).fetchall()

    def _count(self, sql, params=()):
        return self._rows(sql, params)[0][0]

    def _schulpokal_anlegen(self):
        location = self._post("/assistent/entwurf/aus-vorlage", {"vorlage": es.SYSTEM_VORLAGE_SCHULPOKAL})
        entwurf_id = int(location.split("/")[3])
        self._post(location, {
            "name": "Schulpokal 2026", "datum": "2026-12-01",
            "jahrgaenge": ["7", "8", "9", "10"],
            "sportarten": ["Zweifelderball", "Volleyball", "Tischtennis"],
            "ort_gesendet_Turnhalle": "1", "ort_aktiv_Turnhalle": "1",
            "ort_gesendet_Mensa": "1", "ort_aktiv_Mensa": "1", "ziel": "grunddaten",
        })
        self._uebernehmen(entwurf_id)
        return self._entwurf(entwurf_id)["event_id"]

    def _bearbeiten(self):
        location = self._post(f"/assistent/entwurf/bearbeiten/{self.event_id}")
        entwurf_id = int(location.split("/")[3])
        return entwurf_id

    def _stand(self):
        return {
            "events": self._count("SELECT COUNT(*) FROM events"),
            "competitions": sorted(
                (row["id"], row["name"]) for row in self._rows("SELECT id, name FROM competitions")
            ),
            "spiele": self._count("SELECT COUNT(*) FROM slots WHERE slot_typ = 'Spiel'"),
            "programmpunkte": self._count("SELECT COUNT(*) FROM event_programmpunkte"),
        }

    # Tests -----------------------------------------------------------------

    def test_entwurf_behaelt_name_datum_und_ordnet_bestand_zu(self):
        entwurf_id = self._bearbeiten()
        daten = self._entwurf(entwurf_id)["daten"]
        self.assertEqual(daten["grunddaten"]["name"], "Schulpokal 2026")
        self.assertEqual(daten["grunddaten"]["datum"], "2026-12-01")
        self.assertEqual(daten["grunddaten"]["event_type"], "Schulpokal")
        self.assertIsNone(daten["abgleich"])
        self.assertEqual(daten["bestand"]["event_id"], self.event_id)
        anzahl_wettbewerbe = self._count(
            "SELECT COUNT(*) FROM competitions WHERE final_source_ids IS NULL OR final_source_ids = ''")
        self.assertEqual(len(daten["bestand"]["wettbewerbe"]), anzahl_wettbewerbe)
        self.assertEqual(len(daten["bestand"]["showspiele"]), 2)
        self.assertIn("(bearbeiten)", self._entwurf(entwurf_id)["titel"])
        # Zweiter Klick öffnet denselben Entwurf
        self.assertEqual(self._bearbeiten(), entwurf_id)
        # Alle Schritte laden, Abschluss zeigt „Änderungen übernehmen“
        for schritt in es.SCHRITT_KEYS:
            self.assertEqual(self.client.get(f"/assistent/entwurf/{entwurf_id}/{schritt}").status_code, 200)
        self.assertIn("Änderungen übernehmen", self.client.get(f"/assistent/entwurf/{entwurf_id}/abschluss").text)

    def test_unveraendert_uebernehmen_aendert_nichts_und_kollidiert_nicht_mit_sich_selbst(self):
        vorher = self._stand()
        entwurf_id = self._bearbeiten()
        plan = self._plan(entwurf_id)
        # Die eigenen alten Spiele zählen nicht als fremde Belegung.
        self.assertFalse([h for h in plan["hinweise"] if h["id"].startswith("fremd-")],
                         [h["titel"] for h in plan["hinweise"] if h["id"].startswith("fremd-")])
        self.assertFalse([h for h in plan["hinweise"] if h["id"] == "name-doppelt"])
        slot_ids_vorher = sorted(r["id"] for r in self._rows("SELECT id FROM slots"))
        punkt_ids_vorher = sorted(r["id"] for r in self._rows("SELECT id FROM event_programmpunkte"))
        location = self._uebernehmen(entwurf_id)
        seite = self.client.get(location).text
        self.assertIn("Änderungen übernommen", seite)
        self.assertIn("8 unverändert", seite)
        # Unverändert heißt wirklich unverändert: gleiche Spiele, gleiche ids.
        self.assertEqual(slot_ids_vorher, sorted(r["id"] for r in self._rows("SELECT id FROM slots")))
        self.assertEqual(punkt_ids_vorher,
                         sorted(r["id"] for r in self._rows("SELECT id FROM event_programmpunkte")))
        nachher = self._stand()
        self.assertEqual(vorher["events"], nachher["events"])
        self.assertEqual(vorher["spiele"], nachher["spiele"])
        self.assertEqual(vorher["programmpunkte"], nachher["programmpunkte"])
        self.assertEqual(vorher["competitions"], nachher["competitions"])
        self.assertEqual(self._count("SELECT COUNT(*) FROM competitions WHERE jahrgang = 'mixed'"), 2)
        for url in ("/", "/spielplan", "/tabellen", f"/events/{self.event_id}"):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_klasse_abwaehlen_ersetzt_nur_diesen_spielplan(self):
        entwurf_id = self._bearbeiten()
        daten = self._entwurf(entwurf_id)["daten"]
        team_7e = self._rows("SELECT id FROM teams WHERE name = '7e'")[0]["id"]
        daten["teams_abgewaehlt"] = [team_7e]
        self._speichere(entwurf_id, daten)
        self._uebernehmen(entwurf_id)
        self.assertEqual(self._count(
            "SELECT COUNT(*) FROM slots WHERE team_a_id = ? OR team_b_id = ?", (team_7e, team_7e)), 0)
        self.assertEqual(self._count(
            "SELECT COUNT(*) FROM competition_teams WHERE team_id = ?", (team_7e,)), 0)
        # 7er-Wettbewerb hat jetzt 4 statt 5 Klassen: 6 statt 10 Spiele.
        zwb7 = self._rows("SELECT id FROM competitions WHERE name = 'Zweifelderball Jahrgang 7'")[0]["id"]
        self.assertEqual(self._count("SELECT COUNT(*) FROM slots WHERE competition_id = ?", (zwb7,)), 6)
        self.assertEqual(self._count("SELECT COUNT(*) FROM events"), 1)

    def test_block_mit_ergebnis_bleibt_unveraendert(self):
        zwb7 = self._rows("SELECT id FROM competitions WHERE name = 'Zweifelderball Jahrgang 7'")[0]["id"]
        erstes = self._rows(
            "SELECT id FROM slots WHERE competition_id = ? ORDER BY startzeit LIMIT 1", (zwb7,))[0]["id"]
        with database.get_conn() as conn:
            conn.execute("UPDATE slots SET status = 'beendet', score_a = 2, score_b = 1 WHERE id = ?", (erstes,))
            conn.commit()
        zwb_slots_vorher = sorted(tuple(r) for r in self._rows(
            """
            SELECT s.id, s.startzeit, s.court_id FROM slots s JOIN competitions c ON c.id = s.competition_id
            WHERE c.sportart = 'Zweifelderball' AND c.jahrgang != 'mixed'
            """))
        vb_ids_vorher = {r["id"] for r in self._rows(
            "SELECT s.id FROM slots s JOIN competitions c ON c.id = s.competition_id "
            "WHERE c.sportart = 'Volleyball' AND c.jahrgang != 'mixed'")}

        entwurf_id = self._bearbeiten()
        daten = self._entwurf(entwurf_id)["daten"]
        for block in daten["zeitbloecke"]:
            block["spielzeit"] = max(4, int(block["spielzeit"]) - 1)
        self._speichere(entwurf_id, daten)
        plan = self._plan(entwurf_id)
        self.assertTrue([h for h in plan["hinweise"] if h["id"].startswith("bestand-gesperrt-")])
        self.assertIn("Zweifelderball Jahrgang 7", plan["bestand"]["gesperrt_namen"])
        self.assertIn("Zweifelderball Jahrgang 8", plan["bestand"]["gesperrt_namen"])
        for schritt in ("vorschau", "abschluss"):
            seite = self.client.get(f"/assistent/entwurf/{entwurf_id}/{schritt}")
            self.assertEqual(seite.status_code, 200, schritt)
        self.assertIn("hat schon begonnen", self.client.get(f"/assistent/entwurf/{entwurf_id}/vorschau").text)
        self._uebernehmen(entwurf_id)

        zwb_slots_nachher = sorted(tuple(r) for r in self._rows(
            """
            SELECT s.id, s.startzeit, s.court_id FROM slots s JOIN competitions c ON c.id = s.competition_id
            WHERE c.sportart = 'Zweifelderball' AND c.jahrgang != 'mixed'
            """))
        self.assertEqual(zwb_slots_vorher, zwb_slots_nachher)
        self.assertEqual(self._rows("SELECT status FROM slots WHERE id = ?", (erstes,))[0]["status"], "beendet")
        vb_ids_nachher = {r["id"] for r in self._rows(
            "SELECT s.id FROM slots s JOIN competitions c ON c.id = s.competition_id "
            "WHERE c.sportart = 'Volleyball' AND c.jahrgang != 'mixed'")}
        self.assertTrue(vb_ids_nachher)
        self.assertFalse(vb_ids_vorher & vb_ids_nachher, "Volleyball sollte neu geplant sein")
        # Das Jahrgangsfinale 7/8 hängt am gesperrten Block und bleibt bestehen.
        self.assertEqual(self._count(
            "SELECT COUNT(*) FROM competitions WHERE name LIKE 'Zweifelderball Jahrgangsfinale%'"), 1)

    def test_wettbewerb_entfernen_ohne_und_mit_ergebnis(self):
        entwurf_id = self._bearbeiten()
        daten = self._entwurf(entwurf_id)["daten"]
        daten["matrix"]["Tischtennis"] = ["9"]
        self._speichere(entwurf_id, daten)
        plan = self._plan(entwurf_id)
        self.assertTrue(any("wird gelöscht" in name or "wird gelöscht" in h["titel"]
                            for h in plan["hinweise"] for name in [h["titel"]]))
        tt10 = [r["id"] for r in self._rows("SELECT id FROM competitions WHERE name LIKE 'Tischtennis % Jahrgang 10'")]
        self.assertTrue(tt10)

        # Mit Ergebnis: blockiert
        with database.get_conn() as conn:
            conn.execute(
                "UPDATE slots SET status = 'beendet' WHERE id = (SELECT id FROM slots WHERE competition_id = ? LIMIT 1)",
                (tt10[0],),
            )
            conn.commit()
        plan = self._plan(entwurf_id)
        self.assertFalse(plan["uebernahme_moeglich"])
        self.assertTrue([h for h in plan["blockierend"] if "hat schon Ergebnisse" in h["titel"]])

        # Ergebnis zurückgenommen: gelöscht beim Übernehmen
        with database.get_conn() as conn:
            conn.execute("UPDATE slots SET status = 'geplant' WHERE competition_id = ?", (tt10[0],))
            conn.commit()
        self._uebernehmen(entwurf_id)
        for cid in tt10:
            self.assertEqual(self._count("SELECT COUNT(*) FROM competitions WHERE id = ?", (cid,)), 0)
            self.assertEqual(self._count("SELECT COUNT(*) FROM slots WHERE competition_id = ?", (cid,)), 0)
        self.assertEqual(self._count("SELECT COUNT(*) FROM competitions WHERE name LIKE 'Tischtennis % Jahrgang 9'"), 2)

    def test_vorjahr_uebernehmen_startet_neue_veranstaltung_mit_abgleich(self):
        location = self._post("/assistent/entwurf/bestehend", {"event_id": str(self.event_id), "aktion": "vorjahr"})
        self.assertTrue(location.endswith("/abgleich"))
        entwurf_id = int(location.split("/")[3])
        daten = self._entwurf(entwurf_id)["daten"]
        self.assertNotIn("bestand", daten)
        self.assertEqual(daten["grunddaten"]["name"], "")
        self.assertTrue(daten["abgleich"]["offen"])
        self.assertEqual(set(daten["sportarten"]), {"Zweifelderball", "Volleyball", "Tischtennis"})
        self.assertEqual(len(daten["showspiele"]), 2)
        # Kachel „Bearbeiten“ nutzt dieselbe Route wie die Veranstaltungsseite
        location = self._post("/assistent/entwurf/bestehend", {"event_id": str(self.event_id), "aktion": "bearbeiten"})
        self.assertEqual(int(location.split("/")[3]), self._bearbeiten())
        start = self.client.get("/assistent").text
        self.assertIn("Bestehende Veranstaltung ändern", start)
        self.assertIn("Weiter ändern", start)  # offener Bearbeiten-Entwurf
        self.assertIn(f'value="event:{self.event_id}"', start)
        self.assertIn(f"/assistent/entwurf/bearbeiten/{self.event_id}",
                      self.client.get(f"/events/{self.event_id}").text)


    def test_startpunkt_auswahl(self):
        location = self._post("/assistent/entwurf/start", {"start": "leer"})
        self.assertTrue(location.endswith("/grunddaten"))
        location = self._post("/assistent/entwurf/start", {"start": es.SYSTEM_VORLAGE_SCHULPOKAL})
        self.assertTrue(location.endswith("/abgleich"))
        location = self._post("/assistent/entwurf/start", {"start": f"event:{self.event_id}"})
        self.assertTrue(location.endswith("/abgleich"))
        daten = self._entwurf(int(location.split("/")[3]))["daten"]
        self.assertEqual(daten["vorlage"]["quelle"], f"veranstaltung:{self.event_id}")
        location = self._post("/assistent/entwurf/start", {"start": "event:999"})
        self.assertIn("einstieg_fehler", location)
        # Neue Entwürfe stehen unter „Entwurf fortsetzen“, Bearbeiten-Entwürfe nicht.
        self._bearbeiten()
        start = self.client.get("/assistent").text
        fortsetzen = start.split('id="entwuerfe"', 1)[1]
        self.assertNotIn("(bearbeiten)", fortsetzen)

    def test_veranstaltungsseite_hat_plan_aendern(self):
        seite = self.client.get(f"/events/{self.event_id}").text
        self.assertIn("Plan ändern", seite)
        self.assertIn("Name &amp; Beschreibung", seite)


if __name__ == "__main__":
    unittest.main()
