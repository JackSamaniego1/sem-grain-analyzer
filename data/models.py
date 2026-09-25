"""
Data models for the local workspace/session store.

No Qt imports (CLAUDE.md: core/, data/, reports/ stay Qt-free). Everything
here is stdlib + numpy + the existing ``core.grain_detector`` dataclasses,
which we serialize but never mutate the definition of.

Every model dataclass carries every field with a default so
``from_dict``/generic construction can build a valid instance from a
partial ``dict`` (older manifest, or one with fields this version doesn't
know about yet): unknown keys are ignored, missing keys fall back to the
dataclass default. ``schema_version`` is always present so a future loader
can branch on it if a breaking change is ever needed.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import MISSING, dataclass, field, fields as dc_fields, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.grain_detector import AnalysisResult, GrainResult

SCHEMA_VERSION = 1


class _ClearSentinel:
    """Unique marker passed to ``update_session`` to explicitly clear a
    field back to its default ("use the session default" / "unset"),
    distinct from ``None`` which means "leave this field unchanged"."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "CLEAR"

    def __reduce__(self):
        # Never actually pickled (kept in-memory only), but keep the
        # singleton identity if it ever is.
        return (_ClearSentinel, ())


CLEAR = _ClearSentinel()


def field_default(cls, name: str) -> Any:
    """The dataclass default for ``cls.<name>`` (evaluating a
    ``default_factory`` if that's how it's declared). Used to resolve what
    ``CLEAR`` means for an arbitrary field."""
    for f in dc_fields(cls):
        if f.name == name:
            if f.default_factory is not MISSING:  # type: ignore[misc]
                return f.default_factory()
            if f.default is not MISSING:
                return f.default
            return None
    return None


# ======================================================================
# Small generic helpers shared by every model below
# ======================================================================

def _generic_from_dict(cls, d: Optional[dict]):
    """Build ``cls`` (a dataclass where every field has a default) from a
    plain dict, dropping unknown keys and leaving missing keys at their
    dataclass default."""
    valid = {f.name for f in dc_fields(cls)}
    kwargs = {k: v for k, v in (d or {}).items() if k in valid}
    return cls(**kwargs)


def _generic_to_dict(obj) -> dict:
    d = asdict(obj)
    d.pop("path", None)  # runtime-only convenience field, never persisted
    return d


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_now_display() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def session_timestamp_id() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def default_operator() -> str:
    try:
        name = os.getlogin()
        if name:
            return name
    except Exception:
        pass
    try:
        import getpass
        return getpass.getuser()
    except Exception:
        return ""


_SANITIZE_RE = re.compile(r"[^\w\-. ]")

# Deep Project/Sample/Lot/Session nesting under a workspace root plus
# Windows' MAX_PATH (260) means any one path component needs headroom; 120
# keeps a realistic four-level path comfortably under the limit.
MAX_NAME_LEN = 120

_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL",
                    *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10))}


def sanitize_name(name: str) -> str:
    """Turn arbitrary user text into a safe single path component.

    Any character that is not a word character, ``-``, ``.``, or space is
    replaced with ``_``; leading/trailing whitespace and dots are stripped
    (trailing dots/spaces are invalid on Windows); the result is capped at
    ``MAX_NAME_LEN`` characters (re-stripped after truncation, since
    truncation can itself leave a trailing dot/space) so a deeply nested
    workspace path never exceeds Windows' MAX_PATH. Raises ``ValueError``
    on an empty result so callers never silently create a folder named "".
    Windows' reserved device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9),
    case-insensitively and whether or not they carry an extension (e.g.
    "con.txt"), are prefixed with "_" to neutralise them.
    """
    safe = _SANITIZE_RE.sub("_", str(name))
    safe = safe.strip().strip(".").strip()
    if not safe:
        raise ValueError(f"Name sanitizes to empty string: {name!r}")
    if len(safe) > MAX_NAME_LEN:
        safe = safe[:MAX_NAME_LEN].strip().strip(".").strip()
        if not safe:
            raise ValueError(f"Name sanitizes to empty string after truncation: {name!r}")
    stem = safe.split(".", 1)[0]
    if stem.upper() in _RESERVED_NAMES:
        safe = f"_{safe}"
    return safe


def dedupe_name(parent: Path, name: str, exclude: Optional[Path] = None) -> str:
    """Return ``name`` or ``name (2)``, ``name (3)``, ... such that
    ``parent / result`` does not already exist (ignoring ``exclude``)."""
    candidate = name
    n = 2
    while True:
        target = parent / candidate
        if not target.exists() or target == exclude:
            return candidate
        candidate = f"{name} ({n})"
        n += 1


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json_atomic(path: Path, data: dict) -> None:
    """Write ``data`` as JSON to ``path`` atomically (temp file + os.replace
    in the same directory, so a crash mid-write never leaves a corrupt
    file)."""
    import json

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=_json_default)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def read_json(path: Path) -> dict:
    import json
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _json_default(o):
    import numpy as np
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, tuple):
        return list(o)
    raise TypeError(f"Object of type {type(o)} is not JSON serializable")


# ======================================================================
# Hierarchy metadata
# ======================================================================

@dataclass
class ProjectMeta:
    schema_version: int = SCHEMA_VERSION
    name: str = ""
    description: str = ""
    customer: str = ""
    created_utc: str = ""
    # INN-02: [{id, name, revision, decision_rule, rules:[{metric, lower,
    # upper, unit}], applies_to:{sample_ids?}}, ...] -- plain dicts here
    # (data.specs.Spec.to_dict()/from_dict() owns the shape); kept generic
    # so this module doesn't need to import data.specs.
    specs: list = field(default_factory=list)
    path: Optional[str] = None  # runtime-only, set when listed/loaded

    def to_dict(self) -> dict:
        return _generic_to_dict(self)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "ProjectMeta":
        return _generic_from_dict(cls, d)


@dataclass
class SampleMeta:
    schema_version: int = SCHEMA_VERSION
    sample_id: str = ""
    material: str = ""
    alloy_grade: str = ""
    heat_treatment: str = ""
    description: str = ""
    created_utc: str = ""
    path: Optional[str] = None

    def to_dict(self) -> dict:
        return _generic_to_dict(self)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "SampleMeta":
        return _generic_from_dict(cls, d)


@dataclass
class LotMeta:
    schema_version: int = SCHEMA_VERSION
    lot_number: str = ""
    supplier: str = ""
    received_date: str = ""
    notes: str = ""
    spec_limits: dict = field(default_factory=dict)  # placeholder for INN-02
    created_utc: str = ""
    # INN-43: the qualified reference lot for its material (SampleMeta.material);
    # at most one per material -- Workspace.set_baseline_lot keeps that true.
    # Absent in older lot.json files -> False.
    is_baseline: bool = False
    path: Optional[str] = None

    def to_dict(self) -> dict:
        return _generic_to_dict(self)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "LotMeta":
        return _generic_from_dict(cls, d)


@dataclass
class ImageManifestEntry:
    filename: str = ""
    # HIER-01: the display filename may be rendered from the workspace's
    # image_name_template; original_name preserves the source file name
    # (before renaming/sanitizing) so the UI/report can show both.
    original_name: str = ""
    sha256: str = ""
    width: int = 0
    height: int = 0
    grain_count: int = 0
    has_result: bool = False
    px_per_um: float = 0.0
    has_calibration: bool = False
    scan_rect: Optional[list] = None  # [x, y, w, h] or None
    notes: str = ""
    source_path: str = ""  # informational: where it was copied from
    filters_override: Optional[dict] = None  # None -> use session-level filters
    manual_excluded: List[int] = field(default_factory=list)  # hand-removed grain ids
    # UI-05/INN-04: hand merges/splits, in order (core.grain_edit op dicts).
    # The saved labels.npz holds the edited labels (so every reader sees the
    # edits) plus ``detector_label_image`` = the detector's original labels.
    grain_edits: List[dict] = field(default_factory=list)
    # INN-27: whether this field counts toward the lot statistics. Old
    # manifests lack both keys and load as included / no reason.
    included: bool = True
    exclusion_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "ImageManifestEntry":
        return _generic_from_dict(cls, d)


# INN-27 spec name for one analysed image record in a session manifest.
ImageRecord = ImageManifestEntry


@dataclass
class SessionMeta:
    schema_version: int = SCHEMA_VERSION
    session_id: str = ""
    label: str = ""
    created_utc: str = ""
    created_local: str = ""
    operator: str = ""
    project: str = ""
    sample_id: str = ""
    lot_number: str = ""
    instrument: str = ""
    magnification: str = ""
    accelerating_voltage_kv: float = 0.0
    working_distance_mm: float = 0.0
    detector_mode: str = ""
    px_per_um: float = 0.0
    scan_rect: Optional[list] = None
    detection_params: dict = field(default_factory=dict)
    software_version: str = ""
    notes: str = ""
    tags: List[str] = field(default_factory=list)
    filters: dict = field(default_factory=dict)  # session-level grain-filter options
    images: List[ImageManifestEntry] = field(default_factory=list)
    # INN-27: append-only audit notes (field include/exclude with reason,
    # operator, UTC time) until a global audit log (INN-07) exists.
    audit_log: List[dict] = field(default_factory=list)
    # INN-29 (optional feature): calibration check this session cites
    # (data/cal_records.py). None/"" when verification is not in use.
    calibration_check_id: Optional[str] = None
    calibration_status: str = ""      # "" (off) | "verified" | "not verified"
    calibration_reason: str = ""
    path: Optional[str] = None

    def to_dict(self) -> dict:
        d = _generic_to_dict(self)
        d["images"] = [img.to_dict() if isinstance(img, ImageManifestEntry) else img
                        for img in self.images]
        return d

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "SessionMeta":
        obj = _generic_from_dict(cls, d)
        obj.images = [ImageManifestEntry.from_dict(x) if isinstance(x, dict) else x
                       for x in (obj.images or [])]
        return obj


@dataclass
class SessionRef:
    """Returned by save_session/update_session/import_loose_images."""
    path: Path
    project: str
    sample_id: str
    lot_number: str
    session_id: str


@dataclass
class ImageEntry:
    """One image to be added/updated in a session by ``save_session`` /
    ``update_session``.

    For ``update_session`` specifically: ``scan_rect``, ``px_per_um``,
    ``notes``, ``filters_override`` and ``manual_excluded`` each follow the
    same convention — ``None`` (or, for ``px_per_um``, ``0.0``) means "leave
    the stored value unchanged"; the sentinel ``data.models.CLEAR`` means
    "clear it back to the session default / unset"; any other value sets it
    explicitly.
    """
    source_path: Optional[str] = None
    image_bgr: Optional[Any] = None  # np.ndarray, optional if source_path readable
    result: Optional[AnalysisResult] = None
    scan_rect: Optional[Tuple[int, int, int, int]] = None  # or CLEAR
    px_per_um: float = 0.0  # or CLEAR
    notes: str = ""  # or CLEAR
    filename: Optional[str] = None  # override the destination filename
    filters_override: Optional[Any] = None  # dict, None (leave), or CLEAR
    manual_excluded: Optional[Any] = None  # list[int], None (leave), or CLEAR
    grain_edits: Optional[Any] = None      # list[dict], None (leave), or CLEAR
    # detector's original label image when grain_edits is non-empty (saved
    # next to the edited labels so edits stay reversible)
    detector_label_image: Optional[Any] = None


# ======================================================================
# AnalysisResult / GrainResult <-> dict (forward-compatible)
# ======================================================================

_ANALYSIS_ARRAY_FIELDS = {"grains", "label_image", "overlay_image",
                           "binary_image", "valid_mask"}


def _analysis_scalar_field_names() -> List[str]:
    return [f.name for f in dc_fields(AnalysisResult)
            if f.name not in _ANALYSIS_ARRAY_FIELDS]


def analysis_summary_to_dict(result: AnalysisResult) -> dict:
    """Every scalar (non-array, non-grains) field of ``AnalysisResult``.
    New fields added to the dataclass later are picked up automatically."""
    return {name: getattr(result, name) for name in _analysis_scalar_field_names()}


def analysis_summary_defaults() -> dict:
    return analysis_summary_to_dict(AnalysisResult())


def analysis_summary_from_dict(d: Optional[dict]) -> dict:
    """Kwargs suitable for ``AnalysisResult(**kwargs)``: known scalar
    fields from ``d`` overlaid on defaults; unknown keys in ``d`` ignored."""
    out = analysis_summary_defaults()
    for k in out:
        if d and k in d:
            out[k] = d[k]
    return out


_GRAIN_DEFAULTS = dict(
    grain_id=0, area_px=0.0, area_um2=0.0, perimeter_px=0.0, perimeter_um=0.0,
    equivalent_diameter_px=0.0, equivalent_diameter_um=0.0, major_axis_um=0.0,
    minor_axis_um=0.0, aspect_ratio=0.0, circularity=0.0, eccentricity=0.0,
    centroid_x=0.0, centroid_y=0.0, bbox=(0, 0, 0, 0),
)


def grain_to_dict(g: GrainResult) -> dict:
    d = asdict(g)
    d["bbox"] = list(d["bbox"])
    return d


def grain_from_dict(d: Optional[dict]) -> GrainResult:
    merged = dict(_GRAIN_DEFAULTS)
    for k, v in (d or {}).items():
        if k in merged:
            merged[k] = v
    merged["bbox"] = tuple(merged["bbox"])
    return GrainResult(**merged)


def grains_to_list(grains: List[GrainResult]) -> list:
    return [grain_to_dict(g) for g in grains]


def grains_from_list(items: Optional[list]) -> List[GrainResult]:
    return [grain_from_dict(x) for x in (items or [])]


# ======================================================================
# App settings
# ======================================================================

@dataclass
class AppSettings:
    schema_version: int = SCHEMA_VERSION
    workspace_root: str = str(Path.home() / "Documents" / "GrainAnalyzer" / "Projects")
    operator: str = ""
    recent_sessions: List[str] = field(default_factory=list)
    theme: str = "system"
    last_export_dir: str = ""
    # INN-27 lot statistics (ASTM E112 sec. 15: >= 5 fields, %RA <= 10 %)
    required_fields: int = 5
    target_RA_pct: float = 10.0
    # INN-29 calibration verification: OPT-IN, default off (user requirement)
    calibration_verification_enabled: bool = False
    # [{name, tolerance_pct (2.0), check_interval_days (7)}]
    instruments: List[dict] = field(default_factory=list)
    # UX-15: user-made report palettes, each {id, name, colors: [hex, hex, hex]}
    # (reports.charts.derive_custom_palette expands the 3 colours to the
    # full palette shape at render time, so tweaking the derivation later
    # improves every saved palette for free). Listed in the report
    # designer's Palette combo next to the 4 built-ins.
    custom_palettes: List[dict] = field(default_factory=list)
    # UX-14: "Save as my default" chart options, applied to new reports
    # (reports.model.ReportModel.chart_options for the shape).
    default_chart_options: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "AppSettings":
        return _generic_from_dict(cls, d)
