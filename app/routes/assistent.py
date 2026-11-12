"""Neuer Einrichtungsassistent (Entwurf -> Vorschau -> Uebernehmen).

Alle Seiten liegen unter /assistent und damit im bestehenden Bereichsschutz
fuer Admins. Die Startseite /assistent und die Weiterleitungen der
frueheren Assistent-Wege liegen in app/routes/wizard.py."""
import json
from typing import List
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.database import get_conn
from app.routes.events import EVENT_TYPES
from app.routes.teams import normalize_jahrgang
from app.services import assistent_entwurf_service as es
from app.services.assistent_planung_service import MODI, plane
from app.services.schedule_location_service import COMPETITION_LOCATIONS, normalize_competition_location
from app.services.tiebreak_service import (
    DEFAULT_TIEBREAK_ORDER,
    TIEBREAK_KRITERIEN,
    describe_tiebreak_order,
    parse_tiebreak_order,
)
from app.web import templates

VORSCHAU_SCHRITTE = ("vorschau", "wertung", "abschluss")


def _redirect(url, **params):
    params = {k: v for k, v in params.items() if v not in (None, "")}
    return RedirectResponse(url + ("?" + urlencode(params) if params else ""), status_code=303)


def _schritt_url(entwurf_id, schritt):
    return f"/assistent/entwurf/{entwurf_id}/{schritt}"


def _naechster(schritt, richtung):
    keys = list(es.SCHRITT_KEYS)
    if schritt not in keys:
        return keys[0]
    index = keys.index(schritt) + richtung
    return keys[max(0, min(index, len(keys) - 1))]


def _wettbewerbs_modus_label(daten, wb, plan_wb=None):
    if wb["einstellungen"]["art"] == "Sechskampf":
        return "Sechskampf"
    modus = (plan_wb or wb).get("modus") or wb["einstellungen"].get("modus")
    if modus in MODI:
        return MODI[modus]
    vorschlag = (plan_wb or {}).get("modus_vorschlag")
    return f"Vorschlag: {MODI[vorschlag]}" if vorschlag in MODI else "automatisch"


def _wertung_text(daten, wb):
    if wb["einstellungen"]["art"] == "Sechskampf":
        return "Platzierung aus den Disziplinen, Platzierungspunkte in die Gesamtwertung"
    if es.ist_schulpokal(daten):
        return "Spielpunkte zählen für die Jahrgangswertung (Summe aller Sportarten des Jahrgangs)"
    return "Platzierungspunkte (1. Platz = Anzahl Teams, dann absteigend) in die Gesamtwertung"


def create_router() -> APIRouter:
    router = APIRouter()

    def lade(conn, entwurf_id):
        return es.lade_entwurf(conn, entwurf_id)

    def render(request, entwurf, schritt, **extra):
        with get_conn() as conn:
            stamm = es.lade_stammdaten(conn)
            daten = entwurf["daten"]
            plan = None
            if schritt in VORSCHAU_SCHRITTE or schritt == "zeitplan":
                plan = plane(conn, daten, stamm)
        wettbewerbe = es.ableiten_wettbewerbe(daten, stamm)
        plan_wbs = {wb["key"]: wb for wb in (plan or {}).get("wettbewerbe", [])}
        teams_by_jahrgang = es.teams_nach_jahrgang(stamm)
        verfuegbar = es.sortiere_jahrgaenge(
            list(teams_by_jahrgang.keys()) + list(daten.get("jahrgaenge") or [])
        )
        orte = []
        for ort in es.planbare_orte():
            info = es.ort_einstellung(daten, stamm, ort)
            orte.append({
                "name": ort, **info,
                "courts": es.felder_am_ort(stamm, ort, nur_aktive=False),
            })
        wb_index = 0
        try:
            wb_index = int(request.query_params.get("wb", "0"))
        except ValueError:
            wb_index = 0
        context = {
            "entwurf": entwurf,
            "daten": daten,
            "schritt": schritt,
            "schritte": es.SCHRITTE,
            "experte": daten.get("modus") == "experte",
            "stamm": stamm,
            "wettbewerbe": wettbewerbe,
            "plan": plan,
            "plan_wbs": plan_wbs,
            "teams_by_jahrgang": teams_by_jahrgang,
            "verfuegbare_jahrgaenge": verfuegbar,
            "orte": orte,
            "alle_orte": [o for o in COMPETITION_LOCATIONS if o in es.planbare_orte()],
            "event_types": EVENT_TYPES,
            "modi": MODI,
            "ablaeufe": es.ABLAEUFE,
            "programm_arten": es.PROGRAMMPUNKT_ARTEN,
            "wettbewerb_arten": es.WETTBEWERB_ARTEN,
            "tiebreak_kriterien": TIEBREAK_KRITERIEN,
            "default_tiebreak": list(DEFAULT_TIEBREAK_ORDER),
            "parse_tiebreak_order": parse_tiebreak_order,
            "describe_tiebreak_order": describe_tiebreak_order,
            "modus_label": lambda wb: _wettbewerbs_modus_label(daten, wb, plan_wbs.get(wb["key"])),
            "wertung_text": lambda wb: _wertung_text(daten, wb),
            "ist_schulpokal": es.ist_schulpokal(daten),
            "wb_index": wb_index,
            "fehler": request.query_params.get("fehler", ""),
            "meldung": request.query_params.get("meldung", ""),
            "json": json,
        }
        context.update(extra)
        return templates.TemplateResponse(request=request, name="assistent_entwurf.html", context=context)

    # ------------------------------------------------------------------
    # Start, Entwurf anlegen, Vorlagen
    # ------------------------------------------------------------------

    @router.post("/assistent/entwurf/neu")
    def entwurf_neu():
        with get_conn() as conn:
            entwurf_id = es.erstelle_entwurf(conn, es.neue_daten())
            conn.commit()
        return _redirect(_schritt_url(entwurf_id, "grunddaten"))

    @router.post("/assistent/entwurf/aus-vorlage")
    def entwurf_aus_vorlage(vorlage: str = Form(...)):
        with get_conn() as conn:
            try:
                vorlage_daten, name = es.lade_vorlage(conn, vorlage)
            except ValueError as error:
                return _redirect("/assistent", einstieg_fehler=str(error))
            daten = es.entwurf_aus_vorlage(vorlage_daten, es.lade_stammdaten(conn), vorlage, name)
            entwurf_id = es.erstelle_entwurf(conn, daten)
            conn.commit()
        return _redirect(_schritt_url(entwurf_id, "abgleich"))

    @router.post("/assistent/entwurf/start")
    def entwurf_start(start: str = Form("leer")):
        """Kachel „Neue Veranstaltung“: ein Startpunkt für alles Neue –
        leer, Vorlage oder wie eine frühere Veranstaltung."""
        if start.startswith("event:"):
            try:
                event_id = int(start.partition(":")[2])
            except ValueError:
                return _redirect("/assistent", einstieg_fehler="Unbekannte Veranstaltung.")
            return _vorjahr(event_id)
        if start == "leer":
            return entwurf_neu()
        return entwurf_aus_vorlage(start)

    def _vorjahr(event_id):
        with get_conn() as conn:
            try:
                daten = es.entwurf_aus_vorjahr(conn, event_id, es.lade_stammdaten(conn))
            except ValueError as error:
                return _redirect("/assistent", einstieg_fehler=str(error))
            entwurf_id = es.erstelle_entwurf(conn, daten)
            conn.commit()
        return _redirect(_schritt_url(entwurf_id, "abgleich"))

    @router.post("/assistent/entwurf/bearbeiten/{event_id}")
    def entwurf_bearbeiten(event_id: int):
        """Bestehende Veranstaltung im Assistenten öffnen. Ein schon offener
        Bearbeiten-Entwurf derselben Veranstaltung wird weiterverwendet."""
        with get_conn() as conn:
            entwurf_id = es.offener_entwurf_fuer(conn, event_id)
            if entwurf_id is None:
                try:
                    daten = es.entwurf_aus_veranstaltung(conn, event_id, es.lade_stammdaten(conn))
                except ValueError as error:
                    return _redirect("/assistent", einstieg_fehler=str(error))
                entwurf_id = es.erstelle_entwurf(conn, daten)
                conn.commit()
        return _redirect(f"/assistent/entwurf/{entwurf_id}")

    @router.post("/assistent/entwurf/bestehend")
    def entwurf_bestehend(event_id: int = Form(...), aktion: str = Form("vorjahr")):
        """Ältere Formularform (PR #170): bearbeiten oder als Muster nutzen."""
        if aktion == "bearbeiten":
            return entwurf_bearbeiten(event_id)
        return _vorjahr(event_id)

    @router.get("/assistent/vorlagen")
    def vorlagen(request: Request):
        with get_conn() as conn:
            liste = es.liste_vorlagen(conn)
            events = [dict(row) for row in conn.execute(
                "SELECT id, name, event_date FROM events WHERE status != 'archiviert' ORDER BY event_date DESC, name"
            ).fetchall()]
        return templates.TemplateResponse(
            request=request, name="assistent_vorlagen.html",
            context={
                "vorlagen": liste, "events": events,
                "fehler": request.query_params.get("fehler", ""),
                "meldung": request.query_params.get("meldung", ""),
            },
        )

    @router.post("/assistent/vorlagen/{vorlage_id}/umbenennen")
    def vorlage_umbenennen(vorlage_id: int, name: str = Form("")):
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM assistent_vorlagen WHERE id = ?", (vorlage_id,)).fetchone()
            if row is None:
                return _redirect("/assistent/vorlagen", fehler="Vorlage nicht gefunden.")
            try:
                es.speichere_vorlage(conn, name, json.loads(row["daten"]), vorlage_id)
            except ValueError as error:
                return _redirect("/assistent/vorlagen", fehler=str(error))
            conn.commit()
        return _redirect("/assistent/vorlagen", meldung="Vorlage umbenannt.")

    @router.post("/assistent/vorlagen/kopieren")
    def vorlage_kopieren(vorlage: str = Form(...), name: str = Form("")):
        with get_conn() as conn:
            try:
                daten, alter_name = es.lade_vorlage(conn, vorlage)
                es.speichere_vorlage(conn, name.strip() or f"{alter_name} (Kopie)", daten)
            except ValueError as error:
                return _redirect("/assistent/vorlagen", fehler=str(error))
            conn.commit()
        return _redirect("/assistent/vorlagen", meldung="Vorlage kopiert.")

    @router.post("/assistent/vorlagen/{vorlage_id}/loeschen")
    def vorlage_loeschen(vorlage_id: int, bestaetigt: str = Form("")):
        if bestaetigt != "1":
            return _redirect("/assistent/vorlagen", fehler="Bitte das Löschen ausdrücklich bestätigen.")
        with get_conn() as conn:
            conn.execute("DELETE FROM assistent_vorlagen WHERE id = ?", (vorlage_id,))
            conn.commit()
        return _redirect("/assistent/vorlagen", meldung="Vorlage gelöscht.")

    @router.post("/assistent/vorlagen/{vorlage_id}/bearbeiten")
    def vorlage_bearbeiten(vorlage_id: int):
        """Vorlage im Assistenten bearbeiten: ein Entwurf mit Ziel Vorlage,
        der am Ende die Vorlage speichert statt eine Veranstaltung anzulegen."""
        with get_conn() as conn:
            try:
                vorlage_daten, name = es.lade_vorlage(conn, f"vorlage:{vorlage_id}")
            except ValueError as error:
                return _redirect("/assistent/vorlagen", fehler=str(error))
            daten = es.entwurf_aus_vorlage(
                vorlage_daten, es.lade_stammdaten(conn), f"vorlage:{vorlage_id}", name
            )
            daten["abgleich"]["offen"] = False
            daten["ziel"] = f"vorlage:{vorlage_id}"
            daten["grunddaten"]["name"] = name
            entwurf_id = es.erstelle_entwurf(conn, daten)
            conn.commit()
        return _redirect(_schritt_url(entwurf_id, "grunddaten"))

    @router.post("/assistent/vorlagen/aus-veranstaltung")
    def vorlage_aus_veranstaltung_auswahl(event_id: int = Form(...), name: str = Form("")):
        return vorlage_aus_veranstaltung(event_id, name)

    @router.post("/assistent/vorlagen/aus-veranstaltung/{event_id}")
    def vorlage_aus_veranstaltung(event_id: int, name: str = Form("")):
        with get_conn() as conn:
            try:
                vorlage = es.vorlage_aus_veranstaltung(conn, event_id)
                event = conn.execute("SELECT name FROM events WHERE id = ?", (event_id,)).fetchone()
                es.speichere_vorlage(conn, name.strip() or f"Vorlage {event['name']}", vorlage)
            except ValueError as error:
                return _redirect("/assistent/vorlagen", fehler=str(error))
            conn.commit()
        return _redirect("/assistent/vorlagen", meldung="Veranstaltung als Vorlage gespeichert.")

    # ------------------------------------------------------------------
    # Entwurf: Schritte
    # ------------------------------------------------------------------

    @router.post("/assistent/entwurf/{entwurf_id}/loeschen")
    def entwurf_loeschen(entwurf_id: int, bestaetigt: str = Form("")):
        if bestaetigt != "1":
            return _redirect(_schritt_url(entwurf_id, "abschluss"), fehler="Bitte das Verwerfen bestätigen.")
        with get_conn() as conn:
            conn.execute("DELETE FROM assistent_entwuerfe WHERE id = ? AND status = 'offen'", (entwurf_id,))
            conn.commit()
        return _redirect("/assistent", meldung="Entwurf verworfen.")

    @router.get("/assistent/entwurf/{entwurf_id}")
    def entwurf_fortsetzen(entwurf_id: int):
        with get_conn() as conn:
            entwurf = lade(conn, entwurf_id)
        if entwurf is None:
            return _redirect("/assistent")
        if entwurf["status"] == "uebernommen":
            return _redirect(_schritt_url(entwurf_id, "fertig"))
        daten = entwurf["daten"]
        if (daten.get("abgleich") or {}).get("offen"):
            return _redirect(_schritt_url(entwurf_id, "abgleich"))
        schritt = daten.get("schritt") if daten.get("schritt") in es.SCHRITT_KEYS else "grunddaten"
        return _redirect(_schritt_url(entwurf_id, schritt))

    @router.get("/assistent/entwurf/{entwurf_id}/fertig")
    def entwurf_fertig(request: Request, entwurf_id: int):
        with get_conn() as conn:
            entwurf = lade(conn, entwurf_id)
            if entwurf is None:
                return _redirect("/assistent")
            event = None
            competitions = []
            if entwurf["event_id"]:
                event = conn.execute("SELECT * FROM events WHERE id = ?", (entwurf["event_id"],)).fetchone()
                competitions = conn.execute(
                    "SELECT id, name FROM competitions WHERE event_id = ? ORDER BY start_time, name",
                    (entwurf["event_id"],),
                ).fetchall()
        return templates.TemplateResponse(
            request=request, name="assistent_fertig.html",
            context={"entwurf": entwurf, "event": event, "competitions": competitions,
                     "meldung": request.query_params.get("meldung", ""),
                     "fehler": request.query_params.get("fehler", "")},
        )

    def speichere_schritt(conn, daten, stamm, schritt, form):
        modus = form.get("modus")
        if modus in ("schnell", "experte"):
            daten["modus"] = modus
        if schritt == "abgleich":
            return _speichere_abgleich(daten, form)
        if schritt == "grunddaten":
            return _speichere_grunddaten(conn, daten, stamm, form)
        if schritt == "sportarten":
            return _speichere_sportarten(daten, stamm, form)
        if schritt == "orte":
            return _speichere_orte(conn, daten, stamm, form)
        if schritt == "zeitplan":
            return _speichere_zeitplan(daten, stamm, form)
        if schritt == "wertung":
            return _speichere_wertung(daten, stamm, form)
        return ""

    def _speichere_abgleich(daten, form):
        grunddaten = daten["grunddaten"]
        grunddaten["name"] = (form.get("name") or "").strip()
        grunddaten["datum"] = (form.get("datum") or "").strip()
        if not grunddaten["datum"]:
            raise ValueError("Bitte das Datum der Veranstaltung angeben.")
        daten["jahrgaenge"] = es.sortiere_jahrgaenge(form.getlist("jahrgaenge"))
        aktive = set(form.getlist("sportarten"))
        daten["sportarten"] = [s for s in daten.get("sportarten") or [] if s in aktive]
        for ort in es.planbare_orte():
            if form.get(f"ort_gesendet_{ort}"):
                daten.setdefault("orte", {}).setdefault(ort, {})["aktiv"] = bool(form.get(f"ort_aktiv_{ort}"))
        daten["abgleich"]["offen"] = False
        return "Abgleich gespeichert."

    def _speichere_grunddaten(conn, daten, stamm, form):
        if form.get("aktion") == "team_anlegen":
            name = (form.get("team_name") or "").strip()
            jahrgang = normalize_jahrgang(form.get("team_jahrgang"))
            if not name or jahrgang is None:
                raise ValueError("Bitte Name und Jahrgang der Klasse angeben.")
            if conn.execute("SELECT 1 FROM teams WHERE name = ?", (name,)).fetchone():
                raise ValueError("Eine Klasse mit diesem Namen gibt es schon.")
            conn.execute("INSERT INTO teams (name, jahrgang, active) VALUES (?, ?, 1)", (name, jahrgang))
            jahrgaenge = list(daten.get("jahrgaenge") or [])
            jahrgaenge.append(str(jahrgang))
            daten["jahrgaenge"] = es.sortiere_jahrgaenge(jahrgaenge)
            return f"Klasse {name} angelegt."
        grunddaten = daten["grunddaten"]
        grunddaten["name"] = (form.get("name") or "").strip()
        grunddaten["datum"] = (form.get("datum") or "").strip()
        event_type = form.get("event_type") or es.DEFAULT_EVENT_TYPE
        grunddaten["event_type"] = event_type if event_type in EVENT_TYPES else es.DEFAULT_EVENT_TYPE
        grunddaten["start"] = es.uhrzeit(form.get("start")) or grunddaten.get("start") or es.DEFAULT_START
        grunddaten["ende"] = es.uhrzeit(form.get("ende")) or grunddaten.get("ende") or es.DEFAULT_ENDE
        if form.get("jahrgaenge_gesendet"):
            daten["jahrgaenge"] = es.sortiere_jahrgaenge(form.getlist("jahrgaenge"))
        if form.get("teams_gesendet"):
            gezeigt = {int(t) for t in form.getlist("teams_gezeigt") if str(t).isdigit()}
            aktiv = {int(t) for t in form.getlist("teams_aktiv") if str(t).isdigit()}
            bisher = {int(t) for t in daten.get("teams_abgewaehlt") or [] if str(t).isdigit()}
            daten["teams_abgewaehlt"] = sorted((bisher - gezeigt) | (gezeigt - aktiv))
        return ""

    def _speichere_sportarten(daten, stamm, form):
        sportarten = list(daten.get("sportarten") or [])
        count = int(form.get("sportart_anzahl") or 0)
        matrix = daten.setdefault("matrix", {})
        for index in range(count):
            sportart = form.get(f"sportart_{index}")
            if sportart is None:
                continue
            matrix[sportart] = es.sortiere_jahrgaenge(form.getlist(f"matrix_{index}"))
        neue = (form.get("neue_sportart") or "").strip()
        meldung = ""
        if neue and neue.casefold() not in {s.casefold() for s in sportarten}:
            sportarten.append(neue)
            matrix.setdefault(neue, [])
            meldung = f"Sportart {neue} ergänzt."
        entfernen = form.get("sportart_entfernen")
        if entfernen:
            sportarten = [s for s in sportarten if s != entfernen]
            meldung = f"Sportart {entfernen} entfernt."
        daten["sportarten"] = sportarten
        aktion = form.get("aktion") or ""
        if aktion == "zusammenlegen":
            keys = form.getlist("auswahl")
            sports = {k.partition("|")[0] for k in keys}
            if len(sports) != 1:
                raise ValueError("Bitte mindestens zwei Wettbewerbe derselben Sportart auswählen.")
            es.zusammenlegen(daten, sports.pop(), keys)
            meldung = "Wettbewerbe zusammengelegt: gemeinsame Begegnungen und ein gemeinsamer Sieger."
        elif aktion.startswith("trennen:"):
            es.trennen(daten, aktion.partition(":")[2])
            meldung = "Zusammenlegung aufgehoben: wieder getrennte Wettbewerbe mit eigenen Siegern."
        return meldung

    def _speichere_orte(conn, daten, stamm, form):
        if form.get("aktion") == "feld_anlegen":
            name = (form.get("feld_name") or "").strip()
            ort = normalize_competition_location(form.get("feld_ort"))
            if not name or not ort:
                raise ValueError("Bitte Name und Ort des Feldes angeben.")
            if conn.execute("SELECT 1 FROM courts WHERE name = ?", (name,)).fetchone():
                raise ValueError("Ein Feld mit diesem Namen gibt es schon.")
            court_id = conn.execute(
                "INSERT INTO courts (name, location, active) VALUES (?, ?, 1)", (name, ort)
            ).lastrowid
            eintrag = daten.setdefault("orte", {}).setdefault(ort, {})
            if eintrag.get("felder") is not None:
                eintrag["felder"].append(court_id)
            return f"Feld {name} angelegt."
        for ort in es.planbare_orte():
            if not form.get(f"ort_gesendet_{ort}"):
                continue
            eintrag = daten.setdefault("orte", {}).setdefault(ort, {})
            eintrag["aktiv"] = bool(form.get(f"ort_aktiv_{ort}"))
            eintrag["felder"] = [int(v) for v in form.getlist(f"ort_felder_{ort}") if str(v).isdigit()]
        count = int(form.get("wb_anzahl") or 0)
        for index in range(count):
            key = form.get(f"wb_key_{index}")
            if not key:
                continue
            einstellung = daten.setdefault("wettbewerbe", {}).setdefault(key, {})
            ort = normalize_competition_location(form.get(f"wb_ort_{index}"))
            bisher = es.wettbewerb_einstellungen(daten, stamm, key, key.partition("|")[0])["ort"]
            if ort and ort != bisher:
                if any(key in (b.get("wettbewerbe") or []) for b in daten.get("zeitbloecke") or []):
                    es.wende_vorschlag_an(daten, "wb_ort", {"wb": key, "ort": ort})
                else:
                    einstellung["ort"] = ort
                    einstellung["felder"] = []
            if form.get(f"wb_erweitert_{index}"):
                einstellung["felder"] = [int(v) for v in form.getlist(f"wb_felder_{index}") if str(v).isdigit()]
                art = form.get(f"wb_art_{index}")
                einstellung["art"] = art if art in es.WETTBEWERB_ARTEN else "Turnier"
                einstellung["teilmannschaften"] = (form.get(f"wb_teile_{index}") or "").strip()
                einstellung["disziplinen"] = (form.get(f"wb_disziplinen_{index}") or "").strip()
        return ""

    def _speichere_zeitplan(daten, stamm, form):
        aktion = form.get("aktion") or ""
        bloecke = []
        for block_id in form.getlist("block_id"):
            block = next((b for b in daten.get("zeitbloecke") or [] if b.get("id") == block_id), None)
            if block is None:
                continue
            block["ort"] = normalize_competition_location(form.get(f"block_ort_{block_id}")) or block["ort"]
            block["start"] = es.uhrzeit(form.get(f"block_start_{block_id}")) or block.get("start")
            block["ende"] = es.uhrzeit(form.get(f"block_ende_{block_id}")) or block.get("ende")
            block["wettbewerbe"] = form.getlist(f"block_wb_{block_id}")
            if form.get(f"block_ablauf_{block_id}") in es.ABLAEUFE:
                block["ablauf"] = form.get(f"block_ablauf_{block_id}")
            block["spielzeit"] = es.zahl(form.get(f"block_spielzeit_{block_id}"), block.get("spielzeit") or 7, 1, 240)
            block["wechselzeit"] = es.zahl(form.get(f"block_wechselzeit_{block_id}"), block.get("wechselzeit") or 0, 0, 120)
            bloecke.append(block)
        if form.get("bloecke_gesendet"):
            # Ein Wettbewerb gehoert zu genau einem Zeitblock.
            gesehen = set()
            for block in bloecke:
                block["wettbewerbe"] = [k for k in block["wettbewerbe"] if not (k in gesehen or gesehen.add(k))]
            daten["zeitbloecke"] = bloecke
        punkte = []
        for punkt_id in form.getlist("pp_id"):
            punkte.append({
                "id": punkt_id,
                "titel": (form.get(f"pp_titel_{punkt_id}") or "").strip(),
                "art": form.get(f"pp_art_{punkt_id}") if form.get(f"pp_art_{punkt_id}") in es.PROGRAMMPUNKT_ARTEN else "sonstiges",
                "start": es.uhrzeit(form.get(f"pp_start_{punkt_id}")) or "",
                "ende": es.uhrzeit(form.get(f"pp_ende_{punkt_id}")) or "",
                "jahrgaenge": es.sortiere_jahrgaenge(form.getlist(f"pp_jg_{punkt_id}")),
            })
        if form.get("punkte_gesendet"):
            daten["programmpunkte"] = punkte
        shows = []
        for show_id in form.getlist("show_id"):
            shows.append({
                "id": show_id,
                "wettbewerb_a": form.get(f"show_a_{show_id}") or "",
                "wettbewerb_b": form.get(f"show_b_{show_id}") or "",
                "start": es.uhrzeit(form.get(f"show_start_{show_id}")) or "",
            })
        if form.get("shows_gesendet"):
            daten["showspiele"] = shows
        count = int(form.get("wb_anzahl") or 0)
        for index in range(count):
            key = form.get(f"wb_key_{index}")
            if not key or form.get(f"wb_modus_{index}") is None:
                continue
            einstellung = daten.setdefault("wettbewerbe", {}).setdefault(key, {})
            neu = form.get(f"wb_modus_{index}") or None
            neu = neu if neu in MODI else None
            if neu != einstellung.get("modus"):
                einstellung["modus"] = neu
                einstellung["modus_quelle"] = "nutzer" if neu else None
            einstellung["spiele_pro_team"] = es.zahl(form.get(f"wb_spt_{index}"), einstellung.get("spiele_pro_team") or 2, 1, 20)
        if aktion == "block_neu":
            es.neuer_block(daten, normalize_competition_location(form.get("neuer_block_ort")) or "Turnhalle")
            return "Zeitblock ergänzt."
        if aktion.startswith("block_loeschen:"):
            block_id = aktion.partition(":")[2]
            daten["zeitbloecke"] = [b for b in daten["zeitbloecke"] if b.get("id") != block_id]
            return "Zeitblock entfernt."
        if aktion == "punkt_neu":
            art = form.get("neuer_punkt_art") if form.get("neuer_punkt_art") in es.PROGRAMMPUNKT_ARTEN else "pause"
            daten["programmpunkte"].append({
                "id": es.naechste_id(daten["programmpunkte"], "p"), "titel": es.PROGRAMMPUNKT_ARTEN[art],
                "art": art, "start": daten["grunddaten"].get("start") or "",
                "ende": es.uhr((es.minuten(daten["grunddaten"].get("start")) or 480) + 10),
                "jahrgaenge": [],
            })
            return "Programmpunkt ergänzt. Bitte Uhrzeit prüfen."
        if aktion.startswith("punkt_loeschen:"):
            punkt_id = aktion.partition(":")[2]
            daten["programmpunkte"] = [p for p in daten["programmpunkte"] if p.get("id") != punkt_id]
            return "Programmpunkt entfernt."
        if aktion == "show_neu":
            daten["showspiele"].append({
                "id": es.naechste_id(daten["showspiele"], "s"), "wettbewerb_a": "", "wettbewerb_b": "",
                "start": "",
            })
            return "Showspiel ergänzt. Bitte Wettbewerbe und Uhrzeit wählen."
        if aktion.startswith("show_loeschen:"):
            show_id = aktion.partition(":")[2]
            daten["showspiele"] = [s for s in daten["showspiele"] if s.get("id") != show_id]
            return "Showspiel entfernt."
        return ""

    def _speichere_wertung(daten, stamm, form):
        count = int(form.get("wb_anzahl") or 0)
        for index in range(count):
            key = form.get(f"wb_key_{index}")
            if not key:
                continue
            einstellung = daten.setdefault("wettbewerbe", {}).setdefault(key, {})
            if form.get(f"wb_sieg_{index}") is not None:
                einstellung["punkte_sieg"] = es.zahl(form.get(f"wb_sieg_{index}"), 3, 0, 100)
                einstellung["punkte_unentschieden"] = es.zahl(form.get(f"wb_remis_{index}"), 1, 0, 100)
            if form.get(f"wb_tb_{index}") is not None:
                order = parse_tiebreak_order(form.get(f"wb_tb_{index}"))
                einstellung["tiebreak"] = None if order == list(DEFAULT_TIEBREAK_ORDER) else order
        return ""

    # ------------------------------------------------------------------
    # Vorschau-Aktionen
    # ------------------------------------------------------------------

    def _aktion(entwurf_id, aenderung, zurueck="vorschau", wb=None):
        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            entwurf = lade(conn, entwurf_id)
            if entwurf is None or entwurf["status"] != "offen":
                conn.rollback()
                return _redirect(f"/assistent/entwurf/{entwurf_id}")
            daten = entwurf["daten"]
            stamm = es.lade_stammdaten(conn)
            vorher = plane(conn, daten, stamm)
            try:
                meldung = aenderung(conn, daten, stamm, vorher)
            except ValueError as error:
                conn.rollback()
                return _redirect(_schritt_url(entwurf_id, zurueck), fehler=str(error), wb=wb)
            nachher = plane(conn, daten, stamm)
            auswirkungen = _auswirkungen(vorher, nachher)
            if auswirkungen:
                meldung = f"{meldung} {auswirkungen}".strip()
            es.speichere_entwurf(conn, entwurf_id, daten)
            conn.commit()
        return _redirect(_schritt_url(entwurf_id, zurueck), meldung=meldung, wb=wb)

    @router.post("/assistent/entwurf/{entwurf_id}/hinweis/bestaetigen")
    def hinweis_bestaetigen(entwurf_id: int, kennung: str = Form(...), wb: str = Form("")):
        def aenderung(conn, daten, stamm, vorher):
            hinweis = next((h for h in vorher["hinweise"] if h["id"] == kennung), None)
            if hinweis is None or not hinweis["bestaetigbar"]:
                raise ValueError("Dieser Hinweis kann nicht bestätigt werden.")
            liste = daten.setdefault("akzeptierte_hinweise", [])
            if kennung not in liste:
                liste.append(kennung)
            return f"Bewusst zugelassen: {hinweis['titel']}."
        return _aktion(entwurf_id, aenderung, wb=wb)

    @router.post("/assistent/entwurf/{entwurf_id}/hinweis/zuruecknehmen")
    def hinweis_zuruecknehmen(entwurf_id: int, kennung: str = Form(...), wb: str = Form("")):
        def aenderung(conn, daten, stamm, vorher):
            daten["akzeptierte_hinweise"] = [k for k in daten.get("akzeptierte_hinweise") or [] if k != kennung]
            return "Bestätigung zurückgenommen."
        return _aktion(entwurf_id, aenderung, wb=wb)

    @router.post("/assistent/entwurf/{entwurf_id}/vorschlag")
    def vorschlag_anwenden(entwurf_id: int, aktion: str = Form(...), werte: str = Form("{}"), wb: str = Form("")):
        def aenderung(conn, daten, stamm, vorher):
            try:
                parsed = json.loads(werte)
            except ValueError:
                parsed = {}
            return es.wende_vorschlag_an(daten, aktion, parsed)
        return _aktion(entwurf_id, aenderung, wb=wb)

    @router.post("/assistent/entwurf/{entwurf_id}/bearbeiten")
    async def bearbeiten(request: Request, entwurf_id: int):
        """Feinjustierung aus dem Dialog der Vorschau: minutengenaue Werte
        fuer Zeitblock und Wettbewerb, danach neu berechnet."""
        form = await request.form()
        wb_key = form.get("wb_key") or ""
        block_id = form.get("block_id") or ""

        def aenderung(conn, daten, stamm, vorher):
            if block_id:
                block = es._block(daten, block_id)
                start = es.uhrzeit(form.get("start"))
                ende = es.uhrzeit(form.get("ende"))
                if not start or not ende or es.minuten(ende) <= es.minuten(start):
                    raise ValueError("Bitte gültige Uhrzeiten angeben (Ende nach Beginn).")
                block["start"], block["ende"] = start, ende
                block["spielzeit"] = es.zahl(form.get("spielzeit"), block.get("spielzeit"), 1, 240)
                block["wechselzeit"] = es.zahl(form.get("wechselzeit"), block.get("wechselzeit"), 0, 120)
                if form.get("ablauf") in es.ABLAEUFE:
                    block["ablauf"] = form.get("ablauf")
            if wb_key:
                einstellung = daten.setdefault("wettbewerbe", {}).setdefault(wb_key, {})
                if form.get("felder_gesendet"):
                    einstellung["felder"] = [int(v) for v in form.getlist("felder") if str(v).isdigit()]
                modus = form.get("modus")
                if modus is not None:
                    modus = modus if modus in MODI else None
                    if modus != einstellung.get("modus"):
                        einstellung["modus"] = modus
                        einstellung["modus_quelle"] = "nutzer" if modus else None
            return "Vorschlag neu berechnet."
        return _aktion(entwurf_id, aenderung, wb=form.get("wb") or "")

    @router.post("/assistent/entwurf/{entwurf_id}/uebernehmen")
    def uebernehmen(
        entwurf_id: int,
        version: str = Form(...),
        als_vorlage: str = Form(""),
        vorlage_name: str = Form(""),
    ):
        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            entwurf = lade(conn, entwurf_id)
            if entwurf is None:
                conn.rollback()
                return _redirect("/assistent")
            ziel = (entwurf["daten"].get("ziel") or "veranstaltung")
            if entwurf["status"] == "offen" and ziel.startswith("vorlage:"):
                vorlage_id = int(ziel.partition(":")[2])
                try:
                    stamm = es.lade_stammdaten(conn)
                    es.speichere_vorlage(
                        conn, entwurf["daten"]["grunddaten"].get("name") or vorlage_name,
                        es.vorlage_aus_daten(entwurf["daten"], stamm), vorlage_id,
                    )
                except ValueError as error:
                    conn.rollback()
                    return _redirect(_schritt_url(entwurf_id, "abschluss"), fehler=str(error))
                conn.execute(
                    "UPDATE assistent_entwuerfe SET status = 'uebernommen', updated_at = ? WHERE id = ?",
                    (es.jetzt(), entwurf_id),
                )
                conn.commit()
                return _redirect("/assistent/vorlagen", meldung="Vorlage gespeichert.")
            war_offen = entwurf["status"] == "offen"
            statistik = {}
            event_id, fehler = es.uebernehmen(conn, entwurf_id, version, plane, statistik)
            if fehler:
                conn.rollback()
                return _redirect(_schritt_url(entwurf_id, "abschluss"), fehler=" ".join(fehler))
            bearbeiten = bool(es.bestand_event_id(entwurf["daten"]))
            if bearbeiten:
                meldung = "Änderungen übernommen." if war_offen else "Die Änderungen wurden bereits übernommen."
                if war_offen and statistik:
                    teile = [
                        f"{statistik[key]} {text}" for key, text in (
                            ("geaendert", "neu geplant"), ("neu", "neu angelegt"), ("geloescht", "gelöscht"),
                            ("unveraendert", "unverändert"), ("gesperrt", "schon gespielt, nicht angefasst"),
                        ) if statistik.get(key)
                    ]
                    if teile:
                        meldung += " Wettbewerbe: " + ", ".join(teile) + "."
            else:
                meldung = "Veranstaltung erstellt." if war_offen else "Die Veranstaltung wurde bereits erstellt."
            if war_offen and als_vorlage == "1":
                try:
                    stamm = es.lade_stammdaten(conn)
                    es.speichere_vorlage(conn, vorlage_name, es.vorlage_aus_daten(entwurf["daten"], stamm))
                    meldung += " Vorlage gespeichert."
                except ValueError as error:
                    conn.rollback()
                    return _redirect(_schritt_url(entwurf_id, "abschluss"), fehler=str(error))
            conn.commit()
        return _redirect(_schritt_url(entwurf_id, "fertig"), meldung=meldung)

    @router.get("/assistent/entwurf/{entwurf_id}/{schritt}")
    def entwurf_schritt(request: Request, entwurf_id: int, schritt: str):
        if schritt not in es.SCHRITT_KEYS and schritt != "abgleich":
            return _redirect(f"/assistent/entwurf/{entwurf_id}")
        with get_conn() as conn:
            entwurf = lade(conn, entwurf_id)
            if entwurf is None:
                return _redirect("/assistent")
            if entwurf["status"] == "uebernommen":
                return _redirect(_schritt_url(entwurf_id, "fertig"))
            daten = entwurf["daten"]
            if schritt != "abgleich" and (daten.get("abgleich") or {}).get("offen") and schritt != "grunddaten":
                return _redirect(_schritt_url(entwurf_id, "abgleich"))
            geaendert = False
            if schritt in es.SCHRITT_KEYS and daten.get("schritt") != schritt:
                daten["schritt"] = schritt
                geaendert = True
            if schritt == "zeitplan" and es.bloecke_vorschlagen(daten, es.lade_stammdaten(conn)):
                geaendert = True
            if geaendert:
                es.speichere_entwurf(conn, entwurf_id, daten)
                conn.commit()
                entwurf = lade(conn, entwurf_id)
        return render(request, entwurf, schritt)

    @router.post("/assistent/entwurf/{entwurf_id}/{schritt}")
    async def entwurf_schritt_speichern(request: Request, entwurf_id: int, schritt: str):
        form = await request.form()
        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            entwurf = lade(conn, entwurf_id)
            if entwurf is None or entwurf["status"] != "offen":
                conn.rollback()
                return _redirect(f"/assistent/entwurf/{entwurf_id}")
            daten = entwurf["daten"]
            stamm = es.lade_stammdaten(conn)
            meldung = ""
            try:
                meldung = speichere_schritt(conn, daten, stamm, schritt, form)
            except ValueError as error:
                conn.rollback()
                return _redirect(_schritt_url(entwurf_id, schritt), fehler=str(error))
            es.speichere_entwurf(conn, entwurf_id, daten)
            conn.commit()
        ziel = form.get("ziel") or "bleiben"
        if ziel == "weiter":
            ziel = _naechster(schritt, 1)
        elif ziel == "zurueck":
            ziel = _naechster(schritt, -1)
        elif ziel == "bleiben" or ziel not in es.SCHRITT_KEYS:
            ziel = schritt
        anker = form.get("anker") or ""
        return RedirectResponse(
            _schritt_url(entwurf_id, ziel) + ("?" + urlencode({"meldung": meldung}) if meldung else "")
            + (f"#{anker}" if anker else ""),
            status_code=303,
        )

    return router


def _auswirkungen(vorher, nachher):
    """Kurze Beschreibung, was sich durch eine Aenderung an anderen Stellen
    mitbewegt hat (Blockenden, Hinweise)."""
    teile = []
    for block in nachher["bloecke"]:
        alt = vorher["block_ergebnisse"].get(block["id"], {}).get("ende_min")
        neu = nachher["block_ergebnisse"].get(block["id"], {}).get("ende_min")
        if alt != neu and neu is not None:
            teile.append(f"{block['ort']} {block['start']}: letztes Spiel endet jetzt {es.uhr(neu)}")
    alt_b, neu_b = len(vorher["blockierend"]), len(nachher["blockierend"])
    if alt_b != neu_b:
        teile.append(f"blockierende Hinweise: {alt_b} → {neu_b}")
    neu_w = len(nachher["offene_warnungen"])
    if neu_w != len(vorher["offene_warnungen"]):
        teile.append(f"offene Warnungen: {neu_w}")
    return ("Auswirkungen: " + "; ".join(teile) + ".") if teile else ""
