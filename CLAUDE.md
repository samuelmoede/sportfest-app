# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Sportfest-App: a FastAPI + Jinja2 + SQLite web app for managing school sports events
(tournaments, six-event "Sechskampf" competitions, schedules, teams, results, tables).
The UI and domain vocabulary are **German** — keep new identifiers, routes, templates,
and user-facing strings in German to match (`wettbewerbe`, `spielplan`, `ergebnisse`,
`teams`, `spielfelder`, `tabellen`, `beamer`).

## Commands

Run locally (the repo root must be the working directory — paths are resolved relative to it):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install fastapi uvicorn jinja2 python-multipart itsdangerous
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8500
```

Run with Docker (mounts `app/`, `data/`, `VERSION`, `CHANGELOG.md`; auto-reload enabled):

```powershell
docker compose up --build
```

- App runs at http://localhost:8500
- Seed/reset the DB with sample teams, courts, competitions, and slots: `python -m app.seed`
  (**destructive** — it deletes all slots, competitions, courts, and teams first)
- Back up the SQLite DB: `python backup_database.py` (copies `data/sportfest.db` → `data/backups/sportfest-<timestamp>.db`)
- Run the test suite: `pip install -r requirements-dev.txt` then `python -m pytest tests -v`.
  Runtime dependencies also live in `requirements.txt` now (mirrors the `Dockerfile`'s inline
  `pip install`, which is left as-is so the production build stays unaffected). There is still
  **no linter**. CI (GitHub Actions, `.github/workflows/ci.yml`) runs this suite on every push/PR
  to `main`/`develop`.

## Architecture

- `app/main.py` — the FastAPI app: ~50 routes, still large (~2300 lines); use Grep to
  locate routes rather than reading it whole. Business logic that used to live entirely in
  this file has been split out into `app/services/*.py` modules (scheduling, table/ranking
  calculation, Sechskampf scoring, settings, backups, etc.) — check there first for
  scheduling/scoring logic, and prefer testing new logic at the service-function level
  (see `tests/`).
- `app/database.py` — `get_conn()` (returns a `sqlite3.Connection` with `Row` factory)
  and `init_db()`. The DB lives at `data/sportfest.db`, resolved relative to the repo root.
- `app/seed.py` — runs its logic at import time (no functions), so it executes on `python -m app.seed`.
- `tests/` — `unittest`-style tests (run via `pytest`), one file per service module plus
  `test_smoke.py` (boots the app via `TestClient` and hits core public routes). Tests that
  touch the database always override `app.database.DB_PATH` to a temp file first — **never**
  let a test use the real `DB_PATH`, since (see Workflow section) the repo root can be a
  live-mounted production folder.

Templates in `app/templates/` (Jinja2, all extend `base.html`), single stylesheet `app/static/style.css`.
`app_version` (read from the `VERSION` file) is injected as a Jinja global.

### Schema management — important

`init_db()` runs on every app startup (`@app.on_event("startup")`). There are **no migration
files**: the schema is created with `CREATE TABLE IF NOT EXISTS`, and schema changes are
applied as idempotent in-place `ALTER TABLE` blocks guarded by `PRAGMA table_info(...)`
checks (see the bottom of `database.py`). When adding or changing a column, follow that
same pattern — add the `CREATE TABLE` definition **and** a guarded `ALTER TABLE` so existing
databases upgrade in place.

### Data model

`events` group `competitions`. A `competition` has a `competition_type` of either `Turnier`
(tournament) or `Sechskampf` (six-event), which branches behavior throughout (notably the
`/ergebnisse` route). Teams and competitions are matched by integer `jahrgang` (school year).
Match scheduling uses `slots` (rows for games, breaks, and filler), each tied to a `court`
(`spielfeld`) and time. Sechskampf scoring uses `competition_disciplines` +
`sixkampf_team_results`. (The `sixkampf_participants` / `discipline_results` tables exist in
the schema but the active flow uses the team-results tables.)

German status/enum values are used directly in SQL throughout — e.g. slot `status` is one of
`geplant` / `läuft` / `beendet`; competition `status` includes `archiviert`; phases are
`Gruppenphase` / `Halbfinale` / `Finale` / `Spiel um Platz 3`; `slot_typ` is `Spiel` / `Leer`.
Match these strings exactly when writing queries.

### Key logic in main.py

- `generate_group_plan()` / `validate_generated_plan()` — auto-build a group-phase schedule
  (with special hardcoded pairings for 6- and 7-team groups) and surface conflict warnings.
  The plan generator is preview-then-apply: `/plan-generator/preview` renders proposed slots,
  `/plan-generator/apply` persists them.
- `calculate_table()` / `calculate_group_table()` / `sort_table_rows()` — standings, with
  tie-breaking by points → goal difference → goals → head-to-head (`calculate_direct_comparison`).
- `generate_semifinals` / `generate_finals` — fill KO-round slots from group results / semifinal winners.
- `calculate_sixkampf_team_ranking()` — Sechskampf placement and points.
- `fetch_beamer_data()` — backs `/beamer`, a projector/scoreboard view of the live competition.

## Branch-, CI- und Deploy-Workflow

- `main` = die live-deploybare Version. Es wird nicht mehr direkt auf `main`
  entwickelt; Änderungen kommen per Pull Request aus `develop` (oder
  Feature-Branches davon), gated durch den `ci`-Workflow (pytest muss grün
  sein).
- **Wichtig:** Der Ordner, in dem dieses Repo auf `main` ausgecheckt ist
  (`Z:\sportfest-app`, Netzwerkfreigabe `\\Server\docker\sportfest-app`), ist
  **derselbe Ordner, aus dem der Produktiv-Container per Docker Compose mit
  `--reload` läuft** (`https://sportfest.moede-digital.org/`). Dateiänderungen
  dort werden potenziell sofort live wirksam. Deshalb: hier nicht mehr direkt
  entwickeln.
- Für Entwicklung/Vorschau gibt es einen zweiten Checkout auf `develop`
  (`Z:\sportfest-app-dev`, ein `git worktree` desselben Repos) mit eigenem
  Docker-Compose-Stack (`docker-compose.dev.yml`, Port 8501, eigene
  `data/sportfest.db`), erreichbar im Heimnetz/per WireGuard-VPN unter
  `192.168.178.20:8501`. Entwicklung kann genauso von jedem anderen Klon aus
  passieren (Laptop, anderes Gerät) — entscheidend ist nur, dass auf
  `develop`/Feature-Branches gearbeitet und dorthin gepusht wird.
- Ein selbstgehosteter GitHub-Actions-Runner läuft als Docker-Container auf
  dem NAS (192.168.178.20) und deployt automatisch: Push auf `develop` →
  `.github/workflows/deploy-staging.yml` aktualisiert den `sportfest-app-dev`-
  Ordner/Container; Push auf `main` (= gemergter PR) →
  `.github/workflows/deploy-prod.yml` aktualisiert den Produktivordner.

## Caveats

- **Authentication is opt-in.** `security_enabled` defaults to false and must keep the disabled
  mode fully backward-compatible. When enabled, central middleware protects settings, teams,
  courts, and competition administration; results and schedule actions remain public. CSRF
  protection is still pending.
- `data/*.db` and `backups/` are gitignored — never commit the live database.
