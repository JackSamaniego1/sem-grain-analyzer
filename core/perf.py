"""
Thread caps for the numeric libraries (UPDATE 4 item 7: stability on weak PCs).

OpenCV, torch and onnxruntime (the info-bar OCR) each default to one worker
thread per logical core.  Run side by side with the app's own worker pool
and the UI thread they oversubscribe the CPU; on a 2-4 core laptop the UI
then stalls for seconds and Windows may report the app as "not responding".

:func:`configure_threads` caps them:

* OpenCV: ``logical cores - 1`` (min 1) - leaves one core for the UI.
* torch (SAM on CPU): intra-op ``max(1, physical cores - 1)``; inter-op 1.
  torch's compute threads gain nothing from hyper-threading siblings.
* OCR (onnxruntime session inside RapidOCR): 1-2 intra-op threads, 1
  inter-op.  The info bar is a thin strip; more threads only add overhead.

Thread counts change only how work is scheduled, never which pixels are
labelled: the boundary/threshold pipelines use integer / deterministic
OpenCV + scikit-image operations (checked by ``tests/test_perf.py``).

torch is NOT imported here (it takes seconds).  Its caps are stored and
applied by :func:`apply_torch_threads`, which ``core.ai_device._import_torch``
and ``core.grain_detector.get_sam_model`` call the moment torch is loaded.
The OCR cap is read by ``core.info_bar_ocr`` when it builds its engine.

Every function here is safe to call repeatedly and never raises.  No Qt,
no psutil (not bundled): physical cores are estimated from
``os.cpu_count()``.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

__all__ = ["configure_threads", "apply_torch_threads", "ocr_threads",
           "current_caps", "estimate_physical_cores"]

_LOCK = threading.Lock()
_CAPS: Optional[Dict[str, Any]] = None       # set by configure_threads
_TORCH_APPLIED_ID: Optional[int] = None       # id(torch module) already capped
_TORCH_INTEROP_DONE = False                   # set_num_interop_threads is one-shot


def estimate_physical_cores(logical: Optional[int] = None) -> int:
    """Physical cores without psutil: desktop / laptop x86 CPUs with 4+
    logical cores almost always run 2 threads per core (SMT), so half the
    logical count; below that assume no SMT.  Never below 1."""
    n = logical if logical is not None else (os.cpu_count() or 1)
    try:
        n = int(n)
    except Exception:
        n = 1
    n = max(1, n)
    return max(1, n // 2) if n >= 4 else n


def _compute_caps(logical_cores: Optional[int],
                  physical_cores: Optional[int]) -> Dict[str, Any]:
    try:
        logical = int(logical_cores) if logical_cores else int(os.cpu_count() or 1)
    except Exception:
        logical = 1
    logical = max(1, logical)
    try:
        physical = (int(physical_cores) if physical_cores
                    else estimate_physical_cores(logical))
    except Exception:
        physical = estimate_physical_cores(logical)
    physical = max(1, min(physical, logical))
    return {
        "logical_cores": logical,
        "physical_cores": physical,
        "opencv_threads": max(1, logical - 1),
        "torch_threads": max(1, physical - 1),
        "torch_interop_threads": 1,
        "ocr_threads": 2 if logical >= 4 else 1,
    }


def _apply_opencv(n: int) -> bool:
    try:
        import cv2
        cv2.setNumThreads(int(n))
        return True
    except Exception as exc:                  # pragma: no cover - defensive
        logger.debug("cv2.setNumThreads failed: %s", exc)
        return False


def _apply_torch(torch: Any, caps: Dict[str, Any]) -> bool:
    """Cap torch's thread pools.  ``set_num_interop_threads`` may only be
    called once and only before any parallel work; later calls raise
    RuntimeError, which is swallowed (the intra-op cap still applies)."""
    global _TORCH_APPLIED_ID, _TORCH_INTEROP_DONE
    ok = False
    try:
        torch.set_num_threads(int(caps["torch_threads"]))
        ok = True
    except Exception as exc:
        logger.debug("torch.set_num_threads failed: %s", exc)
    if not _TORCH_INTEROP_DONE:
        _TORCH_INTEROP_DONE = True
        try:
            torch.set_num_interop_threads(int(caps["torch_interop_threads"]))
        except Exception as exc:              # already started / already set
            logger.debug("torch.set_num_interop_threads skipped: %s", exc)
    if ok:
        _TORCH_APPLIED_ID = id(torch)
    return ok


def configure_threads(logical_cores: Optional[int] = None,
                      physical_cores: Optional[int] = None) -> dict:
    """Cap OpenCV / torch / OCR worker threads.  Call once at app start,
    before any analysis or OCR runs (calling again is harmless and
    re-applies).  ``logical_cores`` / ``physical_cores`` override the
    detected counts (tests, or a future Settings option).

    Returns what was set, e.g.::

        {"logical_cores": 8, "physical_cores": 4, "opencv_threads": 7,
         "torch_threads": 3, "torch_interop_threads": 1, "ocr_threads": 2,
         "opencv_applied": True, "torch_applied": False}

    ``torch_applied`` is False while torch is not loaded yet; the cap is
    then applied automatically when the detector first imports torch.
    Never raises (returns ``{"error": ...}`` in the impossible case)."""
    global _CAPS, _TORCH_APPLIED_ID
    try:
        caps = _compute_caps(logical_cores, physical_cores)
        with _LOCK:
            _CAPS = dict(caps)
            _TORCH_APPLIED_ID = None          # re-apply new caps to torch
        out = dict(caps)
        out["opencv_applied"] = _apply_opencv(caps["opencv_threads"])
        torch = sys.modules.get("torch")
        out["torch_applied"] = (apply_torch_threads(torch)
                                if torch is not None else False)
        logger.info("Thread caps: %s", out)
        return out
    except Exception as exc:                  # pragma: no cover - defensive
        return {"error": f"{type(exc).__name__}: {exc}"}


def current_caps() -> Optional[Dict[str, Any]]:
    """The caps set by the last :func:`configure_threads` (None if never)."""
    with _LOCK:
        return dict(_CAPS) if _CAPS is not None else None


def apply_torch_threads(torch: Any = None) -> bool:
    """Apply the torch caps to ``torch`` (or the already-imported torch
    module; never imports it).  Uses the caps from
    :func:`configure_threads`, or the defaults when it was never called.
    Cheap after the first call for the same module.  Never raises."""
    try:
        if torch is None:
            torch = sys.modules.get("torch")
        if torch is None:
            return False
        with _LOCK:
            if _TORCH_APPLIED_ID == id(torch):
                return True
            caps = dict(_CAPS) if _CAPS is not None else _compute_caps(None, None)
            return _apply_torch(torch, caps)
    except Exception as exc:                  # pragma: no cover - defensive
        logger.debug("apply_torch_threads failed: %s", exc)
        return False


def ocr_threads() -> int:
    """Intra-op threads for the OCR onnxruntime sessions (1 or 2)."""
    try:
        with _LOCK:
            caps = _CAPS
        if caps is None:
            caps = _compute_caps(None, None)
        return max(1, min(2, int(caps["ocr_threads"])))
    except Exception:                         # pragma: no cover - defensive
        return 1


def _reset_for_tests() -> None:
    global _CAPS, _TORCH_APPLIED_ID, _TORCH_INTEROP_DONE
    with _LOCK:
        _CAPS = None
        _TORCH_APPLIED_ID = None
        _TORCH_INTEROP_DONE = False
