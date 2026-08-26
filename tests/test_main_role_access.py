import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import app.database as database


class EventDetailRoleExceptionTests(unittest.TestCase):
    """Turnierleitung darf die Gesamtwertung/Siegerehrung auf /events/{id}
    einsehen, obwohl /events sonst komplett admin-only ist (siehe
    ACTION_ACCESS_RULES/AREA_ACCESS_RULES und get_required_role() in
    app/main.py). Die restlichen /events-Pfade (Liste, anlegen, bearbeiten,
    loeschen, Siegerehrung-Sichtbarkeit umschalten) bleiben admin-only."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "main-role-access-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_event_detail_requires_only_tournament_lead(self):
        from app.main import get_required_role

        self.assertEqual(get_required_role("/events/42"), "tournament_lead")

    def test_other_event_paths_stay_admin_only(self):
        from app.main import get_required_role

        for path in (
            "/events",
            "/events/new",
            "/events/42/edit",
            "/events/42/update",
            "/events/42/delete",
            "/events/42/archive",
            "/events/42/siegerehrung-public",
            "/events/42/siegerehrung-private",
        ):
            self.assertEqual(get_required_role(path), "admin", path)

    def test_existing_referee_and_admin_rules_are_unaffected(self):
        from app.main import get_required_role

        self.assertEqual(get_required_role("/ergebnisse"), "referee")
        self.assertEqual(get_required_role("/wettbewerbe"), "admin")
        self.assertEqual(get_required_role("/spielfelder"), "admin")
        self.assertEqual(get_required_role("/teams"), "admin")
        self.assertIsNone(get_required_role("/tabellen"))
        self.assertIsNone(get_required_role("/"))


if __name__ == "__main__":
    unittest.main()
