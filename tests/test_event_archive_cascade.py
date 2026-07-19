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
from app.services.event_status_service import archive_event, restore_event


class EventArchiveCascadeTests(unittest.TestCase):
    """archive_event()/restore_event() muessen den Status der zugehoerigen
    Wettbewerbe mitziehen, damit die ueberall im Code schon vorhandenen
    'status != archiviert'-Filter archivierte Veranstaltungen automatisch
    ausblenden - ohne dass jede einzelne Ansicht das selbst pruefen muss."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "archive-cascade-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

        with get_conn() as conn:
            conn.execute(
                "INSERT INTO events (name, status) VALUES (?, ?)",
                ("Testfest", "geplant"),
            )
            self.event_id = conn.execute(
                "SELECT id FROM events WHERE name = ?", ("Testfest",)
            ).fetchone()["id"]

            conn.execute(
                """
                INSERT INTO competitions (name, sportart, jahrgang, status, event_id, competition_type)
                VALUES (?, 'Fussball', 7, 'geplant', ?, 'Turnier')
                """,
                ("Normaler Wettbewerb", self.event_id),
            )
            self.normal_competition_id = conn.execute(
                "SELECT id FROM competitions WHERE name = ?", ("Normaler Wettbewerb",)
            ).fetchone()["id"]

            # Bereits vor dem Archivieren der Veranstaltung unabhaengig
            # archiviert (z.B. Wettbewerb wurde einzeln abgeschlossen) - muss
            # beim Wiederherstellen der Veranstaltung archiviert bleiben.
            conn.execute(
                """
                INSERT INTO competitions (name, sportart, jahrgang, status, event_id, competition_type)
                VALUES (?, 'Handball', 7, 'archiviert', ?, 'Turnier')
                """,
                ("Bereits archivierter Wettbewerb", self.event_id),
            )
            self.independently_archived_competition_id = conn.execute(
                "SELECT id FROM competitions WHERE name = ?",
                ("Bereits archivierter Wettbewerb",),
            ).fetchone()["id"]
            conn.commit()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def _competition_row(self, conn, competition_id):
        return conn.execute(
            "SELECT status, archived_via_event FROM competitions WHERE id = ?",
            (competition_id,),
        ).fetchone()

    def test_archive_event_cascades_to_non_archived_competition(self):
        with get_conn() as conn:
            archive_event(conn, self.event_id)
            conn.commit()
            row = self._competition_row(conn, self.normal_competition_id)

        self.assertEqual(row["status"], "archiviert")
        self.assertEqual(row["archived_via_event"], 1)

    def test_archive_event_does_not_flag_already_archived_competition_as_cascaded(self):
        with get_conn() as conn:
            archive_event(conn, self.event_id)
            conn.commit()
            row = self._competition_row(conn, self.independently_archived_competition_id)

        self.assertEqual(row["status"], "archiviert")
        self.assertEqual(row["archived_via_event"], 0)

    def test_restore_event_only_restores_cascaded_competitions(self):
        with get_conn() as conn:
            archive_event(conn, self.event_id)
            conn.commit()
            restore_event(conn, self.event_id)
            conn.commit()

            cascaded_row = self._competition_row(conn, self.normal_competition_id)
            independent_row = self._competition_row(
                conn, self.independently_archived_competition_id
            )

        self.assertEqual(cascaded_row["status"], "geplant")
        self.assertEqual(cascaded_row["archived_via_event"], 0)
        # Unabhaengig archivierter Wettbewerb bleibt archiviert.
        self.assertEqual(independent_row["status"], "archiviert")

    def test_startup_backfill_cascades_pre_existing_archived_events(self):
        # Simuliert Altdaten von vor Einfuehrung der Kaskade: Veranstaltung
        # wurde direkt per SQL archiviert (ohne archive_event()), ihr
        # Wettbewerb hinkt hinterher. init_db() muss das beim naechsten
        # Programmstart automatisch nachziehen.
        with get_conn() as conn:
            conn.execute(
                "UPDATE events SET status = 'archiviert' WHERE id = ?",
                (self.event_id,),
            )
            conn.commit()

        init_db()

        with get_conn() as conn:
            row = self._competition_row(conn, self.normal_competition_id)

        self.assertEqual(row["status"], "archiviert")
        self.assertEqual(row["archived_via_event"], 1)


if __name__ == "__main__":
    unittest.main()
