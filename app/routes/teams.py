from datetime import datetime
from typing import Callable, List

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.database import get_conn
from app.web import templates


def table_has_column(conn, table_name: str, column_name: str):
    return any(
        row["name"] == column_name
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    )


def create_router(app_now_db_timestamp: Callable[[], str]) -> APIRouter:
    router = APIRouter()

    @router.get("/teams")
    def teams(request: Request):
        saved_team_id = request.query_params.get("saved_team_id", "").strip()
        saved_at = request.query_params.get("saved_at", "").strip()

        with get_conn() as conn:
            has_discipline_team = table_has_column(conn, "discipline_results", "team_id")
            query = """
                SELECT t.*,
                       (SELECT COUNT(*) FROM slots WHERE team_a_id = t.id OR team_b_id = t.id) AS slots_count,
                       (SELECT COUNT(*) FROM sixkampf_team_results WHERE team_id = t.id) AS sixkampf_count,
            """
            if has_discipline_team:
                query += "                   (SELECT COUNT(*) FROM discipline_results WHERE team_id = t.id) AS discipline_count\n"
            else:
                query += "                   0 AS discipline_count\n"
            query += """
                FROM teams t
                ORDER BY jahrgang, name
            """
            team_rows = conn.execute(query).fetchall()

        teams = []
        for row in team_rows:
            team = dict(row)
            team["dependent_count"] = (
                row["slots_count"] + row["sixkampf_count"] + row["discipline_count"]
            )
            team["has_slots"] = row["slots_count"] > 0
            team["has_sixkampf"] = row["sixkampf_count"] > 0
            team["has_discipline_results"] = row["discipline_count"] > 0
            team["deletable"] = team["dependent_count"] == 0
            teams.append(team)

        try:
            saved_team_id_value = int(saved_team_id) if saved_team_id else None
        except ValueError:
            saved_team_id_value = None
        try:
            saved_at_value = datetime.strptime(saved_at, "%H:%M").strftime("%H:%M") if saved_at else ""
        except ValueError:
            saved_at_value = ""

        return templates.TemplateResponse(
            request=request,
            name="teams.html",
            context={
                "teams": teams,
                "saved_team_id": saved_team_id_value,
                "saved_at": saved_at_value,
            },
        )

    @router.post("/team/create")
    def create_team(
        name: str = Form(...),
        jahrgang: int = Form(...),
    ):
        with get_conn() as conn:
            conn.execute("INSERT INTO teams (name, jahrgang, active) VALUES (?, ?, 1)", (name.strip(), jahrgang))
            conn.commit()

        return RedirectResponse("/teams", status_code=303)

    @router.post("/team/{team_id}/update")
    def update_team(
        team_id: int,
        name: str = Form(...),
        jahrgang: int = Form(...),
        active: int = Form(0),
    ):
        name_value = name.strip()
        if not name_value or not 1 <= jahrgang <= 13:
            return RedirectResponse("/teams", status_code=303)
        with get_conn() as conn:
            conn.execute(
                "UPDATE teams SET name = ?, jahrgang = ?, active = ? WHERE id = ?",
                (name_value, jahrgang, 1 if active else 0, team_id),
            )
            conn.commit()
        saved_at = app_now_db_timestamp()
        return RedirectResponse(f"/teams?saved_team_id={team_id}&saved_at={saved_at}", status_code=303)

    @router.post("/team/{team_id}/delete")
    def delete_team(team_id: int):
        with get_conn() as conn:
            try:
                conn.execute("BEGIN")
                has_discipline_team = table_has_column(conn, "discipline_results", "team_id")
                if has_discipline_team:
                    conn.execute("DELETE FROM discipline_results WHERE team_id = ?", (team_id,))
                conn.execute("DELETE FROM sixkampf_team_results WHERE team_id = ?", (team_id,))
                conn.execute(
                    "DELETE FROM slots WHERE team_a_id = ? OR team_b_id = ?",
                    (team_id, team_id),
                )
                conn.execute("DELETE FROM teams WHERE id = ?", (team_id,))
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        return RedirectResponse("/teams", status_code=303)

    @router.post("/teams/bulk-action")
    def teams_bulk_action(
        team_ids: List[int] = Form([]),
        action: str = Form(...),
    ):
        if not team_ids:
            return RedirectResponse("/teams", status_code=303)

        placeholders = ",".join("?" for _ in team_ids)
        with get_conn() as conn:
            try:
                conn.execute("BEGIN")
                if action == "deactivate":
                    conn.execute(
                        f"UPDATE teams SET active = 0 WHERE id IN ({placeholders})",
                        tuple(team_ids),
                    )
                elif action == "delete":
                    has_discipline_team = table_has_column(conn, "discipline_results", "team_id")
                    if has_discipline_team:
                        conn.execute(
                            f"DELETE FROM discipline_results WHERE team_id IN ({placeholders})",
                            tuple(team_ids),
                        )
                    conn.execute(
                        f"DELETE FROM sixkampf_team_results WHERE team_id IN ({placeholders})",
                        tuple(team_ids),
                    )
                    conn.execute(
                        f"DELETE FROM slots WHERE team_a_id IN ({placeholders}) OR team_b_id IN ({placeholders})",
                        tuple(team_ids) + tuple(team_ids),
                    )
                    conn.execute(
                        f"DELETE FROM teams WHERE id IN ({placeholders})",
                        tuple(team_ids),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        return RedirectResponse("/teams", status_code=303)

    return router