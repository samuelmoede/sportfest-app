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

# Einheitlicher Liniensymbol-Satz (Lucide v0.460.0, ISC-Lizenz,
# https://lucide.dev) statt gemischter Emojis: gleiche Strichstaerke, erbt
# die Schriftfarbe (currentColor) und sieht auf jedem Geraet gleich aus.
# Inhalt ist jeweils das Innere eines <svg viewBox="0 0 24 24">.
NAV_ICONS = {
    "layout-dashboard": (
        '<rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="3" rx="1"/><rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="5" x="3" y="16" rx="1"/>'
    ),
    "calendar-clock": (
        '<path d="M21 7.5V6a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h3.5"/><path d="M16 2v4"/><path d="M8 2v4"/><path d="M3 10h5"/><path d="M17.5 17.5 16 16.3V14"/><circle cx="16" cy="16" r="6"/>'
    ),
    "clipboard-pen-line": (
        '<rect width="8" height="4" x="8" y="2" rx="1"/><path d="M8 4H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-.5"/><path d="M16 4h2a2 2 0 0 1 1.73 1"/><path d="M8 18h1"/><path d="M21.378 12.626a1 1 0 0 0-3.004-3.004l-4.01 4.012a2 2 0 0 0-.506.854l-.837 2.87a.5.5 0 0 0 .62.62l2.87-.837a2 2 0 0 0 .854-.506z"/>'
    ),
    "list-ordered": (
        '<path d="M10 12h11"/><path d="M10 18h11"/><path d="M10 6h11"/><path d="M4 10h2"/><path d="M4 6h1v4"/><path d="M6 18H4c0-1 2-2 2-3s-1-1.5-2-1"/>'
    ),
    "award": (
        '<path d="m15.477 12.89 1.515 8.526a.5.5 0 0 1-.81.47l-3.58-2.687a1 1 0 0 0-1.197 0l-3.586 2.686a.5.5 0 0 1-.81-.469l1.514-8.526"/><circle cx="12" cy="8" r="6"/>'
    ),
    "wand-sparkles": (
        '<path d="m21.64 3.64-1.28-1.28a1.21 1.21 0 0 0-1.72 0L2.36 18.64a1.21 1.21 0 0 0 0 1.72l1.28 1.28a1.2 1.2 0 0 0 1.72 0L21.64 5.36a1.2 1.2 0 0 0 0-1.72"/><path d="m14 7 3 3"/><path d="M5 6v4"/><path d="M19 14v4"/><path d="M10 2v2"/><path d="M7 8H3"/><path d="M21 16h-4"/><path d="M11 3H9"/>'
    ),
    "flag": (
        '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" x2="4" y1="22" y2="15"/>'
    ),
    "trophy": (
        '<path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"/><path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18"/><path d="M4 22h16"/><path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22"/><path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22"/><path d="M18 2H6v7a6 6 0 0 0 12 0V2Z"/>'
    ),
    "users": (
        '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>'
    ),
    "map-pin": (
        '<path d="M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0"/><circle cx="12" cy="10" r="3"/>'
    ),
    "calendar-cog": (
        '<path d="m15.2 16.9-.9-.4"/><path d="m15.2 19.1-.9.4"/><path d="M16 2v4"/><path d="m16.9 15.2-.4-.9"/><path d="m16.9 20.8-.4.9"/><path d="m19.5 14.3-.4.9"/><path d="m19.5 21.7-.4-.9"/><path d="M21 10.5V6a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h6"/><path d="m21.7 16.5-.9.4"/><path d="m21.7 19.5-.9-.4"/><path d="M3 10h18"/><path d="M8 2v4"/><circle cx="18" cy="18" r="3"/>'
    ),
    "settings": (
        '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>'
    ),
    "book-open": (
        '<path d="M12 7v14"/><path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z"/>'
    ),
    "milestone": (
        '<path d="M12 13v8"/><path d="M12 3v3"/><path d="M4 6a1 1 0 0 0-1 1v5a1 1 0 0 0 1 1h13a2 2 0 0 0 1.152-.365l3.424-2.317a1 1 0 0 0 0-1.635l-3.424-2.318A2 2 0 0 0 17 6z"/>'
    ),
    "projector": (
        '<path d="M5 7 3 5"/><path d="M9 6V3"/><path d="m13 7 2-2"/><circle cx="9" cy="13" r="3"/><path d="M11.83 12H20a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2h2.17"/><path d="M16 16h2"/>'
    ),
    "log-out": (
        '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" x2="9" y1="12" y2="12"/>'
    ),
    "log-in": (
        '<path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><polyline points="10 17 15 12 10 7"/><line x1="15" x2="3" y1="12" y2="12"/>'
    ),
}

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
    return (
        f'<svg class="{css_class}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
        f'aria-hidden="true" focusable="false">{NAV_ICONS[name]}</svg>'
    )
