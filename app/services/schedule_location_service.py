import re

COMPETITION_LOCATIONS = ("Turnhalle", "Fußballplatz", "Schulhaus", "Mensa")
COMPETITION_LOCATION_ALIASES = {
    "Sportplatz": "Fußballplatz",
    "Aussenbereich": "Schulhaus",
    "Außenbereich": "Schulhaus",
}
DEFAULT_COMPETITION_LOCATION = "Turnhalle"
NO_SLOT_LOCATION = "Schulhaus"

COURT_NAMES_BY_LOCATION = {
    "Turnhalle": ("Feld 1", "Feld 2", "Feld 3"),
    "Fußballplatz": ("Rasenplatz", "Käfig"),
    "Mensa": tuple(f"Platte {i}" for i in range(1, 13)),
}
COURT_LOCATION_BY_NAME = {
    court_name: location
    for location, court_names in COURT_NAMES_BY_LOCATION.items()
    for court_name in court_names
}

NO_SLOT_LOCATION_HINT = (
    "Dieser Wettbewerb wird als Programmpunkt über Start- und Endzeit angezeigt."
)


def _get_value(row, key, default=None):
    if row is None:
        return default
    if hasattr(row, "get"):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def normalize_competition_location(raw_value):
    if raw_value is None:
        return None
    normalized = str(raw_value).strip()
    if not normalized:
        return None
    normalized = COMPETITION_LOCATION_ALIASES.get(normalized, normalized)
    if normalized in COMPETITION_LOCATIONS:
        return normalized
    return None


def get_effective_competition_location(competition):
    value = _get_value(competition, "location")
    return normalize_competition_location(value) or DEFAULT_COMPETITION_LOCATION


def get_court_location(court):
    location = normalize_competition_location(_get_value(court, "location"))
    if location:
        return location
    name = str(_get_value(court, "name", "")).strip()
    return COURT_LOCATION_BY_NAME.get(name, DEFAULT_COMPETITION_LOCATION)


def schedule_planning_available(competition):
    return get_effective_competition_location(competition) != NO_SLOT_LOCATION


def get_allowed_court_names(competition):
    location = get_effective_competition_location(competition)
    if location == NO_SLOT_LOCATION:
        return ()
    return COURT_NAMES_BY_LOCATION.get(location, ())


def natural_sort_key(name):
    """'Platte 2' vor 'Platte 10': Text/Zahl-Abschnitte getrennt vergleichen,
    statt rein alphabetisch (sonst 'Platte 10' < 'Platte 2')."""
    return [
        int(part) if part.isdigit() else part
        for part in re.split(r"(\d+)", str(name))
    ]


def filter_courts_for_location(courts, location):
    normalized_location = normalize_competition_location(location) or DEFAULT_COMPETITION_LOCATION
    if normalized_location == NO_SLOT_LOCATION:
        return []
    return sorted(
        (
            court for court in courts
            if get_court_location(court) == normalized_location
        ),
        key=lambda court: natural_sort_key(_get_value(court, "name", "")),
    )


def filter_courts_for_competition(courts, competition):
    return filter_courts_for_location(
        courts,
        get_effective_competition_location(competition),
    )


def filter_groups_for_competition(groups, competition):
    if not schedule_planning_available(competition):
        return []

    location = get_effective_competition_location(competition)
    filtered_groups = []
    for group in groups:
        court = group["court"]
        court_id = _get_value(court, "id", "")
        has_slots = bool(group.get("slots"))
        if court_id in (None, ""):
            if has_slots:
                filtered_groups.append(group)
            continue
        if get_court_location(court) == location or has_slots:
            filtered_groups.append(group)
    return filtered_groups


def filter_court_ids_for_competition(court_ids, courts, competition):
    allowed_ids = {
        int(_get_value(court, "id"))
        for court in filter_courts_for_competition(courts, competition)
        if _get_value(court, "id") not in (None, "")
    }
    filtered_ids = []
    for court_id in court_ids:
        try:
            parsed_id = int(court_id)
        except (TypeError, ValueError):
            continue
        if parsed_id in allowed_ids:
            filtered_ids.append(parsed_id)
    return filtered_ids