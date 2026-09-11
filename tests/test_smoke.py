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

    def test_public_routes_respond_ok_with_mixed_jahrgang_competition(self):
        """Regression: /competition/create speichert bei rein expliziter
        Teamauswahl (ohne Jahrgang) den String "mixed" in competitions.jahrgang
        - einer INTEGER-Spalte, die sonst nur Zahlen enthaelt. Mehrere Routen
        bauten daraus per sorted({...jahrgang...}) eine Jahrgangs-Filterliste;
        sobald sowohl int- als auch "mixed"-Wettbewerbe existierten, brach das
        mit TypeError: '<' not supported between instances of 'int' and 'str'
        (500 Internal Server Error auf u.a. /tabellen und /spielplan). Der
        Rauchtest oben lief bisher immer gegen eine leere DB und deckte das
        nicht auf - hier bewusst mit einem "mixed"- und einem regulaeren
        Wettbewerb nebeneinander."""
        from app.main import app as fastapi_app
        from app.database import get_conn, init_db

        init_db()
        with get_conn() as conn:
            conn.execute("INSERT INTO courts (name) VALUES ('Feld 1')")
            conn.execute("INSERT INTO teams (name, jahrgang) VALUES ('7a', 7)")
            conn.execute("""
                INSERT INTO competitions (name, sportart, jahrgang, status, competition_type)
                VALUES ('Regulärer Wettbewerb', 'Fußball', 7, 'geplant', 'Turnier')
            """)
            conn.execute("""
                INSERT INTO competitions (name, sportart, jahrgang, status, competition_type)
                VALUES ('Gemischter Wettbewerb', 'Fußball', 'mixed', 'geplant', 'Turnier')
            """)
            conn.commit()

        with TestClient(fastapi_app) as client:
            for path in ("/tabellen", "/spielplan", "/ergebnisse", "/assistent"):
                response = client.get(path)
                self.assertEqual(response.status_code, 200, f"{path} -> {response.status_code}")


if __name__ == "__main__":
    unittest.main()
