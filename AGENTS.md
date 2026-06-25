# AGENTS.md

## Projektbeschreibung

Die Sportfest-App ist eine FastAPI-Anwendung fuer schulische Sportveranstaltungen.
Sie verwaltet Veranstaltungen, Wettbewerbe, Turniere, Sechskampf, Teams, Orte,
Spielplaene, Ergebnisse, Tabellen, Tagesplaene und grundlegende Auswertungen.

Der aktuelle technische Kern liegt bewusst noch in wenigen Dateien:

- `app/main.py`: FastAPI-App, Routen, Formularverarbeitung und grosse Teile der Fachlogik.
- `app/database.py`: SQLite-Initialisierung, Schema-Erweiterungen und `get_conn()`.
- `app/seed.py`: optionale Beispieldaten.
- `app/templates/`: Jinja2-Templates fuer die Oberflaeche.
- `app/static/`: CSS und statische Assets.
- `data/sportfest.db`: lokale SQLite-Datenbank, nicht versionieren.

Die fachliche Sprache der Anwendung ist Deutsch. Neue UI-Texte, Routen, Statuswerte
und fachliche Begriffe sollen zur bestehenden Sprache passen.

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

- Es gibt aktuell keine etablierte Testsuite und keine CI.
- Wenn Tests ergaenzt werden, klein anfangen und kritische Fachlogik priorisieren: Turniertabellen, Punkteberechnung, Sechskampf-Wertung, Spielplan-Zeiten und Rollen-/Sicherheitslogik.
- Fuer Datenbanklogik moeglichst temporaere SQLite-Datenbanken verwenden, nicht `data/sportfest.db`.
- Nach UI- oder Routing-Aenderungen mindestens die betroffenen Seiten lokal starten und manuell pruefen.
- Vor riskanten Refactorings erst Charakterisierungstests fuer bestehendes Verhalten schreiben.
- Wenn keine Tests ausgefuehrt wurden, dies am Ende der Arbeit offen nennen.