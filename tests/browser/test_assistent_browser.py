"""Echte Browser-Tests fuer den Einrichtungsassistenten: Live-Liste der
Matrix, Pfeil-Navigation durch die Wettbewerbsplaene, Bearbeiten-Dialog und
mobile Ansicht ohne horizontales Scrollen."""
import pytest
from playwright.sync_api import expect

from tests.browser.conftest import chromium_available

pytestmark = pytest.mark.skipif(
    not chromium_available(),
    reason="Chromium-Browser-Binary nicht installiert (python -m playwright install chromium)",
)


@pytest.fixture(scope="module")
def entwurf_id(live_server_url):
    """Freier Entwurf (ohne Vorlage) mit drei Wettbewerben, direkt ueber den
    Service angelegt - gegen die temporaere DB des Live-Servers."""
    import app.database as database
    from app.services import assistent_entwurf_service as es

    with database.get_conn() as conn:
        for jahrgang in (9, 10):
            for buchstabe in "abcd":
                conn.execute(
                    "INSERT INTO teams (name, jahrgang, active) VALUES (?, ?, 1)",
                    (f"B{jahrgang}{buchstabe}", jahrgang),
                )
        for i in range(2, 4):
            conn.execute(
                "INSERT INTO courts (name, location, active) VALUES (?, 'Turnhalle', 1)", (f"Browserfeld {i}",)
            )
        daten = es.neue_daten()
        daten["grunddaten"].update({"name": "Browsertest", "datum": "2026-12-03"})
        daten["jahrgaenge"] = ["9", "10"]
        daten["sportarten"] = ["Volleyball", "Basketball"]
        daten["matrix"] = {"Volleyball": ["9", "10"], "Basketball": ["9"]}
        es.bloecke_vorschlagen(daten, es.lade_stammdaten(conn))
        entwurf = es.erstelle_entwurf(conn, daten)
        conn.commit()
    return entwurf


def test_matrix_live_liste(live_server_url, entwurf_id, page):
    page.goto(f"{live_server_url}/assistent/entwurf/{entwurf_id}/sportarten")
    liste = page.locator("#wiz-wb-list")
    expect(liste).to_contain_text("Volleyball")
    vorher = liste.locator("li").count()
    page.locator("#wiz-matrix input[type=checkbox][data-sportart='Basketball']").last.check()
    expect(liste.locator("li")).to_have_count(vorher + 1)


def test_vorschau_pfeile_und_dialog(live_server_url, entwurf_id, page):
    page.goto(f"{live_server_url}/assistent/entwurf/{entwurf_id}/vorschau")
    position = page.locator("[data-carousel-pos]")
    expect(position).to_have_text("1 von 3")
    expect(page.locator("[data-carousel-item]:not([hidden])")).to_have_count(1)
    page.locator("[data-carousel-next]").click()
    expect(position).to_have_text("2 von 3")
    page.locator("[data-carousel-prev]").click()
    page.locator("[data-carousel-prev]").click()
    expect(position).to_have_text("3 von 3")

    karte = page.locator("[data-carousel-item]:not([hidden])")
    karte.locator("[data-dialog-open]").click()
    dialog = karte.locator("dialog")
    expect(dialog).to_be_visible()
    expect(dialog.locator("input[name=spielzeit]")).to_be_visible()


def test_vorschau_mobil_ohne_horizontales_scrollen(live_server_url, entwurf_id, page):
    page.set_viewport_size({"width": 375, "height": 812})
    for schritt in ("sportarten", "zeitplan", "vorschau"):
        page.goto(f"{live_server_url}/assistent/entwurf/{entwurf_id}/{schritt}")
        breite = page.evaluate("() => document.documentElement.scrollWidth")
        assert breite <= 375 + 1, f"{schritt}: Seite ist {breite}px breit"


def test_orte_checkboxen_inline_und_kompakt(live_server_url, entwurf_id, page):
    """Schritt Orte: "Ort nutzen" und die Feld-Chips haben ein normales,
    kleines Haekchen links neben dem Text (nicht riesig/darueber), und ein
    abgewaehlter Ort blendet seine Felder sichtbar ab."""
    page.goto(f"{live_server_url}/assistent/entwurf/{entwurf_id}/orte")
    karte = page.locator("fieldset.wiz-card").first
    for label in (karte.locator("label.wiz-ort-schalter"), karte.locator(".wiz-chips label").first):
        box = label.locator("input[type=checkbox]").bounding_box()
        assert box["width"] <= 20 and box["height"] <= 20, box
        richtung = label.evaluate("el => getComputedStyle(el).flexDirection")
        assert richtung == "row"
        # Haekchen und Text in einer Zeile: Label kaum hoeher als eine Textzeile.
        assert label.bounding_box()["height"] < 40, label.bounding_box()

    chips = karte.locator(".wiz-chips")
    assert chips.evaluate("el => getComputedStyle(el).opacity") == "1"
    karte.locator("label.wiz-ort-schalter input").uncheck()
    assert float(chips.evaluate("el => getComputedStyle(el).opacity")) < 1
