from datetime import datetime
from typing import Callable

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.database import get_conn
from app.services.schedule_location_service import (
    COMPETITION_LOCATIONS,
    DEFAULT_COMPETITION_LOCATION,
    normalize_competition_location,
)
from app.web import templates


def parse_positive_int(value, default=0):
    try:
        parsed_value = int(value)
    except (TypeError, ValueError):
        return default
    return parsed_value if parsed_value >= 0 else default


def create_router(app_now_db_timestamp: Callable[[], str]) -> APIRouter:
    router = APIRouter()

    @router.get("/spielfelder")
    def spielfelder(request: Request):
        delete_status = request.query_params.get("delete_status", "").strip()
        used_games = parse_positive_int(request.query_params.get("used_games"), 0)
        used_slots = parse_positive_int(request.query_params.get("used_slots"), 0)
        used_other_links = parse_positive_int(request.query_params.get("used_other_links"), 0)
        saved_court_id = request.query_params.get("saved_court_id", "").strip()
        saved_at = request.query_params.get("saved_at", "").strip()
        try:
            saved_court_id_value = int(saved_court_id) if saved_court_id else None
        except ValueError:
            saved_court_id_value = None
        try:
            saved_at_value = datetime.strptime(saved_at, "%H:%M").strftime("%H:%M") if saved_at else ""
        except ValueError:
            saved_at_value = ""

        with get_conn() as conn:
            courts = conn.execute(
                "SELECT * FROM courts ORDER BY location, name"
            ).fetchall()

        return templates.TemplateResponse(
            request=request,
            name="spielfelder.html",
            context={
                "courts": courts,
                "court_locations": COMPETITION_LOCATIONS,
                "delete_status": delete_status,
                "used_games": used_games,
                "used_slots": used_slots,
                "used_other_links": used_other_links,
                "saved_court_id": saved_court_id_value,
                "saved_at": saved_at_value,
            },
        )

    @router.post("/court/create")
    def create_court(
        name: str = Form(...),
        sportart: str = Form(""),
        location: str = Form(DEFAULT_COMPETITION_LOCATION),
    ):
        location_value = normalize_competition_location(location) or DEFAULT_COMPETITION_LOCATION
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO courts (name, sportart, location, active)
                VALUES (?, ?, ?, 1)
            """, (name.strip(), sportart.strip() or None, location_value))
            conn.commit()

        return RedirectResponse("/spielfelder", status_code=303)

    @router.post("/court/{court_id}/update")
    def update_court(
        court_id: int,
        name: str = Form(...),
        sportart: str = Form(""),
        location: str = Form(DEFAULT_COMPETITION_LOCATION),
        active: int = Form(0),
    ):
        location_value = normalize_competition_location(location) or DEFAULT_COMPETITION_LOCATION
        with get_conn() as conn:
            conn.execute("""
                UPDATE courts
                SET name = ?, sportart = ?, location = ?, active = ?
                WHERE id = ?
            """, (
                name.strip(),
                sportart.strip() or None,
                location_value,
                1 if active else 0,
                court_id,
            ))
            conn.commit()

        saved_at = app_now_db_timestamp()
        return RedirectResponse(
            f"/spielfelder?saved_court_id={court_id}&saved_at={saved_at}",
            status_code=303,
        )

    @router.post("/court/{court_id}/delete")
    def delete_court(court_id: int):
        with get_conn() as conn:
            usage = conn.execute(
                """
                SELECT
                    COUNT(*) AS used_slots,
                    SUM(CASE WHEN slot_typ = 'Spiel' THEN 1 ELSE 0 END) AS used_games
                FROM slots
                WHERE court_id = ?
                """,
                (court_id,),
            ).fetchone()
            used_slots = int(usage["used_slots"] or 0)
            used_games = int(usage["used_games"] or 0)
            used_other_links = 0

            table_rows = conn.execute("""
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name NOT LIKE 'sqlite_%'
                  AND name <> 'slots'
            """).fetchall()

            for table_row in table_rows:
                table_name = table_row["name"]
                table_columns = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
                has_court_id = any(column["name"] == "court_id" for column in table_columns)
                if not has_court_id:
                    continue

                ref_count = conn.execute(
                    f"SELECT COUNT(*) AS n FROM {table_name} WHERE court_id = ?",
                    (court_id,),
                ).fetchone()["n"]
                used_other_links += int(ref_count or 0)

            if used_slots > 0 or used_other_links > 0:
                return RedirectResponse(
                    f"/spielfelder?delete_status=blocked&used_games={used_games}&used_slots={used_slots}&used_other_links={used_other_links}",
                    status_code=303,
                )

            conn.execute("DELETE FROM courts WHERE id = ?", (court_id,))
            conn.commit()

        return RedirectResponse("/spielfelder?delete_status=deleted", status_code=303)

    return router