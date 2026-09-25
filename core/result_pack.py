"""
Compact in-memory form of an analysis result's pixel-sized arrays.

A full-resolution analysis result carries several image-sized arrays: the
grain label map (int32, 4 bytes/pixel), the binary map, the valid-area mask
and a BGR overlay (3 bytes/pixel).  With hundreds of images loaded that is
gigabytes, while the numbers that matter (per-grain measurements) are tiny.

:func:`pack_result` swaps those arrays for zlib-compressed copies kept on
the result object itself (label maps are long runs of equal values, so they
shrink ~20-100x) and drops the overlay — it is regenerated on demand from
the pixels + labels (see ``ui.overlay_cache``).  :func:`unpack_result`
restores the arrays exactly.  Keeping the packed data ON the result object
means an undo command that swaps results around always carries its own
arrays.

No Qt here: safe on worker threads.  ``pack_result`` / ``unpack_result`` only
REBIND attributes (never write into an array), so a shallow ``copy.copy`` of
a result taken before packing keeps working on another thread.
"""
from __future__ import annotations

import zlib
from typing import Any, Dict, Optional

import numpy as np

__all__ = ["PackedArray", "PACKED_FIELDS", "pack_array", "pack_result", "unpack_result",
           "is_packed", "unpacked_copy", "array_bytes", "packed_bytes", "live_bytes"]

# Image-sized arrays of a core.grain_detector.AnalysisResult
PACKED_FIELDS = ("label_image", "binary_image", "valid_mask", "detector_label_image")
_ATTR = "_packed_arrays"


class PackedArray:
    """zlib-compressed ndarray (exact round trip, dtype and shape kept)."""

    __slots__ = ("shape", "dtype", "store_dtype", "blob")

    def __init__(self, arr: np.ndarray, level: int = 1) -> None:
        a = np.ascontiguousarray(arr)
        self.shape = a.shape
        self.dtype = a.dtype
        store = a
        # label maps: most images have < 65 536 grains -> half the bytes to squeeze
        if a.dtype.kind in "iu" and a.itemsize > 2 and a.size:
            lo, hi = int(a.min()), int(a.max())
            if lo >= 0 and hi <= 0xFFFF:
                store = a.astype(np.uint16)
        elif a.dtype == bool:
            store = np.packbits(a.ravel())
        self.store_dtype = store.dtype
        self.blob = zlib.compress(store.tobytes(), level)

    @property
    def nbytes(self) -> int:
        return len(self.blob)

    def unpack(self) -> np.ndarray:
        raw = np.frombuffer(zlib.decompress(self.blob), dtype=self.store_dtype)
        if self.dtype == bool:
            n = int(np.prod(self.shape)) if self.shape else 0
            return np.unpackbits(raw)[:n].astype(bool).reshape(self.shape)
        return raw.astype(self.dtype).reshape(self.shape)  # astype copies -> writeable


def pack_array(arr: Optional[np.ndarray], memo: Optional[dict] = None) -> Optional[PackedArray]:
    if arr is None:
        return None
    if memo is not None:
        hit = memo.get(id(arr))
        if hit is not None:
            return hit[1]
    p = PackedArray(arr)
    if memo is not None:
        memo[id(arr)] = (arr, p)          # keep arr alive so id() stays unique
    return p


def is_packed(res: Any) -> bool:
    return res is not None and getattr(res, _ATTR, None) is not None


def pack_result(res: Any, memo: Optional[dict] = None, drop_overlay: bool = True) -> bool:
    """Compress ``res``'s image-sized arrays in place.  ``memo`` shares one
    packed copy between results that share an array (raw and filtered
    results often do).  Returns True when anything changed."""
    if res is None or is_packed(res):
        return False
    memo = {} if memo is None else memo
    packed: Dict[str, Any] = {}
    for name in PACKED_FIELDS:
        a = getattr(res, name, None)
        if isinstance(a, np.ndarray):
            packed[name] = pack_array(a, memo)
            setattr(res, name, None)
    if drop_overlay and getattr(res, "overlay_image", None) is not None:
        res.overlay_image = None
    setattr(res, _ATTR, packed)
    return True


def unpack_result(res: Any, memo: Optional[dict] = None) -> bool:
    """Restore the arrays packed by :func:`pack_result` (the overlay stays
    None until something regenerates it).  Returns True when anything
    changed."""
    if not is_packed(res):
        return False
    memo = {} if memo is None else memo
    for name, p in getattr(res, _ATTR).items():
        if p is None:
            continue
        arr = memo.get(id(p))
        if arr is None:
            arr = p.unpack()
            memo[id(p)] = arr
        setattr(res, name, arr)
    setattr(res, _ATTR, None)
    return True


def unpacked_copy(res: Any) -> Any:
    """Shallow copy of ``res`` with its arrays unpacked (``res`` itself is
    left as it is — use on a worker thread for a packed result)."""
    import copy
    if res is None:
        return None
    r = copy.copy(res)
    if is_packed(res):
        setattr(r, _ATTR, dict(getattr(res, _ATTR)))
        unpack_result(r)
    return r


def array_bytes(arr: Any) -> int:
    return int(arr.nbytes) if isinstance(arr, np.ndarray) else 0


def packed_bytes(res: Any, seen: Optional[set] = None) -> int:
    if not is_packed(res):
        return 0
    seen = set() if seen is None else seen
    total = 0
    for p in getattr(res, _ATTR).values():
        if p is not None and id(p) not in seen:
            seen.add(id(p))
            total += p.nbytes
    return total


def live_bytes(res: Any, seen: Optional[set] = None) -> int:
    """Bytes of image-sized arrays (labels, masks, overlay) plus packed
    blobs held by ``res`` (arrays shared with another result counted once
    via ``seen``)."""
    if res is None:
        return 0
    seen = set() if seen is None else seen
    total = 0
    for name in PACKED_FIELDS + ("overlay_image",):
        a = getattr(res, name, None)
        if isinstance(a, np.ndarray) and id(a) not in seen:
            seen.add(id(a))
            total += a.nbytes
    return total + packed_bytes(res, seen)
