"""Jahrgangsfinale beim Schulpokal: die Sieger zweier Wettbewerbe (z.B.
Zweifelderball Jahrgang 7 und Jahrgang 8) spielen im Anschluss gegeneinander.

Das Finale ist ein eigener Wettbewerb vom Typ Turnier mit genau einem Spiel
(Phase "Finale"). Die Quell-Wettbewerbe stehen in
competitions.final_source_ids ("12,13"). Teams kommen erst beim Besetzen
dazu (competition_teams), vorher ist das Spiel ein Platzhalter. So zeigt die
Tabelle des Finales nur die beiden Finalisten und nicht alle Klassen beider
Jahrgaenge."""
from typing import Callable, List, Optional

from app.services.schedule_location_service import get_effective_competition_location


def parse_final_source_ids(value) -> List[int]:
    ids = []
    for part in str(value or "").split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    return ids


def _short_jahrgang(competition) -> str:
    return str(competition["jahrgang"]).strip()


def _unique_name(conn, base_name: str) -> str:
    candidate = base_name
    suffix = 2
    while conn.execute("SELECT 1 FROM competitions WHERE name = ?", (candidate,)).fetchone():
        candidate = f"{base_name} ({suffix})"
        suffix += 1
    return candidate


def find_jahrgangsfinale(conn, source_competition_id: int):
    """Liefert das bereits angelegte Finale, an dem dieser Wettbewerb
    beteiligt ist, oder None."""
    rows = conn.execute(
        "SELECT * FROM competitions WHERE final_source_ids IS NOT NULL AND final_source_ids != ''"
    ).fetchall()
    for row in rows:
        if source_competition_id in parse_final_source_ids(row["final_source_ids"]):
            return row
    return None


def create_jahrgangsfinale(
    conn,
    source_ids: List[int],
    startzeit: str,
    court_id: Optional[int] = None,
):
    """Legt den Finale-Wettbewerb samt Platzhalter-Spiel an und gibt dessen
    id zurueck. Fehler kommen als ValueError mit einer Meldung fuer die
    Oberflaeche."""
    source_ids = list(dict.fromkeys(source_ids))
    if len(source_ids) != 2:
        raise ValueError("Für ein Jahrgangsfinale braucht es genau zwei Wettbewerbe.")
    if not str(startzeit or "").strip():
        raise ValueError("Bitte eine Uhrzeit für das Finale angeben.")

    sources = []
    for source_id in source_ids:
        row = conn.execute("SELECT * FROM competitions WHERE id = ?", (source_id,)).fetchone()
        if row is None:
            raise ValueError("Einer der Wettbewerbe wurde nicht gefunden.")
        if find_jahrgangsfinale(conn, source_id) is not None:
            raise ValueError(f'Für "{row["name"]}" gibt es schon ein Jahrgangsfinale.')
        sources.append(row)

    first, second = sources
    base_name = (
        f"{first['sportart']} Jahrgangsfinale "
        f"{_short_jahrgang(first)}/{_short_jahrgang(second)}"
    )
    name = _unique_name(conn, base_name)
    cursor = conn.execute(
        """
        INSERT INTO competitions (
            name, sportart, jahrgang, status, points_win, points_draw, points_loss,
            points_first_place, event_id, competition_type, tournament_mode,
            game_duration_minutes, changeover_duration_minutes, location,
            start_time, competition_date, final_source_ids
        ) VALUES (?, ?, 'mixed', 'geplant', 3, 1, 0, 2, ?, 'Turnier', 'punkterunde',
                  ?, ?, ?, ?, ?, ?)
        """,
        (
            name, first["sportart"], first["event_id"],
            first["game_duration_minutes"], first["changeover_duration_minutes"],
            get_effective_competition_location(first),
            startzeit.strip(), first["competition_date"],
            ",".join(str(source_id) for source_id in source_ids),
        ),
    )
    final_id = cursor.lastrowid
    conn.execute(
        """
        INSERT INTO slots (competition_id, court_id, startzeit, slot_typ, phase, status, note)
        VALUES (?, ?, ?, 'Spiel', 'Finale', 'geplant', ?)
        """,
        (
            final_id, court_id, startzeit.strip(),
            f"Sieger {first['name']} gegen Sieger {second['name']}",
        ),
    )
    return final_id


def _open_games(conn, competition_id: int) -> int:
    return conn.execute(
        """
        SELECT COUNT(*) AS n FROM slots
        WHERE competition_id = ? AND slot_typ = 'Spiel' AND phase = 'Gruppenphase'
          AND status != 'beendet'
        """,
        (competition_id,),
    ).fetchone()["n"]


def _schulpokal_jahrgang_winner(conn, source, calculate_event_overall_ranking):
    """Beim Schulpokal gibt es einen Sieger je Jahrgang aus allen
    Wettbewerben dieses Jahrgangs (z.B. Volleyball + Tischtennis), siehe
    calculate_event_overall_ranking. Liefert (team_id, meldung)."""
    competitions = conn.execute(
        """
        SELECT id, name FROM competitions
        WHERE event_id = ? AND jahrgang = ?
          AND (final_source_ids IS NULL OR final_source_ids = '')
        """,
        (source["event_id"], source["jahrgang"]),
    ).fetchall()
    for competition in competitions:
        open_games = _open_games(conn, competition["id"])
        if open_games:
            return None, f'In "{competition["name"]}" sind noch {open_games} Spiele offen.'
    label = f"Gesamtwertung Jahrgang {source['jahrgang']}"
    for group in calculate_event_overall_ranking(source["event_id"]):
        if group["year_group"] == label and group["rows"]:
            winner = group["rows"][0]
            if winner.get("total_points"):
                return winner["team_id"], None
    return None, f"Für Jahrgang {source['jahrgang']} gibt es noch keine Ergebnisse."


def fill_jahrgangsfinale(
    conn,
    final_competition_id: int,
    calculate_table: Callable,
    confirm_overwrite: bool = False,
    calculate_event_overall_ranking: Optional[Callable] = None,
):
    """Traegt die Sieger der Quell-Wettbewerbe ins Finale ein: beim
    Schulpokal den Jahrgangssieger (alle Wettbewerbe des Jahrgangs), sonst
    den Tabellenersten. Gibt eine Liste mit Meldungen zurueck; leer heisst
    erfolgreich."""
    final = conn.execute(
        "SELECT * FROM competitions WHERE id = ?", (final_competition_id,)
    ).fetchone()
    source_ids = parse_final_source_ids(final["final_source_ids"]) if final else []
    if len(source_ids) != 2:
        return ["Dieser Wettbewerb ist kein Jahrgangsfinale."]

    final_slot = conn.execute(
        """
        SELECT * FROM slots
        WHERE competition_id = ? AND slot_typ = 'Spiel' AND phase = 'Finale'
        ORDER BY startzeit, id LIMIT 1
        """,
        (final_competition_id,),
    ).fetchone()
    if final_slot is None:
        return ["Das Finalspiel fehlt im Spielplan."]
    if final_slot["status"] != "geplant" and not confirm_overwrite:
        return ["Das Finale läuft schon oder ist beendet und wurde nicht verändert."]

    event = conn.execute(
        "SELECT event_type FROM events WHERE id = ?", (final["event_id"],)
    ).fetchone()
    use_jahrgang_winner = (
        calculate_event_overall_ranking is not None
        and event is not None and event["event_type"] == "Schulpokal"
    )

    winners = []
    for source_id in source_ids:
        source = conn.execute("SELECT * FROM competitions WHERE id = ?", (source_id,)).fetchone()
        if source is None:
            return ["Einer der Wettbewerbe wurde nicht gefunden."]
        if use_jahrgang_winner:
            team_id, message = _schulpokal_jahrgang_winner(
                conn, source, calculate_event_overall_ranking
            )
            if message:
                return [message]
            winners.append(team_id)
            continue
        source_name = source["name"]
        open_games = _open_games(conn, source_id)
        if open_games:
            return [f'In "{source_name}" sind noch {open_games} Spiele offen.']
        ranking = calculate_table(source_id, phase="Gruppenphase")
        if not ranking or not ranking[0].get("sp"):
            return [f'"{source_name}" hat noch keine Ergebnisse.']
        winners.append(ranking[0]["team_id"])

    conn.execute("DELETE FROM competition_teams WHERE competition_id = ?", (final_competition_id,))
    for team_id in winners:
        conn.execute(
            "INSERT OR IGNORE INTO competition_teams (competition_id, team_id) VALUES (?, ?)",
            (final_competition_id, team_id),
        )
    conn.execute(
        """
        UPDATE slots
        SET team_a_id = ?, team_b_id = ?, score_a = NULL, score_b = NULL, status = 'geplant'
        WHERE id = ?
        """,
        (winners[0], winners[1], final_slot["id"]),
    )
    return []
