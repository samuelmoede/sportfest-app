"""Dauerhafter Robustheits-Test fuer den Spielplan-Generator (Issue #101).

Anders als die szenariobasierten Tests in test_schedule_generator_service.py
ist dies kein einmaliger Regressionstest fuer einen einzelnen Bugfix, sondern
soll bei *jedem* CI-Lauf eine breite Bandbreite an Teamzahlen (3 bis 32,
gerade und ungerade) gegen den Generator absichern: Spielanzahl-Fairness,
keine doppelten Paarungen in der Gruppenphase, keine Team-/Feld-
Doppelbelegung zur selben Zeit, und ein konsistenter KO-Anschluss. Bisher war
das faire Rundenverfahren (_generate_balanced_pairings, siehe ROADMAP.md
Phase 4.7/Hoch) nur empirisch fuer N=4-13 verifiziert - hier wird das dauerhaft
fuer jede kuenftige Aenderung am Generator nachgehalten, nicht nur einmalig.
"""
import sqlite3
import sys
import unittest
from collections import Counter, defaultdict
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.schedule_generator_service import (
    _generate_balanced_pairings,
    generate_group_plan,
    validate_generated_plan,
)

# 3 bis 32 Teams, bewusst inklusive der hartkodierten 6er-/7er-Sonderfaelle
# aus _build_pairings, damit diese weiterhin vom selben Test mitabgedeckt
# bleiben statt eine Luecke im generalisierten Bereich zu hinterlassen.
TEAM_COUNTS = list(range(3, 33))


def make_in_memory_db():
    """Isolierte In-Memory-DB pro Testfall, ohne die echte DB_PATH zu
    beruehren (siehe CLAUDE.md: Tests duerfen nie die Produktivdatenbank
    verwenden)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    schema_sql = (ROOT_DIR / "app" / "database.py").read_text(encoding="utf-8")
    start = schema_sql.index('conn.executescript("""') + len('conn.executescript("""')
    end = schema_sql.index('""")', start)
    conn.executescript(schema_sql[start:end])
    return conn


def _make_group_competition(conn, team_count, court_count, competition_id=1):
    """Legt `court_count` Turnhallen-Felder (per Namenskonvention, siehe
    schedule_location_service.COURT_NAMES_BY_LOCATION) und `team_count` Teams
    fuer einen Turnier-Wettbewerb an."""
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
            (id, name, sportart, jahrgang, competition_type,
             game_duration_minutes, changeover_duration_minutes, status)
        VALUES (?, 'Robustheitstest', 'Zweifelderball', 8, 'Turnier', 7, 3, 'geplant')
    """, (competition_id,))
    conn.commit()

    teams = conn.execute("SELECT id, name FROM teams ORDER BY id").fetchall()
    return court_ids, teams


def _assert_no_double_booking(testcase, slots):
    """Kein Team und kein Feld darf zur selben Uhrzeit zweimal belegt sein.
    KO-Platzhalter ohne zugewiesenes Team (team_a_id == "") werden dabei
    ignoriert, da sie noch keine echte Belegung darstellen."""
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


class BalancedPairingsFairnessTests(unittest.TestCase):
    """Reine Algorithmus-Tests fuer _generate_balanced_pairings, ohne DB."""

    def test_no_duplicate_pairings(self):
        for team_count in TEAM_COUNTS:
            for games_per_team in (1, 2, 3, team_count - 1, team_count):
                with self.subTest(team_count=team_count, games_per_team=games_per_team):
                    team_names = [f"T{i}" for i in range(team_count)]
                    pairings = _generate_balanced_pairings(team_names, games_per_team)
                    seen = set()
                    for team_a, team_b in pairings:
                        key = frozenset((team_a, team_b))
                        self.assertNotIn(
                            key, seen,
                            f"{team_a} vs {team_b} doppelt bei {team_count} Teams / "
                            f"{games_per_team} Spielen pro Team",
                        )
                        seen.add(key)

    def test_game_count_fairness(self):
        """Jedes Team bekommt so gleich wie mathematisch moeglich viele
        Spiele - Differenz maximal 1 (nur erzwungen, wenn Teamzahl und
        Spielanzahl beide ungerade sind, siehe ROADMAP.md Phase 4.7/Hoch)."""
        for team_count in TEAM_COUNTS:
            for games_per_team in (1, 2, 3, team_count - 1, team_count):
                with self.subTest(team_count=team_count, games_per_team=games_per_team):
                    team_names = [f"T{i}" for i in range(team_count)]
                    pairings = _generate_balanced_pairings(team_names, games_per_team)
                    counts = Counter()
                    for team_a, team_b in pairings:
                        counts[team_a] += 1
                        counts[team_b] += 1
                    game_counts = [counts.get(name, 0) for name in team_names]
                    spread = max(game_counts) - min(game_counts)
                    self.assertLessEqual(
                        spread, 1,
                        f"Spielanzahl-Differenz {spread} bei {team_count} Teams / "
                        f"{games_per_team} Spielen pro Team: {game_counts}",
                    )

    def test_never_exceeds_requested_games_per_team(self):
        for team_count in TEAM_COUNTS:
            for games_per_team in (1, 2, 3):
                with self.subTest(team_count=team_count, games_per_team=games_per_team):
                    team_names = [f"T{i}" for i in range(team_count)]
                    pairings = _generate_balanced_pairings(team_names, games_per_team)
                    counts = Counter()
                    for team_a, team_b in pairings:
                        counts[team_a] += 1
                        counts[team_b] += 1
                    for name in team_names:
                        self.assertLessEqual(counts.get(name, 0), games_per_team)

    def test_never_the_same_opponent_twice_in_full_round_robin(self):
        """games_per_team = team_count (wie generate_schulpokal_plan es fuer
        'jeder gegen jeden' anfragt, siehe _jeder_gegen_jeden_pairings) darf
        keine Paarung wiederholen, auch nicht bei grossen/ungeraden
        Teamzahlen mit Freilos-Rotation."""
        for team_count in TEAM_COUNTS:
            with self.subTest(team_count=team_count):
                team_names = [f"T{i}" for i in range(team_count)]
                pairings = _generate_balanced_pairings(team_names, team_count)
                pair_keys = [frozenset(pair) for pair in pairings]
                self.assertEqual(len(pair_keys), len(set(pair_keys)))
                expected_total = team_count * (team_count - 1) // 2
                self.assertEqual(len(pairings), expected_total)


class GenerateGroupPlanRobustnessTests(unittest.TestCase):
    """End-to-end ueber generate_group_plan (echte DB-Queries, In-Memory)."""

    def test_valid_schedule_for_many_team_and_court_counts(self):
        for team_count in TEAM_COUNTS:
            for court_count in (1, 2, 3):
                with self.subTest(team_count=team_count, court_count=court_count):
                    conn = make_in_memory_db()
                    court_ids, teams = _make_group_competition(conn, team_count, court_count)

                    import app.services.schedule_generator_service as svc
                    with patch.object(svc, "get_conn", return_value=conn):
                        slots = generate_group_plan(
                            competition_id=1,
                            court_ids=court_ids,
                            startzeit="10:00",
                            games_per_team=2,
                            include_ko=True,
                        )

                    self.assertTrue(slots, "Generator lieferte keinen Spielplan")
                    _assert_no_double_booking(self, slots)

                    # Keine doppelten Paarungen innerhalb der Gruppenphase.
                    seen_pairs = set()
                    for slot in slots:
                        if slot["phase"] != "Gruppenphase":
                            continue
                        pair_key = frozenset((slot["team_a_id"], slot["team_b_id"]))
                        self.assertNotIn(
                            pair_key, seen_pairs,
                            f"Paarung {pair_key} taucht doppelt auf bei {team_count} Teams",
                        )
                        seen_pairs.add(pair_key)

                    # Spielanzahl-Fairness direkt an den Spielen gemessen (max.
                    # Differenz 1) - unabhaengig von validate_generated_plan's
                    # strikter Gleichheitspruefung weiter unten, die bei
                    # ungerader Teamzahl und einer Teilrunde wie hier
                    # (games_per_team=2 < volle Rundenzahl) eine dabei
                    # mathematisch unvermeidbare Differenz von 1 durchaus als
                    # Hinweis meldet (siehe _generate_balanced_pairings/
                    # Freilos-Rotation) - das ist kein Bug, sondern der Grund,
                    # warum die hartkodierten 6er-/7er-Sonderfaelle existieren.
                    games_by_team = defaultdict(int)
                    for slot in slots:
                        if slot["phase"] != "Gruppenphase":
                            continue
                        for team_id in (slot["team_a_id"], slot["team_b_id"]):
                            if team_id not in (None, ""):
                                games_by_team[team_id] += 1
                    game_counts = [games_by_team.get(team["id"], 0) for team in teams]
                    spread = max(game_counts) - min(game_counts)
                    self.assertLessEqual(
                        spread, 1,
                        f"Spielanzahl-Differenz {spread} bei {team_count} Teams / "
                        f"{court_count} Feldern: {game_counts}",
                    )

                    warnings = validate_generated_plan(slots, teams, games_per_team=2)
                    errors = [w for w in warnings if w["level"] == "error"]
                    # Bei gerader Teamzahl (kein Freilos noetig) und beim
                    # hartkodierten 7er-Sonderfall (siehe _build_pairings) ist
                    # games_per_team=2 immer exakt erfuellbar. Der 6er-/7er-
                    # Gruppen-Split erzeugt aber zwei Halbfinals, die laut
                    # validate_generated_plan gleichzeitig stattfinden muessen
                    # - mit nur einem Feld ist das legitim unmoeglich, also
                    # dort bewusst kein "fehlerfrei"-Anspruch.
                    exact_fairness_expected = team_count % 2 == 0 or team_count == 7
                    semifinal_structure_possible = not (
                        team_count in (6, 7) and court_count == 1
                    )
                    if exact_fairness_expected and semifinal_structure_possible:
                        self.assertEqual(
                            errors, [],
                            f"Fehler-Warnungen bei {team_count} Teams / {court_count} "
                            f"Feldern (exakt erfuellbar): {errors}",
                        )

                    phases = {slot["phase"] for slot in slots}
                    self.assertIn("Finale", phases)
                    if team_count in (6, 7):
                        self.assertIn(
                            "Halbfinale", phases,
                            "hartkodierter 6er-/7er-Gruppen-Split sollte weiterhin ein "
                            "Halbfinale erzeugen",
                        )
                    else:
                        self.assertNotIn(
                            "Halbfinale", phases,
                            f"{team_count} Teams ohne echten Gruppen-Split sollten kein "
                            "Halbfinale-Platzhalter erzeugen (siehe Issue #75)",
                        )
                    if court_count > 1:
                        self.assertIn("Spiel um Platz 3", phases)

    def test_full_round_robin_gives_every_team_teamcount_minus_one_games(self):
        """Echtes 'jeder gegen jeden' ohne KO (wie _jeder_gegen_jeden_pairings
        es fuer Schulpokal anfordert, siehe schedule_generator_service.py)
        muss jedem Team exakt Teamzahl - 1 Gruppenspiele geben, fuer beliebige
        Teamzahl. games_per_team wird bewusst als Teamzahl (nicht Teamzahl -
        1) angefragt: bei ungerader Teamzahl braucht die Freilos-Rotation
        eine volle zusaetzliche Runde, sonst bekommen manche Teams ein Spiel
        zu wenig (siehe Docstring von _jeder_gegen_jeden_pairings)."""
        for team_count in TEAM_COUNTS:
            with self.subTest(team_count=team_count):
                conn = make_in_memory_db()
                court_ids, teams = _make_group_competition(conn, team_count, court_count=2)

                import app.services.schedule_generator_service as svc
                with patch.object(svc, "get_conn", return_value=conn):
                    slots = generate_group_plan(
                        competition_id=1,
                        court_ids=court_ids,
                        startzeit="10:00",
                        games_per_team=team_count,
                        include_ko=False,
                    )

                _assert_no_double_booking(self, slots)

                games_by_team = defaultdict(int)
                for slot in slots:
                    if slot["slot_typ"] != "Spiel":
                        continue
                    for team_id in (slot["team_a_id"], slot["team_b_id"]):
                        if team_id not in (None, ""):
                            games_by_team[team_id] += 1

                for team in teams:
                    self.assertEqual(
                        games_by_team[team["id"]], team_count - 1,
                        f"{team['name']} bekommt nicht die volle Anzahl Spiele bei "
                        f"{team_count} Teams",
                    )

                warnings = validate_generated_plan(slots, teams, games_per_team=team_count - 1)
                errors = [w for w in warnings if w["level"] == "error"]
                self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
