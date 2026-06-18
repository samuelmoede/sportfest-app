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

- Kein Benutzer-Login / kein Zugriffsschutz
- Kein CSRF-Schutz
- Tests und CI fehlen noch
- Die Veranstaltungshierarchie ist geplant, aber nicht vollständig als zentrale Navigation implementiert
- `PROJECT_CONTEXT.md` ist neu ergänzt worden und beschreibt den aktuellen Projektaufbau
