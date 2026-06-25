from datetime import datetime, timedelta


def parse_grid_time(value: str):
    if not value:
        return None
    for time_format in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(value, time_format)
        except ValueError:
            continue
    return None


def build_editor_time_grid(groups, competition=None, timing=None):
    time_values = {
        slot["startzeit"]
        for group in groups
        for slot in group["slots"]
        if parse_grid_time(slot.get("startzeit")) is not None
    }

    if (
        competition is not None
        and competition["competition_type"] == "Turnier"
        and timing is not None
    ):
        interval = timing["slot_interval_minutes"]
        start = parse_grid_time(competition["start_time"])
        planned_end = parse_grid_time(competition["end_time"])

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

    time_marks = sorted(time_values, key=lambda value: parse_grid_time(value))
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