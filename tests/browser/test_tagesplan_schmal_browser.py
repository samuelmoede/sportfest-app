"""Echter Browser-Test: Tagesplan-Zeitraster im Dashboard bei halber
Fensterbreite (Desktop-Fenster nur halb so breit, 701-860 px).

Die Regel `.dashboard-page * { min-width: 0 }` (responsive.css, <= 860 px)
hatte die Mindestbreite der Jahrgangsspalten aufgehoben: die Spalten
schrumpften auf ~30 px, Wettbewerbsnamen standen buchstabenweise
untereinander. Jetzt behalten die Spalten ihre Mindestbreite und das Raster
scrollt waagerecht; die Zeitachse bleibt dabei stehen.
"""
import datetime

import pytest

from tests.browser.conftest import chromium_available

pytestmark = pytest.mark.skipif(
    not chromium_available(),
    reason="Chromium-Browser-Binary nicht installiert (python -m playwright install chromium)",
)


@pytest.fixture(scope="module")
def tagesplan_event(live_server_url):
    import app.database as database

    with database.get_conn() as conn:
        event_id = conn.execute(
            "INSERT INTO events (name, event_date, status) VALUES ('Tagesplan schmal', ?, 'geplant')",
            (datetime.date.today().isoformat(),),
        ).lastrowid
        for jahrgang, sportart in ((7, "Zweifelderball"), (8, "Zweifelderball"), (9, "Volleyball"), (10, "Volleyball")):
            conn.execute(
                """
                INSERT INTO competitions (name, sportart, jahrgang, status, competition_type,
                                          location, event_id, start_time, end_time)
                VALUES (?, ?, ?, 'geplant', 'Turnier', 'Turnhalle', ?, '08:00', '10:30')
                """,
                (f"{sportart} Tagesplan JG {jahrgang}", sportart, jahrgang, event_id),
            )
        conn.commit()
    return event_id


@pytest.mark.parametrize("breite", [760, 860, 1280])
def test_tagesplan_spalten_behalten_mindestbreite(live_server_url, tagesplan_event, page, breite):
    page.set_viewport_size({"width": breite, "height": 900})
    page.goto(f"{live_server_url}/")
    spalten = page.locator(".dashboard-schedule .day-timeline-column-body")
    assert spalten.count() >= 4
    breiten = spalten.evaluate_all("els => els.map(el => el.getBoundingClientRect().width)")
    assert min(breiten) >= 199, breiten
    kopf = page.locator(".dashboard-schedule .day-timeline-column-label").first
    assert kopf.evaluate("el => el.getBoundingClientRect().width") >= 199

    # Die Seite selbst scrollt nicht waagerecht, nur das Raster.
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_tagesplan_zeitachse_bleibt_beim_scrollen_stehen(live_server_url, tagesplan_event, page):
    page.set_viewport_size({"width": 820, "height": 900})
    page.goto(f"{live_server_url}/")
    wrapper = page.locator(".dashboard-schedule .day-timeline-wrapper")
    achse = page.locator(".dashboard-schedule .day-timeline-axis")
    links_vorher = achse.evaluate("el => el.getBoundingClientRect().left")
    assert wrapper.evaluate("el => el.scrollWidth > el.clientWidth")
    wrapper.evaluate("el => { el.scrollLeft = 300; }")
    assert achse.evaluate("el => el.getBoundingClientRect().left") == pytest.approx(links_vorher, abs=1)
