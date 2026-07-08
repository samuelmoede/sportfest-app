from datetime import datetime, timedelta


DEFAULT_GAME_DURATION_MINUTES = 7
DEFAULT_CHANGEOVER_DURATION_MINUTES = 3


def _get_value(row, key, default=None):
    if row is None:
        return default
    if hasattr(row, "get"):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def parse_slot_time(value: str):
    if not value:
        return None
    for time_format in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(value, time_format)
        except ValueError:
            continue
    return None


def format_time_range(start: datetime, end: datetime):
    return f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}"


def get_competition_timing(competition):
    def read_minutes(key, default, minimum):
        raw_value = _get_value(competition, key, default)
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            value = default
        return value if value >= minimum else default

    game_duration_minutes = read_minutes(
        "game_duration_minutes", DEFAULT_GAME_DURATION_MINUTES, 1
    )
    changeover_duration_minutes = read_minutes(
        "changeover_duration_minutes", DEFAULT_CHANGEOVER_DURATION_MINUTES, 0
    )
    return {
        "game_duration_minutes": game_duration_minutes,
        "changeover_duration_minutes": changeover_duration_minutes,
        "slot_interval_minutes": game_duration_minutes + changeover_duration_minutes,
    }


def get_game_end_time(startzeit: str, game_duration_minutes: int):
    start = parse_slot_time(startzeit)
    if start is None:
        return None
    return (start + timedelta(minutes=game_duration_minutes)).strftime("%H:%M")


def build_end_time_forecast(slots, competition):
    if competition is None or _get_value(competition, "competition_type") not in ("Turnier", "Schulpokal"):
        return None

    timing = get_competition_timing(competition)
    planned_end = parse_slot_time(_get_value(competition, "end_time"))
    projected_by_court = {}

    for slot in slots:
        if _get_value(slot, "slot_typ") != "Spiel":
            continue
        start = parse_slot_time(_get_value(slot, "startzeit"))
        if start is None:
            continue
        projected_end = start + timedelta(
            minutes=timing["game_duration_minutes"]
        )
        court_key = _get_value(slot, "court_id")
        court_name = _get_value(slot, "court_name") or "Ohne Feld"
        existing = projected_by_court.get(court_key)
        if existing is None or projected_end > existing["projected_end_value"]:
            projected_by_court[court_key] = {
                "court_id": court_key,
                "court_name": court_name,
                "projected_end_value": projected_end,
            }

    if not projected_by_court:
        return {
            "courts": [],
            "overall_end": None,
            "planned_end": planned_end.strftime("%H:%M") if planned_end else None,
            "exceeds_planned_end": False,
        }

    courts = sorted(
        projected_by_court.values(),
        key=lambda entry: (entry["projected_end_value"], entry["court_name"]),
    )
    for entry in courts:
        entry["projected_end"] = entry["projected_end_value"].strftime("%H:%M")
        entry["exceeds_planned_end"] = bool(
            planned_end and entry["projected_end_value"] > planned_end
        )
        del entry["projected_end_value"]

    overall_end = max(
        parse_slot_time(entry["projected_end"]) for entry in courts
    )
    return {
        "courts": courts,
        "overall_end": overall_end.strftime("%H:%M"),
        "planned_end": planned_end.strftime("%H:%M") if planned_end else None,
        "exceeds_planned_end": bool(
            planned_end and overall_end > planned_end
        ),
    }


def recalculate_competition_court_times(conn, competition_id: int, court_id):
    competition = conn.execute(
        "SELECT * FROM competitions WHERE id = ?", (competition_id,)
    ).fetchone()
    if competition is None:
        return None

    start = parse_slot_time(competition["start_time"])
    if start is None:
        return None

    game_slots = conn.execute("""
        SELECT id
        FROM slots
        WHERE competition_id = ?
          AND court_id IS ?
          AND slot_typ = 'Spiel'
        ORDER BY sort_order, id
    """, (competition_id, court_id)).fetchall()
    if not game_slots:
        return None

    timing = get_competition_timing(competition)
    for index, slot in enumerate(game_slots):
        calculated_start = start + timedelta(
            minutes=index * timing["slot_interval_minutes"]
        )
        conn.execute(
            "UPDATE slots SET startzeit = ? WHERE id = ?",
            (calculated_start.strftime("%H:%M"), slot["id"]),
        )

    projected_end = start + timedelta(
        minutes=(len(game_slots) - 1) * timing["slot_interval_minutes"]
        + timing["game_duration_minutes"]
    )
    planned_end = parse_slot_time(competition["end_time"])
    if planned_end is None or projected_end <= planned_end:
        return None

    if court_id is None:
        court_name = "Ohne Feld"
    else:
        court = conn.execute(
            "SELECT name FROM courts WHERE id = ?", (court_id,)
        ).fetchone()
        court_name = court["name"] if court is not None else f"Feld {court_id}"

    return {
        "court_id": court_id,
        "court_name": court_name,
        "projected_end": projected_end.strftime("%H:%M"),
        "planned_end": planned_end.strftime("%H:%M"),
        "message": (
            f"Warnung: {court_name} endet voraussichtlich um "
            f"{projected_end.strftime('%H:%M')}, geplant war "
            f"{planned_end.strftime('%H:%M')}."
        ),
    }