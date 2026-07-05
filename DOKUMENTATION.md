# Dokumentation Sportfest-App

## Ziel der Anwendung

Die Sportfest-App verwaltet schulische Sportveranstaltungen auf einer gemeinsamen Oberfläche.
Im Mittelpunkt stehen Veranstaltungen, Wettbewerbe, Teams, Spielfelder, Spielpläne, Ergebnisse und Auswertungen.

Die Anwendung ist für zwei Hauptfälle ausgelegt:

- Eine Veranstaltung besteht aus genau einem Wettbewerb, zum Beispiel einem einzelnen Fußballturnier.
- Eine Veranstaltung bündelt mehrere Wettbewerbe, zum Beispiel ein Bewegungsfest mit Turnieren und Sechskampf.

## Grundprinzip

Die Struktur der App ist derzeit wie folgt aufgebaut:

1. Eine Veranstaltung bildet den organisatorischen Rahmen.
2. Innerhalb einer Veranstaltung liegen ein oder mehrere Wettbewerbe.
3. Wettbewerbe sind entweder Turniere oder Sechskampf.
4. Teams, Spielfelder, Zeitfenster, Ergebnisse und Auswertungen hängen an den Wettbewerben.

## Veranstaltungstypen

Jede Veranstaltung besitzt einen Veranstaltungstyp. Der Typ ist zunächst eine vorbereitende Einordnung und steuert noch nicht die komplette Fachlogik.

Aktuell gibt es diese Typen:

- `Bewegungsfest`
- `Einzelturnier`
- `Käthelauf`
- `Sonstiges`

### Bedeutung der Veranstaltungstypen

`Bewegungsfest`

- Gedacht für Veranstaltungen mit mehreren Wettbewerben.
- Typischerweise Kombinationen aus Turnieren und Sechskampf.
- Beispiel: Fußball, Volleyball und Sechskampf innerhalb eines gemeinsamen Tages.

`Einzelturnier`

- Gedacht für Veranstaltungen mit genau einem zentralen Turnier oder wenigen sehr nah verwandten Turnieren.
- Auf der Veranstaltungsdetailseite erscheint dafür bereits ein vorbereitender Schnellstart-Link: `Schnellstart: Turnier hinzufügen`.
- Der Link führt aktuell zur bestehenden Wettbewerbsanlage und wählt die Veranstaltung vor, wenn sie über die Detailseite geöffnet wurde.

`Käthelauf`

- Reserviert für eine spätere, eigene Lauf-Logik.
- Der Typ kann bereits ausgewählt und angezeigt werden.
- Es gibt aktuell noch keine spezielle Käthelauf-Auswertung, keine eigene Ergebnislogik und keine gesonderten Schnellaktionen.

`Sonstiges`

- Neutraler Auffangtyp für Veranstaltungen, die noch keiner spezialisierten Kategorie zugeordnet sind.
- Bestehende Veranstaltungen ohne gepflegten Typ werden beim Start der App minimal auf `Sonstiges` oder, bei klaren Hinweisen, auf `Bewegungsfest` gesetzt.

## Bestehende Funktionen

## 1. Veranstaltungen

Die Veranstaltungsverwaltung ist die oberste organisatorische Ebene.

Vorhanden sind derzeit:

- Veranstaltungen anlegen
- Veranstaltungen bearbeiten
- Veranstaltungen archivieren und wiederherstellen
- Veranstaltungen duplizieren
- Veranstaltungen löschen, sofern keine Wettbewerbe daran hängen
- Veranstaltungsdetailseite mit zugeordneten Wettbewerben
- Anzeige des Veranstaltungstyps auf Übersicht und Detailseite
- Tagesplan pro Veranstaltung
- Gesamtwertung auf Veranstaltungsdetailseite

## 2. Wettbewerbe

Wettbewerbe sind die eigentlichen fachlichen Einheiten innerhalb einer Veranstaltung.

Vorhanden sind derzeit:

- Wettbewerbe anlegen
- Wettbewerbe bearbeiten
- Wettbewerbe duplizieren
- Wettbewerbe archivieren
- Wettbewerbe löschen
- Wettbewerbe einer Veranstaltung zuordnen
- Startzeit und Endzeit pflegen
- Wettbewerbstyp unterscheiden zwischen `Turnier` und `Sechskampf`

## 3. Turnierfunktionen

Für Wettbewerbe vom Typ `Turnier` gibt es bereits eine breite Funktionsbasis.

Vorhanden sind derzeit:

- Teams einem Jahrgang zuordnen
- Spielplan erzeugen
- Spielplan manuell bearbeiten
- Spielfelder verwalten
- Spiele starten und beenden
- Ergebnisse erfassen
- Tabellen und Platzierungen berechnen
- Beamer- und Übersichtsseiten nutzen

Nicht Teil der neuen Veranstaltungstypen-Änderung:

- Die bestehende Spielplanlogik bleibt unverändert.
- Die Turnierauswertung wurde durch den Veranstaltungstyp nicht verändert.

## 4. Sechskampf

Für Wettbewerbe vom Typ `Sechskampf` gibt es bereits eine eigene Struktur.

Vorhanden sind derzeit:

- Disziplinen pro Wettbewerb pflegen
- Werte klassenweise erfassen
- Mehrere Werte pro Team und Disziplin unterstützen
- Ranglisten und Wertungspunkte berechnen
- Ergebnisse in die bestehende Veranstaltungsdarstellung einbinden
- Stationsrotation je Klasse anzeigen (1. Klasse startet an Station 1, 2. an Station 2 usw., rotierend je Runde; bei mehr Klassen als Stationen pausieren die überzähligen reihum)

Wichtig:

- Sechskampf ist kein Turnier mit Paarungen.
- Sechskampf verwendet keine Spielplanlogik wie ein normales Turnier.
- Die neue Einführung von Veranstaltungstypen ändert diese Fachlogik nicht.

## 5. Teams und Spielfelder

Vorhanden sind derzeit:

- Teams oder Klassen anlegen und bearbeiten
- Teams nach Jahrgang verwalten
- Teams deaktivieren statt problematisch löschen
- Spielfelder anlegen und verwalten
- Spielfelder in Zeitplänen und Spielen verwenden

## 6. Ergebnisse und Auswertungen

Vorhanden sind derzeit:

- Turnierergebnisse erfassen
- Sechskampfwerte erfassen
- Tabellen pro Wettbewerb anzeigen
- Veranstaltungsbezogene Gesamtwertung auf der Detailseite anzeigen
- Gesamtwertung nach Jahrgängen gruppiert darstellen
- Hinweis in Tabellen, wodurch ein Punktgleichstand aufgelöst wurde (Tordifferenz, Tore, Mini-Tabelle unter den punktgleichen Teams oder ein weiterhin unaufgelöster/zyklischer Gleichstand)
- Warnung vor dem letzten offenen Gruppenspiel, wenn bestimmte Ergebnisse zu einem unauflösbaren Gleichstand führen würden

Aktueller Stand:

- Die bestehende Gesamtwertung ist vorhanden.
- Die Einführung von Veranstaltungstypen ändert die Gesamtwertung derzeit nicht.
- Für `Käthelauf` gibt es noch keine eigene Gesamtwertungslogik.

## 7. Tagesplan und Beamer

Vorhanden sind derzeit:

- Tagesplan auf Veranstaltungsdetailseite
- Dashboard mit nächster Veranstaltung und weiteren kommenden Veranstaltungen
- Beameransicht für laufende Wettbewerbe
- Spielplan-Aushang zum Ausdrucken je Ort (Turnhalle, Fußballplatz) als kompaktes Zeit-x-Feld-Raster auf A4, über den Browser-Druckdialog als PDF speicherbar
- global konfigurierbares Aktualisierungsintervall für den Beamer

## Typische Nutzung

Ein üblicher Arbeitsablauf sieht aktuell so aus:

1. Veranstaltung anlegen.
2. Veranstaltungstyp auswählen.
3. Wettbewerbe der Veranstaltung hinzufügen.
4. Teams und Spielfelder vorbereiten.
5. Spielpläne oder Sechskampf-Disziplinen pflegen.
6. Ergebnisse erfassen.
7. Tagesplan, Tabellen und Gesamtwertung nutzen.

## Technischer Überblick

- Backend: FastAPI
- Templates: Jinja2
- Datenbank: SQLite in `data/sportfest.db`
- Hauptlogik: `app/main.py`
- Datenbankinitialisierung: `app/database.py`
- Statische Assets: `app/static/`
- Templates: `app/templates/`

## Aktuelle Grenzen

Diese Punkte sind bewusst noch nicht oder nur vorbereitend umgesetzt:

- `Käthelauf` hat noch keine eigene Fachlogik.
- Veranstaltungstypen steuern noch nicht automatisch alle Module oder Schnellaktionen.
- Die Navigation ist noch nicht vollständig auf Veranstaltungen als alleinigen Einstieg umgestellt.
- Zugriffsschutz, Tests und CI fehlen weiterhin.

## Datenbankhinweis

Der Veranstaltungstyp wird in der Tabelle `events` als Feld `event_type` gespeichert.

Beim Start der Anwendung gilt:

- Fehlt die Spalte in einer älteren Datenbank, wird sie automatisch ergänzt.
- Fehlt bei bestehenden Veranstaltungen ein Wert, wird er minimal zurückgefüllt.
- Ohne klare Zuordnung wird `Sonstiges` verwendet.

## Einordnung der Dokumente

- `README.md` beschreibt Start, Betrieb und grundlegende Projektinfos.
- `DOKUMENTATION.md` beschreibt die fachlichen Funktionen und den aktuellen Anwendungsstand.
- `ROADMAP.md` beschreibt offene Entwicklungsschritte und Zielbilder.
- `CHANGELOG.md` dokumentiert umgesetzte Änderungen.