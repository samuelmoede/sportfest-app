"""Zwischenstand eines Spiels automatisch speichern (Ergebnis-Raster und
Feldansicht speichern beim Tippen, ohne Speichern-Knopf).

Bewusst getrennt vom bisherigen /slot/{id}/save:

* Nur der Stand wird geschrieben. Status, Startzeit und Ende bleiben, wie sie
  sind - wer in ein geplantes Spiel tippt, startet damit nicht die Uhr.
  Start und Beenden bleiben eigene, bewusste Schritte.
* Beide Felder leer loescht den Stand (vorher unmoeglich: nach "Start
  rueckgaengig" blieb z.B. 2:1 stehen und liess sich nicht mehr entfernen).
* Ein beendetes Spiel wird nie automatisch geaendert. Korrekturen gehen
  weiter ueber "Bearbeiten" mit Rueckfrage, weil sie Folgephasen betreffen.
* Konfliktschutz: Das Geraet schickt mit, welchen Stand es zuletzt vom Server
  kannte (basis). Hat inzwischen ein anderes Geraet etwas anderes gespeichert,
  wird nichts ueberschrieben, sondern der aktuelle Stand zurueckgemeldet.
"""

GESPEICHERT = "gespeichert"
UNVERAENDERT = "unveraendert"
KONFLIKT = "konflikt"
BEENDET = "beendet"
UNGUELTIG = "ungueltig"
NICHT_GEFUNDEN = "nicht_gefunden"

MAX_PUNKTE = 9999


def lies_zahl(wert):
    """'' -> None, '3' -> 3; alles andere -> ValueError."""
    text = "" if wert is None else str(wert).strip()
    if text == "":
        return None
    if not text.isdigit():
        raise ValueError(text)
    zahl = int(text)
    if zahl > MAX_PUNKTE:
        raise ValueError(text)
    return zahl


def lies_stand(roh_a, roh_b):
    """Liefert (a, b): beide Zahlen oder beide None (= Stand loeschen).
    Nur ein Feld ausgefuellt ist (noch) kein Stand -> ValueError."""
    a = lies_zahl(roh_a)
    b = lies_zahl(roh_b)
    if (a is None) != (b is None):
        raise ValueError("nur ein Feld ausgefuellt")
    return a, b


def speichere_zwischenstand(conn, slot_id, roh_a, roh_b, basis=None, protokolliere=None):
    """Schreibt den Zwischenstand. Committet nicht.

    basis: None (keine Pruefung) oder (roh_a, roh_b) - der Stand, den das
    Geraet zuletzt vom Server kannte.
    protokolliere(action, slot, alt, neu): schreibt den Eintrag ins
    Aenderungsprotokoll (main.record_change mit Request).

    Rueckgabe: dict mit ergebnis (siehe Konstanten oben), score_a, score_b
    (aktueller Stand nach dem Aufruf) und status des Spiels.
    """
    slot = conn.execute(
        "SELECT id, competition_id, status, score_a, score_b FROM slots WHERE id = ?",
        (slot_id,),
    ).fetchone()
    if slot is None:
        return {"ergebnis": NICHT_GEFUNDEN, "score_a": None, "score_b": None, "status": None}

    aktuell = (slot["score_a"], slot["score_b"])

    def antwort(ergebnis, stand=aktuell):
        return {"ergebnis": ergebnis, "score_a": stand[0], "score_b": stand[1], "status": slot["status"]}

    try:
        neu = lies_stand(roh_a, roh_b)
    except ValueError:
        return antwort(UNGUELTIG)

    if slot["status"] == "beendet":
        return antwort(BEENDET)

    if neu == aktuell:
        return antwort(UNVERAENDERT)

    if basis is not None:
        try:
            erwartet = (lies_zahl(basis[0]), lies_zahl(basis[1]))
        except ValueError:
            erwartet = None
        if erwartet != aktuell:
            return antwort(KONFLIKT)

    conn.execute(
        "UPDATE slots SET score_a = ?, score_b = ? WHERE id = ?",
        (neu[0], neu[1], slot_id),
    )
    if protokolliere is not None:
        if aktuell == (None, None):
            action = "Ergebnis erfasst"
        elif neu == (None, None):
            action = "Ergebnis gelöscht"
        else:
            action = "Ergebnis geändert"
        protokolliere(action, slot, aktuell, neu)
    return antwort(GESPEICHERT, neu)
