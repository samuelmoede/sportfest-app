"""Tests fuer app/services/schedule_conflict_service.py (ROADMAP Phase 4.7):
Konfliktpruefung eines Spielplan-Vorschlags gegen den gespeicherten Spielplan
anderer Wettbewerbe und serverseitige Pruefung in /plan-generator/apply."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi.testclient import TestClient

import app.database as database
from app.database import get_conn, init_db
from app.services.schedule_conflict_service import (
    find_existing_schedule_conflicts,
    find_internal_conflicts,
)


class ConflictTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "schedule-conflicts-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()
        with get_conn() as conn:
            conn.execute("INSERT INTO courts (name) VALUES ('Konfliktfeld 1'), ('Konfliktfeld 2')")
            self.court_1, self.court_2 = [
                row["id"] for row in conn.execute(
                    "SELECT id FROM courts WHERE name LIKE 'Konfliktfeld%' ORDER BY name"
                ).fetchall()
            ]
            conn.execute("INSERT INTO teams (name, jahrgang) VALUES ('5a', 5), ('5b', 5), ('5c', 5), ('5d', 5)")
            self.teams = {
                row["name"]: row["id"]
                for row in conn.execute("SELECT id, name FROM teams WHERE jahrgang = 5").fetchall()
            }
            conn.commit()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def _competition(self, competition_id, date="2026-06-01", status="geplant", sportart="Fußball"):
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO competitions
                    (id, name, sportart, jahrgang, competition_type, status,
                     competition_date, game_duration_minutes, changeover_duration_minutes)
                VALUES (?, ?, ?, 5, 'Turnier', ?, ?, 10, 2)
            """, (competition_id, f"Wettbewerb {competition_id}", sportart, status, date))
            conn.commit()

    def _existing(self, competition_id, startzeit, team_a, team_b, court_id=None):
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO slots (competition_id, court_id, startzeit, slot_typ, phase,
                                   team_a_id, team_b_id, status)
                VALUES (?, ?, ?, 'Spiel', 'Gruppenphase', ?, ?, 'geplant')
            """, (competition_id, court_id or self.court_1, startzeit,
                  self.teams[team_a], self.teams[team_b]))
            conn.commit()

    def _proposed(self, startzeit, team_a, team_b, court_id=None, competition_id=2):
        return {
            "competition_id": competition_id,
            "startzeit": startzeit,
            "slot_typ": "Spiel",
            "court_id": court_id or self.court_2,
            "team_a_id": self.teams[team_a],
            "team_b_id": self.teams[team_b],
        }

    def _check(self, proposed):
        with get_conn() as conn:
            return find_existing_schedule_conflicts(conn, proposed)


class ExistingScheduleConflictTests(ConflictTestCase):
    def test_team_playing_elsewhere_at_same_time_is_an_error(self):
        self._competition(1)
        self._competition(2)
        self._existing(1, "10:00", "5a", "5b")
        warnings = self._check([self._proposed("10:05", "5a", "5c")])
        self.assertEqual([w["level"] for w in warnings], ["error"])
        self.assertIn("5a", warnings[0]["message"])
        self.assertIn("Wettbewerb 1", warnings[0]["message"])

    def test_team_playing_other_sport_at_same_time_is_only_a_hint(self):
        # Schulpokal: Volleyball in der Halle und Tischtennis in der Mensa
        # mit denselben Klassen zur selben Zeit.
        self._competition(1, sportart="Volleyball")
        self._competition(2, sportart="Tischtennis")
        self._existing(1, "10:00", "5a", "5b")
        warnings = self._check([self._proposed("10:05", "5a", "5c")])
        self.assertEqual([w["level"] for w in warnings], ["warning"])
        self.assertIn("Andere Sportart", warnings[0]["message"])
        self.assertIn("Volleyball", warnings[0]["message"])

    def test_same_sport_with_different_spelling_stays_an_error(self):
        self._competition(1, sportart="Volleyball")
        self._competition(2, sportart=" volleyball ")
        self._existing(1, "10:00", "5a", "5b")
        warnings = self._check([self._proposed("10:05", "5a", "5c")])
        self.assertEqual([w["level"] for w in warnings], ["error"])

    def test_same_court_with_other_sport_stays_an_error(self):
        self._competition(1, sportart="Volleyball")
        self._competition(2, sportart="Tischtennis")
        self._existing(1, "10:00", "5a", "5b", court_id=self.court_1)
        warnings = self._check([self._proposed("10:00", "5c", "5d", court_id=self.court_1)])
        self.assertEqual([w["level"] for w in warnings], ["error"])

    def test_same_court_at_same_time_is_an_error(self):
        self._competition(1)
        self._competition(2)
        self._existing(1, "10:00", "5a", "5b", court_id=self.court_1)
        warnings = self._check([self._proposed("10:00", "5c", "5d", court_id=self.court_1)])
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["level"], "error")
        self.assertIn("Konfliktfeld 1", warnings[0]["message"])

    def test_back_to_back_games_do_not_conflict(self):
        self._competition(1)
        self._competition(2)
        self._existing(1, "10:00", "5a", "5b", court_id=self.court_2)
        self.assertEqual(self._check([self._proposed("10:10", "5a", "5c")]), [])

    def test_other_day_is_ignored(self):
        self._competition(1, date="2026-06-02")
        self._competition(2)
        self._existing(1, "10:00", "5a", "5b")
        self.assertEqual(self._check([self._proposed("10:00", "5a", "5c")]), [])

    def test_unknown_day_is_only_a_hint(self):
        self._competition(1, date=None)
        self._competition(2)
        self._existing(1, "10:00", "5a", "5b")
        warnings = self._check([self._proposed("10:00", "5a", "5c")])
        self.assertEqual([w["level"] for w in warnings], ["warning"])
        self.assertIn("Datum", warnings[0]["message"])

    def test_archived_competition_is_ignored(self):
        self._competition(1, status="archiviert")
        self._competition(2)
        self._existing(1, "10:00", "5a", "5b")
        self.assertEqual(self._check([self._proposed("10:00", "5a", "5c")]), [])

    def test_existing_games_of_same_competition_give_one_hint(self):
        self._competition(2)
        self._existing(2, "10:00", "5a", "5b")
        self._existing(2, "10:12", "5c", "5d")
        warnings = self._check([self._proposed("11:00", "5a", "5c")])
        self.assertEqual([w["level"] for w in warnings], ["warning"])
        self.assertIn("bereits 2 Spiele", warnings[0]["message"])


class InternalConflictTests(ConflictTestCase):
    def test_team_against_itself_and_double_booking(self):
        self._competition(2)
        proposed = [
            self._proposed("10:00", "5a", "5a"),
            self._proposed("10:00", "5b", "5c", court_id=self.court_1),
            self._proposed("10:05", "5b", "5d", court_id=self.court_1),
        ]
        with get_conn() as conn:
            messages = [w["message"] for w in find_internal_conflicts(conn, proposed)]
        self.assertTrue(any("gegen sich selbst" in m for m in messages))
        self.assertTrue(any("5b" in m and "gleichzeitig" in m for m in messages))
        self.assertTrue(any("doppelt belegt" in m for m in messages))

    def test_same_team_in_two_sports_inside_plan_is_only_a_hint(self):
        self._competition(1, sportart="Volleyball")
        self._competition(2, sportart="Tischtennis")
        proposed = [
            self._proposed("10:00", "5a", "5b", court_id=self.court_1, competition_id=1),
            self._proposed("10:00", "5a", "5c", court_id=self.court_2, competition_id=2),
        ]
        with get_conn() as conn:
            warnings = find_internal_conflicts(conn, proposed)
        self.assertEqual([w["level"] for w in warnings], ["warning"])


class ApplyRouteTests(ConflictTestCase):
    def _apply(self, client, rows):
        data = {key: [] for key in (
            "competition_id", "startzeit", "slot_typ", "court_id", "phase",
            "gruppe", "team_a_id", "team_b_id", "note",
        )}
        for row in rows:
            data["competition_id"].append(str(row["competition_id"]))
            data["startzeit"].append(row["startzeit"])
            data["slot_typ"].append("Spiel")
            data["court_id"].append(str(row["court_id"]))
            data["phase"].append("Gruppenphase")
            data["gruppe"].append("")
            data["team_a_id"].append(str(row["team_a_id"]))
            data["team_b_id"].append(str(row["team_b_id"]))
            data["note"].append("")
        return client.post("/plan-generator/apply", data=data, follow_redirects=False)

    def _count(self, competition_id):
        with get_conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) AS n FROM slots WHERE competition_id = ?", (competition_id,)
            ).fetchone()["n"]

    def test_conflicting_plan_is_rejected_and_explained(self):
        from app.main import app as fastapi_app

        self._competition(1)
        self._competition(2)
        self._existing(1, "10:00", "5a", "5b")
        with TestClient(fastapi_app) as client:
            response = self._apply(client, [self._proposed("10:00", "5a", "5c")])
            self.assertEqual(response.status_code, 303)
            query = parse_qs(urlsplit(response.headers["location"]).query)
            self.assertEqual(query["plan_error"], ["konflikt"])
            page = client.get(response.headers["location"])
            self.assertIn("Der Vorschlag wurde nicht übernommen.", page.text)
        self.assertEqual(self._count(2), 0)

    def test_plan_with_double_booking_inside_is_rejected(self):
        from app.main import app as fastapi_app

        self._competition(2)
        with TestClient(fastapi_app) as client:
            self._apply(client, [
                self._proposed("10:00", "5a", "5b", court_id=self.court_1),
                self._proposed("10:00", "5a", "5c", court_id=self.court_2),
            ])
        self.assertEqual(self._count(2), 0)

    def test_clean_plan_is_saved(self):
        from app.main import app as fastapi_app

        self._competition(1)
        self._competition(2)
        self._existing(1, "10:00", "5a", "5b")
        with TestClient(fastapi_app) as client:
            response = self._apply(client, [self._proposed("10:15", "5a", "5c")])
        self.assertNotIn("plan_error", response.headers["location"])
        self.assertEqual(self._count(2), 1)

    def test_plan_with_other_sport_at_same_time_is_saved(self):
        from app.main import app as fastapi_app

        self._competition(1, sportart="Volleyball")
        self._competition(2, sportart="Tischtennis")
        self._existing(1, "10:00", "5a", "5b")
        with TestClient(fastapi_app) as client:
            response = self._apply(client, [self._proposed("10:00", "5a", "5c")])
        self.assertNotIn("plan_error", response.headers["location"])
        self.assertEqual(self._count(2), 1)

    def test_preview_shows_conflict_with_other_competition(self):
        from app.main import app as fastapi_app

        self._competition(1)
        self._competition(2)
        with get_conn() as conn:
            conn.execute("UPDATE competitions SET tournament_mode = 'ko_runde' WHERE id = 2")
            conn.commit()
        self._existing(1, "09:00", "5a", "5b", court_id=self.court_1)
        with TestClient(fastapi_app) as client:
            page = client.post("/plan-generator/preview-ko", data={
                "competition_id": "2",
                "court_ids": [str(self.court_1)],
                "startzeit": "09:00",
            })
        self.assertEqual(page.status_code, 200)
        self.assertIn("bereits durch &#34;Wettbewerb 1&#34;", page.text)


if __name__ == "__main__":
    unittest.main()
