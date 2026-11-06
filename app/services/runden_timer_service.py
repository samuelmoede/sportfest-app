"""Runden-Timer fuer die Ergebniseingabe (z.B. Tischtennis in der Mensa).

Alle zeitgleich geplanten Spiele einer Uhrzeit werden mit einem Klick
gemeinsam gestartet (gleicher started_at) und haben damit einen gemeinsamen
Timer. Der Timer laesst sich anhalten (timer_stopped_at); die Spiele selbst
werden weiter einzeln gespeichert und beendet. Die Ergebnisseite laeuft dabei
am Beamer, damit alle sehen, wer als Naechstes dran ist und welche
Punktzahl eingetragen wurde."""


def _game(slot):
    return (
        slot.get("slot_typ") == "Spiel"
        and slot.get("team_a_id") is not None
        and slot.get("team_b_id") is not None
    )


def build_round_timer(slots):
    """Liefert {"running": ..., "next": ...} fuer die angezeigten Spiele.

    running: die zuletzt gemeinsam gestartete Runde, die noch laeuft
    (started_at, planned_seconds, stopped_at, slot_ids, count, startzeit).
    next: die frueheste Uhrzeit mit noch geplanten Spielen (startzeit,
    slot_ids, count). Beides None, wenn es nichts gibt."""
    running_by_start = {}
    for slot in slots:
        if _game(slot) and slot.get("status") == "läuft" and slot.get("started_at"):
            running_by_start.setdefault(slot["started_at"], []).append(slot)

    running = None
    if running_by_start:
        started_at = max(running_by_start)
        group = running_by_start[started_at]
        stopped = [slot.get("timer_stopped_at") for slot in group if slot.get("timer_stopped_at")]
        running = {
            "started_at": started_at,
            "startzeit": min(slot.get("startzeit") or "" for slot in group),
            "planned_seconds": max(
                (slot.get("planned_duration_seconds") or 0) for slot in group
            ) or None,
            "stopped_at": min(stopped) if len(stopped) == len(group) else None,
            "slot_ids": [slot["id"] for slot in group],
            "count": len(group),
            "competition_id": group[0].get("competition_id"),
        }

    planned = [slot for slot in slots if _game(slot) and slot.get("status") == "geplant"]
    next_round = None
    if planned:
        startzeit = min(slot.get("startzeit") or "" for slot in planned)
        group = [slot for slot in planned if (slot.get("startzeit") or "") == startzeit]
        next_round = {
            "startzeit": startzeit,
            "slot_ids": [slot["id"] for slot in group],
            "count": len(group),
            "competition_id": group[0].get("competition_id"),
        }

    return {"running": running, "next": next_round}


def start_round(conn, slot_ids, started_at):
    """Startet alle uebergebenen, noch geplanten Spiele mit demselben
    Zeitstempel. Gibt die Anzahl gestarteter Spiele zurueck."""
    started = 0
    for slot_id in slot_ids:
        cursor = conn.execute(
            """
            UPDATE slots
            SET status = 'läuft', started_at = ?, finished_at = NULL, timer_stopped_at = NULL
            WHERE id = ? AND status = 'geplant' AND slot_typ = 'Spiel'
            """,
            (started_at, slot_id),
        )
        started += cursor.rowcount
    return started


def stop_round_timer(conn, slot_ids, stopped_at):
    """Haelt den Timer der laufenden Spiele an; Status und Ergebnis bleiben."""
    stopped = 0
    for slot_id in slot_ids:
        cursor = conn.execute(
            """
            UPDATE slots SET timer_stopped_at = ?
            WHERE id = ? AND status = 'läuft' AND timer_stopped_at IS NULL
            """,
            (stopped_at, slot_id),
        )
        stopped += cursor.rowcount
    return stopped
