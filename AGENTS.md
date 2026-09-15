# AGENTS.md

## Gemeinsame fachliche Referenz

`CLAUDE.md` ist fuer Claude Code und Codex die gemeinsame fachliche und
architektonische Referenz (Datenmodell, Services, Templates, Schema und
Kompatibilitaet). Vor Aenderungen die relevanten Abschnitte und den betroffenen
Service lesen. Die Claude-spezifische Automation bleibt in `CLAUDE.md` und
`.github/workflows/claude.yml`; diese Datei beschreibt die Codex-Variante.
`CLAUDE.md` nicht im Rahmen der Codex-Workflow-Pflege veraendern.

## Nicht verhandelbare Regeln fuer automatisierte Aenderungen (Codex)

- Entwicklungsbasis ist `develop`. Neue Aenderungen auf einem davon abgeleiteten
  `codex/*`-Feature-Branch in einem separaten Klon erstellen. `claude/*` sind die
  Feature-Branches von Claude. Niemals direkt auf `main` entwickeln oder pushen;
  auch auf das geschuetzte `develop` nicht direkt pushen.
- Niemals im Live-Produktivmount `Z:\sportfest-app` beziehungsweise
  `/volume1/docker/sportfest-app` arbeiten; Dateiänderungen koennen dort sofort
  produktiv wirksam werden.
- Pull Requests ausschliesslich gegen `develop` oeffnen; die tatsaechliche
  PR-Basis vor und nach dem Erstellen pruefen. Niemals einen Codex-PR gegen
  `main` oeffnen.
- Niemals selbst mergen, auch nicht per API, `gh pr merge`, Auto-Merge oder
  Merge Queue. Merge und spaetere Freigaben bleiben beim Menschen.
- Branch-Protection und Pflichtchecks `test` und `docker` niemals umgehen,
  abschwaechen oder durch Force-Push auf geschuetzte Branches aushebeln.
- Niemals die Produktivfreigabe selbst erteilen, ein Prod-Deployment ausloesen
  oder die Freigabe des Environments `production` in
  `.github/workflows/deploy-prod.yml` umgehen. Den self-hosted NAS-Runner
  ausschliesslich dem bestehenden Deployment-Workflow ueberlassen.
- Niemals die Produktivdatenbank `data/sportfest.db` lesen, schreiben,
  ueberschreiben, kopieren oder committen. Tests muessen vor jedem DB-Zugriff
  `app.database.DB_PATH` auf eine temporaere SQLite-Datei umstellen.
  Niemals Seed-/Reset-Befehle gegen Produktivdaten ausfuehren.
- Zuerst analysieren, dann aendern. So wenige Dateien wie moeglich aendern;
  keine auftragsfremden Refactorings oder Aufraeumarbeiten.
- Bestehende Funktionen nicht ohne expliziten Auftrag loeschen und Verhalten
  nicht stillschweigend aendern.
- Neue oder geaenderte Logik erhaelt Tests in `tests/`, bevorzugt auf
  Service-Ebene. Relevante Tests ausfuehren und Ergebnisse sowie nicht
  ausgefuehrte Pruefungen im PR offen nennen.

## Entwicklungsregeln

- Vor Aenderungen zuerst `PROJECT_CONTEXT.md`, `ROADMAP.md`, `CHANGELOG.md` und diese Datei lesen.
- Bestehende Muster im Projekt bevorzugen, besonders bei Routen, Templates, Datenbankzugriff und Formular-Redirects.
- Keine neue Framework-Schicht einfuehren, solange eine kleine lokale Loesung ausreicht.
- Datenbankzugriff erfolgt ueber `get_conn()` und SQLite-Row-Objekte.
- Schema-Aenderungen muessen additiv und idempotent sein: `CREATE TABLE IF NOT EXISTS` plus abgesicherte `ALTER TABLE`-Bloecke fuer bestehende Datenbanken.
- Die Live-Datenbank `data/sportfest.db` sowie Backups duerfen nicht committed werden.
- Benutzeroberflaeche und Navigationslogik sollen fuer Smartphone, Tablet und Desktop brauchbar bleiben.
- Sicherheitslogik bleibt optional: deaktivierte Sicherheit muss weiter vollstaendig rueckwaertskompatibel funktionieren.

## Refactoring-Regeln

- Refactorings klein schneiden und fachlich nachvollziehbar benennen.
- Keine Route verschieben, ohne vorher ihre Templates, Redirects, Formularnamen und Abhaengigkeiten zu pruefen.
- Vor dem Herausziehen von Logik aus `app/main.py` zuerst die betroffenen Funktionen und Routen kartieren.
- Fachlogik nicht gleichzeitig mit UI-Umbauten und Datenbankschema-Aenderungen mischen.
- Bestehende URLs, Formularfelder, Statuswerte und Datenbankwerte nicht beilaufig umbenennen.
- Veranstaltungs-, Wettbewerbs-, Turnier- und Sechskampf-Logik getrennt halten, auch wenn sie aktuell in einer Datei steht.
- Oeffentliche Ansichten, Ergebniseingabe und Spielplanaktionen nicht versehentlich durch Admin-Schutz blockieren.
- Beim Aufteilen von Code zuerst reine Hilfsfunktionen extrahieren, danach Routen und Templates.
- Nach jedem Refactoring pruefen, ob `PROJECT_CONTEXT.md`, `ROADMAP.md` oder `CHANGELOG.md` aktualisiert werden muessen.

## Git-Regeln

- Vor groesseren Aenderungen `git status` pruefen.
- Fremde oder unerklaerte lokale Aenderungen nicht zuruecksetzen.
- Keine destruktiven Git-Kommandos wie `git reset --hard` oder `git checkout --` ohne ausdrueckliche Anweisung.
- Commits sollen fachlich zusammenhaengend und klein bleiben.
- Live-Daten, lokale `.env`-Dateien, virtuelle Umgebungen, Caches und Backups nicht committen.
- In Commit-Nachrichten kurz beschreiben, welcher fachliche Bereich betroffen ist.

## Test-Regeln

- CI ist in `.github/workflows/ci.yml` definiert: Python 3.12, pytest samt
  Playwright/Chromium sowie Docker-Build und Container-Healthcheck. Sie laeuft
  bei Pull Requests und bei Pushes auf `main`/`develop`.
- Im Projekt-Root Abhaengigkeiten mit `python -m pip install -r requirements-dev.txt`
  installieren; das schliesst `requirements.txt` ein. Fuer Browser-Tests einmalig
  `python -m playwright install --with-deps chromium` auf Linux ausfuehren
  (unter Windows ohne `--with-deps`). Tests: `python -m pytest tests -v`.
- Fehlendes Chromium fuehrt zu uebersprungenen Browser-Tests; dies nicht als
  vollstaendig bestandene Testsuite ausgeben.
- Wenn Tests ergaenzt werden, klein anfangen und kritische Fachlogik priorisieren: Turniertabellen, Punkteberechnung, Sechskampf-Wertung, Spielplan-Zeiten und Rollen-/Sicherheitslogik.
- Fuer Datenbanklogik immer temporaere SQLite-Datenbanken verwenden (DB_PATH wie oben umstellen).
- Nach UI- oder Routing-Aenderungen mindestens die betroffenen Seiten lokal starten und manuell pruefen.
- Vor riskanten Refactorings erst Charakterisierungstests fuer bestehendes Verhalten schreiben.
- Wenn keine Tests ausgefuehrt wurden, dies am Ende der Arbeit offen nennen.
