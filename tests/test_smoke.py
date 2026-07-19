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


class SmokeTests(unittest.TestCase):
    """End-to-end-Rauchtest: App muss starten und zentrale oeffentliche
    Routen muessen antworten. Nutzt immer eine temporaere Datenbank, damit
    ein lokaler Testlauf niemals data/sportfest.db beruehrt (siehe CLAUDE.md
    Hinweis zur Netzwerkfreigabe)."""

    def setUp(self):
        # ignore_cleanup_errors: sqlite3-Verbindungen werden von get_conn()
        # nicht explizit geschlossen (nur committet/rollback via
        # Context-Manager), was unter Windows die Datei bis zum
        # Prozessende sperrt.
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "sportfest-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_public_routes_respond_ok(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            for path in ("/", "/tabellen", "/ergebnisse", "/beamer"):
                response = client.get(path)
                self.assertEqual(response.status_code, 200, f"{path} -> {response.status_code}")


if __name__ == "__main__":
    unittest.main()
