"""
Scale Bar Detection
===================
Finds the scale-bar LINE inside the SEM data/info bar and reports its length
in pixels.  The search region comes from :func:`core.infobar.detect_info_bar`
(DET-05 / INN-05) instead of a hard-coded bottom strip; polarity (white line
on a black bar or black line on a white bar) follows the bar background.

The numeric label is NOT read automatically: no OCR engine is bundled (D-14,
offline installer).  The UI prefills the bar length in pixels and asks the
user only for the micrometre value; :func:`compute_px_per_um` converts.
``_read_label_ocr`` remains an optional, never-bundled pytesseract hook.
"""

import numpy as np
import cv2
import re
import logging
from typing import Optional, Tuple

from core.infobar import detect_info_bar, InfoBarResult

logger = logging.getLogger(__name__)

_UNIT_TO_UM = {
    'nm': 0.001, 'um': 1.0, 'µm': 1.0, 'μm': 1.0,
    'micron': 1.0, 'microns': 1.0, 'mm': 1000.0,
}

_INK_DELTA = 80


def _gray(image):
    """8-bit single-channel view of a gray/BGR/BGRA image of any dtype.
    uint16 keeps its high byte (no min/max stretch: the bar's black/white
    levels must stay where the instrument put them); other dtypes are
    min/max scaled."""
    image = np.asarray(image)
    if image.dtype == np.uint16:
        image = (image >> 8).astype(np.uint8)
    elif image.dtype != np.uint8:
        a = image.astype(np.float64)
        lo, hi = float(a.min()), float(a.max())
        image = ((a - lo) * (255.0 / (hi - lo if hi > lo else 1.0))).astype(np.uint8)
    if image.ndim == 3:
        if image.shape[2] == 4:
            return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
        if image.shape[2] == 1:
            return image[:, :, 0]
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return image


def _horizontal_segments(ink: np.ndarray, max_h: int, max_w: int):
    """Solid horizontal strokes (x, y, w, h) in a binary ink mask.
    Opening with a 21x1 kernel keeps horizontal strokes >= 21 px and removes
    text glyphs and the 1-px vertical end ticks of the bar."""
    # 21 (odd): an even-width kernel shifts the opened stroke 1 px right
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (21, 1))
    horiz = cv2.morphologyEx(ink, cv2.MORPH_OPEN, kernel)
    contours, _ = cv2.findContours(horiz, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    segs = []
    for cnt in contours:
        x, y, cw, ch = cv2.boundingRect(cnt)
        if cw <= 20 or ch > max_h or cw > max_w:
            continue
        roi = horiz[y:y + ch, x:x + cw]
        if np.count_nonzero(roi) / max(roi.size, 1) > 0.5:
            segs.append((x, y, cw, ch))
    return segs


# Frame test tolerances: the two horizontal edges of a drawn box share their
# x-extent within this many px, and the box sides are inked over at least
# this fraction of the gap between the edges.
_FRAME_END_TOL = 4
_FRAME_SIDE_FILL = 0.8


def _side_filled(ink: np.ndarray, x: int, y_a: int, y_b: int) -> bool:
    """True when a column near ``x`` is inked over most of rows y_a..y_b."""
    h, w = ink.shape[:2]
    if y_b - y_a < 2:
        return False
    x_lo, x_hi = max(0, x - 2), min(w, x + 3)
    cols = ink[y_a:y_b, x_lo:x_hi] > 0
    if cols.size == 0:
        return False
    return float(cols.mean(axis=0).max()) >= _FRAME_SIDE_FILL


def _frame_edges(ink: np.ndarray, segs) -> set:
    """Indices of segments that are the top/bottom border of a drawn
    rectangle (FIX-17).  SEM info bars often box the scale bar; the box
    edges are longer than the bar itself and used to win "longest line".
    A pair of segments with matching x-extent whose ends are joined by
    inked vertical sides (a closed outline) is a frame, not a bar.  The
    bar's own end ticks are short and never reach a second, equally long
    parallel line, so a real bar is not rejected."""
    out = set()
    for i, (xa, ya, wa, ha) in enumerate(segs):
        for j in range(i + 1, len(segs)):
            xb, yb, wb, hb = segs[j]
            if (abs(xa - xb) > _FRAME_END_TOL
                    or abs((xa + wa) - (xb + wb)) > _FRAME_END_TOL):
                continue
            (t, b) = ((ya, ha), (yb, hb)) if ya < yb else ((yb, hb), (ya, ha))
            y_a, y_b = t[0] + t[1], b[0]          # rows strictly between
            if y_b - y_a < 3:
                continue
            left = min(xa, xb)
            right = max(xa + wa, xb + wb) - 1
            if _side_filled(ink, left, y_a, y_b) and _side_filled(ink, right, y_a, y_b):
                out.update((i, j))
    return out


# Faint-line pass: some exports (downscaled Thermo/FEI data bars) draw the
# scale line and its ticks only ~35-50 grey levels above the bar background,
# below _INK_DELTA.  A second, lower threshold finds those; its segments are
# only used where the normal threshold found nothing (see _line_candidates).
_INK_DELTA_FAINT = 30
# A text glyph ("L"+"E" feet in a bold font) can form a >= 21 px horizontal
# run.  Unlike a bar, that run is attached to strokes rising/falling from its
# INTERIOR; a bar only has end ticks.  Interior attached ink above this many
# px per px of run length marks a glyph.
_GLYPH_INTERIOR_INK = 0.3
# Split-line joining (Thermo/FEI: the label sits in a gap in the middle of
# the line): the two halves must be on the same row (+-2 px), of similar
# length, and the gap no longer than the longer half.
_SPLIT_ROW_TOL = 2
_SPLIT_HALF_RATIO = 0.5


def _glyph_attached(ink: np.ndarray, seg, labels: np.ndarray) -> bool:
    """True when ``seg`` is the horizontal stroke of a text glyph: its
    connected ink component carries substantial ink above/below the run
    away from the run's two ends (end ticks are allowed)."""
    x, y, w, h = seg
    comp_ids = np.unique(labels[y:y + h, x:x + w])
    comp_ids = comp_ids[comp_ids > 0]
    if comp_ids.size == 0:
        return False
    margin = max(3, int(round(0.08 * w)))
    xa, xb = x + margin, x + w - margin
    if xb <= xa:
        return False
    cols = labels[:, xa:xb]
    mask = np.isin(cols, comp_ids)
    mask[max(0, y - 1):y + h + 1, :] = False          # the run itself (+AA)
    return mask.sum() > _GLYPH_INTERIOR_INK * (xb - xa)


def _has_tick(ink: np.ndarray, x: int, y: int, h: int) -> bool:
    """A vertical end tick at column ~x: ink above or below the line."""
    H, W = ink.shape[:2]
    x_lo, x_hi = max(0, x - 2), min(W, x + 3)
    above = ink[max(0, y - 5):max(0, y - 1), x_lo:x_hi] > 0
    below = ink[min(H, y + h + 1):min(H, y + h + 5), x_lo:x_hi] > 0
    best = 0
    for part in (above, below):
        if part.size:
            best = max(best, int(part.sum(axis=0).max()))
    return best >= 3


def _box_in_gap(boxes, x_lo: int, x_hi: int, y: int, h: int) -> bool:
    """An OCR word box (region coordinates) lies inside the gap x_lo..x_hi
    and on the line's row band (the label printed in the line's gap)."""
    for bx, by, bw, bh in boxes:
        if bx >= x_lo - 3 and bx + bw <= x_hi + 3                 and by <= y + h + bh and by + bh >= y - bh:
            return True
    return False


def _join_split_lines(ink: np.ndarray, segs, text_boxes=None):
    """Join a scale line that the instrument split in two to print its
    label in the middle (Thermo/FEI data bars: "|-----100 um-----|").
    Two segments on the same row, of similar length (shorter >= half the
    longer), gap no longer than the longer half, with non-line ink (the
    label) in the gap, are one bar only when there is positive evidence
    that they are the two halves of a scale bar:

    * end ticks at BOTH outer ends (the bar's own end marks), or
    * (OCR path only, ``text_boxes`` given) an OCR word box sits in the gap.

    Two plain dashes with a number between them (a dimension line, an
    underline) have neither and stay separate.  The joined length runs from
    the outer end of one half to the outer end of the other, which is the
    length the label refers to.  Output keeps the input (scan) order; a
    joined bar takes the place of its first half."""
    order = sorted(range(len(segs)), key=lambda k: segs[k][0])
    partner = {}
    taken = set()
    for pos, i in enumerate(order):
        if i in taken:
            continue
        a = segs[i]
        for j in order[pos + 1:]:
            if j in taken:
                continue
            b = segs[j]
            if abs(a[1] - b[1]) > _SPLIT_ROW_TOL                     or abs(a[3] - b[3]) > _SPLIT_ROW_TOL:
                continue
            gap = b[0] - (a[0] + a[2])
            longer = max(a[2], b[2])
            ratio = min(a[2], b[2]) / float(longer)
            if gap <= 2 or gap > longer or ratio < _SPLIT_HALF_RATIO:
                continue
            y_lo = max(0, min(a[1], b[1]) - 12)
            y_hi = max(a[1] + a[3], b[1] + b[3]) + 12
            label_ink = int(np.count_nonzero(
                ink[y_lo:y_hi, a[0] + a[2] + 1:b[0] - 1]))
            if label_ink < 6:
                continue
            ticks = _has_tick(ink, a[0], a[1], a[3]) and                 _has_tick(ink, b[0] + b[2] - 1, b[1], b[3])
            boxed = bool(text_boxes) and _box_in_gap(
                text_boxes, a[0] + a[2], b[0], a[1], a[3])
            if not (ticks or boxed):
                continue
            partner[i] = j
            taken.update((i, j))
            break
    out = []
    for k, a in enumerate(segs):
        if k in partner:
            b = segs[partner[k]]
            y0 = min(a[1], b[1])
            y1 = max(a[1] + a[3], b[1] + b[3])
            out.append((a[0], y0, b[0] + b[2] - a[0], y1 - y0))
        elif k not in taken:
            out.append(a)
    return out


def _line_candidates(ink: np.ndarray, max_h: int, max_w: int,
                     faint: Optional[np.ndarray] = None,
                     reject_glyphs: bool = True, join_split: bool = True,
                     text_boxes=None):
    """Scale-bar line candidates (x, y, w, h), longest first (ties keep the
    contour scan order, as the original "first longest" rule did):
    horizontal runs that are not the border of a drawn frame and, with
    ``reject_glyphs``, not a text-glyph stroke; with ``join_split`` a line
    split around its centred label is joined.  ``faint`` (optional, a
    lower-threshold ink mask) contributes runs that do not overlap any
    normal-threshold run.  ``text_boxes`` are OCR word boxes in the ink's
    coordinates (evidence for a split-line join)."""
    segs = _horizontal_segments(ink, max_h, max_w)
    frames = _frame_edges(ink, segs)
    labels = cv2.connectedComponents(ink, connectivity=8)[1]         if reject_glyphs else None
    keep = [s for k, s in enumerate(segs)
            if k not in frames and not (
                reject_glyphs and _glyph_attached(ink, s, labels))]
    union = ink
    if faint is not None:
        fsegs = _horizontal_segments(faint, max_h, max_w)
        fframes = _frame_edges(faint, fsegs)
        flabels = cv2.connectedComponents(faint, connectivity=8)[1]             if reject_glyphs else None
        for k, s in enumerate(fsegs):
            if k in fframes or (reject_glyphs
                                and _glyph_attached(faint, s, flabels)):
                continue
            x, y, w, h = s
            if np.any(ink[max(0, y - 1):y + h + 1, x:x + w]):
                continue                      # already covered by a strong run
            keep.append(s)
        union = faint
    if join_split:
        keep = _join_split_lines(union, keep, text_boxes)
    keep.sort(key=lambda s: -s[2])            # stable: ties keep scan order
    return keep


def find_scale_bar_candidates(image, info_bar: Optional[InfoBarResult] = None,
                              fallback_strip: bool = False,
                              text_boxes=None) -> list:
    """All plausible scale-bar lines, longest first (ties in scan order), in
    the format of :func:`find_scale_bar_line`.

    Inside a detected info bar, text-glyph strokes are rejected, a faint
    line (>= _INK_DELTA_FAINT above the background) is accepted and a line
    split around its label is joined.  ``text_boxes`` (full-frame OCR word
    boxes, OCR path only) also allow the join without end ticks when a word
    sits in the gap.  The ``fallback_strip`` search on the micrograph itself
    is unchanged from v2 (no glyph rejection, no joining): bright grains
    touching a real bar must not get it rejected."""
    gray = _gray(image)
    H, W = gray.shape[:2]
    ib = info_bar if info_bar is not None else detect_info_bar(gray)
    candidates = []
    if ib is not None:
        for bar in ib.bars:
            x0, y0, bw, bh = bar.rect
            region = gray[y0:y0 + bh, x0:x0 + bw].astype(np.int16)
            diff = np.abs(region - bar.background_value)
            ink = (diff >= _INK_DELTA).astype(np.uint8) * 255
            faint = (diff >= _INK_DELTA_FAINT).astype(np.uint8) * 255
            boxes = [(bx - x0, by - y0, w_, h_)
                     for (bx, by, w_, h_) in (text_boxes or ())]
            for (x, y, cw, ch) in _line_candidates(
                    ink, max(10, bh // 4), int(bw * 0.9), faint,
                    text_boxes=boxes or None):
                candidates.append({"length_px": int(cw),
                                   "rect": (x0 + x, y0 + y, int(cw), int(ch)),
                                   "info_bar_rect": tuple(bar.rect),
                                   "source": "info_bar"})
    elif fallback_strip:
        crop_top = int(H * 0.78)
        _, thresh = cv2.threshold(gray[crop_top:], 180, 255, cv2.THRESH_BINARY)
        for (x, y, cw, ch) in _line_candidates(thresh, 10, W,
                                               reject_glyphs=False,
                                               join_split=False):
            candidates.append({"length_px": int(cw),
                               "rect": (x, crop_top + y, int(cw), int(ch)),
                               "info_bar_rect": None,
                               "source": "bottom_strip"})
    candidates.sort(key=lambda c: -c["length_px"])      # stable
    return candidates


def find_scale_bar_line(image, info_bar: Optional[InfoBarResult] = None,
                        fallback_strip: bool = False) -> Optional[dict]:
    """Locate the scale-bar line.

    Parameters
    ----------
    image : BGR or gray array (full frame).
    info_bar : a precomputed :func:`detect_info_bar` result for ``image``
        (computed here when omitted).
    fallback_strip : when no info bar is detected, search the bottom 22 %
        with the legacy bright-line rule (v2 behaviour).  Off by default:
        on a bar-less micrograph bright grain edges can mimic a line.

    Returns
    -------
    ``None`` or ``{"length_px": int, "rect": (x, y, w, h) full-frame,
    "info_bar_rect": (x, y, w, h) | None, "source": "info_bar" |
    "bottom_strip"}``.
    """
    candidates = find_scale_bar_candidates(image, info_bar, fallback_strip)
    return candidates[0] if candidates else None


def auto_detect_scale_bar(image_bgr: np.ndarray) -> Tuple[Optional[float], Optional[np.ndarray]]:
    """Scale bar line + label. Returns (px_per_um, annotated_image) or
    (None, None).  Without a bundled OCR engine the label cannot be read, so
    this normally returns (None, None) — use :func:`find_scale_bar_line`
    and ask the user for the µm value."""
    found = find_scale_bar_line(image_bgr, fallback_strip=True)
    if found is None or found["length_px"] < 10:
        logger.warning("No scale bar line found")
        return None, None
    bar_px = found["length_px"]
    x, y, cw, ch = found["rect"]
    um_value = _read_label_ocr(image_bgr, (x, y, cw, ch),
                               _gray(image_bgr))
    if um_value is None:
        um_value = _read_label_simple(_gray(image_bgr))
    if um_value is None or um_value <= 0:
        logger.warning(f"Scale bar found ({bar_px} px) but could not read label")
        return None, None
    px_per_um = bar_px / um_value
    logger.info(f"Scale bar: {bar_px} px = {um_value} µm  →  {px_per_um:.4f} px/µm")
    annotated = _annotate_image(image_bgr.copy(), (x, y, cw, ch), 0,
                                bar_px, um_value, px_per_um)
    return px_per_um, annotated


def _read_label_ocr(strip_bgr, bar_rect, gray):
    try:
        import pytesseract
    except ImportError:
        return None
    x, y, cw, ch = bar_rect
    h, w = strip_bgr.shape[:2]
    sy1 = max(0, y - 30)
    sy2 = min(h, y + ch + 30)
    sx1 = max(0, x - 10)
    sx2 = min(w, x + cw + 60)
    roi = strip_bgr[sy1:sy2, sx1:sx2]
    if roi.size == 0:
        return None
    roi_up = cv2.resize(roi, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    gray_up = cv2.cvtColor(roi_up, cv2.COLOR_BGR2GRAY)
    results = []
    for tv in [128, 160, 200]:
        _, binary = cv2.threshold(gray_up, tv, 255, cv2.THRESH_BINARY)
        _, inv = cv2.threshold(gray_up, tv, 255, cv2.THRESH_BINARY_INV)
        for img in [binary, inv]:
            try:
                text = pytesseract.image_to_string(img, config='--psm 7 --oem 3 -c tessedit_char_whitelist=0123456789.nmkuµμ ')
                val = _parse_scale_text(text)
                if val:
                    results.append(val)
            except Exception:
                pass
    return results[0] if results else None


def _read_label_simple(gray):
    return None


def _parse_scale_text(text):
    if not text:
        return None
    text = text.strip().replace('\n', ' ')
    pattern = r'(\d+(?:[.,]\d+)?)\s*(nm|um|µm|μm|mm|micron|microns)?'
    matches = re.findall(pattern, text, re.IGNORECASE)
    for value_str, unit in matches:
        try:
            value = float(value_str.replace(',', '.'))
            unit = unit.lower().strip() if unit else 'um'
            multiplier = _UNIT_TO_UM.get(unit, 1.0)
            result = value * multiplier
            if 0.001 <= result <= 10000:
                return result
        except ValueError:
            continue
    return None


def _annotate_image(image, bar_rect, crop_top, bar_px, um_value, px_per_um):
    x, y, cw, ch = bar_rect
    abs_y = crop_top + y
    cv2.rectangle(image, (x-4, abs_y-8), (x+cw+4, abs_y+ch+8), (0, 255, 80), 3)
    label = f"{bar_px}px = {um_value}µm  ({px_per_um:.2f} px/µm)"
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale, thickness = 0.6, 2
    (tw, th), _ = cv2.getTextSize(label, font, scale, thickness)
    tx = max(0, x)
    ty = max(th+4, abs_y-12)
    cv2.putText(image, label, (tx+1, ty+1), font, scale, (0,0,0), thickness+1)
    cv2.putText(image, label, (tx, ty), font, scale, (0, 255, 80), thickness)
    return image


def detect_scale_bar_length_px(image_bgr):
    """Return (bar_length_px, debug_bgr) or (None, None)."""
    found = find_scale_bar_line(image_bgr, fallback_strip=True)
    if found is None:
        return None, None
    debug = image_bgr.copy() if image_bgr.ndim == 3 else cv2.cvtColor(
        image_bgr, cv2.COLOR_GRAY2BGR)
    x, y, cw, ch = found["rect"]
    cv2.rectangle(debug, (x, y), (x + cw, y + ch), (0, 255, 0), 2)
    return found["length_px"], debug


def compute_px_per_um(bar_length_px, bar_length_um):
    if bar_length_um <= 0:
        raise ValueError("Scale bar length must be positive")
    return bar_length_px / bar_length_um
