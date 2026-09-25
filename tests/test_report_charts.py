"""Tests for reports.charts: palette resolution and UX-15 custom palettes."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from reports.charts import (
    DEFAULT_CHART_OPTIONS, PALETTES, SERIES, build_bins, convert_bound, derive_custom_palette,
    filter_range, new_custom_palette_id, normalize_hex, resolve_chart_options, resolve_palette,
    series_for, shade,
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


# ---------------------------------------------------------------------------
# UX-14: chart options
# ---------------------------------------------------------------------------

def test_resolve_chart_options_empty_returns_defaults():
    assert resolve_chart_options(None) == DEFAULT_CHART_OPTIONS
    assert resolve_chart_options({}) == DEFAULT_CHART_OPTIONS


def test_resolve_chart_options_merges_partial_overrides():
    resolved = resolve_chart_options({"normal_fit": False, "area": {"enabled": False, "min": 5}})
    assert resolved["normal_fit"] is False
    assert resolved["area"]["enabled"] is False
    assert resolved["area"]["min"] == 5
    assert resolved["area"]["max"] is None            # untouched key keeps its default
    assert resolved["diameter"] == DEFAULT_CHART_OPTIONS["diameter"]   # untouched metric


def test_resolve_chart_options_drops_unknown_keys():
    resolved = resolve_chart_options({"area": {"bogus": 1, "enabled": False}})
    assert "bogus" not in resolved["area"]
    assert resolved["area"]["enabled"] is False


def test_filter_range_bounds():
    assert filter_range([1, 2, 3, 4, 5], vmin=2, vmax=4) == [2, 3, 4]
    assert filter_range([1, 2, 3], vmin=None, vmax=None) == [1, 2, 3]
    assert filter_range([1, 2, 3], vmin=10) == []


def test_filter_range_then_build_bins_matches_filtered_data():
    vals = list(range(1, 21))          # 1..20
    filtered = filter_range(vals, vmin=5, vmax=10)
    labels, counts, edges = build_bins(filtered, n_bins=3)
    assert sum(counts) == len(filtered) == 6
    assert max(filtered) <= edges[-1]         # every filtered value fits inside the bin grid
    # values outside [5, 10] never entered the histogram at all
    assert sum(counts) == len([v for v in vals if 5 <= v <= 10])


# ---------------------------------------------------------------------------
# UX-14 fix: unit-aware chart min/max (convert_bound)
# ---------------------------------------------------------------------------

def test_convert_bound_none_value_is_none():
    assert convert_bound(None, "µm", "nm", "diameter") is None


def test_convert_bound_no_from_unit_is_unchanged():
    """A bound with no recorded unit (old report.json) is treated as already
    being in whatever unit it is about to render in -- i.e. never converted."""
    assert convert_bound(5.0, None, "nm", "diameter") == 5.0
    assert convert_bound(5.0, "", "µm²", "area") == 5.0


def test_convert_bound_same_unit_is_unchanged():
    assert convert_bound(5.0, "µm", "µm", "diameter") == 5.0


def test_convert_bound_diameter_um_to_nm():
    assert convert_bound(5.0, "µm", "nm", "diameter") == pytest.approx(5000.0)
    assert convert_bound(5000.0, "nm", "µm", "diameter") == pytest.approx(5.0)


def test_convert_bound_area_um2_to_nm2():
    assert convert_bound(2.0, "µm²", "nm²", "area") == pytest.approx(2e6)
    assert convert_bound(2e6, "nm²", "µm²", "area") == pytest.approx(2.0)


def test_convert_bound_px_never_converts():
    """px/px² (uncalibrated) has no real-world scale to convert with --
    the raw number always passes through unchanged."""
    assert convert_bound(5.0, "px", "nm", "diameter") == 5.0
    assert convert_bound(5.0, "µm", "px", "diameter") == 5.0


def test_default_chart_options_has_bound_unit_key():
    assert DEFAULT_CHART_OPTIONS["area"]["bound_unit"] is None
    assert DEFAULT_CHART_OPTIONS["diameter"]["bound_unit"] is None


def test_resolve_chart_options_carries_bound_unit():
    resolved = resolve_chart_options({"diameter": {"min": 5, "max": 50, "bound_unit": "µm"}})
    assert resolved["diameter"]["bound_unit"] == "µm"
    assert resolved["area"]["bound_unit"] is None   # untouched metric keeps default


def test_resolve_chart_options_old_dict_without_bound_unit_key():
    """UX-14 fix: an old report.json's chart_options sub-dict never had a
    ``bound_unit`` key at all -- resolving it must fill in ``None`` rather
    than error, so ``convert_bound`` sees the "no unit recorded" case."""
    resolved = resolve_chart_options({"area": {"min": 5, "max": 50}})
    assert resolved["area"]["bound_unit"] is None
    assert resolved["area"]["min"] == 5
    assert resolved["area"]["max"] == 50
