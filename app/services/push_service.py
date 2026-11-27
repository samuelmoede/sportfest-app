"""Web-Push fuer die Feldansicht: "Noch 1 Minute" und "Spielzeit vorbei".

Bei ausgeschaltetem Bildschirm haelt Android die Webseite an; sie kann dann
weder piepen noch selbst eine Benachrichtigung zeigen. Deshalb schickt der
Server die beiden Meldungen per Web-Push an die Handys, die in der
Feldansicht "Benachrichtigung" eingeschaltet haben (Tabelle push_abos, je
Handy das zuletzt angesehene Feld). Der Push-Dienst des Browsers (bei Android
Google) stellt sie auch bei gesperrtem Handy zu, eine gekoppelte Uhr spiegelt
sie.

Umgesetzt nur mit `cryptography` und der Standardbibliothek (kein pywebpush):
- VAPID (RFC 8292): ES256-signiertes JWT im Authorization-Header. Der
  Schluessel wird aus einem einmalig erzeugten Geheimnis in
  data/.vapid_secret abgeleitet, damit alle Worker-Prozesse denselben nutzen.
- Verschluesselung aes128gcm (RFC 8188 / RFC 8291).

Ein Hintergrund-Takt (starte_takt) prueft alle paar Sekunden die laufenden
Spiele. Mit mehreren Workern laeuft der Takt in jedem Prozess; doppelt
gesendet wird trotzdem nicht, weil jede Meldung zuerst per INSERT OR IGNORE
in push_gesendet eingetragen wird und nur der Prozess sendet, dessen Eintrag
neu war.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import struct
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from app.services.schedule_time_service import get_competition_timing

logger = logging.getLogger(__name__)

# Nur echte Push-Dienste der Browser - sonst koennte jemand den Server
# Anfragen an beliebige (interne) Adressen schicken lassen.
ERLAUBTE_PUSH_HOSTS = (
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    "push.services.mozilla.com",
    ".push.apple.com",
    ".notify.windows.com",
)
MAX_ABOS = 500
KNAPP_SEKUNDEN = 60
ENDE_NACHLAUF_SEKUNDEN = 90   # "Spielzeit vorbei" nur so lange nach dem Ende
TAKT_SEKUNDEN = 5
PUSH_TTL_SEKUNDEN = 60

TITEL = {"knapp": "Noch 1 Minute", "ende": "Spielzeit vorbei"}


def _b64url(daten: bytes) -> str:
    return base64.urlsafe_b64encode(daten).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    text = text.strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ---------------------------------------------------------------- Schluessel

def privater_schluessel(geheimnis: str):
    """Leitet den VAPID-Schluessel (P-256) deterministisch aus dem Geheimnis ab."""
    from cryptography.hazmat.primitives.asymmetric import ec

    ordnung = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
    wert = int.from_bytes(hashlib.sha256(geheimnis.encode("utf-8")).digest(), "big")
    return ec.derive_private_key(wert % (ordnung - 1) + 1, ec.SECP256R1())


def oeffentlicher_schluessel_bytes(privat) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return privat.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def lade_schluessel(datenordner):
    """Privater VAPID-Schluessel oder None, wenn `cryptography` fehlt."""
    try:
        import cryptography  # noqa: F401
    except ImportError:
        return None
    from app.services.session_secret_service import load_or_create_session_secret

    geheimnis = os.getenv("SPORTFEST_VAPID_SECRET") or load_or_create_session_secret(
        datenordner / ".vapid_secret"
    )
    return privater_schluessel(geheimnis)


def oeffentlicher_schluessel_text(privat):
    return _b64url(oeffentlicher_schluessel_bytes(privat)) if privat is not None else None


# ---------------------------------------------------------------- VAPID-JWT

def vapid_header(privat, endpoint: str, absender: str, jetzt=None) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    teile = urlsplit(endpoint)
    jetzt = int(jetzt if jetzt is not None else time.time())
    kopf = _b64url(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    inhalt = _b64url(json.dumps({
        "aud": f"{teile.scheme}://{teile.netloc}",
        "exp": jetzt + 12 * 3600,
        "sub": absender,
    }, separators=(",", ":")).encode())
    signiert = f"{kopf}.{inhalt}".encode("ascii")
    r, s = decode_dss_signature(privat.sign(signiert, ec.ECDSA(hashes.SHA256())))
    signatur = _b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={kopf}.{inhalt}.{signatur}, k={oeffentlicher_schluessel_text(privat)}"


# ---------------------------------------------------------------- aes128gcm

def _hkdf(salz: bytes, ikm: bytes, info: bytes, laenge: int) -> bytes:
    prk = hmac.new(salz, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:laenge]


def verschluessele(nachricht: bytes, p256dh: str, auth: str, salz=None, sender_privat=None) -> bytes:
    """Verschluesselt `nachricht` fuer ein Abo (RFC 8291, ein Datensatz)."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    empfaenger_bytes = _b64url_decode(p256dh)
    auth_geheimnis = _b64url_decode(auth)
    empfaenger = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), empfaenger_bytes)
    sender_privat = sender_privat or ec.generate_private_key(ec.SECP256R1())
    sender_bytes = oeffentlicher_schluessel_bytes(sender_privat)
    gemeinsam = sender_privat.exchange(ec.ECDH(), empfaenger)

    ikm = _hkdf(auth_geheimnis, gemeinsam, b"WebPush: info\x00" + empfaenger_bytes + sender_bytes, 32)
    salz = salz or os.urandom(16)
    schluessel = _hkdf(salz, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salz, ikm, b"Content-Encoding: nonce\x00", 12)
    geheimtext = AESGCM(schluessel).encrypt(nonce, nachricht + b"\x02", None)
    return salz + struct.pack("!IB", 4096, len(sender_bytes)) + sender_bytes + geheimtext


# ---------------------------------------------------------------- Abos

def endpoint_erlaubt(endpoint) -> bool:
    if not isinstance(endpoint, str) or len(endpoint) > 1000:
        return False
    teile = urlsplit(endpoint)
    host = (teile.hostname or "").lower()
    if teile.scheme != "https" or not host:
        return False
    return any(host == h or (h.startswith(".") and host.endswith(h)) for h in ERLAUBTE_PUSH_HOSTS)


def speichere_abo(conn, abo, court_id, absender, jetzt_text, user_id=None) -> bool:
    """Legt ein Abo an oder aktualisiert Feld und Benutzer. Ohne court_id
    (Anmeldung auf "Mein Konto") bleibt ein schon bekanntes Feld erhalten.
    False bei ungueltigen Daten."""
    if not isinstance(abo, dict):
        return False
    endpoint = abo.get("endpoint")
    schluessel = abo.get("keys") or {}
    p256dh, auth = schluessel.get("p256dh"), schluessel.get("auth")
    if not endpoint_erlaubt(endpoint) or not isinstance(p256dh, str) or not isinstance(auth, str):
        return False
    try:
        if len(_b64url_decode(p256dh)) != 65 or len(_b64url_decode(auth)) != 16:
            return False
    except (ValueError, TypeError):
        return False
    conn.execute(
        """
        INSERT INTO push_abos (endpoint, p256dh, auth, court_id, absender, updated_at, user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(endpoint) DO UPDATE SET
            p256dh = excluded.p256dh, auth = excluded.auth,
            court_id = COALESCE(excluded.court_id, push_abos.court_id),
            absender = excluded.absender, updated_at = excluded.updated_at,
            user_id = excluded.user_id
        """,
        (endpoint, p256dh, auth, court_id, absender, jetzt_text, user_id),
    )
    conn.execute(
        """
        DELETE FROM push_abos WHERE endpoint NOT IN (
            SELECT endpoint FROM push_abos ORDER BY updated_at DESC LIMIT ?
        )
        """,
        (MAX_ABOS,),
    )
    return True


def loesche_abo(conn, endpoint):
    conn.execute("DELETE FROM push_abos WHERE endpoint = ?", (endpoint,))


# ---------------------------------------------------------------- Faellig?

def _zeit(text):
    try:
        return datetime.strptime(str(text).strip()[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            return datetime.strptime(str(text).strip()[:16].replace("T", " "), "%Y-%m-%d %H:%M")
        except ValueError:
            return None


def faellige_meldungen(slots, jetzt):
    """[(slot, "knapp"|"ende")] fuer laufende Spiele mit Uhr.

    "Spielzeit vorbei" nur bis ENDE_NACHLAUF_SEKUNDEN nach dem Ende (nach
    einem Neustart keine alten Meldungen), "Noch 1 Minute" nur vor dem Ende.
    """
    faellig = []
    for slot in slots:
        start = _zeit(slot.get("started_at"))
        if start is None or slot.get("timer_stopped_at"):
            continue
        dauer = get_competition_timing(slot)["game_duration_seconds"]
        ende = start + timedelta(seconds=dauer)
        if ende <= jetzt < ende + timedelta(seconds=ENDE_NACHLAUF_SEKUNDEN):
            faellig.append((slot, "ende"))
        elif ende - timedelta(seconds=KNAPP_SEKUNDEN) <= jetzt < ende:
            faellig.append((slot, "knapp"))
    return faellig


LAUFENDE_SPIELE_SQL = """
    SELECT s.id, s.court_id, s.started_at, s.timer_stopped_at, s.started_by_user_id,
           c.name AS competition_name, c.competition_type,
           c.game_duration_minutes, c.game_duration_seconds,
           ta.name AS team_a, tb.name AS team_b, ct.name AS court_name
    FROM slots s
    JOIN competitions c ON c.id = s.competition_id
    LEFT JOIN teams ta ON ta.id = s.team_a_id
    LEFT JOIN teams tb ON tb.id = s.team_b_id
    LEFT JOIN courts ct ON ct.id = s.court_id
    WHERE s.status = 'läuft' AND s.slot_typ = 'Spiel'
      AND s.started_at IS NOT NULL AND s.timer_stopped_at IS NULL
      AND c.competition_type != 'Sechskampf'
"""


def meldung(slot, phase) -> dict:
    paarung = f"{slot.get('team_a') or '?'} – {slot.get('team_b') or '?'}"
    return {
        "titel": f"{TITEL[phase]} · {paarung}",
        "text": " · ".join(t for t in (slot.get("court_name"), slot.get("competition_name")) if t),
        "tag": f"spiel-{slot['id']}",
        "url": f"/ergebnisse/feld?court_id={slot['court_id']}",
    }


def sende(abo, daten: dict, privat, oeffner=urllib.request.urlopen) -> int:
    """Schickt eine Meldung an ein Abo; liefert den HTTP-Status (0 = Netzfehler)."""
    koerper = verschluessele(json.dumps(daten).encode("utf-8"), abo["p256dh"], abo["auth"])
    anfrage = urllib.request.Request(
        abo["endpoint"],
        data=koerper,
        method="POST",
        headers={
            "Authorization": vapid_header(privat, abo["endpoint"], abo["absender"] or "mailto:sportfest@example.org"),
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(PUSH_TTL_SEKUNDEN),
            "Urgency": "high",
        },
    )
    try:
        with oeffner(anfrage, timeout=10) as antwort:
            return antwort.status
    except urllib.error.HTTPError as fehler:
        return fehler.code
    except OSError:
        return 0


def empfaenger(conn, slot):
    """Abos, die eine Meldung zu diesem Spiel bekommen: nur die Handys des
    Benutzers, der das Spiel gestartet hat (sofern er Benachrichtigungen
    eingeschaltet hat). Hat niemand angemeldet gestartet, gilt der alte Weg:
    Handys ohne Konto, die fuer dieses Feld angemeldet sind."""
    starter = slot.get("started_by_user_id")
    if starter is not None:
        sql = """
            SELECT a.* FROM push_abos a
            JOIN users u ON u.id = a.user_id
            WHERE a.user_id = ? AND u.signal_benachrichtigung = 1
        """
        werte = (starter,)
    else:
        sql = "SELECT * FROM push_abos WHERE court_id = ? AND user_id IS NULL"
        werte = (slot["court_id"],)
    return [dict(row) for row in conn.execute(sql, werte).fetchall()]


def verarbeite(conn, jetzt, privat, sender=sende) -> int:
    """Ein Takt: faellige Meldungen eintragen und senden. Liefert die Anzahl
    gesendeter Push-Nachrichten."""
    slots = [dict(row) for row in conn.execute(LAUFENDE_SPIELE_SQL).fetchall()]
    gesendet = 0
    for slot, phase in faellige_meldungen(slots, jetzt):
        neu = conn.execute(
            "INSERT OR IGNORE INTO push_gesendet (slot_id, started_at, phase, gesendet_at) VALUES (?, ?, ?, ?)",
            (slot["id"], slot["started_at"], phase, jetzt.strftime("%Y-%m-%d %H:%M:%S")),
        ).rowcount
        conn.commit()
        if not neu:
            continue
        abos = empfaenger(conn, slot)
        daten = meldung(slot, phase)
        for abo in abos:
            status = sender(abo, daten, privat)
            if status in (404, 410):
                loesche_abo(conn, abo["endpoint"])
                conn.commit()
            elif 200 <= status < 300:
                gesendet += 1
            else:
                logger.warning("Web-Push an %s fehlgeschlagen (Status %s)", urlsplit(abo["endpoint"]).hostname, status)
    return gesendet


def sende_test(conn, endpoint, privat, sender=None):
    """Schickt sofort eine Test-Meldung an ein angemeldetes Handy.

    Liefert den HTTP-Status des Push-Dienstes (201 = angenommen, 0 =
    Server erreicht den Dienst nicht) oder None, wenn das Handy nicht
    angemeldet ist.
    """
    zeile = conn.execute("SELECT * FROM push_abos WHERE endpoint = ?", (endpoint,)).fetchone()
    if zeile is None:
        return None
    status = (sender or sende)(dict(zeile), {
        "titel": "Test · Sportfest",
        "text": "So kommt „Noch 1 Minute“ an.",
        "tag": "test",
        "url": f"/ergebnisse/feld?court_id={zeile['court_id']}" if zeile["court_id"] else "/ergebnisse/feld",
    }, privat)
    if status in (404, 410):
        loesche_abo(conn, endpoint)
        conn.commit()
    return status


_takt_gestartet = False
_takt_sperre = threading.Lock()


def starte_takt(get_conn, jetzt_funktion, privat):
    """Startet den Hintergrund-Takt einmal je Prozess."""
    global _takt_gestartet
    if privat is None:
        return
    with _takt_sperre:
        if _takt_gestartet:
            return
        _takt_gestartet = True

    def schleife():
        while True:
            time.sleep(TAKT_SEKUNDEN)
            try:
                with get_conn() as conn:
                    if conn.execute("SELECT 1 FROM push_abos LIMIT 1").fetchone():
                        verarbeite(conn, jetzt_funktion(), privat)
            except Exception:  # der Takt darf nie die App stoeren
                logger.exception("Web-Push-Takt fehlgeschlagen")

    threading.Thread(target=schleife, name="web-push-takt", daemon=True).start()
