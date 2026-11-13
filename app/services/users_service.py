import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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


# --- Angemeldete Sitzungen ---------------------------------------------
# Die Login-Session selbst steckt weiterhin im signierten Cookie
# (SessionMiddleware). Damit Admins in den Einstellungen sehen koennen, wer
# gerade angemeldet ist und wann zuletzt aktiv, bekommt jede Anmeldung
# zusaetzlich ein zufaelliges Token (session["session_token"]) und eine
# Zeile in user_sessions. Abmelden setzt ended_at; eine Sitzung ohne
# Aktivitaet laenger als SESSION_MAX_AGE_SECONDS ist auch ohne Abmelden
# abgelaufen (das Cookie selbst laeuft dann ebenfalls ab).

SESSION_MAX_AGE_SECONDS = 14 * 24 * 60 * 60
SESSION_TOUCH_INTERVAL_SECONDS = 60
SESSION_ONLINE_SECONDS = 5 * 60

DISPLAY_TIMEZONE = ZoneInfo(os.getenv("SPORTFEST_TIMEZONE", "Europe/Berlin"))

# Prozess-lokale Drosselung, damit nicht jede einzelne Anfrage einen
# Schreibzugriff auf die Datenbank ausloest (die App laeuft als
# Einzelprozess, siehe Login-Rate-Limit in app/main.py).
_last_session_touch: dict = {}


def _parse_utc(value):
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def start_user_session(user_id: int) -> str:
    token = secrets.token_urlsafe(24)
    now = _now()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO user_sessions (token, user_id, created_at, last_seen_at)
            VALUES (?, ?, ?, ?)
            """,
            (token, user_id, now, now),
        )
        conn.commit()
    _last_session_touch[token] = time.monotonic()
    return token


def end_user_session(token) -> None:
    if not token:
        return
    with get_conn() as conn:
        conn.execute(
            "UPDATE user_sessions SET ended_at = ? WHERE token = ? AND ended_at IS NULL",
            (_now(), token),
        )
        conn.commit()
    _last_session_touch.pop(token, None)


def touch_user_session(token, user_id, force: bool = False) -> None:
    """Aktualisiert die letzte Aktivitaet einer Sitzung (hoechstens einmal
    pro SESSION_TOUCH_INTERVAL_SECONDS). Fehlt die Zeile (z.B. Login von
    vor Einfuehrung dieser Tabelle), wird sie nachtraeglich angelegt."""
    if not token or user_id is None:
        return
    now_monotonic = time.monotonic()
    last = _last_session_touch.get(token)
    if not force and last is not None and now_monotonic - last < SESSION_TOUCH_INTERVAL_SECONDS:
        return
    _last_session_touch[token] = now_monotonic
    now = _now()
    with get_conn() as conn:
        result = conn.execute(
            "UPDATE user_sessions SET last_seen_at = ? WHERE token = ? AND ended_at IS NULL",
            (now, token),
        )
        if result.rowcount == 0:
            conn.execute(
                """
                INSERT OR IGNORE INTO user_sessions (token, user_id, created_at, last_seen_at)
                VALUES (?, ?, ?, ?)
                """,
                (token, user_id, now, now),
            )
        conn.commit()


def list_logged_in_sessions(now=None):
    """Alle offenen (nicht abgemeldeten, nicht abgelaufenen) Sitzungen,
    zuletzt aktive zuerst - mit lokal formatierten Zeiten fuer die
    Einstellungsseite."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(seconds=SESSION_MAX_AGE_SECONDS)).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT s.token, s.created_at, s.last_seen_at,
                   u.id AS user_id, u.username, u.role, u.active
            FROM user_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.ended_at IS NULL AND s.last_seen_at >= ?
            ORDER BY s.last_seen_at DESC
            """,
            (cutoff,),
        ).fetchall()

    today = now.astimezone(DISPLAY_TIMEZONE).date()
    sessions = []
    for row in rows:
        last_seen = _parse_utc(row["last_seen_at"])
        created = _parse_utc(row["created_at"])
        sessions.append({
            "user_id": row["user_id"],
            "username": row["username"],
            "role": row["role"],
            "active": bool(row["active"]),
            "logged_in_at": _format_local(created, today),
            "last_seen_at": _format_local(last_seen, today),
            "online": (now - last_seen).total_seconds() <= SESSION_ONLINE_SECONDS,
        })
    return sessions


def _format_local(value, today):
    local = value.astimezone(DISPLAY_TIMEZONE)
    if local.date() == today:
        return local.strftime("%H:%M")
    return local.strftime("%d.%m. %H:%M")


def get_last_activity_by_user(now=None):
    """Letzte Aktivitaet je Benutzer ueber alle (auch beendete) Sitzungen -
    {user_id: "HH:MM" bzw. "TT.MM. HH:MM"} fuer die Benutzertabelle."""
    now = now or datetime.now(timezone.utc)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT user_id, MAX(last_seen_at) AS last_seen_at FROM user_sessions GROUP BY user_id"
        ).fetchall()
    today = now.astimezone(DISPLAY_TIMEZONE).date()
    return {
        row["user_id"]: _format_local(_parse_utc(row["last_seen_at"]), today)
        for row in rows
    }
