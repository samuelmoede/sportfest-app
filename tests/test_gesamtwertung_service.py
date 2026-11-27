"""Wertungsarten der Gesamtwertung (app/services/gesamtwertung_service.py)
und Regeln-Hinweise der Ergebniseingabe (app/services/regeln_service.py)."""
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services import gesamtwertung_service as gw
from app.services.regeln_service import normalisiere_regeln, regeln_hinweise


class ModusTests(unittest.TestCase):
    def test_standard_bleibt_wie_bisher(self):
        self.assertEqual(gw.wirksamer_modus({"event_type": "Schulpokal", "wertung_modus": None}), "spielpunkte")
        self.assertEqual(gw.wirksamer_modus({"event_type": "Sportfest", "wertung_modus": None}), "platzpunkte_wettbewerb")
        self.assertEqual(gw.wirksamer_modus({"event_type": "Sportfest"}), "platzpunkte_wettbewerb")
        self.assertEqual(gw.wirksamer_modus(None), "platzpunkte_wettbewerb")

    def test_gespeicherter_modus_gewinnt(self):
        event = {"event_type": "Schulpokal", "wertung_modus": "platzpunkte_sportart"}
        self.assertEqual(gw.wirksamer_modus(event), "platzpunkte_sportart")
        self.assertEqual(gw.wirksamer_modus({"event_type": "Schulpokal", "wertung_modus": "quatsch"}), "spielpunkte")


class SportartWertungTests(unittest.TestCase):
    def test_beispiel_der_sportfachschaft(self):
        """7 Klassen: Jungen 10a 1. (7), Maedchen 10a 4. (4) = 11."""
        klassen = list(range(1, 8))
        jungen = {k: 8 - k for k in klassen}           # Klasse 1 = 1. Platz = 7
        maedchen_plaetze = {4: 1, 2: 2, 3: 3, 1: 4, 5: 5, 6: 6, 7: 7}
        maedchen = {k: 8 - p for k, p in maedchen_plaetze.items()}
        wertung = gw.sportart_wertung([
            {"label": "Jungen", "punkte": jungen, "plaetze": {k: k for k in klassen}},
            {"label": "Mädchen", "punkte": maedchen, "plaetze": maedchen_plaetze},
        ], klassen)
        self.assertEqual(wertung[1]["summe"], 11)
        self.assertEqual(wertung[2]["summe"], 12)  # 6 + 6
        self.assertEqual(wertung[2]["platz"], 1)
        self.assertEqual(wertung[2]["punkte"], 7)
        self.assertEqual(wertung[1]["platz"], 2)
        self.assertEqual(wertung[1]["punkte"], 6)
        self.assertEqual(
            gw.sportart_detail_text(wertung[1]), "Jungen 1. (7) + Mädchen 4. (4) = 11 → 2. Platz"
        )

    def test_gleiche_summe_gleicher_platz(self):
        wertung = gw.sportart_wertung([
            {"label": "Jungen", "punkte": {1: 3, 2: 2, 3: 1}, "plaetze": {1: 1, 2: 2, 3: 3}},
            {"label": "Mädchen", "punkte": {1: 1, 2: 2, 3: 3}, "plaetze": {1: 3, 2: 2, 3: 1}},
        ], [1, 2, 3])
        self.assertEqual({k: v["platz"] for k, v in wertung.items()}, {1: 1, 2: 1, 3: 1})
        self.assertEqual({v["punkte"] for v in wertung.values()}, {3})

    def test_ohne_teilmannschaften_bleibt_der_platz(self):
        wertung = gw.sportart_wertung(
            [{"label": "Volleyball", "punkte": {1: 2, 2: 3, 3: 1}, "plaetze": {1: 2, 2: 1, 3: 3}}], [1, 2, 3]
        )
        self.assertEqual({k: v["punkte"] for k, v in wertung.items()}, {1: 2, 2: 3, 3: 1})
        self.assertEqual(gw.sportart_detail_text(wertung[1]), "")

    def test_fehlende_teilmannschaft_zaehlt_null(self):
        wertung = gw.sportart_wertung([
            {"label": "Jungen", "punkte": {1: 2, 2: 1}, "plaetze": {1: 1, 2: 2}},
            {"label": "Mädchen", "punkte": {2: 2}, "plaetze": {2: 1}},
        ], [1, 2])
        self.assertEqual(wertung[2]["summe"], 3)
        self.assertEqual(wertung[1]["summe"], 2)
        self.assertIn("Mädchen –", gw.sportart_detail_text(wertung[1]))


def _row(team_id, team, total, placements, diff=0):
    return {
        "team_id": team_id, "team": team, "total_points": total, "placements": placements,
        "placement_counts": Counter(placements), "tournament_score_diff": diff,
    }


def _spiel(a, b, score_a, score_b):
    return {"team_a_id": a, "team_b_id": b, "score_a": score_a, "score_b": score_b,
            "points_win": 2, "points_draw": 1, "points_loss": 0}


class GleichstandTests(unittest.TestCase):
    def test_direkter_vergleich_vor_platzierungsbilanz(self):
        rows = [_row(1, "9a", 10, [1, 3]), _row(2, "9b", 10, [2, 2]), _row(3, "9c", 12, [1, 1])]
        gw.sortiere_gesamtwertung(rows, spiele_entscheid=[_spiel(2, 1, 25, 20)], entscheid_label="Volleyball")
        self.assertEqual([r["team"] for r in rows], ["9c", "9b", "9a"])
        self.assertEqual([r["place"] for r in rows], [1, 2, 3])
        self.assertEqual(rows[2]["tie_note"], "Gleichstand: direkter Vergleich Volleyball")
        self.assertIsNone(rows[0]["tie_note"])

    def test_ohne_duell_entscheidet_die_bilanz(self):
        rows = [_row(1, "9a", 10, [2, 2]), _row(2, "9b", 10, [1, 3])]
        gw.sortiere_gesamtwertung(rows, spiele_entscheid=[], entscheid_label="Volleyball")
        self.assertEqual([r["team"] for r in rows], ["9b", "9a"])
        self.assertIsNone(rows[0]["tie_note"])

    def test_unentschiedenes_duell_dann_bilanz(self):
        rows = [_row(1, "9a", 10, [2, 2]), _row(2, "9b", 10, [1, 3])]
        gw.sortiere_gesamtwertung(rows, spiele_entscheid=[_spiel(1, 2, 1, 1)], entscheid_label="Volleyball")
        self.assertEqual([r["team"] for r in rows], ["9b", "9a"])

    def test_ohne_entscheid_wie_bisher(self):
        rows = [_row(1, "9a", 10, [2, 2], diff=5), _row(2, "9b", 10, [2, 2], diff=1), _row(3, "9c", 10, [2, 2], diff=1)]
        gw.sortiere_gesamtwertung(rows)
        self.assertEqual([r["team"] for r in rows], ["9a", "9b", "9c"])
        self.assertEqual([r["place"] for r in rows], [1, 2, 2])


class RegelnTests(unittest.TestCase):
    def test_je_sportart_einmal(self):
        hinweise = regeln_hinweise([
            {"name": "Volleyball Jahrgang 9", "sportart": "Volleyball", "regeln": "Aufschlag von hinten"},
            {"name": "Volleyball Jahrgang 10", "sportart": "Volleyball", "regeln": "Aufschlag von hinten"},
            {"name": "Tischtennis Jungen 9", "sportart": "Tischtennis", "regeln": "  "},
            {"name": "Fußball 5", "sportart": "Fußball", "regeln": None},
        ])
        self.assertEqual(hinweise, [{"titel": "Volleyball", "text": "Aufschlag von hinten"}])

    def test_verschiedene_texte_mit_wettbewerbsnamen(self):
        hinweise = regeln_hinweise([
            {"name": "Volleyball Jahrgang 9", "sportart": "Volleyball", "regeln": "A"},
            {"name": "Volleyball Jahrgang 10", "sportart": "Volleyball", "regeln": "B"},
        ])
        self.assertEqual([h["titel"] for h in hinweise], ["Volleyball Jahrgang 9", "Volleyball Jahrgang 10"])

    def test_normalisieren(self):
        self.assertIsNone(normalisiere_regeln("   "))
        self.assertEqual(normalisiere_regeln("a\r\nb "), "a\nb")
        self.assertEqual(len(normalisiere_regeln("x" * 5000)), 4000)


if __name__ == "__main__":
    unittest.main()
