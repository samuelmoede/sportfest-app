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
    """Issue #79: /assistent fuehrt schrittweise durch Veranstaltung waehlen/
    anlegen, Stammdaten-Check (Teams/Spielfelder), Wettbewerbe anlegen und
    Weiterleitung zu Spielplan/Disziplinen - ohne bestehende Routen (teams,
    venues, competitions, schedule) zu veraendern."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "wizard-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def _create_event(self, client, name="Sportfest 2026"):
        response = client.post(
            "/assistent/veranstaltung/anlegen",
            data={"name": name, "event_type": "Einzelturnier", "event_date": ""},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        location = response.headers["location"]
        self.assertTrue(location.startswith("/assistent/"))
        return int(location.rsplit("/", 1)[-1])

    def test_start_page_lists_events_and_form(self):
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
        self.assertIn("Bestehende Veranstaltung", response.text)
        self.assertIn("Neue Veranstaltung anlegen", response.text)

    def test_create_event_redirects_into_wizard(self):
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            event_id = self._create_event(client)
        with get_conn() as conn:
            event = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        self.assertIsNotNone(event)
        self.assertEqual(event["name"], "Sportfest 2026")

    def test_create_event_without_name_redirects_with_error(self):
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            response = client.post(
                "/assistent/veranstaltung/anlegen",
                data={"name": "  ", "event_type": "Einzelturnier"},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertIn("error=invalid", response.headers["location"])
        with get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]
        self.assertEqual(count, 0)

    def test_event_step_flags_missing_teams_and_courts(self):
        from app.main import app as fastapi_app
        with get_conn() as conn:
            # init_db() seedet standardmaessig aktive Spielfelder (Rasenplatz,
            # Kaefig); fuer den "nichts angelegt"-Fall muessen diese deaktiviert
            # werden, sonst waere has_courts immer True.
            conn.execute("UPDATE courts SET active = 0")
            conn.commit()
        with TestClient(fastapi_app) as client:
            event_id = self._create_event(client)
            response = client.get(f"/assistent/{event_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Teams anlegen", response.text)
        self.assertIn("Spielfelder anlegen", response.text)

    def test_event_step_shows_existing_teams_and_courts(self):
        from app.main import app as fastapi_app
        with get_conn() as conn:
            conn.execute("INSERT INTO teams (name, jahrgang, active) VALUES ('7a', 7, 1)")
            conn.execute("INSERT INTO teams (name, jahrgang, active) VALUES ('7b', 7, 1)")
            conn.execute("INSERT INTO courts (name, location, active) VALUES ('Feld 1', 'Turnhalle', 1)")
            conn.commit()
        with TestClient(fastapi_app) as client:
            event_id = self._create_event(client)
            response = client.get(f"/assistent/{event_id}")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Teams anlegen", response.text)
        self.assertNotIn("Spielfelder anlegen", response.text)
        self.assertIn("Weiteren Wettbewerb hinzufügen", response.text)

    def test_create_competition_for_event(self):
        from app.main import app as fastapi_app
        with get_conn() as conn:
            for name in ("7a", "7b", "7c"):
                conn.execute("INSERT INTO teams (name, jahrgang, active) VALUES (?, 7, 1)", (name,))
            conn.commit()
        with TestClient(fastapi_app) as client:
            event_id = self._create_event(client)
            response = client.post(
                f"/assistent/{event_id}/wettbewerb/anlegen",
                data={
                    "name": "",
                    "sportart": "Völkerball",
                    "jahrgang": "7",
                    "competition_type": "Turnier",
                },
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], f"/assistent/{event_id}")

        with get_conn() as conn:
            competition = conn.execute(
                "SELECT * FROM competitions WHERE event_id = ?", (event_id,)
            ).fetchone()
        self.assertIsNotNone(competition)
        self.assertEqual(competition["sportart"], "Völkerball")
        self.assertEqual(competition["jahrgang"], 7)
        self.assertEqual(competition["competition_type"], "Turnier")
        self.assertEqual(competition["points_first_place"], 3)
        self.assertEqual(competition["name"], "Völkerball Jahrgang 7")

    def test_create_competition_with_too_few_teams_is_rejected(self):
        from app.main import app as fastapi_app
        with get_conn() as conn:
            conn.execute("INSERT INTO teams (name, jahrgang, active) VALUES ('7a', 7, 1)")
            conn.commit()
        with TestClient(fastapi_app) as client:
            event_id = self._create_event(client)
            response = client.post(
                f"/assistent/{event_id}/wettbewerb/anlegen",
                data={"sportart": "Völkerball", "jahrgang": "7", "competition_type": "Turnier"},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertIn("error=team_count", response.headers["location"])
        with get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM competitions").fetchone()["n"]
        self.assertEqual(count, 0)

    def test_created_competitions_appear_with_next_step_links(self):
        from app.main import app as fastapi_app
        with get_conn() as conn:
            for name in ("7a", "7b"):
                conn.execute("INSERT INTO teams (name, jahrgang, active) VALUES (?, 7, 1)", (name,))
            conn.commit()
        with TestClient(fastapi_app) as client:
            event_id = self._create_event(client)
            client.post(
                f"/assistent/{event_id}/wettbewerb/anlegen",
                data={"sportart": "Völkerball", "jahrgang": "7", "competition_type": "Sechskampf"},
                follow_redirects=False,
            )
            response = client.get(f"/assistent/{event_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Disziplinen anlegen", response.text)
        self.assertIn("Abschluss", response.text)

    def test_unknown_event_redirects_to_start(self):
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            response = client.get("/assistent/999999", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/assistent")


class WizardQuickstartIntegrationTests(unittest.TestCase):
    """Issue #83: der Turnier-Schnellstart (/turnier-schnellstart) ist kein
    eigener Nav-Eintrag mehr, sondern wird direkt eingebettet auf der
    /assistent-Startseite angeboten (Formular postet weiterhin an die
    unveraenderte /turnier-schnellstart-Route, siehe test_quickstart_route.py)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "wizard-quickstart-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_start_page_embeds_quickstart_form_with_teams_and_courts(self):
        from app.main import app as fastapi_app
        with get_conn() as conn:
            conn.execute("INSERT INTO teams (name, jahrgang, active) VALUES ('7a', 7, 1)")
            conn.execute("INSERT INTO teams (name, jahrgang, active) VALUES ('7b', 7, 1)")
            # Der Schnellstart filtert Felder auf DEFAULT_COMPETITION_LOCATION
            # ("Turnhalle"); die von init_db() geseedeten Standardfelder
            # (Rasenplatz, Kaefig) liegen am Ort "Fussballplatz" und zaehlen
            # hier nicht - ohne ein aktives Turnhalle-Feld bliebe courts leer.
            conn.execute(
                "INSERT INTO courts (name, location, active) VALUES ('Feld 1', 'Turnhalle', 1)"
            )
            conn.commit()
        with TestClient(fastapi_app) as client:
            response = client.get("/assistent")
        self.assertEqual(response.status_code, 200)
        self.assertIn('action="/turnier-schnellstart"', response.text)
        self.assertIn("7a", response.text)
        self.assertIn("Feld 1", response.text)

    def test_start_page_shows_hint_when_no_teams(self):
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            response = client.get("/assistent")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Es sind keine aktiven Teams angelegt", response.text)
        self.assertNotIn('action="/turnier-schnellstart"', response.text)


if __name__ == "__main__":
    unittest.main()
