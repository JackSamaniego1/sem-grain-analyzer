"""
Image acquisition details on image load (UPDATE 4 item 11, UI half).

What the user sees: under the image on the Analyze page, a compact "Image
details" line -- instrument · magnification · accelerating voltage ·
working distance · detector -- filled in automatically, with the same
"Please check" badge the scale-bar label uses when a value needs a look.
The session's own acquisition fields (Projects details, report metadata)
are filled from the same reading when they are still empty; values the
operator typed are never overwritten.

How it is read (``core.image_info.read_image_info``, Qt-free):

1. **Metadata first** -- one background task per batch of images (fast
   file reads) so the line fills almost at once, even for 50 images.
2. **Data-bar reading (OCR)** -- only for images added to a lot whose file
   metadata left instrument / magnification / kV / WD empty, one image at a
   time on a single-thread pool (never 50 threads).  Opening an existing
   lot does not start OCR; "Auto-find scan area & scale bar" reads the data
   bar anyway and its one reading also fills the details (and a label the
   details reading already found is re-used by Auto-find), so no image is
   read twice.
3. **Saved with the lot** -- ``ImageInfo.to_dict()`` per image in
   ``image_info.json`` inside the lot / session folder, so reopening does
   not read again.  (The manifest has no per-image field for it yet.)

Offline: local files and the bundled OCR engine only.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, QTimer, Signal

_log = logging.getLogger(__name__)

IMAGE_INFO_FILE = "image_info.json"
#: fields shown in the "Image details" line, in order
DETAIL_FIELDS = ("instrument", "magnification", "accelerating_voltage_kv",
                 "working_distance_mm", "detector")
#: an empty one of these after the metadata read asks for the data-bar read
OCR_FIELDS = ("instrument", "magnification", "accelerating_voltage_kv", "working_distance_mm")
FIELD_LABELS = {"instrument": "Instrument", "vendor": "Manufacturer",
                "magnification": "Magnification", "accelerating_voltage_kv": "Accelerating voltage",
                "working_distance_mm": "Working distance", "detector": "Detector",
                "scale_label": "Scale-bar label"}
SAVE_DEBOUNCE_MS = 400
_UM = {"nm": 0.001, "µm": 1.0, "mm": 1000.0}


# ======================================================================
# Plain helpers (no Qt; any thread)
# ======================================================================

def info_from_dict(d) -> Any:
    from core.image_info import ImageInfo
    return ImageInfo.from_dict(d)


def needs_ocr(info) -> bool:
    """The data bar has not been read and the file metadata left one of the
    main fields empty."""
    return (info is not None and info.ocr_status == "not_run"
            and any(info.source.get(k) is None for k in OCR_FIELDS))


def fmt_value(key: str, v) -> str:
    if v is None:
        return ""
    if key == "magnification":
        v = float(v)
        return f"{v:,.0f}×" if v >= 100 else f"{v:g}×"
    if key == "accelerating_voltage_kv":
        return f"{float(v):g} kV"
    if key == "working_distance_mm":
        return f"WD {float(v):g} mm"
    return str(v)


def summary_text(info) -> str:
    """"JEOL JSM-7800F · 20,000× · 5 kV · WD 10.1 mm · SE" (known fields)."""
    if info is None:
        return ""
    return "  ·  ".join(fmt_value(k, info.value_of(k)) for k in DETAIL_FIELDS
                        if info.source.get(k) is not None and info.value_of(k) is not None)


def checked_fields(info) -> List[str]:
    """Shown fields flagged "please check"."""
    if info is None:
        return []
    return [k for k in DETAIL_FIELDS
            if info.source.get(k) is not None and info.needs_check.get(k)]


def check_text(info) -> str:
    """Plain-language reason for the "Please check" badge, or ""."""
    keys = checked_fields(info)
    if not keys:
        return ""
    parts = []
    for k in keys:
        note = (info.field_notes.get(k) or "").strip()
        name = FIELD_LABELS[k].lower()
        parts.append(f"{name} ({note})" if note else name)
    return ("Please compare with the image's data bar: " + "; ".join(parts) + ".")


def source_key(info) -> str:
    """"metadata" | "info_bar" | "both" | "" (nothing known)."""
    if info is None:
        return ""
    srcs = {info.source.get(k) for k in DETAIL_FIELDS} - {None}
    if len(srcs) > 1:
        return "both"
    return next(iter(srcs), "")


def tooltip_text(info) -> str:
    if info is None:
        return ""
    where = {"metadata": "from the image file", "info_bar": "read from the data bar"}
    lines = []
    for k in DETAIL_FIELDS:
        v = info.value_of(k)
        if info.source.get(k) is None or v is None:
            continue
        line = f"{FIELD_LABELS[k]}: {fmt_value(k, v).replace('WD ', '')} ({where[info.source[k]]})"
        if info.needs_check.get(k):
            line += " - please check"
        lines.append(line)
    lines += [n for n in info.notes if n]
    return "\n".join(lines)


def acquisition_values(info) -> dict:
    """Values for the session's acquisition fields (the keys
    ``AppState._fill_acquisition`` takes).  Values flagged "please check"
    are left out: they never reach a report unchecked."""
    out = {}
    if info is None:
        return out
    for k in ("instrument", "magnification", "accelerating_voltage_kv", "working_distance_mm"):
        if info.source.get(k) is not None and not info.needs_check.get(k) \
                and info.value_of(k) is not None:
            out[k] = info.value_of(k)
    return out


def label_reading(info) -> Optional[dict]:
    """The scale-bar label found by the details reading as the summary dict
    Auto-find uses (``app_state._scale_reading_summary``), or None."""
    if info is None or info.source.get("scale_label") != "info_bar":
        return None
    v, u = info.scale_label_value, info.scale_label_unit
    if not v or u not in _UM:
        return None
    return {"status": "ok", "available": True, "message": "", "value": float(v), "unit": u,
            "um": float(v) * _UM[u], "confirm": bool(info.needs_check.get("scale_label", True)),
            "meta_ok": None, "bar_px": float(info.scale_bar_px or 0),
            "note": str(info.field_notes.get("scale_label", "") or "")}


# ---------------------------------------------------------------- worker side
def load_saved(record_path) -> Dict[str, dict]:
    """``{filename: ImageInfo dict}`` saved in a lot / session folder."""
    p = Path(record_path) / IMAGE_INFO_FILE
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    imgs = d.get("images") if isinstance(d, dict) else None
    return {str(k): v for k, v in imgs.items() if isinstance(v, dict)} \
        if isinstance(imgs, dict) else {}


def save_infos(record_path, infos: Dict[str, dict]) -> None:
    """Merge ``infos`` into the folder's ``image_info.json`` (atomic write)."""
    folder = Path(record_path)
    if not folder.is_dir():
        return
    data = load_saved(folder)
    data.update(infos)
    fd, tmp = tempfile.mkstemp(prefix=".image_info.", suffix=".tmp", dir=str(folder))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "images": data}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, folder / IMAGE_INFO_FILE)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def read_batch(record_path, items: List[Tuple[Any, str, Optional[str]]]) -> list:
    """Worker: saved details, else the metadata-only reading, for each
    ``(uid, filename, path)``.  Returns ``[(uid, info dict, saved?)]``."""
    from core.image_info import read_image_info
    saved = load_saved(record_path) if record_path else {}
    out = []
    for uid, filename, path in items:
        if filename in saved:
            out.append((uid, saved[filename], True))
            continue
        info = read_image_info(path, None, use_ocr=False) if path else None
        if info is not None:
            out.append((uid, info.to_dict(), False))
    return out


def read_with_ocr(path, image=None) -> Optional[dict]:
    """Worker: metadata + data-bar reading of one image (None when the app
    is closing)."""
    from ui.workers import is_shutting_down
    if is_shutting_down():
        return None
    from core.image_info import read_image_info
    return read_image_info(path, image, use_ocr=True).to_dict()


def details_from_reading(path, image, reading) -> Optional[dict]:
    """Worker: the details from a data-bar reading Auto-find already made
    (one OCR per image).  None if that is not possible."""
    try:
        from core import image_info as ii
        apply = getattr(ii, "_apply_reading", None)
        if apply is None or reading is None:
            return None
        info = ii.read_image_info(path, image, use_ocr=False)
        info.ocr_status = str(getattr(reading, "status", "") or "error")
        apply(info, reading)
        if info.instrument is None and info.vendor:
            info._set("instrument", info.vendor, info.source.get("vendor"),
                      info.needs_check.get("vendor", False))
        return info.to_dict()
    except Exception:                               # never take Auto-find down
        _log.exception("image details from the Auto-find reading failed")
        return None


# ======================================================================
# GUI-thread service
# ======================================================================

class ImageDetailsService(QObject):
    """Owns the reading queue for the open lot (one per AppState)."""

    ready = Signal(object)                 # uid: ImageDoc.image_info set / changed

    def __init__(self, state) -> None:
        super().__init__(state)
        self.state = state
        self._queue: deque = deque()       # uids waiting for the data-bar read
        self._running = None               # uid being read
        self._pending: set = set()         # uids with the metadata read in flight
        self._doc = None
        # bumped by reset(): a batch still in flight for an earlier document
        # never touches the bookkeeping of the current one
        self._gen = 0
        self._to_save: Dict[Any, Dict[str, dict]] = {}   # record path -> {filename: dict}
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self.flush_saves)

    # ------------------------------------------------------------ queries
    def is_reading(self, uid) -> bool:
        return uid in self._pending or uid == self._running or uid in self._queue

    def queued(self) -> List:
        return list(self._queue)

    # ------------------------------------------------------------ requests
    def reset(self) -> None:
        """Session closed / replaced: forget the queue (late results are
        dropped because they belong to another document)."""
        self.flush_saves()
        self._queue.clear()
        self._pending.clear()
        self._running = None
        self._doc = None
        self._gen += 1

    def request(self, doc, images, ocr: bool = False) -> None:
        """Fill ``image_info`` of ``images`` (saved -> metadata; with
        ``ocr`` the data bar of images whose metadata left fields empty is
        read afterwards, one image at a time)."""
        from ui.workers import run_task
        if doc is not self._doc:
            self.reset()
            self._doc = doc
        groups: Dict[Any, list] = {}
        for im in images:
            if im is None or im.uid in self._pending or getattr(im, "loading", False):
                continue
            if im.image_info is not None:
                if ocr and needs_ocr(im.image_info):
                    self._enqueue(im.uid)
                continue
            rec = doc.record_for(im)
            groups.setdefault(rec.path if rec is not None else None, []).append(im)
        for rpath, ims in groups.items():
            items = [(im.uid, im.filename, str(im.path) if im.path else None) for im in ims]
            self._pending.update(im.uid for im in ims)
            g = self._gen
            run_task(read_batch, rpath, items,
                     on_done=lambda out, d=doc, o=ocr, u=[i[0] for i in items], g=g:
                     self._batch_done(d, out, o, u, g),
                     on_error=lambda _m, u=[i[0] for i in items], g=g:
                     self._batch_failed(u, g))
        self._pump()

    def _image(self, doc, uid):
        if doc is None or self.state.session is not doc:
            return None
        im = doc.image(uid)
        if im is None:
            im = next((x for x in doc.removed if x.uid == uid), None)
        return im

    def _batch_failed(self, uids, gen: Optional[int] = None) -> None:
        if gen is None or gen == self._gen:
            self._pending.difference_update(uids)

    def _batch_done(self, doc, out, ocr: bool, uids, gen: Optional[int] = None) -> None:
        if gen is not None and gen != self._gen:
            return                         # an earlier document's batch
        self._pending.difference_update(uids)
        if doc is not self._doc:
            return
        for uid, d, saved in out:
            im = self._image(doc, uid)
            if im is None or im.image_info is not None:
                continue
            info = info_from_dict(d)
            # details saved earlier: the lot's acquisition fields were filled
            # (or deliberately cleared) back then -- never refilled on open
            self._set(doc, im, info, save=not saved and not info.is_empty, fresh=not saved)
            if ocr and needs_ocr(info):
                self._enqueue(uid)
        self._pump()

    def _enqueue(self, uid) -> None:
        if uid != self._running and uid not in self._queue:
            self._queue.append(uid)

    def _pump(self) -> None:
        from ui.workers import is_shutting_down, ocr_pool, run_task
        doc = self._doc
        while self._running is None and self._queue and not is_shutting_down():
            uid = self._queue.popleft()
            im = self._image(doc, uid)
            if im is None or not im.path or not needs_ocr(im.image_info):
                continue
            self._running = uid
            g = self._gen
            run_task(read_with_ocr, str(im.path), im.image_bgr,
                     on_done=lambda d, u=uid, dc=doc, g=g: self._ocr_done(dc, u, d, g),
                     on_error=lambda _m, u=uid, dc=doc, g=g: self._ocr_done(dc, u, None, g),
                     pool=ocr_pool())

    def _ocr_done(self, doc, uid, d, gen: Optional[int] = None) -> None:
        if gen is not None and gen != self._gen:
            return                         # an earlier document's reading
        if self._running == uid and doc is self._doc:
            self._running = None
        im = self._image(doc, uid)
        if im is not None and d is not None and needs_ocr(im.image_info):
            self._set(doc, im, info_from_dict(d), save=True)
        self._pump()

    def adopt(self, doc, im, d: Optional[dict]) -> None:
        """Auto-find read this image's data bar: take the details from that
        same reading (no second read)."""
        if d is None or im is None or self.state.session is not doc:
            return
        if im.image_info is not None and not needs_ocr(im.image_info) \
                and im.image_info.ocr_status != "not_run":
            return
        try:
            self._queue.remove(im.uid)
        except ValueError:
            pass
        self._set(doc, im, info_from_dict(d), save=True)

    # ------------------------------------------------------------ apply / save
    def _set(self, doc, im, info, save: bool, fresh: bool = True) -> None:
        """``fresh``: the details were just read from the image (not loaded
        from the saved details file) -- only then do they fill the lot's
        empty acquisition fields."""
        im.image_info = info
        if save:
            rec = doc.record_for(im)
            if rec is not None:
                self._to_save.setdefault(rec.path, {})[im.filename] = info.to_dict()
                self._save_timer.start()
        if fresh and doc.records and doc.record_for(im) is doc.records[0]:
            vals = acquisition_values(info)
            if vals:
                self.state._fill_acquisition(doc, vals)
        self.ready.emit(im.uid)

    def flush_saves(self) -> None:
        """Write pending details now (serial pool: after / between saves)."""
        from ui.workers import run_task, serial_pool
        self._save_timer.stop()
        todo, self._to_save = self._to_save, {}
        for rpath, infos in todo.items():
            run_task(save_infos, rpath, infos, pool=serial_pool(),
                     on_error=lambda m: _log.warning("Saving image details failed: %s",
                                                     m.splitlines()[0]))


__all__ = ["ImageDetailsService", "IMAGE_INFO_FILE", "DETAIL_FIELDS", "OCR_FIELDS",
           "needs_ocr", "summary_text", "check_text", "checked_fields", "source_key",
           "tooltip_text", "acquisition_values", "label_reading", "load_saved", "save_infos",
           "read_batch", "read_with_ocr", "details_from_reading", "fmt_value"]
