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

import sys
import numpy as np
import cv2
from scipy import ndimage as ndi
from skimage.segmentation import watershed
from skimage.feature import peak_local_max
from skimage.measure import regionprops
from skimage.filters import threshold_otsu, gaussian
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import logging

from core.metrics import compute_statistics
from core.astm import update_astm
from core.infobar import detect_info_bar
from core.overlay_compose import compose_full_overlay, rc_to_xywh
from core.cancel import AnalysisCancelled, make_cancel_check  # noqa: F401 (re-export)

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
    total_analyzed_area_um2: float = 0.0   # = valid (test-field) area
    grain_coverage_pct: float = 0.0        # grain area / valid area
    # Test-field bookkeeping (DET-02). The valid mask is in the same
    # (auto-cropped) coordinates as label_image.
    valid_mask: Optional[np.ndarray] = None
    valid_area_px: float = 0.0
    valid_area_um2: float = 0.0
    invalid_area_pct: float = 0.0
    # ASTM E112 / E1382 (DET-04, INN-26): JSON-able dict produced by
    # core.astm.AstmResult.to_dict() for the full (unedited) field, and the
    # primary grain-size number (None when uncalibrated).
    astm: dict = field(default_factory=dict)
    astm_g: Optional[float] = None
    # Auto-crop / SEM data bar (DET-05).  All in the coordinates of the
    # image passed to GrainDetector.analyze():
    #   auto_crop_rect  (r0, c0, r1, c1) of the analysed sub-image, or None
    #                   when the whole image was analysed; label_image,
    #                   binary_image, valid_mask and grain coordinates are
    #                   relative to (r0, c0).  overlay_image is the
    #                   exception: always the FULL input frame (crop overlay
    #                   pasted at (r0, c0) + analysed-region outline; see
    #                   core.overlay_compose) because it is what gets exported.
    #   info_bar_rect   (x, y, w, h) of the detected data bar (bottom bar
    #                   preferred), or None.
    #   info_bar        detect_info_bar(...).to_dict() (analysis_rect,
    #                   bar_rect, confidence, bars) or {}.
    auto_crop_rect: Optional[Tuple[int, int, int, int]] = None
    info_bar_rect: Optional[Tuple[int, int, int, int]] = None
    info_bar: dict = field(default_factory=dict)


@dataclass
class DetectionParams:
    """Detection parameters.

    Invalid-region (pure black) gate — DET-01
    ----------------------------------------
    invalid_intensity_threshold
        uint8 gray level at or below which a pixel is a candidate "no
        specimen information" pixel (black info bar, detector drop-out,
        masked area, deep pore).  Also the minimum mean intensity a
        detected region must have to be reported as a grain.  0 disables
        the whole gate (legacy v2.3 behaviour).  Default 12: SEM
        secondary/back-scatter images of real grains, even dark phases,
        sit well above this; true black is 0-10.
    invalid_min_width_px
        A dark structure only counts as invalid if it is at least this
        thick (morphological opening with a disk of this diameter).
        Thinner dark lines are grain-boundary grooves and stay VALID so
        they keep separating grains.
    invalid_min_area_px
        A thick dark blob must also cover at least this many pixels to be
        invalid (groove triple junctions and small pits stay valid; regions
        seeded inside them are still rejected by the mean-intensity gate).
    min_valid_fraction
        A region is dropped if less than this fraction of its pixels lie in
        the valid mask.
    sam_min_intensity_std
        SAM only: masks whose gray-level standard deviation is below this
        are near-uniform (flat black/saturated patches) and are rejected.
        Real grains carry detector noise and texture (std > ~3).
    """
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
    invalid_intensity_threshold: int = 12
    invalid_min_width_px: int = 9
    invalid_min_area_px: int = 400
    min_valid_fraction: float = 0.5
    sam_min_intensity_std: float = 1.5
    # ASTM E112 procedure (INN-26): "both" | "planimetric" |
    # "intercept_lines" | "intercept_circles"; test-line spacing as a
    # multiple of the mean ECD.
    astm_method: str = "both"
    astm_pattern_spacing_factor: float = 2.0
    # DET-05: detect the SEM data/info bar (full-width uniform band with
    # sparse text / scale bar, bottom or top) and exclude it from analysis
    # even when no scan area is drawn.  See core/infobar.py.
    auto_exclude_info_bar: bool = True


# ======================================================================
# Valid-pixel mask (DET-01)
# ======================================================================

def compute_valid_mask(gray, threshold=12, min_width_px=9, min_area_px=400):
    """Return a bool mask, True where the image carries specimen information.

    A pixel is INVALID only if it belongs to a dark (``<= threshold`` raw or
    after a 3x3 median, which absorbs isolated shot noise in black areas)
    structure that is both

    * thick — survives a morphological opening with a disk of diameter
      ``min_width_px`` (i.e. its distance transform reaches the disk
      radius), then one conditional dilation back into the dark set
      restores the corners the opening rounded off, and
    * large — the opened component covers ``>= min_area_px`` pixels.

    Thin dark grain-boundary grooves fail the width test and therefore stay
    valid, so the boundary pipelines can still use them as separators.
    ``threshold <= 0`` disables the gate (all pixels valid).
    """
    gray = np.asarray(gray)
    if gray.ndim == 3:
        gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
    if threshold is None or threshold <= 0:
        return np.ones(gray.shape[:2], dtype=bool)
    g = gray if gray.dtype == np.uint8 else np.clip(gray, 0, 255).astype(np.uint8)
    # raw OR median: the median fills isolated bright noise pixels inside
    # black areas, the raw test keeps exact corners (median rounds them);
    # isolated dark noise pixels inside grains are removed by the opening.
    dark = ((g <= threshold) | (cv2.medianBlur(g, 3) <= threshold)
            ).astype(np.uint8)
    if not dark.any():
        return np.ones(g.shape, dtype=bool)
    k = int(max(1, min_width_px))
    if k > 1:
        kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        thick = cv2.morphologyEx(dark, cv2.MORPH_OPEN, kern)
        # The opening rounds convex corners of black regions; one
        # conditional dilation (within ``dark``) restores them. Thin grooves
        # only become invalid within k/2 px of a thick black region.
        thick = cv2.dilate(thick, kern) & dark
    else:
        thick = dark
    if min_area_px and min_area_px > 1 and thick.any():
        n, lab, stats, _ = cv2.connectedComponentsWithStats(thick, connectivity=8)
        keep = stats[:, cv2.CC_STAT_AREA] >= min_area_px
        keep[0] = False
        thick = keep[lab]
    return ~thick.astype(bool)


def _remove_small_bool(mask, min_size):
    """Drop 4-connected components with fewer than ``min_size`` pixels.

    Exact equivalent of the deprecated
    ``skimage.morphology.remove_small_objects(mask, min_size=min_size)``
    (strictly-smaller-than semantics, connectivity=1). Implemented locally
    because the replacement keyword ``max_size`` (``<=`` semantics, i.e.
    ``max_size=min_size-1``) only exists in scikit-image >= 0.26 while the
    requirements allow >= 0.22.
    """
    lab, n = ndi.label(mask)
    if n == 0:
        return mask.astype(bool)
    sizes = np.bincount(lab.ravel())
    keep = sizes >= min_size
    keep[0] = False
    return keep[lab]


def _relabel_sequential(labels):
    """Map the positive labels of ``labels`` to 1..N, preserving order."""
    ids = np.unique(labels)
    ids = ids[ids > 0]
    lut = np.zeros(int(labels.max()) + 1 if labels.size else 1, dtype=np.int32)
    lut[ids] = np.arange(1, len(ids) + 1, dtype=np.int32)
    return lut[labels]


def _drop_by_size(labels, min_sz, max_sz=0):
    """Zero regions smaller than ``min_sz`` (or larger than ``max_sz`` > 0)."""
    if labels.max() == 0:
        return labels
    sizes = np.bincount(labels.ravel())
    bad = sizes < min_sz
    if max_sz and max_sz > 0:
        bad |= sizes > max_sz
    bad[0] = False
    if bad.any():
        labels = labels.copy()
        labels[bad[labels]] = 0
    return labels


def gate_labels_by_validity(labels, gray, valid_mask, min_mean_intensity,
                            min_valid_fraction=0.5):
    """Apply the invalid-region gate to a label image.

    Drops every region whose mean gray level (over all its pixels) is below
    ``min_mean_intensity`` or whose fraction of pixels inside ``valid_mask``
    is below ``min_valid_fraction``; zeros all labels outside the mask.
    Labels are renumbered 1..N only if something was removed, so results on
    images without invalid regions are bit-identical to the ungated ones.
    """
    if labels.max() == 0:
        return labels
    n = int(labels.max()) + 1
    flat = labels.ravel()
    counts = np.bincount(flat, minlength=n).astype(np.float64)
    sums = np.bincount(flat, weights=gray.ravel().astype(np.float64), minlength=n)
    vcount = np.bincount(flat, weights=valid_mask.ravel().astype(np.float64),
                         minlength=n)
    present = counts > 0
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(present, sums / np.maximum(counts, 1), 0.0)
        vfrac = np.where(present, vcount / np.maximum(counts, 1), 0.0)
    bad = present & ((mean < min_mean_intensity) | (vfrac < min_valid_fraction))
    bad[0] = False
    outside = (labels > 0) & ~valid_mask
    if not bad.any() and not outside.any():
        return labels
    out = labels.copy()
    out[bad[labels]] = 0
    out[~valid_mask] = 0
    return _relabel_sequential(out) if bad.any() else out


def filter_sam_masks(masks, gray, valid_mask, *, min_area, max_area,
                     min_mean_intensity=12.0, min_intensity_std=1.5,
                     min_valid_fraction=0.5, min_predicted_iou=0.75,
                     max_frame_fraction=0.4, cancel=None):
    """Turn SAM automatic-mask output into a label image.

    Pure function (no model needed) so it can be unit-tested with fake mask
    dicts carrying ``segmentation`` (bool HxW), ``area`` and
    ``predicted_iou``.  Masks are processed largest-first so smaller grains
    overwrite larger background-like masks.  A mask is rejected if

    * its area is outside [min_area, max_area] or > max_frame_fraction of
      the frame (background),
    * ``predicted_iou < min_predicted_iou``,
    * mean gray < ``min_mean_intensity`` (black region),
    * gray std-dev < ``min_intensity_std`` (near-uniform patch: SAM gives
      flat regions high stability scores, but grains carry texture/noise),
    * fraction on valid pixels < ``min_valid_fraction``,
    * > 50 % of it overlaps already accepted grains.

    ``cancel`` is an optional cancel token (see :mod:`core.cancel`),
    checked once per mask.

    Returns ``(labels int32, binary uint8 0/255, n_accepted)``.
    """
    check = make_cancel_check(cancel)
    h, w = gray.shape[:2]
    labels = np.zeros((h, w), dtype=np.int32)
    binary = np.zeros((h, w), dtype=np.uint8)
    gid = 0
    gray_f = gray.astype(np.float32)
    for m in sorted(masks, key=lambda d: d['area'], reverse=True):
        check()
        area = m['area']
        if area < min_area or area > max_area:
            continue
        if area > h * w * max_frame_fraction:
            continue
        if m.get('predicted_iou', 1.0) < min_predicted_iou:
            continue
        seg = m['segmentation']
        n = int(np.count_nonzero(seg))
        if n == 0:
            continue
        vals = gray_f[seg]
        if float(vals.mean()) < min_mean_intensity:
            continue
        if float(vals.std()) < min_intensity_std:
            continue
        if valid_mask is not None and \
                np.count_nonzero(valid_mask[seg]) < min_valid_fraction * n:
            continue
        if np.count_nonzero(seg & (labels > 0)) > 0.5 * area:
            continue
        gid += 1
        labels[seg] = gid
        binary[seg] = 255
    return labels, binary, gid


def discard_border_grains(result, image_bgr=None, detector=None):
    """Remove grains whose label touches the frame edge (partial grains).

    ASTM E112 planimetric counting excludes grains cut by the test-field
    boundary from the full-grain count; this is what the UI does when a scan
    area is set.  Mutates and returns ``result``: labels zeroed, grain list
    filtered, statistics recomputed via :func:`core.metrics.compute_statistics`
    and, if ``image_bgr`` is given, the overlay redrawn.  The valid (test
    field) area is unchanged.  ``result.astm`` is deliberately kept: the
    E112 count was made on the full field, where border grains already
    count 1/2 (re-counting after discarding them would bias N_A).
    """
    lab = result.label_image
    if lab is None or lab.size == 0:
        return result
    border = np.unique(np.concatenate(
        [lab[0, :], lab[-1, :], lab[:, 0], lab[:, -1]]))
    border = border[border > 0]
    if len(border):
        kill = np.zeros(int(lab.max()) + 1, dtype=bool)
        kill[border] = True
        lab[kill[lab]] = 0
        bset = set(int(b) for b in border)
        result.grains = [g for g in result.grains if g.grain_id not in bset]
    compute_statistics(result, lab.shape)
    if image_bgr is not None:
        det = detector or GrainDetector()
        crop = rc_to_xywh(getattr(result, "auto_crop_rect", None))
        img = image_bgr
        if crop is not None and img.shape[:2] != lab.shape[:2]:
            x, y, w, h = crop          # full frame given, crop labels
            img = image_bgr[y:y + h, x:x + w]
        else:
            crop = None
        ov = det._draw_overlay(img, result.label_image, result.grains)
        result.overlay_image = compose_full_overlay(image_bgr, ov, crop)
    return result


def _offset_info_bar(info, dx, dy):
    """Shift every rectangle of an InfoBarResult by (dx, dy)."""
    x, y, w, h = info.analysis_rect
    info.analysis_rect = (x + dx, y + dy, w, h)
    for b in info.bars:
        bx, by, bw, bh = b.rect
        b.rect = (bx + dx, by + dy, bw, bh)
    return info


# Points per SAM decoder batch on CPU (UX-07).  segment_anything's default
# is 64; one 64-point batch plus its full-resolution mask post-processing can
# take several seconds on a laptop CPU, which is how long a cancel waited.
SAM_POINTS_PER_BATCH_CPU = 16


def run_sam_generator(mask_generator, image_rgb, total_points, progress,
                      check_cancel, device_label="CPU"):
    """Run ``mask_generator.generate(image_rgb)`` with progress reporting and
    cooperative cancellation (UX-07).

    ``check_cancel()`` raises :class:`AnalysisCancelled`; it is called

    * before every ViT image-encoder block (the encoder is the longest
      single step on CPU: 12 blocks for vit_b),
    * before every point batch (``_process_batch``), which is also where
      progress is reported (10 %..50 %),
    * before every mask in SAM's small-region post-processing.

    All hooks are instance attributes (or a module attribute for the
    post-processing helper) and are removed in ``finally``, so a cancelled
    or failed run leaves the model untouched.  Works with any object that
    exposes ``generate``/``_process_batch``/``points_per_batch`` (tests use
    a fake generator; no model needed).
    """
    batch_size = max(1, int(getattr(mask_generator, "points_per_batch", 64) or 64))
    total_batches = max(1, (int(total_points) + batch_size - 1) // batch_size)
    counter = [0]
    restore = []

    def _wrap_attr(obj, name, before):
        original = getattr(obj, name)
        had_instance_attr = name in getattr(obj, "__dict__", {})

        def wrapped(*args, **kwargs):
            before()
            return original(*args, **kwargs)

        setattr(obj, name, wrapped)
        restore.append((obj, name, original, had_instance_attr))

    def _before_batch():
        check_cancel()
        counter[0] += 1
        pct = min(50, 10 + int(40 * counter[0] / total_batches))
        progress(pct, f"SAM on {device_label}: batch {counter[0]}/{total_batches} ({pct}%)")

    try:
        if hasattr(mask_generator, "_process_batch"):
            _wrap_attr(mask_generator, "_process_batch", _before_batch)
        model = getattr(getattr(mask_generator, "predictor", None), "model", None)
        blocks = getattr(getattr(model, "image_encoder", None), "blocks", None)
        for blk in (blocks or []):
            _wrap_attr(blk, "forward", check_cancel)
        try:
            import segment_anything.automatic_mask_generator as _amg
        except Exception:  # pragma: no cover - SAM not installed (fake generator)
            _amg = None
        if _amg is not None and hasattr(_amg, "remove_small_regions"):
            _wrap_attr(_amg, "remove_small_regions", check_cancel)

        progress(10, f"Running SAM on {device_label}: 0/{total_batches} batches...")
        check_cancel()
        masks = mask_generator.generate(image_rgb)
        check_cancel()
        return masks
    finally:
        for obj, name, original, had_instance_attr in reversed(restore):
            if had_instance_attr or not hasattr(type(obj), name):
                setattr(obj, name, original)
            else:
                try:
                    delattr(obj, name)   # fall back to the class attribute
                except AttributeError:
                    setattr(obj, name, original)


class GrainDetector:

    def __init__(self):
        self._last_result = None
        self._valid_mask = None
        self._check_cancel = make_cancel_check(None)

    def _ws_mask(self, shape):
        """Valid mask for watershed ``mask=`` (None when everything is valid,
        which keeps legacy results bit-identical)."""
        vm = self._valid_mask
        if vm is None or vm.shape != tuple(shape[:2]) or vm.all():
            return None
        return vm

    def _seeded_watershed(self, landscape_u8, coords, h, w):
        """Marker watershed on ``landscape_u8``; seeds outside the valid
        mask are discarded and flooding is confined to valid pixels."""
        vm = self._ws_mask((h, w))
        if vm is not None and len(coords):
            coords = coords[vm[coords[:, 0], coords[:, 1]]]
        if len(coords) == 0:
            return np.zeros((h, w), dtype=np.int32), 0
        markers = np.zeros((h, w), dtype=np.int32)
        markers[coords[:, 0], coords[:, 1]] = np.arange(
            1, len(coords) + 1, dtype=np.int32)
        return watershed(landscape_u8, markers, mask=vm), len(coords)

    def analyze(self, image_bgr, px_per_um=0.0, params=None, progress_callback=None,
                cancel=None):
        """Detect and measure grains.

        ``cancel`` (UX-07) is an optional cancel token -- a
        ``threading.Event`` or a zero-argument callable returning True.
        It is checked at every progress step and inside every long loop
        (SAM encoder blocks / point batches, per-region splits, per-grain
        measurement, overlay drawing); once it fires,
        :class:`core.cancel.AnalysisCancelled` is raised and no result is
        returned, so nothing partial can be saved.  Measurements are
        unaffected when ``cancel`` is None or never fires.
        """
        if params is None:
            params = DetectionParams()
        check = self._check_cancel = make_cancel_check(cancel)

        result = AnalysisResult(px_per_um=px_per_um, has_calibration=(px_per_um > 0))

        def progress(pct, msg):
            check()
            if progress_callback:
                progress_callback(pct, msg)

        progress(2, "Preprocessing...")
        full_bgr = image_bgr   # uncropped frame, for the exported overlay
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)

        crop_rect, info = self.auto_crop_details(gray, params)
        result.auto_crop_rect = crop_rect
        if info is not None:
            result.info_bar = info.to_dict()
            result.info_bar_rect = info.bar_rect
        if crop_rect is not None:
            r0, c0, r1, c1 = crop_rect
            gray = gray[r0:r1, c0:c1]
            image_bgr = image_bgr[r0:r1, c0:c1]

        # One valid-pixel mask for every pipeline (DET-01).
        thr = int(getattr(params, "invalid_intensity_threshold", 0) or 0)
        valid = compute_valid_mask(
            gray, thr,
            getattr(params, "invalid_min_width_px", 9),
            getattr(params, "invalid_min_area_px", 400))
        self._valid_mask = valid
        has_invalid = not bool(valid.all())
        if has_invalid and valid.any():
            # Neutralise invalid pixels for the contrast-based pipelines:
            # the huge step at a black edge would otherwise dominate the
            # max-normalised boundary signals. Fill with the valid median.
            seg_gray = gray.copy()
            seg_gray[~valid] = np.uint8(np.median(gray[valid]))
        else:
            seg_gray = gray

        mode = params.detection_mode
        if mode == "auto":
            mode = self._auto_detect_mode(seg_gray)

        if not valid.any():
            h0, w0 = gray.shape
            labels = np.zeros((h0, w0), dtype=np.int32)
            binary = np.zeros((h0, w0), dtype=np.uint8)
        elif mode == "sam_astm":
            labels, binary = self._sam_astm_pipeline(
                gray, image_bgr, params, progress)
        elif mode == "boundary":
            labels, binary = self._boundary_pipeline(
                seg_gray, image_bgr, params, progress)
        else:
            labels, binary = self._threshold_pipeline(
                seg_gray, image_bgr, params, progress)

        if thr > 0:
            labels = gate_labels_by_validity(
                labels, gray, valid, thr,
                getattr(params, "min_valid_fraction", 0.5))
            if has_invalid and binary is not None:
                binary = binary.copy()
                binary[~valid] = 0

        result.binary_image = binary
        result.valid_mask = valid
        result.valid_area_px = float(np.count_nonzero(valid))

        check()
        progress(78, "Measuring grain properties...")
        grains = self._measure_grains(labels, params, px_per_um)

        progress(88, "Computing statistics...")
        result.grains = grains
        result.grain_count = len(grains)
        result.label_image = labels
        result = self._compute_statistics(result, image_bgr)

        # ASTM E112 planimetric + intercept on the full field (DET-04).
        try:
            update_astm(result, params)
        except Exception:  # never let the standard report break detection
            logger.exception("ASTM E112 evaluation failed")

        progress(94, "Generating overlay...")
        # label_image stays in analysed (crop) coordinates; the overlay is
        # the FULL original frame with the crop overlay at its offset, so an
        # exported overlay keeps the SEM data bar and original resolution.
        result.overlay_image = compose_full_overlay(
            full_bgr, self._draw_overlay(image_bgr, labels, grains),
            rc_to_xywh(crop_rect))

        check()
        progress(100, f"Complete — {result.grain_count} grains detected.")
        self._last_result = result
        return result

    # ==================================================================
    # Auto-crop
    # ==================================================================

    def _auto_crop(self, gray, params=None):
        """(r0, c0, r1, c1) of the region to analyse, or None for the whole
        image.  ``params=None`` means default DetectionParams (info-bar
        exclusion ON) — this is the legacy call ui/workers.py makes; prefer
        ``AnalysisResult.auto_crop_rect`` after analyze()."""
        return self.auto_crop_details(gray, params)[0]

    def auto_crop_details(self, gray, params=None):
        """Return ``(crop_rect, info_bar_result)``.

        ``crop_rect`` is (r0, c0, r1, c1) in ``gray`` coordinates or None;
        ``info_bar_result`` is the :class:`core.infobar.InfoBarResult`
        (rectangles in ``gray`` coordinates) or None.

        Order: the data bar is looked for on the full frame first; the
        legacy white-border crop is then applied inside the micrograph
        area.  If no bar is found on the full frame but a white border is
        cropped, the bar search is repeated inside the cropped region (an
        SEM image pasted on a white page).
        """
        if gray.ndim == 3:
            gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        use_bar = True if params is None else bool(
            getattr(params, "auto_exclude_info_bar", True))
        info = None
        r0, c0, r1, c1 = 0, 0, h, w
        if use_bar:
            try:
                info = detect_info_bar(gray)
            except Exception:  # never let bar detection break analysis
                logger.exception("info-bar detection failed")
                info = None
            if info is not None:
                x, y, rw, rh = info.analysis_rect
                r0, c0, r1, c1 = y, x, y + rh, x + rw
        sub = gray[r0:r1, c0:c1]
        white = self._white_border_crop(sub)
        if white is not None:
            wr0, wc0, wr1, wc1 = white
            r0, c0, r1, c1 = r0 + wr0, c0 + wc0, r0 + wr1, c0 + wc1
            if use_bar and info is None:
                try:
                    inner = detect_info_bar(gray[r0:r1, c0:c1])
                except Exception:
                    logger.exception("info-bar detection failed")
                    inner = None
                if inner is not None:
                    info = _offset_info_bar(inner, c0, r0)
                    x, y, rw, rh = info.analysis_rect
                    r0, c0, r1, c1 = y, x, y + rh, x + rw
        if (r0, c0, r1, c1) == (0, 0, h, w):
            return None, info
        return (int(r0), int(c0), int(r1), int(c1)), info

    def _white_border_crop(self, gray):
        """Legacy (v2) white-border auto-crop."""
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
        fg_frac = np.mean(blurred > thresh)
        balance = min(fg_frac, 1.0 - fg_frac)

        # Strong foreground/background separation → threshold mode
        # (e.g. particles on a substrate, not mosaic grains)
        if balance < 0.20:
            return "threshold"

        # Everything else: boundary mode (handles both grooves and mosaic)
        return "boundary"

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

        progress(5, "Measuring image characteristics...")
        texture_ratio = self._measure_texture(gray)

        # Detect groove-type boundaries via morphological gradient
        kern_mg = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        morph_grad = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, kern_mg)
        groove_strength = morph_grad.astype(float).mean()
        has_grooves = groove_strength > 55

        progress(8, "CLAHE enhancement...")
        if params.use_clahe:
            clahe = cv2.createCLAHE(
                clipLimit=params.clahe_clip_limit, tileGridSize=(8, 8))
            enhanced = clahe.apply(gray)
        else:
            enhanced = gray.copy()

        if has_grooves:
            # ---- GROOVE MODE: dark boundary lines between grains ----
            return self._groove_boundary_pipeline(
                enhanced, image_bgr, h, w, params, progress)
        else:
            # ---- MOSAIC MODE: subtle contrast/texture boundaries ----
            return self._mosaic_boundary_pipeline(
                enhanced, gray, image_bgr, h, w, texture_ratio, params, progress)

    def _groove_boundary_pipeline(self, enhanced, image_bgr, h, w, params, progress):
        """Boundary detection for images with dark groove boundaries."""
        progress(12, "Light denoising (groove mode)...")
        bl = cv2.bilateralFilter(enhanced, d=5, sigmaColor=25, sigmaSpace=5)
        bf = bl.astype(np.float32) / 255.0

        # Multi-scale black top-hat: extracts dark grooves at multiple widths
        progress(20, "Multi-scale groove detection...")
        bth = np.zeros_like(bf)
        for ks in [9, 15, 21]:
            self._check_cancel()
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ks, ks))
            b = cv2.morphologyEx(enhanced, cv2.MORPH_BLACKHAT, k)
            b = b.astype(np.float32)
            b /= max(b.max(), 1e-6)
            bth = np.maximum(bth, b)

        progress(30, "Supporting signals...")
        dog = np.abs(cv2.GaussianBlur(bf, (0, 0), 1.0) -
                     cv2.GaussianBlur(bf, (0, 0), 5.0))
        dog /= max(dog.max(), 1e-6)

        gx = cv2.Sobel(bf, cv2.CV_32F, 1, 0, ksize=5)
        gy = cv2.Sobel(bf, cv2.CV_32F, 0, 1, ksize=5)
        grad = np.sqrt(gx ** 2 + gy ** 2)
        grad /= max(grad.max(), 1e-6)

        progress(35, "Building groove boundary map...")
        # Black top-hat dominates for groove images
        contrast_combo = 0.10 * dog + 0.10 * grad + 0.80 * bth
        contrast_combo /= max(contrast_combo.max(), 1e-6)

        power = 1.0 / max(params.edge_sensitivity, 0.3)
        boosted = np.power(contrast_combo, power)
        boosted /= max(boosted.max(), 1e-6)

        # Tight smoothing to separate closely-packed grains
        progress(42, "Finding grain centers (groove mode)...")
        interior = 1.0 - boosted
        interior_smooth = cv2.GaussianBlur(interior, (0, 0), 3.0)

        min_dist = max(8, min(h, w) // 40)
        thresh_abs = max(0.15, 0.35 + params.threshold_offset)
        coords = peak_local_max(
            interior_smooth, min_distance=min_dist,
            threshold_abs=thresh_abs)

        progress(50, f"Watershed ({len(coords)} seeds)...")
        labels, _ = self._seeded_watershed(
            (boosted * 255).astype(np.uint8), coords, h, w)

        # Filter
        min_sz = max(params.min_grain_size_px, 20)
        labels = _drop_by_size(labels, min_sz, params.max_grain_size_px)

        # Relabel
        new_labels = _relabel_sequential(labels)

        return new_labels, (boosted * 255).astype(np.uint8)

    def _mosaic_boundary_pipeline(self, enhanced, gray, image_bgr,
                                   h, w, texture_ratio, params, progress):
        """Boundary detection for mosaic images with subtle boundaries."""
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
            self._check_cancel()
            dark += np.clip(
                cv2.GaussianBlur(df, (ks, ks), ks / 4.0) - df, 0, None)
        dark /= max(dark.max(), 1e-6)

        # Multi-scale step change: medium + heavy bilateral
        # Medium catches moderate contrast; heavy catches broad transitions
        step_scores = np.zeros_like(bf)
        for bf_scale in [bf_m, bf_h]:
            self._check_cancel()
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

        # Black top-hat: detects thin dark grooves (boundary lines)
        progress(28, "Detecting dark grooves...")
        kern_bth = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
        bth = cv2.morphologyEx(enhanced, cv2.MORPH_BLACKHAT, kern_bth)
        bth = bth.astype(np.float32)
        bth /= max(bth.max(), 1e-6)

        progress(32, "Building contrast boundary map...")
        # Adaptive weighting: black top-hat is critical for groove boundaries
        # but causes over-segmentation on mosaic/texture images
        if texture_ratio > 3.0:
            # Groove-type: bth is the key signal
            contrast_combo = (0.15 * dog + 0.10 * nlap + 0.10 * dark +
                              0.20 * step + 0.10 * grad + 0.35 * bth)
        else:
            # Mosaic/texture: step change dominates, bth is minor
            contrast_combo = (0.15 * dog + 0.10 * nlap + 0.15 * dark +
                              0.40 * step + 0.10 * grad + 0.10 * bth)
        contrast_combo /= max(contrast_combo.max(), 1e-6)

        # Sensitivity boost
        power = 1.0 / max(params.edge_sensitivity, 0.3)
        boosted = np.power(contrast_combo, power)
        boosted /= max(boosted.max(), 1e-6)

        # ---- Watershed on contrast map ----
        progress(42, "Finding grain centers...")
        interior = 1.0 - boosted
        interior_smooth = cv2.GaussianBlur(interior, (0, 0), 8.0)

        min_dist = max(8, min(h, w) // 40)
        thresh_abs = max(0.15, 0.35 + params.threshold_offset)
        coords = peak_local_max(
            interior_smooth, min_distance=min_dist,
            threshold_abs=thresh_abs)

        progress(48, f"Contrast watershed ({len(coords)} seeds)...")
        labels, _ = self._seeded_watershed(
            (boosted * 255).astype(np.uint8), coords, h, w)
        self._check_cancel()

        # Filter small/large
        min_sz = max(params.min_grain_size_px, 20)
        labels = _drop_by_size(labels, min_sz, params.max_grain_size_px)

        # ---- PASS 2: Texture orientation split on oversized regions ----
        progress(58, "Computing texture orientation...")
        gf_raw = gray.astype(np.float32) / 255.0
        orient = self._compute_orientation_change(gf_raw)

        progress(65, "Splitting oversized regions by texture...")
        labels = self._texture_split(
            labels, boosted, orient, params)

        # Final filter + relabel
        labels = _drop_by_size(labels, min_sz)
        new_labels = _relabel_sequential(labels)

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

        # Blend: contrast dominates, texture is secondary refinement
        power = 1.0 / max(params.edge_sensitivity, 0.3)
        orient_boosted = np.power(orient_map, power)
        orient_boosted /= max(orient_boosted.max(), 1e-6)
        split_landscape = 0.70 * contrast_map + 0.30 * orient_boosted
        split_landscape /= max(split_landscape.max(), 1e-6)

        output = labels.copy()
        max_lbl = labels.max()

        for region in regionprops(labels):
            self._check_cancel()
            if region.area < merge_thresh:
                continue

            r0, c0, r1, c1 = region.bbox
            lmask = (labels[r0:r1, c0:c1] == region.label)
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
                out_win = output[r0:r1, c0:c1]   # view
                for sr in sub_r:
                    max_lbl += 1
                    out_win[sub == sr.label] = max_lbl

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
        vm = self._ws_mask((h, w))
        if vm is not None and np.count_nonzero(vm) > 100:
            # Otsu on the test field only: black areas would otherwise
            # drag the histogram split toward zero.
            thresh_val = threshold_otsu(blurred_g[vm])
        else:
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
        if vm is not None:
            binary_bool &= vm

        progress(40, "Removing debris...")
        if params.min_grain_size_px > 0:
            binary_bool = _remove_small_bool(
                binary_bool, params.min_grain_size_px)

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
        self._check_cancel()

        return labels, binary_bool.astype(np.uint8) * 255

    # ==================================================================
    # PIPELINE C: SAM + ASTM E112 (AI-assisted)
    # ==================================================================

    def _sam_astm_pipeline(self, gray, image_bgr, params, progress):
        """
        Uses Meta's Segment Anything Model (SAM) for instance segmentation,
        then applies ASTM E112 intercept-based validation.
        """
        from segment_anything import sam_model_registry, SamAutomaticMaskGenerator
        import torch
        import os

        h, w = gray.shape

        # --- Step 1: Load SAM model ---
        progress(2, "Loading SAM model...")
        model_type = "vit_b"
        # Look for checkpoint in a few locations (including PyInstaller bundle)
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_dir = os.path.dirname(script_dir)
        frozen_dir = getattr(sys, '_MEIPASS', project_dir)
        checkpoint_name = "sam_vit_b_01ec64.pth"
        search_paths = [
            os.path.join(frozen_dir, "models", checkpoint_name),
            os.path.join(project_dir, "models", checkpoint_name),
            os.path.join(project_dir, checkpoint_name),
            os.path.join(script_dir, checkpoint_name),
        ]
        checkpoint_path = None
        for p in search_paths:
            if os.path.isfile(p):
                checkpoint_path = p
                break

        if checkpoint_path is None:
            # Offline app (D-14): never point the user to a download.
            raise FileNotFoundError(
                f"The AI segmentation model ('{checkpoint_name}') could not be "
                f"found. The model ships inside the SEM Grain Analyzer "
                f"installer; please reinstall or repair the application "
                f"(run GrainAnalyzer_Setup.exe again). Other detection modes "
                f"remain available."
            )

        device = "cuda" if torch.cuda.is_available() else "cpu"
        progress(5, f"Loading SAM weights to {device.upper()}...")
        sam = sam_model_registry[model_type](checkpoint=checkpoint_path)
        self._check_cancel()
        sam.to(device=device)

        # --- Step 2: Generate masks with SAM ---
        # Downscale large images for CPU speed
        max_dim = 1024 if device == "cpu" else 2048
        scale = 1.0
        if max(h, w) > max_dim:
            scale = max_dim / max(h, w)
            new_h, new_w = int(h * scale), int(w * scale)
            image_resized = cv2.resize(image_bgr, (new_w, new_h),
                                       interpolation=cv2.INTER_AREA)
            progress(8, f"Downscaled {w}x{h} → {new_w}x{new_h} for speed...")
        else:
            image_resized = image_bgr

        # Tune SAM parameters — use 32 points for CPU, 64 for GPU
        min_area = max(params.min_grain_size_px, 50)
        pts = 32 if device == "cpu" else 64
        mask_generator = SamAutomaticMaskGenerator(
            model=sam,
            points_per_side=pts,
            # UX-07: smaller point batches on CPU so a cancel is noticed
            # within ~1 s.  Batching only splits the work; SAM concatenates
            # all batches before NMS, so the masks are identical.
            points_per_batch=SAM_POINTS_PER_BATCH_CPU if device == "cpu" else 64,
            pred_iou_thresh=0.80,
            stability_score_thresh=0.88,
            crop_n_layers=0,
            min_mask_region_area=int(min_area * scale * scale),
        )

        # SAM expects RGB
        image_rgb = cv2.cvtColor(image_resized, cv2.COLOR_BGR2RGB)

        masks = run_sam_generator(
            mask_generator, image_rgb, pts * pts, progress, self._check_cancel,
            device_label=device.upper())

        # Scale masks back up if we downscaled
        if scale < 1.0:
            progress(55, "Upscaling masks to original resolution...")
            for m in masks:
                self._check_cancel()
                m['segmentation'] = cv2.resize(
                    m['segmentation'].astype(np.uint8), (w, h),
                    interpolation=cv2.INTER_NEAREST).astype(bool)
                m['area'] = int(np.sum(m['segmentation']))

        progress(60, f"SAM found {len(masks)} candidate regions...")

        # --- Step 3: Filter and build label image ---
        progress(65, "Filtering masks...")
        max_area = params.max_grain_size_px if params.max_grain_size_px > 0 else (h * w * 0.5)
        thr = float(getattr(params, "invalid_intensity_threshold", 0) or 0)
        labels, binary, grain_id = filter_sam_masks(
            masks, gray, self._ws_mask((h, w)),
            min_area=min_area, max_area=max_area,
            min_mean_intensity=thr,
            min_intensity_std=(float(getattr(params, "sam_min_intensity_std", 0.0))
                               if thr > 0 else 0.0),
            min_valid_fraction=float(getattr(params, "min_valid_fraction", 0.5)),
            cancel=self._check_cancel,
        )

        progress(72, f"Accepted {grain_id} grains after filtering...")

        # --- Step 4: ASTM E112 intercept validation ---
        progress(75, "ASTM E112 intercept analysis...")
        labels = self._astm_e112_refine(labels, params, h, w)

        # Relabel contiguously
        new_labels = _relabel_sequential(labels)

        binary = (new_labels > 0).astype(np.uint8) * 255
        return new_labels, binary

    def _astm_e112_refine(self, labels, params, h, w):
        """
        Apply ASTM E112 linear intercept method to validate and refine
        grain segmentation. Grains that are suspiciously large compared
        to the intercept-derived mean grain size get re-examined.
        """
        # Draw test lines (horizontal + vertical) and count boundary crossings
        n_lines = 20
        total_intercepts = 0
        total_line_length = 0

        # Horizontal test lines
        for i in range(n_lines):
            row = int(h * (i + 1) / (n_lines + 1))
            line = labels[row, :]
            crossings = np.sum(np.diff(line) != 0)
            total_intercepts += crossings
            total_line_length += w

        # Vertical test lines
        for i in range(n_lines):
            col = int(w * (i + 1) / (n_lines + 1))
            line = labels[:, col]
            crossings = np.sum(np.diff(line) != 0)
            total_intercepts += crossings
            total_line_length += h

        if total_intercepts < 2:
            return labels

        # Mean intercept length in pixels
        mean_intercept_px = total_line_length / max(total_intercepts, 1)
        # Expected grain area from intercept (circular approximation)
        expected_grain_area = np.pi * (mean_intercept_px / 2) ** 2

        # Any region > 4x the expected area is likely multiple merged grains
        # Try to split them with watershed
        merge_threshold = expected_grain_area * 4.0
        output = labels.copy()
        max_lbl = labels.max()

        for region in regionprops(labels):
            self._check_cancel()
            if region.area < merge_threshold:
                continue

            r0, c0, r1, c1 = region.bbox
            lmask = (labels[r0:r1, c0:c1] == region.label)

            # Use distance transform + watershed to split
            dist = ndi.distance_transform_edt(lmask)
            min_d = max(5, int(mean_intercept_px / 3))
            coords = peak_local_max(
                dist, min_distance=min_d,
                labels=lmask, exclude_border=False)

            if len(coords) <= 1:
                continue

            lmarkers = np.zeros_like(lmask, dtype=np.int32)
            for i, (r, c) in enumerate(coords, 1):
                lmarkers[r, c] = i

            sub = watershed(-dist, lmarkers, mask=lmask)
            sub_regions = regionprops(sub)

            min_sz = max(params.min_grain_size_px, 20)
            if (len(sub_regions) > 1 and
                    all(sr.area >= min_sz for sr in sub_regions)):
                out_win = output[r0:r1, c0:c1]   # view
                for sr in sub_regions:
                    max_lbl += 1
                    out_win[sub == sr.label] = max_lbl

        return output

    # ==================================================================
    # Measurement
    # ==================================================================

    def _measure_grains(self, labels, params, px_per_um):
        regions = regionprops(labels)
        grains = []
        check = self._check_cancel
        for i, region in enumerate(regions):
            if not i & 31:
                check()
            if region.area < max(params.min_grain_size_px, 5):
                continue
            if (params.max_grain_size_px > 0 and
                    region.area > params.max_grain_size_px):
                continue
            area_px = float(region.area)
            perim_raw = region.perimeter
            perim_px = float(perim_raw) if perim_raw > 0 else 1.0
            eq_diam_px = float(region.equivalent_diameter_area)
            major_ax = region.axis_major_length
            minor_ax = region.axis_minor_length
            if px_per_um > 0:
                px2 = px_per_um ** 2
                area_um2 = area_px / px2
                perim_um = perim_px / px_per_um
                eq_diam_um = eq_diam_px / px_per_um
                major_um = major_ax / px_per_um
                minor_um = minor_ax / px_per_um
            else:
                area_um2 = perim_um = eq_diam_um = major_um = minor_um = 0.0
            circularity = min(
                (4 * np.pi * area_px) / (perim_px ** 2), 1.0)
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

    def _compute_statistics(self, result, original=None):
        """Backward-compatible wrapper; see core.metrics.compute_statistics
        (coverage is relative to the valid test-field area, DET-02/03)."""
        shape = original.shape[:2] if original is not None else None
        return compute_statistics(result, shape)

    def _draw_overlay(self, image_bgr, labels, grains):
        overlay = image_bgr.copy()
        H, W = labels.shape[:2]
        unique_labels = np.unique(labels)
        unique_labels = unique_labels[unique_labels > 0]
        # Colour lookup table (vectorised; one full-frame pass instead of
        # one per grain — identical output to the per-label loop).
        lut = np.zeros((int(labels.max()) + 1 if labels.size else 1, 3),
                       dtype=np.uint8)
        colors = {}
        for lbl in unique_labels:
            # per-pixel conversion on purpose: OpenCV's SIMD path for long
            # rows rounds differently, which would shift colours by 1 level
            hue = int((lbl * 137.508) % 180)
            hsv = np.array([[[hue, 200, 220]]], dtype=np.uint8)
            bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0][0]
            lut[lbl] = bgr
            colors[int(lbl)] = bgr.tolist()
        color_map = lut[labels]
        alpha = 0.4
        mask = labels > 0
        blended = cv2.addWeighted(overlay, 1 - alpha, color_map, alpha, 0)
        overlay[mask] = blended[mask]
        grain_map = {g.grain_id: g for g in grains}
        slices = ndi.find_objects(labels)
        check = self._check_cancel
        for i, lbl in enumerate(unique_labels):
            if not i & 63:
                check()
            if lbl not in grain_map:
                continue
            grain = grain_map[lbl]
            color = colors.get(int(lbl), [0, 200, 255])
            sl = slices[lbl - 1]
            r0 = max(sl[0].start - 1, 0); r1 = min(sl[0].stop + 1, H)
            c0 = max(sl[1].start - 1, 0); c1 = min(sl[1].stop + 1, W)
            grain_mask = (labels[r0:r1, c0:c1] == lbl).astype(np.uint8)
            contours, _ = cv2.findContours(
                grain_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE,
                offset=(int(c0), int(r0)))
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
