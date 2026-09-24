"""Exported overlays are full-frame (user report: exported overlay showed only
the scanned area, SEM info bar missing).

Every overlay a result carries must be the FULL original image at original
resolution: grain overlay at the analysed-region offset, everything outside
it (info bar, outside the scan area) bit-identical to the original, plus a
thin outline on the analysed-region boundary.  Measurements are untouched.
"""
import cv2
import numpy as np
import pytest

from conftest import make_mosaic
from test_infobar import add_bar
from core.grain_detector import GrainDetector, DetectionParams
from core.overlay_compose import (
    analysed_rect_from_mask, compose_full_overlay, draw_analysed_outline,
    rc_to_xywh,
)


def _bar_image():
    img, bar, _ = add_bar(make_mosaic()[0], "bottom", "black", 0.10)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR), bar


def _outside(shape, rect):
    """Boolean mask of pixels outside rect (x, y, w, h)."""
    m = np.ones(shape[:2], bool)
    x, y, w, h = rect
    m[y:y + h, x:x + w] = False
    return m


# ---------------------------------------------------------------- unit ----

def test_compose_pastes_at_offset_and_keeps_outside_unaltered():
    rng = np.random.default_rng(0)
    orig = rng.integers(0, 255, (60, 80, 3), dtype=np.uint8)
    crop = np.full((20, 30, 3), 7, np.uint8)
    rect = (10, 5, 30, 20)
    before = orig.copy()
    out = compose_full_overlay(orig, crop, rect)
    assert out.shape == orig.shape
    np.testing.assert_array_equal(orig, before)          # input not mutated
    out_m = _outside(orig.shape, rect)
    np.testing.assert_array_equal(out[out_m], orig[out_m])
    # interior (inside the 1-px outline ring) is the crop overlay
    np.testing.assert_array_equal(out[6:24, 11:39], crop[1:-1, 1:-1])
    # outline ring differs from the plain paste (it is visible)
    assert not np.array_equal(out[5, 10:40], crop[0])


def test_compose_no_outline_on_image_border_sides_and_full_frame():
    orig = np.full((40, 50, 3), 100, np.uint8)
    crop = np.full((36, 50, 3), 100, np.uint8)
    out = compose_full_overlay(orig, crop, (0, 0, 50, 36))    # bottom bar only
    changed = np.argwhere((out != orig).any(axis=2))
    assert len(changed) and set(changed[:, 0]) == {35}        # only the bottom side
    same = compose_full_overlay(orig, orig, None)             # nothing analysed-crop
    np.testing.assert_array_equal(same, orig)


def test_compose_rejects_mismatched_rect_and_accepts_full_overlay():
    orig = np.zeros((40, 50, 3), np.uint8)
    with pytest.raises(ValueError):
        compose_full_overlay(orig, np.zeros((10, 10, 3), np.uint8), (45, 0, 10, 10))
    with pytest.raises(ValueError):
        compose_full_overlay(orig, np.zeros((10, 10, 3), np.uint8), None)
    full = np.full((40, 50, 3), 9, np.uint8)
    out = compose_full_overlay(orig, full, (5, 5, 10, 10))
    assert out.shape == orig.shape and out[0, 0, 0] == 9


def test_rect_helpers():
    assert rc_to_xywh((5, 10, 25, 50)) == (10, 5, 40, 20)
    assert rc_to_xywh(None) is None
    vm = np.zeros((30, 40), bool)
    assert analysed_rect_from_mask(vm | True) is None
    vm[3:20, 4:35] = True
    assert analysed_rect_from_mask(vm) == (4, 3, 31, 17)
    assert analysed_rect_from_mask(vm, (31, 40)) is None       # shape mismatch
    img = np.zeros((30, 40, 3), np.uint8)
    assert draw_analysed_outline(img, None) is img and not img.any()


# ------------------------------------------------------------ detector ----

def test_detector_overlay_is_full_frame_with_info_bar():
    """Core API: labels stay in crop coords (measurements unchanged), the
    overlay is the full frame with the data bar untouched."""
    bgr, bar = _bar_image()
    res = GrainDetector().analyze(bgr)
    assert res.auto_crop_rect is not None
    x, y, w, h = rc_to_xywh(res.auto_crop_rect)
    assert res.label_image.shape == (h, w)                    # unchanged contract
    assert res.overlay_image.shape == bgr.shape
    out_m = _outside(bgr.shape, (x, y, w, h))
    np.testing.assert_array_equal(res.overlay_image[out_m], bgr[out_m])
    assert np.array_equal(res.overlay_image[bar[1]:], bgr[bar[1]:])
    crop_ov = GrainDetector()._draw_overlay(
        bgr[y:y + h, x:x + w], res.label_image, res.grains)
    np.testing.assert_array_equal(res.overlay_image[y:y + h - 1, x:x + w],
                                  crop_ov[:-1])               # all but outline row


def test_detector_measurements_unaffected_by_overlay_change():
    bgr, _ = _bar_image()
    a = GrainDetector().analyze(bgr)
    x, y, w, h = rc_to_xywh(a.auto_crop_rect)
    b = GrainDetector().analyze(np.ascontiguousarray(bgr[y:y + h, x:x + w]),
                                params=DetectionParams(auto_exclude_info_bar=False))
    assert a.grain_count == b.grain_count
    np.testing.assert_array_equal(a.label_image, b.label_image)


# ------------------------------------------------------------ UI flow -----

@pytest.mark.parametrize("scan", [None, (40, 30, 400, 380)])
def test_app_flow_overlays_full_frame(scan):
    """analyze_image -> filter_image -> edit -> redraw_overlay: every overlay
    is full-frame, the info bar and outside-scan pixels are the original."""
    from ui.workers import analyze_image, redraw_overlay
    from ui.filtering import analysed_rect, default_options, filter_image
    bgr, bar = _bar_image()
    raw = analyze_image(bgr, px_per_um=1.0, scan_rect=scan)
    rect = analysed_rect(raw, bgr.shape)
    assert rect is not None
    out_m = _outside(bgr.shape, rect)

    def check(ov):
        assert ov is not None and ov.shape == bgr.shape
        np.testing.assert_array_equal(ov[out_m], bgr[out_m])
        np.testing.assert_array_equal(ov[bar[1]:], bgr[bar[1]:])
        x, y, w, h = rect                                   # outline drawn
        ring = np.zeros(bgr.shape[:2], bool)
        ring[y:y + h, x:x + w] = True
        ring[y + 1:y + h - 1, x + 1:x + w - 1] = False
        assert (ov[ring] != bgr[ring]).any()

    check(raw.overlay_image)
    res = filter_image(raw, bgr, default_options(scan))["result"]
    check(res.overlay_image)
    # a grain edit (delete) then redraw keeps the full frame
    gone = res.grains[0].grain_id
    res.label_image = np.where(res.label_image == gone, 0, res.label_image)
    res.grains = res.grains[1:]
    redraw_overlay(bgr, res)
    check(res.overlay_image)


def test_redraw_overlay_places_crop_result_into_full_frame():
    from ui.workers import redraw_overlay
    bgr, bar = _bar_image()
    res = GrainDetector().analyze(bgr)                 # crop-coordinate labels
    res.overlay_image = None
    redraw_overlay(bgr, res)
    assert res.overlay_image.shape == bgr.shape
    np.testing.assert_array_equal(res.overlay_image[bar[1]:], bgr[bar[1]:])


def test_canvas_click_maps_to_grain_on_full_frame(qapp):
    """Click-to-grain mapping on the full-frame canvas hits the right grain
    (labels are full-frame, info bar never selects anything)."""
    pytest.importorskip("pytestqt")
    from PySide6.QtCore import QPointF
    from ui.canvas import GrainCanvas
    from ui.workers import analyze_image
    bgr, bar = _bar_image()
    res = analyze_image(bgr, px_per_um=1.0, scan_rect=(40, 30, 400, 380))
    c = GrainCanvas()
    c.resize(512, 512)
    c.set_image(bgr, res)
    c.fit(animate=False)
    s, off = c._scale, c._offset

    def at(ix, iy):
        return c.grain_at(QPointF(off.x() + (ix + 0.5) * s, off.y() + (iy + 0.5) * s))

    hits = 0
    for g in res.grains[:15]:
        ys, xs = np.nonzero(res.label_image == g.grain_id)
        i = len(ys) // 2
        hits += at(xs[i], ys[i]) == g.grain_id
    assert hits == min(15, len(res.grains))
    assert at(bgr.shape[1] // 2, bar[1] + bar[3] // 2) == 0     # info bar
    assert at(10, 10) == 0                                      # outside scan area
