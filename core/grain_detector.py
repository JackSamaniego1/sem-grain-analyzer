"""
Grain Detection Engine v2.2
============================
Optimized boundary-first detection for SEM polycrystalline grain images.

Key improvements over v2.1:
  - TEXTURE-ADAPTIVE denoising: measures fine/coarse gradient ratio to
    detect internal grain texture (hatching/stripes) and applies bilateral
    filter at appropriate strength — light for clean images, aggressive
    for heavily textured ones
  - DIFFERENCE-OF-GAUSSIANS (DoG) boundary signal: isolates features at
    the grain-boundary spatial scale, robust against periodic texture
  - LAPLACIAN-OF-GAUSSIAN (LoG) for dark valley detection
  - PERCENTILE-BASED thresholding instead of Otsu (boundary scores are
    heavily skewed, Otsu fails on them)
  - DIRECTIONAL GAP CLOSING with oriented kernels at 0/45/90/135 degrees
    to bridge boundary fragments that are nearly aligned
  - AUTO-CROP to remove whitespace/text borders using variance-based
    contiguous content detection
  - ADAPTIVE weight mixing: texture-robust signals (DoG, dark valleys)
    get higher weight when texture is detected
  - More aggressive watershed splitting (2x median threshold)
"""

import numpy as np
import cv2
from scipy import ndimage as ndi
from skimage.segmentation import watershed
from skimage.feature import peak_local_max
from skimage.measure import regionprops
from skimage.morphology import remove_small_objects, skeletonize
from skimage.filters import threshold_otsu, gaussian
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


# ======================================================================
# Data classes (unchanged interface)
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
    use_adaptive: bool = True
    adaptive_block_size: int = 0
    use_clahe: bool = True
    clahe_clip_limit: float = 2.0
    boundary_weight: float = 0.5
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

        # Auto-crop whitespace/text borders
        crop_rect = self._auto_crop(gray)
        if crop_rect is not None:
            r0, c0, r1, c1 = crop_rect
            gray = gray[r0:r1, c0:c1]
            image_bgr = image_bgr[r0:r1, c0:c1]
            logger.info(f"Auto-cropped to {c1-c0}x{r1-r0}")

        # Choose detection mode
        mode = params.detection_mode
        if mode == "auto":
            mode = self._auto_detect_mode(gray)
            logger.info(f"Auto-detected mode: {mode}")

        if mode == "boundary":
            labels, binary = self._boundary_pipeline(gray, image_bgr, params, progress)
        else:
            labels, binary = self._threshold_pipeline(gray, image_bgr, params, progress)

        result.binary_image = binary

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
    # Auto-crop
    # ==================================================================

    def _auto_crop(self, gray: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
        """
        Detect and remove whitespace/text borders around the SEM image.

        Strategy: look for rows/columns that are nearly white (mean > 240).
        Only crop if a significant fraction of the image is white border.
        Uses the longest contiguous run of non-white rows/cols to find
        the SEM content region.
        """
        h, w = gray.shape
        if h < 50 or w < 50:
            return None

        # Check if there's significant whitespace at all
        white_frac = (gray > 240).mean()
        if white_frac < 0.10:
            # Less than 10% white — no border to crop
            return None

        col_means = gray.mean(axis=0)
        row_means = gray.mean(axis=1)

        # A column/row is "white" if its mean > 240
        def longest_dark_run(means, threshold=240):
            """Find the longest contiguous run of non-white values."""
            dark = means < threshold
            runs = []
            start = None
            for i, v in enumerate(dark):
                if v and start is None:
                    start = i
                elif not v and start is not None:
                    runs.append((start, i, i - start))
                    start = None
            if start is not None:
                runs.append((start, len(dark), len(dark) - start))
            return max(runs, key=lambda x: x[2]) if runs else None

        col_run = longest_dark_run(col_means)
        row_run = longest_dark_run(row_means)

        if col_run is None or row_run is None:
            return None

        c0, c1, cw = col_run
        r0, r1, rh = row_run

        # Only crop if we'd remove at least 15% of the image
        crop_area = cw * rh
        orig_area = w * h
        if crop_area >= orig_area * 0.85:
            return None

        # Add small margin
        margin = 2
        r0 = max(0, r0 - margin)
        c0 = max(0, c0 - margin)
        r1 = min(h, r1 + margin)
        c1 = min(w, c1 + margin)

        logger.info(f"Auto-crop: {c0},{r0} to {c1},{r1} (was {w}x{h})")
        return (r0, c0, r1, c1)

    # ==================================================================
    # Auto-detect mode
    # ==================================================================

    def _auto_detect_mode(self, gray: np.ndarray) -> str:
        gray_f = gray.astype(np.float64) / 255.0
        blurred = gaussian(gray_f, sigma=2.0)
        thresh = threshold_otsu(blurred)
        fg_frac = np.mean(blurred > thresh)
        balance = min(fg_frac, 1.0 - fg_frac)
        logger.info(f"Auto-detect: Otsu balance={balance:.3f}")
        return "threshold" if balance < 0.20 else "boundary"

    # ==================================================================
    # Texture measurement
    # ==================================================================

    def _measure_texture(self, gray: np.ndarray) -> float:
        """
        Measure internal grain texture level.
        Returns ratio of fine-scale to coarse-scale gradient energy.
        High ratio (>3.5) = sharp clean boundaries, low internal texture.
        Low ratio (<3.0) = significant internal texture (hatching/stripes).
        """
        gray_f = gray.astype(np.float32)
        fine = cv2.GaussianBlur(gray_f, (0, 0), 1.0)
        coarse = cv2.GaussianBlur(gray_f, (0, 0), 4.0)
        fine_grad = np.abs(cv2.Sobel(fine, cv2.CV_32F, 1, 0, ksize=3)) + \
                    np.abs(cv2.Sobel(fine, cv2.CV_32F, 0, 1, ksize=3))
        coarse_grad = np.abs(cv2.Sobel(coarse, cv2.CV_32F, 1, 0, ksize=3)) + \
                      np.abs(cv2.Sobel(coarse, cv2.CV_32F, 0, 1, ksize=3))
        ratio = fine_grad.mean() / max(coarse_grad.mean(), 1e-6)
        logger.info(f"Texture ratio: {ratio:.2f}")
        return ratio

    def _adaptive_denoise(self, gray: np.ndarray, texture_ratio: float) -> np.ndarray:
        """Apply bilateral filter with strength adapted to texture level."""
        if texture_ratio > 3.5:
            return cv2.bilateralFilter(gray, d=5, sigmaColor=30, sigmaSpace=5)
        elif texture_ratio > 2.8:
            return cv2.bilateralFilter(gray, d=9, sigmaColor=40, sigmaSpace=9)
        else:
            return cv2.bilateralFilter(gray, d=15, sigmaColor=50, sigmaSpace=15)

    # ==================================================================
    # PIPELINE A: Boundary-first (optimized v2.2)
    # ==================================================================

    def _boundary_pipeline(self, gray, image_bgr, params, progress):
        h, w = gray.shape

        progress(5, "Measuring texture level...")
        texture_ratio = self._measure_texture(gray)

        progress(8, "Enhancing contrast (CLAHE)...")
        if params.use_clahe:
            clahe = cv2.createCLAHE(
                clipLimit=params.clahe_clip_limit, tileGridSize=(8, 8)
            )
            enhanced = clahe.apply(gray)
        else:
            enhanced = gray.copy()

        progress(12, "Adaptive denoising...")
        denoised = self._adaptive_denoise(enhanced, texture_ratio)
        df = denoised.astype(np.float32) / 255.0
        df_raw = denoised.astype(np.float32)

        # ---- Boundary detection signals ----
        progress(18, "Boundary signal: Difference of Gaussians...")
        dog = self._detect_dog(df)

        progress(25, "Boundary signal: Laplacian of Gaussian...")
        neg_lap = self._detect_log(df)

        progress(32, "Boundary signal: dark valleys...")
        dark_scores = self._detect_dark_valleys(df_raw)

        progress(38, "Boundary signal: gradient magnitude...")
        grad = self._detect_gradient(df)

        progress(42, "Boundary signal: Hessian ridges...")
        ridge = self._detect_ridges(df)

        # ---- Fuse signals ----
        progress(48, "Fusing boundary signals...")
        combo = self._fuse_signals(
            dog, neg_lap, dark_scores, grad, ridge,
            texture_ratio, params.edge_sensitivity
        )

        # ---- Threshold + close gaps ----
        progress(54, "Thresholding boundaries...")
        binary_bound = self._threshold_boundary_map(
            combo, texture_ratio, params.threshold_offset
        )

        progress(58, "Closing boundary gaps...")
        final_boundary = self._close_gaps(binary_bound, texture_ratio)

        grain_mask = (final_boundary == 0).astype(np.uint8)

        # ---- Label + filter ----
        progress(63, "Labeling grain regions...")
        labels, _ = ndi.label(grain_mask)

        min_sz = max(params.min_grain_size_px, 20)
        for r in regionprops(labels):
            if r.area < min_sz:
                labels[labels == r.label] = 0
            if params.max_grain_size_px > 0 and r.area > params.max_grain_size_px:
                labels[labels == r.label] = 0

        # ---- Watershed refinement ----
        progress(68, "Splitting merged grains (watershed)...")
        if params.use_watershed:
            labels = self._watershed_split(
                labels, combo, grad, params
            )

        # ---- Relabel consecutively ----
        unique = np.unique(labels)
        unique = unique[unique > 0]
        new_labels = np.zeros_like(labels)
        for i, lbl in enumerate(unique, 1):
            new_labels[labels == lbl] = i

        return new_labels, grain_mask * 255

    # ------------------------------------------------------------------
    # Boundary detection signals
    # ------------------------------------------------------------------

    def _detect_dog(self, df: np.ndarray) -> np.ndarray:
        """Difference of Gaussians — isolates boundary-scale features."""
        fine = cv2.GaussianBlur(df, (0, 0), 1.0)
        coarse = cv2.GaussianBlur(df, (0, 0), 5.0)
        dog = np.abs(fine - coarse)
        dmax = dog.max()
        return dog / dmax if dmax > 0 else dog

    def _detect_log(self, df: np.ndarray) -> np.ndarray:
        """Laplacian of Gaussian — detects dark valleys (boundary grooves)."""
        blurred = cv2.GaussianBlur(df, (0, 0), 3.0)
        lap = cv2.Laplacian(blurred, cv2.CV_32F)
        neg_lap = np.clip(-lap, 0, None)
        nmax = neg_lap.max()
        return neg_lap / nmax if nmax > 0 else neg_lap

    def _detect_dark_valleys(self, df_raw: np.ndarray) -> np.ndarray:
        """Multi-scale local darkness detector."""
        scores = np.zeros_like(df_raw)
        for ksize in [11, 21, 41]:
            local_mean = cv2.GaussianBlur(df_raw, (ksize, ksize), ksize / 4.0)
            diff = np.clip(local_mean - df_raw, 0, None)
            scores += diff
        smax = scores.max()
        return scores / smax if smax > 0 else scores

    def _detect_gradient(self, df: np.ndarray) -> np.ndarray:
        """Multi-scale Sobel gradient magnitude."""
        gx = cv2.Sobel(df, cv2.CV_32F, 1, 0, ksize=5)
        gy = cv2.Sobel(df, cv2.CV_32F, 0, 1, ksize=5)
        mag = np.sqrt(gx ** 2 + gy ** 2)
        mmax = mag.max()
        return mag / mmax if mmax > 0 else mag

    def _detect_ridges(self, df: np.ndarray) -> np.ndarray:
        """Hessian-based ridge/valley detector for thin dark lines."""
        smooth = cv2.GaussianBlur(df, (0, 0), 1.5)
        dxx = cv2.Sobel(smooth, cv2.CV_32F, 2, 0, ksize=5)
        dyy = cv2.Sobel(smooth, cv2.CV_32F, 0, 2, ksize=5)
        dxy = cv2.Sobel(smooth, cv2.CV_32F, 1, 1, ksize=5)
        trace = dxx + dyy
        det = dxx * dyy - dxy * dxy
        discriminant = np.sqrt(np.clip(trace ** 2 - 4 * det, 0, None))
        lambda1 = 0.5 * (trace + discriminant)
        ridge = np.clip(lambda1, 0, None)
        rmax = ridge.max()
        return ridge / rmax if rmax > 0 else ridge

    # ------------------------------------------------------------------
    # Signal fusion + thresholding + gap closing
    # ------------------------------------------------------------------

    def _fuse_signals(self, dog, neg_lap, dark, grad, ridge,
                      texture_ratio, sensitivity) -> np.ndarray:
        """Weighted combination adapted to texture level."""
        if texture_ratio > 3.5:
            # Clean image: all signals reliable
            combo = (0.25 * dog + 0.20 * neg_lap +
                     0.25 * dark + 0.15 * grad + 0.15 * ridge)
        else:
            # Textured: favor DoG and dark valleys (most robust)
            combo = (0.30 * dog + 0.25 * neg_lap +
                     0.25 * dark + 0.10 * grad + 0.10 * ridge)

        cmax = combo.max()
        if cmax > 0:
            combo /= cmax

        # Nonlinear boost controlled by sensitivity
        combo = np.power(combo, 1.0 / max(sensitivity, 0.3))
        cmax = combo.max()
        if cmax > 0:
            combo /= cmax

        return combo

    def _threshold_boundary_map(self, combo, texture_ratio, thresh_offset):
        """Percentile-based thresholding (Otsu fails on skewed boundary scores)."""
        if texture_ratio > 3.5:
            pct = 82  # clean: detect more boundaries
        else:
            pct = 85  # textured: be more selective

        threshold_val = np.percentile(combo, pct)
        threshold_val += thresh_offset * 0.1
        threshold_val = max(0.01, threshold_val)

        return (combo > threshold_val).astype(np.uint8)

    def _close_gaps(self, binary_bound, texture_ratio):
        """Directional + round gap closing, then skeletonize."""
        # Directional closing at 0°, 45°, 90°, 135°
        length = 9 if texture_ratio <= 3.5 else 7
        for angle in [0, 45, 90, 135]:
            kern = np.zeros((length, length), dtype=np.uint8)
            mid = length // 2
            if angle == 0:
                kern[mid, :] = 1
            elif angle == 90:
                kern[:, mid] = 1
            elif angle == 45:
                for i in range(length):
                    kern[i, i] = 1
            elif angle == 135:
                for i in range(length):
                    kern[i, length - 1 - i] = 1
            binary_bound = cv2.morphologyEx(
                binary_bound, cv2.MORPH_CLOSE, kern
            )

        # Round closing for remaining gaps
        kernel_round = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        binary_bound = cv2.morphologyEx(
            binary_bound, cv2.MORPH_CLOSE, kernel_round
        )

        # Skeletonize to 1px + fatten slightly
        skel = skeletonize(binary_bound > 0)
        kernel_fat = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        final = cv2.dilate(skel.astype(np.uint8), kernel_fat, iterations=1)

        return final

    # ------------------------------------------------------------------
    # Watershed splitting
    # ------------------------------------------------------------------

    def _watershed_split(self, labels, combo, grad, params):
        """Split regions larger than 2x median area."""
        areas = [r.area for r in regionprops(labels)
                 if r.area >= params.min_grain_size_px]
        if len(areas) < 3:
            return labels

        median_area = np.median(areas)
        merge_thresh = median_area * 2.0

        output = labels.copy()
        max_lbl = labels.max()

        for region in regionprops(labels):
            if region.area < merge_thresh:
                continue

            rmask = (labels == region.label)
            r0, c0, r1, c1 = region.bbox
            lmask = rmask[r0:r1, c0:c1]
            lcombo = combo[r0:r1, c0:c1]
            lgrad = grad[r0:r1, c0:c1]

            landscape = (0.5 * lgrad + 0.5 * lcombo)
            lu8 = (landscape * 255).astype(np.uint8)

            dist = ndi.distance_transform_edt(lmask)
            min_d = max(params.watershed_min_dist, 5)
            coords = peak_local_max(
                dist, min_distance=min_d,
                labels=lmask, exclude_border=False
            )

            if len(coords) <= 1:
                continue

            markers = np.zeros_like(lmask, dtype=np.int32)
            for i, (r, c) in enumerate(coords, 1):
                markers[r, c] = i

            sub = watershed(lu8, markers, mask=lmask)
            sub_r = regionprops(sub)

            if (len(sub_r) > 1 and
                    all(sr.area >= params.min_grain_size_px for sr in sub_r)):
                for sr in sub_r:
                    max_lbl += 1
                    fm = np.zeros_like(labels, dtype=bool)
                    fm[r0:r1, c0:c1] = (sub == sr.label)
                    output[fm] = max_lbl

        return output

    # ==================================================================
    # PIPELINE B: Threshold-based (backward compatible)
    # ==================================================================

    def _threshold_pipeline(self, gray, image_bgr, params, progress):
        h, w = gray.shape

        progress(5, "Enhancing contrast...")
        if params.use_clahe:
            clahe = cv2.createCLAHE(
                clipLimit=params.clahe_clip_limit, tileGridSize=(8, 8))
            enhanced = clahe.apply(gray)
        else:
            enhanced = gray.copy()

        if params.blur_sigma > 0:
            blurred = cv2.GaussianBlur(enhanced, (0, 0), params.blur_sigma)
        else:
            blurred = enhanced

        progress(15, "Applying threshold...")
        gray_float = blurred.astype(np.float64) / 255.0
        blurred_g = gaussian(gray_float, sigma=max(params.blur_sigma, 0.5))
        thresh_val = threshold_otsu(blurred_g)
        thresh_val = float(np.clip(
            thresh_val + params.threshold_offset, 0.01, 0.99))

        if params.dark_grains:
            binary_otsu = (blurred_g < thresh_val).astype(np.uint8)
        else:
            binary_otsu = (blurred_g > thresh_val).astype(np.uint8)

        if params.use_adaptive:
            progress(22, "Adaptive thresholding...")
            block = max(11, int(min(h, w) / 15))
            if block % 2 == 0:
                block += 1
            c_value = 5 + int(params.threshold_offset * 30)
            thresh_type = (cv2.THRESH_BINARY_INV if params.dark_grains
                           else cv2.THRESH_BINARY)
            adaptive = cv2.adaptiveThreshold(
                blurred, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                thresh_type, block, c_value)
            binary = cv2.bitwise_or(binary_otsu * 255, adaptive)
            binary = (binary > 0).astype(np.uint8)
        else:
            binary = binary_otsu

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

        progress(40, "Removing debris...")
        if params.min_grain_size_px > 0:
            binary_bool = remove_small_objects(
                binary_bool, min_size=params.min_grain_size_px)

        progress(50, "Watershed segmentation...")
        if params.use_watershed:
            distance = ndi.distance_transform_edt(binary_bool)
            min_dist = max(params.watershed_min_dist, 3)
            coords = peak_local_max(
                distance, min_distance=min_dist,
                labels=binary_bool, exclude_border=False)
            if len(coords) > 0:
                markers = np.zeros(distance.shape, dtype=bool)
                markers[tuple(coords.T)] = True
                markers_labeled, _ = ndi.label(markers)
                labels = watershed(
                    -distance, markers_labeled, mask=binary_bool)
            else:
                labels, _ = ndi.label(binary_bool)
        else:
            labels, _ = ndi.label(binary_bool)

        return labels, binary_bool.astype(np.uint8) * 255

    # ==================================================================
    # Measurement
    # ==================================================================

    def _measure_grains(self, labels, params, px_per_um):
        regions = regionprops(labels)
        grains = []

        for region in regions:
            if region.area < max(params.min_grain_size_px, 5):
                continue
            if (params.max_grain_size_px > 0 and
                    region.area > params.max_grain_size_px):
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

            circularity = min(
                (4 * np.pi * area_px) / (perim_px ** 2), 1.0)
            major_ax = region.axis_major_length
            minor_ax = region.axis_minor_length
            aspect = (major_ax / minor_ax) if minor_ax > 0 else 1.0
            cy, cx = region.centroid

            grains.append(GrainResult(
                grain_id=region.label,
                area_px=area_px, area_um2=area_um2,
                perimeter_px=perim_px, perimeter_um=perim_um,
                equivalent_diameter_px=eq_diam_px,
                equivalent_diameter_um=eq_diam_um,
                major_axis_um=major_um, minor_axis_um=minor_um,
                aspect_ratio=float(aspect),
                circularity=float(circularity),
                eccentricity=float(region.eccentricity),
                centroid_x=float(cx), centroid_y=float(cy),
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
            diameters = np.array(
                [g.equivalent_diameter_um for g in result.grains])
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
            result.grain_coverage_pct = (
                float(np.sum(areas)) / total_img_um2 * 100.0)
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
                grain_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(overlay, contours, -1, color, 1)
            cx, cy = int(grain.centroid_x), int(grain.centroid_y)
            if 0 <= cx < overlay.shape[1] and 0 <= cy < overlay.shape[0]:
                text = str(grain.grain_id)
                fs = 0.35
                (tw, th), _ = cv2.getTextSize(
                    text, cv2.FONT_HERSHEY_SIMPLEX, fs, 1)
                tx = max(0, cx - tw // 2)
                ty = max(th, cy + th // 2)
                cv2.putText(
                    overlay, text, (tx + 1, ty + 1),
                    cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 0, 0), 2)
                cv2.putText(
                    overlay, text, (tx, ty),
                    cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 255, 255), 1)
        return overlay
