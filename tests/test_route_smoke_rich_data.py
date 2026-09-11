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


class RichDataRouteSmokeTests(unittest.TestCase):
    """Der urspruengliche Rauchtest (test_smoke.py) lief immer gegen eine
    komplett leere Datenbank - Randfaelle, die nur mit echten Daten
    auftreten (z.B. ein Wettbewerb ohne festen Jahrgang, der den String
    "mixed" statt einer Zahl in einer INTEGER-Spalte speichert, siehe
    PR #94/Issue "Internal Server Error auf /tabellen"), wurden dadurch nie
    getriggert. Dieser Test seedet stattdessen einen realistischen,
    "bunten" Datenbestand - normaler Wettbewerb, Wettbewerb ohne festen
    Jahrgang, Sechskampf mit Ergebnissen, Spiele in allen Status inkl.
    nachtraeglicher Korrektur, eine oeffentliche und eine archivierte
    Veranstaltung - und ruft anschliessend jede oeffentliche GET-Route
    einmal auf. Ziel: ein neuer Datenrandfall, der eine Seite zum Absturz
    bringt, faellt hier auf, statt erst live auf der Dev-/Prod-Instanz."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "route-smoke-rich-data.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()
        self._seed()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def _seed(self):
        with get_conn() as conn:
            conn.execute("INSERT INTO courts (name, sportart) VALUES ('Feld 1', 'Fußball')")
            conn.execute("INSERT INTO courts (name, sportart) VALUES ('Feld 2', 'Fußball')")
            court_ids = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM courts")}

            for name, jahrgang in [
                ("7a", 7), ("7b", 7), ("8a", 8), ("8b", 8), ("9a", 9), ("9b", 9),
            ]:
                conn.execute(
                    "INSERT INTO teams (name, jahrgang) VALUES (?, ?)", (name, jahrgang)
                )
            team_ids = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM teams")}

            conn.execute("""
                INSERT INTO events (name, status, event_type, siegerehrung_public)
                VALUES ('Bundesjugendspiele', 'geplant', 'Einzelturnier', 1)
            """)
            self.event_id = conn.execute(
                "SELECT id FROM events WHERE name = 'Bundesjugendspiele'"
            ).fetchone()["id"]

            conn.execute("""
                INSERT INTO events (name, status, event_type)
                VALUES ('Altes Sportfest', 'archiviert', 'Einzelturnier')
            """)

            # Regulaerer Turnier-Wettbewerb mit Spielen in allen Status,
            # inkl. einer nachtraeglichen Korrektur (change_log).
            conn.execute("""
                INSERT INTO competitions (
                    name, sportart, jahrgang, status, competition_type, event_id
                ) VALUES (?, 'Fußball', 7, 'läuft', 'Turnier', ?)
            """, ("Zweifelderball Jahrgang 7", self.event_id))
            comp_turnier_id = conn.execute(
                "SELECT id FROM competitions WHERE name = 'Zweifelderball Jahrgang 7'"
            ).fetchone()["id"]

            conn.execute("""
                INSERT INTO slots (
                    competition_id, court_id, startzeit, slot_typ, phase, gruppe,
                    team_a_id, team_b_id, score_a, score_b, status, started_at, finished_at
                ) VALUES (?, ?, '09:00', 'Spiel', 'Gruppenphase', 'A', ?, ?, 4, 1,
                          'beendet', '2026-09-11 09:00:00', '2026-09-11 09:10:00')
            """, (comp_turnier_id, court_ids["Feld 1"], team_ids["7a"], team_ids["7b"]))
            beendet_slot_id = conn.execute(
                "SELECT id FROM slots WHERE competition_id = ? AND startzeit = '09:00'",
                (comp_turnier_id,),
            ).fetchone()["id"]
            conn.execute("""
                INSERT INTO change_log (
                    created_at, actor_role, action, entity_type, entity_id,
                    competition_id, old_value, new_value
                ) VALUES ('2026-09-11 09:15:00', 'schiedsrichter',
                          'Ergebnis nachträglich korrigiert', 'slot_result', ?, ?, '3:1', '4:1')
            """, (beendet_slot_id, comp_turnier_id))

            conn.execute("""
                INSERT INTO slots (
                    competition_id, court_id, startzeit, slot_typ, phase, gruppe,
                    team_a_id, team_b_id, status
                ) VALUES (?, ?, '09:30', 'Spiel', 'Gruppenphase', 'A', ?, ?, 'läuft')
            """, (comp_turnier_id, court_ids["Feld 2"], team_ids["7a"], team_ids["7b"]))
            conn.execute("""
                INSERT INTO slots (
                    competition_id, court_id, startzeit, slot_typ, phase, gruppe,
                    team_a_id, team_b_id, status
                ) VALUES (?, ?, '10:00', 'Spiel', 'Gruppenphase', 'A', ?, ?, 'geplant')
            """, (comp_turnier_id, court_ids["Feld 1"], team_ids["7a"], team_ids["7b"]))
            conn.execute("""
                INSERT INTO slots (
                    competition_id, court_id, startzeit, slot_typ, phase, status
                ) VALUES (?, ?, '11:00', 'Spiel', 'Halbfinale', 'geplant')
            """, (comp_turnier_id, court_ids["Feld 1"]))
            conn.execute("""
                INSERT INTO slots (
                    competition_id, court_id, startzeit, slot_typ, phase, status
                ) VALUES (?, ?, '11:30', 'Spiel', 'Finale', 'geplant')
            """, (comp_turnier_id, court_ids["Feld 1"]))

            # Wettbewerb ohne festen Jahrgang (explizite Teamauswahl statt
            # Jahrgangs-Dropdown) - speichert "mixed" statt einer Zahl,
            # siehe app/routes/competitions.py. Regression fuer den 500er
            # aus PR #94.
            conn.execute("""
                INSERT INTO competitions (
                    name, sportart, jahrgang, status, competition_type, event_id
                ) VALUES (?, 'Fußball', 'mixed', 'geplant', 'Turnier', ?)
            """, ("Gemischtes Turnier", self.event_id))
            comp_mixed_id = conn.execute(
                "SELECT id FROM competitions WHERE name = 'Gemischtes Turnier'"
            ).fetchone()["id"]
            for team_name in ("8a", "9a"):
                conn.execute(
                    "INSERT INTO competition_teams (competition_id, team_id) VALUES (?, ?)",
                    (comp_mixed_id, team_ids[team_name]),
                )

            # Sechskampf-Wettbewerb mit Disziplinen und erfassten Ergebnissen.
            conn.execute("""
                INSERT INTO competitions (
                    name, sportart, jahrgang, status, competition_type, event_id,
                    points_first_place
                ) VALUES (?, 'Sechskampf', 9, 'läuft', 'Sechskampf', ?, 7)
            """, ("Sechskampf Jahrgang 9", self.event_id))
            comp_sk_id = conn.execute(
                "SELECT id FROM competitions WHERE name = 'Sechskampf Jahrgang 9'"
            ).fetchone()["id"]
            for name, unit in [("Weitsprung", "cm"), ("50m Lauf", "s")]:
                conn.execute("""
                    INSERT INTO competition_disciplines (competition_id, name, unit)
                    VALUES (?, ?, ?)
                """, (comp_sk_id, name, unit))
            discipline_ids = [
                r["id"] for r in conn.execute(
                    "SELECT id FROM competition_disciplines WHERE competition_id = ? ORDER BY id",
                    (comp_sk_id,),
                )
            ]
            for discipline_id in discipline_ids:
                for team_name in ("9a", "9b"):
                    conn.execute("""
                        INSERT INTO sixkampf_team_results (
                            competition_id, discipline_id, team_id, value_index, value
                        ) VALUES (?, ?, ?, 1, 4.2)
                    """, (comp_sk_id, discipline_id, team_ids[team_name]))

            conn.commit()

    def test_public_get_routes_respond_ok(self):
        from app.main import app as fastapi_app

        parameterless_paths = [
            "/", "/login", "/ergebnisse", "/beamer",
            "/tabellen", "/tabellen?jahrgang=mixed", "/tabellen?jahrgang=9",
            "/tabellen/csv",
            "/wettbewerbe", "/events", "/events/new", "/turnier-schnellstart",
            "/spielplan", "/spielplan/aushang", "/spielplan/monitor",
            "/spielplan-bearbeiten",
            "/einstellungen", "/einstellungen/aenderungsprotokoll",
            "/dokumentation", "/roadmap", "/impressum",
            "/teams", "/spielfelder", "/assistent",
        ]
        paths_with_real_ids = [
            f"/siegerehrung/{self.event_id}",
            f"/events/{self.event_id}",
            f"/events/{self.event_id}/edit",
            f"/assistent/{self.event_id}",
        ]

        with TestClient(fastapi_app) as client:
            for path in parameterless_paths + paths_with_real_ids:
                response = client.get(path)
                self.assertEqual(
                    response.status_code, 200, f"{path} -> {response.status_code}"
                )


if __name__ == "__main__":
    unittest.main()
