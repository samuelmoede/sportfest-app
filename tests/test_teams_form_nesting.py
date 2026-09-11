import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi.testclient import TestClient

import app.database as database
from app.database import get_conn, init_db


class _FormNestingParser(HTMLParser):
    """Verfolgt die Verschachtelungstiefe von <form>-Elementen und sammelt
    alle form="..."-Attribute (Feld/Button) sowie alle vorhandenen
    <form id="..."> -Elemente."""

    def __init__(self):
        super().__init__()
        self.form_depth = 0
        self.max_form_depth = 0
        self.form_ids = set()
        self.form_owner_refs = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.form_depth += 1
            self.max_form_depth = max(self.max_form_depth, self.form_depth)
            if attrs.get("id"):
                self.form_ids.add(attrs["id"])
        form_ref = attrs.get("form")
        if form_ref:
            self.form_owner_refs.add(form_ref)

    def handle_endtag(self, tag):
        if tag == "form" and self.form_depth > 0:
            self.form_depth -= 1


class TeamsFormNestingTests(unittest.TestCase):
    """Issue #72: Auf /teams sind Speichern- und Loeschen-Button je Team ueber
    das form="..."-Attribut mit einem eigenen <form> verknuepft. Stehen diese
    <form>-Elemente verschachtelt in einem anderen <form> (z. B. dem
    #team-bulk-form-Massenaktionsformular), verwirft jeder Browser beim
    HTML-Parsen das zuerst geoeffnete verschachtelte <form> stillschweigend -
    das betroffene Team laesst sich dann clientseitig nicht mehr speichern
    oder loeschen, ohne dass Server oder Nutzer etwas davon bemerken."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp_db_path = Path(self._tmpdir.name) / "teams-form-nesting-test.db"
        self._db_path_patcher = patch.object(database, "DB_PATH", tmp_db_path)
        self._db_path_patcher.start()
        init_db()

        with get_conn() as conn:
            # Team mit dem kleinsten Jahrgang landet unter ORDER BY jahrgang,
            # name als allererste Zeile auf der Seite - genau die Zeile,
            # deren <form> beim alten, verschachtelten Markup vom
            # HTML-Parser verworfen wurde.
            first_team_id = conn.execute(
                "INSERT INTO teams (name, jahrgang) VALUES ('11/1', 11)"
            ).lastrowid
            conn.execute("INSERT INTO teams (name, jahrgang) VALUES ('11/2', 11)")
            conn.execute("INSERT INTO teams (name, jahrgang) VALUES ('12/1', 12)")
            conn.execute("INSERT INTO teams (name, jahrgang) VALUES ('Tutorium A', 'GOST')")

            competition_id = conn.execute(
                """
                INSERT INTO competitions (name, sportart, jahrgang, status, competition_type)
                VALUES ('Zweifelderball', 'Zweifelderball', 11, 'geplant', 'Turnier')
                """
            ).lastrowid

            # Viele verknuepfte Datensaetze fuers erste Team, analog zu den
            # in Issue #72 gemeldeten 65 Datensaetzen ("Verwendet in").
            for i in range(65):
                conn.execute(
                    """
                    INSERT INTO slots (competition_id, startzeit, slot_typ, phase, team_a_id)
                    VALUES (?, ?, 'Spiel', 'Gruppenphase', ?)
                    """,
                    (competition_id, f"{9 + i // 60:02d}:{i % 60:02d}", first_team_id),
                )
            conn.commit()

        self.first_team_id = first_team_id

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def _get_teams_html(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get("/teams")
        self.assertEqual(response.status_code, 200)
        return response.text

    def test_no_nested_form_elements(self):
        html = self._get_teams_html()
        parser = _FormNestingParser()
        parser.feed(html)
        self.assertLessEqual(
            parser.max_form_depth,
            1,
            "kein <form>-Element darf innerhalb eines anderen <form> stehen",
        )

    def test_every_form_attribute_reference_resolves_to_an_existing_form(self):
        html = self._get_teams_html()
        parser = _FormNestingParser()
        parser.feed(html)
        missing = parser.form_owner_refs - parser.form_ids
        self.assertFalse(
            missing,
            f'form="..."-Referenzen ohne zugehoeriges <form id=...>: {missing}',
        )

    def test_first_team_row_gets_real_top_level_update_and_delete_forms(self):
        html = self._get_teams_html()
        self.assertIn(f'<form id="team-update-{self.first_team_id}"', html)
        self.assertIn(f'<form id="team-delete-{self.first_team_id}"', html)


if __name__ == "__main__":
    unittest.main()
