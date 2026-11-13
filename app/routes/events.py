from datetime import datetime
from typing import Callable
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.database import get_conn
from app.services.event_status_service import (
    EVENT_STATUS_ACTIVE,
    EVENT_STATUSES,
    EVENT_STATUS_SET,
    archive_event,
    clear_active_events,
    deactivate_event,
    fetch_events_with_competition_counts,
    restore_event,
    set_active_event,
)
from app.services.programmpunkte_service import (
    PROGRAMMPUNKT_VORSCHLAEGE,
    create_programmpunkt,
    delete_programmpunkt,
    list_programmpunkte,
)
from app.web import templates


EVENT_TYPES = ["Bewegungsfest", "Einzelturnier", "Käthelauf", "Schulpokal", "Sonstiges"]
EVENT_TYPES_SET = set(EVENT_TYPES)


def get_unique_event_name(conn, original_name: str):
    base_name = f"{original_name} (Kopie)"
    candidate = base_name
    copy_number = 2
    while conn.execute("SELECT 1 FROM events WHERE name = ?", (candidate,)).fetchone():
        candidate = f"{base_name} {copy_number}"
        copy_number += 1
    return candidate


def create_router(
    *,
    app_now_db_timestamp: Callable[[], str],
    get_day_schedule_for_event: Callable,
    calculate_event_overall_ranking: Callable,
    get_competition_timing: Callable,
    get_unique_competition_name: Callable,
    copy_competition_disciplines: Callable,
) -> APIRouter:
    router = APIRouter()

    @router.get("/events")
    def events_list(request: Request):
        with get_conn() as conn:
            events = fetch_events_with_competition_counts(conn, include_archived=True)
        return templates.TemplateResponse(
            request=request, name="events.html", context={"events": events}
        )

    @router.get("/events/new")
    def event_new(request: Request):
        return templates.TemplateResponse(
            request=request, name="event_form.html",
            context={
                "event": None,
                "event_types": EVENT_TYPES,
                "event_statuses": EVENT_STATUSES,
                "form_action": "/events/create",
            }
        )

    @router.post("/events/create")
    def event_create(
        name: str = Form(...), description: str = Form(""),
        details: str = Form(""),
        event_date: str = Form(""), status: str = Form("geplant"),
        event_type: str = Form(...),
    ):
        if (
            not name.strip()
            or status not in EVENT_STATUS_SET
            or event_type not in EVENT_TYPES_SET
        ):
            return RedirectResponse("/events", status_code=303)
        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM events WHERE name = ?", (name.strip(),)).fetchone():
                return RedirectResponse("/events", status_code=303)
            if status == EVENT_STATUS_ACTIVE:
                clear_active_events(conn)
            conn.execute("""
                INSERT INTO events (name, description, details, event_date, status, event_type)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                name.strip(), description.strip() or None, details.strip() or None,
                event_date or None, status, event_type,
            ))
            conn.commit()
        return RedirectResponse("/events", status_code=303)

    @router.get("/events/{event_id}/edit")
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
                "event_statuses": EVENT_STATUSES,
                "form_action": f"/events/{event_id}/update",
                "saved_at": saved_at_value,
            }
        )

    @router.post("/events/{event_id}/update")
    def event_update(
        event_id: int, name: str = Form(...), description: str = Form(""),
        details: str = Form(""),
        event_date: str = Form(""), status: str = Form("geplant"),
        event_type: str = Form(...),
    ):
        if (
            not name.strip()
            or status not in EVENT_STATUS_SET
            or event_type not in EVENT_TYPES_SET
        ):
            return RedirectResponse("/events", status_code=303)
        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            duplicate = conn.execute(
                "SELECT 1 FROM events WHERE name = ? AND id != ?",
                (name.strip(), event_id)
            ).fetchone()
            if duplicate:
                return RedirectResponse(f"/events/{event_id}/edit", status_code=303)
            if status == EVENT_STATUS_ACTIVE:
                clear_active_events(conn, except_event_id=event_id)
            conn.execute("""
                UPDATE events
                SET name = ?, description = ?, details = ?, event_date = ?, status = ?, event_type = ?
                WHERE id = ?
            """, (
                name.strip(), description.strip() or None, details.strip() or None,
                event_date or None, status, event_type, event_id,
            ))
            conn.commit()
        saved_at = app_now_db_timestamp()
        return RedirectResponse(f"/events/{event_id}/edit?saved_at={saved_at}", status_code=303)

    @router.post("/events/{event_id}/activate")
    def event_activate(event_id: int):
        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            set_active_event(conn, event_id)
            conn.commit()
        return RedirectResponse("/events", status_code=303)

    @router.post("/events/{event_id}/deactivate")
    def event_deactivate(event_id: int):
        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            deactivate_event(conn, event_id)
            conn.commit()
        return RedirectResponse("/events", status_code=303)

    @router.post("/events/{event_id}/archive")
    def event_archive(event_id: int):
        with get_conn() as conn:
            archive_event(conn, event_id)
            conn.commit()
        return RedirectResponse("/events", status_code=303)

    @router.post("/events/{event_id}/restore")
    def event_restore(event_id: int):
        with get_conn() as conn:
            restore_event(conn, event_id)
            conn.commit()
        return RedirectResponse("/events", status_code=303)

    @router.post("/events/{event_id}/siegerehrung-public")
    def event_siegerehrung_public(event_id: int):
        with get_conn() as conn:
            conn.execute(
                "UPDATE events SET siegerehrung_public = 1 WHERE id = ?", (event_id,)
            )
            conn.commit()
        return RedirectResponse(f"/events/{event_id}", status_code=303)

    @router.post("/events/{event_id}/siegerehrung-private")
    def event_siegerehrung_private(event_id: int):
        with get_conn() as conn:
            conn.execute(
                "UPDATE events SET siegerehrung_public = 0 WHERE id = ?", (event_id,)
            )
            conn.commit()
        return RedirectResponse(f"/events/{event_id}", status_code=303)

    @router.post("/events/{event_id}/delete")
    def event_delete(event_id: int):
        with get_conn() as conn:
            competition_count = conn.execute(
                "SELECT COUNT(*) FROM competitions WHERE event_id = ?", (event_id,)
            ).fetchone()[0]
            if competition_count:
                return RedirectResponse(f"/events/{event_id}", status_code=303)
            conn.execute("DELETE FROM event_programmpunkte WHERE event_id = ?", (event_id,))
            conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
            conn.commit()
        return RedirectResponse("/events", status_code=303)

    @router.post("/events/{event_id}/duplicate")
    def event_duplicate(event_id: int):
        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            event = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
            if event is None:
                return RedirectResponse("/events", status_code=303)
            cursor = conn.execute("""
                INSERT INTO events (name, description, details, event_date, status, event_type)
                VALUES (?, ?, ?, ?, 'geplant', ?)
            """, (
                get_unique_event_name(conn, event["name"]),
                event["description"], event["details"], event["event_date"], event["event_type"],
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
                        start_time, end_time, location, competition_date
                    ) VALUES (?, ?, ?, 'geplant', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    # Neue Veranstaltung, also bleibt der Name frei: Namen
                    # sind nur innerhalb einer Veranstaltung eindeutig.
                    competition["name"],
                    competition["sportart"], competition["jahrgang"],
                    competition["points_win"], competition["points_draw"],
                    competition["points_loss"], competition["points_first_place"],
                    competition["placement_points"], new_event_id,
                    competition["competition_type"],
                    timing["game_duration_minutes"], timing["changeover_duration_minutes"],
                    competition["start_time"],
                    competition["end_time"], competition["location"],
                    competition["competition_date"],
                ))
                copy_competition_disciplines(
                    conn, competition["id"], competition_cursor.lastrowid
                )
            conn.commit()
        return RedirectResponse(f"/events/{new_event_id}", status_code=303)

    @router.get("/events/{event_id}")
    def event_detail(request: Request, event_id: int):
        with get_conn() as conn:
            event = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        if event is None:
            return RedirectResponse("/events", status_code=303)

        schedule = get_day_schedule_for_event(event_id)
        overall_ranking = calculate_event_overall_ranking(event_id)
        with get_conn() as conn:
            programmpunkte = list_programmpunkte(conn, event_id)
            jahrgang_rows = conn.execute(
                """
                SELECT DISTINCT jahrgang FROM competitions
                WHERE event_id = ? AND status != 'archiviert'
                  AND jahrgang IS NOT NULL AND jahrgang != '' AND jahrgang != 'mixed'
                """,
                (event_id,),
            ).fetchall()
        jahrgaenge = sorted(
            {str(row["jahrgang"]) for row in jahrgang_rows},
            key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else 0, value),
        )
        return templates.TemplateResponse(
            request=request, name="event_detail.html",
            context={
                "event": event,
                "overall_ranking": overall_ranking,
                "schedule": schedule,
                "programmpunkte": programmpunkte,
                "programmpunkt_jahrgaenge": jahrgaenge,
                "programmpunkt_vorschlaege": PROGRAMMPUNKT_VORSCHLAEGE,
                "programmpunkt_fehler": request.query_params.get("programmpunkt_fehler", ""),
            }
        )

    @router.post("/events/{event_id}/programmpunkt/anlegen")
    async def event_programmpunkt_anlegen(request: Request, event_id: int):
        form = await request.form()
        try:
            with get_conn() as conn:
                create_programmpunkt(
                    conn, event_id,
                    form.get("titel"), form.get("start_time"), form.get("end_time"),
                    form.getlist("jahrgaenge"),
                )
                conn.commit()
        except ValueError as error:
            return RedirectResponse(
                f"/events/{event_id}?{urlencode({'programmpunkt_fehler': str(error)})}#programmpunkte",
                status_code=303,
            )
        return RedirectResponse(f"/events/{event_id}#tagesplan", status_code=303)

    @router.post("/events/{event_id}/programmpunkt/{programmpunkt_id}/loeschen")
    def event_programmpunkt_loeschen(event_id: int, programmpunkt_id: int):
        with get_conn() as conn:
            delete_programmpunkt(conn, event_id, programmpunkt_id)
            conn.commit()
        return RedirectResponse(f"/events/{event_id}#programmpunkte", status_code=303)

    return router