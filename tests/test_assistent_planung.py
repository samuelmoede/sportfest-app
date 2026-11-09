"""Service-Tests fuer den neuen Einrichtungsassistenten: Wettbewerbe aus der
Matrix ableiten, Zusammenlegen/Trennen, Taktung der Zeitbloecke und die
Machbarkeitspruefung (Szenarien S2-S5, S8 aus ASSISTENT_SPECS_FINAL.md).

Alle Tests laufen gegen eine temporaere Datenbank (DB_PATH wird
ueberschrieben), niemals gegen data/sportfest.db."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import app.database as database
from app.services import assistent_entwurf_service as es
from app.services.assistent_planung_service import baue_spielraster, plane, takte_block


def _lege_stammdaten_an(conn):
    for jahrgang, anzahl in ((7, 5), (8, 4), (9, 4), (10, 4)):
        for i in range(anzahl):
            conn.execute(
                "INSERT INTO teams (name, jahrgang, active) VALUES (?, ?, 1)",
                (f"{jahrgang}{'abcdef'[i]}", jahrgang),
            )
    for i in range(1, 4):
        conn.execute("INSERT INTO courts (name, location, active) VALUES (?, 'Turnhalle', 1)", (f"Feld {i}",))
    for i in range(1, 7):
        conn.execute("INSERT INTO courts (name, location, active) VALUES (?, 'Mensa', 1)", (f"Platte {i}",))
    conn.commit()


def _keine_doppelten_felder(testcase, slots):
    belegung = {}
    for slot in slots:
        for anderer in belegung.get(slot["court_id"], []):
            testcase.assertFalse(
                slot["start_min"] < anderer["ende_min"] and anderer["start_min"] < slot["ende_min"],
                f"Feld {slot['court_id']} doppelt belegt um {slot['startzeit']}",
            )
        belegung.setdefault(slot["court_id"], []).append(slot)


class AssistentPlanungTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._patcher = patch.object(database, "DB_PATH", Path(self._tmpdir.name) / "test.db")
        self._patcher.start()
        database.init_db()
        with database.get_conn() as conn:
            _lege_stammdaten_an(conn)
            self.stamm = es.lade_stammdaten(conn)

    def tearDown(self):
        self._patcher.stop()
        self._tmpdir.cleanup()

    def _freie_daten(self):
        daten = es.neue_daten()
        daten["grunddaten"].update({"name": "Sportfest Test", "datum": "2026-12-01",
                                    "start": "08:00", "ende": "13:00"})
        daten["jahrgaenge"] = ["9", "10"]
        daten["sportarten"] = ["Volleyball"]
        daten["matrix"] = {"Volleyball": ["9", "10"]}
        daten["wettbewerbe"] = {
            "Volleyball|9": {"ort": "Turnhalle"},
            "Volleyball|10": {"ort": "Turnhalle"},
        }
        return daten

    def _plane(self, daten):
        with database.get_conn() as conn:
            return plane(conn, daten, es.lade_stammdaten(conn))

    # S2 -----------------------------------------------------------------

    def test_matrix_ergibt_je_jahrgang_einen_wettbewerb(self):
        wettbewerbe = es.ableiten_wettbewerbe(self._freie_daten(), self.stamm)
        self.assertEqual([wb["key"] for wb in wettbewerbe], ["Volleyball|9", "Volleyball|10"])
        self.assertEqual(len(wettbewerbe[0]["teams"]), 4)

    def test_zusammenlegen_und_trennen(self):
        daten = self._freie_daten()
        es.bloecke_vorschlagen(daten, self.stamm)
        neuer_key = es.zusammenlegen(daten, "Volleyball", ["Volleyball|9", "Volleyball|10"])
        self.assertEqual(neuer_key, "Volleyball|9+10")
        wettbewerbe = es.ableiten_wettbewerbe(daten, self.stamm)
        self.assertEqual(len(wettbewerbe), 1)
        self.assertTrue(wettbewerbe[0]["zusammengelegt"])
        self.assertEqual(len(wettbewerbe[0]["teams"]), 8)
        self.assertEqual(daten["zeitbloecke"][0]["wettbewerbe"], ["Volleyball|9+10"])
        # Einstellungen (Ort) gehen beim Zusammenlegen nicht verloren
        self.assertEqual(wettbewerbe[0]["einstellungen"]["ort"], "Turnhalle")

        es.trennen(daten, neuer_key)
        wettbewerbe = es.ableiten_wettbewerbe(daten, self.stamm)
        self.assertEqual([wb["key"] for wb in wettbewerbe], ["Volleyball|9", "Volleyball|10"])
        self.assertEqual(sorted(daten["zeitbloecke"][0]["wettbewerbe"]),
                         ["Volleyball|10", "Volleyball|9"])

    def test_zusammenlegen_nur_innerhalb_einer_sportart(self):
        daten = self._freie_daten()
        with self.assertRaises(ValueError):
            es.zusammenlegen(daten, "Volleyball", ["Volleyball|9", "Tischtennis|10"])
        with self.assertRaises(ValueError):
            es.zusammenlegen(daten, "Volleyball", ["Volleyball|9"])

    def test_abgewaehlte_sportart_bleibt_im_entwurf(self):
        daten = self._freie_daten()
        daten["sportarten"] = []
        self.assertEqual(es.ableiten_wettbewerbe(daten, self.stamm), [])
        self.assertEqual(daten["matrix"]["Volleyball"], ["9", "10"])

    # Taktung -------------------------------------------------------------

    def test_takte_block_wechselt_ab_und_belegt_kein_feld_doppelt(self):
        def eintrag(eintrag_id, jahrgang):
            runden = [[{"team_a": "A", "team_b": "B", "feld": 1}, {"team_a": "C", "team_b": "D", "feld": 2}]] * 3
            return {"id": eintrag_id, "jahrgaenge": [jahrgang], "felder": [1, 2], "runden": runden}

        slots, schritte = takte_block(480, 10, 2, [[eintrag("a", "9")], [eintrag("b", "10")]], [])
        self.assertEqual([index for index, _ in schritte], [0, 1, 0, 1, 0, 1])
        self.assertEqual(len(slots), 12)
        zeiten = {}
        for slot in slots:
            key = (slot["court_id"], slot["start_min"])
            self.assertNotIn(key, zeiten)
            zeiten[key] = slot

    def test_takte_block_ueberspringt_programmpunkte(self):
        runden = [[{"team_a": "A", "team_b": "B", "feld": 1}]] * 3
        gruppe = [{"id": "a", "jahrgaenge": ["9"], "felder": [1], "runden": runden}]
        puffer = [{"start": 492, "ende": 510, "jahrgaenge": ["9"]}]
        slots, _ = takte_block(480, 10, 2, [gruppe], puffer)
        for slot in slots:
            self.assertFalse(slot["start_min"] < 510 and slot["ende_min"] > 492,
                             f"Spiel {slot['start_min']} liegt im Puffer")
        # Puffer fuer einen anderen Jahrgang blockiert nicht
        slots_frei, _ = takte_block(480, 10, 2, [gruppe], [{"start": 492, "ende": 510, "jahrgaenge": ["7"]}])
        self.assertEqual([s["start_min"] for s in slots_frei], [480, 492, 504])

    # S8: freie Planung ohne Vorlage ---------------------------------------

    def test_freie_planung_ohne_vorlage_ist_uebernehmbar(self):
        daten = self._freie_daten()
        self.assertTrue(es.bloecke_vorschlagen(daten, self.stamm))
        self.assertEqual(len(daten["zeitbloecke"]), 1)
        plan = self._plane(daten)
        self.assertEqual(plan["blockierend"], [], [h["titel"] for h in plan["blockierend"]])
        self.assertTrue(plan["uebernahme_moeglich"])
        self.assertGreater(len(plan["slots"]), 0)
        _keine_doppelten_felder(self, plan["slots"])
        # automatisch gewaehlter Modus wird begruendet, nicht still gesetzt
        for wb in plan["wettbewerbe"]:
            self.assertTrue(wb["einstellungen"].get("modus") is None)

    # S5: Zeitfenster zu knapp ----------------------------------------------

    def test_ueberzug_blockiert_mit_vorschlaegen_und_modus_bleibt(self):
        daten = self._freie_daten()
        es.bloecke_vorschlagen(daten, self.stamm)
        daten["zeitbloecke"][0]["ende"] = "08:30"
        daten["wettbewerbe"]["Volleyball|9"].update({"modus": "jeder_gegen_jeden", "modus_quelle": "vorlage"})
        plan = self._plane(daten)
        ueberzug = [h for h in plan["blockierend"] if h["id"].startswith("ueberzug-")]
        self.assertEqual(len(ueberzug), 1)
        self.assertIn("Turniermodus aus der Vorlage", ueberzug[0]["titel"])
        aktionen = {v["aktion"] for v in ueberzug[0]["vorschlaege"]}
        self.assertIn("block_ende", aktionen)
        self.assertFalse(plan["uebernahme_moeglich"])
        # Nichts wird automatisch angewendet
        self.assertEqual(daten["wettbewerbe"]["Volleyball|9"]["modus"], "jeder_gegen_jeden")
        self.assertEqual(daten["zeitbloecke"][0]["ende"], "08:30")

        vorschlag = next(v for v in ueberzug[0]["vorschlaege"] if v["aktion"] == "block_ende")
        es.wende_vorschlag_an(daten, vorschlag["aktion"], vorschlag["werte"])
        plan = self._plane(daten)
        self.assertFalse([h for h in plan["blockierend"] if h["id"].startswith("ueberzug-")])

    # S4: Feld blockiert, Klasse ist bestaetigbar ------------------------------

    def test_feldkonflikt_blockiert_klassenkonflikt_ist_warnung(self):
        daten = self._freie_daten()
        daten["sportarten"] = ["Volleyball", "Tischtennis"]
        daten["matrix"]["Tischtennis"] = ["9"]
        daten["wettbewerbe"]["Tischtennis|9"] = {"ort": "Mensa"}
        es.bloecke_vorschlagen(daten, self.stamm)
        plan = self._plane(daten)
        warnungen = [h for h in plan["hinweise"] if h["id"].startswith("klasse-")]
        self.assertEqual(len(warnungen), 1)
        self.assertEqual(warnungen[0]["stufe"], "warnung")
        self.assertTrue(warnungen[0]["bestaetigbar"])
        self.assertFalse(plan["uebernahme_moeglich"])

        daten["akzeptierte_hinweise"] = [warnungen[0]["id"]]
        plan = self._plane(daten)
        self.assertTrue(plan["uebernahme_moeglich"])

        # zweiter Block auf denselben Feldern zur selben Zeit: blockierend
        es.neuer_block(daten, "Turnhalle", ["Volleyball|10"])
        daten["zeitbloecke"][0]["wettbewerbe"] = ["Volleyball|9"]
        plan = self._plane(daten)
        feld = [h for h in plan["hinweise"] if h["id"].startswith("feld-")]
        self.assertTrue(feld)
        self.assertTrue(all(h["stufe"] == "blockierend" and not h["bestaetigbar"] for h in feld))
        self.assertTrue(any(v["aktion"] == "block_start" for h in feld for v in h["vorschlaege"]))

    # S3: Vorlage gegen veraenderte Stammdaten ---------------------------------

    def test_vorlage_meldet_inaktiven_ort_und_fehlende_felder(self):
        vorlage = es.system_vorlage_schulpokal()
        with database.get_conn() as conn:
            conn.execute("UPDATE courts SET active = 0 WHERE location = 'Mensa'")
            conn.commit()
            stamm = es.lade_stammdaten(conn)
        daten = es.entwurf_aus_vorlage(vorlage, stamm, es.SYSTEM_VORLAGE_SCHULPOKAL, "Schulpokal")
        self.assertTrue(daten["abgleich"]["offen"])
        self.assertIn("Mensa", daten["abgleich"]["inaktive_orte"])
        self.assertEqual(daten["grunddaten"]["name"], "")
        self.assertEqual(daten["grunddaten"]["datum"], "")
        plan = self._plane(daten)
        self.assertIn("abgleich", {h["id"] for h in plan["blockierend"]})

    def test_vorlage_aus_daten_speichert_feldnamen_statt_ids(self):
        daten = self._freie_daten()
        daten["wettbewerbe"]["Volleyball|9"]["felder"] = [self.stamm["courts"][0]["id"]]
        vorlage = es.vorlage_aus_daten(daten, self.stamm)
        self.assertNotIn("name", vorlage["grunddaten"])
        self.assertNotIn("datum", vorlage["grunddaten"])
        self.assertTrue(vorlage["wettbewerbe"]["Volleyball|9"]["felder_namen"])
        wieder = es.entwurf_aus_vorlage(vorlage, self.stamm, "eigen", "Test")
        self.assertEqual(wieder["wettbewerbe"]["Volleyball|9"]["felder"], [self.stamm["courts"][0]["id"]])

    # Spielplan-Vorschau als Zeitraster (Zeit x Feld) ------------------------

    def test_spielraster_zeigt_gleichzeitige_spiele_in_einer_zeile(self):
        def slot(start, court_id, name, a, b):
            return {"start_min": start, "startzeit": f"{start // 60:02d}:{start % 60:02d}",
                    "endzeit": f"{(start + 7) // 60:02d}:{(start + 7) % 60:02d}",
                    "court_id": court_id, "court_name": name, "team_a": a, "team_b": b,
                    "phase": "Gruppenphase"}

        eintrag = {
            "jahrgaenge": ["10"],
            "block": {"start_min": 670, "ende_min": 820},
            "slots": [
                slot(670, 10, "Platte 10", "10a", "10f"),
                slot(670, 2, "Platte 2", "10b", "10e"),
                slot(680, 10, "Platte 10", "10a", "10e"),
            ],
        }
        blocker = [
            {"start": 675, "ende": 680, "jahrgaenge": ["10"], "titel": "Pause", "art": "puffer"},
            {"start": 675, "ende": 680, "jahrgaenge": ["7"], "titel": "Nur JG 7", "art": "sonstiges"},
            {"start": 900, "ende": 910, "jahrgaenge": [], "titel": "Ausserhalb", "art": "sonstiges"},
        ]
        raster = baue_spielraster(eintrag, blocker)
        # Felder natuerlich sortiert: Platte 2 vor Platte 10
        self.assertEqual(raster["felder"], ["Platte 2", "Platte 10"])
        self.assertEqual([z["zeit"] for z in raster["zeilen"]], ["11:10–11:17", "11:15–11:20", "11:20–11:27"])
        erste = raster["zeilen"][0]
        self.assertEqual(erste["runde"], 1)
        self.assertEqual([[s["team_a"] for s in zelle] for zelle in erste["zellen"]], [["10b"], ["10a"]])
        self.assertEqual(raster["zeilen"][1]["punkt"]["titel"], "Pause")
        dritte = raster["zeilen"][2]
        self.assertEqual(dritte["runde"], 2)
        self.assertEqual(dritte["zellen"][0], [])

    def test_plan_liefert_raster_je_turnierwettbewerb(self):
        plan = self._plane(self._freie_daten())
        for eintrag in plan["eintraege"]:
            raster = eintrag["raster"]
            anzahl = sum(len(zelle) for z in raster["zeilen"] if "zellen" in z for zelle in z["zellen"])
            self.assertEqual(anzahl, len(eintrag["slots"]))


if __name__ == "__main__":
    unittest.main()
