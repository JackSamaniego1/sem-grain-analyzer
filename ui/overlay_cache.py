"""
On-demand grain overlays (memory for large loads).

Analysed images that are not in use keep their label maps compressed
(``core.result_pack``) and no overlay at all.  Whoever needs an overlay —
the report builder / exports, a thumbnail, the review of an image — asks
for it here: it is drawn again from the pixels + the grain labels + the
current exclusions, exactly like the grain filters draw it, and the last
few are kept in a small LRU (like the pixel cache).

No Qt: :class:`OverlayJob` is a plain callable captured on the GUI thread
and run on a pool thread (``reports.model.ReportImageInput.overlay_loader``).
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Dict, List, Optional

import numpy as np

from core.result_pack import is_packed, unpacked_copy

OVERLAY_CACHE_MAX = 4

__all__ = ["OverlayCache", "OverlayJob", "regenerate_overlay", "OVERLAY_CACHE_MAX"]


class OverlayCache:
    """Thread-safe LRU of regenerated overlays keyed by (uid, filter_gen)."""

    def __init__(self, max_items: int = OVERLAY_CACHE_MAX) -> None:
        self.max_items = max_items
        self._d: "OrderedDict[object, np.ndarray]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key) -> Optional[np.ndarray]:
        with self._lock:
            v = self._d.get(key)
            if v is not None:
                self._d.move_to_end(key)
            return v

    def put(self, key, arr: Optional[np.ndarray]) -> None:
        if arr is None:
            return
        with self._lock:
            self._d[key] = arr
            self._d.move_to_end(key)
            while len(self._d) > self.max_items:
                self._d.popitem(last=False)

    def discard_uid(self, uid) -> None:
        with self._lock:
            for k in [k for k in self._d if k[0] == uid]:
                del self._d[k]

    def clear(self) -> None:
        with self._lock:
            self._d.clear()

    def __len__(self) -> int:
        return len(self._d)

    def nbytes(self) -> int:
        with self._lock:
            return sum(int(a.nbytes) for a in self._d.values())


def regenerate_overlay(image_bgr: Optional[np.ndarray], raw, result,
                       excluded: Dict[int, List[str]]) -> Optional[np.ndarray]:
    """The filtered overlay of one image, drawn again (worker-safe).

    ``raw``: the image's raw result (labels of EVERY grain, may be packed);
    ``result``: its filtered result (the kept grains); ``excluded``: grain
    id -> reasons.  Same drawing as ``ui.filtering.filter_image``."""
    from core.postfilter import draw_filtered_overlay
    from ui.filtering import analysed_rect, draw_analysed_outline
    if image_bgr is None or raw is None:
        return None
    if is_packed(raw):
        raw = unpacked_copy(raw)
    lab = getattr(raw, "label_image", None)
    if lab is None or lab.shape[:2] != image_bgr.shape[:2]:
        return None
    src = result if result is not None else raw
    kept = {int(g.grain_id) for g in src.grains}
    ov = draw_filtered_overlay(image_bgr, lab, kept, dict(excluded or {}))
    rect = analysed_rect(raw, lab.shape[:2])
    if ov is not None and rect is not None:
        draw_analysed_outline(ov, rect)
    return ov


class OverlayJob:
    """Callable returning one image's current overlay (BGR ndarray or None).

    Captured on the GUI thread with shallow copies (packing / edits only
    rebind attributes, so the snapshot stays valid); called on any thread.
    """

    def __init__(self, *, key, path, image_bgr, raw, result, excluded,
                 overlay: Optional[np.ndarray] = None,
                 cache: Optional[OverlayCache] = None) -> None:
        import copy
        self.key = key
        self.path = str(path) if path else ""
        self.image_bgr = image_bgr
        self.raw = copy.copy(raw) if raw is not None else None
        self.result = copy.copy(result) if result is not None else None
        self.excluded = {int(k): list(v) for k, v in (excluded or {}).items()}
        self.overlay = overlay
        self.cache = cache

    def __call__(self) -> Optional[np.ndarray]:
        if self.overlay is not None:
            return self.overlay
        if self.cache is not None:
            hit = self.cache.get(self.key)
            if hit is not None:
                return hit
        img = self.image_bgr
        if img is None and self.path:
            from ui.workers import read_image
            try:
                img = read_image(self.path)
            except Exception:
                img = None
        ov = regenerate_overlay(img, self.raw, self.result, self.excluded)
        if self.cache is not None:
            self.cache.put(self.key, ov)
        return ov
