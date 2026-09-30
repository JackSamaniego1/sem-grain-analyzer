"""
Vendor logo recognition in the SEM data bar (offline, shape analysis only)
==========================================================================

Thermo Fisher / FEI data bars start with the company's *atom* mark at the
left end: three identical thin elliptical orbits (axis ratio ~0.3) sharing
one centre and rotated 60 degrees apart, plus small "electron" dots.  No
trademark image is stored anywhere: the reference shape is *described* in
code (:func:`_atom_template`) and a candidate blob is compared with it.

A blob is accepted only when all hold:

1. it sits in the left end of a data bar and is not inside an OCR word box;
2. its bounding box is nearly square (w/h 0.8-1.5) and at least 45 % of
   the bar height;
3. it encloses >= 4 holes (three crossed orbits enclose a centre cell and
   six petals = 7; letters and digits enclose at most 2, "8"/"B");
4. **strokes**: blob ink and template ink coincide within 1 px in both
   directions (F-score of the two coverage fractions >= 0.85);
5. **cells**: the regions the blob encloses overlap the template's
   enclosed cells (centre + six petals) with IoU >= 0.45.  The stroke test
   alone is fooled by thin radial symbols (an asterisk is three degenerate
   ellipses, a wheel has spokes on the orbit axes); the cell test is not.

Measured on the user's real Thermo export (33x30 px mark): strokes 0.92,
cells 0.57.  Worst non-logo symbol glyphs with >= 4 holes (circled plus,
wheel, florettes, U+269B atom symbol at 34 px): strokes <= 0.84 or cells
<= 0.47.

No Qt, no I/O, no network.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

import cv2
import numpy as np

Rect = Tuple[int, int, int, int]

_MIN_HOLES = 4
_MIN_STROKE = 0.85
_MIN_CELLS = 0.45
_MATCH_TOL_PX = 1.0

VENDOR_THERMO = "Thermo Fisher"


@dataclass
class LogoMatch:
    vendor: str
    bbox: Rect                   # full-frame (x, y, w, h)
    stroke: float                # 0..1 stroke F-score vs the template
    cells: float                 # 0..1 enclosed-cell IoU vs the template


def _atom_template(w: int, h: int, ratio: float, base_deg: float,
                   thick: int) -> np.ndarray:
    """Three ellipses, semi-major = w/2, semi-minor = ratio * semi-major,
    rotated base, base+60, base+120 degrees about the box centre."""
    pad = 3
    canvas = np.zeros((h + 2 * pad, w + 2 * pad), np.uint8)
    cx, cy = (w + 2 * pad - 1) / 2.0, (h + 2 * pad - 1) / 2.0
    a = max(2.0, (w - thick) / 2.0)
    b = max(1.0, a * ratio)
    for k in range(3):
        cv2.ellipse(canvas, (int(round(cx * 16)), int(round(cy * 16))),
                    (int(round(a * 16)), int(round(b * 16))),
                    base_deg + 60 * k, 0, 360, 255, thick, cv2.LINE_8, 4)
    return canvas[pad:pad + h, pad:pad + w]


def _stroke_score(blob: np.ndarray, tmpl: np.ndarray) -> float:
    """F-score of (blob ink near template ink, template ink near blob ink)."""
    if not blob.any() or not tmpl.any():
        return 0.0
    d_t = cv2.distanceTransform((tmpl == 0).astype(np.uint8), cv2.DIST_L2, 3)
    d_b = cv2.distanceTransform((blob == 0).astype(np.uint8), cv2.DIST_L2, 3)
    p = float((d_t[blob > 0] <= _MATCH_TOL_PX).mean())
    r = float((d_b[tmpl > 0] <= _MATCH_TOL_PX).mean())
    return 0.0 if p + r == 0 else 2 * p * r / (p + r)


def _hole_mask(mask: np.ndarray) -> np.ndarray:
    """Pixels enclosed by ink (filled outline minus the ink itself)."""
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_NONE)
    filled = np.zeros_like(mask)
    cv2.drawContours(filled, contours, -1, 255, -1)
    return (filled > 0) & (mask == 0)


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    u = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum()) / u if u else 0.0


def _holes(mask: np.ndarray) -> int:
    contours, hier = cv2.findContours(mask, cv2.RETR_CCOMP,
                                      cv2.CHAIN_APPROX_SIMPLE)
    if hier is None:
        return 0
    return sum(1 for (c, h) in zip(contours, hier[0])
               if h[3] >= 0 and cv2.contourArea(c) >= 2)


def _overlaps(a: Rect, b: Rect) -> bool:
    return (a[0] < b[0] + b[2] and b[0] < a[0] + a[2]
            and a[1] < b[1] + b[3] and b[1] < a[1] + a[3])


def atom_logo_scores(mask: np.ndarray) -> Tuple[float, float]:
    """(stroke, cells) of the best-fitting template for a binary blob given
    as its tight bounding box (uint8, ink = 255).  The template search
    covers axis ratios 0.25-0.40, both 0/90 degree base orientations and
    stroke widths 1-3 px; the fit maximising stroke x cells is returned."""
    h, w = mask.shape[:2]
    holes = _hole_mask(mask)
    best, best_key = (0.0, 0.0), -1.0
    for base in (0.0, 90.0):
        for ratio in (0.25, 0.3, 0.35, 0.4):
            for thick in (1, 2, 3):
                t = _atom_template(w, h, ratio, base, thick)
                s = _stroke_score(mask, t)
                c = _iou(holes, _hole_mask(t))
                if s * c > best_key:
                    best, best_key = (s, c), s * c
    return best


def is_atom_logo(mask: np.ndarray) -> Tuple[bool, float, float]:
    """Gates 3-5 of the module docstring on a tight binary blob."""
    if _holes(mask) < _MIN_HOLES:
        return False, 0.0, 0.0
    s, c = atom_logo_scores(mask)
    return (s >= _MIN_STROKE and c >= _MIN_CELLS), s, c


def detect_vendor_logo(gray: np.ndarray, bar_rect: Rect, background: float,
                       text_boxes: Sequence[Rect] = ()) -> Optional[LogoMatch]:
    """Look for the Thermo Fisher atom mark at the left end of a data bar.

    ``gray`` is the full frame (uint8), ``bar_rect`` the data-bar rectangle,
    ``background`` its background grey level, ``text_boxes`` full-frame OCR
    boxes (a blob inside a word is never a logo).  Only the first 4 bar
    heights of the bar are searched: the mark is printed at the left end."""
    x0, y0, bw, bh = (int(v) for v in bar_rect)
    if bh < 12 or bw < bh:
        return None
    roi = gray[y0:y0 + bh, x0:x0 + min(bw, 4 * bh)].astype(np.int16)
    diff = np.abs(roi - float(background))
    peak = float(diff.max()) if diff.size else 0.0
    if peak < 60:
        return None
    ink = (diff >= max(30.0, 0.35 * peak)).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    best = None
    for i in range(1, n):
        x, y, w, h = (int(v) for v in stats[i][:4])
        if h < max(12, 0.45 * bh) or not 0.8 <= w / float(h) <= 1.5:
            continue
        box = (x0 + x, y0 + y, w, h)
        if any(_overlaps(box, t) for t in text_boxes):
            continue
        blob = (labels[y:y + h, x:x + w] == i).astype(np.uint8) * 255
        ok, s, c = is_atom_logo(blob)
        if ok and (best is None or s * c > best.stroke * best.cells):
            best = LogoMatch(VENDOR_THERMO, box, round(s, 3), round(c, 3))
    return best
