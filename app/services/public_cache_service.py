"""Kurzer Zwischenspeicher fuer oeffentliche Leseseiten.

Wenn viele Zuschauer (z.B. die ganze Schule) gleichzeitig Tagesplan,
Spielplan oder Tabellen oeffnen, soll nicht jeder Aufruf die App erreichen.
Antworten an Besucher OHNE Anmeldung bekommen deshalb eine Cache-Kopfzeile
(`s-maxage`), mit der ein vorgeschalteter Cache (z.B. Cloudflare) dieselbe
Seite einige Sekunden lang an alle ausliefern darf. `max-age=0` sorgt dafuer,
dass der Browser selbst nichts aufhebt.

Angemeldete Benutzer (Session-Cookie vorhanden oder gerade gesetzt) bekommen
`private`, ihre Seiten landen also nie in einem gemeinsamen Cache. Die
Kopfzeile allein aendert nichts: Erst eine passende Regel im vorgeschalteten
Cache nutzt sie.

Dauer in Sekunden per Umgebungsvariable SPORTFEST_PUBLIC_CACHE_SECONDS
(Standard 15, 0 schaltet ab, hoechstens 300).
"""
import os

DEFAULT_PUBLIC_CACHE_SECONDS = 15
MAX_PUBLIC_CACHE_SECONDS = 300

PUBLIC_CACHE_PATHS = frozenset({
    "/",
    "/spielplan",
    "/spielplan/aushang",
    "/tabellen",
    "/beamer",
    "/durchsage/aktuell",
})
PUBLIC_CACHE_PREFIXES = ("/siegerehrung/",)


def get_public_cache_seconds(value=None):
    if value is None:
        value = os.getenv("SPORTFEST_PUBLIC_CACHE_SECONDS")
    if value is None or str(value).strip() == "":
        return DEFAULT_PUBLIC_CACHE_SECONDS
    try:
        seconds = int(str(value).strip())
    except ValueError:
        return DEFAULT_PUBLIC_CACHE_SECONDS
    return max(0, min(seconds, MAX_PUBLIC_CACHE_SECONDS))


def is_public_cache_path(path):
    return path in PUBLIC_CACHE_PATHS or path.startswith(PUBLIC_CACHE_PREFIXES)


def public_cache_control(method, path, status_code, has_session, seconds=None):
    """Wert fuer die Cache-Control-Kopfzeile oder None (Antwort unveraendert).

    has_session: Die Anfrage brachte ein Session-Cookie mit oder die Antwort
    setzt eines - dann ist die Seite persoenlich und darf nicht geteilt werden.
    """
    if method.upper() not in {"GET", "HEAD"} or not is_public_cache_path(path):
        return None
    if seconds is None:
        seconds = get_public_cache_seconds()
    if seconds <= 0:
        return None
    if has_session:
        return "private"
    if status_code != 200:
        return None
    return f"public, max-age=0, s-maxage={seconds}"
