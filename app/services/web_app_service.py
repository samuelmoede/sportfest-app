"""Installierbare Web-App (PWA): Manifest fuer "Zum Home-Bildschirm".

Damit oeffnet sich die Seite vom Startbildschirm im Vollbild ohne
Adressleiste, mit eigenem Symbol. Auf dem iPhone kommen Push-Meldungen
(siehe push_service) ueberhaupt nur in einer so installierten Web-App an.
Keine Offline-Funktion: der vorhandene Service Worker
(/static/js/benachrichtigung-sw.js, Scope /static/js/) bleibt unveraendert
nur fuer Benachrichtigungen zustaendig.

Alle Datei-URLs tragen ?v=<Inhalts-Hash>, damit der Cloudflare-Edge-Cache
nach einer Aenderung keine alten Symbole ausliefert.
"""

import hashlib
import json

from app.web import get_datei_version

APP_NAME = "Sportfest Manager"
APP_KURZNAME = "Sportfest"
APP_FARBE = "#17233c"

SYMBOLE = (
    # (Datei unter app/static, Groesse, purpose)
    ("icons/icon-192.png", "192x192", "any"),
    ("icons/icon-512.png", "512x512", "any"),
    # Symbol liegt innerhalb der sicheren Zone (80 %), daher auch maskierbar.
    ("icons/icon-512.png", "512x512", "maskable"),
)


def build_manifest():
    return {
        "id": "/",
        "name": APP_NAME,
        "short_name": APP_KURZNAME,
        "description": "Spielplan, Ergebnisse und Tabellen des Sportfests.",
        "lang": "de",
        "dir": "ltr",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": APP_FARBE,
        "theme_color": APP_FARBE,
        "icons": [
            {
                "src": f"/static/{pfad}?v={get_datei_version(pfad)}",
                "sizes": groesse,
                "type": "image/png",
                "purpose": purpose,
            }
            for pfad, groesse, purpose in SYMBOLE
        ],
    }


def manifest_json():
    return json.dumps(build_manifest(), ensure_ascii=False, indent=2)


def manifest_version():
    """Kurzer Hash des Manifests, fuer ?v= am <link rel="manifest">."""
    return hashlib.sha256(manifest_json().encode("utf-8")).hexdigest()[:12]
