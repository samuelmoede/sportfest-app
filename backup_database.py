from pathlib import Path
import sqlite3
from datetime import datetime

ROOT_DIR = Path(__file__).resolve().parent
DB_PATH = ROOT_DIR / "data" / "sportfest.db"
BACKUP_DIR = ROOT_DIR / "data" / "backups"


def create_backup():
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Datenbankdatei nicht gefunden: {DB_PATH}")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_name = f"sportfest-{timestamp}.db"
    backup_path = BACKUP_DIR / backup_name
    # SQLite-Backup-API statt Dateikopie: auch bei laufender App mit
    # gleichzeitigen Schreibzugriffen entsteht ein konsistentes Abbild
    # (wie app/services/backup_service.py).
    source = sqlite3.connect(DB_PATH)
    try:
        dest = sqlite3.connect(backup_path)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()
    return backup_path


if __name__ == "__main__":
    try:
        target = create_backup()
        print(f"Backup erfolgreich erstellt: {target}")
    except Exception as exc:
        print(f"Fehler beim Erstellen des Backups: {exc}")
        raise
