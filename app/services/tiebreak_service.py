"""Einstellbare Gleichstandsregeln fuer Turniertabellen (Einrichtungsassistent,
Expertenmodus).

Nach den Punkten entscheidet die Tabelle standardmaessig (Spalte
competitions.tiebreak_order leer) wie bisher: direkter Vergleich der
punktgleichen Teams, dann Tordifferenz, dann Tore - siehe
_resolve_points_tied_group in main.py, das dafuer unveraendert bleibt.

Ist eine Reihenfolge gespeichert, sortiert resolve_with_order die
punktgleichen Teams exakt in dieser Reihenfolge. Der direkte Vergleich ist
dabei wie bisher eine Mini-Tabelle (Punkte, Tordifferenz, Tore) nur aus den
Spielen der punktgleichen Teams untereinander."""

TIEBREAK_KRITERIEN = {
    "direkter_vergleich": "Direkter Vergleich",
    "tordifferenz": "Tordifferenz",
    "tore": "Erzielte Tore",
}
DEFAULT_TIEBREAK_ORDER = ("direkter_vergleich", "tordifferenz", "tore")

_NOTES = {
    "direkter_vergleich": "Platzierung durch den direkten Vergleich der punktgleichen Teams entschieden",
    "tordifferenz": "Platzierung durch Tordifferenz entschieden",
    "tore": "Platzierung durch Anzahl geschossener Tore entschieden",
}
_UNRESOLVED = "Punktgleichstand ohne eindeutige Entscheidung – Reihenfolge alphabetisch"
_CYCLE = (
    "Zyklischer Gleichstand (z.B. A schlägt B, B schlägt C, C schlägt A) – keine "
    "eindeutige Auflösung möglich, Reihenfolge alphabetisch"
)


def parse_tiebreak_order(value):
    """'tore, tordifferenz' -> ['tore', 'tordifferenz', 'direkter_vergleich'].
    Unbekannte Eintraege fallen weg, fehlende Kriterien werden in der
    Standardreihenfolge angehaengt. Leer/None -> None (Standard)."""
    if value is None:
        return None
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
    else:
        parts = [str(part).strip() for part in value]
    order = []
    for part in parts:
        if part in TIEBREAK_KRITERIEN and part not in order:
            order.append(part)
    if not order:
        return None
    for key in DEFAULT_TIEBREAK_ORDER:
        if key not in order:
            order.append(key)
    return order


def format_tiebreak_order(order):
    """Speicherwert fuer competitions.tiebreak_order; die Standardreihenfolge
    wird als NULL gespeichert, damit die Tabelle den bisherigen Weg nimmt."""
    parsed = parse_tiebreak_order(order)
    if parsed is None or tuple(parsed) == DEFAULT_TIEBREAK_ORDER:
        return None
    return ",".join(parsed)


def competition_tiebreak_order(competition):
    try:
        value = competition["tiebreak_order"]
    except (KeyError, IndexError, TypeError):
        return None
    return parse_tiebreak_order(value)


def describe_tiebreak_order(order):
    order = parse_tiebreak_order(order) or list(DEFAULT_TIEBREAK_ORDER)
    return ["Punkte"] + [TIEBREAK_KRITERIEN[key] for key in order]


def resolve_with_order(rows_group, slots, competition, order, mini_league_stats):
    """Sortiert eine Gruppe punktgleicher Tabellenzeilen nach order und setzt
    row['tie_note']. mini_league_stats ist main._mini_league_stats (Punkte,
    Tordifferenz, Tore nur aus den Duellen untereinander)."""
    if len(rows_group) < 2:
        for row in rows_group:
            row["tie_note"] = None
        return rows_group

    team_names = [row["team"] for row in rows_group]
    rows_by_team = {row["team"]: row for row in rows_group}
    mini = mini_league_stats(team_names, slots, competition)

    def criterion_values(team):
        values = []
        for key in order:
            if key == "direkter_vergleich":
                values.append((-mini[team]["pkt"], -mini[team]["diff"], -mini[team]["plus"]))
            elif key == "tordifferenz":
                values.append(-rows_by_team[team]["diff"])
            elif key == "tore":
                values.append(-rows_by_team[team]["plus"])
        return tuple(values)

    ordered = sorted(team_names, key=lambda team: (criterion_values(team), team.lower()))

    notes = {}
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and criterion_values(ordered[j + 1]) == criterion_values(ordered[i]):
            j += 1
        if j > i:
            label = _CYCLE if (j - i + 1) >= 3 else _UNRESOLVED
            for team in ordered[i:j + 1]:
                notes[team] = label
        elif i > 0:
            previous = ordered[i - 1]
            for index, key in enumerate(order):
                if criterion_values(previous)[index] != criterion_values(ordered[i])[index]:
                    notes.setdefault(previous, _NOTES[key])
                    notes[ordered[i]] = _NOTES[key]
                    break
        i = j + 1

    for row in rows_group:
        row["tie_note"] = notes.get(row["team"])
    return [rows_by_team[name] for name in ordered]
