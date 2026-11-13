"""Aufbau der Hauptnavigation (Seitenleiste und mobiles Menue).

Eine einzige Quelle fuer Gruppen, Reihenfolge, Benennung und Symbole - das
Template partials/navigation.html rendert daraus sowohl die Seitenleiste als
auch das mobile Menue, damit beide nie auseinanderlaufen.

Gliederung nach Haeufigkeit und Zeitdruck statt nach Datenmodell:
"Wettkampftag" und "Auswertung" stehen oben, weil sie am Veranstaltungstag
von allen Rollen gebraucht werden; "Vorbereitung" und "System" sieht nur der
Admin, und zwar in der Reihenfolge, in der die Daten voneinander abhaengen
(Veranstaltung -> Teams/Spielfelder -> Wettbewerbe -> Spielplan). So stehen
Spielplan, Ergebnisse und Tabellen fuer jede Rolle an derselben Stelle.

Die Rolle je Eintrag muss zu AREA_ACCESS_RULES in app/main.py passen (siehe
tests/test_navigation_service.py) - sie blendet nur Links aus, den
eigentlichen Schutz uebernimmt weiterhin die zentrale Middleware.
"""
from typing import Callable

# Symbole kommen aus dem app-weiten Satz in icon_service (Lucide).
from app.services.icon_service import ICONS as NAV_ICONS, icon_svg

# (label, href, icon, erforderliche Rolle oder None, Treffer-Art)
# Treffer-Art "prefix": auch Unterseiten (z.B. /events/3) markieren den Eintrag.
NAV_GRUPPEN = (
    (None, (
        ("Dashboard", "/", "layout-dashboard", None, "exact"),
    )),
    ("Wettkampftag", (
        ("Spielplan", "/spielplan", "calendar-clock", None, "exact"),
        ("Ergebnisse eintragen", "/ergebnisse", "clipboard-pen-line", "referee", "exact"),
    )),
    ("Auswertung", (
        ("Tabellen", "/tabellen", "list-ordered", None, "exact"),
        ("Urkunden", "/urkunden", "award", "tournament_lead", "exact"),
    )),
    ("Vorbereitung", (
        ("Einrichtungsassistent", "/assistent", "wand-sparkles", "admin", "prefix"),
        ("Veranstaltungen", "/events", "flag", "admin", "prefix"),
        ("Teams", "/teams", "users", "admin", "exact"),
        ("Spielfelder", "/spielfelder", "map-pin", "admin", "exact"),
        ("Wettbewerbe", "/wettbewerbe", "trophy", "admin", "exact"),
        ("Spielplan bearbeiten", "/spielplan-bearbeiten", "calendar-cog", "admin", "exact"),
    )),
    ("System", (
        ("Einstellungen", "/einstellungen", "settings", "admin", "exact"),
        ("Dokumentation", "/dokumentation", "book-open", "admin", "exact"),
        ("Roadmap", "/roadmap", "milestone", "admin", "exact"),
    )),
)


def _ist_aktiv(current_path: str, href: str, treffer: str) -> bool:
    if treffer == "prefix":
        return current_path == href or current_path.startswith(f"{href}/")
    return current_path == href


def build_navigation(current_path: str, darf: Callable[[str], bool]):
    """Liefert die sichtbaren Navigationsgruppen fuer eine Rolle.

    darf(rolle) sagt, ob die aktuelle Sitzung Eintraege dieser Rolle sehen
    darf (im Template: can_access_role(request, rolle)). Gruppen ohne
    sichtbaren Eintrag entfallen ganz, damit keine leere Ueberschrift bleibt.
    """
    gruppen = []
    for titel, eintraege in NAV_GRUPPEN:
        sichtbar = [
            {
                "label": label,
                "href": href,
                "icon": icon,
                "aktiv": _ist_aktiv(current_path, href, treffer),
            }
            for label, href, icon, rolle, treffer in eintraege
            if rolle is None or darf(rolle)
        ]
        if sichtbar:
            gruppen.append({"titel": titel, "eintraege": sichtbar})
    return gruppen


def nav_icon_svg(name: str, css_class: str = "nav-icon") -> str:
    """Fertiges, dekoratives Inline-SVG fuer ein Symbol aus NAV_ICONS."""
    return icon_svg(name, css_class)
