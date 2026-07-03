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

## Phase 4.7 – Spielplan-Generator: Robustheit für andere Schulen

Audit vom Juli 2026 (KO-Runden-Zuweisung und Generator), vor Weitergabe an andere Schulen abzuarbeiten. Nach Priorität sortiert.

Kritisch:

[ ] Halbfinale/Finale: Überschreiben bereits gespielter Ergebnisse verhindern (Status-Check statt bedingungslosem Reset auf `geplant`)
[ ] "Halbfinale/Finale automatisch besetzen": sichtbare Fehlermeldung statt stillem Redirect, wenn keine gültigen Gruppen vorhanden sind
[ ] Ergebniseingabe: dauerhaften Button zum (erneuten) Besetzen von Halbfinale/Finale anbieten (nicht nur das einmalige Bestätigungs-Banner direkt nach Abschluss der Vorphase), damit Schiedsrichter nach einer nachträglichen Ergebniskorrektur nicht wieder einen Admin für den Spielplan-Editor brauchen

Hoch:

[ ] Fallback-Paarungsalgorithmus durch korrektes Rundenverfahren ersetzen (aktuell keine garantiert faire Spielanzahl bei Teamzahlen ≠ 6/7, siehe Testfall mit 8 Teams)
[ ] Gruppenbildung für KO-Runden generalisieren statt hart auf 6/7 Teams kodiert
[ ] Cross-Wettbewerb-Konfliktprüfung: Team- und Feldkollisionen gegen den gesamten bestehenden Spielplan prüfen, nicht nur innerhalb des aktuellen Vorschlags

Mittel:

[ ] Feld-Auswahl fürs Spiel um Platz 3 über bestehende Orts-Filterung laufen lassen statt beliebiges freies Feld
[ ] Serverseitige Validierung beim Übernehmen des Vorschlags nachrüsten (bisher nur Template-seitig ausgeblendet)
[ ] Unentschieden im Halbfinale: Fehlermeldung plus Lösung (manuelle Korrektur oder Verlängerung/Neunmeterschießen)

Niedrig:

[ ] HF1/HF2-Zuordnung robuster machen (aktuell nur über Sortierreihenfolge der Slots)
[ ] Gruppentabelle bei 4er-Gruppen: Hinweis, dass nicht jeder gegen jeden spielt (unvollständiger Round-Robin)
[ ] generate_semifinals/generate_finals: schedule_planning_available() prüfen (Konsistenz zu anderen Spielplan-Routen)
[ ] Mit Testdaten für 4, 5, 8, 9, 10, 12 Teams pro Jahrgang durchspielen, bevor eine andere Schule live geht

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