"""Feldansicht der Ergebniseingabe (/ergebnisse/feld).

Das Raster Zeit x Feld von /ergebnisse ist fuer die Turnierleitung am Laptop
gedacht. Am Handy steht eine Lehrkraft an genau einem Feld: Sie waehlt Ort und
Feld und sieht dann nur dessen Spiele - beendete als eine Zeile, das laufende
gross, die naechsten als Zeilen.
"""
from app.services.schedule_location_service import (
    COMPETITION_LOCATIONS,
    get_court_location,
    natural_sort_key,
)

# So viele beendete Spiele bleiben sichtbar; aeltere klappen ein, damit das
# laufende Spiel auch am Nachmittag ohne Scrollen erreichbar bleibt.
SICHTBARE_BEENDETE = 2


def _wert(row, key, default=None):
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def build_feldansicht(slots, courts, ort=None, court_id=None):
    """Baut die Daten der Feldansicht.

    slots: Spiele (slot_typ 'Spiel') der sichtbaren Wettbewerbe.
    courts: aktive Spielfelder.
    ort / court_id: gewuenschte Auswahl; ein gueltiges Feld bestimmt den Ort,
    sonst gilt der Ort, sonst der erste Ort mit Spielen.
    """
    spiele = sorted(
        (slot for slot in slots if _wert(slot, "slot_typ", "Spiel") == "Spiel"),
        key=lambda slot: (_wert(slot, "startzeit", ""), _wert(slot, "id", 0)),
    )
    spiele_je_feld = {}
    for slot in spiele:
        spiele_je_feld.setdefault(slot["court_id"], []).append(slot)

    felder_mit_spielen = sorted(
        (court for court in courts if court["id"] in spiele_je_feld),
        key=lambda court: natural_sort_key(_wert(court, "name", "")),
    )
    ort_je_feld = {court["id"]: get_court_location(court) for court in felder_mit_spielen}
    orte = [
        location for location in COMPETITION_LOCATIONS
        if location in ort_je_feld.values()
    ]

    gewaehlter_ort = None
    if court_id in ort_je_feld and (ort is None or ort_je_feld[court_id] == ort):
        gewaehlter_ort = ort_je_feld[court_id]
    elif ort in orte:
        gewaehlter_ort = ort
        court_id = None
    elif orte:
        gewaehlter_ort = orte[0]
        court_id = None

    felder = []
    gewaehltes_feld = None
    for court in felder_mit_spielen:
        if ort_je_feld[court["id"]] != gewaehlter_ort:
            continue
        feld_spiele = spiele_je_feld[court["id"]]
        feld = {
            "id": court["id"],
            "name": court["name"],
            "laeuft": any(slot["status"] == "läuft" for slot in feld_spiele),
            "offen": sum(1 for slot in feld_spiele if slot["status"] != "beendet"),
            "gewaehlt": False,
        }
        felder.append(feld)
        if court["id"] == court_id:
            gewaehltes_feld = feld
    if gewaehltes_feld is None and felder:
        gewaehltes_feld = felder[0]
    if gewaehltes_feld is not None:
        gewaehltes_feld["gewaehlt"] = True

    feld_spiele = spiele_je_feld.get(gewaehltes_feld["id"], []) if gewaehltes_feld else []
    beendet = [slot for slot in feld_spiele if slot["status"] == "beendet"]
    laufend = [slot for slot in feld_spiele if slot["status"] == "läuft"]
    geplant = [slot for slot in feld_spiele if slot["status"] == "geplant"]

    # Laeuft gerade nichts, steht das naechste geplante Spiel gross da
    # ("dran") - sonst muesste man es erst in der Liste suchen.
    dran = None
    if not laufend and geplant:
        dran = geplant[0]
        geplant = geplant[1:]

    return {
        "orte": [{"name": name, "gewaehlt": name == gewaehlter_ort} for name in orte],
        "ort": gewaehlter_ort,
        "felder": felder,
        "feld": gewaehltes_feld,
        "beendet_frueher": beendet[:-SICHTBARE_BEENDETE] if len(beendet) > SICHTBARE_BEENDETE else [],
        "beendet": beendet[-SICHTBARE_BEENDETE:],
        "laufend": laufend,
        "dran": dran,
        "geplant": geplant,
        "hat_spiele": bool(spiele),
    }
