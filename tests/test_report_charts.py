"""Tests for reports.charts: palette resolution and UX-15 custom palettes."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from reports.charts import (
    PALETTES, SERIES, derive_custom_palette, new_custom_palette_id, normalize_hex,
    resolve_palette, series_for, shade,
)


def test_normalize_hex_accepts_with_and_without_hash():
    assert normalize_hex("#a1b2c3") == "#A1B2C3"
    assert normalize_hex("a1b2c3") == "#A1B2C3"
    assert normalize_hex("ABCDEF") == "#ABCDEF"


def test_normalize_hex_rejects_bad_values():
    for bad in ("", "red", "#12345", "#1234567", "not a colour", None):
        assert normalize_hex(bad) is None


def test_shade_darken_and_lighten():
    assert shade("#800000", 0.5) == "#400000"
    assert shade("#000000", 1.0) == "#000000"     # factor 1 is a no-op
    lightened = shade("#800000", 1.5)
    r = int(lightened[1:3], 16)
    assert r > 0x80


def test_derive_custom_palette_maps_three_colours():
    p = derive_custom_palette(["#FF0000", "#00FF00", "#0000FF"], name="Lab Blue")
    assert p["name"] == "Lab Blue"
    assert p["area_bar"] == "#FF0000"
    assert p["diameter_bar"] == "#00FF00"
    assert p["normal_fit"] == "#0000FF"
    assert p["header"] == "#FF0000"
    assert p["accent2"] == "#00FF00"
    # accent/count_bar are a derived dark shade of colour 1, not a raw input
    assert p["accent"] == p["count_bar"]
    assert p["accent"] != "#FF0000"


def test_derive_custom_palette_normalizes_and_validates():
    p = derive_custom_palette(["ff0000", "#00ff00", "0000ff"])
    assert p["area_bar"] == "#FF0000"
    with pytest.raises(ValueError):
        derive_custom_palette(["not-a-colour", "#00FF00", "#0000FF"])
    with pytest.raises(ValueError):
        derive_custom_palette(["#FF0000", "#00FF00"])   # only 2 colours


def test_new_custom_palette_id_avoids_collisions():
    existing = [{"id": "custom-1"}, {"id": "custom-2"}]
    assert new_custom_palette_id(existing) == "custom-3"
    assert new_custom_palette_id([]) == "custom-1"


def test_resolve_palette_falls_back_to_default_without_custom():
    assert resolve_palette("custom") == PALETTES["default"]
    assert resolve_palette("nonsense-id") == PALETTES["default"]


def test_resolve_palette_uses_custom_when_provided():
    custom = derive_custom_palette(["#111111", "#222222", "#333333"], name="Mine")
    assert resolve_palette("custom", custom) == custom
    assert resolve_palette("custom:custom-1", custom) == custom


def test_resolve_palette_ignores_custom_for_a_known_builtin_id():
    custom = derive_custom_palette(["#111111", "#222222", "#333333"])
    assert resolve_palette("slate_teal", custom) == PALETTES["slate_teal"]


def test_series_for_reflects_custom_palette():
    custom = derive_custom_palette(["#111111", "#222222", "#333333"])
    series = series_for("custom", custom)
    assert series["area_bar"] == "#111111"
    assert series["diameter_bar"] == "#222222"
    assert series["normal_fit"] == "#333333"
    assert series["navy"] == custom["accent"]
    # non-palette series entries (typography/bands/gridlines) are unchanged
    assert series["gridline"] == SERIES["gridline"]
