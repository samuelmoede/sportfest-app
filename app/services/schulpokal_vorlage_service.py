"""Vorlage Schulpokal (ROADMAP Phase 7): legt eine Veranstaltung mit allen
Schulpokal-Wettbewerben in einem Schritt an.

Ein Block ist eine Sportart fuer einen oder mehrere Jahrgaenge an einem Ort
in einem Zeitfenster, z.B. Zweifelderball fuer Jahrgang 7 und 8 in der
Turnhalle von 08:00 bis 10:40. Je Jahrgang entsteht ein Wettbewerb vom Typ
Schulpokal; bei genau zwei Jahrgaengen und einer Finalzeit zusaetzlich das
Jahrgangsfinale (siehe jahrgangsfinale_service). Den Spielplan erzeugt man
danach wie gewohnt im Plan-Generator (abwechselnd, mit Spielzeit-Vorschlag
aus dem Zeitfenster)."""
from app.routes.teams import normalize_jahrgang
from app.services.jahrgangsfinale_service import create_jahrgangsfinale
from app.services.programmpunkte_service import create_programmpunkt
from app.services.schedule_generator_service import DEFAULT_SCHULPOKAL_MODE
from app.services.schedule_location_service import (
    DEFAULT_COMPETITION_LOCATION,
    normalize_competition_location,
)
from app.services.schedule_time_service import (
    DEFAULT_CHANGEOVER_DURATION_MINUTES,
    DEFAULT_GAME_DURATION_MINUTES,
)

SCHULPOKAL_EVENT_TYPE = "Schulpokal"
SIEGEREHRUNG_DAUER_MINUTEN = 15


def _add_minutes(clock, minutes):
    hours, mins = (int(part) for part in clock.split(":")[:2])
    total = (hours * 60 + mins + minutes) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


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


def parse_jahrgaenge(value):
    jahrgaenge = []
    for part in str(value or "").replace(";", ",").replace("/", ",").split(","):
        jahrgang = normalize_jahrgang(part)
        if jahrgang is not None and jahrgang not in jahrgaenge:
            jahrgaenge.append(jahrgang)
    return jahrgaenge


def parse_teilmannschaften(value):
    """'Jungen, Mädchen' -> ['Jungen', 'Mädchen']; leer -> []."""
    result = []
    for part in str(value or "").replace(";", ",").replace("/", ",").split(","):
        part = part.strip()
        if part and part.casefold() not in {item.casefold() for item in result}:
            result.append(part)
    return result


def parse_points(points_win, points_draw, competition_type=SCHULPOKAL_EVENT_TYPE):
    """Punkte fuer Sieg und Unentschieden. Leer: beim Schulpokal 2/1 (wie
    in der Gesamtwertung der Sportfachschaft), sonst wie bisher 3/1."""
    default_win, default_draw = (2, 1) if competition_type == SCHULPOKAL_EVENT_TYPE else (3, 1)

    def number(value, default):
        try:
            parsed = int(str(value).strip())
        except (TypeError, ValueError):
            return default
        return parsed if parsed >= 0 else default

    return number(points_win, default_win), number(points_draw, default_draw)


def _unique_name(conn, base_name):
    candidate = base_name
    suffix = 2
    while conn.execute("SELECT 1 FROM competitions WHERE name = ?", (candidate,)).fetchone():
        candidate = f"{base_name} ({suffix})"
        suffix += 1
    return candidate


def create_schulpokal_from_template(conn, *, name, event_date, bloecke):
    """Legt Veranstaltung, Wettbewerbe und Jahrgangsfinals an. Gibt
    (event_id, hinweise) zurueck; hinweise nennt uebersprungene Jahrgaenge.
    Wirft ValueError, wenn die Veranstaltung nicht angelegt werden kann."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Bitte einen Namen für die Veranstaltung angeben.")
    if conn.execute("SELECT 1 FROM events WHERE name = ?", (name,)).fetchone():
        raise ValueError("Eine Veranstaltung mit diesem Namen existiert bereits.")

    event_id = conn.execute(
        """
        INSERT INTO events (name, description, details, event_date, status, event_type)
        VALUES (?, '', '', ?, 'geplant', ?)
        """,
        (name, (event_date or "").strip() or None, SCHULPOKAL_EVENT_TYPE),
    ).lastrowid

    hinweise = []
    siegerehrungen = {}
    for block in bloecke:
        sportart = (block.get("sportart") or "").strip()
        if not sportart:
            continue
        location = (
            normalize_competition_location(block.get("location"))
            or DEFAULT_COMPETITION_LOCATION
        )
        start_time = (block.get("start_time") or "").strip() or None
        end_time = (block.get("end_time") or "").strip() or None
        competition_ids = []
        created_jahrgaenge = []
        teilmannschaften = parse_teilmannschaften(block.get("teilmannschaften")) or [None]
        points_win, points_draw = parse_points(block.get("points_win"), block.get("points_draw"))
        for jahrgang in parse_jahrgaenge(block.get("jahrgaenge")):
            team_count = conn.execute(
                "SELECT COUNT(*) AS n FROM teams WHERE active = 1 AND jahrgang = ?",
                (jahrgang,),
            ).fetchone()["n"]
            if team_count < 2:
                hinweise.append(
                    f"{sportart} Jahrgang {jahrgang}: weniger als zwei aktive Klassen, nicht angelegt."
                )
                continue
            created_jahrgaenge.append(str(jahrgang))
            for teilmannschaft in teilmannschaften:
                label = f"{sportart} {teilmannschaft}" if teilmannschaft else sportart
                competition_ids.append(conn.execute(
                    """
                    INSERT INTO competitions (
                        name, sportart, jahrgang, status, points_win, points_draw,
                        points_loss, points_first_place, event_id, competition_type,
                        tournament_mode, game_duration_minutes, changeover_duration_minutes,
                        location, start_time, end_time, teilmannschaft
                    ) VALUES (?, ?, ?, 'geplant', ?, ?, 0, ?, ?, 'Schulpokal', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _unique_name(conn, f"{label} Jahrgang {jahrgang}"),
                        sportart, jahrgang, points_win, points_draw, team_count, event_id,
                        DEFAULT_SCHULPOKAL_MODE,
                        DEFAULT_GAME_DURATION_MINUTES, DEFAULT_CHANGEOVER_DURATION_MINUTES,
                        location, start_time, end_time, teilmannschaft,
                    ),
                ).lastrowid)

        finale_time = (block.get("finale_time") or "").strip()
        if finale_time and len(competition_ids) == 2 and teilmannschaften == [None]:
            create_jahrgangsfinale(conn, competition_ids, finale_time)
        elif finale_time and competition_ids:
            hinweise.append(
                f"{sportart}: Ein Jahrgangsfinale braucht genau zwei Jahrgänge, es wurde keins angelegt."
            )

        # Siegerehrung als Programmpunkt der Veranstaltung direkt nach dem
        # Block, bis zum Jahrgangsfinale (das nicht mehr in die Wertung
        # eingeht) bzw. 15 Minuten. Parallele Bloecke mit gleichem Ende
        # (Volleyball und Tischtennis) teilen sich eine Siegerehrung.
        if end_time and competition_ids and block.get("siegerehrung", True):
            ceremony = siegerehrungen.setdefault(end_time, {"jahrgaenge": [], "finale": []})
            for jahrgang in created_jahrgaenge:
                if jahrgang not in ceremony["jahrgaenge"]:
                    ceremony["jahrgaenge"].append(jahrgang)
            if finale_time and len(competition_ids) == 2 and teilmannschaften == [None]:
                ceremony["finale"].append(finale_time)

    for end_time, ceremony in siegerehrungen.items():
        start = _add_minutes(end_time, 0)
        finals = sorted(time for time in ceremony["finale"] if _add_minutes(time, 0) > start)
        create_programmpunkt(
            conn, event_id, "Siegerehrung", start,
            finals[0] if finals else _add_minutes(start, SIEGEREHRUNG_DAUER_MINUTEN),
            ceremony["jahrgaenge"],
        )

    return event_id, hinweise
