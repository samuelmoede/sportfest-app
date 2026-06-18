from collections import defaultdict
from datetime import datetime, timedelta
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
            (group_a[0], group_a[2], "A"),
            (group_a[1], group_a[2], "A"),
            (group_b[0], group_b[1], "B"),
            (group_b[2], group_b[3], "B"),
            (group_b[0], group_b[2], "B"),
            (group_b[1], group_b[3], "B"),
        ]

    elif len(team_names) == 6 and games_per_team == 2:
        group_a = team_names[:3]
        group_b = team_names[3:6]

        pairings = [
            (group_a[0], group_a[1], "A"),
            (group_a[0], group_a[2], "A"),
            (group_a[1], group_a[2], "A"),
            (group_b[0], group_b[1], "B"),
            (group_b[0], group_b[2], "B"),
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

    index = 0

    while index < len(pairings):
        time_value = current_time.strftime("%H:%M")
        used_courts = []

        for court_id in court_ids:
            if index >= len(pairings):
                break

            team_a, team_b, gruppe = pairings[index]

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
            index += 1

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

    return templates.TemplateResponse(
        request=request,
        name="wettbewerbe.html",
        context={"competitions": competitions}
    )


@app.post("/competition/create")
def create_competition(
    name: str = Form(...),
    sportart: str = Form(...),
    jahrgang: int = Form(...),
    points_win: float = Form(3),
    points_draw: float = Form(1),
    points_loss: float = Form(0),
):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO competitions (
                name,
                sportart,
                jahrgang,
                status,
                points_win,
                points_draw,
                points_loss
            )
            VALUES (?, ?, ?, 'geplant', ?, ?, ?)
        """, (
            name.strip(),
            sportart.strip(),
            jahrgang,
            points_win,
            points_draw,
            points_loss,
        ))
        conn.commit()

    return RedirectResponse("/wettbewerbe", status_code=303)


@app.post("/competition/{competition_id}/update")
def update_competition(
    competition_id: int,
    status: str = Form(...),
    points_win: float = Form(...),
    points_draw: float = Form(...),
    points_loss: float = Form(...)
):
    with get_conn() as conn:
        conn.execute("""
            UPDATE competitions
            SET status = ?,
                points_win = ?,
                points_draw = ?,
                points_loss = ?
            WHERE id = ?
        """, (
            status,
            points_win,
            points_draw,
            points_loss,
            competition_id
        ))
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


@app.post("/competition/{competition_id}/delete")
def delete_competition(competition_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM slots WHERE competition_id = ?", (competition_id,))
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
