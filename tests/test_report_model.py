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
