"""Echter Browser-Test fuer den Uhrzeit-Picker aus Issue #113/#117/#120.

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

# Muss dieselbe Doppelring-Positionierung wie positionOnCircle()/hourAngle()
# in base.html nachbilden (OUTER_NUMBER_RADIUS_PERCENT = 38, aeusserer Ring
# fuer Stunden 1-12; INNER_NUMBER_RADIUS_PERCENT = 24, innerer Ring fuer
# Stunden 13-23 und 0), damit der Klick auf der Zifferblatt-Flaeche an der
# Position der jeweiligen Zahl landet. Minuten liegen weiterhin auf einem
# einzelnen Ring mit dem aeusseren Radius.
OUTER_RADIUS_PERCENT = 38
INNER_RADIUS_PERCENT = 24


def _position_on_circle(width: float, height: float, angle_deg: float, radius_percent: float) -> dict:
    angle_rad = math.radians(angle_deg)
    left_percent = 50 + radius_percent * math.sin(angle_rad)
    top_percent = 50 - radius_percent * math.cos(angle_rad)
    return {"x": width * left_percent / 100, "y": height * top_percent / 100}


def _hour_click_position(width: float, height: float, hour: int) -> dict:
    angle_deg = (hour % 12) * 30
    radius_percent = OUTER_RADIUS_PERCENT if 1 <= hour <= 12 else INNER_RADIUS_PERCENT
    return _position_on_circle(width, height, angle_deg, radius_percent)


def _minute_click_position(width: float, height: float, value: int) -> dict:
    angle_deg = (value / 60) * 360
    return _position_on_circle(width, height, angle_deg, OUTER_RADIUS_PERCENT)


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
    # Stunde 9 liegt auf dem aeusseren Ring (Stunden 1-12).
    clock.click(position=_hour_click_position(box["width"], box["height"], 9))

    # Nach der Stundenauswahl wechselt der Picker automatisch in den
    # Minuten-Modus (siehe stopDrag() in base.html).
    expect(page.locator('.time-picker-display-part[data-mode="minutes"]')).to_have_class(
        re.compile(r"\bis-active\b")
    )

    box = clock.bounding_box()
    assert box is not None
    clock.click(position=_minute_click_position(box["width"], box["height"], 15))

    expect(time_input).to_have_value("09:15")


def test_time_picker_dial_click_selects_inner_ring_hour(live_server_url, page):
    page.goto(f"{live_server_url}/assistent")

    time_input = page.locator('input[name="startzeit"]')
    expect(time_input).to_be_visible()

    page.locator(".time-picker-toggle").click()
    panel = page.locator(".time-picker-panel")
    expect(panel).to_be_visible()

    clock = page.locator(".time-clock")
    box = clock.bounding_box()
    assert box is not None
    # Stunde 14 liegt auf dem inneren Ring (Stunden 13-23 und 0).
    clock.click(position=_hour_click_position(box["width"], box["height"], 14))

    expect(page.locator('.time-picker-display-part[data-mode="minutes"]')).to_have_class(
        re.compile(r"\bis-active\b")
    )

    box = clock.bounding_box()
    assert box is not None
    clock.click(position=_minute_click_position(box["width"], box["height"], 30))

    expect(time_input).to_have_value("14:30")


def test_time_picker_panel_closes_on_escape(live_server_url, page):
    page.goto(f"{live_server_url}/assistent")

    page.locator(".time-picker-toggle").click()
    panel = page.locator(".time-picker-panel")
    expect(panel).to_be_visible()

    page.keyboard.press("Escape")
    expect(panel).to_be_hidden()
