from datetime import datetime
from typing import Callable

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
from app.web import templates


EVENT_TYPES = ["Bewegungsfest", "Einzelturnier", "Käthelauf", "Sonstiges"]
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

    @router.get("/events/{event_id}")
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

    return router