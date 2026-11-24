"""backup_database.py nutzt die SQLite-Backup-API (Pruefbericht B-06)."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import backup_database


class BackupScriptTests(unittest.TestCase):
    def test_erstellt_lesbare_kopie_mit_offener_schreibverbindung(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "sportfest.db"
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE t (x INTEGER)")
            conn.execute("INSERT INTO t VALUES (1)")
            conn.commit()
            try:
                with patch.object(backup_database, "DB_PATH", db_path), \
                        patch.object(backup_database, "BACKUP_DIR", Path(tmp) / "backups"):
                    target = backup_database.create_backup()
            finally:
                conn.close()
            kopie = sqlite3.connect(target)
            try:
                self.assertEqual(kopie.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(kopie.execute("SELECT x FROM t").fetchall(), [(1,)])
            finally:
                kopie.close()
