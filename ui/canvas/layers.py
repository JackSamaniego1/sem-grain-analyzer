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


def overlay_layer(labels: Optional[np.ndarray], excluded=(), show_excluded: bool = True,
                  fill_alpha: int = 78, edge_alpha: int = 235) -> QImage:
    """Translucent per-grain tint + crisp boundaries, as an RGBA QImage.

    Grains in ``excluded`` (post-filtered / removed by hand) are drawn in a
    neutral grey hatch when ``show_excluded`` is on, and omitted otherwise."""
    if labels is None or labels.size == 0:
        return QImage()
    lab = labels.astype(np.int64, copy=False)
    lut = label_palette(int(lab.max()) if lab.size else 0)
    rgba = np.zeros(lab.shape + (4,), dtype=np.uint8)
    rgba[..., :3] = lut[lab]
    inside = lab > 0
    rgba[..., 3] = np.where(inside, fill_alpha, 0).astype(np.uint8)
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
    return _rgba_qimage(rgba)


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
