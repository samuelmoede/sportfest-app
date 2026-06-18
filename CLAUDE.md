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
- There is **no test suite, linter, or CI** and **no `requirements.txt`** — dependencies
  live inline in the `Dockerfile` and README only.

## Architecture

Three Python files do everything:

- `app/main.py` — the entire FastAPI app: ~50 routes plus all business logic
  (scheduling, table calculation, ranking) as module-level functions. This is one large
  file (~2300 lines); use Grep to locate routes/functions rather than reading it whole.
- `app/database.py` — `get_conn()` (returns a `sqlite3.Connection` with `Row` factory)
  and `init_db()`. The DB lives at `data/sportfest.db`.
- `app/seed.py` — runs its logic at import time (no functions), so it executes on `python -m app.seed`.

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

## Caveats

- **No authentication.** `README.md` and `PROJECT_CONTEXT.md` describe an admin login / CSRF
  protection, but it was fully removed (see recent commits). The app is currently open; those
  docs are out of date. `itsdangerous` is still installed but unused.
- `data/*.db` and `backups/` are gitignored — never commit the live database.
