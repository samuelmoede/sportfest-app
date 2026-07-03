from app.database import get_conn, init_db

init_db()

with get_conn() as conn:
    conn.executescript("""
    DELETE FROM slots;
    DELETE FROM competitions;
    DELETE FROM courts;
    DELETE FROM teams;
    """)

    for name in ["7a", "7b", "7c", "7d", "7e", "7f", "7g", "8a", "8b", "8c", "9a", "9b"]:
        jahrgang = int(name[0])
        conn.execute("INSERT INTO teams (name, jahrgang) VALUES (?, ?)", (name, jahrgang))

    courts = [
        ("Feld 1", "Zweifelderball", "Turnhalle"),
        ("Feld 2", "Zweifelderball", "Turnhalle"),
        ("Feld 3", "Reserve", "Turnhalle"),
        ("Rasenplatz", "Fußball", "Fußballplatz"),
        ("Käfig", "Fußball", "Fußballplatz"),
        ("Halle A", "Volleyball", "Turnhalle"),
    ]
    for name, sportart, location in courts:
        conn.execute(
            "INSERT INTO courts (name, sportart, location) VALUES (?, ?, ?)",
            (name, sportart, location),
        )
    competitions = [
        ("Zweifelderball Jahrgang 7", "Zweifelderball", 7, "läuft", 3, 1, 0),
        ("Zweifelderball Jahrgang 8", "Zweifelderball", 8, "geplant", 3, 1, 0),
        ("Volleyball Jahrgang 9", "Volleyball", 9, "geplant", 2, 1, 0),
    ]
    for comp in competitions:
        conn.execute("""
            INSERT INTO competitions (name, sportart, jahrgang, status, points_win, points_draw, points_loss)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, comp)

    conn.commit()

    team_ids = {r["name"]: r["id"] for r in conn.execute("SELECT * FROM teams").fetchall()}
    court_ids = {r["name"]: r["id"] for r in conn.execute("SELECT * FROM courts").fetchall()}
    comp_ids = {r["name"]: r["id"] for r in conn.execute("SELECT * FROM competitions").fetchall()}

    slots = [
        ("09:00", "Spiel", "Zweifelderball Jahrgang 7", "Feld 1", "7a", "7b", "Gruppenphase", "A", "beendet", 2, 1, None),
        ("09:15", "Spiel", "Zweifelderball Jahrgang 7", "Feld 2", "7c", "7d", "Gruppenphase", "A", "beendet", 0, 3, None),
        ("09:30", "Spiel", "Zweifelderball Jahrgang 7", "Feld 1", "7a", "7c", "Gruppenphase", "A", "läuft", 1, 1, None),
        ("09:30", "Spiel", "Zweifelderball Jahrgang 7", "Feld 2", "7b", "7d", "Gruppenphase", "A", "läuft", 2, 0, None),
        ("10:00", "Pause", "Zweifelderball Jahrgang 7", None, None, None, "Pause", None, "geplant", None, None, "Frühstückspause"),
        ("10:15", "Spiel", "Zweifelderball Jahrgang 7", "Feld 1", "7b", "7c", "Gruppenphase", "A", "geplant", None, None, None),
        ("11:00", "Spiel", "Zweifelderball Jahrgang 7", "Feld 1", None, None, "Halbfinale", None, "geplant", None, None, "1. Platz Gruppe A vs 2. Platz Gruppe B"),
        ("11:00", "Spiel", "Zweifelderball Jahrgang 7", "Feld 2", None, None, "Halbfinale", None, "geplant", None, None, "1. Platz Gruppe B vs 2. Platz Gruppe A"),
        ("11:30", "Spiel", "Zweifelderball Jahrgang 7", "Feld 1", None, None, "Finale", None, "geplant", None, None, "Finale"),
        ("12:00", "Siegerehrung", "Zweifelderball Jahrgang 7", None, None, None, "Siegerehrung", None, "geplant", None, None, "Siegerehrung Jahrgang 7"),
    ]

    for startzeit, typ, comp, court, ta, tb, phase, gruppe, status, sa, sb, note in slots:
        conn.execute("""
            INSERT INTO slots (
                startzeit, slot_typ, competition_id, court_id,
                team_a_id, team_b_id, phase, gruppe, status,
                score_a, score_b, note
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            startzeit,
            typ,
            comp_ids[comp],
            court_ids.get(court) if court else None,
            team_ids.get(ta) if ta else None,
            team_ids.get(tb) if tb else None,
            phase,
            gruppe,
            status,
            sa,
            sb,
            note
        ))

    conn.commit()

print("Seed-Daten wurden angelegt.")