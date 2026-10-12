"""Konfliktpruefung eines Spielplan-Vorschlags gegen den bereits gespeicherten
Spielplan aller Wettbewerbe (ROADMAP Phase 4.7: "Cross-Wettbewerb-
Konfliktpruefung" und "Serverseitige Validierung beim Uebernehmen").

Die bestehenden validate_*_plan-Funktionen pruefen nur den Vorschlag in sich.
Hier geht es um Kollisionen mit Spielen, die schon in der Datenbank stehen:
dasselbe Team oder dasselbe Feld zur selben Zeit in einem anderen Wettbewerb.

Slots speichern nur eine Uhrzeit, kein Datum. Verglichen werden deshalb nur
Wettbewerbe am selben Tag (eigenes Datum, sonst Datum der Veranstaltung).
Fehlt einer Seite das Datum, ist der Tag unbekannt - eine Ueberschneidung
wird dann nur als Hinweis gemeldet, nicht als Fehler.
"""
import re

from app.services.schedule_time_service import get_competition_timing

_TIME_PATTERN = re.compile(r"^\s*(\d{1,3}):(\d{2})(?::\d{2})?\s*$")

# Mehr Einzelmeldungen helfen in der Vorschau nicht weiter.
MAX_CONFLICT_MESSAGES = 8


def _to_minutes(startzeit):
    """'HH:MM' -> Minuten seit Mitternacht. Stunden >= 24 sind erlaubt (siehe
    _minutes_to_clock im Generator), damit sehr lange Plaene nicht auf
    00:00 zurueckspringen."""
    match = _TIME_PATTERN.match(str(startzeit or ""))
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def _competition_day(competition, event_dates):
    own_date = (competition["competition_date"] or "").strip()
    if own_date:
        return own_date
    event_date = (event_dates.get(competition["event_id"]) or "").strip()
    return event_date or None


def _to_int(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _interval(slot, competitions):
    start = _to_minutes(slot["startzeit"])
    competition = competitions.get(_to_int(slot["competition_id"]))
    if start is None or competition is None:
        return None
    duration = get_competition_timing(competition)["game_duration_minutes"]
    return start, start + duration


def _overlaps(a, b):
    return a[0] < b[1] and b[0] < a[1]


def _load_context(conn):
    competitions = {
        row["id"]: row for row in conn.execute("SELECT * FROM competitions").fetchall()
    }
    event_dates = {
        row["id"]: row["event_date"]
        for row in conn.execute("SELECT id, event_date FROM events").fetchall()
    }
    team_names = {
        row["id"]: row["name"] for row in conn.execute("SELECT id, name FROM teams").fetchall()
    }
    court_names = {
        row["id"]: row["name"] for row in conn.execute("SELECT id, name FROM courts").fetchall()
    }
    return competitions, event_dates, team_names, court_names


def _limit(messages, level):
    warnings = [{"level": level, "message": message} for message in messages[:MAX_CONFLICT_MESSAGES]]
    if len(messages) > MAX_CONFLICT_MESSAGES:
        warnings.append({
            "level": level,
            "message": f"... und {len(messages) - MAX_CONFLICT_MESSAGES} weitere Überschneidungen.",
        })
    return warnings


def find_existing_schedule_conflicts(conn, proposed_slots):
    """Vergleicht die Spiele eines Vorschlags mit allen gespeicherten Spielen
    nicht archivierter Wettbewerbe und liefert Warnungen im Format der
    validate_*_plan-Funktionen ({"level", "message"}).

    - anderer Wettbewerb, gleicher bekannter Tag: Fehler
    - anderer Wettbewerb, Tag unbekannt: Hinweis
    - derselbe Wettbewerb hat schon gespeicherte Spiele: ein Hinweis, dass
      die Uebernahme zusaetzliche Slots anlegt (z.B. vorher "geplante Spiele
      loeschen")."""
    proposed_games = [slot for slot in proposed_slots if slot.get("slot_typ") == "Spiel"]
    if not proposed_games:
        return []

    competitions, event_dates, team_names, court_names = _load_context(conn)
    proposed_competition_ids = {
        _to_int(slot["competition_id"]) for slot in proposed_games
    }

    existing_rows = conn.execute("""
        SELECT slots.* FROM slots
        JOIN competitions c ON c.id = slots.competition_id
        LEFT JOIN events e ON e.id = c.event_id
        WHERE slots.slot_typ = 'Spiel'
          AND COALESCE(c.status, '') != 'archiviert'
          AND COALESCE(e.status, '') != 'archiviert'
    """).fetchall()

    own_existing = [
        row for row in existing_rows if row["competition_id"] in proposed_competition_ids
    ]
    other_existing = [
        row for row in existing_rows if row["competition_id"] not in proposed_competition_ids
    ]

    errors = []
    hints = []
    seen = set()

    for slot in proposed_games:
        interval = _interval(slot, competitions)
        competition = competitions.get(_to_int(slot["competition_id"]))
        if interval is None or competition is None:
            continue
        day = _competition_day(competition, event_dates)
        slot_team_ids = {
            team_id for team_id in (_to_int(slot.get("team_a_id")), _to_int(slot.get("team_b_id")))
            if team_id is not None
        }
        slot_court_id = _to_int(slot.get("court_id"))

        for existing in other_existing:
            other_competition = competitions[existing["competition_id"]]
            other_day = _competition_day(other_competition, event_dates)
            if day is not None and other_day is not None and day != other_day:
                continue
            other_interval = _interval(existing, competitions)
            if other_interval is None or not _overlaps(interval, other_interval):
                continue

            target = errors if day is not None and other_day is not None else hints
            for team_id in slot_team_ids & {existing["team_a_id"], existing["team_b_id"]}:
                key = ("team", team_id, existing["id"])
                if key in seen:
                    continue
                seen.add(key)
                target.append(
                    f'{team_names.get(team_id, "Team")} spielt um {slot["startzeit"]} '
                    f'bereits in "{other_competition["name"]}" ({existing["startzeit"]}).'
                )
            if slot_court_id is not None and slot_court_id == existing["court_id"]:
                key = ("court", slot_court_id, existing["id"])
                if key not in seen:
                    seen.add(key)
                    target.append(
                        f'{court_names.get(slot_court_id, "Feld")} ist um {slot["startzeit"]} '
                        f'bereits durch "{other_competition["name"]}" ({existing["startzeit"]}) belegt.'
                    )

    warnings = _limit(errors, "error")
    if hints:
        hint_warnings = _limit(hints, "warning")
        hint_warnings[0] = {
            "level": "warning",
            "message": (
                "Mögliche Überschneidung (Datum eines Wettbewerbs unbekannt): "
                + hint_warnings[0]["message"]
            ),
        }
        warnings.extend(hint_warnings)

    if own_existing:
        warnings.append({
            "level": "warning",
            "message": (
                f"Für diesen Wettbewerb sind bereits {len(own_existing)} Spiele gespeichert. "
                "Beim Übernehmen kommen die neuen Spiele hinzu."
            ),
        })
    return warnings


def find_internal_conflicts(conn, proposed_slots):
    """Prueft einen (z.B. per Formular uebermittelten) Vorschlag in sich:
    Team gegen sich selbst, Team oder Feld zweimal zur selben Zeit. Die
    Vorschau prueft das je Modus schon, beim Uebernehmen muss es aber
    serverseitig erneut geschehen, weil das Formular beliebig veraendert
    abgeschickt werden kann."""
    competitions, _, team_names, court_names = _load_context(conn)
    games = [slot for slot in proposed_slots if slot.get("slot_typ") == "Spiel"]
    messages = []

    for slot in games:
        team_a = _to_int(slot.get("team_a_id"))
        if team_a is not None and team_a == _to_int(slot.get("team_b_id")):
            messages.append(f'{team_names.get(team_a, "Ein Team")} spielt um {slot["startzeit"]} gegen sich selbst.')

    for index, slot in enumerate(games):
        interval = _interval(slot, competitions)
        if interval is None:
            continue
        teams = {
            team_id for team_id in (_to_int(slot.get("team_a_id")), _to_int(slot.get("team_b_id")))
            if team_id is not None
        }
        for other in games[index + 1:]:
            other_interval = _interval(other, competitions)
            if other_interval is None or not _overlaps(interval, other_interval):
                continue
            other_teams = {
                team_id for team_id in (_to_int(other.get("team_a_id")), _to_int(other.get("team_b_id")))
                if team_id is not None
            }
            for team_id in teams & other_teams:
                messages.append(
                    f'{team_names.get(team_id, "Ein Team")} ist um {slot["startzeit"]} '
                    "gleichzeitig für mehrere Spiele eingeplant."
                )
            court_id = _to_int(slot.get("court_id"))
            if court_id is not None and court_id == _to_int(other.get("court_id")):
                messages.append(
                    f'{court_names.get(court_id, "Ein Feld")} ist um {slot["startzeit"]} doppelt belegt.'
                )

    return _limit(messages, "error")
