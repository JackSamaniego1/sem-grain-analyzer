"""
Pure numpy helpers that turn an ``AnalysisResult`` into display layers.
No widgets here — everything returns numpy arrays / QImages and is cheap
enough to run on the GUI thread for a 2048x1536 image (vectorised).
"""
from __future__ import annotations

from typing import Optional

import cv2
import numpy as np
from PySide6.QtGui import QImage

from ui.workers import mask_to_display


def label_palette(n_labels: int) -> np.ndarray:
    """(n+1, 3) RGB lut; same golden-angle hue sequence as the core overlay."""
    ids = np.arange(n_labels + 1, dtype=np.float64)
    hue = ((ids * 137.508) % 180).astype(np.uint8)
    hsv = np.stack([hue, np.full_like(hue, 200), np.full_like(hue, 225)], axis=1)
    rgb = cv2.cvtColor(hsv[:, None, :], cv2.COLOR_HSV2RGB)[:, 0, :]
    rgb[0] = 0
    return rgb


def boundaries(labels: np.ndarray) -> np.ndarray:
    """Bool mask of pixels on a label boundary (4-neighbour change)."""
    b = np.zeros(labels.shape, dtype=bool)
    d = labels[:, 1:] != labels[:, :-1]
    b[:, 1:] |= d
    b[:, :-1] |= d
    d = labels[1:, :] != labels[:-1, :]
    b[1:, :] |= d
    b[:-1, :] |= d
    return b & (labels > 0)


def _rgba_qimage(rgba: np.ndarray) -> QImage:
    h, w = rgba.shape[:2]
    rgba = np.ascontiguousarray(rgba)
    return QImage(rgba.data, w, h, w * 4, QImage.Format_RGBA8888).copy()


def _id_mask(labels: np.ndarray, ids) -> np.ndarray:
    """Bool lookup over ``labels`` for a set of ids (vectorised)."""
    n = int(labels.max()) + 1 if labels.size else 1
    lut = np.zeros(n, dtype=bool)
    arr = np.asarray([int(i) for i in ids if 0 < int(i) < n], dtype=np.int64)
    if arr.size:
        lut[arr] = True
    return lut[labels]


def _overlay_rgba(lab: np.ndarray, excluded, show_excluded: bool, fill_alpha: int,
                  edge_alpha: int, origin=(0, 0)) -> np.ndarray:
    """RGBA pixels of :func:`overlay_layer` (UPDATE 4 item 12: one gather
    through a per-grain lookup table instead of several whole-image passes).

    ``origin`` = (y, x) of ``lab[0, 0]`` in the full image so the hatch of
    the excluded grains keeps its phase when only a patch is redrawn."""
    h, w = lab.shape[:2]
    n = int(lab.max()) if lab.size else 0
    rgb = label_palette(n)
    fill = np.zeros((n + 1, 4), dtype=np.uint8)
    fill[:, :3] = rgb
    fill[1:, 3] = fill_alpha
    edge_lut = np.zeros((n + 1, 4), dtype=np.uint8)
    edge_lut[:, :3] = np.minimum(255, rgb.astype(np.int16) + 40)
    edge_lut[1:, 3] = edge_alpha
    ex_ids = np.asarray([int(i) for i in excluded if 0 < int(i) <= n], dtype=np.int64)
    if ex_ids.size:
        if show_excluded:
            fill[ex_ids] = (150, 150, 150, 120)
            edge_lut[ex_ids] = (200, 200, 200, 200)
        else:
            fill[ex_ids] = 0
            edge_lut[ex_ids] = 0
    # one pixel = one uint32 (the 4 bytes are only ever viewed, never read as
    # a number, so byte order does not matter); flat 1-D gathers are the
    # fastest numpy offers
    flat = np.ascontiguousarray(lab, dtype=np.intp).ravel()
    out = fill.view(np.uint32).ravel()[flat]
    edge = boundaries(lab).ravel()
    out[edge] = edge_lut.view(np.uint32).ravel()[flat[edge]]
    if ex_ids.size and show_excluded:
        y0, x0 = origin
        yy, xx = np.ogrid[y0:y0 + h, x0:x0 + w]
        stripe = (((xx - yy) // 5) % 2 == 0).ravel()
        ex_lut = np.zeros(n + 1, dtype=bool)
        ex_lut[ex_ids] = True
        off = ex_lut[flat] & ~edge & ~stripe
        keep_rgb = np.array([255, 255, 255, 0], np.uint8).view(np.uint32)[0]
        alpha_60 = np.array([0, 0, 0, 60], np.uint8).view(np.uint32)[0]
        out[off] = (out[off] & keep_rgb) | alpha_60
    return out.view(np.uint8).reshape(h, w, 4)


def overlay_layer(labels: Optional[np.ndarray], excluded=(), show_excluded: bool = True,
                  fill_alpha: int = 78, edge_alpha: int = 235) -> QImage:
    """Translucent per-grain tint + crisp boundaries, as an RGBA QImage.

    Grains in ``excluded`` (post-filtered / removed by hand) are drawn in a
    neutral grey hatch when ``show_excluded`` is on, and omitted otherwise."""
    if labels is None or labels.size == 0:
        return QImage()
    return _rgba_qimage(_overlay_rgba(labels, excluded, show_excluded, fill_alpha, edge_alpha))


def overlay_patch(labels: np.ndarray, rect, excluded=(), show_excluded: bool = True,
                  fill_alpha: int = 78, edge_alpha: int = 235):
    """The overlay of one rectangle ``(y0, y1, x0, x1)`` of ``labels`` only,
    identical to that region of :func:`overlay_layer` (the rectangle is
    computed with a 1-px margin so the boundaries at its edge are right).
    Returns ``(QImage, x0, y0)`` for painting at that spot."""
    H, W = labels.shape[:2]
    y0, y1, x0, x1 = (int(v) for v in rect)
    y0, x0 = max(0, y0), max(0, x0)
    y1, x1 = min(H, y1), min(W, x1)
    if y1 <= y0 or x1 <= x0:
        return QImage(), x0, y0
    py0, px0 = max(0, y0 - 1), max(0, x0 - 1)
    py1, px1 = min(H, y1 + 1), min(W, x1 + 1)
    rgba = _overlay_rgba(labels[py0:py1, px0:px1], excluded, show_excluded,
                         fill_alpha, edge_alpha, origin=(py0, px0))
    rgba = rgba[y0 - py0:y1 - py0, x0 - px0:x1 - px0]
    return _rgba_qimage(rgba), x0, y0


def changed_rect(old: Optional[np.ndarray], new: Optional[np.ndarray],
                 ids=()) -> Optional[tuple]:
    """Bounding box ``(y0, y1, x0, x1)`` of the pixels whose label differs
    between ``old`` and ``new`` plus every pixel of the grains in ``ids``
    (their drawing changed); ``None`` when nothing changed.  Falls back to
    the whole image (its full rect) when the two cannot be compared."""
    if new is None or new.size == 0:
        return None
    H, W = new.shape[:2]
    if old is None or old.shape != new.shape:
        return 0, H, 0, W
    dirty = None
    if old is not new:
        dirty = old != new
    if ids:
        m = _id_mask(new, ids)
        dirty = m if dirty is None else (dirty | m)
    if dirty is None:
        return None
    rows = np.flatnonzero(dirty.any(axis=1))
    if rows.size == 0:
        return None
    cols = np.flatnonzero(dirty.any(axis=0))
    return int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1


def kept_labels(labels: Optional[np.ndarray], excluded=()) -> Optional[np.ndarray]:
    if labels is None or not excluded:
        return labels
    return np.where(_id_mask(labels, excluded), 0, labels)


def grain_mask_image(result, labels: Optional[np.ndarray] = None,
                     excluded=()) -> Optional[np.ndarray]:
    """Mask view: white grains, black background, black 1-px separations.

    Uses the label image (kept grains only) so filters/deletions show; falls
    back to the detector's binary image through :func:`mask_to_display`
    (DET-03 fix: a 0/255 mask is never multiplied by 255 again)."""
    lab = labels if labels is not None else getattr(result, "label_image", None)
    lab = kept_labels(lab, excluded)
    if lab is not None and lab.size:
        m = (lab > 0).astype(np.uint8) * 255
        m[boundaries(lab)] = 0
        return m
    return mask_to_display(getattr(result, "binary_image", None))


def excluded_layer(valid_mask: Optional[np.ndarray], rgb=(242, 195, 102)) -> QImage:
    """Hatched amber tint over pixels that were NOT analysed (invalid)."""
    if valid_mask is None or valid_mask.size == 0:
        return QImage()
    inv = ~valid_mask.astype(bool)
    if not inv.any():
        return QImage()
    h, w = inv.shape
    yy, xx = np.ogrid[:h, :w]
    stripe = ((xx + yy) // 7) % 2 == 0
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = rgb
    a = np.where(stripe, 150, 85).astype(np.uint8)
    rgba[..., 3] = np.where(inv, a, 0)
    # outline of the excluded area
    er = cv2.erode(inv.astype(np.uint8), np.ones((3, 3), np.uint8))
    edge = inv & (er == 0)
    rgba[edge, 3] = 255
    return _rgba_qimage(rgba)


def desaturate(bgr: np.ndarray, amount: float = 0.55) -> np.ndarray:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    g3 = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    return cv2.addWeighted(bgr, 1 - amount, g3, amount, 0)


def grain_outline(labels: np.ndarray, grain) -> list:
    """Contours (list of Nx2 int arrays, image coords) of one grain."""
    H, W = labels.shape[:2]
    try:
        r0, c0, r1, c1 = [int(v) for v in grain.bbox]
    except (TypeError, ValueError):
        r0, c0, r1, c1 = 0, 0, H, W
    r0, c0 = max(0, r0 - 1), max(0, c0 - 1)
    r1, c1 = min(H, max(r1, r0 + 1) + 1), min(W, max(c1, c0 + 1) + 1)
    crop = (labels[r0:r1, c0:c1] == grain.grain_id).astype(np.uint8)
    if not crop.any():
        crop = (labels == grain.grain_id).astype(np.uint8)
        r0 = c0 = 0
    cs, _ = cv2.findContours(crop, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE,
                             offset=(c0, r0))
    return [c.reshape(-1, 2) for c in cs]
