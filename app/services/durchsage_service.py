"""Durchsage an alle: ein Hinweis der Turnierleitung (z.B. "Spielzeit ab
sofort 6 statt 7 Minuten"), der auf allen offenen Seiten der App erscheint
und stehen bleibt, bis man ihn mit "Verstanden" bestaetigt (je Geraet, im
Browser gemerkt). Es gibt immer hoechstens eine aktive Durchsage; eine neue
ersetzt die alte."""

MAX_TEXT_LAENGE = 500


def durchsage_senden(conn, text, created_at, created_by=None):
    text = " ".join((text or "").split())
    if not text:
        raise ValueError("Bitte einen Text für die Durchsage eingeben.")
    text = text[:MAX_TEXT_LAENGE]
    conn.execute("UPDATE durchsagen SET active = 0 WHERE active = 1")
    return conn.execute(
        "INSERT INTO durchsagen (text, created_at, created_by, active) VALUES (?, ?, ?, 1)",
        (text, created_at, created_by),
    ).lastrowid


def aktuelle_durchsage(conn):
    row = conn.execute(
        "SELECT id, text, created_at FROM durchsagen WHERE active = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def durchsage_beenden(conn, durchsage_id):
    conn.execute("UPDATE durchsagen SET active = 0 WHERE id = ?", (durchsage_id,))
