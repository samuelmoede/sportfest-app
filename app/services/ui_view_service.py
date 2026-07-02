from collections import defaultdict
from datetime import datetime

from app.services.schedule_grid_service import get_all_slots
from app.services.schedule_time_service import parse_slot_time


STATUS_PRESENTATION = {
    "planned": "geplant",
    "running": "läuft",
    "next": "als Nächstes",
    "soon": "gleich",
    "done": "erledigt",
}


def _format_schedule_time_range(start_value, end_value):
    start = parse_slot_time(start_value)
    end = parse_slot_time(end_value)
    if start is None or end is None or end <= start:
        return None
    return f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}"


def _year_group_label(value):
    if value in (None, ""):
        return ""
    return f"Jahrgang {value}"


def _group_label(groups):
    values = sorted({
        str(group).strip()
        for group in groups
        if group not in (None, "") and str(group).strip()
    })
    if not values:
        return ""
    if len(values) == 1:
        return f"Gruppe {values[0]}"
    return f"Gruppen {', '.join(values)}"


def _location_label(competition, court_names=()):
    parts = []
    for value in (
        competition.get("location"),
        competition.get("location_subarea"),
    ):
        text = str(value or "").strip()
        if text and text not in parts:
            parts.append(text)

    courts = sorted({str(name).strip() for name in court_names if str(name).strip()})
    for court in courts:
        if court not in parts:
            parts.append(court)
    return " · ".join(parts)


def _schedule_row_status(schedule, row, row_index, current_index, next_index):
    event_state = schedule.get("event_state")
    if schedule.get("completed") or event_state == "past":
        return "done"
    if row.get("current"):
        return "running"
    if next_index is not None and row_index == next_index:
        return "soon" if event_state == "today" else "next"

    pivot_index = current_index if current_index is not None else next_index
    if event_state == "today":
        if pivot_index is None or row_index < pivot_index:
            return "done"
    return "planned"


def enrich_day_schedule_view(schedule, competitions, event_id, now=None):
    """Add presentation-only cards and marker data to an existing day schedule."""
    view_schedule = dict(schedule)
    rows = [dict(row) for row in schedule.get("rows", [])]
    view_schedule["rows"] = rows

    slots = get_all_slots(event_id=event_id) if event_id is not None else []
    groups_by_competition = defaultdict(set)
    courts_by_competition = defaultdict(set)
    for slot in slots:
        competition_id = slot.get("competition_id")
        if slot.get("gruppe"):
            groups_by_competition[competition_id].add(slot["gruppe"])
        if slot.get("court_name"):
            courts_by_competition[competition_id].add(slot["court_name"])

    rows_by_time = {
        row.get("display_time"): row
        for row in rows
    }
    items_by_time = defaultdict(list)
    for competition in competitions:
        display_time = _format_schedule_time_range(
            competition.get("start_time"),
            competition.get("end_time"),
        )
        if display_time not in rows_by_time:
            continue

        competition_id = competition.get("id")
        items_by_time[display_time].append({
            "competition_id": competition_id,
            "name": (
                competition.get("name")
                or competition.get("sportart")
                or "Ohne Bezeichnung"
            ),
            "sportart": competition.get("sportart") or "",
            "year_group": _year_group_label(competition.get("jahrgang")),
            "group": _group_label(groups_by_competition[competition_id]),
            "location": _location_label(competition),
            "display_time": display_time,
        })

    current_index = next(
        (index for index, row in enumerate(rows) if row.get("current")),
        None,
    )

    # Timer-Override: if any slot is actively running, its time row takes priority
    # over the wall-clock-based current row (handles delayed starts).
    running_comp_ids = {
        slot["competition_id"]
        for slot in slots
        if slot.get("status") == "läuft" and slot.get("competition_id") is not None
    }
    if running_comp_ids:
        for competition in competitions:
            if competition.get("id") in running_comp_ids:
                dt = _format_schedule_time_range(
                    competition.get("start_time"),
                    competition.get("end_time"),
                )
                override_index = next(
                    (j for j, r in enumerate(rows) if r.get("display_time") == dt),
                    None,
                )
                if override_index is not None:
                    for r in rows:
                        r["current"] = False
                    rows[override_index]["current"] = True
                    current_index = override_index
                    break

    next_display_time = (schedule.get("next_block") or {}).get("display_time")
    next_index = next(
        (
            index
            for index, row in enumerate(rows)
            if row.get("display_time") == next_display_time
        ),
        None,
    )

    for index, row in enumerate(rows):
        status_key = _schedule_row_status(
            schedule,
            row,
            index,
            current_index,
            next_index,
        )
        row["status_key"] = status_key
        row["status_label"] = STATUS_PRESENTATION[status_key]
        row["items"] = items_by_time.get(row.get("display_time"), [])
        for item in row["items"]:
            item["status_key"] = status_key
            item["status_label"] = STATUS_PRESENTATION[status_key]

    marker = None
    if schedule.get("event_state") == "today":
        marker_index = (
            current_index
            if current_index is not None
            else next_index
        )
        if marker_index is None and rows:
            marker_index = len(rows)
        current_time = now or datetime.now()
        marker = {
            "row_index": marker_index,
            "label": f"JETZT · {current_time.strftime('%H:%M')} Uhr",
        }
    view_schedule["now_marker"] = marker
    return view_schedule


def _enrich_slot(slot):
    if slot is None:
        return None

    enriched = dict(slot)
    location_source = {
        "location": (
            slot.get("effective_competition_location")
            or slot.get("competition_location")
        ),
        "location_subarea": None,
    }
    enriched["year_group_label"] = _year_group_label(slot.get("jahrgang"))
    enriched["group_label"] = _group_label([slot.get("gruppe")])
    enriched["location_label"] = _location_label(
        location_source,
        [slot.get("court_name")],
    )
    return enriched


def enrich_beamer_view(data, slots):
    """Add display context and parallel next slots without changing slot selection."""
    view_data = dict(data)
    enriched_slots = [_enrich_slot(slot) for slot in slots]
    slots_by_id = {
        slot["id"]: slot
        for slot in enriched_slots
        if slot.get("id") is not None
    }

    def enriched_reference(slot):
        if slot is None:
            return None
        return slots_by_id.get(slot.get("id"), _enrich_slot(slot))

    current = enriched_reference(data.get("current"))
    next_slot = enriched_reference(data.get("next_slot"))
    running_slots = [
        enriched_reference(slot)
        for slot in data.get("running_slots", [])
    ]

    open_count_by_court = {
        item["court"]["id"]: item.get("open_count", 0)
        for item in data.get("court_summaries", [])
    }
    next_slots = []
    if next_slot is not None:
        next_slots = [
            dict(
                slot,
                open_count=open_count_by_court.get(slot.get("court_id"), 0),
                status_key="soon",
                status_label=STATUS_PRESENTATION["soon"],
            )
            for slot in enriched_slots
            if slot.get("status") == "geplant"
            and slot.get("startzeit") == next_slot.get("startzeit")
        ]
        next_slots.sort(key=lambda slot: (
            slot.get("court_name") or "",
            slot.get("competition_name") or "",
            slot.get("id") or 0,
        ))

    view_data["current"] = current
    view_data["running_slots"] = running_slots
    view_data["next_slot"] = (
        dict(
            next_slot,
            status_key="next",
            status_label=STATUS_PRESENTATION["next"],
        )
        if next_slot is not None
        else None
    )
    view_data["next_slots"] = next_slots
    view_data["recent_results"] = [
        enriched_reference(slot) for slot in data.get("recent_results", [])
    ]
    view_data["court_summaries"] = [
        {
            **item,
            "next_games": [enriched_reference(s) for s in item.get("next_games", [])],
        }
        for item in data.get("court_summaries", [])
    ]
    return view_data
