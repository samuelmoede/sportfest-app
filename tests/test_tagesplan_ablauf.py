"""Tests fuer den Ablauf im Tagesplan-Block (Eroeffnung, Spiele, Siegerehrung,
Jahrgangsfinale) und das Oeffnen des Spielplans per Klick auf den Block,
siehe build_block_segments in app/services/ui_view_service.py und
app/templates/partials/day_schedule.html."""
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
from app.services.ui_view_service import build_block_segments


def _slot(startzeit, slot_typ="Spiel", phase="Gruppenphase", end=None, note=None):
    return {
        "startzeit": startzeit, "slot_typ": slot_typ, "phase": phase,
        "game_end_time": end, "note": note,
    }


class BuildBlockSegmentsTests(unittest.TestCase):
    def test_opening_games_and_ceremony(self):
        slots = [
            _slot("08:00", "Eröffnung", "Eröffnung"),
            _slot("08:10", end="08:17"),
            _slot("10:30", end="10:37"),
            _slot("10:40", "Siegerehrung", "Siegerehrung", note="Siegerehrung"),
        ]
        segments = build_block_segments(8 * 60, 10 * 60 + 40, slots, [_slot("10:45", end="10:52")])
        inside = [(s["time"], s["label"]) for s in segments["inside"]]
        after = [(s["time"], s["label"]) for s in segments["after"]]
        self.assertEqual(inside, [("08:00", "Eröffnung"), ("08:10", "Spiele")])
        self.assertEqual(after, [("10:40", "Siegerehrung"), ("10:45", "Jahrgangsfinale")])
        opening, games = segments["inside"]
        self.assertEqual(opening["top_percent"], 0)
        # Eroeffnung dauert bis zum ersten Spiel
        self.assertAlmostEqual(opening["height_percent"], 10 / 160 * 100)
        self.assertAlmostEqual(games["top_percent"], 10 / 160 * 100)

    def test_ko_phases_are_own_segments(self):
        slots = [
            _slot("09:00", end="09:10"),
            _slot("10:00", phase="Halbfinale", end="10:10"),
            _slot("10:30", phase="Finale", end="10:40"),
        ]
        labels = [s["label"] for s in build_block_segments(9 * 60, 11 * 60, slots)["inside"]]
        self.assertEqual(labels, ["Spiele", "Halbfinale", "Finale"])


class TagesplanBlockPageTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "tagesplan-ablauf-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()
        with get_conn() as conn:
            self.event_id = conn.execute(
                "INSERT INTO events (name, event_date, event_type) VALUES ('Schulpokal', '2026-12-17', 'Schulpokal')"
            ).lastrowid
            team_ids = [
                conn.execute(
                    "INSERT INTO teams (name, jahrgang, active) VALUES (?, 7, 1)", (name,)
                ).lastrowid
                for name in ("7a", "7b")
            ]
            self.competition_id = conn.execute(
                """
                INSERT INTO competitions (name, sportart, jahrgang, status, event_id,
                                          competition_type, start_time, end_time)
                VALUES ('Zweifelderball Jahrgang 7', 'Zweifelderball', 7, 'geplant', ?,
                        'Schulpokal', '08:00', '10:40')
                """,
                (self.event_id,),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO slots (competition_id, startzeit, slot_typ, phase, status, note)
                VALUES (?, '08:00', 'Eröffnung', 'Eröffnung', 'geplant', '')
                """,
                (self.competition_id,),
            )
            conn.execute(
                """
                INSERT INTO slots (competition_id, startzeit, slot_typ, phase, team_a_id,
                                   team_b_id, status)
                VALUES (?, '08:15', 'Spiel', 'Gruppenphase', ?, ?, 'geplant')
                """,
                (self.competition_id, team_ids[0], team_ids[1]),
            )
            conn.commit()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_block_shows_ablauf_and_opens_plan_without_arrow_link(self):
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            page = client.get(f"/events/{self.event_id}")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("day-timeline-block-link", page.text)
        self.assertIn(f'data-href="/spielplan?competition_id={self.competition_id}"', page.text)
        # Eroeffnung als eigener Block vor dem Spielblock, ohne Spielanzahl
        self.assertIn('title="08:00–08:15 Eröffnung"', page.text)
        self.assertIn("08:15 Spiele<", page.text)
        self.assertNotIn("Spiele (", page.text)

    def test_editor_offers_opening_slot_type(self):
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            page = client.get(f"/spielplan-bearbeiten?competition_id={self.competition_id}")
        self.assertIn('<option value="Eröffnung">Eröffnung</option>', page.text)


if __name__ == "__main__":
    unittest.main()
