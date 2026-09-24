"""Tests for core.postfilter (DET-10): non-destructive post-analysis
grain filters (border elimination, false-grain / low-contrast
elimination, size/shape limits, manual deletion)."""
import copy
import time

import numpy as np
import pytest

from core.astm import update_astm
from core.grain_detector import AnalysisResult, DetectionParams, GrainResult
from core.metrics import compute_statistics
from core.postfilter import (
    FilterOutcome,
    PostFilterOptions,
    apply_post_filters,
    draw_filtered_overlay,
)


def _grain(gid, area_px=100.0, aspect_ratio=1.2, circularity=0.85,
          cx=0.0, cy=0.0, bbox=(0, 0, 1, 1)):
    return GrainResult(
        grain_id=gid, area_px=area_px, area_um2=area_px,
        perimeter_px=4 * area_px ** 0.5, perimeter_um=4 * area_px ** 0.5,
        equivalent_diameter_px=2 * (area_px / np.pi) ** 0.5,
        equivalent_diameter_um=2 * (area_px / np.pi) ** 0.5,
        major_axis_um=10.0, minor_axis_um=8.0, aspect_ratio=aspect_ratio,
        circularity=circularity, eccentricity=0.5,
        centroid_x=cx, centroid_y=cy, bbox=bbox,
    )


def _fill_rect(labels, gid, r0, r1, c0, c1):
    labels[r0:r1, c0:c1] = gid


def build_result(h=80, w=80, px_per_um=1.0, has_calibration=True):
    """A small synthetic AnalysisResult with hand-placed grains:

    1: interior, normal contrast/size            -> always kept
    2: touches the top image edge                 -> border
    3: interior, adjacent to an invalid region     -> touching_invalid
    4: interior, uniform DARK fill (std=0, mean=5) -> low_contrast + too_dark
    5: interior, uniform BRIGHT fill (std=0)       -> low_contrast only
    6: interior, low mean but noisy (std>3)        -> too_dark only
    7: tiny grain                                  -> too_small (with options)
    8: huge grain                                  -> too_large (with options)
    9: normal, will be manually deleted            -> manual
    """
    rng = np.random.default_rng(0)
    gray = rng.integers(110, 180, size=(h, w)).astype(np.uint8)
    labels = np.zeros((h, w), dtype=np.int32)
    valid = np.ones((h, w), dtype=bool)

    _fill_rect(labels, 1, 10, 20, 10, 20)
    _fill_rect(labels, 2, 0, 5, 30, 40)                 # touches row 0
    _fill_rect(labels, 3, 25, 35, 25, 35)
    valid[25:35, 22:25] = False                          # left of grain 3
    _fill_rect(labels, 4, 40, 50, 5, 15)
    gray[40:50, 5:15] = 5
    _fill_rect(labels, 5, 40, 50, 20, 30)
    gray[40:50, 20:30] = 150
    _fill_rect(labels, 6, 40, 50, 35, 45)
    gray[40:50, 35:45] = rng.integers(0, 21, size=(10, 10)).astype(np.uint8)
    _fill_rect(labels, 7, 60, 62, 5, 7)
    _fill_rect(labels, 8, 55, 79, 45, 79)
    _fill_rect(labels, 9, 60, 65, 50, 55)

    grains = []
    for gid in range(1, 10):
        area = float(np.count_nonzero(labels == gid))
        ys, xs = np.nonzero(labels == gid)
        cy, cx = float(ys.mean()), float(xs.mean())
        grains.append(_grain(gid, area_px=area, cx=cx, cy=cy,
                             bbox=(int(ys.min()), int(xs.min()),
                                   int(ys.max()) + 1, int(xs.max()) + 1)))

    result = AnalysisResult(
        grains=grains, grain_count=len(grains), label_image=labels,
        binary_image=(labels > 0).astype(np.uint8) * 255,
        px_per_um=px_per_um, has_calibration=has_calibration,
        valid_mask=valid,
    )
    compute_statistics(result, (h, w))
    image_bgr = np.repeat(gray[:, :, None], 3, axis=2)
    return result, image_bgr


# ---------------------------------------------------------------------
# Reason codes
# ---------------------------------------------------------------------

def test_border_reason():
    raw, img = build_result()
    outcome = apply_post_filters(raw, img, PostFilterOptions(exclude_border=True))
    assert outcome.excluded.get(2) == ["border"]
    assert 1 not in outcome.excluded
    assert outcome.counts.get("border") == 1
    assert 2 not in {g.grain_id for g in outcome.result.grains}
    assert 1 in {g.grain_id for g in outcome.result.grains}


def test_touching_invalid_reason():
    raw, img = build_result()
    outcome = apply_post_filters(raw, img, PostFilterOptions(exclude_touching_invalid=True))
    assert outcome.excluded.get(3) == ["touching_invalid"]
    assert outcome.counts.get("touching_invalid") == 1


def test_low_contrast_and_too_dark_reasons():
    raw, img = build_result()
    outcome = apply_post_filters(raw, img, PostFilterOptions(exclude_low_contrast=True))
    assert set(outcome.excluded.get(4)) == {"low_contrast", "too_dark"}
    assert outcome.excluded.get(5) == ["low_contrast"]
    assert outcome.excluded.get(6) == ["too_dark"]
    assert 1 not in outcome.excluded
    assert outcome.counts["low_contrast"] == 2
    assert outcome.counts["too_dark"] == 2


def test_size_reasons():
    raw, img = build_result()
    opts = PostFilterOptions(min_area_px=20, max_area_px=500)
    outcome = apply_post_filters(raw, img, opts)
    assert outcome.excluded.get(7) == ["too_small"]
    assert outcome.excluded.get(8) == ["too_large"]
    assert 1 not in outcome.excluded


def test_aspect_and_circularity_reasons():
    raw, img = build_result()
    # grain 1 given an artificially high aspect ratio / low circularity via
    # its GrainResult fields (filters read the dataclass fields directly).
    for g in raw.grains:
        if g.grain_id == 1:
            g.aspect_ratio = 9.0
            g.circularity = 0.1
    outcome = apply_post_filters(
        raw, img, PostFilterOptions(max_aspect_ratio=4.0, min_circularity=0.3))
    assert set(outcome.excluded.get(1)) == {"elongated", "low_circularity"}


def test_manual_exclusion():
    raw, img = build_result()
    outcome = apply_post_filters(raw, img, PostFilterOptions(), manual_excluded=frozenset({9}))
    assert outcome.excluded.get(9) == ["manual"]
    assert outcome.counts.get("manual") == 1
    assert 9 not in {g.grain_id for g in outcome.result.grains}


def test_multi_reason_counts():
    raw, img = build_result()
    opts = PostFilterOptions(exclude_border=True, exclude_touching_invalid=True,
                             exclude_low_contrast=True)
    outcome = apply_post_filters(raw, img, opts, manual_excluded=frozenset({9}))
    assert outcome.counts["border"] == 1
    assert outcome.counts["touching_invalid"] == 1
    assert outcome.counts["low_contrast"] == 2
    assert outcome.counts["too_dark"] == 2
    assert outcome.counts["manual"] == 1
    # grain 1 survives everything
    assert 1 in {g.grain_id for g in outcome.result.grains}


# ---------------------------------------------------------------------
# Non-destructiveness / toggles
# ---------------------------------------------------------------------

def test_raw_not_mutated():
    raw, img = build_result()
    raw_labels_before = raw.label_image.copy()
    raw_count_before = raw.grain_count
    raw_sum_before = int(raw.label_image.sum())
    astm_before = update_astm(raw)
    astm_before_copy = copy.deepcopy(astm_before)

    opts = PostFilterOptions(exclude_border=True, exclude_touching_invalid=True,
                             exclude_low_contrast=True, min_area_px=20,
                             max_area_px=500, max_aspect_ratio=4.0,
                             min_circularity=0.3)
    apply_post_filters(raw, img, opts, manual_excluded=frozenset({9}))

    assert np.array_equal(raw.label_image, raw_labels_before)
    assert int(raw.label_image.sum()) == raw_sum_before
    assert raw.grain_count == raw_count_before
    assert raw.astm == astm_before_copy


def test_toggle_off_matches_raw_stats():
    raw, img = build_result()
    outcome = apply_post_filters(raw, img, PostFilterOptions())
    assert outcome.excluded == {}
    assert outcome.result.grain_count == raw.grain_count
    assert outcome.result.grain_coverage_pct == pytest.approx(raw.grain_coverage_pct)
    assert outcome.result.mean_area_um2 == pytest.approx(raw.mean_area_um2)
    assert int(outcome.result.label_image.sum()) == int(raw.label_image.sum())
    # returned label image must be an independent array
    assert outcome.result.label_image is not raw.label_image


def test_stats_recomputed_after_exclusion():
    raw, img = build_result()
    outcome = apply_post_filters(raw, img, PostFilterOptions(exclude_border=True))
    assert outcome.result.grain_count == raw.grain_count - 1
    assert outcome.result.grain_coverage_pct < raw.grain_coverage_pct
    # excluded label's pixels are zeroed in the returned label image
    assert not np.any(outcome.result.label_image == 2)
    assert np.any(raw.label_image == 2)


# ---------------------------------------------------------------------
# Overlay
# ---------------------------------------------------------------------

def test_overlay_shape_dtype_and_greying():
    raw, img = build_result()
    outcome = apply_post_filters(raw, img, PostFilterOptions(exclude_border=True))
    ov = outcome.result.overlay_image
    assert ov.shape == img.shape
    assert ov.dtype == np.uint8

    kept_only = draw_filtered_overlay(img, raw.label_image, {1, 2}, {})
    excl_2 = draw_filtered_overlay(img, raw.label_image, {1}, {2: ["border"]})
    # a pixel inside grain 2's region should differ between the two calls
    # (colour-coded fill vs. translucent grey), and the excluded version's
    # pixel should be roughly neutral grey (channels close to each other).
    py, px = 2, 35
    assert not np.array_equal(kept_only[py, px], excl_2[py, px])
    ch = excl_2[py, px].astype(int)
    assert max(ch) - min(ch) <= 12


def test_draw_filtered_overlay_direct():
    raw, img = build_result()
    ov = draw_filtered_overlay(img, raw.label_image, {1, 3, 4, 5, 6, 7, 8, 9},
                               {2: ["border"]})
    assert ov.shape == img.shape
    assert ov.dtype == np.uint8


# ---------------------------------------------------------------------
# ASTM
# ---------------------------------------------------------------------

def test_astm_unaffected_by_border_but_changed_by_low_contrast():
    raw, img = build_result()
    update_astm(raw, DetectionParams())
    assert raw.astm.get("G_planimetric") is not None

    out_border = apply_post_filters(raw, img, PostFilterOptions(exclude_border=True))
    assert out_border.result.astm.get("G_planimetric") == pytest.approx(
        raw.astm.get("G_planimetric"))
    assert out_border.result.astm.get("grains_counted") == pytest.approx(
        raw.astm.get("grains_counted"))
    notes = out_border.result.astm.get("notes") or []
    assert any("border grains RETAINED" in n for n in notes)

    out_lc = apply_post_filters(raw, img, PostFilterOptions(exclude_low_contrast=True))
    assert out_lc.result.astm.get("grains_counted") != pytest.approx(
        raw.astm.get("grains_counted"))


def test_astm_failure_does_not_raise(monkeypatch):
    raw, img = build_result()
    update_astm(raw)

    import core.postfilter as pf

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(pf, "compute_astm", boom)
    outcome = apply_post_filters(raw, img, PostFilterOptions(exclude_border=True))
    # falls back to a copy of raw.astm rather than raising
    assert outcome.result.astm.get("G_planimetric") == raw.astm.get("G_planimetric")


# ---------------------------------------------------------------------
# Options (de)serialisation
# ---------------------------------------------------------------------

def test_options_round_trip_and_unknown_keys_ignored():
    opts = PostFilterOptions(exclude_border=True, min_area_px=30)
    d = opts.to_dict()
    assert d["exclude_border"] is True
    d["totally_unknown_future_field"] = 123
    restored = PostFilterOptions.from_dict(d)
    assert restored == PostFilterOptions(exclude_border=True, min_area_px=30)
    assert PostFilterOptions.from_dict(None) == PostFilterOptions()
    assert PostFilterOptions.from_dict({}) == PostFilterOptions()


# ---------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------

def _big_grid_result(h=1536, w=2048, rows=45, cols=45, cell_h=35, cell_w=46,
                     invalid_corner=False):
    rng = np.random.default_rng(1)
    grid_r = np.minimum(np.arange(h) // cell_h, rows - 1)
    grid_c = np.minimum(np.arange(w) // cell_w, cols - 1)
    labels = (grid_r[:, None] * cols + grid_c[None, :] + 1).astype(np.int32)
    gray = rng.integers(90, 200, size=(h, w)).astype(np.uint8)
    image_bgr = np.repeat(gray[:, :, None], 3, axis=2)
    valid = np.ones((h, w), dtype=bool)
    if invalid_corner:
        valid[0:20, 0:20] = False

    ids = np.unique(labels)
    counts = np.bincount(labels.ravel())
    grains = [_grain(int(gid), area_px=float(counts[gid]), aspect_ratio=1.5,
                     circularity=0.7) for gid in ids]

    result = AnalysisResult(grains=grains, grain_count=len(grains),
                            label_image=labels, px_per_um=1.0,
                            has_calibration=True, valid_mask=valid)
    compute_statistics(result, (h, w))
    return result, image_bgr


def test_performance_single_toggle():
    """The common interactive case: one toggle (border elimination) with no
    grains removed for a non-border reason. ``apply_post_filters`` takes the
    fast ASTM short-circuit (see module docstring) since E112 planimetric
    counting is unaffected by border exclusion, so this should stay close to
    the < 150 ms budget."""
    result, image_bgr = _big_grid_result()
    update_astm(result)

    opts = PostFilterOptions(exclude_border=True)
    t0 = time.perf_counter()
    outcome = apply_post_filters(result, image_bgr, opts)
    elapsed = time.perf_counter() - t0

    assert len(result.grains) >= 1900
    assert isinstance(outcome, FilterOutcome)
    assert elapsed < 0.35, f"apply_post_filters took {elapsed:.3f}s"
    print(f"\napply_post_filters (border-only, {len(result.grains)} grains, "
         f"{image_bgr.shape[1]}x{image_bgr.shape[0]}): {elapsed * 1000:.1f} ms")


def test_performance_full_recompute():
    """Worst case: exclusions that actually remove grains for a *non*-border
    reason (touching_invalid here), so ``core.astm.compute_astm`` must be
    run for real with the "both" (planimetric + intercept) method. That
    intercept-grid evaluation lives in core/astm.py (owned by another
    workstream, read-only for this task) and dominates the measured time —
    it alone accounts for the bulk of this. Loose bound only: this exercises
    code this module does not own."""
    result, image_bgr = _big_grid_result(invalid_corner=True)
    opts = PostFilterOptions(exclude_border=True, exclude_touching_invalid=True,
                             exclude_low_contrast=True, min_area_px=100,
                             max_area_px=100000, max_aspect_ratio=4.0,
                             min_circularity=0.1)

    t0 = time.perf_counter()
    outcome = apply_post_filters(result, image_bgr, opts)
    elapsed = time.perf_counter() - t0

    assert isinstance(outcome, FilterOutcome)
    assert elapsed < 1.5, f"apply_post_filters took {elapsed:.3f}s"
    print(f"\napply_post_filters (full recompute incl. ASTM intercept, "
         f"{len(result.grains)} grains): {elapsed * 1000:.1f} ms")
