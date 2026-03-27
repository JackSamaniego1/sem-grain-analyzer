"""
Scale Bar Detection
===================
Detects scale bar in SEM images to establish pixel-to-micron calibration.
Strategy:
  1. Crop bottom region (scale bars are almost always at the bottom)
  2. Find horizontal white/light lines using Hough transform
  3. Use OCR (pytesseract if available) or manual input for the length value
  4. Fall back to manual calibration if auto-detection fails
"""

import numpy as np
import cv2
import re
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def detect_scale_bar_length_px(image_bgr: np.ndarray) -> Tuple[Optional[int], Optional[np.ndarray]]:
    """
    Try to detect the scale bar pixel length from the image.
    Returns (length_in_pixels, debug_image) or (None, None).
    """
    h, w = image_bgr.shape[:2]
    
    # Scale bar is almost always in the bottom 15-20% of SEM images
    crop_top = int(h * 0.80)
    bottom_strip = image_bgr[crop_top:, :]
    gray = cv2.cvtColor(bottom_strip, cv2.COLOR_BGR2GRAY)
    
    # Threshold: scale bar lines are typically bright white
    _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
    
    # Look for horizontal line segments
    # Use morphological erosion to find only wide horizontal structures
    kernel_horiz = cv2.getStructuringElement(cv2.MORPH_RECT, (30, 1))
    horiz = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel_horiz)
    
    # Find contours of horizontal bars
    contours, _ = cv2.findContours(horiz, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    best_bar = None
    best_length = 0
    
    for cnt in contours:
        x, y, cw, ch = cv2.boundingRect(cnt)
        # Scale bar: much wider than tall, and reasonably long
        if cw > 30 and ch <= 8 and cw > best_length:
            # Check fill ratio (must be mostly solid)
            roi = horiz[y:y+ch, x:x+cw]
            fill = np.sum(roi > 0) / roi.size
            if fill > 0.6:
                best_bar = (x, y + crop_top, cw, ch)
                best_length = cw
    
    if best_bar is None:
        return None, None
    
    # Draw debug visualization
    debug = image_bgr.copy()
    x, y, cw, ch = best_bar
    cv2.rectangle(debug, (x, y), (x + cw, y + ch), (0, 255, 0), 2)
    cv2.putText(debug, f"{cw}px", (x, y - 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    
    return best_length, debug


def read_scale_bar_text(image_bgr: np.ndarray) -> Optional[float]:
    """
    Attempt to read the scale bar label using pytesseract OCR.
    Returns value in micrometers, or None if OCR unavailable / failed.
    """
    try:
        import pytesseract
    except ImportError:
        logger.info("pytesseract not available — using manual calibration")
        return None
    
    h, w = image_bgr.shape[:2]
    crop_top = int(h * 0.78)
    strip = image_bgr[crop_top:, :]
    
    # Upscale for better OCR accuracy
    strip_up = cv2.resize(strip, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(strip_up, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 160, 255, cv2.THRESH_BINARY)
    
    try:
        text = pytesseract.image_to_string(thresh, config='--psm 7')
        logger.info(f"OCR text: {repr(text)}")
    except Exception as e:
        logger.warning(f"OCR failed: {e}")
        return None
    
    # Parse common SEM scale bar formats: "1 µm", "500 nm", "10 um", "2.5μm"
    text = text.strip()
    
    # Match patterns like "1 µm", "500nm", "10 um", "0.5 mm"
    pattern = r'(\d+(?:\.\d+)?)\s*(nm|um|µm|μm|mm|micron|microns)?'
    matches = re.findall(pattern, text, re.IGNORECASE)
    
    for value_str, unit in matches:
        value = float(value_str)
        unit = unit.lower().strip()
        
        if unit in ('nm',):
            return value / 1000.0  # nm -> µm
        elif unit in ('um', 'µm', 'μm', 'micron', 'microns', ''):
            return value  # already µm
        elif unit in ('mm',):
            return value * 1000.0  # mm -> µm
    
    return None


def compute_px_per_um(bar_length_px: int, bar_length_um: float) -> float:
    """Convert bar measurements to pixels-per-micron calibration."""
    if bar_length_um <= 0:
        raise ValueError("Scale bar length must be positive")
    return bar_length_px / bar_length_um
