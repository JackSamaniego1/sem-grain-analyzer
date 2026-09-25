"""Tests for reports.model.ReportModel: construction, JSON round-trip, edits."""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import cv2

from tests.conftest import make_mosaic
from core.grain_detector import GrainDetector, DetectionParams
from reports.model import ReportModel, ReportImageInput


def _build_items(tmp_path, n=3, px_per_um=8.0, seeds=None):
    det = GrainDetector()
    seeds = seeds or list(range(1, n + 1))
    items = []
    for i, seed in enumerate(seeds):
        gray, _ = make_mosaic(seed=seed, h=256, w=256, n_grains=30)
        bgr = np.repeat(gray[:, :, None], 3, axis=2)
        res = det.analyze(bgr, px_per_um=px_per_um, params=DetectionParams())
        img_path = str(tmp_path / f"synth_{i}.png")
        cv2.imwrite(img_path, bgr)
        items.append(ReportImageInput(
            image_path=img_path, result=res, image_bgr=bgr,
            sample_id=f"S{i}", lot_number="L1", notes=f"note {i}",
        ))
    return items


def _build_model(tmp_path, n=3, px_per_um=8.0):
    items = _build_items(tmp_path, n=n, px_per_um=px_per_um)
    return ReportModel.from_results(
        items, title="Test Report", operator="Jack", organization="Acme",
        metadata={"detection_mode": "boundary"}, asset_dir=str(tmp_path / "assets"),
    )


def test_from_results_builds_one_image_per_item(tmp_path):
    model = _build_model(tmp_path, n=3)
    assert len(model.images) == 3
    assert [i.id for i in model.images] == ["img_1", "img_2", "img_3"]
    for img in model.images:
        assert img.grain_count > 0
        assert len(img.grains) == img.grain_count
        assert img.has_calibration is True
        assert os.path.exists(img.image_path)
        assert img.overlay_path and os.path.exists(img.overlay_path)


def test_from_results_uncalibrated(tmp_path):
    items = _build_items(tmp_path, n=1, px_per_um=0.0)
    model = ReportModel.from_results(items, asset_dir=str(tmp_path / "assets"))
    img = model.images[0]
    assert img.has_calibration is False
    assert img.px_per_um == 0.0
    # px columns still populated even without calibration
    assert img.grains[0]["area_px"] > 0


def test_validate_clean_model_has_no_problems(tmp_path):
    model = _build_model(tmp_path)
    assert model.validate() == []


def test_validate_catches_missing_image_and_bad_units(tmp_path):
    model = _build_model(tmp_path, n=1)
    model.images[0].image_path = str(tmp_path / "does_not_exist.png")
    model.units = "furlongs"
    problems = model.validate()
    assert any("does not exist" in p for p in problems)
    assert any("units" in p for p in problems)


def test_json_round_trip_preserves_data(tmp_path):
    model = _build_model(tmp_path, n=2)
    s = model.to_json()
    restored = ReportModel.from_json(s)
    assert restored.to_dict() == model.to_dict()
    assert restored.schema_version == model.schema_version
    assert len(restored.images) == 2
    assert restored.images[0].grains == model.images[0].grains


def test_save_and_load_file_round_trip(tmp_path):
    model = _build_model(tmp_path, n=2)
    path = str(tmp_path / "report.json")
    model.save(path)
    restored = ReportModel.load(path)
    assert restored.to_dict() == model.to_dict()


def test_edit_disable_section_persists_through_round_trip(tmp_path):
    model = _build_model(tmp_path, n=2)
    sec = model.get_section("combined_distribution")
    sec.enabled = False
    restored = ReportModel.from_json(model.to_json())
    assert restored.get_section("combined_distribution").enabled is False
    assert restored.is_enabled("combined_distribution") is False


def test_edit_reorder_images_persists(tmp_path):
    model = _build_model(tmp_path, n=3)
    # Reverse order.
    for img in model.images:
        img.order = 4 - img.order  # 1,2,3 -> 3,2,1
    restored = ReportModel.from_json(model.to_json())
    ordered = restored.ordered_images()
    assert [i.id for i in ordered] == ["img_3", "img_2", "img_1"]


def test_edit_caption_and_include_flag_persist(tmp_path):
    model = _build_model(tmp_path, n=2)
    model.images[0].caption = "Great grains here"
    model.images[1].include = False
    restored = ReportModel.from_json(model.to_json())
    assert restored.images[0].caption == "Great grains here"
    assert restored.images[1].include is False
    assert [i.id for i in restored.ordered_images()] == ["img_1"]


def test_image_section_disable_excludes_from_ordered_images(tmp_path):
    model = _build_model(tmp_path, n=2)
    sec = model.get_section("image", image_id="img_1")
    sec.enabled = False
    ordered = model.ordered_images()
    assert [i.id for i in ordered] == ["img_2"]


def test_astm_g_present_when_calibrated(tmp_path):
    model = _build_model(tmp_path, n=1)
    g = model.images[0].astm_g
    assert g is not None and 0.0 < g < 20.0


def test_astm_g_none_when_uncalibrated(tmp_path):
    model = _build_model(tmp_path, n=1, px_per_um=0.0)
    assert model.images[0].astm_g is None


# ---------------------------------------------------------------------------
# HIER-01: user-defined folder hierarchy fields on ReportModel
# ---------------------------------------------------------------------------

_HIERARCHY = [
    {"key": "project", "label": "Job #", "value": "24-117"},
    {"key": "sample", "label": "Part Number", "value": "7718-A"},
    {"key": "lot", "label": "Lot", "value": "L-44A"},
]


def test_legacy_model_has_empty_hierarchy_and_export_basename(tmp_path):
    """Old report.json files (no hierarchy/export_basename keys) load with
    the new fields defaulted, so nothing about legacy rendering changes."""
    model = _build_model(tmp_path, n=1)
    assert model.hierarchy == []
    assert model.export_basename == ""
    assert model.images[0].display_name == ""
    assert model.images[0].levels == {}
    assert model.level_columns() == [("sample", "Sample"), ("lot", "Lot")]
    assert model.hierarchy_header() == ""


def test_from_results_with_hierarchy_and_display_names(tmp_path):
    items = _build_items(tmp_path, n=2)
    items[0].display_name = "L-44A_01"
    items[1].display_name = "L-44A_02"
    model = ReportModel.from_results(
        items, title="Job Report", hierarchy=_HIERARCHY,
        export_basename="24-117_7718-A_L-44A_Grain_Report_20260924",
        asset_dir=str(tmp_path / "assets"),
    )
    assert model.hierarchy == _HIERARCHY
    assert model.export_basename == "24-117_7718-A_L-44A_Grain_Report_20260924"
    assert model.images[0].display() == "L-44A_01"
    assert model.images[0].original_name.startswith("synth_0")
    assert model.level_columns() == [("project", "Job #"), ("sample", "Part Number"), ("lot", "Lot")]
    assert model.hierarchy_header() == "Job #: 24-117 | Part Number: 7718-A | Lot: L-44A"
    assert model.row_levels(model.images[0]) == ["24-117", "7718-A", "L-44A"]


def test_image_display_falls_back_to_basename(tmp_path):
    model = _build_model(tmp_path, n=1)
    img = model.images[0]
    assert img.display_name == ""
    assert img.display() == os.path.basename(img.image_path)


def test_per_image_levels_override_report_hierarchy(tmp_path):
    items = _build_items(tmp_path, n=2)
    items[1].levels = {"lot": "L-45B"}
    model = ReportModel.from_results(items, hierarchy=_HIERARCHY, asset_dir=str(tmp_path / "assets"))
    assert model.row_levels(model.images[0]) == ["24-117", "7718-A", "L-44A"]
    assert model.row_levels(model.images[1]) == ["24-117", "7718-A", "L-45B"]


def test_hierarchy_and_export_basename_round_trip(tmp_path):
    items = _build_items(tmp_path, n=1)
    items[0].display_name = "Custom Name"
    items[0].levels = {"lot": "L-99Z"}
    model = ReportModel.from_results(
        items, hierarchy=_HIERARCHY, export_basename="my_export", asset_dir=str(tmp_path / "assets"),
    )
    restored = ReportModel.from_json(model.to_json())
    assert restored.hierarchy == _HIERARCHY
    assert restored.export_basename == "my_export"
    assert restored.images[0].display_name == "Custom Name"
    assert restored.images[0].levels == {"lot": "L-99Z"}
    assert restored.to_dict() == model.to_dict()


# ---------------------------------------------------------------------------
# reports.suggest_filename
# ---------------------------------------------------------------------------

def test_suggest_filename_uses_export_basename(tmp_path):
    import reports
    model = _build_model(tmp_path, n=1)
    model.export_basename = "24-117_7718-A_L-44A_Grain_Report_20260924"
    assert reports.suggest_filename(model, "xlsx") == "24-117_7718-A_L-44A_Grain_Report_20260924.xlsx"
    assert reports.suggest_filename(model, "pptx") == "24-117_7718-A_L-44A_Grain_Report_20260924.pptx"


def test_suggest_filename_legacy_default_from_title(tmp_path):
    import reports
    model = _build_model(tmp_path, n=1)
    assert model.export_basename == ""
    name = reports.suggest_filename(model, "xlsx")
    assert name.endswith(".xlsx")
    assert "Test_Report" in name


def test_suggest_filename_sanitizes_for_windows(tmp_path):
    import reports
    model = _build_model(tmp_path, n=1)
    model.export_basename = 'Bad:Name/With*Chars?'
    name = reports.suggest_filename(model, "xlsx")
    assert not any(ch in name for ch in ':/\\*?"<>|')


# ---------------------------------------------------------------------------
# FIX-07: ReportModel.calibration (INN-29 scale-verification payload)
# ---------------------------------------------------------------------------

_CAL_PAYLOAD = {
    "source": "metadata", "px_per_um": 5.0, "check": None, "status": "not verified",
    "reason": "no check on file", "warnings": [], "text": "Scale not verified (no check on file)",
}


def test_calibration_defaults_to_none(tmp_path):
    model = _build_model(tmp_path, n=1)
    assert model.calibration is None


def test_from_results_populates_calibration_field(tmp_path):
    items = _build_items(tmp_path, n=1)
    model = ReportModel.from_results(
        items, asset_dir=str(tmp_path / "assets"), calibration=_CAL_PAYLOAD,
    )
    assert model.calibration == _CAL_PAYLOAD


def test_from_results_derives_calibration_from_metadata(tmp_path):
    """Callers that only set metadata["calibration"] (the pre-FIX-07 path,
    e.g. ui.pages.report_builder.apply_calibration before this field
    existed) still get it populated onto the new field."""
    items = _build_items(tmp_path, n=1)
    model = ReportModel.from_results(
        items, asset_dir=str(tmp_path / "assets"), metadata={"calibration": _CAL_PAYLOAD},
    )
    assert model.calibration == _CAL_PAYLOAD


def test_calibration_round_trips_through_json(tmp_path):
    items = _build_items(tmp_path, n=1)
    model = ReportModel.from_results(
        items, asset_dir=str(tmp_path / "assets"), calibration=_CAL_PAYLOAD,
    )
    restored = ReportModel.from_json(model.to_json())
    assert restored.calibration == _CAL_PAYLOAD
    assert restored.to_dict() == model.to_dict()


def test_calibration_absent_round_trips_to_none(tmp_path):
    model = _build_model(tmp_path, n=1)
    assert model.calibration is None
    restored = ReportModel.from_json(model.to_json())
    assert restored.calibration is None
    assert "calibration" in model.to_dict()  # key always present, just null


def test_legacy_report_json_without_calibration_key_loads_cleanly(tmp_path):
    """Old report.json saved before FIX-07 has no top-level "calibration"
    key at all -- must load with calibration=None, no errors."""
    model = _build_model(tmp_path, n=1)
    d = model.to_dict()
    del d["calibration"]
    restored = ReportModel.from_dict(d)
    assert restored.calibration is None


def test_legacy_report_json_with_only_metadata_calibration_backfills_field(tmp_path):
    """Old report.json saved with the INN-29 payload only inside
    metadata["calibration"] (no top-level key) still surfaces it on the new
    field after loading."""
    model = _build_model(tmp_path, n=1)
    d = model.to_dict()
    del d["calibration"]
    d["metadata"]["calibration"] = dict(_CAL_PAYLOAD)
    restored = ReportModel.from_dict(d)
    assert restored.calibration == _CAL_PAYLOAD


# ---------------------------------------------------------------------------
# UX-16: overlay_opacity
# ---------------------------------------------------------------------------

def test_overlay_opacity_defaults_to_fully_opaque(tmp_path):
    model = _build_model(tmp_path, n=1)
    assert model.overlay_opacity == 1.0


def test_overlay_opacity_set_via_from_results(tmp_path):
    items = _build_items(tmp_path, n=1)
    model = ReportModel.from_results(items, asset_dir=str(tmp_path / "assets"),
                                     overlay_opacity=0.35)
    assert model.overlay_opacity == 0.35


def test_overlay_opacity_round_trips_through_json(tmp_path):
    model = _build_model(tmp_path, n=1)
    model.overlay_opacity = 0.6
    restored = ReportModel.from_json(model.to_json())
    assert restored.overlay_opacity == 0.6
    assert restored.to_dict() == model.to_dict()


def test_legacy_report_json_without_overlay_opacity_key_defaults_to_one(tmp_path):
    """Old report.json saved before UX-16 has no "overlay_opacity" key at
    all -- must load as 1.0, the exact look those reports already had."""
    model = _build_model(tmp_path, n=1)
    d = model.to_dict()
    del d["overlay_opacity"]
    restored = ReportModel.from_dict(d)
    assert restored.overlay_opacity == 1.0


def test_validate_catches_overlay_opacity_out_of_range(tmp_path):
    model = _build_model(tmp_path, n=1)
    model.overlay_opacity = 1.4
    problems = model.validate()
    assert any("overlay_opacity" in p for p in problems)


# ---------------------------------------------------------------------------
# UX-15: custom_palette
# ---------------------------------------------------------------------------

def test_custom_palette_defaults_to_none(tmp_path):
    model = _build_model(tmp_path, n=1)
    assert model.custom_palette is None


def test_custom_palette_round_trips_through_json(tmp_path):
    from reports.charts import derive_custom_palette
    model = _build_model(tmp_path, n=1)
    model.theme = "custom:custom-1"
    model.custom_palette = derive_custom_palette(["#111111", "#222222", "#333333"], "Mine")
    restored = ReportModel.from_json(model.to_json())
    assert restored.theme == "custom:custom-1"
    assert restored.custom_palette == model.custom_palette
    assert restored.to_dict() == model.to_dict()


def test_legacy_report_json_without_custom_palette_key_loads_cleanly(tmp_path):
    model = _build_model(tmp_path, n=1)
    d = model.to_dict()
    del d["custom_palette"]
    restored = ReportModel.from_dict(d)
    assert restored.custom_palette is None


def test_validate_catches_custom_theme_without_palette(tmp_path):
    model = _build_model(tmp_path, n=1)
    model.theme = "custom:custom-1"
    problems = model.validate()
    assert any("custom_palette" in p for p in problems)


# ---------------------------------------------------------------------------
# UX-14: chart_options
# ---------------------------------------------------------------------------

def test_chart_options_defaults_to_empty(tmp_path):
    model = _build_model(tmp_path, n=1)
    assert model.chart_options == {}


def test_chart_options_set_via_from_results(tmp_path):
    items = _build_items(tmp_path, n=1)
    opts = {"normal_fit": False, "area": {"enabled": False}}
    model = ReportModel.from_results(items, asset_dir=str(tmp_path / "assets"),
                                     chart_options=opts)
    assert model.chart_options == opts


def test_chart_options_round_trip_through_json(tmp_path):
    model = _build_model(tmp_path, n=1)
    model.chart_options = {"area": {"min": 1.0, "max": 50.0, "title": "My area chart"}}
    restored = ReportModel.from_json(model.to_json())
    assert restored.chart_options == model.chart_options
    assert restored.to_dict() == model.to_dict()


def test_legacy_report_json_without_chart_options_key_loads_cleanly(tmp_path):
    model = _build_model(tmp_path, n=1)
    d = model.to_dict()
    del d["chart_options"]
    restored = ReportModel.from_dict(d)
    assert restored.chart_options == {}


def test_validate_catches_min_greater_than_max(tmp_path):
    model = _build_model(tmp_path, n=1)
    model.chart_options = {"diameter": {"min": 50, "max": 10}}
    problems = model.validate()
    assert any("chart_options" in p and "diameter" in p for p in problems)


# ---------------------------------------------------------------------------
# UX-14 fix: unit-aware chart min/max (bound_unit) round-trips
# ---------------------------------------------------------------------------

def test_chart_options_bound_unit_round_trips_through_json(tmp_path):
    model = _build_model(tmp_path, n=1)
    model.chart_options = {"diameter": {"min": 5.0, "max": 50.0, "bound_unit": "µm"}}
    restored = ReportModel.from_json(model.to_json())
    assert restored.chart_options == model.chart_options
    assert restored.chart_options["diameter"]["bound_unit"] == "µm"


def test_old_report_json_chart_options_without_bound_unit_key_loads_cleanly(tmp_path):
    """A report.json saved before UX-14's fix has ``min``/``max`` but no
    ``bound_unit`` key at all in the ``chart_options`` sub-dicts -- it must
    load without error, and ``reports.charts.resolve_chart_options`` must
    fill in ``bound_unit: None`` (treated as "already the current render
    unit", i.e. unconverted, at render time)."""
    from reports.charts import resolve_chart_options
    model = _build_model(tmp_path, n=1)
    model.chart_options = {"area": {"min": 1.0, "max": 50.0}}
    d = model.to_dict()
    assert "bound_unit" not in d["chart_options"]["area"]   # genuinely old-shaped dict
    restored = ReportModel.from_dict(d)
    assert restored.chart_options == {"area": {"min": 1.0, "max": 50.0}}
    resolved = resolve_chart_options(restored.chart_options)
    assert resolved["area"]["bound_unit"] is None
    assert resolved["area"]["min"] == 1.0 and resolved["area"]["max"] == 50.0
