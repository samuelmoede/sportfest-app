"""Tests fuer Programmpunkte mit Dauer (Eroeffnung, Siegerehrung ...) am
Tagesplan, siehe app/services/programmpunkte_service.py, die Routen in
app/routes/events.py und build_block_segments in ui_view_service."""
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
from app.main import get_required_role
from app.services.programmpunkte_service import (
    applies_to_jahrgang,
    create_programmpunkt,
    list_programmpunkte,
)
from datetime import datetime

from app.services.ui_view_service import block_verspaetung, build_block_segments


class _DbTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._patcher = patch.object(database, "DB_PATH", Path(self._tmpdir.name) / "pp.db")
        self._patcher.start()
        init_db()
        with get_conn() as conn:
            self.event_id = conn.execute(
                "INSERT INTO events (name, event_date, event_type) VALUES ('Schulpokal', '2026-12-17', 'Schulpokal')"
            ).lastrowid
            self.competition_ids = {}
            for jahrgang, start, end in ((7, "08:00", "10:40"), (8, "08:00", "10:40"), (9, "11:10", "13:40")):
                self.competition_ids[jahrgang] = conn.execute(
                    """
                    INSERT INTO competitions (name, sportart, jahrgang, status, event_id,
                                              competition_type, start_time, end_time)
                    VALUES (?, 'Zweifelderball', ?, 'geplant', ?, 'Schulpokal', ?, ?)
                    """,
                    (f"Zweifelderball Jahrgang {jahrgang}", jahrgang, self.event_id, start, end),
                ).lastrowid
            conn.commit()

    def tearDown(self):
        self._patcher.stop()
        self._tmpdir.cleanup()

    def _client(self):
        from app.main import app as fastapi_app
        return TestClient(fastapi_app)


class ProgrammpunktServiceTests(_DbTestCase):
    def test_create_and_list(self):
        with get_conn() as conn:
            create_programmpunkt(conn, self.event_id, "Siegerehrung", "10:40", "10:55", ["7", "8"])
            create_programmpunkt(conn, self.event_id, " Eröffnung ", "8:00", "08:10")
            punkte = list_programmpunkte(conn, self.event_id)
        self.assertEqual(
            [(p["titel"], p["start_time"], p["end_time"], p["jahrgaenge_label"]) for p in punkte],
            [("Eröffnung", "08:00", "08:10", "alle Jahrgänge"),
             ("Siegerehrung", "10:40", "10:55", "Jahrgang 7 + 8")],
        )
        self.assertTrue(applies_to_jahrgang(punkte[0], 9))
        self.assertTrue(applies_to_jahrgang(punkte[1], 7))
        self.assertFalse(applies_to_jahrgang(punkte[1], 9))

    def test_invalid_input_is_refused(self):
        with get_conn() as conn:
            for args in (("", "08:00", "08:10"), ("Pause", "", "08:10"), ("Pause", "08:10", "08:00")):
                with self.assertRaises(ValueError):
                    create_programmpunkt(conn, self.event_id, *args)


class ProgrammpunktSegmentTests(unittest.TestCase):
    def test_programmpunkt_has_own_duration_and_replaces_same_slot(self):
        slots = [
            {"startzeit": "08:00", "slot_typ": "Eröffnung", "phase": "Eröffnung", "note": ""},
            {"startzeit": "08:15", "slot_typ": "Spiel", "phase": "Gruppenphase", "game_end_time": "08:22"},
        ]
        punkte = [
            {"titel": "Eröffnung", "start_time": "08:00", "end_time": "08:10"},
            {"titel": "Siegerehrung", "start_time": "10:40", "end_time": "10:55"},
        ]
        segments = build_block_segments(8 * 60, 10 * 60 + 40, slots, (), punkte)
        self.assertEqual(
            [(s["time"], s["label"]) for s in segments["inside"]],
            [("08:00–08:10", "Eröffnung"), ("08:15", "Spiele (1)")],
        )
        self.assertAlmostEqual(segments["inside"][0]["height_percent"], 10 / 160 * 100)
        self.assertEqual([(s["time"], s["label"]) for s in segments["after"]], [("10:40–10:55", "Siegerehrung")])


class ProgrammpunktRouteTests(_DbTestCase):
    def test_create_show_and_delete_via_event_page(self):
        with self._client() as client:
            response = client.post(
                f"/events/{self.event_id}/programmpunkt/anlegen",
                data={"titel": "Siegerehrung", "start_time": "10:40", "end_time": "10:55", "jahrgaenge": ["7", "8"]},
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)
            client.post(
                f"/events/{self.event_id}/programmpunkt/anlegen",
                data={"titel": "Eröffnung", "start_time": "08:00", "end_time": "08:10"},
            )
            page = client.get(f"/events/{self.event_id}")
            self.assertIn("Jahrgang 7 + 8", page.text)
            self.assertIn('name="jahrgaenge" value="9"', page.text)
            # Block Jahrgang 7: Eroeffnung im Block, Siegerehrung danach
            self.assertIn("08:00–08:10 Eröffnung", page.text)
            self.assertIn("danach 10:40–10:55 Siegerehrung", page.text)
            # Block Jahrgang 9 (11:10) bekommt keins von beiden: Eroeffnung
            # liegt vor dem Block, Siegerehrung gilt nur fuer 7 und 8.
            self.assertEqual(page.text.count("danach 10:40–10:55 Siegerehrung"), 2)
            # Eroeffnung je Block als title und Beschriftung, nur Jahrgang 7 und 8
            self.assertEqual(page.text.count("08:00–08:10 Eröffnung"), 4)
            with get_conn() as conn:
                pid = list_programmpunkte(conn, self.event_id)[0]["id"]
            client.post(f"/events/{self.event_id}/programmpunkt/{pid}/loeschen")
            with get_conn() as conn:
                self.assertEqual(len(list_programmpunkte(conn, self.event_id)), 1)

    def test_invalid_times_show_error(self):
        with self._client() as client:
            page = client.post(
                f"/events/{self.event_id}/programmpunkt/anlegen",
                data={"titel": "Pause", "start_time": "09:00", "end_time": "08:00"},
            )
        self.assertIn("Die Endzeit muss nach der Startzeit liegen.", page.text)

    def test_access_rules(self):
        self.assertEqual(get_required_role(f"/events/{self.event_id}/programmpunkt/anlegen"), "admin")
        self.assertEqual(get_required_role(f"/events/{self.event_id}/programmpunkt/3/loeschen"), "admin")
        self.assertEqual(get_required_role("/slots/runde/starten"), "referee")
        self.assertEqual(get_required_role("/slots/runde/timer-stoppen"), "referee")


class OhneFeldEditorTests(_DbTestCase):
    def test_courtless_opening_can_be_deleted_from_editor(self):
        competition_id = self.competition_ids[7]
        with get_conn() as conn:
            slot_id = conn.execute(
                """
                INSERT INTO slots (competition_id, startzeit, slot_typ, phase, status, note)
                VALUES (?, '08:00', 'Eröffnung', 'Eröffnung', 'geplant', '')
                """,
                (competition_id,),
            ).lastrowid
            conn.commit()
        with self._client() as client:
            page = client.get(f"/spielplan-bearbeiten?competition_id={competition_id}")
            self.assertIn('id="slots-ohne-feld"', page.text)
            self.assertIn(f'action="/slot/{slot_id}/delete"', page.text)
            response = client.post(f"/slot/{slot_id}/delete", follow_redirects=False)
        self.assertEqual(
            response.headers["location"], f"/spielplan-bearbeiten?competition_id={competition_id}"
        )
        with get_conn() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM slots WHERE id = ?", (slot_id,)).fetchone())


class VerspaetungTests(unittest.TestCase):
    NOW = datetime(2026, 12, 17, 9, 0)

    def _game(self, startzeit, status="geplant", started_at=None):
        return {"slot_typ": "Spiel", "startzeit": startzeit, "status": status, "started_at": started_at}

    def test_not_started_block_before_start(self):
        self.assertIsNone(block_verspaetung([self._game("09:10")], self.NOW))

    def test_overdue_first_game(self):
        self.assertEqual(block_verspaetung([self._game("08:50")], self.NOW), 10)

    def test_last_started_game_and_next_overdue(self):
        slots = [
            self._game("08:30", "beendet", "2026-12-17 08:34:10"),
            self._game("08:40", "läuft", "2026-12-17 08:47:00"),
            self._game("09:05"),
        ]
        self.assertEqual(block_verspaetung(slots, self.NOW), 7)
        slots[-1]["startzeit"] = "08:50"
        self.assertEqual(block_verspaetung(slots, self.NOW), 10)

    def test_on_time_and_finished(self):
        on_time = [self._game("08:50", "läuft", "2026-12-17 08:49:00"), self._game("09:00")]
        self.assertEqual(block_verspaetung(on_time, self.NOW), 0)
        done = [self._game("08:50", "beendet", "2026-12-17 08:55:00")]
        self.assertIsNone(block_verspaetung(done, self.NOW))
        self.assertIsNone(block_verspaetung(on_time, None))


if __name__ == "__main__":
    unittest.main()
