"""
Info-bar OCR: read the scale label and instrument settings off an SEM data bar
=============================================================================

Exported JPG/PNG micrographs carry no metadata, but the instrument burns a
*data bar* into the image ("100nm JEOL / X 30,000 7.0kV LEI SEM WD 9.7mm").
This module OCRs **only that strip** and turns the text into structured,
per-field values so Automatic mode can pre-fill the calibration.

Single entry point for the UI::

    from core.info_bar_ocr import read_info_bar, is_ocr_available
    reading = read_info_bar(image_bgr, scale_bar_bbox=None)   # never raises
    reading.scale        # ReadingField(value=100.0, unit="nm", ...) or None
    reading.px_per_um    # scale-bar px / label length, or None

Engine
------
RapidOCR (``rapidocr-onnxruntime``, Apache-2.0; PP-OCRv4 ONNX models shipped
*inside the wheel*; onnxruntime, MIT).  CPU only, no network code, no model
download on first use.  Loaded lazily, cached process-wide and serialised by a
lock, so :func:`read_info_bar` is safe to call from a worker thread.  When the
package is absent (broken install) the result has ``status="engine_missing"``;
the UI tells the user to reinstall — there is never a download.

Why a label-aware parser (the physics of the data bar)
------------------------------------------------------
A scale bar's label is a *length*; so are the working distance (WD, the
lens-to-specimen distance) and the horizontal field width (FW/HFW, the width
of the whole frame).  Taking "the first length in the bar" would calibrate a
1280 px image from WD 9.7 mm — off by 10^5.  Therefore:

1. Tokens (OCR boxes) are classified by their **labels**: ``WD``, ``Mag``/
   ``X``/``×``, ``HV``/``kV``, ``FW``/``HFW``, ``Det``, ``Vac``, dates and
   times.  A label written inline ("WD 9.7mm") claims its value in the same
   token; a label-only token (Thermo column layout, label above value) claims
   the token directly below it (or directly right of it).
2. The scale value is the **unclaimed** length token (nm/µm/mm, 1 nm .. 5 mm)
   spatially **nearest the detected scale bar**.
3. Cross-checks (never silently replace the reading; see
   :func:`_cross_check` for the exact policy — the scale is auto-accepted
   only when FW (with an unambiguous pick) or the file metadata agrees;
   otherwise it is returned as a best guess with ``needs_confirmation``):

   * **FW**: the field width spans the full image width, so
     ``scale ≈ FW · bar_px / image_width_px`` (within 8 %).
   * **Magnification**: nominal SEM magnification is defined against a
     reference display width *D*: ``M = D / FW``.  Vendors use D ≈ 127 mm
     (Polaroid 4x5 / JEOL) up to ≈ 0.5 m (Phenom/Thermo monitor mag), so
     ``D = FW_implied · M`` must lie in 60–800 mm; a decade misread of
     the label (100 nm vs 1 µm) lands outside that window.
   * **Magnification, vendor reference** (JEOL only): see
     :data:`_VENDOR_DISPLAY_MM`.  With the vendor read from the bar's text,
     ``FW_implied * M`` must match that vendor's fixed reference width
     within ~10 %; this is an independent length check (bar px, label and
     magnification all have to agree) and may clear the flag.
   * **Metadata** (``core.sem_metadata``), when supplied: bar_px / scale must
     match the stored pixel size within 5 %.

4. Scale bar: :func:`core.scale_bar.find_scale_bar_candidates`; after OCR,
   a candidate lying inside a word box is skipped (a bold glyph stroke is
   not a bar).  A label on the SAME text line as a solid bar, with nothing
   between them (JEOL: "[bar]   100nm"), is paired with it even across a
   wide gap.
5. Vendor: from the text ("JEOL", "Thermo", ...), else from the Thermo
   Fisher atom mark at the left end of the bar (:mod:`core.vendor_logo`,
   shape analysis, no stored image), else guessed from the column layout
   (flagged).

No Qt, no I/O except optional image loading from a path.
"""
from __future__ import annotations

import logging
import math
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, List, Optional, Sequence, Tuple, Union

import numpy as np

from core.infobar import InfoBarResult, detect_info_bar

logger = logging.getLogger(__name__)

Rect = Tuple[int, int, int, int]          # (x, y, w, h), full-frame pixels

ENGINE_NAME = "RapidOCR (PP-OCRv4, onnxruntime)"
REINSTALL_MESSAGE = ("The text-reading component (OCR) is missing from this "
                     "installation. Please reinstall SEM Grain Analyzer.")

# --- tunables ----------------------------------------------------------------
_CONFIRM_BELOW = 0.80        # confidence below which a field needs confirmation
# Public alias for callers (core.image_info) - do not import the private name.
CONFIRM_BELOW = _CONFIRM_BELOW
_FW_TOL = 0.08               # FW cross-check relative tolerance
_META_TOL = 0.05             # metadata pixel-size cross-check tolerance
_DISPLAY_MM = (60.0, 800.0)  # plausible reference display width for mag check
_SCALE_UM = (0.001, 5000.0)  # sane scale-bar label range (1 nm .. 5 mm)
_CURR_NA = (1e-4, 1e4)       # sane beam current range (0.1 pA .. 10 uA)
_PAIR_MAX_LINES = 10.0       # max bar-label gap (text-line heights) on one line
_IN_WORD = 0.5               # bar-candidate area inside one OCR box -> glyph

# Vendor-specific reference display width for the magnification check.
# SEM magnification is M = D / FW, with D a fixed reference image width
# (FW = the horizontal field width, i.e. the specimen length across the
# whole frame; it does not depend on how many pixels the file has).
# JEOL defines M against its standard photo width: 120 mm (120 x 90 mm
# photo format) or 128 mm (128 x 96 mm) depending on the model.  The user's
# real JEOL export gives D = 4.0 um (32 px of 100 nm on 1280 px) x 30,000 =
# 120 mm.  The window 110-135 mm covers both references plus bar
# quantisation (+-0.5 px on a 30 px bar = +-1.7 %) and the rounding of the
# printed magnification.  Scale labels follow the 1-2-5 series, so a
# misread label (or a wrong bar) is off by >= 2x and falls outside it.
# Thermo/Zeiss/Hitachi use display- or monitor-dependent references, so no
# window is claimed for them (only the loose _DISPLAY_MM check applies).
_VENDOR_DISPLAY_MM = {"JEOL": (110.0, 135.0)}

_LEN_TO_UM = {"nm": 1e-3, "\u00b5m": 1.0, "mm": 1e3}
_CURR_TO_NA = {"pA": 1e-3, "nA": 1.0, "\u00b5A": 1e3}


# =============================================================================
# Result types
# =============================================================================

@dataclass
class OcrToken:
    text: str
    bbox: Rect                  # full-frame
    score: float


@dataclass
class ReadingField:
    """One value read from the bar.  ``value`` is a float for numeric fields
    (in ``unit`` as printed, normalised: "nm"/"µm"/"mm", "x", "kV", "mm",
    "Pa") or a string for detector/vendor/instrument/date/time."""
    value: Any
    unit: str = ""
    confidence: float = 0.0
    needs_confirmation: bool = False
    text: str = ""              # raw OCR text of the matched span
    bbox: Optional[Rect] = None
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class InfoBarReading:
    status: str = "no_text"     # ok | no_text | no_info_bar | engine_missing | error
    available: bool = True      # False when the OCR engine is missing
    message: str = ""
    scale: Optional[ReadingField] = None
    magnification: Optional[ReadingField] = None
    hv: Optional[ReadingField] = None               # accelerating voltage
    wd: Optional[ReadingField] = None               # working distance
    field_width: Optional[ReadingField] = None      # FW / HFW
    vacuum: Optional[ReadingField] = None
    beam_current: Optional[ReadingField] = None     # "curr": value in pA/nA/µA
    detector: Optional[ReadingField] = None
    vendor: Optional[ReadingField] = None
    instrument: Optional[ReadingField] = None
    date: Optional[ReadingField] = None
    time: Optional[ReadingField] = None
    scale_bar_rect: Optional[Rect] = None
    scale_bar_px: Optional[int] = None
    info_bar_rect: Optional[Rect] = None
    image_width_px: Optional[int] = None
    # True when no data bar was found and the bottom strip of the image was
    # read instead (every field is then flagged needs_confirmation).
    bar_fallback: bool = False
    checks: dict = field(default_factory=dict)      # name -> True/False
    tokens: List[OcrToken] = field(default_factory=list)
    engine: str = ENGINE_NAME
    elapsed_s: float = 0.0

    @property
    def scale_um(self) -> Optional[float]:
        if self.scale is None:
            return None
        return float(self.scale.value) * _LEN_TO_UM.get(self.scale.unit, 1.0)

    @property
    def px_per_um(self) -> Optional[float]:
        s = self.scale_um
        if not s or not self.scale_bar_px:
            return None
        return self.scale_bar_px / s

    @property
    def beam_current_na(self) -> Optional[float]:
        if self.beam_current is None:
            return None
        return float(self.beam_current.value) * _CURR_TO_NA.get(
            self.beam_current.unit, 1.0)

    @property
    def needs_confirmation(self) -> bool:
        return self.scale is None or bool(self.scale.needs_confirmation)

    FIELDS = ("scale", "magnification", "hv", "wd", "field_width", "vacuum",
              "beam_current", "detector", "vendor", "instrument", "date",
              "time")

    def to_dict(self) -> dict:
        d = {k: (getattr(self, k).to_dict() if getattr(self, k) else None)
             for k in self.FIELDS}
        d.update(status=self.status, available=self.available,
                 message=self.message, scale_bar_rect=self.scale_bar_rect,
                 scale_bar_px=self.scale_bar_px,
                 info_bar_rect=self.info_bar_rect,
                 image_width_px=self.image_width_px,
                 bar_fallback=self.bar_fallback, checks=dict(self.checks),
                 scale_um=self.scale_um, px_per_um=self.px_per_um,
                 beam_current_na=self.beam_current_na,
                 needs_confirmation=self.needs_confirmation,
                 engine=self.engine, elapsed_s=self.elapsed_s,
                 tokens=[asdict(t) for t in self.tokens])
        return d


# =============================================================================
# Engine (lazy, cached, thread-safe)
# =============================================================================

_ENGINE_LOCK = threading.Lock()
_ENGINE = None
_ENGINE_ERROR: Optional[str] = None


def _load_engine():
    """Build the OCR callable: ``engine(bgr) -> [(4x2 points, text, score)]``.

    RapidOCR resolves its ONNX models relative to its own package directory
    (``rapidocr_onnxruntime/models``); nothing is fetched.  The angle
    classifier is skipped (data bars are never rotated)."""
    import onnxruntime as ort                     # noqa: F401
    try:                                          # Windows ETW telemetry off
        ort.disable_telemetry_events()
    except Exception:
        pass
    from rapidocr_onnxruntime import RapidOCR
    ocr = RapidOCR()

    def run(img):
        res, _ = ocr(img, use_cls=False)
        return [(np.asarray(b, dtype=float), str(t), float(s))
                for b, t, s in (res or [])]
    return run


def _get_engine():
    global _ENGINE, _ENGINE_ERROR
    with _ENGINE_LOCK:
        if _ENGINE is None and _ENGINE_ERROR is None:
            try:
                _ENGINE = _load_engine()
            except Exception as exc:              # ImportError, bad models, ...
                _ENGINE_ERROR = f"{type(exc).__name__}: {exc}"
                logger.warning("OCR engine unavailable: %s", _ENGINE_ERROR)
        return _ENGINE


def is_ocr_available() -> bool:
    """True when the bundled OCR engine loads (loads it on first call)."""
    return _get_engine() is not None


def _reset_engine_cache() -> None:
    """Testing hook: forget the cached engine / load error."""
    global _ENGINE, _ENGINE_ERROR
    with _ENGINE_LOCK:
        _ENGINE, _ENGINE_ERROR = None, None


def _run_engine(engine, img) -> list:
    with _ENGINE_LOCK:                            # serialise onnx sessions
        return engine(img)


# =============================================================================
# Text normalisation and patterns
# =============================================================================

_NUM = r"\d+(?:[.,]\d+)?"
# magnification numbers: thousands separated by comma / (thin) space / apostrophe
_MAGNUM = r"\d{1,3}(?:[,\u2009\u202f\u00a0' ]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_LEN_UNIT = r"nm|nrn|[u\u00b5p]m|urn|mm"

_RE_DATE = re.compile(r"(?<!\d)(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|"
                      r"\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})(?!\d)")
_RE_TIME = re.compile(r"(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)")
_RE_WD = re.compile(r"\bWD\s*[:=]?\s*(" + _NUM + r")\s*(mm|[u\u00b5]m)?", re.I)
_RE_FW = re.compile(r"\b(?:H?FW|FOV|Width)\s*[:=]?\s*(" + _NUM + r")\s*("
                    + _LEN_UNIT + r")?", re.I)
_RE_HV = re.compile(r"(?:\b(?:HV|EHT|Acc\.?\s*V(?:olt(?:age)?)?)\s*[:=]?\s*)?"
                    r"(" + _NUM + r")\s*(kV)(?![A-Za-z])", re.I)
_RE_VAC = re.compile(r"(?:\bVac\.?\s*[:=]?\s*)?(" + _NUM
                     + r"(?:[eE][-+]?\d+)?)\s*((?-i:Pa|mbar|Torr))\b", re.I)
# (unit case-sensitive: "pA" is picoamps of beam current, not pascals)
_RE_MAG_PRE = re.compile(r"(?:\bMag\.?\s*[:=]?\s*|(?<![A-Za-z0-9.,])[xX\u00d7]\s*)("
                         + _MAGNUM + r")\s*([kK](?![A-Za-z]))?\s*[xX\u00d7]?"
                         r"(?![A-Za-z0-9])")
_RE_MAG_SUF = re.compile(r"(?<![A-Za-z0-9.,])(" + _MAGNUM + r")\s*([kK])?\s*"
                         r"[xX\u00d7](?![A-Za-z0-9])")
_RE_CURR = re.compile(r"(?:\b(?:Curr(?:ent)?|(?:Beam|Probe)\s*current|"
                      r"I\s*beam)\.?\s*[:=]?\s*)?(" + _NUM
                      + r")\s*(pA|nA|[uµ]A)(?![A-Za-z])", re.I)
_RE_DET_INLINE = re.compile(r"\b(?:Det(?:ector)?|Signal\s*A?)\.?\s*[:=]\s*"
                            r"([A-Za-z0-9+\-]+(?:\s[A-Za-z][A-Za-z0-9+\-]*)?)",
                            re.I)
_RE_LEN = re.compile(r"(?<![A-Za-z0-9.,])(" + _NUM + r")\s*(" + _LEN_UNIT
                     + r"|m)(?![A-Za-z])")

_VENDORS = {"jeol": "JEOL", "thermo": "Thermo Fisher", "thermofisher":
            "Thermo Fisher", "fei": "FEI/Thermo Fisher", "phenom":
            "Thermo Fisher (Phenom)", "zeiss": "Zeiss", "hitachi": "Hitachi",
            "tescan": "TESCAN", "coxem": "COXEM", "hirox": "Hirox"}
_RE_VENDOR = re.compile(r"\b(JEOL|Thermo\s*Fisher|Thermo|FEI|Phenom|ZEISS|"
                        r"Hitachi|TESCAN|COXEM|Hirox)\b", re.I)
_RE_INSTRUMENT = re.compile(
    r"\b(JSM-?\s?\w+|JCM-?\s?\w+|IT\d{3}\w*|Phenom\s+(?:XL|Pro\w*|Pharos|"
    r"ParticleX|Desktop)\w*|Quanta\s*\w+|Apreo\s*\w*|Helios\s*\w+|Prisma\s*E?|"
    r"Axia|Scios\s*\w*|Sigma\s*\d*|GeminiSEM\s*\d*|EVO\s*\w*|SU\d{3,4}\w*|"
    r"TM\d{4}\w*|FlexSEM\s*\w*|MIRA\d?|VEGA\d?|CLARA|AMBER|MAIA\d?)\b", re.I)

# canonical detector names; lookup key = upper-case alnum with I->L, 0->O
_DETECTORS = ["LEI", "SEI", "LED", "UED", "UHD", "BED-C", "BED-S", "BEI",
              "BEC", "COMPO", "TOPO", "SED", "BSD", "BSD Full", "BSE", "BSED",
              "SE", "SE2", "ETD", "TLD", "CBS", "ICD", "DBS", "LVD", "GSED",
              "InLens", "InBeam", "UVD", "CL", "HyperFine", "Mix", "SE+BSE"]


def _det_key(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9+]", "", s).upper()
    return s.replace("I", "L").replace("0", "O").replace("1", "L")


_DET_BY_KEY = {_det_key(d): d for d in _DETECTORS}

# label-only tokens (column layout).  kind "other" just claims its value.
_LABELS = {"wd": "wd", "fw": "fw", "hfw": "fw", "fov": "fw", "mag": "mag",
           "magnification": "mag", "hv": "hv", "eht": "hv", "det": "det",
           "detector": "det", "signal": "det", "vac": "vac", "vacuum": "vac",
           "pressure": "vac", "date": "date", "time": "time", "int": "other",
           "spot": "other", "dwell": "other", "tilt": "other", "mode": "other",
           "pixel": "other", "scan": "other",
           "curr": "curr", "current": "curr", "beamcurrent": "curr",
           "probecurrent": "curr", "hfov": "fw", "vfw": "other", "pv": "other"}


# OCR confuses O/o with 0 and I/l/| with 1 inside numbers ("1O0nm",
# "l00 nm", "X3O,000").  Repair only a run that (a) is not glued to a word
# on its left (an X magnification prefix is allowed), (b) contains at least
# one real digit and (c) is followed by a unit, a non-letter or the end —
# so words like "LEI", "Image", "Vol2" are never touched.
_RE_CONFUSED_NUM = re.compile(
    r"(?<![A-WYZa-wyz])[0-9OoIl|][0-9OoIl|.,]*"
    r"(?=\s*(?:nm|nrn|[u\u00b5p]m|urn|mm|kV|Pa|[pnu\u00b5]A|[xX\u00d7](?![A-Za-z])|"
    r"[^A-Za-z]|$))")
_CONFUSION = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1"})


def _norm_text(t: str) -> Tuple[str, bool]:
    """Normalise OCR text -> (text, corrected?).  Greek small mu (U+03BC)
    becomes the micro sign (U+00B5), which is the only "µ" used for unit
    comparison in this module; thin / no-break spaces become spaces."""
    t = t.replace("\u03bc", "\u00b5")
    t = t.replace("\u2009", " ").replace("\u202f", " ").replace("\u00a0", " ")
    t = t.strip()
    corrected = False

    def fix(m):
        nonlocal corrected
        s = m.group(0)
        if not re.search(r"\d", s):
            return s
        out = s.translate(_CONFUSION)
        corrected = corrected or out != s
        return out
    t = _RE_CONFUSED_NUM.sub(fix, t)
    return t, corrected


def _to_float(s: str) -> Optional[float]:
    try:
        return float(s.replace(",", "."))
    except (TypeError, ValueError):
        return None


def _mag_float(num: str, k: Optional[str]) -> Optional[float]:
    s = re.sub(r"[,\u2009\u202f\u00a0' ]", "", num)
    try:
        v = float(s)
    except ValueError:
        return None
    return v * (1000.0 if k else 1.0)


def _len_unit(u: str) -> Tuple[str, bool]:
    """Normalise an OCR'd length unit -> (unit, fuzzy?).  'pm' and a bare 'm'
    are the usual misreads of 'µm' (SEM scale bars are never picometres)."""
    u = (u or "").strip().replace("\u03bc", "\u00b5")
    lu = u.lower()
    if lu in ("nm",):
        return "nm", False
    if lu in ("nrn",):
        return "nm", True
    if lu in ("mm",):
        return "mm", False
    if u in ("\u00b5m",) or lu == "um":
        return "\u00b5m", False
    if lu in ("urn", "pm", "m"):
        return "\u00b5m", True
    return "\u00b5m", True


# =============================================================================
# Token model used by the parser
# =============================================================================

class _Tok:
    def __init__(self, text: str, bbox: Rect, score: float):
        self.text, self.corrected = _norm_text(text)
        self.bbox = bbox
        self.score = float(score)
        self.claimed = [False] * len(self.text)
        self.label = self._label_kind()

    def _label_kind(self) -> Optional[str]:
        if re.search(r"\d", self.text):
            return None
        key = re.sub(r"[^a-z]", "", self.text.lower())
        return _LABELS.get(key)

    @property
    def cx(self):
        return self.bbox[0] + self.bbox[2] / 2.0

    @property
    def cy(self):
        return self.bbox[1] + self.bbox[3] / 2.0

    def free(self, a: int, b: int) -> bool:
        return not any(self.claimed[a:b])

    def claim(self, a: int, b: int) -> None:
        for i in range(a, min(b, len(self.claimed))):
            self.claimed[i] = True

    def sub_bbox(self, a: int, b: int) -> Rect:
        n = max(1, len(self.text))
        x, y, w, h = self.bbox
        x0 = x + int(round(w * a / n))
        x1 = x + int(round(w * b / n))
        return (x0, y, max(1, x1 - x0), h)

    def finditer(self, rx):
        for m in rx.finditer(self.text):
            if self.free(m.start(), m.end()):
                yield m


def _gap(a: Rect, b: Rect) -> float:
    ax0, ay0, aw, ah = a
    bx0, by0, bw, bh = b
    dx = max(0.0, bx0 - (ax0 + aw), ax0 - (bx0 + bw))
    dy = max(0.0, by0 - (ay0 + ah), ay0 - (by0 + bh))
    return math.hypot(dx, dy)


# =============================================================================
# Field parsers
# =============================================================================

def _field(value, unit, tok: _Tok, m=None, conf=None, note="") -> ReadingField:
    a, b = (m.start(), m.end()) if m is not None else (0, len(tok.text))
    c = tok.score if conf is None else conf
    return ReadingField(value=value, unit=unit, confidence=round(float(c), 3),
                        needs_confirmation=c < _CONFIRM_BELOW,
                        text=tok.text[a:b], bbox=tok.sub_bbox(a, b), note=note)


def _parse_kind(kind: str, tok: _Tok, labelled: bool):
    """Parse a value of ``kind`` from ``tok``.  With ``labelled`` (the token is
    the value under/next to a label) the label word itself is optional.
    Returns (ReadingField | None, match | None)."""
    t = tok
    if kind == "wd":
        rx = _RE_WD if not labelled else re.compile(
            r"(?:WD\s*[:=]?\s*)?(" + _NUM + r")\s*(mm|[u\u00b5]m)?", re.I)
        for m in t.finditer(rx):
            v = _to_float(m.group(1))
            if v is None:
                continue
            unit = "\u00b5m" if (m.group(2) or "").lower() in ("um", "\u00b5m") else "mm"
            f = _field(v, unit, t, m)
            wd_mm = v / 1000.0 if unit == "\u00b5m" else v
            if not 0.1 <= wd_mm <= 100:
                f.needs_confirmation, f.note = True, "implausible WD"
            return f, m
    elif kind == "fw":
        rx = _RE_FW if not labelled else re.compile(
            r"(" + _NUM + r")\s*(" + _LEN_UNIT + r"|m)?(?![A-Za-z])", re.I)
        for m in t.finditer(rx):
            v = _to_float(m.group(1))
            if v is None:
                continue
            unit, fuzzy = _len_unit(m.group(2) or "\u00b5m")
            f = _field(v, unit, t, m, conf=t.score * (0.8 if fuzzy else 1.0))
            return f, m
    elif kind == "hv":
        rx = _RE_HV if not labelled else re.compile(
            r"(" + _NUM + r")\s*(kV|V)?(?![A-Za-z])", re.I)
        for m in t.finditer(rx):
            v = _to_float(m.group(1))
            if v is None:
                continue
            if (m.group(2) or "").lower() == "v" and v > 100:
                v = v / 1000.0
            f = _field(v, "kV", t, m)
            if not 0.01 <= v <= 40:
                f.needs_confirmation, f.note = True, "implausible HV"
            return f, m
    elif kind == "mag":
        rxs = (_RE_MAG_PRE, _RE_MAG_SUF) if not labelled else (re.compile(
            r"(" + _MAGNUM + r")\s*([kK](?![A-Za-z]))?\s*[xX\u00d7]?"),)
        for rx in rxs:
            for m in t.finditer(rx):
                v = _mag_float(m.group(1), m.group(2))
                if v is None or v <= 0:
                    continue
                f = _field(v, "x", t, m)
                if not 1 <= v <= 2e6:
                    f.needs_confirmation, f.note = True, "implausible magnification"
                return f, m
    elif kind == "vac":
        rx = _RE_VAC if not labelled else re.compile(
            r"(" + _NUM + r"(?:[eE][-+]?\d+)?)\s*((?-i:Pa|mbar|Torr))?", re.I)
        for m in t.finditer(rx):
            v = _to_float(m.group(1))
            if v is None:
                continue
            return _field(v, m.group(2) or "Pa", t, m), m
    elif kind == "curr":
        rx = _RE_CURR if not labelled else re.compile(
            r"(" + _NUM + r")\s*(pA|nA|[uµ]A)?(?![A-Za-z])", re.I)
        for m in t.finditer(rx):
            v = _to_float(m.group(1))
            if v is None:
                continue
            raw = (m.group(2) or "").lower()
            unit = {"pa": "pA", "na": "nA", "ua": "µA",
                    "µa": "µA"}.get(raw, "nA")
            f = _field(v, unit, t, m)
            if not raw:
                f.needs_confirmation, f.note = True, "unit missing (nA assumed)"
            elif not _CURR_NA[0] <= v * _CURR_TO_NA[unit] <= _CURR_NA[1]:
                f.needs_confirmation, f.note = True, "implausible beam current"
            return f, m
    elif kind == "det":
        if labelled:
            name = _DET_BY_KEY.get(_det_key(t.text), t.text)
            m = re.match(r".*", t.text)
            return _field(name, "", t, m), m
        for m in t.finditer(_RE_DET_INLINE):
            raw = m.group(1)
            return _field(_DET_BY_KEY.get(_det_key(raw), raw), "", t, m), m
    elif kind in ("date", "time"):
        rx = _RE_DATE if kind == "date" else _RE_TIME
        for m in t.finditer(rx):
            return _field(m.group(0), "", t, m), m
        if labelled:
            m = re.match(r".*", t.text)
            return _field(t.text, "", t, m), m
    elif kind == "other" and labelled:
        m = re.match(r".*", t.text)
        return None, m
    return None, None


def _value_token_for(label: _Tok, toks: Sequence[_Tok]) -> Optional[_Tok]:
    """Token holding the value of a label-only token: directly below it
    (column layout), else directly to its right on the same row."""
    lx0, ly0, lw, lh = label.bbox
    pad = 0.6 * lh
    best, best_key = None, None
    for t in toks:
        if t is label or t.label is not None or not t.free(0, len(t.text)):
            continue
        tx0, ty0, tw, th = t.bbox
        overlap = min(lx0 + lw + pad, tx0 + tw) - max(lx0 - pad, tx0)
        if overlap > 0 and t.cy > label.cy + 0.4 * lh \
                and ty0 - (ly0 + lh) < 1.5 * lh:
            key = (0, ty0 - ly0, -overlap)
        elif abs(t.cy - label.cy) < 0.5 * lh and tx0 >= lx0 + lw - 2 \
                and tx0 - (lx0 + lw) < 2.0 * lh:
            key = (1, tx0 - (lx0 + lw), 0)
        else:
            continue
        if best_key is None or key < best_key:
            best, best_key = t, key
    return best


def _better(cur: Optional[ReadingField], new: Optional[ReadingField]):
    if new is None:
        return cur
    if cur is None or new.confidence > cur.confidence:
        return new
    return cur


# =============================================================================
# Core parsing (engine-independent; unit-tested with synthetic tokens)
# =============================================================================

def _same_line_pair(bar: Rect, label: Rect, toks: Sequence["_Tok"],
                    line_h: float) -> bool:
    """True when ``label`` is on the same text line as the bar (the bar's
    centre row lies inside the label box), within _PAIR_MAX_LINES line
    heights, and no other OCR box sits between them on that line.  This is
    the JEOL layout ("[bar]    100nm JEOL"): the gap is wide but the
    pairing is unambiguous."""
    bx, by, bw, bh = bar
    lx, ly, lw, lh = label
    cy = by + bh / 2.0
    if not ly - 0.2 * lh <= cy <= ly + 1.2 * lh:
        return False
    if lx >= bx + bw:
        g0, g1 = bx + bw, lx
    elif lx + lw <= bx:
        g0, g1 = lx + lw, bx
    else:
        return True
    if g1 - g0 > _PAIR_MAX_LINES * line_h:
        return False
    for t in toks:
        tx, ty, tw, th = t.bbox
        if (tx, ty, tw, th) == tuple(label) or ty > cy or ty + th < cy:
            continue
        if tx < g1 - 1 and tx + tw > g0 + 1 and not (
                tx <= lx and tx + tw >= lx + lw):       # not the label's token
            return False
    return True


def parse_tokens(tokens: Sequence[OcrToken], image_width: int,
                 scale_bar_rect: Optional[Rect] = None,
                 metadata=None, logo=None) -> InfoBarReading:
    """Classify OCR tokens into fields and pick the scale label.

    ``logo`` is an optional :class:`core.vendor_logo.LogoMatch` found in the
    bar; it names the vendor when the text does not."""
    r = InfoBarReading(status="ok", image_width_px=int(image_width),
                       tokens=list(tokens), scale_bar_rect=scale_bar_rect,
                       scale_bar_px=int(scale_bar_rect[2]) if scale_bar_rect
                       else None)
    toks = [_Tok(t.text, t.bbox, t.score) for t in tokens if t.text.strip()]
    if not toks:
        r.status, r.message = "no_text", "No text found in the data bar."
        return r
    line_h = float(np.median([t.bbox[3] for t in toks]))

    # 1. label-only tokens claim their value token (column layout)
    label_kinds = set()
    for lab in toks:
        if lab.label is None:
            continue
        label_kinds.add(lab.label)
        lab.claim(0, len(lab.text))
        vt = _value_token_for(lab, toks)
        if vt is None:
            continue
        f, m = _parse_kind(lab.label, vt, labelled=True)
        if m is not None:
            vt.claim(m.start(), m.end())
        _assign(r, lab.label, f)

    # 2. inline labelled patterns, in priority order
    for kind in ("date", "time", "wd", "fw", "hv", "curr", "vac", "mag", "det"):
        for t in toks:
            while True:
                f, m = _parse_kind(kind, t, labelled=False)
                if m is None:
                    break
                t.claim(m.start(), m.end())
                _assign(r, kind, f)

    # 3. vendor / instrument / bare detector words
    for t in toks:
        for m in t.finditer(_RE_INSTRUMENT):
            t.claim(m.start(), m.end())
            r.instrument = _better(r.instrument, _field(m.group(1), "", t, m))
        for m in t.finditer(_RE_VENDOR):
            t.claim(m.start(), m.end())
            key = re.sub(r"\s", "", m.group(1)).lower()
            r.vendor = _better(r.vendor, _field(_VENDORS.get(key, m.group(1)),
                                                "", t, m))
        for m in t.finditer(re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9+\-]*"
                                       r"(?![A-Za-z0-9])")):
            name = _DET_BY_KEY.get(_det_key(m.group(0)))
            if name and m.group(0).upper() not in ("SEM",) and len(m.group(0)) >= 2:
                t.claim(m.start(), m.end())
                r.detector = _better(r.detector, _field(name, "", t, m))
    if r.vendor is None and logo is not None:
        c = float(getattr(logo, "stroke", 0.9))
        r.vendor = ReadingField(logo.vendor, "", c, c < _CONFIRM_BELOW, "",
                                tuple(logo.bbox), "identified from the logo")
    if r.vendor is None and {"fw", "det"} <= label_kinds and \
            label_kinds & {"vac", "hv", "mag"}:
        r.vendor = ReadingField("Thermo Fisher", "", 0.5, True, "", None,
                                "inferred from the data-bar layout")

    # 4. scale-label candidates: unclaimed lengths in the sane range
    cands = []
    for t in toks:
        for m in t.finditer(_RE_LEN):
            v = _to_float(m.group(1))
            if v is None or v <= 0:
                continue
            bare_m = m.group(2) == "m"
            if bare_m and (scale_bar_rect is None or _gap(
                    scale_bar_rect, t.sub_bbox(m.start(), m.end())) > 2 * line_h):
                # a bare "m" is only a plausible lost-mu "um" when the token
                # sits right at the scale bar; elsewhere ignore it
                continue
            unit, fuzzy = _len_unit(m.group(2))
            um = v * _LEN_TO_UM[unit]
            if not _SCALE_UM[0] <= um <= _SCALE_UM[1]:
                continue
            conf = t.score * (0.6 if fuzzy else 1.0)
            note = "unit read uncertainly" if fuzzy else ""
            if t.corrected:
                conf *= 0.9
                note = _join(note, "digits repaired (O/0, l/1)")
            cands.append((_field(v, unit, t, m, conf=conf, note=note), t, m,
                          fuzzy or t.corrected))
    ambiguous = False
    if cands:
        if scale_bar_rect is not None:
            cands.sort(key=lambda c: _gap(scale_bar_rect, c[0].bbox))
            f = cands[0][0]
            d0 = _gap(scale_bar_rect, f.bbox)
            c = f.confidence
            if d0 > 4 * line_h and _same_line_pair(scale_bar_rect, f.bbox,
                                                   toks, line_h):
                f.note = _join(f.note, "label on the bar's text line")
            elif d0 > 4 * line_h:
                c *= 0.6
                ambiguous = True
                f.note = _join(f.note, "label far from the scale bar")
            if len(cands) > 1:
                d1 = _gap(scale_bar_rect, cands[1][0].bbox)
                if d1 < 2 * d0 + line_h:
                    c *= 0.75
                    ambiguous = True
                    f.note = _join(f.note, "several length labels near the bar")
        else:
            cands.sort(key=lambda c: -c[0].confidence)
            f = cands[0][0]
            c = f.confidence * (0.7 if len(cands) == 1 else 0.5)
            ambiguous = True
            f.note = _join(f.note, "scale bar not located")
        ambiguous = ambiguous or cands[0][3]
        cands[0][1].claim(cands[0][2].start(), cands[0][2].end())
        f.confidence = c
        r.scale = f
    _cross_check(r, metadata, ambiguous)
    for name in r.FIELDS:
        fv = getattr(r, name)
        if fv is not None:
            fv.confidence = round(float(fv.confidence), 3)
            if fv.confidence < _CONFIRM_BELOW:
                fv.needs_confirmation = True
    if r.scale is None:
        r.message = "Scale label not read - please enter it."
    return r


def _join(a: str, b: str) -> str:
    return f"{a}; {b}" if a else b


def _assign(r: InfoBarReading, kind: str, f: Optional[ReadingField]):
    attr = {"wd": "wd", "fw": "field_width", "hv": "hv", "vac": "vacuum",
            "mag": "magnification", "det": "detector", "date": "date",
            "time": "time", "curr": "beam_current"}.get(kind)
    if attr and f is not None:
        setattr(r, attr, _better(getattr(r, attr), f))


# Highest confidence a scale reading may have while still being flagged.
_FLAGGED_MAX = _CONFIRM_BELOW - 0.01


def _cross_check(r: InfoBarReading, metadata, ambiguous: bool = False) -> None:
    """Cross-check the scale reading.  Policy (a wrong scale silently accepted
    is the worst failure):

    * any failing check -> confidence cut, ``needs_confirmation``;
    * the scale is only auto-accepted (``needs_confirmation`` False) when an
      INDEPENDENT length check agrees: FW (with an unambiguous pick), the
      vendor magnification reference (JEOL, unambiguous pick; see
      :data:`_VENDOR_DISPLAY_MM`) or the file-metadata pixel size;
    * the magnification window alone never clears the flag: 60-800 mm spans
      ~13x, so a decade misread can still land inside it;
    * an ambiguous pick (no bar, several labels near the bar, far label,
      fuzzy unit, repaired digits) that FW agrees with only gets a small
      raise that stays below the confirmation threshold.
    """
    s = r.scale
    bar = r.scale_bar_px
    W = r.image_width_px
    if s is None:
        return
    s_um = r.scale_um
    failed = False
    fw_ok = meta_ok = mag_ref_ok = False
    # FW: the field width spans the full image width -> scale = FW*bar/W
    if r.field_width is not None and bar and W:
        fw_um = float(r.field_width.value) * _LEN_TO_UM.get(r.field_width.unit, 1.0)
        expect = fw_um * bar / W
        fw_ok = abs(s_um - expect) <= _FW_TOL * expect
        r.checks["field_width"] = fw_ok
        if not fw_ok:
            failed = True
            s.confidence *= 0.5
            s.note = _join(s.note, f"disagrees with FW (expected ~{expect:.3g} um)")
    # magnification: implied reference display width D = FW * M
    if r.magnification is not None and bar and W:
        disp_mm = s_um * W / bar * float(r.magnification.value) / 1000.0
        ok = _DISPLAY_MM[0] <= disp_mm <= _DISPLAY_MM[1]
        r.checks["magnification"] = ok
        if not ok:
            failed = True
            s.confidence *= 0.6
            s.note = _join(s.note, "inconsistent with magnification")
        # vendor reference width (see _VENDOR_DISPLAY_MM): only with the
        # vendor READ from the text and an unflagged magnification.  A miss
        # is recorded but not penalised (the reference is model-dependent).
        v = r.vendor
        ref = _VENDOR_DISPLAY_MM.get(v.value) if (
            v is not None and v.bbox is not None and not v.needs_confirmation
            and v.note == "") else None
        if ref is not None and not r.magnification.needs_confirmation:
            mag_ref_ok = ref[0] <= disp_mm <= ref[1]
            r.checks["magnification_reference"] = mag_ref_ok
    # metadata pixel size
    ppu = getattr(metadata, "px_per_um", None) if metadata is not None else None
    if ppu and bar:
        meta_ok = abs(bar / s_um - ppu) <= _META_TOL * ppu
        r.checks["metadata"] = meta_ok
        if not meta_ok:
            failed = True
            s.confidence *= 0.4
            s.note = _join(s.note, "disagrees with the file metadata")
    if metadata is not None:
        for attr, mattr in (("magnification", "magnification"),
                            ("hv", "accelerating_voltage_kv"),
                            ("wd", "working_distance_mm")):
            fv, mv = getattr(r, attr), getattr(metadata, mattr, None)
            if fv is None or not mv:
                continue
            v = float(fv.value)
            if attr == "wd" and fv.unit != "mm":      # WD printed in um
                v /= 1000.0
            ok = abs(v - mv) <= 0.02 * abs(mv)
            r.checks[f"metadata_{attr}"] = ok
            if not ok:
                fv.needs_confirmation = True
                fv.note = _join(fv.note, "disagrees with the file metadata")

    if failed:
        s.confidence = min(s.confidence, _FLAGGED_MAX)
        s.needs_confirmation = True
        return
    if meta_ok:
        # Exception to the ambiguity rule: the metadata pixel size is an
        # independent calibration written by the instrument itself.  If
        # bar_px / label reproduces it within 5 %, the label demonstrably
        # belongs to this bar and was read at the right decade (a WD/FW
        # value or a 10x misread would miss by >= ~2x), so it is accepted
        # even when the token pick itself was ambiguous.
        s.confidence = max(s.confidence, 0.99)
        s.needs_confirmation = False
        return
    if fw_ok and not ambiguous:
        s.confidence = max(s.confidence, 0.97)
        s.needs_confirmation = s.confidence < _CONFIRM_BELOW
        return
    if mag_ref_ok and not ambiguous:
        # bar px, label and magnification reproduce the vendor's fixed
        # reference width: an independent length check (a 1-2-5 misread
        # or a wrong bar misses by >= 2x)
        s.confidence = max(s.confidence, 0.9)
        s.needs_confirmation = False
        return
    if fw_ok or mag_ref_ok:
        s.confidence = min(s.confidence + 0.1, _FLAGGED_MAX)
    else:
        s.confidence = min(s.confidence, _FLAGGED_MAX)
        s.note = _join(s.note, "no independent check (FW, magnification "
                               "reference or file metadata)")
    s.needs_confirmation = True


# =============================================================================
# Entry point
# =============================================================================

def _load_image(image) -> Optional[np.ndarray]:
    if isinstance(image, np.ndarray):
        return image
    import cv2
    data = np.fromfile(str(image), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_UNCHANGED)


def _as_bgr(img: np.ndarray) -> np.ndarray:
    import cv2
    if img.dtype == np.uint16:
        # plain high-byte conversion: a min/max stretch over the whole frame
        # would change the bar's black/white levels relative to the text
        img = (img >> 8).astype(np.uint8)
    elif img.dtype != np.uint8:
        a = img.astype(np.float64)
        lo, hi = float(a.min()), float(a.max())
        img = ((a - lo) * (255.0 / (hi - lo if hi > lo else 1.0))).astype(np.uint8)
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    return img


def read_info_bar(image: Union[np.ndarray, str], scale_bar_bbox: Optional[Rect] = None,
                  *, info_bar: Optional[InfoBarResult] = None,
                  metadata=None) -> InfoBarReading:
    """Read scale label, magnification, HV, WD, FW, vacuum, detector, vendor,
    instrument, date and time from the image's data bar.

    Parameters
    ----------
    image : BGR/gray/BGRA array (full frame) or a file path.
    scale_bar_bbox : full-frame (x, y, w, h) of the scale bar if the caller
        already knows it (e.g. user-snapped); otherwise the longest
        :func:`core.scale_bar.find_scale_bar_candidates` entry that is not
        inside an OCR word.  ``w`` is taken as the bar length in px.
    info_bar : precomputed :func:`core.infobar.detect_info_bar` result.
    metadata : optional :class:`core.sem_metadata.SemMetadata` for
        cross-checking.

    Threading — CALL FROM A WORKER THREAD, never the GUI thread: the first
    call loads the OCR models (about 0.5 s, can be several seconds on a slow
    work PC) while holding the engine lock, and every call then runs OCR for
    roughly 0.4-0.7 s.  Concurrent calls are safe; they are serialised on
    the shared engine.

    Never raises; problems are reported through ``status``/``message``.
    ``needs_confirmation`` is True unless the scale was independently
    confirmed (FW, JEOL magnification reference or file metadata); the UI
    should then ask the user to
    confirm the pre-filled value."""
    t0 = time.perf_counter()
    try:
        r = _read(image, scale_bar_bbox, info_bar, metadata)
    except Exception as exc:                      # never raise to the UI
        logger.exception("info-bar OCR failed")
        r = InfoBarReading(status="error", message=f"Could not read the data "
                           f"bar ({type(exc).__name__}).")
    r.elapsed_s = round(time.perf_counter() - t0, 4)
    return r


def _inside_word(rect: Rect, boxes: Sequence[Rect]) -> bool:
    """More than _IN_WORD of the bar candidate's area lies inside ONE OCR
    word box: it is a glyph stroke (e.g. the feet of a bold "LE"), not a
    bar.  A centred label inside a split line covers only a small part of
    the joined line, so a real bar is kept."""
    x, y, w, h = rect
    area = float(max(1, w * h))
    for bx, by, bw, bh in boxes:
        ix = min(x + w, bx + bw) - max(x, bx)
        iy = min(y + h, by + bh) - max(y, by)
        if ix > 0 and iy > 0 and ix * iy / area > _IN_WORD:
            return True
    return False


def _pick_scale_bar(bgr, ib, boxes) -> Optional[Rect]:
    """Longest scale-bar candidate (>= 8 px) that is not inside a word."""
    from core.scale_bar import find_scale_bar_candidates
    for c in find_scale_bar_candidates(bgr, ib, text_boxes=boxes):
        rect = tuple(int(v) for v in c["rect"])
        if c["length_px"] >= 8 and not _inside_word(rect, boxes):
            return rect
    return None


def _read(image, scale_bar_bbox, info_bar, metadata) -> InfoBarReading:
    import cv2
    img = _load_image(image)
    if img is None or img.size == 0 or img.ndim not in (2, 3):
        return InfoBarReading(status="error", message="Image could not be read.")
    bgr = _as_bgr(img)
    H, W = bgr.shape[:2]
    engine = _get_engine()
    if engine is None:
        return InfoBarReading(status="engine_missing", available=False,
                              message=REINSTALL_MESSAGE, image_width_px=W)
    ib = info_bar if info_bar is not None else detect_info_bar(bgr)
    fallback = False
    if ib is not None and ib.bars:
        rects = [tuple(int(v) for v in b.rect) for b in ib.bars]
    else:                                         # no bar found: bottom strip
        h = min(H, max(40, int(round(0.10 * H))))
        rects = [(0, H - h, W, h)]
        fallback = True
    tokens: List[OcrToken] = []
    for (x0, y0, bw, bh) in rects:
        strip = np.ascontiguousarray(bgr[y0:y0 + bh, x0:x0 + bw])
        if strip.size == 0:
            continue
        for pts, text, score in _run_engine(engine, strip):
            xs, ys = pts[:, 0], pts[:, 1]
            bx, by = int(np.floor(xs.min())), int(np.floor(ys.min()))
            tokens.append(OcrToken(text=text, score=score, bbox=(
                x0 + bx, y0 + by, max(1, int(np.ceil(xs.max())) - bx),
                max(1, int(np.ceil(ys.max())) - by))))
    tokens.sort(key=lambda t: (t.bbox[1] // 8, t.bbox[0]))
    boxes = [t.bbox for t in tokens]
    if scale_bar_bbox is None and ib is not None:
        scale_bar_bbox = _pick_scale_bar(bgr, ib, boxes)
    logo = None
    if ib is not None and ib.bars:
        from core.vendor_logo import detect_vendor_logo
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        for b in ib.bars:
            logo = detect_vendor_logo(gray, b.rect, b.background_value, boxes)
            if logo is not None:
                break
    r = parse_tokens(tokens, W, scale_bar_bbox, metadata, logo=logo)
    r.info_bar_rect = None if fallback else rects[0]
    if fallback:
        r.bar_fallback = True
        r.message = _join(r.message, "No data bar detected; read the bottom "
                                     "of the image instead.")
        for name in r.FIELDS:
            fv = getattr(r, name)
            if fv is not None:
                fv.needs_confirmation = True
    return r
