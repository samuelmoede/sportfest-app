import sqlite3
from pathlib import Path

DB_PATH = Path("/app/data/sportfest.db")


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

        CREATE TABLE IF NOT EXISTS competitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            sportart TEXT NOT NULL,
            jahrgang INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'geplant',
            points_win REAL NOT NULL DEFAULT 3,
            points_draw REAL NOT NULL DEFAULT 1,
            points_loss REAL NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS competition_disciplines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            competition_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            unit TEXT,
            FOREIGN KEY (competition_id) REFERENCES competitions(id)
        );

        CREATE INDEX IF NOT EXISTS idx_competition_disciplines_order
            ON competition_disciplines (competition_id, sort_order, id);

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

        conn.commit()