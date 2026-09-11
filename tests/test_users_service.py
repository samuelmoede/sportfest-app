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
from app.services.users_service import (
    ASSIGNABLE_ROLES,
    count_active_admins,
    create_user,
    get_user_by_id,
    get_user_by_username,
    has_prepared_admin_user,
    list_users,
    normalize_username,
    set_user_active,
    set_user_password,
    update_user_role,
    verify_login,
    verify_user_password,
)


class UsersServiceTests(unittest.TestCase):
    """Benutzerbasierter Login (loest das alte geteilte Rollen-Passwort ab,
    siehe app/main.py /login): jeder Benutzer hat Benutzername + Passwort +
    genau eine Rolle. Migration (siehe database.py init_db()) legt beim
    ersten Start immer 'ADMIN' und 'MOSA' an - hier direkt gegen eine leere
    Instanz getestet, ohne Env-Var-Passwoerter."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "users-service-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_migration_seeds_admin_and_mosa(self):
        users = {u["username"]: u for u in list_users()}
        self.assertIn("ADMIN", users)
        self.assertIn("MOSA", users)
        self.assertEqual(users["ADMIN"]["role"], "admin")
        self.assertEqual(users["MOSA"]["role"], "referee")
        self.assertEqual(users["ADMIN"]["active"], 1)

    def test_migration_is_idempotent_and_does_not_duplicate(self):
        init_db()
        init_db()
        self.assertEqual(len(list_users()), 2)

    def test_create_user_then_verify_login(self):
        status = create_user("mosa2", "GeheimesPw1", "tournament_lead")
        self.assertEqual(status, "ok")
        user = verify_login("mosa2", "GeheimesPw1")
        self.assertIsNotNone(user)
        self.assertEqual(user["role"], "tournament_lead")

    def test_username_lookup_is_case_insensitive(self):
        create_user("wxyz", "pw12345", "referee")
        self.assertIsNotNone(get_user_by_username("wxyz"))
        self.assertIsNotNone(get_user_by_username("WXYZ"))
        self.assertIsNotNone(get_user_by_username("WxYz"))
        self.assertIsNotNone(verify_login("wxyz", "pw12345"))
        self.assertIsNotNone(verify_login("WXYZ", "pw12345"))

    def test_password_is_case_sensitive(self):
        create_user("wxyz", "GeheimesPw1", "referee")
        self.assertIsNone(verify_login("wxyz", "geheimespw1"))
        self.assertIsNone(verify_login("wxyz", "GEHEIMESPW1"))
        self.assertIsNotNone(verify_login("wxyz", "GeheimesPw1"))

    def test_normalize_username_strips_and_uppercases(self):
        self.assertEqual(normalize_username("  mosa  "), "MOSA")
        self.assertEqual(normalize_username(""), "")
        self.assertEqual(normalize_username(None), "")

    def test_verify_login_with_wrong_password_returns_none(self):
        self.assertIsNone(verify_login("ADMIN", "falsches-passwort"))

    def test_verify_login_with_unknown_username_returns_none(self):
        self.assertIsNone(verify_login("NICHT_VORHANDEN", "irgendwas"))

    def test_create_user_rejects_duplicate_username_case_insensitively(self):
        create_user("wxyz", "pw12345", "referee")
        status = create_user("WXYZ", "anderes-pw", "admin")
        self.assertEqual(status, "duplicate_username")

    def test_create_user_rejects_empty_password(self):
        status = create_user("wxyz", "", "referee")
        self.assertEqual(status, "invalid_password")

    def test_create_user_rejects_invalid_role(self):
        status = create_user("wxyz", "pw12345", "turnierleitung")
        self.assertEqual(status, "invalid_role")

    def test_create_user_rejects_empty_username(self):
        status = create_user("   ", "pw12345", "referee")
        self.assertEqual(status, "invalid_username")

    def test_all_assignable_roles_exclude_viewer(self):
        self.assertNotIn("viewer", ASSIGNABLE_ROLES)
        for role in ("admin", "tournament_lead", "referee", "station_helper"):
            self.assertIn(role, ASSIGNABLE_ROLES)

    def test_update_user_role_changes_role(self):
        create_user("wxyz", "pw12345", "referee")
        user = get_user_by_username("wxyz")
        status = update_user_role(user["id"], "tournament_lead")
        self.assertEqual(status, "ok")
        self.assertEqual(get_user_by_id(user["id"])["role"], "tournament_lead")

    def test_set_user_password_changes_password(self):
        create_user("wxyz", "altes-pw12", "referee")
        user = get_user_by_username("wxyz")
        status = set_user_password(user["id"], "neues-pw12")
        self.assertEqual(status, "ok")
        self.assertIsNone(verify_login("wxyz", "altes-pw12"))
        self.assertIsNotNone(verify_login("wxyz", "neues-pw12"))

    def test_set_user_active_deactivates_and_blocks_login(self):
        create_user("wxyz", "pw12345", "referee")
        user = get_user_by_username("wxyz")
        status = set_user_active(user["id"], False)
        self.assertEqual(status, "ok")
        self.assertIsNone(get_user_by_username("wxyz"))
        self.assertIsNone(verify_login("wxyz", "pw12345"))

    def test_cannot_deactivate_last_active_admin(self):
        admin = get_user_by_username("ADMIN")
        status = set_user_active(admin["id"], False)
        self.assertEqual(status, "last_admin")
        self.assertEqual(get_user_by_id(admin["id"])["active"], 1)

    def test_cannot_demote_last_active_admin(self):
        admin = get_user_by_username("ADMIN")
        status = update_user_role(admin["id"], "referee")
        self.assertEqual(status, "last_admin")
        self.assertEqual(get_user_by_id(admin["id"])["role"], "admin")

    def test_second_admin_allows_deactivating_the_first(self):
        create_user("second_admin", "pw12345", "admin")
        admin = get_user_by_username("ADMIN")
        status = set_user_active(admin["id"], False)
        self.assertEqual(status, "ok")

    def test_verify_user_password_matches_own_account(self):
        create_user("wxyz", "pw12345", "referee")
        user = get_user_by_username("wxyz")
        self.assertTrue(verify_user_password(user["id"], "pw12345"))
        self.assertFalse(verify_user_password(user["id"], "falsch"))
        self.assertFalse(verify_user_password(None, "pw12345"))

    def test_has_prepared_admin_user_true_after_migration_with_env_password(self):
        with patch.dict("os.environ", {"SPORTFEST_ADMIN_PASSWORD": "geheim"}, clear=False):
            tmp_db_path = Path(self._tmpdir.name) / "users-service-test-env.db"
            with patch.object(database, "DB_PATH", tmp_db_path):
                init_db()
                self.assertTrue(has_prepared_admin_user())

    def test_has_prepared_admin_user_false_without_configured_password(self):
        # setUp() initialisiert ohne SPORTFEST_ADMIN_PASSWORD - ADMIN existiert,
        # hat aber keinen Passwort-Hash.
        self.assertFalse(has_prepared_admin_user())

    def test_count_active_admins_excludes_deactivated(self):
        create_user("second_admin", "pw12345", "admin")
        with get_conn() as conn:
            self.assertEqual(count_active_admins(conn), 2)
        second = get_user_by_username("second_admin")
        set_user_active(second["id"], False)
        with get_conn() as conn:
            self.assertEqual(count_active_admins(conn), 1)


if __name__ == "__main__":
    unittest.main()
