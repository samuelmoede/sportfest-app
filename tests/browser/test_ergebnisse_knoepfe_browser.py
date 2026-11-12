"""Echte Browser-Tests fuer die Knopfleiste Start/Speichern/Beenden im
Ergebnis-Raster (/ergebnisse, Zeit x Feld).

In schmalen Feld-Spalten brach "Speichern" mitten im Wort zu "Speicher/n"
um und "Start rueckgaengig" wurde dreizeilig, weil die Knoepfe per
min-width: 0 unter ihre Textbreite schrumpfen durften und
overflow-wrap: anywhere Umbrueche mitten im Wort erlaubte. Jetzt bleibt jede
Beschriftung einzeilig; reicht der Platz nicht, rutscht der ganze Knopf in
die naechste Zeile. Das laesst sich nur mit echtem Layout pruefen.
"""
import pytest

from tests.browser.conftest import chromium_available

pytestmark = pytest.mark.skipif(
    not chromium_available(),
    reason="Chromium-Browser-Binary nicht installiert (python -m playwright install chromium)",
)

DESKTOP_VIEWPORT = {"width": 1280, "height": 900}
MOBILE_VIEWPORT = {"width": 375, "height": 812}


@pytest.fixture(scope="module")
def ergebnis_slots(live_server_url):
    """Ein geplantes (Start/Speichern/Beenden) und ein laufendes Spiel
    (Start rueckgaengig/Speichern/Beenden - die laengste Beschriftung)."""
    import app.database as database

    with database.get_conn() as conn:
        comp_id = conn.execute(
            """
            INSERT INTO competitions (name, sportart, jahrgang, status, competition_type, location)
            VALUES ('Knopfleiste Browsertest', 'Zweifelderball', 7, 'geplant', 'Turnier', 'Turnhalle')
            """
        ).lastrowid
        court_id = conn.execute("SELECT id FROM courts WHERE name = 'Feld 1'").fetchone()["id"]
        team_a = conn.execute("SELECT id FROM teams WHERE name = '7a'").fetchone()["id"]
        team_b = conn.execute("SELECT id FROM teams WHERE name = '7b'").fetchone()["id"]
        for startzeit, status in (("09:00", "geplant"), ("09:15", "läuft")):
            conn.execute(
                """
                INSERT INTO slots (startzeit, slot_typ, competition_id, court_id,
                                   team_a_id, team_b_id, phase, gruppe, status)
                VALUES (?, 'Spiel', ?, ?, ?, ?, 'Gruppenphase', 'A', ?)
                """,
                (startzeit, comp_id, court_id, team_a, team_b, status),
            )
        conn.commit()
        return comp_id


KNOEPFE_JS = """
() => [...document.querySelectorAll('.results-court-column .result-actions')]
    .map(leiste => [...leiste.querySelectorAll(':scope > button, :scope > form > button')]
        .filter(b => b.offsetParent !== null)
        .map(b => {
            const range = document.createRange();
            range.selectNodeContents(b);
            const zeilen = new Set([...range.getClientRects()].map(r => Math.round(r.top))).size;
            return {
                text: b.textContent.trim(),
                zeilen,
                ueberlauf: b.scrollWidth > b.clientWidth + 1,
            };
        }))
    .filter(knoepfe => knoepfe.length > 0)
"""


@pytest.mark.parametrize("viewport", [DESKTOP_VIEWPORT, MOBILE_VIEWPORT], ids=["desktop", "handy"])
def test_knopf_beschriftungen_brechen_nicht_um(live_server_url, ergebnis_slots, page, viewport):
    page.set_viewport_size(viewport)
    page.goto(f"{live_server_url}/ergebnisse?competition_id={ergebnis_slots}")

    leisten = page.evaluate(KNOEPFE_JS)
    texte = {k["text"] for leiste in leisten for k in leiste}
    assert {"Start", "Start rückgängig", "Speichern", "Beenden"} <= texte, texte

    for leiste in leisten:
        for knopf in leiste:
            assert knopf["zeilen"] == 1, knopf
            assert not knopf["ueberlauf"], knopf
