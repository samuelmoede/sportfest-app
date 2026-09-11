from datetime import datetime, timezone

from app.database import get_conn
from app.utils.password_hashing import hash_password, verify_password

# "viewer" ist kein zuweisbarer Account-Status (jeder nicht angemeldete
# Besucher ist automatisch Viewer) - siehe ROLES in settings_service.py.
ASSIGNABLE_ROLES = ("admin", "tournament_lead", "referee", "station_helper")


def normalize_username(username: str) -> str:
    """Benutzernamen sind case-insensitiv (Kürzel wie "MOSA" werden
    kanonisch in Grossbuchstaben gespeichert und verglichen) - beim
    Passwort ist Gross-/Kleinschreibung dagegen weiterhin relevant."""
    return (username or "").strip().upper()


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def get_user_by_username(username: str):
    normalized = normalize_username(username)
    if not normalized:
        return None
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE username = ? AND active = 1",
            (normalized,),
        ).fetchone()


def get_user_by_id(user_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def list_users():
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users ORDER BY active DESC, username"
        ).fetchall()


def count_active_admins(conn, exclude_user_id=None):
    query = "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND active = 1"
    params = ()
    if exclude_user_id is not None:
        query += " AND id != ?"
        params = (exclude_user_id,)
    return conn.execute(query, params).fetchone()["n"]


def verify_login(username: str, password: str):
    """Gibt die users-Zeile zurueck, wenn Benutzername (case-insensitiv)
    und Passwort (case-sensitiv) zusammenpassen, sonst None."""
    user = get_user_by_username(username)
    if user is None:
        return None
    if not verify_password(password, user["password_hash"]):
        return None
    return user


def verify_user_password(user_id, password: str) -> bool:
    """Prueft das Passwort eines konkreten (bereits ueber die Session
    identifizierten) Benutzers - z.B. fuer die Re-Bestaetigung beim
    Umschalten der Sicherheit in den Einstellungen."""
    if user_id is None:
        return False
    user = get_user_by_id(user_id)
    if user is None or not user["active"]:
        return False
    return verify_password(password, user["password_hash"])


def has_prepared_admin_user() -> bool:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM users
            WHERE role = 'admin' AND active = 1 AND password_hash != ''
            LIMIT 1
            """
        ).fetchone()
    return row is not None


def create_user(username: str, password: str, role: str):
    normalized = normalize_username(username)
    if not normalized:
        return "invalid_username"
    if role not in ASSIGNABLE_ROLES:
        return "invalid_role"
    if not password:
        return "invalid_password"
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT 1 FROM users WHERE username = ?", (normalized,)
        ).fetchone()
        if existing:
            return "duplicate_username"
        conn.execute(
            """
            INSERT INTO users (username, password_hash, role, active, created_at)
            VALUES (?, ?, ?, 1, ?)
            """,
            (normalized, hash_password(password), role, _now()),
        )
        conn.commit()
    return "ok"


def update_user_role(user_id: int, role: str):
    if role not in ASSIGNABLE_ROLES:
        return "invalid_role"
    with get_conn() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if user is None:
            return "not_found"
        if (
            user["role"] == "admin"
            and role != "admin"
            and user["active"]
            and count_active_admins(conn, exclude_user_id=user_id) == 0
        ):
            return "last_admin"
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        conn.commit()
    return "ok"


def set_user_password(user_id: int, new_password: str):
    if not new_password:
        return "invalid_password"
    with get_conn() as conn:
        result = conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(new_password), user_id),
        )
        conn.commit()
        if result.rowcount == 0:
            return "not_found"
    return "ok"


def set_user_active(user_id: int, active: bool):
    with get_conn() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if user is None:
            return "not_found"
        if (
            not active
            and user["role"] == "admin"
            and user["active"]
            and count_active_admins(conn, exclude_user_id=user_id) == 0
        ):
            return "last_admin"
        conn.execute(
            "UPDATE users SET active = ? WHERE id = ?", (1 if active else 0, user_id)
        )
        conn.commit()
    return "ok"
