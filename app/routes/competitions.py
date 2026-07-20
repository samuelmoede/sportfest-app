from collections import defaultdict
from datetime import date, datetime
from math import isfinite
from typing import Callable, List

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.database import get_conn
from app.routes.teams import normalize_jahrgang
from app.services.event_status_service import (
    fetch_events_with_competition_counts,
    resolve_selected_event_id,
)
from app.services.schedule_generator_service import (
    DEFAULT_SCHULPOKAL_MODE,
    SCHULPOKAL_MODES,
)
from app.web import templates

COMPETITION_TYPES = ("Turnier", "Sechskampf", "Schulpokal")


def _normalize_tournament_mode(competition_type: str, tournament_mode: str):
    if competition_type != "Schulpokal":
        return None
    return tournament_mode if tournament_mode in SCHULPOKAL_MODES else DEFAULT_SCHULPOKAL_MODE


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


def create_router(
    *,
    app_now_db_timestamp: Callable[[], str],
    app_today: Callable[[], date],
    get_competition_timing: Callable,
    get_unique_competition_name: Callable,
    copy_competition_disciplines: Callable,
    normalize_competition_location: Callable,
    parse_placement_points_config: Callable,
    competition_locations,
    default_game_duration_minutes: int,
    default_changeover_duration_minutes: int,
) -> APIRouter:
    router = APIRouter()

    def _discipline_saved_redirect(competition_id, event_id, saved_at):
        # Den aktiven Veranstaltungsfilter der Wettbewerbe-Seite beibehalten,
        # sonst faellt /wettbewerbe auf die Standard-(aktive)-Veranstaltung
        # zurueck und der gerade bearbeitete Wettbewerb (aus einer anderen
        # Veranstaltung) verschwindet aus der gefilterten Ansicht - der
        # AJAX-Handler faende seinen Abschnitt dann nicht und meldete
        # faelschlich einen Fehler, obwohl gespeichert wurde. Ein leerer
        # event_id-Parameter erzwingt die ungefilterte Gesamtansicht (fuer
        # Wettbewerbe ohne Veranstaltung).
        event_param = event_id if event_id is not None else ""
        return RedirectResponse(
            f"/wettbewerbe?event_id={event_param}"
            f"&saved_competition_id={competition_id}&saved_at={saved_at}"
            f"#competition-{competition_id}",
            status_code=303,
        )

    @router.get("/wettbewerbe")
    def wettbewerbe(request: Request):
        competitions = get_all_competitions()
        selected_event_id = request.query_params.get("event_id", "").strip()
        saved_competition_id = request.query_params.get("saved_competition_id", "").strip()
        saved_at = request.query_params.get("saved_at", "").strip()
        show_archived = request.query_params.get("show_archived", "") == "1"
        saved_competition_id_value = None
        if saved_competition_id.isdigit():
            saved_competition_id_value = int(saved_competition_id)
        if not show_archived:
            competitions = [c for c in competitions if c["status"] != "archiviert"]
        with get_conn() as conn:
            disciplines = conn.execute("""
                SELECT * FROM competition_disciplines
                ORDER BY competition_id, sort_order, id
            """).fetchall()
            events = fetch_events_with_competition_counts(
                conn,
                include_archived=show_archived,
            )
            team_counts = conn.execute("""
                SELECT jahrgang, COUNT(*) AS count
                FROM teams
                WHERE active = 1
                GROUP BY jahrgang
                ORDER BY jahrgang
            """).fetchall()
            all_teams = conn.execute("""
                SELECT * FROM teams WHERE active = 1 ORDER BY jahrgang, name
            """).fetchall()
            ct_rows = conn.execute("""
                SELECT competition_id, team_id
                FROM competition_teams
            """).fetchall()
        selected_event_id_value = resolve_selected_event_id(
            events,
            event_id_value=selected_event_id,
            event_filter_present="event_id" in request.query_params,
            today=app_today(),
        )
        if selected_event_id_value is not None:
            competitions = [
                competition
                for competition in competitions
                if competition["event_id"] == selected_event_id_value
            ]
        disciplines_by_competition = defaultdict(list)
        for discipline in disciplines:
            disciplines_by_competition[discipline["competition_id"]].append(discipline)

        # Build explicit team sets per competition for template rendering
        competition_explicit_team_ids = defaultdict(set)
        for row in ct_rows:
            competition_explicit_team_ids[row["competition_id"]].add(row["team_id"])

        # Group all teams by gruppe for the team-selection UI
        all_teams_by_gruppe = defaultdict(list)
        for team in all_teams:
            all_teams_by_gruppe[str(team["jahrgang"])].append(dict(team))

        team_years = [row["jahrgang"] for row in team_counts]
        all_groups = sorted(all_teams_by_gruppe.keys(), key=lambda g: (0 if g.isdigit() else 1, int(g) if g.isdigit() else g))

        return templates.TemplateResponse(
            request=request, name="wettbewerbe.html",
            context={
                "competitions": competitions,
                "disciplines_by_competition": disciplines_by_competition,
                "events": events,
                "events_by_id": {event["id"]: event for event in events},
                "selected_event_id": selected_event_id_value,
                "show_archived": show_archived,
                "saved_competition_id": saved_competition_id_value,
                "saved_at": saved_at,
                "team_counts_by_year": {
                    str(row["jahrgang"]): row["count"] for row in team_counts
                },
                "team_years": team_years,
                "all_teams": [dict(t) for t in all_teams],
                "all_teams_by_gruppe": dict(all_teams_by_gruppe),
                "all_groups": all_groups,
                "competition_explicit_team_ids": dict(competition_explicit_team_ids),
                "competition_locations": competition_locations,
                "default_game_duration_minutes": default_game_duration_minutes,
                "default_changeover_duration_minutes": default_changeover_duration_minutes,
                "schulpokal_modes": SCHULPOKAL_MODES,
                "default_schulpokal_mode": DEFAULT_SCHULPOKAL_MODE,
            }
        )

    @router.post("/competition/create")
    def create_competition(
        name: str = Form(...), sportart: str = Form(...), jahrgang: str = Form(""),
        points_win: float = Form(3), points_draw: float = Form(1), points_loss: float = Form(0),
        start_time: str = Form(""), end_time: str = Form(""),
        location: str = Form(""),
        event_id: str = Form(""), competition_type: str = Form("Turnier"),
        tournament_mode: str = Form(""),
        status: str = Form("geplant"),
        game_duration_minutes: int = Form(default_game_duration_minutes),
        changeover_duration_minutes: int = Form(default_changeover_duration_minutes),
        points_first_place: str = Form(""),
        placement_points: str = Form(""),
        team_ids: List[str] = Form([]),
    ):
        name_value = name.strip()
        sportart_value = sportart.strip()
        jahrgang_value = normalize_jahrgang(jahrgang)
        tournament_mode_value = _normalize_tournament_mode(competition_type, tournament_mode)

        # Resolve explicit team IDs from checkboxes
        explicit_team_ids = []
        for tid in team_ids:
            try:
                explicit_team_ids.append(int(tid))
            except (ValueError, TypeError):
                pass

        if (
            not name_value
            or not sportart_value
            or competition_type not in COMPETITION_TYPES
            or status not in {"geplant", "läuft", "beendet", "archiviert"}
            or game_duration_minutes < 1
            or changeover_duration_minutes < 0
            or not all(isfinite(value) for value in (points_win, points_draw, points_loss))
        ):
            return RedirectResponse("/wettbewerbe", status_code=303)

        # Need either explicit teams or a valid jahrgang with existing teams
        if not explicit_team_ids and jahrgang_value is None:
            return RedirectResponse("/wettbewerbe", status_code=303)

        location_raw = location.strip()
        location_value = normalize_competition_location(location_raw)
        if location_raw and location_value is None:
            return RedirectResponse("/wettbewerbe", status_code=303)
        try:
            event_id_value = int(event_id) if event_id else None
        except ValueError:
            return RedirectResponse("/wettbewerbe", status_code=303)

        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            duplicate_name = conn.execute(
                "SELECT 1 FROM competitions WHERE name = ?",
                (name_value,)
            ).fetchone()
            if duplicate_name:
                return RedirectResponse("/wettbewerbe", status_code=303)
            if event_id_value is not None and conn.execute(
                "SELECT 1 FROM events WHERE id = ?", (event_id_value,)
            ).fetchone() is None:
                return RedirectResponse("/wettbewerbe", status_code=303)

            if not explicit_team_ids:
                # Check that at least one team with this jahrgang exists
                if conn.execute(
                    "SELECT 1 FROM teams WHERE active = 1 AND jahrgang = ? LIMIT 1",
                    (jahrgang_value,)
                ).fetchone() is None:
                    return RedirectResponse("/wettbewerbe", status_code=303)

            try:
                if points_first_place:
                    points_first_place_value = int(points_first_place)
                elif explicit_team_ids:
                    points_first_place_value = len(explicit_team_ids) or 7
                else:
                    points_first_place_value = conn.execute("""
                        SELECT COUNT(*) AS count FROM teams
                        WHERE active = 1 AND jahrgang = ?
                    """, (jahrgang_value,)).fetchone()["count"] or 7
            except (TypeError, ValueError):
                return RedirectResponse("/wettbewerbe", status_code=303)
            placement_points_value = placement_points.strip()
            if placement_points_value and not parse_placement_points_config(placement_points_value):
                return RedirectResponse("/wettbewerbe", status_code=303)
            if points_first_place_value < 1:
                return RedirectResponse("/wettbewerbe", status_code=303)

            # Use 'mixed' as jahrgang label when only explicit teams are selected without a group
            stored_jahrgang = jahrgang_value if jahrgang_value is not None else "mixed"

            cursor = conn.execute("""
                INSERT INTO competitions (
                    name, sportart, jahrgang, status, points_win, points_draw,
                    points_loss, points_first_place, placement_points, event_id, competition_type,
                    tournament_mode, game_duration_minutes, changeover_duration_minutes,
                    start_time, end_time, location
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                name_value, sportart_value, stored_jahrgang, status, points_win, points_draw, points_loss,
                points_first_place_value, placement_points_value or None, event_id_value, competition_type,
                tournament_mode_value, game_duration_minutes, changeover_duration_minutes,
                start_time.strip() or None, end_time.strip() or None,
                location_value or None,
            ))
            new_competition_id = cursor.lastrowid

            if explicit_team_ids:
                for team_id in explicit_team_ids:
                    conn.execute(
                        "INSERT OR IGNORE INTO competition_teams (competition_id, team_id) VALUES (?, ?)",
                        (new_competition_id, team_id),
                    )
            conn.commit()
        return RedirectResponse("/wettbewerbe", status_code=303)

    @router.post("/competition/{competition_id}/duplicate")
    def duplicate_competition(competition_id: int):
        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            competition = conn.execute(
                "SELECT * FROM competitions WHERE id = ?", (competition_id,)
            ).fetchone()
            if competition is None:
                return RedirectResponse("/wettbewerbe", status_code=303)
            timing = get_competition_timing(competition)
            cursor = conn.execute("""
                INSERT INTO competitions (
                    name, sportart, jahrgang, status, points_win, points_draw,
                    points_loss, points_first_place, placement_points, event_id, competition_type,
                    tournament_mode, game_duration_minutes, changeover_duration_minutes,
                    start_time, end_time, location
                ) VALUES (?, ?, ?, 'geplant', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                get_unique_competition_name(conn, competition["name"]),
                competition["sportart"], competition["jahrgang"], competition["points_win"],
                competition["points_draw"], competition["points_loss"],
                competition["points_first_place"], competition["placement_points"],
                competition["event_id"], competition["competition_type"],
                competition["tournament_mode"],
                timing["game_duration_minutes"], timing["changeover_duration_minutes"],
                competition["start_time"], competition["end_time"],
                competition["location"],
            ))
            new_id = cursor.lastrowid
            copy_competition_disciplines(conn, competition_id, new_id)
            # Also copy explicit team assignments
            existing_teams = conn.execute(
                "SELECT team_id FROM competition_teams WHERE competition_id = ?",
                (competition_id,)
            ).fetchall()
            for row in existing_teams:
                conn.execute(
                    "INSERT OR IGNORE INTO competition_teams (competition_id, team_id) VALUES (?, ?)",
                    (new_id, row["team_id"]),
                )
            conn.commit()
        return RedirectResponse("/wettbewerbe", status_code=303)

    @router.post("/competition/{competition_id}/update")
    def update_competition(
        competition_id: int, name: str = Form(...), sportart: str = Form(...),
        jahrgang: str = Form(""), status: str = Form(...),
        points_win: str = Form(...), points_draw: str = Form(...), points_loss: str = Form(...),
        start_time: str = Form(""), end_time: str = Form(""),
        location: str = Form(""),
        event_id: str = Form(""), competition_type: str = Form("Turnier"),
        tournament_mode: str = Form(""),
        game_duration_minutes: int = Form(default_game_duration_minutes),
        changeover_duration_minutes: int = Form(default_changeover_duration_minutes),
        points_first_place: str = Form("7"),
        placement_points: str = Form(""),
        team_ids: List[str] = Form([]),
    ):
        try:
            points_win_value = float(points_win.strip().replace(",", "."))
            points_draw_value = float(points_draw.strip().replace(",", "."))
            points_loss_value = float(points_loss.strip().replace(",", "."))
            points_first_place_value = int(points_first_place)
            event_id_value = int(event_id) if event_id else None
        except (TypeError, ValueError):
            return RedirectResponse("/wettbewerbe", status_code=303)

        jahrgang_value = normalize_jahrgang(jahrgang)
        tournament_mode_value = _normalize_tournament_mode(competition_type, tournament_mode)

        # Resolve explicit team IDs from checkboxes
        explicit_team_ids = []
        for tid in team_ids:
            try:
                explicit_team_ids.append(int(tid))
            except (ValueError, TypeError):
                pass

        name_value = name.strip()
        sportart_value = sportart.strip()
        location_raw = location.strip()
        location_value = normalize_competition_location(location_raw)
        valid_statuses = {"geplant", "läuft", "beendet", "archiviert"}
        if (
            not name_value or not sportart_value
            or status not in valid_statuses
            or competition_type not in COMPETITION_TYPES
            or (location_raw and location_value is None)
            or game_duration_minutes < 1
            or changeover_duration_minutes < 0
            or points_first_place_value < 1
            or not all(isfinite(value) for value in (points_win_value, points_draw_value, points_loss_value))
        ):
            return RedirectResponse("/wettbewerbe", status_code=303)

        # Need either explicit teams or a valid jahrgang with existing teams
        if not explicit_team_ids and jahrgang_value is None:
            return RedirectResponse("/wettbewerbe", status_code=303)

        placement_points_value = placement_points.strip()
        if placement_points_value and not parse_placement_points_config(placement_points_value):
            return RedirectResponse("/wettbewerbe", status_code=303)

        with get_conn() as conn:
            if not explicit_team_ids and conn.execute(
                "SELECT 1 FROM teams WHERE active = 1 AND jahrgang = ? LIMIT 1",
                (jahrgang_value,)
            ).fetchone() is None:
                return RedirectResponse("/wettbewerbe", status_code=303)
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

            stored_jahrgang = jahrgang_value if jahrgang_value is not None else "mixed"

            conn.execute("""
                UPDATE competitions
                SET name = ?, sportart = ?, jahrgang = ?, status = ?,
                    points_win = ?, points_draw = ?, points_loss = ?,
                    points_first_place = ?, placement_points = ?, event_id = ?, competition_type = ?,
                    tournament_mode = ?, game_duration_minutes = ?, changeover_duration_minutes = ?,
                    start_time = ?, end_time = ?, location = ?, location_subarea = NULL
                WHERE id = ?
            """, (
                name_value, sportart_value, stored_jahrgang, status,
                points_win_value, points_draw_value, points_loss_value,
                points_first_place_value, placement_points_value or None, event_id_value, competition_type,
                tournament_mode_value, game_duration_minutes, changeover_duration_minutes,
                start_time.strip() or None, end_time.strip() or None,
                location_value or None,
                competition_id,
            ))

            # Update explicit team assignments: clear then re-insert
            conn.execute("DELETE FROM competition_teams WHERE competition_id = ?", (competition_id,))
            for team_id in explicit_team_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO competition_teams (competition_id, team_id) VALUES (?, ?)",
                    (competition_id, team_id),
                )
            conn.commit()
        saved_at = app_now_db_timestamp()
        return RedirectResponse(
            f"/wettbewerbe?saved_competition_id={competition_id}&saved_at={saved_at}#competition-{competition_id}",
            status_code=303,
        )

    @router.post("/competition/{competition_id}/discipline/create")
    def create_competition_discipline(
        competition_id: int, name: str = Form(...), sort_order: str = Form(...),
        unit: str = Form(""), scoring_direction: str = Form("higher"),
        values_per_team: str = Form("1"), location: str = Form(""),
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
                    scoring_direction, values_per_team, location
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                competition_id, name_value, sort_order_value, unit.strip() or None,
                scoring_direction, values_per_team_value, location.strip() or None,
            ))
            conn.commit()
        saved_at = app_now_db_timestamp()
        return _discipline_saved_redirect(competition_id, competition["event_id"], saved_at)

    @router.post("/discipline/{discipline_id}/update")
    def update_competition_discipline(
        discipline_id: int, name: str = Form(...), sort_order: str = Form(...),
        unit: str = Form(""), scoring_direction: str = Form("higher"),
        values_per_team: str = Form("1"), location: str = Form(""),
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
            existing = conn.execute(
                """
                SELECT cd.competition_id, c.event_id
                FROM competition_disciplines cd
                JOIN competitions c ON c.id = cd.competition_id
                WHERE cd.id = ?
                """,
                (discipline_id,)
            ).fetchone()
            if existing is None:
                return RedirectResponse("/wettbewerbe", status_code=303)
            conn.execute("""
                UPDATE competition_disciplines
                SET name = ?, sort_order = ?, unit = ?,
                    scoring_direction = ?, values_per_team = ?, location = ?
                WHERE id = ?
            """, (
                name_value, sort_order_value, unit.strip() or None,
                scoring_direction, values_per_team_value, location.strip() or None,
                discipline_id,
            ))
            conn.commit()
        saved_at = app_now_db_timestamp()
        return _discipline_saved_redirect(
            existing["competition_id"], existing["event_id"], saved_at
        )

    @router.post("/competition/{competition_id}/discipline/reorder")
    def reorder_competition_disciplines(
        competition_id: int, discipline_ids: List[int] = Form(...),
    ):
        with get_conn() as conn:
            owned_ids = {
                row["id"] for row in conn.execute(
                    "SELECT id FROM competition_disciplines WHERE competition_id = ?",
                    (competition_id,),
                ).fetchall()
            }
            if set(discipline_ids) != owned_ids:
                return JSONResponse({"ok": False, "error": "mismatch"}, status_code=400)
            conn.execute("BEGIN IMMEDIATE")
            for index, discipline_id in enumerate(discipline_ids, start=1):
                conn.execute(
                    "UPDATE competition_disciplines SET sort_order = ? WHERE id = ?",
                    (index, discipline_id),
                )
            conn.commit()
        return JSONResponse({"ok": True})

    @router.post("/discipline/{discipline_id}/delete")
    def delete_competition_discipline(discipline_id: int):
        with get_conn() as conn:
            existing = conn.execute(
                """
                SELECT cd.competition_id, c.event_id
                FROM competition_disciplines cd
                JOIN competitions c ON c.id = cd.competition_id
                WHERE cd.id = ?
                """,
                (discipline_id,)
            ).fetchone()
            if existing is None:
                return RedirectResponse("/wettbewerbe", status_code=303)
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
        saved_at = app_now_db_timestamp()
        return _discipline_saved_redirect(
            existing["competition_id"], existing["event_id"], saved_at
        )

    @router.post("/competition/{competition_id}/archive")
    def archive_competition(competition_id: int):
        with get_conn() as conn:
            conn.execute("""
                UPDATE competitions
                SET status = 'archiviert'
                WHERE id = ?
            """, (competition_id,))
            conn.commit()

        return RedirectResponse("/wettbewerbe", status_code=303)

    @router.post("/competition/{competition_id}/restore")
    def restore_competition(competition_id: int):
        with get_conn() as conn:
            conn.execute("""
                UPDATE competitions
                SET status = 'geplant'
                WHERE id = ?
            """, (competition_id,))
            conn.commit()

        return RedirectResponse("/wettbewerbe", status_code=303)

    @router.post("/competition/{competition_id}/reset")
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

    @router.post("/competition/{competition_id}/delete")
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
            conn.execute("DELETE FROM competition_teams WHERE competition_id = ?", (competition_id,))
            conn.execute("DELETE FROM competitions WHERE id = ?", (competition_id,))
            conn.commit()

        return RedirectResponse("/wettbewerbe", status_code=303)

    return router
