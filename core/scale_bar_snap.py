"""
Scale-bar end detection for manual calibration (UI-11)
======================================================
Pure numpy/cv2 helpers (no Qt) used by the calibration dialog to snap a
user-drawn rectangle or level line to the real ends of the scale-bar line.

Coordinates are continuous image coordinates in which pixel ``i`` covers
``[i, i + 1)``.  A bar whose ink occupies columns ``x .. x + w - 1`` therefore
has its left end at ``x`` and its right end at ``x + w`` and is ``w`` px long,
which matches ``core.scale_bar.find_scale_bar_line``'s ``length_px``.

The bar may be light-on-dark or dark-on-light: both polarities are searched.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

__all__ = ["find_bar_ends_in_roi", "bar_edges_near", "snap_x"]

# Segment = (x0, x1, y0, y1) with x1 / y1 exclusive, in full-frame coords.
Segment = Tuple[int, int, int, int]


def _gray(image) -> np.ndarray:
    arr = np.asarray(image)
    if arr.ndim == 3:
        return cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
    return arr


def _clip_roi(shape, x0: float, y0: float, x1: float, y1: float
              ) -> Optional[Tuple[int, int, int, int]]:
    h, w = shape[:2]
    ix0 = max(0, int(np.floor(min(x0, x1))))
    ix1 = min(w, int(np.ceil(max(x0, x1))))
    iy0 = max(0, int(np.floor(min(y0, y1))))
    iy1 = min(h, int(np.ceil(max(y0, y1))))
    if ix1 - ix0 < 3 or iy1 - iy0 < 1:
        return None
    return ix0, iy0, ix1, iy1


def _segments(gray: np.ndarray, roi: Tuple[int, int, int, int],
              min_len: int = 6) -> List[Segment]:
    """Solid horizontal ink strokes (both polarities) inside ``roi``.

    Otsu splits the ROI into light and dark; each mask is opened with a
    horizontal kernel so text glyphs and 1-px end ticks drop out and only
    horizontal strokes remain.  Components are returned in full-frame
    coordinates."""
    x0, y0, x1, y1 = roi
    crop = gray[y0:y1, x0:x1]
    if crop.size == 0 or int(crop.max()) - int(crop.min()) < 30:
        return []
    thr, _ = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    light = (crop > thr).astype(np.uint8) * 255
    dark = (crop <= thr).astype(np.uint8) * 255
    # odd width: an even kernel shifts the opened stroke by one pixel
    k = int(max(3, min(21, min_len))) | 1
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, 1))
    out: List[Segment] = []
    for mask in (light, dark):
        horiz = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        n, _, stats, _ = cv2.connectedComponentsWithStats(horiz, connectivity=4)
        for i in range(1, n):
            sx, sy, sw, sh, area = (int(v) for v in stats[i])
            if sw < k or area < 0.5 * sw * sh:
                continue
            out.append((x0 + sx, x0 + sx + sw, y0 + sy, y0 + sy + sh))
    return out


def find_bar_ends_in_roi(image, roi: Sequence[float], pad: int = 8
                         ) -> Optional[dict]:
    """Find the scale-bar line inside a (possibly loose) box.

    Parameters
    ----------
    image : BGR or gray full frame.
    roi : ``(x, y, w, h)`` box drawn by the user (image coords).
    pad : columns added on each side so a box drawn slightly too narrow still
        finds the true ends.

    Returns ``None`` or ``{"x0", "x1", "y0", "y1", "length_px", "cy"}``
    where ``x0``/``x1`` are the bar's left/right ends (``x1`` exclusive) and
    ``cy`` its vertical centre.  Strokes touching the left or right edge of
    the padded search area are rejected: they are background, or a bar that
    continues outside the box and whose ends therefore are not visible.
    """
    gray = _gray(image)
    x, y, w, h = (float(v) for v in roi)
    if w <= 0 or h <= 0:
        return None
    # A very thin box (e.g. a line) still needs a few rows to see the bar.
    cy = y + h / 2.0
    half_h = max(h / 2.0, 3.0)
    r = _clip_roi(gray.shape, x - pad, cy - half_h, x + w + pad, cy + half_h)
    if r is None:
        return None
    rx0, _, rx1, _ = r
    min_len = int(max(6, min(20, w // 5)))
    best = None
    for sx0, sx1, sy0, sy1 in _segments(gray, r, min_len):
        if sx0 <= rx0 and rx0 > 0:
            continue
        if sx1 >= rx1 and rx1 < gray.shape[1]:
            continue
        if sx0 <= rx0 or sx1 >= rx1:      # touches the frame edge too
            continue
        if best is None or (sx1 - sx0) > (best[1] - best[0]):
            best = (sx0, sx1, sy0, sy1)
    if best is None:
        return None
    sx0, sx1, sy0, sy1 = best
    return {"x0": float(sx0), "x1": float(sx1), "y0": float(sy0),
            "y1": float(sy1), "length_px": int(sx1 - sx0),
            "cy": (sy0 + sy1) / 2.0}


def bar_edges_near(image, x: float, y: float, tol: float = 6.0,
                   band: int = 6, reach: int = 40) -> List[Tuple[float, str, float]]:
    """Candidate bar ends near ``(x, y)``.

    Searches horizontal strokes crossing rows ``y ± band`` within
    ``x ± (tol + reach)`` and returns ``[(edge_x, side, stroke_cy), ...]``
    for ends within ``tol`` px of ``x`` (``side`` is ``"left"`` or
    ``"right"``), best first (strokes on row ``y`` before strokes near it).  Ends lying on the search-window border are
    not real ends and are skipped."""
    gray = _gray(image)
    r = _clip_roi(gray.shape, x - tol - reach, y - band,
                  x + tol + reach + 1, y + band + 1)
    if r is None:
        return []
    rx0, _, rx1, _ = r
    H, W = gray.shape[:2]
    found = []
    for sx0, sx1, sy0, sy1 in _segments(gray, r, 6):
        # the stroke must reach the row the user is working on (± 2 px)
        if not (sy0 - 2 <= y < sy1 + 2):
            continue
        scy = (sy0 + sy1) / 2.0
        on_row = 0 if sy0 <= y < sy1 else 1
        if (sx0 > rx0 or rx0 == 0) and abs(sx0 - x) <= tol:
            found.append((on_row, abs(scy - y), abs(sx0 - x), float(sx0), "left", scy))
        if (sx1 < rx1 or rx1 == W) and abs(sx1 - x) <= tol:
            found.append((on_row, abs(scy - y), abs(sx1 - x), float(sx1), "right", scy))
    # strokes on the user's row first, then the one centred nearest to it,
    # then the nearest end
    found.sort(key=lambda e: e[:3])
    return [e[3:] for e in found]


def snap_x(image, x: float, y: float, tol: float = 6.0,
           side: Optional[str] = None) -> Optional[Tuple[float, float]]:
    """Nearest bar end to ``(x, y)`` within ``tol`` px, optionally only a
    ``"left"`` or ``"right"`` end.  Returns ``(edge_x, stroke_cy)`` or
    ``None`` when nothing is close enough."""
    for ex, s, scy in bar_edges_near(image, x, y, tol):
        if side is None or s == side:
            return ex, scy
    return None
