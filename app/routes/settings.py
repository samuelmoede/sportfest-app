from datetime import datetime
import secrets

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.services.backup_service import (
    create_database_backup,
    delete_backup_file,
    restore_database_backup,
)
from app.services.settings_service import (
    ROLE_DESCRIPTIONS,
    ROLE_LABELS,
    app_now_db_timestamp,
    collect_system_info,
    get_admin_password,
    get_change_log_count,
    get_current_role,
    get_current_role_description,
    get_current_role_label,
    get_referee_password,
    get_recent_change_log,
    get_security_enabled_setting,
    get_security_environment_override,
    get_site_theme,
    is_logged_in,
    is_login_prepared,
    is_security_enabled,
    load_documentation_text,
    load_roadmap_text,
    set_beamer_refresh_seconds,
    set_dashboard_info_text,
    set_setting,
    set_site_theme,
    SITE_THEMES,
)
from app.database import get_conn
from app.web import templates


router = APIRouter()


@router.get("/einstellungen")
def einstellungen(
    request: Request,
    backup_status: str = "",
    backup_file: str = "",
    settings_status: str = "",
    security_status: str = "",
    reset_status: str = "",
    restore_status: str = "",
    delete_status: str = "",
    theme_status: str = "",
    saved_at: str = "",
):
    try:
        saved_at_value = datetime.strptime(saved_at, "%H:%M").strftime("%H:%M") if saved_at else ""
    except ValueError:
        saved_at_value = ""

    context = collect_system_info()
    recent_changes = get_recent_change_log()
    context.update({
        "backup_status": backup_status,
        "backup_file": backup_file,
        "settings_status": settings_status,
        "security_status": security_status,
        "reset_status": reset_status,
        "restore_status": restore_status,
        "delete_status": delete_status,
        "theme_status": theme_status,
        "saved_at": saved_at_value,
        "site_theme": get_site_theme(),
        "site_themes": SITE_THEMES,
        "security_enabled": is_security_enabled(),
        "security_requested": get_security_enabled_setting(),
        "security_environment_override": get_security_environment_override(),
        "login_prepared": is_login_prepared(),
        "helper_login_prepared": bool(get_referee_password()),
        "logged_in": is_logged_in(request),
        "current_role": get_current_role(request),
        "current_role_label": get_current_role_label(request),
        "current_role_description": get_current_role_description(request),
        "role_overview": [
            {
                "key": role,
                "label": ROLE_LABELS[role],
                "description": ROLE_DESCRIPTIONS[role],
                "prepared_only": role == "station_helper",
            }
            for role in ("viewer", "station_helper", "referee", "admin")
        ],
        "recent_changes": recent_changes,
        "change_log_count": get_change_log_count(),
    })
    return templates.TemplateResponse(
        request=request,
        name="einstellungen.html",
        context=context,
    )


@router.get("/dokumentation")
def dokumentation(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="dokumentation.html",
        context={"documentation_text": load_documentation_text()},
    )


@router.get("/roadmap")
def roadmap(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="roadmap.html",
        context={"roadmap_text": load_roadmap_text()},
    )


@router.get("/impressum")
def impressum(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="impressum.html",
        context={},
    )


@router.post("/einstellungen/security")
def update_security_setting(
    request: Request,
    security_enabled: str = Form(...),
    admin_password: str = Form(...),
):
    configured_password = get_admin_password()
    if not configured_password or not secrets.compare_digest(
        admin_password,
        configured_password,
    ):
        return RedirectResponse(
            "/einstellungen?security_status=invalid_password",
            status_code=303,
        )

    if get_security_environment_override() is not None:
        return RedirectResponse(
            "/einstellungen?security_status=environment_override",
            status_code=303,
        )

    target_value = security_enabled.strip().lower()
    if target_value not in {"true", "false"}:
        return RedirectResponse(
            "/einstellungen?security_status=invalid_value",
            status_code=303,
        )

    set_setting("security_enabled", target_value)
    request.session["admin_logged_in"] = True
    request.session["role"] = "admin"
    return RedirectResponse(
        f"/einstellungen?security_status={'enabled' if target_value == 'true' else 'disabled'}",
        status_code=303,
    )


@router.post("/einstellungen/backup")
def create_backup():
    backup_name = create_database_backup()
    if backup_name is None:
        return RedirectResponse(
            "/einstellungen?backup_status=error",
            status_code=303,
        )

    return RedirectResponse(
        f"/einstellungen?backup_status=ok&backup_file={backup_name}",
        status_code=303,
    )


@router.post("/einstellungen/backup/restore")
def restore_backup(
    backup_file: str = Form(...),
    admin_password: str = Form(...),
):
    configured_password = get_admin_password()
    if not configured_password or not secrets.compare_digest(
        admin_password,
        configured_password,
    ):
        return RedirectResponse(
            "/einstellungen?restore_status=invalid_password",
            status_code=303,
        )

    restore_status = restore_database_backup(backup_file)
    return RedirectResponse(
        f"/einstellungen?restore_status={restore_status}",
        status_code=303,
    )


@router.post("/einstellungen/backup/delete")
def delete_backup(backup_file: str = Form(...)):
    delete_status = delete_backup_file(backup_file)
    return RedirectResponse(
        f"/einstellungen?delete_status={delete_status}",
        status_code=303,
    )


@router.post("/einstellungen/beamer-intervall")
def update_beamer_interval(
    beamer_refresh_seconds: int = Form(...),
):
    if beamer_refresh_seconds <= 0:
        return RedirectResponse(
            "/einstellungen?settings_status=invalid",
            status_code=303,
        )

    set_beamer_refresh_seconds(beamer_refresh_seconds)

    saved_at = app_now_db_timestamp()
    return RedirectResponse(
        f"/einstellungen?settings_status=saved&saved_at={saved_at}",
        status_code=303,
    )

@router.post("/einstellungen/dashboard-info")
def update_dashboard_info(
    dashboard_info_text: str = Form(""),
):
    set_dashboard_info_text(dashboard_info_text)

    saved_at = app_now_db_timestamp()
    return RedirectResponse(
        f"/einstellungen?settings_status=dashboard_info_saved&saved_at={saved_at}",
        status_code=303,
    )


@router.post("/einstellungen/theme")
def update_site_theme(
    site_theme: str = Form(...),
):
    if not set_site_theme(site_theme):
        return RedirectResponse(
            "/einstellungen?theme_status=invalid",
            status_code=303,
        )

    saved_at = app_now_db_timestamp()
    return RedirectResponse(
        f"/einstellungen?theme_status=saved&saved_at={saved_at}",
        status_code=303,
    )


@router.post("/einstellungen/reset-aenderungszaehler")
def reset_aenderungszaehler():
    with get_conn() as conn:
        conn.execute("DELETE FROM change_log")
        conn.commit()
    return RedirectResponse(
        "/einstellungen?reset_status=ok",
        status_code=303,
    )
