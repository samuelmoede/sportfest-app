"""Wettbewerbsnamen sind nur innerhalb einer Veranstaltung eindeutig
(vorher: in der ganzen Datenbank). Prueft die einmalige Schema-Umstellung
bestehender Datenbanken und die Namenspruefung in den Routen/Services."""
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
from app.database import get_conn, init_db
from app.services.competition_name_service import (
    competition_name_vergeben,
    freier_competition_name,
)

NAME = "Zweifelderball Jahrgang 7"


class _TempDbTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self._tmpdir.name) / "namen-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", self.db_path)
        self._db_path_patcher.start()
        init_db()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def _event(self, conn, name):
        return conn.execute("INSERT INTO events (name) VALUES (?)", (name,)).lastrowid

    def _competition(self, conn, name, event_id, jahrgang=7):
        return conn.execute(
            """
            INSERT INTO competitions (name, sportart, jahrgang, status, event_id,
                                      competition_type, tournament_mode)
            VALUES (?, 'Zweifelderball', ?, 'geplant', ?, 'Turnier', 'gruppen_ko')
            """,
            (name, jahrgang, event_id),
        ).lastrowid


class SchemaMigrationTests(_TempDbTestCase):
    def _alte_regel_herstellen(self):
        """Baut die Tabelle so um, wie sie vor der Umstellung aussah:
        name ... UNIQUE, kein Index je Veranstaltung."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='competitions'"
        ).fetchone()["sql"]
        self.assertNotIn("UNIQUE", sql)
        old_sql = sql.replace("name TEXT NOT NULL,", "name TEXT NOT NULL UNIQUE,", 1)
        self.assertIn("UNIQUE", old_sql)
        conn.execute("DROP INDEX idx_competitions_event_name")
        conn.execute("ALTER TABLE competitions RENAME TO competitions_tmp")
        conn.execute(old_sql)
        conn.execute("INSERT INTO competitions SELECT * FROM competitions_tmp")
        conn.execute("DROP TABLE competitions_tmp")
        conn.commit()
        conn.close()

    def test_bestehende_datenbank_wird_ohne_datenverlust_umgestellt(self):
        self._alte_regel_herstellen()
        with get_conn() as conn:
            alt = self._event(conn, "Schulpokal 2025")
            neu = self._event(conn, "15. Schulpokal")
            first = self._competition(conn, NAME, alt)
            second = self._competition(conn, f"{NAME} (2)", neu)
            geloescht = self._competition(conn, "Wird geloescht", neu)
            conn.execute("DELETE FROM competitions WHERE id = ?", (geloescht,))
            conn.execute(
                "INSERT INTO slots (competition_id, startzeit, slot_typ, status, phase) "
                "VALUES (?, '08:00', 'Leer', 'geplant', 'Gruppenphase')", (second,)
            )
            with self.assertRaises(sqlite3.IntegrityError):
                self._competition(conn, NAME, neu)
            conn.commit()
            columns_before = [tuple(r) for r in conn.execute("PRAGMA table_info(competitions)")]
            rows_before = [tuple(r) for r in conn.execute("SELECT * FROM competitions ORDER BY id")]

        init_db()
        init_db()  # zweiter Lauf darf nichts mehr aendern

        with get_conn() as conn:
            self.assertEqual(
                [tuple(r) for r in conn.execute("PRAGMA table_info(competitions)")], columns_before
            )
            self.assertEqual(
                [tuple(r) for r in conn.execute("SELECT * FROM competitions ORDER BY id")], rows_before
            )
            self.assertEqual(
                conn.execute("SELECT competition_id FROM slots").fetchone()[0], second
            )
            self.assertIsNone(
                conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'competitions_neu'").fetchone()
            )
            # Jetzt geht der Name ohne "(2)" - der Wettbewerb der anderen
            # Veranstaltung bleibt unberuehrt.
            conn.execute("UPDATE competitions SET name = ? WHERE id = ?", (NAME, second))
            self.assertEqual(
                conn.execute("SELECT name FROM competitions WHERE id = ?", (first,)).fetchone()[0], NAME
            )
            # ids geloeschter Wettbewerbe werden nicht neu vergeben.
            self.assertGreater(self._competition(conn, "Neu", neu), geloescht)

    def test_innerhalb_einer_veranstaltung_bleibt_der_name_eindeutig(self):
        with get_conn() as conn:
            event_id = self._event(conn, "15. Schulpokal")
            self._competition(conn, NAME, event_id)
            with self.assertRaises(sqlite3.IntegrityError):
                self._competition(conn, NAME, event_id)
            self._competition(conn, "Ohne Veranstaltung", None)
            with self.assertRaises(sqlite3.IntegrityError):
                self._competition(conn, "Ohne Veranstaltung", None)

    def test_namenspruefung_gilt_je_veranstaltung(self):
        with get_conn() as conn:
            alt = self._event(conn, "Schulpokal 2025")
            neu = self._event(conn, "15. Schulpokal")
            first = self._competition(conn, NAME, alt)
            self.assertTrue(competition_name_vergeben(conn, NAME, alt))
            self.assertFalse(competition_name_vergeben(conn, NAME, alt, ausser_id=first))
            self.assertFalse(competition_name_vergeben(conn, NAME, neu))
            self.assertFalse(competition_name_vergeben(conn, NAME, None))
            self.assertEqual(freier_competition_name(conn, NAME, neu), NAME)
            self.assertEqual(freier_competition_name(conn, NAME, alt), f"{NAME} (2)")


class RouteTests(_TempDbTestCase):
    def setUp(self):
        super().setUp()
        from app.main import app
        self.client = TestClient(app)
        with get_conn() as conn:
            self.alt = self._event(conn, "Schulpokal 2025")
            self.neu = self._event(conn, "15. Schulpokal")
            conn.execute("INSERT INTO teams (name, jahrgang, active) VALUES ('7a', 7, 1)")
            self.first = self._competition(conn, NAME, self.alt)
            self.second = self._competition(conn, f"{NAME} (2)", self.neu)
            self.other = self._competition(conn, "Zweifelderball Jahrgang 8", self.neu, 8)
            conn.commit()

    def _update(self, competition_id, name, event_id):
        return self.client.post(
            f"/competition/{competition_id}/update",
            data={
                "name": name, "sportart": "Zweifelderball", "jahrgang": "7",
                "status": "geplant", "points_win": "3", "points_draw": "1",
                "points_loss": "0", "event_id": str(event_id),
                "competition_type": "Turnier", "tournament_mode": "gruppen_ko",
            },
            follow_redirects=False,
        )

    def _name(self, competition_id):
        with get_conn() as conn:
            return conn.execute(
                "SELECT name FROM competitions WHERE id = ?", (competition_id,)
            ).fetchone()[0]

    def test_umbenennen_auf_namen_einer_anderen_veranstaltung(self):
        self._update(self.second, NAME, self.neu)
        self.assertEqual(self._name(self.second), NAME)
        self.assertEqual(self._name(self.first), NAME)

    def test_umbenennen_auf_namen_derselben_veranstaltung_bleibt_gesperrt(self):
        self._update(self.other, f"{NAME} (2)", self.neu)
        self.assertEqual(self._name(self.other), "Zweifelderball Jahrgang 8")

    def test_verschieben_in_veranstaltung_mit_gleichem_namen_bleibt_gesperrt(self):
        self._update(self.second, NAME, self.alt)
        self.assertEqual(self._name(self.second), f"{NAME} (2)")

    def test_wettbewerb_duplizieren_haengt_kopie_an(self):
        self.client.post(f"/competition/{self.first}/duplicate", follow_redirects=False)
        with get_conn() as conn:
            names = [r[0] for r in conn.execute(
                "SELECT name FROM competitions WHERE event_id = ? ORDER BY id", (self.alt,)
            )]
        self.assertEqual(names, [NAME, f"{NAME} (Kopie)"])

    def test_veranstaltung_duplizieren_behaelt_wettbewerbsnamen(self):
        self.client.post(f"/events/{self.neu}/duplicate", follow_redirects=False)
        with get_conn() as conn:
            new_event = conn.execute("SELECT MAX(id) FROM events").fetchone()[0]
            self.assertNotIn(new_event, (self.alt, self.neu))
            names = [r[0] for r in conn.execute(
                "SELECT name FROM competitions WHERE event_id = ? ORDER BY id", (new_event,)
            )]
        self.assertEqual(names, [f"{NAME} (2)", "Zweifelderball Jahrgang 8"])


if __name__ == "__main__":
    unittest.main()
