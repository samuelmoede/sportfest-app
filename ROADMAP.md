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

[x] Halbfinale/Finale: Überschreiben bereits gespielter Ergebnisse verhindern (Status-Check statt bedingungslosem Reset auf `geplant`)
[ ] "Halbfinale/Finale automatisch besetzen": sichtbare Fehlermeldung statt stillem Redirect, wenn keine gültigen Gruppen vorhanden sind
[ ] Ergebniseingabe: dauerhaften Button zum (erneuten) Besetzen von Halbfinale/Finale anbieten (nicht nur das einmalige Bestätigungs-Banner direkt nach Abschluss der Vorphase), damit Schiedsrichter nach einer nachträglichen Ergebniskorrektur nicht wieder einen Admin für den Spielplan-Editor brauchen
[x] Unentschieden in K.-o.-Spielen: Entscheidung gefallen, **keine** eigene Sieger-Erfassung (Verlängerung/Elfmeterschießen wird real ausgetragen, danach wird direkt der entschiedene Endstand z.B. "4:2" eingetragen - es gibt dann technisch gar kein Unentschieden mehr). Stattdessen Warnhinweis ergänzt: Ergebniseingabe zeigt bei `score_a == score_b` in einer K.-o.-Phase (Halbfinale/Finale/Platz 3) einen auffälligen Hinweis "Unentschieden – Verlängerung/Elfmeterschießen eintragen", sowohl bei aktiven als auch archivierten Spielen. Verhindert nicht technisch das Speichern eines echten Unentschiedens, macht es aber sehr unwahrscheinlich, dass es unbemerkt bleibt (betrifft damit auch den Finale-Sonderfall unten, ohne separaten Codepfad)
[x] Kaskadierende Inkonsistenz beim Überschreiben: gelöst durch eine Korrektur-Sperre statt Kaskaden-Warnung - auf Wunsch des Nutzers werden bereits gespielte Folgephasen aus Zeitgründen nicht nachträglich wiederholt. Sobald die nächste Phase (Halbfinale nach Gruppenphase, Finale/Platz 3 nach Halbfinale) `läuft` oder `beendet` ist, sind Korrektur, Reaktivieren und Ergebnis-Löschen der vorherigen Phase serverseitig gesperrt (`is_next_phase_started()`), inkl. entsprechendem Hinweis statt Formular im Archiv. Update 2026-07-04 (Simulation, echter Fund + Fix): Die Sperre selbst war korrekt, aber sie deckte nicht den Zeitraum zwischen "Folgephase ausgelost" (`generate-semifinals`/`generate-finals` gedrückt) und "Folgephase gestartet" ab - `is_next_phase_started()` prüft nur `läuft`/`beendet`. In diesem Fenster ließ sich ein Halbfinale/Gruppenspiel klaglos korrigieren, während die bereits ausgeloste Folgephase (z.B. das Finale) unverändert mit den alten, jetzt falschen Teams stehen blieb - keine Sperre, keine Warnung, einfach ein stiller Widerspruch im Spielplan. Reproduziert in der Simulation: Halbfinale nachträglich auf ein anderes Ergebnis geändert, Finale zeigte weiterhin unveraendert die alten Teams. Fix in `save_slot()`: Bei jeder erfolgreichen Korrektur eines bereits entschiedenen Gruppen-/Halbfinalspiels werden alle abhängigen Folgephase-Slots, die zwar schon Teams zugewiesen bekommen haben (`team_a_id`/`team_b_id` gesetzt) aber noch `geplant` sind (noch nicht gestartet), automatisch auf unbesetzt zurückgesetzt (Teams, Ergebnis, Status) und im Änderungsprotokoll vermerkt - der Widerspruch wird dadurch sofort sichtbar ("?" statt der alten Teams) und die Turnierleitung muss die Folgephase bewusst über den bestehenden "automatisch besetzen"-Button neu auslosen. Per Test verifiziert (inkl. Regressionstest, dass die bestehende Sperre bei bereits gestarteter/beendeter Folgephase weiterhin unverändert blockiert)
[x] Gruppentabellen-Tiebreak bei 3+ punktgleichen Teams entschied faktisch nach Alphabet statt nach direktem Vergleich: `sort_table_rows()` machte nur einen einzigen Durchlauf mit paarweisem Vergleich benachbarter Zeilen (`calculate_direct_comparison`), keine echte Unter-Tabelle nur unter den punktgleichen Teams. In der Simulation Juli 2026 reproduziert: 3 Teams komplett punktgleich (A schlägt B, B schlägt C, C schlägt A, überall 2:1) - bei 3 Teams im Rundenspiel ohne Unentschieden ist eine solche Konstellation bei Punktegleichheit *immer* zyklisch, eine echte Hierarchie ist rechnerisch unmöglich. Ergebnis: Team A wurde rein alphabetisch als Gruppenerster geführt und zog ins Halbfinale ein, obwohl Team C es im direkten Vergleich geschlagen hatte; Team C schied aus. Update 2026-07-04 (echter Fix, nicht nur Anzeige): `sort_table_rows()` bildet jetzt für jede komplett punktgleiche Gruppe (Punkte, Tordifferenz, Tore) eine echte Mini-Tabelle nur aus den Duellen untereinander (`_resolve_tied_group()`/`_mini_league_stats()`, FIFA-Style) - dabei werden alle Teams der Gruppe gemeinsam betrachtet statt nur Nachbarn. Kann die Mini-Tabelle trennen (z.B. weil ein Team beide Duelle gewann), wird danach sortiert und mit "...im direkten Vergleich der punktgleichen Teams entschieden" gekennzeichnet. Bleibt ein echter Zyklus (A/B/C je 2:1 im Kreis) auch in der Mini-Tabelle komplett gleich, gibt es dafür weiterhin keine "richtige" Auflösung allein aus der Tabelle (wie im Ligafußball, üblich ist dann z.B. Los-Entscheid) - dieser Fall wird jetzt aber explizit als "Zyklischer Gleichstand ... keine eindeutige Auflösung möglich" gekennzeichnet, statt fälschlich als entschieden zu erscheinen; die interne Reihenfolge bleibt dabei alphabetisch, ist aber klar als nicht belastbar markiert. Per Test verifiziert: derselbe A/B/C-2:1-im-Kreis-Fall aus der Simulation liefert jetzt korrekt den Zyklus-Hinweis statt eines falschen "Direktvergleich entschieden"; `find_group_patt_risk()` erkennt darüber auch 3er-Zyklus-Konstellationen als Patt, nicht nur 2er-Gleichstände
[x] Patt-Warnung vor dem letzten offenen Gruppenspiel (2026-07-04): `find_group_patt_risk()` simuliert alle Ergebnisse des letzten noch offenen Gruppenspiels und meldet, wenn bestimmte Ergebnisse (z.B. jedes Unentschieden) zu einem komplett unauflösbaren Gleichstand (Punkte, Tordifferenz, Tore und Direktvergleich alle gleich) an der Qualifikationsgrenze oder um Platz 1 führen würden. Erscheint als Warn-Hinweis mit Beispiel-Ergebnissen direkt an der Ergebniskarte, sobald in einer Gruppe nur noch ein Spiel offen ist
[x] Sechskampf-Team-Deaktivierung liess bereits erfasste Ergebnisse spurlos verschwinden (2026-07-04, auf Nutzer-Nachfrage zur Datenkorrektheit gefunden + behoben): Bei einem reinen Sechskampf-Wettbewerb (keine explizite `competition_teams`-Zuordnung) wurde die Team-Roster fuer `/tabellen` und die Gesamtwertung ausschliesslich ueber `get_teams_for_competition()`/`teams_by_jahrgang` ermittelt, beide gefiltert auf `active = 1`. Wurde ein Team nachtraeglich deaktiviert (z.B. bei einer Klassenlisten-Bereinigung nach der Veranstaltung), verschwand sein kompletter Tabellen-/Gesamtwertungs-Eintrag inklusive bereits erzielter Punkte vollstaendig - die Werte blieben zwar in `sixkampf_team_results` in der Datenbank, wurden aber nirgends mehr angezeigt (bestaetigt: ein Team mit dem besten Ergebnis einer Disziplin verschwand nach Deaktivierung komplett aus Tabelle und Gesamtwertung). Turniere waren davon nicht betroffen, da deren Tabelle sich direkt aus den gespielten Spielen speist, unabhaengig vom active-Status. Fix: neue Funktion `include_teams_with_existing_sixkampf_results()` ergaenzt die aktive Roster um jedes Team, das fuer den Wettbewerb bereits eine Zeile in `sixkampf_team_results` hat, unabhaengig vom aktuellen active-Status - angewendet in `collect_tabellen_view_data()` und `calculate_event_overall_ranking()`. Per Test verifiziert: deaktiviertes Team mit vorhandenem Ergebnis bleibt jetzt in beiden Ansichten sichtbar

Hoch:

[x] Fallback-Paarungsalgorithmus durch korrektes Rundenverfahren ersetzt (2026-07-04, echter Fix nach Nutzer-Nachfrage zur Datenkorrektheit): Die bisherige Greedy-Paarung (`else`-Zweig in `generate_group_plan`, Teams in Namensreihenfolge durchlaufen) konnte bei Teamzahlen außerhalb der hartkodierten 6er-/7er-Sonderfälle einzelne Teams komplett ohne Spiel lassen, waehrend andere ihre volle Anzahl bekamen - z.B. bei 10 Teams/2 Spielen pro Team bekam ein Team 0 Spiele, alle anderen 2. Da die Tabelle ausschliesslich aus tatsaechlich gespielten Spielen berechnet wird, waere das betroffene Team unfair auf dem letzten Platz gelandet, obwohl es nie eine Chance hatte. Ersetzt durch das klassische Rundenverfahren (Circle-Method, `_generate_balanced_pairings()`): ein Team fest, alle anderen rotieren pro Runde; bei ungerader Teamzahl ein Freilos pro Runde. Empirisch für N=4 bis 13 und diverse Spielanzahlen pro Team verifiziert: Spielanzahl-Differenz zwischen Teams jetzt maximal 1 (nur wenn durch Teamzahl+Spielanzahl beide ungerade mathematisch zwingend), nie zweimal derselbe Gegner. End-to-End über die echte Vorschau-Route mit 10 Teams nachgeprüft (vorher 0 Spiele für ein Team, jetzt 2/2/2/2/2/2/2/2/2/2)
[ ] Gruppenbildung für KO-Runden generalisieren statt hart auf 6/7 Teams kodiert
[ ] Cross-Wettbewerb-Konfliktprüfung: Team- und Feldkollisionen gegen den gesamten bestehenden Spielplan prüfen, nicht nur innerhalb des aktuellen Vorschlags

Mittel:

[ ] Feld-Auswahl fürs Spiel um Platz 3 über bestehende Orts-Filterung laufen lassen statt beliebiges freies Feld
[ ] Serverseitige Validierung beim Übernehmen des Vorschlags nachrüsten (bisher nur Template-seitig ausgeblendet)
[ ] Wettbewerb ohne `event_id`: `/spielplan`, `/ergebnisse` etc. werfen die Kompetition beim aktiven Event-Filter stillschweigend aus der Auswahl und fallen auf die ungefilterte Gesamtansicht zurück, statt z.B. beim Anlegen eines Wettbewerbs auf die fehlende Veranstaltungszuordnung hinzuweisen (in der Simulation Juli 2026 aufgefallen: Testwettbewerb ohne Event tauchte im gefilterten Spielplan gar nicht auf)

Niedrig:

[ ] HF1/HF2-Zuordnung robuster machen (aktuell nur über Sortierreihenfolge der Slots)
[ ] Gruppentabelle bei 4er-Gruppen: Hinweis, dass nicht jeder gegen jeden spielt (unvollständiger Round-Robin). Update 2026-07-04: Auf Nutzer-Nachfrage zur Datenkorrektheit gezielt geprüft - die Tabelle selbst ist auch hier korrekt/ehrlich: zwei nie gegeneinander angetretene, aber punktgleiche Teams werden per Mini-Tabelle korrekt als "Punktgleichstand ohne eindeutige Entscheidung" markiert statt einen der beiden faelschlich als "entschieden" auszugeben (per Simulation mit 4 Teams/2 Spielen pro Team verifiziert). Offen bleibt nur die hier ursprünglich gemeinte reine Transparenz-Ergänzung (expliziter Hinweis "nicht jeder hat gegen jeden gespielt" unabhängig davon, ob gerade ein Gleichstand vorliegt)
[ ] generate_semifinals/generate_finals: schedule_planning_available() prüfen (Konsistenz zu anderen Spielplan-Routen)
[ ] Mit Testdaten für 4, 5, 8, 9, 10, 12 Teams pro Jahrgang durchspielen, bevor eine andere Schule live geht

### Simulation Juli 2026 – Turniertag-Durchlauf

Vollständiger Durchlauf über die echten Routen (Gruppenphase → Halbfinale → Finale/Spiel um Platz 3) mit
7 Teams, absichtlichem Unentschieden im Halbfinale und anschließendem Überschreib-Versuch. Danach wieder
gelöscht (Testwettbewerb, keine Produktivdaten betroffen).

Bestätigt funktionierend:
- Gruppenstände, Halbfinale-Paarung (1. Gruppe A vs. 2. Gruppe B usw.) korrekt berechnet
- Feld-Kontinuität in der Gruppenphase (keine unnötigen Feldwechsel), Pause-Karte korrekt positioniert und gleich hoch
- Phasen-Badges (Gruppe A/B, Halbfinale, Finale, Spiel um Platz 3) korrekt in Spielplan, Ergebniseingabe und Tabellen
- `phase_ready`-Bestätigung erscheint exakt beim letzten Spiel einer Phase, nicht früher, nicht bei späteren Korrekturen erneut
- Endstand in `/tabellen` folgt korrekt dem K.-o.-Baum (Finalsieger etc.), nicht der reinen Punktetabelle
- Überschreib-Schutz: bereits gespieltes Finale wurde beim Neu-Besetzen ohne Bestätigung nicht verändert; erst nach expliziter Bestätigung überschrieben und im Änderungsprotokoll gesichert

Dabei gefunden (siehe Einträge oben unter Kritisch/Mittel): Unentschieden im Halbfinale führt zu stillem
No-Op bei "Finale besetzen"; Unentschieden im Finale lässt die Platzierung unbemerkt auf die alte
Flach-Tabellen-Logik zurückfallen; Überschreiben einer frühen Phase lässt bereits gespielte spätere Phasen
inkonsistent (aber unverändert) stehen; Wettbewerbe ohne Veranstaltungszuordnung verschwinden aus
gefilterten Ansichten.

## Phase 5 – Sechskampf 2.0

[ ] mehrere Jahrgänge pro Wettbewerb
[ ] GOST jahrgangsübergreifend
[ ] Stationshelfer-Rolle
[ ] Stationssperren

## Phase 5.5 – Feld-/Stationsfindung für Schiedsrichter

Idee (2026-07-07, Kollegen-Feedback nach dem ersten Live-Test): Schiedsrichtern fiel es schwer, schnell das
richtige Feld/die richtige Station zu finden. Erste gesammelte Ideen, noch nicht bewertet/priorisiert:

[ ] QR-Code je Feld/Station (ausgedruckt/laminiert vor Ort), der direkt zur passenden Ergebniseingabe-URL
    verlinkt (z.B. `/ergebnisse?competition_id=X&discipline_id=Y` bzw. `?court_id=Z`)
[ ] Kachel-Startseite "Wohin muss ich?" mit großen, leicht antippbaren Kacheln pro Feld/Station statt
    Dropdown-Filtern
[ ] Kurze, leicht merkbare Codes je Feld (z.B. "Feld 3 = Code 103") als Alternative zu QR-Codes
[ ] Perspektivisch: direkte Weiterleitung zur zugewiesenen Station nach Login, falls es einmal eine
    Zuteilungsliste (wer betreut welches Feld) im System gibt

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
[x] Spielplan-Aushang zum Ausdrucken (2026-07-04): Neue Route `/spielplan/aushang?ort=Turnhalle|Fußballplatz` zeigt je Ort ein Zeit-x-Feld-Raster (alle Felder eines Ortes nebeneinander) statt der Karten-Listenansicht - auf A4 (Hochformat, kompakte Schrift/Zeilenabstand per Print-CSS) optimiert, damit Turnhalle und Fußballplatz je auf einer Seite Platz haben und getrennt ausgehängt werden können. Portrait statt des urspruenglich vermuteten Querformats gewaehlt, da bei typischerweise vielen Zeitzeilen (ein voller Turniertag ueber mehrere Wettbewerbe) und wenigen Feld-Spalten die groessere Hochformat-Hoehe (297mm) mehr Zeilen ohne Schriftverkleinerung erlaubt als die groessere Querformat-Breite genutzt haette. `build_location_print_grid()` in `schedule_grid_service.py` baut das Raster aus den bestehenden `court_groups`; robust gegen die bekannte Cross-Wettbewerb-Feldkollision (siehe Phase 4.7/Hoch) - kollidieren zwei Wettbewerbe auf demselben Feld zur selben Zeit, zeigt die Zelle beide Begegnungen gestapelt statt eine davon stillschweigend zu verlieren. Kein PDF-Export serverseitig (keine neue Abhaengigkeit) - "Drucken/Als PDF speichern" nutzt den Browser-Druckdialog. Live gegen echte Veranstaltungsdaten mit 22 Zeilen/Ort geprueft (Layout, keine Konsolenfehler); die tatsaechliche Ein-Seiten-Passform haengt vom Drucker/PDF-Renderer ab und sollte einmal probeweise ausgedruckt werden

## Phase 8 – Alltagsmodus für Vereins-/Freizeitturniere (Badminton u.a.)

Idee (2026-07-07, Kollegen-Wunsch nach dem ersten Live-Test): das Tool auch außerhalb des Sportfests für
spontane Turniere (z.B. Feierabend-Badminton) nutzen, bei denen es keine festen Schulklassen als Teams gibt,
sondern einzelne Personen, die sich erst am Tag selbst zu Teams (Doppel) zusammenfinden. Noch nicht
konzipiert, nur erste Ideen:

[ ] Teilnehmer als Einzelpersonen erfassen (statt nur Teams/Klassen mit `jahrgang`)
[ ] Ad-hoc-Teambildung: einzelne Personen manuell oder per Zufallslosung zu Doppel-/Team-Paarungen
    zusammenstellen, bevor der Spielplan generiert wird
[ ] Bestehende Turnier-/Gruppenphasen-Logik (Rundenverfahren, KO-Baum) für solche Ad-hoc-Teams
    wiederverwenden, statt eine komplett neue Planungslogik zu bauen
[ ] Prüfen, ob `jahrgang` als Pflichtfeld für Teams das eigentliche Hindernis ist, oder ob ein eigener
    "freier" Team-Modus (ohne Jahrgangsbindung) einfacher ist

## Langfristig

[ ] Stations-PIN
[ ] Benutzerverwaltung
[ ] Rechteverwaltung per Checkbox

## Bugs/UX

[x] Dashboard-Tagesplan tabellarisch anzeigen
[ ] Sechskampf: Ganzzahlen nicht als Dezimalzahl anzeigen
[ ] Sechskampf: Speicher-Uhrzeit korrigieren
[x] Dashboard: redundante Status-Badges entfernt (Tagesplan-Panel-Kopf, „Beginnt als Nächstes“-Panel-Kopf) - der Status stand dort zusätzlich zum ohnehin pro Zeitblock/Spiel angezeigten Badge (2026-07-04)
[x] Sechskampf: Klick auf einen Wettbewerb im Tagesplan zeigt jetzt eine Stationsrotation je Klasse statt nur des generischen „kein Zeitplan“-Hinweises - 1. Klasse startet an Station 1, 2. an Station 2 usw., rotierend je Runde; bei mehr Klassen als Stationen pausieren die überzähligen reihum (`calculate_sixkampf_station_rotation()`, 2026-07-04)
[x] Sechskampf-Tabelle auf `/tabellen`: Rang-Spalte zeigte `loop.index` (reine Zeilennummer) statt des bereits korrekt berechneten `r.placement`-Felds - ein echter Punktegleichstand erschien dadurch faelschlich als Platz 2 vs. 3 statt gleichauf. In der Simulation Juli 2026 gefunden (zwei Teams mit identischen Werten in allen Disziplinen) und sofort behoben; live nachgeprueft (2026-07-04)
[ ] Sechskampf-Stationsrotation: Bei deutlich mehr Klassen als Stationen (z.B. 7 Klassen/4 Stationen) pausieren die ueberzaehligen Klassen zwar korrekt reihum, aber mehrere Runden am Stueck hintereinander statt ueber den Tag verteilt - in der Simulation Juli 2026 beobachtet, noch nicht behoben