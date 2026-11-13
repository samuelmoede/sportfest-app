"""CSRF-Schutz per Herkunftspruefung (Origin- bzw. Referer-Header).

Ergaenzt das Session-Cookie mit SameSite=Lax: Schreibende Anfragen (POST
usw.) werden abgelehnt, wenn der Browser eine fremde Herkunft meldet. Das
funktioniert ohne Token in den ueber 80 Formularen und den fetch()-Aufrufen
der Templates, weil Browser Origin/Referer selbst setzen.

Hinter einem Reverse-Proxy muss der Proxy entweder den Host-Header
weiterreichen oder X-Forwarded-Host setzen. Andernfalls lassen sich weitere
erlaubte Adressen per Umgebungsvariable SPORTFEST_TRUSTED_ORIGINS
(kommagetrennt, z.B. "https://sportfest.example.org") eintragen.
"""
import os
from urllib.parse import urlsplit

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _netloc(value):
    """Host[:Port] aus einer URL oder einem Host-Header, kleingeschrieben."""
    value = (value or "").strip()
    if not value:
        return None
    if "://" not in value:
        value = f"//{value}"
    parts = urlsplit(value)
    return parts.netloc.lower() or None


def get_trusted_hosts(headers, extra_origins=None):
    if extra_origins is None:
        extra_origins = os.getenv("SPORTFEST_TRUSTED_ORIGINS", "")
    hosts = {
        _netloc(headers.get("host")),
        # Reverse-Proxys koennen mehrere, kommagetrennte Hosts melden.
        *(_netloc(part) for part in (headers.get("x-forwarded-host") or "").split(",")),
        *(_netloc(part) for part in extra_origins.split(",")),
    }
    hosts.discard(None)
    return hosts


def is_request_origin_allowed(method, headers, extra_origins=None) -> bool:
    """True, wenn die Anfrage nach Origin/Referer von dieser App stammt.

    Lesende Methoden und Anfragen ganz ohne Origin und Referer (z.B. aeltere
    Browser, Skripte) werden durchgelassen - fuer Letztere greift weiterhin
    SameSite=Lax beim Session-Cookie."""
    if method.upper() not in UNSAFE_METHODS:
        return True

    origin = headers.get("origin")
    if origin is not None:
        if origin.strip().lower() == "null":
            return False
        source = origin
    else:
        source = headers.get("referer")
        if not source:
            return True

    return _netloc(source) in get_trusted_hosts(headers, extra_origins)
