# Roadmap Sportfest-App

## Leitbild

Die Sportfest-App soll langfristig nicht nur einzelne Turniere verwalten, sondern ganze schulische Sportveranstaltungen abbilden.

Eine Veranstaltung kann aus einem einzelnen Wettbewerb bestehen, zum Beispiel einem Schulpokal, oder aus mehreren Wettbewerben, zum Beispiel einem Bewegungsfest mit Sechskampf, Fußball, Volleyball und Zweifelderball.

## Grundstruktur

Veranstaltung
→ Wettbewerbe / Disziplinen
→ Ergebnisse
→ Auswertungen
→ Beamer-/Zeitplanansichten

Beispiele:

* Bewegungsfest 2026

  * Sechskampf Jahrgang 7
  * Zweifelderball Jahrgang 7
  * Fußball Jahrgang 7
  * Sechskampf Jahrgang 8
  * Volleyball Jahrgang 8
  * Fußball Jahrgang 8

* Schulpokal

  * Fußballturnier Jahrgang 7
  * Fußballturnier Jahrgang 8

* Käthelauf

  * Laufwertung Jahrgang 7
  * Laufwertung Jahrgang 8

---

## 1. Infrastruktur und Entwicklung

Status: weitgehend erledigt

* [x] Git lokal eingerichtet
* [x] GitHub-Repository eingerichtet
* [x] Live-Reload aktiviert
* [x] Versionsanzeige in der App
* [x] `.gitignore` eingerichtet
* [x] Codex mit GitHub verbunden
* [x] README.md ergänzen
* [x] PROJECT_CONTEXT.md ergänzen
* [x] CHANGELOG.md regelmäßig pflegen
* [x] Backup-Konzept für SQLite-Datenbank dokumentieren
* [x] Einstellungen-Seite mit globalen Systeminfos erweitert (Version, DB-Groesse, Kennzahlen)
* [x] Manuelle Backup-Funktion auf /einstellungen inkl. Backup-Liste umgesetzt
* [x] Globales Beamer-/Live-Aktualisierungsintervall (settings key/value, Standard 30s) umgesetzt
* [x] Einstellungen als zentrale Verwaltungsseite ausgebaut (Systemstatus, Backup-Uebersicht, kompakte Info-Karten)
* [x] optionale Grundlage für Zugriffsschutz / Admin-Login vorbereitet (standardmäßig deaktiviert)
* [ ] später: CSRF-Schutz
* [ ] später: Tests und CI ergänzen
* Hinweis: App verwendet jetzt relative Pfade für Templates, statische Assets und `VERSION`.
* Hinweis: Erste Changelog-Einträge dokumentieren Projektstatus und implementierte Infrastrukturänderungen.
* Hinweis: Backup über /einstellungen wird in `backups/` mit Zeitstempel gespeichert.

---

## 2. Veranstaltungen als zentrale Ebene

Ziel: Veranstaltungen werden die oberste Arbeitsebene.

* [x] Veranstaltungsseite vorhanden
* [x] Einfachen Veranstaltungstyp vorbereitend eingeführt (Bewegungsfest, Einzelturnier, Käthelauf, Sonstiges)
* [ ] Navigation langfristig vereinfachen
* [ ] Seitenleistenpunkt „Wettbewerbe“ später entfernen oder nur noch als Unterseite der Veranstaltungen verwenden
* [x] Veranstaltung anlegen
* [x] Veranstaltung bearbeiten
* [x] Veranstaltung duplizieren
* [x] Veranstaltung archivieren
* [x] Veranstaltung löschen, nur wenn keine abhängigen Daten vorhanden sind
* [x] Detailseite einer Veranstaltung verbessern
* [x] Einfachen Veranstaltungsplan mit Wettbewerbszeiten und Sechskampf-Stationen anzeigen
* [x] Gesamtwertung auf der Veranstaltungsdetailseite nach Jahrgängen gruppieren
* [~] Wettbewerbe aus der Veranstaltung heraus anlegen (aktuell per vorgefiltertem Link)
* [~] Veranstaltung als Einstiegspunkt für Zeitplan, Ergebnisse und Auswertung verwenden (Wettbewerbsliste auf Detailseite mit Direktaktionen umgesetzt)
* [x] Erste einfache Gesamtwertung pro Veranstaltung anzeigen

Zielbild:

1. Veranstaltung anlegen, z. B. „Bewegungsfest 2026“
2. Innerhalb der Veranstaltung Wettbewerbe erstellen:

   * Sechskampf Jahrgang 7
   * Fußball Jahrgang 7
   * Zweifelderball Jahrgang 7
3. Veranstaltung oder Wettbewerbsstruktur für weitere Jahrgänge duplizieren

Vorbereitung umgesetzt:

* Veranstaltungstyp kann bereits an Veranstaltungen gepflegt und angezeigt werden.
* Veranstaltungsdetailseite zeigt zugeordnete Wettbewerbe kompakt mit Direktaktionen sowie einen zeitlich sortierten Veranstaltungsplan; Sechskampf erscheint dort als Stationsliste statt als Spielfeldplan.
* Noch keine eigene Käthelauf-Logik, keine Einzelturnier-Speziallogik und keine Schnellanlage; außerdem keine Änderung an Gesamtwertung, Sechskampf, Ergebnissen oder Spielplan.

---

## 3. Wettbewerbe / Disziplinen

Status: teilweise umgesetzt

* [x] Wettbewerb anlegen
* [x] Wettbewerb bearbeiten
* [x] Wettbewerb duplizieren (als Kopie mit Disziplinenübernahme)
* [x] Wettbewerb zurücksetzen
* [x] Wettbewerb archivieren
* [x] Wettbewerb löschen
* [ ] Wettbewerbe nur noch über Veranstaltungen erreichbar machen
* [x] Startzeit und Endzeit pro Wettbewerb ergänzen
* [x] Optionalen Veranstaltungsort pro Wettbewerb ergänzen, slotlose Wettbewerbe nach Ort anzeigen und Spielplan nach Ort filtern
* [x] Grobes Ortsmodell am Wettbewerb: Turnhalle, Fußballplatz oder Außenbereich; Felder und Unterbereiche werden erst in der Spielplanbearbeitung zugeordnet
* [x] Anlageformular auf `/wettbewerbe` platzsparend aufklappbar gemacht
* [ ] Wettbewerbstyp sauber unterscheiden:

  * Turnier
  * Sechskampf
  * Lauf / Einzelwettbewerb später optional
* [x] Wettbewerbe mit Zeitfenster in Veranstaltungszeitplan einbauen
* [x] Tabellenansicht mit kombinierbaren Filtern fuer Veranstaltung, Jahrgang und Wettbewerb erweitert
* [ ] Wettbewerbe nach Jahrgang und Veranstaltung gruppieren
* [ ] Wettbewerbe in Veranstaltungen kopieren

Wichtig:

* Turniere benötigen Spielplan, Felder, Teams, Ergebnisse und Tabellen.
* Sechskampf ist ein Stationslauf und benötigt keinen Spielplan mit Paarungen.
* Sechskampf benötigt Disziplinen, Werte je Klasse und Auswertung.

---

## 4. Teams / Klassenverwaltung

Ziel: Klassen robust, kompakt und sicher verwalten.


* [x] Teamansicht von Kartenlayout auf Tabellenlayout umstellen
* [x] Teams zeilenweise anzeigen
* [x] Teamname bearbeiten
* [x] Jahrgang bearbeiten
* [x] Team aktiv/inaktiv setzen
* [x] Löschen nur erlauben, wenn keine abhängigen Daten existieren
* [x] Bei vorhandenen Ergebnissen nicht löschen, sondern deaktivieren
* [x] Konsistenz sicherstellen, wenn Teams bereits in Slots, Ergebnissen oder Sechskampfwerten verwendet wurden
* [ ] optional: Jahrgangsgruppen besser abbilden, z. B. GOST

Regel:

Wenn ein Team bereits in Spielen, Ergebnissen oder Sechskampfwerten verwendet wurde, darf es nicht hart gelöscht werden.

---

## 5. Spielplan-Generator

Status: teilweise umgesetzt

* [x] Spielplanvorschläge erzeugen
* [x] Gleichzeitige Einsätze derselben Klasse verhindern
* [x] Validierung der Vorschau ergänzen
* [x] Generator auf echte Spiel-Slots ohne automatische Leer-/Puffer-Slots begrenzen
* [ ] Generator für beliebige Jahrgangsstärken robuster machen
* [ ] exakt gewünschte Anzahl Spiele pro Team garantieren
* [x] Modul „Spielzeit und Wechselzeit“:

  * [x] Spielzeit pro Turnier einstellbar
  * [x] Wechselzeit pro Turnier einstellbar
  * [x] Startabstand aus Spielzeit plus Wechselzeit berechnen
  * [x] Schiedsrichter-Timer auf die reine Spielzeit begrenzen
  * [ ] optionale Pufferzeiten später ergänzen
* [ ] ungerade Teamzahlen sauber behandeln
* [ ] Gruppengrößen flexibel machen
* [ ] KO-Runden nur bei gültiger Konfiguration erzeugen
* [ ] doppelte Anwendung desselben Vorschlags verhindern
* [ ] Warnbericht vor dem Übernehmen verbessern
* [ ] Generator für Turniere klar von Sechskampf trennen

Wichtig:

Sechskampf darf nicht durch den Spielplan-Generator laufen, weil dort keine Klassen gegeneinander antreten.

---

## 6. Spielplan bearbeiten

Status: weitgehend funktionsfähig

* [x] Slots manuell anlegen
* [x] Slots bearbeiten
* [x] Slots kopieren
* [x] Slots löschen
* [x] mehrere Slots löschen
* [x] Drag & Drop
* [ ] Drag & Drop auch mobil/touchfähig machen
* [ ] Reihenfolge langfristig robuster speichern
* [ ] Zeitverschiebung mehrerer Slots ermöglichen
* [ ] markierte Slots um X Minuten verschieben
* [ ] markierte Slots Phase ändern
* [ ] markierte Slots Feld ändern
* [ ] bessere Filter nach Veranstaltung und Wettbewerb

---

## 7. Ergebniserfassung für Turniere

Status: funktionsfähig

* [x] Spiele starten
* [x] Ergebnis zwischenspeichern
* [x] Spiel beenden
* [x] beendete Spiele archivieren
* [x] Ergebnis löschen / korrigieren
* [x] Start rückgängig machen
* [ ] Schiedsrichter-Timer ergänzen
* [ ] Spiel pausieren
* [ ] Spiel fortsetzen
* [ ] laufende Zeit pro Spiel anzeigen
* [ ] Timer in Beameransicht anzeigen
* [ ] Schiedsrichteransicht für Handy ergänzen

### Schiedsrichter-Uhr

- [ ] Spielzeit pro Wettbewerb konfigurierbar
- [ ] Start / Pause / Fortsetzen
- [ ] Restzeit anzeigen
- [ ] Ablaufsignal bei Spielende
- [ ] Schiedsrichter-Handyansicht
- [ ] Beameransicht mit laufender Uhr
- [ ] Mehrere gleichzeitig laufende Spiele unterstützen
---

## 8. Sechskampf-Modul

Status: im Aufbau

Ziel:

Der Sechskampf ist kein Turnier, sondern ein Stationslauf. Es werden keine Paarungen erzeugt. Es werden keine Schülernamen benötigt.

Grundprinzip:

* Wettbewerbstyp: Sechskampf
* Jahrgang auswählen
* Disziplinen anlegen
* Anzahl Werte pro Klasse und Disziplin festlegen
* Ergebnisse klassenweise erfassen
* Auswertung je Klasse erzeugen

### 8.1 Konfiguration

* [x] Disziplinen anlegen
* [x] Disziplinen bearbeiten
* [x] Reihenfolge speichern
* [ ] Anzahl Werte pro Disziplin speichern
* [ ] Einheit speichern
* [ ] Bewertungsrichtung speichern:

  * höher ist besser
  * niedriger ist besser
* [ ] sinnvolle Standarddisziplinen optional automatisch anlegen

Beispiele:

* Medizinballweitwurf: 10 Werte

* Standweitsprung: 15 Werte
* Torwandschießen: 5 Werte
* Tischtennis-Challenge: 5 Werte
* Wasserkrug-Schieben: 10 Werte
* Zielwerfen: 5 Werte

### 8.2 Ergebniseingabe

- [x] Sechskampf-Erfassung auf /ergebnisse integrieren
- [ ] Wenn Sechskampf ausgewählt ist: keine aktiven Spiele anzeigen
- [ ] Disziplin-Tabs anzeigen

### Automatische Klassenauswahl

- [ ] Bei Sechskampf automatisch alle Klassen des gewählten Jahrgangs laden
- [ ] Keine manuelle Auswahl von 7A, 7B, 7C usw.
- [ ] Neue Klassen automatisch berücksichtigen
- [ ] Klassen alphabetisch sortieren
- [ ] Eingabemasken automatisch erzeugen

### Eingabe

- [ ] Pro Klasse automatisch die konfigurierte Anzahl Werte anzeigen
- [ ] Werte je Klasse speichern
- [ ] Gesamtwert je Klasse live anzeigen
- [ ] Letzte Speicherung je Klasse mit Uhrzeit anzeigen
- [ ] Fehlgeschlagene Speicherung sichtbar machen

### Speicherung

- [ ] Speichern pro Klasse
- [ ] Letzte erfolgreiche Speicherung anzeigen
- [ ] Uhrzeit der letzten Speicherung direkt neben dem Speicherbutton anzeigen
- [ ] Keine störenden Popups verwenden
- [ ] Fehlgeschlagene Speicherung deutlich kennzeichnen
- [ ] Bei erneuter Speicherung Uhrzeit aktualisieren

Keine Teilnehmerverwaltung mit Namen.

### 8.3 Sechskampf-Auswertung

* [ ] Summe je Klasse und Disziplin
* [ ] Gesamtsumme über alle Disziplinen
* [ ] Rangliste der Klassen
* [ ] spätere Integration in Gesamtwertung

### Rangliste

- [ ] Rangliste automatisch nach Gesamtpunktzahl sortieren
- [ ] Höchste Punktzahl auf Platz 1
- [ ] Gleiche Punktzahlen erhalten denselben Rang
- [ ] Nach Änderung von Ergebnissen automatisch neu berechnen
- [ ] Platzierung in Beameransicht anzeigen

### Sechskampf-Wertungspunkte

- [x] Punkte für Platz 1 konfigurierbar machen
- [x] absteigende Punktevergabe je Platz
- [x] gleiche Platzierungen erhalten gleiche Punkte
- [x] Wertungspunkte in `/ergebnisse` und `/tabellen` anzeigen
- [x] Grundlage für spätere Gesamtwertung schaffen
---

## 9. Zeitplan-Modul

Ziel:

Eine schulweite Übersicht, welche Jahrgänge/Klassen zu welcher Zeit welchen Wettbewerb absolvieren.

Beispiel:

| Uhrzeit     | Klasse 7       | Klasse 8       | Klasse 9   | GOST       |
| ----------- | -------------- | -------------- | ---------- | ---------- |
| 08:00–09:00 | Sechskampf     | Zweifelderball | Fußball    | Pause      |
| 09:10–10:10 | Pause          | Sechskampf     | Volleyball | Fußball    |
| 10:20–11:20 | Fußball        | Pause          | Sechskampf | Volleyball |
| 11:30–12:30 | Zweifelderball | Fußball        | Pause      | Sechskampf |

Aufgaben:

* [ ] Start- und Endzeit pro Wettbewerb
* [ ] Zeitplanansicht für Veranstaltung
* [ ] Jahrgänge als Spalten
* [ ] Zeitfenster als Zeilen
* [ ] Pausen anzeigen
* [ ] öffentliche Ansicht für Klassenleitungen
* [ ] Beamer-/Infomodus für Zeitplan

---

## 10. Beamer-Ansicht

Status: vorhanden, aber ausbaufähig

* [x] Beameransicht vorhanden
* [ ] Veranstaltung auswählen
* [ ] Wettbewerb auswählen
* [ ] mehrere laufende Spiele gleichzeitig anzeigen
* [ ] Schiedsrichter-Timer anzeigen
* [ ] Sechskampf-Zwischenstände anzeigen
* [ ] Zeitplan anzeigen
* [ ] automatische Rotation zwischen Ansichten
* [ ] bessere Darstellung für große Monitore
* [ ] Auto-Refresh robuster machen

---

## 11. Gesamtwertung

Ziel:

Alle Wettbewerbe einer Veranstaltung fließen in eine Gesamtwertung ein.

Beispiel Bewegungsfest:

* Sechskampf
* Fußball
* Volleyball/Zweifelderball

Aufgaben:


* [ ] Wertungssystem definieren
* [x] Wettbewerbsergebnisse in Punkte umrechnen
* [x] Sechskampf in Gesamtwertung einbeziehen
* [x] Turniere in Gesamtwertung einbeziehen
* [x] Gesamtwertung pro Jahrgang
* [ ] Gesamtwertung über alle Jahrgänge optional
* [x] Gleichstände behandeln
* [ ] Beameransicht für Gesamtwertung
* [x] CSV-Export für Tabellen und gefilterte Gesamtwertungen auf `/tabellen`

---

## 12. Responsive Design

* [x] Smartphone-Bedienung verbessern
* [x] Tablet-Bedienung verbessern
* [ ] Navigation mobil einklappbar machen
* [x] Ergebniseingabe mobil optimieren
* [x] Sechskampf-Eingabe tabletfreundlich gestalten
* [x] Spielplan mobil besser lesbar machen
* [ ] Drag & Drop touchfähig machen

---

## 13. Betrieb und Sicherheit später

Nicht sofort, aber vor echter produktiver Nutzung außerhalb eines kleinen internen Kreises:

* [ ] Zugriffsschutz später sauber als eigenes Feature umsetzen
* [x] kurzfristig: Cloudflare Access / App-Login optional vorbereiten (App-Login-Grundlage vorhanden, Cloudflare bleibt extern)
* [~] mittelfristig: schreibende Aktionen schützen (Admin-Bereiche zentral geschützt; Ergebnis- und Spielplanaktionen bewusst noch offen)
* [x] Rollenmodell Phase 1: Rollen technisch vorbereitet
* [x] Nicht angemeldete Nutzung als viewer sowie Rechteerweiterung für Helfer und Admin vorbereitet
* [~] Rollenmodell Phase 2: referee/Helfer-Anmeldung vorhanden; Freigabe für Ergebniseingabe folgt später
* [ ] Rollenmodell Phase 3 später: Navigation rollenabhängig ausblenden
* [~] Admin-Login (Grundlage und zentraler Bereichsschutz vorhanden; Rollen und weitere Schreibbereiche noch offen)
* [x] Technisch vorbereitete Rollen: viewer, referee, admin
* [ ] CSRF-Schutz
* [ ] SQLite-Fremdschlüssel aktivieren
* [ ] WAL-Modus / Busy Timeout prüfen
* [ ] regelmäßige Datenbank-Backups
* [ ] Restore-Anleitung dokumentieren
* [ ] Container langfristig ohne Root betreiben
* [ ] Healthcheck ergänzen

---

## Arbeitsweise mit Codex

Grundregel:

1. Ein Feature beauftragen
2. Einen Pull Request erzeugen lassen
3. PR prüfen
4. Mergen
5. Lokal/Synology pullen
6. Testen
7. Erst danach nächstes Feature

Keine parallelen großen PRs, wenn dieselben Dateien betroffen sind.

---

## Nächste konkrete Schritte

1. Navigation stärker auf Veranstaltungen als zentrale Einstiegsebene ausrichten.
2. Wettbewerbe innerhalb einer Veranstaltung direkt erstellen/bearbeiten (ohne Seitenwechsel).
3. Zeitplan-Modul als Veranstaltungsansicht weiter ausbauen.
4. Beameransicht um Gesamtwertung und robustere Mehrfachansichten erweitern.
5. Vorbereiteten optionalen App-Login schrittweise auf schreibende Aktionen anwenden und CSRF-Schutz ergänzen.

