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
    if image.ndim == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return image


def _longest_line(ink: np.ndarray, max_h: int, max_w: int):
    """Longest solid horizontal run (x, y, w, h) in a binary ink mask.
    Opening with a 21x1 kernel keeps horizontal strokes >= 21 px and removes
    text glyphs and the 1-px vertical end ticks of the bar."""
    # 21 (odd): an even-width kernel shifts the opened stroke 1 px right
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (21, 1))
    horiz = cv2.morphologyEx(ink, cv2.MORPH_OPEN, kernel)
    contours, _ = cv2.findContours(horiz, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    best, best_len = None, 0
    for cnt in contours:
        x, y, cw, ch = cv2.boundingRect(cnt)
        if cw <= 20 or ch > max_h or cw > max_w:
            continue
        roi = horiz[y:y + ch, x:x + cw]
        fill = np.count_nonzero(roi) / max(roi.size, 1)
        if fill > 0.5 and cw > best_len:
            best, best_len = (x, y, cw, ch), cw
    return best


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
    gray = _gray(np.asarray(image))
    H, W = gray.shape[:2]
    ib = info_bar if info_bar is not None else detect_info_bar(gray)
    candidates = []
    if ib is not None:
        for bar in ib.bars:
            x0, y0, bw, bh = bar.rect
            region = gray[y0:y0 + bh, x0:x0 + bw].astype(np.int16)
            ink = (np.abs(region - bar.background_value) >= _INK_DELTA
                   ).astype(np.uint8) * 255
            found = _longest_line(ink, max(10, bh // 4), int(bw * 0.9))
            if found is not None:
                x, y, cw, ch = found
                candidates.append({"length_px": int(cw),
                                   "rect": (x0 + x, y0 + y, int(cw), int(ch)),
                                   "info_bar_rect": tuple(bar.rect),
                                   "source": "info_bar"})
    elif fallback_strip:
        crop_top = int(H * 0.78)
        _, thresh = cv2.threshold(gray[crop_top:], 180, 255, cv2.THRESH_BINARY)
        found = _longest_line(thresh, 10, W)
        if found is not None:
            x, y, cw, ch = found
            candidates.append({"length_px": int(cw),
                               "rect": (x, crop_top + y, int(cw), int(ch)),
                               "info_bar_rect": None,
                               "source": "bottom_strip"})
    if not candidates:
        return None
    return max(candidates, key=lambda c: c["length_px"])


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
