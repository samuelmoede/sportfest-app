import sqlite3
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = ROOT_DIR / "data" / "sportfest.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            jahrgang INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS courts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            sportart TEXT,
            active INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            event_date TEXT,
            status TEXT NOT NULL DEFAULT 'geplant',
            event_type TEXT NOT NULL DEFAULT 'Sonstiges'
        );

        CREATE TABLE IF NOT EXISTS competitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            sportart TEXT NOT NULL,
            jahrgang INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'geplant',
            points_win REAL NOT NULL DEFAULT 3,
            points_draw REAL NOT NULL DEFAULT 1,
            points_loss REAL NOT NULL DEFAULT 0,
            points_first_place INTEGER NOT NULL DEFAULT 7,
            placement_points TEXT,
            event_id INTEGER,
            competition_type TEXT NOT NULL DEFAULT 'Turnier',
            FOREIGN KEY (event_id) REFERENCES events(id)
        );

        CREATE TABLE IF NOT EXISTS competition_disciplines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            competition_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            unit TEXT,
            scoring_direction TEXT NOT NULL DEFAULT 'higher',
            values_per_team INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (competition_id) REFERENCES competitions(id)
        );

        CREATE INDEX IF NOT EXISTS idx_competition_disciplines_order
            ON competition_disciplines (competition_id, sort_order, id);

        CREATE TABLE IF NOT EXISTS sixkampf_participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            competition_id INTEGER NOT NULL,
            class_name TEXT NOT NULL,
            participant_number INTEGER NOT NULL,
            FOREIGN KEY (competition_id) REFERENCES competitions(id),
            UNIQUE (competition_id, class_name, participant_number)
        );

        CREATE TABLE IF NOT EXISTS discipline_results (
            participant_id INTEGER NOT NULL,
            discipline_id INTEGER NOT NULL,
            value REAL NOT NULL,
            PRIMARY KEY (participant_id, discipline_id),
            FOREIGN KEY (participant_id) REFERENCES sixkampf_participants(id),
            FOREIGN KEY (discipline_id) REFERENCES competition_disciplines(id)
        );

        CREATE INDEX IF NOT EXISTS idx_sixkampf_participants_competition
            ON sixkampf_participants (competition_id, class_name, participant_number);

        CREATE INDEX IF NOT EXISTS idx_discipline_results_discipline
            ON discipline_results (discipline_id, participant_id);

        CREATE TABLE IF NOT EXISTS sixkampf_team_results (
            competition_id INTEGER NOT NULL,
            discipline_id INTEGER NOT NULL,
            team_id INTEGER NOT NULL,
            value_index INTEGER NOT NULL,
            value REAL NOT NULL,
            PRIMARY KEY (competition_id, discipline_id, team_id, value_index),
            FOREIGN KEY (competition_id) REFERENCES competitions(id),
            FOREIGN KEY (discipline_id) REFERENCES competition_disciplines(id),
            FOREIGN KEY (team_id) REFERENCES teams(id)
        );

        CREATE INDEX IF NOT EXISTS idx_sixkampf_team_results_lookup
            ON sixkampf_team_results (competition_id, discipline_id, team_id);

        CREATE TABLE IF NOT EXISTS slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            competition_id INTEGER NOT NULL,
            court_id INTEGER,
            startzeit TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            slot_typ TEXT NOT NULL,
            phase TEXT NOT NULL,
            gruppe TEXT,
            team_a_id INTEGER,
            team_b_id INTEGER,
            score_a INTEGER,
            score_b INTEGER,

            status TEXT NOT NULL DEFAULT 'geplant',
            note TEXT,
            FOREIGN KEY (competition_id) REFERENCES competitions(id),
            FOREIGN KEY (court_id) REFERENCES courts(id),
            FOREIGN KEY (team_a_id) REFERENCES teams(id),
            FOREIGN KEY (team_b_id) REFERENCES teams(id)
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """)

        columns = [
            row["name"]
            for row in conn.execute("PRAGMA table_info(slots)").fetchall()
        ]

        if "sort_order" not in columns:
            conn.execute("""
                ALTER TABLE slots
                ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0
            """)

            conn.execute("""
                UPDATE slots
                SET sort_order = id
            """)

        if "started_at" not in columns:
            conn.execute("ALTER TABLE slots ADD COLUMN started_at TEXT")

        if "finished_at" not in columns:
            conn.execute("ALTER TABLE slots ADD COLUMN finished_at TEXT")

        event_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(events)").fetchall()
        }

        if "event_type" not in event_columns:
            conn.execute("ALTER TABLE events ADD COLUMN event_type TEXT DEFAULT 'Sonstiges'")

        conn.execute("""
            UPDATE events
            SET event_type = 'Sonstiges'
            WHERE event_type IS NULL OR TRIM(event_type) = ''
        """)

        competition_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(competitions)").fetchall()
        }

        if "event_id" not in competition_columns:
            conn.execute("ALTER TABLE competitions ADD COLUMN event_id INTEGER")

        if "competition_type" not in competition_columns:
            conn.execute("""
                ALTER TABLE competitions
                ADD COLUMN competition_type TEXT NOT NULL DEFAULT 'Turnier'
            """)

        if "points_first_place" not in competition_columns:
            conn.execute("""
                ALTER TABLE competitions
                ADD COLUMN points_first_place INTEGER NOT NULL DEFAULT 7
            """)
            conn.execute("""
                UPDATE competitions
                SET points_first_place = COALESCE(
                    NULLIF((
                        SELECT COUNT(*) FROM teams
                        WHERE teams.active = 1
                          AND teams.jahrgang = competitions.jahrgang
                    ), 0),
                    7
                )
                WHERE competition_type = 'Sechskampf'
            """)

        if "placement_points" not in competition_columns:
            conn.execute("ALTER TABLE competitions ADD COLUMN placement_points TEXT")

        if "start_time" not in competition_columns:
            conn.execute("ALTER TABLE competitions ADD COLUMN start_time TEXT")

        if "end_time" not in competition_columns:
            conn.execute("ALTER TABLE competitions ADD COLUMN end_time TEXT")

        conn.execute("""
            UPDATE competitions
            SET competition_type = 'Turnier'
            WHERE competition_type IS NULL OR competition_type = ''
        """)

        discipline_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(competition_disciplines)").fetchall()
        }

        if "scoring_direction" not in discipline_columns:
            conn.execute("""
                ALTER TABLE competition_disciplines
                ADD COLUMN scoring_direction TEXT NOT NULL DEFAULT 'higher'
            """)

        if "values_per_team" not in discipline_columns:
            conn.execute("""
                ALTER TABLE competition_disciplines
                ADD COLUMN values_per_team INTEGER NOT NULL DEFAULT 1
            """)

        conn.execute("""
            INSERT OR IGNORE INTO settings (key, value)
            VALUES ('beamer_refresh_seconds', '30')
        """)

        conn.execute("""
            INSERT OR IGNORE INTO settings (key, value)
            VALUES ('security_enabled', 'false')
        """)

        conn.commit()

