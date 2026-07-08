from datetime import date

EVENT_STATUS_PLANNED = "geplant"
EVENT_STATUS_ACTIVE = "aktiv"
EVENT_STATUS_ARCHIVED = "archiviert"
EVENT_STATUSES = [EVENT_STATUS_PLANNED, EVENT_STATUS_ACTIVE, EVENT_STATUS_ARCHIVED]
EVENT_STATUS_SET = set(EVENT_STATUSES)


def _get_value(row, key, default=None):
    if row is None:
        return default
    if hasattr(row, "get"):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def parse_event_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _event_sort_key(event):
    event_date = parse_event_date(_get_value(event, "event_date"))
    date_key = event_date.isoformat() if event_date else "9999-12-31"
    return (
        date_key,
        str(_get_value(event, "name", "")).casefold(),
        int(_get_value(event, "id", 0) or 0),
    )


def select_active_event(events):
    active_events = [
        event for event in events
        if _get_value(event, "status") == EVENT_STATUS_ACTIVE
    ]
    if not active_events:
        return None
    return sorted(active_events, key=_event_sort_key)[0]


def select_next_event(events, today):
    candidates = []
    for event in events:
        if _get_value(event, "status") == EVENT_STATUS_ARCHIVED:
            continue
        event_date = parse_event_date(_get_value(event, "event_date"))
        if event_date is not None and event_date >= today:
            candidates.append(event)
    if not candidates:
        return None
    return sorted(candidates, key=_event_sort_key)[0]


def select_default_event(events, today):
    return select_active_event(events) or select_next_event(events, today)


def resolve_selected_event_id(
    events,
    *,
    event_id_value="",
    event_filter_present=False,
    today=None,
    fallback_event_id=None,
):
    today = today or date.today()
    event_ids = {_get_value(event, "id") for event in events}
    requested_event_id = None
    try:
        requested_event_id = int(event_id_value) if str(event_id_value).strip() else None
    except (TypeError, ValueError):
        requested_event_id = None

    if event_filter_present:
        return requested_event_id if requested_event_id in event_ids else None

    if fallback_event_id in event_ids:
        return fallback_event_id

    default_event = select_default_event(events, today)
    return _get_value(default_event, "id") if default_event is not None else None


def fetch_events_with_competition_counts(conn, *, include_archived=True):
    where = ""
    params = []
    if not include_archived:
        where = "WHERE e.status != ?"
        params.append(EVENT_STATUS_ARCHIVED)

    return conn.execute(
        f"""
        SELECT e.*,
               (
                   SELECT COUNT(*)
                   FROM competitions c
                   WHERE c.event_id = e.id
               ) AS competition_count
        FROM events e
        {where}
        ORDER BY
            CASE e.status
                WHEN 'aktiv' THEN 0
                WHEN 'geplant' THEN 1
                WHEN 'archiviert' THEN 2
                ELSE 3
            END,
            CASE WHEN e.event_date IS NULL OR TRIM(e.event_date) = '' THEN 1 ELSE 0 END,
            e.event_date,
            e.name
        """,
        params,
    ).fetchall()


def get_dashboard_event(conn, today):
    events = fetch_events_with_competition_counts(conn, include_archived=False)
    return select_default_event(events, today)


def get_upcoming_events(conn, today, *, limit=4, exclude_event_id=None):
    params = [EVENT_STATUS_ARCHIVED, today.isoformat()]
    exclude_clause = ""
    if exclude_event_id is not None:
        exclude_clause = "AND e.id != ?"
        params.append(exclude_event_id)
    params.append(limit)
    return conn.execute(
        f"""
        SELECT e.*,
               (
                   SELECT COUNT(*)
                   FROM competitions c
                   WHERE c.event_id = e.id
               ) AS competition_count
        FROM events e
        WHERE e.status != ?
          AND e.event_date IS NOT NULL
          AND TRIM(e.event_date) != ''
          AND e.event_date >= ?
          {exclude_clause}
        ORDER BY e.event_date ASC, e.name ASC
        LIMIT ?
        """,
        params,
    ).fetchall()


def clear_active_events(conn, *, except_event_id=None):
    if except_event_id is None:
        conn.execute(
            "UPDATE events SET status = ? WHERE status = ?",
            (EVENT_STATUS_PLANNED, EVENT_STATUS_ACTIVE),
        )
    else:
        conn.execute(
            "UPDATE events SET status = ? WHERE status = ? AND id != ?",
            (EVENT_STATUS_PLANNED, EVENT_STATUS_ACTIVE, except_event_id),
        )


def set_active_event(conn, event_id):
    event = conn.execute(
        "SELECT id, status FROM events WHERE id = ?",
        (event_id,),
    ).fetchone()
    if event is None or event["status"] == EVENT_STATUS_ARCHIVED:
        return False
    clear_active_events(conn, except_event_id=event_id)
    conn.execute(
        "UPDATE events SET status = ? WHERE id = ?",
        (EVENT_STATUS_ACTIVE, event_id),
    )
    return True


def deactivate_event(conn, event_id):
    cursor = conn.execute(
        "UPDATE events SET status = ? WHERE id = ? AND status = ?",
        (EVENT_STATUS_PLANNED, event_id, EVENT_STATUS_ACTIVE),
    )
    return cursor.rowcount > 0


def archive_event(conn, event_id):
    cursor = conn.execute(
        "UPDATE events SET status = ? WHERE id = ?",
        (EVENT_STATUS_ARCHIVED, event_id),
    )
    return cursor.rowcount > 0


def restore_event(conn, event_id):
    cursor = conn.execute(
        "UPDATE events SET status = ? WHERE id = ?",
        (EVENT_STATUS_PLANNED, event_id),
    )
    return cursor.rowcount > 0


def normalize_event_statuses(conn):
    for running_status in ("läuft", "lÃ¤uft", "l?uft"):
        conn.execute(
            "UPDATE events SET status = ? WHERE status = ?",
            (EVENT_STATUS_ACTIVE, running_status),
        )
    conn.execute(
        "UPDATE events SET status = ? WHERE status = ?",
        (EVENT_STATUS_ARCHIVED, "beendet"),
    )
    conn.execute(
        f"""
        UPDATE events
        SET status = ?
        WHERE status IS NULL
           OR TRIM(status) = ''
           OR status NOT IN ({','.join('?' for _ in EVENT_STATUSES)})
        """,
        [EVENT_STATUS_PLANNED, *EVENT_STATUSES],
    )
    active_events = conn.execute(
        """
        SELECT id
        FROM events
        WHERE status = ?
        ORDER BY
            CASE WHEN event_date IS NULL OR TRIM(event_date) = '' THEN 1 ELSE 0 END,
            event_date,
            id
        """,
        (EVENT_STATUS_ACTIVE,),
    ).fetchall()
    for event in active_events[1:]:
        conn.execute(
            "UPDATE events SET status = ? WHERE id = ?",
            (EVENT_STATUS_PLANNED, event["id"]),
        )
