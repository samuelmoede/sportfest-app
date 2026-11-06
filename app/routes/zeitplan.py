"""Zeitplan aufholen (Turnierleitung) und Durchsage an alle, siehe
app/services/zeitplan_aufholen_service.py und durchsage_service.py."""
from typing import Callable
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.database import get_conn
from app.services.durchsage_service import (
    aktuelle_durchsage,
    durchsage_beenden,
    durchsage_senden,
)
from app.services.zeitplan_aufholen_service import (
    berechne_aufholen,
    durchsage_text,
    is_event_today,
    load_block_competitions,
    uebernehmen_aufholen,
)
from app.web import templates


def _int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _spielzeit_sekunden(params):
    """Spielzeit aus Minuten- und Sekundenfeld; None = unveraendert."""
    minuten = _int(params.get("spielzeit_min"))
    sekunden = _int(params.get("spielzeit_sek")) or 0
    if minuten is None:
        return None
    return minuten * 60 + sekunden


def create_router(*, app_now: Callable, app_now_db_timestamp: Callable) -> APIRouter:
    router = APIRouter()

    def _vorschlag(conn, competition_id, params):
        competitions = load_block_competitions(conn, competition_id)
        if not competitions:
            return None
        now = app_now()
        today = is_event_today(conn, competitions[0], now.date())
        return berechne_aufholen(
            conn, competition_id,
            spielzeit_sekunden=_spielzeit_sekunden(params),
            wechselzeit_minuten=_int(params.get("wechselzeit")),
            naechste_runde=(params.get("naechste_runde") or "").strip() or None,
            now=now.replace(tzinfo=None) if today else None,
        )

    @router.get("/zeitplan-aufholen")
    def zeitplan_aufholen(request: Request):
        competition_id = _int(request.query_params.get("competition_id"))
        with get_conn() as conn:
            vorschlag = _vorschlag(conn, competition_id, request.query_params) if competition_id else None
            durchsage = aktuelle_durchsage(conn)
        if vorschlag is None:
            return RedirectResponse("/spielplan-bearbeiten", status_code=303)
        return templates.TemplateResponse(
            request=request, name="zeitplan_aufholen.html",
            context={
                "vorschlag": vorschlag,
                "durchsage_vorschlag": durchsage_text(vorschlag),
                "durchsage": durchsage,
                "gespeichert": _int(request.query_params.get("gespeichert")),
                "gesendet": request.query_params.get("gesendet") == "1",
                "fehler": request.query_params.get("fehler", ""),
            },
        )

    @router.post("/zeitplan-aufholen/uebernehmen")
    async def zeitplan_aufholen_uebernehmen(request: Request):
        form = await request.form()
        competition_id = _int(form.get("competition_id"))
        with get_conn() as conn:
            vorschlag = _vorschlag(conn, competition_id, form) if competition_id else None
            if vorschlag is None:
                return RedirectResponse("/spielplan-bearbeiten", status_code=303)
            query = {"competition_id": competition_id}
            if vorschlag["fehler"]:
                query["fehler"] = " ".join(vorschlag["fehler"])
                return RedirectResponse(f"/zeitplan-aufholen?{urlencode(query)}", status_code=303)
            query["gespeichert"] = uebernehmen_aufholen(conn, vorschlag)
            if form.get("durchsage_senden"):
                try:
                    durchsage_senden(
                        conn, form.get("durchsage_text") or durchsage_text(vorschlag),
                        app_now_db_timestamp(), request.session.get("username") if "session" in request.scope else None,
                    )
                    query["gesendet"] = "1"
                except ValueError as error:
                    query["fehler"] = str(error)
            conn.commit()
        return RedirectResponse(f"/zeitplan-aufholen?{urlencode(query)}", status_code=303)

    @router.get("/durchsage/aktuell")
    def durchsage_aktuell():
        with get_conn() as conn:
            durchsage = aktuelle_durchsage(conn)
        return JSONResponse(durchsage or {}, headers={"Cache-Control": "no-store"})

    @router.post("/durchsage/senden")
    async def durchsage_senden_route(request: Request):
        form = await request.form()
        zurueck = form.get("zurueck") or "/"
        if not str(zurueck).startswith("/") or str(zurueck).startswith("//"):
            zurueck = "/"
        try:
            with get_conn() as conn:
                durchsage_senden(
                    conn, form.get("text"), app_now_db_timestamp(),
                    request.session.get("username") if "session" in request.scope else None,
                )
                conn.commit()
        except ValueError:
            pass
        return RedirectResponse(zurueck, status_code=303)

    @router.post("/durchsage/{durchsage_id}/beenden")
    async def durchsage_beenden_route(request: Request, durchsage_id: int):
        form = await request.form()
        zurueck = form.get("zurueck") or "/"
        if not str(zurueck).startswith("/") or str(zurueck).startswith("//"):
            zurueck = "/"
        with get_conn() as conn:
            durchsage_beenden(conn, durchsage_id)
            conn.commit()
        return RedirectResponse(zurueck, status_code=303)

    return router
