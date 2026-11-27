"""Web-Push fuer die Feldansicht (app/services/push_service.py): "Noch 1
Minute" und "Spielzeit vorbei" auch bei ausgeschaltetem Bildschirm.

Geprueft werden die Verschluesselung (per unabhaengiger Entschluesselung wie
im Browser), das VAPID-JWT, die Auswahl faelliger Meldungen, der Schutz vor
Doppelten bei mehreren Workern und die Anmelde-Route.
"""
import base64
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi.testclient import TestClient

import app.database as database
from app.database import get_conn, init_db
from app.services import push_service as ps


def _b64(daten):
    return base64.urlsafe_b64encode(daten).rstrip(b"=").decode()


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _hkdf(salz, ikm, info, laenge):
    return HKDF(algorithm=hashes.SHA256(), length=laenge, salt=salz, info=info).derive(ikm)


def _browser_abo():
    """Schluessel wie ein Browser sie fuer ein Push-Abo erzeugt."""
    privat = ec.generate_private_key(ec.SECP256R1())
    oeffentlich = privat.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    auth = b"0123456789abcdef"
    return privat, oeffentlich, auth


def _entschluessele(koerper, privat, oeffentlich, auth):
    """Entschluesselung nach RFC 8291 wie im Browser (unabhaengig vom Code)."""
    salz, rs, id_laenge = koerper[:16], int.from_bytes(koerper[16:20], "big"), koerper[20]
    sender = koerper[21:21 + id_laenge]
    geheimtext = koerper[21 + id_laenge:]
    assert rs == 4096
    gemeinsam = privat.exchange(
        ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender)
    )
    ikm = _hkdf(auth, gemeinsam, b"WebPush: info\x00" + oeffentlich + sender, 32)
    schluessel = _hkdf(salz, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salz, ikm, b"Content-Encoding: nonce\x00", 12)
    klartext = AESGCM(schluessel).decrypt(nonce, geheimtext, None)
    assert klartext.endswith(b"\x02")
    return klartext[:-1]


class VerschluesselungTests(unittest.TestCase):
    def test_browser_kann_entschluesseln(self):
        privat, oeffentlich, auth = _browser_abo()
        koerper = ps.verschluessele(b'{"titel": "Noch 1 Minute"}', _b64(oeffentlich), _b64(auth))
        self.assertEqual(_entschluessele(koerper, privat, oeffentlich, auth), b'{"titel": "Noch 1 Minute"}')

    def test_beispiel_aus_rfc_8291(self):
        # Anhang A von RFC 8291: feste Schluessel und Salz, erwartete Nachricht.
        from cryptography.hazmat.primitives.asymmetric import ec

        server = ec.derive_private_key(int.from_bytes(_unb64("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), ec.SECP256R1())
        koerper = ps.verschluessele(
            b"When I grow up, I want to be a watermelon",
            "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
            "BTBZMqHH6r4Tts7J_aSIgg",
            salz=_unb64("DGv6ra1nlYgDCS1FRnbzlw"),
            sender_privat=server,
        )
        self.assertEqual(_b64(koerper), (
            "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_"
            "yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
        ))

    def test_vapid_jwt_ist_mit_oeffentlichem_schluessel_pruefbar(self):
        privat = ps.privater_schluessel("geheim")
        kopfzeile = ps.vapid_header(privat, "https://fcm.googleapis.com/fcm/send/abc", "https://sportfest.example", jetzt=1000)
        self.assertTrue(kopfzeile.startswith("vapid t="))
        token, k = kopfzeile[len("vapid t="):].split(", k=")
        kopf, inhalt, signatur = token.split(".")
        daten = json.loads(_unb64(inhalt))
        self.assertEqual(daten, {"aud": "https://fcm.googleapis.com", "exp": 1000 + 12 * 3600, "sub": "https://sportfest.example"})
        oeffentlich = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), _unb64(k))
        roh = _unb64(signatur)
        der = encode_dss_signature(int.from_bytes(roh[:32], "big"), int.from_bytes(roh[32:], "big"))
        oeffentlich.verify(der, f"{kopf}.{inhalt}".encode(), ec.ECDSA(hashes.SHA256()))  # wirft bei Fehler

    def test_schluessel_ist_je_geheimnis_gleich(self):
        a = ps.oeffentlicher_schluessel_text(ps.privater_schluessel("x"))
        self.assertEqual(a, ps.oeffentlicher_schluessel_text(ps.privater_schluessel("x")))
        self.assertNotEqual(a, ps.oeffentlicher_schluessel_text(ps.privater_schluessel("y")))
        self.assertEqual(len(_unb64(a)), 65)


class EndpointTests(unittest.TestCase):
    def test_nur_push_dienste_der_browser(self):
        self.assertTrue(ps.endpoint_erlaubt("https://fcm.googleapis.com/fcm/send/abc"))
        self.assertTrue(ps.endpoint_erlaubt("https://web.push.apple.com/abc"))
        self.assertTrue(ps.endpoint_erlaubt("https://updates.push.services.mozilla.com/wpush/v2/abc"))
        self.assertFalse(ps.endpoint_erlaubt("http://fcm.googleapis.com/fcm/send/abc"))
        self.assertFalse(ps.endpoint_erlaubt("https://192.168.178.20/abc"))
        self.assertFalse(ps.endpoint_erlaubt("https://evilpush.apple.com.example.org/abc"))
        self.assertFalse(ps.endpoint_erlaubt(None))


def _slot(started_at, dauer=480, **extra):
    slot = {"id": 1, "court_id": 2, "started_at": started_at, "timer_stopped_at": None,
            "game_duration_minutes": dauer // 60, "game_duration_seconds": dauer}
    slot.update(extra)
    return slot


class FaelligTests(unittest.TestCase):
    START = datetime(2026, 12, 21, 9, 0, 0)

    def _phasen(self, sekunden_nach_start, **extra):
        slot = _slot("2026-12-21 09:00:00", **extra)
        return [p for _, p in ps.faellige_meldungen([slot], self.START + timedelta(seconds=sekunden_nach_start))]

    def test_phasen_ueber_die_spielzeit(self):
        self.assertEqual(self._phasen(100), [])
        self.assertEqual(self._phasen(419), [])
        self.assertEqual(self._phasen(420), ["knapp"])
        self.assertEqual(self._phasen(479), ["knapp"])
        self.assertEqual(self._phasen(480), ["ende"])
        self.assertEqual(self._phasen(480 + 89), ["ende"])
        # Lange nach dem Ende (z.B. nach einem Neustart) keine alten Meldungen.
        self.assertEqual(self._phasen(480 + 90), [])

    def test_gestoppte_uhr_meldet_nichts(self):
        self.assertEqual(self._phasen(480, timer_stopped_at="2026-12-21 09:05:00"), [])


class VerarbeiteTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._patcher = patch.object(database, "DB_PATH", Path(self._tmpdir.name) / "push.db")
        self._patcher.start()
        init_db()
        with get_conn() as conn:
            self.court_id = conn.execute("INSERT INTO courts (name) VALUES ('Feld 2')").lastrowid
            anderes_feld = conn.execute("INSERT INTO courts (name) VALUES ('Feld 3')").lastrowid
            comp_id = conn.execute(
                """
                INSERT INTO competitions (name, sportart, jahrgang, status, competition_type,
                                          game_duration_minutes, game_duration_seconds)
                VALUES ('Zweifelderball Jg. 7', 'Zweifelderball', 7, 'geplant', 'Turnier', 8, 480)
                """
            ).lastrowid
            a = conn.execute("INSERT INTO teams (name, jahrgang) VALUES ('7a', 7)").lastrowid
            b = conn.execute("INSERT INTO teams (name, jahrgang) VALUES ('7c', 7)").lastrowid
            self.slot_id = conn.execute(
                """
                INSERT INTO slots (competition_id, court_id, startzeit, slot_typ, phase,
                                   team_a_id, team_b_id, status, started_at)
                VALUES (?, ?, '09:00', 'Spiel', 'Gruppenphase', ?, ?, 'läuft', '2026-12-21 09:00:00')
                """,
                (comp_id, self.court_id, a, b),
            ).lastrowid
            for nr, feld in ((1, self.court_id), (2, self.court_id), (3, anderes_feld)):
                self.assertTrue(ps.speichere_abo(conn, {
                    "endpoint": f"https://fcm.googleapis.com/fcm/send/{nr}",
                    "keys": {"p256dh": _b64(b"\x04" + b"\x01" * 64), "auth": _b64(b"a" * 16)},
                }, feld, "https://sportfest.example", "2026-12-21 08:00:00"))
            conn.commit()
        self.gesendet = []

    def tearDown(self):
        self._patcher.stop()
        self._tmpdir.cleanup()

    def _sender(self, status=201):
        def sender(abo, daten, privat):
            self.gesendet.append((abo["endpoint"], daten))
            return status
        return sender

    def test_meldung_geht_nur_an_handys_dieses_felds_und_nur_einmal(self):
        jetzt = datetime(2026, 12, 21, 9, 7, 10)
        with get_conn() as conn:
            self.assertEqual(ps.verarbeite(conn, jetzt, None, sender=self._sender()), 2)
            # Zweiter Takt (oder zweiter Worker): nichts doppelt.
            self.assertEqual(ps.verarbeite(conn, jetzt + timedelta(seconds=5), None, sender=self._sender()), 0)
        self.assertEqual(sorted(e for e, _ in self.gesendet), [
            "https://fcm.googleapis.com/fcm/send/1", "https://fcm.googleapis.com/fcm/send/2",
        ])
        daten = self.gesendet[0][1]
        self.assertEqual(daten["titel"], "Noch 1 Minute · 7a – 7c")
        self.assertEqual(daten["text"], "Feld 2 · Zweifelderball Jg. 7")
        self.assertEqual(daten["tag"], f"spiel-{self.slot_id}")
        self.assertEqual(daten["url"], f"/ergebnisse/feld?court_id={self.court_id}")

        # Am Ende die zweite Meldung.
        self.gesendet.clear()
        with get_conn() as conn:
            self.assertEqual(ps.verarbeite(conn, datetime(2026, 12, 21, 9, 8, 2), None, sender=self._sender()), 2)
        self.assertTrue(all(d["titel"].startswith("Spielzeit vorbei") for _, d in self.gesendet))

    def test_abgelaufenes_abo_wird_geloescht(self):
        with get_conn() as conn:
            ps.verarbeite(conn, datetime(2026, 12, 21, 9, 7, 10), None, sender=self._sender(status=410))
            rest = conn.execute("SELECT COUNT(*) FROM push_abos").fetchone()[0]
        self.assertEqual(rest, 1)  # nur das Abo des anderen Felds bleibt

    def test_test_push_geht_nur_an_dieses_handy(self):
        with get_conn() as conn:
            status = ps.sende_test(conn, "https://fcm.googleapis.com/fcm/send/1", None, sender=self._sender())
            self.assertEqual(status, 201)
            self.assertEqual([e for e, _ in self.gesendet], ["https://fcm.googleapis.com/fcm/send/1"])
            self.assertEqual(self.gesendet[0][1]["tag"], "test")
            # Unbekanntes Handy: nichts senden.
            self.assertIsNone(ps.sende_test(conn, "https://fcm.googleapis.com/fcm/send/99", None, sender=self._sender()))
            # Abgelaufenes Abo wird beim Test geloescht.
            self.assertEqual(ps.sende_test(conn, "https://fcm.googleapis.com/fcm/send/2", None, sender=self._sender(410)), 410)
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM push_abos WHERE endpoint = 'https://fcm.googleapis.com/fcm/send/2'"
            ).fetchone())

    def test_neustart_des_spiels_meldet_erneut(self):
        with get_conn() as conn:
            ps.verarbeite(conn, datetime(2026, 12, 21, 9, 7, 10), None, sender=self._sender())
            conn.execute("UPDATE slots SET started_at = '2026-12-21 09:10:00' WHERE id = ?", (self.slot_id,))
            conn.commit()
            self.assertEqual(ps.verarbeite(conn, datetime(2026, 12, 21, 9, 17, 10), None, sender=self._sender()), 2)


class RouteTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._patcher = patch.object(database, "DB_PATH", Path(self._tmpdir.name) / "push-route.db")
        self._patcher.start()
        from app.main import app
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self._patcher.stop()
        self._tmpdir.cleanup()

    def _abo(self, endpoint="https://fcm.googleapis.com/fcm/send/xyz"):
        return {"endpoint": endpoint, "keys": {"p256dh": _b64(b"\x04" + b"\x02" * 64), "auth": _b64(b"b" * 16)}}

    def test_anmelden_feld_wechseln_und_abmelden(self):
        antwort = self.client.post("/push/abo", json={"abo": self._abo(), "court_id": "3"},
                                   headers={"host": "sportfest.example"})
        self.assertEqual(antwort.json(), {"ok": True})
        self.client.post("/push/abo", json={"abo": self._abo(), "court_id": "4"})
        with get_conn() as conn:
            zeilen = conn.execute("SELECT court_id, absender FROM push_abos").fetchall()
        self.assertEqual([(z["court_id"], z["absender"]) for z in zeilen], [(4, "https://testserver")])

        self.client.post("/push/abmelden", json={"endpoint": self._abo()["endpoint"]})
        with get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM push_abos").fetchone()[0], 0)

    def test_test_push_route(self):
        self.client.post("/push/abo", json={"abo": self._abo(), "court_id": "3"})
        with patch.object(ps, "sende", return_value=201) as gesendet:
            antwort = self.client.post("/push/test", json={"endpoint": self._abo()["endpoint"]})
        self.assertEqual(antwort.json(), {"ok": True, "status": 201})
        self.assertEqual(gesendet.call_count, 1)
        unbekannt = self.client.post("/push/test", json={"endpoint": "https://fcm.googleapis.com/fcm/send/unbekannt"})
        self.assertEqual(unbekannt.json(), {"ok": False, "status": None})

    def test_fremde_adresse_wird_abgelehnt(self):
        antwort = self.client.post("/push/abo", json={"abo": self._abo("https://192.168.178.1/x"), "court_id": "3"})
        self.assertEqual(antwort.status_code, 400)
        self.assertEqual(self.client.post("/push/abo", content=b"kein json").status_code, 400)

    def test_feldansicht_liefert_oeffentlichen_schluessel(self):
        seite = self.client.get("/ergebnisse/feld").text
        self.assertIn('data-push-schluessel="', seite)
        schluessel = seite.split('data-push-schluessel="', 1)[1].split('"', 1)[0]
        self.assertEqual(len(_unb64(schluessel)), 65)

    def test_service_worker_url_traegt_inhalts_hash(self):
        # Sonst liefert der Cloudflare-Edge-Cache den Worker noch alt aus.
        import hashlib
        from app.web import APP_DIR

        inhalt = (APP_DIR / "static" / "js" / "benachrichtigung-sw.js").read_bytes()
        erwartet = hashlib.sha256(inhalt).hexdigest()[:12]
        seite = self.client.get("/ergebnisse/feld").text
        self.assertIn(f"/static/js/benachrichtigung-sw.js?v={erwartet}", seite)


if __name__ == "__main__":
    unittest.main()
