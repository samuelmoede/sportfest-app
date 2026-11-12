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


class WizardRouteTests(unittest.TestCase):
    """Startseite /assistent (app/routes/wizard.py) und Weiterleitungen der
    frueheren Assistent-Wege. Der Einrichtungsassistent selbst wird in
    tests/test_assistent_routes.py und tests/test_assistent_bearbeiten.py
    geprueft."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "wizard-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_start_page_shows_only_new_entry_points(self):
        """Die Startseite zeigt nur noch die Einstiege des neuen
        Einrichtungsassistenten; die alten Formulare (Veranstaltung waehlen/
        anlegen, Vorlage Schulpokal, Turnier-Schnellstart) sind entfernt."""
        from app.main import app as fastapi_app
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO events (name, status, event_type) VALUES (?, 'geplant', 'Einzelturnier')",
                ("Bestehende Veranstaltung",),
            )
            conn.commit()
        with TestClient(fastapi_app) as client:
            response = client.get("/assistent")
        self.assertEqual(response.status_code, 200)
        for label in ("Neue Veranstaltung", "Bestehende Veranstaltung ändern", "Vorlagen verwalten"):
            self.assertIn(label, response.text)
        for alt in ("Weitere Wege", "Neue Veranstaltung anlegen", 'action="/assistent/veranstaltung/anlegen"',
                    'action="/assistent/vorlage/schulpokal"', 'action="/turnier-schnellstart"'):
            self.assertNotIn(alt, response.text)

    def test_old_wizard_urls_redirect(self):
        """Alte Lesezeichen landen beim neuen Assistenten bzw. auf der
        Veranstaltungsseite mit dem Knopf „Plan ändern“."""
        from app.main import app as fastapi_app
        with get_conn() as conn:
            event_id = conn.execute(
                "INSERT INTO events (name, status, event_type) VALUES ('Sportfest', 'geplant', 'Einzelturnier')"
            ).lastrowid
            conn.commit()
        with TestClient(fastapi_app) as client:
            for alt, ziel in (
                (f"/assistent/{event_id}", f"/events/{event_id}"),
                (f"/assistent/{event_id}/schulpokal-plan", f"/events/{event_id}"),
                ("/turnier-schnellstart", "/assistent"),
            ):
                response = client.get(alt, follow_redirects=False)
                self.assertEqual(response.status_code, 303, alt)
                self.assertEqual(response.headers["location"], ziel, alt)
            # Die Seiten des neuen Assistenten werden nicht als alte URL gelesen.
            self.assertEqual(client.get("/assistent/vorlagen").status_code, 200)

    def test_old_wizard_forms_are_gone(self):
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            for alt in (
                "/assistent/veranstaltung/anlegen",
                "/assistent/vorlage/schulpokal",
                "/assistent/team/anlegen",
                "/assistent/1/wettbewerb/anlegen",
                "/assistent/1/schulpokal-plan/uebernehmen",
                "/turnier-schnellstart",
            ):
                response = client.post(alt, data={"name": "X"}, follow_redirects=False)
                self.assertIn(response.status_code, (404, 405), alt)
        with get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
