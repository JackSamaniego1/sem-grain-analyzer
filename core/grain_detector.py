"""
Grain Detection Engine
======================
Uses OpenCV + scikit-image to detect and measure grains in SEM images.
Pipeline:
  1. Grayscale conversion + Gaussian blur
  2. Otsu thresholding (automatic)
  3. Morphological cleanup
  4. Watershed segmentation (separates touching grains)
  5. Region property measurement
"""

import numpy as np
import cv2
from scipy import ndimage as ndi
from skimage.segmentation import watershed
from skimage.feature import peak_local_max
from skimage.measure import regionprops
from skimage.morphology import remove_small_objects
from skimage.filters import threshold_otsu, gaussian
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


@dataclass
class GrainResult:
    grain_id: int
    area_px: float
    area_um2: float
    perimeter_px: float
    perimeter_um: float
    equivalent_diameter_px: float
    equivalent_diameter_um: float
    major_axis_um: float
    minor_axis_um: float
    aspect_ratio: float
    circularity: float
    eccentricity: float
    centroid_x: float
    centroid_y: float
    bbox: Tuple[int, int, int, int]


@dataclass
class AnalysisResult:
    grains: List[GrainResult] = field(default_factory=list)
    grain_count: int = 0
    label_image: Optional[np.ndarray] = None
    overlay_image: Optional[np.ndarray] = None
    binary_image: Optional[np.ndarray] = None
    px_per_um: float = 0.0
    has_calibration: bool = False
    mean_area_um2: float = 0.0
    std_area_um2: float = 0.0
    median_area_um2: float = 0.0
    min_area_um2: float = 0.0
    max_area_um2: float = 0.0
    mean_diameter_um: float = 0.0
    std_diameter_um: float = 0.0
    mean_circularity: float = 0.0
    mean_aspect_ratio: float = 0.0
    total_analyzed_area_um2: float = 0.0
    grain_coverage_pct: float = 0.0


@dataclass
class DetectionParams:
    blur_sigma: float = 1.5
    threshold_offset: float = 0.0
    min_grain_size_px: int = 50
    max_grain_size_px: int = 0
    watershed_min_dist: int = 5
    dark_grains: bool = False
    use_watershed: bool = True
    morph_close_size: int = 3
    morph_open_size: int = 2


class GrainDetector:

    def __init__(self):
        self._last_result = None

    def analyze(self, image_bgr, px_per_um=0.0, params=None, progress_callback=None):
        if params is None:
            params = DetectionParams()

        result = AnalysisResult(px_per_um=px_per_um, has_calibration=(px_per_um > 0))

        def progress(pct, msg):
            if progress_callback:
                progress_callback(pct, msg)

        progress(5, "Converting to grayscale...")
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)

        progress(15, "Applying Gaussian blur...")
        gray_float = gray.astype(np.float64) / 255.0
        if params.blur_sigma > 0:
            blurred = gaussian(gray_float, sigma=params.blur_sigma)
        else:
            blurred = gray_float

        progress(25, "Computing Otsu threshold...")
        thresh_val = threshold_otsu(blurred)
        thresh_val = float(np.clip(thresh_val + params.threshold_offset, 0.01, 0.99))

        binary = (blurred < thresh_val) if params.dark_grains else (blurred > thresh_val)
        binary = binary.astype(np.uint8)

        progress(35, "Morphological cleanup...")
        if params.morph_close_size > 0:
            k = params.morph_close_size * 2 + 1
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
            binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
        if params.morph_open_size > 0:
            k = params.morph_open_size * 2 + 1
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
            binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

        binary_bool = binary.astype(bool)
        result.binary_image = binary

        progress(45, "Removing debris...")
        if params.min_grain_size_px > 0:
            binary_bool = remove_small_objects(binary_bool, max_size=params.min_grain_size_px)

        progress(55, "Watershed segmentation...")
        if params.use_watershed:
            labels = self._watershed_segment(binary_bool, params)
        else:
            labels, _ = ndi.label(binary_bool)

        progress(65, "Measuring grain properties...")
        regions = regionprops(labels)
        grains = []

        for region in regions:
            if region.area < max(params.min_grain_size_px, 5):
                continue
            if params.max_grain_size_px > 0 and region.area > params.max_grain_size_px:
                continue

            area_px = float(region.area)
            perim_px = float(region.perimeter) if region.perimeter > 0 else 1.0
            eq_diam_px = float(region.equivalent_diameter_area)

            if px_per_um > 0:
                px2 = px_per_um ** 2
                area_um2 = area_px / px2
                perim_um = perim_px / px_per_um
                eq_diam_um = eq_diam_px / px_per_um
                major_um = region.axis_major_length / px_per_um
                minor_um = region.axis_minor_length / px_per_um
            else:
                area_um2 = perim_um = eq_diam_um = major_um = minor_um = 0.0

            circularity = min((4 * np.pi * area_px) / (perim_px ** 2), 1.0)
            major_ax = region.axis_major_length
            minor_ax = region.axis_minor_length
            aspect = (major_ax / minor_ax) if minor_ax > 0 else 1.0
            cy, cx = region.centroid

            grains.append(GrainResult(
                grain_id=region.label,
                area_px=area_px,
                area_um2=area_um2,
                perimeter_px=perim_px,
                perimeter_um=perim_um,
                equivalent_diameter_px=eq_diam_px,
                equivalent_diameter_um=eq_diam_um,
                major_axis_um=major_um,
                minor_axis_um=minor_um,
                aspect_ratio=float(aspect),
                circularity=float(circularity),
                eccentricity=float(region.eccentricity),
                centroid_x=float(cx),
                centroid_y=float(cy),
                bbox=region.bbox,
            ))

        progress(80, "Computing statistics...")
        result.grains = grains
        result.grain_count = len(grains)
        result.label_image = labels
        result = self._compute_statistics(result, image_bgr)

        progress(90, "Generating overlay...")
        result.overlay_image = self._draw_overlay(image_bgr, labels, grains)

        progress(100, f"Complete — {result.grain_count} grains detected.")
        self._last_result = result
        return result

    def _watershed_segment(self, binary, params):
        distance = ndi.distance_transform_edt(binary)
        min_dist = max(params.watershed_min_dist, 3)
        coords = peak_local_max(distance, min_distance=min_dist, labels=binary, exclude_border=False)
        if len(coords) == 0:
            labels, _ = ndi.label(binary)
            return labels
        mask_markers = np.zeros(distance.shape, dtype=bool)
        mask_markers[tuple(coords.T)] = True
        markers, _ = ndi.label(mask_markers)
        return watershed(-distance, markers, mask=binary)

    def _compute_statistics(self, result, original):
        if not result.grains:
            return result
        if result.has_calibration:
            areas = np.array([g.area_um2 for g in result.grains])
            diameters = np.array([g.equivalent_diameter_um for g in result.grains])
            result.mean_area_um2 = float(np.mean(areas))
            result.std_area_um2 = float(np.std(areas))
            result.median_area_um2 = float(np.median(areas))
            result.min_area_um2 = float(np.min(areas))
            result.max_area_um2 = float(np.max(areas))
            result.mean_diameter_um = float(np.mean(diameters))
            result.std_diameter_um = float(np.std(diameters))
            h, w = original.shape[:2]
            total_img_um2 = (h * w) / (result.px_per_um ** 2)
            result.total_analyzed_area_um2 = total_img_um2
            result.grain_coverage_pct = (float(np.sum(areas)) / total_img_um2) * 100.0
        circs = np.array([g.circularity for g in result.grains])
        aspects = np.array([g.aspect_ratio for g in result.grains])
        result.mean_circularity = float(np.mean(circs))
        result.mean_aspect_ratio = float(np.mean(aspects))
        return result

    def _draw_overlay(self, image_bgr, labels, grains):
        overlay = image_bgr.copy()
        color_map = np.zeros((*labels.shape, 3), dtype=np.uint8)
        unique_labels = np.unique(labels)
        unique_labels = unique_labels[unique_labels > 0]
        colors = {}
        for lbl in unique_labels:
            hue = int((lbl * 137.508) % 180)
            hsv = np.array([[[hue, 200, 220]]], dtype=np.uint8)
            bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0][0]
            colors[lbl] = bgr.tolist()
            color_map[labels == lbl] = bgr
        alpha = 0.4
        mask = labels > 0
        blended = cv2.addWeighted(overlay, 1 - alpha, color_map, alpha, 0)
        overlay[mask] = blended[mask]
        grain_map = {g.grain_id: g for g in grains}
        for lbl in unique_labels:
            if lbl not in grain_map:
                continue
            grain = grain_map[lbl]
            color = colors.get(lbl, [0, 200, 255])
            grain_mask = (labels == lbl).astype(np.uint8)
            contours, _ = cv2.findContours(grain_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(overlay, contours, -1, color, 1)
            cx, cy = int(grain.centroid_x), int(grain.centroid_y)
            if 0 <= cx < overlay.shape[1] and 0 <= cy < overlay.shape[0]:
                text = str(grain.grain_id)
                fs = 0.35
                (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, fs, 1)
                tx = max(0, cx - tw // 2)
                ty = max(th, cy + th // 2)
                cv2.putText(overlay, text, (tx+1, ty+1), cv2.FONT_HERSHEY_SIMPLEX, fs, (0,0,0), 2)
                cv2.putText(overlay, text, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, fs, (255,255,255), 1)
        return overlay
