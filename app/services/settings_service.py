from datetime import datetime
import html
import os
import re
import secrets
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Request

from app.database import DB_PATH, get_conn
from app.services.backup_service import list_backup_files
from app.utils.formatting import format_bytes
from app.web import ROOT_DIR, get_app_version


DEFAULT_BEAMER_REFRESH_SECONDS = 30
DEFAULT_DASHBOARD_INFO_TEXT = "Achtung - bei Regen wird Plan B durchgeführt."
DEFAULT_SECURITY_ENABLED = False
SITE_THEMES = {
    "standard": "Standard",
    "dunkel": "Dunkel",
}
DEFAULT_SITE_THEME = "standard"
ALLOWED_BOLD_TAG_RE = re.compile(r"</?(?:b|strong)>", re.IGNORECASE)
TRUE_SETTING_VALUES = {"1", "true", "yes", "on", "ja"}
ROLES = {"viewer", "station_helper", "referee", "admin"}
ROLE_LABELS = {
    "viewer": "Viewer",
    "station_helper": "Stationshelfer",
    "referee": "Schiedsrichter",
    "admin": "Admin",
}
ROLE_DESCRIPTIONS = {
    "viewer": "\u00d6ffentliche Ansichten ohne Bearbeitungsrechte",
    "station_helper": "Stationsrolle vorbereitet, derzeit noch ohne Anmeldung und Stationsrechte",
    "referee": "Ergebnisse erfassen und Spieltimer bedienen",
    "admin": "Vollzugriff auf Verwaltung und Planung",
}
ROLE_ACCESS_LEVELS = {
    "viewer": 0,
    "station_helper": 0,
    "referee": 1,
    "admin": 2,
}


def parse_positive_int(value, default: int):
    try:
        parsed = int(value)
        if parsed > 0:
            return parsed
    except (TypeError, ValueError):
        pass
    return default


def get_setting(key: str, default: Optional[str] = None):
    with get_conn() as conn:
        setting = conn.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,),
        ).fetchone()
    return setting["value"] if setting else default


def set_setting(key: str, value: str):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        conn.commit()


def get_beamer_refresh_seconds():
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO settings (key, value)
            VALUES ('beamer_refresh_seconds', ?)
        """, (str(DEFAULT_BEAMER_REFRESH_SECONDS),))
        setting = conn.execute(
            "SELECT value FROM settings WHERE key = 'beamer_refresh_seconds'"
        ).fetchone()
    return parse_positive_int(
        setting["value"] if setting else None,
        DEFAULT_BEAMER_REFRESH_SECONDS,
    )


def set_beamer_refresh_seconds(value: int):
    set_setting("beamer_refresh_seconds", str(value))


def get_dashboard_info_text():
    return get_setting("dashboard_info_text", DEFAULT_DASHBOARD_INFO_TEXT) or ""


def set_dashboard_info_text(value: str):
    set_setting("dashboard_info_text", value or "")


def get_site_theme():
    value = get_setting("site_theme", DEFAULT_SITE_THEME)
    return value if value in SITE_THEMES else DEFAULT_SITE_THEME


def set_site_theme(value: str):
    if value not in SITE_THEMES:
        return False
    set_setting("site_theme", value)
    return True


def render_rich_text_html(value: str):
    """Escaped Text mit erlaubten <b>/<strong>-Tags und Zeilenumbrueche als
    <br> - genutzt sowohl fuer den Dashboard-Banner als auch fuer die
    Veranstaltungsdetails, damit beide dieselbe einfache Formatierung
    unterstuetzen."""
    text = str(value or "")
    placeholders = []

    def keep_allowed_tag(match):
        token = f"__SPORTFEST_RICH_TEXT_TAG_{len(placeholders)}__"
        placeholders.append((token, match.group(0).lower()))
        return token

    protected = ALLOWED_BOLD_TAG_RE.sub(keep_allowed_tag, text)
    rendered = html.escape(protected)
    for token, tag in placeholders:
        rendered = rendered.replace(token, tag)
    return (
        rendered
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "<br>")
    )


def get_dashboard_info_html():
    text = get_dashboard_info_text()
    if not text.strip():
        return ""
    return render_rich_text_html(text)


def get_security_environment_override():
    environment_value = os.getenv("SPORTFEST_SECURITY_ENABLED")
    if environment_value in (None, ""):
        return None
    return environment_value.strip().lower() in TRUE_SETTING_VALUES


def get_security_database_setting():
    raw_value = get_setting(
        "security_enabled",
        str(DEFAULT_SECURITY_ENABLED).lower(),
    )
    return str(raw_value).strip().lower() in TRUE_SETTING_VALUES


def get_security_enabled_setting():
    environment_override = get_security_environment_override()
    if environment_override is not None:
        return environment_override
    return get_security_database_setting()


def get_admin_password():
    environment_password = (
        os.getenv("SPORTFEST_ADMIN_PASSWORD")
        or os.getenv("ADMIN_PASSWORD")
    )
    if environment_password:
        return environment_password
    return get_setting("admin_password", "") or ""


def get_referee_password():
    environment_password = os.getenv("SPORTFEST_REFEREE_PASSWORD")
    if environment_password:
        return environment_password
    return get_setting("referee_password", "") or ""


PASSWORD_ROLES = {
    "admin": {
        "setting_key": "admin_password",
        "environment_variables": ("SPORTFEST_ADMIN_PASSWORD", "ADMIN_PASSWORD"),
        "get_password": get_admin_password,
    },
    "referee": {
        "setting_key": "referee_password",
        "environment_variables": ("SPORTFEST_REFEREE_PASSWORD",),
        "get_password": get_referee_password,
    },
}


def get_password_environment_override(role: str):
    config = PASSWORD_ROLES.get(role)
    if not config:
        return None
    for variable in config["environment_variables"]:
        value = os.getenv(variable)
        if value:
            return value
    return None


def change_role_password(role: str, current_password: str, new_password: str):
    """Aendert das gespeicherte Passwort einer Rolle, sofern das aktuelle
    Passwort korrekt bestaetigt wird. Ein per Umgebungsvariable gesetztes
    Passwort ueberschreibt die Datenbank immer (siehe get_admin_password /
    get_referee_password) - eine Aenderung waere daher wirkungslos und wird
    hier abgelehnt, statt still zu scheitern."""
    config = PASSWORD_ROLES.get(role)
    if not config:
        return "invalid_role"

    if get_password_environment_override(role) is not None:
        return "environment_override"

    configured_password = config["get_password"]()
    if not configured_password or not secrets.compare_digest(
        current_password or "", configured_password
    ):
        return "invalid_current_password"

    if not new_password:
        return "invalid_new_password"

    set_setting(config["setting_key"], new_password)
    return "ok"


def is_login_prepared():
    return bool(get_admin_password())


def is_security_enabled():
    return get_security_enabled_setting() and is_login_prepared()


def get_current_role(request: Request):
    role = request.session.get("role")
    if role in ROLES:
        return role

    if request.session.get("admin_logged_in") is True:
        return "admin"
    return "viewer"


def get_current_role_label(request: Request):
    return ROLE_LABELS[get_current_role(request)]


def get_current_role_description(request: Request):
    return ROLE_DESCRIPTIONS[get_current_role(request)]


def can_access_role(request: Request, required_role: str):
    if not is_security_enabled():
        return True
    current_role = get_current_role(request)
    if required_role == "station_helper":
        return current_role in {"station_helper", "admin"}
    if current_role == "station_helper":
        return required_role == "viewer"
    current_level = ROLE_ACCESS_LEVELS[current_role]
    required_level = ROLE_ACCESS_LEVELS[required_role]
    return current_level >= required_level


def is_logged_in(request: Request):
    return get_current_role(request) in {"station_helper", "referee", "admin"}


def get_recent_change_log(limit: int = 50):
    safe_limit = max(1, min(int(limit), 200))
    with get_conn() as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT log.*,
                       competition.name AS competition_name,
                       discipline.name AS discipline_name,
                       team.name AS team_name,
                       team_a.name AS team_a_name,
                       team_b.name AS team_b_name
                FROM change_log AS log
                LEFT JOIN competitions AS competition
                    ON competition.id = log.competition_id
                LEFT JOIN competition_disciplines AS discipline
                    ON discipline.id = log.discipline_id
                LEFT JOIN teams AS team
                    ON team.id = log.team_id
                LEFT JOIN slots AS slot
                    ON log.entity_type = 'slot_result'
                   AND slot.id = log.entity_id
                LEFT JOIN teams AS team_a
                    ON team_a.id = slot.team_a_id
                LEFT JOIN teams AS team_b
                    ON team_b.id = slot.team_b_id
                ORDER BY log.id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        ]

    for row in rows:
        try:
            row["created_at_display"] = datetime.strptime(
                row["created_at"], "%Y-%m-%d %H:%M:%S"
            ).strftime("%d.%m.%Y %H:%M:%S")
        except (TypeError, ValueError):
            row["created_at_display"] = row["created_at"]
        if row["entity_type"] == "slot_result":
            teams = " \u2013 ".join(
                name
                for name in (row.get("team_a_name"), row.get("team_b_name"))
                if name
            )
            row["target_label"] = teams or f"Spiel #{row['entity_id']}"
        else:
            parts = [
                row.get("discipline_name"),
                row.get("team_name"),
            ]
            row["target_label"] = " \u00b7 ".join(
                part for part in parts if part
            ) or f"Eintrag #{row['entity_id']}"
        row["actor_role_label"] = ROLE_LABELS.get(
            row["actor_role"], row["actor_role"]
        )
    return rows


def get_change_log_count():
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM change_log"
        ).fetchone()["n"]


def collect_system_info():
    db_reachable = False
    write_access = False

    with get_conn() as conn:
        conn.execute("SELECT 1").fetchone()
        db_reachable = True
        counts = {
            "events": conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"],
            "competitions": conn.execute("SELECT COUNT(*) AS n FROM competitions").fetchone()["n"],
            "teams": conn.execute("SELECT COUNT(*) AS n FROM teams").fetchone()["n"],
            "courts": conn.execute("SELECT COUNT(*) AS n FROM courts").fetchone()["n"],
            "slots": conn.execute("SELECT COUNT(*) AS n FROM slots").fetchone()["n"],
        }
        tournament_results_count = conn.execute("""
            SELECT COUNT(*) AS n
            FROM slots
            WHERE slot_typ = 'Spiel'
              AND (score_a IS NOT NULL OR score_b IS NOT NULL)
        """).fetchone()["n"]
        sixkampf_results_count = conn.execute(
            "SELECT COUNT(*) AS n FROM sixkampf_team_results"
        ).fetchone()["n"]

    db_size_bytes = DB_PATH.stat().st_size if DB_PATH.exists() else 0
    backups = list_backup_files()

    data_dir = DB_PATH.parent
    data_dir.mkdir(parents=True, exist_ok=True)
    write_probe_path = data_dir / f".write_probe_{int(datetime.now().timestamp() * 1000)}.tmp"
    try:
        write_probe_path.write_text("ok", encoding="utf-8")
        write_access = True
    except OSError:
        write_access = False
    finally:
        try:
            if write_probe_path.exists():
                write_probe_path.unlink()
        except OSError:
            pass

    return {
        "app_version_value": get_app_version(),
        "db_size_bytes": db_size_bytes,
        "db_size_display": format_bytes(db_size_bytes),
        "counts": counts,
        "results_count": tournament_results_count + sixkampf_results_count,
        "last_backup": backups[0] if backups else None,
        "backup_count": len(backups),
        "backups": backups,
        "db_reachable": db_reachable,
        "write_access": write_access,
        "beamer_refresh_seconds": get_beamer_refresh_seconds(),
        "dashboard_info_text": get_dashboard_info_text(),
    }


def load_documentation_text():
    documentation_path = ROOT_DIR / "DOKUMENTATION.md"
    try:
        return documentation_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "Die Dokumentation wurde nicht gefunden."


def load_roadmap_text():
    roadmap_path = ROOT_DIR / "ROADMAP.md"
    try:
        return roadmap_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "Die Roadmap wurde nicht gefunden."


def app_now_db_timestamp():
    timezone_name = os.getenv("SPORTFEST_TIMEZONE", "Europe/Berlin")
    try:
        timezone = ZoneInfo(timezone_name)
        now = datetime.now(timezone)
    except ZoneInfoNotFoundError:
        now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S")