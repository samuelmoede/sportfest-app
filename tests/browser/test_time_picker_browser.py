"""Echter Browser-Test fuer den Uhrzeit-Picker aus Issue #113/#117.

Ergaenzt die reinen Quelltext-/Struktur-Pruefungen in
tests/test_time_picker_enhancement.py (dort dokumentiert, warum es bisher
keine Browser-Testinfrastruktur gab) um eine echte Interaktionspruefung:
auf das Zifferblatt klicken und pruefen, dass sich der sichtbare/native Wert
tatsaechlich aendert - nicht nur, dass der Quelltext bestimmte Muster
enthaelt.
"""
import math
import re

import pytest
from playwright.sync_api import expect

from tests.browser.conftest import chromium_available

pytestmark = pytest.mark.skipif(
    not chromium_available(),
    reason="Chromium-Browser-Binary nicht installiert (python -m playwright install chromium)",
)

# Muss dieselbe Kreis-Positionierung wie positionOnCircle() in base.html
# nachbilden (NUMBER_RADIUS_PERCENT = 38), damit der Klick auf der
# Zifferblatt-Flaeche an der Position der jeweiligen Zahl landet.
NUMBER_RADIUS_PERCENT = 38


def _clock_click_position(width: float, height: float, value: int, total: int) -> dict:
    angle_deg = (value / total) * 360
    angle_rad = math.radians(angle_deg)
    left_percent = 50 + NUMBER_RADIUS_PERCENT * math.sin(angle_rad)
    top_percent = 50 - NUMBER_RADIUS_PERCENT * math.cos(angle_rad)
    return {"x": width * left_percent / 100, "y": height * top_percent / 100}


def test_time_picker_dial_click_updates_native_input_value(live_server_url, page):
    page.goto(f"{live_server_url}/assistent")

    time_input = page.locator('input[name="startzeit"]')
    expect(time_input).to_be_visible()

    page.locator(".time-picker-toggle").click()
    panel = page.locator(".time-picker-panel")
    expect(panel).to_be_visible()

    clock = page.locator(".time-clock")
    box = clock.bounding_box()
    assert box is not None
    clock.click(position=_clock_click_position(box["width"], box["height"], 9, 24))

    # Nach der Stundenauswahl wechselt der Picker automatisch in den
    # Minuten-Modus (siehe stopDrag() in base.html).
    expect(page.locator('.time-picker-display-part[data-mode="minutes"]')).to_have_class(
        re.compile(r"\bis-active\b")
    )

    box = clock.bounding_box()
    assert box is not None
    clock.click(position=_clock_click_position(box["width"], box["height"], 15, 60))

    expect(time_input).to_have_value("09:15")


def test_time_picker_panel_closes_on_escape(live_server_url, page):
    page.goto(f"{live_server_url}/assistent")

    page.locator(".time-picker-toggle").click()
    panel = page.locator(".time-picker-panel")
    expect(panel).to_be_visible()

    page.keyboard.press("Escape")
    expect(panel).to_be_hidden()
