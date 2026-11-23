"""Gesamtwertung (normales Sportfest): Gruppen kommen aus den vorhandenen
Jahrgaengen. Jahrgang 10 gehoert zur Sek I (Samuel, 2026-11-23), nur 11-13
sind GOST; Jahrgang 5/6 fielen frueher still heraus."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import app.database as database
from app.database import get_conn, init_db


class GesamtwertungGruppenTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._patcher = patch.object(
            database, "DB_PATH", Path(self._tmpdir.name) / "gesamtwertung.db"
        )
        self._patcher.start()
        init_db()

    def tearDown(self):
        self._patcher.stop()
        self._tmpdir.cleanup()

    def test_gruppen_6_10_und_gost(self):
        from app.main import calculate_event_overall_ranking

        with get_conn() as conn:
            event_id = conn.execute(
                "INSERT INTO events (name, event_date, event_type) VALUES ('Sportfest', '2026-06-01', 'Sportfest')"
            ).lastrowid
            for jahrgang in (6, 10, 11):
                comp_id = conn.execute(
                    """
                    INSERT INTO competitions (name, sportart, jahrgang, status, competition_type, event_id)
                    VALUES (?, 'Sechskampf', ?, 'geplant', 'Sechskampf', ?)
                    """,
                    (f"Sechskampf {jahrgang}", jahrgang, event_id),
                ).lastrowid
                disc_id = conn.execute(
                    "INSERT INTO competition_disciplines (competition_id, name) VALUES (?, 'Wurf')",
                    (comp_id,),
                ).lastrowid
                for suffix, wert in (("a", 30.0), ("b", 25.0)):
                    team_id = conn.execute(
                        "INSERT INTO teams (name, jahrgang) VALUES (?, ?)",
                        (f"{jahrgang}{suffix}", jahrgang),
                    ).lastrowid
                    conn.execute(
                        """
                        INSERT INTO sixkampf_team_results
                            (competition_id, discipline_id, team_id, value_index, value)
                        VALUES (?, ?, ?, 1, ?)
                        """,
                        (comp_id, disc_id, team_id, wert),
                    )
            conn.commit()

        gruppen = [g["year_group"] for g in calculate_event_overall_ranking(event_id)]
        self.assertEqual(gruppen, [
            "Gesamtwertung Jahrgang 6",
            "Gesamtwertung Jahrgang 10",
            "Gesamtwertung GOST",
        ])
