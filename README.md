# Sportfest-App

Die Sportfest-App ist eine FastAPI-Anwendung zur Verwaltung schulischer Sportveranstaltungen.
Sie unterstützt Wettbewerbe, Sechskampf, Spielpläne, Teams, Ergebnisse und grundlegende Auswertungen.

## Übersicht

- Backend: FastAPI
- Templates: Jinja2
- Datenbank: SQLite (`data/sportfest.db`)
- Version: aus der Datei `VERSION`
- Docker-optimiert, kann aber auch lokal ausgeführt werden

## Projektstruktur

- `app/`
  - `main.py` - FastAPI-App
  - `database.py` - SQLite-Initialisierung und Datenbankzugriff
  - `seed.py` - (optional) Datenvorbereitung
  - `static/` - statische Assets
  - `templates/` - HTML-Templates
- `data/` - persistente SQLite-Datenbank
- `VERSION` - App-Version
- `CHANGELOG.md` - Änderungsverlauf
- `ROADMAP.md` - Projektplan und offene Entwicklungspunkte

## Voraussetzungen

- Python 3.12+
- `fastapi`
- `uvicorn`
- `jinja2`
- `python-multipart`

## Lokal starten

1. Virtuelle Umgebung erstellen:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

2. Abhängigkeiten installieren:

   ```powershell
   pip install fastapi uvicorn jinja2 python-multipart
   ```

3. Die App starten:

   ```powershell
   python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8500
   ```

4. Im Browser öffnen:

   ```text
   http://localhost:8500
   ```

## Mit Docker starten

1. Build und Start:

   ```powershell
   docker compose up --build
   ```

2. Die App im Browser öffnen:

   ```text
   http://localhost:8500
   ```

## Hinweise

- Die Anwendung verwendet jetzt relative Pfade zum Repository-Wurzelverzeichnis für `app/templates`, `app/static` und `VERSION`.
- Lokale Ausführung ist möglich, wenn das Repository als Arbeitsverzeichnis genutzt wird.
- Die Datenbank wird bei Bedarf unter `data/sportfest.db` angelegt.
- Ein Backup der SQLite-Datenbank kann mit `python backup_database.py` erstellt werden.

## Backup-Konzept für SQLite

- Die SQLite-Datenbank liegt in `data/sportfest.db`.
- Backups werden in `data/backups/` abgelegt.
- Jeder Backup-Dateiname enthält einen Zeitstempel im Format `sportfest-YYYYMMDD-HHMMSS.db`.
- Das Skript `backup_database.py` kopiert die aktuelle Datenbankdatei unter Wahrung von Zeitstempel und Dateiattributen.
- Für regelmäßige Sicherungen kann das Skript in einen Cron-Job, Windows Task Scheduler oder CI/CD-Workflow eingebunden werden.

## Zugriffsschutz

- Die App läuft derzeit bewusst ohne Login, Sessions oder Zugriffsschutz.
- Zugriffsschutz wird später als eigenes Feature geplant und umgesetzt.
- Bis dahin sollen keine Login- oder Session-Funktionen ergänzt werden.

## Bekannte offene Punkte

- `PROJECT_CONTEXT.md` ist aktuell noch nicht vorhanden.
- Es gibt noch kein dokumentiertes Backup-Konzept für die SQLite-Datenbank.
- Tests und CI sind noch nicht implementiert.
