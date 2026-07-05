from collections import defaultdict
from datetime import datetime, timedelta
from itertools import permutations
from typing import List

from app.database import get_conn
from app.services.schedule_location_service import (
    filter_court_ids_for_competition,
    schedule_planning_available,
)
from app.services.schedule_time_service import get_competition_timing, get_game_end_time


def _generate_balanced_pairings(team_names, games_per_team):
    """Faires Rundenverfahren (Circle-Method) statt einer Greedy-Paarung in
    Namensreihenfolge: Ein Team fest, alle anderen rotieren jede Runde um eine
    Position; bei ungerader Teamzahl bekommt pro Runde ein Team ein Freilos.
    Damit bekommt in jeder der ersten `games_per_team` Runden jedes Team genau
    ein Spiel (Ausnahme: das Team mit Freilos in dieser Runde) und nie zweimal
    denselben Gegner - im Unterschied zur vorherigen Greedy-Variante, die z.B.
    bei 10 Teams/2 Spielen pro Team ein Team komplett ohne Spiel lassen konnte,
    waehrend alle anderen ihre volle Spielanzahl bekamen (unfaire Tabelle als
    Folge, da die Platzierung rein aus den tatsaechlich gespielten Spielen
    berechnet wird)."""
    teams = list(team_names)
    if len(teams) < 2:
        return []

    bye = object()
    if len(teams) % 2 == 1:
        teams = teams + [bye]

    team_count = len(teams)
    max_rounds = team_count - 1
    rounds_needed = min(games_per_team, max_rounds)

    fixed = teams[0]
    rotating = teams[1:]
    pairings = []

    for _ in range(rounds_needed):
        round_teams = [fixed] + rotating
        for i in range(team_count // 2):
            team_a, team_b = round_teams[i], round_teams[team_count - 1 - i]
            if team_a is not bye and team_b is not bye:
                pairings.append((team_a, team_b))
        rotating = rotating[-1:] + rotating[:-1]

    return pairings


def _assign_courts_for_round(round_selections, court_ids, last_court_by_team):
    """Verteilt die für diesen Durchgang bereits feststehenden Paarungen auf die
    Felder so, dass Teams nach Möglichkeit auf ihrem zuletzt genutzten Feld
    bleiben, statt unnötig zwischen Feldern zu wechseln. Welche Teams
    gegeneinander antreten und wann, bleibt davon unberührt - es wird nur
    entschieden, welches Feld welche bereits gewählte Paarung bekommt."""
    best_combo = None
    best_penalty = None

    for court_combo in permutations(court_ids, len(round_selections)):
        penalty = 0
        for court_id, (team_a, team_b, _gruppe, _index) in zip(court_combo, round_selections):
            for team in (team_a, team_b):
                last_court = last_court_by_team.get(team)
                if last_court is not None and last_court != court_id:
                    penalty += 1

        if best_penalty is None or penalty < best_penalty:
            best_penalty = penalty
            best_combo = court_combo
            if best_penalty == 0:
                break

    return list(zip(best_combo, round_selections))


def generate_group_plan(
    competition_id: int,
    court_ids: List[int],
    startzeit: str,
    games_per_team: int,
    include_ko: bool,
):
    with get_conn() as conn:
        competition = conn.execute(
            "SELECT * FROM competitions WHERE id = ?", (competition_id,)
        ).fetchone()
        if competition is None or not schedule_planning_available(competition):
            return []

        all_courts = conn.execute(
            "SELECT * FROM courts WHERE active = 1 ORDER BY name"
        ).fetchall()
        court_ids = filter_court_ids_for_competition(
            court_ids, all_courts, competition
        )
        if not court_ids:
            return []

        explicit_teams = conn.execute("""
            SELECT t.* FROM teams t
            JOIN competition_teams ct ON ct.team_id = t.id
            WHERE ct.competition_id = ?
            ORDER BY t.jahrgang, t.name
        """, (competition_id,)).fetchall()
        if explicit_teams:
            teams = list(explicit_teams)
        else:
            teams = conn.execute("""
                SELECT *
                FROM teams
                WHERE active = 1
                  AND jahrgang = ?
                ORDER BY name
            """, (competition["jahrgang"],)).fetchall()

    selected_ids = set(court_ids)
    court_order = {court_id: index for index, court_id in enumerate(court_ids)}
    courts = [court for court in all_courts if court["id"] in selected_ids]
    courts.sort(key=lambda court: court_order.get(court["id"], 999))

    team_names = [team["name"] for team in teams]
    team_ids = {team["name"]: team["id"] for team in teams}
    court_map = {court["id"]: court["name"] for court in courts}
    timing = get_competition_timing(competition)
    slot_interval_minutes = timing["slot_interval_minutes"]

    if len(team_names) < 2 or len(court_ids) < 1:
        return []

    pairings = []

    if len(team_names) == 7 and games_per_team == 2:
        group_a = team_names[:3]
        group_b = team_names[3:7]

        pairings = [
            (group_a[0], group_a[1], "A"),
            (group_b[0], group_b[1], "B"),
            (group_a[0], group_a[2], "A"),
            (group_b[2], group_b[3], "B"),
            (group_a[1], group_a[2], "A"),
            (group_b[0], group_b[2], "B"),
            (group_b[1], group_b[3], "B"),
        ]

    elif len(team_names) == 6 and games_per_team == 2:
        group_a = team_names[:3]
        group_b = team_names[3:6]

        pairings = [
            (group_a[0], group_a[1], "A"),
            (group_b[0], group_b[1], "B"),
            (group_a[0], group_a[2], "A"),
            (group_b[0], group_b[2], "B"),
            (group_a[1], group_a[2], "A"),
            (group_b[1], group_b[2], "B"),
        ]

    else:
        for team_a, team_b in _generate_balanced_pairings(team_names, games_per_team):
            pairings.append((team_a, team_b, ""))

    proposed_slots = []
    current_time = datetime.strptime(startzeit, "%H:%M")

    remaining_pairings = pairings[:]
    last_round_by_team = {team_name: -1000 for team_name in team_names}
    last_court_by_team = {}
    round_index = 0

    while remaining_pairings:
        time_value = current_time.strftime("%H:%M")
        used_teams = set()
        round_selections = []

        for _ in court_ids:
            candidate_scores = []
            for index, (team_a, team_b, _gruppe) in enumerate(remaining_pairings):
                if team_a in used_teams or team_b in used_teams:
                    continue

                gap_a = round_index - last_round_by_team.get(team_a, -1000)
                gap_b = round_index - last_round_by_team.get(team_b, -1000)
                consecutive_count = int(gap_a == 1) + int(gap_b == 1)
                candidate_scores.append((
                    consecutive_count,
                    -min(gap_a, gap_b),
                    -(gap_a + gap_b),
                    index,
                ))

            if not candidate_scores:
                break

            candidate_scores.sort()
            selected_index = candidate_scores[0][3]
            team_a, team_b, gruppe = remaining_pairings[selected_index]
            round_selections.append((team_a, team_b, gruppe, selected_index))
            used_teams.add(team_a)
            used_teams.add(team_b)

        if not round_selections:
            break

        for court_id, (team_a, team_b, gruppe, _index) in _assign_courts_for_round(
            round_selections, court_ids, last_court_by_team
        ):
            proposed_slots.append({
                "competition_id": competition_id,
                "competition_name": competition["name"],
                "startzeit": time_value,
                "slot_typ": "Spiel",
                "court_id": court_id,
                "court_name": court_map.get(court_id, ""),
                "phase": "Gruppenphase",
                "gruppe": gruppe,
                "team_a_id": team_ids[team_a],
                "team_b_id": team_ids[team_b],
                "team_a": team_a,
                "team_b": team_b,
                "note": "",
            })

            last_round_by_team[team_a] = round_index
            last_round_by_team[team_b] = round_index
            last_court_by_team[team_a] = court_id
            last_court_by_team[team_b] = court_id

        for index in sorted((selection[3] for selection in round_selections), reverse=True):
            remaining_pairings.pop(index)

        current_time += timedelta(minutes=slot_interval_minutes)
        round_index += 1

    if include_ko:
        hf_time = current_time.strftime("%H:%M")

        proposed_slots.append({
            "competition_id": competition_id,
            "competition_name": competition["name"],
            "startzeit": hf_time,
            "slot_typ": "Spiel",
            "court_id": court_ids[0],
            "court_name": court_map.get(court_ids[0], ""),
            "phase": "Halbfinale",
            "gruppe": "",
            "team_a_id": "",
            "team_b_id": "",
            "team_a": "?",
            "team_b": "?",
            "note": "HF1: 1. Gruppe A gegen 2. Gruppe B",
        })
        if len(court_ids) > 1:
            proposed_slots.append({
                "competition_id": competition_id,
                "competition_name": competition["name"],
                "startzeit": hf_time,
                "slot_typ": "Spiel",
                "court_id": court_ids[1],
                "court_name": court_map.get(court_ids[1], ""),
                "phase": "Halbfinale",
                "gruppe": "",
                "team_a_id": "",
                "team_b_id": "",
                "team_a": "?",
                "team_b": "?",
                "note": "HF2: 1. Gruppe B gegen 2. Gruppe A",
            })
        current_time += timedelta(minutes=slot_interval_minutes)

        final_time = current_time.strftime("%H:%M")

        proposed_slots.append({
            "competition_id": competition_id,
            "competition_name": competition["name"],
            "startzeit": final_time,
            "slot_typ": "Spiel",
            "court_id": court_ids[0],
            "court_name": court_map.get(court_ids[0], ""),
            "phase": "Finale",
            "gruppe": "",
            "team_a_id": "",
            "team_b_id": "",
            "team_a": "?",
            "team_b": "?",
            "note": "Finale: Sieger HF1 gegen Sieger HF2",
        })
        if len(court_ids) > 1:
            proposed_slots.append({
                "competition_id": competition_id,
                "competition_name": competition["name"],
                "startzeit": final_time,
                "slot_typ": "Spiel",
                "court_id": court_ids[1],
                "court_name": court_map.get(court_ids[1], ""),
                "phase": "Spiel um Platz 3",
                "gruppe": "",
                "team_a_id": "",
                "team_b_id": "",
                "team_a": "?",
                "team_b": "?",
                "note": "Spiel um Platz 3: Verlierer HF1 gegen Verlierer HF2",
            })
    for slot in proposed_slots:
        slot["game_end_time"] = get_game_end_time(
            slot["startzeit"], timing["game_duration_minutes"]
        )
    return proposed_slots


def validate_generated_plan(proposed_slots, expected_teams, games_per_team: int):
    warnings = []
    game_slots = [slot for slot in proposed_slots if slot["slot_typ"] == "Spiel"]
    group_games = [slot for slot in game_slots if slot["phase"] == "Gruppenphase"]

    games_by_time_and_team = defaultdict(list)
    game_times_by_team = defaultdict(set)
    team_names = {team["id"]: team["name"] for team in expected_teams}

    for slot in game_slots:
        for team_id_key, team_name_key in (("team_a_id", "team_a"), ("team_b_id", "team_b")):
            team_id = slot[team_id_key]
            if team_id in (None, ""):
                continue

            team_names.setdefault(team_id, slot[team_name_key])
            games_by_time_and_team[(slot["startzeit"], team_id)].append(slot)
            game_times_by_team[team_id].add(slot["startzeit"])

    for (startzeit, team_id), slots in games_by_time_and_team.items():
        if len(slots) > 1:
            warnings.append({
                "level": "error",
                "message": f'{team_names[team_id]} ist um {startzeit} gleichzeitig für mehrere Spiele eingeplant.',
            })

    all_times = sorted({slot["startzeit"] for slot in proposed_slots})
    time_positions = {startzeit: index for index, startzeit in enumerate(all_times)}

    for team_id, startzeiten in game_times_by_team.items():
        positions = sorted(time_positions[startzeit] for startzeit in startzeiten)

        for previous, current in zip(positions, positions[1:]):
            if current == previous + 1:
                warnings.append({
                    "level": "warning",
                    "message": f'{team_names[team_id]} spielt in zwei direkt aufeinanderfolgenden Zeitslots.',
                })
                break

    group_game_counts = defaultdict(int)

    for slot in group_games:
        for team_id_key in ("team_a_id", "team_b_id"):
            team_id = slot[team_id_key]
            if team_id not in (None, ""):
                group_game_counts[team_id] += 1

    for team in expected_teams:
        actual_games = group_game_counts[team["id"]]
        if actual_games != games_per_team:
            warnings.append({
                "level": "error",
                "message": (
                    f'{team["name"]} erhält {actual_games} statt der geforderten '
                    f'{games_per_team} Gruppenspiele.'
                ),
            })

    semifinals = [slot for slot in game_slots if slot["phase"] == "Halbfinale"]
    groups = {slot["gruppe"] for slot in group_games if slot["gruppe"]}

    if semifinals and len(groups) < 2:
        warnings.append({
            "level": "error",
            "message": "Halbfinals können nur mit mindestens zwei Gruppen erzeugt werden.",
        })

    if semifinals and len(semifinals) != 2:
        warnings.append({
            "level": "error",
            "message": f"Für die Finalrunde werden genau zwei Halbfinals benötigt; geplant sind {len(semifinals)}.",
        })

    if len(semifinals) >= 2:
        semifinal_times = {slot["startzeit"] for slot in semifinals}
        if len(semifinal_times) != 1:
            warnings.append({
                "level": "error",
                "message": "Beide Halbfinals müssen zur gleichen Uhrzeit stattfinden.",
            })

    return warnings


def group_phase_finished(competition_id: int):
    with get_conn() as conn:
        unfinished = conn.execute("""
            SELECT COUNT(*) AS n
            FROM slots
            WHERE competition_id = ?
              AND slot_typ = 'Spiel'
              AND phase = 'Gruppenphase'
              AND status != 'beendet'
        """, (competition_id,)).fetchone()["n"]

        total = conn.execute("""
            SELECT COUNT(*) AS n
            FROM slots
            WHERE competition_id = ?
              AND slot_typ = 'Spiel'
              AND phase = 'Gruppenphase'
        """, (competition_id,)).fetchone()["n"]

    return total > 0 and unfinished == 0


def semifinals_finished(competition_id: int):
    with get_conn() as conn:
        unfinished = conn.execute("""
            SELECT COUNT(*) AS n
            FROM slots
            WHERE competition_id = ?
              AND slot_typ = 'Spiel'
              AND phase = 'Halbfinale'
              AND status != 'beendet'
        """, (competition_id,)).fetchone()["n"]

        total = conn.execute("""
            SELECT COUNT(*) AS n
            FROM slots
            WHERE competition_id = ?
              AND slot_typ = 'Spiel'
              AND phase = 'Halbfinale'
        """, (competition_id,)).fetchone()["n"]

    return total >= 2 and unfinished == 0


def get_already_played_ko_targets(competition_id: int, phase: str):
    """Liefert die Zielspiele einer K.-o.-Phase (Halbfinale bei
    generate_semifinals; Finale/Spiel um Platz 3 bei generate_finals), die
    bereits ein gespeichertes Ergebnis haben. Grundlage für die
    Sicherheitsabfrage vor dem Überschreiben durch eine Neu-Besetzung."""
    if phase == "Halbfinale":
        target_phases = ("Halbfinale",)
    else:
        target_phases = ("Finale", "Spiel um Platz 3", "Kleines Finale", "Platzierung")

    placeholders = ", ".join("?" for _ in target_phases)
    with get_conn() as conn:
        rows = conn.execute(f"""
            SELECT slots.*, ta.name AS team_a, tb.name AS team_b
            FROM slots
            LEFT JOIN teams ta ON ta.id = slots.team_a_id
            LEFT JOIN teams tb ON tb.id = slots.team_b_id
            WHERE competition_id = ?
              AND slot_typ = 'Spiel'
              AND phase IN ({placeholders})
              AND status = 'beendet'
            ORDER BY startzeit, court_id, id
        """, (competition_id, *target_phases)).fetchall()

    return [dict(row) for row in rows]


KO_DECISIVE_PHASES = ("Halbfinale", "Finale", "Spiel um Platz 3", "Kleines Finale", "Platzierung")


def get_next_phase_names(phase: str):
    """Welche Phase(n) auf `phase` aufbauen (deren Besetzung von `phase`
    abhängt). Leer, wenn `phase` eine Endphase ist (Finale/Platz 3)."""
    if phase == "Gruppenphase":
        return ("Halbfinale",)
    if phase == "Halbfinale":
        return ("Finale", "Spiel um Platz 3", "Kleines Finale", "Platzierung")
    return ()


def is_next_phase_started(competition_id: int, phase: str) -> bool:
    """True, wenn die auf `phase` aufbauende K.-o.-Phase bereits läuft oder
    beendet ist. Grundlage dafür, eine nachträgliche Korrektur eines längst
    gespielten Gruppen-/Halbfinalspiels zu sperren, sobald die nächste Runde
    schon begonnen hat - ein Nachholen der bereits gespielten Folgespiele ist
    aus Zeitgründen am Turniertag nicht vorgesehen."""
    next_phases = get_next_phase_names(phase)
    if not next_phases:
        return False

    placeholders = ", ".join("?" for _ in next_phases)
    with get_conn() as conn:
        row = conn.execute(f"""
            SELECT COUNT(*) AS n FROM slots
            WHERE competition_id = ? AND slot_typ = 'Spiel'
              AND phase IN ({placeholders})
              AND status IN ('läuft', 'beendet')
        """, (competition_id, *next_phases)).fetchone()

    return row["n"] > 0


def get_winner_loser(slot):
    if slot["score_a"] is None or slot["score_b"] is None:
        return None, None

    if slot["score_a"] > slot["score_b"]:
        return slot["team_a_id"], slot["team_b_id"]

    if slot["score_b"] > slot["score_a"]:
        return slot["team_b_id"], slot["team_a_id"]

    return None, None