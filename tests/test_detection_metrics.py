"""
DET-02 / DET-03: statistics use the valid (test-field) area; single
implementation in core.metrics; discard_border_grains lives in core.
"""
import numpy as np
import pytest

from core.grain_detector import (
    GrainDetector, DetectionParams, AnalysisResult, GrainResult,
    discard_border_grains)
from core.metrics import compute_statistics


def _grain(gid, area_px, ppu=2.0, circ=0.8, ar=1.2):
    d = 2 * np.sqrt(area_px / np.pi)
    return GrainResult(
        grain_id=gid, area_px=area_px, area_um2=area_px / ppu ** 2,
        perimeter_px=1.0, perimeter_um=1.0,
        equivalent_diameter_px=d, equivalent_diameter_um=d / ppu,
        major_axis_um=1.0, minor_axis_um=1.0, aspect_ratio=ar,
        circularity=circ, eccentricity=0.1, centroid_x=0.0, centroid_y=0.0,
        bbox=(0, 0, 1, 1))


def test_coverage_uses_valid_area():
    vm = np.zeros((100, 100), bool)
    vm[:50, :] = True                              # half the frame valid
    r = AnalysisResult(px_per_um=2.0, has_calibration=True,
                       grains=[_grain(1, 1000), _grain(2, 1500)],
                       valid_mask=vm, label_image=np.zeros((100, 100), np.int32))
    compute_statistics(r)
    assert r.valid_area_px == 5000
    assert r.valid_area_um2 == pytest.approx(1250.0)
    assert r.total_analyzed_area_um2 == pytest.approx(1250.0)
    assert r.invalid_area_pct == pytest.approx(50.0)
    assert r.grain_coverage_pct == pytest.approx(50.0)
    assert r.mean_area_um2 == pytest.approx(312.5)
    assert r.grain_count == 2


def test_legacy_fallback_whole_frame():
    r = AnalysisResult(px_per_um=1.0, has_calibration=True,
                       grains=[_grain(1, 2500, ppu=1.0)])
    compute_statistics(r, (100, 100))
    assert r.grain_coverage_pct == pytest.approx(25.0)
    assert r.total_analyzed_area_um2 == pytest.approx(10000.0)


def test_empty_grains_resets_stale_stats():
    r = AnalysisResult(px_per_um=1.0, has_calibration=True,
                       mean_area_um2=99.0, grain_coverage_pct=50.0,
                       valid_area_px=400.0)
    compute_statistics(r, (20, 20))
    assert r.mean_area_um2 == 0.0 and r.grain_coverage_pct == 0.0
    assert r.grain_count == 0


def test_detector_wrapper_is_the_same_implementation(mosaic_with_black_regions):
    bgr, black = mosaic_with_black_regions
    det = GrainDetector()
    r = det.analyze(bgr, 2.0, DetectionParams(detection_mode="boundary"))
    assert r.valid_area_px == pytest.approx((~black).sum(), rel=0.01)
    assert r.invalid_area_pct == pytest.approx(100 * black.mean(), abs=1.0)
    total = sum(g.area_px for g in r.grains)
    assert r.grain_coverage_pct == pytest.approx(100 * total / r.valid_area_px)
    assert r.grain_coverage_pct <= 100.0
    before = r.grain_coverage_pct
    # old UI call signature still works and gives the same answer
    det._compute_statistics(r, bgr)
    assert r.grain_coverage_pct == pytest.approx(before)


def test_discard_border_grains(mosaic_bgr):
    det = GrainDetector()
    r = det.analyze(mosaic_bgr, 2.0, DetectionParams(detection_mode="boundary"))
    n0, valid0 = r.grain_count, r.valid_area_px
    discard_border_grains(r, mosaic_bgr)
    lab = r.label_image
    edge = np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])
    assert not edge.any()
    assert 0 < r.grain_count < n0
    assert r.grain_count == len(r.grains)
    assert {g.grain_id for g in r.grains} == set(np.unique(lab[lab > 0]).tolist())
    assert r.valid_area_px == valid0
    total = sum(g.area_px for g in r.grains)
    assert r.grain_coverage_pct == pytest.approx(100 * total / valid0)
    assert r.overlay_image.shape == mosaic_bgr.shape
