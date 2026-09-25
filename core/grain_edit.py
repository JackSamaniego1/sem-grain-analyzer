"""
Manual grain edits on the label image (UI-05 / INN-04).
========================================================

Pure numpy / scipy / cv2 — no Qt.  The Review canvas collects the user's
gestures (lasso loop, merge of a selection, cut line) and calls into this
module; the app state wraps every edit in an undo command.

Edits and the non-destructive pattern
-------------------------------------
Hand *removal* of grains stays what it always was: an id list
(``manual_excluded``) fed to :mod:`core.postfilter`.  A lasso selection is
only a selection — deleting it goes through that same path.

*Merge* and *split* change the grain geometry, so they produce a new label
image.  Every edit is described by a small JSON-able ``op`` dict, stored in
the session manifest as ``grain_edits`` next to the already-edited label
image (``results/*.labels.npz``), so the detector's original output
(``detector_label_image``) is never lost and every edit is auditable, even
though the op list itself is not replayed on load — the edited labels are
saved and reloaded directly:

``{"op": "merge", "ids": [4, 9, 12], "into": 4, "gap_px": 3}``
    grains 9 and 12 become part of grain 4; background pixels (grain
    boundary lines) lying within ``gap_px`` of two merged grains are filled
    so the merged grain has no internal boundary.
``{"op": "split", "id": 7, "line": [[x, y], ...], "new_ids": [31]}``
    grain 7 is cut along the polyline; the largest piece keeps id 7, the
    other piece(s) get ``new_ids``.  Cut pixels are given back to the nearest
    piece, so the grain's total area is unchanged.

Measurements of the affected grains are recomputed with exactly the
detector's own measuring code (``GrainDetector._measure_grains``) on a
padded crop, so a merged / split grain is measured the same way a detected
one is.  :func:`remeasure_after_edit` clears the result's ASTM dict so the
post-filter re-evaluates E112 on the edited labels (see
``core.postfilter.apply_post_filters``).

Coordinates
-----------
All coordinates here are *label-image* pixel coordinates ``(x, y)``.
The detector's label image may be in analysed-region (auto-cropped)
coordinates while the canvas shows the full frame; use
:func:`label_offset` + :func:`to_label_coords` to map canvas points.
"""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np
from scipy import ndimage as ndi

logger = logging.getLogger(__name__)

MIN_PIECE_PX = 5          # smallest region GrainDetector._measure_grains keeps
DEFAULT_GAP_PX = 3        # boundary-line width bridged when merging
_EIGHT = np.ones((3, 3), dtype=bool)


class GrainEditError(ValueError):
    """An edit that cannot be applied; the message is shown to the user."""


@dataclass
class EditOutcome:
    labels: np.ndarray                       # new label image (a copy)
    changed: List[int] = field(default_factory=list)   # ids to (re)measure
    removed: List[int] = field(default_factory=list)   # ids that no longer exist
    op: dict = field(default_factory=dict)   # JSON-able description (replayable)


# ======================================================================
# Coordinate mapping (canvas / full frame -> label image)
# ======================================================================

def label_offset(label_shape: Sequence[int], image_shape: Sequence[int],
                 auto_crop_rect: Optional[Sequence[int]] = None) -> Tuple[int, int]:
    """``(dx, dy)`` such that ``label_xy = image_xy - (dx, dy)``.

    ``auto_crop_rect`` is the detector's ``(r0, c0, r1, c1)`` of the
    analysed sub-image.  Full-frame labels (the UI pads them) give (0, 0)."""
    lh, lw = int(label_shape[0]), int(label_shape[1])
    ih, iw = int(image_shape[0]), int(image_shape[1])
    if (lh, lw) == (ih, iw):
        return 0, 0
    if auto_crop_rect is not None and len(auto_crop_rect) == 4:
        r0, c0, r1, c1 = (int(v) for v in auto_crop_rect)
        if (r1 - r0, c1 - c0) == (lh, lw):
            return c0, r0
    raise GrainEditError("The grain map does not line up with the image — "
                         "analyse the image again before editing grains.")


def to_label_coords(points, offset: Tuple[int, int] = (0, 0)) -> np.ndarray:
    """(N, 2) float array of ``(x, y)`` in label-image coordinates."""
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    return pts - np.asarray(offset, dtype=np.float64)[None, :]


def _int_pts(points) -> np.ndarray:
    return np.round(np.asarray(points, dtype=np.float64).reshape(-1, 2)).astype(np.int32)


# ======================================================================
# Lasso selection
# ======================================================================

def grains_in_polygon(labels: np.ndarray, polygon, candidates: Optional[Iterable[int]] = None,
                      min_fraction: float = 0.5) -> List[int]:
    """Grain ids with at least ``min_fraction`` of their area inside the
    closed ``polygon`` (label coords).  ``candidates`` limits the result
    (e.g. only grains that are currently kept)."""
    if labels is None or labels.size == 0:
        return []
    pts = _int_pts(polygon)
    if len(pts) < 3:
        return []
    H, W = labels.shape[:2]
    x0, y0 = max(0, int(pts[:, 0].min())), max(0, int(pts[:, 1].min()))
    x1, y1 = min(W, int(pts[:, 0].max()) + 1), min(H, int(pts[:, 1].max()) + 1)
    if x1 <= x0 or y1 <= y0:
        return []
    mask = np.zeros((y1 - y0, x1 - x0), dtype=np.uint8)
    cv2.fillPoly(mask, [pts - np.array([x0, y0], dtype=np.int32)], 1)
    sub = labels[y0:y1, x0:x1]
    inside_ids = sub[(mask > 0) & (sub > 0)]
    if inside_ids.size == 0:
        return []
    n = int(labels.max()) + 1
    inside = np.bincount(inside_ids.astype(np.int64, copy=False), minlength=n)
    total = np.bincount(labels.ravel().astype(np.int64, copy=False), minlength=n)
    ids = np.nonzero(inside)[0]
    ids = ids[(ids > 0) & (inside[ids] >= min_fraction * np.maximum(total[ids], 1))]
    out = [int(i) for i in ids]
    if candidates is not None:
        allowed = {int(c) for c in candidates}
        out = [i for i in out if i in allowed]
    return out


# ======================================================================
# Merge
# ======================================================================

def _bbox(mask: np.ndarray, pad: int, shape) -> Optional[Tuple[int, int, int, int]]:
    rows = np.flatnonzero(mask.any(axis=1))
    if rows.size == 0:
        return None
    cols = np.flatnonzero(mask.any(axis=0))
    H, W = shape[:2]
    return (max(0, int(rows[0]) - pad), max(0, int(cols[0]) - pad),
            min(H, int(rows[-1]) + pad + 1), min(W, int(cols[-1]) + pad + 1))


def _shift(a: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """``out[y, x] = a[y - dy, x - dx]`` (zero-filled)."""
    out = np.zeros_like(a)
    H, W = a.shape
    ys, yd = (slice(0, H - dy), slice(dy, H)) if dy >= 0 else (slice(-dy, H), slice(0, H + dy))
    xs, xd = (slice(0, W - dx), slice(dx, W)) if dx >= 0 else (slice(-dx, W), slice(0, W + dx))
    out[yd, xd] = a[ys, xs]
    return out


def _boundary_bridge(sub: np.ndarray, member: np.ndarray, gap: int) -> np.ndarray:
    """Background pixels on a straight (horizontal / vertical / diagonal)
    run of at most ``gap`` background pixels that has a *different* merged
    grain at each end — i.e. the boundary line between two merged grains,
    not the outer background around them."""
    lab = np.where(member, sub, 0).astype(np.int64)
    blocked = (sub > 0) & ~member            # another grain stops the search
    bridge = np.zeros(sub.shape, dtype=bool)
    for dy, dx in ((0, 1), (1, 0), (1, 1), (1, -1)):
        ends = []
        for sgn in (1, -1):
            found = np.zeros(sub.shape, dtype=np.int64)     # grain id at the run end
            dist = np.zeros(sub.shape, dtype=np.int64)
            stop_ = np.zeros(sub.shape, dtype=bool)
            for k in range(1, gap + 1):
                g = _shift(lab, sgn * k * dy, sgn * k * dx)
                b = _shift(blocked, sgn * k * dy, sgn * k * dx)
                hit = ~stop_ & (g > 0)
                found[hit] = g[hit]
                dist[hit] = k
                stop_ |= hit | b
            ends.append((found, dist))
        (fa, da), (fb, db) = ends
        bridge |= (sub == 0) & (fa > 0) & (fb > 0) & (fa != fb) & (da + db - 1 <= gap)
    return bridge


def merge_grains(labels: np.ndarray, ids: Iterable[int], gap_px: int = DEFAULT_GAP_PX,
                 valid_mask: Optional[np.ndarray] = None, into: Optional[int] = None
                 ) -> EditOutcome:
    """Merge 2+ touching grains into one (the smallest id, or ``into``).

    Raises :class:`GrainEditError` when fewer than two of the ids exist or
    when the grains do not touch (allowing boundary lines up to ``gap_px``)."""
    if labels is None:
        raise GrainEditError("This image has no grain map to edit.")
    ids = sorted({int(i) for i in ids if int(i) > 0})
    present = set(np.unique(labels[np.isin(labels, ids)]).tolist()) if ids else set()
    ids = [i for i in ids if i in present]
    if len(ids) < 2:
        raise GrainEditError("Select at least two grains to merge.")
    into = int(into) if into is not None and int(into) in ids else ids[0]
    gap = max(0, int(gap_px))
    member_full = np.isin(labels, ids)
    bb = _bbox(member_full, gap + 1, labels.shape)
    r0, c0, r1, c1 = bb
    sub = labels[r0:r1, c0:c1]
    member = member_full[r0:r1, c0:c1]
    union = member.copy()
    if gap > 0:
        bridge = _boundary_bridge(sub, member, gap)
        if valid_mask is not None and valid_mask.shape[:2] == labels.shape[:2]:
            bridge &= valid_mask[r0:r1, c0:c1].astype(bool)
        union |= bridge
    _, n = ndi.label(union, structure=_EIGHT)
    if n != 1:
        raise GrainEditError("The selected grains do not touch each other — only "
                             "neighbouring grains can be merged.")
    out = labels.copy()
    out[r0:r1, c0:c1][union] = into
    return EditOutcome(labels=out, changed=[into], removed=[i for i in ids if i != into],
                       op={"op": "merge", "ids": ids, "into": into, "gap_px": gap})


# ======================================================================
# Split
# ======================================================================

def grain_under_line(labels: np.ndarray, polyline,
                     candidates: Optional[Iterable[int]] = None) -> int:
    """The grain crossed by most pixels of ``polyline`` (label coords), 0 if
    none.  ``candidates`` limits which grains count."""
    if labels is None or labels.size == 0:
        return 0
    pts = _int_pts(polyline)
    if len(pts) < 2:
        return 0
    canvas = np.zeros(labels.shape[:2], dtype=np.uint8)
    cv2.polylines(canvas, [pts], False, 1, thickness=1, lineType=cv2.LINE_4)
    hit = labels[(canvas > 0) & (labels > 0)]
    if candidates is not None:
        allowed = np.fromiter((int(c) for c in candidates), dtype=np.int64)
        hit = hit[np.isin(hit, allowed)]
    if hit.size == 0:
        return 0
    vals, counts = np.unique(hit, return_counts=True)
    return int(vals[int(np.argmax(counts))])


def split_grain(labels: np.ndarray, polyline, grain_id: Optional[int] = None,
                new_ids: Optional[Sequence[int]] = None,
                candidates: Optional[Iterable[int]] = None) -> EditOutcome:
    """Cut one grain along ``polyline`` (label coords).

    The largest piece keeps ``grain_id``; the others get ``new_ids`` (default
    ``max(labels) + 1, ...``).  Cut pixels and crumbs smaller than
    :data:`MIN_PIECE_PX` go to the nearest piece, so the total area is
    unchanged.  Raises :class:`GrainEditError` if the line does not cut the
    grain into at least two real pieces."""
    if labels is None:
        raise GrainEditError("This image has no grain map to edit.")
    line = np.asarray(polyline, dtype=np.float64).reshape(-1, 2)
    if len(line) < 2:
        raise GrainEditError("Draw a cut line across the grain.")
    gid = int(grain_id) if grain_id else grain_under_line(labels, line, candidates)
    if gid <= 0:
        raise GrainEditError("The cut line does not cross a grain.")
    gmask_full = labels == gid
    bb = _bbox(gmask_full, 1, labels.shape)
    if bb is None:
        raise GrainEditError(f"Grain #{gid} no longer exists.")
    r0, c0, r1, c1 = bb
    g = gmask_full[r0:r1, c0:c1]
    cut = np.zeros(g.shape, dtype=np.uint8)
    cv2.polylines(cut, [_int_pts(line) - np.array([c0, r0], dtype=np.int32)], False, 1,
                  thickness=1, lineType=cv2.LINE_4)   # 4-connected: blocks 8-connectivity
    pieces, n = ndi.label(g & (cut == 0), structure=_EIGHT)
    sizes = np.bincount(pieces.ravel(), minlength=n + 1)
    big = [i for i in range(1, n + 1) if sizes[i] >= MIN_PIECE_PX]
    if len(big) < 2:
        raise GrainEditError("Draw the cut line all the way across the grain, "
                             "from one edge to the other.")
    big.sort(key=lambda i: (-int(sizes[i]), i))
    piece_lab = np.zeros(g.shape, dtype=np.int32)
    for k, i in enumerate(big, start=1):
        piece_lab[pieces == i] = k
    leftover = g & (piece_lab == 0)
    if leftover.any():
        idx = ndi.distance_transform_edt(piece_lab == 0, return_distances=False,
                                         return_indices=True)
        piece_lab[leftover] = piece_lab[idx[0][leftover], idx[1][leftover]]
    n_new = len(big) - 1
    if new_ids is None or len(new_ids) != n_new:
        start = int(labels.max()) + 1
        new_ids = list(range(start, start + n_new))
    new_ids = [int(i) for i in new_ids]
    if min(new_ids) <= 0 or np.isin(labels, new_ids).any():
        raise GrainEditError("Grain numbers for the new pieces are already in use.")
    lut = np.array([0, gid] + new_ids, dtype=labels.dtype)
    out = labels.copy()
    region = out[r0:r1, c0:c1]
    region[g] = lut[piece_lab[g]]
    rounded = [[round(float(x), 1), round(float(y), 1)] for x, y in line]
    return EditOutcome(labels=out, changed=[gid] + new_ids, removed=[],
                       op={"op": "split", "id": gid, "line": rounded, "new_ids": new_ids})


# ======================================================================
# Measurements
# ======================================================================

def measure_ids(labels: np.ndarray, ids: Iterable[int], px_per_um: float) -> list:
    """``GrainResult`` for each id, measured exactly like the detector does
    (on a 1-px padded crop; coordinates are full label-image coordinates)."""
    from core.grain_detector import DetectionParams, GrainDetector
    from dataclasses import replace

    ids = sorted({int(i) for i in ids if int(i) > 0})
    if not ids or labels is None:
        return []
    member = np.isin(labels, ids)
    bb = _bbox(member, 1, labels.shape)
    if bb is None:
        return []
    r0, c0, r1, c1 = bb
    sub = np.where(member[r0:r1, c0:c1], labels[r0:r1, c0:c1], 0)
    p = DetectionParams(min_grain_size_px=0, max_grain_size_px=0)
    grains = GrainDetector()._measure_grains(sub, p, float(px_per_um or 0.0))
    out = []
    for g in grains:
        b = g.bbox
        out.append(replace(g, centroid_x=g.centroid_x + c0, centroid_y=g.centroid_y + r0,
                           bbox=(b[0] + r0, b[1] + c0, b[2] + r0, b[3] + c0)))
    return out


def remeasure_after_edit(raw, outcome: EditOutcome, image_shape=None):
    """New raw ``AnalysisResult`` (``raw`` is not mutated) with the edited
    labels, the affected grains re-measured and summary statistics
    recomputed.  ``astm`` is cleared so the post-filter re-evaluates E112 on
    the edited labels."""
    from core.metrics import compute_statistics

    r = copy.copy(raw)
    drop = set(outcome.changed) | set(outcome.removed)
    ppu = float(getattr(raw, "px_per_um", 0.0) or 0.0)
    keep = [g for g in (raw.grains or []) if int(g.grain_id) not in drop]
    new = measure_ids(outcome.labels, outcome.changed, ppu)
    r.grains = sorted(keep + new, key=lambda g: g.grain_id)
    r.grain_count = len(r.grains)
    r.label_image = outcome.labels
    r.astm = {}
    r.astm_g = None
    compute_statistics(r, image_shape or outcome.labels.shape[:2])
    return r


__all__ = [
    "GrainEditError", "EditOutcome", "MIN_PIECE_PX", "DEFAULT_GAP_PX",
    "label_offset", "to_label_coords", "grains_in_polygon", "merge_grains",
    "grain_under_line", "split_grain", "measure_ids",
    "remeasure_after_edit",
]
