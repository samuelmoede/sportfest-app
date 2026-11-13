import re
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


class SpielplanOhneVeranstaltungsfilterTests(unittest.TestCase):
    """/spielplan hat keine Veranstaltungsauswahl mehr: Es gilt die aktive
    Veranstaltung. Ein Link mit ?event_id=... zeigt weiterhin genau diese
    Veranstaltung und bleibt beim Filtern erhalten."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "spielplan-filter-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

        with get_conn() as conn:
            self.event_ids = {}
            for name, status in (("Aktives Fest", "aktiv"), ("Geplantes Fest", "geplant")):
                conn.execute(
                    "INSERT INTO events (name, status) VALUES (?, ?)", (name, status)
                )
                self.event_ids[name] = conn.execute(
                    "SELECT id FROM events WHERE name = ?", (name,)
                ).fetchone()["id"]
            conn.execute("INSERT INTO courts (name) VALUES (?)", ("Feld 1",))
            court_id = conn.execute(
                "SELECT id FROM courts WHERE name = ?", ("Feld 1",)
            ).fetchone()["id"]
            for name, event_name in (
                ("Aktiver Wettbewerb", "Aktives Fest"),
                ("Geplanter Wettbewerb", "Geplantes Fest"),
            ):
                conn.execute(
                    """
                    INSERT INTO competitions (name, sportart, jahrgang, status, event_id, competition_type)
                    VALUES (?, 'Fussball', 7, 'geplant', ?, 'Turnier')
                    """,
                    (name, self.event_ids[event_name]),
                )
                competition_id = conn.execute(
                    "SELECT id FROM competitions WHERE name = ?", (name,)
                ).fetchone()["id"]
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

    def _get(self, url):
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get(url)
        self.assertEqual(response.status_code, 200)
        return response.text

    def test_keine_veranstaltungsauswahl_und_aktive_veranstaltung_gilt(self):
        html = self._get("/spielplan")

        self.assertNotIn('<select name="event_id"', html)
        self.assertNotIn("Alle Veranstaltungen", html)
        self.assertNotIn('name="event_id"', html)
        self.assertIn("Aktives Fest", html)
        self.assertIn("Aktiver Wettbewerb", html)
        self.assertNotIn("Geplanter Wettbewerb", html)

    def test_link_mit_event_id_zeigt_diese_veranstaltung_und_bleibt_erhalten(self):
        event_id = self.event_ids["Geplantes Fest"]
        html = self._get(f"/spielplan?event_id={event_id}")

        self.assertIn("Geplanter Wettbewerb", html)
        self.assertNotIn("Aktiver Wettbewerb", html)
        self.assertIn(
            f'<input type="hidden" name="event_id" value="{event_id}">', html
        )

    def test_veranstaltungsseite_verlinkt_spielplan_mit_event_id(self):
        event_id = self.event_ids["Geplantes Fest"]
        html = self._get(f"/events/{event_id}")

        self.assertIn(f'href="/spielplan?event_id={event_id}"', html)

    def test_beamer_haekchen_steht_in_einer_zeile_hinter_den_filtern(self):
        html = self._get("/spielplan")

        label = re.search(r'<label class="([^"]*)">\s*<input type="checkbox" name="beamer"', html)
        self.assertIsNotNone(label)
        # .filter-checkbox legt Haekchen und Text nebeneinander (theme.css);
        # ohne die Klasse stapelt .filter-row label beides uebereinander.
        self.assertIn("filter-checkbox", label.group(1).split())
        self.assertLess(html.index('name="ort"'), html.index('name="beamer"'))


if __name__ == "__main__":
    unittest.main()
