from datetime import datetime
from pathlib import Path
import os
import shutil
import sqlite3
import tempfile

from app.database import DB_PATH, init_db
from app.utils.formatting import format_bytes


# Muss im selben (persistenten) Volume wie die Datenbank liegen - sonst gehen
# Backups bei jedem Container-Neuaufbau (z.B. docker compose up --force-recreate)
# verloren, weil nur ./data:/app/data gemountet ist.
BACKUP_DIR = DB_PATH.parent / "backups"


def list_backup_files():
    if not BACKUP_DIR.exists():
        return []

    backups = []
    for file_path in BACKUP_DIR.glob("*.db"):
        stat = file_path.stat()
        created_at = datetime.fromtimestamp(stat.st_ctime)
        backups.append({
            "name": file_path.name,
            "size_bytes": stat.st_size,
            "size_display": format_bytes(stat.st_size),
            "created_at": created_at,
            "created_at_display": created_at.strftime("%d.%m.%Y %H:%M"),
        })
    backups.sort(key=lambda item: item["created_at"], reverse=True)
    return backups


def _sqlite_backup(source_path: Path, dest_path: Path):
    """Konsistente Kopie ueber die SQLite-Backup-API statt eines rohen
    Datei-Copys, damit ein zeitgleicher Schreibzugriff kein unvollstaendiges
    Abbild erzeugt."""
    source = sqlite3.connect(source_path)
    try:
        dest = sqlite3.connect(dest_path)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()


def create_database_backup():
    if not DB_PATH.exists():
        return None

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    backup_name = f"sportfest_backup_{timestamp}.db"
    backup_path = BACKUP_DIR / backup_name
    suffix = 1
    while backup_path.exists():
        backup_name = f"sportfest_backup_{timestamp}_{suffix}.db"
        backup_path = BACKUP_DIR / backup_name
        suffix += 1

    try:
        _sqlite_backup(DB_PATH, backup_path)
    except (OSError, sqlite3.Error):
        backup_path.unlink(missing_ok=True)
        return None

    return backup_name


def _resolve_backup_path(backup_name: str):
    """Verhindert Path-Traversal (z.B. '../../etc/passwd') und stellt sicher,
    dass die Datei tatsaechlich im Backup-Ordner liegt."""
    if not backup_name:
        return None
    candidate = (BACKUP_DIR / backup_name).resolve()
    try:
        candidate.relative_to(BACKUP_DIR.resolve())
    except ValueError:
        return None
    return candidate


def delete_backup_file(backup_name: str) -> str:
    """Loescht eine einzelne Backup-Datei. Gibt einen Status-String zurueck."""
    backup_path = _resolve_backup_path(backup_name)
    if backup_path is None:
        return "invalid"
    if not backup_path.is_file():
        return "not_found"

    try:
        backup_path.unlink()
    except OSError:
        return "error"

    return "ok"


def restore_database_backup(backup_name: str) -> str:
    """Spielt ein Backup als aktuelle Datenbank ein.

    Legt vorher automatisch ein Sicherungs-Backup des aktuellen Stands an,
    damit ein versehentliches Wiedereinspielen selbst rueckgaengig gemacht
    werden kann. Gibt einen Status-String zurueck.
    """
    backup_path = _resolve_backup_path(backup_name)
    if backup_path is None:
        return "invalid"
    if not backup_path.is_file():
        return "not_found"

    try:
        check_conn = sqlite3.connect(backup_path)
        try:
            check_conn.execute("PRAGMA integrity_check").fetchone()
            check_conn.execute("SELECT COUNT(*) FROM competitions").fetchone()
        finally:
            check_conn.close()
    except sqlite3.Error:
        return "invalid_db"

    if DB_PATH.exists():
        create_database_backup()

    tmp_fd, tmp_name = tempfile.mkstemp(dir=DB_PATH.parent, suffix=".db.tmp")
    os.close(tmp_fd)
    tmp_path = Path(tmp_name)
    try:
        shutil.copyfile(backup_path, tmp_path)
        # Aeltere Backups koennen ein veraltetes Schema haben (z.B. fehlende
        # settings-Tabelle); die gleichen idempotenten Migrationen wie beim
        # App-Start nachziehen, damit die App danach sofort weiterlaeuft.
        init_db(tmp_path)
        try:
            os.replace(tmp_path, DB_PATH)
        except OSError:
            # os.replace() ist auf manchen Netzlaufwerken/SMB-Freigaben nicht
            # atomar verfuegbar (Windows-Zugriffsfehler) - dann direkt kopieren.
            shutil.copyfile(tmp_path, DB_PATH)
            tmp_path.unlink(missing_ok=True)
    except (OSError, sqlite3.Error):
        tmp_path.unlink(missing_ok=True)
        return "error"

    return "ok"
