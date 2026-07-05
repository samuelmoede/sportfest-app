from collections import defaultdict
from datetime import timedelta
from typing import Optional

from app.database import get_conn
from app.services.schedule_location_service import (
    COMPETITION_LOCATIONS,
    DEFAULT_COMPETITION_LOCATION,
    filter_courts_for_location,
    get_effective_competition_location,
    normalize_competition_location,
)
from app.services.schedule_time_service import (
    format_time_range,
    get_competition_timing,
    get_game_end_time,
    parse_slot_time,
)


def build_editor_time_grid(groups, competition=None, timing=None):
    time_values = {
        slot["startzeit"]
        for group in groups
        for slot in group["slots"]
        if parse_slot_time(slot.get("startzeit")) is not None
    }

    if (
        competition is not None
        and competition["competition_type"] == "Turnier"
        and timing is not None
    ):
        interval = timing["slot_interval_minutes"]
        start = parse_slot_time(competition["start_time"])
        planned_end = parse_slot_time(competition["end_time"])

        if start is not None and interval > 0:
            generated_time = start
            generated_count = 0
            while generated_count < 200:
                if planned_end is not None and planned_end > start:
                    if generated_time >= planned_end:
                        break
                elif generated_count >= 3:
                    break
                time_values.add(generated_time.strftime("%H:%M"))
                generated_time += timedelta(minutes=interval)
                generated_count += 1

    time_marks = sorted(time_values, key=lambda value: parse_slot_time(value))
    row_by_time = {
        startzeit: index + 2 for index, startzeit in enumerate(time_marks)
    }

    for group in groups:
        slots_by_time = {startzeit: [] for startzeit in time_marks}
        untimed_slots = []
        for slot in group["slots"]:
            startzeit = slot.get("startzeit")
            slot["editor_grid_row"] = row_by_time.get(startzeit, 2)
            if startzeit in row_by_time:
                slots_by_time.setdefault(startzeit, []).append(slot)
            else:
                untimed_slots.append(slot)

        group["time_cells"] = [
            {
                "startzeit": startzeit,
                "editor_grid_row": row_by_time[startzeit],
                "slots": slots_by_time.get(startzeit, []),
            }
            for startzeit in time_marks
        ]
        group["untimed_slots"] = untimed_slots

    return time_marks


def get_all_slots(
    competition_id: Optional[int] = None,
    jahrgang: Optional[int] = None,
    location: Optional[str] = None,
    event_id: Optional[int] = None,
):
    query = """
        SELECT slots.*, c.name AS competition_name, c.sportart, c.jahrgang,
               c.location AS competition_location,
               c.competition_type,
               c.game_duration_minutes,
               c.changeover_duration_minutes,
               c.start_time AS competition_start_time,
               c.end_time AS competition_end_time,
               co.name AS court_name, ta.name AS team_a, tb.name AS team_b
        FROM slots
        JOIN competitions c ON c.id = slots.competition_id
        LEFT JOIN courts co ON co.id = slots.court_id
        LEFT JOIN teams ta ON ta.id = slots.team_a_id
        LEFT JOIN teams tb ON tb.id = slots.team_b_id
        WHERE c.status != 'archiviert'
    """
    params = []

    if competition_id:
        query += " AND slots.competition_id = ?"
        params.append(competition_id)

    if event_id is not None:
        query += " AND c.event_id = ?"
        params.append(event_id)

    if jahrgang is not None:
        query += " AND c.jahrgang = ?"
        params.append(jahrgang)

    normalized_location = normalize_competition_location(location)
    if normalized_location is not None:
        if normalized_location == DEFAULT_COMPETITION_LOCATION:
            query += " AND (c.location = ? OR c.location IS NULL OR TRIM(c.location) = '')"
            params.append(normalized_location)
        else:
            query += " AND c.location = ?"
            params.append(normalized_location)

    query += """
    ORDER BY
        slots.court_id,
        slots.sort_order,
        slots.startzeit,
        slots.id
    """

    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()

    slots = [dict(row) for row in rows]
    change_counts = {}
    if slots:
        slot_ids = [slot["id"] for slot in slots]
        placeholders = ",".join("?" for _ in slot_ids)
        with get_conn() as conn:
            change_counts = {
                row["entity_id"]: row["change_count"]
                for row in conn.execute(
                    f"""
                    SELECT entity_id, COUNT(*) AS change_count
                    FROM change_log
                    WHERE entity_type = 'slot_result'
                      AND entity_id IN ({placeholders})
                    GROUP BY entity_id
                    """,
                    slot_ids,
                ).fetchall()
            }
    for slot in slots:
        slot["change_count"] = change_counts.get(slot["id"], 0)
        slot["planned_duration_seconds"] = None
        slot["game_end_time"] = None
        slot["effective_competition_location"] = get_effective_competition_location({
            "location": slot.get("competition_location")
        })
        if slot["slot_typ"] != "Spiel" or slot["competition_type"] == "Sechskampf":
            continue
        timing = get_competition_timing(slot)
        slot["planned_duration_seconds"] = timing["game_duration_minutes"] * 60
        slot["game_end_time"] = get_game_end_time(
            slot["startzeit"], timing["game_duration_minutes"]
        )

    return slots


def get_slots_grouped_by_court(
    competition_id: Optional[int] = None,
    jahrgang: Optional[int] = None,
    location: Optional[str] = None,
    event_id: Optional[int] = None,
):
    with get_conn() as conn:
        courts = conn.execute(
            "SELECT * FROM courts WHERE active = 1 ORDER BY name"
        ).fetchall()

    slots = [
        dict(slot)
        for slot in get_all_slots(competition_id, jahrgang, location, event_id)
    ]
    grouped = {court["id"]: {"court": court, "slots": []} for court in courts}
    without_court = {"court": {"id": "", "name": "Ohne Feld"}, "slots": []}

    for slot in slots:
        if slot["court_id"] in grouped:
            grouped[slot["court_id"]]["slots"].append(slot)
        else:
            without_court["slots"].append(slot)

    return list(grouped.values()) + [without_court]


def get_unslotted_competitions_by_location(
    competition_id: Optional[int] = None,
    jahrgang: Optional[int] = None,
    location: Optional[str] = None,
    event_id: Optional[int] = None,
):
    clauses = [
        "competition.status != 'archiviert'",
        "competition.start_time IS NOT NULL",
        "TRIM(competition.start_time) != ''",
        "competition.end_time IS NOT NULL",
        "TRIM(competition.end_time) != ''",
        """
        NOT EXISTS (
            SELECT 1
            FROM slots
            WHERE slots.competition_id = competition.id
              AND slots.slot_typ = 'Spiel'
        )
        """,
    ]
    params = []
    if competition_id is not None:
        clauses.append("competition.id = ?")
        params.append(competition_id)
    if event_id is not None:
        clauses.append("competition.event_id = ?")
        params.append(event_id)
    if jahrgang is not None:
        clauses.append("competition.jahrgang = ?")
        params.append(jahrgang)

    with get_conn() as conn:
        competitions = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT competition.*
                FROM competitions AS competition
                WHERE {' AND '.join(clauses)}
                """,
                params,
            ).fetchall()
        ]

        disciplines_by_competition = defaultdict(list)
        if competitions:
            placeholders = ",".join("?" for _ in competitions)
            discipline_rows = conn.execute(
                f"""
                SELECT competition_id, name, sort_order
                FROM competition_disciplines
                WHERE competition_id IN ({placeholders})
                ORDER BY competition_id, sort_order, id
                """,
                [competition["id"] for competition in competitions],
            ).fetchall()
            for discipline in discipline_rows:
                disciplines_by_competition[discipline["competition_id"]].append(
                    dict(discipline)
                )

    selected_location = normalize_competition_location(location)
    entries = []
    for competition in competitions:
        effective_location = get_effective_competition_location(competition)
        if selected_location is not None and effective_location != selected_location:
            continue
        start = parse_slot_time(competition["start_time"])
        end = parse_slot_time(competition["end_time"])
        if start is None or end is None:
            continue
        competition["effective_location"] = effective_location
        competition["time_label"] = format_time_range(start, end)
        competition["disciplines"] = disciplines_by_competition.get(
            competition["id"], []
        )
        entries.append(competition)

    location_order = {
        location: index for index, location in enumerate(COMPETITION_LOCATIONS)
    }
    entries.sort(
        key=lambda competition: (
            location_order.get(competition.get("effective_location"), 99),
            competition["start_time"],
            competition["end_time"],
            competition["name"].casefold(),
        )
    )

    groups = []
    for location_name in COMPETITION_LOCATIONS:
        location_competitions = [
            competition
            for competition in entries
            if competition["effective_location"] == location_name
        ]
        if location_competitions:
            groups.append({
                "location": location_name,
                "competitions": location_competitions,
            })
    return groups


def get_competition_timeline_for_location(
    location: str,
    competition_id: Optional[int] = None,
    jahrgang: Optional[int] = None,
    event_id: Optional[int] = None,
):
    selected_location = normalize_competition_location(location)
    clauses = ["status != 'archiviert'"]
    params = []
    if competition_id is not None:
        clauses.append("id = ?")
        params.append(competition_id)
    if event_id is not None:
        clauses.append("event_id = ?")
        params.append(event_id)
    if jahrgang is not None:
        clauses.append("jahrgang = ?")
        params.append(jahrgang)

    with get_conn() as conn:
        competitions = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT *
                FROM competitions
                WHERE {' AND '.join(clauses)}
                ORDER BY
                    CASE
                        WHEN start_time IS NULL OR TRIM(start_time) = '' THEN 1
                        ELSE 0
                    END,
                    start_time,
                    end_time,
                    name
                """,
                params,
            ).fetchall()
        ]

    timeline = []
    for competition in competitions:
        if get_effective_competition_location(competition) != selected_location:
            continue
        start = parse_slot_time(competition.get("start_time"))
        end = parse_slot_time(competition.get("end_time"))
        competition["start_time_display"] = (
            start.strftime("%H:%M") if start else "–"
        )
        competition["end_time_display"] = (
            end.strftime("%H:%M") if end else "–"
        )
        timeline.append(competition)
    return timeline


def _insert_group_phase_gaps(location_slots):
    """Fügt Pause-Platzhalter ein, wenn ein Wettbewerb auf einem seiner Felder
    zu einer Zeit kein Spiel hat, die eines seiner anderen Felder aber schon
    genutzt hat (z.B. weil bei ungerader Teamzahl eine Gruppenphasen-Runde
    nicht alle Felder füllt). Ohne diese Lücke rutschen die Karten der
    beiden Felder in der einfachen Listenansicht gegeneinander, sodass z.B.
    ein Halbfinale optisch in derselben Zeile wie ein Gruppenspiel des
    anderen Feldes steht, obwohl die Uhrzeiten unterschiedlich sind."""
    slot_times_by_court = defaultdict(set)
    game_slots_by_competition = defaultdict(list)
    for slot in location_slots:
        if slot["court_id"] is not None:
            slot_times_by_court[slot["court_id"]].add(slot["startzeit"])
        if slot["slot_typ"] == "Spiel":
            game_slots_by_competition[slot["competition_id"]].append(slot)

    gap_slots = []
    for competition_id, comp_slots in game_slots_by_competition.items():
        courts_used = sorted({
            slot["court_id"] for slot in comp_slots if slot["court_id"] is not None
        })
        if len(courts_used) < 2:
            continue

        all_times = sorted({slot["startzeit"] for slot in comp_slots})
        competition_name = comp_slots[0]["competition_name"]

        for court_id in courts_used:
            for startzeit in all_times:
                if startzeit in slot_times_by_court[court_id]:
                    continue
                gap_slots.append({
                    "id": f"gap-{competition_id}-{court_id}-{startzeit}",
                    "competition_id": competition_id,
                    "competition_name": competition_name,
                    "court_id": court_id,
                    "startzeit": startzeit,
                    "slot_typ": "Pause",
                    "is_gap": True,
                })
                slot_times_by_court[court_id].add(startzeit)

    return location_slots + gap_slots


def build_location_print_grid(court_groups):
    """Baut aus den court_groups einer Ortssektion (siehe get_location_schedule_sections)
    ein Zeit-x-Feld-Raster fuer den Aushang-Druck: jede Zeile eine Startzeit, jede
    Spalte ein Feld dieses Ortes. Pause-Platzhalter (is_gap, nur fuer die
    Editor-Kartenansicht gedacht) werden uebersprungen, da ein leeres Feld in der
    Rasterzelle bereits eindeutig "keine Begegnung" bedeutet. Jede Zelle ist eine
    Liste (nicht nur ein einzelner Slot): faellt ein Feld zur selben Zeit versehentlich
    zwei Wettbewerben zu (Cross-Wettbewerb-Kollision, siehe ROADMAP.md), sollen auf dem
    Aushang trotzdem beide Begegnungen sichtbar bleiben statt eine davon stillschweigend
    zu verlieren."""
    courts = [group["court"] for group in court_groups]
    time_values = sorted({
        slot["startzeit"]
        for group in court_groups
        for slot in group["slots"]
        if not slot.get("is_gap")
    }, key=parse_slot_time)

    rows = []
    for startzeit in time_values:
        cells = []
        for group in court_groups:
            matching_slots = [
                s for s in group["slots"]
                if s["startzeit"] == startzeit and not s.get("is_gap")
            ]
            cells.append(matching_slots)
        rows.append({"startzeit": startzeit, "cells": cells})

    return courts, rows


def get_location_schedule_sections(
    competition_id: Optional[int] = None,
    jahrgang: Optional[int] = None,
    selected_location: Optional[str] = None,
    event_id: Optional[int] = None,
):
    selected_location = normalize_competition_location(selected_location)
    unslotted_groups = get_unslotted_competitions_by_location(
        competition_id,
        jahrgang,
        selected_location,
        event_id,
    )
    unslotted_by_location = {
        group["location"]: group["competitions"]
        for group in unslotted_groups
    }
    locations = (
        (selected_location,)
        if selected_location
        else COMPETITION_LOCATIONS
    )
    all_slots = get_all_slots(competition_id, jahrgang, event_id=event_id)
    sections = []

    with get_conn() as conn:
        all_courts = conn.execute(
            "SELECT * FROM courts WHERE active = 1 ORDER BY name"
        ).fetchall()

    for location in locations:
        timeline = []
        court_groups = []
        competitions = unslotted_by_location.get(location, [])

        if location == "Außenbereich":
            timeline = get_competition_timeline_for_location(
                location,
                competition_id,
                jahrgang,
                event_id,
            )
            competitions = []
        else:
            location_slots = [
                slot
                for slot in all_slots
                if slot.get("effective_competition_location") == location
            ]
            if location_slots:
                location_slots = _insert_group_phase_gaps(location_slots)
                courts = filter_courts_for_location(all_courts, location)
                grouped = {
                    court["id"]: {"court": court, "slots": []}
                    for court in courts
                }
                without_court = {
                    "court": {"id": "", "name": "Ohne Feld"},
                    "slots": [],
                }
                for slot in location_slots:
                    target = grouped.get(slot["court_id"], without_court)
                    target["slots"].append(slot)
                for group in (*grouped.values(), without_court):
                    group["slots"].sort(key=lambda slot: slot["startzeit"])
                court_groups = [
                    group
                    for group in (*grouped.values(), without_court)
                    if group["slots"]
                ]

        has_entries = bool(timeline or court_groups or competitions)
        if has_entries or (
            selected_location is not None and selected_location == location
        ):
            sections.append({
                "location_key": location,
                "location": location,
                "timeline": timeline,
                "court_groups": court_groups,
                "competitions": competitions,
                "has_entries": has_entries,
            })

    return sections