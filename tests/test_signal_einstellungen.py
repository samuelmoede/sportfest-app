"""Signale beim Spiel je Konto (Seite "Mein Konto", /konto).

Geprueft: Schalter je Benutzer speichern/laden, "Benachrichtigung aus"
loescht alle Push-Abos des Benutzers, wer ein Spiel startet wird gemerkt
und nur dessen Handys bekommen den Push (Schiris wechseln sich ab), der
alte Weg ohne Konto (Feld-Abo) bleibt, Seite und Routen.
"""
import base64
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi.testclient import TestClient

import app.database as database
from app.database import get_conn, init_db
from app.services import push_service as ps
from app.services import signal_einstellungen_service as ses
from app.services.users_service import create_user


def _b64(daten):
    return base64.urlsafe_b64encode(daten).rstrip(b"=").decode()


def _abo(nr):
    return {
        "endpoint": f"https://fcm.googleapis.com/fcm/send/{nr}",
        "keys": {"p256dh": _b64(b"\x04" + b"\x01" * 64), "auth": _b64(b"a" * 16)},
    }


class _TempDb(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._patcher = patch.object(database, "DB_PATH", Path(self._tmpdir.name) / "signale.db")
        self._patcher.start()
        init_db()
        create_user("schiri1", "GeheimesPw1", "referee")
        create_user("schiri2", "GeheimesPw2", "referee")
        with get_conn() as conn:
            self.schiri1 = conn.execute("SELECT id FROM users WHERE username = 'SCHIRI1'").fetchone()[0]
            self.schiri2 = conn.execute("SELECT id FROM users WHERE username = 'SCHIRI2'").fetchone()[0]
            self.court_id = conn.execute("INSERT INTO courts (name) VALUES ('Feld 1')").lastrowid
            comp_id = conn.execute(
                """
                INSERT INTO competitions (name, sportart, jahrgang, status, competition_type,
                                          game_duration_minutes, game_duration_seconds)
                VALUES ('Volleyball Jg. 9', 'Volleyball', 9, 'geplant', 'Turnier', 8, 480)
                """
            ).lastrowid
            a = conn.execute("INSERT INTO teams (name, jahrgang) VALUES ('9a', 9)").lastrowid
            b = conn.execute("INSERT INTO teams (name, jahrgang) VALUES ('9b', 9)").lastrowid
            self.slot_id = conn.execute(
                """
                INSERT INTO slots (competition_id, court_id, startzeit, slot_typ, phase,
                                   team_a_id, team_b_id, status)
                VALUES (?, ?, '09:00', 'Spiel', 'Gruppenphase', ?, ?, 'geplant')
                """,
                (comp_id, self.court_id, a, b),
            ).lastrowid
            conn.commit()
        self.gesendet = []

    def tearDown(self):
        self._patcher.stop()
        self._tmpdir.cleanup()

    def _sender(self, abo, daten, privat):
        self.gesendet.append(abo["endpoint"])
        return 201


class EinstellungenTests(_TempDb):
    def test_standard_alles_an_und_speichern(self):
        with get_conn() as conn:
            self.assertEqual(ses.lade_einstellungen(conn, self.schiri1),
                             {"ton": True, "countdown": True, "benachrichtigung": True})
            ses.speichere_einstellungen(conn, self.schiri1, {"countdown": False})
            conn.commit()
            self.assertEqual(ses.lade_einstellungen(conn, self.schiri1),
                             {"ton": True, "countdown": False, "benachrichtigung": True})
            # Anderer Benutzer unberuehrt.
            self.assertTrue(ses.lade_einstellungen(conn, self.schiri2)["countdown"])
            self.assertIsNone(ses.lade_einstellungen(conn, None))
            self.assertIsNone(ses.lade_einstellungen(conn, 9999))

    def test_benachrichtigung_aus_loescht_alle_abos_des_benutzers(self):
        with get_conn() as conn:
            ps.speichere_abo(conn, _abo(1), None, "https://x", "t", user_id=self.schiri1)
            ps.speichere_abo(conn, _abo(2), None, "https://x", "t", user_id=self.schiri1)
            ps.speichere_abo(conn, _abo(3), None, "https://x", "t", user_id=self.schiri2)
            ses.speichere_einstellungen(conn, self.schiri1, {"benachrichtigung": False})
            conn.commit()
            rest = [r[0] for r in conn.execute("SELECT endpoint FROM push_abos")]
        self.assertEqual(rest, [_abo(3)["endpoint"]])

    def test_abo_ohne_feld_behaelt_bekanntes_feld(self):
        with get_conn() as conn:
            ps.speichere_abo(conn, _abo(1), self.court_id, "https://x", "t")
            ps.speichere_abo(conn, _abo(1), None, "https://x", "t2", user_id=self.schiri1)
            zeile = conn.execute("SELECT court_id, user_id FROM push_abos").fetchone()
        self.assertEqual((zeile["court_id"], zeile["user_id"]), (self.court_id, self.schiri1))


class NurWerGestartetHatTests(_TempDb):
    def _starte(self, user_id):
        with get_conn() as conn:
            conn.execute(
                "UPDATE slots SET status = 'läuft', started_at = '2026-12-21 09:00:00' WHERE id = ?",
                (self.slot_id,),
            )
            ses.merke_starter(conn, [self.slot_id], user_id)
            conn.commit()

    def _abos(self):
        with get_conn() as conn:
            ps.speichere_abo(conn, _abo(1), self.court_id, "https://x", "t", user_id=self.schiri1)
            ps.speichere_abo(conn, _abo(2), self.court_id, "https://x", "t", user_id=self.schiri2)
            ps.speichere_abo(conn, _abo(3), self.court_id, "https://x", "t")  # Handy ohne Konto
            conn.commit()

    def _takt(self):
        with get_conn() as conn:
            return ps.verarbeite(conn, datetime(2026, 12, 21, 9, 7, 10), None, sender=self._sender)

    def test_push_nur_an_den_startenden_schiri(self):
        self._abos()
        self._starte(self.schiri2)
        self.assertEqual(self._takt(), 1)
        self.assertEqual(self.gesendet, [_abo(2)["endpoint"]])

    def test_startender_schiri_hat_benachrichtigung_aus(self):
        self._abos()
        with get_conn() as conn:
            ses.speichere_einstellungen(conn, self.schiri2, {"benachrichtigung": False})
            conn.commit()
        self._starte(self.schiri2)
        self.assertEqual(self._takt(), 0)

    def test_ohne_konto_gestartet_gilt_das_feld(self):
        self._abos()
        self._starte(None)
        self.assertEqual(self._takt(), 1)
        self.assertEqual(self.gesendet, [_abo(3)["endpoint"]])

    def test_mehrere_gleichzeitig_gestartete_spiele_nur_eine_meldung(self):
        with get_conn() as conn:
            feld2 = conn.execute("INSERT INTO courts (name) VALUES ('Feld 2')").lastrowid
            zweites = conn.execute(
                """
                INSERT INTO slots (competition_id, court_id, startzeit, slot_typ, phase,
                                   team_a_id, team_b_id, status)
                SELECT competition_id, ?, startzeit, slot_typ, phase, team_a_id, team_b_id, status
                FROM slots WHERE id = ?
                """,
                (feld2, self.slot_id),
            ).lastrowid
            ps.speichere_abo(conn, _abo(1), None, "https://x", "t", user_id=self.schiri1)
            conn.execute(
                "UPDATE slots SET status = 'läuft', started_at = '2026-12-21 09:00:00' WHERE id IN (?, ?)",
                (self.slot_id, zweites),
            )
            ses.merke_starter(conn, [self.slot_id, zweites], self.schiri1)
            conn.commit()
            daten = []

            def sender(abo, inhalt, privat):
                daten.append(inhalt)
                return 201

            self.assertEqual(ps.verarbeite(conn, datetime(2026, 12, 21, 9, 7, 10), None, sender=sender), 1)
            self.assertEqual(ps.verarbeite(conn, datetime(2026, 12, 21, 9, 8, 1), None, sender=sender), 1)
        self.assertEqual([d["titel"] for d in daten], ["Noch 1 Minute · 2 Spiele", "Spielzeit vorbei · 2 Spiele"])
        self.assertEqual(daten[0]["text"], "Feld 1 · Feld 2")
        # "Ende" ersetzt "Noch 1 Minute" auf Handy und Uhr.
        self.assertEqual(daten[0]["tag"], daten[1]["tag"])

    def test_eigenes_spiel(self):
        self._starte(self.schiri1)
        with get_conn() as conn:
            slot = conn.execute("SELECT * FROM slots WHERE id = ?", (self.slot_id,)).fetchone()
        self.assertTrue(ses.eigenes_spiel(slot, self.schiri1))
        self.assertFalse(ses.eigenes_spiel(slot, self.schiri2))
        self.assertFalse(ses.eigenes_spiel(slot, None))
        self.assertTrue(ses.eigenes_spiel({"started_by_user_id": None}, self.schiri2))


class RouteTests(_TempDb):
    def setUp(self):
        super().setUp()
        from app.main import app
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        super().tearDown()

    def _login(self, name="schiri1", passwort="GeheimesPw1"):
        antwort = self.client.post(
            "/login", data={"username": name, "password": passwort, "next": "/"},
            headers={"CF-Connecting-IP": f"10.1.0.{abs(hash(self.id())) % 250 + 1}"},
            follow_redirects=False,
        )
        self.assertEqual(antwort.status_code, 303)

    def test_konto_seite_ohne_anmeldung(self):
        antwort = self.client.get("/konto")
        self.assertEqual(antwort.status_code, 200)
        self.assertIn("Signale beim Spiel", antwort.text)
        self.assertIn("nur für dieses Gerät", antwort.text)
        self.assertIn("/static/js/signale.js?v=", antwort.text)
        # Speichern geht ohne Konto nicht (der Browser merkt es sich selbst).
        self.assertEqual(self.client.post("/konto/signale", json={"ton": False}).status_code, 400)

    def test_konto_speichern_und_abzeichen_verlinkt(self):
        self._login()
        seite = self.client.get("/konto")
        self.assertIn("Gespeichert für SCHIRI1", seite.text)
        self.assertIn('href="/konto"', seite.text)
        self.assertIn("Test-Push in 15 s", seite.text)
        antwort = self.client.post("/konto/signale", json={"ton": False})
        self.assertEqual(antwort.json(), {
            "ok": True, "einstellungen": {"ton": False, "countdown": True, "benachrichtigung": True},
        })
        self.assertIn('"ton": false', self.client.get("/konto").text)

    def test_start_merkt_den_schiri_und_abo_den_benutzer(self):
        self._login()
        self.client.post(f"/slot/{self.slot_id}/start", follow_redirects=False)
        self.client.post("/push/abo", json={"abo": _abo(5), "court_id": str(self.court_id)})
        with get_conn() as conn:
            starter = conn.execute(
                "SELECT started_by_user_id FROM slots WHERE id = ?", (self.slot_id,)
            ).fetchone()[0]
            abo_user = conn.execute("SELECT user_id FROM push_abos").fetchone()[0]
        self.assertEqual(starter, self.schiri1)
        self.assertEqual(abo_user, self.schiri1)

    def test_feldansicht_markiert_fremde_spiele(self):
        with get_conn() as conn:
            conn.execute(
                "UPDATE slots SET status = 'läuft', started_at = '2026-12-21 09:00:00', started_by_user_id = ? WHERE id = ?",
                (self.schiri2, self.slot_id),
            )
            conn.commit()
        self._login()
        seite = self.client.get(f"/ergebnisse/feld?court_id={self.court_id}")
        self.assertEqual(seite.status_code, 200)
        self.assertIn('data-eigenes="0"', seite.text)
        self.assertIn('href="/konto"', seite.text)
        self.assertNotIn("data-signal-schalter", seite.text)

    def test_test_push_spaeter_prueft_anmeldung(self):
        self._login()
        with patch("app.main.push_schluessel", return_value=object()):
            unbekannt = self.client.post("/push/test", json={
                "endpoint": "https://fcm.googleapis.com/fcm/send/unbekannt", "verzoegerung": 15,
            })
            self.assertEqual(unbekannt.json()["ok"], False)
            self.client.post("/push/abo", json={"abo": _abo(6)})
            with patch("app.main.threading.Timer") as zeitgeber:
                geplant = self.client.post("/push/test", json={"endpoint": _abo(6)["endpoint"], "verzoegerung": 15})
            self.assertEqual(geplant.json(), {"ok": True, "status": None, "geplant": True})
            self.assertEqual(zeitgeber.call_args[0][0], 15)
            zeitgeber.return_value.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
