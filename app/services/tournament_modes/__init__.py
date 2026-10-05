"""Registry der Turniermodi fuer Wettbewerbe vom Typ "Turnier" (siehe Issue
#101, Teilschritt 2 - analog zur bereits bestehenden SCHULPOKAL_MODES-Registry
in app/services/schedule_generator_service.py fuer den Typ "Schulpokal").

Jeder Modus lebt als eigenstaendiges Modul unter app/services/tournament_modes/
und kann unabhaengig von den anderen ergaenzt werden:

- "gruppenphase_ko": bisheriges Standardverhalten (Gruppenphase, optional
  gefolgt von Halbfinale/Finale) - siehe generate_group_plan() in
  schedule_generator_service.py, unveraendert. Default fuer Bestandsdaten
  (competitions.tournament_mode ist dafuer NULL).
- "ko_runde": direktes Einzelausscheiden ohne Gruppenphase - siehe
  app/services/tournament_modes/ko_runde.py.
- "punkterunde": reine Jeder-gegen-Jeden-Runde ohne KO-Phase, Endplatzierung
  ergibt sich allein aus der Tabelle - siehe
  app/services/tournament_modes/punkterunde.py.

Wettbewerbsverwaltung (app/routes/competitions.py) und Spielplan-Generator
(app/routes/schedule.py) lesen ausschliesslich aus dieser Registry, damit ein
weiterer Modus nicht an mehreren Stellen im Code nachgezogen werden muss.
"""
from app.services.tournament_modes.ko_runde import LABEL as KO_RUNDE_LABEL
from app.services.tournament_modes.ko_runde import MODE_KEY as KO_RUNDE_MODE_KEY
from app.services.tournament_modes.punkterunde import LABEL as PUNKTERUNDE_LABEL
from app.services.tournament_modes.punkterunde import MODE_KEY as PUNKTERUNDE_MODE_KEY

DEFAULT_TURNIER_MODE = "gruppenphase_ko"

TURNIER_MODES = {
    DEFAULT_TURNIER_MODE: {"label": "Gruppenphase mit KO-Runde"},
    KO_RUNDE_MODE_KEY: {"label": KO_RUNDE_LABEL},
    PUNKTERUNDE_MODE_KEY: {"label": PUNKTERUNDE_LABEL},
}
