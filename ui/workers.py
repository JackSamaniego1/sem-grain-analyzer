"""
Background work for the v3 shell — nothing here ever touches a widget.

* :func:`analyze_image` — the full per-image pipeline (scan-area crop →
  ``GrainDetector.analyze`` → border-grain discard → pad back to full-frame
  coordinates).  Pure function, unit-testable without Qt.
* :class:`AnalysisWorker` — QObject wrapper run on a ``QThread`` (the v2.3
  pattern users like: live progress messages, incl. the SAM monkey-patch).
* :class:`AnalysisQueue` — sequential batch runner keyed by stable image ids
  (fixes B11: closing an image mid-batch can no longer desync indices).
* :func:`run_task` — fire-and-forget ``QRunnable`` for IO (session loading,
  saving, thumbnails, catalog search).  Callbacks are delivered on the GUI
  thread through a queued signal.
"""
from __future__ import annotations

import copy
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np
from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QImage

from core.grain_detector import (
    AnalysisResult, DetectionParams, GrainDetector, discard_border_grains,
)
from core.metrics import compute_statistics
from core.overlay_compose import analysed_rect_from_mask, compose_full_overlay, rc_to_xywh

IMAGE_FILTER = "SEM images (*.tif *.tiff *.png *.jpg *.jpeg *.bmp);;All files (*)"
IMAGE_EXTS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp"}


# ======================================================================
# Image IO helpers (unicode-safe on Windows, usable from any thread)
# ======================================================================

def read_image(path) -> Optional[np.ndarray]:
    """``cv2.imread`` that also works for non-ASCII Windows paths."""
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    return img


def bgr_to_qimage(arr: Optional[np.ndarray]) -> QImage:
    """Deep-copied QImage from a BGR (or gray) uint8 array."""
    if arr is None:
        return QImage()
    a = np.ascontiguousarray(arr)
    if a.ndim == 2:
        h, w = a.shape
        return QImage(a.data, w, h, w, QImage.Format_Grayscale8).copy()
    h, w = a.shape[:2]
    rgb = np.ascontiguousarray(cv2.cvtColor(a, cv2.COLOR_BGR2RGB))
    return QImage(rgb.data, w, h, w * 3, QImage.Format_RGB888).copy()


def thumb_qimage(arr: Optional[np.ndarray], max_w: int = 320, max_h: int = 220) -> QImage:
    """Area-downsampled thumbnail as a QImage (safe off the GUI thread)."""
    if arr is None or arr.size == 0:
        return QImage()
    h, w = arr.shape[:2]
    s = min(max_w / w, max_h / h, 1.0)
    small = cv2.resize(arr, (max(1, int(w * s)), max(1, int(h * s))),
                       interpolation=cv2.INTER_AREA) if s < 1.0 else arr
    return bgr_to_qimage(small)


def load_thumb_file(path) -> QImage:
    """Read a thumbnail jpg into a QImage (off-thread safe)."""
    img = QImage()
    try:
        data = Path(path).read_bytes()
    except OSError:
        return img
    img.loadFromData(data)
    return img


# ======================================================================
# The analysis pipeline
# ======================================================================

def _clamp_rect(rect, w: int, h: int) -> Optional[Tuple[int, int, int, int]]:
    if not rect:
        return None
    x, y, rw, rh = [int(v) for v in rect]
    x, y = max(0, x), max(0, y)
    rw, rh = min(rw, w - x), min(rh, h - y)
    if rw <= 10 or rh <= 10:
        return None
    if x == 0 and y == 0 and rw >= w and rh >= h:
        return None
    return x, y, rw, rh


def _pad(arr: Optional[np.ndarray], shape, ox: int, oy: int, dtype=None, fill=0):
    if arr is None:
        return None
    out = np.full(shape, fill, dtype=dtype or arr.dtype)
    out[oy:oy + arr.shape[0], ox:ox + arr.shape[1]] = arr
    return out


def analyze_image(image_bgr: np.ndarray, px_per_um: float = 0.0,
                  params: Optional[DetectionParams] = None,
                  scan_rect=None,
                  progress: Optional[Callable[[int, str], None]] = None,
                  discard_border: bool = False,
                  draw_overlay: bool = True) -> AnalysisResult:
    """Run detection on one image and return the RAW result in FULL-frame
    coordinates.

    Steps:
      1. crop to the scan area (if any),
      2. ``GrainDetector.analyze`` (which may auto-crop white borders),
      3. optionally discard grains cut by the analysed-area border
         (``discard_border=True``; the v3 UI instead applies this as a
         non-destructive post-filter, see :mod:`ui.filtering`),
      4. pad label/binary/valid-mask back to the full frame (outside the
         analysed area counts as *not analysed*, i.e. invalid), shift grain
         centroids and bounding boxes, recompute statistics via
         :func:`core.metrics.compute_statistics` and (optionally) redraw the
         overlay.
    """
    params = params or DetectionParams()
    H, W = image_bgr.shape[:2]
    rect = _clamp_rect(scan_rect, W, H)
    img = image_bgr
    ox = oy = 0
    if rect:
        x, y, rw, rh = rect
        img = image_bgr[y:y + rh, x:x + rw]
        ox, oy = x, y

    det = GrainDetector()
    result = det.analyze(img, px_per_um=px_per_um, params=params,
                         progress_callback=progress)
    # The detector reports the crop it actually applied (white borders and/or
    # the SEM info bar), as (r0, c0, r1, c1) in scan-area coordinates.
    auto = getattr(result, "auto_crop_rect", None)

    # Info-bar rectangles are in scan-area coordinates: shift to full frame.
    def _shift(r):
        return (r[0] + ox, r[1] + oy, r[2], r[3]) if r else r
    if getattr(result, "info_bar_rect", None):
        result.info_bar_rect = _shift(tuple(result.info_bar_rect))
    ib = getattr(result, "info_bar", None)
    if isinstance(ib, dict):
        for key in ("analysis_rect", "bar_rect"):
            if ib.get(key):
                ib[key] = list(_shift(tuple(ib[key])))

    if discard_border:
        discard_border_grains(result)

    lab = result.label_image
    if lab is not None and (auto is not None or rect is not None):
        ay, ax = (auto[0], auto[1]) if auto is not None else (0, 0)
        dx, dy = ox + ax, oy + ay
        if lab.shape[:2] != (H, W):
            result.label_image = _pad(lab, (H, W), dx, dy)
            result.binary_image = _pad(result.binary_image, (H, W), dx, dy)
            if result.valid_mask is not None:
                result.valid_mask = _pad(result.valid_mask.astype(bool), (H, W),
                                         dx, dy, dtype=bool, fill=False)
            for g in result.grains:
                g.centroid_x += dx
                g.centroid_y += dy
                if g.bbox and len(g.bbox) == 4:
                    r0, c0, r1, c1 = g.bbox
                    g.bbox = (r0 + dy, c0 + dx, r1 + dy, c1 + dx)
    compute_statistics(result, (H, W))
    if not draw_overlay:
        result.overlay_image = None
    elif result.label_image is not None:
        result.overlay_image = full_frame_overlay(image_bgr, result)
    return result


def full_frame_overlay(image_bgr: np.ndarray, result: AnalysisResult) -> np.ndarray:
    """Overlay of a full-frame ``result`` on the full original image, with
    the analysed-region boundary (bounding box of ``valid_mask``) outlined.
    The unanalysed area (SEM data bar, outside the scan area) is left as in
    the original, so exports keep the instrument's annotations."""
    ov = GrainDetector()._draw_overlay(image_bgr, result.label_image, result.grains)
    rect = analysed_rect_from_mask(getattr(result, "valid_mask", None), image_bgr.shape)
    return compose_full_overlay(image_bgr, ov, rect)


def snapshot_result(result: Optional[AnalysisResult]) -> Optional[AnalysisResult]:
    """Copy of a result safe to hand to another thread while the GUI keeps
    editing the original (label image copied; grain list copied)."""
    if result is None:
        return None
    r = copy.copy(result)
    r.grains = list(result.grains)
    if result.label_image is not None:
        r.label_image = result.label_image.copy()
    if isinstance(getattr(result, "astm", None), dict):
        r.astm = dict(result.astm)
    return r


def redraw_overlay(image_bgr: Optional[np.ndarray], result: AnalysisResult) -> None:
    """Regenerate ``result.overlay_image`` after grain edits (off-thread)."""
    if image_bgr is None or result.label_image is None:
        return
    if result.label_image.shape[:2] != image_bgr.shape[:2]:
        # Labels still in analysed-crop coordinates (core result): place the
        # crop overlay back into the full frame.
        crop = rc_to_xywh(getattr(result, "auto_crop_rect", None))
        if crop is not None and (crop[3], crop[2]) == result.label_image.shape[:2]:
            x, y, w, h = crop
            ov = GrainDetector()._draw_overlay(
                image_bgr[y:y + h, x:x + w], result.label_image, result.grains)
            result.overlay_image = compose_full_overlay(image_bgr, ov, crop)
        return
    result.overlay_image = full_frame_overlay(image_bgr, result)


def mask_to_display(binary: Optional[np.ndarray]) -> Optional[np.ndarray]:
    """0/1 **or** 0/255 mask -> 0/255 uint8 (DET-03 / B2: never ``* 255`` a
    uint8 array that is already 0/255 — that overflowed to near-black)."""
    if binary is None:
        return None
    return (np.asarray(binary) > 0).astype(np.uint8) * 255


# ======================================================================
# QThread worker (v2.3 pattern)
# ======================================================================

class AnalysisWorker(QObject):
    """Runs :func:`analyze_image` on a ``QThread``; never touches widgets."""

    progress = Signal(int, str)
    finished = Signal(object)
    error = Signal(str)

    def __init__(self, image_bgr, px_per_um, params, scan_rect=None,
                 discard_border: bool = False, draw_overlay: bool = True):
        super().__init__()
        self.image_bgr = image_bgr
        self.px_per_um = px_per_um
        self.params = params
        self.scan_rect = scan_rect
        self.discard_border = discard_border
        self.draw_overlay = draw_overlay

    @Slot()
    def run(self):
        try:
            result = analyze_image(self.image_bgr, self.px_per_um, self.params,
                                   self.scan_rect, self.progress.emit,
                                   discard_border=self.discard_border,
                                   draw_overlay=self.draw_overlay)
            self.finished.emit(result)
        except Exception as e:  # pragma: no cover - surfaced to the UI
            self.error.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


@dataclass
class AnalysisJob:
    uid: Any
    image_bgr: np.ndarray
    px_per_um: float
    params: DetectionParams
    scan_rect: Optional[tuple] = None


class AnalysisQueue(QObject):
    """Sequential batch analysis keyed by image uid (no index bookkeeping)."""

    job_started = Signal(object)            # uid
    job_progress = Signal(object, int, str)  # uid, pct, message
    job_finished = Signal(object, object)   # uid, AnalysisResult
    job_failed = Signal(object, str)        # uid, message
    overall_progress = Signal(float)        # 0..100
    queue_finished = Signal(bool)           # cancelled

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._jobs: List[AnalysisJob] = []
        self._total = 0
        self._done = 0
        self._current: Optional[AnalysisJob] = None
        self._thread: Optional[QThread] = None
        self._worker: Optional[AnalysisWorker] = None
        self._cancelled = False
        self._running = False

    # -- API ---------------------------------------------------------------
    def is_running(self) -> bool:
        return self._running

    def current_uid(self):
        return self._current.uid if self._current else None

    def pending_uids(self) -> List[Any]:
        return [j.uid for j in self._jobs]

    def start(self, jobs: Sequence[AnalysisJob]) -> bool:
        if self._running or not jobs:
            return False
        self._jobs = list(jobs)
        self._total = len(self._jobs)
        self._done = 0
        self._cancelled = False
        self._running = True
        self.overall_progress.emit(0.0)
        self._next()
        return True

    def cancel(self) -> None:
        """Drop pending jobs; the running image's result is discarded."""
        if not self._running:
            return
        self._cancelled = True
        self._jobs.clear()
        if self._thread is None:
            self._finish()

    def remove(self, uid) -> None:
        """Forget a pending job (image closed mid-batch)."""
        self._jobs = [j for j in self._jobs if j.uid != uid]

    def wait(self, ms: int = 30000) -> None:
        """Block until the running thread ends (used on application close)."""
        if self._thread is not None:
            self._thread.wait(ms)

    # -- internals ---------------------------------------------------------
    def _next(self) -> None:
        if self._cancelled or not self._jobs:
            self._finish()
            return
        job = self._jobs.pop(0)
        self._current = job
        self.job_started.emit(job.uid)
        th = QThread()
        wk = AnalysisWorker(job.image_bgr, job.px_per_um, job.params, job.scan_rect,
                            draw_overlay=False)
        wk.moveToThread(th)
        th.started.connect(wk.run)
        wk.progress.connect(self._on_progress)
        wk.finished.connect(self._on_finished)
        wk.error.connect(self._on_error)
        wk.finished.connect(th.quit)
        wk.error.connect(th.quit)
        th.finished.connect(self._on_thread_done)
        self._thread, self._worker = th, wk
        th.start()

    @Slot(int, str)
    def _on_progress(self, pct: int, msg: str) -> None:
        if self._current is None or self._cancelled:
            return
        self.job_progress.emit(self._current.uid, int(pct), msg)
        if self._total:
            self.overall_progress.emit((self._done + pct / 100.0) / self._total * 100.0)

    @Slot(object)
    def _on_finished(self, result) -> None:
        job = self._current
        if job is not None and not self._cancelled:
            self._done += 1
            self.job_finished.emit(job.uid, result)
            self.overall_progress.emit(self._done / max(1, self._total) * 100.0)

    @Slot(str)
    def _on_error(self, msg: str) -> None:
        job = self._current
        if job is not None:
            self._done += 1
            self.job_failed.emit(job.uid, msg)

    @Slot()
    def _on_thread_done(self) -> None:
        th, wk = self._thread, self._worker
        self._thread = self._worker = None
        self._current = None
        if wk is not None:
            wk.deleteLater()
        if th is not None:
            th.deleteLater()
        if self._cancelled:
            self._finish()
        else:
            QTimer.singleShot(30, self._next)

    def _finish(self) -> None:
        if not self._running:
            return
        self._running = False
        self._current = None
        self.queue_finished.emit(self._cancelled)


# ======================================================================
# Generic off-thread tasks
# ======================================================================

_LIVE: set = set()
# Set once the main window is closing: finished tasks then drop their result
# instead of emitting into widgets that are being destroyed.
_SHUTTING_DOWN = False


class _TaskSignals(QObject):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, on_done, on_error) -> None:
        super().__init__()
        self._on_done = on_done
        self._on_error = on_error
        # queued: emitted from the pool thread, delivered on this (GUI) thread
        self.done.connect(self._deliver_done)
        self.failed.connect(self._deliver_failed)

    @Slot(object)
    def _deliver_done(self, value) -> None:
        _LIVE.discard(self)
        if self._on_done is not None:
            try:
                self._on_done(value)
            except RuntimeError:  # receiver widget already destroyed
                pass

    @Slot(str)
    def _deliver_failed(self, msg: str) -> None:
        _LIVE.discard(self)
        if self._on_error is not None:
            try:
                self._on_error(msg)
            except RuntimeError:
                pass


class Task(QRunnable):
    """Run ``fn(*args, **kwargs)`` on a pool thread."""

    def __init__(self, fn: Callable, *args, on_done=None, on_error=None, **kwargs) -> None:
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = _TaskSignals(on_done, on_error)
        _LIVE.add(self.signals)

    def run(self) -> None:
        try:
            value = self.fn(*self.args, **self.kwargs)
        except Exception as e:
            self.signals.failed.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
            return
        self.signals.done.emit(value)


_SERIAL_POOL: Optional[QThreadPool] = None


def serial_pool() -> QThreadPool:
    """Single-thread pool: writes to one session are never concurrent."""
    global _SERIAL_POOL
    if _SERIAL_POOL is None:
        _SERIAL_POOL = QThreadPool()
        _SERIAL_POOL.setMaxThreadCount(1)
    return _SERIAL_POOL


def run_task(fn: Callable, *args, on_done=None, on_error=None,
             pool: Optional[QThreadPool] = None, **kwargs) -> Task:
    """Start ``fn`` off the GUI thread; ``on_done(value)`` / ``on_error(msg)``
    run on the GUI thread."""
    task = Task(fn, *args, on_done=on_done, on_error=on_error, **kwargs)
    (pool or QThreadPool.globalInstance()).start(task)
    return task


def shutdown_tasks(timeout_ms: int = 10000) -> None:
    """Called when the main window closes: stop delivering results to the UI
    and wait for running background tasks (saves included) to finish."""
    global _SHUTTING_DOWN
    _SHUTTING_DOWN = True
    try:
        QThreadPool.globalInstance().waitForDone(timeout_ms)
        serial_pool().waitForDone(timeout_ms)
    finally:
        # Tasks that finished while we waited dropped their results; reset
        # so a new window in the same process (tests) works normally.
        _SHUTTING_DOWN = False


def pending_tasks() -> int:
    """Number of tasks whose callbacks have not been delivered yet (tests)."""
    return len(_LIVE)


__all__ = [
    "IMAGE_FILTER", "IMAGE_EXTS", "read_image", "bgr_to_qimage", "thumb_qimage",
    "load_thumb_file", "analyze_image", "snapshot_result", "redraw_overlay", "full_frame_overlay",
    "mask_to_display", "AnalysisWorker", "AnalysisJob", "AnalysisQueue",
    "Task", "run_task", "serial_pool", "shutdown_tasks", "pending_tasks",
]
