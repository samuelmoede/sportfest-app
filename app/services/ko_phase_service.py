"""Welche KO-Phase eines Turniers als Naechstes automatisch besetzt werden
kann. Grundlage fuer den dauerhaften Besetzen-Button in der Ergebniseingabe
(ROADMAP Phase 4.7): Schiedsrichter sollen nach einer nachtraeglichen
Ergebniskorrektur die Folgephase selbst (erneut) besetzen koennen, ohne einen
Admin fuer die Spielplan-Bearbeitung zu brauchen.

Eigenes Modul, weil es sowohl schedule_generator_service als auch den
Turniermodus "Reine KO-Runde" braucht (ko_runde importiert seinerseits
schedule_generator_service)."""
from app.database import get_conn
from app.services.schedule_generator_service import (
    group_phase_finished,
    has_semifinal_slots,
    semifinals_finished,
)
from app.services.tournament_modes import DEFAULT_TURNIER_MODE
from app.services.tournament_modes.ko_runde import plan_ko_advancement

FINAL_PHASES = ("Finale", "Spiel um Platz 3", "Kleines Finale", "Platzierung")

# Fehlercodes der Besetzen-Aktionen (Query-Parameter ko_error), damit statt
# eines stillen Redirects eine verstaendliche Meldung erscheint.
KO_ERROR_MESSAGES = {
    "keine_gruppen": (
        "Das Halbfinale konnte nicht besetzt werden: Es gibt keine zwei Gruppen "
        "A und B mit jeweils mindestens zwei Teams."
    ),
    "halbfinale_offen": (
        "Das Finale konnte nicht besetzt werden: Es sind noch nicht beide "
        "Halbfinals mit Ergebnis beendet."
    ),
    "halbfinale_unentschieden": (
        "Das Finale konnte nicht besetzt werden: Ein Halbfinale endete "
        "unentschieden. Bitte den entschiedenen Endstand "
        "(Verlängerung/Elfmeterschießen) eintragen."
    ),
    "tabelle_zu_klein": (
        "Das Finale konnte nicht besetzt werden: Die Tabelle der Vorrunde "
        "enthält weniger als zwei Teams."
    ),
}


def get_ko_error_message(code: str):
    return KO_ERROR_MESSAGES.get(code or "")


def _phase_started(competition_id: int, phases) -> bool:
    placeholders = ", ".join("?" for _ in phases)
    with get_conn() as conn:
        row = conn.execute(f"""
            SELECT COUNT(*) AS n FROM slots
            WHERE competition_id = ? AND slot_typ = 'Spiel'
              AND phase IN ({placeholders})
              AND status IN ('läuft', 'beendet')
        """, (competition_id, *phases)).fetchone()
    return row["n"] > 0


def get_next_phase_action(competition):
    """Liefert die aktuell sinnvolle Besetzen-Aktion fuer einen Wettbewerb
    als dict ({"label", "action", "hint"}) oder None.

    Angeboten wird eine Phase nur, solange sie noch nicht begonnen hat - ein
    erneutes Besetzen danach wuerde laufende/gespielte Spiele zuruecksetzen,
    und Korrekturen der Vorphase sind dann ohnehin gesperrt
    (is_next_phase_started)."""
    if competition is None or competition["competition_type"] != "Turnier":
        return None

    competition_id = competition["id"]
    mode = competition["tournament_mode"] or DEFAULT_TURNIER_MODE

    if mode == "ko_runde":
        with get_conn() as conn:
            plan = plan_ko_advancement(conn, competition_id)
        if not plan["updates"]:
            return None
        return {
            "label": "Nächste KO-Runde besetzen",
            "action": f"/competition/{competition_id}/advance-ko-round",
            "hint": "Es stehen Sieger fest, die in die nächste Runde eingetragen werden können.",
        }

    if mode == "punkterunde" and not (
        competition["punkterunde_grosses_finale"] or competition["punkterunde_kleines_finale"]
    ):
        return None

    if has_semifinal_slots(competition_id):
        if semifinals_finished(competition_id):
            if _phase_started(competition_id, FINAL_PHASES):
                return None
            return {
                "label": "Finale besetzen",
                "action": f"/competition/{competition_id}/generate-finals",
                "hint": "Finale und Spiel um Platz 3 anhand der Halbfinal-Ergebnisse (neu) besetzen.",
            }
        if group_phase_finished(competition_id) and not _phase_started(
            competition_id, ("Halbfinale",)
        ):
            return {
                "label": "Halbfinale besetzen",
                "action": f"/competition/{competition_id}/generate-semifinals",
                "hint": "Halbfinals anhand der Gruppentabellen (neu) besetzen.",
            }
        return None

    if group_phase_finished(competition_id) and not _phase_started(competition_id, FINAL_PHASES):
        if not _has_final_slots(competition_id):
            return None
        return {
            "label": "Finale besetzen",
            "action": f"/competition/{competition_id}/generate-finals-from-table",
            "hint": "Finale und Spiel um Platz 3 anhand der Tabelle (neu) besetzen.",
        }
    return None


def _has_final_slots(competition_id: int) -> bool:
    placeholders = ", ".join("?" for _ in FINAL_PHASES)
    with get_conn() as conn:
        row = conn.execute(f"""
            SELECT COUNT(*) AS n FROM slots
            WHERE competition_id = ? AND slot_typ = 'Spiel'
              AND phase IN ({placeholders})
        """, (competition_id, *FINAL_PHASES)).fetchone()
    return row["n"] > 0
