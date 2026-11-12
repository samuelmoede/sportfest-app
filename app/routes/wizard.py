"""Startseite des Einrichtungsassistenten (/assistent) und Weiterleitungen
der frueheren Assistent-Wege.

Der Einrichtungsassistent selbst (Entwuerfe, Vorlagen, Bearbeiten einer
bestehenden Veranstaltung) liegt in app/routes/assistent.py. Die frueheren
Wege - der Assistent je Veranstaltung (/assistent/{event_id}), die Vorlage
Schulpokal, der Schulpokal-Gesamtplan und der Turnier-Schnellstart - sind
entfernt; alte Lesezeichen fuehren per Weiterleitung zum neuen Assistenten
bzw. zur Veranstaltungsseite mit dem Knopf „Plan ändern“."""
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.database import get_conn
from app.services.assistent_entwurf_service import liste_vorlagen, offene_entwuerfe
from app.services.event_status_service import fetch_events_with_competition_counts
from app.web import templates


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/assistent")
    def assistent_start(request: Request):
        with get_conn() as conn:
            events = fetch_events_with_competition_counts(conn, include_archived=False)
            entwuerfe = offene_entwuerfe(conn)
            vorlagen = liste_vorlagen(conn)
            # Bearbeiten-Entwürfe stehen bei ihrer Veranstaltung („Weiter
            # ändern“), die übrigen unter „Entwurf fortsetzen“.
            bearbeiten_entwuerfe = {}
            for entwurf in entwuerfe:
                if entwurf["bestand_event_id"]:
                    bearbeiten_entwuerfe.setdefault(entwurf["bestand_event_id"], entwurf["id"])
            neue_entwuerfe = [e for e in entwuerfe if not e["bestand_event_id"]]
        return templates.TemplateResponse(
            request=request,
            name="assistent_start.html",
            context={
                "entwuerfe": entwuerfe,
                "neue_entwuerfe": neue_entwuerfe,
                "bearbeiten_entwuerfe": bearbeiten_entwuerfe,
                "vorlagen": vorlagen,
                "events": events,
                "meldung": request.query_params.get("meldung", "").strip(),
                "einstieg_fehler": request.query_params.get("einstieg_fehler", "").strip(),
            },
        )

    # Weiterleitungen fuer alte Lesezeichen -------------------------------

    @router.get("/assistent/{event_id:int}")
    @router.get("/assistent/{event_id:int}/schulpokal-plan")
    def alter_assistent_je_veranstaltung(event_id: int):
        return RedirectResponse(f"/events/{event_id}", status_code=303)

    @router.get("/turnier-schnellstart")
    def alter_turnier_schnellstart():
        return RedirectResponse("/assistent", status_code=303)

    return router
