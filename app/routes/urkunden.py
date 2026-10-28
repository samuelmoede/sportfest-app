import re
from datetime import date
from typing import Callable
from urllib.parse import urlencode

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, Response

from app.database import get_conn
from app.services.urkunden_pdf_service import erzeuge_urkunden_pdf, pdf_seite_als_png
from app.services.urkunden_service import (
    JAHRGANGSGRUPPEN,
    LAYOUT_DEFAULTS,
    STANDARD_SCHWELLE,
    URKUNDENTYPEN,
    WERTUNG_DISZIPLIN,
    WERTUNG_GESAMT,
    WERTUNG_WETTBEWERB,
    WERTUNGEN,
    eintraege_aus_disziplinen,
    eintraege_aus_gesamtwertung,
    eintraege_aus_sechskampf,
    eintraege_aus_turnier,
    filtere_eintraege,
    formatiere_datum,
    lade_anpassungen,
    lade_layout,
    lade_logo,
    lade_schwellen,
    loesche_anpassung,
    loesche_logo,
    sortiere_eintraege,
    speichere_anpassung,
    speichere_layout,
    speichere_logo,
    speichere_schwellen,
    wende_anpassungen_an,
)
from app.utils.formatting import slugify_filename_part
from app.web import templates


def _parse_int(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def anker_fuer(key: str):
    return "u-" + re.sub(r"[^A-Za-z0-9_-]+", "-", key or "")


def _sicherer_rueckweg(value: str):
    """Nur Ruecksprung innerhalb von /urkunden (kein offener Redirect)."""
    if value and value.startswith("/urkunden") and not value.startswith("//"):
        return value
    return "/urkunden"


def create_router(
    *,
    app_now_db_timestamp: Callable[[], str],
    calculate_tournament_points: Callable,
    calculate_sixkampf_competition_ranking: Callable,
    calculate_event_overall_ranking: Callable,
) -> APIRouter:
    router = APIRouter()

    def lade_events():
        with get_conn() as conn:
            return conn.execute("""
                SELECT * FROM events
                ORDER BY
                    CASE status WHEN 'aktiv' THEN 0 WHEN 'geplant' THEN 1 ELSE 2 END,
                    event_date DESC, name
            """).fetchall()

    def sammle_eintraege(event, wertung: str):
        heute = formatiere_datum(date.today())
        event_datum = formatiere_datum(event["event_date"]) or heute
        with get_conn() as conn:
            competitions = conn.execute("""
                SELECT * FROM competitions
                WHERE event_id = ?
                ORDER BY jahrgang, name
            """, (event["id"],)).fetchall()
            team_jahrgaenge = {
                row["id"]: row["jahrgang"]
                for row in conn.execute("SELECT id, jahrgang FROM teams").fetchall()
            }

        if wertung == WERTUNG_GESAMT:
            eintraege = eintraege_aus_gesamtwertung(
                calculate_event_overall_ranking(event["id"]), event_datum
            )
        else:
            eintraege = []
            for competition in competitions:
                datum = formatiere_datum(competition["competition_date"]) or event_datum
                if competition["competition_type"] == "Sechskampf":
                    disciplines, ranking = calculate_sixkampf_competition_ranking(competition)
                    if wertung == WERTUNG_DISZIPLIN:
                        eintraege += eintraege_aus_disziplinen(competition, disciplines, ranking, datum)
                    else:
                        eintraege += eintraege_aus_sechskampf(competition, ranking, datum)
                elif wertung == WERTUNG_WETTBEWERB:
                    eintraege += eintraege_aus_turnier(
                        competition, calculate_tournament_points(competition), datum,
                        team_jahrgaenge=team_jahrgaenge,
                    )

        for eintrag in eintraege:
            eintrag["veranstaltung"] = event["name"]
            eintrag["anker"] = anker_fuer(eintrag["key"])
        return wende_anpassungen_an(eintraege, lade_anpassungen(event["id"]), lade_schwellen())

    def lese_auswahl(params):
        events = lade_events()
        event_id = _parse_int(params.get("event_id"))
        event = next((e for e in events if e["id"] == event_id), None)
        if event is None and events:
            event = events[0]
        wertung = params.get("wertung") if params.get("wertung") in WERTUNGEN else WERTUNG_WETTBEWERB
        filter_werte = {
            "jahrgangsgruppe": params.get("jahrgangsgruppe") if params.get("jahrgangsgruppe") in JAHRGANGSGRUPPEN else "",
            "team_id": _parse_int(params.get("team_id")),
            "quelle": params.get("quelle") or "",
            "platz_bis": _parse_int(params.get("platz_bis")),
            "typ": params.get("typ") if params.get("typ") in URKUNDENTYPEN else "",
            "sortierung": "klasse" if params.get("sortierung") == "klasse" else "wertung",
        }
        alle = sammle_eintraege(event, wertung) if event is not None else []
        gefiltert = sortiere_eintraege(filtere_eintraege(
            alle,
            team_id=filter_werte["team_id"],
            jahrgangsgruppe=filter_werte["jahrgangsgruppe"] or None,
            quelle_id=filter_werte["quelle"] or None,
            platz_bis=filter_werte["platz_bis"],
            urkundentyp=filter_werte["typ"] or None,
        ), filter_werte["sortierung"])
        return events, event, wertung, filter_werte, alle, gefiltert

    def query_string(event, wertung, filter_werte):
        values = {"event_id": event["id"] if event else "", "wertung": wertung}
        for key in ("jahrgangsgruppe", "team_id", "quelle", "platz_bis", "typ", "sortierung"):
            value = filter_werte.get(key)
            if value not in (None, ""):
                values[key] = value
        return urlencode(values)

    @router.get("/urkunden")
    def urkunden_seite(request: Request):
        events, event, wertung, filter_werte, alle, gefiltert = lese_auswahl(request.query_params)
        quellen = {}
        teams = {}
        for eintrag in sortiere_eintraege(alle):
            quellen.setdefault(eintrag["quelle_id"], eintrag["quelle_label"])
            teams.setdefault(eintrag["team_id"], eintrag["class_name"])
        qs = query_string(event, wertung, filter_werte)
        vorschau_key = request.query_params.get("vorschau", "")
        vorschau_eintrag = next((e for e in alle if e["key"] == vorschau_key), None)
        if vorschau_eintrag is not None:
            vorschau_url = f"/urkunden/pdf?{qs}&{urlencode({'key': vorschau_key})}"
            vorschau_titel = f"{vorschau_eintrag['recipient_name']} · {vorschau_eintrag['quelle_label']}"
        else:
            vorschau_url = f"/urkunden/pdf?{qs}"
            vorschau_titel = "Sammel-PDF"
        logo_bytes, _ = lade_logo()
        schwellen = lade_schwellen()
        return templates.TemplateResponse(
            request=request, name="urkunden.html",
            context={
                "events": events,
                "event": event,
                "wertung": wertung,
                "wertungen": WERTUNGEN,
                "filter": filter_werte,
                "eintraege": gefiltert,
                "anzahl_druck": sum(1 for e in gefiltert if not e["ausgeblendet"]),
                "quellen": quellen,
                "teams": sorted(teams.items(), key=lambda item: str(item[1]).lower()),
                "jahrgangsgruppen": JAHRGANGSGRUPPEN,
                "urkundentypen": URKUNDENTYPEN,
                "query_string": qs,
                "vorschau_key": vorschau_key if vorschau_eintrag is not None else "",
                "vorschau_url": vorschau_url,
                "vorschau_keys": [e["key"] for e in gefiltert if not e["ausgeblendet"]],
                "vorschau_titel": vorschau_titel,
                "rueckweg": f"/urkunden?{qs}",
                "layout": lade_layout(),
                "layout_defaults": LAYOUT_DEFAULTS,
                "schwellen": schwellen,
                "schwellen_felder": tuple(STANDARD_SCHWELLE),
                "hat_logo": logo_bytes is not None,
                "meldung": request.query_params.get("meldung", ""),
                "fehler": request.query_params.get("fehler", ""),
            },
        )

    @router.get("/urkunden/pdf")
    def urkunden_pdf(request: Request):
        events, event, wertung, filter_werte, alle, gefiltert = lese_auswahl(request.query_params)
        key = request.query_params.get("key")
        if key:
            auswahl = [e for e in alle if e["key"] == key]
        else:
            auswahl = [e for e in gefiltert if not e["ausgeblendet"]]
        logo_bytes, _ = lade_logo()
        pdf = erzeuge_urkunden_pdf(auswahl, lade_layout(), logo_bytes)

        teile = ["urkunden", event["name"] if event else ""]
        if key and auswahl:
            teile += [auswahl[0]["class_name"], auswahl[0]["quelle_label"]]
        else:
            teile.append(WERTUNGEN[wertung])
        dateiname = "_".join(slugify_filename_part(t) for t in teile if t) + ".pdf"
        art = "attachment" if request.query_params.get("download") else "inline"
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'{art}; filename="{dateiname}"'},
        )

    @router.get("/urkunden/vorschau.png")
    def urkunden_vorschau_bild(request: Request):
        """Eine Urkunde (key) als Bild, fuer die Vorschau auch auf Handys."""
        _, _, _, _, alle, _ = lese_auswahl(request.query_params)
        key = request.query_params.get("key")
        auswahl = [e for e in alle if e["key"] == key]
        if not auswahl:
            return Response(status_code=404)
        logo_bytes, _ = lade_logo()
        pdf = erzeuge_urkunden_pdf(auswahl, lade_layout(), logo_bytes)
        return Response(
            content=pdf_seite_als_png(pdf),
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/urkunden/anpassung")
    def urkunden_anpassung(
        event_id: int = Form(...),
        key: str = Form(...),
        platz: str = Form(""),
        urkundentyp: str = Form(""),
        name: str = Form(""),
        leistung: str = Form(""),
        drucken: str = Form(""),
        rueckweg: str = Form("/urkunden"),
    ):
        speichere_anpassung(
            event_id, key,
            platz=platz, urkundentyp=urkundentyp, name=name, leistung=leistung,
            ausgeblendet=not drucken, now=app_now_db_timestamp(),
        )
        ziel = _sicherer_rueckweg(rueckweg)
        trenner = "&" if "?" in ziel else "?"
        # Nach dem Speichern zeigt die Vorschau direkt die geaenderte Urkunde.
        return RedirectResponse(
            f"{ziel}{trenner}{urlencode({'vorschau': key})}#{anker_fuer(key)}", status_code=303
        )

    @router.post("/urkunden/anpassung/zuruecksetzen")
    def urkunden_anpassung_zuruecksetzen(
        event_id: int = Form(...),
        key: str = Form(""),
        rueckweg: str = Form("/urkunden"),
    ):
        loesche_anpassung(event_id, key or None)
        ziel = _sicherer_rueckweg(rueckweg)
        if not key:
            return RedirectResponse(ziel, status_code=303)
        trenner = "&" if "?" in ziel else "?"
        return RedirectResponse(
            f"{ziel}{trenner}{urlencode({'vorschau': key})}#{anker_fuer(key)}", status_code=303
        )

    @router.post("/urkunden/einstellungen")
    async def urkunden_einstellungen(request: Request):
        form = await request.form()
        speichere_layout({key: form.get(key, "") for key in LAYOUT_DEFAULTS})
        schwellen = {"standard": {f: form.get(f"standard_{f}", "") for f in STANDARD_SCHWELLE}}
        for index, gruppe in enumerate(JAHRGANGSGRUPPEN):
            schwellen[gruppe] = {f: form.get(f"gruppe{index}_{f}", "") for f in STANDARD_SCHWELLE}
        speichere_schwellen(schwellen)
        rueckweg = _sicherer_rueckweg(str(form.get("rueckweg", "")))
        trenner = "&" if "?" in rueckweg else "?"
        return RedirectResponse(f"{rueckweg}{trenner}meldung=Einstellungen+gespeichert", status_code=303)

    @router.post("/urkunden/logo")
    async def urkunden_logo_hochladen(
        logo: UploadFile = File(...),
        rueckweg: str = Form("/urkunden"),
    ):
        data = await logo.read()
        ziel = _sicherer_rueckweg(rueckweg)
        trenner = "&" if "?" in ziel else "?"
        try:
            speichere_logo(data)
        except ValueError as error:
            return RedirectResponse(f"{ziel}{trenner}{urlencode({'fehler': str(error)})}", status_code=303)
        return RedirectResponse(f"{ziel}{trenner}meldung=Logo+gespeichert", status_code=303)

    @router.post("/urkunden/logo/loeschen")
    def urkunden_logo_loeschen(rueckweg: str = Form("/urkunden")):
        loesche_logo()
        return RedirectResponse(_sicherer_rueckweg(rueckweg), status_code=303)

    @router.get("/urkunden/logo")
    def urkunden_logo():
        data, mime = lade_logo()
        if data is None:
            return Response(status_code=404)
        return Response(content=data, media_type=mime)

    return router
