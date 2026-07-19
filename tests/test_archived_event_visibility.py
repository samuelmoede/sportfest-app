import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import app.database as database
from app.database import get_conn, init_db
from app.services.event_status_service import get_archived_event_ids
from app.services.schedule_grid_service import get_all_slots


class ArchivedEventVisibilityTests(unittest.TestCase):
    """Archivierte Veranstaltungen duerfen im oeffentlichen Spielplan nicht
    mehr auftauchen (weder im Filter noch in der Spielliste), bleiben aber
    fuer Admins ueber die Veranstaltungsseite (/events/{id}) erreichbar."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "archived-events-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

        with get_conn() as conn:
            conn.execute(
                "INSERT INTO events (name, status) VALUES (?, ?)",
                ("Aktives Fest", "aktiv"),
            )
            conn.execute(
                "INSERT INTO events (name, status) VALUES (?, ?)",
                ("Altes Fest", "archiviert"),
            )
            self.active_event_id = conn.execute(
                "SELECT id FROM events WHERE name = ?", ("Aktives Fest",)
            ).fetchone()["id"]
            self.archived_event_id = conn.execute(
                "SELECT id FROM events WHERE name = ?", ("Altes Fest",)
            ).fetchone()["id"]

            conn.execute("INSERT INTO courts (name) VALUES (?)", ("Feld 1",))
            court_id = conn.execute(
                "SELECT id FROM courts WHERE name = ?", ("Feld 1",)
            ).fetchone()["id"]

            conn.execute(
                """
                INSERT INTO competitions (name, sportart, jahrgang, status, event_id, competition_type)
                VALUES (?, 'Fussball', 7, 'geplant', ?, 'Turnier')
                """,
                ("Aktiver Wettbewerb", self.active_event_id),
            )
            self.active_competition_id = conn.execute(
                "SELECT id FROM competitions WHERE name = ?", ("Aktiver Wettbewerb",)
            ).fetchone()["id"]

            conn.execute(
                """
                INSERT INTO competitions (name, sportart, jahrgang, status, event_id, competition_type)
                VALUES (?, 'Fussball', 7, 'geplant', ?, 'Turnier')
                """,
                ("Archivierter Wettbewerb", self.archived_event_id),
            )
            self.archived_competition_id = conn.execute(
                "SELECT id FROM competitions WHERE name = ?", ("Archivierter Wettbewerb",)
            ).fetchone()["id"]

            for competition_id in (self.active_competition_id, self.archived_competition_id):
                conn.execute(
                    """
                    INSERT INTO slots (competition_id, court_id, startzeit, slot_typ, phase, status)
                    VALUES (?, ?, '10:00', 'Spiel', 'Gruppenphase', 'geplant')
                    """,
                    (competition_id, court_id),
                )
            conn.commit()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_get_archived_event_ids_returns_only_archived(self):
        with get_conn() as conn:
            archived_ids = get_archived_event_ids(conn)
        self.assertEqual(archived_ids, {self.archived_event_id})

    def test_get_all_slots_excludes_archived_events_when_requested(self):
        slots = get_all_slots(exclude_archived_events=True)
        competition_ids = {slot["competition_id"] for slot in slots}
        self.assertIn(self.active_competition_id, competition_ids)
        self.assertNotIn(self.archived_competition_id, competition_ids)

    def test_get_all_slots_keeps_archived_events_by_default(self):
        # Der Admin-Editor (/spielplan-bearbeiten) nutzt den Standardwert und
        # muss archivierte Veranstaltungen weiterhin sehen koennen.
        slots = get_all_slots()
        competition_ids = {slot["competition_id"] for slot in slots}
        self.assertIn(self.archived_competition_id, competition_ids)

    def test_spielplan_route_hides_archived_event(self):
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get("/spielplan")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Aktives Fest", response.text)
        self.assertNotIn("Altes Fest", response.text)
        self.assertNotIn("Archivierter Wettbewerb", response.text)

    def test_spielplan_route_ignores_direct_archived_event_id_link(self):
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get(f"/spielplan?event_id={self.archived_event_id}")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Archivierter Wettbewerb", response.text)

    def test_tabellen_route_hides_archived_event_competition(self):
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get("/tabellen")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Aktiver Wettbewerb", response.text)
        self.assertNotIn("Archivierter Wettbewerb", response.text)


if __name__ == "__main__":
    unittest.main()
