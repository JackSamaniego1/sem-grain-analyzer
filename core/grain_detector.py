"""
Grain Detection Engine v1.3
============================
Improved touching grain separation using edge/boundary detection.

Key improvement: SEM images often have thin dark boundary lines between grains.
The old approach (pure brightness threshold) merges touching grains.
New approach:
  1. Detect dark boundary edges using Canny + morphology
  2. Use boundaries as barriers in watershed (not just distance peaks)
  3. Fall back gracefully to old method if no clear edges found
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
    # New: edge sensitivity for boundary detection (0=off, 1=auto, higher=more sensitive)
    edge_sensitivity: float = 1.0


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

        progress(15, "Detecting grain boundaries...")
        # Build edge map from dark boundary lines between grains
        edge_mask = self._detect_boundaries(gray, image_bgr, params)

        progress(25, "Applying threshold...")
        gray_float = gray.astype(np.float64) / 255.0
        if params.blur_sigma > 0:
            blurred = gaussian(gray_float, sigma=params.blur_sigma)
        else:
            blurred = gray_float

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

        # Carve boundary lines INTO the binary mask so touching grains get separated
        if edge_mask is not None:
            binary[edge_mask > 0] = 0

        binary_bool = binary.astype(bool)
        result.binary_image = binary

        progress(45, "Removing debris...")
        if params.min_grain_size_px > 0:
            binary_bool = remove_small_objects(binary_bool, max_size=params.min_grain_size_px)

        progress(55, "Watershed segmentation...")
        if params.use_watershed:
            labels = self._watershed_segment(binary_bool, params, edge_mask)
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

            area_px   = float(region.area)
            perim_px  = float(region.perimeter) if region.perimeter > 0 else 1.0
            eq_diam_px = float(region.equivalent_diameter_area)

            if px_per_um > 0:
                px2 = px_per_um ** 2
                area_um2    = area_px / px2
                perim_um    = perim_px / px_per_um
                eq_diam_um  = eq_diam_px / px_per_um
                major_um    = region.axis_major_length / px_per_um
                minor_um    = region.axis_minor_length / px_per_um
            else:
                area_um2 = perim_um = eq_diam_um = major_um = minor_um = 0.0

            circularity = min((4 * np.pi * area_px) / (perim_px ** 2), 1.0)
            major_ax = region.axis_major_length
            minor_ax = region.axis_minor_length
            aspect   = (major_ax / minor_ax) if minor_ax > 0 else 1.0
            cy, cx   = region.centroid

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
        result.grains      = grains
        result.grain_count = len(grains)
        result.label_image = labels
        result = self._compute_statistics(result, image_bgr)

        progress(90, "Generating overlay...")
        result.overlay_image = self._draw_overlay(image_bgr, labels, grains)

        progress(100, f"Complete — {result.grain_count} grains detected.")
        self._last_result = result
        return result

    # ------------------------------------------------------------------
    # Boundary / edge detection
    # ------------------------------------------------------------------

    def _detect_boundaries(self, gray: np.ndarray, bgr: np.ndarray,
                            params: DetectionParams) -> Optional[np.ndarray]:
        """
        Detect thin dark boundary lines between grains.
        Returns a binary mask (uint8) where 255 = boundary, 0 = grain interior.
        Returns None if no clear boundaries found.
        """
        if params.edge_sensitivity <= 0:
            return None

        h, w = gray.shape

        # --- Strategy 1: Canny edge detection on grayscale ---
        # Smooth lightly first so we don't pick up noise
        smoothed = cv2.GaussianBlur(gray, (3, 3), 0.8)
        # Auto thresholds via median heuristic
        median_val = float(np.median(smoothed))
        lo = max(10, int(median_val * 0.4 * params.edge_sensitivity))
        hi = min(250, int(median_val * 1.2 * params.edge_sensitivity))
        edges_canny = cv2.Canny(smoothed, lo, hi)

        # --- Strategy 2: Local minima (dark valleys) ---
        # Works well when grain boundaries are clearly darker than grain interiors
        blur_wide = cv2.GaussianBlur(gray, (7, 7), 2.0)
        # Dark pixels significantly below local average = boundary
        local_avg = cv2.GaussianBlur(gray.astype(np.float32), (21, 21), 6.0)
        dark_diff = local_avg - gray.astype(np.float32)
        dark_thresh = np.percentile(dark_diff, 85)  # top 15% darkest relative to local mean
        dark_boundaries = (dark_diff > dark_thresh).astype(np.uint8) * 255

        # Combine both signals
        combined = cv2.bitwise_or(edges_canny, dark_boundaries)

        # Thin the boundary lines (skeletonize) so they don't eat too much grain
        kernel_thin = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
        combined = cv2.morphologyEx(combined, cv2.MORPH_ERODE, kernel_thin, iterations=1)

        # Only keep if meaningful (>0.5% of pixels are boundaries)
        boundary_frac = np.sum(combined > 0) / (h * w)
        if boundary_frac < 0.003:
            logger.info(f"Boundary fraction too low ({boundary_frac:.4f}), skipping edge carving")
            return None

        logger.info(f"Boundary detection: {boundary_frac:.3f} of pixels are edges")
        return combined

    # ------------------------------------------------------------------
    # Watershed
    # ------------------------------------------------------------------

    def _watershed_segment(self, binary: np.ndarray, params: DetectionParams,
                            edge_mask: Optional[np.ndarray] = None) -> np.ndarray:
        """
        Improved watershed that uses the edge map as barriers.
        """
        distance = ndi.distance_transform_edt(binary)

        # If we have an edge map, suppress distance at edges so watershed
        # naturally places cuts there
        if edge_mask is not None:
            edge_float = (edge_mask > 0).astype(np.float32)
            # Dilate edges slightly for a stronger barrier
            kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
            edge_dilated = cv2.dilate(edge_float, kern, iterations=1)
            suppression = 1.0 - edge_dilated * 0.7   # reduce distance by 70% at edges
            distance = distance * suppression

        min_dist = max(params.watershed_min_dist, 3)
        coords = peak_local_max(
            distance,
            min_distance=min_dist,
            labels=binary,
            exclude_border=False
        )
        if len(coords) == 0:
            labels, _ = ndi.label(binary)
            return labels

        mask_markers = np.zeros(distance.shape, dtype=bool)
        mask_markers[tuple(coords.T)] = True
        markers, _ = ndi.label(mask_markers)
        return watershed(-distance, markers, mask=binary)

    # ------------------------------------------------------------------
    # Statistics + overlay (unchanged)
    # ------------------------------------------------------------------

    def _compute_statistics(self, result, original):
        if not result.grains:
            return result
        if result.has_calibration:
            areas     = np.array([g.area_um2 for g in result.grains])
            diameters = np.array([g.equivalent_diameter_um for g in result.grains])
            result.mean_area_um2    = float(np.mean(areas))
            result.std_area_um2     = float(np.std(areas))
            result.median_area_um2  = float(np.median(areas))
            result.min_area_um2     = float(np.min(areas))
            result.max_area_um2     = float(np.max(areas))
            result.mean_diameter_um = float(np.mean(diameters))
            result.std_diameter_um  = float(np.std(diameters))
            h, w = original.shape[:2]
            total_img_um2 = (h * w) / (result.px_per_um ** 2)
            result.total_analyzed_area_um2 = total_img_um2
            result.grain_coverage_pct = (float(np.sum(areas)) / total_img_um2) * 100.0
        circs   = np.array([g.circularity  for g in result.grains])
        aspects = np.array([g.aspect_ratio for g in result.grains])
        result.mean_circularity  = float(np.mean(circs))
        result.mean_aspect_ratio = float(np.mean(aspects))
        return result

    def _draw_overlay(self, image_bgr, labels, grains):
        overlay   = image_bgr.copy()
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
        mask    = labels > 0
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
                fs   = 0.35
                (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, fs, 1)
                tx = max(0, cx - tw // 2)
                ty = max(th, cy + th // 2)
                cv2.putText(overlay, text, (tx+1, ty+1), cv2.FONT_HERSHEY_SIMPLEX, fs, (0,0,0), 2)
                cv2.putText(overlay, text, (tx, ty),     cv2.FONT_HERSHEY_SIMPLEX, fs, (255,255,255), 1)
        return overlay
