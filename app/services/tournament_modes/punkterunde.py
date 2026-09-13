"""Turniermodus "Punkterunde": reine Jeder-gegen-Jeden-Runde ohne KO-Phase
(Issue #105) - fuer den Wettbewerbstyp "Turnier", als eigenstaendiges Modul in
der TURNIER_MODES-Registry analog zu ko_runde.py. Die Endplatzierung ergibt
sich ausschliesslich aus der Tabelle (calculate_table()/sort_table_rows() in
app/main.py, unveraendert) - es gibt bewusst keine Halbfinale-/Finale-
Platzhalter.

Die Paarungs- und Rundenlogik wird bewusst nicht dupliziert, sondern 1:1 aus
app/services/schedule_generator_service.py wiederverwendet
(_jeder_gegen_jeden_pairings, _schedule_pairings_into_rounds, _minutes_to_clock):
das ist exakt dasselbe faire Rundenverfahren, das dort bereits fuer den
separaten Wettbewerbstyp "Schulpokal" (generate_schulpokal_plan) genutzt wird,
nur hier fuer einen einzelnen Turnier-Wettbewerb statt mehrerer abwechselnd
verplanter Wettbewerbe."""
from datetime import datetime

from app.database import get_conn
from app.services.schedule_generator_service import (
    _jeder_gegen_jeden_pairings,
    _minutes_to_clock,
    _schedule_pairings_into_rounds,
    validate_generated_plan,
)
from app.services.schedule_location_service import (
    filter_court_ids_for_competition,
    schedule_planning_available,
)
from app.services.schedule_time_service import get_competition_timing, get_game_end_time

MODE_KEY = "punkterunde"
LABEL = "Punkterunde (Jeder gegen Jeden)"


def generate_punkterunde_plan(competition_id: int, court_ids, startzeit: str):
    """Erzeugt die Vorschau-Slots fuer eine komplette Jeder-gegen-Jeden-Runde
    eines Turnier-Wettbewerbs (beliebige Teamzahl, Freilos-Rotation bei
    ungerader Teamzahl via _jeder_gegen_jeden_pairings), analog zum Aufbau von
    generate_ko_plan. Alle Spiele bekommen die Phase "Gruppenphase" (wie beim
    Modus "Gruppenphase mit KO-Runde" und bei generate_schulpokal_plan), damit
    calculate_table() sie ohne Anpassung mitzaehlt."""
    with get_conn() as conn:
        competition = conn.execute(
            "SELECT * FROM competitions WHERE id = ?", (competition_id,)
        ).fetchone()
        if competition is None or not schedule_planning_available(competition):
            return []

        all_courts = conn.execute(
            "SELECT * FROM courts WHERE active = 1 ORDER BY name"
        ).fetchall()
        court_ids = filter_court_ids_for_competition(court_ids, all_courts, competition)
        if not court_ids:
            return []

        explicit_teams = conn.execute("""
            SELECT t.* FROM teams t
            JOIN competition_teams ct ON ct.team_id = t.id
            WHERE ct.competition_id = ?
            ORDER BY t.jahrgang, t.name
        """, (competition_id,)).fetchall()
        if explicit_teams:
            teams = list(explicit_teams)
        else:
            teams = conn.execute("""
                SELECT *
                FROM teams
                WHERE active = 1
                  AND jahrgang = ?
                ORDER BY name
            """, (competition["jahrgang"],)).fetchall()

    selected_ids = set(court_ids)
    court_order = {court_id: index for index, court_id in enumerate(court_ids)}
    courts = [court for court in all_courts if court["id"] in selected_ids]
    courts.sort(key=lambda court: court_order.get(court["id"], 999))

    team_names = [team["name"] for team in teams]
    team_ids = {team["name"]: team["id"] for team in teams}
    court_map = {court["id"]: court["name"] for court in courts}
    timing = get_competition_timing(competition)
    slot_interval_minutes = timing["slot_interval_minutes"]

    if len(team_names) < 2 or not court_ids:
        return []

    pairings = _jeder_gegen_jeden_pairings(team_names)
    rounds = _schedule_pairings_into_rounds(pairings, team_names, court_ids)

    proposed_slots = []
    start_dt = datetime.strptime(startzeit, "%H:%M")
    current_minutes = start_dt.hour * 60 + start_dt.minute

    for round_assignments in rounds:
        time_value = _minutes_to_clock(current_minutes)
        for court_id, team_a, team_b, gruppe in round_assignments:
            proposed_slots.append({
                "competition_id": competition_id,
                "competition_name": competition["name"],
                "startzeit": time_value,
                "slot_typ": "Spiel",
                "court_id": court_id,
                "court_name": court_map.get(court_id, ""),
                "phase": "Gruppenphase",
                "gruppe": gruppe,
                "team_a_id": team_ids[team_a],
                "team_b_id": team_ids[team_b],
                "team_a": team_a,
                "team_b": team_b,
                "note": "",
            })
        current_minutes += slot_interval_minutes

    for slot in proposed_slots:
        slot["game_end_time"] = get_game_end_time(
            slot["startzeit"], timing["game_duration_minutes"]
        )
    return proposed_slots


def validate_punkterunde_plan(proposed_slots, expected_teams):
    """Duenner Wrapper um validate_generated_plan: bei echtem Jeder-gegen-
    Jeden bekommt jedes Team Teamzahl - 1 Spiele, analog zu
    validate_schulpokal_plan fuer den Modus "jeder_gegen_jeden" bei
    Schulpokal-Wettbewerben."""
    games_per_team = max(len(expected_teams) - 1, 0)
    return validate_generated_plan(proposed_slots, expected_teams, games_per_team)
