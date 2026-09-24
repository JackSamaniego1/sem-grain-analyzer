"""
SEM metadata → calibration (INN-05)
===================================

Reads the pixel size and acquisition parameters that SEM vendors embed in
their TIFF files or write to a sidecar text file, so the technician never has
to type a pixel size.

Offline & private (D-14): pure local file reads with the Python standard
library (a ~60-line TIFF IFD reader below — no tifffile/Pillow needed at
runtime).  Only the image file itself and a sidecar in the *same folder with
the same stem* are opened.  Nothing in the metadata (paths, URLs, host names)
is ever followed.  Every parser is defensive: garbage → ``None``, never an
exception.

Supported sources, in priority order, with the confidence reported:

======================  ============================================  ==========
Vendor / source         Where / fields                                Confidence
======================  ============================================  ==========
Zeiss SmartSEM          TIFF tag 34118 ``CZ_SEM`` text:                high
                        ``Image Pixel Size = 2.791 nm``, ``EHT``,
                        ``WD``, ``Mag = 40.00 K X``, ``Signal A``
FEI / Thermo Fisher     TIFF tag 34682 (``FEI_HELIOS``) or 34680       high
                        (``FEI_SFEG``) INI text: ``[Scan]
                        PixelWidth=`` (metres), ``[EBeam] HV`` (V),
                        ``WD`` (m), ``[Detectors] Name``
TESCAN                  TIFF tag 50431 INI text (``PixelSizeX`` m,     high
                        ``HV`` V, ``WD`` m, ``Magnification``), or the
                        ``<stem>-tif.hdr`` / ``<stem>.hdr`` sidecar
Hitachi                 sidecar ``<stem>.txt`` ``[SemImageFile]``      high
                        ``PixelSize=`` (nm), ``Magnification=``,
                        ``AcceleratingVoltage=15000 Volt``,
                        ``WorkingDistance=8100 um``
JEOL                    sidecar ``<stem>.txt`` ``$CM_MAG``,            medium
                        ``$$SM_MICRON_BAR`` (px) +
                        ``$$SM_MICRON_MARKER`` (e.g. ``5um``) →
                        pixel = marker / bar (integer bar length)
ImageJ                  ImageDescription ``ImageJ=…`` + ``unit=``      medium
                        with XResolution (px per unit)
Generic TIFF            XResolution + ResolutionUnit (inch/cm).        low
                        Usually a *print DPI*, not physical — never
                        auto-apply; ``is_probably_dpi`` flags it
======================  ============================================  ==========

``px_per_um = 1 / pixel_size_um``.
"""
from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass, field
from typing import Optional, Tuple

__all__ = ["SemMetadata", "read_sem_metadata", "calibration_from_metadata",
           "CONFIDENCE_ORDER"]

CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}

# Plausible SEM pixel sizes: 1 pm .. 1 mm.
_PIX_MIN_UM = 1e-6
_PIX_MAX_UM = 1e3
_MAX_TAG_BYTES = 8 * 1024 * 1024
_MAX_SIDECAR_BYTES = 1024 * 1024

_LEN_TO_UM = {
    "m": 1e6, "mm": 1e3, "cm": 1e4, "um": 1.0, "micron": 1.0,
    "microns": 1.0, "micrometer": 1.0, "micrometre": 1.0,
    "nm": 1e-3, "pm": 1e-6, "a": 1e-4, "angstrom": 1e-4,
}


@dataclass
class SemMetadata:
    vendor: str                                   # "Zeiss", "FEI/Thermo Fisher", ...
    instrument: Optional[str] = None
    pixel_size_um_x: Optional[float] = None
    pixel_size_um_y: Optional[float] = None
    magnification: Optional[float] = None
    accelerating_voltage_kv: Optional[float] = None
    working_distance_mm: Optional[float] = None
    detector: Optional[str] = None
    date: Optional[str] = None
    confidence: str = "low"                       # "high" | "medium" | "low"
    source: str = ""                              # human-readable origin
    is_probably_dpi: bool = False                 # generic-TIFF only
    raw: dict = field(default_factory=dict)

    @property
    def pixel_size_um(self) -> Optional[float]:
        return self.pixel_size_um_x

    @property
    def px_per_um(self) -> Optional[float]:
        p = self.pixel_size_um_x
        return (1.0 / p) if p else None

    def to_dict(self) -> dict:
        return {
            "vendor": self.vendor, "instrument": self.instrument,
            "pixel_size_um_x": self.pixel_size_um_x,
            "pixel_size_um_y": self.pixel_size_um_y,
            "px_per_um": self.px_per_um,
            "magnification": self.magnification,
            "accelerating_voltage_kv": self.accelerating_voltage_kv,
            "working_distance_mm": self.working_distance_mm,
            "detector": self.detector, "date": self.date,
            "confidence": self.confidence, "source": self.source,
            "is_probably_dpi": self.is_probably_dpi,
        }


# ======================================================================
# Small helpers
# ======================================================================

_NUM_UNIT = re.compile(
    r"([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([A-Za-zµμÅ]*)")


def _norm_unit(u: str) -> str:
    u = (u or "").strip().replace("µ", "u").replace("μ", "u")
    u = u.replace("Å", "a")
    return u.lower()


def _num_unit(text) -> Tuple[Optional[float], str]:
    if text is None:
        return None, ""
    m = _NUM_UNIT.search(str(text))
    if not m:
        return None, ""
    try:
        return float(m.group(1)), _norm_unit(m.group(2))
    except ValueError:
        return None, ""


def _to_float(text) -> Optional[float]:
    v, _ = _num_unit(text)
    return v


def _length_um(text, default_unit: str) -> Optional[float]:
    v, u = _num_unit(text)
    if v is None:
        return None
    factor = _LEN_TO_UM.get(u or default_unit)
    if factor is None:
        factor = _LEN_TO_UM.get(default_unit)
    if factor is None:
        return None
    return v * factor


def _valid_pix(p: Optional[float]) -> Optional[float]:
    if p is None:
        return None
    try:
        p = float(p)
    except (TypeError, ValueError):
        return None
    if p != p or not (_PIX_MIN_UM <= p <= _PIX_MAX_UM):
        return None
    return p


def _voltage_kv(text, default_unit: str = "v") -> Optional[float]:
    v, u = _num_unit(text)
    if v is None:
        return None
    u = u or default_unit
    if u in ("kv",):
        return v
    if u in ("v", "volt", "volts"):
        return v / 1000.0
    if u == "mv":
        return v * 1000.0
    return None


def _decode(b: bytes) -> str:
    b = b.rstrip(b"\x00")
    for enc in ("utf-8", "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return ""


def _parse_ini(text: str) -> dict:
    """INI-ish ``[Section]`` / ``key=value`` → {section: {key: value}}
    (keys case-preserved; lookups via :func:`_ini_get`)."""
    out: dict = {}
    sec = ""
    for line in text.replace("\r", "\n").split("\n"):
        line = line.strip().strip("\x00")
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and "]" in line:
            sec = line[1:line.index("]")].strip()
            out.setdefault(sec, {})
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            out.setdefault(sec, {})[k.strip()] = v.strip()
    return out


def _ini_get(ini: dict, sections, key):
    key_l = key.lower()
    for s in sections:
        for sec, kv in ini.items():
            if sec.lower() != s.lower():
                continue
            for k, v in kv.items():
                if k.lower() == key_l and v != "":
                    return v
    return None


def _ini_any(ini: dict, key):
    return _ini_get(ini, list(ini.keys()), key)


# ======================================================================
# Minimal TIFF IFD0 reader (stdlib only)
# ======================================================================

_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8,
              11: 4, 12: 8, 13: 4, 16: 8, 17: 8, 18: 8}


def _read_tiff_tags(path: str, wanted) -> Optional[dict]:
    """Return {tag: value} for the ``wanted`` tags of IFD0, or None if the
    file is not a TIFF.  Values: bytes for ASCII/BYTE/UNDEFINED, tuple of
    numbers otherwise (RATIONAL → float)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
            if len(head) < 8 or head[:2] not in (b"II", b"MM"):
                return None
            bo = "<" if head[:2] == b"II" else ">"
            magic = struct.unpack(bo + "H", head[2:4])[0]
            if magic == 42:
                big = False
                ifd = struct.unpack(bo + "I", head[4:8])[0]
            elif magic == 43 and len(head) >= 16:
                big = True
                ifd = struct.unpack(bo + "Q", head[8:16])[0]
            else:
                return None
            fh.seek(0, os.SEEK_END)
            fsize = fh.tell()
            if ifd <= 0 or ifd >= fsize:
                return None
            fh.seek(ifd)
            if big:
                n = struct.unpack(bo + "Q", fh.read(8))[0]
                esize, cfmt, inline = 20, "Q", 8
            else:
                n = struct.unpack(bo + "H", fh.read(2))[0]
                esize, cfmt, inline = 12, "I", 4
            if n > 4096:
                return None
            entries = fh.read(n * esize)
            out = {}
            for i in range(n):
                e = entries[i * esize:(i + 1) * esize]
                if len(e) < esize:
                    break
                tag, typ = struct.unpack(bo + "HH", e[:4])
                if tag not in wanted:
                    continue
                count = struct.unpack(bo + cfmt, e[4:4 + inline])[0]
                tsize = _TYPE_SIZE.get(typ)
                if tsize is None:
                    continue
                nbytes = tsize * count
                if nbytes > _MAX_TAG_BYTES:
                    continue
                field_ = e[4 + inline:4 + 2 * inline]
                if nbytes <= inline:
                    data = field_[:nbytes]
                else:
                    off = struct.unpack(bo + cfmt, field_)[0]
                    if off + nbytes > fsize:
                        continue
                    pos = fh.tell()
                    fh.seek(off)
                    data = fh.read(nbytes)
                    fh.seek(pos)
                if typ in (1, 2, 6, 7):
                    out[tag] = bytes(data)
                elif typ in (5, 10):
                    f = "I" if typ == 5 else "i"
                    vals = struct.unpack(bo + f * (2 * count), data)
                    out[tag] = tuple(
                        (vals[j] / vals[j + 1]) if vals[j + 1] else 0.0
                        for j in range(0, len(vals), 2))
                else:
                    f = {3: "H", 4: "I", 8: "h", 9: "i", 11: "f", 12: "d",
                         13: "I", 16: "Q", 17: "q", 18: "Q"}[typ]
                    out[tag] = struct.unpack(bo + f * count, data)
            return out
    except (OSError, struct.error, ValueError, OverflowError):
        return None


TAG_IMAGE_WIDTH = 256
TAG_IMAGE_DESCRIPTION = 270
TAG_X_RES = 282
TAG_Y_RES = 283
TAG_RES_UNIT = 296
TAG_CZ_SEM = 34118
TAG_FEI_SFEG = 34680
TAG_FEI_HELIOS = 34682
TAG_TESCAN = 50431
_WANTED = {TAG_IMAGE_WIDTH, TAG_IMAGE_DESCRIPTION, TAG_X_RES, TAG_Y_RES,
           TAG_RES_UNIT, TAG_CZ_SEM, TAG_FEI_SFEG, TAG_FEI_HELIOS, TAG_TESCAN}


# ======================================================================
# Vendor parsers
# ======================================================================

def _parse_zeiss(text: str) -> Optional[SemMetadata]:
    """Zeiss SmartSEM CZ_SEM: pairs of ``AP_CODE`` / ``Label = value unit``
    lines (``Date :24 Mar 2017`` uses a colon)."""
    raw = {}
    for line in text.replace("\r", "\n").split("\n"):
        line = line.strip().strip("\x00")
        m = re.match(r"^([A-Za-z][^=:]*?)\s*(?:=|:)\s*(.*)$", line)
        if m:
            raw.setdefault(m.group(1).strip(), m.group(2).strip())
    low = {k.lower(): v for k, v in raw.items()}
    if not low:
        return None
    md = SemMetadata(vendor="Zeiss", raw=raw, confidence="high",
                     source="Zeiss SmartSEM TIFF tag 34118 (CZ_SEM)")
    pix = low.get("image pixel size") or low.get("pixel size")
    p = _valid_pix(_length_um(pix, "nm"))
    md.pixel_size_um_x = md.pixel_size_um_y = p
    md.accelerating_voltage_kv = _voltage_kv(low.get("eht"), "kv")
    wd = _length_um(low.get("wd"), "mm")
    md.working_distance_mm = wd / 1000.0 if wd is not None else None
    mag = low.get("mag")
    if mag:
        v, u = _num_unit(mag)
        if v is not None:
            md.magnification = v * (1000.0 if u.startswith("k") else 1.0)
    md.detector = low.get("signal a") or low.get("detector")
    date, time_ = low.get("date"), low.get("time")
    md.date = (f"{date} {time_}" if date and time_ else date)
    for k in ("sem", "instrument", "column type", "system type"):
        if low.get(k):
            md.instrument = low[k]
            break
    if md.pixel_size_um_x is None and md.magnification is None:
        return None
    return md


def _parse_fei(text: str, tag: int) -> Optional[SemMetadata]:
    ini = _parse_ini(text)
    if not ini:
        return None
    name = "FEI_HELIOS" if tag == TAG_FEI_HELIOS else (
        "FEI_SFEG" if tag == TAG_FEI_SFEG else "ImageDescription")
    md = SemMetadata(vendor="FEI/Thermo Fisher", raw=ini, confidence="high",
                     source=f"FEI/Thermo Fisher TIFF tag {tag} ({name})"
                     if tag else "FEI/Thermo Fisher INI in ImageDescription")
    px = _ini_get(ini, ["Scan", "EScan", "IScan"], "PixelWidth")
    py = _ini_get(ini, ["Scan", "EScan", "IScan"], "PixelHeight")
    md.pixel_size_um_x = _valid_pix(_length_um(px, "m"))
    md.pixel_size_um_y = _valid_pix(_length_um(py, "m")) or md.pixel_size_um_x
    hv = _ini_get(ini, ["EBeam", "Beam"], "HV")
    md.accelerating_voltage_kv = _voltage_kv(hv, "v")
    wd = _ini_get(ini, ["EBeam", "Stage", "Beam"], "WD") or \
        _ini_get(ini, ["Stage"], "WorkingDistance")
    wd_um = _length_um(wd, "m")
    md.working_distance_mm = wd_um / 1000.0 if wd_um is not None else None
    det = _ini_get(ini, ["Detectors"], "Name")
    mode = _ini_get(ini, ["Detectors"], "Mode")
    md.detector = f"{det} {mode}".strip() if det and mode else (det or mode)
    date = _ini_get(ini, ["User"], "Date")
    time_ = _ini_get(ini, ["User"], "Time")
    md.date = f"{date} {time_}" if date and time_ else date
    md.instrument = (_ini_get(ini, ["System"], "SystemType")
                     or _ini_get(ini, ["System"], "Type"))
    hfw = _to_float(_ini_get(ini, ["EScan", "Scan", "Image"], "HorFieldsize"))
    dw = _to_float(_ini_get(ini, ["System"], "DisplayWidth"))
    if hfw and dw and hfw > 0:
        md.magnification = round(dw / hfw, 3)      # display-referenced mag
    if md.pixel_size_um_x is None:
        return None
    return md


def _parse_tescan(text: str, source: str) -> Optional[SemMetadata]:
    ini = _parse_ini(text)
    if not ini or _ini_any(ini, "PixelSizeX") is None:
        return None
    md = SemMetadata(vendor="TESCAN", raw=ini, confidence="high", source=source)
    md.pixel_size_um_x = _valid_pix(_length_um(_ini_any(ini, "PixelSizeX"), "m"))
    md.pixel_size_um_y = _valid_pix(
        _length_um(_ini_any(ini, "PixelSizeY"), "m")) or md.pixel_size_um_x
    md.accelerating_voltage_kv = _voltage_kv(_ini_any(ini, "HV"), "v")
    wd = _length_um(_ini_any(ini, "WD"), "m")
    md.working_distance_mm = wd / 1000.0 if wd is not None else None
    md.magnification = _to_float(_ini_any(ini, "Magnification"))
    md.detector = _ini_any(ini, "Detector")
    date, time_ = _ini_any(ini, "Date"), _ini_any(ini, "Time")
    md.date = f"{date} {time_}" if date and time_ else date
    md.instrument = _ini_any(ini, "DeviceModel") or _ini_any(ini, "Device")
    if md.pixel_size_um_x is None:
        return None
    return md


def _parse_hitachi(text: str, source: str) -> Optional[SemMetadata]:
    ini = _parse_ini(text)
    flat = {}
    for kv in ini.values():
        for k, v in kv.items():
            flat.setdefault(k.lower(), v)
    if "pixelsize" not in flat:
        return None
    md = SemMetadata(vendor="Hitachi", raw=ini, confidence="high", source=source)
    # Hitachi writes PixelSize in nanometres (unit usually omitted).
    md.pixel_size_um_x = md.pixel_size_um_y = _valid_pix(
        _length_um(flat.get("pixelsize"), "nm"))
    md.magnification = _to_float(flat.get("magnification"))
    md.accelerating_voltage_kv = _voltage_kv(flat.get("acceleratingvoltage"), "v")
    wd = _length_um(flat.get("workingdistance"), "um")
    md.working_distance_mm = wd / 1000.0 if wd is not None else None
    md.detector = flat.get("signalname")
    md.instrument = flat.get("instructname") or flat.get("instrumentname")
    date, time_ = flat.get("date"), flat.get("time")
    md.date = f"{date} {time_}" if date and time_ else date
    if md.pixel_size_um_x is None:
        return None
    return md


def _parse_jeol(text: str, source: str, image_width: Optional[int]
                ) -> Optional[SemMetadata]:
    raw = {}
    for line in text.replace("\r", "\n").split("\n"):
        line = line.strip()
        if not line.startswith("$"):
            continue
        parts = line.split(None, 1)
        key = parts[0].lstrip("$").upper()
        raw[key] = parts[1].strip() if len(parts) > 1 else ""
    if not any(k.startswith(("CM_", "SM_")) for k in raw):
        return None
    md = SemMetadata(vendor="JEOL", raw=raw, confidence="medium", source=source)

    def get(name):          # JEOL versions use both SM_ and CM_ prefixes
        return raw.get("SM_" + name) or raw.get("CM_" + name)

    bar = _to_float(get("MICRON_BAR"))
    marker = _length_um(get("MICRON_MARKER"), "um")
    p = None
    if bar and bar > 0 and marker:
        p = marker / bar
        full = raw.get("CM_FULL_SIZE")
        if full and image_width:
            fw = _to_float(full)
            if fw and fw > 0 and int(fw) != int(image_width):
                p *= fw / float(image_width)        # image was resampled
                md.source += f" (rescaled {int(fw)}→{int(image_width)} px)"
    md.pixel_size_um_x = md.pixel_size_um_y = _valid_pix(p)
    md.magnification = _to_float(raw.get("CM_MAG"))
    md.accelerating_voltage_kv = _voltage_kv(raw.get("CM_ACCEL_VOLT"), "kv")
    for k in ("SM_WD", "CM_WD", "SM_WORKING_DISTANCE"):
        if raw.get(k):
            wd = _length_um(raw[k], "mm")
            md.working_distance_mm = wd / 1000.0 if wd is not None else None
            break
    md.detector = raw.get("CM_SIGNAL")
    md.instrument = raw.get("CM_INSTRUMENT")
    date, time_ = raw.get("CM_DATE"), raw.get("CM_TIME")
    md.date = f"{date} {time_}" if date and time_ else date
    if md.pixel_size_um_x is None and md.magnification is None:
        return None
    return md


def _parse_imagej(desc: str, xres, yres) -> Optional[SemMetadata]:
    if not desc.startswith("ImageJ="):
        return None
    kv = dict(line.split("=", 1) for line in desc.splitlines() if "=" in line)
    unit = _norm_unit(kv.get("unit", ""))
    unit = {"micron": "um", "microns": "um"}.get(unit, unit)
    factor = _LEN_TO_UM.get(unit)
    if factor is None or not xres or not xres[0]:
        return None
    px = _valid_pix(factor / float(xres[0]))
    py = _valid_pix(factor / float(yres[0])) if yres and yres[0] else px
    if px is None:
        return None
    return SemMetadata(vendor="ImageJ", pixel_size_um_x=px, pixel_size_um_y=py,
                       confidence="medium", raw=kv,
                       source=f"ImageJ calibration (unit={kv.get('unit')})")


_COMMON_DPI = {72, 75, 96, 100, 120, 144, 150, 180, 200, 240, 300, 350, 360,
               400, 600, 720, 1200, 2400}


def _parse_generic_res(xres, yres, unit) -> Optional[SemMetadata]:
    if not xres or not xres[0] or xres[0] <= 0:
        return None
    u = int(unit[0]) if unit else 2       # TIFF default unit is inch
    per = {2: 25400.0, 3: 10000.0}.get(u)
    if per is None:
        return None
    px = _valid_pix(per / float(xres[0]))
    py = _valid_pix(per / float(yres[0])) if yres and yres[0] else px
    if px is None:
        return None
    ppi = xres[0] * (2.54 if u == 3 else 1.0)
    dpi_like = (round(ppi) in _COMMON_DPI and abs(ppi - round(ppi)) < 1e-3) \
        or px > 5.0
    return SemMetadata(
        vendor="Generic TIFF", pixel_size_um_x=px, pixel_size_um_y=py,
        confidence="low", is_probably_dpi=bool(dpi_like),
        raw={"XResolution": xres[0], "ResolutionUnit": u},
        source=("TIFF XResolution/ResolutionUnit "
                f"({xres[0]:g} px/{'inch' if u == 2 else 'cm'}) — often a "
                "print DPI, not a physical pixel size"))


# ======================================================================
# Sidecars
# ======================================================================

def _sidecar_text(path: str, suffixes) -> list:
    """[(sidecar_path, text)] for existing sidecars next to ``path``."""
    folder, name = os.path.split(os.path.abspath(path))
    stem = os.path.splitext(name)[0]
    out, seen = [], set()
    for suf in suffixes:
        for cand in (stem + suf, stem + suf.upper()):
            p = os.path.join(folder, cand)
            key = os.path.normcase(p)
            if key in seen or not os.path.isfile(p):
                continue
            seen.add(key)
            try:
                if os.path.getsize(p) > _MAX_SIDECAR_BYTES:
                    continue
                with open(p, "rb") as fh:
                    out.append((p, _decode(fh.read())))
            except OSError:
                continue
    return out


# ======================================================================
# Public API
# ======================================================================

def read_sem_metadata(path, image_width: Optional[int] = None
                      ) -> Optional[SemMetadata]:
    """Parse SEM acquisition metadata for the image at ``path``.

    ``image_width`` (optional, px) lets JEOL calibrations be corrected when
    the image was resampled; for TIFFs it is read from the file.
    Returns ``None`` when nothing usable is found.  Never raises.
    """
    try:
        return _read(path, image_width)
    except Exception:          # defensive: metadata must never break loading
        return None


def _read(path, image_width):
    if path is None:
        return None
    path = os.fspath(path)
    if "://" in path or not os.path.isfile(path):
        return None                        # local files only, no URLs
    tags = _read_tiff_tags(path, _WANTED) or {}
    if image_width is None and tags.get(TAG_IMAGE_WIDTH):
        image_width = int(tags[TAG_IMAGE_WIDTH][0])

    candidates = []
    if TAG_CZ_SEM in tags:
        md = _parse_zeiss(_decode(tags[TAG_CZ_SEM]))
        if md:
            candidates.append(md)
    for t in (TAG_FEI_HELIOS, TAG_FEI_SFEG):
        if t in tags and not candidates:
            md = _parse_fei(_decode(tags[t]), t)
            if md:
                candidates.append(md)
    if TAG_TESCAN in tags and not candidates:
        md = _parse_tescan(_decode(tags[TAG_TESCAN]),
                           "TESCAN TIFF tag 50431")
        if md:
            candidates.append(md)
    desc = _decode(tags.get(TAG_IMAGE_DESCRIPTION, b"")) if isinstance(
        tags.get(TAG_IMAGE_DESCRIPTION), bytes) else ""
    if not candidates and desc.lstrip().startswith("[User]"):
        md = _parse_fei(desc, 0)
        if md:
            candidates.append(md)

    if not candidates:
        for sp, text in _sidecar_text(path, (".txt",)):
            src = f"sidecar {os.path.basename(sp)}"
            md = None
            if "$CM_" in text or "$$SM_" in text or "$SM_" in text:
                md = _parse_jeol(text, "JEOL " + src, image_width)
            elif re.search(r"(?im)^\s*PixelSize\s*=", text):
                md = _parse_hitachi(text, "Hitachi " + src)
            elif re.search(r"(?im)^\s*PixelSizeX\s*=", text):
                md = _parse_tescan(text, "TESCAN " + src)
            if md:
                candidates.append(md)
                break
    if not candidates:
        for sp, text in _sidecar_text(path, ("-tif.hdr", ".hdr")):
            md = _parse_tescan(text, f"TESCAN sidecar {os.path.basename(sp)}")
            if md:
                candidates.append(md)
                break
    if not candidates and desc:
        md = _parse_imagej(desc, tags.get(TAG_X_RES), tags.get(TAG_Y_RES))
        if md:
            candidates.append(md)
    if not candidates:
        md = _parse_generic_res(tags.get(TAG_X_RES), tags.get(TAG_Y_RES),
                                tags.get(TAG_RES_UNIT))
        if md:
            candidates.append(md)
    return candidates[0] if candidates else None


def calibration_from_metadata(path, min_confidence: str = "low",
                              image_width: Optional[int] = None
                              ) -> Optional[Tuple[float, str, str]]:
    """``(px_per_um, source_description, confidence)`` or ``None``.

    ``confidence`` is ``"high"`` (vendor pixel-size field), ``"medium"``
    (derived: JEOL bar/marker, ImageJ) or ``"low"`` (generic TIFF
    resolution — usually print DPI; show it, never auto-apply it).
    Pass ``min_confidence="medium"`` to drop the generic fallback.
    """
    md = read_sem_metadata(path, image_width=image_width)
    if md is None or md.px_per_um is None:
        return None
    if CONFIDENCE_ORDER.get(md.confidence, 0) < CONFIDENCE_ORDER.get(
            min_confidence, 0):
        return None
    desc = md.source
    if md.vendor and md.instrument:
        desc = f"{md.source} — {md.instrument}"
    return float(md.px_per_um), desc, md.confidence
