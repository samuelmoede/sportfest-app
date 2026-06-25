# Project Context

## Ziel der Sportfest-App

Die Sportfest-App soll Schulen ermöglichen, Sportveranstaltungen digital zu planen und zu verwalten.
Der Fokus liegt auf Wettbewerben wie Turnieren, Sechskämpfen und anderen schulischen Sportereignissen.

## Kernfunktionen

- Verwaltung von Wettbewerben (`competitions`) und Veranstaltungen (`events`)
- Team- und Feldverwaltung (`teams`, `courts`)
- Spielplanerstellung und Slot-Management (`slots`)
- Ergebnisaufnahme und Auswertung von Turnieren und Sechskampf
- Anzeige von Statistiken über Jinja2-Templates

## Fachlicher Kontext

### Veranstaltungen

Veranstaltungen sind die organisatorische Ober-Ebene der App. Eine Veranstaltung
kann genau einen Wettbewerb enthalten, zum Beispiel ein einzelnes Turnier, oder
mehrere Wettbewerbe buendeln, zum Beispiel ein Bewegungsfest mit Turnieren und
Sechskampf.

Aktuelle Veranstaltungstypen:

- `Bewegungsfest`
- `Einzelturnier`
- `Käthelauf`
- `Sonstiges`

Veranstaltungen koennen angelegt, bearbeitet, archiviert, dupliziert und auf
Detailseiten mit Tagesplan, zugeordneten Wettbewerben und Gesamtwertung
angezeigt werden.

### Wettbewerbe

Wettbewerbe sind die fachlichen Einheiten innerhalb einer Veranstaltung. Sie
besitzen unter anderem Name, Sportart, Jahrgang, Status, Start-/Endzeit,
Wertungseinstellungen, Ort und einen Typ.

Wichtige Wettbewerbtypen:

- `Turnier`
- `Sechskampf`

Die Veranstaltungshierarchie ist fuer spaetere Refactorings wichtig: Routen und
Templates duerfen Wettbewerbe nicht isoliert betrachten, wenn die Darstellung
oder Auswertung an der uebergeordneten Veranstaltung haengt.

### Turniere

Turniere arbeiten mit Teams, Spielfeldern, Slots, Spielzeiten, Wechselzeiten,
Ergebnissen und Tabellen. Die Planung kann automatisch erzeugt und danach in
der Spielplanbearbeitung angepasst werden.

Turnierlogik umfasst insbesondere:

- Gruppenspiele und KO-Runden
- Tabellenberechnung mit Punkten, Tordifferenz, Toren und Direktvergleich
- Spielstatus wie `geplant`, `läuft` und `beendet`
- Spielplan- und Ergebnisansichten fuer Viewer, Referee und Admin

### Sechskampf

Sechskampf ist kein Turnier mit Paarungen und soll nicht mit der normalen
Spielplanlogik vermischt werden. Sechskampf-Wettbewerbe bestehen aus
Disziplinen bzw. Stationen, erfassen Werte pro Team/Klasse und berechnen daraus
Ranglisten sowie Wertungspunkte fuer die Gesamtwertung.

Wichtig fuer spaetere Refactorings:

- Disziplinen gehoeren zu einem Sechskampf-Wettbewerb.
- Teams/Klassen werden nach Jahrgang bewertet.
- Nicht eingetragene Ergebnisse duerfen nicht wie echte Nullergebnisse wirken.
- Sechskampf-Programmpunkte koennen im Tagesplan erscheinen, bleiben aber fachlich von Turnierspielen getrennt.

## Orte

Wettbewerbe verwenden derzeit eine grobe Ortsangabe. Konkrete Spielfelder oder
Unterbereiche werden vor allem in der Spielplanbearbeitung relevant.

### Turnhalle

- Ort fuer klassische Hallenturniere.
- Typische Spalten/Felder: Feld 1, Feld 2, Feld 3.
- Spielplanlogik mit Slots und parallelen Feldern ist hier zentral.

### Fußballplatz

- Ort fuer Fussballturniere oder andere Aussensportarten mit Platzlogik.
- Geplante bzw. genutzte Unterbereiche: Rasenplatz und Tartanplatz.
- Der oeffentliche Spielplan soll Fussballplatz-Wettbewerbe getrennt von Hallenwettbewerben darstellen.

### Außenbereich

- Ort fuer Wettbewerbe oder Programmpunkte ohne klassische Feld-/Slotplanung.
- Darstellung erfolgt eher chronologisch nach Start- und Endzeit.
- Nicht automatisch wie ein Hallenturnier behandeln.

## Rollenmodell

Die Sicherheit ist vorbereitet und standardmaessig deaktiviert. Ohne aktive
Sicherheit bleiben bestehende Seiten offen. Bei aktiver Sicherheit schuetzt eine
zentrale Middleware Verwaltungsbereiche.

Aktuelle und vorbereitete Rollen:

- `Viewer`: Standardrolle fuer nicht angemeldete Nutzer; darf oeffentliche Ansichten wie Dashboard, Spielplan, Tabellen und Beamer sehen.
- `Referee`: Helfer-/Schiedsrichterrolle fuer Ergebnis- und Spielstatus-nahe Nutzung; Ergebnis- und Spielplanaktionen bleiben in der aktuellen Phase bewusst offen.
- `Admin`: Verwaltungsrolle fuer Einstellungen, Teams, Spielfelder, Wettbewerbe und sicherheitsrelevante Aktionen.
- `Stationshelfer`: zukuenftige Rolle fuer Sechskampf-Stationen; technisch als Idee vorbereitet, aber noch ohne Anmeldung, PIN, Stationsrechte oder eigene Ergebnisrechte.
## Architektur

- `app/main.py` enthält die FastAPI-Anwendung sowie die Routing- und Geschäftslogik.
- `app/database.py` initialisiert die SQLite-Datenbank und stellt `get_conn()` zur Verfügung.
- `app/seed.py` legt Beispiel-Daten für Teams, Courts, Wettbewerbe und Slots an.
- `app/templates/` enthält die HTML-Templates für die Weboberfläche.
- `app/static/` enthält statische Assets wie CSS.
- `data/sportfest.db` ist der persistente SQLite-Datenbankdateipfad.
- `VERSION` enthält die App-Version.

## Datenmodell

- `events`: Veranstaltungen, die mehrere Wettbewerbe gruppieren können.
- `competitions`: Wettbewerbe mit Attributen wie `name`, `sportart`, `jahrgang`, `status`, `competition_type`.
- `competition_disciplines`: Disziplinen für Sechskampf-Wettbewerbe.
- `teams`: Teams oder Klassen mit `jahrgang` und Aktivitätsstatus.
- `courts`: Sportstätten für Spielpläne.
- `slots`: Zeitplan-Einträge für Spiele, Pausen und Ereignisse.
- `sixkampf_participants`, `discipline_results`, `sixkampf_team_results`: Tabellen zur Sechskampf-Erfassung und Auswertung.

## Laufzeit und Deployment

- Lokal: `python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8500`
- Docker: `docker compose up --build`
- Die Anwendung verwendet relative Pfade zum Repository-Wurzelverzeichnis für statische Inhalte, Templates und `VERSION`.
- Die Datenbank wird lokal in `data/sportfest.db` angelegt.

## Bekannte Einschränkungen

- Eine kleine, standardmäßig deaktivierte Login- und Session-Grundlage ist vorhanden.
- `security_enabled` ist standardmäßig `false`; bestehende Seiten und Aktionen bleiben dann vollständig offen.
- Eine zentrale Middleware schützt bei aktiver Sicherheit die Admin-Bereiche Einstellungen, Teams, Spielfelder und Wettbewerbe; Ergebnis- und Spielplanaktionen bleiben bewusst offen.
- Kein CSRF-Schutz
- Tests und CI fehlen noch
- Die Veranstaltungshierarchie ist geplant, aber nicht vollständig als zentrale Navigation implementiert
- `PROJECT_CONTEXT.md` ist neu ergänzt worden und beschreibt den aktuellen Projektaufbau
