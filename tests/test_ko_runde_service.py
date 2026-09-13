"""Tests fuer den Turniermodus "Reine KO-Runde" (Issue #101, Teilschritt 2):
app/services/tournament_modes/ko_runde.py. Analog zu
test_schedule_generator_robustness.py wird build_ko_rounds() gegen eine
breite Bandbreite an Teamzahlen (2 bis 32, gerade und ungerade) geprueft,
damit der KO-Baum fuer beliebige Teamzahl nachweislich lueckenlos und fair
bleibt (kein doppeltes Freilos, jedes Team genau einmal im Baum, korrekte
Rundenzahl/-namen)."""
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
from app.services.tournament_modes.ko_runde import (
    LABEL,
    MODE_KEY,
    build_ko_rounds,
    generate_ko_plan,
    validate_ko_plan,
)

TEAM_COUNTS = list(range(2, 33))


def make_in_memory_db():
    """Isolierte In-Memory-DB pro Testfall, ohne die echte DB_PATH zu
    beruehren (siehe CLAUDE.md: Tests duerfen nie die Produktivdatenbank
    verwenden) - identischer Aufbau wie in
    test_schedule_generator_robustness.py."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    schema_sql = (ROOT_DIR / "app" / "database.py").read_text(encoding="utf-8")
    start = schema_sql.index('conn.executescript("""') + len('conn.executescript("""')
    end = schema_sql.index('""")', start)
    conn.executescript(schema_sql[start:end])

    # tournament_mode wird erst per nachgelagerter, geguardeter ALTER-TABLE-
    # Migration in init_db() ergaenzt (siehe database.py), ist also nicht Teil
    # des oben extrahierten Basis-CREATE-TABLE-Blocks - hier direkt nachziehen,
    # weil _make_ko_competition() die Spalte beim INSERT befuellt.
    conn.execute("ALTER TABLE competitions ADD COLUMN tournament_mode TEXT")
    return conn


def _make_ko_competition(conn, team_count, court_count, tournament_mode="ko_runde", competition_id=1):
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
        VALUES (?, 'KO-Test', 'Zweifelderball', 8, 'Turnier', ?, 7, 3, 'geplant')
    """, (competition_id, tournament_mode))
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
    def test_ko_runde_is_registered(self):
        self.assertIn(MODE_KEY, TURNIER_MODES)
        self.assertEqual(TURNIER_MODES[MODE_KEY]["label"], LABEL)

    def test_default_mode_is_gruppenphase_ko(self):
        # Bestandswettbewerbe (tournament_mode ist NULL) muessen unveraendert
        # das bisherige Verhalten bekommen.
        self.assertEqual(DEFAULT_TURNIER_MODE, "gruppenphase_ko")
        self.assertIn(DEFAULT_TURNIER_MODE, TURNIER_MODES)


class BuildKoRoundsTests(unittest.TestCase):
    """Reine Algorithmus-Tests fuer build_ko_rounds, ohne DB."""

    def test_too_few_teams_returns_empty(self):
        self.assertEqual(build_ko_rounds([]), [])
        self.assertEqual(build_ko_rounds(["Solo"]), [])

    def test_every_team_appears_exactly_once(self):
        """Jedes Team muss irgendwo im Baum als real benanntes Team
        auftauchen (Erstrundenspiel oder direkter Aufstieg per Freilos in
        eine spaetere Runde) - und zwar genau einmal, nie doppelt und nie
        gar nicht (lueckenloser Baum)."""
        for team_count in TEAM_COUNTS:
            with self.subTest(team_count=team_count):
                team_names = [f"T{i}" for i in range(team_count)]
                rounds = build_ko_rounds(team_names)

                appearances = Counter()
                for round_matches in rounds:
                    for match in round_matches:
                        if match["team_a"] is not None:
                            appearances[match["team_a"]] += 1
                        if match["team_b"] is not None:
                            appearances[match["team_b"]] += 1

                for name in team_names:
                    self.assertEqual(
                        appearances.get(name, 0), 1,
                        f"{name} taucht {appearances.get(name, 0)}x im Baum auf "
                        f"bei {team_count} Teams (erwartet: genau 1x)",
                    )
                self.assertEqual(sum(appearances.values()), team_count)

    def test_final_round_has_exactly_one_match(self):
        for team_count in TEAM_COUNTS:
            with self.subTest(team_count=team_count):
                team_names = [f"T{i}" for i in range(team_count)]
                rounds = build_ko_rounds(team_names)
                self.assertEqual(len(rounds[-1]), 1)
                self.assertEqual(rounds[-1][0]["phase"], "Finale")

    def test_round_count_matches_bracket_depth(self):
        for team_count in TEAM_COUNTS:
            with self.subTest(team_count=team_count):
                bracket_size = 1
                while bracket_size < team_count:
                    bracket_size *= 2
                expected_rounds = bracket_size.bit_length() - 1

                team_names = [f"T{i}" for i in range(team_count)]
                rounds = build_ko_rounds(team_names)
                self.assertEqual(len(rounds), expected_rounds)

    def test_round_labels_for_standard_bracket_sizes(self):
        expected_labels = {
            2: ["Finale"],
            4: ["Halbfinale", "Finale"],
            8: ["Viertelfinale", "Halbfinale", "Finale"],
            16: ["Achtelfinale", "Viertelfinale", "Halbfinale", "Finale"],
            32: ["Sechzehntelfinale", "Achtelfinale", "Viertelfinale", "Halbfinale", "Finale"],
        }
        for team_count, labels in expected_labels.items():
            with self.subTest(team_count=team_count):
                team_names = [f"T{i}" for i in range(team_count)]
                rounds = build_ko_rounds(team_names)
                actual_labels = [round_matches[0]["phase"] for round_matches in rounds]
                self.assertEqual(actual_labels, labels)

    def test_position_indices_sequential_within_round(self):
        for team_count in TEAM_COUNTS:
            with self.subTest(team_count=team_count):
                team_names = [f"T{i}" for i in range(team_count)]
                rounds = build_ko_rounds(team_names)
                for round_matches in rounds:
                    positions = [match["position"] for match in round_matches]
                    self.assertEqual(positions, list(range(len(round_matches))))

    def test_no_bye_ever_faces_another_bye(self):
        """Indirekt abgesichert durch test_every_team_appears_exactly_once
        (ein Bug hier wuerde ein Team verschlucken oder verdoppeln), hier
        zusaetzlich explizit: jede Runde muss mindestens ein echtes Spiel
        enthalten (nie eine komplett leere Runde durch zwei aufeinander-
        treffende Freilose)."""
        for team_count in TEAM_COUNTS:
            with self.subTest(team_count=team_count):
                team_names = [f"T{i}" for i in range(team_count)]
                rounds = build_ko_rounds(team_names)
                for round_index, round_matches in enumerate(rounds):
                    self.assertGreater(
                        len(round_matches), 0,
                        f"Runde {round_index} ist leer bei {team_count} Teams",
                    )

    def test_third_place_eligibility(self):
        """Ein Spiel um Platz 3 ergibt nur Sinn, wenn beide Finalisten aus
        einem echten Spiel der vorletzten Runde hervorgehen - das ist bei
        weniger als 4 Teams nie der Fall (2 Teams: nur ein Finale ohne
        Vorrunde; 3 Teams: das Freilos-Team erreicht das Finale ohne
        jemals gespielt zu haben, siehe build_ko_rounds-Docstring), ab 4
        Teams dagegen immer, weil das Freilos ausschliesslich in der
        ersten Runde vergeben wird."""
        for team_count in TEAM_COUNTS:
            with self.subTest(team_count=team_count):
                team_names = [f"T{i}" for i in range(team_count)]
                rounds = build_ko_rounds(team_names)
                final_match = rounds[-1][0]
                eligible = (
                    final_match["source_a"] is not None
                    and final_match["source_b"] is not None
                )
                self.assertEqual(eligible, team_count >= 4)


class GenerateKoPlanRobustnessTests(unittest.TestCase):
    """End-to-end ueber generate_ko_plan (echte DB-Queries, In-Memory)."""

    def test_valid_bracket_for_many_team_and_court_counts(self):
        for team_count in TEAM_COUNTS:
            for court_count in (1, 2, 3):
                with self.subTest(team_count=team_count, court_count=court_count):
                    conn = make_in_memory_db()
                    court_ids, teams = _make_ko_competition(conn, team_count, court_count)

                    import app.services.tournament_modes.ko_runde as svc
                    with patch.object(svc, "get_conn", return_value=conn):
                        slots = generate_ko_plan(
                            competition_id=1,
                            court_ids=court_ids,
                            startzeit="10:00",
                        )

                    self.assertTrue(slots, "KO-Generator lieferte keinen Spielplan")
                    _assert_no_double_booking(self, slots)

                    warnings = validate_ko_plan(slots, teams)
                    errors = [w for w in warnings if w["level"] == "error"]
                    self.assertEqual(errors, [], f"Validierungsfehler: {errors}")

                    phases = {slot["phase"] for slot in slots}
                    self.assertIn("Finale", phases)
                    if team_count >= 4 and court_count > 1:
                        self.assertIn("Spiel um Platz 3", phases)
                    if team_count < 4 or court_count == 1:
                        self.assertNotIn(
                            "Spiel um Platz 3", phases,
                            f"Kein sinnvolles Spiel um Platz 3 bei {team_count} Teams / "
                            f"{court_count} Feld(ern) erwartet",
                        )

    def test_apply_generic_route_receives_plain_slot_rows(self):
        """/plan-generator/apply (siehe app/routes/schedule.py) ist bewusst
        modusunabhaengig und persistiert einfach die uebergebenen Zeilen -
        hier wird nur sichergestellt, dass jede vorgeschlagene Zeile alle
        dafuer noetigen Felder besitzt (kein KeyError bei der Uebernahme)."""
        conn = make_in_memory_db()
        court_ids, teams = _make_ko_competition(conn, 6, 2)

        import app.services.tournament_modes.ko_runde as svc
        with patch.object(svc, "get_conn", return_value=conn):
            slots = generate_ko_plan(competition_id=1, court_ids=court_ids, startzeit="09:00")

        required_fields = {
            "competition_id", "startzeit", "slot_typ", "court_id",
            "phase", "gruppe", "team_a_id", "team_b_id", "note",
        }
        for slot in slots:
            self.assertTrue(required_fields.issubset(slot.keys()))


if __name__ == "__main__":
    unittest.main()
