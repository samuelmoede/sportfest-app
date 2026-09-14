"""Echte Browser-Tests fuer die Mobile-Grid-Column-Fixes aus Issue #109/#111.

Ergaenzt tests/test_mobile_grid_column_inline_style.py und
tests/test_admin_form_mobile_layout.py (reine CSS-/HTML-Quelltextpruefungen)
um echte getComputedStyle()-Pruefungen bei tatsaechlich schmaler
Viewport-Breite, wie sie die urspruenglichen Bugs betrafen (Grid blieb auf
Mobile zwei- statt einspaltig).
"""
import pytest

from tests.browser.conftest import chromium_available

pytestmark = pytest.mark.skipif(
    not chromium_available(),
    reason="Chromium-Browser-Binary nicht installiert (python -m playwright install chromium)",
)

DESKTOP_VIEWPORT = {"width": 1280, "height": 900}
MOBILE_VIEWPORT = {"width": 375, "height": 812}


def test_generator_hint_grid_column_resets_below_860px(live_server_url, page):
    """Issue #109: .generator-hint (spielplan_bearbeiten.html) muss unter
    860px per Media Query auf grid-column: auto zurueckgesetzt werden, statt
    fest auf der zweiten Spalte zu bleiben."""
    page.goto(f"{live_server_url}/spielplan-bearbeiten")
    hint = page.locator(".generator-hint").first

    page.set_viewport_size(DESKTOP_VIEWPORT)
    assert hint.evaluate("el => getComputedStyle(el).gridColumnStart") == "2"

    page.set_viewport_size(MOBILE_VIEWPORT)
    assert hint.evaluate("el => getComputedStyle(el).gridColumnStart") == "auto"


def test_admin_form_collapses_to_single_column_below_700px(live_server_url, page):
    """Issue #111: .admin-form (u.a. assistent_start.html) muss unter 700px
    auf eine Spalte kollabieren; die Label-Spalte (160px 1fr) darf nur ab
    701px gelten."""
    page.goto(f"{live_server_url}/assistent")
    admin_form = page.locator(".admin-form").first

    page.set_viewport_size(DESKTOP_VIEWPORT)
    desktop_columns = admin_form.evaluate("el => getComputedStyle(el).gridTemplateColumns").split()
    assert len(desktop_columns) == 2, desktop_columns

    page.set_viewport_size(MOBILE_VIEWPORT)
    mobile_columns = admin_form.evaluate("el => getComputedStyle(el).gridTemplateColumns").split()
    assert len(mobile_columns) == 1, mobile_columns
