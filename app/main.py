from collections import Counter
import csv
from datetime import date, datetime
import io
import logging
from math import isfinite
import os
import re
import secrets
import time
from typing import Optional
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from app.database import init_db, get_conn, DB_PATH
from app.routes.settings import router as settings_router
from app.routes.competitions import create_router as create_competitions_router
from app.routes.events import create_router as create_events_router
from app.routes.schedule import create_router as create_schedule_router
from app.routes.teams import create_router as create_teams_router
from app.routes.venues import create_router as create_venues_router
from app.services.schedule_grid_service import get_all_slots
from app.services.schedule_location_service import (
    COMPETITION_LOCATIONS,
    normalize_competition_location,
)
from app.services.event_status_service import (
    get_dashboard_event,
    get_upcoming_events,
    normalize_event_statuses,
)
from app.services.results_filter_service import (
    build_results_filter_state,
    build_results_redirect_url,
    parse_filter_id,
    sort_result_teams,
)
from app.services.ranking_points_service import assign_points_by_placement_groups
from app.services.change_log_service import insert_change_log_entry
from app.services.schedule_generator_service import (
    KO_DECISIVE_PHASES,
    get_already_played_ko_targets,
    get_next_phase_names,
    get_winner_loser,
    group_phase_finished,
    is_next_phase_started,
    semifinals_finished,
)
from app.services.sixkampf_service import (
    calculate_sixkampf_station_rotation,
    calculate_sixkampf_team_ranking,
)
from app.services.ui_view_service import (
    enrich_beamer_view,
    enrich_day_schedule_view,
)
from app.services.schedule_time_service import (
    DEFAULT_CHANGEOVER_DURATION_MINUTES,
    DEFAULT_GAME_DURATION_MINUTES,
    format_time_range,
    get_competition_timing,
    parse_slot_time,
)
from app.services.settings_service import (
    ROLE_LABELS,
    can_access_role,
    get_admin_password,
    get_beamer_refresh_seconds,
    get_current_role,
    get_current_role_description,
    get_current_role_label,
    get_dashboard_info_html,
    get_referee_password,
    is_logged_in,
    is_login_prepared,
    is_security_enabled,
)
from app.web import APP_DIR, templates, get_style_version
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
    ("/roadmap", "admin"),
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
    (
        re.compile(r"^/competition/[^/]+/(?:generate-semifinals|generate-finals)$"),
        "referee",
    ),
    (re.compile(r"^/team(?:/create|/[^/]+/(?:update|delete))$"), "admin"),
    (re.compile(r"^/court(?:/create|/[^/]+/(?:update|delete))$"), "admin"),
    (re.compile(r"^/competition/create$"), "admin"),
    (
        re.compile(
            r"^/competition/[^/]+/(?:duplicate|update|discipline/create|archive|restore|reset|delete|delete-planned-slots)$"
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

STYLE_IMPORT_RE = re.compile(r'url\("(css/[^"]+\.css)"\)')


# Muss vor dem /static-Mount registriert werden, sonst faengt der Mount die
# Anfrage zuerst ab. Versioniert jeden @import einzeln, damit z.B. der
# Cloudflare-Tunnel nach CSS-Aenderungen nicht einzelne Teildateien veraltet
# aus dem Edge-Cache ausliefert.
@app.get("/static/style.css")
def style_css():
    version = get_style_version()
    content = (APP_DIR / "static" / "style.css").read_text(encoding="utf-8")
    versioned = STYLE_IMPORT_RE.sub(lambda m: f'url("{m.group(1)}?v={version}")', content)
    return Response(content=versioned, media_type="text/css")


app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
def app_now():
    return datetime.now(APP_TIMEZONE)


def app_now_display_time():
    return app_now().strftime("%H:%M")


def app_now_db_timestamp():
    return app_now().strftime("%Y-%m-%d %H:%M:%S")


def slot_starts_soon(startzeit, now, threshold_minutes=7):
    """True if startzeit (HH:MM) lies within the next threshold_minutes from now."""
    try:
        hour, minute = (int(part) for part in startzeit.split(":")[:2])
    except (AttributeError, ValueError):
        return False
    slot_minutes = hour * 60 + minute
    now_minutes = now.hour * 60 + now.minute
    return 0 <= (slot_minutes - now_minutes) <= threshold_minutes




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
    insert_change_log_entry(
        conn,
        request=request,
        created_at=app_now_db_timestamp(),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        competition_id=competition_id,
        old_value=old_value,
        new_value=new_value,
        discipline_id=discipline_id,
        team_id=team_id,
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
            disciplines = []
            if competition["competition_type"] == "Sechskampf":
                teams = get_teams_for_competition(conn, competition["id"], competition["jahrgang"])
                teams = include_teams_with_existing_sixkampf_results(conn, competition["id"], teams)
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
                    require_result_entry=True,
                    placement_points=get_competition_placement_points(competition),
                )
            else:
                rows = calculate_table(competition["id"])
                for index, row in enumerate(rows, start=1):
                    row.setdefault("placement", index)
                rows = apply_ko_bracket_placements(rows, competition["id"])
            tables.append({"competition": competition, "rows": rows, "disciplines": disciplines})

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
            disciplines = table.get("disciplines", [])
            header = ["Klasse"]
            header.extend([discipline["name"] for discipline in disciplines])
            header.extend(["Summe", "Platz", "Wertungspunkte"])
            writer.writerow(header)
            for row in rows:
                row_values = [row["team"]["name"]]
                for discipline in disciplines:
                    row_values.append(
                        row["discipline_points_display"].get(discipline["id"]) or "-"
                    )
                row_values.extend([
                    f"{row['intermediate_total_display']} / {row['max_intermediate_total_display']}",
                    row["placement"],
                    row["scoring_points_display"],
                ])
                writer.writerow(row_values)
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
        # Handle patterns like "10a", "11b", "12/1", "13" as Oberstufe
        for grade in range(10, 14):
            if normalized.startswith(str(grade)):
                return "Oberstufe"
        return None  # Lehrer, Mixed, Finalrunde, Eltern, etc. → no column

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


def build_day_schedule(competitions, event_date=None, now=None, event_status=None):
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

    completed = event_status == "archiviert"

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
            "SELECT event_date, status FROM events WHERE id = ?", (event_id,)
        ).fetchone()
        competitions = [dict(row) for row in conn.execute("""
            SELECT * FROM competitions
            WHERE event_id = ?
              AND status != 'archiviert'
            ORDER BY start_time, end_time, jahrgang, name
        """, (event_id,)).fetchall()]
    schedule = build_day_schedule(
        competitions,
        event["event_date"] if event is not None else None,
        event_status=event["status"] if event is not None else None,
    )
    return enrich_day_schedule_view(
        schedule,
        competitions,
        event_id,
        now=app_now(),
    )


def get_unique_competition_name(conn, original_name: str):
    base_name = f"{original_name} (Kopie)"
    candidate = base_name
    copy_number = 2
    while conn.execute("SELECT 1 FROM competitions WHERE name = ?", (candidate,)).fetchone():
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


@app.on_event("startup")
def startup():
    init_db()
    with get_conn() as conn:
        normalize_event_statuses(conn)
        conn.commit()


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

        now = app_now()
        upcoming = [dict(row) for row in conn.execute("""
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
        """).fetchall()]
        for slot in upcoming:
            slot["is_soon"] = slot_starts_soon(slot.get("startzeit"), now)

        ended_count = conn.execute("""
            SELECT COUNT(*) AS n
            FROM slots
            JOIN competitions c ON c.id = slots.competition_id
            WHERE slots.status = 'beendet'
              AND c.status != 'archiviert'
        """).fetchone()["n"]

        today = now.date()
        next_event = get_dashboard_event(conn, today)
        additional_upcoming_events = get_upcoming_events(
            conn,
            today,
            limit=4,
            exclude_event_id=next_event["id"] if next_event else None,
        )

    return {
        "competitions": competitions,
        "teams": teams,
        "courts": courts,
        "running": running,
        "upcoming": upcoming,
        "ended_count": ended_count,
        "next_event": next_event,
        "schedule_event": next_event,
        "additional_upcoming_events": additional_upcoming_events,
        "dashboard_info_html": get_dashboard_info_html(),
    }


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


TIE_BREAK_LABELS = {
    "tordifferenz": "Platzierung durch Tordifferenz entschieden",
    "tore": "Platzierung durch Anzahl geschossener Tore entschieden",
    "mini_pkt": "Platzierung durch Punkte im direkten Vergleich der punktgleichen Teams entschieden",
    "mini_diff": "Platzierung durch Tordifferenz im direkten Vergleich der punktgleichen Teams entschieden",
    "mini_tore": "Platzierung durch Tore im direkten Vergleich der punktgleichen Teams entschieden",
    "unentschieden": "Punktgleichstand ohne eindeutige Entscheidung – Reihenfolge alphabetisch",
    "zyklus": "Zyklischer Gleichstand (z.B. A schlägt B, B schlägt C, C schlägt A) – keine eindeutige Auflösung möglich, Reihenfolge alphabetisch",
}


def _mini_league_stats(team_names, slots, competition):
    """FIFA-Style Mini-Tabelle: Punkte, Tordifferenz und Tore ausschliesslich aus
    den Spielen zwischen den uebergebenen (bereits komplett gleichauf liegenden)
    Teams - nicht aus der Gesamttabelle."""
    team_set = set(team_names)
    stats = {team: {"pkt": 0.0, "diff": 0, "plus": 0} for team in team_names}
    for slot in slots:
        team_a, team_b = slot["team_a"], slot["team_b"]
        if team_a not in team_set or team_b not in team_set or team_a == team_b:
            continue
        score_a, score_b = slot["score_a"], slot["score_b"]
        if score_a is None or score_b is None:
            continue
        stats[team_a]["plus"] += score_a
        stats[team_b]["plus"] += score_b
        stats[team_a]["diff"] += score_a - score_b
        stats[team_b]["diff"] += score_b - score_a
        if score_a > score_b:
            stats[team_a]["pkt"] += competition["points_win"]
            stats[team_b]["pkt"] += competition["points_loss"]
        elif score_b > score_a:
            stats[team_b]["pkt"] += competition["points_win"]
            stats[team_a]["pkt"] += competition["points_loss"]
        else:
            stats[team_a]["pkt"] += competition["points_draw"]
            stats[team_b]["pkt"] += competition["points_draw"]
    return stats


def _resolve_tied_group(team_names, slots, competition):
    """Loest eine Gruppe von Teams auf, die in der Gesamttabelle komplett gleichauf
    liegen (Punkte, Tordifferenz, Tore), per Mini-Tabelle nur aus den Duellen
    untereinander - im Unterschied zu einem reinen Nachbar-Vergleich werden dabei
    alle Teams der Gruppe gemeinsam betrachtet. Bleiben Teams auch danach gleichauf
    (z.B. ein echter Zyklus A schlaegt B, B schlaegt C, C schlaegt A - bei 3
    punktgleichen Teams im Rundenspiel ohne Unentschieden rechnerisch zwangslaeufig),
    werden sie explizit als unaufloesbar markiert statt eine der Reihenfolgen als
    vermeintlich "entschieden" auszugeben. Gibt die neue Teamreihenfolge sowie einen
    tie_note-Grund je Team zurueck."""
    if len(team_names) < 2:
        return list(team_names), {}

    mini = _mini_league_stats(team_names, slots, competition)
    ordered = sorted(
        team_names,
        key=lambda t: (-mini[t]["pkt"], -mini[t]["diff"], -mini[t]["plus"], t.lower()),
    )

    tie_notes = {}
    i = 0
    while i < len(ordered):
        j = i
        while (
            j + 1 < len(ordered)
            and mini[ordered[j + 1]]["pkt"] == mini[ordered[i]]["pkt"]
            and mini[ordered[j + 1]]["diff"] == mini[ordered[i]]["diff"]
            and mini[ordered[j + 1]]["plus"] == mini[ordered[i]]["plus"]
        ):
            j += 1

        if j > i:
            reason = "zyklus" if (j - i + 1) >= 3 else "unentschieden"
            label = TIE_BREAK_LABELS[reason]
            for team in ordered[i:j + 1]:
                tie_notes[team] = label
        elif i > 0:
            previous = ordered[i - 1]
            if mini[ordered[i]]["pkt"] != mini[previous]["pkt"]:
                reason = "mini_pkt"
            elif mini[ordered[i]]["diff"] != mini[previous]["diff"]:
                reason = "mini_diff"
            else:
                reason = "mini_tore"
            label = TIE_BREAK_LABELS[reason]
            tie_notes[previous] = label
            tie_notes[ordered[i]] = label

        i = j + 1

    return ordered, tie_notes


def sort_table_rows(rows, slots, competition):
    rows.sort(key=lambda r: (-r["pkt"], -r["diff"], -r["plus"], r["team"].lower()))

    for row in rows:
        row["tie_note"] = None

    i = 0
    while i < len(rows):
        j = i
        while (
            j + 1 < len(rows)
            and rows[j + 1]["pkt"] == rows[i]["pkt"]
            and rows[j + 1]["diff"] == rows[i]["diff"]
            and rows[j + 1]["plus"] == rows[i]["plus"]
        ):
            j += 1

        if j > i:
            group_names = [row["team"] for row in rows[i:j + 1]]
            ordered_names, tie_notes = _resolve_tied_group(group_names, slots, competition)
            rows_by_team = {row["team"]: row for row in rows[i:j + 1]}
            rows[i:j + 1] = [rows_by_team[name] for name in ordered_names]
            for row in rows[i:j + 1]:
                row["tie_note"] = tie_notes.get(row["team"])

        i = j + 1

    # Punktgleichstand, den schon die Gesamttabelle ueber Tordifferenz oder Tore
    # entscheidet (kein Duell noetig) - nur ergaenzen, wo noch kein Grund feststeht.
    for i in range(len(rows) - 1):
        if rows[i]["pkt"] != rows[i + 1]["pkt"]:
            continue
        if rows[i]["diff"] == rows[i + 1]["diff"] and rows[i]["plus"] == rows[i + 1]["plus"]:
            continue
        reason = "tordifferenz" if rows[i]["diff"] != rows[i + 1]["diff"] else "tore"
        label = TIE_BREAK_LABELS[reason]
        if rows[i]["tie_note"] is None:
            rows[i]["tie_note"] = label
        if rows[i + 1]["tie_note"] is None:
            rows[i + 1]["tie_note"] = label

    for row in rows:
        if float(row["pkt"]).is_integer():
            row["pkt_display"] = str(int(row["pkt"]))
        else:
            row["pkt_display"] = str(row["pkt"]).replace(".", ",")

    return rows


def get_teams_for_competition(conn, competition_id: int, jahrgang):
    """Fetch teams: explicit competition_teams assignment first, then jahrgang fallback."""
    explicit = conn.execute("""
        SELECT t.* FROM teams t
        JOIN competition_teams ct ON ct.team_id = t.id
        WHERE ct.competition_id = ?
        ORDER BY t.jahrgang, t.name
    """, (competition_id,)).fetchall()
    if explicit:
        return list(explicit)
    if jahrgang is None:
        return []
    return conn.execute("""
        SELECT * FROM teams
        WHERE active = 1 AND jahrgang = ?
        ORDER BY name
    """, (jahrgang,)).fetchall()


def include_teams_with_existing_sixkampf_results(conn, competition_id: int, teams):
    """Ergaenzt eine Team-Liste (z.B. aus get_teams_for_competition, das inaktive
    Teams ausschliesst) um Teams, fuer die in diesem Wettbewerb bereits
    Sechskampf-Ergebnisse gespeichert sind - unabhaengig vom aktuellen
    active-Status. Ohne das wuerde ein nachtraeglich deaktiviertes Team (z.B. bei
    einer Rueckwaerts-Bereinigung der Klassenliste nach der Veranstaltung)
    spurlos aus Tabelle und Gesamtwertung verschwinden, obwohl die Ergebnisse
    weiterhin in der Datenbank stehen - anders als bei Turnieren, deren Tabelle
    sich direkt aus den gespielten Spielen speist und daher vom active-Status
    unabhaengig ist."""
    known_ids = {team["id"] for team in teams}
    missing_ids = [
        row["team_id"]
        for row in conn.execute(
            "SELECT DISTINCT team_id FROM sixkampf_team_results WHERE competition_id = ?",
            (competition_id,),
        ).fetchall()
        if row["team_id"] not in known_ids
    ]
    if not missing_ids:
        return teams
    placeholders = ", ".join("?" for _ in missing_ids)
    extra_teams = conn.execute(
        f"SELECT * FROM teams WHERE id IN ({placeholders})", missing_ids
    ).fetchall()
    return list(teams) + list(extra_teams)


def calculate_table(competition_id: int):
    with get_conn() as conn:
        competition = conn.execute(
            "SELECT * FROM competitions WHERE id = ?",
            (competition_id,)
        ).fetchone()

        if competition is None:
            return []

        teams = get_teams_for_competition(conn, competition_id, competition["jahrgang"])

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


def _ko_slot_complete(slot):
    return (
        slot is not None
        and slot["status"] == "beendet"
        and slot["team_a_id"] is not None
        and slot["team_b_id"] is not None
        and slot["score_a"] is not None
        and slot["score_b"] is not None
    )


def get_ko_phase_slots(competition_id: int):
    with get_conn() as conn:
        finale = conn.execute("""
            SELECT * FROM slots
            WHERE competition_id = ? AND slot_typ = 'Spiel' AND phase = 'Finale'
            ORDER BY startzeit, court_id, id
        """, (competition_id,)).fetchall()

        platz3 = conn.execute("""
            SELECT * FROM slots
            WHERE competition_id = ? AND slot_typ = 'Spiel'
              AND phase IN ('Spiel um Platz 3', 'Kleines Finale', 'Platzierung')
            ORDER BY startzeit, court_id, id
        """, (competition_id,)).fetchall()

        halbfinale = conn.execute("""
            SELECT * FROM slots
            WHERE competition_id = ? AND slot_typ = 'Spiel' AND phase = 'Halbfinale'
            ORDER BY startzeit, court_id, id
        """, (competition_id,)).fetchall()

    return finale, platz3, halbfinale


def apply_ko_bracket_placements(rows, competition_id: int):
    """Überschreibt row["placement"] für Finalisten/Halbfinalisten anhand des
    K.-o.-Baums (Sieger Finale = Platz 1 usw.), sobald Finale (und ggf. Spiel
    um Platz 3) beendet sind. Alle übrigen Teams behalten ihre bisherige,
    punktebasierte Reihenfolge bei, nur um die belegten Podiumsplätze verschoben.
    Solange die K.-o.-Phase nicht vollständig entschieden ist, bleiben die
    übergebenen rows/placements unverändert."""
    finale_slots, platz3_slots, halbfinale_slots = get_ko_phase_slots(competition_id)

    if not finale_slots or not _ko_slot_complete(finale_slots[0]):
        return rows

    winner_final, loser_final = get_winner_loser(finale_slots[0])
    if winner_final is None:
        return rows

    podium = {winner_final: 1, loser_final: 2}
    next_free_place = 3

    if platz3_slots:
        if not _ko_slot_complete(platz3_slots[0]):
            return rows
        winner_p3, loser_p3 = get_winner_loser(platz3_slots[0])
        if winner_p3 is not None:
            podium[winner_p3] = 3
            podium[loser_p3] = 4
            next_free_place = 5
    else:
        semifinal_losers = [
            get_winner_loser(slot)[1]
            for slot in halbfinale_slots
            if _ko_slot_complete(slot)
        ]
        semifinal_losers = [team_id for team_id in semifinal_losers if team_id not in podium]
        for team_id in semifinal_losers:
            podium[team_id] = 3
        if semifinal_losers:
            next_free_place = 3 + len(semifinal_losers)

    remaining_rows = [row for row in rows if row["team_id"] not in podium]

    for row in rows:
        if row["team_id"] in podium:
            row["placement"] = podium[row["team_id"]]

    for offset, row in enumerate(remaining_rows):
        row["placement"] = next_free_place + offset

    rows.sort(key=lambda row: row["placement"])
    return rows


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
        previous = row
        previous_place = place

    rows = apply_ko_bracket_placements(rows, competition["id"])

    assign_points_by_placement_groups(
        rows,
        placement_points,
        placement_key="placement",
        points_key="competition_points",
    )
    for row in rows:
        row["competition_points_display"] = format_points_value(row["competition_points"])

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

    # Fetch explicit team assignments so cross-group competitions use the right teams
    with get_conn() as conn:
        ct_rows = conn.execute("""
            SELECT ct.competition_id, t.id, t.name, t.jahrgang
            FROM competition_teams ct
            JOIN teams t ON t.id = ct.team_id
            ORDER BY ct.competition_id, t.jahrgang, t.name
        """).fetchall()
    explicit_teams_by_competition = {}
    for row in ct_rows:
        explicit_teams_by_competition.setdefault(row["competition_id"], []).append(dict(row))

    overall = {}
    for competition in competitions:
        comp_teams_list = (
            explicit_teams_by_competition.get(competition["id"])
            or teams_by_jahrgang.get(competition["jahrgang"], [])
        )
        for team in comp_teams_list:
            record = overall.setdefault(team["id"], {
                "team_id": team["id"],
                "team": team["name"],
                "jahrgang": team["jahrgang"],
                "points_by_competition": {},
                "total_points": 0.0,
                "placements": [],
                "tournament_score_diff": 0,
            })
            record["points_by_competition"].setdefault(
                competition["id"],
                None,
            )

    for competition in competitions:
        competition_id = competition["id"]
        placement_points = get_competition_placement_points(competition)
        if competition["competition_type"] == "Sechskampf":
            competition_teams = (
                explicit_teams_by_competition.get(competition_id)
                or teams_by_jahrgang.get(competition["jahrgang"], [])
            )
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
                competition_teams = include_teams_with_existing_sixkampf_results(
                    conn, competition_id, competition_teams
                )

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
                    "placements": [],
                    "tournament_score_diff": 0,
                })
                record["points_by_competition"][competition_id] = row["scoring_points"]
                record["placements"].append(row["placement"])
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
                    "placements": [],
                    "tournament_score_diff": 0,
                })
                record["points_by_competition"][competition_id] = row.get("competition_points", 0)
                record["tournament_score_diff"] += row.get("diff", 0)
                record["placements"].append(row["placement"])

    for record in overall.values():
        record["total_points"] = sum(
            value
            for value in record["points_by_competition"].values()
            if isinstance(value, (int, float))
        )
        record["total_points_display"] = format_points_value(record["total_points"])
        record["placement_counts"] = Counter(record["placements"])
        record["placement_bilanz_display"] = (
            ", ".join(str(place) for place in sorted(record["placements"]))
            if record["placements"] else "–"
        )
        record["tournament_score_diff_display"] = (
            f"{record['tournament_score_diff']:+d}"
        )
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

        # Platzierungsbilanz: Anzahl 1. Plaetze vergleichen, bei Gleichstand
        # Anzahl 2. Plaetze usw. Erst wenn auch das komplett gleich ist,
        # entscheidet die aufsummierte Tor-/Punktedifferenz.
        max_place = max(
            (max(row["placements"], default=0) for row in rows),
            default=0,
        )

        def placement_bilanz_key(row):
            counts = row["placement_counts"]
            return tuple(-counts.get(place, 0) for place in range(1, max_place + 1))

        rows.sort(key=lambda row: (
            -row["total_points"],
            placement_bilanz_key(row),
            -row["tournament_score_diff"],
            row["team"].lower(),
        ))

        place = 0
        previous_rank_key = None
        for index, row in enumerate(rows, start=1):
            rank_key = (
                row["total_points"],
                placement_bilanz_key(row),
                row["tournament_score_diff"],
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


def _fetch_group_decided_slots(competition_id: int, gruppe: str):
    with get_conn() as conn:
        return conn.execute("""
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


def _format_group_patt_message(team_a, team_b, scores, max_score):
    draw_scores = {(value, value) for value in range(max_score + 1)}
    if draw_scores.issubset(set(scores)):
        return (
            f"Bei einem Unentschieden zwischen {team_a} und {team_b} steht kein eindeutiger "
            "Gruppensieger fest (Punkte, Tordifferenz und Tore wären dann komplett gleich)."
        )
    examples = ", ".join(f"{a}:{b}" for a, b in scores[:6])
    suffix = " …" if len(scores) > 6 else ""
    return (
        f"Bei bestimmten Ergebnissen (z. B. {examples}{suffix}) zwischen {team_a} und {team_b} "
        "steht kein eindeutiger Gruppensieger fest (Punkte, Tordifferenz und Tore komplett gleich)."
    )


def find_group_patt_risk(
    competition_id: int,
    gruppe: str,
    pending_team_a: str,
    pending_team_b: str,
    qualifying_count: int = 2,
    max_score: int = 12,
):
    """Prueft fuer das noch offene, letzte Gruppenspiel, ob es Ergebnisse gibt, die
    an der Qualifikationsgrenze (Platz `qualifying_count`/`qualifying_count`+1) zu
    einem komplett unaufloesbaren Gleichstand fuehren wuerden (Punkte, Tordifferenz,
    Tore und Direktvergleich alle gleich). Gibt es solche Ergebnisse, kann der
    Schiedsrichter waehrend des Spiels reagieren (z. B. Verlaengerung, Neunmeter)."""
    with get_conn() as conn:
        competition = conn.execute(
            "SELECT * FROM competitions WHERE id = ?", (competition_id,)
        ).fetchone()
    if competition is None:
        return None

    base_table = calculate_group_table(competition_id, gruppe)
    if len(base_table) < qualifying_count + 1:
        return None

    decided_slots = _fetch_group_decided_slots(competition_id, gruppe)
    base_by_team = {row["team"]: row for row in base_table}
    empty_base = {"team_id": None, "pkt": 0.0, "diff": 0, "plus": 0}
    base_a = base_by_team.get(pending_team_a, empty_base)
    base_b = base_by_team.get(pending_team_b, empty_base)

    def _project(team_name, base_row, own_score, opp_score):
        pkt = base_row["pkt"]
        if own_score > opp_score:
            pkt += competition["points_win"]
        elif own_score < opp_score:
            pkt += competition["points_loss"]
        else:
            pkt += competition["points_draw"]
        return {
            "team": team_name,
            "team_id": base_row.get("team_id"),
            "pkt": pkt,
            "diff": base_row["diff"] + (own_score - opp_score),
            "plus": base_row["plus"] + own_score,
        }

    risky_scores = []
    for score_a in range(max_score + 1):
        for score_b in range(max_score + 1):
            rows = [
                dict(row) for row in base_table
                if row["team"] not in (pending_team_a, pending_team_b)
            ]
            hypothetical_slot = {
                "team_a": pending_team_a, "team_b": pending_team_b,
                "score_a": score_a, "score_b": score_b,
            }
            all_slots = list(decided_slots) + [hypothetical_slot]
            rows.append(_project(pending_team_a, base_a, score_a, score_b))
            rows.append(_project(pending_team_b, base_b, score_b, score_a))

            sorted_rows = sort_table_rows(rows, all_slots, competition)

            # Jede Rang-Grenze bis zur Qualifikationsgrenze pruefen (nicht nur den
            # Cutoff selbst): ein unaufloesbarer Gleichstand um Platz 1 bedeutet
            # "kein eindeutiger Gruppensieger" (relevant fuer die KO-Auslosung),
            # auch wenn beide Teams ohnehin qualifiziert waeren.
            for boundary_index in range(qualifying_count):
                lower_boundary = sorted_rows[boundary_index]
                upper_boundary = sorted_rows[boundary_index + 1]
                involves_pending_match = pending_team_a in (
                    lower_boundary["team"], upper_boundary["team"]
                ) or pending_team_b in (lower_boundary["team"], upper_boundary["team"])
                if not involves_pending_match:
                    continue
                unresolved_labels = (TIE_BREAK_LABELS["unentschieden"], TIE_BREAK_LABELS["zyklus"])
                if (
                    lower_boundary["tie_note"] in unresolved_labels
                    and lower_boundary["tie_note"] == upper_boundary["tie_note"]
                ):
                    risky_scores.append((score_a, score_b))
                    break

    if not risky_scores:
        return None

    return {
        "team_a": pending_team_a,
        "team_b": pending_team_b,
        "scores": risky_scores,
        "message": _format_group_patt_message(pending_team_a, pending_team_b, risky_scores, max_score),
    }


def fetch_beamer_data():
    with get_conn() as conn:
        active_comps = conn.execute("""
            SELECT id FROM competitions
            WHERE status = 'läuft' AND competition_type = 'Turnier'
        """).fetchall()
        courts = conn.execute("SELECT * FROM courts WHERE active = 1 ORDER BY name").fetchall()

    active_comp_ids = {row["id"] for row in active_comps}

    if not active_comp_ids:
        return enrich_beamer_view({
            "running_slots": [],
            "recent_results": [],
            "court_summaries": [{"court": dict(c), "open_count": 0, "next": None} for c in courts],
        }, [])

    all_slots = get_all_slots()
    slots = [s for s in all_slots if s.get("competition_id") in active_comp_ids]

    running_slots = [
        s for s in slots if s["slot_typ"] == "Spiel" and s["status"] == "läuft"
    ]

    recent_results = sorted(
        [s for s in slots
         if s["status"] == "beendet" and s["slot_typ"] == "Spiel"
         and s.get("team_a_id") is not None and s.get("team_b_id") is not None],
        key=lambda s: (s.get("startzeit", ""), s.get("id", 0)),
        reverse=True,
    )[:6]

    court_summaries = []
    for court in courts:
        court_slots = [s for s in slots if s["court_id"] == court["id"] and s["slot_typ"] == "Spiel"]
        open_count = len([s for s in court_slots if s["status"] != "beendet"])
        next_games = [s for s in court_slots if s["status"] == "geplant"][:3]
        court_summaries.append({"court": court, "open_count": open_count, "next_games": next_games})

    return enrich_beamer_view({
        "running_slots": running_slots,
        "recent_results": recent_results,
        "court_summaries": court_summaries,
    }, slots)

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


LOGIN_ATTEMPT_LIMIT = 5
LOGIN_ATTEMPT_WINDOW_SECONDS = 600
LOGIN_LOCKOUT_SECONDS = 300

# Prozess-lokaler Speicher reicht hier: die App laeuft als Einzelprozess
# (siehe README/Dockerfile), ein Neustart loescht die Sperren mit.
_login_failures: dict[str, list[float]] = {}
_login_lockouts: dict[str, float] = {}


def _login_client_key(request: Request) -> str:
    # Hinter dem Cloudflare-Tunnel ist request.client.host die Tunnel-IP;
    # CF-Connecting-IP traegt die tatsaechliche Besucher-IP.
    forwarded = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _login_lockout_remaining(key: str) -> Optional[float]:
    locked_until = _login_lockouts.get(key)
    if locked_until is None:
        return None
    remaining = locked_until - time.monotonic()
    if remaining <= 0:
        _login_lockouts.pop(key, None)
        return None
    return remaining


def _register_login_failure(key: str) -> None:
    now = time.monotonic()
    attempts = [t for t in _login_failures.get(key, []) if now - t < LOGIN_ATTEMPT_WINDOW_SECONDS]
    attempts.append(now)
    if len(attempts) >= LOGIN_ATTEMPT_LIMIT:
        _login_lockouts[key] = now + LOGIN_LOCKOUT_SECONDS
        attempts = []
    _login_failures[key] = attempts


def _register_login_success(key: str) -> None:
    _login_failures.pop(key, None)
    _login_lockouts.pop(key, None)


@app.post("/login")
def login(
    request: Request,
    password: str = Form(""),
    next: str = Form("/"),
    target_role: str = Form("admin"),
):
    client_key = _login_client_key(request)
    lockout_remaining = _login_lockout_remaining(client_key)
    if lockout_remaining is not None:
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
                    "Zu viele Fehlversuche. Bitte in "
                    f"{max(1, int(lockout_remaining // 60) + 1)} Minute(n) erneut versuchen."
                ),
            },
            status_code=429,
        )

    role_passwords = {
        "referee": get_referee_password(),
        "admin": get_admin_password(),
    }
    configured_password = role_passwords.get(target_role, "")
    if configured_password and secrets.compare_digest(password, configured_password):
        _register_login_success(client_key)
        request.session.clear()
        request.session["admin_logged_in"] = target_role == "admin"
        request.session["role"] = target_role
        return RedirectResponse(safe_next_url(next), status_code=303)

    if configured_password:
        _register_login_failure(client_key)

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
    template_name = (
        "dashboard_modern.html"
        if request.query_params.get("ui_theme") == "modern"
        else "dashboard.html"
    )
    return templates.TemplateResponse(request=request, name=template_name, context=data)


@app.get("/ergebnisse")
def ergebnisse(
    request: Request,
    event_id: str = "",
    competition_id: str = "",
    court_id: str = "",
    discipline_id: str = "",
    saved_team_id: str = "",
    saved_at: str = "",
    phase_ready: str = "",
    phase_ready_competition_id: str = "",
    overwrite_warning: str = "",
    correction_blocked: str = "",
):
    filter_state = build_results_filter_state(
        get_active_competitions(),
        event_id_value=event_id,
        competition_id_value=competition_id,
        event_filter_present="event_id" in request.query_params,
        today=app_now().date(),
    )
    competitions = filter_state["competitions"]
    event_options = filter_state["event_options"]
    selected_event_id = filter_state["selected_event_id"]
    selected_competition_id = filter_state["selected_competition_id"]
    selected_competition = filter_state["selected_competition"]

    with get_conn() as conn:
        courts = conn.execute(
            "SELECT * FROM courts WHERE active = 1 ORDER BY name"
        ).fetchall()
    selected_court_id = parse_filter_id(court_id)
    if selected_court_id not in {court["id"] for court in courts}:
        selected_court_id = None

    phase_ready_prompt = None
    phase_ready_dismiss_url = None
    parsed_phase_ready_competition_id = parse_filter_id(phase_ready_competition_id)
    if (
        phase_ready in ("Halbfinale", "Finale")
        and parsed_phase_ready_competition_id is not None
        and parsed_phase_ready_competition_id == selected_competition_id
    ):
        phase_ready_prompt = {
            "phase": phase_ready,
            "action": (
                f"/competition/{parsed_phase_ready_competition_id}/generate-semifinals"
                if phase_ready == "Halbfinale"
                else f"/competition/{parsed_phase_ready_competition_id}/generate-finals"
            ),
        }
        current_url = request.url.path
        if request.url.query:
            current_url += f"?{request.url.query}"
        phase_ready_dismiss_url = build_results_redirect_url(current_url)

    overwrite_warning_prompt = None
    overwrite_warning_dismiss_url = None
    if overwrite_warning in ("Halbfinale", "Finale") and selected_competition_id is not None:
        already_played = get_already_played_ko_targets(selected_competition_id, overwrite_warning)
        if already_played:
            overwrite_warning_prompt = {
                "phase": overwrite_warning,
                "results": [
                    f"{slot['phase']}: {slot['team_a'] or '?'} {slot['score_a']}:{slot['score_b']} {slot['team_b'] or '?'}"
                    for slot in already_played
                ],
                "action": (
                    f"/competition/{selected_competition_id}/generate-semifinals"
                    if overwrite_warning == "Halbfinale"
                    else f"/competition/{selected_competition_id}/generate-finals"
                ),
            }
            current_url = request.url.path
            if request.url.query:
                current_url += f"?{request.url.query}"
            overwrite_warning_dismiss_url = build_results_redirect_url(current_url)

    if (
        selected_competition is not None
        and selected_competition["competition_type"] == "Sechskampf"
    ):
        with get_conn() as conn:
            teams = sort_result_teams(get_teams_for_competition(
                conn, selected_competition_id, selected_competition["jahrgang"]
            ))
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
                "event_options": event_options,
                "selected_event_id": selected_event_id,
                "selected_competition_id": selected_competition_id,
                "selected_competition": selected_competition,
                "courts": courts,
                "selected_court_id": selected_court_id,
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
                "phase_ready_prompt": None,
                "phase_ready_dismiss_url": None,
                "overwrite_warning_prompt": None,
                "overwrite_warning_dismiss_url": None,
                "correction_blocked": False,
            }
        )

    slots = get_all_slots(selected_competition_id)
    if selected_competition_id is None and selected_event_id is not None:
        visible_competition_ids = filter_state["visible_competition_ids"]
        slots = [
            slot for slot in slots
            if slot["competition_id"] in visible_competition_ids
        ]
    if selected_court_id is not None:
        slots = [slot for slot in slots if slot["court_id"] == selected_court_id]
    active_slots = sorted(
        (
            slot for slot in slots
            if slot["slot_typ"] == "Spiel" and slot["status"] in ("geplant", "läuft")
        ),
        key=lambda slot: (slot["startzeit"], slot["id"]),
    )
    archived_slots = [
        slot for slot in slots
        if slot["slot_typ"] == "Spiel" and slot["status"] == "beendet"
    ]
    archived_slots = list(reversed(archived_slots))[:20]

    group_pending_counts = {}
    for slot in slots:
        if slot["slot_typ"] == "Spiel" and slot["phase"] == "Gruppenphase" and slot["gruppe"]:
            if slot["status"] != "beendet":
                key = (slot["competition_id"], slot["gruppe"])
                group_pending_counts[key] = group_pending_counts.get(key, 0) + 1

    for slot in active_slots:
        slot["group_patt_risk"] = None
        if (
            slot["slot_typ"] == "Spiel"
            and slot["phase"] == "Gruppenphase"
            and slot["gruppe"]
            and slot["team_a"]
            and slot["team_b"]
            and group_pending_counts.get((slot["competition_id"], slot["gruppe"])) == 1
        ):
            slot["group_patt_risk"] = find_group_patt_risk(
                slot["competition_id"], slot["gruppe"], slot["team_a"], slot["team_b"]
            )

    for slot in active_slots + archived_slots:
        slot["is_draw_unresolved"] = (
            slot["phase"] in KO_DECISIVE_PHASES
            and slot["score_a"] is not None
            and slot["score_a"] == slot["score_b"]
        )
    for slot in archived_slots:
        slot["correction_locked"] = is_next_phase_started(
            slot["competition_id"], slot["phase"]
        )

    return templates.TemplateResponse(
        request=request, name="ergebnisse.html",
        context={
            "is_sixkampf": False,
            "slots": active_slots,
            "archived_slots": archived_slots,
            "competitions": competitions,
            "event_options": event_options,
            "selected_event_id": selected_event_id,
            "selected_competition_id": selected_competition_id,
            "courts": courts,
            "selected_court_id": selected_court_id,
            "phase_ready_prompt": phase_ready_prompt,
            "phase_ready_dismiss_url": phase_ready_dismiss_url,
            "overwrite_warning_prompt": overwrite_warning_prompt,
            "overwrite_warning_dismiss_url": overwrite_warning_dismiss_url,
            "correction_blocked": correction_blocked == "1",
        }
    )

@app.post("/competition/{competition_id}/discipline/{discipline_id}/team/{team_id}/results")
async def sixkampf_team_results_save(
    request: Request, competition_id: int, discipline_id: int, team_id: int
):
    form = await request.form()
    results_return_to = str(form.get("results_return_to", ""))
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
            return RedirectResponse(build_results_redirect_url(results_return_to), status_code=303)

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
                build_results_redirect_url(
                    results_return_to,
                    competition_id=competition_id,
                    discipline_id=discipline_id,
                ),
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
        build_results_redirect_url(
            results_return_to,
            competition_id=competition_id,
            discipline_id=discipline_id,
            saved_team_id=team_id,
            saved_at=saved_at,
        ),
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
    finish: str = Form("0"),
    results_return_to: str = Form(""),
):
    score_a = max(0, score_a)
    score_b = max(0, score_b)

    new_status = "beendet" if finish == "1" else "läuft"

    correction_blocked = False
    with get_conn() as conn:
        slot = conn.execute(
            """
            SELECT competition_id, status, started_at, score_a, score_b, phase, slot_typ
            FROM slots
            WHERE id = ?
            """,
            (slot_id,)
        ).fetchone()

        is_correction = (
            slot is not None
            and slot["status"] == "beendet"
            and (slot["score_a"] != score_a or slot["score_b"] != score_b)
        )
        if is_correction and is_next_phase_started(slot["competition_id"], slot["phase"]):
            # Die nächste Phase (Halbfinale/Finale) hat bereits begonnen -
            # ein Nachtragen der davon abhängigen Spiele ist am Turniertag aus
            # Zeitgründen nicht vorgesehen, daher bleibt das Ergebnis gesperrt.
            correction_blocked = True

        if not correction_blocked:
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

            if is_correction:
                # Eine Korrektur eines bereits entschiedenen Gruppen-/Halbfinalspiels
                # kann die Besetzung einer bereits ausgelosten, aber noch nicht
                # gestarteten Folgephase ungueltig machen (z.B. anderer Gruppensieger).
                # is_next_phase_started() hat oben nur "laeuft"/"beendet" gesperrt -
                # eine lediglich ausgeloste (noch 'geplant') Folgephase wuerde sonst
                # unbemerkt mit den alten, jetzt falschen Teams stehen bleiben.
                next_phases = get_next_phase_names(slot["phase"])
                if next_phases:
                    placeholders = ", ".join("?" for _ in next_phases)
                    stale_slots = conn.execute(f"""
                        SELECT id FROM slots
                        WHERE competition_id = ? AND slot_typ = 'Spiel'
                          AND phase IN ({placeholders})
                          AND status = 'geplant'
                          AND (team_a_id IS NOT NULL OR team_b_id IS NOT NULL)
                    """, (slot["competition_id"], *next_phases)).fetchall()
                    if stale_slots:
                        stale_ids = [row["id"] for row in stale_slots]
                        id_placeholders = ", ".join("?" for _ in stale_ids)
                        conn.execute(f"""
                            UPDATE slots
                            SET team_a_id = NULL, team_b_id = NULL,
                                score_a = NULL, score_b = NULL,
                                status = 'geplant', started_at = NULL, finished_at = NULL
                            WHERE id IN ({id_placeholders})
                        """, stale_ids)
                        for stale_id in stale_ids:
                            record_change(
                                conn,
                                request=request,
                                action="Auslosung zurückgesetzt (Korrektur einer vorherigen Phase)",
                                entity_type="slot_result",
                                entity_id=stale_id,
                                competition_id=slot["competition_id"],
                                old_value=None,
                                new_value=None,
                            )

            conn.commit()

    # Nur wenn genau dieses Spiel die Phase gerade komplettiert (nicht bei einer
    # nachträglichen Korrektur eines längst beendeten Spiels), bieten wir an,
    # die nächste Phase zu besetzen - unabhängig davon, wann das Spiel gestartet
    # wurde, sondern rein danach, ob alle anderen Spiele der Phase bereits
    # gespeichert sind.
    phase_ready = None
    just_completed = (
        slot is not None
        and new_status == "beendet"
        and slot["status"] != "beendet"
        and slot["slot_typ"] == "Spiel"
    )
    if just_completed:
        if slot["phase"] == "Gruppenphase" and group_phase_finished(slot["competition_id"]):
            phase_ready = "Halbfinale"
        elif slot["phase"] == "Halbfinale" and semifinals_finished(slot["competition_id"]):
            phase_ready = "Finale"

    redirect_updates = {}
    if not results_return_to and slot is not None:
        redirect_updates["competition_id"] = slot["competition_id"]
    if phase_ready:
        redirect_updates["phase_ready"] = phase_ready
        redirect_updates["phase_ready_competition_id"] = slot["competition_id"]
    if correction_blocked:
        redirect_updates["correction_blocked"] = "1"
    return RedirectResponse(
        build_results_redirect_url(results_return_to, **redirect_updates),
        status_code=303,
    )


@app.post("/slot/{slot_id}/reactivate")
def reactivate_slot(slot_id: int, results_return_to: str = Form("")):
    correction_blocked = False
    with get_conn() as conn:
        slot = conn.execute(
            "SELECT competition_id, phase FROM slots WHERE id = ?",
            (slot_id,)
        ).fetchone()
        if slot is not None and is_next_phase_started(slot["competition_id"], slot["phase"]):
            correction_blocked = True
        else:
            conn.execute("UPDATE slots SET status = 'läuft' WHERE id = ?", (slot_id,))
            conn.commit()

    redirect_updates = {}
    if correction_blocked:
        redirect_updates["correction_blocked"] = "1"
    return RedirectResponse(
        build_results_redirect_url(results_return_to, **redirect_updates),
        status_code=303,
    )


@app.post("/slot/{slot_id}/clear-result")
def clear_slot_result(
    slot_id: int,
    request: Request,
    results_return_to: str = Form(""),
):
    correction_blocked = False
    with get_conn() as conn:
        slot = conn.execute(
            """
            SELECT competition_id, score_a, score_b, phase
            FROM slots
            WHERE id = ?
            """,
            (slot_id,),
        ).fetchone()

        if slot is not None and is_next_phase_started(slot["competition_id"], slot["phase"]):
            correction_blocked = True
        else:
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

    redirect_updates = {}
    if not results_return_to and slot is not None:
        redirect_updates["competition_id"] = slot["competition_id"]
    if correction_blocked:
        redirect_updates["correction_blocked"] = "1"
    return RedirectResponse(
        build_results_redirect_url(results_return_to, **redirect_updates),
        status_code=303,
    )


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

app.include_router(create_schedule_router(
    app_now_db_timestamp=app_now_db_timestamp,
    app_today=lambda: app_now().date(),
    parse_competition_id=parse_competition_id,
    parse_jahrgang_filter=parse_jahrgang_filter,
    get_active_competitions=get_active_competitions,
    calculate_group_table=calculate_group_table,
    get_teams_for_competition=get_teams_for_competition,
    calculate_sixkampf_station_rotation=calculate_sixkampf_station_rotation,
))
app.include_router(create_competitions_router(
    app_now_db_timestamp=app_now_db_timestamp,
    app_today=lambda: app_now().date(),
    get_competition_timing=get_competition_timing,
    get_unique_competition_name=get_unique_competition_name,
    copy_competition_disciplines=copy_competition_disciplines,
    normalize_competition_location=normalize_competition_location,
    parse_placement_points_config=parse_placement_points_config,
    competition_locations=COMPETITION_LOCATIONS,
    default_game_duration_minutes=DEFAULT_GAME_DURATION_MINUTES,
    default_changeover_duration_minutes=DEFAULT_CHANGEOVER_DURATION_MINUTES,
))


app.include_router(create_events_router(
    app_now_db_timestamp=app_now_db_timestamp,
    get_day_schedule_for_event=get_day_schedule_for_event,
    calculate_event_overall_ranking=calculate_event_overall_ranking,
    get_competition_timing=get_competition_timing,
    get_unique_competition_name=get_unique_competition_name,
    copy_competition_disciplines=copy_competition_disciplines,
))


@app.get("/beamer")
def beamer(request: Request):
    data = fetch_beamer_data()
    data["refresh_seconds"] = get_beamer_refresh_seconds()
    template_name = (
        "beamer_modern.html"
        if request.query_params.get("ui_theme") == "modern"
        else "beamer.html"
    )
    return templates.TemplateResponse(request=request, name=template_name, context=data)

