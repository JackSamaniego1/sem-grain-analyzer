"""
UPDATE 4 item 12 (UI side): the grain overlay is built through lookup
tables and, after a grain edit, only the changed rectangle is redrawn into
the cached overlay.  Pixels must be identical to the straightforward
whole-image drawing in every case.
"""
import cv2
import numpy as np
import pytest

pytest.importorskip("pytestqt")

from PySide6.QtGui import QImage, QPixmap  # noqa: E402

from ui.canvas.layers import (  # noqa: E402
    _id_mask, boundaries, changed_rect, label_palette, overlay_layer, overlay_patch,
)


def _reference(labels, excluded=(), show_excluded=True, fill_alpha=78, edge_alpha=235):
    """The drawing as it was written before item 12 (kept as the oracle)."""
    lab = labels.astype(np.int64, copy=False)
    lut = label_palette(int(lab.max()) if lab.size else 0)
    rgba = np.zeros(lab.shape + (4,), dtype=np.uint8)
    rgba[..., :3] = lut[lab]
    rgba[..., 3] = np.where(lab > 0, fill_alpha, 0).astype(np.uint8)
    edge = boundaries(lab)
    rgba[edge, 3] = edge_alpha
    rgba[edge, :3] = np.minimum(255, rgba[edge, :3].astype(np.int16) + 40).astype(np.uint8)
    if excluded:
        ex = _id_mask(lab, excluded)
        if show_excluded:
            h, w = lab.shape
            yy, xx = np.ogrid[:h, :w]
            stripe = ((xx - yy) // 5) % 2 == 0
            rgba[ex, 0:3] = 150
            rgba[ex, 3] = np.where(stripe, 120, 60)[ex] if ex.any() else 0
            exe = ex & edge
            rgba[exe, 0:3] = 200
            rgba[exe, 3] = 200
        else:
            rgba[ex] = 0
    return rgba


def _qimage_rgba(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format_RGBA8888)
    h, w = img.height(), img.width()
    buf = img.constBits()
    arr = np.frombuffer(buf, dtype=np.uint8, count=h * img.bytesPerLine())
    return arr.reshape(h, img.bytesPerLine())[:, :w * 4].reshape(h, w, 4).copy()


def _labels(h=96, w=128, n=40, seed=1):
    rng = np.random.default_rng(seed)
    seeds = rng.integers(0, [h, w], size=(n, 2))
    yy, xx = np.mgrid[:h, :w]
    d = (yy[..., None] - seeds[:, 0]) ** 2 + (xx[..., None] - seeds[:, 1]) ** 2
    lab = (d.argmin(axis=-1) + 1).astype(np.int32)
    lab[boundaries(lab)] = 0
    return lab


@pytest.mark.parametrize("excluded,show", [((), True), ((3, 7, 12), True), ((3, 7, 12), False),
                                           ((0, 999, 5), True)])
def test_overlay_layer_matches_reference(qapp, excluded, show):
    lab = _labels()
    got = _qimage_rgba(overlay_layer(lab, list(excluded), show))
    assert np.array_equal(got, _reference(lab, excluded, show))


def test_overlay_layer_int_dtypes(qapp):
    lab = _labels()
    ref = _reference(lab, (2, 4), True)
    for dt in (np.int32, np.int64, np.uint16):
        assert np.array_equal(_qimage_rgba(overlay_layer(lab.astype(dt), [2, 4], True)), ref)


def test_overlay_patch_equals_region_of_full(qapp):
    lab = _labels()
    ref = _reference(lab, (5, 9), True)
    for rect in [(0, 96, 0, 128), (10, 50, 20, 70), (0, 1, 0, 128), (90, 200, 120, 300)]:
        img, x0, y0 = overlay_patch(lab, rect, [5, 9], True)
        y1, x1 = min(96, rect[1]), min(128, rect[3])
        assert (x0, y0) == (rect[2], rect[0])
        assert np.array_equal(_qimage_rgba(img), ref[y0:y1, x0:x1])
    assert overlay_patch(lab, (10, 10, 0, 5), [], True)[0].isNull()


def test_changed_rect():
    a = _labels()
    b = a.copy()
    assert changed_rect(a, b) is None
    assert changed_rect(a, a) is None
    b[30:40, 50:60] = 999
    assert changed_rect(a, b) == (30, 40, 50, 60)
    ys, xs = np.nonzero(a == 3)
    r = changed_rect(a, a, ids=[3])
    assert r == (ys.min(), ys.max() + 1, xs.min(), xs.max() + 1)
    assert changed_rect(None, b) == (0, 96, 0, 128)
    assert changed_rect(a[:50], b) == (0, 96, 0, 128)
    assert changed_rect(a, None) is None


class _Grain:
    def __init__(self, gid, lab):
        self.grain_id = gid
        ys, xs = np.nonzero(lab == gid)
        self.bbox = (int(ys.min()), int(xs.min()), int(ys.max()) + 1, int(xs.max()) + 1)
        self.area_um2 = float(len(ys))
        self.equivalent_diameter_um = 1.0
        self.circularity = 0.9


class _Result:
    def __init__(self, lab, valid=None):
        self.label_image = lab
        self.grains = [_Grain(i, lab) for i in np.unique(lab) if i > 0]
        self.valid_mask = valid
        self.overlay_image = None
        self.binary_image = None


def _canvas(qtbot, lab, excluded=None, valid=None):
    from ui.canvas.grain_canvas import GrainCanvas
    c = GrainCanvas()
    qtbot.addWidget(c)
    bgr = np.full(lab.shape + (3,), 90, np.uint8)
    res = _Result(lab, valid)
    c.set_image(bgr, res, raw=res, excluded=excluded or {})
    c.set_view("overlay")
    return c, res


def _overlay_pixels(c):
    return _qimage_rgba(c._pm["overlay"].toImage())


def _full_pixels(lab, excluded=(), show=True):
    """What a full rebuild of the cached pixmap shows (same premultiplied
    round trip as the canvas, so the comparison is exact)."""
    return _qimage_rgba(QPixmap.fromImage(overlay_layer(lab, list(excluded), show)).toImage())


def test_canvas_patches_overlay_after_edit(qtbot):
    lab = _labels()
    c, res = _canvas(qtbot, lab)
    before = c._pm["overlay"]
    # merge-like edit: grain 8 absorbed into 4 -> new label array
    lab2 = lab.copy()
    lab2[lab2 == 8] = 4
    res2 = _Result(lab2)
    c.set_result(res2, raw=res2, excluded={})
    assert c._pm["overlay"] is before          # patched in place, not rebuilt
    assert np.array_equal(_overlay_pixels(c), _full_pixels(lab2))


def test_canvas_patches_overlay_on_exclusion_change(qtbot):
    lab = _labels()
    c, res = _canvas(qtbot, lab)
    before = c._pm["overlay"]
    c.set_result(res, raw=res, excluded={6: ["manual"], 11: ["size"]})
    assert c._pm["overlay"] is before
    assert np.array_equal(_overlay_pixels(c), _full_pixels(lab, (6, 11)))
    c.set_result(res, raw=res, excluded={11: ["size"]})   # one put back
    assert np.array_equal(_overlay_pixels(c), _full_pixels(lab, (11,)))


def test_canvas_rebuilds_when_most_of_the_image_changed(qtbot):
    lab = _labels()
    c, _ = _canvas(qtbot, lab)
    before = c._pm["overlay"]
    lab2 = _labels(seed=2)
    res2 = _Result(lab2)
    c.set_result(res2, raw=res2, excluded={})
    assert c._pm["overlay"] is not before
    assert np.array_equal(_overlay_pixels(c), _full_pixels(lab2))


def test_canvas_keeps_unchanged_side_layers(qtbot):
    lab = _labels()
    valid = np.ones(lab.shape, bool)
    valid[:, :20] = False
    c, res = _canvas(qtbot, lab, valid=valid)
    c.set_show_excluded_regions(True)
    c.set_view("excluded")
    tint, desat = c._pm["excluded_layer"], c._pm["desat"]
    res2 = _Result(lab.copy(), valid.copy())          # refilter: same content, new arrays
    c.set_result(res2, raw=res2, excluded={})
    assert c._pm["excluded_layer"] is tint and c._pm["desat"] is desat
    valid2 = valid.copy()
    valid2[:, -20:] = False
    res3 = _Result(lab.copy(), valid2)
    c.set_result(res3, raw=res3, excluded={})
    assert c._pm["excluded_layer"] is not tint and c._pm["desat"] is desat
