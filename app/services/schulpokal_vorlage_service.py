"""Vorlage Schulpokal (ROADMAP Phase 7): die Bloecke des Schulpokals laut
Ausschreibung der Sportfachschaft. Der Einrichtungsassistent
(assistent_entwurf_service) baut daraus seine System-Vorlage „Schulpokal“.

Ein Block ist eine Sportart fuer einen oder mehrere Jahrgaenge an einem Ort
in einem Zeitfenster, z.B. Zweifelderball fuer Jahrgang 7 und 8 in der
Turnhalle von 08:00 bis 10:40."""

SCHULPOKAL_EVENT_TYPE = "Schulpokal"


# Ablauf des Schulpokals laut Ausschreibung der Sportfachschaft.
DEFAULT_SCHULPOKAL_BLOECKE = (
    {
        "sportart": "Zweifelderball",
        "jahrgaenge": "7, 8",
        "location": "Turnhalle",
        "start_time": "08:00",
        "end_time": "10:40",
        "finale_time": "10:45",
        "teilmannschaften": "",
        "points_win": 2,
        "points_draw": 1,
    },
    {
        "sportart": "Volleyball",
        "jahrgaenge": "9, 10",
        "location": "Turnhalle",
        "start_time": "11:10",
        "end_time": "13:40",
        "finale_time": "13:50",
        "teilmannschaften": "",
        "points_win": 2,
        "points_draw": 1,
    },
    {
        "sportart": "Tischtennis",
        "jahrgaenge": "9, 10",
        "location": "Mensa",
        "start_time": "11:10",
        "end_time": "13:40",
        "finale_time": "",
        # Jungen spielen gegen Jungen, Maedchen gegen Maedchen; jeder Sieg
        # bringt der Klasse einen Punkt (Wertung der Sportfachschaft).
        "teilmannschaften": "Jungen, Mädchen",
        "points_win": 1,
        "points_draw": 0,
    },
)


def parse_teilmannschaften(value):
    """'Jungen, Mädchen' -> ['Jungen', 'Mädchen']; leer -> []."""
    result = []
    for part in str(value or "").replace(";", ",").replace("/", ",").split(","):
        part = part.strip()
        if part and part.casefold() not in {item.casefold() for item in result}:
            result.append(part)
    return result
