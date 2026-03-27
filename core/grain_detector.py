"""
Grain Detection Engine v2.0
============================
Major upgrade: multi-strategy detection for SEM grain images.

Problems with v1.3:
  - Single global Otsu threshold misses grains whose brightness is close
    to the background (low-contrast grains appear as "background")
  - Touching grains with subtle boundaries merge into blobs
  - Edge carving is too aggressive or too timid depending on image

New approach (v2.0):
  1. GRADIENT MAGNITUDE map — detects ALL edges regardless of absolute brightness
  2. ADAPTIVE (local) thresholding — catches grains at every contrast level
  3. MULTI-SCALE boundary detection — combines fine and coarse edge signals
  4. MARKER-CONTROLLED WATERSHED on gradient — splits touching grains at their
     true boundary lines rather than arbitrary distance-based cuts
  5. Optional CLAHE preprocessing to boost local contrast before detection
"""

import numpy as np
import cv2
from scipy import ndimage as ndi
from skimage.segmentation import watershed
from skimage.feature import peak_local_max
from skimage.measure import regionprops, label as sk_label
from skimage.morphology import remove_small_objects, disk, erosion, dilation
from skimage.filters import threshold_otsu, gaussian, sobel
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
    edge_sensitivity: float = 1.0
    # --- v2.0 new params ---
    use_adaptive: bool = True           # Use adaptive (local) thresholding
    adaptive_block_size: int = 0        # 0 = auto-calculate from image size
    use_clahe: bool = True              # CLAHE contrast enhancement
    clahe_clip_limit: float = 2.0       # CLAHE clip limit (higher = more contrast)
    boundary_weight: float = 0.5        # How much gradient boundaries influence watershed (0-1)


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

        progress(2, "Preprocessing image...")
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape

        # ========== STAGE 1: Preprocessing ==========
        progress(5, "Enhancing contrast (CLAHE)...")
        if params.use_clahe:
            enhanced = self._apply_clahe(gray, params.clahe_clip_limit)
        else:
            enhanced = gray.copy()

        # Apply blur
        if params.blur_sigma > 0:
            enhanced_blur = cv2.GaussianBlur(enhanced, (0, 0), params.blur_sigma)
        else:
            enhanced_blur = enhanced

        # ========== STAGE 2: Multi-strategy grain mask ==========
        progress(10, "Computing gradient map...")
        gradient = self._compute_gradient(enhanced_blur)

        progress(18, "Adaptive thresholding...")
        if params.use_adaptive:
            mask_adaptive = self._adaptive_threshold(enhanced_blur, params)
        else:
            mask_adaptive = None

        progress(25, "Global thresholding (Otsu)...")
        mask_otsu = self._otsu_threshold(enhanced_blur, params)

        progress(30, "Combining detection strategies...")
        binary = self._combine_masks(mask_otsu, mask_adaptive, gradient, params)

        # ========== STAGE 3: Morphological cleanup ==========
        progress(38, "Morphological cleanup...")
        binary = self._morphological_cleanup(binary, params)

        # ========== STAGE 4: Boundary carving ==========
        progress(45, "Detecting grain boundaries...")
        boundary_mask = self._detect_boundaries_v2(gray, enhanced_blur, gradient, params)
        if boundary_mask is not None:
            binary[boundary_mask > 0] = 0

        binary_bool = binary.astype(bool)
        result.binary_image = binary

        # ========== STAGE 5: Remove debris ==========
        progress(50, "Removing debris...")
        if params.min_grain_size_px > 0:
            binary_bool = remove_small_objects(binary_bool, min_size=params.min_grain_size_px)

        # ========== STAGE 6: Watershed segmentation ==========
        progress(58, "Watershed segmentation...")
        if params.use_watershed:
            labels = self._watershed_on_gradient(binary_bool, gradient, params, boundary_mask)
        else:
            labels, _ = ndi.label(binary_bool)

        # ========== STAGE 7: Measure grains ==========
        progress(70, "Measuring grain properties...")
        grains = self._measure_grains(labels, params, px_per_um)

        # ========== STAGE 8: Statistics ==========
        progress(82, "Computing statistics...")
        result.grains = grains
        result.grain_count = len(grains)
        result.label_image = labels
        result = self._compute_statistics(result, image_bgr)

        progress(90, "Generating overlay...")
        result.overlay_image = self._draw_overlay(image_bgr, labels, grains)

        progress(100, f"Complete — {result.grain_count} grains detected.")
        self._last_result = result
        return result

    # ==================================================================
    # STAGE 1: Preprocessing
    # ==================================================================

    def _apply_clahe(self, gray: np.ndarray, clip_limit: float) -> np.ndarray:
        """
        CLAHE (Contrast Limited Adaptive Histogram Equalization).
        Boosts local contrast so low-contrast grains become visible
        without blowing out already-bright areas.
        """
        clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(8, 8))
        return clahe.apply(gray)

    # ==================================================================
    # STAGE 2: Multi-strategy mask generation
    # ==================================================================

    def _compute_gradient(self, gray: np.ndarray) -> np.ndarray:
        """
        Multi-scale gradient magnitude map.
        This finds ALL edges regardless of whether grains are bright or dark.
        Uses Sobel at multiple kernel sizes and combines them.
        """
        gray_f = gray.astype(np.float32) / 255.0

        # Fine scale (3x3) — catches thin boundary lines
        gx3 = cv2.Sobel(gray_f, cv2.CV_32F, 1, 0, ksize=3)
        gy3 = cv2.Sobel(gray_f, cv2.CV_32F, 0, 1, ksize=3)
        mag3 = np.sqrt(gx3**2 + gy3**2)

        # Medium scale (5x5) — catches broader gradients
        gx5 = cv2.Sobel(gray_f, cv2.CV_32F, 1, 0, ksize=5)
        gy5 = cv2.Sobel(gray_f, cv2.CV_32F, 0, 1, ksize=5)
        mag5 = np.sqrt(gx5**2 + gy5**2)

        # Combine: weight fine edges more heavily
        combined = 0.6 * mag3 + 0.4 * mag5
        # Normalize to 0-1
        cmax = combined.max()
        if cmax > 0:
            combined = combined / cmax

        return combined

    def _adaptive_threshold(self, gray: np.ndarray, params: DetectionParams) -> np.ndarray:
        """
        Adaptive (local) thresholding — the key improvement.
        Instead of one global threshold, each pixel is compared against
        the mean of its local neighborhood. This catches grains that are
        only slightly different from their local background.
        """
        h, w = gray.shape

        # Auto block size: ~1/15th of smallest dimension, must be odd and >= 11
        if params.adaptive_block_size > 0:
            block = params.adaptive_block_size
        else:
            block = max(11, int(min(h, w) / 15))
            if block % 2 == 0:
                block += 1

        # Offset: positive C means we need MORE difference from local mean
        # Negative threshold_offset -> detect more (lower the bar)
        c_value = 5 + int(params.threshold_offset * 30)

        if params.dark_grains:
            method = cv2.ADAPTIVE_THRESH_MEAN_C
            thresh_type = cv2.THRESH_BINARY_INV
        else:
            method = cv2.ADAPTIVE_THRESH_MEAN_C
            thresh_type = cv2.THRESH_BINARY

        adaptive = cv2.adaptiveThreshold(
            gray, 255, method, thresh_type, block, c_value
        )
        return adaptive

    def _otsu_threshold(self, gray: np.ndarray, params: DetectionParams) -> np.ndarray:
        """Classic Otsu thresholding (kept as one signal among several)."""
        gray_float = gray.astype(np.float64) / 255.0
        if params.blur_sigma > 0:
            blurred = gaussian(gray_float, sigma=params.blur_sigma)
        else:
            blurred = gray_float

        thresh_val = threshold_otsu(blurred)
        thresh_val = float(np.clip(thresh_val + params.threshold_offset, 0.01, 0.99))

        if params.dark_grains:
            binary = (blurred < thresh_val).astype(np.uint8) * 255
        else:
            binary = (blurred > thresh_val).astype(np.uint8) * 255

        return binary

    def _combine_masks(self, mask_otsu: np.ndarray,
                       mask_adaptive: Optional[np.ndarray],
                       gradient: np.ndarray,
                       params: DetectionParams) -> np.ndarray:
        """
        Combine multiple detection signals into one binary mask.

        Strategy: UNION of Otsu and adaptive, then use gradient to
        carve boundaries. The union catches grains that either method
        alone would miss.
        """
        # Start with Otsu
        combined = mask_otsu.copy()

        if mask_adaptive is not None:
            # Union: a pixel is "grain" if EITHER method says so
            combined = cv2.bitwise_or(combined, mask_adaptive)

        # Clean up tiny specks introduced by adaptive thresholding
        kernel_small = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        combined = cv2.morphologyEx(combined, cv2.MORPH_OPEN, kernel_small)

        # Use high-gradient pixels as boundary hints: zero out pixels
        # where gradient is very strong (these are edges, not interiors)
        grad_thresh = np.percentile(gradient, 90)
        strong_edges = (gradient > grad_thresh).astype(np.uint8)
        # Only carve edges that are thin (1-2px wide boundary lines)
        # by checking that the edge pixels are narrow
        kernel_edge = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2))
        thin_edges = cv2.erode(strong_edges, kernel_edge, iterations=1)
        combined[thin_edges > 0] = 0

        return (combined > 0).astype(np.uint8)

    # ==================================================================
    # STAGE 3: Morphological cleanup
    # ==================================================================

    def _morphological_cleanup(self, binary: np.ndarray, params: DetectionParams) -> np.ndarray:
        """Fill holes inside grains and remove noise outside."""
        if params.morph_close_size > 0:
            k = params.morph_close_size * 2 + 1
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
            binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

        if params.morph_open_size > 0:
            k = params.morph_open_size * 2 + 1
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
            binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

        # Fill small internal holes (common in SEM grains with texture)
        # Use flood fill from the border: anything not reached = hole
        h, w = binary.shape
        flood = binary.copy()
        mask = np.zeros((h + 2, w + 2), dtype=np.uint8)
        # Invert: holes become foreground
        inv = 1 - flood
        # Flood fill from corners
        cv2.floodFill(inv, mask, (0, 0), 0)
        # inv now contains only internal holes
        binary = binary | inv

        return binary

    # ==================================================================
    # STAGE 4: Boundary detection v2
    # ==================================================================

    def _detect_boundaries_v2(self, gray: np.ndarray, enhanced: np.ndarray,
                               gradient: np.ndarray,
                               params: DetectionParams) -> Optional[np.ndarray]:
        """
        Multi-strategy boundary detection.

        Combines:
        1. Gradient ridges (high gradient = boundary between grains)
        2. Dark valley detection (SEM boundaries are often darker)
        3. Morphological gradient (dilation - erosion = edge)

        Returns thin boundary lines suitable for carving into binary mask.
        """
        if params.edge_sensitivity <= 0:
            return None

        h, w = gray.shape
        sensitivity = params.edge_sensitivity

        # --- Strategy 1: Gradient ridges ---
        # Top percentile of gradient magnitude = boundaries
        grad_pct = max(75, 95 - sensitivity * 10)  # sensitivity=1 -> top 15%
        grad_thresh = np.percentile(gradient, grad_pct)
        grad_mask = (gradient > grad_thresh).astype(np.uint8) * 255

        # --- Strategy 2: Dark valleys (local minima) ---
        local_mean = cv2.GaussianBlur(gray.astype(np.float32), (21, 21), 6.0)
        dark_diff = local_mean - gray.astype(np.float32)
        dark_pct = max(75, 92 - sensitivity * 8)
        dark_thresh = np.percentile(dark_diff, dark_pct)
        dark_mask = (dark_diff > dark_thresh).astype(np.uint8) * 255

        # --- Strategy 3: Morphological gradient ---
        kernel_mg = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        morph_grad = cv2.morphologyEx(enhanced, cv2.MORPH_GRADIENT, kernel_mg)
        mg_thresh = np.percentile(morph_grad, max(75, 90 - sensitivity * 8))
        morph_mask = (morph_grad > mg_thresh).astype(np.uint8) * 255

        # Combine: require at least 2 of 3 strategies to agree
        # This reduces false boundaries while catching real ones
        vote = (grad_mask > 0).astype(np.uint8) + \
               (dark_mask > 0).astype(np.uint8) + \
               (morph_mask > 0).astype(np.uint8)
        combined = (vote >= 2).astype(np.uint8) * 255

        # Thin the boundaries via morphological skeleton approximation
        kernel_thin = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
        thinned = cv2.morphologyEx(combined, cv2.MORPH_ERODE, kernel_thin, iterations=1)

        # Only keep if meaningful
        boundary_frac = np.sum(thinned > 0) / (h * w)
        if boundary_frac < 0.002:
            logger.info(f"Boundary fraction too low ({boundary_frac:.4f}), skipping edge carving")
            return None
        if boundary_frac > 0.15:
            # Too many boundaries = noisy, raise the bar
            logger.info(f"Boundary fraction too high ({boundary_frac:.4f}), tightening")
            thinned = (vote >= 3).astype(np.uint8) * 255
            thinned = cv2.morphologyEx(thinned, cv2.MORPH_ERODE, kernel_thin, iterations=1)

        logger.info(f"Boundary detection v2: {np.sum(thinned > 0) / (h * w):.3f} coverage")
        return thinned

    # ==================================================================
    # STAGE 6: Watershed on gradient
    # ==================================================================

    def _watershed_on_gradient(self, binary: np.ndarray, gradient: np.ndarray,
                                params: DetectionParams,
                                boundary_mask: Optional[np.ndarray] = None) -> np.ndarray:
        """
        Marker-controlled watershed on the GRADIENT IMAGE.

        Key insight: instead of doing watershed on the distance transform
        (which makes arbitrary cuts), we do it on the gradient magnitude.
        This means watershed lines naturally follow the actual grain
        boundaries visible in the image.

        Seeds come from the distance transform (grain centers), but the
        flooding landscape is the gradient — so cuts land on real edges.
        """
        distance = ndi.distance_transform_edt(binary)

        # Build the landscape for watershed
        # Blend distance-based (traditional) with gradient-based (new)
        bw = params.boundary_weight  # 0 = pure distance, 1 = pure gradient

        # Invert gradient for watershed (low = ridge = boundary)
        grad_for_ws = gradient.copy()
        # Suppress gradient outside the binary mask
        grad_for_ws[~binary] = 0

        # Composite landscape
        dist_norm = distance / max(distance.max(), 1)
        landscape = (1.0 - bw) * (-dist_norm) + bw * grad_for_ws
        # Convert to form where boundaries are HIGH (watershed floods into valleys)
        landscape = -landscape  # now: boundaries=low valleys=high

        # If we have a boundary mask, strengthen it in the landscape
        if boundary_mask is not None:
            edge_float = (boundary_mask > 0).astype(np.float32)
            kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
            edge_dilated = cv2.dilate(edge_float, kern, iterations=1)
            # Push edges further down in the landscape (stronger barrier)
            landscape = landscape - edge_dilated * 0.5

        # Find seed points (grain centers)
        min_dist = max(params.watershed_min_dist, 3)

        # Use distance transform for seeds — these are grain centers
        coords = peak_local_max(
            distance,
            min_distance=min_dist,
            labels=binary,
            exclude_border=False,
        )

        if len(coords) == 0:
            labels, _ = ndi.label(binary)
            return labels

        mask_markers = np.zeros(distance.shape, dtype=bool)
        mask_markers[tuple(coords.T)] = True
        markers, _ = ndi.label(mask_markers)

        # Watershed on the gradient-based landscape
        labels = watershed(landscape.astype(np.float64), markers, mask=binary)

        return labels

    # ==================================================================
    # STAGE 7: Measure grains
    # ==================================================================

    def _measure_grains(self, labels: np.ndarray, params: DetectionParams,
                        px_per_um: float) -> List[GrainResult]:
        """Measure properties of each labeled grain."""
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

        return grains

    # ==================================================================
    # Statistics + overlay
    # ==================================================================

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
                cv2.putText(overlay, text, (tx + 1, ty + 1), cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 0, 0), 2)
                cv2.putText(overlay, text, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 255, 255), 1)
        return overlay
