from typing import Optional

from fastapi import Request

from app.services.settings_service import get_current_role


def insert_change_log_entry(
    conn,
    *,
    request: Request,
    created_at: str,
    action: str,
    entity_type: str,
    entity_id: Optional[int],
    competition_id: Optional[int],
    old_value: Optional[str],
    new_value: Optional[str],
    discipline_id: Optional[int] = None,
    team_id: Optional[int] = None,
):
    conn.execute(
        """
        INSERT INTO change_log (
            created_at, actor_role, action, entity_type, entity_id,
            competition_id, discipline_id, team_id, old_value, new_value
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            created_at,
            get_current_role(request),
            action,
            entity_type,
            entity_id,
            competition_id,
            discipline_id,
            team_id,
            old_value,
            new_value,
        ),
    )
