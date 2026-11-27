"""Echte Browser-Tests fuer das Signal vor Spielende in der Feldansicht
(/ergebnisse/feld): eine Minute vor Ablauf kurz Ton + Vibration, am Ende
lang. Eingestellt und getestet wird auf "Mein Konto" (/konto); ohne
Anmeldung merkt sich der Browser die Schalter. Signale gibt es nur fuer
Spiele, die man selbst gestartet hat. Ton (Web Audio) und Vibration werden im Browser durch Attrappen
ersetzt, die jeden Aufruf mitschreiben - echte Toene kann ein Test nicht
hoeren, wohl aber pruefen, dass und wann sie angestossen werden.
"""
import pytest

from tests.browser.conftest import chromium_available

pytestmark = pytest.mark.skipif(
    not chromium_available(),
    reason="Chromium-Browser-Binary nicht installiert (python -m playwright install chromium)",
)

MOBILE_VIEWPORT = {"width": 390, "height": 844}

ATTRAPPEN_JS = """
window.__signale = [];
navigator.vibrate = muster => { window.__signale.push({ art: 'vibration', muster }); return true; };
class AttrappenAudio {
    constructor() { this.state = 'running'; this.currentTime = 0; this.destination = {}; }
    resume() { return Promise.resolve(); }
    createGain() {
        const knoten = () => {};
        return { gain: { setValueAtTime: knoten, exponentialRampToValueAtTime: knoten }, connect: knoten };
    }
    createOscillator() {
        const ton = { frequency: { value: 0 }, connect: () => {}, stop: () => {} };
        ton.start = () => window.__signale.push({ art: 'ton', frequenz: ton.frequency.value });
        return ton;
    }
}
window.AudioContext = AttrappenAudio;
window.webkitAudioContext = AttrappenAudio;
"""

# Startzeit so, wie der Browser sie liest (lokale Zeit, siehe leseZeit()).
VOR_SEKUNDEN_JS = """
sekunden => {
    const d = new Date(Date.now() - sekunden * 1000);
    const z = w => String(w).padStart(2, '0');
    return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())} ${z(d.getHours())}:${z(d.getMinutes())}:${z(d.getSeconds())}`;
}
"""


def _laufendes_spiel(name, spielzeit_sekunden, started_at, starter=None):
    """Legt ein eigenes Feld mit einem laufenden Spiel an und gibt die
    Feld-ID zurueck (eigener Ort, damit andere Browser-Tests nicht stoeren)."""
    import app.database as database

    with database.get_conn() as conn:
        court_id = conn.execute(
            "INSERT INTO courts (name, sportart, location) VALUES (?, 'Zweifelderball', 'Signalhalle')",
            (name,),
        ).lastrowid
        comp_id = conn.execute(
            """
            INSERT INTO competitions (name, sportart, jahrgang, status, competition_type, location,
                                      game_duration_minutes, game_duration_seconds)
            VALUES (?, 'Zweifelderball', 7, 'geplant', 'Turnier', 'Signalhalle', 1, ?)
            """,
            (f"Signal {name}", spielzeit_sekunden),
        ).lastrowid
        team_a = conn.execute("SELECT id FROM teams WHERE name = '7a'").fetchone()["id"]
        team_b = conn.execute("SELECT id FROM teams WHERE name = '7b'").fetchone()["id"]
        conn.execute(
            """
            INSERT INTO slots (startzeit, slot_typ, competition_id, court_id, team_a_id, team_b_id,
                               phase, gruppe, status, started_at, started_by_user_id)
            VALUES ('09:00', 'Spiel', ?, ?, ?, ?, 'Gruppenphase', 'A', 'läuft', ?, ?)
            """,
            (comp_id, court_id, team_a, team_b, started_at, starter),
        )
        conn.commit()
    return court_id


def _signale(page, art):
    return [s for s in page.evaluate("window.__signale") if s["art"] == art]


def test_letzte_minute_gibt_kurzes_signal(live_server_url, page):
    page.add_init_script(ATTRAPPEN_JS)
    page.set_viewport_size(MOBILE_VIEWPORT)
    page.goto(f"{live_server_url}/impressum")
    # Noch 62 s Restzeit: beim Laden kein Signal, nach ~2 s die letzte Minute.
    court_id = _laufendes_spiel("Signal-Feld 1", 120, page.evaluate(VOR_SEKUNDEN_JS, 58))
    page.goto(f"{live_server_url}/ergebnisse/feld?court_id={court_id}")

    karte = page.locator(".fa-jetzt")
    assert karte.count() == 1
    assert page.locator("[data-signal-zusammenfassung]").inner_text().startswith("Ton an")
    assert page.evaluate("window.__signale") == []

    page.wait_for_selector(".fa-jetzt.signal-knapp", timeout=6000)
    assert page.locator(".fa-zeitansage").inner_text() == "Letzte Minute"
    assert _signale(page, "vibration") == [{"art": "vibration", "muster": [200, 100, 200]}]
    assert len(_signale(page, "ton")) == 2


def test_countdown_und_ende_beenden_nichts(live_server_url, page):
    page.add_init_script(ATTRAPPEN_JS)
    page.set_viewport_size(MOBILE_VIEWPORT)
    page.goto(f"{live_server_url}/impressum")
    # Mit ~7 s Restzeit geladen: fuer die letzte Minute kein Nach-Piepen,
    # aber der Countdown 5-4-3-2-1 (je einmal kurz vibrieren, ohne Ton) und das Ende.
    court_id = _laufendes_spiel("Signal-Feld 2", 30, page.evaluate(VOR_SEKUNDEN_JS, 22))
    page.goto(f"{live_server_url}/ergebnisse/feld?court_id={court_id}")

    page.wait_for_selector(".fa-jetzt.signal-knapp", timeout=3000)
    assert page.evaluate("window.__signale") == []

    page.wait_for_selector(".fa-jetzt.signal-ende", timeout=13000)
    assert page.locator(".fa-zeitansage").inner_text() == "Spielzeit vorbei"
    vibrationen = [s["muster"] for s in _signale(page, "vibration")]
    assert vibrationen == [120, 120, 120, 120, 120, [600, 200, 600, 200, 1000]], vibrationen
    # Ton nur am Ende (der Countdown vibriert nur).
    assert len(_signale(page, "ton")) == 3
    # Nur ein Hinweis: das Spiel laeuft weiter, die Uhr zaehlt die Ueberziehung.
    assert page.locator(".fa-jetzt [data-aktion='beenden']").is_visible()
    page.wait_for_function("document.querySelector('.fa-uhr').textContent.startsWith('+')")


def test_signal_laesst_sich_auf_mein_konto_abschalten(live_server_url, page):
    page.add_init_script(ATTRAPPEN_JS)
    page.set_viewport_size(MOBILE_VIEWPORT)
    # Ohne Anmeldung gilt die Wahl fuer dieses Geraet (Browser-Speicher).
    page.goto(f"{live_server_url}/konto")
    assert page.locator("[data-schalter='ton']").is_checked()
    page.locator("[data-schalter='ton']").uncheck()
    page.wait_for_selector("[data-speichern-meldung]:not([hidden])")

    court_id = _laufendes_spiel("Signal-Feld 3", 120, page.evaluate(VOR_SEKUNDEN_JS, 58))
    page.goto(f"{live_server_url}/ergebnisse/feld?court_id={court_id}")
    assert page.locator("[data-signal-zusammenfassung]").inner_text().startswith("Ton aus")
    page.wait_for_selector(".fa-jetzt.signal-knapp", timeout=6000)
    # Farbe und Ansage bleiben, nur Ton und Vibration entfallen.
    assert page.evaluate("window.__signale") == []

    # Testen geht ohne laufendes Spiel (und auch bei ausgeschaltetem Ton).
    page.goto(f"{live_server_url}/konto")
    assert not page.locator("[data-schalter='ton']").is_checked()
    page.locator("[data-aktion='ton']").click()
    assert len(_signale(page, "ton")) == 3


def test_countdown_testen_auf_mein_konto(live_server_url, page):
    page.add_init_script(ATTRAPPEN_JS)
    page.set_viewport_size(MOBILE_VIEWPORT)
    page.goto(f"{live_server_url}/konto")
    knopf = page.locator("[data-aktion='countdown']")
    knopf.click()
    page.wait_for_function("document.querySelector('[data-aktion=countdown]').textContent === '3'")
    page.wait_for_function("document.querySelector('[data-aktion=countdown]').textContent === 'Countdown testen'", timeout=10000)
    vibrationen = [s["muster"] for s in _signale(page, "vibration")]
    assert vibrationen == [120, 120, 120, 120, 120, [600, 200, 600, 200, 1000]], vibrationen


def test_fremd_gestartetes_spiel_gibt_kein_signal(live_server_url, page):
    """Schiris wechseln sich ab: Ton nur fuer den, der das Spiel gestartet hat."""
    page.add_init_script(ATTRAPPEN_JS)
    page.set_viewport_size(MOBILE_VIEWPORT)
    page.goto(f"{live_server_url}/impressum")
    court_id = _laufendes_spiel("Signal-Feld 6", 120, page.evaluate(VOR_SEKUNDEN_JS, 58), starter=4711)
    page.goto(f"{live_server_url}/ergebnisse/feld?court_id={court_id}")
    assert page.locator(".fa-jetzt").get_attribute("data-eigenes") == "0"
    assert "selbst gestartet" in page.locator("[data-signal-zusammenfassung]").inner_text()
    page.wait_for_selector(".fa-jetzt.signal-knapp", timeout=6000)
    page.wait_for_timeout(300)
    assert page.evaluate("window.__signale") == []


def test_mehrere_gleichzeitig_gestartete_spiele_ein_signal(live_server_url, page):
    """Runden-Timer: zwei eigene Spiele mit gleicher Startzeit auf einem
    Feld - ein Ton, eine Vibration, nicht doppelt."""
    import app.database as database

    page.add_init_script(ATTRAPPEN_JS)
    page.set_viewport_size(MOBILE_VIEWPORT)
    page.goto(f"{live_server_url}/impressum")
    start = page.evaluate(VOR_SEKUNDEN_JS, 58)
    court_id = _laufendes_spiel("Signal-Feld 7", 120, start)
    with database.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO slots (startzeit, slot_typ, competition_id, court_id, team_a_id, team_b_id,
                               phase, gruppe, status, started_at)
            SELECT startzeit, slot_typ, competition_id, court_id, team_b_id, team_a_id,
                   phase, gruppe, status, started_at
            FROM slots WHERE court_id = ?
            """,
            (court_id,),
        )
        conn.commit()
    page.goto(f"{live_server_url}/ergebnisse/feld?court_id={court_id}")
    assert page.locator(".fa-jetzt[data-slot]").count() == 2

    page.wait_for_selector(".fa-jetzt.signal-knapp", timeout=6000)
    page.wait_for_timeout(500)
    assert _signale(page, "vibration") == [{"art": "vibration", "muster": [200, 100, 200]}]
    assert len(_signale(page, "ton")) == 2


BENACHRICHTIGUNG_ATTRAPPE_JS = """
ServiceWorkerRegistration.prototype.showNotification = function (titel, optionen) {
    window.__signale.push({ art: 'benachrichtigung', titel, tag: optionen.tag, text: optionen.body });
    return Promise.resolve();
};
"""


def test_benachrichtigung_fuer_smartwatch(live_server_url, browser):
    # Eigener Kontext, damit die Erlaubnis keine anderen Tests beeinflusst.
    kontext = browser.new_context(viewport=MOBILE_VIEWPORT)
    kontext.grant_permissions(["notifications"], origin=live_server_url)
    page = kontext.new_page()
    # Headless-Chromium meldet Notification.permission trotz Freigabe als
    # "denied"; die Erlaubnis selbst gehoert dem Browser, nicht der App.
    page.add_init_script("""
        Object.defineProperty(Notification, 'permission', { get: () => 'granted' });
        Notification.requestPermission = () => Promise.resolve('granted');
    """)
    page.add_init_script(ATTRAPPEN_JS)
    page.add_init_script(BENACHRICHTIGUNG_ATTRAPPE_JS)
    # Auf "Mein Konto" einschalten (ohne Anmeldung: fuer dieses Geraet).
    page.goto(f"{live_server_url}/konto")
    schalter = page.locator("[data-schalter='benachrichtigung']")
    assert not schalter.is_checked()
    schalter.check()
    page.wait_for_function("navigator.serviceWorker.getRegistration('/static/js/').then(r => !!(r && r.active))")
    page.wait_for_selector("[data-status='erlaubnis'].ok")

    court_id = _laufendes_spiel("Signal-Feld 4", 120, page.evaluate(VOR_SEKUNDEN_JS, 56))
    page.goto(f"{live_server_url}/ergebnisse/feld?court_id={court_id}")
    assert "Benachrichtigung an" in page.locator("[data-signal-zusammenfassung]").inner_text()
    assert page.locator("[data-benachrichtigung-hinweis]").is_hidden()

    page.wait_for_selector(".fa-jetzt.signal-knapp", timeout=8000)
    page.wait_for_function("window.__signale.some(s => s.art === 'benachrichtigung')")
    meldungen = [s for s in page.evaluate("window.__signale") if s["art"] == "benachrichtigung"]
    assert len(meldungen) == 1, meldungen
    assert meldungen[0]["titel"] == "Noch 1 Minute · 7a – 7b"
    assert meldungen[0]["text"] == "Signal-Feld 4 · Signal Signal-Feld 4"
    assert meldungen[0]["tag"].startswith("spiel-")

    # Wahl bleibt erhalten.
    page.goto(f"{live_server_url}/konto")
    assert page.locator("[data-schalter='benachrichtigung']").is_checked()
    kontext.close()


def test_benachrichtigung_standardmaessig_aus(live_server_url, page):
    page.add_init_script(ATTRAPPEN_JS)
    page.add_init_script(BENACHRICHTIGUNG_ATTRAPPE_JS)
    page.set_viewport_size(MOBILE_VIEWPORT)
    page.goto(f"{live_server_url}/impressum")
    court_id = _laufendes_spiel("Signal-Feld 5", 120, page.evaluate(VOR_SEKUNDEN_JS, 58))
    page.goto(f"{live_server_url}/ergebnisse/feld?court_id={court_id}")

    page.wait_for_selector(".fa-jetzt.signal-knapp", timeout=6000)
    page.wait_for_timeout(300)
    assert [s for s in page.evaluate("window.__signale") if s["art"] == "benachrichtigung"] == []
