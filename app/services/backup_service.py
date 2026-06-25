from datetime import datetime
from pathlib import Path
import shutil

from app.database import DB_PATH
from app.utils.formatting import format_bytes


ROOT_DIR = Path(__file__).resolve().parents[2]
BACKUP_DIR = ROOT_DIR / "backups"


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
        shutil.copyfile(DB_PATH, backup_path)
    except OSError:
        return None

    return backup_name