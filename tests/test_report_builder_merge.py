"""Tests for ui.pages.report_builder's pure-Python helpers: merge_refresh
(UX-14/15/16 fields must survive a "Refresh numbers"), chart_options_arg,
overlay_opacity_arg. No Qt/session needed -- ui.pages.report_builder does
not import Qt at module level."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from reports.charts import derive_custom_palette
from reports.model import ImageSummary, ReportModel
from ui.pages.report_builder import chart_options_arg, merge_refresh, overlay_opacity_arg


def _model(theme="default", custom_palette=None, overlay_opacity=1.0, chart_options=None):
    return ReportModel(title="T", images=[ImageSummary(id="img_1", image_path="a.png")],
                       theme=theme, custom_palette=custom_palette,
                       overlay_opacity=overlay_opacity, chart_options=chart_options or {})


def test_merge_refresh_preserves_custom_palette_overlay_opacity_and_chart_options():
    palette = derive_custom_palette(["#111111", "#222222", "#333333"], "Mine")
    old = _model(theme="custom:custom-1", custom_palette=palette, overlay_opacity=0.4,
                chart_options={"normal_fit": False, "area": {"enabled": False}})
    new = _model()   # a freshly rebuilt model, as if from a fresh session build
    merged = merge_refresh(old, new)
    assert merged.theme == "custom:custom-1"
    assert merged.custom_palette == palette
    assert merged.overlay_opacity == 0.4
    assert merged.chart_options == {"normal_fit": False, "area": {"enabled": False}}


def test_chart_options_arg_reads_app_settings_default():
    class FakeSettings:
        default_chart_options = {"normal_fit": False}

    class FakeState:
        settings = FakeSettings()

    assert chart_options_arg(FakeState()) == {"normal_fit": False}


def test_chart_options_arg_defaults_to_empty_without_settings():
    class FakeState:
        pass

    assert chart_options_arg(FakeState()) == {}


def test_overlay_opacity_arg_reads_app_state_property():
    class FakeState:
        overlay_opacity = 0.65

    assert overlay_opacity_arg(FakeState()) == 0.65


def test_overlay_opacity_arg_defaults_to_one_without_property():
    class FakeState:
        pass

    assert overlay_opacity_arg(FakeState()) == 1.0
