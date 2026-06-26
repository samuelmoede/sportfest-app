import re
from datetime import date
from urllib.parse import parse_qsl, urlencode, urlsplit

from app.database import get_conn
from app.services.event_status_service import select_default_event


_NATURAL_PART_RE = re.compile(r"\d+|\D+")


def _get_value(row, key, default=None):
    if row is None:
        return default
    if hasattr(row, "get"):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _natural_text_key(value):
    text = str(value or "").strip().casefold()
    parts = []
    for part in _NATURAL_PART_RE.findall(text):
        if part.isdigit():
            parts.append((0, int(part)))
        else:
            parts.append((1, part))
    return tuple(parts)


def _numeric_key(value):
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (1, str(value or "").strip().casefold())


def parse_filter_id(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_event_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def sort_result_competitions(competitions, event_names_by_id=None):
    event_names_by_id = event_names_by_id or {}

    def sort_key(competition):
        event_name = _get_value(competition, "event_name")
        if not event_name:
            event_name = event_names_by_id.get(_get_value(competition, "event_id"), "")
        return (
            _numeric_key(_get_value(competition, "jahrgang")),
            _natural_text_key(_get_value(competition, "name")),
            _natural_text_key(event_name),
            _numeric_key(_get_value(competition, "id")),
        )

    return sorted(competitions, key=sort_key)


def _event_sort_key(event, today):
    event_date = _parse_event_date(_get_value(event, "event_date"))
    name_key = _natural_text_key(_get_value(event, "name"))
    if event_date is None:
        return (2, "", name_key)
    if event_date == today:
        return (0, event_date.isoformat(), name_key)
    if event_date > today:
        return (1, event_date.isoformat(), name_key)
    return (3, event_date.isoformat(), name_key)


def get_result_event_options(competitions, today):
    event_ids = {
        _get_value(competition, "event_id")
        for competition in competitions
        if _get_value(competition, "event_id") is not None
    }
    if not event_ids:
        return []

    placeholders = ",".join("?" for _ in event_ids)
    with get_conn() as conn:
        events = conn.execute(
            f"""
            SELECT *
            FROM events
            WHERE id IN ({placeholders})
            """,
            sorted(event_ids),
        ).fetchall()
    return sorted(events, key=lambda event: _event_sort_key(event, today))


def get_default_result_event_id(events, today):
    default_event = select_default_event(events, today)
    return _get_value(default_event, "id") if default_event is not None else None


_RESULTS_REDIRECT_PATH = "/ergebnisse"
_RESULTS_TRANSIENT_PARAMS = {"saved_team_id", "saved_at"}


def build_results_redirect_url(return_to="", **updates):
    params = {}
    raw_return_to = str(return_to or "")
    if (
        raw_return_to
        and "\\" not in raw_return_to
        and "\r" not in raw_return_to
        and "\n" not in raw_return_to
    ):
        parts = urlsplit(raw_return_to)
        if (
            not parts.scheme
            and not parts.netloc
            and parts.path == _RESULTS_REDIRECT_PATH
        ):
            params = {
                key: value
                for key, value in parse_qsl(parts.query, keep_blank_values=True)
                if key not in _RESULTS_TRANSIENT_PARAMS
            }

    for key, value in updates.items():
        if value is None:
            continue
        value = str(value).strip()
        if value:
            params[key] = value
        else:
            params.pop(key, None)

    query = urlencode(params)
    if query:
        return f"{_RESULTS_REDIRECT_PATH}?{query}"
    return _RESULTS_REDIRECT_PATH


def build_results_filter_state(
    competitions,
    *,
    event_id_value,
    competition_id_value,
    event_filter_present,
    today,
):
    all_competitions = list(competitions)
    event_options = get_result_event_options(all_competitions, today)
    event_ids = {event["id"] for event in event_options}
    requested_event_id = parse_filter_id(event_id_value)
    requested_competition_id = parse_filter_id(competition_id_value)
    competition_by_id = {
        competition["id"]: competition
        for competition in all_competitions
    }
    requested_competition = competition_by_id.get(requested_competition_id)

    if event_filter_present:
        selected_event_id = (
            requested_event_id
            if requested_event_id in event_ids
            else None
        )
    elif requested_competition is not None:
        selected_event_id = _get_value(requested_competition, "event_id")
        if selected_event_id not in event_ids:
            selected_event_id = None
    else:
        selected_event_id = get_default_result_event_id(event_options, today)

    visible_competitions = [
        competition
        for competition in all_competitions
        if (
            selected_event_id is None
            or _get_value(competition, "event_id") == selected_event_id
        )
    ]
    visible_competitions = sort_result_competitions(visible_competitions)
    visible_competition_ids = {
        competition["id"] for competition in visible_competitions
    }

    selected_competition_id = requested_competition_id
    if selected_competition_id not in visible_competition_ids:
        selected_competition_id = None

    return {
        "event_options": event_options,
        "selected_event_id": selected_event_id,
        "competitions": visible_competitions,
        "visible_competition_ids": visible_competition_ids,
        "selected_competition_id": selected_competition_id,
        "selected_competition": competition_by_id.get(selected_competition_id),
    }


def sort_result_events(events):
    return sorted(
        events,
        key=lambda event: (
            _natural_text_key(_get_value(event, "name")),
            _numeric_key(_get_value(event, "id")),
        ),
    )


def sort_result_years(years):
    return sorted(years, key=_numeric_key)


def sort_result_teams(teams):
    return sorted(
        teams,
        key=lambda team: (
            _natural_text_key(_get_value(team, "name")),
            _numeric_key(_get_value(team, "jahrgang")),
            _numeric_key(_get_value(team, "id")),
        ),
    )