from collections import defaultdict
from datetime import datetime, timedelta
from math import isfinite
from typing import List, Optional

from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.database import init_db, get_conn

app = FastAPI(title="Sportfest-App")

app.mount("/static", StaticFiles(directory="/app/app/static"), name="static")
templates = Jinja2Templates(directory="/app/app/templates")

def get_app_version():
    try:
        with open("/app/VERSION", "r", encoding="utf-8") as version_file:
            return version_file.read().strip()
    except FileNotFoundError:
        return "dev"

templates.env.globals["app_version"] = get_app_version


def parse_competition_id(value):
    if value in (None, "", "None"):
        return None
    return int(value)


def get_unique_competition_name(conn, original_name: str):
    base_name = f"{original_name} Kopie"
    candidate = base_name
    copy_number = 2
    while conn.execute("SELECT 1 FROM competitions WHERE name = ?", (candidate,)).fetchone():
        candidate = f"{base_name} {copy_number}"
        copy_number += 1
    return candidate


def get_unique_event_name(conn, original_name: str):
    base_name = f"{original_name} Kopie"
    candidate = base_name
    copy_number = 2
    while conn.execute("SELECT 1 FROM events WHERE name = ?", (candidate,)).fetchone():
        candidate = f"{base_name} {copy_number}"
        copy_number += 1
    return candidate


def copy_competition_disciplines(conn, source_competition_id: int, target_competition_id: int):
    disciplines = conn.execute("""
        SELECT name, sort_order, unit, scoring_direction
        FROM competition_disciplines
        WHERE competition_id = ?
        ORDER BY sort_order, id
    """, (source_competition_id,)).fetchall()
    for discipline in disciplines:
        conn.execute("""
            INSERT INTO competition_disciplines (
                competition_id, name, sort_order, unit, scoring_direction
            ) VALUES (?, ?, ?, ?, ?)
        """, (
            target_competition_id, discipline["name"], discipline["sort_order"],
            discipline["unit"], discipline["scoring_direction"],
        ))


def calculate_sixkampf_ranking(competition_id: int):
    with get_conn() as conn:
        participants = conn.execute("""
            SELECT * FROM sixkampf_participants
            WHERE competition_id = ?
            ORDER BY class_name, participant_number
        """, (competition_id,)).fetchall()
        disciplines = conn.execute("""
            SELECT * FROM competition_disciplines
            WHERE competition_id = ?
            ORDER BY sort_order, id
        """, (competition_id,)).fetchall()
        results = conn.execute("""
            SELECT dr.* FROM discipline_results dr
            JOIN sixkampf_participants p ON p.id = dr.participant_id
            WHERE p.competition_id = ?
        """, (competition_id,)).fetchall()

    participant_count = len(participants)
    totals = {participant["id"]: 0 for participant in participants}
    values_by_discipline = defaultdict(list)
    for result in results:
        values_by_discipline[result["discipline_id"]].append(
            (result["participant_id"], result["value"])
        )

    for discipline in disciplines:
        discipline_values = values_by_discipline[discipline["id"]]
        reverse = discipline["scoring_direction"] == "higher"
        discipline_values.sort(key=lambda item: item[1], reverse=reverse)
        previous_value = None
        rank = 0
        for index, (participant_id, value) in enumerate(discipline_values, start=1):
            if previous_value is None or value != previous_value:
                rank = index
                previous_value = value
            totals[participant_id] += max(participant_count - rank + 1, 1)

    ranking = [{
        "participant": participant,
        "total_points": totals[participant["id"]],
        "placement": 0,
    } for participant in participants]
    ranking.sort(key=lambda row: (
        -row["total_points"],
        row["participant"]["class_name"].lower(),
        row["participant"]["participant_number"],
    ))
    previous_points = None
    placement = 0
    for index, row in enumerate(ranking, start=1):
        if previous_points is None or row["total_points"] != previous_points:
            placement = index
            previous_points = row["total_points"]
        row["placement"] = placement
    return ranking


@app.on_event("startup")
def startup():
    init_db()


def get_active_competitions():
    with get_conn() as conn:
        return conn.execute("""
            SELECT *
            FROM competitions
            WHERE status != 'archiviert'
            ORDER BY jahrgang, name
        """).fetchall()


def get_all_competitions():
    with get_conn() as conn:
        return conn.execute("""
            SELECT *
            FROM competitions
            ORDER BY
                CASE WHEN status = 'archiviert' THEN 1 ELSE 0 END,
                jahrgang,
                name
        """).fetchall()


def fetch_dashboard_data():
    with get_conn() as conn:
        competitions = conn.execute("""
            SELECT *
            FROM competitions
            WHERE status != 'archiviert'
            ORDER BY jahrgang, name
        """).fetchall()

        teams = conn.execute("SELECT * FROM teams WHERE active = 1 ORDER BY jahrgang, name").fetchall()
        courts = conn.execute("SELECT * FROM courts WHERE active = 1 ORDER BY name").fetchall()

        running = conn.execute("""
            SELECT slots.*, c.name AS competition_name, c.sportart, c.jahrgang,
                   co.name AS court_name, ta.name AS team_a, tb.name AS team_b
            FROM slots
            JOIN competitions c ON c.id = slots.competition_id
            LEFT JOIN courts co ON co.id = slots.court_id
            LEFT JOIN teams ta ON ta.id = slots.team_a_id
            LEFT JOIN teams tb ON tb.id = slots.team_b_id
            WHERE slots.status = 'läuft'
              AND c.status != 'archiviert'
            ORDER BY slots.startzeit, slots.court_id
        """).fetchall()

        upcoming = conn.execute("""
            SELECT slots.*, c.name AS competition_name, co.name AS court_name,
                   ta.name AS team_a, tb.name AS team_b
            FROM slots
            JOIN competitions c ON c.id = slots.competition_id
            LEFT JOIN courts co ON co.id = slots.court_id
            LEFT JOIN teams ta ON ta.id = slots.team_a_id
            LEFT JOIN teams tb ON tb.id = slots.team_b_id
            WHERE slots.status = 'geplant'
              AND c.status != 'archiviert'
            ORDER BY slots.startzeit, slots.court_id
            LIMIT 8
        """).fetchall()

        ended_count = conn.execute("""
            SELECT COUNT(*) AS n
            FROM slots
            JOIN competitions c ON c.id = slots.competition_id
            WHERE slots.status = 'beendet'
              AND c.status != 'archiviert'
        """).fetchone()["n"]

    return {
        "competitions": competitions,
        "teams": teams,
        "courts": courts,
        "running": running,
        "upcoming": upcoming,
        "ended_count": ended_count,
    }


def get_all_slots(competition_id: Optional[int] = None):
    query = """
        SELECT slots.*, c.name AS competition_name, c.sportart, c.jahrgang,
               co.name AS court_name, ta.name AS team_a, tb.name AS team_b
        FROM slots
        JOIN competitions c ON c.id = slots.competition_id
        LEFT JOIN courts co ON co.id = slots.court_id
        LEFT JOIN teams ta ON ta.id = slots.team_a_id
        LEFT JOIN teams tb ON tb.id = slots.team_b_id
        WHERE c.status != 'archiviert'
    """
    params = []

    if competition_id:
        query += " AND slots.competition_id = ?"
        params.append(competition_id)

    query += """
    ORDER BY
        slots.court_id,
        slots.sort_order,
        slots.startzeit,
        slots.id
    """

    with get_conn() as conn:
        return conn.execute(query, params).fetchall()


def get_slots_grouped_by_court(competition_id: Optional[int] = None):
    with get_conn() as conn:
        courts = conn.execute("SELECT * FROM courts WHERE active = 1 ORDER BY name").fetchall()

    slots = get_all_slots(competition_id)
    grouped = {court["id"]: {"court": court, "slots": []} for court in courts}
    without_court = {"court": {"id": "", "name": "Ohne Feld"}, "slots": []}

    for slot in slots:
        if slot["court_id"] in grouped:
            grouped[slot["court_id"]]["slots"].append(slot)
        else:
            without_court["slots"].append(slot)

    return list(grouped.values()) + [without_court]


def calculate_direct_comparison(team_a: str, team_b: str, slots, competition):
    points = {team_a: 0.0, team_b: 0.0}
    diff = {team_a: 0, team_b: 0}
    goals = {team_a: 0, team_b: 0}

    for slot in slots:
        if slot["team_a"] is None or slot["team_b"] is None:
            continue

        if {slot["team_a"], slot["team_b"]} != {team_a, team_b}:
            continue

        score_a = slot["score_a"]
        score_b = slot["score_b"]

        if score_a is None or score_b is None:
            continue

        a = slot["team_a"]
        b = slot["team_b"]

        goals[a] += score_a
        goals[b] += score_b
        diff[a] += score_a - score_b
        diff[b] += score_b - score_a

        if score_a > score_b:
            points[a] += competition["points_win"]
            points[b] += competition["points_loss"]
        elif score_b > score_a:
            points[b] += competition["points_win"]
            points[a] += competition["points_loss"]
        else:
            points[a] += competition["points_draw"]
            points[b] += competition["points_draw"]

    if points[team_a] != points[team_b]:
        return points[team_a] - points[team_b]
    if diff[team_a] != diff[team_b]:
        return diff[team_a] - diff[team_b]
    if goals[team_a] != goals[team_b]:
        return goals[team_a] - goals[team_b]

    return 0


def sort_table_rows(rows, slots, competition):
    rows.sort(key=lambda r: (-r["pkt"], -r["diff"], -r["plus"], r["team"].lower()))

    i = 0
    while i < len(rows) - 1:
        current = rows[i]
        next_row = rows[i + 1]

        same_basic_rank = (
            current["pkt"] == next_row["pkt"]
            and current["diff"] == next_row["diff"]
            and current["plus"] == next_row["plus"]
        )

        if same_basic_rank:
            direct = calculate_direct_comparison(current["team"], next_row["team"], slots, competition)
            if direct < 0:
                rows[i], rows[i + 1] = rows[i + 1], rows[i]

        i += 1

    for row in rows:
        if float(row["pkt"]).is_integer():
            row["pkt_display"] = str(int(row["pkt"]))
        else:
            row["pkt_display"] = str(row["pkt"]).replace(".", ",")

    return rows


def calculate_table(competition_id: int):
    with get_conn() as conn:
        competition = conn.execute(
            "SELECT * FROM competitions WHERE id = ?",
            (competition_id,)
        ).fetchone()

        if competition is None:
            return []

        teams = conn.execute("""
            SELECT *
            FROM teams
            WHERE active = 1
              AND jahrgang = ?
            ORDER BY name
        """, (competition["jahrgang"],)).fetchall()

        slots = conn.execute("""
            SELECT slots.*, ta.name AS team_a, tb.name AS team_b
            FROM slots
            LEFT JOIN teams ta ON ta.id = slots.team_a_id
            LEFT JOIN teams tb ON tb.id = slots.team_b_id
            WHERE competition_id = ?
              AND slot_typ = 'Spiel'
              AND status = 'beendet'
              AND team_a_id IS NOT NULL
              AND team_b_id IS NOT NULL
        """, (competition_id,)).fetchall()

    table = {}

    for team in teams:
        table[team["name"]] = {
            "team_id": team["id"],
            "team": team["name"],
            "sp": 0,
            "s": 0,
            "u": 0,
            "n": 0,
            "plus": 0,
            "minus": 0,
            "diff": 0,
            "pkt": 0.0,
        }

    for slot in slots:
        team_a = slot["team_a"]
        team_b = slot["team_b"]
        score_a = slot["score_a"]
        score_b = slot["score_b"]

        if team_a is None or team_b is None or score_a is None or score_b is None:
            continue

        if team_a not in table:
            table[team_a] = {"team_id": slot["team_a_id"], "team": team_a, "sp": 0, "s": 0, "u": 0, "n": 0, "plus": 0, "minus": 0, "diff": 0, "pkt": 0.0}
        if team_b not in table:
            table[team_b] = {"team_id": slot["team_b_id"], "team": team_b, "sp": 0, "s": 0, "u": 0, "n": 0, "plus": 0, "minus": 0, "diff": 0, "pkt": 0.0}

        table[team_a]["sp"] += 1
        table[team_b]["sp"] += 1
        table[team_a]["plus"] += score_a
        table[team_a]["minus"] += score_b
        table[team_b]["plus"] += score_b
        table[team_b]["minus"] += score_a

        if score_a > score_b:
            table[team_a]["s"] += 1
            table[team_b]["n"] += 1
            table[team_a]["pkt"] += competition["points_win"]
            table[team_b]["pkt"] += competition["points_loss"]
        elif score_a < score_b:
            table[team_b]["s"] += 1
            table[team_a]["n"] += 1
            table[team_b]["pkt"] += competition["points_win"]
            table[team_a]["pkt"] += competition["points_loss"]
        else:
            table[team_a]["u"] += 1
            table[team_b]["u"] += 1
            table[team_a]["pkt"] += competition["points_draw"]
            table[team_b]["pkt"] += competition["points_draw"]

    rows = []

    for row in table.values():
        row["diff"] = row["plus"] - row["minus"]
        rows.append(row)

    return sort_table_rows(rows, slots, competition)


def calculate_group_table(competition_id: int, gruppe: str):
    with get_conn() as conn:
        competition = conn.execute(
            "SELECT * FROM competitions WHERE id = ?",
            (competition_id,)
        ).fetchone()

        slots = conn.execute("""
            SELECT slots.*, ta.name AS team_a, tb.name AS team_b
            FROM slots
            LEFT JOIN teams ta ON ta.id = slots.team_a_id
            LEFT JOIN teams tb ON tb.id = slots.team_b_id
            WHERE competition_id = ?
              AND slot_typ = 'Spiel'
              AND phase = 'Gruppenphase'
              AND gruppe = ?
              AND status = 'beendet'
              AND team_a_id IS NOT NULL
              AND team_b_id IS NOT NULL
        """, (competition_id, gruppe)).fetchall()

    table = {}

    for slot in slots:
        for side in ("a", "b"):
            team_name = slot[f"team_{side}"]
            team_id = slot[f"team_{side}_id"]

            if team_name not in table:
                table[team_name] = {
                    "team_id": team_id,
                    "team": team_name,
                    "sp": 0,
                    "s": 0,
                    "u": 0,
                    "n": 0,
                    "plus": 0,
                    "minus": 0,
                    "diff": 0,
                    "pkt": 0.0,
                }

    for slot in slots:
        team_a = slot["team_a"]
        team_b = slot["team_b"]
        score_a = slot["score_a"]
        score_b = slot["score_b"]

        if team_a is None or team_b is None or score_a is None or score_b is None:
            continue

        table[team_a]["sp"] += 1
        table[team_b]["sp"] += 1
        table[team_a]["plus"] += score_a
        table[team_a]["minus"] += score_b
        table[team_b]["plus"] += score_b
        table[team_b]["minus"] += score_a

        if score_a > score_b:
            table[team_a]["s"] += 1
            table[team_b]["n"] += 1
            table[team_a]["pkt"] += competition["points_win"]
            table[team_b]["pkt"] += competition["points_loss"]
        elif score_a < score_b:
            table[team_b]["s"] += 1
            table[team_a]["n"] += 1
            table[team_b]["pkt"] += competition["points_win"]
            table[team_a]["pkt"] += competition["points_loss"]
        else:
            table[team_a]["u"] += 1
            table[team_b]["u"] += 1
            table[team_a]["pkt"] += competition["points_draw"]
            table[team_b]["pkt"] += competition["points_draw"]

    rows = []

    for row in table.values():
        row["diff"] = row["plus"] - row["minus"]
        rows.append(row)

    return sort_table_rows(rows, slots, competition)


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


def get_winner_loser(slot):
    if slot["score_a"] is None or slot["score_b"] is None:
        return None, None

    if slot["score_a"] > slot["score_b"]:
        return slot["team_a_id"], slot["team_b_id"]

    if slot["score_b"] > slot["score_a"]:
        return slot["team_b_id"], slot["team_a_id"]

    return None, None


def fetch_beamer_data():
    with get_conn() as conn:
        active_competition = conn.execute("""
            SELECT *
            FROM competitions
            WHERE status = 'läuft'
            ORDER BY jahrgang, name
            LIMIT 1
        """).fetchone()

    competition_id = active_competition["id"] if active_competition else None
    slots = get_all_slots(competition_id)

    running_slots = [
        slot for slot in slots
        if slot["slot_typ"] == "Spiel" and slot["status"] == "läuft"
    ]

    current = running_slots[0] if running_slots else None

    if current is None:
        for slot in slots:
            if slot["status"] == "geplant":
                current = slot
                break

    table_rows = calculate_table(competition_id) if competition_id else []

    next_slot = None
    if current:
        for slot in slots:
            if slot["status"] == "geplant" and slot["startzeit"] > current["startzeit"]:
                next_slot = slot
                break

    with get_conn() as conn:
        courts = conn.execute("SELECT * FROM courts WHERE active = 1 ORDER BY name").fetchall()

    court_summaries = []
    for court in courts:
        court_slots = [
            slot for slot in slots
            if slot["court_id"] == court["id"] and slot["slot_typ"] == "Spiel"
        ]
        open_slots = [
            slot for slot in court_slots
            if slot["status"] != "beendet"
        ]

        court_summaries.append({
            "court": court,
            "open_count": len(open_slots),
            "next": open_slots[0] if open_slots else None,
        })

    return {
        "current": current,
        "running_slots": running_slots,
        "next_slot": next_slot,
        "table_rows": table_rows,
        "court_summaries": court_summaries,
    }

def generate_group_plan(competition_id: int, court_ids: List[int], startzeit: str, slot_minutes: int, games_per_team: int, include_ko: bool):
    with get_conn() as conn:
        competition = conn.execute("SELECT * FROM competitions WHERE id = ?", (competition_id,)).fetchone()
        teams = conn.execute("""
            SELECT *
            FROM teams
            WHERE active = 1
              AND jahrgang = ?
            ORDER BY name
        """, (competition["jahrgang"],)).fetchall()

        courts = conn.execute(
            f"SELECT * FROM courts WHERE id IN ({','.join(['?'] * len(court_ids))}) ORDER BY name",
            court_ids
        ).fetchall()

    team_names = [team["name"] for team in teams]
    team_ids = {team["name"]: team["id"] for team in teams}
    court_map = {court["id"]: court["name"] for court in courts}

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
        counts = {team: 0 for team in team_names}

        for i, team_a in enumerate(team_names):
            for team_b in team_names[i + 1:]:
                if counts[team_a] < games_per_team and counts[team_b] < games_per_team:
                    pairings.append((team_a, team_b, ""))
                    counts[team_a] += 1
                    counts[team_b] += 1

    proposed_slots = []
    current_time = datetime.strptime(startzeit, "%H:%M")

    def add_empty_slots_for_unused_courts(used_courts, time_value, phase="Pause"):
        for court_id in court_ids:
            if court_id not in used_courts:
                proposed_slots.append({
                    "competition_id": competition_id,
                    "competition_name": competition["name"],
                    "startzeit": time_value,
                    "slot_typ": "Leer",
                    "court_id": court_id,
                    "court_name": court_map.get(court_id, ""),
                    "phase": phase,
                    "gruppe": "",
                    "team_a_id": "",
                    "team_b_id": "",
                    "team_a": "",
                    "team_b": "",
                    "note": "Feld frei / Puffer",
                })

    remaining_pairings = pairings[:]

    while remaining_pairings:
        time_value = current_time.strftime("%H:%M")
        used_courts = []
        used_teams = set()
        scheduled_indices = []

        for court_id in court_ids:
            selected_index = None

            for index, (team_a, team_b, gruppe) in enumerate(remaining_pairings):
                if team_a not in used_teams and team_b not in used_teams:
                    selected_index = index
                    break

            if selected_index is None:
                break

            team_a, team_b, gruppe = remaining_pairings[selected_index]

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

            used_courts.append(court_id)
            used_teams.add(team_a)
            used_teams.add(team_b)
            scheduled_indices.append(selected_index)

        for index in sorted(scheduled_indices, reverse=True):
            remaining_pairings.pop(index)

        add_empty_slots_for_unused_courts(used_courts, time_value, phase="Gruppenphase")
        current_time += timedelta(minutes=slot_minutes)

    if include_ko:
        hf_time = current_time.strftime("%H:%M")
        used_courts = []

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
        used_courts.append(court_ids[0])

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
            used_courts.append(court_ids[1])

        add_empty_slots_for_unused_courts(used_courts, hf_time, phase="Halbfinale")
        current_time += timedelta(minutes=slot_minutes)

        final_time = current_time.strftime("%H:%M")
        used_courts = []

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
        used_courts.append(court_ids[0])

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
            used_courts.append(court_ids[1])

        add_empty_slots_for_unused_courts(used_courts, final_time, phase="Finale")

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


@app.get("/")
def dashboard(request: Request):
    data = fetch_dashboard_data()
    return templates.TemplateResponse(request=request, name="dashboard.html", context=data)


@app.get("/spielplan")
def spielplan(request: Request, competition_id: str = ""):
    selected_competition_id = parse_competition_id(competition_id)

    competitions = get_active_competitions()
    groups = get_slots_grouped_by_court(selected_competition_id)

    return templates.TemplateResponse(
        request=request,
        name="spielplan.html",
        context={
            "groups": groups,
            "competitions": competitions,
            "selected_competition_id": selected_competition_id,
        }
    )


@app.get("/spielplan-bearbeiten")
def spielplan_bearbeiten(request: Request, competition_id: str = ""):
    selected_competition_id = parse_competition_id(competition_id)

    with get_conn() as conn:
        groups = get_slots_grouped_by_court(selected_competition_id)
        competitions = conn.execute("""
            SELECT *
            FROM competitions
            WHERE status != 'archiviert'
            ORDER BY jahrgang, name
        """).fetchall()
        courts = conn.execute("SELECT * FROM courts WHERE active = 1 ORDER BY name").fetchall()
        teams = conn.execute("SELECT * FROM teams WHERE active = 1 ORDER BY jahrgang, name").fetchall()

    ko_hint = None
    can_generate_semifinals = False
    can_generate_finals = False

    if selected_competition_id:
        can_generate_semifinals = group_phase_finished(selected_competition_id)
        can_generate_finals = semifinals_finished(selected_competition_id)

        if can_generate_semifinals:
            ko_hint = "Die Gruppenphase ist beendet. Die Halbfinals können automatisch besetzt werden."
        elif can_generate_finals:
            ko_hint = "Die Halbfinals sind beendet. Finale und Spiel um Platz 3 können automatisch besetzt werden."

    return templates.TemplateResponse(
        request=request,
        name="spielplan_bearbeiten.html",
        context={
            "groups": groups,
            "competitions": competitions,
            "courts": courts,
            "teams": teams,
            "selected_competition_id": selected_competition_id,
            "proposed_slots": [],
            "plan_warnings": [],
            "has_plan_errors": False,
            "ko_hint": ko_hint,
            "can_generate_semifinals": can_generate_semifinals,
            "can_generate_finals": can_generate_finals,
        }
    )


@app.post("/plan-generator/preview")
def plan_generator_preview(
    request: Request,
    competition_id: int = Form(...),
    court_ids: List[int] = Form(...),
    startzeit: str = Form(...),
    slot_minutes: int = Form(...),
    games_per_team: int = Form(...),
    include_ko: str = Form("0"),
):
    proposed_slots = generate_group_plan(
        competition_id=competition_id,
        court_ids=court_ids,
        startzeit=startzeit,
        slot_minutes=slot_minutes,
        games_per_team=games_per_team,
        include_ko=include_ko == "1",
    )

    with get_conn() as conn:
        groups = get_slots_grouped_by_court(competition_id)
        competitions = conn.execute("""
            SELECT *
            FROM competitions
            WHERE status != 'archiviert'
            ORDER BY jahrgang, name
        """).fetchall()
        courts = conn.execute("SELECT * FROM courts WHERE active = 1 ORDER BY name").fetchall()
        teams = conn.execute("SELECT * FROM teams WHERE active = 1 ORDER BY jahrgang, name").fetchall()
        competition = conn.execute(
            "SELECT * FROM competitions WHERE id = ?",
            (competition_id,)
        ).fetchone()
    expected_teams = [
        team for team in teams
        if competition is not None and team["jahrgang"] == competition["jahrgang"]
    ]
    plan_warnings = validate_generated_plan(proposed_slots, expected_teams, games_per_team)
    has_plan_errors = any(warning["level"] == "error" for warning in plan_warnings)

    return templates.TemplateResponse(
        request=request,
        name="spielplan_bearbeiten.html",
        context={
            "groups": groups,
            "competitions": competitions,
            "courts": courts,
            "teams": teams,
            "selected_competition_id": competition_id,
            "proposed_slots": proposed_slots,
            "plan_warnings": plan_warnings,
            "has_plan_errors": has_plan_errors,
            "ko_hint": None,
            "can_generate_semifinals": False,
            "can_generate_finals": False,
        }
    )


@app.post("/plan-generator/apply")
def plan_generator_apply(
    competition_id: List[int] = Form(...),
    startzeit: List[str] = Form(...),
    slot_typ: List[str] = Form(...),
    court_id: List[str] = Form(...),
    phase: List[str] = Form(...),
    gruppe: List[str] = Form(...),
    team_a_id: List[str] = Form(...),
    team_b_id: List[str] = Form(...),
    note: List[str] = Form(...),
):
    with get_conn() as conn:
        for i in range(len(startzeit)):
            conn.execute("""
                INSERT INTO slots (
                    competition_id, court_id, startzeit, slot_typ, phase, gruppe,
                    team_a_id, team_b_id, status, note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'geplant', ?)
            """, (
                competition_id[i],
                int(court_id[i]) if court_id[i] else None,
                startzeit[i],
                slot_typ[i],
                phase[i],
                gruppe[i] or None,
                int(team_a_id[i]) if team_a_id[i] else None,
                int(team_b_id[i]) if team_b_id[i] else None,
                note[i] or None,
            ))

        conn.commit()

    return RedirectResponse(f"/spielplan-bearbeiten?competition_id={competition_id[0]}", status_code=303)


@app.post("/competition/{competition_id}/generate-semifinals")
def generate_semifinals(competition_id: int):
    group_a = calculate_group_table(competition_id, "A")
    group_b = calculate_group_table(competition_id, "B")

    if len(group_a) < 2 or len(group_b) < 2:
        return RedirectResponse(f"/spielplan-bearbeiten?competition_id={competition_id}", status_code=303)

    team_1a = group_a[0]["team_id"]
    team_2a = group_a[1]["team_id"]
    team_1b = group_b[0]["team_id"]
    team_2b = group_b[1]["team_id"]

    with get_conn() as conn:
        semifinals = conn.execute("""
            SELECT *
            FROM slots
            WHERE competition_id = ?
              AND slot_typ = 'Spiel'
              AND phase = 'Halbfinale'
            ORDER BY startzeit, court_id, id
        """, (competition_id,)).fetchall()

        if len(semifinals) >= 1:
            conn.execute("""
                UPDATE slots
                SET team_a_id = ?,
                    team_b_id = ?,
                    score_a = NULL,
                    score_b = NULL,
                    status = 'geplant',
                    note = 'HF1: 1. Gruppe A gegen 2. Gruppe B'
                WHERE id = ?
            """, (team_1a, team_2b, semifinals[0]["id"]))

        if len(semifinals) >= 2:
            conn.execute("""
                UPDATE slots
                SET team_a_id = ?,
                    team_b_id = ?,
                    score_a = NULL,
                    score_b = NULL,
                    status = 'geplant',
                    note = 'HF2: 1. Gruppe B gegen 2. Gruppe A'
                WHERE id = ?
            """, (team_1b, team_2a, semifinals[1]["id"]))

        conn.commit()

    return RedirectResponse(f"/spielplan-bearbeiten?competition_id={competition_id}", status_code=303)


@app.post("/competition/{competition_id}/generate-finals")
def generate_finals(competition_id: int):
    with get_conn() as conn:
        semifinals = conn.execute("""
            SELECT *
            FROM slots
            WHERE competition_id = ?
              AND slot_typ = 'Spiel'
              AND phase = 'Halbfinale'
              AND status = 'beendet'
              AND team_a_id IS NOT NULL
              AND team_b_id IS NOT NULL
              AND score_a IS NOT NULL
              AND score_b IS NOT NULL
            ORDER BY startzeit, court_id, id
        """, (competition_id,)).fetchall()

        if len(semifinals) < 2:
            return RedirectResponse(f"/spielplan-bearbeiten?competition_id={competition_id}", status_code=303)

        winner_1, loser_1 = get_winner_loser(semifinals[0])
        winner_2, loser_2 = get_winner_loser(semifinals[1])

        if None in (winner_1, loser_1, winner_2, loser_2):
            return RedirectResponse(f"/spielplan-bearbeiten?competition_id={competition_id}", status_code=303)

        final_slot = conn.execute("""
            SELECT *
            FROM slots
            WHERE competition_id = ?
              AND slot_typ = 'Spiel'
              AND phase = 'Finale'
            ORDER BY startzeit, court_id, id
            LIMIT 1
        """, (competition_id,)).fetchone()

        small_final_slot = conn.execute("""
            SELECT *
            FROM slots
            WHERE competition_id = ?
              AND slot_typ = 'Spiel'
              AND phase IN ('Spiel um Platz 3', 'Kleines Finale', 'Platzierung')
            ORDER BY startzeit, court_id, id
            LIMIT 1
        """, (competition_id,)).fetchone()

        if final_slot:
            conn.execute("""
                UPDATE slots
                SET team_a_id = ?,
                    team_b_id = ?,
                    score_a = NULL,
                    score_b = NULL,
                    status = 'geplant',
                    note = 'Finale: Sieger HF1 gegen Sieger HF2'
                WHERE id = ?
            """, (winner_1, winner_2, final_slot["id"]))

        if small_final_slot:
            conn.execute("""
                UPDATE slots
                SET team_a_id = ?,
                    team_b_id = ?,
                    score_a = NULL,
                    score_b = NULL,
                    status = 'geplant',
                    note = 'Spiel um Platz 3: Verlierer HF1 gegen Verlierer HF2'
                WHERE id = ?
            """, (loser_1, loser_2, small_final_slot["id"]))
        elif final_slot:
            court = conn.execute("""
                SELECT id
                FROM courts
                WHERE active = 1
                  AND id != ?
                ORDER BY name
                LIMIT 1
            """, (final_slot["court_id"],)).fetchone()

            court_id = court["id"] if court else final_slot["court_id"]

            conn.execute("""
                INSERT INTO slots (
                    competition_id, court_id, startzeit, slot_typ, phase, gruppe,
                    team_a_id, team_b_id, status, note
                )
                VALUES (?, ?, ?, 'Spiel', 'Spiel um Platz 3', NULL, ?, ?, 'geplant',
                        'Spiel um Platz 3: Verlierer HF1 gegen Verlierer HF2')
            """, (
                competition_id,
                court_id,
                final_slot["startzeit"],
                loser_1,
                loser_2,
            ))

        conn.commit()

    return RedirectResponse(f"/spielplan-bearbeiten?competition_id={competition_id}", status_code=303)


@app.post("/slot/create")
def create_slot(
    competition_id: int = Form(...),
    startzeit: str = Form(...),
    slot_typ: str = Form(...),
    court_id: str = Form(""),
    phase: str = Form(...),
    gruppe: str = Form(""),
    team_a_id: str = Form(""),
    team_b_id: str = Form(""),
    note: str = Form("")
):
    court_value = int(court_id) if court_id else None
    team_a_value = int(team_a_id) if team_a_id else None
    team_b_value = int(team_b_id) if team_b_id else None

    with get_conn() as conn:
        conn.execute("""
            INSERT INTO slots (
                competition_id, court_id, startzeit, slot_typ, phase, gruppe,
                team_a_id, team_b_id, status, note
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'geplant', ?)
        """, (
            competition_id,
            court_value,
            startzeit,
            slot_typ,
            phase,
            gruppe or None,
            team_a_value,
            team_b_value,
            note or None,
        ))
        conn.commit()

    return RedirectResponse(f"/spielplan-bearbeiten?competition_id={competition_id}", status_code=303)

@app.post("/slot/{slot_id}/update")
def update_slot(
    slot_id: int,
    competition_id: int = Form(...),
    startzeit: str = Form(...),
    slot_typ: str = Form(...),
    court_id: str = Form(""),
    phase: str = Form(...),
    gruppe: str = Form(""),
    team_a_id: str = Form(""),
    team_b_id: str = Form(""),
    status: str = Form("geplant"),
    note: str = Form("")
):
    with get_conn() as conn:
        conn.execute("""
            UPDATE slots
            SET competition_id = ?,
                court_id = ?,
                startzeit = ?,
                slot_typ = ?,
                phase = ?,
                gruppe = ?,
                team_a_id = ?,
                team_b_id = ?,
                status = ?,
                note = ?
            WHERE id = ?
        """, (
            competition_id,
            int(court_id) if court_id else None,
            startzeit,
            slot_typ,
            phase,
            gruppe or None,
            int(team_a_id) if team_a_id else None,
            int(team_b_id) if team_b_id else None,
            status,
            note or None,
            slot_id,
        ))
        conn.commit()

    return RedirectResponse(f"/spielplan-bearbeiten?competition_id={competition_id}", status_code=303)


@app.post("/slot/{slot_id}/delete")
def delete_slot(slot_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM slots WHERE id = ?", (slot_id,))
        conn.commit()

    return RedirectResponse("/spielplan-bearbeiten", status_code=303)

@app.post("/slots/delete")
def delete_multiple_slots(
    slot_ids: List[int] = Form(...)
):
    with get_conn() as conn:
        placeholders = ",".join("?" for _ in slot_ids)

        conn.execute(
            f"DELETE FROM slots WHERE id IN ({placeholders})",
            slot_ids
        )

        conn.commit()

    return RedirectResponse(
        "/spielplan-bearbeiten",
        status_code=303
    )


@app.post("/slot/{slot_id}/move")
def move_slot(
    slot_id: int,
    court_id: str = Form(""),
    startzeit: str = Form(...),
    sort_order: int = Form(0)
):
    court_value = int(court_id) if court_id else None

    with get_conn() as conn:
        conn.execute("""
            UPDATE slots
            SET court_id = ?,
                startzeit = ?,
                sort_order = ?
            WHERE id = ?
        """, (
            court_value,
            startzeit,
            sort_order,
            slot_id
        ))
        conn.commit()

    return JSONResponse({"success": True})

@app.post("/slot/{slot_id}/copy")
def copy_slot(slot_id: int):
    with get_conn() as conn:
        slot = conn.execute("""
            SELECT *
            FROM slots
            WHERE id = ?
        """, (slot_id,)).fetchone()

        if slot is None:
            return RedirectResponse("/spielplan-bearbeiten", status_code=303)

        try:
            new_time = (
                datetime.strptime(slot["startzeit"], "%H:%M")
                + timedelta(minutes=10)
            ).strftime("%H:%M")
        except ValueError:
            new_time = slot["startzeit"]

        max_sort_order = conn.execute("""
            SELECT COALESCE(MAX(sort_order), 0) AS max_sort_order
            FROM slots
            WHERE competition_id = ?
              AND court_id IS ?
        """, (
            slot["competition_id"],
            slot["court_id"]
        )).fetchone()["max_sort_order"]

        conn.execute("""
            INSERT INTO slots (
                competition_id,
                court_id,
                startzeit,
                sort_order,
                slot_typ,
                phase,
                gruppe,
                team_a_id,
                team_b_id,
                score_a,
                score_b,
                status,
                note
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, 'geplant', ?)
        """, (
            slot["competition_id"],
            slot["court_id"],
            new_time,
            max_sort_order + 10,
            slot["slot_typ"],
            slot["phase"],
            slot["gruppe"],
            slot["team_a_id"],
            slot["team_b_id"],
            slot["note"],
        ))

        conn.commit()

    return RedirectResponse(
        f"/spielplan-bearbeiten?competition_id={slot['competition_id']}",
        status_code=303
    )

@app.get("/ergebnisse")
def ergebnisse(request: Request, competition_id: str = ""):
    selected_competition_id = parse_competition_id(competition_id)

    slots = get_all_slots(selected_competition_id)

    active_slots = [
        slot for slot in slots
        if slot["slot_typ"] == "Spiel" and slot["status"] in ("geplant", "läuft")
    ]

    archived_slots = [
        slot for slot in slots
        if slot["slot_typ"] == "Spiel" and slot["status"] == "beendet"
    ]

    archived_slots = list(reversed(archived_slots))[:20]
    competitions = get_active_competitions()

    return templates.TemplateResponse(
        request=request,
        name="ergebnisse.html",
        context={
            "slots": active_slots,
            "archived_slots": archived_slots,
            "competitions": competitions,
            "selected_competition_id": selected_competition_id,
        }
    )


@app.post("/slot/{slot_id}/start")
def start_slot(slot_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE slots SET status = 'läuft' WHERE id = ?", (slot_id,))
        conn.commit()

    return RedirectResponse("/ergebnisse", status_code=303)


@app.post("/slot/{slot_id}/unstart")
def unstart_slot(slot_id: int):
    with get_conn() as conn:
        conn.execute("""
            UPDATE slots
            SET status = 'geplant'
            WHERE id = ?
              AND status = 'läuft'
        """, (slot_id,))
        conn.commit()

    return RedirectResponse("/ergebnisse", status_code=303)


@app.post("/slot/{slot_id}/save")
def save_slot(
    slot_id: int,
    score_a: int = Form(...),
    score_b: int = Form(...),
    finish: str = Form("0")
):
    score_a = max(0, score_a)
    score_b = max(0, score_b)

    new_status = "beendet" if finish == "1" else "läuft"

    with get_conn() as conn:
        conn.execute("""
            UPDATE slots
            SET score_a = ?,
                score_b = ?,
                status = ?
            WHERE id = ?
        """, (
            score_a,
            score_b,
            new_status,
            slot_id
        ))
        conn.commit()

    return RedirectResponse("/ergebnisse", status_code=303)


@app.post("/slot/{slot_id}/reactivate")
def reactivate_slot(slot_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE slots SET status = 'läuft' WHERE id = ?", (slot_id,))
        conn.commit()

    return RedirectResponse("/ergebnisse", status_code=303)


@app.post("/slot/{slot_id}/clear-result")
def clear_slot_result(slot_id: int):
    with get_conn() as conn:
        conn.execute("""
            UPDATE slots
            SET score_a = NULL,
                score_b = NULL,
                status = 'geplant'
            WHERE id = ?
        """, (slot_id,))
        conn.commit()

    return RedirectResponse("/ergebnisse", status_code=303)


@app.get("/tabellen")
def tabellen(request: Request, competition_id: str = ""):
    selected_competition_id = parse_competition_id(competition_id)
    competitions = get_active_competitions()

    visible_competitions = [
        c for c in competitions
        if selected_competition_id is None or c["id"] == selected_competition_id
    ]

    tables = [
        {
            "competition": competition,
            "rows": calculate_table(competition["id"])
        }
        for competition in visible_competitions
    ]

    return templates.TemplateResponse(
        request=request,
        name="tabellen.html",
        context={
            "tables": tables,
            "competitions": competitions,
            "selected_competition_id": selected_competition_id,
        }
    )


@app.get("/teams")
def teams(request: Request):
    with get_conn() as conn:
        team_rows = conn.execute("SELECT * FROM teams ORDER BY jahrgang, name").fetchall()

    return templates.TemplateResponse(request=request, name="teams.html", context={"teams": team_rows})


@app.post("/team/create")
def create_team(name: str = Form(...), jahrgang: int = Form(...)):
    with get_conn() as conn:
        conn.execute("INSERT INTO teams (name, jahrgang, active) VALUES (?, ?, 1)", (name.strip(), jahrgang))
        conn.commit()

    return RedirectResponse("/teams", status_code=303)


@app.post("/team/{team_id}/delete")
def delete_team(team_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM teams WHERE id = ?", (team_id,))
        conn.commit()

    return RedirectResponse("/teams", status_code=303)


@app.get("/wettbewerbe")
def wettbewerbe(request: Request):
    competitions = get_all_competitions()
    with get_conn() as conn:
        disciplines = conn.execute("""
            SELECT * FROM competition_disciplines
            ORDER BY competition_id, sort_order, id
        """).fetchall()
        events = conn.execute("""
            SELECT * FROM events
            ORDER BY CASE WHEN status = 'archiviert' THEN 1 ELSE 0 END,
                     event_date, name
        """).fetchall()
    disciplines_by_competition = defaultdict(list)
    for discipline in disciplines:
        disciplines_by_competition[discipline["competition_id"]].append(discipline)
    return templates.TemplateResponse(
        request=request, name="wettbewerbe.html",
        context={
            "competitions": competitions,
            "disciplines_by_competition": disciplines_by_competition,
            "events": events,
            "events_by_id": {event["id"]: event for event in events},
        }
    )

@app.post("/competition/create")
def create_competition(
    name: str = Form(...), sportart: str = Form(...), jahrgang: int = Form(...),
    points_win: float = Form(3), points_draw: float = Form(1), points_loss: float = Form(0),
    event_id: str = Form(""), competition_type: str = Form("Turnier"),
):
    if competition_type not in {"Turnier", "Sechskampf"}:
        return RedirectResponse("/wettbewerbe", status_code=303)
    try:
        event_id_value = int(event_id) if event_id else None
    except ValueError:
        return RedirectResponse("/wettbewerbe", status_code=303)
    with get_conn() as conn:
        if event_id_value is not None and conn.execute(
            "SELECT 1 FROM events WHERE id = ?", (event_id_value,)
        ).fetchone() is None:
            return RedirectResponse("/wettbewerbe", status_code=303)
        conn.execute("""
            INSERT INTO competitions (
                name, sportart, jahrgang, status, points_win, points_draw,
                points_loss, event_id, competition_type
            ) VALUES (?, ?, ?, 'geplant', ?, ?, ?, ?, ?)
        """, (
            name.strip(), sportart.strip(), jahrgang, points_win, points_draw, points_loss,
            event_id_value, competition_type,
        ))
        conn.commit()
    return RedirectResponse("/wettbewerbe", status_code=303)

@app.post("/competition/{competition_id}/duplicate")
def duplicate_competition(competition_id: int):
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        competition = conn.execute(
            "SELECT * FROM competitions WHERE id = ?", (competition_id,)
        ).fetchone()
        if competition is None:
            return RedirectResponse("/wettbewerbe", status_code=303)
        cursor = conn.execute("""
            INSERT INTO competitions (
                name, sportart, jahrgang, status, points_win, points_draw,
                points_loss, event_id, competition_type
            ) VALUES (?, ?, ?, 'geplant', ?, ?, ?, ?, ?)
        """, (
            get_unique_competition_name(conn, competition["name"]),
            competition["sportart"], competition["jahrgang"], competition["points_win"],
            competition["points_draw"], competition["points_loss"],
            competition["event_id"], competition["competition_type"],
        ))
        copy_competition_disciplines(conn, competition_id, cursor.lastrowid)
        conn.commit()
    return RedirectResponse("/wettbewerbe", status_code=303)

@app.post("/competition/{competition_id}/update")
def update_competition(
    competition_id: int, name: str = Form(...), sportart: str = Form(...),
    jahrgang: str = Form(...), status: str = Form(...),
    points_win: str = Form(...), points_draw: str = Form(...), points_loss: str = Form(...),
    event_id: str = Form(""), competition_type: str = Form("Turnier"),
):
    try:
        jahrgang_value = int(jahrgang)
        points_win_value = float(points_win.strip().replace(",", "."))
        points_draw_value = float(points_draw.strip().replace(",", "."))
        points_loss_value = float(points_loss.strip().replace(",", "."))
        event_id_value = int(event_id) if event_id else None
    except (TypeError, ValueError):
        return RedirectResponse("/wettbewerbe", status_code=303)
    name_value = name.strip()
    sportart_value = sportart.strip()
    valid_statuses = {"geplant", "läuft", "beendet", "archiviert"}
    if (
        not name_value or not sportart_value or not 1 <= jahrgang_value <= 13
        or status not in valid_statuses
        or competition_type not in {"Turnier", "Sechskampf"}
        or not all(isfinite(value) for value in (points_win_value, points_draw_value, points_loss_value))
    ):
        return RedirectResponse("/wettbewerbe", status_code=303)
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        duplicate_name = conn.execute(
            "SELECT 1 FROM competitions WHERE name = ? AND id != ?",
            (name_value, competition_id)
        ).fetchone()
        if duplicate_name:
            return RedirectResponse("/wettbewerbe", status_code=303)
        if event_id_value is not None and conn.execute(
            "SELECT 1 FROM events WHERE id = ?", (event_id_value,)
        ).fetchone() is None:
            return RedirectResponse("/wettbewerbe", status_code=303)
        conn.execute("""
            UPDATE competitions
            SET name = ?, sportart = ?, jahrgang = ?, status = ?,
                points_win = ?, points_draw = ?, points_loss = ?,
                event_id = ?, competition_type = ?
            WHERE id = ?
        """, (
            name_value, sportart_value, jahrgang_value, status,
            points_win_value, points_draw_value, points_loss_value,
            event_id_value, competition_type, competition_id,
        ))
        conn.commit()
    return RedirectResponse("/wettbewerbe", status_code=303)

@app.post("/competition/{competition_id}/discipline/create")
def create_competition_discipline(
    competition_id: int, name: str = Form(...), sort_order: str = Form(...),
    unit: str = Form(""), scoring_direction: str = Form("higher"),
):
    try:
        sort_order_value = int(sort_order)
    except (TypeError, ValueError):
        return RedirectResponse("/wettbewerbe", status_code=303)
    name_value = name.strip()
    if (
        not name_value or sort_order_value < 1
        or scoring_direction not in {"higher", "lower"}
    ):
        return RedirectResponse("/wettbewerbe", status_code=303)
    with get_conn() as conn:
        competition = conn.execute(
            "SELECT * FROM competitions WHERE id = ?", (competition_id,)
        ).fetchone()
        if competition is None or competition["competition_type"] != "Sechskampf":
            return RedirectResponse("/wettbewerbe", status_code=303)
        conn.execute("""
            INSERT INTO competition_disciplines (
                competition_id, name, sort_order, unit, scoring_direction
            ) VALUES (?, ?, ?, ?, ?)
        """, (
            competition_id, name_value, sort_order_value,
            unit.strip() or None, scoring_direction,
        ))
        conn.commit()
    return RedirectResponse("/wettbewerbe", status_code=303)

@app.post("/discipline/{discipline_id}/update")
def update_competition_discipline(
    discipline_id: int, name: str = Form(...), sort_order: str = Form(...),
    unit: str = Form(""), scoring_direction: str = Form("higher"),
):
    try:
        sort_order_value = int(sort_order)
    except (TypeError, ValueError):
        return RedirectResponse("/wettbewerbe", status_code=303)
    name_value = name.strip()
    if (
        not name_value or sort_order_value < 1
        or scoring_direction not in {"higher", "lower"}
    ):
        return RedirectResponse("/wettbewerbe", status_code=303)
    with get_conn() as conn:
        conn.execute("""
            UPDATE competition_disciplines
            SET name = ?, sort_order = ?, unit = ?, scoring_direction = ?
            WHERE id = ?
        """, (
            name_value, sort_order_value, unit.strip() or None,
            scoring_direction, discipline_id,
        ))
        conn.commit()
    return RedirectResponse("/wettbewerbe", status_code=303)

@app.post("/discipline/{discipline_id}/delete")
def delete_competition_discipline(discipline_id: int):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM discipline_results WHERE discipline_id = ?",
            (discipline_id,)
        )
        conn.execute(
            "DELETE FROM competition_disciplines WHERE id = ?",
            (discipline_id,)
        )
        conn.commit()
    return RedirectResponse("/wettbewerbe", status_code=303)

@app.post("/competition/{competition_id}/archive")
def archive_competition(competition_id: int):
    with get_conn() as conn:
        conn.execute("""
            UPDATE competitions
            SET status = 'archiviert'
            WHERE id = ?
        """, (competition_id,))
        conn.commit()

    return RedirectResponse("/wettbewerbe", status_code=303)


@app.post("/competition/{competition_id}/restore")
def restore_competition(competition_id: int):
    with get_conn() as conn:
        conn.execute("""
            UPDATE competitions
            SET status = 'geplant'
            WHERE id = ?
        """, (competition_id,))
        conn.commit()

    return RedirectResponse("/wettbewerbe", status_code=303)


@app.post("/competition/{competition_id}/reset")
def reset_competition(competition_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM slots WHERE competition_id = ?", (competition_id,))
        conn.execute("""
            UPDATE competitions
            SET status = 'geplant'
            WHERE id = ?
        """, (competition_id,))
        conn.commit()

    return RedirectResponse("/wettbewerbe", status_code=303)


@app.post("/competition/{competition_id}/delete")
def delete_competition(competition_id: int):
    with get_conn() as conn:
        conn.execute("""
            DELETE FROM discipline_results
            WHERE participant_id IN (
                SELECT id FROM sixkampf_participants WHERE competition_id = ?
            )
        """, (competition_id,))
        conn.execute("""
            DELETE FROM discipline_results
            WHERE discipline_id IN (
                SELECT id FROM competition_disciplines WHERE competition_id = ?
            )
        """, (competition_id,))
        conn.execute("DELETE FROM sixkampf_participants WHERE competition_id = ?", (competition_id,))
        conn.execute("DELETE FROM slots WHERE competition_id = ?", (competition_id,))
        conn.execute("DELETE FROM competition_disciplines WHERE competition_id = ?", (competition_id,))
        conn.execute("DELETE FROM competitions WHERE id = ?", (competition_id,))
        conn.commit()

    return RedirectResponse("/wettbewerbe", status_code=303)


@app.post("/competition/{competition_id}/delete-planned-slots")
def delete_planned_slots(competition_id: int):
    with get_conn() as conn:
        conn.execute("""
            DELETE FROM slots
            WHERE competition_id = ?
              AND status = 'geplant'
        """, (competition_id,))
        conn.commit()

    return RedirectResponse(f"/spielplan-bearbeiten?competition_id={competition_id}", status_code=303)


@app.get("/events")
def events_list(request: Request):
    with get_conn() as conn:
        events = conn.execute("""
            SELECT e.*, COUNT(c.id) AS competition_count
            FROM events e
            LEFT JOIN competitions c ON c.event_id = e.id
            GROUP BY e.id
            ORDER BY CASE WHEN e.status = 'archiviert' THEN 1 ELSE 0 END,
                     e.event_date, e.name
        """).fetchall()
    return templates.TemplateResponse(
        request=request, name="events.html", context={"events": events}
    )


@app.get("/events/new")
def event_new(request: Request):
    return templates.TemplateResponse(
        request=request, name="event_form.html",
        context={"event": None, "form_action": "/events/create"}
    )


@app.post("/events/create")
def event_create(
    name: str = Form(...), description: str = Form(""),
    event_date: str = Form(""), status: str = Form("geplant"),
):
    if not name.strip() or status not in {"geplant", "läuft", "beendet", "archiviert"}:
        return RedirectResponse("/events", status_code=303)
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM events WHERE name = ?", (name.strip(),)).fetchone():
            return RedirectResponse("/events", status_code=303)
        conn.execute("""
            INSERT INTO events (name, description, event_date, status)
            VALUES (?, ?, ?, ?)
        """, (name.strip(), description.strip() or None, event_date or None, status))
        conn.commit()
    return RedirectResponse("/events", status_code=303)


@app.get("/events/{event_id}/edit")
def event_edit(request: Request, event_id: int):
    with get_conn() as conn:
        event = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    if event is None:
        return RedirectResponse("/events", status_code=303)
    return templates.TemplateResponse(
        request=request, name="event_form.html",
        context={"event": event, "form_action": f"/events/{event_id}/update"}
    )


@app.post("/events/{event_id}/update")
def event_update(
    event_id: int, name: str = Form(...), description: str = Form(""),
    event_date: str = Form(""), status: str = Form("geplant"),
):
    if not name.strip() or status not in {"geplant", "läuft", "beendet", "archiviert"}:
        return RedirectResponse("/events", status_code=303)
    with get_conn() as conn:
        duplicate = conn.execute(
            "SELECT 1 FROM events WHERE name = ? AND id != ?",
            (name.strip(), event_id)
        ).fetchone()
        if duplicate:
            return RedirectResponse(f"/events/{event_id}/edit", status_code=303)
        conn.execute("""
            UPDATE events
            SET name = ?, description = ?, event_date = ?, status = ?
            WHERE id = ?
        """, (
            name.strip(), description.strip() or None,
            event_date or None, status, event_id,
        ))
        conn.commit()
    return RedirectResponse(f"/events/{event_id}", status_code=303)


@app.post("/events/{event_id}/archive")
def event_archive(event_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE events SET status = 'archiviert' WHERE id = ?", (event_id,))
        conn.commit()
    return RedirectResponse("/events", status_code=303)


@app.post("/events/{event_id}/restore")
def event_restore(event_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE events SET status = 'geplant' WHERE id = ?", (event_id,))
        conn.commit()
    return RedirectResponse("/events", status_code=303)


@app.post("/events/{event_id}/delete")
def event_delete(event_id: int):
    with get_conn() as conn:
        competition_count = conn.execute(
            "SELECT COUNT(*) FROM competitions WHERE event_id = ?", (event_id,)
        ).fetchone()[0]
        if competition_count:
            return RedirectResponse(f"/events/{event_id}", status_code=303)
        conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
        conn.commit()
    return RedirectResponse("/events", status_code=303)


@app.post("/events/{event_id}/duplicate")
def event_duplicate(event_id: int):
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        event = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        if event is None:
            return RedirectResponse("/events", status_code=303)
        cursor = conn.execute("""
            INSERT INTO events (name, description, event_date, status)
            VALUES (?, ?, ?, 'geplant')
        """, (
            get_unique_event_name(conn, event["name"]),
            event["description"], event["event_date"],
        ))
        new_event_id = cursor.lastrowid
        competitions = conn.execute(
            "SELECT * FROM competitions WHERE event_id = ? ORDER BY id",
            (event_id,)
        ).fetchall()
        for competition in competitions:
            competition_cursor = conn.execute("""
                INSERT INTO competitions (
                    name, sportart, jahrgang, status, points_win, points_draw,
                    points_loss, event_id, competition_type
                ) VALUES (?, ?, ?, 'geplant', ?, ?, ?, ?, ?)
            """, (
                get_unique_competition_name(conn, competition["name"]),
                competition["sportart"], competition["jahrgang"],
                competition["points_win"], competition["points_draw"],
                competition["points_loss"], new_event_id,
                competition["competition_type"],
            ))
            copy_competition_disciplines(
                conn, competition["id"], competition_cursor.lastrowid
            )
        conn.commit()
    return RedirectResponse(f"/events/{new_event_id}", status_code=303)


@app.get("/events/{event_id}")
def event_detail(request: Request, event_id: int):
    with get_conn() as conn:
        event = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        competitions = conn.execute("""
            SELECT * FROM competitions
            WHERE event_id = ?
            ORDER BY jahrgang, name
        """, (event_id,)).fetchall()
    if event is None:
        return RedirectResponse("/events", status_code=303)
    return templates.TemplateResponse(
        request=request, name="event_detail.html",
        context={"event": event, "competitions": competitions}
    )


@app.get("/competition/{competition_id}/sechskampf")
def sechskampf_detail(request: Request, competition_id: int):
    with get_conn() as conn:
        competition = conn.execute(
            "SELECT * FROM competitions WHERE id = ?", (competition_id,)
        ).fetchone()
        disciplines = conn.execute("""
            SELECT * FROM competition_disciplines
            WHERE competition_id = ?
            ORDER BY sort_order, id
        """, (competition_id,)).fetchall()
        participants = conn.execute("""
            SELECT * FROM sixkampf_participants
            WHERE competition_id = ?
            ORDER BY class_name, participant_number
        """, (competition_id,)).fetchall()
        result_rows = conn.execute("""
            SELECT dr.* FROM discipline_results dr
            JOIN sixkampf_participants p ON p.id = dr.participant_id
            WHERE p.competition_id = ?
        """, (competition_id,)).fetchall()
    if competition is None or competition["competition_type"] != "Sechskampf":
        return RedirectResponse("/wettbewerbe", status_code=303)
    participants_by_class = defaultdict(list)
    for participant in participants:
        participants_by_class[participant["class_name"]].append(participant)
    results = {
        (row["participant_id"], row["discipline_id"]): row["value"]
        for row in result_rows
    }
    return templates.TemplateResponse(
        request=request, name="sechskampf.html",
        context={
            "competition": competition, "disciplines": disciplines,
            "participants_by_class": participants_by_class,
            "participant_count": len(participants), "results": results,
            "ranking": calculate_sixkampf_ranking(competition_id),
        }
    )


@app.post("/competition/{competition_id}/participants/create")
def participant_create(
    competition_id: int, class_name: str = Form(...),
    participant_number: int = Form(...),
):
    if not class_name.strip() or participant_number < 1:
        return RedirectResponse(f"/competition/{competition_id}/sechskampf", status_code=303)
    with get_conn() as conn:
        competition = conn.execute(
            "SELECT * FROM competitions WHERE id = ?", (competition_id,)
        ).fetchone()
        if competition is None or competition["competition_type"] != "Sechskampf":
            return RedirectResponse("/wettbewerbe", status_code=303)
        conn.execute("""
            INSERT OR IGNORE INTO sixkampf_participants (
                competition_id, class_name, participant_number
            ) VALUES (?, ?, ?)
        """, (competition_id, class_name.strip(), participant_number))
        conn.commit()
    return RedirectResponse(f"/competition/{competition_id}/sechskampf", status_code=303)


@app.post("/competition/{competition_id}/participants/generate")
def participants_generate(
    competition_id: int, class_name: str = Form(...),
    participant_count: int = Form(...),
):
    if not class_name.strip() or participant_count < 1:
        return RedirectResponse(f"/competition/{competition_id}/sechskampf", status_code=303)
    with get_conn() as conn:
        competition = conn.execute(
            "SELECT * FROM competitions WHERE id = ?", (competition_id,)
        ).fetchone()
        if competition is None or competition["competition_type"] != "Sechskampf":
            return RedirectResponse("/wettbewerbe", status_code=303)
        for number in range(1, participant_count + 1):
            conn.execute("""
                INSERT OR IGNORE INTO sixkampf_participants (
                    competition_id, class_name, participant_number
                ) VALUES (?, ?, ?)
            """, (competition_id, class_name.strip(), number))
        conn.commit()
    return RedirectResponse(f"/competition/{competition_id}/sechskampf", status_code=303)


@app.post("/participants/{participant_id}/delete")
def participant_delete(participant_id: int):
    with get_conn() as conn:
        participant = conn.execute(
            "SELECT * FROM sixkampf_participants WHERE id = ?", (participant_id,)
        ).fetchone()
        if participant is None:
            return RedirectResponse("/wettbewerbe", status_code=303)
        conn.execute("DELETE FROM discipline_results WHERE participant_id = ?", (participant_id,))
        conn.execute("DELETE FROM sixkampf_participants WHERE id = ?", (participant_id,))
        conn.commit()
    return RedirectResponse(
        f"/competition/{participant['competition_id']}/sechskampf", status_code=303
    )


@app.post("/participants/{participant_id}/results")
async def participant_results_save(request: Request, participant_id: int):
    form = await request.form()
    with get_conn() as conn:
        participant = conn.execute(
            "SELECT * FROM sixkampf_participants WHERE id = ?", (participant_id,)
        ).fetchone()
        if participant is None:
            return RedirectResponse("/wettbewerbe", status_code=303)
        disciplines = conn.execute("""
            SELECT id FROM competition_disciplines
            WHERE competition_id = ?
        """, (participant["competition_id"],)).fetchall()
        parsed_values = []
        try:
            for discipline in disciplines:
                raw_value = str(form.get(f"discipline_{discipline['id']}", "")).strip()
                value = None if raw_value == "" else float(raw_value.replace(",", "."))
                if value is not None and not isfinite(value):
                    raise ValueError
                parsed_values.append((discipline["id"], value))
        except ValueError:
            return RedirectResponse(
                f"/competition/{participant['competition_id']}/sechskampf",
                status_code=303
            )
        for discipline_id, value in parsed_values:
            if value is None:
                conn.execute("""
                    DELETE FROM discipline_results
                    WHERE participant_id = ? AND discipline_id = ?
                """, (participant_id, discipline_id))
            else:
                conn.execute("""
                    INSERT INTO discipline_results (participant_id, discipline_id, value)
                    VALUES (?, ?, ?)
                    ON CONFLICT(participant_id, discipline_id)
                    DO UPDATE SET value = excluded.value
                """, (participant_id, discipline_id, value))
        conn.commit()
    return RedirectResponse(
        f"/competition/{participant['competition_id']}/sechskampf", status_code=303
    )


@app.get("/spielfelder")
def spielfelder(request: Request):
    with get_conn() as conn:
        courts = conn.execute("SELECT * FROM courts ORDER BY name").fetchall()

    return templates.TemplateResponse(request=request, name="spielfelder.html", context={"courts": courts})


@app.post("/court/create")
def create_court(name: str = Form(...), sportart: str = Form("")):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO courts (name, sportart, active)
            VALUES (?, ?, 1)
        """, (name.strip(), sportart.strip() or None))
        conn.commit()

    return RedirectResponse("/spielfelder", status_code=303)


@app.post("/court/{court_id}/update")
def update_court(court_id: int, name: str = Form(...), sportart: str = Form("")):
    with get_conn() as conn:
        conn.execute("""
            UPDATE courts
            SET name = ?, sportart = ?
            WHERE id = ?
        """, (name.strip(), sportart.strip() or None, court_id))
        conn.commit()

    return RedirectResponse("/spielfelder", status_code=303)


@app.post("/court/{court_id}/delete")
def delete_court(court_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE slots SET court_id = NULL WHERE court_id = ?", (court_id,))
        conn.execute("DELETE FROM courts WHERE id = ?", (court_id,))
        conn.commit()

    return RedirectResponse("/spielfelder", status_code=303)


@app.get("/einstellungen")
def einstellungen(request: Request):
    return templates.TemplateResponse(request=request, name="einstellungen.html", context={})


@app.get("/beamer")
def beamer(request: Request):
    data = fetch_beamer_data()
    return templates.TemplateResponse(request=request, name="beamer.html", context=data)
