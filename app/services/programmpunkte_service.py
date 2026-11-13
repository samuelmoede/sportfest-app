"""Programmpunkte einer Veranstaltung fuer den Tagesplan: Eroeffnung,
Siegerehrung, Pause, Umbau usw. mit Uhrzeit von/bis, fuer alle oder nur
bestimmte Jahrgaenge (z.B. eine Siegerehrung fuer Jahrgang 7 und 8).

Sie werden direkt am Tagesplan der Veranstaltung angelegt und erscheinen
als Abschnitt in den Bloecken der betroffenen Jahrgaenge (siehe
build_block_segments in ui_view_service)."""
from app.services.schedule_time_service import parse_slot_time

PROGRAMMPUNKT_VORSCHLAEGE = ("Eröffnung", "Siegerehrung", "Pause", "Umbau", "Mittagspause")


def _clock(value):
    parsed = parse_slot_time(value)
    return parsed.strftime("%H:%M") if parsed else None


def parse_jahrgaenge_liste(values):
    """Liste aus Formular-Checkboxen oder "7,8" -> ["7", "8"] (als Text,
    damit auch "GOST" geht)."""
    if isinstance(values, str):
        values = values.split(",")
    result = []
    for value in values or []:
        value = str(value).strip()
        if value and value not in result:
            result.append(value)
    return result


def create_programmpunkt(conn, event_id, titel, start_time, end_time, jahrgaenge=None):
    """Legt einen Programmpunkt an. Wirft ValueError mit einer Meldung fuer
    die Oberflaeche."""
    titel = (titel or "").strip()
    start = _clock(start_time)
    end = _clock(end_time)
    if not titel:
        raise ValueError("Bitte eine Bezeichnung angeben, z. B. Eröffnung.")
    if start is None or end is None:
        raise ValueError("Bitte Uhrzeit von und bis angeben.")
    if end <= start:
        raise ValueError("Die Endzeit muss nach der Startzeit liegen.")
    return conn.execute(
        """
        INSERT INTO event_programmpunkte (event_id, titel, start_time, end_time, jahrgaenge)
        VALUES (?, ?, ?, ?, ?)
        """,
        (event_id, titel, start, end, ",".join(parse_jahrgaenge_liste(jahrgaenge)) or None),
    ).lastrowid


def list_programmpunkte(conn, event_id):
    rows = conn.execute(
        """
        SELECT * FROM event_programmpunkte WHERE event_id = ?
        ORDER BY start_time, end_time, id
        """,
        (event_id,),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["jahrgaenge_liste"] = parse_jahrgaenge_liste(item.get("jahrgaenge") or "")
        item["jahrgaenge_label"] = (
            "alle Jahrgänge" if not item["jahrgaenge_liste"]
            else "Jahrgang " + " + ".join(item["jahrgaenge_liste"])
        )
        result.append(item)
    return result


def applies_to_jahrgang(programmpunkt, jahrgang):
    jahrgaenge = programmpunkt.get("jahrgaenge_liste")
    if jahrgaenge is None:
        jahrgaenge = parse_jahrgaenge_liste(programmpunkt.get("jahrgaenge") or "")
    return not jahrgaenge or str(jahrgang).strip() in jahrgaenge


def delete_programmpunkt(conn, event_id, programmpunkt_id):
    conn.execute(
        "DELETE FROM event_programmpunkte WHERE id = ? AND event_id = ?",
        (programmpunkt_id, event_id),
    )
