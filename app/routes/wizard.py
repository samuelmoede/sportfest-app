from typing import Callable, List

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.database import get_conn
from app.routes.competitions import COMPETITION_TYPES
from app.routes.events import EVENT_TYPES
from app.routes.quickstart import build_quickstart_context
from app.routes.teams import normalize_jahrgang
from app.services.event_status_service import fetch_events_with_competition_counts
from app.utils.formatting import jahrgang_sort_key
from app.services.schedule_generator_service import (
    DEFAULT_SCHULPOKAL_MODE,
    SCHULPOKAL_MODES,
)
from app.services.schedule_location_service import (
    COMPETITION_LOCATIONS,
    DEFAULT_COMPETITION_LOCATION,
    normalize_competition_location,
)
from app.services.schedule_time_service import (
    DEFAULT_CHANGEOVER_DURATION_MINUTES,
    DEFAULT_GAME_DURATION_MINUTES,
)
from app.web import templates

DISCIPLINE_SCORING_DIRECTIONS = ("higher", "lower")

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


def create_router(
    *,
    app_now_display_time: Callable[[], str],
) -> APIRouter:
    router = APIRouter()

    @router.get("/assistent")
    def assistent_start(request: Request):
        error = request.query_params.get("error", "").strip()
        qs_error = request.query_params.get("qs_error", "").strip()
        with get_conn() as conn:
            events = fetch_events_with_competition_counts(conn, include_archived=False)
        context = {
            "events": events,
            "event_types": EVENT_TYPES,
            "default_event_type": DEFAULT_WIZARD_EVENT_TYPE,
            "error": error,
            "qs_error": qs_error,
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

    @router.post("/assistent/team/anlegen")
    def assistent_create_team_quickstart(
        name: str = Form(...),
        jahrgang: str = Form(...),
    ):
        """Spontanes Anlegen einer Klasse aus dem Schnellstart-Bereich der
        Assistent-Startseite (Issue #107), noch ohne gewaehlte Veranstaltung -
        nutzt dieselbe Insert-Logik wie assistent_create_team unten."""
        name_value = name.strip()
        jahrgang_value = normalize_jahrgang(jahrgang)
        if not name_value or jahrgang_value is None:
            return RedirectResponse("/assistent?qs_error=team_invalid", status_code=303)

        with get_conn() as conn:
            conn.execute(
                "INSERT INTO teams (name, jahrgang, active) VALUES (?, ?, 1)",
                (name_value, jahrgang_value),
            )
            conn.commit()

        return RedirectResponse("/assistent", status_code=303)

    @router.post("/assistent/spielfeld/anlegen")
    def assistent_create_court_quickstart(
        name: str = Form(...),
        sportart: str = Form(""),
        location: str = Form(DEFAULT_COMPETITION_LOCATION),
    ):
        """Spontanes Anlegen eines Spielfelds aus dem Schnellstart-Bereich der
        Assistent-Startseite (Issue #107) - nutzt dieselbe Insert-Logik wie
        /court/create (siehe app/routes/venues.py)."""
        name_value = name.strip()
        if not name_value:
            return RedirectResponse("/assistent?qs_error=court_invalid", status_code=303)
        location_value = normalize_competition_location(location) or DEFAULT_COMPETITION_LOCATION

        with get_conn() as conn:
            conn.execute(
                "INSERT INTO courts (name, sportart, location, active) VALUES (?, ?, ?, 1)",
                (name_value, sportart.strip() or None, location_value),
            )
            conn.commit()

        return RedirectResponse("/assistent", status_code=303)

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
            disciplines = conn.execute(
                """
                SELECT cd.*
                FROM competition_disciplines cd
                JOIN competitions c ON c.id = cd.competition_id
                WHERE c.event_id = ?
                ORDER BY cd.competition_id, cd.sort_order, cd.id
                """,
                (event_id,),
            ).fetchall()

        teams_by_jahrgang = {}
        for team in teams:
            teams_by_jahrgang.setdefault(team["jahrgang"], []).append(dict(team))
        jahrgang_options = sorted(teams_by_jahrgang.keys(), key=jahrgang_sort_key)

        disciplines_by_competition = {}
        for discipline in disciplines:
            disciplines_by_competition.setdefault(
                discipline["competition_id"], []
            ).append(dict(discipline))

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
                "disciplines_by_competition": disciplines_by_competition,
                "court_locations": COMPETITION_LOCATIONS,
                "default_court_location": DEFAULT_COMPETITION_LOCATION,
            },
        )

    @router.post("/assistent/{event_id}/team/anlegen")
    def assistent_create_team(
        event_id: int,
        name: str = Form(...),
        jahrgang: str = Form(...),
    ):
        """Spontanes Anlegen einer einzelnen Klasse/Teams direkt im Assistenten
        (Issue #85), ohne die Seite zu verlassen - nutzt dieselbe Insert-Logik
        wie /team/create (siehe app/routes/teams.py)."""
        name_value = name.strip()
        jahrgang_value = normalize_jahrgang(jahrgang)
        if not name_value or jahrgang_value is None:
            return RedirectResponse(f"/assistent/{event_id}?error=team_invalid", status_code=303)

        with get_conn() as conn:
            event = conn.execute("SELECT id FROM events WHERE id = ?", (event_id,)).fetchone()
            if event is None:
                return RedirectResponse("/assistent", status_code=303)
            conn.execute(
                "INSERT INTO teams (name, jahrgang, active) VALUES (?, ?, 1)",
                (name_value, jahrgang_value),
            )
            conn.commit()

        return RedirectResponse(f"/assistent/{event_id}", status_code=303)

    @router.post("/assistent/{event_id}/spielfeld/anlegen")
    def assistent_create_court(
        event_id: int,
        name: str = Form(...),
        sportart: str = Form(""),
        location: str = Form(DEFAULT_COMPETITION_LOCATION),
    ):
        """Spontanes Anlegen eines Spielfelds direkt im Assistenten (Issue #107),
        analog zu assistent_create_team - nutzt dieselbe Insert-Logik wie
        /court/create (siehe app/routes/venues.py)."""
        name_value = name.strip()
        if not name_value:
            return RedirectResponse(f"/assistent/{event_id}?error=court_invalid", status_code=303)
        location_value = normalize_competition_location(location) or DEFAULT_COMPETITION_LOCATION

        with get_conn() as conn:
            event = conn.execute("SELECT id FROM events WHERE id = ?", (event_id,)).fetchone()
            if event is None:
                return RedirectResponse("/assistent", status_code=303)
            conn.execute(
                "INSERT INTO courts (name, sportart, location, active) VALUES (?, ?, ?, 1)",
                (name_value, sportart.strip() or None, location_value),
            )
            conn.commit()

        return RedirectResponse(f"/assistent/{event_id}", status_code=303)

    @router.post("/assistent/{event_id}/wettbewerb/anlegen")
    def assistent_create_competition(
        event_id: int,
        name: str = Form(""),
        sportart: str = Form(...),
        jahrgang: str = Form(...),
        competition_type: str = Form("Turnier"),
        tournament_mode: str = Form(""),
        team_ids: List[str] = Form([]),
        team_selection_active: str = Form(""),
    ):
        sportart_value = sportart.strip()
        jahrgang_value = normalize_jahrgang(jahrgang)

        if (
            not sportart_value
            or jahrgang_value is None
            or competition_type not in COMPETITION_TYPES
        ):
            return RedirectResponse(f"/assistent/{event_id}?error=invalid", status_code=303)

        explicit_team_ids = []
        for team_id in team_ids:
            try:
                explicit_team_ids.append(int(team_id))
            except (TypeError, ValueError):
                pass

        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            event = conn.execute("SELECT id FROM events WHERE id = ?", (event_id,)).fetchone()
            if event is None:
                return RedirectResponse("/assistent", status_code=303)

            # Team-Feinauswahl (Issue #85): das Formular zeigt standardmaessig
            # alle Teams des gewaehlten Jahrgangs vorausgewaehlt an, einzelne
            # lassen sich abwaehlen (team_selection_active markiert, dass die
            # Checkbox-Auswahl aktiv ist statt eines leeren/veralteten
            # team_ids ohne jegliche Absicht). Nur gueltige, aktive Teams des
            # gewaehlten Jahrgangs werden uebernommen, analog zu
            # /competition/create.
            if team_selection_active == "1":
                valid_team_ids = {
                    row["id"] for row in conn.execute(
                        "SELECT id FROM teams WHERE active = 1 AND jahrgang = ?",
                        (jahrgang_value,),
                    ).fetchall()
                }
                explicit_team_ids = [tid for tid in explicit_team_ids if tid in valid_team_ids]
                team_count = len(explicit_team_ids)
            else:
                explicit_team_ids = []
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

            cursor = conn.execute(
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
            new_competition_id = cursor.lastrowid
            for team_id in explicit_team_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO competition_teams (competition_id, team_id) VALUES (?, ?)",
                    (new_competition_id, team_id),
                )
            conn.commit()

        return RedirectResponse(f"/assistent/{event_id}", status_code=303)

    @router.post("/assistent/{event_id}/wettbewerb/{competition_id}/disziplin/anlegen")
    def assistent_create_discipline(
        event_id: int,
        competition_id: int,
        name: str = Form(...),
        unit: str = Form(""),
        scoring_direction: str = Form("higher"),
    ):
        """Schlanke Inline-Anlage einer Sechskampf-Disziplin direkt im
        Assistenten (Issue #107), ohne auf /wettbewerbe umzuleiten - nutzt
        dieselbe Insert-Logik wie /competition/{id}/discipline/create (siehe
        app/routes/competitions.py), mit sinnvollen Defaults fuer
        values_per_team/location, die dort feiner einstellbar bleiben."""
        name_value = name.strip()
        if not name_value or scoring_direction not in DISCIPLINE_SCORING_DIRECTIONS:
            return RedirectResponse(
                f"/assistent/{event_id}?error=discipline_invalid", status_code=303
            )

        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            competition = conn.execute(
                "SELECT id FROM competitions WHERE id = ? AND event_id = ? AND competition_type = 'Sechskampf'",
                (competition_id, event_id),
            ).fetchone()
            if competition is None:
                return RedirectResponse(f"/assistent/{event_id}", status_code=303)
            next_sort_order = conn.execute(
                "SELECT COALESCE(MAX(sort_order), 0) + 1 AS n FROM competition_disciplines WHERE competition_id = ?",
                (competition_id,),
            ).fetchone()["n"]
            conn.execute(
                """
                INSERT INTO competition_disciplines (
                    competition_id, name, sort_order, unit, scoring_direction, values_per_team
                ) VALUES (?, ?, ?, ?, ?, 1)
                """,
                (competition_id, name_value, next_sort_order, unit.strip() or None, scoring_direction),
            )
            conn.commit()

        return RedirectResponse(f"/assistent/{event_id}", status_code=303)

    @router.post("/assistent/{event_id}/disziplin/{discipline_id}/loeschen")
    def assistent_delete_discipline(event_id: int, discipline_id: int):
        """Entfernt eine Sechskampf-Disziplin direkt im Assistenten (Issue
        #107) - nutzt dieselbe Delete-Logik wie /discipline/{id}/delete
        (siehe app/routes/competitions.py), redirected aber zurueck in den
        Assistenten statt nach /wettbewerbe."""
        with get_conn() as conn:
            existing = conn.execute(
                """
                SELECT cd.id
                FROM competition_disciplines cd
                JOIN competitions c ON c.id = cd.competition_id
                WHERE cd.id = ? AND c.event_id = ?
                """,
                (discipline_id, event_id),
            ).fetchone()
            if existing is None:
                return RedirectResponse(f"/assistent/{event_id}", status_code=303)
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM sixkampf_team_results WHERE discipline_id = ?", (discipline_id,)
            )
            conn.execute(
                "DELETE FROM discipline_results WHERE discipline_id = ?", (discipline_id,)
            )
            conn.execute(
                "DELETE FROM competition_disciplines WHERE id = ?", (discipline_id,)
            )
            conn.commit()

        return RedirectResponse(f"/assistent/{event_id}", status_code=303)

    return router
