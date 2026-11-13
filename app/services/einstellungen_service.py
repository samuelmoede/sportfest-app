"""Gliederung der Einstellungsseite in Bereiche und Beamer-Link-Baukasten.

/einstellungen war eine einzige lange Seite mit zehn Kaesten. Sie ist jetzt
in fuenf Bereiche geteilt, sortiert nach Haeufigkeit; es wird immer nur ein
Bereich gerendert (?bereich=...). Die Bereichsleiste zeigt je Bereich eine
Zeile zum aktuellen Zustand, damit man ohne Klick sieht, ob etwas zu tun ist.
"""
from typing import Optional
from urllib.parse import urlencode

from app.services.beamer_service import STANDARD_SEKUNDEN
from app.services.schedule_location_service import (
    COMPETITION_LOCATIONS,
    NO_SLOT_LOCATION,
    normalize_competition_location,
)

# (Schluessel, Bezeichnung) in Anzeige-Reihenfolge.
BEREICHE = (
    ("anzeige", "Anzeige"),
    ("benutzer", "Benutzer & Sicherheit"),
    ("datensicherung", "Datensicherung"),
    ("protokoll", "Protokoll"),
    ("system", "System"),
)
BEREICH_SCHLUESSEL = tuple(key for key, _ in BEREICHE)
STANDARD_BEREICH = "anzeige"

# Status-Parameter aelterer Weiterleitungen/Lesezeichen -> zugehoeriger Bereich.
# So landet z. B. /einstellungen?user_status=created weiterhin bei den Benutzern.
_STATUS_BEREICHE = (
    ("user_status", "benutzer"),
    ("security_status", "benutzer"),
    ("backup_status", "datensicherung"),
    ("restore_status", "datensicherung"),
    ("delete_status", "datensicherung"),
    ("reset_status", "protokoll"),
    ("change_log_role", "protokoll"),
    ("change_log_competition_id", "protokoll"),
    ("change_log_username", "protokoll"),
)


def bestimme_bereich(bereich: str = "", **status) -> str:
    """Der anzuzeigende Bereich: ausdruecklich gewaehlt, sonst aus einem
    gesetzten Status-Parameter abgeleitet, sonst der Standardbereich."""
    gewaehlt = (bereich or "").strip().lower()
    if gewaehlt in BEREICH_SCHLUESSEL:
        return gewaehlt
    for parameter, ziel in _STATUS_BEREICHE:
        if status.get(parameter):
            return ziel
    return STANDARD_BEREICH


def bereich_url(bereich: str, **parameter) -> str:
    """Adresse eines Bereichs, optional mit Status-Parametern (leere entfallen)."""
    werte = {"bereich": bereich}
    werte.update({key: value for key, value in parameter.items() if value not in (None, "")})
    return f"/einstellungen?{urlencode(werte)}"


def baue_bereichsleiste(
    aktiv: str,
    *,
    benutzer_anzahl: int = 0,
    sicherheit_aktiv: bool = False,
    letztes_backup: Optional[str] = None,
    aenderungen: int = 0,
    version: str = "",
    system_ok: bool = True,
):
    """Eintraege der Bereichsleiste mit einer Zustandszeile je Bereich."""
    hinweise = {
        "anzeige": "Schule, Banner, Design, Beamer",
        "benutzer": (
            f"{benutzer_anzahl} Benutzer · "
            f"Anmeldung {'an' if sicherheit_aktiv else 'aus'}"
        ),
        "datensicherung": (
            f"Letztes Backup: {letztes_backup}" if letztes_backup else "Noch kein Backup"
        ),
        "protokoll": (
            "1 Ergebnisänderung" if aenderungen == 1 else f"{aenderungen} Ergebnisänderungen"
        ),
        "system": (
            f"Version {version} · {'alles in Ordnung' if system_ok else 'Problem erkannt'}"
        ),
    }
    return [
        {
            "key": key,
            "label": label,
            "hinweis": hinweise[key],
            "href": bereich_url(key),
            "aktiv": key == aktiv,
            "warnung": key == "system" and not system_ok,
        }
        for key, label in BEREICHE
    ]


def beamer_orte():
    """Orte, die eine eigene Beamer-Seite haben koennen (ohne den Ort, an dem
    keine Spiele stattfinden)."""
    return [ort for ort in COMPETITION_LOCATIONS if ort != NO_SLOT_LOCATION]


def baue_beamer_link(
    basis: str,
    *,
    ort: str = "",
    tabellen: bool = True,
    hell: bool = False,
    sekunden: Optional[int] = None,
) -> str:
    """Adresse der Beamer-Ansicht mit den gewaehlten Optionen. Standardwerte
    werden weggelassen, damit der Link kurz bleibt. Die gleiche Logik steckt
    im Skript der Einstellungsseite (Baukasten zum Anklicken)."""
    parameter = {}
    gewaehlter_ort = normalize_competition_location(ort)
    if gewaehlter_ort:
        parameter["ort"] = gewaehlter_ort
    if not tabellen:
        parameter["tabellen"] = "aus"
    if hell:
        parameter["hell"] = "1"
    if sekunden and int(sekunden) != STANDARD_SEKUNDEN:
        parameter["sekunden"] = str(min(max(int(sekunden), 5), 120))
    adresse = f"{basis.rstrip('/')}/beamer"
    return f"{adresse}?{urlencode(parameter)}" if parameter else adresse
