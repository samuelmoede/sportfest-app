from typing import Callable

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.database import get_conn
from app.routes.competitions import COMPETITION_TYPES
from app.routes.events import EVENT_TYPES
from app.routes.quickstart import build_quickstart_context
from app.routes.teams import normalize_jahrgang
from app.services.event_status_service import fetch_events_with_competition_counts
from app.services.schedule_generator_service import (
    DEFAULT_SCHULPOKAL_MODE,
    SCHULPOKAL_MODES,
)
from app.services.schedule_time_service import (
    DEFAULT_CHANGEOVER_DURATION_MINUTES,
    DEFAULT_GAME_DURATION_MINUTES,
)
from app.web import templates

DEFAULT_WIZARD_EVENT_STATUS = "geplant"
DEFAULT_WIZARD_EVENT_TYPE = "Einzelturnier"


def _unique_competition_name(conn, base_name: str) -> str:
    candidate = base_name
    suffix = 2
    while conn.execute(
        "SELECT 1 FROM competitions WHERE name = ?", (candidate,)
    ).fetchone():
        candidate = f"{base_name} ({suffix})"
        suffix += 1
    return candidate


def _jahrgang_sort_key(value):
    return (0, value) if isinstance(value, int) else (1, str(value))


def create_router(
    *,
    app_now_display_time: Callable[[], str],
) -> APIRouter:
    router = APIRouter()

    @router.get("/assistent")
    def assistent_start(request: Request):
        error = request.query_params.get("error", "").strip()
        with get_conn() as conn:
            events = fetch_events_with_competition_counts(conn, include_archived=False)
        context = {
            "events": events,
            "event_types": EVENT_TYPES,
            "default_event_type": DEFAULT_WIZARD_EVENT_TYPE,
            "error": error,
        }
        context.update(build_quickstart_context(app_now_display_time))
        return templates.TemplateResponse(
            request=request,
            name="assistent_start.html",
            context=context,
        )

    @router.post("/assistent/veranstaltung/anlegen")
    def assistent_create_event(
        name: str = Form(...),
        event_type: str = Form(DEFAULT_WIZARD_EVENT_TYPE),
        event_date: str = Form(""),
    ):
        name_value = name.strip()
        event_type_value = event_type if event_type in EVENT_TYPES else DEFAULT_WIZARD_EVENT_TYPE
        if not name_value:
            return RedirectResponse("/assistent?error=invalid", status_code=303)

        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM events WHERE name = ?", (name_value,)).fetchone():
                return RedirectResponse("/assistent?error=duplicate", status_code=303)
            cursor = conn.execute(
                """
                INSERT INTO events (name, description, details, event_date, status, event_type)
                VALUES (?, '', '', ?, ?, ?)
                """,
                (name_value, event_date.strip() or None, DEFAULT_WIZARD_EVENT_STATUS, event_type_value),
            )
            event_id = cursor.lastrowid
            conn.commit()

        return RedirectResponse(f"/assistent/{event_id}", status_code=303)

    @router.get("/assistent/{event_id}")
    def assistent_event(request: Request, event_id: int):
        error = request.query_params.get("error", "").strip()
        with get_conn() as conn:
            event = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
            if event is None:
                return RedirectResponse("/assistent", status_code=303)

            teams = conn.execute(
                "SELECT * FROM teams WHERE active = 1 ORDER BY jahrgang, name"
            ).fetchall()
            courts = conn.execute(
                "SELECT * FROM courts WHERE active = 1 ORDER BY name"
            ).fetchall()
            competitions = conn.execute(
                """
                SELECT c.*,
                    (SELECT COUNT(*) FROM slots s WHERE s.competition_id = c.id) AS slot_count,
                    (SELECT COUNT(*) FROM competition_disciplines cd WHERE cd.competition_id = c.id) AS discipline_count
                FROM competitions c
                WHERE c.event_id = ?
                ORDER BY c.jahrgang, c.name
                """,
                (event_id,),
            ).fetchall()

        teams_by_jahrgang = {}
        for team in teams:
            teams_by_jahrgang.setdefault(team["jahrgang"], []).append(dict(team))
        jahrgang_options = sorted(teams_by_jahrgang.keys(), key=_jahrgang_sort_key)

        return templates.TemplateResponse(
            request=request,
            name="assistent_event.html",
            context={
                "event": event,
                "error": error,
                "has_teams": bool(teams),
                "has_courts": bool(courts),
                "teams_by_jahrgang": teams_by_jahrgang,
                "jahrgang_options": jahrgang_options,
                "competitions": [dict(c) for c in competitions],
                "competition_types": COMPETITION_TYPES,
                "schulpokal_modes": SCHULPOKAL_MODES,
                "default_schulpokal_mode": DEFAULT_SCHULPOKAL_MODE,
            },
        )

    @router.post("/assistent/{event_id}/wettbewerb/anlegen")
    def assistent_create_competition(
        event_id: int,
        name: str = Form(""),
        sportart: str = Form(...),
        jahrgang: str = Form(...),
        competition_type: str = Form("Turnier"),
        tournament_mode: str = Form(""),
    ):
        sportart_value = sportart.strip()
        jahrgang_value = normalize_jahrgang(jahrgang)

        if (
            not sportart_value
            or jahrgang_value is None
            or competition_type not in COMPETITION_TYPES
        ):
            return RedirectResponse(f"/assistent/{event_id}?error=invalid", status_code=303)

        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            event = conn.execute("SELECT id FROM events WHERE id = ?", (event_id,)).fetchone()
            if event is None:
                return RedirectResponse("/assistent", status_code=303)

            team_count = conn.execute(
                "SELECT COUNT(*) AS n FROM teams WHERE active = 1 AND jahrgang = ?",
                (jahrgang_value,),
            ).fetchone()["n"]
            if team_count < 2:
                return RedirectResponse(f"/assistent/{event_id}?error=team_count", status_code=303)

            tournament_mode_value = None
            if competition_type == "Schulpokal":
                tournament_mode_value = (
                    tournament_mode if tournament_mode in SCHULPOKAL_MODES else DEFAULT_SCHULPOKAL_MODE
                )

            base_name = name.strip() or f"{sportart_value} Jahrgang {jahrgang_value}"
            competition_name = _unique_competition_name(conn, base_name)

            conn.execute(
                """
                INSERT INTO competitions (
                    name, sportart, jahrgang, status, points_win, points_draw,
                    points_loss, points_first_place, event_id, competition_type,
                    tournament_mode, game_duration_minutes, changeover_duration_minutes
                ) VALUES (?, ?, ?, 'geplant', 3, 1, 0, ?, ?, ?, ?, ?, ?)
                """,
                (
                    competition_name, sportart_value, jahrgang_value, team_count,
                    event_id, competition_type, tournament_mode_value,
                    DEFAULT_GAME_DURATION_MINUTES, DEFAULT_CHANGEOVER_DURATION_MINUTES,
                ),
            )
            conn.commit()

        return RedirectResponse(f"/assistent/{event_id}", status_code=303)

    return router
