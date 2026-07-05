from datetime import date, datetime, timedelta
from typing import Callable, List
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.database import get_conn
from app.services.change_log_service import insert_change_log_entry
from app.services.event_status_service import (
    fetch_events_with_competition_counts,
    resolve_selected_event_id,
)
from app.services.schedule_generator_service import (
    generate_group_plan,
    get_already_played_ko_targets,
    get_winner_loser,
    group_phase_finished,
    semifinals_finished,
    validate_generated_plan,
)
from app.services.schedule_grid_service import (
    build_editor_time_grid,
    build_location_print_grid,
    get_location_schedule_sections,
    get_slots_grouped_by_court,
)
from app.services.schedule_location_service import (
    COMPETITION_LOCATIONS,
    NO_SLOT_LOCATION_HINT,
    filter_court_ids_for_competition,
    filter_courts_for_competition,
    filter_groups_for_competition,
    get_effective_competition_location,
    normalize_competition_location,
    schedule_planning_available,
)
from app.services.schedule_time_service import (
    build_end_time_forecast,
    get_competition_timing,
    parse_slot_time,
    recalculate_competition_court_times,
)
from app.services.results_filter_service import build_results_redirect_url
from app.web import templates


def _phase_generation_redirect_target(
    competition_id: int, results_return_to: str, **extra_params: str
) -> str:
    """Schiedsrichter, die die Bestätigung in der Ergebniseingabe auslösen,
    sollen dorthin zurückkehren; Admins, die den Button in der
    Spielplan-Bearbeitung nutzen, weiterhin dorthin. extra_params landen in
    beiden Fällen als zusätzliche Query-Parameter im Ziel (z.B. eine
    Überschreib-Warnung)."""
    if results_return_to:
        return build_results_redirect_url(
            results_return_to, competition_id=competition_id, **extra_params
        )
    query = urlencode({"competition_id": competition_id, **extra_params})
    return f"/spielplan-bearbeiten?{query}"


def _format_slot_result(slot) -> str:
    team_a = slot["team_a"] or "?"
    team_b = slot["team_b"] or "?"
    return f"{team_a} {slot['score_a']}:{slot['score_b']} {team_b}"


def create_router(
    *,
    app_now_db_timestamp: Callable[[], str],
    app_today: Callable[[], date],
    parse_competition_id: Callable,
    parse_jahrgang_filter: Callable,
    get_active_competitions: Callable,
    calculate_group_table: Callable,
    get_teams_for_competition: Callable,
    calculate_sixkampf_station_rotation: Callable,
) -> APIRouter:
    router = APIRouter()

    def record_change(conn, request, **kwargs):
        insert_change_log_entry(
            conn, request=request, created_at=app_now_db_timestamp(), **kwargs
        )

    def get_competition_event_id(competition_id):
        if competition_id is None:
            return None
        with get_conn() as conn:
            competition = conn.execute(
                "SELECT event_id FROM competitions WHERE id = ?",
                (competition_id,),
            ).fetchone()
        return competition["event_id"] if competition is not None else None

    def resolve_event_context(request, event_id_value, selected_competition_id=None):
        with get_conn() as conn:
            event_options = fetch_events_with_competition_counts(
                conn,
                include_archived=True,
            )
        selected_event_id = resolve_selected_event_id(
            event_options,
            event_id_value=event_id_value,
            event_filter_present="event_id" in request.query_params,
            today=app_today(),
            fallback_event_id=get_competition_event_id(selected_competition_id),
        )
        return event_options, selected_event_id

    def build_editor_context(
        selected_competition_id,
        *,
        selected_event_id=None,
        event_options=None,
        proposed_slots=None,
        plan_warnings=None,
        has_plan_errors=False,
        plan_timing=None,
        plan_end_time_forecast=None,
        selected_court_ids=None,
        overwrite_warning=None,
    ):
        proposed_slots = proposed_slots or []
        plan_warnings = plan_warnings or []
        selected_court_ids = selected_court_ids or []

        if event_options is None:
            with get_conn() as conn:
                event_options = fetch_events_with_competition_counts(
                    conn,
                    include_archived=True,
                )

        with get_conn() as conn:
            competition_params = []
            event_clause = ""
            if selected_event_id is not None:
                event_clause = " AND event_id = ?"
                competition_params.append(selected_event_id)
            competitions = conn.execute(f"""
                SELECT *
                FROM competitions
                WHERE status != 'archiviert'{event_clause}
                ORDER BY jahrgang, name
            """, competition_params).fetchall()
            all_courts = conn.execute(
                "SELECT * FROM courts WHERE active = 1 ORDER BY name"
            ).fetchall()
            teams = conn.execute(
                "SELECT * FROM teams WHERE active = 1 ORDER BY jahrgang, name"
            ).fetchall()

        visible_competition_ids = {competition["id"] for competition in competitions}
        if selected_competition_id not in visible_competition_ids:
            selected_competition_id = None

        groups = get_slots_grouped_by_court(
            selected_competition_id,
            event_id=selected_event_id,
        )
        selected_competition = next(
            (
                competition
                for competition in competitions
                if competition["id"] == selected_competition_id
            ),
            None,
        )
        schedule_planning_enabled = (
            selected_competition is None
            or schedule_planning_available(selected_competition)
        )
        selected_competition_location = (
            get_effective_competition_location(selected_competition)
            if selected_competition is not None
            else None
        )

        if selected_competition is not None:
            groups = filter_groups_for_competition(groups, selected_competition)
            courts = filter_courts_for_competition(all_courts, selected_competition)
        else:
            courts = all_courts

        selected_timing = (
            get_competition_timing(selected_competition)
            if selected_competition is not None
            and selected_competition["competition_type"] == "Turnier"
            and schedule_planning_enabled
            else None
        )
        editor_time_marks = (
            build_editor_time_grid(groups, selected_competition, selected_timing)
            if schedule_planning_enabled
            else []
        )
        current_end_time_forecast = (
            build_end_time_forecast(
                [
                    slot
                    for group in groups
                    for slot in group["slots"]
                ],
                selected_competition,
            )
            if schedule_planning_enabled
            else None
        )

        ko_hint = None
        can_generate_semifinals = False
        can_generate_finals = False
        if selected_competition_id and schedule_planning_enabled:
            can_generate_semifinals = group_phase_finished(selected_competition_id)
            can_generate_finals = semifinals_finished(selected_competition_id)

            if can_generate_semifinals:
                ko_hint = "Die Gruppenphase ist beendet. Die Halbfinals können automatisch besetzt werden."
            elif can_generate_finals:
                ko_hint = "Die Halbfinals sind beendet. Finale und Spiel um Platz 3 können automatisch besetzt werden."

        overwrite_warning_prompt = None
        if (
            overwrite_warning in ("Halbfinale", "Finale")
            and selected_competition_id is not None
        ):
            already_played = get_already_played_ko_targets(
                selected_competition_id, overwrite_warning
            )
            if already_played:
                overwrite_warning_prompt = {
                    "phase": overwrite_warning,
                    "results": [_format_slot_result(slot) for slot in already_played],
                    "action": (
                        f"/competition/{selected_competition_id}/generate-semifinals"
                        if overwrite_warning == "Halbfinale"
                        else f"/competition/{selected_competition_id}/generate-finals"
                    ),
                }

        return {
            "groups": groups,
            "competitions": competitions,
            "courts": courts,
            "teams": teams,
            "event_options": event_options,
            "selected_event_id": selected_event_id,
            "selected_competition": selected_competition,
            "selected_competition_id": selected_competition_id,
            "selected_competition_location": selected_competition_location,
            "schedule_planning_enabled": schedule_planning_enabled,
            "no_slot_location_hint": NO_SLOT_LOCATION_HINT,
            "editor_time_marks": editor_time_marks,
            "proposed_slots": proposed_slots,
            "plan_warnings": plan_warnings,
            "has_plan_errors": has_plan_errors,
            "plan_timing": plan_timing,
            "selected_timing": selected_timing,
            "current_end_time_forecast": current_end_time_forecast,
            "plan_end_time_forecast": plan_end_time_forecast,
            "selected_generator_court_ids": set(selected_court_ids),
            "ko_hint": ko_hint,
            "can_generate_semifinals": can_generate_semifinals,
            "can_generate_finals": can_generate_finals,
            "overwrite_warning_prompt": overwrite_warning_prompt,
        }

    def render_editor(request, selected_competition_id, **context_overrides):
        context = build_editor_context(selected_competition_id, **context_overrides)
        return templates.TemplateResponse(
            request=request,
            name="spielplan_bearbeiten.html",
            context=context,
        )

    def get_allowed_court_id(conn, competition, court_id):
        if not court_id:
            return None
        try:
            court_value = int(court_id)
        except (TypeError, ValueError):
            return None
        if competition is None or not schedule_planning_available(competition):
            return None
        courts = conn.execute(
            "SELECT * FROM courts WHERE active = 1 ORDER BY name"
        ).fetchall()
        allowed_ids = filter_court_ids_for_competition(
            [court_value], courts, competition
        )
        return court_value if allowed_ids else None

    @router.get("/spielplan")
    def spielplan(
        request: Request,
        event_id: str = "",
        competition_id: str = "",
        jahrgang: str = "",
        ort: str = "",
    ):
        selected_competition_id = parse_competition_id(competition_id)
        selected_jahrgang = parse_jahrgang_filter(jahrgang)
        selected_location = normalize_competition_location(ort)
        event_options, selected_event_id = resolve_event_context(
            request,
            event_id,
            selected_competition_id,
        )

        competitions = [
            competition
            for competition in get_active_competitions()
            if selected_event_id is None
            or competition["event_id"] == selected_event_id
        ]
        visible_competition_ids = {competition["id"] for competition in competitions}
        if selected_competition_id not in visible_competition_ids:
            selected_competition_id = None

        location_sections = get_location_schedule_sections(
            selected_competition_id,
            selected_jahrgang,
            selected_location,
            event_id=selected_event_id,
        )
        available_jahrgaenge = sorted({competition["jahrgang"] for competition in competitions})

        sixkampf_rotation_competition = next(
            (
                competition for competition in competitions
                if competition["id"] == selected_competition_id
                and competition["competition_type"] == "Sechskampf"
            ),
            None,
        )
        sixkampf_rotation = []
        if sixkampf_rotation_competition is not None:
            with get_conn() as conn:
                disciplines = conn.execute("""
                    SELECT * FROM competition_disciplines
                    WHERE competition_id = ?
                    ORDER BY sort_order, id
                """, (selected_competition_id,)).fetchall()
                teams = get_teams_for_competition(
                    conn, selected_competition_id, sixkampf_rotation_competition["jahrgang"]
                )
            sixkampf_rotation = calculate_sixkampf_station_rotation(teams, disciplines)

        return templates.TemplateResponse(
            request=request,
            name="spielplan.html",
            context={
                "location_sections": location_sections,
                "competitions": competitions,
                "event_options": event_options,
                "selected_event_id": selected_event_id,
                "selected_competition_id": selected_competition_id,
                "selected_jahrgang": selected_jahrgang,
                "selected_location": selected_location,
                "competition_locations": COMPETITION_LOCATIONS,
                "available_jahrgaenge": available_jahrgaenge,
                "sixkampf_rotation_competition": sixkampf_rotation_competition,
                "sixkampf_rotation": sixkampf_rotation,
            }
        )

    @router.get("/spielplan/aushang")
    def spielplan_aushang(
        request: Request,
        ort: str = "",
        event_id: str = "",
        jahrgang: str = "",
    ):
        printable_locations = [
            location for location in COMPETITION_LOCATIONS
            if location != "Außenbereich"
        ]
        selected_location = normalize_competition_location(ort) or printable_locations[0]
        selected_jahrgang = parse_jahrgang_filter(jahrgang)
        event_options, selected_event_id = resolve_event_context(request, event_id)

        location_sections = get_location_schedule_sections(
            None, selected_jahrgang, selected_location, event_id=selected_event_id,
        )
        section = next(
            (s for s in location_sections if s["location"] == selected_location), None
        )
        courts, rows = build_location_print_grid(
            section["court_groups"] if section else []
        )

        return templates.TemplateResponse(
            request=request,
            name="spielplan_aushang.html",
            context={
                "printable_locations": printable_locations,
                "selected_location": selected_location,
                "selected_jahrgang": selected_jahrgang,
                "event_options": event_options,
                "selected_event_id": selected_event_id,
                "courts": courts,
                "rows": rows,
            }
        )

    @router.get("/spielplan-bearbeiten")
    def spielplan_bearbeiten(
        request: Request,
        event_id: str = "",
        competition_id: str = "",
        overwrite_warning: str = "",
    ):
        selected_competition_id = parse_competition_id(competition_id)
        event_options, selected_event_id = resolve_event_context(
            request,
            event_id,
            selected_competition_id,
        )
        return render_editor(
            request,
            selected_competition_id,
            selected_event_id=selected_event_id,
            event_options=event_options,
            overwrite_warning=overwrite_warning,
        )

    @router.post("/plan-generator/preview")
    def plan_generator_preview(
        request: Request,
        competition_id: int = Form(...),
        court_ids: List[int] = Form(default=[]),
        startzeit: str = Form(...),
        games_per_team: int = Form(...),
        include_ko: str = Form("0"),
    ):
        with get_conn() as conn:
            competition = conn.execute(
                "SELECT * FROM competitions WHERE id = ?",
                (competition_id,)
            ).fetchone()
            all_courts = conn.execute(
                "SELECT * FROM courts WHERE active = 1 ORDER BY name"
            ).fetchall()
            teams = conn.execute(
                "SELECT * FROM teams WHERE active = 1 ORDER BY jahrgang, name"
            ).fetchall()

        selected_court_ids = (
            filter_court_ids_for_competition(court_ids, all_courts, competition)
            if competition is not None
            else []
        )
        proposed_slots = []
        plan_warnings = []
        plan_timing = get_competition_timing(competition) if competition is not None else None
        plan_end_time_forecast = None

        if competition is None:
            plan_warnings.append({
                "level": "error",
                "message": "Der ausgewählte Wettbewerb wurde nicht gefunden.",
            })
        elif not schedule_planning_available(competition):
            plan_warnings.append({
                "level": "warning",
                "message": NO_SLOT_LOCATION_HINT,
            })
        elif not selected_court_ids:
            plan_warnings.append({
                "level": "error",
                "message": "Wähle mindestens ein Feld oder einen Bereich für den Vorschlag aus.",
            })
        else:
            proposed_slots = generate_group_plan(
                competition_id=competition_id,
                court_ids=selected_court_ids,
                startzeit=startzeit,
                games_per_team=games_per_team,
                include_ko=include_ko == "1",
            )
            expected_teams = [
                team for team in teams
                if team["jahrgang"] == competition["jahrgang"]
            ]
            plan_warnings.extend(
                validate_generated_plan(proposed_slots, expected_teams, games_per_team)
            )
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
        return render_editor(
            request,
            competition_id,
            proposed_slots=proposed_slots,
            plan_warnings=plan_warnings,
            has_plan_errors=has_plan_errors,
            plan_timing=plan_timing,
            plan_end_time_forecast=plan_end_time_forecast,
            selected_court_ids=selected_court_ids,
        )

    @router.post("/plan-generator/apply")
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
            all_courts = conn.execute(
                "SELECT * FROM courts WHERE active = 1 ORDER BY name"
            ).fetchall()
            competitions = {
                row["id"]: row
                for row in conn.execute("SELECT * FROM competitions").fetchall()
            }
            for i in range(len(startzeit)):
                if slot_typ[i] != "Spiel":
                    continue
                competition = competitions.get(competition_id[i])
                allowed_court_ids = filter_court_ids_for_competition(
                    [court_id[i]], all_courts, competition
                )
                if not allowed_court_ids:
                    continue
                conn.execute("""
                    INSERT INTO slots (
                        competition_id, court_id, startzeit, slot_typ, phase, gruppe,
                        team_a_id, team_b_id, status, note
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'geplant', ?)
                """, (
                    competition_id[i],
                    allowed_court_ids[0],
                    startzeit[i],
                    slot_typ[i],
                    phase[i],
                    gruppe[i] or None,
                    int(team_a_id[i]) if team_a_id[i] else None,
                    int(team_b_id[i]) if team_b_id[i] else None,
                    note[i] or None,
                ))

            conn.commit()

        return RedirectResponse(
            f"/spielplan-bearbeiten?competition_id={competition_id[0]}",
            status_code=303,
        )

    @router.post("/competition/{competition_id}/generate-semifinals")
    def generate_semifinals(
        competition_id: int,
        request: Request,
        results_return_to: str = Form(""),
        confirm_overwrite: str = Form("0"),
    ):
        redirect_target = _phase_generation_redirect_target(competition_id, results_return_to)

        already_played = get_already_played_ko_targets(competition_id, "Halbfinale")
        if already_played and confirm_overwrite != "1":
            return RedirectResponse(
                _phase_generation_redirect_target(
                    competition_id, results_return_to, overwrite_warning="Halbfinale"
                ),
                status_code=303,
            )

        group_a = calculate_group_table(competition_id, "A")
        group_b = calculate_group_table(competition_id, "B")

        if len(group_a) < 2 or len(group_b) < 2:
            return RedirectResponse(redirect_target, status_code=303)

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

            for slot in already_played:
                record_change(
                    conn, request,
                    action="Halbfinale neu besetzt (vorheriges Ergebnis überschrieben)",
                    entity_type="slot_result",
                    entity_id=slot["id"],
                    competition_id=competition_id,
                    old_value=_format_slot_result(slot),
                    new_value=None,
                )

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

        return RedirectResponse(redirect_target, status_code=303)

    @router.post("/competition/{competition_id}/generate-finals")
    def generate_finals(
        competition_id: int,
        request: Request,
        results_return_to: str = Form(""),
        confirm_overwrite: str = Form("0"),
    ):
        redirect_target = _phase_generation_redirect_target(competition_id, results_return_to)

        already_played = get_already_played_ko_targets(competition_id, "Finale")
        if already_played and confirm_overwrite != "1":
            return RedirectResponse(
                _phase_generation_redirect_target(
                    competition_id, results_return_to, overwrite_warning="Finale"
                ),
                status_code=303,
            )

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
                return RedirectResponse(redirect_target, status_code=303)

            winner_1, loser_1 = get_winner_loser(semifinals[0])
            winner_2, loser_2 = get_winner_loser(semifinals[1])

            if None in (winner_1, loser_1, winner_2, loser_2):
                return RedirectResponse(redirect_target, status_code=303)

            for slot in already_played:
                record_change(
                    conn, request,
                    action=f"{slot['phase']} neu besetzt (vorheriges Ergebnis überschrieben)",
                    entity_type="slot_result",
                    entity_id=slot["id"],
                    competition_id=competition_id,
                    old_value=_format_slot_result(slot),
                    new_value=None,
                )

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

        return RedirectResponse(redirect_target, status_code=303)

    @router.post("/slot/create")
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
        team_a_value = int(team_a_id) if team_a_id else None
        team_b_value = int(team_b_id) if team_b_id else None

        with get_conn() as conn:
            competition = conn.execute(
                "SELECT * FROM competitions WHERE id = ?", (competition_id,)
            ).fetchone()
            if competition is not None and not schedule_planning_available(competition):
                return RedirectResponse(f"/spielplan-bearbeiten?competition_id={competition_id}", status_code=303)
            court_value = get_allowed_court_id(conn, competition, court_id)
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

    @router.post("/slot/{slot_id}/update")
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
            competition = conn.execute(
                "SELECT * FROM competitions WHERE id = ?", (competition_id,)
            ).fetchone()
            if competition is not None and not schedule_planning_available(competition):
                return RedirectResponse(f"/spielplan-bearbeiten?competition_id={competition_id}", status_code=303)
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
                get_allowed_court_id(conn, competition, court_id),
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

    @router.post("/slot/{slot_id}/delete")
    def delete_slot(slot_id: int):
        with get_conn() as conn:
            conn.execute("DELETE FROM slots WHERE id = ?", (slot_id,))
            conn.commit()

        return RedirectResponse("/spielplan-bearbeiten", status_code=303)

    @router.post("/slots/delete")
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

    @router.post("/slot/{slot_id}/move")
    def move_slot(
        slot_id: int,
        court_id: str = Form(""),
        sort_order: int = Form(0),
        startzeit: str = Form("")
    ):
        court_value = int(court_id) if court_id else None
        startzeit_value = startzeit.strip()
        if startzeit_value and parse_slot_time(startzeit_value) is None:
            return JSONResponse({"success": False, "warnings": []}, status_code=400)

        with get_conn() as conn:
            slot = conn.execute(
                "SELECT competition_id, court_id FROM slots WHERE id = ?", (slot_id,)
            ).fetchone()
            if slot is None:
                return JSONResponse({"success": False, "warnings": []}, status_code=404)
            competition = conn.execute(
                "SELECT * FROM competitions WHERE id = ?", (slot["competition_id"],)
            ).fetchone()
            if not schedule_planning_available(competition):
                return JSONResponse({"success": False, "warnings": []}, status_code=400)
            if court_value is not None:
                allowed_court = get_allowed_court_id(conn, competition, court_value)
                if allowed_court is None:
                    return JSONResponse({"success": False, "warnings": []}, status_code=400)
            affected_courts = {
                (slot["competition_id"], slot["court_id"]),
                (slot["competition_id"], court_value),
            }
            if startzeit_value:
                conn.execute("""
                    UPDATE slots
                    SET court_id = ?,
                        sort_order = ?,
                        startzeit = ?
                    WHERE id = ?
                """, (
                    court_value,
                    sort_order,
                    startzeit_value,
                    slot_id
                ))
                warnings = []
            else:
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

    @router.post("/slot/{slot_id}/copy")
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
            if competition is not None and not schedule_planning_available(competition):
                return RedirectResponse(
                    f"/spielplan-bearbeiten?competition_id={slot['competition_id']}",
                    status_code=303,
                )
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

    @router.post("/competition/{competition_id}/delete-planned-slots")
    def delete_planned_slots(competition_id: int):
        with get_conn() as conn:
            conn.execute("""
                DELETE FROM slots
                WHERE competition_id = ?
                  AND status = 'geplant'
            """, (competition_id,))
            conn.commit()

        return RedirectResponse(f"/spielplan-bearbeiten?competition_id={competition_id}", status_code=303)

    return router