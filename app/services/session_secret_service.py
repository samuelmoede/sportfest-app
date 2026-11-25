"""Gemeinsamer Session-Schluessel fuer alle Worker-Prozesse.

Ist SPORTFEST_SESSION_SECRET_KEY nicht gesetzt, erzeugte die App frueher bei
jedem Start einen Zufallsschluessel. Mit mehreren uvicorn-Workern
(docker-compose: --workers) haette dann jeder Prozess einen anderen
Schluessel: Wer sich bei Worker 1 anmeldet, waere bei Worker 2 wieder
abgemeldet. Deshalb wird der erzeugte Schluessel einmalig in einer Datei neben
der Datenbank abgelegt und von allen Prozessen gelesen. Ein gesetzter
Umgebungswert hat weiterhin Vorrang.
"""
import os
import secrets
from pathlib import Path


def _read(path: Path):
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def load_or_create_session_secret(path: Path) -> str:
    """Liest den Schluessel aus `path` oder legt ihn atomar neu an.

    Starten mehrere Prozesse gleichzeitig, gewinnt genau einer das Anlegen
    (O_EXCL); alle anderen lesen danach dessen Schluessel.
    """
    existing = _read(path)
    if existing:
        return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    new_secret = secrets.token_urlsafe(32)
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            handle.write(new_secret)
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        try:
            # link() schlaegt fehl, wenn ein anderer Prozess schneller war.
            os.link(tmp_path, path)
        except FileExistsError:
            pass
        except OSError:
            # Dateisysteme ohne Hardlinks: notfalls direkt anlegen.
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(new_secret)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass
    return _read(path) or new_secret
