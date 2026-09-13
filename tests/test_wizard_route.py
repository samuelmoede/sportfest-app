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
        self.assertIn("noch keine aktiven Spielfelder", response.text)
        self.assertIn('action="/assistent/{}/spielfeld/anlegen"'.format(event_id), response.text)

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
        self.assertNotIn("noch keine aktiven Spielfelder", response.text)
        self.assertIn("Weiteren Wettbewerb hinzufügen", response.text)
        # Die Inline-Anlage bleibt (analog zu Klassen) auch verfuegbar, wenn
        # bereits Felder vorhanden sind.
        self.assertIn('action="/assistent/{}/spielfeld/anlegen"'.format(event_id), response.text)

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

    def test_create_team_inline_from_wizard(self):
        """Issue #85: Klassen lassen sich direkt im Assistenten anlegen, ohne
        die Seite zu verlassen (nutzt dieselbe Insert-Logik wie /team/create)."""
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            event_id = self._create_event(client)
            response = client.post(
                f"/assistent/{event_id}/team/anlegen",
                data={"name": "5a", "jahrgang": "5"},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], f"/assistent/{event_id}")

        with get_conn() as conn:
            team = conn.execute(
                "SELECT * FROM teams WHERE name = '5a'"
            ).fetchone()
        self.assertIsNotNone(team)
        self.assertEqual(team["jahrgang"], 5)
        self.assertEqual(team["active"], 1)

    def test_create_team_inline_from_wizard_requires_name_and_jahrgang(self):
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            event_id = self._create_event(client)
            response = client.post(
                f"/assistent/{event_id}/team/anlegen",
                data={"name": "  ", "jahrgang": "5"},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertIn("error=team_invalid", response.headers["location"])
        with get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM teams").fetchone()["n"]
        self.assertEqual(count, 0)

    def test_create_team_inline_from_wizard_unknown_event_redirects_to_start(self):
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            response = client.post(
                "/assistent/999999/team/anlegen",
                data={"name": "5a", "jahrgang": "5"},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/assistent")

    def test_create_court_inline_from_wizard(self):
        """Issue #107: Spielfelder lassen sich direkt im Assistenten anlegen,
        analog zur Team-Inline-Anlage (nutzt dieselbe Insert-Logik wie
        /court/create)."""
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            event_id = self._create_event(client)
            response = client.post(
                f"/assistent/{event_id}/spielfeld/anlegen",
                data={"name": "Feld 2", "sportart": "Fußball", "location": "Fußballplatz"},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], f"/assistent/{event_id}")

        with get_conn() as conn:
            court = conn.execute("SELECT * FROM courts WHERE name = 'Feld 2'").fetchone()
        self.assertIsNotNone(court)
        self.assertEqual(court["sportart"], "Fußball")
        self.assertEqual(court["location"], "Fußballplatz")
        self.assertEqual(court["active"], 1)

    def test_create_court_inline_from_wizard_requires_name(self):
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            event_id = self._create_event(client)
            response = client.post(
                f"/assistent/{event_id}/spielfeld/anlegen",
                data={"name": "  "},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertIn("error=court_invalid", response.headers["location"])
        with get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM courts").fetchone()["n"]
        # init_db() seedet bereits zwei Standardfelder - es soll keines dazukommen.
        self.assertEqual(count, 2)

    def test_create_court_inline_from_wizard_unknown_event_redirects_to_start(self):
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            response = client.post(
                "/assistent/999999/spielfeld/anlegen",
                data={"name": "Feld 2"},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/assistent")

    def test_event_step_shows_schritt_4_marker_for_spielplan_and_disziplinen(self):
        """Issue #107: die Nummerierung darf nicht von Schritt 3 direkt auf
        Schritt 5 springen - die Spielplan-/Disziplin-Erzeugung wird als
        Schritt 4 gekennzeichnet, auch wenn sie teils auf einer separaten,
        bereits gefuehrten Seite (/spielplan-bearbeiten) stattfindet."""
        from app.main import app as fastapi_app
        with get_conn() as conn:
            for name in ("7a", "7b"):
                conn.execute("INSERT INTO teams (name, jahrgang, active) VALUES (?, 7, 1)", (name,))
            conn.commit()
        with TestClient(fastapi_app) as client:
            event_id = self._create_event(client)
            client.post(
                f"/assistent/{event_id}/wettbewerb/anlegen",
                data={"sportart": "Völkerball", "jahrgang": "7", "competition_type": "Turnier"},
                follow_redirects=False,
            )
            response = client.get(f"/assistent/{event_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Schritt 4", response.text)

    def test_create_discipline_inline_from_wizard(self):
        """Issue #107: Sechskampf-Disziplinen lassen sich direkt im
        Assistenten anlegen, ohne auf /wettbewerbe umzuleiten - nutzt
        dieselbe Insert-Logik wie /competition/{id}/discipline/create."""
        from app.main import app as fastapi_app
        with get_conn() as conn:
            for name in ("7a", "7b"):
                conn.execute("INSERT INTO teams (name, jahrgang, active) VALUES (?, 7, 1)", (name,))
            conn.commit()
        with TestClient(fastapi_app) as client:
            event_id = self._create_event(client)
            client.post(
                f"/assistent/{event_id}/wettbewerb/anlegen",
                data={"sportart": "Sechskampf", "jahrgang": "7", "competition_type": "Sechskampf"},
                follow_redirects=False,
            )
            with get_conn() as conn:
                competition_id = conn.execute(
                    "SELECT id FROM competitions WHERE event_id = ?", (event_id,)
                ).fetchone()["id"]

            response = client.post(
                f"/assistent/{event_id}/wettbewerb/{competition_id}/disziplin/anlegen",
                data={"name": "Weitsprung", "unit": "cm", "scoring_direction": "higher"},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], f"/assistent/{event_id}")

        with get_conn() as conn:
            discipline = conn.execute(
                "SELECT * FROM competition_disciplines WHERE competition_id = ?", (competition_id,)
            ).fetchone()
        self.assertIsNotNone(discipline)
        self.assertEqual(discipline["name"], "Weitsprung")
        self.assertEqual(discipline["unit"], "cm")
        self.assertEqual(discipline["scoring_direction"], "higher")
        self.assertEqual(discipline["sort_order"], 1)

        with TestClient(fastapi_app) as client:
            response = client.get(f"/assistent/{event_id}")
        self.assertIn("Weitsprung", response.text)
        self.assertIn("Disziplinen bearbeiten", response.text)

    def test_create_discipline_inline_requires_name(self):
        from app.main import app as fastapi_app
        with get_conn() as conn:
            for name in ("7a", "7b"):
                conn.execute("INSERT INTO teams (name, jahrgang, active) VALUES (?, 7, 1)", (name,))
            conn.commit()
        with TestClient(fastapi_app) as client:
            event_id = self._create_event(client)
            client.post(
                f"/assistent/{event_id}/wettbewerb/anlegen",
                data={"sportart": "Sechskampf", "jahrgang": "7", "competition_type": "Sechskampf"},
                follow_redirects=False,
            )
            with get_conn() as conn:
                competition_id = conn.execute(
                    "SELECT id FROM competitions WHERE event_id = ?", (event_id,)
                ).fetchone()["id"]

            response = client.post(
                f"/assistent/{event_id}/wettbewerb/{competition_id}/disziplin/anlegen",
                data={"name": "  "},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertIn("error=discipline_invalid", response.headers["location"])
        with get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM competition_disciplines").fetchone()["n"]
        self.assertEqual(count, 0)

    def test_create_discipline_inline_rejects_non_sechskampf_competition(self):
        from app.main import app as fastapi_app
        with get_conn() as conn:
            for name in ("7a", "7b"):
                conn.execute("INSERT INTO teams (name, jahrgang, active) VALUES (?, 7, 1)", (name,))
            conn.commit()
        with TestClient(fastapi_app) as client:
            event_id = self._create_event(client)
            client.post(
                f"/assistent/{event_id}/wettbewerb/anlegen",
                data={"sportart": "Völkerball", "jahrgang": "7", "competition_type": "Turnier"},
                follow_redirects=False,
            )
            with get_conn() as conn:
                competition_id = conn.execute(
                    "SELECT id FROM competitions WHERE event_id = ?", (event_id,)
                ).fetchone()["id"]

            response = client.post(
                f"/assistent/{event_id}/wettbewerb/{competition_id}/disziplin/anlegen",
                data={"name": "Weitsprung"},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        with get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM competition_disciplines").fetchone()["n"]
        self.assertEqual(count, 0)

    def test_delete_discipline_inline_from_wizard(self):
        from app.main import app as fastapi_app
        with get_conn() as conn:
            for name in ("7a", "7b"):
                conn.execute("INSERT INTO teams (name, jahrgang, active) VALUES (?, 7, 1)", (name,))
            conn.commit()
        with TestClient(fastapi_app) as client:
            event_id = self._create_event(client)
            client.post(
                f"/assistent/{event_id}/wettbewerb/anlegen",
                data={"sportart": "Sechskampf", "jahrgang": "7", "competition_type": "Sechskampf"},
                follow_redirects=False,
            )
            with get_conn() as conn:
                competition_id = conn.execute(
                    "SELECT id FROM competitions WHERE event_id = ?", (event_id,)
                ).fetchone()["id"]
            client.post(
                f"/assistent/{event_id}/wettbewerb/{competition_id}/disziplin/anlegen",
                data={"name": "Weitsprung"},
                follow_redirects=False,
            )
            with get_conn() as conn:
                discipline_id = conn.execute(
                    "SELECT id FROM competition_disciplines WHERE competition_id = ?", (competition_id,)
                ).fetchone()["id"]

            response = client.post(
                f"/assistent/{event_id}/disziplin/{discipline_id}/loeschen",
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], f"/assistent/{event_id}")
        with get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM competition_disciplines").fetchone()["n"]
        self.assertEqual(count, 0)

    def test_create_competition_with_explicit_team_selection(self):
        """Issue #85: Team-Feinauswahl - werden einzelne Klassen abgewaehlt,
        werden nur die verbleibenden explizit ueber competition_teams
        zugeordnet, statt implizit alle Teams des Jahrgangs zu nehmen."""
        from app.main import app as fastapi_app
        with get_conn() as conn:
            for name in ("7a", "7b", "7c"):
                conn.execute("INSERT INTO teams (name, jahrgang, active) VALUES (?, 7, 1)", (name,))
            conn.commit()
            team_ids = {
                row["name"]: row["id"]
                for row in conn.execute("SELECT id, name FROM teams").fetchall()
            }

        with TestClient(fastapi_app) as client:
            event_id = self._create_event(client)
            response = client.post(
                f"/assistent/{event_id}/wettbewerb/anlegen",
                data={
                    "sportart": "Völkerball",
                    "jahrgang": "7",
                    "competition_type": "Turnier",
                    "team_selection_active": "1",
                    "team_ids": [str(team_ids["7a"]), str(team_ids["7b"])],
                },
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)

        with get_conn() as conn:
            competition = conn.execute(
                "SELECT * FROM competitions WHERE event_id = ?", (event_id,)
            ).fetchone()
            explicit_ids = {
                row["team_id"]
                for row in conn.execute(
                    "SELECT team_id FROM competition_teams WHERE competition_id = ?",
                    (competition["id"],),
                ).fetchall()
            }
        self.assertIsNotNone(competition)
        self.assertEqual(competition["points_first_place"], 2)
        self.assertEqual(explicit_ids, {team_ids["7a"], team_ids["7b"]})

    def test_create_competition_explicit_selection_below_two_is_rejected(self):
        from app.main import app as fastapi_app
        with get_conn() as conn:
            for name in ("7a", "7b", "7c"):
                conn.execute("INSERT INTO teams (name, jahrgang, active) VALUES (?, 7, 1)", (name,))
            conn.commit()
            team_a_id = conn.execute("SELECT id FROM teams WHERE name = '7a'").fetchone()["id"]

        with TestClient(fastapi_app) as client:
            event_id = self._create_event(client)
            response = client.post(
                f"/assistent/{event_id}/wettbewerb/anlegen",
                data={
                    "sportart": "Völkerball",
                    "jahrgang": "7",
                    "competition_type": "Turnier",
                    "team_selection_active": "1",
                    "team_ids": [str(team_a_id)],
                },
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertIn("error=team_count", response.headers["location"])
        with get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM competitions").fetchone()["n"]
        self.assertEqual(count, 0)

    def test_create_competition_without_team_selection_keeps_implicit_jahrgang_matching(self):
        """Backward-kompatibel: wird team_selection_active gar nicht
        mitgeschickt (z.B. alter Client), bleibt die bisherige implizite
        Jahrgangs-Zuordnung ohne competition_teams-Eintraege bestehen."""
        from app.main import app as fastapi_app
        with get_conn() as conn:
            for name in ("7a", "7b"):
                conn.execute("INSERT INTO teams (name, jahrgang, active) VALUES (?, 7, 1)", (name,))
            conn.commit()

        with TestClient(fastapi_app) as client:
            event_id = self._create_event(client)
            response = client.post(
                f"/assistent/{event_id}/wettbewerb/anlegen",
                data={"sportart": "Völkerball", "jahrgang": "7", "competition_type": "Turnier"},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)

        with get_conn() as conn:
            competition = conn.execute(
                "SELECT * FROM competitions WHERE event_id = ?", (event_id,)
            ).fetchone()
            explicit_count = conn.execute(
                "SELECT COUNT(*) AS n FROM competition_teams WHERE competition_id = ?",
                (competition["id"],),
            ).fetchone()["n"]
        self.assertEqual(competition["points_first_place"], 2)
        self.assertEqual(explicit_count, 0)

    def test_completion_step_links_to_grobplan_when_multiple_competitions(self):
        from app.main import app as fastapi_app
        with get_conn() as conn:
            for name in ("7a", "7b"):
                conn.execute("INSERT INTO teams (name, jahrgang, active) VALUES (?, 7, 1)", (name,))
            conn.commit()

        with TestClient(fastapi_app) as client:
            event_id = self._create_event(client)
            for sportart in ("Völkerball", "Zweifelderball"):
                client.post(
                    f"/assistent/{event_id}/wettbewerb/anlegen",
                    data={"sportart": sportart, "jahrgang": "7", "competition_type": "Turnier"},
                    follow_redirects=False,
                )
            response = client.get(f"/assistent/{event_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(f"/events/{event_id}#tagesplan", response.text)

    def test_completion_step_hides_grobplan_link_with_single_competition(self):
        from app.main import app as fastapi_app
        with get_conn() as conn:
            for name in ("7a", "7b"):
                conn.execute("INSERT INTO teams (name, jahrgang, active) VALUES (?, 7, 1)", (name,))
            conn.commit()

        with TestClient(fastapi_app) as client:
            event_id = self._create_event(client)
            client.post(
                f"/assistent/{event_id}/wettbewerb/anlegen",
                data={"sportart": "Völkerball", "jahrgang": "7", "competition_type": "Turnier"},
                follow_redirects=False,
            )
            response = client.get(f"/assistent/{event_id}")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("#tagesplan", response.text)


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

    def test_start_page_offers_inline_team_creation_when_no_teams(self):
        """Issue #107: statt auf /teams zu verweisen, bietet der
        Schnellstart-Bereich dieselbe Inline-Team-Anlage wie der
        Wettbewerbs-Assistent selbst."""
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            response = client.get("/assistent")
        self.assertEqual(response.status_code, 200)
        self.assertIn('action="/assistent/team/anlegen"', response.text)
        self.assertNotIn("Lege zuerst unter", response.text)

    def test_start_page_offers_inline_court_creation_when_no_courts(self):
        from app.main import app as fastapi_app
        with get_conn() as conn:
            conn.execute("INSERT INTO teams (name, jahrgang, active) VALUES ('7a', 7, 1)")
            conn.execute("INSERT INTO teams (name, jahrgang, active) VALUES ('7b', 7, 1)")
            # Standardfelder liegen am Ort "Fussballplatz", der Schnellstart
            # filtert auf DEFAULT_COMPETITION_LOCATION ("Turnhalle") - ohne
            # aktives Turnhalle-Feld bleibt courts also leer.
            conn.commit()
        with TestClient(fastapi_app) as client:
            response = client.get("/assistent")
        self.assertEqual(response.status_code, 200)
        self.assertIn('action="/assistent/spielfeld/anlegen"', response.text)
        self.assertNotIn("Lege zuerst unter", response.text)

    def test_quickstart_inline_team_creation(self):
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            response = client.post(
                "/assistent/team/anlegen",
                data={"name": "5a", "jahrgang": "5"},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/assistent")
        with get_conn() as conn:
            team = conn.execute("SELECT * FROM teams WHERE name = '5a'").fetchone()
        self.assertIsNotNone(team)
        self.assertEqual(team["jahrgang"], 5)

    def test_quickstart_inline_team_creation_requires_name_and_jahrgang(self):
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            response = client.post(
                "/assistent/team/anlegen",
                data={"name": "  ", "jahrgang": "5"},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertIn("qs_error=team_invalid", response.headers["location"])
        with get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM teams").fetchone()["n"]
        self.assertEqual(count, 0)

    def test_quickstart_inline_court_creation(self):
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            response = client.post(
                "/assistent/spielfeld/anlegen",
                data={"name": "Halle 1", "location": "Turnhalle"},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/assistent")
        with get_conn() as conn:
            court = conn.execute("SELECT * FROM courts WHERE name = 'Halle 1'").fetchone()
        self.assertIsNotNone(court)
        self.assertEqual(court["location"], "Turnhalle")
        self.assertEqual(court["active"], 1)

    def test_quickstart_inline_court_creation_requires_name(self):
        from app.main import app as fastapi_app
        with TestClient(fastapi_app) as client:
            response = client.post(
                "/assistent/spielfeld/anlegen",
                data={"name": "  "},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertIn("qs_error=court_invalid", response.headers["location"])


if __name__ == "__main__":
    unittest.main()
