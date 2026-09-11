from typing import Callable, List

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.database import get_conn
from app.services.schedule_generator_service import generate_group_plan
from app.services.schedule_location_service import (
    DEFAULT_COMPETITION_LOCATION,
    filter_courts_for_location,
)
from app.services.schedule_time_service import (
    DEFAULT_CHANGEOVER_DURATION_MINUTES,
    DEFAULT_GAME_DURATION_MINUTES,
)
from app.web import templates

DEFAULT_QUICKSTART_SPORTART = "Sportfest"


def _unique_competition_name(conn, base_name: str) -> str:
    candidate = base_name
    suffix = 2
    while conn.execute(
        "SELECT 1 FROM competitions WHERE name = ?", (candidate,)
    ).fetchone():
        candidate = f"{base_name} ({suffix})"
        suffix += 1
    return candidate


def build_quickstart_context(app_now_display_time: Callable[[], str]) -> dict:
    """Formulardaten fuer den Turnier-Schnellstart - wird sowohl von der
    eigenstaendigen /turnier-schnellstart-Route als auch eingebettet auf der
    /assistent-Startseite verwendet (siehe app/routes/wizard.py)."""
    with get_conn() as conn:
        teams = conn.execute(
            "SELECT * FROM teams WHERE active = 1 ORDER BY jahrgang, name"
        ).fetchall()
        all_courts = conn.execute(
            "SELECT * FROM courts WHERE active = 1 ORDER BY name"
        ).fetchall()

    courts = filter_courts_for_location(all_courts, DEFAULT_COMPETITION_LOCATION)

    teams_by_jahrgang = {}
    for team in teams:
        teams_by_jahrgang.setdefault(team["jahrgang"], []).append(dict(team))

    return {
        "teams_by_jahrgang": teams_by_jahrgang,
        "courts": courts,
        "location": DEFAULT_COMPETITION_LOCATION,
        "default_start_time": app_now_display_time(),
        "default_sportart": DEFAULT_QUICKSTART_SPORTART,
    }


def create_router(
    *,
    app_now_display_time: Callable[[], str],
) -> APIRouter:
    router = APIRouter()

    @router.get("/turnier-schnellstart")
    def turnier_schnellstart(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="turnier_schnellstart.html",
            context=build_quickstart_context(app_now_display_time),
        )

    @router.post("/turnier-schnellstart")
    def turnier_schnellstart_create(
        name: str = Form(""),
        sportart: str = Form(DEFAULT_QUICKSTART_SPORTART),
        team_ids: List[int] = Form(default=[]),
        court_ids: List[int] = Form(default=[]),
        startzeit: str = Form(...),
    ):
        team_ids = list(dict.fromkeys(team_ids))
        court_ids = list(dict.fromkeys(court_ids))
        name_value = name.strip()
        sportart_value = sportart.strip() or DEFAULT_QUICKSTART_SPORTART

        if len(team_ids) < 2 or not court_ids:
            return RedirectResponse("/turnier-schnellstart", status_code=303)

        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing_team_ids = {
                row["id"] for row in conn.execute(
                    "SELECT id FROM teams WHERE id IN ({}) AND active = 1".format(
                        ",".join("?" for _ in team_ids)
                    ),
                    team_ids,
                ).fetchall()
            }
            team_ids = [team_id for team_id in team_ids if team_id in existing_team_ids]
            if len(team_ids) < 2:
                return RedirectResponse("/turnier-schnellstart", status_code=303)

            competition_name = _unique_competition_name(
                conn, name_value or f"Schnellturnier {startzeit}"
            )
            cursor = conn.execute("""
                INSERT INTO competitions (
                    name, sportart, jahrgang, status, points_win, points_draw,
                    points_loss, points_first_place, event_id, competition_type,
                    game_duration_minutes, changeover_duration_minutes, start_time
                ) VALUES (?, ?, 'mixed', 'geplant', 3, 1, 0, ?, NULL, 'Turnier', ?, ?, ?)
            """, (
                competition_name, sportart_value, len(team_ids),
                DEFAULT_GAME_DURATION_MINUTES, DEFAULT_CHANGEOVER_DURATION_MINUTES,
                startzeit,
            ))
            competition_id = cursor.lastrowid

            for team_id in team_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO competition_teams (competition_id, team_id) VALUES (?, ?)",
                    (competition_id, team_id),
                )
            conn.commit()

        # games_per_team = Teamzahl (nicht Teamzahl - 1): _generate_balanced_pairings
        # rotiert bei ungerader Teamzahl mit Freilos ueber Teamzahl Runden, nicht
        # Teamzahl - 1 - siehe _jeder_gegen_jeden_pairings fuer dieselbe Logik.
        proposed_slots = generate_group_plan(
            competition_id=competition_id,
            court_ids=court_ids,
            startzeit=startzeit,
            games_per_team=len(team_ids),
            include_ko=True,
        )

        if proposed_slots:
            with get_conn() as conn:
                for slot in proposed_slots:
                    conn.execute("""
                        INSERT INTO slots (
                            competition_id, court_id, startzeit, slot_typ, phase, gruppe,
                            team_a_id, team_b_id, status, note
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'geplant', ?)
                    """, (
                        slot["competition_id"],
                        slot["court_id"],
                        slot["startzeit"],
                        slot["slot_typ"],
                        slot["phase"],
                        slot["gruppe"] or None,
                        slot["team_a_id"] or None,
                        slot["team_b_id"] or None,
                        slot["note"] or None,
                    ))
                conn.commit()

        return RedirectResponse(
            f"/spielplan-bearbeiten?competition_id={competition_id}",
            status_code=303,
        )

    return router
