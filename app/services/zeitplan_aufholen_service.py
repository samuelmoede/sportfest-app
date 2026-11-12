"""Zeitplan aufholen: Liegt ein Block hinter dem Plan, kann die
Turnierleitung Spiel- und Wechselzeit kuerzen (auch um 30 Sekunden). Die noch
nicht gestarteten Spiele des Blocks werden dann ab der naechsten Runde neu
getaktet. Die Vorschau zeigt, wann das letzte Spiel ohne und mit Aenderung
endet und ob die naechste Station (z.B. die Siegerehrung) wieder puenktlich
ist.

Ein Block sind alle Wettbewerbe derselben Veranstaltung, Sportart und
desselben Orts mit ueberlappendem Zeitfenster (z.B. Zweifelderball Jahrgang 7
und 8, die sich die Felder teilen). Eine Runde ist eine Startzeit."""
from datetime import datetime

from app.services.jahrgangsfinale_service import parse_final_source_ids
from app.services.programmpunkte_service import applies_to_jahrgang, list_programmpunkte
from app.services.schedule_location_service import get_effective_competition_location
from app.services.schedule_time_service import (
    format_duration_seconds,
    get_competition_timing,
    parse_slot_time,
)

MIN_SPIELZEIT_SEKUNDEN = 60


def _seconds(value):
    parsed = parse_slot_time(value)
    return parsed.hour * 3600 + parsed.minute * 60 if parsed else None


def _clock(seconds):
    minutes = (int(seconds) + 30) // 60
    return f"{(minutes // 60) % 24:02d}:{minutes % 60:02d}"


def _timestamp_seconds(value):
    text = str(value or "").strip()
    if " " in text:
        text = text.split(" ", 1)[1]
    elif "T" in text:
        text = text.split("T", 1)[1]
    parsed = parse_slot_time(text[:8]) or parse_slot_time(text[:5])
    return parsed.hour * 3600 + parsed.minute * 60 + parsed.second if parsed else None


def _overlaps(first, second):
    a_start, a_end = _seconds(first["start_time"]), _seconds(first["end_time"])
    b_start, b_end = _seconds(second["start_time"]), _seconds(second["end_time"])
    if None in (a_start, b_start):
        return True
    a_end = a_end if a_end is not None else a_start + 1
    b_end = b_end if b_end is not None else b_start + 1
    return a_start < b_end and b_start < a_end


def load_block_competitions(conn, competition_id):
    """Der Wettbewerb und seine Partner im selben Block (siehe Modul-Doku)."""
    competition = conn.execute(
        "SELECT * FROM competitions WHERE id = ?", (competition_id,)
    ).fetchone()
    if competition is None:
        return []
    competition = dict(competition)
    if competition.get("event_id") is None:
        return [competition]
    location = get_effective_competition_location(competition)
    sportart = str(competition.get("sportart") or "").strip().casefold()
    rows = conn.execute(
        """
        SELECT * FROM competitions
        WHERE event_id = ? AND COALESCE(status, '') != 'archiviert'
          AND (final_source_ids IS NULL OR final_source_ids = '')
        ORDER BY jahrgang, teilmannschaft, id
        """,
        (competition["event_id"],),
    ).fetchall()
    block = [
        dict(row) for row in rows
        if str(row["sportart"] or "").strip().casefold() == sportart
        and get_effective_competition_location(row) == location
        and _overlaps(row, competition)
    ]
    if not any(item["id"] == competition["id"] for item in block):
        block.insert(0, competition)
    return block


def _projection(rounds, base_seconds, game_seconds, changeover_minutes):
    step = game_seconds + changeover_minutes * 60
    starts = [base_seconds + index * step for index in range(len(rounds))]
    end = starts[-1] + game_seconds if starts else None
    return starts, end


def _default_base(running_starts, first_planned, game_seconds, changeover_minutes, now_seconds):
    """Wann die naechste Runde beginnen kann: nach der laufenden Runde
    (Start + Spielzeit + Wechselzeit), sonst frueheste geplante Zeit, aber
    nicht vor jetzt. Auf volle Minuten aufgerundet."""
    candidates = [first_planned]
    if running_starts:
        candidates.append(max(running_starts) + game_seconds + changeover_minutes * 60)
    if now_seconds is not None:
        candidates.append(now_seconds)
    base = max(candidates)
    return base + (-base) % 60


def berechne_aufholen(conn, competition_id, spielzeit_sekunden=None, wechselzeit_minuten=None,
                      naechste_runde=None, now=None):
    """Vorschlag zum Aufholen. now nur am Veranstaltungstag uebergeben,
    sonst wird ab der geplanten Zeit gerechnet. Gibt None zurueck, wenn der
    Wettbewerb nicht existiert."""
    competitions = load_block_competitions(conn, competition_id)
    if not competitions:
        return None
    ids = [competition["id"] for competition in competitions]
    placeholders = ", ".join("?" for _ in ids)
    slots = [dict(row) for row in conn.execute(
        f"""
        SELECT id, competition_id, startzeit, status, started_at, phase
        FROM slots
        WHERE competition_id IN ({placeholders}) AND slot_typ = 'Spiel'
        """,
        ids,
    ).fetchall()]

    timing = get_competition_timing(competitions[0])
    alt_spielzeit = timing["game_duration_seconds"]
    alt_wechselzeit = timing["changeover_duration_minutes"]
    neu_spielzeit = alt_spielzeit if spielzeit_sekunden in (None, "") else int(spielzeit_sekunden)
    neu_wechselzeit = alt_wechselzeit if wechselzeit_minuten in (None, "") else int(wechselzeit_minuten)
    fehler = []
    if neu_spielzeit < MIN_SPIELZEIT_SEKUNDEN:
        fehler.append("Die Spielzeit muss mindestens eine Minute betragen.")
        neu_spielzeit = alt_spielzeit
    if neu_wechselzeit < 0:
        fehler.append("Die Wechselzeit darf nicht negativ sein.")
        neu_wechselzeit = alt_wechselzeit

    planned = [slot for slot in slots if slot["status"] == "geplant" and _seconds(slot["startzeit"]) is not None]
    round_starts = sorted({_seconds(slot["startzeit"]) for slot in planned})
    rounds = [
        {
            "alt": _clock(start),
            "slot_ids": [slot["id"] for slot in planned if _seconds(slot["startzeit"]) == start],
        }
        for start in round_starts
    ]
    for item in rounds:
        item["count"] = len(item["slot_ids"])
    running_starts = [
        _timestamp_seconds(slot["started_at"]) for slot in slots
        if slot["status"] == "läuft" and _timestamp_seconds(slot["started_at"]) is not None
    ]
    now_seconds = now.hour * 3600 + now.minute * 60 + now.second if now is not None else None

    result = {
        "competitions": competitions,
        "competition_id": competition_id,
        "alt_spielzeit": alt_spielzeit,
        "alt_spielzeit_label": format_duration_seconds(alt_spielzeit),
        "alt_wechselzeit": alt_wechselzeit,
        "neu_spielzeit": neu_spielzeit,
        "neu_spielzeit_label": format_duration_seconds(neu_spielzeit),
        "neu_wechselzeit": neu_wechselzeit,
        "rounds": rounds,
        "laeuft": bool(running_starts),
        "fehler": fehler,
        "ziel": None,
    }
    if not rounds:
        return result

    first_planned = round_starts[0]
    base_alt = _default_base(running_starts, first_planned, alt_spielzeit, alt_wechselzeit, now_seconds)
    base_neu = _default_base(running_starts, first_planned, neu_spielzeit, neu_wechselzeit, now_seconds)
    if naechste_runde and _seconds(naechste_runde) is not None:
        base_neu = _seconds(naechste_runde)
    _, ende_ohne = _projection(rounds, base_alt, alt_spielzeit, alt_wechselzeit)
    starts_neu, ende_neu = _projection(rounds, base_neu, neu_spielzeit, neu_wechselzeit)
    for item, start in zip(rounds, starts_neu):
        item["neu"] = _clock(start)
    result.update({
        "naechste_runde": _clock(base_neu),
        "ende_plan": _clock(round_starts[-1] + alt_spielzeit),
        "ende_ohne": _clock(ende_ohne),
        "ende_neu": _clock(ende_neu),
    })

    # Ziel: naechster Programmpunkt dieser Jahrgaenge nach Blockbeginn (z.B.
    # die Siegerehrung), sonst das geplante Blockende.
    ends = [_seconds(c["end_time"]) for c in competitions if _seconds(c["end_time"]) is not None]
    block_end = max(ends) if ends else None
    jahrgaenge = {str(c.get("jahrgang") or "") for c in competitions}
    candidates = []
    if competitions[0].get("event_id") is not None:
        for punkt in list_programmpunkte(conn, competitions[0]["event_id"]):
            start = _seconds(punkt["start_time"])
            if (
                start is not None and start > first_planned
                and (block_end is None or start <= block_end + 30 * 60)
                and any(applies_to_jahrgang(punkt, jahrgang) for jahrgang in jahrgaenge)
            ):
                candidates.append((start, punkt["titel"]))
    if candidates:
        start, label = min(candidates)
        result["ziel"] = {"label": label, "time": _clock(start), "seconds": start}
    elif block_end is not None:
        result["ziel"] = {"label": "geplantes Ende", "time": _clock(block_end), "seconds": block_end}
    if result["ziel"]:
        target = result["ziel"]["seconds"]
        result["puffer_ohne"] = (target - ende_ohne) // 60
        result["puffer_neu"] = (target - ende_neu) // 60
    return result


def uebernehmen_aufholen(conn, vorschlag):
    """Speichert die neuen Zeiten der noch geplanten Spiele und die neue
    Spiel-/Wechselzeit fuer alle Wettbewerbe des Blocks. Gibt die Anzahl
    verschobener Spiele zurueck."""
    moved = 0
    for item in vorschlag["rounds"]:
        if "neu" not in item:
            continue
        for slot_id in item["slot_ids"]:
            moved += conn.execute(
                "UPDATE slots SET startzeit = ? WHERE id = ? AND status = 'geplant'",
                (item["neu"], slot_id),
            ).rowcount
    spielzeit = vorschlag["neu_spielzeit"]
    for competition in vorschlag["competitions"]:
        conn.execute(
            """
            UPDATE competitions
            SET game_duration_seconds = ?, game_duration_minutes = ?, changeover_duration_minutes = ?
            WHERE id = ?
            """,
            (
                spielzeit if spielzeit % 60 else None,
                max(1, -(-spielzeit // 60)),
                vorschlag["neu_wechselzeit"],
                competition["id"],
            ),
        )
    return moved


def durchsage_text(vorschlag):
    """Vorausgefuellter Text fuer die Durchsage an alle."""
    sportart = vorschlag["competitions"][0].get("sportart") or "Spiele"
    teile = []
    if vorschlag["neu_spielzeit"] != vorschlag["alt_spielzeit"]:
        teile.append(
            f"Spielzeit ab sofort {vorschlag['neu_spielzeit_label']} statt "
            f"{vorschlag['alt_spielzeit_label']} Minuten"
        )
    if vorschlag["neu_wechselzeit"] != vorschlag["alt_wechselzeit"]:
        teile.append(f"Wechselzeit {vorschlag['neu_wechselzeit']} Minuten")
    text = f"{sportart}: " + (", ".join(teile) if teile else "Zeitplan angepasst")
    if vorschlag.get("naechste_runde"):
        text += f". Nächste Runde um {vorschlag['naechste_runde']} Uhr."
    return text


def is_event_today(conn, competition, today):
    """Ob der Block heute stattfindet (nur dann zaehlt die aktuelle Uhrzeit)."""
    date_value = competition.get("competition_date")
    if not date_value and competition.get("event_id") is not None:
        row = conn.execute(
            "SELECT event_date FROM events WHERE id = ?", (competition["event_id"],)
        ).fetchone()
        date_value = row["event_date"] if row else None
    if not date_value:
        return False
    try:
        return datetime.strptime(str(date_value)[:10], "%Y-%m-%d").date() == today
    except ValueError:
        return False
