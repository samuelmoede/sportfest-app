"""Wettbewerbsnamen sind nur innerhalb einer Veranstaltung eindeutig.

Derselbe Name (z. B. "Zweifelderball Jahrgang 7") darf in mehreren
Veranstaltungen vorkommen - etwa im Schulpokal dieses Jahres und in einem
archivierten aus dem Vorjahr. Wettbewerbe ohne Veranstaltung bilden
zusammen eine eigene Gruppe. Die Datenbank sichert das ueber den Index
idx_competitions_event_name ab (siehe app/database.py).
"""


def competition_name_vergeben(conn, name, event_id, ausser_id=None) -> bool:
    """True, wenn es in derselben Veranstaltung schon einen (anderen)
    Wettbewerb mit diesem Namen gibt."""
    return conn.execute(
        """
        SELECT 1 FROM competitions
        WHERE name = ?
          AND COALESCE(event_id, 0) = COALESCE(?, 0)
          AND id != ?
        """,
        (name, event_id, ausser_id if ausser_id is not None else -1),
    ).fetchone() is not None


def freier_competition_name(conn, base_name, event_id, ausser_id=None) -> str:
    """Liefert base_name oder - falls in der Veranstaltung schon vergeben -
    base_name mit angehaengter Nummer: "Name (2)", "Name (3)", ..."""
    candidate = base_name
    suffix = 2
    while competition_name_vergeben(conn, candidate, event_id, ausser_id):
        candidate = f"{base_name} ({suffix})"
        suffix += 1
    return candidate
