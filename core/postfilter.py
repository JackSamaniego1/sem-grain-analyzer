"""
Non-destructive post-analysis grain filters (DET-10).
======================================================

The detector (``core.grain_detector.GrainDetector.analyze``) produces one
"raw" :class:`~core.grain_detector.AnalysisResult`. This module never
touches that raw result — it reads it and returns a brand-new, independent
``AnalysisResult`` with some grains hidden. The UI keeps the raw result
around so a toggle (border elimination / false-grain elimination / size or
shape sliders / manual delete) can be switched on and off instantly without
re-running detection, and so several toggle combinations can be compared.

Reason codes (a grain may carry more than one)
-----------------------------------------------
``border``            touches the analysed-field edge (image edge; or the
                       scan-area edge if the result ever carries one).
``touching_invalid``   4-adjacent to an excluded/invalid (black) pixel, i.e.
                       the grain is truncated by a masked-off region.
``low_contrast``       "false grain": near-uniform interior
                       (``std(intensity) < low_contrast_std``).
``too_dark``           mean interior intensity below ``dark_threshold``
                       (segmentation noise picked up inside a black patch).
``too_small`` / ``too_large``  area outside ``[min_area_px, max_area_px]``.
``elongated``          aspect ratio above ``max_aspect_ratio`` (slivers).
``low_circularity``    circularity below ``min_circularity``.
``manual``             the user deleted the grain by hand in the UI.

ASTM E112 and border grains
----------------------------
E112 planimetric counting (``core.astm.compute_astm`` / Jeffries method)
already gives grains cut by the field border a weight of 1/2 (1/4 at
corners) — that is how the standard accounts for them, not by discarding
them. If we recomputed ASTM G on labels with border grains erased, N_A
would be biased (every field loses its border population instead of
losing only half of it) and G would drift with field placement alone.
So ``exclude_border`` only affects the *returned* grain list / size
statistics / histograms; the ASTM re-evaluation in this module is always
run on a labels image where border grains are put back (only grains
excluded for a *non-border* reason — touching_invalid, low_contrast,
too_dark, size/shape limits, manual — are actually removed from the ASTM
labels). This mirrors what ``core.grain_detector.discard_border_grains``
already does for the plain border-discard path. A human-readable note is
appended to ``result.astm["notes"]`` explaining this every time filters
are applied and the astm dict is non-empty.

Performance
-----------
Everything here is vectorised with numpy / ``scipy.ndimage`` (label
zeroing via a lookup table, per-label mean/std via
``ndi.mean`` / ``ndi.standard_deviation``, border/adjacency via boolean
shifts) — no per-grain or per-pixel Python loops over the image. Target:
well under 150 ms for ~2000 grains on a 2048x1536 label image (see
``tests/test_postfilter.py::test_performance``).
"""
from __future__ import annotations

import copy
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Dict, FrozenSet, List, Optional, Set

import cv2
import numpy as np
from scipy import ndimage as ndi

from core.astm import compute_astm
from core.grain_detector import AnalysisResult
from core.metrics import compute_statistics

logger = logging.getLogger(__name__)

ALL_REASON_CODES = (
    "border", "touching_invalid", "low_contrast", "too_dark",
    "too_small", "too_large", "elongated", "low_circularity", "manual",
)


@dataclass
class PostFilterOptions:
    """User-facing toggle state for non-destructive post-filtering.

    ``0`` / ``0.0`` disables a numeric threshold ("off"); the two
    ``exclude_*`` booleans gate the corresponding checks independently of
    any threshold.
    """
    exclude_border: bool = False
    exclude_touching_invalid: bool = False
    exclude_low_contrast: bool = False
    low_contrast_std: float = 3.0
    dark_threshold: int = 12
    min_area_px: int = 0
    max_area_px: int = 0
    max_aspect_ratio: float = 0.0
    min_circularity: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "PostFilterOptions":
        if not d:
            return cls()
        names = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in d.items() if k in names}
        return cls(**kwargs)


@dataclass
class FilterOutcome:
    result: AnalysisResult
    excluded: Dict[int, List[str]] = field(default_factory=dict)
    counts: Dict[str, int] = field(default_factory=dict)


def _to_gray(image_bgr: np.ndarray) -> np.ndarray:
    if image_bgr.ndim == 2:
        return image_bgr
    if image_bgr.shape[2] == 1:
        return image_bgr[:, :, 0]
    return cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)


def _border_ids(labels: np.ndarray) -> Set[int]:
    if labels is None or labels.size == 0:
        return set()
    edge = np.unique(np.concatenate(
        [labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]]))
    return {int(x) for x in edge if x > 0}


def _touching_invalid_ids(labels: np.ndarray, valid_mask: Optional[np.ndarray]) -> Set[int]:
    if labels is None or labels.size == 0 or valid_mask is None:
        return set()
    invalid = ~valid_mask
    if not invalid.any():
        return set()
    neigh_invalid = np.zeros_like(invalid)
    neigh_invalid[1:, :] |= invalid[:-1, :]
    neigh_invalid[:-1, :] |= invalid[1:, :]
    neigh_invalid[:, 1:] |= invalid[:, :-1]
    neigh_invalid[:, :-1] |= invalid[:, 1:]
    touch = neigh_invalid & (labels > 0)
    if not touch.any():
        return set()
    ids = np.unique(labels[touch])
    return {int(x) for x in ids if x > 0}


def _label_mean_std(gray: np.ndarray, labels: np.ndarray, ids: np.ndarray):
    """Vectorised per-label mean and std of ``gray`` over ``labels``."""
    if ids.size == 0:
        return np.array([]), np.array([])
    means = ndi.mean(gray, labels=labels, index=ids)
    stds = ndi.standard_deviation(gray, labels=labels, index=ids)
    means = np.atleast_1d(np.asarray(means, dtype=np.float64))
    stds = np.atleast_1d(np.asarray(stds, dtype=np.float64))
    return means, stds


def _zero_labels(labels: np.ndarray, kill_ids: Set[int]) -> np.ndarray:
    """Return a COPY of ``labels`` with the pixels of ``kill_ids`` zeroed."""
    out = labels.copy()
    if not kill_ids:
        return out
    maxlab = int(out.max()) if out.size else 0
    if maxlab <= 0:
        return out
    kill = np.zeros(maxlab + 1, dtype=bool)
    ids = np.fromiter((i for i in kill_ids if 0 < i <= maxlab), dtype=np.int64)
    if ids.size:
        kill[ids] = True
        out[kill[out]] = 0
    return out


def apply_post_filters(raw: AnalysisResult, image_bgr: np.ndarray,
                       options: PostFilterOptions,
                       manual_excluded: FrozenSet[int] = frozenset(),
                       params=None) -> FilterOutcome:
    """Apply non-destructive filters to ``raw`` and return a new result.

    ``raw`` is never mutated: a new label image (copy, with excluded labels
    zeroed) and a new grain list are built; unchanged arrays
    (``valid_mask``, ``binary_image``) are shared by reference since they
    are only read here. See the module docstring for the ASTM/border
    interaction rule.
    """
    grains = list(raw.grains or [])
    labels = raw.label_image

    border_ids = _border_ids(labels) if options.exclude_border else set()
    touching_ids = (_touching_invalid_ids(labels, raw.valid_mask)
                    if options.exclude_touching_invalid else set())

    low_contrast_reasons: Dict[int, List[str]] = {}
    if options.exclude_low_contrast and grains and labels is not None:
        ids = np.array([g.grain_id for g in grains], dtype=np.int64)
        gray = _to_gray(image_bgr)
        means, stds = _label_mean_std(gray, labels, ids)
        for gid, m, s in zip(ids.tolist(), means.tolist(), stds.tolist()):
            r = []
            if s < options.low_contrast_std:
                r.append("low_contrast")
            if m < options.dark_threshold:
                r.append("too_dark")
            if r:
                low_contrast_reasons[int(gid)] = r

    manual_ids = {int(g.grain_id) for g in grains} & {int(i) for i in manual_excluded}

    excluded: Dict[int, List[str]] = defaultdict(list)
    for g in grains:
        gid = int(g.grain_id)
        if gid in border_ids:
            excluded[gid].append("border")
        if gid in touching_ids:
            excluded[gid].append("touching_invalid")
        for r in low_contrast_reasons.get(gid, ()):
            excluded[gid].append(r)
        if options.min_area_px and g.area_px < options.min_area_px:
            excluded[gid].append("too_small")
        if options.max_area_px and g.area_px > options.max_area_px:
            excluded[gid].append("too_large")
        if options.max_aspect_ratio and g.aspect_ratio > options.max_aspect_ratio:
            excluded[gid].append("elongated")
        if options.min_circularity and g.circularity < options.min_circularity:
            excluded[gid].append("low_circularity")
        if gid in manual_ids:
            excluded[gid].append("manual")
    excluded = dict(excluded)

    kept_ids = {int(g.grain_id) for g in grains} - set(excluded.keys())
    new_grains = [replace(g) for g in grains if int(g.grain_id) in kept_ids]
    new_labels = _zero_labels(labels, set(excluded.keys())) if labels is not None else None

    new_result = replace(raw, grains=new_grains, grain_count=len(new_grains),
                         label_image=new_labels)

    compute_statistics(new_result, image_bgr.shape[:2] if image_bgr is not None else None)

    # ---- ASTM: border grains are put back for the field count -----------
    new_result.astm = copy.deepcopy(raw.astm) if raw.astm else {}
    new_result.astm_g = raw.astm_g
    try:
        if labels is not None:
            non_border_excluded = {gid for gid, reasons in excluded.items()
                                    if any(r != "border" for r in reasons)}
            if not non_border_excluded and raw.astm:
                # Nothing that matters to ASTM (everything excluded is
                # excluded only for "border", which E112 already half-counts
                # rather than discards) changed vs. the raw field: the ASTM
                # re-evaluation would be bit-identical to ``raw.astm``, so
                # skip the comparatively expensive intercept-grid recompute
                # entirely. Keeps the common "border elimination only"
                # toggle effectively free.
                d = copy.deepcopy(raw.astm)
                note = ("Post-filter: planimetric field count/G is evaluated "
                        "with border grains RETAINED (E112 already "
                        "half-counts grains cut by the field edge); no "
                        "other post-filter exclusions are active, so this "
                        "ASTM evaluation is unchanged from the raw "
                        "analysis.")
            else:
                astm_labels = _zero_labels(labels, non_border_excluded)
                astm_grains = [replace(g) for g in grains
                               if int(g.grain_id) not in non_border_excluded]
                method = getattr(params, "astm_method", "both") if params else "both"
                factor = (getattr(params, "astm_pattern_spacing_factor", 2.0)
                          if params else 2.0)
                ppu = float(getattr(raw, "px_per_um", 0.0) or 0.0)
                if not getattr(raw, "has_calibration", ppu > 0):
                    ppu = 0.0
                ar = compute_astm(astm_labels, raw.valid_mask, ppu,
                                  grains=astm_grains, method=method,
                                  spacing_factor=factor)
                d = ar.to_dict()
                note = ("Post-filter: planimetric field count/G is evaluated "
                        "with border grains RETAINED (E112 already half-counts "
                        "grains cut by the field edge) even when "
                        "'exclude_border' hides them from the grain list and "
                        "size statistics below; other post-filter exclusions "
                        "(touching_invalid, low_contrast/false-grain, size, "
                        "shape, manual) ARE removed from the ASTM count.")
            notes = d.get("notes")
            if isinstance(notes, list):
                notes.append(note)
            else:
                d["notes"] = [note]
            new_result.astm = d
            new_result.astm_g = d.get("G_primary")
    except Exception:
        logger.exception("Post-filter ASTM re-evaluation failed")

    # ---- overlay ----------------------------------------------------------
    if labels is not None and image_bgr is not None:
        new_result.overlay_image = draw_filtered_overlay(
            image_bgr, labels, kept_ids, excluded)

    counts: Dict[str, int] = defaultdict(int)
    for reasons in excluded.values():
        for r in reasons:
            counts[r] += 1
    counts = dict(counts)

    return FilterOutcome(result=new_result, excluded=excluded, counts=counts)


# ==========================================================================
# Overlay
# ==========================================================================

def _boundary_mask(labels: np.ndarray) -> np.ndarray:
    """True where a pixel's 4-neighbour has a different label (incl. 0) —
    a single vectorised pass over the whole frame, used as the grain
    outline instead of a per-grain ``cv2.findContours`` loop (that does
    not scale to thousands of grains within the toggle-latency budget)."""
    H, W = labels.shape[:2]
    edge = np.zeros((H, W), dtype=bool)
    edge[:, :-1] |= labels[:, :-1] != labels[:, 1:]
    edge[:, 1:] |= labels[:, 1:] != labels[:, :-1]
    edge[:-1, :] |= labels[:-1, :] != labels[1:, :]
    edge[1:, :] |= labels[1:, :] != labels[:-1, :]
    return edge & (labels > 0)


def draw_filtered_overlay(image_bgr: np.ndarray, raw_labels: np.ndarray,
                          kept_ids: Set[int], excluded: Dict[int, List[str]]) -> np.ndarray:
    """Overlay: kept grains as today's colour-coded fill/outline; excluded
    grains as a translucent grey fill with a thin dashed-looking grey
    outline, so the user sees what a toggle removed.

    Fully vectorised (label lookup table + boolean masks) so it stays well
    inside the toggle-latency budget for thousands of grains — no
    per-grain contour finding.
    """
    overlay = image_bgr.copy()
    if raw_labels is None or raw_labels.size == 0:
        return overlay
    H, W = raw_labels.shape[:2]
    kept_ids = {int(k) for k in kept_ids}
    excluded_ids = {int(k) for k in excluded.keys()}

    all_ids = np.unique(raw_labels)
    all_ids = all_ids[all_ids > 0]
    if all_ids.size == 0:
        return overlay

    maxlab = int(raw_labels.max())
    lut = np.zeros((maxlab + 1, 3), dtype=np.uint8)
    hues = ((all_ids.astype(np.int64) * 137.508) % 180).astype(np.uint8)
    hsv_row = np.empty((1, all_ids.size, 3), dtype=np.uint8)
    hsv_row[0, :, 0] = hues
    hsv_row[0, :, 1] = 200
    hsv_row[0, :, 2] = 220
    lut[all_ids] = cv2.cvtColor(hsv_row, cv2.COLOR_HSV2BGR)[0]
    color_map = lut[raw_labels]

    kept_arr = np.fromiter(kept_ids, dtype=np.int64) if kept_ids else np.array([], dtype=np.int64)
    excl_arr = np.fromiter(excluded_ids, dtype=np.int64) if excluded_ids else np.array([], dtype=np.int64)
    kept_mask = np.isin(raw_labels, kept_arr) if kept_arr.size else np.zeros((H, W), dtype=bool)
    excl_mask = np.isin(raw_labels, excl_arr) if excl_arr.size else np.zeros((H, W), dtype=bool)

    # Single alpha blend pass for both kept (colour) and excluded (grey)
    # fills. Un-labelled pixels are blended with themselves
    # ((1-alpha)*O + alpha*O == O, exactly, since the two weights sum to
    # 1.0), so the result can be written back to the whole frame directly
    # instead of through a boolean fancy-index assignment (the dominant
    # cost of this function at ~2000 grains).
    alpha = 0.4
    fill = np.where(kept_mask[..., None], color_map, overlay)
    fill = np.where(excl_mask[..., None], np.uint8(160), fill)
    overlay = cv2.addWeighted(overlay, 1 - alpha, fill, alpha, 0)

    edge = _boundary_mask(raw_labels)
    kept_edge = edge & kept_mask
    excl_edge = edge & excl_mask
    if excl_edge.any():
        # dashed look: keep short runs of the boundary along a fixed
        # diagonal period (cheap: 1-D broadcast, no HxW index arrays).
        row_mod = np.arange(H) % 7
        col_mod = np.arange(W) % 7
        dash_pattern = ((row_mod[:, None] + col_mod[None, :]) % 7) < 4
        excl_edge = excl_edge & dash_pattern

    overlay[kept_edge] = color_map[kept_edge]
    grey_line = np.array([120, 120, 120], dtype=np.uint8)
    overlay[excl_edge] = grey_line

    return overlay
