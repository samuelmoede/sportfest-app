"""Tests fuer den Turniermodus "Punkterunde" (Issue #105):
app/services/tournament_modes/punkterunde.py. Analog zu
test_ko_runde_service.py / test_schedule_generator_robustness.py wird
generate_punkterunde_plan() gegen eine breite Bandbreite an Teamzahlen (2 bis
32, gerade und ungerade) geprueft: Fairness/Vollstaendigkeit (echtes 'jeder
gegen jeden', jedes Team spielt gegen jedes andere genau einmal), keine
doppelten Paarungen, keine Team-/Feld-Doppelbelegung und die korrekte
Spielanzahl (Teamzahl - 1) pro Team."""
import sqlite3
import sys
import unittest
from collections import Counter, defaultdict
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.tournament_modes import DEFAULT_TURNIER_MODE, TURNIER_MODES
from app.services.tournament_modes.punkterunde import (
    LABEL,
    MODE_KEY,
    generate_punkterunde_plan,
    validate_punkterunde_plan,
)

TEAM_COUNTS = list(range(2, 33))


def make_in_memory_db():
    """Isolierte In-Memory-DB pro Testfall, ohne die echte DB_PATH zu
    beruehren (siehe CLAUDE.md: Tests duerfen nie die Produktivdatenbank
    verwenden) - identischer Aufbau wie in test_ko_runde_service.py."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    schema_sql = (ROOT_DIR / "app" / "database.py").read_text(encoding="utf-8")
    start = schema_sql.index('conn.executescript("""') + len('conn.executescript("""')
    end = schema_sql.index('""")', start)
    conn.executescript(schema_sql[start:end])

    # tournament_mode wird erst per nachgelagerter, geguardeter ALTER-TABLE-
    # Migration in init_db() ergaenzt (siehe database.py), ist also nicht Teil
    # des oben extrahierten Basis-CREATE-TABLE-Blocks - hier direkt nachziehen,
    # weil _make_punkterunde_competition() die Spalte beim INSERT befuellt.
    conn.execute("ALTER TABLE competitions ADD COLUMN tournament_mode TEXT")
    return conn


def _make_punkterunde_competition(conn, team_count, court_count, competition_id=1):
    court_names = ["Feld 1", "Feld 2", "Feld 3"][:court_count]
    court_ids = []
    for name in court_names:
        conn.execute("INSERT INTO courts (name) VALUES (?)", (name,))
        court_ids.append(conn.execute(
            "SELECT id FROM courts WHERE name = ?", (name,)
        ).fetchone()["id"])

    for i in range(team_count):
        conn.execute(
            "INSERT INTO teams (name, jahrgang) VALUES (?, 8)", (f"Team {i + 1:02d}",)
        )

    conn.execute("""
        INSERT INTO competitions
            (id, name, sportart, jahrgang, competition_type, tournament_mode,
             game_duration_minutes, changeover_duration_minutes, status)
        VALUES (?, 'Punkterunde-Test', 'Zweifelderball', 8, 'Turnier', 'punkterunde', 7, 3, 'geplant')
    """, (competition_id,))
    conn.commit()

    teams = conn.execute("SELECT id, name FROM teams ORDER BY id").fetchall()
    return court_ids, teams


def _assert_no_double_booking(testcase, slots):
    teams_by_time = defaultdict(list)
    courts_by_time = defaultdict(list)

    for slot in slots:
        if slot["slot_typ"] != "Spiel":
            continue
        courts_by_time[slot["startzeit"]].append(slot["court_id"])
        for team_id in (slot["team_a_id"], slot["team_b_id"]):
            if team_id not in (None, ""):
                teams_by_time[slot["startzeit"]].append(team_id)

    for startzeit, team_ids in teams_by_time.items():
        counts = Counter(team_ids)
        doubled = [team_id for team_id, count in counts.items() if count > 1]
        testcase.assertEqual(
            doubled, [], f"Team-Doppelbelegung um {startzeit}: {doubled}"
        )

    for startzeit, court_ids in courts_by_time.items():
        counts = Counter(court_ids)
        doubled = [court_id for court_id, count in counts.items() if count > 1]
        testcase.assertEqual(
            doubled, [], f"Feld-Doppelbelegung um {startzeit}: {doubled}"
        )


class RegistryTests(unittest.TestCase):
    def test_punkterunde_is_registered(self):
        self.assertIn(MODE_KEY, TURNIER_MODES)
        self.assertEqual(TURNIER_MODES[MODE_KEY]["label"], LABEL)

    def test_default_mode_is_gruppenphase_ko(self):
        # Bestandswettbewerbe (tournament_mode ist NULL) muessen unveraendert
        # das bisherige Verhalten bekommen.
        self.assertEqual(DEFAULT_TURNIER_MODE, "gruppenphase_ko")
        self.assertIn(DEFAULT_TURNIER_MODE, TURNIER_MODES)


class GeneratePunkterundePlanRobustnessTests(unittest.TestCase):
    """End-to-end ueber generate_punkterunde_plan (echte DB-Queries, In-Memory)."""

    def test_full_round_robin_for_many_team_and_court_counts(self):
        for team_count in TEAM_COUNTS:
            for court_count in (1, 2, 3):
                with self.subTest(team_count=team_count, court_count=court_count):
                    conn = make_in_memory_db()
                    court_ids, teams = _make_punkterunde_competition(
                        conn, team_count, court_count
                    )

                    import app.services.tournament_modes.punkterunde as svc
                    with patch.object(svc, "get_conn", return_value=conn):
                        slots = generate_punkterunde_plan(
                            competition_id=1,
                            court_ids=court_ids,
                            startzeit="10:00",
                        )

                    self.assertTrue(slots, "Punkterunden-Generator lieferte keinen Spielplan")
                    _assert_no_double_booking(self, slots)

                    # Ausschliesslich "Gruppenphase"-Spiele, kein KO-Platzhalter.
                    phases = {slot["phase"] for slot in slots}
                    self.assertEqual(phases, {"Gruppenphase"})

                    # Keine doppelten Paarungen - jedes Team spielt gegen
                    # jedes andere hoechstens einmal.
                    seen_pairs = set()
                    for slot in slots:
                        pair_key = frozenset((slot["team_a_id"], slot["team_b_id"]))
                        self.assertNotIn(
                            pair_key, seen_pairs,
                            f"Paarung {pair_key} taucht doppelt auf bei {team_count} Teams",
                        )
                        seen_pairs.add(pair_key)

                    expected_games = team_count * (team_count - 1) // 2
                    self.assertEqual(len(slots), expected_games)

                    games_by_team = defaultdict(int)
                    for slot in slots:
                        for team_id in (slot["team_a_id"], slot["team_b_id"]):
                            games_by_team[team_id] += 1

                    for team in teams:
                        self.assertEqual(
                            games_by_team[team["id"]], team_count - 1,
                            f"{team['name']} bekommt nicht die volle Anzahl Spiele bei "
                            f"{team_count} Teams",
                        )

                    warnings = validate_punkterunde_plan(slots, teams)
                    errors = [w for w in warnings if w["level"] == "error"]
                    self.assertEqual(errors, [], f"Validierungsfehler: {errors}")

    def test_too_few_teams_returns_empty(self):
        conn = make_in_memory_db()
        court_ids, _teams = _make_punkterunde_competition(conn, 1, 2)

        import app.services.tournament_modes.punkterunde as svc
        with patch.object(svc, "get_conn", return_value=conn):
            slots = generate_punkterunde_plan(
                competition_id=1, court_ids=court_ids, startzeit="10:00"
            )

        self.assertEqual(slots, [])

    def test_apply_generic_route_receives_plain_slot_rows(self):
        """/plan-generator/apply (siehe app/routes/schedule.py) ist bewusst
        modusunabhaengig und persistiert einfach die uebergebenen Zeilen -
        hier wird nur sichergestellt, dass jede vorgeschlagene Zeile alle
        dafuer noetigen Felder besitzt (kein KeyError bei der Uebernahme)."""
        conn = make_in_memory_db()
        court_ids, _teams = _make_punkterunde_competition(conn, 6, 2)

        import app.services.tournament_modes.punkterunde as svc
        with patch.object(svc, "get_conn", return_value=conn):
            slots = generate_punkterunde_plan(competition_id=1, court_ids=court_ids, startzeit="09:00")

        required_fields = {
            "competition_id", "startzeit", "slot_typ", "court_id",
            "phase", "gruppe", "team_a_id", "team_b_id", "note",
        }
        for slot in slots:
            self.assertTrue(required_fields.issubset(slot.keys()))


if __name__ == "__main__":
    unittest.main()
