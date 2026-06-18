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

        CREATE TABLE IF NOT EXISTS slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            competition_id INTEGER NOT NULL,
            court_id INTEGER,
            startzeit TEXT NOT NULL,
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