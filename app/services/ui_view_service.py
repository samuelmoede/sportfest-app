from collections import defaultdict
from datetime import datetime

from app.services.schedule_grid_service import get_all_slots
from app.services.schedule_time_service import parse_slot_time
from app.services.programmpunkte_service import applies_to_jahrgang


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


def classify_yeargang(value):
    if value is None:
        return None
    try:
        year = int(value)
    except (TypeError, ValueError):
        normalized = str(value).strip().lower()
        if "oberstufe" in normalized or normalized in ("go", "gost", "sg"):
            return "Oberstufe"
        # Handle patterns like "10a", "11b", "12/1", "13" as Oberstufe
        for grade in range(10, 14):
            if normalized.startswith(str(grade)):
                return "Oberstufe"
        return None  # Lehrer, Mixed, Finalrunde, Eltern, etc. → no column

    if year >= 10:
        return "Oberstufe"
    if year >= 1:
        return f"Jahrgang {year}"
    return None


def competition_column(competition):
    """Spalte im Tagesplan: beim Schulpokal setzt get_day_schedule_for_event
    year_column (Jahrgang 10 bleibt Jahrgang 10 statt Oberstufe), sonst
    classify_yeargang."""
    if isinstance(competition, dict) and competition.get("year_column"):
        return competition["year_column"]
    jahrgang = competition.get("jahrgang") if hasattr(competition, "get") else competition["jahrgang"]
    return classify_yeargang(jahrgang)


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


def _timeline_status(start_minutes, end_minutes, now_minutes):
    if now_minutes is None:
        return "planned"
    if start_minutes <= now_minutes < end_minutes:
        return "running"
    if end_minutes <= now_minutes:
        return "done"
    return "planned"


# Programmpunkte, die im Tagesplan als eigener Abschnitt eines Blocks
# erscheinen (Slottypen im Spielplan-Editor).
PROGRAMM_SLOT_TYPEN = ("Eröffnung", "Siegerehrung", "Pause")
KO_PHASEN = ("Halbfinale", "Spiel um Platz 3", "Kleines Finale", "Platzierung", "Finale")


def _slot_minutes(value):
    parsed = parse_slot_time(value)
    return parsed.hour * 60 + parsed.minute if parsed else None


def build_block_segments(start_minutes, end_minutes, slots, final_slots=(), programmpunkte=()):
    """Unterteilt einen Tagesplan-Block in seinen Ablauf: Programmpunkte
    (Eroeffnung, Siegerehrung, Pause), die Spiele der Gruppenphase und
    K.-o.-Spiele bzw. das Jahrgangsfinale. Jeder Abschnitt bekommt Lage und
    Hoehe in Prozent des Blocks; Abschnitte ab dem Blockende (z.B.
    Siegerehrung direkt danach) kommen in "after" und werden unter dem
    Ablauf als Zeile gezeigt. programmpunkte sind Programmpunkte der
    Veranstaltung mit eigener Dauer (titel, start_time, end_time, siehe
    programmpunkte_service)."""
    raw = []
    games = [
        slot for slot in slots
        if slot.get("slot_typ") == "Spiel" and _slot_minutes(slot.get("startzeit")) is not None
    ]

    def game_span(game_slots):
        starts = [_slot_minutes(slot.get("startzeit")) for slot in game_slots]
        ends = [
            _slot_minutes(slot.get("game_end_time")) or _slot_minutes(slot.get("startzeit"))
            for slot in game_slots
        ]
        return min(starts), max(ends)

    group_games = [slot for slot in games if slot.get("phase") not in KO_PHASEN]
    if group_games:
        start, end = game_span(group_games)
        raw.append({"kind": "spiele", "label": "Spiele", "start": start, "end": end})
    for phase in KO_PHASEN:
        phase_games = [slot for slot in games if slot.get("phase") == phase]
        if phase_games:
            start, end = game_span(phase_games)
            raw.append({"kind": "ko", "label": phase, "start": start, "end": end})
    if final_slots:
        finals = [slot for slot in final_slots if _slot_minutes(slot.get("startzeit")) is not None]
        if finals:
            start, end = game_span(finals)
            raw.append({"kind": "ko", "label": "Jahrgangsfinale", "start": start, "end": end})

    programm = sorted(
        (
            slot for slot in slots
            if slot.get("slot_typ") in PROGRAMM_SLOT_TYPEN
            and _slot_minutes(slot.get("startzeit")) is not None
        ),
        key=lambda slot: _slot_minutes(slot.get("startzeit")),
    )
    seen = set()
    for punkt in programmpunkte:
        start = _slot_minutes(punkt.get("start_time"))
        end = _slot_minutes(punkt.get("end_time"))
        if start is None or end is None or end <= start:
            continue
        label = (punkt.get("titel") or "").strip() or "Programmpunkt"
        seen.add((label, start))
        raw.append({
            "kind": "programm", "label": label, "start": start, "end": end,
            "time_range": True,
        })
    for slot in programm:
        start = _slot_minutes(slot.get("startzeit"))
        key = (slot.get("slot_typ"), start)
        if key in seen:
            continue
        seen.add(key)
        # Ein Programmpunkt dauert bis zum naechsten Abschnitt, hoechstens
        # bis zum Blockende; ohne Folgeabschnitt 5 Minuten.
        following = [
            item["start"] for item in raw if item["start"] > start
        ] + [
            _slot_minutes(other.get("startzeit")) for other in programm
            if (_slot_minutes(other.get("startzeit")) or 0) > start
        ]
        end = min(following) if following else start + 5
        if start < end_minutes:
            end = min(end, end_minutes)
        label = slot.get("slot_typ")
        note = (slot.get("note") or "").strip()
        if note and note != label:
            label = f"{label}: {note}"
        raw.append({"kind": "programm", "label": label, "start": start, "end": max(end, start + 1)})

    raw.sort(key=lambda item: (item["start"], item["end"]))
    length = max(end_minutes - start_minutes, 1)
    inside, after = [], []
    for item in raw:
        item["time"] = f"{item['start'] // 60:02d}:{item['start'] % 60:02d}"
        if item.pop("time_range", False):
            item["time"] += f"–{item['end'] // 60:02d}:{item['end'] % 60:02d}"
        if item["start"] >= end_minutes or item["end"] <= start_minutes:
            after.append(item)
            continue
        top = max(item["start"], start_minutes)
        bottom = min(item["end"], end_minutes)
        item["top_percent"] = (top - start_minutes) / length * 100
        item["height_percent"] = max((bottom - top) / length * 100, 0)
        inside.append(item)
    return {"inside": inside, "after": after}


def _timestamp_minutes(value):
    """'2026-11-06 08:17:30' (started_at) -> Minuten seit Mitternacht."""
    text = str(value or "").strip()
    if " " in text:
        text = text.split(" ", 1)[1]
    elif "T" in text:
        text = text.split("T", 1)[1]
    return _slot_minutes(text[:5])


def block_verspaetung(slots, now):
    """Aktuelle Verspaetung eines Blocks in Minuten (0 = im Plan) fuer die
    Anzeige bei Admin und Turnierleitung: wie viel spaeter das zuletzt
    gestartete Spiel begonnen hat als geplant, bzw. wie lange das naechste
    geplante Spiel schon ueberfaellig ist. None, wenn der Block noch nicht
    begonnen hat oder alle Spiele beendet sind."""
    if now is None:
        return None
    games = [
        slot for slot in slots
        if slot.get("slot_typ") == "Spiel" and _slot_minutes(slot.get("startzeit")) is not None
    ]
    planned = [slot for slot in games if slot.get("status") == "geplant"]
    if not games or not planned and all(slot.get("status") == "beendet" for slot in games):
        return None
    now_minutes = now.hour * 60 + now.minute
    delays = []
    started = [
        slot for slot in games
        if slot.get("status") in ("läuft", "beendet") and _timestamp_minutes(slot.get("started_at")) is not None
    ]
    if started:
        last = max(started, key=lambda slot: _slot_minutes(slot.get("startzeit")))
        delays.append(_timestamp_minutes(last.get("started_at")) - _slot_minutes(last.get("startzeit")))
    if planned:
        next_start = min(_slot_minutes(slot.get("startzeit")) for slot in planned)
        if now_minutes > next_start:
            delays.append(now_minutes - next_start)
        elif not started:
            return None
    if not delays:
        return None
    return max(max(delays), 0)


def _clock_minutes(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


PROGRAMM_ZEILE_MINUTEN = 11


SPORT_FARBEN = 6


def assign_sport_colors(entries, columns=()):
    """Gibt jedem Wettbewerbsblock im Tagesplan eine Farbnummer
    (farbe 1..SPORT_FARBEN) nach seiner Sportart: gleiche Sportart, gleiche
    Farbe ueber alle Jahrgaenge hinweg, verschiedene Sportarten
    unterscheidbar. Vergeben wird in der Reihenfolge des Auftretens im Plan
    (fruehester Block zuerst, bei gleicher Zeit die Spalte weiter links),
    damit bis zu SPORT_FARBEN Sportarten nie dieselbe Farbe bekommen.
    Programmpunkte (Eroeffnung, Siegerehrung, Finale) bekommen keine."""
    column_order = {column: index for index, column in enumerate(columns)}
    farben = {}
    blocks = [entry for entry in entries if not entry.get("is_programm")]
    for entry in sorted(
        blocks,
        key=lambda e: (e["start_minutes"], column_order.get(e.get("column"), len(column_order))),
    ):
        key = (entry.get("sportart") or entry.get("name") or "").strip().casefold()
        if key not in farben:
            farben[key] = len(farben) % SPORT_FARBEN + 1
        entry["farbe"] = farben[key]
    return farben


def _add_programm_entry(target, column, label, kind, start, end):
    target.append({
        "is_programm": True,
        "kind": kind,
        "competition_id": None,
        "name": label,
        "sportart": "",
        "location": "",
        "column": column,
        "start_minutes": start,
        "end_minutes": max(end, start + 1),
        "display_time": (
            f"{_clock_minutes(start)}–{_clock_minutes(end)}" if end > start + 1 else _clock_minutes(start)
        ),
    })


def split_block_segments(start_minutes, end_minutes, segments):
    """Loest Programmpunkte am Anfang und Ende eines Blocks (Eroeffnung,
    Siegerehrung) sowie alles nach dem Block (Siegerehrung, Jahrgangsfinale)
    als eigene Bloecke heraus. Der Hauptblock zeigt dann nur noch die Spiele
    (und Programmpunkte mittendrin, z.B. eine Pause).

    Gibt (display_start, display_end, inside, separate) zurueck; inside hat
    Lage und Hoehe neu in Prozent des verkuerzten Blocks."""
    display_start, display_end = start_minutes, end_minutes
    inside = list(segments.get("inside", []))
    separate = list(segments.get("after", []))
    changed = True
    while changed:
        changed = False
        for item in [i for i in inside if i["kind"] == "programm"]:
            if item["start"] <= display_start < item["end"] and item["end"] < display_end:
                display_start = item["end"]
            elif item["start"] < display_end <= item["end"] and item["start"] > display_start:
                display_end = item["start"]
            else:
                continue
            inside.remove(item)
            separate.append(item)
            changed = True
    length = max(display_end - display_start, 1)
    for item in inside:
        top = max(item["start"], display_start)
        bottom = min(item["end"], display_end)
        item["top_percent"] = (top - display_start) / length * 100
        item["height_percent"] = max((bottom - top) / length * 100, 0)
    separate.sort(key=lambda item: (item["start"], item["end"]))
    return display_start, display_end, inside, separate


def build_day_timeline(columns, competitions, now=None, slots=None, programmpunkte=None,
                       delay_now=None):
    """Zeitproportionale Kalenderansicht fuer den Dashboard-Tagesplan:
    pro Jahrgangs-Spalte eine Liste absolut positionierter Bloecke (top/
    height in Prozent der gemeinsamen Zeitspanne), plus Halbstunden-Marken
    fuer die linke Achse. Ueberlappende Wettbewerbe in derselben Spalte
    (aktuell nicht der Fall, aber moeglich) werden nebeneinander in Spuren
    aufgeteilt statt sich zu ueberdecken. Mit slots bekommt jeder Block
    seinen Ablauf als Abschnitte (siehe build_block_segments), dazu die
    Programmpunkte der Veranstaltung fuer seinen Jahrgang. Mit delay_now
    (nur am Veranstaltungstag) bekommt er seine Verspaetung
    (block_verspaetung)."""
    entries = []
    slots_by_competition = defaultdict(list)
    for slot in slots or []:
        slots_by_competition[slot.get("competition_id")].append(slot)
    # Jahrgangsfinale (eigener Wettbewerb ohne Jahrgangsspalte) erscheint im
    # Block seiner beiden Quell-Wettbewerbe.
    event_by_competition = {competition.get("id"): competition.get("event_id") for competition in competitions}
    shared_by_event = defaultdict(list)
    for slot in slots or []:
        if slot.get("slot_typ") in ("Eröffnung", "Siegerehrung"):
            event_id = event_by_competition.get(slot.get("competition_id"))
            if event_id is not None:
                shared_by_event[event_id].append(slot)
    programm_entries = []
    finals_by_source = defaultdict(list)
    for competition in competitions:
        for part in str(competition.get("final_source_ids") or "").split(","):
            if part.strip().isdigit():
                finals_by_source[int(part.strip())].append(competition.get("id"))
    for competition in competitions:
        start = parse_slot_time(competition.get("start_time"))
        end = parse_slot_time(competition.get("end_time"))
        if start is None or end is None or end <= start:
            continue
        column = competition_column(competition)
        if column not in columns:
            continue
        entries.append({
            "competition_id": competition.get("id"),
            "name": (
                competition.get("name")
                or competition.get("sportart")
                or "Ohne Bezeichnung"
            ),
            "sportart": competition.get("sportart") or "",
            "location": _location_label(competition),
            "column": column,
            "start_minutes": start.hour * 60 + start.minute,
            "end_minutes": end.hour * 60 + end.minute,
            "display_time": _format_schedule_time_range(
                competition.get("start_time"), competition.get("end_time")
            ),
        })
        if slots is not None:
            competition_id = competition.get("id")
            block_start = entries[-1]["start_minutes"]
            block_end = entries[-1]["end_minutes"]
            # Eroeffnung und Siegerehrung gelten fuer alle Bloecke der
            # Veranstaltung, die zu dieser Zeit laufen oder enden (z.B. eine
            # gemeinsame Siegerehrung fuer Volleyball und Tischtennis).
            shared = [
                slot for slot in shared_by_event.get(competition.get("event_id"), [])
                if slot.get("competition_id") != competition_id
                and block_start <= (_slot_minutes(slot.get("startzeit")) or -1) <= block_end
            ]
            final_slots = [
                slot for final_id in finals_by_source.get(competition_id, [])
                for slot in slots_by_competition.get(final_id, [])
                if slot.get("slot_typ") == "Spiel"
            ]
            # Programmpunkte gehoeren zum Block, wenn sie fuer seinen
            # Jahrgang gelten und waehrend des Blocks oder kurz danach
            # (bis 30 Minuten nach Block bzw. Jahrgangsfinale) beginnen.
            window_end = max(
                [block_end] + [
                    _slot_minutes(slot.get("game_end_time")) or _slot_minutes(slot.get("startzeit")) or 0
                    for slot in final_slots
                ]
            ) + 30
            block_punkte = [
                punkt for punkt in programmpunkte or []
                if applies_to_jahrgang(punkt, competition.get("jahrgang") or "")
                and block_start <= (_slot_minutes(punkt.get("start_time")) or -1) <= window_end
            ]
            segments = build_block_segments(
                block_start,
                block_end,
                slots_by_competition.get(competition_id, []) + shared,
                final_slots,
                block_punkte,
            )
            display_start, display_end, inside, separate = split_block_segments(
                block_start, block_end, segments
            )
            entry = entries[-1]
            # Die Spiele fuellen den Block ohnehin, nur K.-o.-Runden und
            # Programmpunkte mittendrin (Pause) werden markiert.
            entry["segments"] = {
                "inside": [item for item in inside if item["kind"] != "spiele"],
                "after": [],
            }
            if (display_start, display_end) != (block_start, block_end):
                # Drag & Drop verschiebt weiter das Zeitfenster des
                # Wettbewerbs, angezeigt wird nur der Spiele-Teil.
                entry["block_start_minutes"] = block_start
                entry["block_end_minutes"] = block_end
                entry["start_minutes"] = display_start
                entry["end_minutes"] = display_end
                entry["display_time"] = f"{_clock_minutes(display_start)}–{_clock_minutes(display_end)}"
            for item in separate:
                _add_programm_entry(programm_entries, column, item["label"], item["kind"], item["start"], item["end"])
            entries[-1]["verspaetung"] = block_verspaetung(
                slots_by_competition.get(competition_id, []) + final_slots, delay_now
            )

    # Programmpunkte der Veranstaltung (z.B. die gemeinsame Eroeffnung)
    # erscheinen in jeder Spalte, fuer deren Jahrgang sie gelten, auch wenn
    # dort zu der Zeit kein Wettbewerb laeuft.
    column_jahrgaenge = defaultdict(set)
    for competition in competitions:
        column_jahrgaenge[competition_column(competition)].add(str(competition.get("jahrgang") or ""))
    for column in columns:
        if column not in column_jahrgaenge:
            continue
        for punkt in programmpunkte or []:
            start = _slot_minutes(punkt.get("start_time"))
            end = _slot_minutes(punkt.get("end_time"))
            if start is None or end is None or end <= start:
                continue
            if any(applies_to_jahrgang(punkt, jahrgang) for jahrgang in column_jahrgaenge[column]):
                label = (punkt.get("titel") or "").strip() or "Programmpunkt"
                _add_programm_entry(programm_entries, column, label, "programm", start, end)

    # Gleicher Programmpunkt mehrfach (aus mehreren Bloecken einer Spalte
    # oder als Programmpunkt und alter Slot): nur einmal, mit der laengsten
    # Dauer.
    unique = {}
    for item in programm_entries:
        key = (item["column"], item["name"], item["start_minutes"])
        if key not in unique or item["end_minutes"] > unique[key]["end_minutes"]:
            unique[key] = item

    # Direkt aufeinander folgende Programmpunkte einer Spalte (Siegerehrung
    # 10:40, Jahrgangsfinale 10:45) werden ein Block mit mehreren Zeilen,
    # sonst waeren die wenigen Minuten zu schmal fuer die Beschriftung.
    merged = []
    for item in sorted(unique.values(), key=lambda e: (e["column"], e["start_minutes"], e["end_minutes"])):
        line = {"time": item["display_time"], "name": item["name"], "kind": item["kind"]}
        previous = merged[-1] if merged else None
        if (
            previous is not None and previous["column"] == item["column"]
            and item["start_minutes"] <= previous["end_minutes"] + 1
        ):
            previous["end_minutes"] = max(previous["end_minutes"], item["end_minutes"])
            previous["lines"].append(line)
            continue
        item["lines"] = [line]
        merged.append(item)
    for item in merged:
        if any(line["kind"] == "programm" for line in item["lines"]):
            item["kind"] = "programm"
        # Jede Zeile braucht etwa 10 Minuten Hoehe im Plan (siehe
        # min-height im Template), damit unten nichts abgeschnitten wird.
        item["extent_end_minutes"] = max(
            item["end_minutes"], item["start_minutes"] + PROGRAMM_ZEILE_MINUTEN * len(item["lines"]) + 2
        )
    entries.extend(merged)
    if not entries:
        return None
    assign_sport_colors(entries, columns)

    timeline_start = (min(e["start_minutes"] for e in entries) // 30) * 30
    raw_end = max(e.get("extent_end_minutes", e["end_minutes"]) for e in entries)
    timeline_end = -(-raw_end // 30) * 30
    total_minutes = max(timeline_end - timeline_start, 30)

    now_minutes = None
    if now is not None:
        candidate = now.hour * 60 + now.minute
        if timeline_start <= candidate <= timeline_end:
            now_minutes = candidate

    hour_marks = []
    mark = timeline_start
    while mark <= timeline_end:
        hour_marks.append({
            "label": f"{mark // 60:02d}:{mark % 60:02d}",
            "top_percent": (mark - timeline_start) / total_minutes * 100,
        })
        mark += 30

    columns_out = []
    for column in columns:
        column_entries = sorted(
            (entry for entry in entries if entry["column"] == column),
            key=lambda entry: (entry["start_minutes"], entry["end_minutes"]),
        )

        # Ueberlappende Eintraege in Cluster gruppieren, statt Spuren ueber die
        # ganze Spalte zu zaehlen - sonst wuerde z.B. ein einzelner, spaeter
        # stattfindender Wettbewerb unnoetig auf halbe Breite gequetscht, nur
        # weil zwei ANDERE, zeitlich ueberschneidende Wettbewerbe zuvor
        # zwei Spuren gebraucht haben.
        clusters = []
        current_cluster = []
        cluster_end = None
        for entry in column_entries:
            if current_cluster and entry["start_minutes"] < cluster_end:
                current_cluster.append(entry)
                cluster_end = max(cluster_end, entry["end_minutes"])
            else:
                if current_cluster:
                    clusters.append(current_cluster)
                current_cluster = [entry]
                cluster_end = entry["end_minutes"]
        if current_cluster:
            clusters.append(current_cluster)

        for cluster in clusters:
            lane_ends = []
            for entry in cluster:
                placed = False
                for lane_index, lane_end in enumerate(lane_ends):
                    if entry["start_minutes"] >= lane_end:
                        lane_ends[lane_index] = entry["end_minutes"]
                        entry["lane"] = lane_index
                        placed = True
                        break
                if not placed:
                    entry["lane"] = len(lane_ends)
                    lane_ends.append(entry["end_minutes"])
            lane_count = max(len(lane_ends), 1)
            for entry in cluster:
                entry["lane_width_percent"] = 100 / lane_count
                entry["lane_left_percent"] = entry["lane"] * entry["lane_width_percent"]

        for entry in column_entries:
            entry["top_percent"] = (
                (entry["start_minutes"] - timeline_start) / total_minutes * 100
            )
            entry["height_percent"] = (
                (entry["end_minutes"] - entry["start_minutes"]) / total_minutes * 100
            )
            entry["status_key"] = _timeline_status(
                entry["start_minutes"], entry["end_minutes"], now_minutes
            )
        columns_out.append({"label": column, "items": column_entries})

    now_top_percent = (
        (now_minutes - timeline_start) / total_minutes * 100
        if now_minutes is not None
        else None
    )

    return {
        "hour_marks": hour_marks,
        "columns": columns_out,
        "now_top_percent": now_top_percent,
        "timeline_start": timeline_start,
        "total_minutes": total_minutes,
    }


def enrich_day_schedule_view(schedule, competitions, now=None, programmpunkte=None):
    """Add presentation-only cards and marker data to an existing day schedule."""
    view_schedule = dict(schedule)
    rows = [dict(row) for row in schedule.get("rows", [])]
    view_schedule["rows"] = rows

    competition_ids = [
        competition.get("id")
        for competition in competitions
        if competition.get("id") is not None
    ]
    slots = get_all_slots(competition_ids=competition_ids) if competition_ids else []
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
            "column": competition_column(competition),
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
    view_schedule["timeline"] = build_day_timeline(
        schedule.get("columns", []), competitions, now=now, slots=slots,
        programmpunkte=programmpunkte,
        delay_now=(now or datetime.now()) if schedule.get("event_state") == "today" else None,
    )
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
