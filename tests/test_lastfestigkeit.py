"""Tests fuer die Absicherung gegen viele gleichzeitige Zugriffe
(Schulpokal mit ~800 Schuelern): WAL-Modus, Komprimierung, gemeinsamer
Session-Schluessel fuer mehrere Worker und Wiederherstellen eines Backups
im WAL-Modus."""
import multiprocessing
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi.testclient import TestClient

import app.database as database
import app.services.backup_service as backup_service
from app.database import enable_wal, init_db
from app.services.session_secret_service import load_or_create_session_secret


def _secret_worker(path, queue):
    queue.put(load_or_create_session_secret(Path(path)))


class EnableWalTests(unittest.TestCase):
    def test_database_switches_to_wal_and_stays_there(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "wal.db"
            init_db(db_path)
            self.assertEqual(enable_wal(db_path), "wal")
            self.assertEqual(enable_wal(db_path), "wal")
            conn = sqlite3.connect(db_path)
            try:
                self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            finally:
                conn.close()

    def test_reader_is_not_blocked_by_open_write_transaction(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "wal.db"
            init_db(db_path)
            enable_wal(db_path)
            writer = sqlite3.connect(db_path)
            reader = sqlite3.connect(db_path, timeout=0.1)
            try:
                writer.execute("BEGIN IMMEDIATE")
                writer.execute("INSERT INTO teams (name, jahrgang) VALUES ('7z', 7)")
                # Ohne WAL wuerde hier nach dem Schreiben ein Leser warten
                # bzw. "database is locked" bekommen; mit WAL liest er den
                # letzten bestaetigten Stand.
                count = reader.execute("SELECT COUNT(*) FROM teams WHERE name = '7z'").fetchone()[0]
                self.assertEqual(count, 0)
                writer.commit()
                count = reader.execute("SELECT COUNT(*) FROM teams WHERE name = '7z'").fetchone()[0]
                self.assertEqual(count, 1)
            finally:
                writer.close()
                reader.close()


class StartupTests(unittest.TestCase):
    def test_startup_enables_wal_on_the_app_database(self):
        from app.main import app as fastapi_app

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "startup.db"
            with patch.object(database, "DB_PATH", db_path):
                with TestClient(fastapi_app):
                    pass
                conn = sqlite3.connect(db_path)
                try:
                    self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
                finally:
                    conn.close()
                # Die Sperrdatei liegt neben der Test-DB, nicht im Repo.
                self.assertTrue((Path(tmp) / ".startup.lock").exists())


class GzipTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._patcher = patch.object(database, "DB_PATH", Path(self._tmpdir.name) / "gzip.db")
        self._patcher.start()
        init_db()

    def tearDown(self):
        self._patcher.stop()
        self._tmpdir.cleanup()

    def test_pages_are_compressed_and_keep_cache_headers(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            for path in ("/", "/spielplan", "/tabellen", "/static/style.css"):
                response = client.get(path, headers={"Accept-Encoding": "gzip"})
                self.assertEqual(response.status_code, 200, path)
                self.assertEqual(response.headers.get("content-encoding"), "gzip", path)
            response = client.get("/spielplan", headers={"Accept-Encoding": "gzip"})
            self.assertEqual(response.headers.get("cache-control"), "public, max-age=0, s-maxage=15")
            vary = response.headers.get("vary", "")
            self.assertIn("Cookie", vary)
            self.assertIn("Accept-Encoding", vary)
            # Gleicher Inhalt wie ohne Komprimierung.
            plain = client.get("/spielplan", headers={"Accept-Encoding": "identity"})
            self.assertIsNone(plain.headers.get("content-encoding"))
            self.assertEqual(response.text, plain.text)

    def test_json_answers_still_decode(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get("/durchsage/aktuell", headers={"Accept-Encoding": "gzip"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {})


class SessionSecretTests(unittest.TestCase):
    def test_secret_is_created_once_and_reused(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "sub" / ".session_secret"
            first = load_or_create_session_secret(path)
            self.assertGreaterEqual(len(first), 32)
            self.assertEqual(load_or_create_session_secret(path), first)
            self.assertEqual(path.read_text(encoding="utf-8"), first)
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_parallel_workers_get_the_same_secret(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / ".session_secret"
            ctx = multiprocessing.get_context("spawn")
            queue = ctx.Queue()
            workers = [ctx.Process(target=_secret_worker, args=(str(path), queue)) for _ in range(4)]
            for worker in workers:
                worker.start()
            results = [queue.get(timeout=30) for _ in workers]
            for worker in workers:
                worker.join(timeout=30)
            self.assertEqual(len(set(results)), 1)
            self.assertEqual(path.read_text(encoding="utf-8"), results[0])


class RestoreInWalModeTests(unittest.TestCase):
    def test_restore_replaces_content_of_wal_database(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp = Path(tmp)
            db_path = tmp / "live.db"
            with (
                patch.object(database, "DB_PATH", db_path),
                patch.object(backup_service, "DB_PATH", db_path),
                patch.object(backup_service, "BACKUP_DIR", tmp / "backups"),
            ):
                init_db()
                enable_wal()
                conn = sqlite3.connect(db_path)
                conn.execute("INSERT INTO teams (name, jahrgang) VALUES ('7a', 7)")
                conn.commit()
                conn.close()

                name = backup_service.create_database_backup()

                # Eine offene Verbindung (wie ein anderer Worker) bleibt bestehen.
                other = sqlite3.connect(db_path)
                other.execute("INSERT INTO teams (name, jahrgang) VALUES ('7b', 7)")
                other.commit()

                self.assertEqual(backup_service.restore_database_backup(name), "ok")

                names = [row[0] for row in other.execute("SELECT name FROM teams ORDER BY name")]
                other.close()
                self.assertEqual(names, ["7a"])
                check = sqlite3.connect(db_path)
                try:
                    self.assertEqual(check.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                    self.assertEqual(check.execute("PRAGMA journal_mode").fetchone()[0], "wal")
                finally:
                    check.close()
                leftovers = [p.name for p in tmp.iterdir() if p.name.endswith(".db.tmp")]
                self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
