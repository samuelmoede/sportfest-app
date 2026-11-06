"""Gesamtplan Schulpokal: alle Bloecke einer Schulpokal-Veranstaltung auf
einmal planen, danach nur noch pruefen.

Ein Block ist eine Sportart an einem Ort (Zweifelderball in der Turnhalle,
Volleyball in der Turnhalle, Tischtennis in der Mensa). Je Block waehlt man
die Anzahl Felder/Platten, ob sich die Jahrgaenge abwechseln oder
gleichzeitig spielen, Startzeit, Spielzeit und Wechselzeit. Wettbewerbe
desselben Jahrgangs (Tischtennis Jungen und Maedchen) spielen immer
zeitgleich, siehe plan_schulpokal_block.

Uebernommen wird nur, wenn kein Block Fehler hat und keine harten Konflikte
bestehen. Vorhandene, noch ungespielte Gruppenspiele der Block-Wettbewerbe
werden dabei ersetzt; sind schon Spiele gestartet oder beendet, bleibt der
Block unangetastet und es gibt eine Meldung."""
from app.services.jahrgangsfinale_service import parse_final_source_ids
from app.services.programmpunkte_service import applies_to_jahrgang, list_programmpunkte
from app.services.schedule_conflict_service import (
    find_existing_schedule_conflicts,
    find_internal_conflicts,
)
from app.services.schedule_generator_service import (
    DEFAULT_SCHULPOKAL_MODE,
    _minutes_to_clock,
    plan_schulpokal_block,
)
from app.services.schedule_location_service import (
    filter_courts_for_location,
    get_effective_competition_location,
    schedule_planning_available,
)
from app.services.schedule_time_service import get_competition_timing, parse_slot_time

ABLAEUFE = {
    "abwechselnd": "Jahrgänge abwechselnd (je Zeitschritt ein Jahrgang)",
    "gleichzeitig": "Jahrgänge gleichzeitig auf getrennten Feldern",
}


def _minutes(value):
    parsed = parse_slot_time(value)
    if parsed is None:
        return None
    return parsed.hour * 60 + parsed.minute


def _teams_for(conn, competition):
    explicit = conn.execute(
        """
        SELECT t.* FROM teams t
        JOIN competition_teams ct ON ct.team_id = t.id
        WHERE ct.competition_id = ?
        ORDER BY t.name
        """,
        (competition["id"],),
    ).fetchall()
    if explicit:
        return list(explicit)
    return conn.execute(
        "SELECT * FROM teams WHERE active = 1 AND jahrgang = ? ORDER BY name",
        (competition["jahrgang"],),
    ).fetchall()


def load_bloecke(conn, event_id):
    """Liefert die Bloecke der Veranstaltung mit Standardwerten fuer das
    Formular. key ist die kleinste Wettbewerbs-id des Blocks (stabil
    zwischen Vorschau und Uebernehmen)."""
    competitions = conn.execute(
        """
        SELECT * FROM competitions
        WHERE event_id = ? AND competition_type = 'Schulpokal'
          AND (final_source_ids IS NULL OR final_source_ids = '')
          AND COALESCE(status, '') != 'archiviert'
        ORDER BY start_time, sportart, jahrgang, teilmannschaft, name
        """,
        (event_id,),
    ).fetchall()
    courts = conn.execute("SELECT * FROM courts WHERE active = 1").fetchall()
    programmpunkte = list_programmpunkte(conn, event_id)

    bloecke = {}
    for competition in competitions:
        if not schedule_planning_available(competition):
            continue
        location = get_effective_competition_location(competition)
        group_key = (str(competition["sportart"] or "").strip().casefold(), location)
        bloecke.setdefault(group_key, {
            "sportart": competition["sportart"],
            "location": location,
            "competitions": [],
        })["competitions"].append(competition)

    result = []
    for block in bloecke.values():
        block_competitions = block["competitions"]
        first = block_competitions[0]
        timing = get_competition_timing(first)
        block_courts = filter_courts_for_location(courts, block["location"])
        entries = []
        for competition in block_competitions:
            teams = _teams_for(conn, competition)
            entries.append({
                "competition_id": competition["id"],
                "competition_name": competition["name"],
                "jahrgang": competition["jahrgang"],
                "teilmannschaft": competition["teilmannschaft"] or "",
                "team_names": [team["name"] for team in teams],
                "team_ids": {team["name"]: team["id"] for team in teams},
            })
        starts = [c["start_time"] for c in block_competitions if _minutes(c["start_time"]) is not None]
        ends = [c["end_time"] for c in block_competitions if _minutes(c["end_time"]) is not None]
        games_per_round = sum(len(entry["team_names"]) // 2 for entry in entries)
        competition_ids = [c["id"] for c in block_competitions]
        placeholders = ", ".join("?" for _ in competition_ids)
        counts = conn.execute(
            f"""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status != 'geplant' OR score_a IS NOT NULL OR score_b IS NOT NULL
                       THEN 1 ELSE 0 END) AS gespielt
            FROM slots
            WHERE competition_id IN ({placeholders})
              AND slot_typ = 'Spiel' AND phase = 'Gruppenphase'
            """,
            competition_ids,
        ).fetchone()
        start_time = min(starts, key=_minutes) if starts else "08:00"
        # Beginnt der Block mit einem Programmpunkt (z.B. Eroeffnung 08:00
        # bis 08:10), starten die Spiele erst danach.
        opening = [
            punkt for punkt in programmpunkte
            if punkt["start_time"] == _minutes_to_clock(_minutes(start_time))
            and any(applies_to_jahrgang(punkt, c["jahrgang"] or "") for c in block_competitions)
        ]
        games_start = max((punkt["end_time"] for punkt in opening), key=_minutes, default=None)
        result.append({
            "key": min(competition_ids),
            "sportart": block["sportart"],
            "location": block["location"],
            "competitions": [dict(c) for c in block_competitions],
            "entries": entries,
            "courts": [dict(court) for court in block_courts],
            "start_time": start_time,
            "games_start": games_start or start_time,
            "start_nach": ", ".join(punkt["titel"] for punkt in opening),
            "end_time": max(ends, key=_minutes) if ends else "",
            "game_duration_minutes": timing["game_duration_minutes"],
            "changeover_duration_minutes": timing["changeover_duration_minutes"],
            "tournament_mode": first["tournament_mode"] or DEFAULT_SCHULPOKAL_MODE,
            # Reichen die Felder fuer alle Spiele einer Runde aller Jahrgaenge
            # (z.B. 12 Platten fuer 4 x 3 Tischtennis-Spiele), spielen alle
            # gleichzeitig, sonst wechseln sich die Jahrgaenge ab.
            "default_ablauf": (
                "gleichzeitig"
                if block_courts and games_per_round <= len(block_courts) else "abwechselnd"
            ),
            "existing_games": counts["total"] or 0,
            "played_games": counts["gespielt"] or 0,
        })
    result.sort(key=lambda block: (_minutes(block["start_time"]) or 0, block["key"]))
    return result


def read_settings(form, bloecke):
    """Liest die Formularwerte je Block (Feldnamen mit Block-key als Suffix)."""
    settings = {}
    for block in bloecke:
        key = block["key"]

        def value(name, default):
            raw = form.get(f"{name}_{key}")
            return default if raw is None or str(raw).strip() == "" else str(raw).strip()

        def number(name, default, minimum):
            try:
                parsed = int(value(name, default))
            except (TypeError, ValueError):
                return default
            return parsed if parsed >= minimum else default

        settings[key] = {
            "planen": form.get(f"planen_{key}") is not None if form.get("gesendet") else True,
            "anzahl_felder": number("anzahl_felder", len(block["courts"]), 1),
            "ablauf": value("ablauf", block["default_ablauf"]),
            "startzeit": value("startzeit", block.get("games_start") or block["start_time"]),
            "spielzeit": number("spielzeit", block["game_duration_minutes"], 1),
            "wechselzeit": number("wechselzeit", block["changeover_duration_minutes"], 0),
        }
        if settings[key]["ablauf"] not in ABLAEUFE:
            settings[key]["ablauf"] = block["default_ablauf"]
    return settings


def build_gesamtplan(conn, event_id, settings=None):
    """Erzeugt den Vorschlag fuer alle angehakten Bloecke. Gibt ein dict mit
    bloecke (je Block: Einstellungen, Spiele, Fehler, Endzeit, Vorschlag),
    slots (alle Spiele), warnings (Konfliktpruefung) und ok zurueck."""
    bloecke = load_bloecke(conn, event_id)
    if settings is None:
        settings = read_settings({}, bloecke)
    all_slots = []
    for block in bloecke:
        block_settings = settings.get(block["key"]) or read_settings({}, [block])[block["key"]]
        block["settings"] = block_settings
        block["slots"] = []
        block["fehler"] = []
        block["hinweise"] = []
        if not block_settings["planen"]:
            continue
        if block["played_games"]:
            block["hinweise"].append(
                f"Hier wurden schon {block['played_games']} Spiele gestartet oder gespielt. "
                "Der Block wird nicht neu geplant."
            )
            continue
        courts = block["courts"][:block_settings["anzahl_felder"]]
        if not courts:
            block["fehler"].append(f"Für {block['location']} sind keine aktiven Felder angelegt.")
            continue
        start = _minutes(block_settings["startzeit"])
        if start is None:
            block["fehler"].append("Bitte eine gültige Startzeit angeben.")
            continue
        for entry in block["entries"]:
            if len(entry["team_names"]) < 2:
                block["hinweise"].append(
                    f'"{entry["competition_name"]}" hat weniger als zwei Klassen und wird nicht geplant.'
                )
        interval = block_settings["spielzeit"] + block_settings["wechselzeit"]
        try:
            slots, steps = plan_schulpokal_block(
                [dict(entry) for entry in block["entries"]],
                [court["id"] for court in courts],
                start,
                interval,
                gleichzeitig=block_settings["ablauf"] == "gleichzeitig",
                tournament_mode=block["tournament_mode"],
            )
        except ValueError as error:
            block["fehler"].append(str(error))
            continue
        court_names = {court["id"]: court["name"] for court in courts}
        for slot in slots:
            slot["court_name"] = court_names.get(slot["court_id"], "")
            slot["game_duration_minutes"] = block_settings["spielzeit"]
        block["slots"] = slots
        block["used_courts"] = courts
        block["steps"] = steps
        block["times"] = sorted({slot["startzeit"] for slot in slots}, key=_minutes)
        if steps:
            end = start + steps * interval - block_settings["wechselzeit"]
            block["planned_end"] = _minutes_to_clock(end)
            planned_end = _minutes(block["end_time"])
            if planned_end is not None and planned_end > start:
                available = planned_end - start
                suggestion = (available - (steps - 1) * block_settings["wechselzeit"]) // steps
                block["spielzeit_vorschlag"] = suggestion if suggestion >= 1 else None
                if end > planned_end:
                    block["hinweise"].append(
                        f"Das letzte Spiel endet um {block['planned_end']}, geplant ist "
                        f"{block['end_time']}."
                    )
        all_slots.extend(slots)

    warnings = []
    if all_slots:
        internal = find_internal_conflicts(conn, all_slots)
        existing = find_existing_schedule_conflicts(conn, all_slots)
        # Hinweise der internen Pruefung sind nur "Klasse spielt zeitgleich in
        # zwei Sportarten" - zusammengefasst statt einer Zeile je Spiel.
        sport_hints = [w for w in internal if w["level"] == "warning"]
        warnings.extend(w for w in internal if w["level"] != "warning")
        if sport_hints:
            warnings.append({
                "level": "warning",
                "message": (
                    "Klassen spielen zeitgleich in zwei Sportarten (z. B. Volleyball und "
                    "Tischtennis). Das ist erlaubt, nur ein Hinweis: bitte prüfen, ob jede "
                    "Klasse genug Spieler für beide hat. "
                    + sport_hints[0]["message"]
                ),
            })
        # Eigene vorhandene Spiele werden beim Uebernehmen ersetzt, nicht ergaenzt.
        warnings.extend(
            w for w in existing
            if not w["message"].startswith("Für diesen Wettbewerb sind bereits")
        )

    ok = bool(all_slots) and not any(block["fehler"] for block in bloecke if block["settings"]["planen"]) \
        and not any(w["level"] == "error" for w in warnings)
    return {"bloecke": bloecke, "slots": all_slots, "warnings": warnings, "ok": ok}


def apply_gesamtplan(conn, event_id, settings):
    """Speichert den Gesamtplan. Gibt (anzahl_spiele, fehlermeldungen) zurueck;
    bei Fehlern wird nichts geaendert."""
    plan = build_gesamtplan(conn, event_id, settings)
    if not plan["ok"]:
        messages = [
            f'{block["sportart"]}: {fehler}'
            for block in plan["bloecke"] if block["settings"]["planen"]
            for fehler in block["fehler"]
        ]
        messages += [w["message"] for w in plan["warnings"] if w["level"] == "error"]
        return 0, messages or ["Es gibt nichts zu planen."]

    finals = conn.execute(
        """
        SELECT * FROM competitions
        WHERE event_id = ? AND final_source_ids IS NOT NULL AND final_source_ids != ''
        """,
        (event_id,),
    ).fetchall()

    for block in plan["bloecke"]:
        if not block["settings"]["planen"] or not block["slots"]:
            continue
        block_settings = block["settings"]
        competition_ids = [c["id"] for c in block["competitions"]]
        placeholders = ", ".join("?" for _ in competition_ids)
        conn.execute(
            f"""
            DELETE FROM slots
            WHERE competition_id IN ({placeholders})
              AND slot_typ = 'Spiel' AND phase = 'Gruppenphase'
            """,
            competition_ids,
        )
        for competition_id in competition_ids:
            conn.execute(
                """
                UPDATE competitions
                SET game_duration_minutes = ?, changeover_duration_minutes = ?, start_time = ?
                WHERE id = ?
                """,
                (
                    block_settings["spielzeit"], block_settings["wechselzeit"],
                    block_settings["startzeit"], competition_id,
                ),
            )
        for slot in block["slots"]:
            conn.execute(
                """
                INSERT INTO slots (
                    competition_id, court_id, startzeit, slot_typ, phase, gruppe,
                    team_a_id, team_b_id, status, note
                ) VALUES (?, ?, ?, 'Spiel', 'Gruppenphase', ?, ?, ?, 'geplant', '')
                """,
                (
                    slot["competition_id"], slot["court_id"], slot["startzeit"],
                    slot["gruppe"] or None, slot["team_a_id"], slot["team_b_id"],
                ),
            )
        # Jahrgangsfinale ohne Feld bekommt das erste Feld des Blocks.
        for final in finals:
            if set(parse_final_source_ids(final["final_source_ids"])) & set(competition_ids):
                conn.execute(
                    """
                    UPDATE slots SET court_id = ?
                    WHERE competition_id = ? AND court_id IS NULL AND slot_typ = 'Spiel'
                    """,
                    (block["used_courts"][0]["id"], final["id"]),
                )
    return len(plan["slots"]), []
