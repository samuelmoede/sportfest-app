"""Turniermodus "Reine KO-Runde": direktes Einzelausscheiden ohne
Gruppenphase (siehe Issue #101, Teilschritt 2). Eigenstaendiges Modul/
Strategie, damit dieser Modus - und kuenftige weitere - ergaenzt werden
koennen, ohne generate_group_plan (Modus "Gruppenphase mit KO-Runde" in
app/services/schedule_generator_service.py) anzufassen.

Anders als bei der Gruppenphase gibt es hier keinen hartkodierten Sonderfall
fuer bestimmte Teamzahlen: build_ko_rounds() baut den vollstaendigen KO-Baum
fuer eine beliebige Teamzahl (>= 2), inklusive fairer Freilos-Vergabe per
Standard-Turniersetzung bei nicht als Zweierpotenz vorliegender Teamzahl.

Die automatische Weiterbesetzung nachfolgender Runden aus den Ergebnissen
der vorigen (Issue #101, Teilschritt 3) uebernimmt plan_ko_advancement() /
advance_ko_rounds(): der Baum wird dazu aus den gespeicherten Slots
rekonstruiert, ohne zusaetzliche Spalten in der Datenbank.
"""
from datetime import datetime

from app.database import get_conn
from app.services.schedule_generator_service import get_winner_loser
from app.services.schedule_location_service import (
    filter_court_ids_for_competition,
    schedule_planning_available,
)
from app.services.schedule_time_service import get_competition_timing, get_game_end_time

MODE_KEY = "ko_runde"
LABEL = "Reine KO-Runde"

# Deckt die per test_schedule_generator_robustness.py dauerhaft abgesicherte
# Spanne von 3-32 Teams vollstaendig ab (Bracket-Groesse bei 17-32 Teams ist
# 32). Groessere Baeume fallen auf einen generischen Rundennamen zurueck.
_ROUND_LABELS_BY_BRACKET_SIZE = {
    2: "Finale",
    4: "Halbfinale",
    8: "Viertelfinale",
    16: "Achtelfinale",
    32: "Sechzehntelfinale",
}


def _round_label(teams_in_round: int) -> str:
    return _ROUND_LABELS_BY_BRACKET_SIZE.get(
        teams_in_round, f"Runde der letzten {teams_in_round}"
    )


def _minutes_to_clock(total_minutes: int) -> str:
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours:02d}:{minutes:02d}"


def _seed_positions(bracket_size: int):
    """Standard-Turniersetzung: liefert die Reihenfolge der Setzplaetze
    (1-indiziert) an den Blattpositionen eines vollstaendigen KO-Baums (z.B.
    [1, 4, 2, 3] fuer 4 Plaetze), damit Setzplatz 1 und 2 erst im Finale
    aufeinandertreffen koennten. Grundlage fuer eine gleichmaessige
    Freilos-Verteilung in build_ko_rounds statt einer Haeufung in einer
    Baumhaelfte."""
    order = [1]
    size = 1
    while size < bracket_size:
        size *= 2
        order = [slot for seed in order for slot in (seed, size + 1 - seed)]
    return order


def build_ko_rounds(team_names):
    """Baut den vollstaendigen, lueckenlosen KO-Baum fuer eine beliebige
    Teamzahl (>= 2). Gibt eine Liste von Runden zurueck (fruehste Runde
    zuerst); jede Runde ist eine Liste von Matches (dict mit "phase",
    "position" [Index innerhalb der Runde], "team_a"/"team_b" [Teamname oder
    None, wenn noch unbekannt] und "source_a"/"source_b" [das Match der
    Vorrunde, dessen noch unbekannter Sieger auf dieser Seite antritt, oder
    None wenn das Team bereits feststeht]).

    Ist die Teamzahl keine Zweierpotenz, wird der Baum auf die naechst-
    groessere Zweierpotenz aufgefuellt; die ueberzaehligen Plaetze sind
    Freilose. Freilose werden per Standard-Turniersetzung (_seed_positions)
    vergeben: da bei der jeweils kleinsten passenden Zweierpotenz immer
    weniger Freilose als Erstrunden-Paarungen existieren, bekommt nie ein
    Team ein zweites Freilos und nie muessten zwei Freilose "gegeneinander"
    antreten - jedes Freilos-Team steigt genau einmal, ausschliesslich in
    der ersten Runde, automatisch ohne Spiel auf."""
    n = len(team_names)
    if n < 2:
        return []

    bracket_size = 1
    while bracket_size < n:
        bracket_size *= 2

    order = _seed_positions(bracket_size)
    current = [
        ("known", team_names[seed - 1]) if seed <= n else ("bye", None)
        for seed in order
    ]

    rounds = []
    while len(current) > 1:
        label = _round_label(len(current))
        round_matches = []
        next_current = []

        for i in range(0, len(current), 2):
            state_a, value_a = current[i]
            state_b, value_b = current[i + 1]

            if state_a == "bye" or state_b == "bye":
                # Nur in der ersten Runde moeglich (siehe Docstring): die
                # nicht-freie Seite steigt automatisch auf, ohne Spiel.
                next_current.append(current[i + 1] if state_a == "bye" else current[i])
                continue

            match = {
                "phase": label,
                "position": len(round_matches),
                "team_a": value_a if state_a == "known" else None,
                "team_b": value_b if state_b == "known" else None,
                "source_a": value_a if state_a == "pending" else None,
                "source_b": value_b if state_b == "pending" else None,
            }
            round_matches.append(match)
            next_current.append(("pending", match))

        rounds.append(round_matches)
        current = next_current

    return rounds


def _describe_winner(team, source):
    if team is not None:
        return team
    return f'Sieger {source["phase"]} Spiel {source["position"] + 1}'


def generate_ko_plan(competition_id: int, court_ids, startzeit: str):
    """Erzeugt die Vorschau-Slots fuer den kompletten KO-Baum eines
    Turnier-Wettbewerbs, analog zu generate_group_plan: reale Teams, wo schon
    feststehend (inkl. per Freilos direkt aufgestiegener Teams), "?"-
    Platzhalter mit erklaerender Notiz sonst. Alle Runden werden sofort als
    Slots angelegt (auch weit in der Zukunft liegende Platzhalter-Runden) -
    identisch zum bestehenden Vorgehen bei Halbfinale/Finale in
    generate_group_plan."""
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

    rounds = build_ko_rounds(team_names)
    if not rounds:
        return []

    proposed_slots = []
    start_dt = datetime.strptime(startzeit, "%H:%M")
    current_minutes = start_dt.hour * 60 + start_dt.minute
    final_round_time = None

    for round_index, round_matches in enumerate(rounds):
        is_final_round = round_index == len(rounds) - 1
        # Mehr Spiele in einer Runde als Felder verfuegbar: in mehreren
        # Zeit-Bloecken hintereinander verplanen, statt mehrere Spiele
        # gleichzeitig auf dasselbe Feld zu legen (Feld-Doppelbelegung).
        for chunk_start in range(0, len(round_matches), len(court_ids)):
            chunk = round_matches[chunk_start:chunk_start + len(court_ids)]
            time_value = _minutes_to_clock(current_minutes)
            if is_final_round:
                final_round_time = time_value

            for offset, match in enumerate(chunk):
                court_id = court_ids[offset]
                both_known = match["team_a"] is not None and match["team_b"] is not None
                note = "" if both_known else (
                    f'{match["phase"]}: '
                    f'{_describe_winner(match["team_a"], match["source_a"])} gegen '
                    f'{_describe_winner(match["team_b"], match["source_b"])}'
                )
                proposed_slots.append({
                    "competition_id": competition_id,
                    "competition_name": competition["name"],
                    "startzeit": time_value,
                    "slot_typ": "Spiel",
                    "court_id": court_id,
                    "court_name": court_map.get(court_id, ""),
                    "phase": match["phase"],
                    "gruppe": "",
                    "team_a_id": team_ids[match["team_a"]] if match["team_a"] is not None else "",
                    "team_b_id": team_ids[match["team_b"]] if match["team_b"] is not None else "",
                    "team_a": match["team_a"] if match["team_a"] is not None else "?",
                    "team_b": match["team_b"] if match["team_b"] is not None else "?",
                    "note": note,
                })

            current_minutes += slot_interval_minutes

    # Spiel um Platz 3 nur, wenn beide Finalisten aus einem echten,
    # vorletzten KO-Spiel hervorgehen - steigt ein Team per Freilos direkt
    # ins Finale auf, gibt es auf dieser Seite keinen "Verlierer" fuer ein
    # Platz-3-Spiel (siehe build_ko_rounds-Docstring). Bei nur einem Feld
    # wird es - wie beim Modus "Gruppenphase mit KO-Runde" - gar nicht erst
    # vorgeschlagen, um keine Feld-Doppelbelegung zur selben Zeit wie das
    # Finale zu erzeugen.
    final_match = rounds[-1][0]
    if (
        final_match["source_a"] is not None
        and final_match["source_b"] is not None
        and len(court_ids) > 1
    ):
        small_final_court_id = court_ids[1]
        proposed_slots.append({
            "competition_id": competition_id,
            "competition_name": competition["name"],
            "startzeit": final_round_time,
            "slot_typ": "Spiel",
            "court_id": small_final_court_id,
            "court_name": court_map.get(small_final_court_id, ""),
            "phase": "Spiel um Platz 3",
            "gruppe": "",
            "team_a_id": "",
            "team_b_id": "",
            "team_a": "?",
            "team_b": "?",
            "note": (
                f'Spiel um Platz 3: Verlierer {final_match["source_a"]["phase"]} Spiel '
                f'{final_match["source_a"]["position"] + 1} gegen Verlierer '
                f'{final_match["source_b"]["phase"]} Spiel {final_match["source_b"]["position"] + 1}'
            ),
        })

    for slot in proposed_slots:
        slot["game_end_time"] = get_game_end_time(
            slot["startzeit"], timing["game_duration_minutes"]
        )
    return proposed_slots


def validate_ko_plan(proposed_slots, expected_teams):
    """Schlankere Variante von validate_generated_plan fuer den KO-Baum: die
    dortigen Gruppenphase-/Zwei-Gruppen-Pruefungen (Spielanzahl pro Team,
    Halbfinale braucht zwei Gruppen) passen nicht auf einen Baum, in dem jedes
    Team unterschiedlich viele Runden weit kommt und "Halbfinale" auch ganz
    ohne Gruppen vorkommt. Geprueft werden stattdessen: keine Team-
    Doppelbelegung zur selben Zeit und dass jedes erwartete Team irgendwo im
    Baum auftaucht."""
    warnings = []
    game_slots = [slot for slot in proposed_slots if slot["slot_typ"] == "Spiel"]

    games_by_time_and_team = {}
    scheduled_team_ids = set()
    team_names = {team["id"]: team["name"] for team in expected_teams}

    for slot in game_slots:
        for team_id_key, team_name_key in (("team_a_id", "team_a"), ("team_b_id", "team_b")):
            team_id = slot[team_id_key]
            if team_id in (None, ""):
                continue

            team_names.setdefault(team_id, slot[team_name_key])
            scheduled_team_ids.add(team_id)
            key = (slot["startzeit"], team_id)
            games_by_time_and_team.setdefault(key, []).append(slot)

    for (startzeit, team_id), slots in games_by_time_and_team.items():
        if len(slots) > 1:
            warnings.append({
                "level": "error",
                "message": f'{team_names[team_id]} ist um {startzeit} gleichzeitig für mehrere Spiele eingeplant.',
            })

    for team in expected_teams:
        if team["id"] not in scheduled_team_ids:
            warnings.append({
                "level": "error",
                "message": f'{team["name"]} taucht im KO-Baum nicht auf.',
            })

    return warnings


SMALL_FINAL_PHASE = "Spiel um Platz 3"
_GENERIC_ROUND_PREFIX = "Runde der letzten "


def _bracket_size_for_phase(phase):
    """Umkehrung von _round_label: Anzahl Teams der Runde zu einem
    Phasennamen, oder None, wenn der Name keine KO-Runde bezeichnet."""
    for size, label in _ROUND_LABELS_BY_BRACKET_SIZE.items():
        if label == phase:
            return size
    if phase and phase.startswith(_GENERIC_ROUND_PREFIX):
        try:
            return int(phase[len(_GENERIC_ROUND_PREFIX):])
        except ValueError:
            return None
    return None


def _matches_structure(structure, slot_rounds) -> bool:
    """Prueft, ob die gespeicherten Slots wirklich zum rekonstruierten Baum
    passen. Neben Rundenzahl, Spielen je Runde und Rundennamen werden die
    Freilos-Seiten der zweiten Runde geprueft: dort muss bereits seit der
    Erzeugung ein Team stehen, das in der ersten Runde nicht spielt. Damit
    faellt z.B. ein geloeschtes Erstrundenspiel auf, das rein rechnerisch
    wieder einen gueltigen (aber anderen) Baum ergeben wuerde."""
    if [len(r) for r in structure] != [len(r) for r in slot_rounds]:
        return False
    if any(
        structure_round[0]["phase"] != slot_round[0]["phase"]
        for structure_round, slot_round in zip(structure, slot_rounds)
    ):
        return False

    first_round_team_ids = set()
    for slot in slot_rounds[0]:
        if slot["team_a_id"] is None or slot["team_b_id"] is None:
            return False
        first_round_team_ids.update((slot["team_a_id"], slot["team_b_id"]))

    for match in structure[1]:
        slot = slot_rounds[1][match["position"]]
        for side, column in (("source_a", "team_a_id"), ("source_b", "team_b_id")):
            if match[side] is None and (
                slot[column] is None or slot[column] in first_round_team_ids
            ):
                return False
    return True


def plan_ko_advancement(conn, competition_id: int):
    """Ermittelt, welche Teams aus bereits beendeten KO-Spielen in die
    jeweils naechste Runde (und ins Spiel um Platz 3) aufruecken.

    Die Slots speichern keine Baum-Verweise. Der Baum wird deshalb aus der
    Anzahl der Spiele je Runde rekonstruiert: Da Freilose nur in der ersten
    Runde vorkommen, gilt fuer die Teamzahl n = Spiele Runde 1 + 2 x Spiele
    Runde 2; build_ko_rounds(n) liefert damit exakt dieselbe Struktur wie bei
    der Erzeugung. Innerhalb einer Runde entspricht die Reihenfolge der
    Slot-IDs der Position im Baum (Uhrzeit/Feld koennen per Drag & Drop
    veraendert worden sein, die ID nicht).

    Gibt ein dict mit "updates" (Liste von {"slot_id", "column", "team_id"})
    und "warnings" (Liste von Texten) zurueck. Bereits gestartete oder
    beendete Zielspiele werden nie veraendert."""
    rows = conn.execute("""
        SELECT * FROM slots
        WHERE competition_id = ? AND slot_typ = 'Spiel'
        ORDER BY id
    """, (competition_id,)).fetchall()

    slots_by_size = {}
    small_final_slot = None
    for row in rows:
        if row["phase"] == SMALL_FINAL_PHASE:
            if small_final_slot is None:
                small_final_slot = row
            continue
        size = _bracket_size_for_phase(row["phase"])
        if size is not None:
            slots_by_size.setdefault(size, []).append(row)

    result = {"updates": [], "warnings": []}
    if len(slots_by_size) < 2:
        return result

    slot_rounds = [slots_by_size[size] for size in sorted(slots_by_size, reverse=True)]
    team_count = len(slot_rounds[0]) + 2 * len(slot_rounds[1])
    structure = build_ko_rounds([str(index) for index in range(team_count)])
    if not _matches_structure(structure, slot_rounds):
        result["warnings"].append(
            "Der gespeicherte KO-Baum passt nicht zur erwarteten Struktur "
            "(Spiele wurden gelöscht oder ergänzt). Bitte die Folgerunden "
            "manuell besetzen."
        )
        return result

    def slot_for(match, round_index):
        return slot_rounds[round_index][match["position"]]

    def decided(source_slot):
        if source_slot["status"] != "beendet":
            return None, None
        winner, loser = get_winner_loser(source_slot)
        if winner is None:
            result["warnings"].append(
                f'{source_slot["phase"]}: Ein beendetes Spiel endete unentschieden. '
                "Bitte den entschiedenen Endstand (Verlängerung/Elfmeterschießen) eintragen."
            )
        return winner, loser

    def plan(target_slot, column, team_id):
        if team_id is None or target_slot[column] == team_id:
            return
        if target_slot["status"] != "geplant" or target_slot["score_a"] is not None \
                or target_slot["score_b"] is not None:
            return
        result["updates"].append({
            "slot_id": target_slot["id"],
            "column": column,
            "team_id": team_id,
        })

    for round_index in range(1, len(structure)):
        for match in structure[round_index]:
            target_slot = slot_for(match, round_index)
            for side, column in (("source_a", "team_a_id"), ("source_b", "team_b_id")):
                source = match[side]
                if source is None:
                    continue
                winner, _ = decided(slot_for(source, round_index - 1))
                plan(target_slot, column, winner)

    final_match = structure[-1][0]
    if (
        small_final_slot is not None
        and final_match["source_a"] is not None
        and final_match["source_b"] is not None
    ):
        semifinal_index = len(structure) - 2
        for side, column in (("source_a", "team_a_id"), ("source_b", "team_b_id")):
            source_slot = slot_for(final_match[side], semifinal_index)
            if source_slot["status"] != "beendet":
                continue
            _, loser = get_winner_loser(source_slot)
            plan(small_final_slot, column, loser)

    # Unentschieden-Hinweise koennen fuer das Halbfinale doppelt entstehen
    # (Finale und Spiel um Platz 3 lesen dieselben Spiele).
    result["warnings"] = list(dict.fromkeys(result["warnings"]))
    return result


def advance_ko_rounds(competition_id: int):
    """Traegt alle per plan_ko_advancement() ermittelten Aufsteiger ein und
    gibt die Anzahl geaenderter Slot-Seiten sowie die Hinweise zurueck."""
    with get_conn() as conn:
        result = plan_ko_advancement(conn, competition_id)
        for update in result["updates"]:
            # column stammt ausschliesslich aus plan_ko_advancement
            # ("team_a_id"/"team_b_id"), nie aus einer Benutzereingabe.
            conn.execute(
                f"UPDATE slots SET {update['column']} = ? WHERE id = ?",
                (update["team_id"], update["slot_id"]),
            )
        conn.commit()
    return result
