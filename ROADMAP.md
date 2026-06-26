# Sportfest Manager Roadmap

## Phase 1 – Stabilität & Bedienbarkeit

[x] Spielplan-Bearbeitung als Zeitraster
[x] feste Zeitspalte links
[x] Zeiten nach Drag & Drop logisch halten
[x] Warnung bei Überschreitung der Wettbewerbs-Endzeit
[x] Fortschrittsbalken anhand Veranstaltungsdatum korrigieren

## Phase 2 – Orte & Spielplan

[x] Ort am Wettbewerb

    - Turnhalle
    - Fußballplatz
    - Außenbereich

[x] Tagesplan der nächsten Veranstaltung
[x] Spielplan nach Ort darstellen
[x] Übersicht und Turnierplanung trennen
[x] Fußballplatz berücksichtigen

## Phase 3 – Zeitplanung

[x] Spielzeit pro Wettbewerb
[x] Wechselzeit pro Wettbewerb
[x] Schiedsrichtertimer nutzt Spielzeit
[x] Endzeit-Prognose

## Phase 4 – Sicherheit

[x] Rollenmodell vorbereitet
[x] Bereichsschutz statt Einzelschutz

[x] Änderungsprotokoll
[x] Änderungsanzahl direkt bei Ergebnissen anzeigen
[x] Rollenanzeige im UI verbessern

Später:

[x] Stationshelfer technisch vorbereitet (ohne Anmeldung, PIN oder Stationsrechte)
[ ] Stations-PIN

## Phase 4.5 – Spielplan- und Rollenmodell-Bereinigung

[x] Öffentliche Spielplanseite ohne Link zur Bearbeitung
[x] Interne Ansichtsumschalter aus der Spielplanbearbeitung entfernen
[x] Leere Zeitraster-Slots als Drop-Zonen nutzbar machen

[x] /spielplan: Link/Button zur Spielplanbearbeitung entfernen
[x] /spielplan-bearbeiten: interne Navigationsbuttons entfernen
[x] Zeitraster: Spiele müssen auch in spätere leere Zeitslots gezogen werden können

## Phase 4.6 – Ortssensitive Spielplanung

[x] Spielplan-Generator berücksichtigt Ort des Wettbewerbs
[x] Turnhalle: Feld 1, Feld 2, Feld 3
[x] Fußballplatz: Rasenplatz, Tartanplatz
[x] Außenbereich: keine Slotplanung
[x] /spielplan-bearbeiten zeigt je nach Ort passende Spalten


## Phase 5 – Sechskampf 2.0

[ ] mehrere Jahrgänge pro Wettbewerb
[ ] GOST jahrgangsübergreifend
[ ] Stationshelfer-Rolle
[ ] Stationssperren

## Phase 6 – Turnierleitungs-Dashboard

[ ] Gesamtwertung prominent anzeigen
[x] Veranstaltungsstatus
[ ] offene Ergebnisse
[ ] Wettbewerbsfortschritt

## Phase 7 – Vorlagen

[ ] globale Wertungseinstellungen
[ ] Vorlage Bewegungsfest
[ ] Vorlage Schulpokal

## Refactoring

[x] Spielplanrouten und Spielplan-Services aus `app/main.py` ausgelagert
[x] Einstellungen, Systeminformationen und Backup aus `app/main.py` ausgelagert
[x] Wettbewerbsverwaltung aus `app/main.py` in `app/routes/competitions.py` ausgelagert
[x] Veranstaltungsverwaltung aus `app/main.py` in `app/routes/events.py` ausgelagert
[ ] Eigene Changelog-Seite/Route auslagern, sobald sie umgesetzt ist

## UI / Komfort

[ ] Changelog-Seite
[ ] Spielstände direkt im Spielplan
[x] Wettbewerbe filtern
[x] Ergebniseingabe filtern
[ ] Tabellen filtern

## Langfristig

[ ] Stations-PIN
[ ] Benutzerverwaltung
[ ] Rechteverwaltung per Checkbox

## Bugs/UX

[x] Dashboard-Tagesplan tabellarisch anzeigen
[ ] Sechskampf: Ganzzahlen nicht als Dezimalzahl anzeigen
[ ] Sechskampf: Speicher-Uhrzeit korrigieren