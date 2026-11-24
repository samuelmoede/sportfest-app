"""Sechskampf-Stationswerte: Konfliktschutz wie bei den Spielen (#200).

Zwei Geraete oeffnen dieselbe Klasse an derselben Station. Speichert das
zweite Geraet auf Basis eines veralteten Stands, darf es nicht still
ueberschreiben, sondern muss gefragt werden ("behalten" oder
"ueberschreiben"; Pruefbericht B-04, Samuels Regel vom 2026-11-17).
"""
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
from app.services.sixkampf_service import (
    parse_sixkampf_werte_basis,
    sixkampf_speichern_konflikt,
    sixkampf_werte_basis,
)


class BasisTextTests(unittest.TestCase):
    def test_hin_und_zurueck(self):
        werte = {2: 13.0, 1: 12.5}
        text = sixkampf_werte_basis(werte)
        self.assertEqual(text, "1=12.5;2=13.0")
        self.assertEqual(parse_sixkampf_werte_basis(text), werte)
        self.assertEqual(sixkampf_werte_basis({}), "")
        self.assertEqual(parse_sixkampf_werte_basis(""), {})
        self.assertEqual(parse_sixkampf_werte_basis("-"), {})
        self.assertEqual(parse_sixkampf_werte_basis("x=1;1=nan;1=2"), {1: 2.0})

    def test_konflikt_regeln(self):
        # Niemand hat dazwischen gespeichert -> kein Konflikt.
        self.assertFalse(sixkampf_speichern_konflikt({1: 5.0}, {1: 6.0}, "1=5.0"))
        # Anderes Geraet hat gespeichert, Eingabe weicht ab -> Konflikt.
        self.assertTrue(sixkampf_speichern_konflikt({1: 7.0}, {1: 6.0}, "1=5.0"))
        self.assertTrue(sixkampf_speichern_konflikt({1: 7.0}, {1: 6.0}, ""))
        # Beide Geraete haben denselben Wert -> kein Konflikt.
        self.assertFalse(sixkampf_speichern_konflikt({1: 6.0}, {1: 6.0}, ""))


class SixkampfSpeichernRouteTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._patcher = patch.object(
            database, "DB_PATH", Path(self._tmpdir.name) / "sixkampf-konflikt.db"
        )
        self._patcher.start()
        init_db()
        with get_conn() as conn:
            self.comp_id = conn.execute(
                """
                INSERT INTO competitions (name, sportart, jahrgang, status, competition_type)
                VALUES ('Sechskampf 5', 'Sechskampf', 5, 'geplant', 'Sechskampf')
                """
            ).lastrowid
            self.disc_id = conn.execute(
                """
                INSERT INTO competition_disciplines
                    (competition_id, name, values_per_team, scoring_direction)
                VALUES (?, 'Weitsprung', 2, 'higher')
                """,
                (self.comp_id,),
            ).lastrowid
            self.team_id = conn.execute(
                "INSERT INTO teams (name, jahrgang) VALUES ('5a', 5)"
            ).lastrowid
            conn.commit()
        from app.main import app
        self.client = TestClient(app)
        self.url = (
            f"/competition/{self.comp_id}/discipline/{self.disc_id}"
            f"/team/{self.team_id}/results"
        )

    def tearDown(self):
        self._patcher.stop()
        self._tmpdir.cleanup()

    def werte(self):
        with get_conn() as conn:
            return {
                row["value_index"]: row["value"]
                for row in conn.execute(
                    "SELECT value_index, value FROM sixkampf_team_results WHERE team_id = ?",
                    (self.team_id,),
                )
            }

    def post(self, **felder):
        return self.client.post(self.url, data=felder, follow_redirects=False)

    def test_zweites_geraet_wird_gefragt_statt_still_zu_ueberschreiben(self):
        # Geraet A speichert auf leerem Stand.
        a = self.post(basis_pruefen="1", basis="", value_1="4,1", value_2="4,3")
        self.assertEqual(a.status_code, 303)
        self.assertEqual(self.werte(), {1: 4.1, 2: 4.3})

        # Geraet B hatte das Formular noch leer geladen.
        b = self.post(basis_pruefen="1", basis="", value_1="3,9", value_2="")
        self.assertEqual(b.status_code, 303)
        self.assertEqual(self.werte(), {1: 4.1, 2: 4.3}, "B darf nicht still ueberschreiben")
        query = parse_qs(urlsplit(b.headers["location"]).query)
        self.assertEqual(query["konflikt_team_id"], [str(self.team_id)])
        self.assertEqual(query["konflikt_werte"], ["1=3.9"])

        # Die Seite zeigt beide Staende und die zwei Wege.
        seite = self.client.get(b.headers["location"])
        self.assertEqual(seite.status_code, 200)
        self.assertIn("data-six-konflikt", seite.text)
        self.assertIn("Wert 1: 4.1 · Wert 2: 4.3", seite.text)
        self.assertIn("Wert 1: 3.9", seite.text)
        self.assertIn("Gespeicherte Werte behalten", seite.text)
        self.assertIn("Mit meiner Eingabe überschreiben", seite.text)

        # B entscheidet sich fuers Ueberschreiben.
        c = self.post(ueberschreiben="1", value_1="3.9", value_2="")
        self.assertEqual(c.status_code, 303)
        self.assertEqual(self.werte(), {1: 3.9})
        self.assertNotIn("konflikt_team_id", c.headers["location"])

    def test_aktueller_stand_speichert_normal(self):
        self.post(basis_pruefen="1", basis="", value_1="4", value_2="")
        antwort = self.post(basis_pruefen="1", basis="1=4.0", value_1="5", value_2="")
        self.assertNotIn("konflikt_team_id", antwort.headers["location"])
        self.assertEqual(self.werte(), {1: 5.0})

    def test_formular_schickt_den_geladenen_stand_mit(self):
        self.post(basis_pruefen="1", basis="", value_1="4", value_2="4.5")
        seite = self.client.get(
            f"/ergebnisse?competition_id={self.comp_id}&discipline_id={self.disc_id}"
        )
        self.assertIn('name="basis" value="1=4.0;2=4.5"', seite.text)
        self.assertIn('name="basis_pruefen" value="1"', seite.text)

    def test_alte_formulare_ohne_basis_speichern_weiter(self):
        self.post(value_1="4", value_2="")
        self.post(value_1="6", value_2="")
        self.assertEqual(self.werte(), {1: 6.0})
