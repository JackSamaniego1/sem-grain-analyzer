"""
Grain Detection Engine v2.4
============================
Two-pass architecture: contrast drives segmentation, texture refines it.

PASS 1 — Contrast-based watershed:
  Builds a boundary probability map from 5 contrast/intensity signals
  (DoG, LoG, dark valleys, step change, gradient). Finds grain centers
  as local minima of boundary signal. Runs marker-controlled watershed
  on the boundary map. This handles all grains with visible intensity
  differences at their boundaries.

PASS 2 — Texture orientation split:
  For regions larger than 2x median grain area (likely merged grains),
  computes the structure tensor on the raw image to detect where
  hatching/stripe direction changes. Uses this as an additional
  watershed landscape to split merged grains that have similar
  brightness but different crystallographic orientation.

Key design principle: texture orientation can only ADD splits to the
contrast-based result, never override it. This prevents over-segmentation
on clean images while still catching subtle boundaries on textured ones.
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
    threshold_offset: float = -0.1
    min_grain_size_px: int = 50
    max_grain_size_px: int = 0
    watershed_min_dist: int = 5
    dark_grains: bool = False
    use_watershed: bool = True
    morph_close_size: int = 3
    morph_open_size: int = 2
    edge_sensitivity: float = 1.5
    use_adaptive: bool = True
    adaptive_block_size: int = 0
    use_clahe: bool = True
    clahe_clip_limit: float = 2.0
    boundary_weight: float = 0.5
    detection_mode: str = "auto"


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

        crop_rect = self._auto_crop(gray)
        if crop_rect is not None:
            r0, c0, r1, c1 = crop_rect
            gray = gray[r0:r1, c0:c1]
            image_bgr = image_bgr[r0:r1, c0:c1]

        mode = params.detection_mode
        if mode == "auto":
            mode = self._auto_detect_mode(gray)

        if mode == "boundary":
            labels, binary = self._boundary_pipeline(
                gray, image_bgr, params, progress)
        else:
            labels, binary = self._threshold_pipeline(
                gray, image_bgr, params, progress)

        result.binary_image = binary

        progress(78, "Measuring grain properties...")
        grains = self._measure_grains(labels, params, px_per_um)

        progress(88, "Computing statistics...")
        result.grains = grains
        result.grain_count = len(grains)
        result.label_image = labels
        result = self._compute_statistics(result, image_bgr)

        progress(94, "Generating overlay...")
        result.overlay_image = self._draw_overlay(image_bgr, labels, grains)

        progress(100, f"Complete — {result.grain_count} grains detected.")
        self._last_result = result
        return result

    # ==================================================================
    # Auto-crop
    # ==================================================================

    def _auto_crop(self, gray):
        h, w = gray.shape
        if h < 50 or w < 50:
            return None
        if (gray > 240).mean() < 0.10:
            return None

        col_means = gray.mean(axis=0)
        row_means = gray.mean(axis=1)

        def longest_dark_run(means, thresh=240):
            dark = means < thresh
            runs, start = [], None
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
        if cw * rh >= w * h * 0.85:
            return None

        m = 2
        return (max(0, r0 - m), max(0, c0 - m),
                min(h, r1 + m), min(w, c1 + m))

    def _auto_detect_mode(self, gray):
        gray_f = gray.astype(np.float64) / 255.0
        blurred = gaussian(gray_f, sigma=2.0)
        thresh = threshold_otsu(blurred)
        balance = min(np.mean(blurred > thresh), 1.0 - np.mean(blurred > thresh))
        return "threshold" if balance < 0.20 else "boundary"

    # ==================================================================
    # Texture measurement
    # ==================================================================

    def _measure_texture(self, gray):
        gf = gray.astype(np.float32)
        fine = cv2.GaussianBlur(gf, (0, 0), 1.0)
        coarse = cv2.GaussianBlur(gf, (0, 0), 4.0)
        fg = np.abs(cv2.Sobel(fine, cv2.CV_32F, 1, 0, ksize=3)) + \
             np.abs(cv2.Sobel(fine, cv2.CV_32F, 0, 1, ksize=3))
        cg = np.abs(cv2.Sobel(coarse, cv2.CV_32F, 1, 0, ksize=3)) + \
             np.abs(cv2.Sobel(coarse, cv2.CV_32F, 0, 1, ksize=3))
        return fg.mean() / max(cg.mean(), 1e-6)

    # ==================================================================
    # PIPELINE A: Boundary-first, two-pass (v2.4)
    # ==================================================================

    def _boundary_pipeline(self, gray, image_bgr, params, progress):
        h, w = gray.shape

        progress(5, "Measuring texture level...")
        texture_ratio = self._measure_texture(gray)

        progress(8, "CLAHE enhancement...")
        if params.use_clahe:
            clahe = cv2.createCLAHE(
                clipLimit=params.clahe_clip_limit, tileGridSize=(8, 8))
            enhanced = clahe.apply(gray)
        else:
            enhanced = gray.copy()

        progress(12, "Adaptive denoising...")
        if texture_ratio > 3.5:
            bl = cv2.bilateralFilter(enhanced, d=5, sigmaColor=30, sigmaSpace=5)
            bl_med = cv2.bilateralFilter(enhanced, d=9, sigmaColor=40, sigmaSpace=9)
            bl_heavy = cv2.bilateralFilter(enhanced, d=15, sigmaColor=50, sigmaSpace=15)
        else:
            bl = cv2.bilateralFilter(enhanced, d=9, sigmaColor=40, sigmaSpace=9)
            bl_med = cv2.bilateralFilter(enhanced, d=15, sigmaColor=50, sigmaSpace=15)
            bl_heavy = cv2.bilateralFilter(enhanced, d=25, sigmaColor=60, sigmaSpace=25)

        bf = bl.astype(np.float32) / 255.0
        bf_m = bl_med.astype(np.float32) / 255.0
        bf_h = bl_heavy.astype(np.float32) / 255.0

        # ---- PASS 1: Contrast-only boundary signals ----
        progress(18, "Computing contrast boundaries...")

        # DoG
        dog = np.abs(cv2.GaussianBlur(bf, (0, 0), 1.0) -
                     cv2.GaussianBlur(bf, (0, 0), 5.0))
        dog /= max(dog.max(), 1e-6)

        # LoG
        nlap = np.clip(-cv2.Laplacian(
            cv2.GaussianBlur(bf, (0, 0), 3.0), cv2.CV_32F), 0, None)
        nlap /= max(nlap.max(), 1e-6)

        # Dark valleys
        df = bl.astype(np.float32)
        dark = np.zeros_like(df)
        for ks in [11, 21, 41]:
            dark += np.clip(
                cv2.GaussianBlur(df, (ks, ks), ks / 4.0) - df, 0, None)
        dark /= max(dark.max(), 1e-6)

        # Multi-scale step change: medium + heavy bilateral
        # Medium catches moderate contrast; heavy catches broad transitions
        step_scores = np.zeros_like(bf)
        for bf_scale in [bf_m, bf_h]:
            gx_s = cv2.Sobel(bf_scale, cv2.CV_32F, 1, 0, ksize=5)
            gy_s = cv2.Sobel(bf_scale, cv2.CV_32F, 0, 1, ksize=5)
            s = np.sqrt(gx_s ** 2 + gy_s ** 2)
            s /= max(s.max(), 1e-6)
            step_scores = np.maximum(step_scores, s)
        step = step_scores

        # Gradient
        gx = cv2.Sobel(bf, cv2.CV_32F, 1, 0, ksize=5)
        gy = cv2.Sobel(bf, cv2.CV_32F, 0, 1, ksize=5)
        grad = np.sqrt(gx ** 2 + gy ** 2)
        grad /= max(grad.max(), 1e-6)

        progress(30, "Building contrast boundary map...")
        # Increased step weight from 0.20 to 0.25, reduced grad from 0.15 to 0.10
        contrast_combo = (0.25 * dog + 0.15 * nlap + 0.20 * dark +
                          0.30 * step + 0.10 * grad)
        contrast_combo /= max(contrast_combo.max(), 1e-6)

        # Sensitivity boost
        power = 1.0 / max(params.edge_sensitivity, 0.3)
        boosted = np.power(contrast_combo, power)
        boosted /= max(boosted.max(), 1e-6)

        # ---- Watershed on contrast map ----
        progress(40, "Finding grain centers...")
        interior = 1.0 - boosted
        interior_smooth = cv2.GaussianBlur(interior, (0, 0), 8.0)

        min_dist = max(8, min(h, w) // 40)
        thresh_abs = max(0.15, 0.35 + params.threshold_offset)
        coords = peak_local_max(
            interior_smooth, min_distance=min_dist,
            threshold_abs=thresh_abs)

        progress(48, f"Contrast watershed ({len(coords)} seeds)...")
        if len(coords) == 0:
            labels = np.zeros((h, w), dtype=np.int32)
        else:
            markers = np.zeros((h, w), dtype=np.int32)
            for i, (r, c) in enumerate(coords, 1):
                markers[r, c] = i
            labels = watershed(
                (boosted * 255).astype(np.uint8), markers)

        # Filter small/large
        min_sz = max(params.min_grain_size_px, 20)
        for r in regionprops(labels):
            if r.area < min_sz:
                labels[labels == r.label] = 0
            if (params.max_grain_size_px > 0 and
                    r.area > params.max_grain_size_px):
                labels[labels == r.label] = 0

        # ---- PASS 2: Texture orientation split on oversized regions ----
        progress(58, "Computing texture orientation...")
        gf_raw = gray.astype(np.float32) / 255.0
        orient = self._compute_orientation_change(gf_raw)

        progress(65, "Splitting oversized regions by texture...")
        labels = self._texture_split(
            labels, boosted, orient, params)

        # Final filter + relabel
        for r in regionprops(labels):
            if r.area < min_sz:
                labels[labels == r.label] = 0

        unique = np.unique(labels)
        unique = unique[unique > 0]
        new_labels = np.zeros_like(labels)
        for i, lbl in enumerate(unique, 1):
            new_labels[labels == lbl] = i

        binary = (boosted * 255).astype(np.uint8)
        return new_labels, binary

    # ------------------------------------------------------------------
    # Texture orientation detection
    # ------------------------------------------------------------------

    def _compute_orientation_change(self, gf_raw):
        """
        Structure tensor on raw image to detect texture direction changes.
        Only used in pass 2 to split oversized regions.
        """
        gx = cv2.Sobel(gf_raw, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gf_raw, cv2.CV_32F, 0, 1, ksize=3)

        sigma = 20
        Jxx = cv2.GaussianBlur(gx * gx, (0, 0), sigma)
        Jxy = cv2.GaussianBlur(gx * gy, (0, 0), sigma)
        Jyy = cv2.GaussianBlur(gy * gy, (0, 0), sigma)

        angle = 0.5 * np.arctan2(2 * Jxy, Jxx - Jyy)
        trace = Jxx + Jyy
        det = Jxx * Jyy - Jxy * Jxy
        disc = np.sqrt(np.clip(trace ** 2 - 4 * det, 0, None))
        coherence = np.where(trace > 1e-8, disc / (trace + 1e-8), 0)

        cos2a = np.cos(2 * angle)
        sin2a = np.sin(2 * angle)
        dc_x = cv2.Sobel(cos2a, cv2.CV_32F, 1, 0, ksize=5)
        dc_y = cv2.Sobel(cos2a, cv2.CV_32F, 0, 1, ksize=5)
        ds_x = cv2.Sobel(sin2a, cv2.CV_32F, 1, 0, ksize=5)
        ds_y = cv2.Sobel(sin2a, cv2.CV_32F, 0, 1, ksize=5)

        orient_change = np.sqrt(
            dc_x ** 2 + dc_y ** 2 + ds_x ** 2 + ds_y ** 2)
        orient_change *= coherence
        omax = orient_change.max()
        return orient_change / omax if omax > 0 else orient_change

    def _texture_split(self, labels, contrast_map, orient_map, params):
        """Split oversized regions using texture orientation + contrast blend."""
        areas = [r.area for r in regionprops(labels)
                 if r.area >= params.min_grain_size_px]
        if len(areas) < 3:
            return labels

        median_area = np.median(areas)
        merge_thresh = median_area * 2.0

        # Blend contrast + orientation for split landscape
        power = 1.0 / max(params.edge_sensitivity, 0.3)
        orient_boosted = np.power(orient_map, power)
        orient_boosted /= max(orient_boosted.max(), 1e-6)
        split_landscape = 0.5 * contrast_map + 0.5 * orient_boosted
        split_landscape /= max(split_landscape.max(), 1e-6)

        output = labels.copy()
        max_lbl = labels.max()

        for region in regionprops(labels):
            if region.area < merge_thresh:
                continue

            rmask = (labels == region.label)
            r0, c0, r1, c1 = region.bbox
            lmask = rmask[r0:r1, c0:c1]
            lu8 = (split_landscape[r0:r1, c0:c1] * 255).astype(np.uint8)

            dist = ndi.distance_transform_edt(lmask)
            min_d = max(5, int(np.sqrt(region.area) / 6))
            coords = peak_local_max(
                dist, min_distance=min_d,
                labels=lmask, exclude_border=False)

            if len(coords) <= 1:
                continue

            lmarkers = np.zeros_like(lmask, dtype=np.int32)
            for i, (r, c) in enumerate(coords, 1):
                lmarkers[r, c] = i

            sub = watershed(lu8, lmarkers, mask=lmask)
            sub_r = regionprops(sub)

            if (len(sub_r) > 1 and
                    all(sr.area >= params.min_grain_size_px
                        for sr in sub_r)):
                for sr in sub_r:
                    max_lbl += 1
                    fm = np.zeros_like(labels, dtype=bool)
                    fm[r0:r1, c0:c1] = (sub == sr.label)
                    output[fm] = max_lbl

        return output

    # ==================================================================
    # PIPELINE B: Threshold-based (unchanged)
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
 
