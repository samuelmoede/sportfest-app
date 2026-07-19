# Sportfest-App

Die Sportfest-App ist eine FastAPI-Anwendung zur Verwaltung schulischer Sportveranstaltungen.
Sie unterstützt Wettbewerbe, Sechskampf, Spielpläne, Teams, Ergebnisse und grundlegende Auswertungen.

## Übersicht

- Backend: FastAPI
- Templates: Jinja2
- Datenbank: SQLite (`data/sportfest.db`)
- Version: aus der Datei `VERSION`
- Docker-optimiert, kann aber auch lokal ausgeführt werden

Weiterführende Inhalte:

- `DOKUMENTATION.md` beschreibt Veranstaltungstypen, bestehende Funktionen und den aktuellen Fachstand der App.

## Projektstruktur

- `app/`
  - `main.py` - FastAPI-App
  - `database.py` - SQLite-Initialisierung und Datenbankzugriff
  - `seed.py` - (optional) Datenvorbereitung
  - `static/` - statische Assets
  - `templates/` - HTML-Templates
- `data/` - persistente SQLite-Datenbank
- `VERSION` - App-Version
- `DOKUMENTATION.md` - fachliche Dokumentation der App
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

## Wiederherstellung aus Backup (manuell)

1. Container stoppen:

   ```powershell
   docker compose down
   ```

2. Aktuelle Datenbank zusätzlich sichern:

   ```powershell
   copy data\sportfest.db data\sportfest.db.before-restore
   ```

3. Gewünschte Backup-Datei nach `data/sportfest.db` kopieren:

   ```powershell
   copy backups\sportfest_backup_YYYY-MM-DD_HHMM.db data\sportfest.db
   ```

4. Container wieder starten:

   ```powershell
   docker compose up -d
   ```

## Zugriffsschutz

- Die Login- und Session-Grundlage ist vorbereitet, aber standardmäßig vollständig deaktiviert.
- Solange `SPORTFEST_SECURITY_ENABLED=false` ist oder kein Admin-Passwort gesetzt wurde, verhält sich die App wie bisher: kein Login und keine blockierten Seiten.
- Zum Testen bzw. späteren Aktivieren in einer `.env`-Datei sichere Werte setzen:

  ```env
  SPORTFEST_SECURITY_ENABLED=true
  SPORTFEST_ADMIN_PASSWORD=ein-langes-zufaelliges-passwort
  SPORTFEST_SESSION_SECRET_KEY=ein-langer-zufaelliger-secret-key
  SPORTFEST_SESSION_HTTPS_ONLY=true
  ```

- `SPORTFEST_SESSION_HTTPS_ONLY=true` erst verwenden, wenn die App über HTTPS erreichbar ist.
- Alternativ kann das Passwort im Settings-Schlüssel `admin_password` liegen; die Umgebungsvariable hat Vorrang und ist für den Betrieb vorzuziehen.
- Bei aktiver Sicherheit schützt eine zentrale Middleware die Admin-Bereiche `/einstellungen`, `/teams`, `/spielfelder` und `/wettbewerbe` einschließlich ihrer Verwaltungsaktionen. Öffentliche Ansichten, Ergebniseingabe und Spielplanaktionen bleiben offen.
- Ohne gesetzten Environment-Override kann die Sicherheit auf `/einstellungen` nach erneuter Eingabe des Admin-Kennworts aktiviert oder deaktiviert werden. `SPORTFEST_SECURITY_ENABLED` hat weiterhin Vorrang und sperrt den Schalter.

## Tests und CI/CD

- Tests ausführen: `pip install -r requirements-dev.txt` dann `python -m pytest tests -v`.
- Bei jedem Push/Pull Request auf `main` oder `develop` läuft die Testsuite automatisch
  über GitHub Actions (`.github/workflows/ci.yml`).
- Branch-Workflow: `main` ist die live-deploybare Version (nur per Pull Request aus
  `develop`, muss den `ci`-Check bestehen). Entwickelt wird auf `develop`, das über einen
  selbstgehosteten Runner automatisch in eine separate Staging-Umgebung deployed wird
  (`docker-compose.dev.yml`, Port 8502). Details siehe `CLAUDE.md`, Abschnitt
  "Branch-, CI- und Deploy-Workflow".

## Bekannte offene Punkte

- `PROJECT_CONTEXT.md` ist aktuell noch nicht vorhanden.
- Es gibt noch kein dokumentiertes Backup-Konzept für die SQLite-Datenbank.
