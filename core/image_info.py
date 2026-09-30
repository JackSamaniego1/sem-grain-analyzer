"""
Image acquisition info: one call for magnification, instrument, kV and WD
=========================================================================

UPDATE 4 item 11 (core half).  On image load the UI wants to show *how the
micrograph was taken*: instrument, magnification, accelerating voltage,
working distance, detector and the scale-bar label.  Two existing sources
are combined; this module parses nothing itself:

1. **File metadata** (:func:`core.sem_metadata.read_sem_metadata`): JEOL
   ``<stem>.txt`` sidecar, Thermo Fisher / FEI / Phenom TIFF tags 34682 /
   34680, Zeiss, TESCAN, Hitachi.  Written by the instrument, so it is
   cheap and trusted.
2. **Info-bar OCR** (:func:`core.info_bar_ocr.read_info_bar`): the data bar
   burnt into exported JPG/PNG files.  Used only for fields the metadata did
   not give; when both exist the OCR value cross-checks the metadata value.

Per field the result records ``source`` (``"metadata"`` | ``"info_bar"`` |
``None``) and ``needs_check`` ("please check" in the UI) which is True when

* the value came from OCR with low confidence (the OCR module's own
  threshold, 0.80) or the OCR module flagged it (no data bar found, scale
  not independently confirmed, ...), or
* metadata and info bar disagree beyond the tolerance below.  The metadata
  value is kept (instrument record beats a text reading) and the note says
  what the bar showed.

Agreement tolerances (printed values are rounded to the displayed digits):

* magnification: 5 % (bars print 3 significant digits; Thermo metadata mag
  is derived as DisplayWidth / HorFieldsize and rounds differently);
* accelerating voltage: 2 % or 0.05 kV, whichever is larger;
* working distance: 3 % or 0.1 mm, whichever is larger (bars print 0.1 mm).

Text fields (instrument, detector) are not cross-checked: vendors abbreviate
them differently in the bar and in the file ("TLD SE" vs "TLD").

Sanity ranges — values outside are dropped (with a note), never returned:
magnification 5-2,000,000 x; accelerating voltage 0.05-40 kV (deceleration
landing energies go below 0.1 kV); working distance 0.5-100 mm.

Offline (D-14): local file reads and the bundled OCR engine only.  A missing
OCR engine yields a "please reinstall" note, never a download.  No Qt.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = ["ImageInfo", "read_image_info", "FIELDS", "SANITY_RANGES",
           "SOURCE_METADATA", "SOURCE_INFO_BAR"]

SOURCE_METADATA = "metadata"
SOURCE_INFO_BAR = "info_bar"

# Field keys used in ImageInfo.source / .needs_check / .field_notes.
FIELDS = ("instrument", "vendor", "magnification", "accelerating_voltage_kv",
          "working_distance_mm", "detector", "scale_label")

SANITY_RANGES = {
    "magnification": (5.0, 2_000_000.0),
    "accelerating_voltage_kv": (0.05, 40.0),
    "working_distance_mm": (0.5, 100.0),
}

# (relative tolerance, absolute floor) for metadata vs info-bar agreement.
_AGREE_TOL = {
    "magnification": (0.05, 0.0),
    "accelerating_voltage_kv": (0.02, 0.05),
    "working_distance_mm": (0.03, 0.1),
}

_LABELS = {
    "instrument": "Instrument", "vendor": "Manufacturer",
    "magnification": "Magnification",
    "accelerating_voltage_kv": "Accelerating voltage",
    "working_distance_mm": "Working distance", "detector": "Detector",
    "scale_label": "Scale bar label",
}
_UNITS = {"magnification": "x", "accelerating_voltage_kv": "kV",
          "working_distance_mm": "mm"}

# Metadata "vendors" that are really file formats, not instruments.
_NOT_A_VENDOR = {"Generic TIFF", "ImageJ"}
_MAX_TEXT = 60
_SCALE_UNITS = {"nm", "µm", "mm"}


@dataclass
class ImageInfo:
    """Acquisition info for one image.  All values are optional.

    ``instrument`` is display text (manufacturer + model, e.g. "JEOL
    JSM-7800F"); ``vendor`` is the manufacturer alone.  ``scale_label_value``
    + ``scale_label_unit`` ("nm" | "µm" | "mm") is the printed scale-bar
    label, ``scale_bar_px`` its bar length in pixels (info bar only; share
    ``source["scale_label"]``).  ``notes`` holds plain-language messages for
    the user; ``ocr_status`` is ``"not_run"`` or the OCR module's status
    (ok | no_text | no_info_bar | engine_missing | error)."""
    path: str = ""
    instrument: Optional[str] = None
    vendor: Optional[str] = None
    magnification: Optional[float] = None
    accelerating_voltage_kv: Optional[float] = None
    working_distance_mm: Optional[float] = None
    detector: Optional[str] = None
    scale_label_value: Optional[float] = None
    scale_label_unit: Optional[str] = None
    scale_bar_px: Optional[int] = None
    source: Dict[str, Optional[str]] = field(
        default_factory=lambda: {k: None for k in FIELDS})
    needs_check: Dict[str, bool] = field(
        default_factory=lambda: {k: False for k in FIELDS})
    field_notes: Dict[str, str] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    metadata_source: Optional[str] = None
    ocr_status: str = "not_run"

    # ------------------------------------------------------------ helpers
    @property
    def any_needs_check(self) -> bool:
        return any(self.needs_check.get(k) for k in FIELDS
                   if self.source.get(k) is not None)

    @property
    def is_empty(self) -> bool:
        return all(self.source.get(k) is None for k in FIELDS)

    def value_of(self, name: str) -> Any:
        if name == "scale_label":
            return self.scale_label_value
        return getattr(self, name, None)

    def _set(self, name: str, value, src: str, check: bool = False,
             note: str = "") -> None:
        if name == "scale_label":
            self.scale_label_value = value
        else:
            setattr(self, name, value)
        self.source[name] = src
        self.needs_check[name] = bool(check)
        if note:
            self._note(name, note)

    def _note(self, name: str, note: str) -> None:
        cur = self.field_notes.get(name)
        self.field_notes[name] = f"{cur}; {note}" if cur else note

    def to_dict(self) -> dict:
        """JSON-safe dict for saving in a session (see :meth:`from_dict`)."""
        return {
            "version": 1, "path": self.path,
            "instrument": self.instrument, "vendor": self.vendor,
            "magnification": self.magnification,
            "accelerating_voltage_kv": self.accelerating_voltage_kv,
            "working_distance_mm": self.working_distance_mm,
            "detector": self.detector,
            "scale_label_value": self.scale_label_value,
            "scale_label_unit": self.scale_label_unit,
            "scale_bar_px": self.scale_bar_px,
            "source": {k: self.source.get(k) for k in FIELDS},
            "needs_check": {k: bool(self.needs_check.get(k)) for k in FIELDS},
            "field_notes": dict(self.field_notes),
            "notes": list(self.notes),
            "metadata_source": self.metadata_source,
            "ocr_status": self.ocr_status,
        }

    @classmethod
    def from_dict(cls, d) -> "ImageInfo":
        """Inverse of :meth:`to_dict`.  Tolerant: unknown keys are ignored,
        missing or malformed ones become ``None``; never raises."""
        info = cls()
        if not isinstance(d, dict):
            return info
        info.path = str(d.get("path") or "")
        ms = d.get("metadata_source")
        info.metadata_source = str(ms) if ms else None
        for k in ("instrument", "vendor", "detector"):
            setattr(info, k, _str(d.get(k)))
        for k in ("magnification", "accelerating_voltage_kv",
                  "working_distance_mm", "scale_label_value"):
            setattr(info, k, _float(d.get(k)))
        u = _str(d.get("scale_label_unit"))
        info.scale_label_unit = u if u in _SCALE_UNITS else None
        px = _float(d.get("scale_bar_px"))
        info.scale_bar_px = int(px) if px else None
        src = d.get("source") if isinstance(d.get("source"), dict) else {}
        chk = d.get("needs_check") if isinstance(d.get("needs_check"),
                                                 dict) else {}
        for k in FIELDS:
            s = src.get(k)
            info.source[k] = s if s in (SOURCE_METADATA,
                                        SOURCE_INFO_BAR) else None
            info.needs_check[k] = bool(chk.get(k, False))
        fn = d.get("field_notes")
        if isinstance(fn, dict):
            info.field_notes = {str(k): str(v) for k, v in fn.items()
                                if k in FIELDS and v}
        notes = d.get("notes")
        if isinstance(notes, list):
            info.notes = [str(n) for n in notes if n]
        info.ocr_status = _str(d.get("ocr_status")) or "not_run"
        return info


def _str(v) -> Optional[str]:
    if v is None:
        return None
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", str(v)).strip()
    return s[:_MAX_TEXT] if s else None


def _float(v) -> Optional[float]:
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _sane(name: str, value) -> Optional[float]:
    v = _float(value)
    if v is None:
        return None
    lo, hi = SANITY_RANGES[name]
    return v if lo <= v <= hi else None


def _fmt(name: str, v: float) -> str:
    return f"{v:g} {_UNITS.get(name, '')}".strip()


def _agree(name: str, a: float, b: float) -> bool:
    rel, floor = _AGREE_TOL[name]
    return abs(a - b) <= max(rel * abs(a), floor)


def _compose_instrument(vendor: Optional[str], model: Optional[str]
                        ) -> Optional[str]:
    if vendor and model:
        if model.lower().startswith(vendor.split("/")[0].split()[0].lower()):
            return model
        return f"{vendor} {model}"
    return model or vendor


# =============================================================================
# Public entry point
# =============================================================================

def read_image_info(path, image=None, use_ocr: bool = True) -> ImageInfo:
    """Acquisition info for the image at ``path``.  Never raises.

    Parameters
    ----------
    path : image file path (str / PathLike).  May be ``None`` when only an
        in-memory ``image`` is available (then only OCR can run).
    image : optional already-decoded image (numpy BGR / gray / BGRA, full
        frame).  Saves a second decode for OCR, and its width lets a JEOL
        calibration be corrected for resampling.
    use_ocr : ``False`` = metadata only (fast: a few small file reads, safe
        on the GUI thread).  ``True`` = also OCR the data bar to fill the
        missing fields and cross-check the metadata ones.

    Threading
    ---------
    * ``use_ocr=False``: pure local file reads, no shared state; safe from
      any thread, fast enough for the GUI thread.
    * ``use_ocr=True``: run it in a WORKER thread.  The first call loads the
      OCR models (0.5 s to several s) and each call OCRs for ~0.5 s.
      Concurrent calls are safe: the shared OCR engine is lazily created and
      serialised by a lock in :mod:`core.info_bar_ocr`.
    * The returned :class:`ImageInfo` is a plain object with no locking; do
      not mutate one instance from two threads (hand it to the GUI via a
      signal and let the GUI own it).
    """
    info = ImageInfo(path=os.fspath(path) if path is not None else "")
    try:
        _fill(info, path, image, use_ocr)
    except Exception as exc:                     # defensive: never raise
        logger.exception("read_image_info failed")
        info.notes.append(f"The image information could not be read "
                          f"({type(exc).__name__}).")
    return info


def _fill(info: ImageInfo, path, image, use_ocr: bool) -> None:
    from core.sem_metadata import read_sem_metadata

    width = None
    if image is not None and getattr(image, "ndim", 0) >= 2:
        width = int(image.shape[1])
    exists = bool(info.path) and os.path.isfile(info.path)
    if info.path and not exists:
        info.notes.append("The image file was not found.")
    elif not info.path and image is None:
        info.notes.append("No image was given.")
    md = read_sem_metadata(info.path, image_width=width) if exists else None
    if md is not None:
        _apply_metadata(info, md)
    if use_ocr and (image is not None or exists):
        from core import info_bar_ocr
        reading = info_bar_ocr.read_info_bar(
            image if image is not None else info.path, metadata=md)
        info.ocr_status = reading.status
        _apply_reading(info, reading)
    if info.instrument is None and info.vendor:     # manufacturer only
        info._set("instrument", info.vendor, info.source["vendor"],
                  info.needs_check.get("vendor", False))


def _apply_metadata(info: ImageInfo, md) -> None:
    info.metadata_source = md.source or None
    vendor = _str(md.vendor) if md.vendor not in _NOT_A_VENDOR else None
    model = _str(md.instrument)
    if vendor:
        info._set("vendor", vendor, SOURCE_METADATA)
    if model:           # vendor-only is filled in at the end (OCR may add a model)
        info._set("instrument", _compose_instrument(vendor, model),
                  SOURCE_METADATA)
    for name, raw in (("magnification", md.magnification),
                      ("accelerating_voltage_kv", md.accelerating_voltage_kv),
                      ("working_distance_mm", md.working_distance_mm)):
        if raw is None:
            continue
        v = _sane(name, raw)
        if v is None:
            info._note(name, f"file metadata value {raw!r} is outside the "
                             "plausible range and was ignored")
            continue
        info._set(name, v, SOURCE_METADATA)
    det = _str(md.detector)
    if det:
        info._set("detector", det, SOURCE_METADATA)


def _ocr_flag(fv) -> bool:
    from core.info_bar_ocr import _CONFIRM_BELOW
    return bool(fv.needs_confirmation) or float(fv.confidence) < _CONFIRM_BELOW


def _apply_reading(info: ImageInfo, r) -> None:
    from core.info_bar_ocr import REINSTALL_MESSAGE
    if r.status == "engine_missing" or not getattr(r, "available", True):
        info.notes.append(REINSTALL_MESSAGE)
        return
    if r.status != "ok":
        if not info.is_empty:
            return                      # metadata was enough; stay quiet
        info.notes.append("No text could be read from the image's data bar."
                          if r.status in ("no_text", "no_info_bar") else
                          (r.message or "The data bar could not be read."))
        return
    if r.message and "No data bar detected" in r.message:
        info.notes.append("No data bar was found; the bottom of the image was "
                          "read instead - please check the values.")

    # numeric fields: fill or cross-check
    for name, fv in (("magnification", r.magnification),
                     ("accelerating_voltage_kv", r.hv),
                     ("working_distance_mm", r.wd)):
        if fv is None:
            continue
        raw = _float(fv.value)
        if raw is None:
            continue
        if name == "working_distance_mm" and fv.unit == "µm":
            raw /= 1000.0
        v = _sane(name, raw)
        if info.source.get(name) == SOURCE_METADATA:
            mv = info.value_of(name)
            if v is not None and not _agree(name, mv, v):
                info.needs_check[name] = True
                info._note(name, f"the data bar shows {_fmt(name, v)} but the "
                                 f"file metadata says {_fmt(name, mv)}")
            continue
        if v is None:
            info._note(name, f"data bar value {_fmt(name, raw)} is outside "
                             "the plausible range and was ignored")
            continue
        info._set(name, v, SOURCE_INFO_BAR, _ocr_flag(fv),
                  "read from the data bar" + (
                      " with low confidence" if _ocr_flag(fv) else ""))

    # text fields: fill only
    vendor_fv = r.vendor
    if info.source.get("vendor") is None and vendor_fv is not None:
        vtxt = _str(vendor_fv.value)
        if vtxt:
            info._set("vendor", vtxt, SOURCE_INFO_BAR, _ocr_flag(vendor_fv),
                      vendor_fv.note or "")
    if info.source.get("instrument") is None:
        model_fv = r.instrument
        model = _str(model_fv.value) if model_fv is not None else None
        inst = _compose_instrument(info.vendor, model)
        if inst:
            flags = [_ocr_flag(f) for f in (model_fv, vendor_fv)
                     if f is not None]
            info._set("instrument", inst, SOURCE_INFO_BAR, any(flags))
    if info.source.get("detector") is None and r.detector is not None:
        det = _str(r.detector.value)
        if det:
            info._set("detector", det, SOURCE_INFO_BAR,
                      _ocr_flag(r.detector))

    # scale-bar label (info bar only)
    s = r.scale
    if s is not None and _float(s.value) and s.unit in _SCALE_UNITS:
        info._set("scale_label", float(s.value), SOURCE_INFO_BAR,
                  _ocr_flag(s), s.note or "")
        info.scale_label_unit = s.unit
        info.scale_bar_px = int(r.scale_bar_px) if r.scale_bar_px else None
