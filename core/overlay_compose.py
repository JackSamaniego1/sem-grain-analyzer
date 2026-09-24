"""Full-frame overlay composition for exported grain-overlay images.

Detection may run on a sub-image of the micrograph (the user's scan area
and/or the automatic crop that drops the SEM data bar and white borders,
DET-05).  Every overlay the app exports (Excel, PPTX, saved PNGs) must
nevertheless be the FULL original frame at original resolution so the
instrument's data bar (magnification, kV, WD, scale bar) stays visible and
any scale bar / px-per-um calibration still applies 1:1 to the exported
pixels.  This module places a crop overlay back at its offset and marks the
analysed-region boundary with a thin dashed line.

Pure numpy/OpenCV, no Qt.  Measurements are never touched: these functions
only produce display images.

Rectangles here are ``(x, y, w, h)`` in full-frame pixels (the scan-area
convention); :func:`rc_to_xywh` converts the detector's ``(r0, c0, r1, c1)``.
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

Rect = Tuple[int, int, int, int]

# Outline style: 1 px dashed line, light dashes alternating with dark gaps
# (visible on bright and dark SEM backgrounds alike), blended so it reads as
# a subtle boundary rather than an annotation competing with the grains.
_DASH = 6
_LIGHT = np.array([235, 235, 235], dtype=np.float32)
_DARK = np.array([30, 30, 30], dtype=np.float32)
_ALPHA = 0.7


def rc_to_xywh(rc) -> Optional[Rect]:
    """``(r0, c0, r1, c1)`` -> ``(x, y, w, h)`` (None passes through)."""
    if rc is None:
        return None
    r0, c0, r1, c1 = (int(v) for v in rc)
    return c0, r0, c1 - c0, r1 - r0


def analysed_rect_from_mask(valid_mask, shape=None) -> Optional[Rect]:
    """Bounding box ``(x, y, w, h)`` of a full-frame valid (analysed) mask,
    or None when the whole frame was analysed / no usable mask."""
    if valid_mask is None:
        return None
    vm = np.asarray(valid_mask)
    if shape is not None and vm.shape[:2] != tuple(shape[:2]):
        return None
    if vm.size == 0 or vm.all():
        return None
    rows = np.flatnonzero(vm.any(axis=1))
    cols = np.flatnonzero(vm.any(axis=0))
    if not len(rows) or not len(cols):
        return None
    H, W = vm.shape[:2]
    y0, y1, x0, x1 = rows[0], rows[-1] + 1, cols[0], cols[-1] + 1
    if y0 == 0 and x0 == 0 and y1 == H and x1 == W:
        return None
    return int(x0), int(y0), int(x1 - x0), int(y1 - y0)


def _as_bgr(img: np.ndarray) -> np.ndarray:
    a = np.asarray(img)
    if a.ndim == 2:
        a = np.repeat(a[..., None], 3, axis=2)
    elif a.shape[2] == 4:
        a = a[..., :3]
    return a.astype(np.uint8, copy=False)


def _clip_rect(rect, H, W) -> Optional[Rect]:
    if rect is None:
        return None
    x, y, w, h = (int(v) for v in rect)
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1 - x0, y1 - y0


def draw_analysed_outline(img: np.ndarray, rect) -> np.ndarray:
    """Draw (in place) a thin dashed boundary of the analysed region.

    The line is drawn on the OUTERMOST pixels of the analysed region itself,
    so pixels outside it (data bar, borders) stay bit-identical to the
    original image.  Sides lying on the image border are skipped (nothing to
    separate there); a full-frame rect draws nothing.  Returns ``img``.
    """
    H, W = img.shape[:2]
    r = _clip_rect(rect, H, W)
    if r is None:
        return img
    x, y, w, h = r
    x1, y1 = x + w - 1, y + h - 1
    segs = []   # (rows, cols) index arrays of each drawn side
    xs = np.arange(x, x1 + 1)
    ys = np.arange(y, y1 + 1)
    if y > 0:
        segs.append((np.full_like(xs, y), xs))
    if y1 < H - 1:
        segs.append((np.full_like(xs, y1), xs))
    if x > 0:
        segs.append((ys, np.full_like(ys, x)))
    if x1 < W - 1:
        segs.append((ys, np.full_like(ys, x1)))
    for rr, cc in segs:
        pos = (rr - y) + (cc - x)          # arc position -> dash phase
        light = ((pos // _DASH) % 2 == 0)[:, None]
        col = np.where(light, _LIGHT, _DARK)
        px = img[rr, cc].astype(np.float32)
        img[rr, cc] = np.clip(px * (1 - _ALPHA) + col * _ALPHA + 0.5,
                              0, 255).astype(np.uint8)
    return img


def compose_full_overlay(original_bgr: np.ndarray,
                         overlay_crop_bgr: Optional[np.ndarray],
                         crop_rect, outline: bool = True) -> np.ndarray:
    """Full-resolution overlay: ``original_bgr`` with ``overlay_crop_bgr``
    pasted at ``crop_rect`` (``(x, y, w, h)``) and, optionally, the
    analysed-region outline.

    * ``crop_rect`` None -> the overlay already covers the whole frame.
    * An overlay that is already full-frame (same shape as the original) is
      used as is (only the outline is added), so the call is idempotent with
      respect to placement.
    * The unanalysed region is copied unaltered from ``original_bgr``.
    Never mutates its inputs.
    """
    orig = _as_bgr(original_bgr)
    H, W = orig.shape[:2]
    if overlay_crop_bgr is None:
        out = orig.copy()
    else:
        ov = _as_bgr(overlay_crop_bgr)
        if ov.shape[:2] == (H, W):
            out = ov.copy()
        else:
            if crop_rect is None:
                raise ValueError(
                    f"overlay {ov.shape[:2]} differs from image {(H, W)} "
                    "but no crop_rect was given")
            x, y, w, h = (int(v) for v in crop_rect)
            if (h, w) != ov.shape[:2] or x < 0 or y < 0 \
                    or x + w > W or y + h > H:
                raise ValueError(
                    f"crop_rect {crop_rect} does not fit overlay "
                    f"{ov.shape[:2]} into image {(H, W)}")
            out = orig.copy()
            out[y:y + h, x:x + w] = ov
    if outline and crop_rect is not None:
        draw_analysed_outline(out, crop_rect)
    return out


__all__ = ["rc_to_xywh", "analysed_rect_from_mask", "draw_analysed_outline",
           "compose_full_overlay"]
