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
        SELECT name, sort_order, unit, scoring_direction, values_per_team
        FROM competition_disciplines
        WHERE competition_id = ?
        ORDER BY sort_order, id
    """, (source_competition_id,)).fetchall()
    for discipline in disciplines:
        conn.execute("""
            INSERT INTO competition_disciplines (
                competition_id, name, sort_order, unit, scoring_direction, values_per_team
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (
            target_competition_id, discipline["name"], discipline["sort_order"],
            discipline["unit"], discipline["scoring_direction"],
            discipline["values_per_team"],
        ))


def calculate_sixkampf_team_ranking(
    teams, disciplines, result_rows, points_first_place: int = 7
):
    totals_by_team_discipline = defaultdict(float)
    overall_totals = {team["id"]: 0.0 for team in teams}

    for result in result_rows:
        key = (result["team_id"], result["discipline_id"])
        totals_by_team_discipline[key] += result["value"]

    ranking = []
    for team in teams:
        overall_total = sum(
            totals_by_team_discipline[(team["id"], discipline["id"])]
            for discipline in disciplines
        )
        overall_totals[team["id"]] = overall_total
        ranking.append({
            "team": team,
            "overall_total": overall_total,
            "placement": 0,
        })

    ranking.sort(key=lambda row: (
        -row["overall_total"], row["team"]["name"].lower()
    ))
    previous_total = None
    placement = 0
    for index, row in enumerate(ranking, start=1):
        if previous_total is None or row["overall_total"] != previous_total:
            placement = index
            previous_total = row["overall_total"]
        row["placement"] = placement
        row["scoring_points"] = max(points_first_place - placement + 1, 0)

    return ranking, totals_by_team_discipline, overall_totals


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
        ).fetch…9960 tokens truncated…reate_competition(
    name: str = Form(...), sportart: str = Form(...), jahrgang: int = Form(...),
    points_win: float = Form(3), points_draw: float = Form(1), points_loss: float = Form(0),
    event_id: str = Form(""), competition_type: str = Form("Turnier"),
    points_first_place: str = Form(""),
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
        try:
            points_first_place_value = (
                int(points_first_place) if points_first_place else
                conn.execute("""
                    SELECT COUNT(*) AS count FROM teams
                    WHERE active = 1 AND jahrgang = ?
                """, (jahrgang,)).fetchone()["count"] or 7
            )
        except (TypeError, ValueError):
            return RedirectResponse("/wettbewerbe", status_code=303)
        if points_first_place_value < 1:
            return RedirectResponse("/wettbewerbe", status_code=303)
        conn.execute("""
            INSERT INTO competitions (
                name, sportart, jahrgang, status, points_win, points_draw,
                points_loss, points_first_place, event_id, competition_type
            ) VALUES (?, ?, ?, 'geplant', ?, ?, ?, ?, ?, ?)
        """, (
            name.strip(), sportart.strip(), jahrgang, points_win, points_draw, points_loss,
            points_first_place_value, event_id_value, competition_type,
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
                points_loss, points_first_place, event_id, competition_type
            ) VALUES (?, ?, ?, 'geplant', ?, ?, ?, ?, ?, ?)
        """, (
            get_unique_competition_name(conn, competition["name"]),
            competition["sportart"], competition["jahrgang"], competition["points_win"],
            competition["points_draw"], competition["points_loss"],
            competition["points_first_place"],
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
    points_first_place: str = Form("7"),
):
    try:
        jahrgang_value = int(jahrgang)
        points_win_value = float(points_win.strip().replace(",", "."))
        points_draw_value = float(points_draw.strip().replace(",", "."))
        points_loss_value = float(points_loss.strip().replace(",", "."))
        points_first_place_value = int(points_first_place)
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
        or points_first_place_value < 1
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
                points_first_place = ?, event_id = ?, competition_type = ?
            WHERE id = ?
        """, (
            name_value, sportart_value, jahrgang_value, status,
            points_win_value, points_draw_value, points_loss_value,
            points_first_place_value, event_id_value, competition_type, competition_id,
        ))
        conn.commit()
    return RedirectResponse("/wettbewerbe", status_code=303)

@app.post("/competition/{competition_id}/discipline/create")
def create_competition_discipline(
    competition_id: int, name: str = Form(...), sort_order: str = Form(...),
    unit: str = Form(""), scoring_direction: str = Form("higher"),
    values_per_team: str = Form("1"),
):
    try:
        sort_order_value = int(sort_order)
        values_per_team_value = int(values_per_team)
    except (TypeError, ValueError):
        return RedirectResponse("/wettbewerbe", status_code=303)
    name_value = name.strip()
    if (
        not name_value or sort_order_value < 1 or values_per_team_value < 1
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
                competition_id, name, sort_order, unit,
                scoring_direction, values_per_team
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (
            competition_id, name_value, sort_order_value, unit.strip() or None,
            scoring_direction, values_per_team_value,
        ))
        conn.commit()
    return RedirectResponse("/wettbewerbe", status_code=303)

@app.post("/discipline/{discipline_id}/update")
def update_competition_discipline(
    discipline_id: int, name: str = Form(...), sort_order: str = Form(...),
    unit: str = Form(""), scoring_direction: str = Form("higher"),
    values_per_team: str = Form("1"),
):
    try:
        sort_order_value = int(sort_order)
        values_per_team_value = int(values_per_team)
    except (TypeError, ValueError):
        return RedirectResponse("/wettbewerbe", status_code=303)
    name_value = name.strip()
    if (
        not name_value or sort_order_value < 1 or values_per_team_value < 1
        or scoring_direction not in {"higher", "lower"}
    ):
        return RedirectResponse("/wettbewerbe", status_code=303)
    with get_conn() as conn:
        conn.execute("""
            UPDATE competition_disciplines
            SET name = ?, sort_order = ?, unit = ?,
                scoring_direction = ?, values_per_team = ?
            WHERE id = ?
        """, (
            name_value, sort_order_value, unit.strip() or None,
            scoring_direction, values_per_team_value, discipline_id,
        ))
        conn.commit()
    return RedirectResponse("/wettbewerbe", status_code=303)

@app.post("/discipline/{discipline_id}/delete")
def delete_competition_discipline(discipline_id: int):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM sixkampf_team_results WHERE discipline_id = ?",
            (discipline_id,)
        )
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
        conn.execute(
            "DELETE FROM sixkampf_team_results WHERE competition_id = ?",
            (competition_id,)
        )
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

