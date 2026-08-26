import re


def format_bytes(byte_count: int):
    value = float(max(byte_count, 0))
    units = ["B", "KB", "MB", "GB"]
    unit_index = 0
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(value)} {units[unit_index]}"
    return f"{value:.1f} {units[unit_index]}"


def format_score(score_a, score_b):
    if score_a is None and score_b is None:
        return "–"
    return f"{score_a if score_a is not None else '–'}:{score_b if score_b is not None else '–'}"


def parse_score(value):
    """Kehrt format_score() um, z.B. fuer die Rueckgaengig-Funktion auf /ergebnisse,
    die einen im Aenderungsprotokoll gespeicherten alten Wert wiederherstellt."""
    if not value or value == "–":
        return None, None
    parts = str(value).split(":", 1)
    if len(parts) != 2:
        return None, None

    def parse_part(part):
        part = part.strip()
        if part == "" or part == "–":
            return None
        try:
            return int(part)
        except ValueError:
            return None

    return parse_part(parts[0]), parse_part(parts[1])


def format_sixkampf_values(values):
    if not values:
        return "–"
    return " · ".join(
        f"Wert {value_index}: {value:g}" if value is not None else f"Wert {value_index}: –"
        for value_index, value in sorted(values.items())
    )


def slugify_filename_part(value: str):
    normalized = (value or "").strip().lower()
    replacements = {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "ß": "ss",
    }
    for old_char, new_char in replacements.items():
        normalized = normalized.replace(old_char, new_char)
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized)
    return normalized.strip("_") or "export"


def format_points_value(value):
    if value is None:
        return None
    if float(value).is_integer():
        return str(int(value))
    return str(value).replace(".", ",")