"""Einstellbare Gleichstandsregeln (Szenario S6): ohne Eintrag bleibt die
Tabelle wie bisher, mit Eintrag entscheidet die gewaehlte Reihenfolge."""

import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.main import sort_table_rows
from app.services.tiebreak_service import (
    DEFAULT_TIEBREAK_ORDER,
    describe_tiebreak_order,
    format_tiebreak_order,
    parse_tiebreak_order,
)


def _competition(order=None):
    return {"points_win": 3, "points_draw": 1, "points_loss": 0, "tiebreak_order": order}


def _slot(a, b, score_a, score_b):
    return {"team_a": a, "team_b": b, "score_a": score_a, "score_b": score_b}


# A schlaegt B knapp, B hat aber insgesamt die bessere Tordifferenz.
SLOTS = [_slot("A", "B", 1, 0), _slot("A", "C", 0, 1), _slot("B", "C", 5, 0)]


def _rows():
    return [
        {"team": "A", "pkt": 3, "diff": 0, "plus": 1},
        {"team": "B", "pkt": 3, "diff": 4, "plus": 5},
        {"team": "C", "pkt": 3, "diff": -4, "plus": 1},
    ]


class TiebreakOrderTests(unittest.TestCase):
    def test_parse_und_format(self):
        self.assertIsNone(parse_tiebreak_order(None))
        self.assertIsNone(parse_tiebreak_order("quatsch"))
        self.assertEqual(parse_tiebreak_order("tore"), ["tore", "direkter_vergleich", "tordifferenz"])
        self.assertIsNone(format_tiebreak_order(list(DEFAULT_TIEBREAK_ORDER)))
        self.assertEqual(format_tiebreak_order(["tordifferenz"]), "tordifferenz,direkter_vergleich,tore")
        self.assertEqual(describe_tiebreak_order(None)[0], "Punkte")

    def test_ohne_eintrag_bleibt_bisheriges_verhalten(self):
        ohne = [r["team"] for r in sort_table_rows(_rows(), SLOTS, _competition(None))]
        standard_explizit = [r["team"] for r in sort_table_rows(
            _rows(), SLOTS, {"points_win": 3, "points_draw": 1, "points_loss": 0})]
        self.assertEqual(ohne, standard_explizit)

    def test_tordifferenz_zuerst_wird_in_der_auswertung_genutzt(self):
        rows = sort_table_rows(_rows(), SLOTS, _competition("tordifferenz,direkter_vergleich,tore"))
        self.assertEqual([r["team"] for r in rows], ["B", "A", "C"])
        self.assertEqual(rows[0]["tie_note"], "Platzierung durch Tordifferenz entschieden")

    def test_direkter_vergleich_zuerst(self):
        # Zyklus A>B, B>C, C>A: direkter Vergleich (Mini-Tabelle) hilft bei
        # Punkten nicht, deren Tordifferenz schon (B 5:1 Tore).
        rows = sort_table_rows(_rows(), SLOTS, _competition("direkter_vergleich,tordifferenz,tore"))
        self.assertEqual(rows[0]["team"], "B")


if __name__ == "__main__":
    unittest.main()
