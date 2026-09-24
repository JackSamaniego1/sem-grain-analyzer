"""
DET-01 / DET-08: the invalid-region (pure black) gate.

* compute_valid_mask keeps thin dark grooves valid and only rejects thick,
  large dark regions.
* Legitimately dark grains are still detected.
* Images without black regions give the same result as v2.3 (baseline
  counts captured from the pre-fix detector at commit 8565392).
"""
import os
import warnings

import numpy as np
import pytest

from conftest import make_mosaic
from core.grain_detector import (
    GrainDetector, DetectionParams, compute_valid_mask,
    gate_labels_by_validity, _remove_small_bool)


def _bgr(gray):
    return np.repeat(gray[:, :, None], 3, axis=2)


def _centroid_hits(result, mask):
    n = 0
    for g in result.grains:
        cy, cx = int(round(g.centroid_y)), int(round(g.centroid_x))
        if 0 <= cy < mask.shape[0] and 0 <= cx < mask.shape[1] and mask[cy, cx]:
            n += 1
    return n


# ----------------------------------------------------------------------
# compute_valid_mask
# ----------------------------------------------------------------------

def test_valid_mask_thin_grooves_stay_valid():
    g = np.full((200, 200), 120, np.uint8)
    g[:, 100:103] = 0            # 3 px pure-black groove, full height
    g[50:53, :] = 0              # crossing groove
    vm = compute_valid_mask(g, 12, 9, 400)
    assert vm.all()


def test_valid_mask_thick_black_region_invalid():
    g = np.full((200, 200), 120, np.uint8)
    g[120:, :] = 0               # 80 px band
    g[10:50, 10:50] = 0          # 40x40 hole
    vm = compute_valid_mask(g, 12, 9, 400)
    assert not vm[120:, :].any()
    assert not vm[10:50, 10:50].any()     # corners included
    assert vm[60:110, 60:190].all()
    # edges are exact (opening reconstructs the original extent)
    assert vm[119, 100] and vm[9, 30] and vm[30, 50]


def test_valid_mask_noisy_black_and_small_pit():
    rng = np.random.default_rng(0)
    g = np.full((200, 200), 120, np.uint8)
    g[150:, :] = rng.integers(0, 9, size=(50, 200))   # noisy detector black
    g[20:30, 20:30] = 0                                # 100 px pit < min area
    vm = compute_valid_mask(g, 12, 9, 400)
    assert (~vm[150:, :]).mean() > 0.99
    assert vm[20:30, 20:30].all()


def test_valid_mask_disabled_with_zero_threshold():
    g = np.zeros((64, 64), np.uint8)
    assert compute_valid_mask(g, 0).all()


def test_gate_labels_drops_dark_and_offmask_regions():
    gray = np.full((20, 20), 100, np.uint8)
    gray[:, 10:] = 3
    labels = np.zeros((20, 20), np.int32)
    labels[:, :10] = 5          # bright region
    labels[:, 10:] = 9          # black region
    valid = np.ones((20, 20), bool)
    out = gate_labels_by_validity(labels, gray, valid, 12, 0.5)
    assert set(np.unique(out)) == {0, 1}
    assert (out[:, :10] == 1).all() and (out[:, 10:] == 0).all()
    # valid-fraction criterion
    valid2 = np.ones((20, 20), bool)
    valid2[:15, :10] = False    # 75 % of the bright region is invalid
    out2 = gate_labels_by_validity(labels, np.full((20, 20), 100, np.uint8),
                                   valid2, 12, 0.5)
    assert not (out2[:, :10] > 0).any()


def test_gate_is_identity_when_nothing_to_remove():
    labels = np.array([[1, 1, 0], [3, 3, 0]], np.int32)
    gray = np.full(labels.shape, 100, np.uint8)
    out = gate_labels_by_validity(labels, gray, np.ones(labels.shape, bool), 12)
    assert out is labels


def test_remove_small_bool_keeps_objects_of_exactly_min_size():
    m = np.zeros((10, 10), bool)
    m[0, 0:4] = True     # 4 px
    m[5, 0:5] = True     # 5 px
    out = _remove_small_bool(m, 5)
    assert not out[0].any() and out[5, 0:5].all()


# ----------------------------------------------------------------------
# Detection behaviour
# ----------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["boundary", "threshold"])
def test_dark_grains_still_detected(dark_mosaic_bgr, mode):
    """DET-08: grains at gray 40-70 are legitimate; the gate must not
    touch them (baseline counts: boundary 80, threshold 60)."""
    baseline = {"boundary": 80, "threshold": 60}[mode]
    r = GrainDetector().analyze(dark_mosaic_bgr, 0.0,
                                DetectionParams(detection_mode=mode))
    assert r.invalid_area_pct == 0.0
    assert abs(r.grain_count - baseline) <= 0.05 * baseline, r.grain_count


def test_dark_grains_next_to_black(dark_mosaic_with_black_regions):
    bgr, black = dark_mosaic_with_black_regions
    r = GrainDetector().analyze(bgr, 0.0,
                                DetectionParams(detection_mode="boundary"))
    # (centroids are not used here: a legit C-shaped grain wrapping the
    # black square has its centroid inside the hole)
    lab = r.label_image
    for g in r.grains:
        m = lab == g.grain_id
        assert (m & black).sum() < 0.5 * m.sum()
    assert np.mean(lab[black] > 0) < 0.02
    assert r.grain_count >= 35
    assert r.invalid_area_pct == pytest.approx(100 * black.mean(), abs=1.0)


@pytest.mark.parametrize("seed,mode,baseline", [
    (7, "boundary", 82), (11, "boundary", 76), (23, "boundary", 77),
    (7, "threshold", 136), (11, "threshold", 114), (23, "threshold", 104),
])
def test_no_black_regions_matches_v23_baseline(seed, mode, baseline):
    gray, _ = make_mosaic(seed=seed)
    r = GrainDetector().analyze(_bgr(gray), 0.0,
                                DetectionParams(detection_mode=mode))
    assert abs(r.grain_count - baseline) <= 0.05 * baseline, r.grain_count
    assert r.valid_mask.all()


def test_threshold_dark_grains_ignores_black_band(mosaic_with_black_regions):
    bgr, black = mosaic_with_black_regions
    r = GrainDetector().analyze(
        bgr, 0.0, DetectionParams(detection_mode="threshold", dark_grains=True))
    assert r.binary_image[black].max() == 0


def test_all_black_image_yields_no_grains():
    r = GrainDetector().analyze(np.zeros((128, 128, 3), np.uint8), 1.0,
                                DetectionParams(detection_mode="boundary"))
    assert r.grain_count == 0
    assert r.valid_area_px == 0 and r.invalid_area_pct == 100.0


def test_gate_disabled_restores_legacy_behaviour(mosaic_with_black_regions):
    bgr, black = mosaic_with_black_regions
    r = GrainDetector().analyze(bgr, 0.0, DetectionParams(
        detection_mode="boundary", invalid_intensity_threshold=0))
    assert _centroid_hits(r, black) > 0      # the v2.3 bug, on purpose


def test_no_future_warnings(mosaic_bgr):
    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        warnings.simplefilter("error", DeprecationWarning)
        GrainDetector().analyze(mosaic_bgr, 0.0,
                                DetectionParams(detection_mode="threshold"))


def test_progress_callback_contract(mosaic_with_black_regions):
    bgr, _ = mosaic_with_black_regions
    calls = []
    GrainDetector().analyze(bgr, 0.0, DetectionParams(detection_mode="boundary"),
                            progress_callback=lambda p, m: calls.append((p, m)))
    assert calls and calls[-1][0] == 100
    assert all(isinstance(p, int) and isinstance(m, str) for p, m in calls)


def test_no_download_links_in_detector():
    """D-14: offline app; the missing-checkpoint error must say reinstall."""
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                        "core", "grain_detector.py")
    src = open(path, encoding="utf-8").read()
    assert "http://" not in src and "https://" not in src
    assert "reinstall" in src
