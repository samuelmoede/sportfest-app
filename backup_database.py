from pathlib import Path
import shutil
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
    shutil.copy2(DB_PATH, backup_path)
    return backup_path


if __name__ == "__main__":
    try:
        target = create_backup()
        print(f"Backup erfolgreich erstellt: {target}")
    except Exception as exc:
        print(f"Fehler beim Erstellen des Backups: {exc}")
        raise
