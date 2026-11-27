"""Echte Browser-Tests fuer das Signal vor Spielende in der Feldansicht
(/ergebnisse/feld): eine Minute vor Ablauf kurz Ton + Vibration, am Ende
lang. Ton (Web Audio) und Vibration werden im Browser durch Attrappen
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


def _laufendes_spiel(name, spielzeit_sekunden, started_at):
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
                               phase, gruppe, status, started_at)
            VALUES ('09:00', 'Spiel', ?, ?, ?, ?, 'Gruppenphase', 'A', 'läuft', ?)
            """,
            (comp_id, court_id, team_a, team_b, started_at),
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
    assert page.locator("[data-signal-schalter]").is_checked()
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
    court_id = _laufendes_spiel("Signal-Feld 2", 30, page.evaluate(VOR_SEKUNDEN_JS, 23))
    page.goto(f"{live_server_url}/ergebnisse/feld?court_id={court_id}")

    page.wait_for_selector(".fa-jetzt.signal-knapp", timeout=3000)
    assert page.evaluate("window.__signale") == []

    page.wait_for_selector(".fa-jetzt.signal-ende", timeout=12000)
    assert page.locator(".fa-zeitansage").inner_text() == "Spielzeit vorbei"
    vibrationen = [s["muster"] for s in _signale(page, "vibration")]
    assert vibrationen == [120, 120, 120, 120, 120, [600, 200, 600, 200, 1000]], vibrationen
    # Ton nur am Ende (der Countdown vibriert nur).
    assert len(_signale(page, "ton")) == 3
    # Nur ein Hinweis: das Spiel laeuft weiter, die Uhr zaehlt die Ueberziehung.
    assert page.locator(".fa-jetzt [data-aktion='beenden']").is_visible()
    page.wait_for_function("document.querySelector('.fa-uhr').textContent.startsWith('+')")


def test_signal_laesst_sich_auf_dem_geraet_abschalten(live_server_url, page):
    page.add_init_script(ATTRAPPEN_JS)
    page.set_viewport_size(MOBILE_VIEWPORT)
    page.goto(f"{live_server_url}/impressum")
    court_id = _laufendes_spiel("Signal-Feld 3", 120, page.evaluate(VOR_SEKUNDEN_JS, 58))
    page.goto(f"{live_server_url}/ergebnisse/feld?court_id={court_id}")

    page.locator("[data-signal-schalter]").uncheck()
    page.wait_for_selector(".fa-jetzt.signal-knapp", timeout=6000)
    # Farbe und Ansage bleiben, nur Ton und Vibration entfallen.
    assert page.evaluate("window.__signale") == []

    # Die Wahl gilt auch nach dem Neuladen.
    page.reload()
    assert not page.locator("[data-signal-schalter]").is_checked()

    page.locator("[data-aktion='signal-testen']").click()
    assert len(_signale(page, "ton")) == 3
