"""Robustheits-Durchlauf fuer den Planungskern des Einrichtungsassistenten
(ROADMAP Phase 4.7/7.5): 4, 5, 8, 9, 10 und 12 Klassen je Jahrgang, alle
Turniermodi, 1 bis 3 Felder.

Geprueft wird nur, was fuer jeden Plan gelten muss: kein Feld und keine
Klasse doppelt belegt, jede Klasse spielt, Rundenverfahren vollstaendig,
K.-o.-Baum mit passender Spielzahl, Plan im grosszuegigen Zeitfenster
uebernehmbar.

Alle Tests laufen gegen eine temporaere Datenbank (DB_PATH wird
ueberschrieben), niemals gegen data/sportfest.db."""

import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import app.database as database
from app.services import assistent_entwurf_service as es
from app.services.assistent_planung_service import plane

TEAMZAHLEN = (4, 5, 8, 9, 10, 12)
FELDZAHLEN = (1, 2, 3)
# Passt selbst von 07:00 bis 23:30 nicht: 132 Spiele auf einem Feld.
ZU_LANG = {(12, 1, "hin_rueckrunde")}
MODI = (None, "jeder_gegen_jeden", "hin_rueckrunde", "gruppenphase_ko", "ko_runde")


def _ueberschneiden(a, b):
    return a["start_min"] < b["ende_min"] and b["start_min"] < a["ende_min"]


class PlanungskernTeamzahlenTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._patcher = patch.object(database, "DB_PATH", Path(self._tmpdir.name) / "test.db")
        self._patcher.start()
        database.init_db()

    def tearDown(self):
        self._patcher.stop()
        self._tmpdir.cleanup()

    def _stammdaten(self, teams, felder):
        with database.get_conn() as conn:
            conn.execute("DELETE FROM teams")
            conn.execute("DELETE FROM courts")
            for i in range(teams):
                conn.execute("INSERT INTO teams (name, jahrgang, active) VALUES (?, 7, 1)", (f"7-{i + 1:02d}",))
            for i in range(felder):
                conn.execute(
                    "INSERT INTO courts (name, location, active) VALUES (?, 'Turnhalle', 1)", (f"Feld {i + 1}",)
                )
            conn.commit()
            return es.lade_stammdaten(conn)

    def _plane(self, teams, felder, modus):
        stamm = self._stammdaten(teams, felder)
        daten = es.neue_daten()
        daten["grunddaten"].update({"name": "Durchlauf", "datum": "2026-12-01", "start": "07:00", "ende": "23:30"})
        daten["jahrgaenge"] = ["7"]
        daten["sportarten"] = ["Volleyball"]
        daten["matrix"] = {"Volleyball": ["7"]}
        daten["wettbewerbe"] = {"Volleyball|7": {"ort": "Turnhalle"}}
        if modus:
            daten["wettbewerbe"]["Volleyball|7"].update({"modus": modus, "modus_quelle": "nutzer"})
        es.bloecke_vorschlagen(daten, stamm)
        for block in daten["zeitbloecke"]:
            block["ende"] = "23:30"
        with database.get_conn() as conn:
            return plane(conn, daten, es.lade_stammdaten(conn))

    def _pruefe(self, teams, felder, modus):
        plan = self._plane(teams, felder, modus)
        slots = plan["slots"]
        if (teams, felder, modus) in ZU_LANG:
            ueberzug = [h for h in plan["blockierend"] if h["id"].startswith("ueberzug-")]
            self.assertEqual(len(ueberzug), 1)
            self.assertIn("nach Mitternacht", ueberzug[0]["text"])
            aktionen = {v["aktion"] for v in ueberzug[0]["vorschlaege"]}
            self.assertNotIn("block_ende", aktionen, "Vorschlag über Mitternacht")
            self.assertTrue(aktionen, "kein Lösungsvorschlag")
            return
        self.assertEqual(plan["blockierend"], [], [h["titel"] for h in plan["blockierend"]])
        self.assertTrue(slots)

        for i, a in enumerate(slots):
            self.assertIsNotNone(a.get("court_id"), f"Spiel ohne Feld um {a.get('startzeit')}")
            for b in slots[i + 1:]:
                if not _ueberschneiden(a, b):
                    continue
                self.assertNotEqual(a["court_id"], b["court_id"], f"Feld doppelt um {a.get('startzeit')}")
                gemeinsam = ({a.get("team_a"), a.get("team_b")} & {b.get("team_a"), b.get("team_b")}) - {None}
                self.assertFalse(gemeinsam, f"Klasse {gemeinsam} doppelt um {a.get('startzeit')}")

        namen = {f"7-{i + 1:02d}" for i in range(teams)}
        gruppe = [s for s in slots if s.get("phase") in (None, "", "Gruppenphase")]
        echte = [s for s in slots if s.get("team_a") and s.get("team_b")]
        gespielt = {s["team_a"] for s in echte} | {s["team_b"] for s in echte}
        if modus != "ko_runde":
            self.assertEqual(gespielt, namen, "nicht jede Klasse spielt")

        paare = Counter(frozenset((s["team_a"], s["team_b"])) for s in gruppe)
        if modus in (None, "jeder_gegen_jeden") and plan["wettbewerbe"][0]["einstellungen"].get("modus") in (
            None, "jeder_gegen_jeden",
        ) and len(gruppe) == teams * (teams - 1) // 2:
            self.assertTrue(all(n == 1 for n in paare.values()))
        if modus == "jeder_gegen_jeden":
            self.assertEqual(len(gruppe), teams * (teams - 1) // 2)
            self.assertEqual(len(paare), teams * (teams - 1) // 2)
        if modus == "hin_rueckrunde":
            self.assertEqual(len(gruppe), teams * (teams - 1))
            self.assertTrue(all(n == 2 for n in paare.values()))
        if modus == "gruppenphase_ko":
            self.assertTrue(all(n == 1 for n in paare.values()), "Paarung doppelt in der Gruppenphase")
            self.assertTrue(any(s.get("phase") == "Finale" for s in slots))
        if modus == "ko_runde":
            ko = [s for s in slots if s.get("phase") != "Spiel um Platz 3"]
            self.assertEqual(len(ko), teams - 1, "K.-o.-Baum hat nicht n-1 Spiele")
            self.assertEqual(sum(1 for s in slots if s.get("phase") == "Finale"), 1)

    def test_alle_teamzahlen_modi_und_feldzahlen(self):
        for teams in TEAMZAHLEN:
            for felder in FELDZAHLEN:
                for modus in MODI:
                    with self.subTest(teams=teams, felder=felder, modus=modus):
                        self._pruefe(teams, felder, modus)

    def test_zwei_jahrgaenge_teilen_sich_die_felder(self):
        """Schulpokal-Fall: zwei Jahrgänge im selben Block, abwechselnd oder
        gleichzeitig. Kein Feld und keine Klasse darf doppelt belegt sein."""
        for teams in TEAMZAHLEN:
            for ablauf in ("abwechselnd", "gleichzeitig"):
                with self.subTest(teams=teams, ablauf=ablauf):
                    with database.get_conn() as conn:
                        conn.execute("DELETE FROM teams")
                        conn.execute("DELETE FROM courts")
                        for jahrgang in (7, 8):
                            for i in range(teams):
                                conn.execute(
                                    "INSERT INTO teams (name, jahrgang, active) VALUES (?, ?, 1)",
                                    (f"{jahrgang}-{i + 1:02d}", jahrgang),
                                )
                        for i in range(3):
                            conn.execute(
                                "INSERT INTO courts (name, location, active) VALUES (?, 'Turnhalle', 1)",
                                (f"Feld {i + 1}",),
                            )
                        conn.commit()
                        stamm = es.lade_stammdaten(conn)
                    daten = es.neue_daten()
                    daten["grunddaten"].update({"name": "Durchlauf", "datum": "2026-12-01",
                                                "start": "07:00", "ende": "23:30"})
                    daten["jahrgaenge"] = ["7", "8"]
                    daten["sportarten"] = ["Zweifelderball"]
                    daten["matrix"] = {"Zweifelderball": ["7", "8"]}
                    daten["wettbewerbe"] = {
                        "Zweifelderball|7": {"ort": "Turnhalle"},
                        "Zweifelderball|8": {"ort": "Turnhalle"},
                    }
                    es.bloecke_vorschlagen(daten, stamm)
                    self.assertEqual(len(daten["zeitbloecke"]), 1)
                    daten["zeitbloecke"][0].update({"ende": "23:30", "ablauf": ablauf})
                    with database.get_conn() as conn:
                        plan = plane(conn, daten, es.lade_stammdaten(conn))
                    self.assertEqual(plan["blockierend"], [], [h["titel"] for h in plan["blockierend"]])
                    slots = plan["slots"]
                    for jahrgang in ("7", "8"):
                        namen = {f"{jahrgang}-{i + 1:02d}" for i in range(teams)}
                        gespielt = {s[k] for s in slots for k in ("team_a", "team_b") if s.get(k)}
                        self.assertTrue(namen <= gespielt, f"Jahrgang {jahrgang}: nicht jede Klasse spielt")
                    for i, a in enumerate(slots):
                        for b in slots[i + 1:]:
                            if _ueberschneiden(a, b):
                                self.assertNotEqual(a["court_id"], b["court_id"],
                                                    f"Feld doppelt um {a.get('startzeit')}")
                                gemeinsam = ({a.get("team_a"), a.get("team_b")}
                                             & {b.get("team_a"), b.get("team_b")}) - {None}
                                self.assertFalse(gemeinsam, f"Klasse doppelt um {a.get('startzeit')}")


if __name__ == "__main__":
    unittest.main()
