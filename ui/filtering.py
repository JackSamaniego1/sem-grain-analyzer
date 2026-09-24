"""
Non-destructive grain post-filters for the UI (border / false-grain removal,
size & shape limits, manual exclusions).

The algorithms live in ``core.postfilter`` (owned by the detection team);
this module only adapts them to the UI's full-frame results.

``filter_image`` adapts the call to the UI's full-frame results: it crops the
raw result to the *analysed area* (bounding box of the valid mask — i.e. the
scan area and/or the detector's auto-crop) so that "touches the border" means
"touches the edge of what was analysed", then pads the outcome back.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, replace
from typing import Dict, Iterable, List, Optional, Set

import numpy as np

from core.grain_detector import AnalysisResult, DetectionParams, GrainDetector
from core.metrics import compute_statistics
from core.overlay_compose import (
    analysed_rect_from_mask, compose_full_overlay, draw_analysed_outline,
)

from core.postfilter import (
    FilterOutcome, PostFilterOptions, apply_post_filters, draw_filtered_overlay,
)

REASON_LABELS = {
    "border": "Cut by the edge of the analysed area",
    "touching_invalid": "Touches an excluded (black) region",
    "low_contrast": "Low contrast — likely not a real grain",
    "too_dark": "Too dark — likely a void / black region",
    "too_small": "Smaller than the minimum area",
    "too_large": "Larger than the maximum area",
    "elongated": "Aspect ratio above the limit",
    "low_circularity": "Circularity below the limit",
    "manual": "Removed by hand",
}

FALSE_REASONS = ("low_contrast", "too_dark", "touching_invalid")


def reason_text(reasons: Iterable[str]) -> str:
    return "; ".join(REASON_LABELS.get(r, r) for r in reasons)


def preview_params(params: Optional[DetectionParams]) -> DetectionParams:
    """Cheaper ASTM evaluation (planimetric only) for interactive previews;
    the session's full method runs once the user stops changing filters."""
    return replace(params or DetectionParams(), astm_method="planimetric")


def default_options(scan_rect=None) -> PostFilterOptions:
    """v2.3 discarded border grains whenever a scan area was set — that is now
    the *default* of a visible toggle rather than hidden behaviour."""
    return PostFilterOptions(exclude_border=bool(scan_rect))


def options_from_dict(d: Optional[dict]) -> PostFilterOptions:
    d = dict(d or {})
    for k in ("min_area_px", "max_area_px", "dark_threshold"):
        if k in d:
            try:
                d[k] = int(round(float(d[k] or 0)))
            except (TypeError, ValueError):
                d.pop(k)
    try:
        return PostFilterOptions.from_dict(d)
    except Exception:
        return PostFilterOptions()


def options_to_dict(o: PostFilterOptions) -> dict:
    try:
        return o.to_dict()
    except Exception:
        return asdict(o)


# ======================================================================
# Full-frame adapter
# ======================================================================

def analysed_rect(result: AnalysisResult, shape) -> Optional[tuple]:
    """(x, y, w, h) of the analysed area, or None for the whole frame."""
    return analysed_rect_from_mask(getattr(result, "valid_mask", None), shape)


def _shift_grains(grains, dx: int, dy: int):
    out = []
    for g in grains:
        bb = g.bbox
        if bb and len(bb) == 4:
            bb = (bb[0] + dy, bb[1] + dx, bb[2] + dy, bb[3] + dx)
        out.append(replace(g, centroid_x=g.centroid_x + dx, centroid_y=g.centroid_y + dy, bbox=bb))
    return out


def _crop_result(raw: AnalysisResult, rect) -> AnalysisResult:
    x, y, w, h = rect
    r = copy.copy(raw)
    r.grains = _shift_grains(raw.grains, -x, -y)
    for name in ("label_image", "binary_image", "valid_mask", "overlay_image"):
        a = getattr(raw, name, None)
        if a is not None:
            setattr(r, name, a[y:y + h, x:x + w].copy())
    return r


def filter_image(raw: AnalysisResult, image_bgr: Optional[np.ndarray],
                 options: PostFilterOptions, manual: Set[int] = frozenset(),
                 params: Optional[DetectionParams] = None) -> dict:
    """Apply post-filters to a full-frame raw result (off the GUI thread).

    Returns ``{"result", "excluded", "counts"}`` with ``result`` in full-frame
    coordinates (``label_image`` = kept grains only)."""
    if raw is None:
        return {"result": None, "excluded": {}, "counts": {}}
    lab = raw.label_image
    manual = frozenset(int(i) for i in manual)
    if lab is None or image_bgr is None:
        out = apply_post_filters(raw, image_bgr, options, manual, params)
        return {"result": out.result, "excluded": dict(out.excluded), "counts": dict(out.counts)}
    H, W = lab.shape[:2]
    rect = analysed_rect(raw, (H, W))
    if rect is None:
        out = apply_post_filters(raw, image_bgr, options, manual, params)
        res = out.result
    else:
        x, y, w, h = rect
        out = apply_post_filters(_crop_result(raw, rect), image_bgr[y:y + h, x:x + w],
                                 options, manual, params)
        res = copy.copy(out.result)
        res.grains = _shift_grains(out.result.grains, x, y)
        for name, fill, dt in (("label_image", 0, None), ("binary_image", 0, None),
                               ("valid_mask", False, bool)):
            a = getattr(out.result, name, None)
            if a is not None:
                full = np.full((H, W), fill, dtype=dt or a.dtype)
                full[y:y + h, x:x + w] = a
                setattr(res, name, full)
        ov = getattr(out.result, "overlay_image", None)
        if ov is not None and ov.shape[:2] == (h, w):
            # Full original frame, crop overlay at its offset, analysed
            # region outlined (exports keep the SEM data bar).
            res.overlay_image = compose_full_overlay(image_bgr, ov, rect)
        else:
            res.overlay_image = None
        compute_statistics(res, (H, W))
    excluded = {int(k): list(v) for k, v in dict(out.excluded).items()}
    if res.overlay_image is None or res.overlay_image.shape[:2] != (H, W):
        kept = [g.grain_id for g in res.grains]
        try:
            res.overlay_image = draw_filtered_overlay(image_bgr, lab, kept, excluded)
        except Exception:
            if res.label_image is not None:
                res.overlay_image = GrainDetector()._draw_overlay(
                    image_bgr, res.label_image, res.grains)
        if res.overlay_image is not None and rect is not None:
            draw_analysed_outline(res.overlay_image, rect)
    return {"result": res, "excluded": excluded, "counts": dict(out.counts)}


def remeasure_excluded(saved: AnalysisResult, excluded_ids: Iterable[int]) -> AnalysisResult:
    """Rebuild the RAW result from a saved (filtered) one: the saved label
    image keeps every raw grain, grains.json holds only the kept ones, so the
    excluded grains are measured again from their labels."""
    raw = copy.copy(saved)
    raw.grains = list(saved.grains)
    lab = saved.label_image
    ids = sorted({int(i) for i in excluded_ids} - {g.grain_id for g in saved.grains})
    if lab is not None and ids:
        sub = np.where(np.isin(lab, np.asarray(ids, dtype=lab.dtype)), lab, 0)
        p = DetectionParams(min_grain_size_px=0, max_grain_size_px=0)
        extra = GrainDetector()._measure_grains(sub, p, float(saved.px_per_um or 0.0))
        raw.grains = sorted(raw.grains + extra, key=lambda g: g.grain_id)
    compute_statistics(raw, lab.shape if lab is not None else None)
    return raw


def counts_summary(counts: Dict[str, int]) -> dict:
    """Numbers for the two headline toggles."""
    return {"border": int(counts.get("border", 0)),
            "false": int(sum(counts.get(r, 0) for r in FALSE_REASONS)),
            "shape": int(sum(counts.get(r, 0) for r in
                             ("too_small", "too_large", "elongated", "low_circularity"))),
            "manual": int(counts.get("manual", 0))}


__all__ = [
    "PostFilterOptions", "FilterOutcome", "apply_post_filters", "draw_filtered_overlay",
    "REASON_LABELS", "preview_params", "FALSE_REASONS", "reason_text",
    "default_options", "options_from_dict", "options_to_dict", "filter_image",
    "remeasure_excluded", "analysed_rect", "counts_summary",
]
