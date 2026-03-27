"""
Grain Detection Engine v2.1
============================
BOUNDARY-FIRST paradigm for SEM polycrystalline grain images.

Why previous approaches failed:
  - Otsu/adaptive thresholding assumes "bright grains on dark background"
  - But SEM grain images are a MOSAIC — the entire image is grains
  - Grains vary in brightness due to crystallographic orientation
  - The only consistent signal is the thin DARK BOUNDARY GROOVES between grains

New paradigm:
  1. Find the BOUNDARY NETWORK (dark grooves between grains)
  2. Make boundaries into a connected skeleton
  3. Everything BETWEEN boundaries = a separate grain
  4. Use watershed on gradient to refine where boundaries are ambiguous

The pipeline:
  STEP 1: Preprocess (CLAHE + denoise)
  STEP 2: Multi-strategy boundary detection:
          a) Local darkness (pixels darker than their neighborhood)
          b) Multi-scale gradient magnitude (edges at any contrast)
          c) Morphological gradient (dilation - erosion)
          d) Ridge/valley filter (Hessian-based dark line detector)
  STEP 3: Fuse boundary votes + close gaps via morphology
  STEP 4: Thin to 1px skeleton
  STEP 5: Label connected regions (grains) between boundary lines
  STEP 6: Watershed refinement on gradient to fix leaks
  STEP 7: Filter by size, measure properties
"""

import numpy as np
import cv2
from scipy import ndimage as ndi
from skimage.segmentation import watershed
from skimage.feature import peak_local_max
from skimage.measure import regionprops
from skimage.morphology import (
    remove_small_objects, skeletonize, remove_small_holes,
    disk, square
)
from skimage.filters import threshold_otsu, gaussian, sobel
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


# ======================================================================
# Data classes (unchanged interface — compatible with rest of codebase)
# ======================================================================

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
    # --- v2.0+ params ---
    use_adaptive: bool = True
    adaptive_block_size: int = 0
    use_clahe: bool = True
    clahe_clip_limit: float = 2.0
    boundary_weight: float = 0.5
    # --- v2.1 boundary-first params ---
    detection_mode: str = "auto"  # "auto", "boundary", "threshold"


# ======================================================================
# Main detector class
# ======================================================================

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

        progress(2, "Preprocessing...")
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape

        # Decide which mode to use
        mode = params.detection_mode
        if mode == "auto":
            mode = self._auto_detect_mode(gray)
            logger.info(f"Auto-detected mode: {mode}")

        if mode == "boundary":
            labels, binary = self._boundary_first_pipeline(
                gray, image_bgr, params, progress
            )
        else:
            labels, binary = self._threshold_pipeline(
                gray, image_bgr, params, progress
            )

        result.binary_image = binary

        # ---- Measure grains ----
        progress(75, "Measuring grain properties...")
        grains = self._measure_grains(labels, params, px_per_um)

        progress(85, "Computing statistics...")
        result.grains = grains
        result.grain_count = len(grains)
        result.label_image = labels
        result = self._compute_statistics(result, image_bgr)

        progress(92, "Generating overlay...")
        result.overlay_image = self._draw_overlay(image_bgr, labels, grains)

        progress(100, f"Complete — {result.grain_count} grains detected.")
        self._last_result = result
        return result

    # ==================================================================
    # Auto-detect: is this a "grains on background" or "mosaic" image?
    # ==================================================================

    def _auto_detect_mode(self, gray: np.ndarray) -> str:
        """
        Determine if the image is:
        - "threshold" mode: distinct grains on a different-colored background
          (e.g. bright grains on dark, or dark grains on bright)
        - "boundary" mode: grains cover the entire field of view as a mosaic
          separated only by thin boundary grooves

        Heuristic: compute Otsu threshold, check what fraction of pixels
        fall in each class. If it's very lopsided (>80% one class),
        it's a mosaic — use boundary mode.
        """
        gray_f = gray.astype(np.float64) / 255.0
        blurred = gaussian(gray_f, sigma=2.0)
        thresh = threshold_otsu(blurred)
        fg_frac = np.mean(blurred > thresh)
        # If one class dominates, it's a mosaic
        balance = min(fg_frac, 1.0 - fg_frac)
        logger.info(f"Auto-detect: Otsu balance = {balance:.3f} (fg={fg_frac:.3f})")
        if balance < 0.20:
            # Very lopsided — one class >80%. Classic foreground/background.
            return "threshold"
        else:
            # Roughly balanced or nearly all one class with subtle variation
            # → mosaic grain image, use boundary detection
            return "boundary"

    # ==================================================================
    # PIPELINE A: Boundary-first (for mosaic grain images)
    # ==================================================================

    def _boundary_first_pipeline(self, gray, image_bgr, params, progress):
        """
        Detect grains by finding the BOUNDARY NETWORK first.
        This is the correct approach when grains fill the entire FOV.
        """
        h, w = gray.shape

        # ---- Step 1: Preprocess ----
        progress(5, "Enhancing contrast (CLAHE)...")
        if params.use_clahe:
            clahe = cv2.createCLAHE(
                clipLimit=params.clahe_clip_limit, tileGridSize=(8, 8)
            )
            enhanced = clahe.apply(gray)
        else:
            enhanced = gray.copy()

        # Light denoise to suppress texture noise but keep boundary grooves
        if params.blur_sigma > 0:
            denoised = cv2.GaussianBlur(enhanced, (0, 0), params.blur_sigma)
        else:
            denoised = enhanced
        denoised_f = denoised.astype(np.float32) / 255.0

        # ---- Step 2: Multi-strategy boundary detection ----
        progress(12, "Detecting boundaries: local darkness...")
        dark_score = self._detect_dark_valleys(denoised, params)

        progress(20, "Detecting boundaries: gradient magnitude...")
        grad_score = self._detect_gradient_boundaries(denoised_f, params)

        progress(28, "Detecting boundaries: morphological edges...")
        morph_score = self._detect_morph_gradient(denoised, params)

        progress(35, "Detecting boundaries: ridge filter...")
        ridge_score = self._detect_ridges(denoised_f, params)

        # ---- Step 3: Fuse boundary signals ----
        progress(42, "Fusing boundary signals...")
        boundary_map = self._fuse_boundary_signals(
            dark_score, grad_score, morph_score, ridge_score, params
        )

        # ---- Step 4: Create closed boundary network ----
        progress(50, "Closing boundary gaps...")
        boundary_binary = self._close_boundary_gaps(boundary_map, params)

        # Binary image: 1 = grain interior, 0 = boundary
        grain_mask = (boundary_binary == 0).astype(np.uint8)

        # ---- Step 5: Label grains between boundaries ----
        progress(57, "Labeling grain regions...")
        labels, _ = ndi.label(grain_mask)

        # Remove tiny fragments (noise between close boundaries)
        progress(62, "Removing fragments...")
        min_sz = max(params.min_grain_size_px, 20)
        for region in regionprops(labels):
            if region.area < min_sz:
                labels[labels == region.label] = 0

        # ---- Step 6: Watershed refinement ----
        progress(67, "Watershed refinement on gradient...")
        if params.use_watershed:
            labels = self._watershed_refine_on_gradient(
                labels, grain_mask, denoised_f, boundary_map, params
            )

        # Re-filter after watershed
        for region in regionprops(labels):
            if region.area < min_sz:
                labels[labels == region.label] = 0
            if params.max_grain_size_px > 0 and region.area > params.max_grain_size_px:
                labels[labels == region.label] = 0

        # Relabel consecutively
        unique = np.unique(labels)
        unique = unique[unique > 0]
        new_labels = np.zeros_like(labels)
        for i, lbl in enumerate(unique, 1):
            new_labels[labels == lbl] = i
        labels = new_labels

        return labels, grain_mask * 255

    # ------------------------------------------------------------------
    # Boundary detection sub-strategies
    # ------------------------------------------------------------------

    def _detect_dark_valleys(self, gray: np.ndarray,
                             params: DetectionParams) -> np.ndarray:
        """
        Find pixels that are darker than their local neighborhood.
        Grain boundaries in SEM are typically dark grooves.
        Returns float score 0-1 (higher = more likely boundary).
        """
        gray_f = gray.astype(np.float32)

        # Multi-scale local mean comparison
        scores = np.zeros_like(gray_f)
        for ksize in [11, 21, 41]:
            local_mean = cv2.GaussianBlur(gray_f, (ksize, ksize), ksize / 4.0)
            diff = local_mean - gray_f  # positive = pixel darker than neighborhood
            diff = np.clip(diff, 0, None)
            scores += diff

        # Normalize to 0-1
        smax = scores.max()
        if smax > 0:
            scores /= smax

        return scores

    def _detect_gradient_boundaries(self, gray_f: np.ndarray,
                                     params: DetectionParams) -> np.ndarray:
        """
        Multi-scale Sobel gradient magnitude.
        High gradient = transition between grains = boundary.
        """
        scores = np.zeros_like(gray_f)

        for ksize in [3, 5, 7]:
            gx = cv2.Sobel(gray_f, cv2.CV_32F, 1, 0, ksize=ksize)
            gy = cv2.Sobel(gray_f, cv2.CV_32F, 0, 1, ksize=ksize)
            mag = np.sqrt(gx ** 2 + gy ** 2)
            mmax = mag.max()
            if mmax > 0:
                mag /= mmax
            scores += mag

        scores /= 3.0  # average across scales
        return scores

    def _detect_morph_gradient(self, gray: np.ndarray,
                                params: DetectionParams) -> np.ndarray:
        """
        Morphological gradient: dilation - erosion.
        Highlights transitions; thick at boundaries, zero in uniform regions.
        """
        scores = np.zeros(gray.shape, dtype=np.float32)

        for ksize in [3, 5]:
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ksize, ksize))
            grad = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, kernel)
            g_f = grad.astype(np.float32)
            gmax = g_f.max()
            if gmax > 0:
                g_f /= gmax
            scores += g_f

        scores /= 2.0
        return scores

    def _detect_ridges(self, gray_f: np.ndarray,
                       params: DetectionParams) -> np.ndarray:
        """
        Hessian-based ridge/valley detector.
        Detects thin dark LINES (grain boundary grooves).
        Uses the eigenvalues of the Hessian matrix:
        a dark line has one large positive eigenvalue.
        """
        # Smooth slightly to suppress texture
        smooth = cv2.GaussianBlur(gray_f, (0, 0), 1.5)

        # Hessian components
        dxx = cv2.Sobel(smooth, cv2.CV_32F, 2, 0, ksize=5)
        dyy = cv2.Sobel(smooth, cv2.CV_32F, 0, 2, ksize=5)
        dxy = cv2.Sobel(smooth, cv2.CV_32F, 1, 1, ksize=5)

        # Eigenvalues of 2x2 Hessian at each pixel
        # lambda1, lambda2 = 0.5 * (dxx+dyy +/- sqrt((dxx-dyy)^2 + 4*dxy^2))
        trace = dxx + dyy
        det = dxx * dyy - dxy * dxy
        discriminant = np.sqrt(np.clip(trace ** 2 - 4 * det, 0, None))

        lambda1 = 0.5 * (trace + discriminant)

        # For dark valleys (boundary grooves), the larger eigenvalue is positive
        # (concave up = valley in at least one direction)
        ridge_strength = np.clip(lambda1, 0, None)

        rmax = ridge_strength.max()
        if rmax > 0:
            ridge_strength /= rmax

        return ridge_strength

    # ------------------------------------------------------------------
    # Fuse + close boundaries
    # ------------------------------------------------------------------

    def _fuse_boundary_signals(self, dark_score, grad_score, morph_score,
                                ridge_score, params) -> np.ndarray:
        """
        Combine all boundary signals into one probability map.
        Uses weighted sum + nonlinear boost.
        """
        sensitivity = params.edge_sensitivity

        # Weighted combination — ridge and dark valleys are most reliable
        # for SEM grain boundaries
        combined = (
            0.30 * dark_score +
            0.25 * grad_score +
            0.15 * morph_score +
            0.30 * ridge_score
        )

        # Nonlinear boost: push strong signals higher, suppress weak ones
        combined = np.power(combined, 1.0 / max(sensitivity, 0.3))

        # Normalize
        cmax = combined.max()
        if cmax > 0:
            combined /= cmax

        return combined

    def _close_boundary_gaps(self, boundary_map: np.ndarray,
                              params: DetectionParams) -> np.ndarray:
        """
        Convert the continuous boundary probability map into a clean,
        connected binary boundary network.

        Key challenge: boundaries must be CLOSED loops to separate grains.
        If there are gaps, grains merge. So we use aggressive gap-closing.
        """
        h, w = boundary_map.shape
        sensitivity = params.edge_sensitivity

        # Threshold the boundary map
        # Use Otsu on the boundary scores to find natural cutoff
        bmap_uint8 = (boundary_map * 255).astype(np.uint8)
        otsu_val, _ = cv2.threshold(bmap_uint8, 0, 255, cv2.THRESH_OTSU)

        # Adjust threshold by sensitivity
        # Lower threshold = more boundary pixels = more separation
        thresh_val = otsu_val * (1.3 - 0.3 * sensitivity)
        thresh_val = max(10, min(200, thresh_val))

        # Apply threshold offset from user params
        thresh_val = thresh_val + params.threshold_offset * 100
        thresh_val = max(5, min(220, thresh_val))

        boundary_binary = (bmap_uint8 > thresh_val).astype(np.uint8)

        # ---- Gap closing strategy ----
        # 1. Dilate to connect nearby boundary fragments
        k_close = max(3, int(3 * sensitivity))
        if k_close % 2 == 0:
            k_close += 1
        kernel_close = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (k_close, k_close)
        )
        closed = cv2.morphologyEx(boundary_binary, cv2.MORPH_CLOSE, kernel_close)

        # 2. Thin back down so boundaries don't eat grain area
        #    Use skimage skeletonize for true 1-pixel skeleton
        skeleton = skeletonize(closed > 0)

        # 3. Dilate skeleton slightly (1-2px) to ensure it's connected
        #    and acts as a proper barrier
        kernel_fatten = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        boundary_final = cv2.dilate(
            skeleton.astype(np.uint8), kernel_fatten, iterations=1
        )

        return boundary_final

    # ------------------------------------------------------------------
    # Watershed refinement
    # ------------------------------------------------------------------

    def _watershed_refine_on_gradient(self, labels, grain_mask, gray_f,
                                      boundary_map, params):
        """
        Use watershed on the gradient image to fix places where
        boundaries had gaps (grains that leaked into each other).

        Only applies to suspiciously large regions that might be
        multiple merged grains.
        """
        # Compute expected grain size from existing labels
        areas = []
        for region in regionprops(labels):
            if region.area >= params.min_grain_size_px:
                areas.append(region.area)

        if len(areas) < 3:
            return labels

        median_area = np.median(areas)
        # Regions larger than 3x median are suspicious
        merge_threshold = median_area * 3.0

        # Build gradient landscape for watershed
        gradient = self._compute_gradient_for_watershed(gray_f)

        output_labels = labels.copy()
        max_label = labels.max()

        for region in regionprops(labels):
            if region.area < merge_threshold:
                continue

            # This region is suspiciously large — try to split it
            region_mask = (labels == region.label)
            r0, c0, r1, c1 = region.bbox

            # Extract local patch
            local_mask = region_mask[r0:r1, c0:c1]
            local_grad = gradient[r0:r1, c0:c1]
            local_bmap = boundary_map[r0:r1, c0:c1]

            # Use gradient + boundary map as watershed landscape
            landscape = 0.5 * local_grad + 0.5 * local_bmap
            landscape_u8 = (landscape * 255).astype(np.uint8)

            # Find seeds via distance transform of the mask
            dist = ndi.distance_transform_edt(local_mask)
            min_dist = max(params.watershed_min_dist, 5)
            coords = peak_local_max(
                dist, min_distance=min_dist,
                labels=local_mask, exclude_border=False
            )

            if len(coords) <= 1:
                # Can't split further
                continue

            # Create markers
            markers = np.zeros_like(local_mask, dtype=np.int32)
            for i, (r, c) in enumerate(coords, 1):
                markers[r, c] = i

            # Watershed
            sub_labels = watershed(
                landscape_u8, markers, mask=local_mask
            )

            # Check if the split produced reasonably sized pieces
            sub_regions = regionprops(sub_labels)
            all_valid = all(
                sr.area >= params.min_grain_size_px
                for sr in sub_regions
            )

            if all_valid and len(sub_regions) > 1:
                # Accept the split
                for sr in sub_regions:
                    max_label += 1
                    sub_mask = (sub_labels == sr.label)
                    full_mask = np.zeros_like(labels, dtype=bool)
                    full_mask[r0:r1, c0:c1] = sub_mask
                    output_labels[full_mask] = max_label

        return output_labels

    def _compute_gradient_for_watershed(self, gray_f):
        """Compute a clean gradient magnitude map for watershed."""
        gx = cv2.Sobel(gray_f, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray_f, cv2.CV_32F, 0, 1, ksize=3)
        mag = np.sqrt(gx ** 2 + gy ** 2)
        mmax = mag.max()
        if mmax > 0:
            mag /= mmax
        return mag

    # ==================================================================
    # PIPELINE B: Threshold-based (for grains-on-background images)
    # ==================================================================

    def _threshold_pipeline(self, gray, image_bgr, params, progress):
        """
        Classic approach for images with distinct foreground/background.
        Kept for backward compatibility with sparse grain images.
        """
        h, w = gray.shape

        progress(5, "Enhancing contrast...")
        if params.use_clahe:
            clahe = cv2.createCLAHE(
                clipLimit=params.clahe_clip_limit, tileGridSize=(8, 8)
            )
            enhanced = clahe.apply(gray)
        else:
            enhanced = gray.copy()

        if params.blur_sigma > 0:
            blurred = cv2.GaussianBlur(enhanced, (0, 0), params.blur_sigma)
        else:
            blurred = enhanced

        # Otsu threshold
        progress(15, "Applying threshold...")
        gray_float = blurred.astype(np.float64) / 255.0
        blurred_g = gaussian(gray_float, sigma=max(params.blur_sigma, 0.5))
        thresh_val = threshold_otsu(blurred_g)
        thresh_val = float(np.clip(thresh_val + params.threshold_offset, 0.01, 0.99))

        if params.dark_grains:
            binary_otsu = (blurred_g < thresh_val).astype(np.uint8)
        else:
            binary_otsu = (blurred_g > thresh_val).astype(np.uint8)

        # Adaptive threshold
        if params.use_adaptive:
            progress(22, "Adaptive thresholding...")
            block = max(11, int(min(h, w) / 15))
            if block % 2 == 0:
                block += 1
            c_value = 5 + int(params.threshold_offset * 30)

            if params.dark_grains:
                thresh_type = cv2.THRESH_BINARY_INV
            else:
                thresh_type = cv2.THRESH_BINARY

            adaptive = cv2.adaptiveThreshold(
                blurred, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                thresh_type, block, c_value
            )
            binary = cv2.bitwise_or(binary_otsu * 255, adaptive)
            binary = (binary > 0).astype(np.uint8)
        else:
            binary = binary_otsu

        # Morphological cleanup
        progress(30, "Morphological cleanup...")
        if params.morph_close_size > 0:
            k = params.morph_close_size * 2 + 1
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
            binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
        if params.morph_open_size > 0:
            k = params.morph_open_size * 2 + 1
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
            binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

        binary_bool = binary.astype(bool)

        # Remove small objects
        progress(40, "Removing debris...")
        if params.min_grain_size_px > 0:
            binary_bool = remove_small_objects(
                binary_bool, min_size=params.min_grain_size_px
            )

        # Watershed
        progress(50, "Watershed segmentation...")
        if params.use_watershed:
            distance = ndi.distance_transform_edt(binary_bool)
            min_dist = max(params.watershed_min_dist, 3)
            coords = peak_local_max(
                distance, min_distance=min_dist,
                labels=binary_bool, exclude_border=False
            )
            if len(coords) > 0:
                markers = np.zeros(distance.shape, dtype=bool)
                markers[tuple(coords.T)] = True
                markers_labeled, _ = ndi.label(markers)
                labels = watershed(-distance, markers_labeled, mask=binary_bool)
            else:
                labels, _ = ndi.label(binary_bool)
        else:
            labels, _ = ndi.label(binary_bool)

        return labels, binary_bool.astype(np.uint8) * 255

    # ==================================================================
    # Measurement
    # ==================================================================

    def _measure_grains(self, labels, params, px_per_um):
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
    # Statistics + overlay (unchanged interface)
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
            contours, _ = cv2.findContours(
                grain_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            cv2.drawContours(overlay, contours, -1, color, 1)
            cx, cy = int(grain.centroid_x), int(grain.centroid_y)
            if 0 <= cx < overlay.shape[1] and 0 <= cy < overlay.shape[0]:
                text = str(grain.grain_id)
                fs = 0.35
                (tw, th), _ = cv2.getTextSize(
                    text, cv2.FONT_HERSHEY_SIMPLEX, fs, 1
                )
                tx = max(0, cx - tw // 2)
                ty = max(th, cy + th // 2)
                cv2.putText(
                    overlay, text, (tx + 1, ty + 1),
                    cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 0, 0), 2
                )
                cv2.putText(
                    overlay, text, (tx, ty),
                    cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 255, 255), 1
                )
        return overlay
