from app.services.ranking_points_service import assign_points_by_placement_groups
from app.utils.formatting import format_points_value


def _get(row, key):
    return row[key]


def _get_optional(row, key, default=None):
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


def _build_default_placement_points(points_first_place: int):
    try:
        highest_points = int(points_first_place)
    except (TypeError, ValueError):
        highest_points = 0
    highest_points = max(highest_points, 0)
    return [float(value) for value in range(highest_points, 0, -1)]


def _team_name(team):
    return str(_get(team, "name")).lower()


def _assign_placements(rows, *, value_key: str, descending: bool):
    rows.sort(key=lambda row: (
        -row[value_key] if descending else row[value_key],
        _team_name(row["team"]),
    ))

    # Dichte Platzvergabe (1, 1, 2, 3, ...) statt Skip-Konvention (1, 1, 3, 4, ...),
    # damit die Platzierung zur Punktevergabe in assign_points_by_placement_groups passt,
    # die ebenfalls je Ergebnis-Gruppe (nicht je Zeilenindex) zaehlt.
    previous_value = None
    placement = 0
    for row in rows:
        value = row[value_key]
        if previous_value is None or value != previous_value:
            placement += 1
            previous_value = value
        row["placement"] = placement


def _format_points_map(points_by_discipline):
    return {
        discipline_id: format_points_value(points)
        for discipline_id, points in points_by_discipline.items()
    }


def _format_result_total(value):
    if value is None:
        return None
    numeric_value = float(value)
    if numeric_value.is_integer():
        return str(int(numeric_value))
    return f"{numeric_value:.3f}".rstrip("0").rstrip(".").replace(".", ",")


def _format_result_total_with_unit(value, unit):
    formatted_value = _format_result_total(value)
    if formatted_value is None:
        return None
    unit = (unit or "").strip()
    if not unit:
        return formatted_value
    return f"{formatted_value} {unit}"


def calculate_sixkampf_team_ranking(
    teams,
    disciplines,
    result_rows,
    points_first_place: int = 7,
    require_result_entry: bool = False,
    placement_points=None,
):
    if placement_points is None:
        placement_points = _build_default_placement_points(points_first_place)

    teams_by_id = {_get(team, "id"): team for team in teams}
    disciplines_by_id = {_get(discipline, "id"): discipline for discipline in disciplines}
    discipline_points_by_team = {team_id: {} for team_id in teams_by_id}
    totals_by_team_discipline = {}
    teams_with_results = set()

    for result in result_rows:
        team_id = _get(result, "team_id")
        discipline_id = _get(result, "discipline_id")
        if team_id not in teams_by_id:
            continue
        key = (team_id, discipline_id)
        totals_by_team_discipline[key] = (
            totals_by_team_discipline.get(key, 0.0) + float(_get(result, "value"))
        )
        teams_with_results.add(team_id)

    for discipline in disciplines:
        discipline_id = _get(discipline, "id")
        descending = _get(discipline, "scoring_direction") != "lower"
        discipline_rows = []
        for team_id, team in teams_by_id.items():
            key = (team_id, discipline_id)
            if key not in totals_by_team_discipline:
                continue
            discipline_rows.append({
                "team": team,
                "discipline_total": totals_by_team_discipline[key],
                "placement": 0,
            })

        _assign_placements(
            discipline_rows,
            value_key="discipline_total",
            descending=descending,
        )
        assign_points_by_placement_groups(
            discipline_rows,
            placement_points,
            placement_key="placement",
            points_key="discipline_points",
        )
        for row in discipline_rows:
            team_id = _get(row["team"], "id")
            discipline_points_by_team[team_id][discipline_id] = row["discipline_points"]

    highest_discipline_points = placement_points[0] if placement_points else 0.0
    max_intermediate_total = len(disciplines) * highest_discipline_points
    ranking_teams = [
        team for team in teams
        if not require_result_entry or _get(team, "id") in teams_with_results
    ]

    overall_totals = {team_id: 0.0 for team_id in teams_by_id}
    ranking = []
    for team in ranking_teams:
        team_id = _get(team, "id")
        discipline_points = discipline_points_by_team.get(team_id, {})
        discipline_results_display = {}
        for discipline_id, discipline in disciplines_by_id.items():
            key = (team_id, discipline_id)
            if key not in totals_by_team_discipline:
                continue
            discipline_results_display[discipline_id] = _format_result_total_with_unit(
                totals_by_team_discipline[key],
                _get_optional(discipline, "unit", ""),
            )
        intermediate_total = sum(discipline_points.values())
        overall_totals[team_id] = intermediate_total
        ranking.append({
            "team": team,
            "discipline_points": discipline_points,
            "discipline_points_display": _format_points_map(discipline_points),
            "discipline_results_display": discipline_results_display,
            "intermediate_total": intermediate_total,
            "intermediate_total_display": format_points_value(intermediate_total),
            "max_intermediate_total": max_intermediate_total,
            "max_intermediate_total_display": format_points_value(max_intermediate_total),
            "overall_total": intermediate_total,
            "placement": 0,
        })

    _assign_placements(
        ranking,
        value_key="intermediate_total",
        descending=True,
    )
    assign_points_by_placement_groups(
        ranking,
        placement_points,
        placement_key="placement",
        points_key="scoring_points",
    )
    for row in ranking:
        row["scoring_points_display"] = format_points_value(row["scoring_points"])

    return ranking, totals_by_team_discipline, overall_totals


def calculate_sixkampf_station_rotation(teams, disciplines):
    """Rotationsplan je Klasse: die 1. Klasse startet an der 1. Station, die
    2. an der 2. usw.; jede Runde rueckt jede Klasse eine Station weiter. Gibt
    es mehr Klassen als Stationen, fuellen zusaetzliche 'Pause'-Plaetze die
    Differenz auf, sodass in jeder Runde weiterhin genau eine Klasse je
    Station steht und die ueberzaehligen Klassen reihum pausieren."""
    station_count = len(disciplines)
    team_count = len(teams)
    if station_count == 0 or team_count == 0:
        return []

    pause_count = max(0, team_count - station_count)
    cycle_length = station_count + pause_count
    slot_names = [_get(discipline, "name") for discipline in disciplines] + ["Pause"] * pause_count

    rotation = []
    for index, team in enumerate(teams):
        rounds = [
            slot_names[(index + offset) % cycle_length]
            for offset in range(cycle_length)
        ]
        rotation.append({"team": team, "rounds": rounds})
    return rotation
