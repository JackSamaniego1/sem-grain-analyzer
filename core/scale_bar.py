"""
Scale Bar Detection - fully automatic
======================================
Detects the scale bar LINE and reads the TEXT label automatically.
No manual input needed.
"""

import numpy as np
import cv2
import re
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_UNIT_TO_UM = {
    'nm': 0.001, 'um': 1.0, 'µm': 1.0, 'μm': 1.0,
    'micron': 1.0, 'microns': 1.0, 'mm': 1000.0,
}


def auto_detect_scale_bar(image_bgr: np.ndarray) -> Tuple[Optional[float], Optional[np.ndarray]]:
    """Fully automatic scale bar detection. Returns (px_per_um, annotated_image) or (None, None)."""
    h, w = image_bgr.shape[:2]
    crop_top = int(h * 0.78)
    strip_bgr = image_bgr[crop_top:, :]
    strip_gray = cv2.cvtColor(strip_bgr, cv2.COLOR_BGR2GRAY)

    bar_px, bar_rect = _find_bar_pixels(strip_gray)
    if bar_px is None or bar_px < 10:
        logger.warning("No scale bar line found")
        return None, None

    um_value = _read_label_ocr(strip_bgr, bar_rect, strip_gray)
    if um_value is None:
        um_value = _read_label_simple(strip_gray)

    if um_value is None or um_value <= 0:
        logger.warning(f"Scale bar found ({bar_px} px) but could not read label")
        return None, None

    px_per_um = bar_px / um_value
    logger.info(f"Scale bar: {bar_px} px = {um_value} µm  →  {px_per_um:.4f} px/µm")
    annotated = _annotate_image(image_bgr.copy(), bar_rect, crop_top, bar_px, um_value, px_per_um)
    return px_per_um, annotated


def _find_bar_pixels(gray: np.ndarray) -> Tuple[Optional[int], Optional[tuple]]:
    _, thresh = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (20, 1))
    horiz = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
    contours, _ = cv2.findContours(horiz, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    best_len = 0
    for cnt in contours:
        x, y, cw, ch = cv2.boundingRect(cnt)
        if cw > 20 and ch <= 10:
            roi = horiz[y:y+ch, x:x+cw]
            fill = np.sum(roi > 0) / max(roi.size, 1)
            if fill > 0.5 and cw > best_len:
                best = (x, y, cw, ch)
                best_len = cw
    if best is None:
        return None, None
    return best[2], best


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
    h, w = image_bgr.shape[:2]
    crop_top = int(h * 0.80)
    strip_gray = cv2.cvtColor(image_bgr[crop_top:, :], cv2.COLOR_BGR2GRAY)
    bar_px, bar_rect = _find_bar_pixels(strip_gray)
    if bar_px is None:
        return None, None
    debug = image_bgr.copy()
    x, y, cw, ch = bar_rect
    cv2.rectangle(debug, (x, crop_top+y), (x+cw, crop_top+y+ch), (0,255,0), 2)
    return bar_px, debug


def compute_px_per_um(bar_length_px, bar_length_um):
    if bar_length_um <= 0:
        raise ValueError("Scale bar length must be positive")
    return bar_length_px / bar_length_um
