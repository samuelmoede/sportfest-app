"""Ein einzelner Wettbewerb kann ein eigenes Datum (competitions.competition_date)
bekommen, unabhaengig vom event_date seiner (optionalen) Veranstaltung (Issue #134).
Steht ein solcher Wettbewerb an, soll er - analog zu einer datierten Veranstaltung -
im Tagesplan der Startseite auftauchen, auch wenn (noch) keine datierte/aktive
Veranstaltung existiert."""
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import app.database as database
from app.database import get_conn, init_db
from app.services.event_status_service import get_dashboard_standalone_competitions

TODAY = date.today()
TOMORROW = TODAY + timedelta(days=1)
YESTERDAY = TODAY - timedelta(days=1)


class CompetitionOwnDateMigrationTests(unittest.TestCase):
    def test_init_db_twice_on_fresh_db_is_idempotent(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "competition-date-migration-test.db"
            init_db(db_path)
            init_db(db_path)

            import sqlite3
            conn = sqlite3.connect(db_path)
            try:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(competitions)")}
                self.assertIn("competition_date", columns)
            finally:
                conn.close()


class GetDashboardStandaloneCompetitionsTests(unittest.TestCase):
    """Service-Ebene: welcher Wettbewerb/e mit eigenem Datum soll das
    Dashboard fuer welches Datum liefern."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "standalone-competitions-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def _insert(self, name, competition_date, status="geplant", jahrgang=7):
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO competitions (name, sportart, jahrgang, status, competition_type, competition_date)
                VALUES (?, 'Fußball', ?, ?, 'Turnier', ?)
                """,
                (name, jahrgang, status, competition_date),
            )
            conn.commit()

    def test_returns_none_when_no_dated_competitions(self):
        self._insert("Ohne Datum", None)
        with get_conn() as conn:
            target_date, competitions = get_dashboard_standalone_competitions(conn, TODAY)
        self.assertIsNone(target_date)
        self.assertEqual(competitions, [])

    def test_picks_nearest_upcoming_date_and_ignores_past(self):
        self._insert("Gestern", YESTERDAY.isoformat())
        self._insert("Morgen", TOMORROW.isoformat())
        self._insert("Heute", TODAY.isoformat())
        with get_conn() as conn:
            target_date, competitions = get_dashboard_standalone_competitions(conn, TODAY)
        self.assertEqual(target_date, TODAY)
        self.assertEqual([c["name"] for c in competitions], ["Heute"])

    def test_bundles_multiple_competitions_on_same_date(self):
        self._insert("Zweifelderball", TOMORROW.isoformat())
        self._insert("Volleyball", TOMORROW.isoformat())
        with get_conn() as conn:
            target_date, competitions = get_dashboard_standalone_competitions(conn, TODAY)
        self.assertEqual(target_date, TOMORROW)
        self.assertEqual(
            sorted(c["name"] for c in competitions), ["Volleyball", "Zweifelderball"]
        )

    def test_excludes_archived_competitions(self):
        self._insert("Archiviert", TODAY.isoformat(), status="archiviert")
        with get_conn() as conn:
            target_date, competitions = get_dashboard_standalone_competitions(conn, TODAY)
        self.assertIsNone(target_date)
        self.assertEqual(competitions, [])


class DashboardStandaloneCompetitionRouteTests(unittest.TestCase):
    """Integrationstest ueber die "/"-Route: ein Wettbewerb mit eigenem
    Datum, aber ohne (datierte) Veranstaltung, muss im Tagesplan der
    Startseite auftauchen."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "standalone-dashboard-route-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_dashboard_shows_standalone_competition_scheduled_today(self):
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO competitions (
                    name, sportart, jahrgang, status, competition_type,
                    competition_date, start_time, end_time
                ) VALUES (?, 'Fußball', 7, 'geplant', 'Turnier', ?, '09:00', '10:00')
                """,
                ("Bundesjugendspiele Jg7", TODAY.isoformat()),
            )
            conn.commit()

        with TestClient(fastapi_app) as client:
            response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Bundesjugendspiele Jg7", response.text)
        self.assertNotIn("Kein Tagesplan verfügbar", response.text)

    def test_dashboard_ignores_competition_with_past_date(self):
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO competitions (
                    name, sportart, jahrgang, status, competition_type,
                    competition_date, start_time, end_time
                ) VALUES (?, 'Fußball', 7, 'geplant', 'Turnier', ?, '09:00', '10:00')
                """,
                ("Laengst vorbei", YESTERDAY.isoformat()),
            )
            conn.commit()

        with TestClient(fastapi_app) as client:
            response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Kein Tagesplan verfügbar", response.text)

    def test_dated_event_takes_precedence_over_standalone_competition(self):
        """Existiert bereits eine datierte/aktive Veranstaltung, bleibt deren
        Tagesplan massgeblich - ein unabhaengiger Wettbewerb mit eigenem
        Datum wird dann (bewusst) nicht zusaetzlich eingeblendet, siehe
        get_dashboard_standalone_competitions."""
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with get_conn() as conn:
            conn.execute(
                "INSERT INTO events (name, status, event_date) VALUES (?, 'aktiv', ?)",
                ("Sportfest 2026", TODAY.isoformat()),
            )
            conn.execute(
                """
                INSERT INTO competitions (
                    name, sportart, jahrgang, status, competition_type,
                    competition_date, start_time, end_time
                ) VALUES (?, 'Fußball', 7, 'geplant', 'Turnier', ?, '09:00', '10:00')
                """,
                ("Unabhaengiger Wettbewerb", TOMORROW.isoformat()),
            )
            conn.commit()

        with TestClient(fastapi_app) as client:
            response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Sportfest 2026", response.text)
        self.assertNotIn("Unabhaengiger Wettbewerb", response.text)


class CompetitionDateFormPersistenceTests(unittest.TestCase):
    """/competition/create und /competition/{id}/update muessen das neue
    Formularfeld competition_date genauso speichern koennen wie die
    uebrigen Grobplan-Felder (start_time/end_time/location)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "competition-date-form-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

        with get_conn() as conn:
            conn.execute("INSERT INTO teams (name, jahrgang) VALUES ('7a', 7)")
            conn.commit()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def _get_competition(self, name):
        with get_conn() as conn:
            return conn.execute(
                "SELECT * FROM competitions WHERE name = ?", (name,)
            ).fetchone()

    def test_create_persists_competition_date(self):
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            client.post(
                "/competition/create",
                data={
                    "name": "Eigenes Datum Turnier", "sportart": "Fußball", "jahrgang": "7",
                    "competition_type": "Turnier", "competition_date": TOMORROW.isoformat(),
                },
                follow_redirects=False,
            )

        competition = self._get_competition("Eigenes Datum Turnier")
        self.assertIsNotNone(competition)
        self.assertEqual(competition["competition_date"], TOMORROW.isoformat())

    def test_update_sets_and_clears_competition_date(self):
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        with get_conn() as conn:
            conn.execute("""
                INSERT INTO competitions (name, sportart, jahrgang, status, competition_type)
                VALUES ('Update-Turnier', 'Fußball', 7, 'geplant', 'Turnier')
            """)
            conn.commit()
            competition_id = conn.execute(
                "SELECT id FROM competitions WHERE name = 'Update-Turnier'"
            ).fetchone()["id"]

        update_payload = {
            "name": "Update-Turnier", "sportart": "Fußball", "jahrgang": "7",
            "status": "geplant", "points_win": "3", "points_draw": "1", "points_loss": "0",
            "competition_type": "Turnier", "competition_date": TODAY.isoformat(),
        }

        with TestClient(fastapi_app) as client:
            client.post(
                f"/competition/{competition_id}/update",
                data=update_payload,
                follow_redirects=False,
            )
            self.assertEqual(
                self._get_competition("Update-Turnier")["competition_date"], TODAY.isoformat()
            )

            update_payload["competition_date"] = ""
            client.post(
                f"/competition/{competition_id}/update",
                data=update_payload,
                follow_redirects=False,
            )
            self.assertIsNone(self._get_competition("Update-Turnier")["competition_date"])


if __name__ == "__main__":
    unittest.main()
