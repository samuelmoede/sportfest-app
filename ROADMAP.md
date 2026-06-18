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
* [ ] README.md ergänzen
* [ ] PROJECT_CONTEXT.md ergänzen
* [ ] CHANGELOG.md regelmäßig pflegen
* [ ] Backup-Konzept für SQLite-Datenbank dokumentieren
* [ ] später: Zugriffsschutz / Admin-Login
* [ ] später: CSRF-Schutz
* [ ] später: Tests und CI ergänzen

---

## 2. Veranstaltungen als zentrale Ebene

Ziel: Veranstaltungen werden die oberste Arbeitsebene.

* [x] Veranstaltungsseite vorhanden
* [ ] Navigation langfristig vereinfachen
* [ ] Seitenleistenpunkt „Wettbewerbe“ später entfernen oder nur noch als Unterseite der Veranstaltungen verwenden
* [ ] Veranstaltung anlegen
* [ ] Veranstaltung bearbeiten
* [ ] Veranstaltung duplizieren
* [ ] Veranstaltung archivieren
* [ ] Veranstaltung löschen, nur wenn keine abhängigen Daten vorhanden sind
* [ ] Detailseite einer Veranstaltung verbessern
* [ ] Wettbewerbe direkt innerhalb einer Veranstaltung anlegen
* [ ] Veranstaltung als Einstiegspunkt für Zeitplan, Ergebnisse und Auswertung verwenden

Zielbild:

1. Veranstaltung anlegen, z. B. „Bewegungsfest 2026“
2. Innerhalb der Veranstaltung Wettbewerbe erstellen:

   * Sechskampf Jahrgang 7
   * Fußball Jahrgang 7
   * Zweifelderball Jahrgang 7
3. Veranstaltung oder Wettbewerbsstruktur für weitere Jahrgänge duplizieren

---

## 3. Wettbewerbe / Disziplinen

Status: teilweise umgesetzt

* [x] Wettbewerb anlegen
* [x] Wettbewerb bearbeiten
* [x] Wettbewerb duplizieren
* [x] Wettbewerb zurücksetzen
* [x] Wettbewerb archivieren
* [x] Wettbewerb löschen
* [ ] Wettbewerbe nur noch über Veranstaltungen erreichbar machen
* [ ] Startzeit und Endzeit pro Wettbewerb ergänzen
* [ ] Wettbewerbstyp sauber unterscheiden:

  * Turnier
  * Sechskampf
  * Lauf / Einzelwettbewerb später optional
* [ ] Wettbewerbe mit Zeitfenster in Veranstaltungszeitplan einbauen
* [ ] Wettbewerbe nach Jahrgang und Veranstaltung gruppieren
* [ ] Wettbewerbe in Veranstaltungen kopieren

Wichtig:

* Turniere benötigen Spielplan, Felder, Teams, Ergebnisse und Tabellen.
* Sechskampf ist ein Stationslauf und benötigt keinen Spielplan mit Paarungen.
* Sechskampf benötigt Disziplinen, Werte je Klasse und Auswertung.

---

## 4. Teams / Klassenverwaltung

Ziel: Klassen robust, kompakt und sicher verwalten.

* [ ] Teamansicht von Kartenlayout auf Tabellenlayout umstellen
* [ ] Teams zeilenweise anzeigen
* [ ] Teamname bearbeiten
* [ ] Jahrgang bearbeiten
* [ ] Team aktiv/inaktiv setzen
* [ ] Löschen nur erlauben, wenn keine abhängigen Daten existieren
* [ ] Bei vorhandenen Ergebnissen nicht löschen, sondern deaktivieren
* [ ] Konsistenz sicherstellen, wenn Teams bereits in Slots, Ergebnissen oder Sechskampfwerten verwendet wurden
* [ ] optional: Jahrgangsgruppen besser abbilden, z. B. GOST

Regel:

Wenn ein Team bereits in Spielen, Ergebnissen oder Sechskampfwerten verwendet wurde, darf es nicht hart gelöscht werden.

---

## 5. Spielplan-Generator

Status: teilweise umgesetzt

* [x] Spielplanvorschläge erzeugen
* [x] Gleichzeitige Einsätze derselben Klasse verhindern
* [x] Validierung der Vorschau ergänzen
* [ ] Generator für beliebige Jahrgangsstärken robuster machen
* [ ] exakt gewünschte Anzahl Spiele pro Team garantieren
* [ ] Pausen zwischen Spielen berücksichtigen
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

- [ ] Sechskampf-Erfassung auf /ergebnisse integrieren
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
* [ ] Wettbewerbsergebnisse in Punkte umrechnen
* [ ] Sechskampf in Gesamtwertung einbeziehen
* [ ] Turniere in Gesamtwertung einbeziehen
* [ ] Gesamtwertung pro Jahrgang
* [ ] Gesamtwertung über alle Jahrgänge optional
* [ ] Gleichstände behandeln
* [ ] Beameransicht für Gesamtwertung
* [ ] Export später optional

---

## 12. Responsive Design

* [ ] Smartphone-Bedienung verbessern
* [ ] Tablet-Bedienung verbessern
* [ ] Navigation mobil einklappbar machen
* [ ] Ergebniseingabe mobil optimieren
* [ ] Sechskampf-Eingabe tabletfreundlich gestalten
* [ ] Spielplan mobil besser lesbar machen
* [ ] Drag & Drop touchfähig machen

---

## 13. Betrieb und Sicherheit später

Nicht sofort, aber vor echter produktiver Nutzung außerhalb eines kleinen internen Kreises:

* [ ] Admin-Login
* [ ] Rollen:

  * Admin
  * Helfer
  * Schiedsrichter
  * Anzeige/Beamer
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

1. Diese Roadmap als `ROADMAP.md` im Repository speichern.
2. Eine `PROJECT_CONTEXT.md` ergänzen.
3. Sechskampf-Erfassung auf `/ergebnisse` fertigstellen.
4. Teams/Klassenverwaltung auf Tabellenlayout umstellen.
5. Zeitfenster für Wettbewerbe ergänzen.
6. Zeitplan-Modul vorbereiten.
