"""
Resolution Profiles - saved (pixel-to-length ratio + scan area) presets that
are reused across jobs.  No Qt.

File: ``<settings dir>/resolution_profiles.json`` (beside ``settings.json``,
i.e. ``%LOCALAPPDATA%\\GrainAnalyzer``).  Atomic writes; a missing or corrupt
file loads as an empty list plus a plain-language warning; bad entries are
skipped; unknown keys are preserved through load/save.

Units: the ratio is stored as ``nm_per_px`` (unambiguous); ``unit`` is only
the display unit the user picked ("nm", "µm", "mm"; the micro sign is
U+00B5, as in the UI unit dropdown).  "um" and the Greek-mu spelling are
accepted on input and normalised; files written with "um" still load.  The app's own scale
is ``px_per_um`` = 1000 / nm_per_px (``ResolutionProfile.px_per_um``).

Fit rule (``check_fit``): a profile applies only to an image of exactly the
same width x height it was defined on.  The scan rect is then used as is and
the calibration too.  A different size returns status "size_mismatch"; the
calibration is never silently rescaled (a different pixel grid usually means
a different magnification/detector setting, so the user must decide).
If the scan rect does not lie fully inside the image, status is
"scan_area_outside".  A profile with no stored image size skips the size
check but still gets the scan-rect bounds check.

A file with a newer ``schema_version`` than this app writes is loaded
best-effort but the store goes read-only (``read_only`` / ``read_only_reason``)
so the newer file is never downgraded; mutations raise ProfileError.
"""
from __future__ import annotations

import copy
import math
import os
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from data.models import read_json, utc_now_iso, write_json_atomic
from data.settings import get_settings_dir

PROFILES_SCHEMA_VERSION = 1
UM = "µm"          # micro sign U+00B5 - matches ui/calibration_dialog
UNITS = ("nm", UM, "mm")
_NM_PER_UNIT = {"nm": 1.0, UM: 1000.0, "mm": 1_000_000.0}
_UNIT_ALIASES = {"nm": "nm", "mm": "mm", "um": UM, "µm": UM, "μm": UM}
MAX_IMPORT_BYTES = 5 * 1024 * 1024

FIT_OK = "ok"
FIT_SIZE_MISMATCH = "size_mismatch"
FIT_SCAN_OUTSIDE = "scan_area_outside"


class ProfileError(ValueError):
    """Raised with a plain-language message the UI can show as is."""


def get_profiles_path() -> Path:
    return get_settings_dir() / "resolution_profiles.json"


def normalize_unit(unit) -> Optional[str]:
    """Canonical unit ("nm", "µm", "mm") for any accepted spelling, else None."""
    if not isinstance(unit, str):
        return None
    return _UNIT_ALIASES.get(unit.strip().lower())


def _pos_finite(v) -> Optional[float]:
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f > 0 else None


def _need_unit(unit) -> str:
    u = normalize_unit(unit)
    if u is None:
        raise ProfileError(f"Unknown unit '{unit}'. Use nm, µm or mm.")
    return u


def _need_ratio(v) -> float:
    f = _pos_finite(v)
    if f is None:
        raise ProfileError("The pixel-to-length ratio must be a number greater than zero.")
    return f


def _int_ge0(v, what: str) -> int:
    if isinstance(v, bool) or v is None:
        if v is None:
            return 0
        raise ProfileError(f"{what} must be a whole number.")
    try:
        f = float(v)
        if not math.isfinite(f) or f != int(f) or f < 0:
            raise ValueError
        return int(f)
    except (TypeError, ValueError, OverflowError):
        raise ProfileError(f"{what} must be a whole number of pixels (0 or more).")


def _rect(v) -> Optional[Tuple[int, int, int, int]]:
    if v is None or v == () or v == []:
        return None
    if not isinstance(v, (list, tuple)) or len(v) != 4:
        raise ProfileError("Scan area must be four numbers: x, y, width, height.")
    try:
        out = []
        for c in v:
            if isinstance(c, bool):
                raise ValueError
            f = float(c)
            if not math.isfinite(f) or f != int(f):
                raise ValueError
            out.append(int(f))
    except (TypeError, ValueError, OverflowError):
        raise ProfileError("Scan area must be four whole numbers: x, y, width, height.")
    return tuple(out)


def nm_per_px_from(length: float, unit: str, pixels: float) -> float:
    """nm per pixel from a scale bar: ``length`` (in ``unit``) spans ``pixels``."""
    u = _need_unit(unit)
    ln, px = _pos_finite(length), _pos_finite(pixels)
    if ln is None or px is None:
        raise ProfileError("Scale bar length and pixel count must be greater than zero.")
    return ln * _NM_PER_UNIT[u] / px


@dataclass
class ResolutionProfile:
    id: str = ""
    name: str = ""
    nm_per_px: float = 0.0
    unit: str = UM
    scan_rect: Optional[Tuple[int, int, int, int]] = None   # (x, y, w, h) px
    image_w: int = 0        # image size the scan_rect/scale were defined on
    image_h: int = 0
    instrument: str = ""    # display only
    magnification: str = ""  # display only
    created_utc: str = ""
    modified_utc: str = ""
    extra: Optional[Dict[str, Any]] = None  # unknown keys, kept verbatim

    @property
    def px_per_um(self) -> float:
        return 1000.0 / self.nm_per_px if self.nm_per_px > 0 else 0.0

    def display_ratio(self) -> str:
        """e.g. '0.0125 µm/px' in the profile's display unit."""
        u = normalize_unit(self.unit) or UM
        return f"{self.nm_per_px / _NM_PER_UNIT[u]:.4g} {u}/px"

    def to_dict(self) -> dict:
        d = dict(self.extra or {})
        d.update({
            "id": self.id, "name": self.name, "nm_per_px": self.nm_per_px,
            "unit": self.unit,
            "scan_rect": list(self.scan_rect) if self.scan_rect else None,
            "image_w": self.image_w, "image_h": self.image_h,
            "instrument": self.instrument, "magnification": self.magnification,
            "created_utc": self.created_utc, "modified_utc": self.modified_utc,
        })
        return d

    def snapshot(self) -> dict:
        """Frozen copy for an image record in a session manifest."""
        return copy.deepcopy(self.to_dict())

    @classmethod
    def from_dict(cls, d: dict) -> "ResolutionProfile":
        """Raises ProfileError on unusable entries (caller skips them)."""
        if not isinstance(d, dict):
            raise ProfileError("entry is not an object")
        known = {"id", "name", "nm_per_px", "unit", "scan_rect", "image_w",
                 "image_h", "instrument", "magnification", "created_utc",
                 "modified_utc"}
        name = str(d.get("name") or "").strip()
        npp = _pos_finite(d.get("nm_per_px"))
        if not name or npp is None:
            raise ProfileError("missing name or a valid ratio")
        unit = normalize_unit(d.get("unit")) or UM
        rect = _rect(d.get("scan_rect"))
        iw = _int_ge0(d.get("image_w"), "image_w")
        ih = _int_ge0(d.get("image_h"), "image_h")
        now = utc_now_iso()
        return cls(
            id=str(d.get("id") or uuid.uuid4()), name=name, nm_per_px=npp,
            unit=unit, scan_rect=rect,
            image_w=iw, image_h=ih,
            instrument=str(d.get("instrument") or ""),
            magnification=str(d.get("magnification") or ""),
            created_utc=str(d.get("created_utc") or now),
            modified_utc=str(d.get("modified_utc") or d.get("created_utc") or now),
            extra={k: v for k, v in d.items() if k not in known} or None)


@dataclass
class FitOutcome:
    status: str                          # FIT_OK | FIT_SIZE_MISMATCH | FIT_SCAN_OUTSIDE
    px_per_um: float = 0.0               # only meaningful when ok
    scan_rect: Optional[Tuple[int, int, int, int]] = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status == FIT_OK


def check_fit(profile: ResolutionProfile, image_w: int, image_h: int) -> FitOutcome:
    """Does ``profile`` apply to an image of ``image_w`` x ``image_h`` px?

    Returns FitOutcome with status FIT_OK, FIT_SIZE_MISMATCH (image size
    differs from the one the profile was made on) or FIT_SCAN_OUTSIDE (the
    scan rect is not fully inside this image).  px_per_um / scan_rect are
    only filled when ok."""
    if profile.image_w and profile.image_h and \
            (int(image_w), int(image_h)) != (profile.image_w, profile.image_h):
        return FitOutcome(
            FIT_SIZE_MISMATCH,
            message=(f"Profile '{profile.name}' was made for "
                     f"{profile.image_w} x {profile.image_h} px images; this "
                     f"image is {int(image_w)} x {int(image_h)} px, so it does "
                     f"not match."))
    rect = profile.scan_rect
    if rect:
        x, y, w, h = rect
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > image_w or y + h > image_h:
            return FitOutcome(
                FIT_SCAN_OUTSIDE,
                message=f"Profile '{profile.name}' scan area lies outside this image.")
    return FitOutcome(FIT_OK, px_per_um=profile.px_per_um, scan_rect=rect)


def _key(name: str) -> str:
    return name.strip().casefold()


@dataclass
class ImportReport:
    added: int = 0
    renamed: int = 0
    skipped_existing: int = 0
    skipped_damaged: int = 0


class ProfileStore:
    """App-wide list of profiles backed by one JSON file.

    ``ProfileStore(path=None)`` uses ``get_profiles_path()``; tests pass a
    tmp path.  ``warnings`` holds plain-language notes from the last load.
    Every mutating call saves immediately; if the save fails (read-only or
    missing folder, full disk) the in-memory change is rolled back and
    ProfileError is raised.  ``read_only`` is True when the file was written
    by a newer app version; mutations then raise ProfileError.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else get_profiles_path()
        self.warnings: List[str] = []
        self.read_only = False
        self.read_only_reason = ""
        self._profiles: List[ResolutionProfile] = []
        self._top_extra: Dict[str, Any] = {}
        self.load()

    # ---- persistence -------------------------------------------------
    def load(self) -> List[str]:
        self.warnings = []
        self.read_only = False
        self.read_only_reason = ""
        self._profiles = []
        self._top_extra = {}
        if not self.path.exists():
            return self.warnings
        try:
            if self.path.stat().st_size > MAX_IMPORT_BYTES:
                raise ValueError("too large")
            data = read_json(self.path)
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except Exception:
            self.warnings.append(
                "The saved Resolution Profiles file could not be read, so the "
                "list is empty. Your image data is not affected.")
            return self.warnings
        self._top_extra = {k: v for k, v in data.items()
                           if k not in ("schema_version", "profiles")}
        ver = data.get("schema_version", PROFILES_SCHEMA_VERSION)
        if isinstance(ver, (int, float)) and not isinstance(ver, bool) \
                and ver > PROFILES_SCHEMA_VERSION:
            self.read_only = True
            self.read_only_reason = (
                "The Resolution Profiles file was saved by a newer version of "
                "this program. Profiles are shown but cannot be changed here, "
                "so the newer file is not overwritten. Update the program to edit them.")
            self.warnings.append(self.read_only_reason)
        self._profiles, skipped = self._parse(data.get("profiles"))
        if skipped:
            self.warnings.append(f"{skipped} damaged profile(s) were skipped.")
        return self.warnings

    def _parse(self, items) -> Tuple[List[ResolutionProfile], int]:
        out: List[ResolutionProfile] = []
        skipped = 0
        seen_ids, seen_names = set(), set()
        for it in items if isinstance(items, list) else []:
            try:
                p = ResolutionProfile.from_dict(it)
            except Exception:
                skipped += 1
                continue
            if p.id in seen_ids or _key(p.name) in seen_names:
                skipped += 1
                continue
            seen_ids.add(p.id)
            seen_names.add(_key(p.name))
            out.append(p)
        return out, skipped

    def _doc(self, profiles=None) -> dict:
        d = dict(self._top_extra)
        d["schema_version"] = PROFILES_SCHEMA_VERSION
        d["profiles"] = [p.to_dict()
                         for p in (self._profiles if profiles is None else profiles)]
        return d

    def _require_writable(self) -> None:
        if self.read_only:
            raise ProfileError(self.read_only_reason)

    def save(self) -> None:
        """Write the file.  Raises ProfileError (never a bare OSError)."""
        self._require_writable()
        try:
            write_json_atomic(self.path, self._doc())
        except OSError:
            raise ProfileError(
                "Could not save Resolution Profiles - the folder may be "
                "read-only or unavailable. Nothing was changed.")

    def _commit(self, new_list: List[ResolutionProfile]) -> None:
        """Swap in ``new_list`` and save; restore the old list if saving fails."""
        self._require_writable()
        old = self._profiles
        self._profiles = new_list
        try:
            self.save()
        except ProfileError:
            self._profiles = old
            raise

    # ---- queries -----------------------------------------------------
    def list(self) -> List[ResolutionProfile]:
        return sorted(self._profiles, key=lambda p: _key(p.name))

    def get(self, profile_id: str) -> Optional[ResolutionProfile]:
        return next((p for p in self._profiles if p.id == profile_id), None)

    def find_by_name(self, name: str) -> Optional[ResolutionProfile]:
        k = _key(name or "")
        return next((p for p in self._profiles if _key(p.name) == k), None)

    # ---- mutations ---------------------------------------------------
    def _check_name(self, name: str, own_id: Optional[str] = None) -> str:
        if name is not None and not isinstance(name, str):
            raise ProfileError("The profile name must be text.")
        name = (name or "").strip()
        if not name:
            raise ProfileError("Give the profile a name.")
        other = self.find_by_name(name)
        if other is not None and other.id != own_id:
            raise ProfileError(f"A profile called '{other.name}' already exists. "
                               f"Choose a different name.")
        return name

    def add(self, name: str, nm_per_px: float, unit: str = UM,
            scan_rect=None, image_size: Tuple[int, int] = (0, 0),
            instrument: str = "", magnification: str = "") -> ResolutionProfile:
        self._require_writable()
        name = self._check_name(name)
        npp = _need_ratio(nm_per_px)
        unit = _need_unit(unit)
        rect = _rect(scan_rect)
        try:
            iw, ih = image_size
        except (TypeError, ValueError):
            raise ProfileError("Image size must be (width, height).")
        now = utc_now_iso()
        p = ResolutionProfile(
            id=str(uuid.uuid4()), name=name, nm_per_px=npp, unit=unit,
            scan_rect=rect, image_w=_int_ge0(iw, "Image width"),
            image_h=_int_ge0(ih, "Image height"),
            instrument=str(instrument or ""), magnification=str(magnification or ""),
            created_utc=now, modified_utc=now)
        self._commit(self._profiles + [p])
        return p

    def update(self, profile_id: str, **changes) -> ResolutionProfile:
        """Change any of name, nm_per_px, unit, scan_rect, image_w, image_h,
        instrument, magnification.  Every value is validated and coerced;
        problems raise ProfileError and nothing changes.  Renaming to an
        existing name (any case) raises ProfileError."""
        self._require_writable()
        p = self.get(profile_id)
        if p is None:
            raise ProfileError("That profile no longer exists.")
        allowed = {"name", "nm_per_px", "unit", "scan_rect", "image_w",
                   "image_h", "instrument", "magnification"}
        bad = set(changes) - allowed
        if bad:
            raise ProfileError(f"Cannot change: {', '.join(sorted(bad))}")
        ch = {}
        for k, v in changes.items():
            if k == "name":
                ch[k] = self._check_name(v, p.id)
            elif k == "nm_per_px":
                ch[k] = _need_ratio(v)
            elif k == "unit":
                ch[k] = _need_unit(v)
            elif k == "scan_rect":
                ch[k] = _rect(v)
            elif k == "image_w":
                ch[k] = _int_ge0(v, "Image width")
            elif k == "image_h":
                ch[k] = _int_ge0(v, "Image height")
            else:
                ch[k] = str(v or "")
        new = replace(p, **ch, modified_utc=utc_now_iso())
        self._commit([new if q.id == p.id else q for q in self._profiles])
        return new

    def rename(self, profile_id: str, new_name: str) -> ResolutionProfile:
        return self.update(profile_id, name=new_name)

    def delete(self, profile_id: str) -> bool:
        self._require_writable()
        kept = [p for p in self._profiles if p.id != profile_id]
        if len(kept) == len(self._profiles):
            return False
        self._commit(kept)
        return True

    # ---- flash-drive transfer ---------------------------------------
    def export_to(self, path: Path, ids: Optional[List[str]] = None) -> int:
        """Write profiles (all, or those in ``ids``) to ``path``; returns count.
        Raises ProfileError if the file cannot be written."""
        chosen = [p for p in self.list() if ids is None or p.id in ids]
        try:
            write_json_atomic(Path(path), self._doc(chosen))
        except OSError:
            raise ProfileError("Could not write the export file - check the "
                               "destination is available and not read-only.")
        return len(chosen)

    def import_from(self, path: Path) -> ImportReport:
        """Merge a profiles file.  A profile whose id already exists is
        skipped (already here); a new id whose name clashes is added as
        'Name (2)', '(3)' ...  Raises ProfileError if the file is unreadable,
        not a profiles file, or larger than 5 MB, or if saving fails (then
        nothing is imported)."""
        try:
            too_big = os.path.getsize(Path(path)) > MAX_IMPORT_BYTES
        except OSError:
            raise ProfileError("That file could not be read.")
        if too_big:
            raise ProfileError("That file is too large to be a Resolution "
                               "Profiles file (over 5 MB).")
        try:
            data = read_json(Path(path))
            if not isinstance(data, dict) or "profiles" not in data:
                raise ValueError
        except Exception:
            raise ProfileError("That file is not a Resolution Profiles file.")
        self._require_writable()
        incoming, skipped = self._parse(data.get("profiles"))
        rep = ImportReport(skipped_damaged=skipped)
        merged = list(self._profiles)

        def taken(nm):
            return any(_key(q.name) == _key(nm) for q in merged)

        for p in incoming:
            if any(q.id == p.id for q in merged):
                rep.skipped_existing += 1
                continue
            if taken(p.name):
                base, n = p.name, 2
                while taken(f"{base} ({n})"):
                    n += 1
                p.name = f"{base} ({n})"
                rep.renamed += 1
            merged.append(p)
            rep.added += 1
        if rep.added:
            self._commit(merged)
        return rep
