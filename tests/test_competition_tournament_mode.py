"""Tests fuer die Turniermodus-Auswahl bei Wettbewerben vom Typ "Turnier"
(Issue #101, Teilschritt 2): app/routes/competitions.py::_normalize_tournament_mode,
Persistenz beim Anlegen/Bearbeiten sowie Kompatibilitaet fuer Bestandsdaten
(tournament_mode bleibt NULL -> Default "gruppenphase_ko", bisheriges
Verhalten unveraendert). Die Spalte competitions.tournament_mode existierte
bereits (bisher nur fuer competition_type = 'Schulpokal' befuellt, siehe
app/database.py) - es gibt daher bewusst keine neue ALTER-TABLE-Migration,
sondern nur eine erweiterte Normalisierung/Registry. Ein Idempotenz-Test fuer
init_db() ist trotzdem enthalten, wie fuer Schema-Aenderungen ueblich."""
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
from app.routes.competitions import _normalize_tournament_mode
from app.services.tournament_modes import DEFAULT_TURNIER_MODE


class NormalizeTournamentModeTests(unittest.TestCase):
    def test_turnier_defaults_to_gruppenphase_ko(self):
        self.assertEqual(_normalize_tournament_mode("Turnier", ""), "gruppenphase_ko")
        self.assertEqual(_normalize_tournament_mode("Turnier", "unbekannt"), "gruppenphase_ko")

    def test_turnier_accepts_ko_runde(self):
        self.assertEqual(_normalize_tournament_mode("Turnier", "ko_runde"), "ko_runde")

    def test_schulpokal_unaffected(self):
        self.assertEqual(_normalize_tournament_mode("Schulpokal", ""), "jeder_gegen_jeden")
        self.assertEqual(_normalize_tournament_mode("Schulpokal", "ko_runde"), "jeder_gegen_jeden")

    def test_sechskampf_has_no_tournament_mode(self):
        self.assertIsNone(_normalize_tournament_mode("Sechskampf", "ko_runde"))


class TournamentModeMigrationTests(unittest.TestCase):
    """init_db() bleibt idempotent (mehrfacher Aufruf auf derselben Datei
    darf nicht fehlschlagen) und die bereits bestehende, nullable Spalte
    tournament_mode existiert unabhaengig vom competition_type."""

    def test_init_db_twice_on_fresh_db_is_idempotent(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "migration-idempotency-test.db"
            init_db(db_path)
            init_db(db_path)

            import sqlite3
            conn = sqlite3.connect(db_path)
            try:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(competitions)")}
                self.assertIn("tournament_mode", columns)
                self.assertIn("competition_type", columns)
            finally:
                conn.close()


class TournamentModePersistenceTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "tournament-mode-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def _get_competition(self, name):
        with get_conn() as conn:
            return conn.execute(
                "SELECT * FROM competitions WHERE name = ?", (name,)
            ).fetchone()

    def test_create_turnier_without_mode_defaults_to_gruppenphase_ko(self):
        """Ein ganz normal angelegtes Turnier ohne explizite Modus-Auswahl
        (z.B. ueber den Schnellstart-Assistenten, der dieses Feld noch nicht
        anbietet) bekommt den aufgeloesten Default "gruppenphase_ko"
        gespeichert - fachlich identisch zum bisherigen NULL-Verhalten, da
        ueberall (or DEFAULT_TURNIER_MODE) gelesen wird."""
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with get_conn() as conn:
            conn.execute("INSERT INTO teams (name, jahrgang) VALUES ('7a', 7)")
            conn.commit()

        with TestClient(fastapi_app) as client:
            client.post(
                "/competition/create",
                data={
                    "name": "Altbestand-Turnier", "sportart": "Fußball", "jahrgang": "7",
                    "competition_type": "Turnier",
                },
                follow_redirects=False,
            )

        competition = self._get_competition("Altbestand-Turnier")
        self.assertIsNotNone(competition)
        self.assertEqual(competition["tournament_mode"], DEFAULT_TURNIER_MODE)

    def test_legacy_row_with_null_tournament_mode_still_treated_as_default(self):
        """Wettbewerbe, die vor Einfuehrung dieses Felds angelegt wurden (und
        seither nie ueber /competition/{id}/update gespeichert wurden), haben
        tournament_mode = NULL in der Datenbank - das muss weiterhin ueberall
        als "gruppenphase_ko" interpretiert werden, siehe
        (c.tournament_mode or DEFAULT_TURNIER_MODE) in den Templates/Routen."""
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO competitions (name, sportart, jahrgang, status, competition_type)
                VALUES ('Legacy-Turnier', 'Fußball', 7, 'geplant', 'Turnier')
            """)
            conn.commit()

        competition = self._get_competition("Legacy-Turnier")
        self.assertIsNone(competition["tournament_mode"])
        self.assertEqual(
            competition["tournament_mode"] or DEFAULT_TURNIER_MODE, DEFAULT_TURNIER_MODE
        )

    def test_create_turnier_with_ko_runde_mode_persists(self):
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with get_conn() as conn:
            conn.execute("INSERT INTO teams (name, jahrgang) VALUES ('7a', 7)")
            conn.commit()

        with TestClient(fastapi_app) as client:
            client.post(
                "/competition/create",
                data={
                    "name": "KO-Turnier", "sportart": "Fußball", "jahrgang": "7",
                    "competition_type": "Turnier", "tournament_mode": "ko_runde",
                },
                follow_redirects=False,
            )

        competition = self._get_competition("KO-Turnier")
        self.assertIsNotNone(competition)
        self.assertEqual(competition["tournament_mode"], "ko_runde")

    def test_update_switches_mode_and_back(self):
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with get_conn() as conn:
            conn.execute("INSERT INTO teams (name, jahrgang) VALUES ('7a', 7)")
            conn.execute("""
                INSERT INTO competitions (name, sportart, jahrgang, status, competition_type)
                VALUES ('Wechsel-Turnier', 'Fußball', 7, 'geplant', 'Turnier')
            """)
            conn.commit()
            competition_id = conn.execute(
                "SELECT id FROM competitions WHERE name = 'Wechsel-Turnier'"
            ).fetchone()["id"]

        update_payload = {
            "name": "Wechsel-Turnier", "sportart": "Fußball", "jahrgang": "7",
            "status": "geplant", "points_win": "3", "points_draw": "1", "points_loss": "0",
            "competition_type": "Turnier", "tournament_mode": "ko_runde",
        }

        with TestClient(fastapi_app) as client:
            client.post(
                f"/competition/{competition_id}/update",
                data=update_payload,
                follow_redirects=False,
            )
            self.assertEqual(self._get_competition("Wechsel-Turnier")["tournament_mode"], "ko_runde")

            update_payload["tournament_mode"] = "gruppenphase_ko"
            client.post(
                f"/competition/{competition_id}/update",
                data=update_payload,
                follow_redirects=False,
            )
            self.assertEqual(
                self._get_competition("Wechsel-Turnier")["tournament_mode"], "gruppenphase_ko"
            )

    def test_switching_competition_type_away_from_schulpokal_drops_its_mode(self):
        """Ein Wettbewerb, der von 'Schulpokal' auf 'Turnier' umgestellt
        wird, darf nicht den fuer Schulpokal gueltigen Modus-Schluessel
        (z.B. 'jeder_gegen_jeden') behalten, der im Turnier-Kontext gar nicht
        existiert - _normalize_tournament_mode berechnet ihn bei jedem Save
        neu aus dem *gesendeten* competition_type."""
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with get_conn() as conn:
            conn.execute("INSERT INTO teams (name, jahrgang) VALUES ('7a', 7)")
            conn.execute("""
                INSERT INTO competitions (
                    name, sportart, jahrgang, status, competition_type, tournament_mode
                )
                VALUES ('Umgestelltes-Turnier', 'Fußball', 7, 'geplant', 'Schulpokal', 'jeder_gegen_jeden')
            """)
            conn.commit()
            competition_id = conn.execute(
                "SELECT id FROM competitions WHERE name = 'Umgestelltes-Turnier'"
            ).fetchone()["id"]

        with TestClient(fastapi_app) as client:
            client.post(
                f"/competition/{competition_id}/update",
                data={
                    "name": "Umgestelltes-Turnier", "sportart": "Fußball", "jahrgang": "7",
                    "status": "geplant", "points_win": "3", "points_draw": "1", "points_loss": "0",
                    "competition_type": "Turnier", "tournament_mode": "jeder_gegen_jeden",
                },
                follow_redirects=False,
            )

        competition = self._get_competition("Umgestelltes-Turnier")
        self.assertEqual(competition["competition_type"], "Turnier")
        # "jeder_gegen_jeden" ist im Turnier-Kontext kein gueltiger Schluessel
        # (siehe TURNIER_MODES) -> Fallback auf den Turnier-Default.
        self.assertEqual(competition["tournament_mode"], DEFAULT_TURNIER_MODE)


if __name__ == "__main__":
    unittest.main()
