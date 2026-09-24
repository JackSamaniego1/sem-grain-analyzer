"""
Grain statistics — the single implementation of summary metrics.

Every place that needs summary statistics for an ``AnalysisResult`` (the
detector after segmentation, border-grain discard, manual grain deletion in
the UI) must call :func:`compute_statistics` so the numbers cannot drift.

Coverage definition
-------------------
Area fraction (ASTM E1245 / E562 "area fraction" A_A) is the grain area
divided by the area of the *test field*.  Pixels that carry no specimen
information — pure-black info bars, detector drop-outs, masked-off regions
(the detector's ``valid_mask``) — are not part of the test field, so:

    grain_coverage_pct      = sum(grain area) / valid area * 100
    total_analyzed_area_um2 = valid area in um^2

No Qt imports here (core/ is UI-agnostic).
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np


def _frame_px(result, image_shape: Optional[Tuple[int, ...]]) -> float:
    if image_shape is not None:
        return float(image_shape[0] * image_shape[1])
    if getattr(result, "label_image", None) is not None:
        h, w = result.label_image.shape[:2]
        return float(h * w)
    return 0.0


def valid_area_px(result, image_shape: Optional[Tuple[int, ...]] = None) -> float:
    """Area of the analysed test field in pixels.

    Priority: the scalar ``result.valid_area_px`` set by the detector, then
    ``result.valid_mask``, then the whole frame (legacy behaviour).
    """
    v = float(getattr(result, "valid_area_px", 0.0) or 0.0)
    if v > 0:
        return v
    vm = getattr(result, "valid_mask", None)
    if vm is not None:
        return float(np.count_nonzero(vm))
    return _frame_px(result, image_shape)


def compute_statistics(result, image_shape: Optional[Tuple[int, ...]] = None):
    """Recompute all summary fields of ``result`` in place and return it.

    ``image_shape`` is only used as a fallback test-field size when the
    result carries no valid-area information (results produced by older
    code paths).
    """
    frame = _frame_px(result, image_shape)
    vpx = valid_area_px(result, image_shape)
    result.valid_area_px = vpx
    ppu = float(result.px_per_um or 0.0)
    calibrated = bool(result.has_calibration) and ppu > 0
    result.valid_area_um2 = vpx / ppu ** 2 if calibrated else 0.0
    if frame > 0:
        result.invalid_area_pct = max(0.0, (frame - vpx) / frame * 100.0)

    grains = list(result.grains or [])
    result.grain_count = len(grains)

    # reset everything so stale values never survive a recompute
    for name in ("mean_area_um2", "std_area_um2", "median_area_um2",
                 "min_area_um2", "max_area_um2", "mean_diameter_um",
                 "std_diameter_um", "mean_circularity", "mean_aspect_ratio",
                 "grain_coverage_pct"):
        setattr(result, name, 0.0)
    result.total_analyzed_area_um2 = result.valid_area_um2

    if not grains:
        return result

    area_px = np.array([g.area_px for g in grains], dtype=np.float64)
    if vpx > 0:
        # dimensionless, identical whether computed in px or um^2
        result.grain_coverage_pct = float(area_px.sum() / vpx * 100.0)

    if calibrated:
        areas = np.array([g.area_um2 for g in grains], dtype=np.float64)
        diams = np.array([g.equivalent_diameter_um for g in grains],
                         dtype=np.float64)
        result.mean_area_um2 = float(np.mean(areas))
        result.std_area_um2 = float(np.std(areas))
        result.median_area_um2 = float(np.median(areas))
        result.min_area_um2 = float(np.min(areas))
        result.max_area_um2 = float(np.max(areas))
        result.mean_diameter_um = float(np.mean(diams))
        result.std_diameter_um = float(np.std(diams))

    result.mean_circularity = float(np.mean([g.circularity for g in grains]))
    result.mean_aspect_ratio = float(np.mean([g.aspect_ratio for g in grains]))
    return result
