"""
Regression tests for: "regions that are completely black are being
identified as grains."

These tests are EXPECTED TO FAIL on the v2.3 detector — they are the
acceptance criteria for the fix (task DET-01 in handoff/03_TASK_BOARD.md).

Root cause (core/grain_detector.py):
  * boundary pipeline: the watershed landscape is built purely from contrast
    signals. A uniform black region has zero contrast, so `interior = 1 -
    boosted` is ~1.0 there; peak_local_max seeds it and watershed floods it
    into one or more "grains". There is no intensity gate anywhere.
  * threshold pipeline with dark_grains=True: black areas become foreground.
  * SAM pipeline: no mean-intensity / std-dev filter on accepted masks.
  * _measure_grains never sees the gray image, so it cannot reject them.
"""
import numpy as np
import pytest

from core.grain_detector import GrainDetector, DetectionParams


def grains_in_mask(result, mask):
    """Grains whose centroid lies inside `mask`."""
    hits = []
    for g in result.grains:
        cy, cx = int(round(g.centroid_y)), int(round(g.centroid_x))
        if 0 <= cy < mask.shape[0] and 0 <= cx < mask.shape[1] and mask[cy, cx]:
            hits.append(g)
    return hits


def label_pixels_in_mask(result, mask):
    """Fraction of black pixels that were assigned to any grain label."""
    if result.label_image is None:
        return 0.0
    return float(np.mean(result.label_image[mask] > 0))


@pytest.mark.parametrize("mode", ["boundary", "threshold"])
def test_black_regions_are_not_grains(mosaic_with_black_regions, mode):
    bgr, black = mosaic_with_black_regions
    params = DetectionParams(detection_mode=mode,
                             dark_grains=(mode == "threshold"))
    result = GrainDetector().analyze(bgr, px_per_um=0.0, params=params)

    bad = grains_in_mask(result, black)
    covered = label_pixels_in_mask(result, black)

    assert not bad, (
        f"{len(bad)} grain(s) have centroids inside pure-black regions "
        f"in '{mode}' mode (ids: {[g.grain_id for g in bad][:10]})")
    assert covered < 0.02, (
        f"{covered:.1%} of black pixels were labelled as grain in '{mode}' mode")


def test_black_regions_do_not_suppress_real_grains(mosaic_with_black_regions):
    """The fix must not throw away legitimate grains next to black areas."""
    bgr, black = mosaic_with_black_regions
    params = DetectionParams(detection_mode="boundary")
    result = GrainDetector().analyze(bgr, px_per_um=0.0, params=params)
    good = [g for g in result.grains if g not in grains_in_mask(result, black)]
    # ~90 synthetic cells, ~30% of the frame is black -> expect dozens remain.
    assert len(good) >= 30, f"only {len(good)} grains detected outside black regions"


def test_plain_mosaic_detects_reasonable_count(mosaic_bgr):
    """Sanity: the untouched mosaic yields a plausible grain count."""
    params = DetectionParams(detection_mode="boundary")
    result = GrainDetector().analyze(mosaic_bgr, px_per_um=0.0, params=params)
    assert 40 <= result.grain_count <= 200, result.grain_count
