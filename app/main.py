from collections import defaultdict
import csv
from datetime import date, datetime, timedelta
import io
import logging
from math import isfinite
import os
import re
import secrets
from typing import List, Optional
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from app.database import init_db, get_conn, DB_PATH
from app.routes.settings import router as settings_router
from app.routes.competitions import create_router as create_competitions_router
from app.routes.teams import create_router as create_teams_router
from app.routes.venues import create_router as create_venues_router
from app.services.settings_service import (
    ROLE_LABELS,
    can_access_role,
    get_admin_password,
    get_beamer_refresh_seconds,
    get_current_role,
    get_current_role_description,
    get_current_role_label,
    get_referee_password,
    is_logged_in,
    is_login_prepared,
    is_security_enabled,
)
from app.web import APP_DIR, templates
from app.utils.formatting import (
    format_points_value,
    format_score,
    format_sixkampf_values,
    slugify_filename_part,
)

app = FastAPI(title="Sportfest-App")

SESSION_SECRET_KEY = (
    os.getenv("SPORTFEST_SESSION_SECRET_KEY")
    or os.getenv("SESSION_SECRET_KEY")
)
if not SESSION_SECRET_KEY:
    SESSION_SECRET_KEY = secrets.token_urlsafe(32)
    logging.getLogger(__name__).warning(
        "Kein Session-Secret gesetzt; verwende einen temporaeren Entwicklungsschluessel."
    )

SESSION_HTTPS_ONLY = os.getenv(
    "SPORTFEST_SESSION_HTTPS_ONLY", "false"
).strip().lower() in {"1", "true", "yes", "on"}

AREA_ACCESS_RULES = (
    ("/spielplan-bearbeiten", "admin"),
    ("/einstellungen", "admin"),
    ("/dokumentation", "admin"),
    ("/spielfelder", "admin"),
    ("/wettbewerbe", "admin"),
    ("/events", "admin"),
    ("/teams", "admin"),
    ("/ergebnisse", "referee"),
)

# Historische Formularziele liegen teilweise außerhalb ihres sichtbaren
# Bereichspfads. Die Zuordnung hier hält auch direkte POST-Aufrufe im selben
# zentralen Bereichsschutz.
ACTION_ACCESS_RULES = (
    (
        re.compile(
            r"^/competition/[^/]+/discipline/[^/]+/team/[^/]+/results$"
        ),
        "referee",
    ),
    (
        re.compile(
            r"^/slot/[^/]+/(?:start|finish|unstart|save|reactivate|clear-result)$"
        ),
        "referee",
    ),
    (re.compile(r"^/team(?:/create|/[^/]+/(?:update|delete))$"), "admin"),
    (re.compile(r"^/court(?:/create|/[^/]+/(?:update|delete))$"), "admin"),
    (re.compile(r"^/competition/create$"), "admin"),
    (
        re.compile(
            r"^/competition/[^/]+/(?:duplicate|update|discipline/create|archive|restore|reset|delete|delete-planned-slots|generate-semifinals|generate-finals)$"
        ),
        "admin",
    ),
    (re.compile(r"^/discipline/[^/]+/(?:update|delete)$"), "admin"),
    (re.compile(r"^/plan-generator/(?:preview|apply)$"), "admin"),
    (
        re.compile(r"^/slot(?:/create|/[^/]+/(?:update|delete|move|copy))$"),
        "admin",
    ),
    (re.compile(r"^/slots/delete$"), "admin"),
)


class RoleAreaMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        required_role = get_required_role(request.url.path)
        if required_role and not can_access_role(request, required_role):
            return RedirectResponse("/", status_code=303)
        return await call_next(request)


# Die Session-Middleware muss außen liegen, damit der Bereichsschutz auf
# request.session zugreifen kann.
app.add_middleware(RoleAreaMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET_KEY,
    session_cookie="sportfest_session",
    same_site="lax",
    https_only=SESSION_HTTPS_ONLY,
)

APP_TIMEZONE = ZoneInfo(os.getenv("SPORTFEST_TIMEZONE", "Europe/Berlin"))
EVENT_TYPES = ["Bewegungsfest", "Einzelturnier", "Käthelauf", "Sonstiges"]
EVENT_TYPES_SET = set(EVENT_TYPES)
COMPETITION_LOCATIONS = ("Turnhalle", "Fußballplatz", "Außenbereich")
COMPETITION_LOCATION_ALIASES = {
    "Sportplatz": "Fußballplatz",
}
DEFAULT_GAME_DURATION_MINUTES = 7
DEFAULT_CHANGEOVER_DURATION_MINUTES = 3

app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
def app_now():
    return datetime.now(APP_TIMEZONE)


def app_now_display_time():
    return app_now().strftime("%H:%M")


def app_now_db_timestamp():
    return app_now().strftime("%Y-%m-%d %H:%M:%S")




templates.env.globals["get_current_role"] = get_current_role
templates.env.globals["get_current_role_label"] = get_current_role_label
templates.env.globals["get_current_role_description"] = get_current_role_description
templates.env.globals["is_logged_in"] = is_logged_in
templates.env.globals["can_access_role"] = can_access_role

app.include_router(settings_router)
app.include_router(create_teams_router(app_now_db_timestamp))
app.include_router(create_venues_router(app_now_db_timestamp))


def record_change(
    conn,
    *,
    request: Request,
    action: str,
    entity_type: str,
    entity_id: Optional[int],
    competition_id: Optional[int],
    old_value: Optional[str],
    new_value: Optional[str],
    discipline_id: Optional[int] = None,
    team_id: Optional[int] = None,
):
    conn.execute(
        """
        INSERT INTO change_log (
            created_at, actor_role, action, entity_type, entity_id,
            competition_id, discipline_id, team_id, old_value, new_value
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_now_db_timestamp(),
            get_current_role(request),
            action,
            entity_type,
            entity_id,
            competition_id,
            discipline_id,
            team_id,
            old_value,
            new_value,
        ),
    )




def safe_next_url(value: str, default: str = "/"):
    if (
        value
        and value.startswith("/")
        and not value.startswith("//")
        and "\\" not in value
        and "\r" not in value
        and "\n" not in value
    ):
        return value
    return default

def get_required_role(path: str):
    for prefix, required_role in AREA_ACCESS_RULES:
        if path == prefix or path.startswith(f"{prefix}/"):
            return required_role

    for pattern, required_role in ACTION_ACCESS_RULES:
        if pattern.fullmatch(path):
            return required_role
    return None



def parse_competition_id(value):
    if value in (None, "", "None"):
        return None
    return int(value)


def parse_jahrgang_filter(value):
    if value in (None, "", "None"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None



def collect_tabellen_view_data(event_id: str = "", jahrgang: str = "", competition_id: str = ""):
    selected_event_id = parse_competition_id(event_id)
    selected_jahrgang = parse_jahrgang_filter(jahrgang)
    selected_competition_id = parse_competition_id(competition_id)
    competitions = get_active_competitions()

    visible_competitions = []
    for competition in competitions:
        if selected_event_id is not None and competition["event_id"] != selected_event_id:
            continue
        if selected_jahrgang is not None and competition["jahrgang"] != selected_jahrgang:
            continue
        if selected_competition_id is not None and competition["id"] != selected_competition_id:
            continue
        visible_competitions.append(competition)

    year_options = sorted({c["jahrgang"] for c in competitions if c["jahrgang"] is not None})

    with get_conn() as conn:
        event_rows = conn.execute("""
            SELECT id, name
            FROM events
            ORDER BY event_date, name
        """).fetchall()
        events_by_id = {event_row["id"]: event_row["name"] for event_row in event_rows}

        tables = []
        for competition in visible_competitions:
            if competition["competition_type"] == "Sechskampf":
                teams = conn.execute("""
                    SELECT * FROM teams
                    WHERE active = 1 AND jahrgang = ?
                    ORDER BY name
                """, (competition["jahrgang"],)).fetchall()
                disciplines = conn.execute("""
                    SELECT * FROM competition_disciplines
                    WHERE competition_id = ?
                    ORDER BY sort_order, id
                """, (competition["id"],)).fetchall()
                result_rows = conn.execute("""
                    SELECT * FROM sixkampf_team_results
                    WHERE competition_id = ?
                """, (competition["id"],)).fetchall()
                rows, _, _ = calculate_sixkampf_team_ranking(
                    teams,
                    disciplines,
                    result_rows,
                    competition["points_first_place"],
                    placement_points=get_competition_placement_points(competition),
                )
            else:
                rows = calculate_table(competition["id"])
            tables.append({"competition": competition, "rows": rows})

    overall_ranking = []
    include_overall = selected_event_id is not None and selected_competition_id is None
    if include_overall:
        overall_ranking = calculate_event_overall_ranking(selected_event_id)
        if selected_jahrgang is not None:
            year_group_filter = classify_yeargang(selected_jahrgang)
            if year_group_filter == "Oberstufe":
                overall_ranking = [
                    group for group in overall_ranking
                    if "GOST" in group["year_group"] or "Oberstufe" in group["year_group"]
                ]
            elif year_group_filter:
                overall_ranking = [
                    group for group in overall_ranking
                    if year_group_filter in group["year_group"]
                ]

    return {
        "tables": tables,
        "competitions": competitions,
        "events_by_id": events_by_id,
        "year_options": year_options,
        "selected_event_id": selected_event_id,
        "selected_jahrgang": selected_jahrgang,
        "selected_competition_id": selected_competition_id,
        "overall_ranking": overall_ranking,
    }


def build_tabellen_csv_filename(view_data):
    selected_competition_id = view_data["selected_competition_id"]
    selected_event_id = view_data["selected_event_id"]
    selected_jahrgang = view_data["selected_jahrgang"]

    if selected_competition_id is not None:
        selected_competition = next(
            (table["competition"] for table in view_data["tables"] if table["competition"]["id"] == selected_competition_id),
            None,
        )
        if selected_competition:
            if selected_competition["competition_type"] == "Sechskampf":
                base_name = "sechskampf"
            else:
                base_name = selected_competition["sportart"] or selected_competition["name"]
            return f"{slugify_filename_part(base_name)}_jg{selected_competition['jahrgang']}.csv"

    if selected_event_id is not None and view_data["overall_ranking"]:
        event_name = view_data["events_by_id"].get(selected_event_id, "veranstaltung")
        return f"gesamtwertung_{slugify_filename_part(event_name)}.csv"

    if selected_jahrgang is not None:
        return f"tabellen_jg{selected_jahrgang}.csv"

    return "tabellen_export.csv"


def build_tabellen_csv_content(view_data):
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";", lineterminator="\n")

    for table in view_data["tables"]:
        competition = table["competition"]
        rows = table["rows"]
        writer.writerow(["Wettbewerb", competition["name"]])
        writer.writerow(["Sportart", competition["sportart"], "Jahrgang", competition["jahrgang"], "Typ", competition["competition_type"]])
        if competition["competition_type"] == "Sechskampf":
            writer.writerow(["Platz", "Klasse", "Gesamtsumme", "Wertungspunkte"])
            for row in rows:
                writer.writerow([
                    row["placement"],
                    row["team"]["name"],
                    f"{row['overall_total']:.3f}",
                    row["scoring_points_display"],
                ])
        else:
            writer.writerow(["Platz", "Team", "Sp", "S", "U", "N", "+", "-", "Diff", "Pkt"])
            for index, row in enumerate(rows, start=1):
                writer.writerow([
                    index,
                    row["team"],
                    row["sp"],
                    row["s"],
                    row["u"],
                    row["n"],
                    row["plus"],
                    row["minus"],
                    row["diff"],
                    row["pkt_display"],
                ])
        writer.writerow([])

    for group in view_data["overall_ranking"]:
        writer.writerow(["Gesamtwertung", group["year_group"]])
        header = ["Platz", "Klasse"]
        header.extend([competition["name"] for competition in group["competitions"]])
        header.append("Gesamtpunkte")
        writer.writerow(header)
        for row in group["rows"]:
            row_values = [row["place"], row["team"]]
            for competition in group["competitions"]:
                points_value = row["points_by_competition_display"].get(competition["id"]) or "-"
                row_values.append(points_value)
            row_values.append(row["total_points_display"])
            writer.writerow(row_values)
        writer.writerow([])

    return output.getvalue()


def parse_slot_time(value: str):
    if not value:
        return None
    for time_format in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(value, time_format)
        except ValueError:
            continue
    return None


def get_competition_timing(competition):
    def read_minutes(key, default, minimum):
        try:
            raw_value = competition[key]
        except (KeyError, TypeError, IndexError):
            raw_value = default
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            value = default
        return value if value >= minimum else default

    game_duration_minutes = read_minutes(
        "game_duration_minutes", DEFAULT_GAME_DURATION_MINUTES, 1
    )
    changeover_duration_minutes = read_minutes(
        "changeover_duration_minutes", DEFAULT_CHANGEOVER_DURATION_MINUTES, 0
    )
    return {
        "game_duration_minutes": game_duration_minutes,
        "changeover_duration_minutes": changeover_duration_minutes,
        "slot_interval_minutes": game_duration_minutes + changeover_duration_minutes,
    }


def get_game_end_time(startzeit: str, game_duration_minutes: int):
    start = parse_slot_time(startzeit)
    if start is None:
        return None
    return (start + timedelta(minutes=game_duration_minutes)).strftime("%H:%M")


def build_end_time_forecast(slots, competition):
    if competition is None or competition["competition_type"] != "Turnier":
        return None

    timing = get_competition_timing(competition)
    planned_end = parse_slot_time(competition["end_time"])
    projected_by_court = {}

    for slot in slots:
        if slot.get("slot_typ") != "Spiel":
            continue
        start = parse_slot_time(slot.get("startzeit"))
        if start is None:
            continue
        projected_end = start + timedelta(
            minutes=timing["game_duration_minutes"]
        )
        court_key = slot.get("court_id")
        court_name = slot.get("court_name") or "Ohne Feld"
        existing = projected_by_court.get(court_key)
        if existing is None or projected_end > existing["projected_end_value"]:
            projected_by_court[court_key] = {
                "court_id": court_key,
                "court_name": court_name,
                "projected_end_value": projected_end,
            }

    if not projected_by_court:
        return {
            "courts": [],
            "overall_end": None,
            "planned_end": planned_end.strftime("%H:%M") if planned_end else None,
            "exceeds_planned_end": False,
        }

    courts = sorted(
        projected_by_court.values(),
        key=lambda entry: (entry["projected_end_value"], entry["court_name"]),
    )
    for entry in courts:
        entry["projected_end"] = entry["projected_end_value"].strftime("%H:%M")
        entry["exceeds_planned_end"] = bool(
            planned_end and entry["projected_end_value"] > planned_end
        )
        del entry["projected_end_value"]

    overall_end = max(
        parse_slot_time(entry["projected_end"]) for entry in courts
    )
    return {
        "courts": courts,
        "overall_end": overall_end.strftime("%H:%M"),
        "planned_end": planned_end.strftime("%H:%M") if planned_end else None,
        "exceeds_planned_end": bool(
            planned_end and overall_end > planned_end
        ),
    }


def recalculate_competition_court_times(conn, competition_id: int, court_id):
    competition = conn.execute(
        "SELECT * FROM competitions WHERE id = ?", (competition_id,)
    ).fetchone()
    if competition is None:
        return None

    start = parse_slot_time(competition["start_time"])
    if start is None:
        return None

    game_slots = conn.execute("""
        SELECT id
        FROM slots
        WHERE competition_id = ?
          AND court_id IS ?
          AND slot_typ = 'Spiel'
        ORDER BY sort_order, id
    """, (competition_id, court_id)).fetchall()
    if not game_slots:
        return None

    timing = get_competition_timing(competition)
    for index, slot in enumerate(game_slots):
        calculated_start = start + timedelta(
            minutes=index * timing["slot_interval_minutes"]
        )
        conn.execute(
            "UPDATE slots SET startzeit = ? WHERE id = ?",
            (calculated_start.strftime("%H:%M"), slot["id"]),
        )

    projected_end = start + timedelta(
        minutes=(len(game_slots) - 1) * timing["slot_interval_minutes"]
        + timing["game_duration_minutes"]
    )
    planned_end = parse_slot_time(competition["end_time"])
    if planned_end is None or projected_end <= planned_end:
        return None

    if court_id is None:
        court_name = "Ohne Feld"
    else:
        court = conn.execute(
            "SELECT name FROM courts WHERE id = ?", (court_id,)
        ).fetchone()
        court_name = court["name"] if court is not None else f"Feld {court_id}"

    return {
        "court_id": court_id,
        "court_name": court_name,
        "projected_end": projected_end.strftime("%H:%M"),
        "planned_end": planned_end.strftime("%H:%M"),
        "message": (
            f"Warnung: {court_name} endet voraussichtlich um "
            f"{projected_end.strftime('%H:%M')}, geplant war "
            f"{planned_end.strftime('%H:%M')}."
        ),
    }


def parse_event_date(value: str):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def classify_yeargang(value):
    if value is None:
        return None
    try:
        year = int(value)
    except (TypeError, ValueError):
        normalized = str(value).strip().lower()
        if "oberstufe" in normalized or normalized in ("go", "gost", "sg"):
            return "Oberstufe"
        return None

    if year == 7:
        return "Jahrgang 7"
    if year == 8:
        return "Jahrgang 8"
    if year == 9:
        return "Jahrgang 9"
    if year >= 10:
        return "Oberstufe"
    return None


def parse_placement_points_config(raw_value: Optional[str]):
    if raw_value is None:
        return []

    values = []
    normalized = str(raw_value).replace("\n", ",").replace(";", ",")
    for part in normalized.split(","):
        cleaned = part.strip()
        if not cleaned:
            continue
        try:
            value = float(cleaned.replace(",", "."))
        except ValueError:
            return []
        if not isfinite(value) or value < 0:
            return []
        values.append(value)
    return values


def normalize_competition_location(raw_value: Optional[str]):
    if raw_value is None:
        return None
    normalized = str(raw_value).strip()
    if not normalized:
        return None
    normalized = COMPETITION_LOCATION_ALIASES.get(normalized, normalized)
    if normalized in COMPETITION_LOCATIONS:
        return normalized
    return None


def build_default_placement_points(points_first_place: int):
    try:
        highest_points = int(points_first_place)
    except (TypeError, ValueError):
        highest_points = 0
    highest_points = max(highest_points, 0)
    return [float(value) for value in range(highest_points, 0, -1)]


def get_competition_placement_points(competition):
    configured_points = parse_placement_points_config(competition["placement_points"])
    if configured_points:
        return configured_points
    return build_default_placement_points(competition["points_first_place"])


def get_points_for_placement(placement_points, placement: int):
    if placement < 1 or placement > len(placement_points):
        return 0.0
    return placement_points[placement - 1]



def combine_time_on_date(value: datetime, target_date: date):
    if value is None or target_date is None:
        return None
    return datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        value.hour,
        value.minute,
        value.second,
    )


def format_time_range(start: datetime, end: datetime):
    return f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}"


def build_day_schedule(competitions, event_date=None, now=None):
    columns = ["Jahrgang 7", "Jahrgang 8", "Jahrgang 9", "Oberstufe"]
    blocks = {}
    now = now or app_now().replace(tzinfo=None)
    event_day = parse_event_date(event_date)
    if event_day is None:
        event_state = "undated"
    elif event_day < now.date():
        event_state = "past"
    elif event_day > now.date():
        event_state = "future"
    else:
        event_state = "today"

    def get_field(item, key):
        if hasattr(item, "get"):
            return item.get(key)
        return item[key]

    for competition in competitions:
        start_value = get_field(competition, "start_time")
        end_value = get_field(competition, "end_time")
        if not start_value or not end_value:
            continue

        start = parse_slot_time(start_value)
        end = parse_slot_time(end_value)
        if not start or not end or end <= start:
            continue

        yeargang = classify_yeargang(get_field(competition, "jahrgang"))
        if yeargang is None:
            continue

        key = (start.strftime("%H:%M"), end.strftime("%H:%M"))
        block = blocks.setdefault(key, {
            "start": start,
            "end": end,
            "display_time": format_time_range(start, end),
            "cells": {col: [] for col in columns},
        })

        label = get_field(competition, "name") or get_field(competition, "sportart") or "–"
        if label not in block["cells"][yeargang]:
            block["cells"][yeargang].append(label)

    rows = sorted(blocks.values(), key=lambda block: (block["start"].time(), block["end"].time()))
    current_block = None
    next_block = None

    if event_state == "today":
        for block in rows:
            start_at = combine_time_on_date(block["start"], event_day)
            end_at = combine_time_on_date(block["end"], event_day)
            if start_at <= now < end_at:
                current_block = {**block, "start_at": start_at, "end_at": end_at}
                break

        future_blocks = [
            {**block, "start_at": combine_time_on_date(block["start"], event_day)}
            for block in rows
            if combine_time_on_date(block["start"], event_day) > now
        ]
        if future_blocks:
            next_block = min(future_blocks, key=lambda block: block["start_at"])
    elif event_state == "future" and rows:
        next_block = {
            **rows[0],
            "start_at": combine_time_on_date(rows[0]["start"], event_day),
        }

    completed = False
    if rows and event_day is not None:
        final_end = max(
            combine_time_on_date(row["end"], event_day) for row in rows
        )
        completed = event_state == "past" or (
            event_state == "today" and now >= final_end
        )

    def build_entries(block):
        entries = []
        for col in columns:
            for label in block["cells"][col]:
                year_label = col.replace("Jahrgang ", "JG")
                entries.append({"yeargang": year_label, "label": label})
        return entries

    schedule = {
        "columns": columns,
        "rows": [
            {
                "display_time": row["display_time"],
                "cells": row["cells"],
                "current": event_state == "today"
                and combine_time_on_date(row["start"], event_day) <= now
                < combine_time_on_date(row["end"], event_day),
            }
            for row in rows
        ],
        "current_block": None,
        "next_block": None,
        "hint_text": "Aktuell kein Zeitblock aktiv",
        "active_competitions": [],
        "time_remaining": None,
        "progress_percent": 100 if completed else 0,
        "completed": completed,
        "event_state": event_state,
        "event_date": event_day.isoformat() if event_day else None,
    }

    if current_block:
        remaining_seconds = int((current_block["end_at"] - now).total_seconds())
        remaining_minutes = max(0, remaining_seconds // 60)
        schedule["current_block"] = {
            "display_time": current_block["display_time"],
            "entries": build_entries(current_block),
        }
        schedule["active_competitions"] = schedule["current_block"]["entries"]
        schedule["hint_text"] = f"Aktueller Zeitblock: {current_block['display_time']}"
        schedule["time_remaining"] = f"{remaining_minutes} Minuten"
        total_seconds = int((current_block["end_at"] - current_block["start_at"]).total_seconds())
        elapsed_seconds = int((now - current_block["start_at"]).total_seconds())
        if total_seconds > 0:
            schedule["progress_percent"] = max(0, min(100, int(elapsed_seconds / total_seconds * 100)))

    if next_block:
        schedule["next_block"] = {
            "display_time": next_block["display_time"],
            "entries": build_entries(next_block),
        }
        if not current_block:
            schedule["hint_text"] = "Aktuell kein Zeitblock aktiv"

    if completed:
        schedule["hint_text"] = "Veranstaltung abgeschlossen"

    return schedule


def get_day_schedule_for_event(event_id: int):
    with get_conn() as conn:
        event = conn.execute(
            "SELECT event_date FROM events WHERE id = ?", (event_id,)
        ).fetchone()
        competitions = [dict(row) for row in conn.execute("""
            SELECT * FROM competitions
            WHERE event_id = ?
              AND status != 'archiviert'
            ORDER BY start_time, end_time, jahrgang, name
        """, (event_id,)).fetchall()]
    return build_day_schedule(
        competitions,
        event["event_date"] if event is not None else None,
    )


def get_unique_competition_name(conn, original_name: str):
    base_name = f"{original_name} (Kopie)"
    candidate = base_name
    copy_number = 2
    while conn.execute("SELECT 1 FROM competitions WHERE name = ?", (candidate,)).fetchone():
        candidate = f"{base_name} {copy_number}"
        copy_number += 1
    return candidate


def get_unique_event_name(conn, original_name: str):
    base_name = f"{original_name} (Kopie)"
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
    teams, disciplines, result_rows, points_first_place: int = 7,
    require_result_entry: bool = False,
    placement_points=None,
):
    totals_by_team_discipline = defaultdict(float)
    overall_totals = {team["id"]: 0.0 for team in teams}
    teams_with_results = {result["team_id"] for result in result_rows}
    if placement_points is None:
        placement_points = build_default_placement_points(points_first_place)

    for result in result_rows:
        key = (result["team_id"], result["discipline_id"])
        totals_by_team_discipline[key] += result["value"]

    ranking_teams = teams
    if require_result_entry:
        ranking_teams = [
            team for team in teams
            if team["id"] in teams_with_results
        ]

    ranking = []
    for team in ranking_teams:
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
        row["scoring_points"] = get_points_for_placement(placement_points, placement)
        row["scoring_points_display"] = format_points_value(row["scoring_points"])

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


def fetch_dashboard_data():
    with get_conn() as conn:
        competitions = [dict(row) for row in conn.execute("""
            SELECT *
            FROM competitions
            WHERE status != 'archiviert'
            ORDER BY jahrgang, name
        """).fetchall()]

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

        upcoming_events = [dict(row) for row in conn.execute("""
            SELECT e.*,
                   (
                       SELECT COUNT(*)
                       FROM competitions c
                       WHERE c.event_id = e.id
                   ) AS competition_count
            FROM events e
            WHERE e.event_date IS NOT NULL
              AND TRIM(e.event_date) != ''
              AND e.event_date >= date('now', 'localtime')
            ORDER BY e.event_date ASC, e.name ASC
            LIMIT 4
        """).fetchall()]

        latest_past_event_row = conn.execute("""
            SELECT e.*,
                   (
                       SELECT COUNT(*)
                       FROM competitions c
                       WHERE c.event_id = e.id
                   ) AS competition_count
            FROM events e
            WHERE e.event_date IS NOT NULL
              AND TRIM(e.event_date) != ''
              AND e.event_date < date('now', 'localtime')
            ORDER BY e.event_date DESC, e.name ASC
            LIMIT 1
        """).fetchone()

    next_event = upcoming_events[0] if upcoming_events else None
    additional_upcoming_events = upcoming_events[1:4] if len(upcoming_events) > 1 else []
    latest_past_event = dict(latest_past_event_row) if latest_past_event_row else None

    return {
        "competitions": competitions,
        "teams": teams,
        "courts": courts,
        "running": running,
        "upcoming": upcoming,
        "ended_count": ended_count,
        "next_event": next_event,
        "schedule_event": next_event or latest_past_event,
        "additional_upcoming_events": additional_upcoming_events,
    }


def get_all_slots(
    competition_id: Optional[int] = None,
    jahrgang: Optional[int] = None,
    location: Optional[str] = None,
):
    query = """
        SELECT slots.*, c.name AS competition_name, c.sportart, c.jahrgang,
               c.location AS competition_location,
               c.competition_type,
               c.game_duration_minutes,
               c.changeover_duration_minutes,
               c.start_time AS competition_start_time,
               c.end_time AS competition_end_time,
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

    if jahrgang is not None:
        query += " AND c.jahrgang = ?"
        params.append(jahrgang)

    if location is not None:
        query += " AND c.location = ?"
        params.append(location)

    query += """
    ORDER BY
        slots.court_id,
        slots.sort_order,
        slots.startzeit,
        slots.id
    """

    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()

    slots = [dict(row) for row in rows]
    change_counts = {}
    if slots:
        slot_ids = [slot["id"] for slot in slots]
        placeholders = ",".join("?" for _ in slot_ids)
        with get_conn() as conn:
            change_counts = {
                row["entity_id"]: row["change_count"]
                for row in conn.execute(
                    f"""
                    SELECT entity_id, COUNT(*) AS change_count
                    FROM change_log
                    WHERE entity_type = 'slot_result'
                      AND entity_id IN ({placeholders})
                    GROUP BY entity_id
                    """,
                    slot_ids,
                ).fetchall()
            }
    for slot in slots:
        slot["change_count"] = change_counts.get(slot["id"], 0)
        slot["planned_duration_seconds"] = None
        slot["game_end_time"] = None
        if slot["slot_typ"] != "Spiel" or slot["competition_type"] == "Sechskampf":
            continue
        timing = get_competition_timing(slot)
        slot["planned_duration_seconds"] = timing["game_duration_minutes"] * 60
        slot["game_end_time"] = get_game_end_time(
            slot["startzeit"], timing["game_duration_minutes"]
        )

    return slots


def get_slots_grouped_by_court(
    competition_id: Optional[int] = None,
    jahrgang: Optional[int] = None,
    location: Optional[str] = None,
):
    with get_conn() as conn:
        courts = conn.execute("SELECT * FROM courts WHERE active = 1 ORDER BY name").fetchall()

    slots = [
        dict(slot)
        for slot in get_all_slots(competition_id, jahrgang, location)
    ]
    grouped = {court["id"]: {"court": court, "slots": []} for court in courts}
    without_court = {"court": {"id": "", "name": "Ohne Feld"}, "slots": []}

    for slot in slots:
        if slot["court_id"] in grouped:
            grouped[slot["court_id"]]["slots"].append(slot)
        else:
            without_court["slots"].append(slot)

    return list(grouped.values()) + [without_court]


def prepare_editor_time_grid(groups, competition=None):
    time_values = {
        slot["startzeit"]
        for group in groups
        for slot in group["slots"]
        if parse_slot_time(slot.get("startzeit")) is not None
    }

    if competition is not None and competition["competition_type"] == "Turnier":
        timing = get_competition_timing(competition)
        interval = timing["slot_interval_minutes"]
        start = parse_slot_time(competition["start_time"])
        planned_end = parse_slot_time(competition["end_time"])

        if start is not None and interval > 0:
            generated_time = start
            generated_count = 0
            while generated_count < 200:
                if planned_end is not None and planned_end > start:
                    if generated_time >= planned_end:
                        break
                elif generated_count >= 3:
                    break
                time_values.add(generated_time.strftime("%H:%M"))
                generated_time += timedelta(minutes=interval)
                generated_count += 1

    time_marks = sorted(time_values, key=lambda value: parse_slot_time(value))
    row_by_time = {
        startzeit: index + 2 for index, startzeit in enumerate(time_marks)
    }
    for group in groups:
        for slot in group["slots"]:
            slot["editor_grid_row"] = row_by_time.get(slot.get("startzeit"), 2)
    return time_marks


def get_unslotted_competitions_by_location(
    competition_id: Optional[int] = None,
    jahrgang: Optional[int] = None,
    location: Optional[str] = None,
):
    clauses = [
        "competition.status != 'archiviert'",
        "competition.start_time IS NOT NULL",
        "TRIM(competition.start_time) != ''",
        "competition.end_time IS NOT NULL",
        "TRIM(competition.end_time) != ''",
        """
        NOT EXISTS (
            SELECT 1
            FROM slots
            WHERE slots.competition_id = competition.id
              AND slots.slot_typ = 'Spiel'
        )
        """,
    ]
    params = []
    if competition_id is not None:
        clauses.append("competition.id = ?")
        params.append(competition_id)
    if jahrgang is not None:
        clauses.append("competition.jahrgang = ?")
        params.append(jahrgang)
    if location is not None:
        clauses.append("competition.location = ?")
        params.append(location)

    with get_conn() as conn:
        competitions = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT competition.*
                FROM competitions AS competition
                WHERE {' AND '.join(clauses)}
                """,
                params,
            ).fetchall()
        ]

        disciplines_by_competition = defaultdict(list)
        if competitions:
            placeholders = ",".join("?" for _ in competitions)
            discipline_rows = conn.execute(
                f"""
                SELECT competition_id, name, sort_order
                FROM competition_disciplines
                WHERE competition_id IN ({placeholders})
                ORDER BY competition_id, sort_order, id
                """,
                [competition["id"] for competition in competitions],
            ).fetchall()
            for discipline in discipline_rows:
                disciplines_by_competition[discipline["competition_id"]].append(
                    dict(discipline)
                )

    entries = []
    for competition in competitions:
        start = parse_slot_time(competition["start_time"])
        end = parse_slot_time(competition["end_time"])
        if start is None or end is None:
            continue
        competition["time_label"] = format_time_range(start, end)
        competition["disciplines"] = disciplines_by_competition.get(
            competition["id"], []
        )
        entries.append(competition)

    location_order = {
        location: index for index, location in enumerate(COMPETITION_LOCATIONS)
    }
    entries.sort(
        key=lambda competition: (
            location_order.get(competition.get("location"), 99),
            competition["start_time"],
            competition["end_time"],
            competition["name"].casefold(),
        )
    )

    groups = []
    for location in (*COMPETITION_LOCATIONS, None):
        location_competitions = [
            competition
            for competition in entries
            if normalize_competition_location(competition.get("location")) == location
        ]
        if location_competitions:
            groups.append({
                "location": location,
                "competitions": location_competitions,
            })
    return groups


def get_competition_timeline_for_location(
    location: str,
    competition_id: Optional[int] = None,
    jahrgang: Optional[int] = None,
):
    clauses = [
        "status != 'archiviert'",
        "location = ?",
    ]
    params = [location]
    if competition_id is not None:
        clauses.append("id = ?")
        params.append(competition_id)
    if jahrgang is not None:
        clauses.append("jahrgang = ?")
        params.append(jahrgang)

    with get_conn() as conn:
        competitions = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT *
                FROM competitions
                WHERE {' AND '.join(clauses)}
                ORDER BY
                    CASE
                        WHEN start_time IS NULL OR TRIM(start_time) = '' THEN 1
                        ELSE 0
                    END,
                    start_time,
                    end_time,
                    name
                """,
                params,
            ).fetchall()
        ]

    for competition in competitions:
        start = parse_slot_time(competition.get("start_time"))
        end = parse_slot_time(competition.get("end_time"))
        competition["start_time_display"] = (
            start.strftime("%H:%M") if start else "–"
        )
        competition["end_time_display"] = (
            end.strftime("%H:%M") if end else "–"
        )
    return competitions


def get_location_schedule_sections(
    competition_id: Optional[int] = None,
    jahrgang: Optional[int] = None,
    selected_location: Optional[str] = None,
):
    unslotted_groups = get_unslotted_competitions_by_location(
        competition_id,
        jahrgang,
        selected_location,
    )
    unslotted_by_location = {
        group["location"]: group["competitions"]
        for group in unslotted_groups
    }
    locations = (
        (selected_location,)
        if selected_location
        else (*COMPETITION_LOCATIONS, None)
    )
    all_slots = get_all_slots(competition_id, jahrgang)
    sections = []

    for location in locations:
        timeline = []
        court_groups = []
        competitions = unslotted_by_location.get(location, [])

        if location == "Außenbereich":
            timeline = get_competition_timeline_for_location(
                location,
                competition_id,
                jahrgang,
            )
            competitions = []
        else:
            location_slots = [
                slot
                for slot in all_slots
                if normalize_competition_location(slot.get("competition_location")) == location
            ]
            if location_slots:
                with get_conn() as conn:
                    courts = conn.execute(
                        "SELECT * FROM courts WHERE active = 1 ORDER BY name"
                    ).fetchall()
                grouped = {
                    court["id"]: {"court": court, "slots": []}
                    for court in courts
                }
                without_court = {
                    "court": {"id": "", "name": "Ohne Feld"},
                    "slots": [],
                }
                for slot in location_slots:
                    target = grouped.get(slot["court_id"], without_court)
                    target["slots"].append(slot)
                court_groups = [
                    group
                    for group in (*grouped.values(), without_court)
                    if group["slots"]
                ]

        has_entries = bool(timeline or court_groups or competitions)
        if has_entries or (
            selected_location is not None and selected_location == location
        ):
            sections.append({
                "location_key": location or "ohne-ortsangabe",
                "location": location or "Keine Ortsangabe",
                "timeline": timeline,
                "court_groups": court_groups,
                "competitions": competitions,
                "has_entries": has_entries,
            })

    return sections



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


def calculate_tournament_points(competition):
    with get_conn() as conn:
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
        """, (competition["id"],)).fetchall()

    if not slots:
        return []

    rows = calculate_table(competition["id"])
    placement_points = get_competition_placement_points(competition)

    previous = None
    previous_place = 0
    for index, row in enumerate(rows, start=1):
        if (
            previous is not None
            and row["pkt"] == previous["pkt"]
            and row["diff"] == previous["diff"]
            and row["plus"] == previous["plus"]
        ):
            direct = calculate_direct_comparison(previous["team"], row["team"], slots, competition)
            place = previous_place if direct == 0 else index
        else:
            place = index

        row["placement"] = place
        row["competition_points"] = get_points_for_placement(placement_points, place)
        row["competition_points_display"] = format_points_value(row["competition_points"])
        previous = row
        previous_place = place

    return rows


def format_duration(start_time: str, end_time: str):
    if not start_time or not end_time:
        return None
    for time_format in ("%H:%M", "%H:%M:%S"):
        try:
            start = datetime.strptime(start_time, time_format)
            end = datetime.strptime(end_time, time_format)
            break
        except ValueError:
            start = end = None
    if not start or not end:
        return None
    duration = end - start
    if duration.total_seconds() < 0:
        return None
    minutes = int(duration.total_seconds() // 60)
    hours = minutes // 60
    minutes = minutes % 60
    return f"{hours}h {minutes}m" if hours else f"{minutes} min"



def calculate_event_overall_ranking(event_id: int):
    with get_conn() as conn:
        competitions = conn.execute("""
            SELECT * FROM competitions
            WHERE event_id = ?
            ORDER BY jahrgang, name
        """, (event_id,)).fetchall()

    if not competitions:
        return []

    grouped_competitions = {}
    for competition in competitions:
        year_group = classify_yeargang(competition["jahrgang"])
        if year_group is None:
            continue
        grouped_competitions.setdefault(year_group, []).append({
            "id": competition["id"],
            "name": competition["name"],
            "jahrgang": competition["jahrgang"],
            "competition_type": competition["competition_type"],
        })

    jahrgaenge = sorted({competition["jahrgang"] for competition in competitions})
    teams_by_jahrgang = {}
    with get_conn() as conn:
        if jahrgaenge:
            placeholders = ", ".join(["?"] * len(jahrgaenge))
            teams = conn.execute(f"""
                SELECT id, name, jahrgang
                FROM teams
                WHERE active = 1
                  AND jahrgang IN ({placeholders})
                ORDER BY jahrgang, name
            """, tuple(jahrgaenge)).fetchall()
        else:
            teams = []

    for team in teams:
        teams_by_jahrgang.setdefault(team["jahrgang"], []).append(team)

    overall = {}
    for competition in competitions:
        for team in teams_by_jahrgang.get(competition["jahrgang"], []):
            record = overall.setdefault(team["id"], {
                "team_id": team["id"],
                "team": team["name"],
                "jahrgang": team["jahrgang"],
                "points_by_competition": {},
                "total_points": 0.0,
                "first_places": 0,
                "second_places": 0,
            })
            record["points_by_competition"].setdefault(
                competition["id"],
                None,
            )

    for competition in competitions:
        competition_id = competition["id"]
        placement_points = get_competition_placement_points(competition)
        if competition["competition_type"] == "Sechskampf":
            competition_teams = teams_by_jahrgang.get(competition["jahrgang"], [])
            with get_conn() as conn:
                disciplines = conn.execute("""
                    SELECT * FROM competition_disciplines
                    WHERE competition_id = ?
                    ORDER BY sort_order, id
                """, (competition_id,)).fetchall()
                result_rows = conn.execute(
                    "SELECT * FROM sixkampf_team_results WHERE competition_id = ?",
                    (competition_id,)
                ).fetchall()

            if not result_rows:
                continue

            ranking, _, _ = calculate_sixkampf_team_ranking(
                competition_teams,
                disciplines,
                result_rows,
                competition["points_first_place"],
                require_result_entry=True,
                placement_points=placement_points,
            )
            for row in ranking:
                team_id = row["team"]["id"]
                record = overall.setdefault(team_id, {
                    "team_id": team_id,
                    "team": row["team"]["name"],
                    "jahrgang": row["team"]["jahrgang"],
                    "points_by_competition": {},
                    "total_points": 0.0,
                    "first_places": 0,
                    "second_places": 0,
                })
                record["points_by_competition"][competition_id] = row["scoring_points"]
                if row["placement"] == 1:
                    record["first_places"] += 1
                elif row["placement"] == 2:
                    record["second_places"] += 1
        else:
            rows = calculate_tournament_points(competition)
            for row in rows:
                team_id = row["team_id"]
                record = overall.setdefault(team_id, {
                    "team_id": team_id,
                    "team": row["team"],
                    "jahrgang": competition["jahrgang"],
                    "points_by_competition": {},
                    "total_points": 0.0,
                    "first_places": 0,
                    "second_places": 0,
                })
                record["points_by_competition"][competition_id] = row.get("competition_points", 0)
                if row["placement"] == 1:
                    record["first_places"] += 1
                elif row["placement"] == 2:
                    record["second_places"] += 1

    for record in overall.values():
        record["total_points"] = sum(
            value
            for value in record["points_by_competition"].values()
            if isinstance(value, (int, float))
        )
        record["total_points_display"] = format_points_value(record["total_points"])
        record["points_by_competition_display"] = {
            competition_id: format_points_value(value)
            for competition_id, value in record["points_by_competition"].items()
        }

    year_groups = {}

    for row in overall.values():
        year_group = classify_yeargang(row["jahrgang"])
        if year_group is None:
            continue
        year_groups.setdefault(year_group, []).append(row)

    ordered_groups = ["Jahrgang 7", "Jahrgang 8", "Jahrgang 9", "Oberstufe"]
    display_year_groups = {
        "Jahrgang 7": "Gesamtwertung Jahrgang 7",
        "Jahrgang 8": "Gesamtwertung Jahrgang 8",
        "Jahrgang 9": "Gesamtwertung Jahrgang 9",
        "Oberstufe": "Gesamtwertung GOST",
    }
    result = []
    for year_group in ordered_groups:
        rows = year_groups.get(year_group)
        if not rows:
            continue

        rows.sort(key=lambda row: (
            -row["total_points"],
            -row["first_places"],
            -row["second_places"],
            row["team"].lower(),
        ))

        place = 0
        previous_rank_key = None
        for index, row in enumerate(rows, start=1):
            rank_key = (
                row["total_points"],
                row["first_places"],
                row["second_places"],
            )
            if previous_rank_key is None or rank_key != previous_rank_key:
                place = index
            row["place"] = place
            previous_rank_key = rank_key

        result.append({
            "year_group": display_year_groups.get(year_group, year_group),
            "competitions": grouped_competitions.get(year_group, []),
            "rows": rows,
        })

    return result


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

def generate_group_plan(
    competition_id: int,
    court_ids: List[int],
    startzeit: str,
    games_per_team: int,
    include_ko: bool,
):
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
        counts = {team: 0 for team in team_names}

        for i, team_a in enumerate(team_names):
            for team_b in team_names[i + 1:]:
                if counts[team_a] < games_per_team and counts[team_b] < games_per_team:
                    pairings.append((team_a, team_b, ""))
                    counts[team_a] += 1
                    counts[team_b] += 1

    proposed_slots = []
    current_time = datetime.strptime(startzeit, "%H:%M")

    remaining_pairings = pairings[:]
    last_round_by_team = {team_name: -1000 for team_name in team_names}
    round_index = 0

    while remaining_pairings:
        time_value = current_time.strftime("%H:%M")
        used_teams = set()
        scheduled_indices = []

        for court_id in court_ids:
            selected_index = None

            candidate_scores = []
            for index, (team_a, team_b, _gruppe) in enumerate(remaining_pairings):
                if team_a in used_teams or team_b in used_teams:
                    continue

                gap_a = round_index - last_round_by_team.get(team_a, -1000)
                gap_b = round_index - last_round_by_team.get(team_b, -1000)
                consecutive_count = int(gap_a == 1) + int(gap_b == 1)
                # Bevorzuge Paarungen ohne direkte Folgespiele; falls nicht moeglich,
                # bleibt die Planung bewusst permissiv.
                candidate_scores.append((
                    consecutive_count,
                    -min(gap_a, gap_b),
                    -(gap_a + gap_b),
                    index,
                ))

            if candidate_scores:
                candidate_scores.sort()
                selected_index = candidate_scores[0][3]

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

            used_teams.add(team_a)
            used_teams.add(team_b)
            last_round_by_team[team_a] = round_index
            last_round_by_team[team_b] = round_index
            scheduled_indices.append(selected_index)

        for index in sorted(scheduled_indices, reverse=True):
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


@app.get("/login")
def login_page(request: Request, next: str = "/", logged_out: str = ""):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "next_url": safe_next_url(next),
            "admin_login_prepared": is_login_prepared(),
            "helper_login_prepared": bool(get_referee_password()),
            "security_enabled": is_security_enabled(),
            "logged_in": is_logged_in(request),
            "current_role": get_current_role(request),
            "current_role_label": get_current_role_label(request),
            "logged_out": logged_out == "1",
            "login_error": "",
        },
    )


@app.post("/login")
def login(
    request: Request,
    password: str = Form(""),
    next: str = Form("/"),
    target_role: str = Form("admin"),
):
    role_passwords = {
        "referee": get_referee_password(),
        "admin": get_admin_password(),
    }
    configured_password = role_passwords.get(target_role, "")
    if configured_password and secrets.compare_digest(password, configured_password):
        request.session.clear()
        request.session["admin_logged_in"] = target_role == "admin"
        request.session["role"] = target_role
        return RedirectResponse(safe_next_url(next), status_code=303)

    role_label = ROLE_LABELS.get(target_role, "ausgewählte")
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "next_url": safe_next_url(next),
            "admin_login_prepared": is_login_prepared(),
            "helper_login_prepared": bool(get_referee_password()),
            "security_enabled": is_security_enabled(),
            "logged_in": is_logged_in(request),
            "current_role": get_current_role(request),
            "current_role_label": get_current_role_label(request),
            "logged_out": False,
            "login_error": (
                f"Das {role_label}-Passwort ist nicht korrekt."
                if configured_password
                else f"Es ist noch kein Passwort für die Rolle {role_label} konfiguriert."
            ),
        },
        status_code=401 if configured_password else 200,
    )


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/")
def dashboard(request: Request):
    data = fetch_dashboard_data()
    if data["schedule_event"]:
        data["schedule"] = get_day_schedule_for_event(data["schedule_event"]["id"])
    else:
        data["schedule"] = build_day_schedule([], event_date=None)
    return templates.TemplateResponse(request=request, name="dashboard.html", context=data)


@app.get("/spielplan")
def spielplan(
    request: Request,
    competition_id: str = "",
    jahrgang: str = "",
    ort: str = "",
):
    selected_competition_id = parse_competition_id(competition_id)
    selected_jahrgang = parse_jahrgang_filter(jahrgang)
    selected_location = normalize_competition_location(ort)

    competitions = get_active_competitions()
    location_sections = get_location_schedule_sections(
        selected_competition_id,
        selected_jahrgang,
        selected_location,
    )
    available_jahrgaenge = sorted({competition["jahrgang"] for competition in competitions})

    return templates.TemplateResponse(
        request=request,
        name="spielplan.html",
        context={
            "location_sections": location_sections,
            "competitions": competitions,
            "selected_competition_id": selected_competition_id,
            "selected_jahrgang": selected_jahrgang,
            "selected_location": selected_location,
            "competition_locations": COMPETITION_LOCATIONS,
            "available_jahrgaenge": available_jahrgaenge,
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
    selected_competition = next(
        (
            competition
            for competition in competitions
            if competition["id"] == selected_competition_id
        ),
        None,
    )
    editor_time_marks = prepare_editor_time_grid(groups, selected_competition)
    selected_timing = (
        get_competition_timing(selected_competition)
        if selected_competition is not None
        and selected_competition["competition_type"] == "Turnier"
        else None
    )
    current_end_time_forecast = build_end_time_forecast(
        [
            slot
            for group in groups
            for slot in group["slots"]
        ],
        selected_competition,
    )

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
            "editor_time_marks": editor_time_marks,
            "proposed_slots": [],
            "plan_warnings": [],
            "has_plan_errors": False,
            "plan_timing": None,
            "selected_timing": selected_timing,
            "current_end_time_forecast": current_end_time_forecast,
            "plan_end_time_forecast": None,
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
    games_per_team: int = Form(...),
    include_ko: str = Form("0"),
):
    proposed_slots = generate_group_plan(
        competition_id=competition_id,
        court_ids=court_ids,
        startzeit=startzeit,
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
    plan_timing = get_competition_timing(competition)
    plan_end_time_forecast = build_end_time_forecast(
        proposed_slots,
        competition,
    )
    if plan_end_time_forecast:
        for court in plan_end_time_forecast["courts"]:
            if court["exceeds_planned_end"]:
                plan_warnings.append({
                    "level": "warning",
                    "message": (
                        f"{court['court_name']} endet voraussichtlich um "
                        f"{court['projected_end']}, geplant war "
                        f"{plan_end_time_forecast['planned_end']}."
                    ),
                })
    has_plan_errors = any(warning["level"] == "error" for warning in plan_warnings)
    editor_time_marks = prepare_editor_time_grid(groups, competition)
    current_end_time_forecast = build_end_time_forecast(
        [
            slot
            for group in groups
            for slot in group["slots"]
        ],
        competition,
    )

    return templates.TemplateResponse(
        request=request,
        name="spielplan_bearbeiten.html",
        context={
            "groups": groups,
            "competitions": competitions,
            "courts": courts,
            "teams": teams,
            "selected_competition_id": competition_id,
            "editor_time_marks": editor_time_marks,
            "proposed_slots": proposed_slots,
            "plan_warnings": plan_warnings,
            "has_plan_errors": has_plan_errors,
            "plan_timing": plan_timing,
            "selected_timing": plan_timing,
            "current_end_time_forecast": current_end_time_forecast,
            "plan_end_time_forecast": plan_end_time_forecast,
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
            if slot_typ[i] != "Spiel":
                continue
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
        slot = conn.execute(
            "SELECT started_at FROM slots WHERE id = ?",
            (slot_id,)
        ).fetchone()
        started_at_value = slot["started_at"] if slot else None
        if status == "läuft" and not started_at_value:
            started_at_value = app_now_db_timestamp()
        if status == "geplant":
            started_at_value = None

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
                note = ?,
                started_at = ?
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
            started_at_value,
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
    sort_order: int = Form(0)
):
    court_value = int(court_id) if court_id else None

    with get_conn() as conn:
        slot = conn.execute(
            "SELECT competition_id, court_id FROM slots WHERE id = ?", (slot_id,)
        ).fetchone()
        if slot is None:
            return JSONResponse({"success": False, "warnings": []}, status_code=404)
        affected_courts = {
            (slot["competition_id"], slot["court_id"]),
            (slot["competition_id"], court_value),
        }
        conn.execute("""
            UPDATE slots
            SET court_id = ?,
                sort_order = ?
            WHERE id = ?
        """, (
            court_value,
            sort_order,
            slot_id
        ))
        warnings = []
        for competition_id, affected_court_id in affected_courts:
            warning = recalculate_competition_court_times(
                conn, competition_id, affected_court_id
            )
            if warning:
                warnings.append(warning)
        conn.commit()

    return JSONResponse({"success": True, "warnings": warnings})

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

        competition = conn.execute(
            "SELECT * FROM competitions WHERE id = ?", (slot["competition_id"],)
        ).fetchone()
        timing = get_competition_timing(competition)

        try:
            new_time = (
                datetime.strptime(slot["startzeit"], "%H:%M")
                + timedelta(minutes=timing["slot_interval_minutes"])
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

        recalculate_competition_court_times(
            conn, slot["competition_id"], slot["court_id"]
        )

        conn.commit()

    return RedirectResponse(
        f"/spielplan-bearbeiten?competition_id={slot['competition_id']}",
        status_code=303

    )

@app.get("/ergebnisse")
def ergebnisse(
    request: Request, competition_id: str = "", discipline_id: str = "",
    saved_team_id: str = "", saved_at: str = "",
):
    selected_competition_id = parse_competition_id(competition_id)
    competitions = get_active_competitions()
    selected_competition = next(
        (competition for competition in competitions
         if competition["id"] == selected_competition_id),
        None,
    )

    if (
        selected_competition is not None
        and selected_competition["competition_type"] == "Sechskampf"
    ):
        with get_conn() as conn:
            teams = conn.execute("""
                SELECT * FROM teams
                WHERE active = 1 AND jahrgang = ?
                ORDER BY name
            """, (selected_competition["jahrgang"],)).fetchall()
            disciplines = conn.execute("""
                SELECT * FROM competition_disciplines
                WHERE competition_id = ?
                ORDER BY sort_order, id
            """, (selected_competition_id,)).fetchall()
            result_rows = conn.execute("""
                SELECT * FROM sixkampf_team_results
                WHERE competition_id = ?
            """, (selected_competition_id,)).fetchall()
            change_counts = {
                (row["team_id"], row["discipline_id"]): row["change_count"]
                for row in conn.execute(
                    """
                    SELECT team_id, discipline_id, COUNT(*) AS change_count
                    FROM change_log
                    WHERE entity_type = 'sixkampf_result'
                      AND competition_id = ?
                    GROUP BY team_id, discipline_id
                    """,
                    (selected_competition_id,),
                ).fetchall()
            }

        show_evaluation = discipline_id == "auswertung"
        selected_discipline_id = None
        if not show_evaluation:
            try:
                selected_discipline_id = int(discipline_id) if discipline_id else None
            except ValueError:
                selected_discipline_id = None
        selected_discipline = None if show_evaluation else next(
            (discipline for discipline in disciplines
             if discipline["id"] == selected_discipline_id),
            disciplines[0] if disciplines else None,
        )
        try:
            saved_team_id_value = int(saved_team_id) if saved_team_id else None
        except ValueError:
            saved_team_id_value = None
        try:
            saved_at_value = (
                datetime.strptime(saved_at, "%H:%M").strftime("%H:%M")
                if saved_at else ""
            )
        except ValueError:
            saved_at_value = ""

        ranking, totals_by_team_discipline, overall_totals = (
            calculate_sixkampf_team_ranking(
                teams, disciplines, result_rows,
                selected_competition["points_first_place"],
                require_result_entry=show_evaluation,
                placement_points=get_competition_placement_points(selected_competition),
            )
        )
        values = {
            (row["team_id"], row["discipline_id"], row["value_index"]): row["value"]
            for row in result_rows
        }
        values_input = {
            key: "" if value is None else value
            for key, value in values.items()
        }
        totals_by_team_discipline_display = {
            key: format_points_value(value)
            for key, value in totals_by_team_discipline.items()
        }
        overall_totals_display = {
            team_id: format_points_value(value)
            for team_id, value in overall_totals.items()
        }
        return templates.TemplateResponse(
            request=request, name="ergebnisse.html",
            context={
                "is_sixkampf": True,
                "competitions": competitions,
                "selected_competition_id": selected_competition_id,
                "selected_competition": selected_competition,
                "disciplines": disciplines,
                "selected_discipline": selected_discipline,
                "show_evaluation": show_evaluation,
                "teams": teams,
                "values": values,
                "values_input": values_input,
                "totals_by_team_discipline": totals_by_team_discipline,
                "totals_by_team_discipline_display": totals_by_team_discipline_display,
                "overall_totals": overall_totals,
                "overall_totals_display": overall_totals_display,
                "ranking": ranking,
                "change_counts": change_counts,
                "saved_team_id": saved_team_id_value,
                "saved_at": saved_at_value,
                "slots": [],
                "archived_slots": [],
            }
        )

    slots = get_all_slots(selected_competition_id)
    active_slots = [
        slot for slot in slots
        if slot["slot_typ"] == "Spiel" and slot["status"] in ("geplant", "läuft")
    ]
    archived_slots = [
        slot for slot in slots
        if slot["slot_typ"] == "Spiel" and slot["status"] == "beendet"
    ]
    return templates.TemplateResponse(
        request=request, name="ergebnisse.html",
        context={
            "is_sixkampf": False,
            "slots": active_slots,
            "archived_slots": list(reversed(archived_slots))[:20],
            "competitions": competitions,
            "selected_competition_id": selected_competition_id,
        }
    )

@app.post("/competition/{competition_id}/discipline/{discipline_id}/team/{team_id}/results")
async def sixkampf_team_results_save(
    request: Request, competition_id: int, discipline_id: int, team_id: int
):
    form = await request.form()
    with get_conn() as conn:
        competition = conn.execute(
            "SELECT * FROM competitions WHERE id = ?", (competition_id,)
        ).fetchone()

        discipline = conn.execute("""
            SELECT * FROM competition_disciplines
            WHERE id = ? AND competition_id = ?
        """, (discipline_id, competition_id)).fetchone()
        team = conn.execute(
            "SELECT * FROM teams WHERE id = ? AND active = 1", (team_id,)
        ).fetchone()
        if (
            competition is None or competition["competition_type"] != "Sechskampf"
            or discipline is None or team is None
            or team["jahrgang"] != competition["jahrgang"]
        ):
            return RedirectResponse("/ergebnisse", status_code=303)

        parsed_values = []
        try:
            for value_index in range(1, discipline["values_per_team"] + 1):
                raw_value = str(form.get(f"value_{value_index}", "")).strip()
                value = None if raw_value == "" else float(raw_value.replace(",", "."))
                if value is not None and not isfinite(value):
                    raise ValueError
                parsed_values.append((value_index, value))
        except ValueError:
            return RedirectResponse(
                f"/ergebnisse?competition_id={competition_id}&discipline_id={discipline_id}",
                status_code=303
            )

        existing_values = {
            row["value_index"]: row["value"]
            for row in conn.execute(
                """
                SELECT value_index, value
                FROM sixkampf_team_results
                WHERE competition_id = ? AND discipline_id = ? AND team_id = ?
                """,
                (competition_id, discipline_id, team_id),
            ).fetchall()
        }
        new_values = {
            value_index: value
            for value_index, value in parsed_values
            if value is not None
        }
        conn.execute("""
            DELETE FROM sixkampf_team_results
            WHERE competition_id = ? AND discipline_id = ? AND team_id = ?
        """, (competition_id, discipline_id, team_id))
        for value_index, value in parsed_values:
            if value is not None:
                conn.execute("""
                    INSERT INTO sixkampf_team_results (
                        competition_id, discipline_id, team_id, value_index, value
                    ) VALUES (?, ?, ?, ?, ?)
                """, (
                    competition_id, discipline_id, team_id, value_index, value,
                ))
        if existing_values != new_values:
            if not existing_values:
                action = "Stationswerte erfasst"
            elif not new_values:
                action = "Stationswerte gelöscht"
            else:
                action = "Stationswerte geändert"
            record_change(
                conn,
                request=request,
                action=action,
                entity_type="sixkampf_result",
                entity_id=team_id,
                competition_id=competition_id,
                discipline_id=discipline_id,
                team_id=team_id,
                old_value=format_sixkampf_values(existing_values),
                new_value=format_sixkampf_values(new_values),
            )
        conn.commit()

    saved_at = app_now_display_time()
    return RedirectResponse(
        f"/ergebnisse?competition_id={competition_id}"
        f"&discipline_id={discipline_id}"
        f"&saved_team_id={team_id}&saved_at={saved_at}",
        status_code=303
    )

@app.post("/slot/{slot_id}/start")
def start_slot(slot_id: int, request: Request, started_at: Optional[str] = Form(None)):
    with get_conn() as conn:
        started_at_value = started_at or app_now_db_timestamp()
        conn.execute(
            "UPDATE slots SET status = 'läuft', started_at = ?, finished_at = NULL WHERE id = ?",
            (started_at_value, slot_id),
        )
        conn.commit()

    referer = request.headers.get("referer") or "/ergebnisse"
    return RedirectResponse(referer, status_code=303)


@app.post("/slot/{slot_id}/finish")
def finish_slot(slot_id: int):
    with get_conn() as conn:
        finished_at = app_now_db_timestamp()
        conn.execute(
            "UPDATE slots SET status = 'beendet', finished_at = ? WHERE id = ?",
            (finished_at, slot_id),
        )
        conn.commit()

    return RedirectResponse("/spielplan", status_code=303)


@app.post("/slot/{slot_id}/unstart")
def unstart_slot(slot_id: int, request: Request):
    with get_conn() as conn:
        conn.execute("""
            UPDATE slots
            SET status = 'geplant', started_at = NULL, finished_at = NULL
            WHERE id = ?
              AND status = 'läuft'
        """, (slot_id,))
        conn.commit()

    referer = request.headers.get("referer") or "/ergebnisse"
    return RedirectResponse(referer, status_code=303)


@app.post("/slot/{slot_id}/save")
def save_slot(
    slot_id: int,
    request: Request,
    score_a: int = Form(...),
    score_b: int = Form(...),
    finish: str = Form("0")
):
    score_a = max(0, score_a)
    score_b = max(0, score_b)

    new_status = "beendet" if finish == "1" else "läuft"

    with get_conn() as conn:
        slot = conn.execute(
            """
            SELECT competition_id, status, started_at, score_a, score_b
            FROM slots
            WHERE id = ?
            """,
            (slot_id,)
        ).fetchone()
        started_at_value = slot["started_at"] if slot else None
        if new_status == "läuft" and not started_at_value:
            started_at_value = app_now_db_timestamp()
        if new_status == "geplant":
            started_at_value = None

        conn.execute("""
            UPDATE slots
            SET score_a = ?,
                score_b = ?,
                status = ?,
                started_at = ?
            WHERE id = ?
        """, (
            score_a,
            score_b,
            new_status,
            started_at_value,
            slot_id
        ))
        if slot is not None and (
            slot["score_a"] != score_a or slot["score_b"] != score_b
        ):
            action = (
                "Ergebnis erfasst"
                if slot["score_a"] is None and slot["score_b"] is None
                else "Ergebnis geändert"
            )
            record_change(
                conn,
                request=request,
                action=action,
                entity_type="slot_result",
                entity_id=slot_id,
                competition_id=slot["competition_id"],
                old_value=format_score(slot["score_a"], slot["score_b"]),
                new_value=format_score(score_a, score_b),
            )
        conn.commit()

    return RedirectResponse("/ergebnisse", status_code=303)


@app.post("/slot/{slot_id}/reactivate")
def reactivate_slot(slot_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE slots SET status = 'läuft' WHERE id = ?", (slot_id,))
        conn.commit()

    return RedirectResponse("/ergebnisse", status_code=303)


@app.post("/slot/{slot_id}/clear-result")
def clear_slot_result(slot_id: int, request: Request):
    with get_conn() as conn:
        slot = conn.execute(
            """
            SELECT competition_id, score_a, score_b
            FROM slots
            WHERE id = ?
            """,
            (slot_id,),
        ).fetchone()
        conn.execute("""
            UPDATE slots
            SET score_a = NULL,
                score_b = NULL,
                status = 'geplant',
                started_at = NULL
            WHERE id = ?
        """, (slot_id,))
        if slot is not None and (
            slot["score_a"] is not None or slot["score_b"] is not None
        ):
            record_change(
                conn,
                request=request,
                action="Ergebnis gelöscht",
                entity_type="slot_result",
                entity_id=slot_id,
                competition_id=slot["competition_id"],
                old_value=format_score(slot["score_a"], slot["score_b"]),
                new_value="–",
            )
        conn.commit()

    return RedirectResponse("/ergebnisse", status_code=303)


@app.get("/tabellen")
def tabellen(
    request: Request,
    event_id: str = "",
    jahrgang: str = "",
    competition_id: str = "",
):
    view_data = collect_tabellen_view_data(event_id, jahrgang, competition_id)

    return templates.TemplateResponse(
        request=request,
        name="tabellen.html",
        context={
            "tables": view_data["tables"],
            "competitions": view_data["competitions"],
            "events_by_id": view_data["events_by_id"],
            "year_options": view_data["year_options"],
            "selected_event_id": view_data["selected_event_id"],
            "selected_jahrgang": view_data["selected_jahrgang"],
            "selected_competition_id": view_data["selected_competition_id"],
        }
    )


@app.get("/tabellen/csv")
def tabellen_csv_export(
    event_id: str = "",
    jahrgang: str = "",
    competition_id: str = "",
):
    view_data = collect_tabellen_view_data(event_id, jahrgang, competition_id)
    csv_content = build_tabellen_csv_content(view_data)
    filename = build_tabellen_csv_filename(view_data)

    return Response(
        content=csv_content.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


app.include_router(create_competitions_router(
    app_now_db_timestamp=app_now_db_timestamp,
    get_competition_timing=get_competition_timing,
    get_unique_competition_name=get_unique_competition_name,
    copy_competition_disciplines=copy_competition_disciplines,
    normalize_competition_location=normalize_competition_location,
    parse_placement_points_config=parse_placement_points_config,
    competition_locations=COMPETITION_LOCATIONS,
    default_game_duration_minutes=DEFAULT_GAME_DURATION_MINUTES,
    default_changeover_duration_minutes=DEFAULT_CHANGEOVER_DURATION_MINUTES,
))


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
        context={
            "event": None,
            "event_types": EVENT_TYPES,
            "form_action": "/events/create",
        }
    )


@app.post("/events/create")
def event_create(
    name: str = Form(...), description: str = Form(""),
    event_date: str = Form(""), status: str = Form("geplant"),
    event_type: str = Form(...),
):
    if (
        not name.strip()
        or status not in {"geplant", "läuft", "beendet", "archiviert"}
        or event_type not in EVENT_TYPES_SET
    ):
        return RedirectResponse("/events", status_code=303)
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM events WHERE name = ?", (name.strip(),)).fetchone():
            return RedirectResponse("/events", status_code=303)
        conn.execute("""
            INSERT INTO events (name, description, event_date, status, event_type)
            VALUES (?, ?, ?, ?, ?)
        """, (
            name.strip(), description.strip() or None,
            event_date or None, status, event_type,
        ))
        conn.commit()
    return RedirectResponse("/events", status_code=303)


@app.get("/events/{event_id}/edit")
def event_edit(request: Request, event_id: int):
    saved_at = request.query_params.get("saved_at", "").strip()
    try:
        saved_at_value = datetime.strptime(saved_at, "%H:%M").strftime("%H:%M") if saved_at else ""
    except ValueError:
        saved_at_value = ""
    with get_conn() as conn:
        event = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    if event is None:
        return RedirectResponse("/events", status_code=303)
    return templates.TemplateResponse(
        request=request, name="event_form.html",
        context={
            "event": event,
            "event_types": EVENT_TYPES,
            "form_action": f"/events/{event_id}/update",
            "saved_at": saved_at_value,
        }
    )


@app.post("/events/{event_id}/update")
def event_update(
    event_id: int, name: str = Form(...), description: str = Form(""),
    event_date: str = Form(""), status: str = Form("geplant"),
    event_type: str = Form(...),
):
    if (
        not name.strip()
        or status not in {"geplant", "läuft", "beendet", "archiviert"}
        or event_type not in EVENT_TYPES_SET
    ):
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
            SET name = ?, description = ?, event_date = ?, status = ?, event_type = ?
            WHERE id = ?
        """, (
            name.strip(), description.strip() or None,
            event_date or None, status, event_type, event_id,
        ))
        conn.commit()
    saved_at = app_now_db_timestamp()
    return RedirectResponse(f"/events/{event_id}/edit?saved_at={saved_at}", status_code=303)



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
            INSERT INTO events (name, description, event_date, status, event_type)
            VALUES (?, ?, ?, 'geplant', ?)
        """, (
            get_unique_event_name(conn, event["name"]),
            event["description"], None, event["event_type"],
        ))
        new_event_id = cursor.lastrowid
        competitions = conn.execute(
            "SELECT * FROM competitions WHERE event_id = ? ORDER BY id",
            (event_id,)
        ).fetchall()
        for competition in competitions:
            timing = get_competition_timing(competition)
            competition_cursor = conn.execute("""
                INSERT INTO competitions (
                    name, sportart, jahrgang, status, points_win, points_draw,
                    points_loss, points_first_place, placement_points,
                    event_id, competition_type,
                    game_duration_minutes, changeover_duration_minutes,
                    start_time, end_time, location
                ) VALUES (?, ?, ?, 'geplant', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                get_unique_competition_name(conn, competition["name"]),
                competition["sportart"], competition["jahrgang"],
                competition["points_win"], competition["points_draw"],
                competition["points_loss"], competition["points_first_place"],
                competition["placement_points"], new_event_id,
                competition["competition_type"],
                timing["game_duration_minutes"], timing["changeover_duration_minutes"],
                competition["start_time"],
                competition["end_time"], competition["location"],
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
    if event is None:
        return RedirectResponse("/events", status_code=303)

    schedule = get_day_schedule_for_event(event_id)
    overall_ranking = calculate_event_overall_ranking(event_id)
    return templates.TemplateResponse(
        request=request, name="event_detail.html",
        context={
            "event": event,
            "overall_ranking": overall_ranking,
            "schedule": schedule,
        }
    )

@app.get("/beamer")
def beamer(request: Request):
    data = fetch_beamer_data()
    data["refresh_seconds"] = get_beamer_refresh_seconds()
    return templates.TemplateResponse(request=request, name="beamer.html", context=data)

