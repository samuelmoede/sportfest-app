"""Beamer-Ansicht fuer den Wettkampftag: baut aus den Spielen die Seiten, durch
die der Beamer automatisch blaettert (je Ort "Jetzt" + "Als Naechstes", dann
Tabellen und letzte Ergebnisse). Reine Funktionen ohne Datenbankzugriff, damit
sie sich einfach testen lassen. Verspaetungen gehoeren bewusst nicht hierher
(nur fuer Turnierleitung im Dashboard)."""
from datetime import datetime

from app.services.runden_timer_service import build_round_timer
from app.services.schedule_location_service import (
    COMPETITION_LOCATIONS,
    DEFAULT_COMPETITION_LOCATION,
    NO_SLOT_LOCATION,
    natural_sort_key,
)
from app.services.ui_view_service import SPORT_FARBEN

MAX_NAECHSTE = 12
MAX_ERGEBNISSE = 10
TABELLEN_ZEILEN = 6
TABELLEN_PRO_SEITE = 3
STANDARD_SEKUNDEN = 12


def _ist_spiel(slot):
    return slot.get("slot_typ") == "Spiel" and slot.get("team_a_id") is not None \
        and slot.get("team_b_id") is not None


def sport_farben(slots):
    """Sportart -> Farbnummer 1..SPORT_FARBEN, wie im Tagesplan (gleiche
    Sportart, gleiche Farbe), vergeben in der Reihenfolge des Auftretens."""
    farben = {}
    keys = sorted(
        {(slot.get("competition_start_time") or "99:99", (slot.get("sportart") or "").strip().casefold())
         for slot in slots}
    )
    for _start, key in keys:
        if key not in farben:
            farben[key] = len(farben) % SPORT_FARBEN + 1
    return farben


def _karte(slot, farben):
    phase = slot.get("phase") or ""
    return {
        "id": slot.get("id"),
        "feld": slot.get("court_name") or "",
        "zeit": slot.get("startzeit") or "",
        "team_a": slot.get("team_a") or "?",
        "team_b": slot.get("team_b") or "?",
        "score_a": slot.get("score_a"),
        "score_b": slot.get("score_b"),
        # Der Wettbewerbsname unterscheidet auch Teilmannschaften
        # (Tischtennis Jungen/Maedchen), die Sportart allein nicht.
        "wettbewerb": (slot.get("competition_name") or slot.get("sportart") or "").replace("Jahrgang ", "Jg. "),
        "phase": phase if phase and phase != "Gruppenphase" else "",
        "farbe": farben.get((slot.get("sportart") or "").strip().casefold(), 1),
    }


def _ort_von(slot):
    ort = slot.get("effective_competition_location") or DEFAULT_COMPETITION_LOCATION
    return ort if ort in COMPETITION_LOCATIONS else DEFAULT_COMPETITION_LOCATION


def _zeitpunkt(value):
    try:
        return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _timer(spiele, jetzt):
    """Runden-Timer eines Ortes: Restsekunden, solange er laeuft (siehe
    runden_timer_service) - sonst None."""
    laufend = build_round_timer(spiele)["running"]
    if not laufend or not laufend.get("planned_seconds"):
        return None
    # Nur eine gemeinsam gestartete Runde hat eine gemeinsame Restzeit;
    # einzeln gestartete Spiele (z.B. drei Felder nacheinander) nicht.
    if laufend["count"] != sum(1 for s in spiele if s.get("status") == "läuft"):
        return None
    start = _zeitpunkt(laufend["started_at"])
    stopp = _zeitpunkt(laufend["stopped_at"]) if laufend.get("stopped_at") else None
    if start is None:
        return None
    bis = stopp or jetzt.replace(tzinfo=None)
    vergangen = max(int((bis - start).total_seconds()), 0)
    return {
        "rest_sekunden": max(laufend["planned_seconds"] - vergangen, 0),
        "angehalten": stopp is not None,
    }


def baue_ort_seiten(slots, farben, jetzt, ort_filter=None):
    """Eine Seite je Ort mit mindestens einem Spiel: laufende Spiele und je
    Feld das naechste geplante Spiel."""
    nach_ort = {}
    for slot in slots:
        if _ist_spiel(slot):
            nach_ort.setdefault(_ort_von(slot), []).append(slot)
    seiten = []
    for ort in COMPETITION_LOCATIONS:
        if ort == NO_SLOT_LOCATION or ort not in nach_ort:
            continue
        if ort_filter and ort != ort_filter:
            continue
        spiele = nach_ort[ort]
        laufend = sorted(
            (s for s in spiele if s["status"] == "läuft"),
            key=lambda s: (natural_sort_key(s.get("court_name") or ""), s.get("id") or 0),
        )
        je_feld = {}
        for slot in sorted(
            (s for s in spiele if s["status"] == "geplant"),
            key=lambda s: (s.get("startzeit") or "", s.get("id") or 0),
        ):
            je_feld.setdefault(slot.get("court_id"), slot)
        naechste = sorted(je_feld.values(), key=lambda s: (
            s.get("startzeit") or "", natural_sort_key(s.get("court_name") or ""),
        ))[:MAX_NAECHSTE]
        if not laufend and not naechste:
            continue
        seiten.append({
            "typ": "ort",
            "titel": ort,
            "laufend": [_karte(s, farben) for s in laufend],
            "naechste": [_karte(s, farben) for s in naechste],
            "timer": _timer(spiele, jetzt),
        })
    return seiten


def baue_tabellen_seiten(gesamtwertung, tabellen):
    """Zwischenstaende: erst die Gesamtwertungen (Schulpokal: je Jahrgang),
    dann die Tabellen der Wettbewerbe, gebuendelt auf Seiten mit wenigen
    Karten, damit die Schrift gross bleibt. Leere Tabellen (noch kein
    beendetes Spiel) entfallen."""
    karten = []
    for gruppe in gesamtwertung or []:
        # Mit nur einem Wettbewerb waere die Gesamtwertung dieselbe Tabelle
        # noch einmal - dann reicht die ausfuehrlichere Wettbewerbstabelle.
        if len(gruppe.get("competitions") or []) == 1:
            continue
        rows = gruppe.get("rows", [])
        if any(r.get("total_points") for r in rows):
            karten.append({"titel": gruppe["year_group"], "kopf": "Pkt.", "gesamt": True, "zeilen": [
                {"platz": r.get("place"), "name": r.get("team"), "wert": _punkte(r.get("total_points"))}
                for r in rows[:TABELLEN_ZEILEN]
            ]})
    for eintrag in tabellen or []:
        rows = eintrag["rows"]
        if any(r.get("sp") for r in rows):
            karten.append({"titel": eintrag["name"], "kopf": "Pkt.", "farbe": eintrag.get("farbe", 1), "zeilen": [
                {"platz": index, "name": r.get("team"), "wert": _punkte(r.get("pkt")),
                 "detail": f"{r.get('plus', 0)}:{r.get('minus', 0)}"}
                for index, r in enumerate(rows[:TABELLEN_ZEILEN], start=1)
            ]})
    return [
        {"typ": "tabellen", "titel": "Zwischenstand", "karten": karten[i:i + TABELLEN_PRO_SEITE]}
        for i in range(0, len(karten), TABELLEN_PRO_SEITE)
    ]


def _punkte(value):
    if value is None:
        return ""
    return str(int(value)) if float(value) == int(value) else f"{value:.1f}".replace(".", ",")


def baue_ergebnis_seite(slots, farben, ort_filter=None):
    fertig = sorted(
        (s for s in slots if _ist_spiel(s) and s["status"] == "beendet"
         and (not ort_filter or _ort_von(s) == ort_filter)
         and s.get("score_a") is not None and s.get("score_b") is not None),
        key=lambda s: (s.get("finished_at") or "", s.get("startzeit") or "", s.get("id") or 0),
        reverse=True,
    )[:MAX_ERGEBNISSE]
    if not fertig:
        return []
    return [{"typ": "ergebnisse", "titel": "Letzte Ergebnisse", "karten": [_karte(s, farben) for s in fertig]}]


def naechster_programmpunkt(programmpunkte, jetzt):
    """Der aktuell laufende oder naechste Programmpunkt (Eroeffnung,
    Siegerehrung ...) fuer die Kopfzeile."""
    minute = jetzt.hour * 60 + jetzt.minute
    kandidaten = []
    for punkt in programmpunkte or []:
        try:
            sh, sm = (int(x) for x in str(punkt["start_time"]).split(":")[:2])
            eh, em = (int(x) for x in str(punkt["end_time"]).split(":")[:2])
        except (KeyError, ValueError):
            continue
        start, ende = sh * 60 + sm, eh * 60 + em
        if ende > minute:
            kandidaten.append((start, ende, punkt))
    if not kandidaten:
        return None
    start, ende, punkt = min(kandidaten, key=lambda k: (k[0], k[1]))
    return {
        "titel": (punkt.get("titel") or "").strip() or "Programmpunkt",
        "von": f"{start // 60:02d}:{start % 60:02d}",
        "bis": f"{ende // 60:02d}:{ende % 60:02d}",
        "laeuft": start <= minute,
    }


def baue_beamer_seiten(slots, jetzt, gesamtwertung=None, tabellen=None,
                       programmpunkte=None, ort=None, mit_tabellen=True):
    farben = sport_farben(slots)
    seiten = baue_ort_seiten(slots, farben, jetzt, ort_filter=ort)
    if mit_tabellen:
        seiten += baue_tabellen_seiten(gesamtwertung, [
            dict(t, farbe=farben.get((t.get("sportart") or "").strip().casefold(), 1)) for t in tabellen or []
            if not ort or t.get("ort") in (None, ort)
        ])
    seiten += baue_ergebnis_seite(slots, farben, ort_filter=ort)
    legende = []
    gesehen = set()
    for slot in sorted(slots, key=lambda s: (s.get("competition_start_time") or "99:99")):
        name = (slot.get("sportart") or "").strip()
        if name and name.casefold() not in gesehen:
            gesehen.add(name.casefold())
            legende.append({"name": name, "farbe": farben[name.casefold()]})
    return {
        "seiten": seiten,
        "legende": legende,
        "programmpunkt": naechster_programmpunkt(programmpunkte, jetzt),
    }
