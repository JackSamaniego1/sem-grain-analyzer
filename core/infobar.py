"""
SEM data/info bar detection (DET-05)
====================================

Most SEM micrographs are saved with a *data bar* burned into the image: a
full-width band (usually at the bottom, sometimes at the top) on a uniform
black or white background carrying sparse, high-contrast annotation —
"SE2  15.00 kV  WD 8.6 mm  Mag 500 x", a scale-bar line, a logo.  The bar is
not specimen information and must never be measured.

Detection works on row profiles only (O(h*w), a few ms on 2048x1536):

1. **Background value** ``v`` — the dominant gray level (histogram mode) of
   the outermost rows (bottom 2 % for a bottom bar; the image is flipped for
   a top bar).
2. **Row uniformity profile** ``f[r]`` — fraction of pixels in row ``r``
   within ``±tol`` of ``v``.  Inside a bar ``f`` is high (text is sparse);
   in a micrograph it is low because real specimen texture, grain contrast
   and detector noise spread the gray levels.
3. **Boundary** — the row maximising the step
   ``mean(f[r:r+k]) - mean(f[r-k:r])`` inside the allowed height range
   (2 %–35 % of the image), refined to the exact crossing row.  A 1–4 px
   uniform separator line directly above the bar is absorbed into the bar.
4. **Acceptance** — ALL of the following must hold, otherwise ``None``:

   * *uniform background*: mean ``f`` over the band >= 0.60;
   * *full width*: each of 8 column blocks of the band has ``f`` >= 0.25
     (rejects a black blob or pore touching the edge);
   * *discontinuity*: band ``f`` minus ``f`` of the micrograph rows just
     above >= 0.40, and the transition happens within a few rows (rejects
     a micrograph that just gets darker towards the bottom);
   * *flat, rendered background*: >= 80 % of the band's background
     (±tol) pixels lie within ±4 of ``v``, and in every band row the mean
     level of those background pixels stays within 5 of ``v`` (a bar is
     burned-in graphics; a darkened or vignetted micrograph keeps its
     texture and drifts in level).  Measured on background pixels only, so
     it does not depend on how much text the bar carries;
   * *annotation present*: 0.1 %–45 % of band pixels are high-contrast
     "ink" (``|g - v| >= 80``) — text or a scale-bar line.  A featureless
     black band is left to the valid-pixel mask (DET-01) instead.

Coordinates: all rectangles are ``(x, y, w, h)`` in the coordinates of the
array passed in.  No Qt, no I/O.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

Rect = Tuple[int, int, int, int]  # (x, y, w, h)

# Tunables (documented in the module docstring).
_TOL = 14                 # gray levels around the background value
_INK_DELTA = 80           # |g - v| for a pixel to count as annotation ink
_MIN_H_FRAC = 0.02
_MAX_H_FRAC = 0.35
_MIN_H_PX = 8
_BAND_F_MIN = 0.60
_BLOCK_F_MIN = 0.25
_N_BLOCKS = 8
_STEP_MIN = 0.40
_ABOVE_F_MAX = 0.40
_INK_MIN = 0.001
_INK_MAX = 0.45
_MAX_TRANSITION = 4       # rows allowed between "micrograph" and "bar"
_MAX_SEPARATOR = 4        # uniform separator-line rows absorbed into bar
_TIGHT = 4                # rendered bar background is flat: of the
_TIGHT_MIN = 0.80         # background (±_TOL) pixels >= 80 % are within ±4
_LEVEL_DRIFT = 5.0        # per-row mean background level stays within 5 of v


@dataclass
class InfoBar:
    """One detected data bar."""
    position: str                   # "bottom" | "top"
    rect: Rect                      # the bar, (x, y, w, h)
    background: str                 # "black" | "white" | "gray"
    background_value: int
    confidence: float               # 0.5 .. 1.0 (only accepted bars exist)
    band_uniformity: float
    above_uniformity: float
    ink_fraction: float
    block_min_uniformity: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class InfoBarResult:
    """Result of :func:`detect_info_bar`."""
    analysis_rect: Rect             # micrograph area (bars excluded)
    bars: list = field(default_factory=list)   # list[InfoBar]
    confidence: float = 0.0         # min confidence over detected bars

    @property
    def bar_rect(self) -> Optional[Rect]:
        """The (first / bottom-preferred) bar rectangle."""
        if not self.bars:
            return None
        for b in self.bars:
            if b.position == "bottom":
                return b.rect
        return self.bars[0].rect

    def to_dict(self) -> dict:
        return {
            "analysis_rect": tuple(int(v) for v in self.analysis_rect),
            "bar_rect": self.bar_rect,
            "confidence": float(self.confidence),
            "bars": [b.to_dict() for b in self.bars],
        }


def _to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 3:
        import cv2
        if img.shape[2] == 4:
            return cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if img.dtype != np.uint8:
        g = img.astype(np.float64)
        lo, hi = float(g.min()), float(g.max())
        if hi <= lo:
            return np.zeros(img.shape, np.uint8)
        return ((g - lo) * (255.0 / (hi - lo))).astype(np.uint8)
    return img


def _background_value(rows: np.ndarray) -> int:
    hist = np.bincount(rows.ravel(), minlength=256).astype(np.float64)
    # light smoothing so JPEG / detector noise does not split the mode
    k = np.ones(5) / 5.0
    hist = np.convolve(hist, k, mode="same")
    return int(np.argmax(hist))


def _detect_bottom(gray: np.ndarray) -> Optional[InfoBar]:
    """Detect a bar at the bottom of ``gray`` (uint8, 2-D)."""
    h, w = gray.shape
    min_h = max(_MIN_H_PX, int(round(h * _MIN_H_FRAC)))
    max_h = int(h * _MAX_H_FRAC)
    if max_h <= min_h + 4 or w < 32:
        return None

    edge_rows = gray[h - max(3, int(h * 0.02)):, :]
    v = _background_value(edge_rows)

    # Row uniformity profile over the search region (plus context above).
    top = max(0, h - max_h - 3 * min_h)
    region = gray[top:, :].astype(np.int16)
    near = np.abs(region - v) <= _TOL
    f = near.mean(axis=1)                       # len = h - top

    # Step detector: k rows below vs k rows above candidate boundary.
    k = max(3, min_h // 2)
    cs = np.concatenate([[0.0], np.cumsum(f)])
    best_r, best_step = None, -1.0
    for r_abs in range(h - max_h, h - min_h + 1):
        r = r_abs - top
        if r - k < 0 or r + k > len(f):
            continue
        below = (cs[r + k] - cs[r]) / k
        above = (cs[r] - cs[r - k]) / k
        step = below - above
        if step > best_step:
            best_step, best_r = step, r
    if best_r is None or best_step < _STEP_MIN * 0.75:
        return None

    # Refine: the boundary is the first row (going down) from which f stays
    # "bar-like"; search a +-k window around the coarse maximum.
    lo = max(0, best_r - k)
    hi = min(len(f) - 1, best_r + k)
    mid = 0.5 * (_BAND_F_MIN + _ABOVE_F_MAX) * 0.9
    r_ref = best_r
    for r in range(lo, hi + 1):
        if f[r] >= mid and (r == 0 or f[r - 1] < mid):
            r_ref = r
            if r >= best_r - 1:
                break
    # Transition sharpness: rows between clearly-micrograph and clearly-bar.
    trans = 0
    r = r_ref - 1
    while r >= 0 and _ABOVE_F_MAX <= f[r] < _BAND_F_MIN and trans <= _MAX_TRANSITION:
        trans += 1
        r -= 1

    # Absorb a thin uniform separator line (bar frame) above the boundary.
    row_std = region.std(axis=1)
    sep = 0
    while (sep < _MAX_SEPARATOR and r_ref - 1 - sep >= 0
           and row_std[r_ref - 1 - sep] < 8.0
           and abs(float(region[r_ref - 1 - sep].mean()) - v) > _TOL):
        sep += 1
    y0 = top + r_ref - sep
    bar_h = h - y0
    if bar_h < min_h or bar_h > max_h:
        return None

    band = gray[y0:, :].astype(np.int16)
    band_near = np.abs(band - v) <= _TOL
    band_f = float(band_near[sep:, :].mean())      # separator rows excluded
    ink = float((np.abs(band - v) >= _INK_DELTA).mean())
    blocks = np.array_split(np.arange(w), _N_BLOCKS)
    block_f = float(min(band_near[:, b].mean() for b in blocks))
    a1 = max(0, r_ref - sep - max(k, int(h * 0.04)))
    a0 = r_ref - sep - 1
    above_f = float(f[a1:max(a1 + 1, a0)].mean()) if a0 > a1 else 1.0
    step = band_f - above_f

    core_band = band[sep:, :]
    dev = np.abs(core_band - v)
    bg_px = dev <= _TOL
    n_bg = max(int(bg_px.sum()), 1)
    tight = float((dev <= _TIGHT).sum()) / n_bg
    row_n = bg_px.sum(axis=1)
    row_lvl = np.where(bg_px, core_band, 0).sum(axis=1) / np.maximum(row_n, 1)
    use = row_n >= 0.1 * w
    drift = float(np.abs(row_lvl[use] - v).max()) if use.any() else 255.0

    ok = (band_f >= _BAND_F_MIN and tight >= _TIGHT_MIN
          and drift <= _LEVEL_DRIFT and block_f >= _BLOCK_F_MIN
          and step >= _STEP_MIN and above_f <= _ABOVE_F_MAX
          and trans <= _MAX_TRANSITION
          and _INK_MIN <= ink <= _INK_MAX)
    if not ok:
        logger.debug(
            "info bar rejected: band_f=%.2f block_f=%.2f step=%.2f above=%.2f "
            "trans=%d ink=%.4f tight=%.2f drift=%.1f", band_f, block_f, step,
            above_f, trans, ink, tight, drift)
        return None

    margins = [
        (band_f - _BAND_F_MIN) / (1.0 - _BAND_F_MIN),
        (block_f - _BLOCK_F_MIN) / (1.0 - _BLOCK_F_MIN),
        (step - _STEP_MIN) / (1.0 - _STEP_MIN),
        1.0 - trans / (_MAX_TRANSITION + 1.0),
        min(1.0, ink / 0.01),
    ]
    conf = 0.5 + 0.5 * float(np.clip(min(margins), 0.0, 1.0))
    bg = "black" if v < 60 else ("white" if v > 195 else "gray")
    return InfoBar("bottom", (0, int(y0), int(w), int(bar_h)), bg, int(v),
                   round(conf, 3), round(band_f, 3), round(above_f, 3),
                   round(ink, 4), round(block_f, 3))


def detect_info_bar(image: np.ndarray) -> Optional[InfoBarResult]:
    """Find a bottom and/or top SEM data bar.

    Parameters
    ----------
    image : 2-D gray (any dtype) or BGR/BGRA uint8 array.

    Returns
    -------
    ``None`` when no bar is found, else an :class:`InfoBarResult` with
    ``analysis_rect`` (micrograph area excluding the bar(s), ``(x, y, w,
    h)``), ``bars`` (``InfoBar`` list with rect/confidence/background) and
    overall ``confidence``.  ``result.to_dict()`` is JSON-able.
    """
    if image is None or image.size == 0:
        return None
    gray = _to_gray(np.asarray(image))
    if gray.ndim != 2:
        return None
    h, w = gray.shape
    if h < 64 or w < 64:
        return None

    bars = []
    bottom = _detect_bottom(gray)
    if bottom is not None:
        bars.append(bottom)
    top_flipped = _detect_bottom(gray[::-1, :])
    if top_flipped is not None:
        x, y, bw, bh = top_flipped.rect
        top_flipped.position = "top"
        top_flipped.rect = (x, 0, bw, bh)
        bars.append(top_flipped)
    if not bars:
        return None

    y0, y1 = 0, h
    for b in bars:
        if b.position == "bottom":
            y1 = min(y1, b.rect[1])
        else:
            y0 = max(y0, b.rect[1] + b.rect[3])
    if y1 - y0 < h * 0.3:          # would leave almost no micrograph
        return None
    return InfoBarResult(analysis_rect=(0, int(y0), int(w), int(y1 - y0)),
                         bars=bars,
                         confidence=float(min(b.confidence for b in bars)))
