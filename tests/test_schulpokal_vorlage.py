"""Tests fuer die Vorlage Schulpokal (app/services/schulpokal_vorlage_service.py)
und den Ablauf danach: abwechselnder Plan je Block, Volleyball und
Tischtennis parallel mit denselben Klassen."""
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi.testclient import TestClient

import app.database as database
from app.database import get_conn, init_db
from app.services.schulpokal_vorlage_service import (
    DEFAULT_SCHULPOKAL_BLOECKE,
    parse_jahrgaenge,
)


def _form(name="Schulpokal 2026", bloecke=DEFAULT_SCHULPOKAL_BLOECKE, siegerehrung="1"):
    data = {
        "name": name,
        "event_date": "2026-12-17",
        "siegerehrung": siegerehrung,
        "block_sportart": [], "block_jahrgaenge": [], "block_location": [],
        "block_start_time": [], "block_end_time": [], "block_finale_time": [],
    }
    for block in bloecke:
        for key in ("sportart", "jahrgaenge", "location", "start_time", "end_time", "finale_time"):
            data[f"block_{key}"].append(block[key])
    return data


class SchulpokalVorlageTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "schulpokal-vorlage-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()
        with get_conn() as conn:
            for jahrgang in (7, 8, 9, 10):
                for letter in "abcde":
                    conn.execute(
                        "INSERT INTO teams (name, jahrgang, active) VALUES (?, ?, 1)",
                        (f"{jahrgang}{letter}", jahrgang),
                    )
            for name in ("Feld 1", "Feld 2", "Feld 3"):
                conn.execute(
                    "INSERT INTO courts (name, location, active) VALUES (?, 'Turnhalle', 1)", (name,)
                )
            for number in range(1, 7):
                conn.execute(
                    "INSERT INTO courts (name, location, active) VALUES (?, 'Mensa', 1)",
                    (f"Platte {number}",),
                )
            conn.commit()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def _client(self):
        from app.main import app as fastapi_app
        return TestClient(fastapi_app)

    def test_parse_jahrgaenge(self):
        self.assertEqual(parse_jahrgaenge("7, 8"), [7, 8])
        self.assertEqual(parse_jahrgaenge("9/10; 9"), [9, 10])
        self.assertEqual(parse_jahrgaenge("GOST"), ["GOST"])
        self.assertEqual(parse_jahrgaenge(""), [])

    def test_start_page_offers_template_with_defaults(self):
        with self._client() as client:
            page = client.get("/assistent")
        self.assertIn("/assistent/vorlage/schulpokal", page.text)
        self.assertIn('value="Zweifelderball"', page.text)
        self.assertIn('value="10:45"', page.text)

    def test_template_creates_event_competitions_finals_and_ceremonies(self):
        with self._client() as client:
            response = client.post("/assistent/vorlage/schulpokal", data=_form(), follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            page = client.get(response.headers["location"])
            self.assertEqual(page.status_code, 200)
            for url in ("/spielplan", "/events/1", "/tabellen", "/ergebnisse"):
                self.assertEqual(client.get(url).status_code, 200, url)
        with get_conn() as conn:
            event = conn.execute("SELECT * FROM events").fetchone()
            competitions = {
                row["name"]: row for row in conn.execute(
                    "SELECT * FROM competitions WHERE event_id = ?", (event["id"],)
                )
            }
            ceremonies = conn.execute(
                "SELECT startzeit FROM slots WHERE slot_typ = 'Siegerehrung' ORDER BY startzeit"
            ).fetchall()
            finals = conn.execute(
                "SELECT c.name, s.startzeit FROM competitions c JOIN slots s ON s.competition_id = c.id "
                "WHERE c.final_source_ids IS NOT NULL ORDER BY s.startzeit"
            ).fetchall()
        self.assertEqual(event["event_type"], "Schulpokal")
        self.assertEqual(event["event_date"], "2026-12-17")
        self.assertEqual(len(competitions), 8)
        tt = competitions["Tischtennis Jahrgang 9"]
        self.assertEqual(tt["competition_type"], "Schulpokal")
        self.assertEqual(tt["location"], "Mensa")
        self.assertEqual((tt["start_time"], tt["end_time"]), ("11:10", "13:40"))
        self.assertEqual(competitions["Zweifelderball Jahrgang 7"]["end_time"], "10:40")
        self.assertEqual(
            [(row["name"], row["startzeit"]) for row in finals],
            [("Zweifelderball Jahrgangsfinale 7/8", "10:45"), ("Volleyball Jahrgangsfinale 9/10", "13:50")],
        )
        # Volleyball und Tischtennis enden gleichzeitig: eine Siegerehrung.
        self.assertEqual([row["startzeit"] for row in ceremonies], ["10:40", "13:40"])

    def test_missing_jahrgang_is_skipped_with_hint(self):
        bloecke = [dict(DEFAULT_SCHULPOKAL_BLOECKE[0], jahrgaenge="7, 11")]
        with self._client() as client:
            response = client.post(
                "/assistent/vorlage/schulpokal", data=_form(bloecke=bloecke), follow_redirects=False
            )
            hint = parse_qs(urlsplit(response.headers["location"]).query)["vorlage_hinweis"][0]
            page = client.get(response.headers["location"])
        self.assertIn("Jahrgang 11", hint)
        self.assertIn("Jahrgang 11", page.text)
        with get_conn() as conn:
            names = [row["name"] for row in conn.execute("SELECT name FROM competitions")]
        self.assertEqual(names, ["Zweifelderball Jahrgang 7"])

    def test_duplicate_event_name_is_refused(self):
        with self._client() as client:
            client.post("/assistent/vorlage/schulpokal", data=_form())
            response = client.post("/assistent/vorlage/schulpokal", data=_form(), follow_redirects=False)
        self.assertIn("vorlage_fehler", response.headers["location"])
        with get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"], 1)

    def test_volleyball_and_tischtennis_in_parallel_only_give_hints(self):
        """Gleiche Klassen spielen 11:10-13:40 Volleyball und Tischtennis:
        der Tischtennis-Plan laesst sich trotzdem uebernehmen."""
        with self._client() as client:
            client.post("/assistent/vorlage/schulpokal", data=_form())
            with get_conn() as conn:
                ids = {
                    row["name"]: row["id"]
                    for row in conn.execute("SELECT id, name FROM competitions")
                }
                feld_ids = [row["id"] for row in conn.execute("SELECT id FROM courts WHERE name LIKE 'Feld%'")]
                platten_ids = [row["id"] for row in conn.execute("SELECT id FROM courts WHERE name LIKE 'Platte%'")]

            for first, second, court_ids in (
                ("Volleyball Jahrgang 9", "Volleyball Jahrgang 10", feld_ids),
                ("Tischtennis Jahrgang 9", "Tischtennis Jahrgang 10", platten_ids),
            ):
                page = client.post("/plan-generator/preview-schulpokal", data={
                    "competition_id": str(ids[first]),
                    "partner_competition_ids": [str(ids[second])],
                    "court_ids": [str(court_id) for court_id in court_ids],
                    "startzeit": "11:10",
                })
                self.assertEqual(page.status_code, 200)
                self.assertIn("Vorschlag übernehmen", page.text)
                self.assertIn("Vorschlag Spielzeit", page.text)
                form = page.text.split('action="/plan-generator/apply"', 1)[1].split("</form>", 1)[0]
                data = {}
                for key, val in re.findall(r'name="(\w+)" value="([^"]*)"', form):
                    data.setdefault(key, []).append(val)
                response = client.post("/plan-generator/apply", data=data, follow_redirects=False)
                self.assertNotIn("plan_error", response.headers["location"])

            self.assertIn("Andere Sportart", page.text)

        with get_conn() as conn:
            tt_games = conn.execute(
                "SELECT COUNT(*) AS n FROM slots WHERE competition_id IN (?, ?) AND slot_typ = 'Spiel'",
                (ids["Tischtennis Jahrgang 9"], ids["Tischtennis Jahrgang 10"]),
            ).fetchone()["n"]
        self.assertEqual(tt_games, 20)


if __name__ == "__main__":
    unittest.main()
