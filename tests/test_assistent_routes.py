"""Routen-Tests fuer den Einrichtungsassistenten (Szenarien S1, S2, S7, S8):
Einstieg, Vorlage mit Abgleich, Zusammenlegen, Entwurf fortsetzen,
idempotentes Uebernehmen, Versionskonflikt und Vorlage aus Veranstaltung.

Immer gegen eine temporaere Datenbank."""

import json
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


class AssistentRoutenTests(unittest.TestCase):
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

    def _bestaetige_warnungen(self, entwurf_id):
        for hinweis in self._plan(entwurf_id)["hinweise"]:
            if hinweis["bestaetigbar"] and not hinweis["bestaetigt"]:
                self._post(f"/assistent/entwurf/{entwurf_id}/hinweis/bestaetigen", {"kennung": hinweis["id"]})

    def _stamm(self):
        with database.get_conn() as conn:
            return es.lade_stammdaten(conn)

    def _count(self, sql):
        with database.get_conn() as conn:
            return conn.execute(sql).fetchone()[0]

    def _schulpokal_entwurf(self):
        location = self._post("/assistent/entwurf/aus-vorlage", {"vorlage": es.SYSTEM_VORLAGE_SCHULPOKAL})
        self.assertTrue(location.endswith("/abgleich"))
        entwurf_id = int(location.split("/")[3])
        self._post(location, {
            "name": "Schulpokal 2026", "datum": "2026-12-01",
            "jahrgaenge": ["7", "8", "9", "10"],
            "sportarten": ["Zweifelderball", "Volleyball", "Tischtennis"],
            "ort_gesendet_Turnhalle": "1", "ort_aktiv_Turnhalle": "1",
            "ort_gesendet_Mensa": "1", "ort_aktiv_Mensa": "1", "ziel": "grunddaten",
        })
        return entwurf_id

    # S1 --------------------------------------------------------------------

    def test_startseite_zeigt_drei_einstiege(self):
        text = self.client.get("/assistent").text
        for label in ("Neue Veranstaltung", "Bestehende Veranstaltung ändern", "Vorlagen verwalten"):
            self.assertIn(label, text)

    def test_schulpokal_aus_vorlage_bis_zur_veranstaltung(self):
        entwurf_id = self._schulpokal_entwurf()
        self.assertFalse(self._entwurf(entwurf_id)["daten"]["abgleich"]["offen"])
        for schritt in es.SCHRITT_KEYS:
            response = self.client.get(f"/assistent/entwurf/{entwurf_id}/{schritt}")
            self.assertEqual(response.status_code, 200, schritt)
        vorschau = self.client.get(f"/assistent/entwurf/{entwurf_id}/vorschau").text
        self.assertIn("von", vorschau)
        self.assertIn("Plan geprüft", vorschau)

        self._bestaetige_warnungen(entwurf_id)
        plan = self._plan(entwurf_id)
        self.assertTrue(plan["uebernahme_moeglich"], [h["titel"] for h in plan["hinweise"]])

        version = self._entwurf(entwurf_id)["version"]
        location = self._post(f"/assistent/entwurf/{entwurf_id}/uebernehmen", {
            "version": str(version), "als_vorlage": "1", "vorlage_name": "Mein Schulpokal",
        })
        self.assertIn("/fertig", location)
        self.assertEqual(self._count("SELECT COUNT(*) FROM events"), 1)
        self.assertGreater(self._count("SELECT COUNT(*) FROM slots"), 0)
        self.assertEqual(self._count("SELECT COUNT(*) FROM competitions WHERE jahrgang = 'mixed'"), 2)
        self.assertEqual(self._count("SELECT COUNT(*) FROM assistent_vorlagen WHERE name = 'Mein Schulpokal'"), 1)
        for url in ("/", "/spielplan", "/tabellen", "/ergebnisse", "/events/1", location):
            self.assertEqual(self.client.get(url).status_code, 200, url)

        # S7: zweites Uebernehmen (Doppelklick/Reload) legt nichts doppelt an
        anzahl = (self._count("SELECT COUNT(*) FROM competitions"), self._count("SELECT COUNT(*) FROM slots"))
        self._post(f"/assistent/entwurf/{entwurf_id}/uebernehmen", {"version": str(version)})
        self.assertEqual(anzahl, (self._count("SELECT COUNT(*) FROM competitions"),
                                  self._count("SELECT COUNT(*) FROM slots")))
        self.assertEqual(self._count("SELECT COUNT(*) FROM events"), 1)

        # S7: Vorlage aus der bestehenden Veranstaltung
        self._post("/assistent/vorlagen/aus-veranstaltung/1", {"name": "Aus Veranstaltung"})
        with database.get_conn() as conn:
            daten = json.loads(conn.execute(
                "SELECT daten FROM assistent_vorlagen WHERE name = 'Aus Veranstaltung'").fetchone()[0])
        self.assertTrue(daten["zeitbloecke"])
        self.assertEqual(len(daten["showspiele"]), 2)
        self.assertNotIn("datum", daten["grunddaten"])

    def test_vorlage_ohne_abgleich_leitet_zum_abgleich(self):
        location = self._post("/assistent/entwurf/aus-vorlage", {"vorlage": es.SYSTEM_VORLAGE_SCHULPOKAL})
        entwurf_id = int(location.split("/")[3])
        response = self.client.get(f"/assistent/entwurf/{entwurf_id}/vorschau", follow_redirects=False)
        self.assertTrue(response.headers["location"].endswith("/abgleich"))
        # Datum ist Pflicht im Abgleich
        location = self._post(f"/assistent/entwurf/{entwurf_id}/abgleich", {"name": "X", "datum": ""})
        self.assertIn("fehler=", location)

    # S7: Versionskonflikt und Fortsetzen ---------------------------------------

    def test_versionskonflikt_und_entwurf_fortsetzen(self):
        entwurf_id = self._schulpokal_entwurf()
        self._bestaetige_warnungen(entwurf_id)
        alte_version = self._entwurf(entwurf_id)["version"]
        self.client.get(f"/assistent/entwurf/{entwurf_id}/wertung")
        self._post(f"/assistent/entwurf/{entwurf_id}/wertung", {"ziel": "bleiben"})
        location = self._post(f"/assistent/entwurf/{entwurf_id}/uebernehmen", {"version": str(alte_version)})
        self.assertIn("fehler=", location)
        self.assertEqual(self._count("SELECT COUNT(*) FROM events"), 0)

        start = self.client.get("/assistent").text
        self.assertIn(f"/assistent/entwurf/{entwurf_id}", start)
        response = self.client.get(f"/assistent/entwurf/{entwurf_id}", follow_redirects=False)
        # Die frühere Seite „Wertung“ ist im Abschluss aufgegangen.
        self.assertIn(f"/assistent/entwurf/{entwurf_id}/abschluss", response.headers["location"])

    def test_blockierende_hinweise_verhindern_uebernahme(self):
        location = self._post("/assistent/entwurf/neu")
        entwurf_id = int(location.split("/")[3])
        version = self._entwurf(entwurf_id)["version"]
        location = self._post(f"/assistent/entwurf/{entwurf_id}/uebernehmen", {"version": str(version)})
        self.assertIn("fehler=", location)
        self.assertEqual(self._count("SELECT COUNT(*) FROM events"), 0)
        self.assertEqual(self._entwurf(entwurf_id)["status"], "offen")

    # S2 + S8: freie Planung mit Zusammenlegung ----------------------------------

    def test_freie_planung_mit_zusammenlegung(self):
        location = self._post("/assistent/entwurf/neu")
        entwurf_id = int(location.split("/")[3])
        basis = f"/assistent/entwurf/{entwurf_id}"
        self._post(f"{basis}/grunddaten", {
            "name": "Oberstufe Volleyball", "datum": "2026-12-02", "event_type": "Sportfest",
            "start": "08:00", "ende": "12:00", "jahrgaenge_gesendet": "1", "jahrgaenge": ["9", "10"],
            "ziel": "sportarten",
        })
        self._post(f"{basis}/sportarten", {"neue_sportart": "Volleyball", "sportart_anzahl": "0"})
        self._post(f"{basis}/sportarten", {
            "sportart_anzahl": "1", "sportart_0": "Volleyball", "matrix_0": ["9", "10"],
        })
        self._post(f"{basis}/sportarten", {
            "sportart_anzahl": "1", "sportart_0": "Volleyball", "matrix_0": ["9", "10"],
            "aktion": "zusammenlegen", "auswahl": ["Volleyball|9", "Volleyball|10"],
        })
        daten = self._entwurf(entwurf_id)["daten"]
        self.assertEqual(daten["zusammenlegungen"], [{"sportart": "Volleyball", "jahrgaenge": ["9", "10"]}])
        daten["wettbewerbe"].setdefault("Volleyball|9+10", {})["ort"] = "Turnhalle"
        with database.get_conn() as conn:
            es.speichere_entwurf(conn, entwurf_id, daten)
            conn.commit()

        for schritt in ("orte", "zeitplan", "vorschau", "abschluss"):
            self.assertEqual(self.client.get(f"{basis}/{schritt}").status_code, 200, schritt)
        self._bestaetige_warnungen(entwurf_id)
        plan = self._plan(entwurf_id)
        self.assertTrue(plan["uebernahme_moeglich"], [h["titel"] for h in plan["hinweise"]])
        version = self._entwurf(entwurf_id)["version"]
        self._post(f"{basis}/uebernehmen", {"version": str(version)})

        with database.get_conn() as conn:
            competitions = conn.execute("SELECT id, name FROM competitions").fetchall()
            self.assertEqual(len(competitions), 1)
            teams = conn.execute(
                "SELECT COUNT(*) FROM competition_teams WHERE competition_id = ?", (competitions[0]["id"],)
            ).fetchone()[0]
        self.assertEqual(teams, 8)  # ein gemeinsamer Wettbewerb, ein gemeinsamer Sieger

    # Weniger Schritte und Machbarkeits-Abzeichen ------------------------------

    def test_klassen_abwaehlen_ohne_vorher_zu_speichern(self):
        """Die Klassen aller Jahrgaenge stehen schon im Formular (versteckt,
        solange der Jahrgang nicht angehakt ist). So laesst sich ein Jahrgang
        anhaken und im selben Zug eine Klasse abwaehlen."""
        location = self._post("/assistent/entwurf/neu")
        entwurf_id = int(location.split("/")[3])
        basis = f"/assistent/entwurf/{entwurf_id}"
        html = self.client.get(f"{basis}/grunddaten").text
        with database.get_conn() as conn:
            teams = {row["name"]: row["id"] for row in conn.execute("SELECT id, name FROM teams")}
        self.assertIn('data-klassen-jahrgang="9" hidden', html)
        for team_id in teams.values():
            self.assertIn(f'name="teams_aktiv" value="{team_id}" checked', html)

        neunte = [str(teams[name]) for name in ("9a", "9b", "9c", "9d")]
        self._post(f"{basis}/grunddaten", {
            "name": "Turnier", "datum": "2026-12-22", "event_type": "Sportfest",
            "start": "08:00", "ende": "13:00", "jahrgaenge_gesendet": "1", "jahrgaenge": ["9"],
            "teams_gesendet": "1", "teams_gezeigt": [str(i) for i in teams.values()],
            "teams_aktiv": [str(i) for i in teams.values() if i != teams["9c"]],
            "ziel": "bleiben",
        })
        daten = self._entwurf(entwurf_id)["daten"]
        self.assertEqual(daten["jahrgaenge"], ["9"])
        self.assertEqual(daten["teams_abgewaehlt"], [teams["9c"]])
        html = self.client.get(f"{basis}/grunddaten").text
        self.assertIn('data-klassen-jahrgang="9" >', html)
        self.assertNotIn(f'name="teams_aktiv" value="{teams["9c"]}" checked', html)
        self.assertIn(f'name="teams_aktiv" value="{neunte[0]}" checked', html)

    def test_wertung_steht_im_abschluss(self):
        """Die frühere Seite „Wertung“ ist im Abschluss aufgegangen: alte
        Links leiten um, die Einstellungen stehen im Abschluss und gelten beim
        Erstellen mit, ohne vorher zu speichern."""
        self.assertNotIn("wertung", es.SCHRITT_KEYS)
        entwurf_id = self._schulpokal_entwurf()
        basis = f"/assistent/entwurf/{entwurf_id}"
        response = self.client.get(f"{basis}/wertung", follow_redirects=False)
        self.assertTrue(response.headers["location"].endswith("/abschluss"))

        self._bestaetige_warnungen(entwurf_id)
        seite = self.client.get(f"{basis}/abschluss").text
        self.assertIn("<h3", seite)
        self.assertIn("Wertung", seite)
        self.assertIn('name="wb_sieg_0"', seite)
        self.assertIn(f'formaction="{basis}/uebernehmen"', seite)

        daten = self._entwurf(entwurf_id)["daten"]
        keys = [wb["key"] for wb in es.ableiten_wettbewerbe(daten, self._stamm())]
        index = next(i for i, key in enumerate(keys) if key.startswith("Volleyball|9"))
        form = {"version": str(self._entwurf(entwurf_id)["version"]), "wb_anzahl": str(len(keys))}
        for i, key in enumerate(keys):
            form[f"wb_key_{i}"] = key
        form[f"wb_sieg_{index}"] = "5"
        form[f"wb_remis_{index}"] = "2"
        location = self._post(f"{basis}/uebernehmen", form)
        self.assertIn("/fertig", location)
        with database.get_conn() as conn:
            row = conn.execute(
                "SELECT points_win, points_draw FROM competitions WHERE name = 'Volleyball Jahrgang 9'"
            ).fetchone()
        self.assertEqual((row["points_win"], row["points_draw"]), (5, 2))

    def test_wertung_aus_veralteter_seite_wird_nicht_gespeichert(self):
        entwurf_id = self._schulpokal_entwurf()
        self._bestaetige_warnungen(entwurf_id)
        alte_version = self._entwurf(entwurf_id)["version"]
        self._post(f"/assistent/entwurf/{entwurf_id}/abschluss", {"ziel": "bleiben"})
        location = self._post(f"/assistent/entwurf/{entwurf_id}/uebernehmen", {
            "version": str(alte_version), "wb_anzahl": "1",
            "wb_key_0": "Volleyball|9", "wb_sieg_0": "9", "wb_remis_0": "0",
        })
        self.assertIn("fehler=", location)
        self.assertEqual(self._count("SELECT COUNT(*) FROM events"), 0)
        einstellung = self._entwurf(entwurf_id)["daten"].get("wettbewerbe", {}).get("Volleyball|9", {})
        self.assertNotEqual(einstellung.get("punkte_sieg"), 9)

    def test_machbarkeits_abzeichen_in_jedem_schritt(self):
        location = self._post("/assistent/entwurf/neu")
        entwurf_id = int(location.split("/")[3])
        seite = self.client.get(f"/assistent/entwurf/{entwurf_id}/grunddaten").text
        self.assertIn('class="wiz-machbar is-block"', seite)
        self.assertIn("Noch nicht machbar", seite)

        entwurf_id = self._schulpokal_entwurf()
        basis = f"/assistent/entwurf/{entwurf_id}"
        offene = len(self._plan(entwurf_id)["offene_warnungen"])
        self.assertGreater(offene, 0)
        for schritt in ("grunddaten", "sportarten", "orte", "zeitplan", "vorschau", "abschluss"):
            seite = self.client.get(f"{basis}/{schritt}").text
            self.assertIn('class="wiz-machbar is-warn"', seite, schritt)
            self.assertIn(f"{offene} Warnung", seite, schritt)
            self.assertIn(f'href="{basis}/vorschau"', seite, schritt)

        self._bestaetige_warnungen(entwurf_id)
        seite = self.client.get(f"{basis}/sportarten").text
        self.assertIn('class="wiz-machbar is-ok"', seite)
        self.assertRegex(seite, r"is-ok\"[^>]*><svg class=\"icon\"[^>]*>.*?</svg> Machbar")

    def test_vorlage_loeschen_braucht_bestaetigung(self):
        with database.get_conn() as conn:
            vorlage_id = es.speichere_vorlage(conn, "Testvorlage", es.system_vorlage_schulpokal())
            conn.commit()
        self._post(f"/assistent/vorlagen/{vorlage_id}/loeschen", {})
        self.assertEqual(self._count("SELECT COUNT(*) FROM assistent_vorlagen"), 1)
        self._post(f"/assistent/vorlagen/{vorlage_id}/loeschen", {"bestaetigt": "1"})
        self.assertEqual(self._count("SELECT COUNT(*) FROM assistent_vorlagen"), 0)


if __name__ == "__main__":
    unittest.main()
