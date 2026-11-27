"""Signale beim Spiel: Einstellungen je Benutzer (Seite "Mein Konto").

Ton und Vibration, Countdown 5-4-3-2-1 und Benachrichtigung (Handy/Uhr,
auch per Web-Push) werden grundsaetzlich je Konto ein- oder ausgeschaltet,
nicht je Spiel. Signale bekommt nur, wer ein Spiel selbst gestartet hat:
dafuer merkt sich ``slots.started_by_user_id`` den startenden Benutzer
(Schiris wechseln sich auf den Feldern ab).

Ohne Anmeldung (Sicherheit aus) gibt es keinen Benutzer: dann merkt sich
der Browser die Schalter selbst, und Signale gehen wie bisher an das Geraet
am Feld.
"""

SCHALTER = ("ton", "countdown", "benachrichtigung")
STANDARD = {name: True for name in SCHALTER}


def lade_einstellungen(conn, user_id):
    """Schalter eines Benutzers als dict, None ohne (gueltigen) Benutzer."""
    if user_id is None:
        return None
    zeile = conn.execute(
        "SELECT signal_ton, signal_countdown, signal_benachrichtigung FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if zeile is None:
        return None
    return {
        "ton": bool(zeile["signal_ton"]),
        "countdown": bool(zeile["signal_countdown"]),
        "benachrichtigung": bool(zeile["signal_benachrichtigung"]),
    }


def speichere_einstellungen(conn, user_id, daten) -> dict:
    """Uebernimmt die mitgeschickten Schalter (fehlende bleiben, wie sie
    sind). Wird die Benachrichtigung ausgeschaltet, werden alle Push-Abos
    dieses Benutzers geloescht: dann kommt auf keinem seiner Handys mehr
    etwas. Liefert die gespeicherten Einstellungen."""
    aktuell = lade_einstellungen(conn, user_id)
    if aktuell is None:
        raise ValueError("Unbekannter Benutzer")
    for name in SCHALTER:
        if name in daten:
            aktuell[name] = bool(daten[name])
    conn.execute(
        """
        UPDATE users
        SET signal_ton = ?, signal_countdown = ?, signal_benachrichtigung = ?
        WHERE id = ?
        """,
        (int(aktuell["ton"]), int(aktuell["countdown"]), int(aktuell["benachrichtigung"]), user_id),
    )
    if not aktuell["benachrichtigung"]:
        conn.execute("DELETE FROM push_abos WHERE user_id = ?", (user_id,))
    return aktuell


def merke_starter(conn, slot_ids, user_id):
    """Haelt fest, wer die (jetzt laufenden) Spiele gestartet hat. Ohne
    Anmeldung bleibt das Feld leer."""
    for slot_id in slot_ids:
        conn.execute(
            "UPDATE slots SET started_by_user_id = ? WHERE id = ? AND status = 'läuft'",
            (user_id, slot_id),
        )


def eigenes_spiel(slot, user_id) -> bool:
    """Bekommt dieses Geraet die Signale fuer das Spiel? Ja, wenn der
    angemeldete Benutzer es gestartet hat - oder wenn niemand angemeldet
    gestartet hat (alter Weg ohne Konten)."""
    starter = slot["started_by_user_id"] if "started_by_user_id" in slot.keys() else None
    return starter is None or (user_id is not None and starter == user_id)
