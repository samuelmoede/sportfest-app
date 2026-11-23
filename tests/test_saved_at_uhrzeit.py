"""Hinweis "Gespeichert um HH:MM" (Pruefbericht B-05): die Weiterleitungen
schicken nur die Uhrzeit, nicht den ganzen Datenbank-Zeitstempel. Sonst
fehlte die Uhrzeit (Teams, Einstellungen) oder erschien mit Datum und
Sekunden (Wettbewerbe, Spielfelder, Veranstaltungen)."""
import re
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
from app.database import init_db

HHMM = re.compile(r"saved_at=\d{2}:\d{2}(?:&|#|$)")


class SavedAtUhrzeitTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._patcher = patch.object(database, "DB_PATH", Path(self._tmpdir.name) / "saved-at.db")
        self._patcher.start()
        init_db()
        from app.main import app
        self.client = TestClient(app)

    def tearDown(self):
        self._patcher.stop()
        self._tmpdir.cleanup()

    def test_routen_quellen_schicken_nur_hhmm(self):
        for datei in ("competitions", "events", "settings", "teams", "venues"):
            text = (ROOT_DIR / "app" / "routes" / f"{datei}.py").read_text(encoding="utf-8")
            self.assertNotRegex(text, r"saved_at = app_now_db_timestamp\(\)\s*$", datei)
            self.assertNotRegex(text, r"saved_at = app_now_db_timestamp\(\)\n", datei)

    def test_teams_speichern_zeigt_uhrzeit(self):
        from app.database import get_conn
        with get_conn() as conn:
            team_id = conn.execute("INSERT INTO teams (name, jahrgang) VALUES ('7a', 7)").lastrowid
            conn.commit()
        antwort = self.client.post(
            f"/team/{team_id}/update",
            data={"name": "7a", "jahrgang": "7", "active": "1"},
            follow_redirects=False,
        )
        self.assertEqual(antwort.status_code, 303)
        self.assertRegex(antwort.headers["location"], HHMM)
        uhrzeit = re.search(r"saved_at=(\d{2}:\d{2})", antwort.headers["location"]).group(1)
        seite = self.client.get(antwort.headers["location"])
        self.assertIn(uhrzeit, seite.text)
