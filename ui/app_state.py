"""
Application state for the v3 shell: workspace, current node, the open
session (images + results in memory) and autosave.

All disk IO is pushed off the GUI thread through :func:`ui.workers.run_task`
(reads on the global pool, writes on a single-thread pool so saves to a
session never overlap).  Pages talk to each other only through this object's
signals, never directly.
"""
from __future__ import annotations

import itertools
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QImage, QUndoCommand, QUndoStack

from core.grain_detector import AnalysisResult, DetectionParams
from core.metrics import compute_statistics
from data import settings as data_settings
from data.catalog import Catalog
from data.models import (
    CLEAR, AppSettings, ImageEntry, SessionMeta, read_json, write_json_atomic,
)
from data.session_io import load_session, save_session, update_session
from data.workspace import Workspace
from ui.filtering import (
    PostFilterOptions, default_options, filter_image, options_from_dict, options_to_dict,
    preview_params, remeasure_excluded,
)
from ui.canvas.layers import kept_labels
from ui.workers import read_image, run_task, serial_pool, snapshot_result, thumb_qimage

_uid_counter = itertools.count(1)
_seq_counter = itertools.count(1)

KIND_ORDER = ("workspace", "project", "sample", "lot", "session")
META_FILES = {"project": "project.json", "sample": "sample.json",
              "lot": "lot.json", "session": "manifest.json"}


# ======================================================================
# UI-only persisted state (window geometry, last page, wizard memory, ...)
# ======================================================================

def ui_state_path() -> Path:
    return data_settings.get_settings_dir() / "ui_state.json"


def load_ui_state() -> dict:
    p = ui_state_path()
    try:
        return read_json(p) if p.exists() else {}
    except Exception:
        return {}


def save_ui_state(state: dict) -> None:
    try:
        write_json_atomic(ui_state_path(), state)
    except OSError:
        pass


def params_to_dict(p: DetectionParams) -> dict:
    return asdict(p)


def params_from_dict(d: Optional[dict]) -> DetectionParams:
    base = DetectionParams()
    for k, v in (d or {}).items():
        if hasattr(base, k):
            try:
                setattr(base, k, type(getattr(base, k))(v))
            except (TypeError, ValueError):
                pass
    return base


# ======================================================================
# Hierarchy node
# ======================================================================

@dataclass(frozen=True)
class NodeRef:
    kind: str   # workspace | project | sample | lot | session
    path: Path

    @property
    def name(self) -> str:
        return self.path.name


def node_for_path(root: Path, path: Path) -> NodeRef:
    """Classify a folder inside the workspace by depth."""
    root, path = Path(root), Path(path)
    try:
        depth = len(path.relative_to(root).parts)
    except ValueError:
        depth = 0
    return NodeRef(KIND_ORDER[min(depth, 4)], path)


def node_chain(root: Path, node: Optional[NodeRef]) -> List[NodeRef]:
    """Workspace -> ... -> node."""
    root = Path(root)
    chain = [NodeRef("workspace", root)]
    if node is None or node.kind == "workspace":
        return chain
    try:
        parts = node.path.relative_to(root).parts
    except ValueError:
        return chain
    p = root
    for i, part in enumerate(parts[:4]):
        p = p / part
        chain.append(NodeRef(KIND_ORDER[i + 1], p))
    return chain


def node_display_name(node: NodeRef, profile=None, crumb: bool = False) -> str:
    """Human label from the node's metadata file (cheap: one small json).

    ``profile`` (HIER-01) supplies the level labels ("Lot" → the user's
    word); ``crumb=True`` prefixes every level with its label
    ("Job # 24-117")."""
    from ui import hierarchy_ui as hui
    if node.kind == "workspace":
        return "Workspace"
    d = hui.read_meta(node.kind, node.path)
    if crumb:
        return hui.crumb_caption(profile, node.kind, d, node.path)
    return hui.node_caption(profile, node.kind, d, node.path)


def session_title(created_local: str, fallback: str) -> str:
    try:
        dt = datetime.strptime(created_local, "%Y-%m-%d %H:%M:%S")
        return "Session " + dt.strftime("%d %b %Y %H:%M")
    except (TypeError, ValueError):
        return fallback


# ======================================================================
# In-memory session model
# ======================================================================

@dataclass(eq=False)
class ImageDoc:
    filename: str
    path: Optional[Path] = None
    image_bgr: Optional[np.ndarray] = None
    thumb: Optional[QImage] = None
    raw: Optional[AnalysisResult] = None       # detector output (+ hand merges/splits)
    result: Optional[AnalysisResult] = None    # filtered result: stats, export, reports
    excluded: Dict[int, List[str]] = field(default_factory=dict)   # grain id -> reasons
    counts: Dict[str, int] = field(default_factory=dict)           # reason -> n
    manual: set = field(default_factory=set)                       # hand-removed grain ids
    # UI-05/INN-04: hand merges/splits (core.grain_edit op dicts, in order)
    # and the detector's original labels they were applied to (None while
    # there are no edits: raw.label_image IS the detector output then).
    edits: List[dict] = field(default_factory=list)
    detector_labels: Optional[np.ndarray] = None
    filter_override: Optional[PostFilterOptions] = None            # None -> session filters
    filter_gen: int = 0
    scan_rect: Optional[tuple] = None     # per-image override (None -> session)
    px_override: float = 0.0              # per-image calibration (0 -> session)
    status: str = "pending"               # pending|queued|running|done|error
    progress: int = 0
    message: str = ""
    original_name: str = ""               # source file name before template renaming
    sem_meta: Optional[dict] = None       # INN-05: read_sem_metadata(path).to_dict()
    cal_suggestion: Optional[tuple] = None  # (px_per_um, source, confidence)
    info_bar: Optional[dict] = None       # DET-05: detect_info_bar().to_dict(); {} = none
    # UX-09: the lot / session folder the image belongs to (None = the
    # session's primary record) and whether its pixels are still loading
    record: Optional["RecordRef"] = None
    loading: bool = False
    # UX-02: where the scan area / scale came from ("auto", "metadata",
    # "manual", "" = unknown / saved earlier) and the scale-bar line found
    # in the info bar (px) with the length the operator typed for it (µm)
    scan_source: str = ""
    scale_source: str = ""
    bar_px: float = 0.0
    bar_um: float = 0.0
    uid: int = field(default_factory=lambda: next(_uid_counter))
    seq: int = field(default_factory=lambda: next(_seq_counter))

    @property
    def name(self) -> str:
        return self.filename

    @property
    def display_name(self) -> str:
        return Path(self.filename).stem

    def tooltip(self) -> str:
        orig = self.original_name
        if orig and orig != self.filename:
            return f"{self.filename}\nOriginal file: {orig}"
        return self.filename


@dataclass(eq=False)
class RecordRef:
    """UX-09: one lot / session folder whose images are in the analyzer.

    ``px_per_um`` / ``scan_rect`` / ``filters`` are the record's own
    session-level values as read from disk (used to keep each image's
    effective scale, scan area and filters when several records are loaded
    together with one shared set of analysis settings)."""
    path: Path
    meta: SessionMeta
    project_meta: dict = field(default_factory=dict)
    sample_meta: dict = field(default_factory=dict)
    lot_meta: dict = field(default_factory=dict)
    px_per_um: float = 0.0
    scan_rect: Optional[tuple] = None
    filters: dict = field(default_factory=dict)
    loaded: bool = True

    @property
    def is_lot(self) -> bool:
        return (self.path / "lot.json").exists()


@dataclass(eq=False)
class SessionDoc:
    path: Path
    meta: SessionMeta
    images: List[ImageDoc] = field(default_factory=list)
    px_per_um: float = 0.0
    scan_rect: Optional[tuple] = None
    params: dict = field(default_factory=dict)
    filters: PostFilterOptions = field(default_factory=PostFilterOptions)
    filters_touched: bool = False
    acquisition_dirty: bool = False
    project_meta: dict = field(default_factory=dict)
    sample_meta: dict = field(default_factory=dict)
    lot_meta: dict = field(default_factory=dict)
    records: List[RecordRef] = field(default_factory=list)       # UX-09
    removed: List[ImageDoc] = field(default_factory=list)        # UX-06 (not on disk)

    def image(self, uid) -> Optional[ImageDoc]:
        for im in self.images:
            if im.uid == uid:
                return im
        return None

    def index_of(self, uid) -> int:
        for i, im in enumerate(self.images):
            if im.uid == uid:
                return i
        return -1

    @property
    def is_lot(self) -> bool:
        """HIER-01: the lot folder itself is the record (images in the lot)."""
        return (self.path / "lot.json").exists()

    @property
    def multi(self) -> bool:
        """UX-09: images of several lots / sessions are loaded together."""
        return len(self.records) > 1

    def record_for(self, im: ImageDoc) -> Optional[RecordRef]:
        if im.record is not None:
            return im.record
        return self.records[0] if self.records else None

    def removed_for(self, record_paths=None) -> List[ImageDoc]:
        """Images taken out of the analyzer (optionally of some records)."""
        if record_paths is None:
            return list(self.removed)
        want = {Path(p) for p in record_paths}
        return [im for im in self.removed
                if (self.record_for(im) is not None and self.record_for(im).path in want)]

    @property
    def title(self) -> str:
        if self.multi:
            n = len(self.records)
            lots = {r.path if r.is_lot else r.path.parent for r in self.records}
            what = f"{len(lots)} lots" if len(lots) > 1 else f"{n} sessions"
            return f"{what} · {len(self.images)} images"
        if self.is_lot:
            lot = self.meta.lot_number or (self.lot_meta or {}).get("lot_number") or self.path.name
            return self.meta.label or str(lot)
        return self.meta.label or session_title(self.meta.created_local, self.path.name)


def _counts_from(excluded: Dict[int, List[str]]) -> Dict[str, int]:
    c: Dict[str, int] = {}
    for rs in excluded.values():
        for r in rs:
            c[r] = c.get(r, 0) + 1
    return c


def _derive_excluded(res, bgr, manual, opts, params) -> Dict[int, List[str]]:
    """Worker-thread: which saved grains were excluded, and why.

    The saved label image keeps every raw grain while grains.json holds only
    the kept ones, so the excluded ids are the labels that are not kept.  The
    reasons are recomputed by running the saved filter options once more
    (preview ASTM — only the exclusions are used); labels the filters do not
    account for are ignored."""
    lab = res.label_image
    if lab is None:
        return {}
    kept = {int(g.grain_id) for g in res.grains}
    ids = {int(i) for i in np.unique(lab).tolist()} - {0} - kept
    if not ids:
        return {}
    manual = {int(i) for i in manual}
    raw = remeasure_excluded(res, ids)
    try:
        out = filter_image(raw, bgr, opts, frozenset(manual & ids), preview_params(params))
        reasons = out.get("excluded") or {}
    except Exception:
        reasons = {}
    excluded = {}
    for gid in sorted(ids):
        rs = list(reasons.get(gid) or [])
        if gid in manual and "manual" not in rs:
            rs.append("manual")
        if rs:
            excluded[gid] = rs
    return excluded


def _image_dict(si, bgr, legacy: Optional[dict] = None, opts: Optional[PostFilterOptions] = None,
                params: Optional[DetectionParams] = None) -> dict:
    """Worker-thread: rebuild raw + filtered results for one saved image.

    Filter state comes from the manifest's first-class fields (DATA-09):
    ``filters_override`` / ``manual_excluded`` per image.  ``legacy`` is the
    pre-DATA-09 ``detection_params["post_filters"]["images"][name]`` entry,
    whose ``excluded`` map (with reasons) is used when present."""
    res = si.result if bgr is not None else None
    if res is not None and res.label_image is not None and bgr is not None             and res.label_image.shape[:2] != bgr.shape[:2]:
        res.label_image = None  # inconsistent file; keep the numbers only
    entry = si.entry
    manual = [int(i) for i in (getattr(entry, "manual_excluded", None) or [])]
    override = getattr(entry, "filters_override", None)
    legacy = legacy or {}
    excluded = {int(k): list(v) for k, v in (legacy.get("excluded") or {}).items()}
    if not excluded and res is not None and bgr is not None:
        use = options_from_dict(override) if override else (opts or PostFilterOptions())
        excluded = _derive_excluded(res, bgr, manual, use, params or DetectionParams())
    raw = None
    if res is not None:
        raw = remeasure_excluded(res, excluded.keys()) if excluded else res
        if excluded and res.label_image is not None:
            import copy as _copy
            res = _copy.copy(res)
            res.label_image = kept_labels(res.label_image, list(excluded))
    edits = [dict(op) for op in (getattr(entry, "grain_edits", None) or [])
             if isinstance(op, dict)]
    base = getattr(si.result, "detector_label_image", None) if edits else None
    return dict(filename=si.filename, path=si.path, bgr=bgr, result=res, raw=raw,
                excluded=excluded, manual=manual, override=override,
                edits=edits, detector_labels=base,
                thumb=thumb_qimage(bgr), scan_rect=entry.scan_rect,
                px=float(entry.px_per_um or 0.0), notes=entry.notes)


def _load_session_bundle(path: Path) -> dict:
    """Worker-thread: read manifest, every image, every saved result.

    A lot used as the record (images stored in the lot) that has no
    manifest yet is opened as an empty record (manifest created in place)."""
    path = Path(path)
    if (path / "lot.json").exists() and not (path / "manifest.json").exists():
        save_session(path, {}, [], in_place=True)
    ls = load_session(path)
    m = ls.manifest
    legacy = ((m.detection_params or {}).get("post_filters", {}) or {}).get("images", {}) or {}
    params = params_from_dict({k: v for k, v in (m.detection_params or {}).items()
                               if k != "post_filters"})
    opts = options_from_dict(m.filters) if m.filters else default_options(m.scan_rect)
    out = []
    for si in ls.images:
        bgr = read_image(si.path) if si.path.exists() else None
        out.append(_image_dict(si, bgr, legacy.get(si.filename), opts, params))
    return dict(loaded=ls, images=out, filters=dict(m.filters or {}))


def _load_new_images(session_path: Path, known: set) -> List[dict]:
    """Worker-thread: images present in the manifest but not yet in memory."""
    ls = load_session(session_path)
    out = []
    for si in ls.images:
        if si.filename in known:
            continue
        bgr = read_image(si.path) if si.path.exists() else None
        out.append(_image_dict(si, bgr))
    return out


def _persist(session_path: Path, root: Path, entries: List[ImageEntry],
             meta_updates: dict) -> str:
    """Worker-thread autosave (DATA-08).

    HIER-01: a lot used as the session (images stored in the lot) is saved
    in place, so a re-analysis archives the previous results to
    ``results/_history`` instead of silently overwriting them."""
    session_path = Path(session_path)
    if entries and (session_path / "lot.json").exists():
        save_session(session_path, {}, entries, in_place=True)
        entries = []
    update_session(session_path, images=entries or None,
                   meta_updates=meta_updates or None, catalog=Catalog(root))
    return datetime.now().strftime("%H:%M")


def _persist_many(root: Path, jobs: List[tuple]) -> str:
    """Worker-thread autosave of several records (UX-09): ``jobs`` =
    [(record_path, entries, meta_updates)].  Records are written one after
    the other on the serial pool, so saves never overlap."""
    stamp = ""
    for path, entries, meta in jobs:
        if entries or meta:
            stamp = _persist(path, root, entries, meta)
    return stamp or datetime.now().strftime("%H:%M")


def _index_records(paths: List[Path]) -> List[dict]:
    """Worker-thread (UX-09): manifests + level metadata of several records,
    without reading any pixels or results (fast, so the analyzer can list
    every image at once and fill them in as they load)."""
    out = []
    for path in paths:
        path = Path(path)
        try:
            if (path / "lot.json").exists() and not (path / "manifest.json").exists():
                continue                  # a lot with no images yet
            ls = load_session(path)
        except Exception:
            continue
        m = ls.manifest
        out.append(dict(path=path, meta=m, project_meta=ls.project_meta or {},
                        sample_meta=ls.sample_meta or {}, lot_meta=ls.lot_meta or {},
                        filenames=[e.filename for e in m.images]))
    return out


def records_under(root: Path, paths) -> List[Path]:
    """Worker-thread (UX-09): every record (a lot holding its own images,
    or a session folder) below the given job / part / lot / session
    folders, in folder order, without duplicates."""
    from data.hierarchy import RESERVED_LOT_SUBDIRS
    root = Path(root)
    ws = Workspace(root)
    out: List[Path] = []

    def add(p: Path) -> None:
        if p not in out:
            out.append(p)

    def add_lot(lp: Path) -> None:
        lp = Path(lp)
        if (lp / "manifest.json").exists():
            add(lp)
        if lp.is_dir():
            for d in sorted(lp.iterdir()):
                if d.is_dir() and d.name not in RESERVED_LOT_SUBDIRS and \
                        (d / "manifest.json").exists():
                    add(d)

    for p in paths:
        p = Path(p)
        kind = node_for_path(root, p).kind
        try:
            if kind == "project":
                for sm in ws.list_samples(p):
                    for lm in ws.list_lots(p, Path(sm.path)):
                        add_lot(Path(lm.path))
            elif kind == "sample":
                for lm in ws.list_lots(p.parent, p):
                    add_lot(Path(lm.path))
            elif kind == "lot":
                add_lot(p)
            elif kind == "session" and (p / "manifest.json").exists():
                add(p)
            elif kind == "workspace":
                for pm in ws.list_projects():
                    pp = Path(pm.path)
                    for sm in ws.list_samples(pp):
                        for lm in ws.list_lots(pp, Path(sm.path)):
                            add_lot(Path(lm.path))
        except (OSError, FileNotFoundError, ValueError):
            continue
    return out


def setup_probe(image_bgr, path=None, want_meta: bool = True) -> dict:
    """Worker-thread (UX-02): everything "Auto-find scan area & scale bar"
    needs for one image -- the SEM info bar (-> scan area), the scale-bar
    line inside it (length in px) and the pixel size stored in the file by
    the microscope (-> scale).  Local only; nothing leaves the PC."""
    out: Dict[str, Any] = {"info": {}, "bar_px": 0.0, "cal": None, "meta": None}
    if image_bgr is None:
        return out
    try:
        from core.infobar import detect_info_bar
        from core.scale_bar import find_scale_bar_line
        ib = detect_info_bar(image_bgr)
        out["info"] = ib.to_dict() if ib is not None and ib.bars else {}
        if ib is not None and ib.bars:
            bar = find_scale_bar_line(image_bgr, info_bar=ib)
            if bar and bar.get("length_px", 0) >= 10:
                out["bar_px"] = float(bar["length_px"])
    except Exception:
        pass
    if want_meta and path:
        try:
            info = probe_sem_metadata(str(path))
        except Exception:
            info = None
        if info:
            out["meta"] = info.get("meta") or {}
            out["cal"] = tuple(info["cal"]) if info.get("cal") else None
    return out


def _add_images_worker(session_path: Path, root: Path, paths: List[str], known: set) -> List[dict]:
    """Worker-thread: copy images into the open session and load them.
    Lot records append in place, named by the profile's image template."""
    session_path = Path(session_path)
    entries = [ImageEntry(source_path=str(p)) for p in paths]
    if (session_path / "lot.json").exists():
        from data.hierarchy import context_for_session
        ws = Workspace(root)
        profile = ws.profile
        save_session(session_path, {}, entries, in_place=True, catalog=Catalog(root),
                     image_name_template=profile.image_name_template,
                     name_context=context_for_session(session_path, profile))
    else:
        update_session(session_path, images=entries, catalog=Catalog(root))
    return _load_new_images(session_path, known)


def detect_info_bar_dict(image_bgr) -> dict:
    """Worker-thread (DET-05): the SEM data bar of a full frame, or {}."""
    from core.infobar import detect_info_bar
    if image_bgr is None:
        return {}
    try:
        info = detect_info_bar(image_bgr)
    except Exception:
        return {}
    return info.to_dict() if info is not None and info.bars else {}


def probe_sem_metadata(path) -> Optional[dict]:
    """Worker-thread (INN-05): SEM acquisition metadata + calibration."""
    from core.sem_metadata import calibration_from_metadata, read_sem_metadata
    if not path:
        return None
    md = read_sem_metadata(path)
    cal = calibration_from_metadata(path)
    if md is None and cal is None:
        return None
    return {"meta": md.to_dict() if md is not None else {},
            "cal": tuple(cal) if cal else None}


# ======================================================================
# Undo: manual grain removal (feeds the post-filter's manual_excluded)
# ======================================================================

class ExcludeGrainsCommand(QUndoCommand):
    """Remove grains by hand (non-destructive); undo puts them back."""

    def __init__(self, state: "AppState", uid: int, grain_ids: List[int]) -> None:
        super().__init__()
        self.state = state
        self.uid = uid
        doc = state.session.image(uid) if state.session else None
        known = {g.grain_id for g in (doc.raw.grains if doc and doc.raw else [])}
        already = doc.manual if doc else set()
        self.ids = [int(g) for g in grain_ids if int(g) in known and int(g) not in already]
        n = len(self.ids)
        self.setText(f"Remove {n} grain{'s' if n != 1 else ''}")

    def is_empty(self) -> bool:
        return not self.ids

    def _doc(self) -> Optional[ImageDoc]:
        return self.state.session.image(self.uid) if self.state.session else None

    def redo(self) -> None:
        doc = self._doc()
        if doc is None:
            return
        doc.manual |= set(self.ids)
        for g in self.ids:
            doc.excluded.setdefault(g, [])
            if "manual" not in doc.excluded[g]:
                doc.excluded[g].append("manual")
        self.state._manual_changed(doc)

    def undo(self) -> None:
        doc = self._doc()
        if doc is None:
            return
        doc.manual -= set(self.ids)
        for g in self.ids:
            rs = [r for r in doc.excluded.get(g, []) if r != "manual"]
            if rs:
                doc.excluded[g] = rs
            else:
                doc.excluded.pop(g, None)
        self.state._manual_changed(doc)


DeleteGrainsCommand = ExcludeGrainsCommand  # backwards-compatible name


class RestoreGrainsCommand(QUndoCommand):
    """Put hand-removed grains back (e.g. re-ticked on the Reports page);
    undo removes them again.  Filter exclusions are not touched."""

    def __init__(self, state: "AppState", uid: int, grain_ids: List[int]) -> None:
        super().__init__()
        self.state = state
        self.uid = uid
        doc = state.session.image(uid) if state.session else None
        manual = doc.manual if doc else set()
        self.ids = [int(g) for g in grain_ids if int(g) in manual]
        n = len(self.ids)
        self.setText(f"Restore {n} grain{'s' if n != 1 else ''}")

    def is_empty(self) -> bool:
        return not self.ids

    def _doc(self) -> Optional[ImageDoc]:
        return self.state.session.image(self.uid) if self.state.session else None

    def redo(self) -> None:
        doc = self._doc()
        if doc is None:
            return
        doc.manual -= set(self.ids)
        for g in self.ids:
            rs = [r for r in doc.excluded.get(g, []) if r != "manual"]
            if rs:
                doc.excluded[g] = rs
            else:
                doc.excluded.pop(g, None)
        self.state._manual_changed(doc)

    def undo(self) -> None:
        doc = self._doc()
        if doc is None:
            return
        doc.manual |= set(self.ids)
        for g in self.ids:
            doc.excluded.setdefault(g, [])
            if "manual" not in doc.excluded[g]:
                doc.excluded[g].append("manual")
        self.state._manual_changed(doc)


class GrainGeometryCommand(QUndoCommand):
    """Merge / split grains (UI-05 / INN-04): swaps the image's raw result
    (edited labels, re-measured grains) and its persisted edit list; undo
    restores the previous raw result.  The filtered result, statistics and
    ASTM G are then recomputed by the post-filter like after any other edit."""

    def __init__(self, state: "AppState", uid: int, new_raw: AnalysisResult,
                 outcome, text: str) -> None:
        super().__init__(text)
        self.state = state
        self.uid = uid
        doc = self._doc()
        self.before = (doc.raw, list(doc.edits), doc.detector_labels)
        base = doc.detector_labels
        if base is None and doc.raw is not None:
            base = doc.raw.label_image          # the detector's own labels
        self.after = (new_raw, list(doc.edits) + [dict(outcome.op)], base)
        self.op = dict(outcome.op)

    def _doc(self) -> Optional[ImageDoc]:
        return self.state.session.image(self.uid) if self.state.session else None

    def _apply(self, raw, edits, base) -> None:
        doc = self._doc()
        if doc is None:
            return
        doc.raw, doc.edits = raw, list(edits)
        doc.detector_labels = base if edits else None
        ids = {int(g.grain_id) for g in (raw.grains if raw else [])}
        doc.manual &= ids
        doc.excluded = {k: v for k, v in doc.excluded.items() if k in ids}
        self.state._geometry_changed(doc)

    def redo(self) -> None:
        self._apply(*self.after)

    def undo(self) -> None:
        self._apply(*self.before)


# ======================================================================
# AppState
# ======================================================================

class AppState(QObject):
    settings_changed = Signal()
    workspace_changed = Signal()                 # root changed or hierarchy edited
    node_changed = Signal(object)                # NodeRef | None
    session_loading = Signal(object)             # Path
    session_opened = Signal()
    session_closed = Signal()
    images_changed = Signal()                    # list of images changed
    image_updated = Signal(object)               # uid: status/result/thumb changed
    result_edited = Signal(object)               # uid: filtered result / exclusions changed
    filters_changed = Signal()                   # session or image filter options changed
    filtering_changed = Signal(object, bool)     # uid, busy (filter card spinner)
    current_image_changed = Signal(object)       # uid | None
    calibration_changed = Signal()
    save_state_changed = Signal(str, str)        # state, detail
    message = Signal(str, str, str)              # title, body, severity (toasts)
    about_to_flush = Signal()                    # pages persist their own pending edits
    profile_changed = Signal()                   # HIER-01: labels / fields / templates edited
    sem_metadata_ready = Signal(object)          # uid: SEM metadata / calibration read
    metadata_calibration = Signal(object, float, str, str, float)  # uid, px, src, conf, prev
    info_bar_ready = Signal(object)              # uid: info bar detected (or not)
    records_loading = Signal(int, int)           # UX-09: records loaded, total
    records_loaded = Signal()                    # UX-09: every record's pixels are in
    setup_changed = Signal()                     # UX-02: scan area / scale readiness changed
    setup_progress = Signal(int, int)            # UX-02: auto-find done, total
    setup_finished = Signal(dict)                # UX-02: auto-find summary
    overlay_opacity_changed = Signal(float)      # UX-05

    def __init__(self, settings_path: Optional[Path] = None,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.settings_path = Path(settings_path) if settings_path else None
        self.settings: AppSettings = data_settings.load_settings(self.settings_path)
        self.ui_state: dict = load_ui_state()
        self._workspace: Optional[Workspace] = None
        self.current_node: Optional[NodeRef] = None
        self.session: Optional[SessionDoc] = None
        self.current_uid = None
        self.undo_stack = QUndoStack(self)
        self._dirty: set = set()
        self._meta_dirty = False
        self._saving = False
        self._save_again = False
        self._filtering: set = set()
        self._final_pending: set = set()
        self._final_timer = QTimer(self)
        self._final_timer.setSingleShot(True)
        self._final_timer.setInterval(1000)
        self._final_timer.timeout.connect(self._run_final_filters)
        self.save_state = "none"
        self.last_saved = ""
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(700)
        self._save_timer.timeout.connect(self.save_now)
        self._records_pending = 0
        self._setup_pending: set = set()
        self._setup_stats: dict = {}

    # ------------------------------------------------------------------ settings
    def save_settings(self) -> None:
        try:
            data_settings.save_settings(self.settings, self.settings_path)
        except OSError:
            pass
        self.settings_changed.emit()

    def persist_ui_state(self) -> None:
        save_ui_state(self.ui_state)

    @property
    def root(self) -> Path:
        # an empty/blank setting must never resolve to the current directory
        return Path(self.settings.workspace_root.strip() or AppSettings().workspace_root)

    @property
    def workspace(self) -> Workspace:
        if self._workspace is None or self._workspace.root != self.root:
            self._workspace = Workspace(self.root)
        return self._workspace

    def catalog(self) -> Catalog:
        return Catalog(self.root)

    def set_workspace_root(self, path) -> None:
        self.close_session()
        self.settings.workspace_root = str(path)
        self._workspace = None
        self.save_settings()
        self.set_node(None)
        self.profile_changed.emit()
        self.workspace_changed.emit()

    # ------------------------------------------------------------------ hierarchy profile
    @property
    def profile(self):
        """The workspace's HierarchyProfile (HIER-01)."""
        return self.workspace.profile

    def lot_mode(self) -> bool:
        return self.profile.images_location == "lot"

    def set_profile(self, profile) -> None:
        """Persist a new profile and let every page relabel itself."""
        self.workspace.set_profile(profile)
        self.profile_changed.emit()

    def operator(self) -> str:
        from data.models import default_operator
        return self.settings.operator or default_operator()

    def default_params(self) -> DetectionParams:
        """New-session detection parameters.  UX-01: without saved defaults
        the mode is AI-assisted (or Boundary when the AI model file is not
        installed -- the Analyze page then shows the reinstall hint)."""
        from ui.detection_modes import normalize_mode
        saved = self.ui_state.get("default_params")
        p = params_from_dict(saved)
        p.detection_mode = normalize_mode(p.detection_mode if saved else "")
        return p

    # ------------------------------------------------------------------ UX-05
    @property
    def overlay_opacity(self) -> float:
        """Opacity (0-1) of the grain overlay on the canvas and in report
        images.  Stored locally in the app's ui_state (key
        ``overlay_opacity``)."""
        try:
            v = float(self.ui_state.get("overlay_opacity", 1.0))
        except (TypeError, ValueError):
            v = 1.0
        return max(0.0, min(1.0, v))

    def set_overlay_opacity(self, value: float, persist: bool = True) -> None:
        v = max(0.0, min(1.0, float(value)))
        if abs(v - self.overlay_opacity) < 1e-6 and "overlay_opacity" in self.ui_state:
            return
        self.ui_state["overlay_opacity"] = round(v, 3)
        if persist:
            self.persist_ui_state()
        self.overlay_opacity_changed.emit(v)

    # ------------------------------------------------------------------ nodes
    def set_node(self, node: Optional[NodeRef]) -> None:
        self.current_node = node
        self.node_changed.emit(node)

    def chain(self) -> List[NodeRef]:
        return node_chain(self.root, self.current_node)

    # ------------------------------------------------------------------ session
    def open_session(self, path, on_done=None) -> None:
        path = Path(path)
        if self.session is not None and self.session.path == path and not self.session.multi:
            if on_done:
                on_done(True)
            self.session_opened.emit()
            return
        self.flush()
        self.session_loading.emit(path)

        def done(bundle):
            self._install_session(path, bundle)
            if on_done:
                on_done(True)

        def failed(msg):
            self.message.emit("Could not open session", msg.splitlines()[0], "danger")
            if on_done:
                on_done(False)

        run_task(_load_session_bundle, path, on_done=done, on_error=failed)

    def _install_session(self, path: Path, bundle: dict) -> None:
        ls = bundle["loaded"]
        m: SessionMeta = ls.manifest
        params = {k: v for k, v in (m.detection_params or {}).items() if k != "post_filters"}
        if not params:
            params = params_to_dict(self.default_params())
        scan = tuple(m.scan_rect) if m.scan_rect else None
        pf = bundle.get("filters") or {}
        doc = SessionDoc(path=path, meta=m, px_per_um=float(m.px_per_um or 0.0),
                         scan_rect=scan, params=params,
                         filters=options_from_dict(pf) if pf else default_options(scan),
                         filters_touched=bool(pf),
                         project_meta=ls.project_meta or {}, sample_meta=ls.sample_meta or {},
                         lot_meta=ls.lot_meta or {})
        rec = RecordRef(path=path, meta=m, project_meta=doc.project_meta,
                        sample_meta=doc.sample_meta, lot_meta=doc.lot_meta,
                        px_per_um=doc.px_per_um, scan_rect=scan, filters=dict(pf))
        doc.records = [rec]
        for d in bundle["images"]:
            doc.images.append(self._make_image(doc, d, rec))
        self._records_pending = 0
        self.session = doc
        self.undo_stack.clear()
        self._dirty.clear()
        self._meta_dirty = False
        self.current_uid = doc.images[0].uid if doc.images else None
        self.settings = data_settings.add_recent_session(
            self.settings, str(path), self.settings_path, save=False)
        self.save_settings()
        self.current_node = NodeRef("lot" if doc.is_lot else "session", path)
        self._set_save_state("saved", "")
        self.node_changed.emit(self.current_node)
        self.session_opened.emit()
        self.images_changed.emit()
        self.current_image_changed.emit(self.current_uid)
        self.calibration_changed.emit()
        self.filters_changed.emit()

    def _make_image(self, doc: SessionDoc, d: dict, rec: Optional[RecordRef] = None,
                    into: Optional[ImageDoc] = None) -> ImageDoc:
        """ImageDoc from a loaded image dict.  ``rec`` is the record the image
        belongs to: its own session-level scale / scan area / filters become
        per-image values when they differ from the analyzer's shared ones
        (UX-09).  ``into`` fills an existing placeholder instead."""
        rec = rec or (doc.records[0] if doc.records else None)
        res = d.get("result")
        px = float(d.get("px") or 0.0)
        eff = px if px > 0 else (rec.px_per_um if rec is not None else doc.px_per_um)
        override = eff if (eff > 0 and (doc.px_per_um <= 0
                                        or abs(eff - doc.px_per_um) > 1e-9)) else 0.0
        excluded = d.get("excluded") or {}
        scan = tuple(d["scan_rect"]) if d.get("scan_rect") else (
            rec.scan_rect if rec is not None else None)
        ov = d.get("override")
        if not ov and rec is not None and rec.filters and doc.records and \
                rec is not doc.records[0] and \
                options_to_dict(options_from_dict(rec.filters)) != options_to_dict(doc.filters):
            ov = rec.filters
        im = into if into is not None else ImageDoc(filename=d["filename"])
        im.filename = d["filename"]
        im.path, im.image_bgr, im.thumb = d.get("path"), d.get("bgr"), d.get("thumb")
        im.result, im.raw = res, d.get("raw") or res
        im.excluded, im.counts = excluded, _counts_from(excluded)
        im.manual = set(d.get("manual") or [])
        im.edits = list(d.get("edits") or [])
        im.detector_labels = d.get("detector_labels")
        im.filter_override = options_from_dict(ov) if ov else None
        im.scan_rect = scan
        im.px_override = override
        im.original_name = d.get("original_name") or ""
        im.status = "done" if res is not None else "pending"
        im.progress, im.message = 0, ""
        im.record = rec
        im.loading = False
        if im.image_bgr is None:
            im.status, im.message = "error", "Image file is missing or unreadable"
        if im.scan_rect is not None and im.scan_rect == doc.scan_rect:
            im.scan_rect = None
        return im

    # ------------------------------------------------------------------ UX-09 several records
    def open_records(self, paths, on_done=None) -> None:
        """Load the images of several lots / sessions into the analyzer.

        The manifests are indexed first (fast, off-thread) so every image is
        listed at once; pixels and saved results then stream in record by
        record on the thread pool (``records_loading`` / ``records_loaded``)
        -- the GUI never waits for them.  One record = :meth:`open_session`."""
        paths = list(dict.fromkeys(Path(p) for p in paths))
        if not paths:
            if on_done:
                on_done(False)
            return
        if len(paths) == 1:
            self.open_session(paths[0], on_done)
            return
        self.flush()
        self.session_loading.emit(paths[0])

        def done(index):
            if not index:
                self.message.emit("Nothing to load", "The selected folders contain no images.",
                                  "info")
                if on_done:
                    on_done(False)
                return
            if len(index) == 1:
                self.open_session(index[0]["path"], on_done)
                return
            self._install_records(index)
            if on_done:
                on_done(True)

        def failed(msg):
            self.message.emit("Could not load the images", msg.splitlines()[0], "danger")
            if on_done:
                on_done(False)

        run_task(_index_records, paths, on_done=done, on_error=failed)

    def _install_records(self, index: List[dict]) -> None:
        first = index[0]
        m0: SessionMeta = first["meta"]
        params = {k: v for k, v in (m0.detection_params or {}).items() if k != "post_filters"}
        if not params:
            params = params_to_dict(self.default_params())
        scan0 = tuple(m0.scan_rect) if m0.scan_rect else None
        pf0 = dict(m0.filters or {})
        doc = SessionDoc(path=first["path"], meta=m0, px_per_um=float(m0.px_per_um or 0.0),
                         scan_rect=scan0, params=params,
                         filters=options_from_dict(pf0) if pf0 else default_options(scan0),
                         filters_touched=bool(pf0), project_meta=first["project_meta"],
                         sample_meta=first["sample_meta"], lot_meta=first["lot_meta"])
        for d in index:
            m = d["meta"]
            rec = RecordRef(path=d["path"], meta=m, project_meta=d["project_meta"],
                            sample_meta=d["sample_meta"], lot_meta=d["lot_meta"],
                            px_per_um=float(m.px_per_um or 0.0),
                            scan_rect=tuple(m.scan_rect) if m.scan_rect else None,
                            filters=dict(m.filters or {}), loaded=False)
            doc.records.append(rec)
            for fn in d["filenames"]:
                doc.images.append(ImageDoc(filename=fn, record=rec, loading=True,
                                           status="queued", message="Loading…"))
        self.session = doc
        self.undo_stack.clear()
        self._dirty.clear()
        self._meta_dirty = False
        self.current_uid = doc.images[0].uid if doc.images else None
        self.current_node = NodeRef("lot" if doc.is_lot else "session", doc.path)
        self._set_save_state("saved", "")
        self._records_pending = len(doc.records)
        self.node_changed.emit(self.current_node)
        self.session_opened.emit()
        self.images_changed.emit()
        self.current_image_changed.emit(self.current_uid)
        self.calibration_changed.emit()
        self.filters_changed.emit()
        self.records_loading.emit(0, len(doc.records))
        for rec in doc.records:
            run_task(_load_session_bundle, rec.path,
                     on_done=lambda b, r=rec: self._fill_record(doc, r, b),
                     on_error=lambda msg, r=rec: self._record_failed(doc, r, msg))

    def is_loading(self) -> bool:
        """True while a multi-record load is still streaming pixels in."""
        return self._records_pending > 0

    def _record_done(self, doc: SessionDoc, rec: RecordRef) -> None:
        rec.loaded = True
        self._records_pending = max(0, self._records_pending - 1)
        total = len(doc.records)
        self.records_loading.emit(total - self._records_pending, total)
        if self._records_pending == 0:
            self.records_loaded.emit()
            self.setup_changed.emit()
            if self.is_dirty():
                self.schedule_save()

    def _fill_record(self, doc: SessionDoc, rec: RecordRef, bundle: dict) -> None:
        if self.session is not doc:
            return
        by_name = {im.filename: im for im in doc.images + doc.removed if im.record is rec}
        for d in bundle["images"]:
            ph = by_name.pop(d["filename"], None)
            if ph is None:
                continue
            self._make_image(doc, d, rec, into=ph)
            self.image_updated.emit(ph.uid)
        for ph in by_name.values():          # listed but not loadable
            ph.loading = False
            ph.status, ph.message = "error", "Image file is missing or unreadable"
            self.image_updated.emit(ph.uid)
        cur = doc.image(self.current_uid) if self.current_uid is not None else None
        if cur is not None and cur.record is rec:
            self.current_image_changed.emit(self.current_uid)
        self.calibration_changed.emit()
        self._record_done(doc, rec)

    def _record_failed(self, doc: SessionDoc, rec: RecordRef, msg: str) -> None:
        if self.session is not doc:
            return
        first = msg.splitlines()[0] if msg else "Unreadable"
        for im in doc.images + doc.removed:
            if im.record is rec and im.loading:
                im.loading = False
                im.status, im.message = "error", first
                self.image_updated.emit(im.uid)
        self.message.emit("Could not load a folder", f"{rec.path.name}: {first}", "danger")
        self._record_done(doc, rec)

    # ------------------------------------------------------------------ UX-06 remove / put back
    def remove_images(self, uids) -> int:
        """Take images out of the analyzer only -- the files, the lot and
        the saved results are untouched.  Returns how many were removed."""
        doc = self.session
        if doc is None:
            return 0
        uids = set(uids)
        gone = [im for im in doc.images if im.uid in uids]
        if not gone:
            return 0
        idx = doc.index_of(self.current_uid)
        doc.images = [im for im in doc.images if im.uid not in uids]
        doc.removed.extend(gone)
        if self.current_uid in uids:
            self.current_uid = (doc.images[min(max(idx, 0), len(doc.images) - 1)].uid
                                if doc.images else None)
        self.images_changed.emit()
        self.current_image_changed.emit(self.current_uid)
        self.setup_changed.emit()
        return len(gone)

    def restore_images(self, record_paths=None, uids=None) -> int:
        """Put removed images back ("Add all from lot"), optionally only
        those of some records, and pick up images added to those records on
        disk meanwhile (off-thread).  ``uids`` restores exactly those images
        (Undo of a removal).  Returns how many came back at once."""
        doc = self.session
        if doc is None:
            return 0
        if uids is not None:
            want_ids = set(uids)
            back = [im for im in doc.removed if im.uid in want_ids]
            doc.removed = [im for im in doc.removed if im.uid not in want_ids]
            doc.images = sorted(doc.images + back, key=lambda im: im.seq)
            if back:
                self.images_changed.emit()
                self.setup_changed.emit()
            return len(back)
        back = doc.removed_for(record_paths)
        ids = {im.uid for im in back}
        doc.removed = [im for im in doc.removed if im.uid not in ids]
        doc.images = sorted(doc.images + back, key=lambda im: im.seq)
        want = None if record_paths is None else {Path(p) for p in record_paths}
        for rec in doc.records:
            if (want is not None and rec.path not in want) or not rec.loaded:
                continue
            known = {im.filename for im in doc.images + doc.removed
                     if doc.record_for(im) is rec}

            def got(new, rec=rec):
                if self.session is not doc or not new:
                    return
                for d in new:
                    doc.images.append(self._make_image(doc, d, rec))
                self.images_changed.emit()
                self.setup_changed.emit()
            run_task(_load_new_images, rec.path, known, on_done=got)
        if back:
            if self.current_uid is None and doc.images:
                self.current_uid = doc.images[0].uid
                self.current_image_changed.emit(self.current_uid)
            self.images_changed.emit()
            self.setup_changed.emit()
        return len(back)

    def close_session(self) -> None:
        if self.session is None:
            return
        self.flush()
        self.session = None
        self.current_uid = None
        self._records_pending = 0
        self._setup_pending = set()
        self.undo_stack.clear()
        self._set_save_state("none", "")
        self.session_closed.emit()
        self.images_changed.emit()
        self.current_image_changed.emit(None)

    def add_images(self, paths: List[str], on_done=None) -> None:
        """Copy images into the open session (off-thread) and load them."""
        if self.session is None or not paths:
            return
        doc = self.session
        cur = self.current_image()
        rec = (doc.record_for(cur) if cur is not None else None) or doc.records[0]
        known = {im.filename for im in doc.images + doc.removed if doc.record_for(im) is rec}
        root = self.root
        self.flush()

        def done(new):
            if self.session is not doc:
                return
            added = []
            for d in new:
                im = self._make_image(doc, d, rec)
                doc.images.append(im)
                added.append(im.uid)
            if self.current_uid is None and doc.images:
                self.current_uid = doc.images[0].uid
                self.current_image_changed.emit(self.current_uid)
            self.images_changed.emit()
            where = "the lot" if doc.is_lot else "the session"
            self.message.emit("Images added", f"{len(new)} image(s) copied into {where}.",
                              "success")
            self.probe_metadata(added)
            if on_done:
                on_done(len(new))

        run_task(_add_images_worker, rec.path, root, [str(p) for p in paths], known,
                 on_done=done, pool=serial_pool(),
                 on_error=lambda m: self.message.emit("Could not add images",
                                                      m.splitlines()[0], "danger"))

    # ------------------------------------------------------------------ SEM metadata (INN-05)
    def probe_metadata(self, uids=None) -> None:
        """Read SEM metadata of the given images (default: all) off-thread.

        High-confidence vendor calibration is applied to a not-yet-analysed
        image at once (per-image scale, undoable from the toast); medium is
        offered; low is only remembered as a prefill.  Instrument / kV / WD /
        magnification fill the session's acquisition fields still empty."""
        doc = self.session
        if doc is None:
            return
        wanted = None if uids is None else set(uids)
        for im in [im for im in doc.images if (wanted is None or im.uid in wanted) and im.path]:
            def done(info, im=im):
                if self.session is not doc or info is None:
                    return
                im.sem_meta = info.get("meta") or {}
                cal = info.get("cal")
                im.cal_suggestion = tuple(cal) if cal else None
                if doc.record_for(im) is doc.records[0]:
                    self._fill_acquisition(doc, im.sem_meta)
                if cal and cal[2] == "high" and im.px_override <= 0 and im.result is None:
                    prev = im.px_override
                    self.set_calibration(float(cal[0]), im.uid)
                    self.metadata_calibration.emit(im.uid, float(cal[0]), str(cal[1]),
                                                   "high", float(prev))
                elif cal:
                    self.metadata_calibration.emit(im.uid, float(cal[0]), str(cal[1]),
                                                   str(cal[2]), float(im.px_override))
                self.sem_metadata_ready.emit(im.uid)
            run_task(probe_sem_metadata, str(im.path), on_done=done)

    # ------------------------------------------------------------------ SEM info bar (DET-05)
    def info_bar_for(self, im: Optional[ImageDoc]) -> Optional[dict]:
        """The image's detected data bar: from its analysis result when
        analysed (full-frame coordinates), else from a background probe
        (:meth:`probe_info_bar`).  ``None`` = unknown / none."""
        if im is None:
            return None
        for src in (getattr(im.result, "info_bar", None), getattr(im.raw, "info_bar", None),
                    im.info_bar):
            if src and src.get("bar_rect"):
                return src
        return None

    def probe_info_bar(self, uid) -> None:
        """Detect the data bar of a not-yet-analysed image off-thread."""
        doc = self.session
        im = doc.image(uid) if doc is not None else None
        if im is None or im.info_bar is not None or im.image_bgr is None:
            return
        im.info_bar = {}          # probing; never probe twice

        def done(d, im=im):
            if self.session is not doc:
                return
            im.info_bar = d or {}
            self.info_bar_ready.emit(im.uid)

        run_task(detect_info_bar_dict, im.image_bgr, on_done=done)

    def use_info_bar_as_scan_area(self, uid, this_image: bool = False) -> Optional[tuple]:
        """Scan area = the micrograph without its data bar.  Returns the
        previous rectangle (for Undo) or ``None`` when nothing was done."""
        im = self.session.image(uid) if self.session is not None else None
        info = self.info_bar_for(im)
        if im is None or not info or not info.get("analysis_rect"):
            return None
        prev = im.scan_rect if this_image else self.session.scan_rect
        self.set_scan_rect(tuple(info["analysis_rect"]), uid if this_image else None)
        return (prev,)

    # ------------------------------------------------------------------ UX-02 pre-analysis setup
    def setup_issues(self, im: Optional[ImageDoc]) -> List[str]:
        """What still has to be set before ``im`` may be analysed:
        ``"loading"``, ``"missing"`` (no pixels), ``"scan"`` (no confirmed
        scan area) and/or ``"scale"`` (no scale / magnification)."""
        if im is None:
            return ["missing"]
        if im.loading:
            return ["loading"]
        if im.image_bgr is None:
            return ["missing"]
        out = []
        if self.scan_for(im) is None:
            out.append("scan")
        if self.px_for(im) <= 0:
            out.append("scale")
        return out

    def setup_ready(self, im: Optional[ImageDoc]) -> bool:
        return not self.setup_issues(im)

    def is_setting_up(self) -> bool:
        return bool(self._setup_pending)

    def auto_setup(self, uids=None) -> int:
        """"Auto-find scan area & scale bar" on the given images (default:
        every image in the analyzer), off the GUI thread.  Per image: the
        SEM info bar becomes the scan area boundary (else the full frame),
        and the scale comes from the file's own metadata or from the scale
        bar found in the info bar.  Values the operator set by hand are
        kept.  Returns how many images are being examined."""
        doc = self.session
        if doc is None:
            return 0
        wanted = None if uids is None else set(uids)
        targets = [im for im in doc.images if (wanted is None or im.uid in wanted)
                   and not im.loading and im.image_bgr is not None
                   and im.uid not in self._setup_pending]
        if not targets:
            return 0
        if not self._setup_pending:
            self._setup_stats = dict(total=0, done=0, info_bar=0, full_frame=0, kept_scan=0,
                                     scale_meta=0, scale_bar=0, kept_scale=0,
                                     needs_length=0, no_scale=0)
        self._setup_stats["total"] += len(targets)
        for im in targets:
            self._setup_pending.add(im.uid)
        self.setup_progress.emit(self._setup_stats["done"], self._setup_stats["total"])
        for im in targets:
            run_task(setup_probe, im.image_bgr, str(im.path) if im.path else None,
                     im.cal_suggestion is None,
                     on_done=lambda out, im=im: self._apply_setup(doc, im, out),
                     on_error=lambda _m, im=im: self._apply_setup(doc, im, {}))
        return len(targets)

    def _matching_bar_um(self, im: ImageDoc) -> float:
        """Length (µm) the operator gave a scale bar of the same pixel
        length on another image (same magnification)."""
        best = 0.0
        for o in self.images():
            if o is im or o.bar_um <= 0 or o.bar_px <= 0:
                continue
            if abs(o.bar_px - im.bar_px) <= 2:
                if o.scale_source == "manual":
                    return o.bar_um
                best = best or o.bar_um
        return best

    def _apply_setup(self, doc: SessionDoc, im: ImageDoc, out: dict) -> None:
        if self.session is not doc or im.uid not in self._setup_pending:
            return
        self._setup_pending.discard(im.uid)
        st = self._setup_stats
        st["done"] += 1
        if im.image_bgr is not None:
            info = out.get("info") or {}
            if info or im.info_bar is None:
                im.info_bar = info
            # scan area
            ar = info.get("analysis_rect")
            if im.scan_source == "manual" or (doc.scan_rect is not None and im.scan_rect is None
                                              and not ar):
                st["kept_scan"] += 1
            else:
                if ar:
                    im.scan_rect = tuple(int(v) for v in ar)
                    st["info_bar"] += 1
                else:
                    h, w = im.image_bgr.shape[:2]
                    im.scan_rect = (0, 0, int(w), int(h))
                    st["full_frame"] += 1
                im.scan_source = "auto"
            # scale
            if out.get("meta") is not None:
                im.sem_meta = out.get("meta") or {}
                cal = out.get("cal")
                im.cal_suggestion = tuple(cal) if cal else None
            bar = float(out.get("bar_px") or 0.0)
            if bar > 0:
                im.bar_px = bar
            cal = im.cal_suggestion
            if im.scale_source == "manual":
                st["kept_scale"] += 1
            elif cal and str(cal[2]) in ("high", "medium") and float(cal[0]) > 0:
                im.px_override = float(cal[0])
                im.scale_source = "metadata"
                st["scale_meta"] += 1
            elif im.bar_px > 0 and (im.bar_um > 0 or self._matching_bar_um(im) > 0):
                im.bar_um = im.bar_um or self._matching_bar_um(im)
                im.px_override = im.bar_px / im.bar_um
                im.scale_source = "auto"
                st["scale_bar"] += 1
            elif self.px_for(im) > 0:
                st["kept_scale"] += 1
            elif im.bar_px > 0:
                st["needs_length"] += 1
            else:
                st["no_scale"] += 1
            self._meta_dirty = True
            self.image_updated.emit(im.uid)
        self.setup_progress.emit(st["done"], st["total"])
        if not self._setup_pending:
            self.calibration_changed.emit()
            self.setup_changed.emit()
            self.info_bar_ready.emit(self.current_uid)
            self.schedule_save()
            self.setup_finished.emit(dict(st))

    def set_bar_length(self, uid, length_um: float, same_bar: bool = True) -> List:
        """The operator typed the length of the scale bar found on image
        ``uid``: scale = bar px / µm.  ``same_bar`` also applies it to the
        other images whose bar has the same pixel length (same
        magnification) and no scale of their own from metadata or by hand.
        Returns the undo snapshot."""
        doc = self.session
        im = doc.image(uid) if doc is not None else None
        if im is None or im.bar_px <= 0 or length_um <= 0:
            return []
        targets = [im]
        if same_bar:
            targets += [o for o in doc.images if o is not im and o.bar_px > 0
                        and abs(o.bar_px - im.bar_px) <= 2
                        and o.scale_source not in ("manual", "metadata")]
        snap = [(o.uid, o.px_override, o.scale_source, o.bar_um) for o in targets]
        for o in targets:
            o.bar_um = float(length_um)
            o.px_override = o.bar_px / float(length_um)
            o.scale_source = "manual" if o is im else "auto"
        self._meta_dirty = True
        self.calibration_changed.emit()
        self.setup_changed.emit()
        self.schedule_save()
        return snap

    def restore_scales(self, snap: List) -> None:
        """Undo :meth:`set_bar_length` / :meth:`set_calibration_all`."""
        doc = self.session
        if doc is None or not snap:
            return
        for item in snap:
            if item[0] == "session":
                doc.px_per_um = float(item[1])
                continue
            o = doc.image(item[0])
            if o is not None:
                o.px_override, o.scale_source = float(item[1]), item[2]
                if len(item) > 3:
                    o.bar_um = float(item[3])
        self._meta_dirty = True
        self.calibration_changed.emit()
        self.setup_changed.emit()
        self.schedule_save()

    def set_calibration_all(self, px_per_um: float) -> List:
        """UX-03: one scale for EVERY image in the analyzer (per-image
        scales are dropped).  Returns the undo snapshot."""
        doc = self.session
        if doc is None:
            return []
        snap = [("session", doc.px_per_um)] + [
            (o.uid, o.px_override, o.scale_source, o.bar_um) for o in doc.images]
        doc.px_per_um = float(px_per_um)
        for o in doc.images:
            o.px_override = 0.0
            o.scale_source = "manual"
        self._meta_dirty = True
        self.calibration_changed.emit()
        self.setup_changed.emit()
        self.schedule_save()
        return snap

    def set_scan_rect_all(self, rect) -> List:
        """One scan area for every image in the analyzer (per-image areas
        dropped).  ``None`` = the full frame of each image.  Returns the
        undo snapshot."""
        doc = self.session
        if doc is None:
            return []
        snap = [("session", doc.scan_rect)] + [(o.uid, o.scan_rect, o.scan_source)
                                               for o in doc.images]
        rect = tuple(int(v) for v in rect) if rect else None
        for o in doc.images:
            if rect is None and o.image_bgr is not None:
                h, w = o.image_bgr.shape[:2]
                o.scan_rect = (0, 0, int(w), int(h))
            else:
                o.scan_rect = None
            o.scan_source = "manual"
        self.set_scan_rect(rect, None)
        self.setup_changed.emit()
        return snap

    def restore_scans(self, snap: List) -> None:
        doc = self.session
        if doc is None or not snap:
            return
        for item in snap:
            if item[0] == "session":
                doc.scan_rect = item[1]
                continue
            o = doc.image(item[0])
            if o is not None:
                o.scan_rect, o.scan_source = item[1], item[2]
        self._meta_dirty = True
        self.calibration_changed.emit()
        self.setup_changed.emit()
        self.schedule_save()

    def _fill_acquisition(self, doc: "SessionDoc", md: dict) -> None:
        m = doc.meta
        changed = False
        if md.get("instrument") and not m.instrument:
            m.instrument = str(md["instrument"])
            changed = True
        if md.get("accelerating_voltage_kv") and not m.accelerating_voltage_kv:
            m.accelerating_voltage_kv = float(md["accelerating_voltage_kv"])
            changed = True
        if md.get("working_distance_mm") and not m.working_distance_mm:
            m.working_distance_mm = float(md["working_distance_mm"])
            changed = True
        if md.get("magnification") and not m.magnification:
            m.magnification = f"{float(md['magnification']):g}×"
            changed = True
        if changed:
            doc.acquisition_dirty = True
            self._meta_dirty = True
            self.schedule_save()

    # ------------------------------------------------------------------ images
    def images(self) -> List[ImageDoc]:
        return list(self.session.images) if self.session else []

    def current_image(self) -> Optional[ImageDoc]:
        return self.session.image(self.current_uid) if self.session else None

    def set_current_image(self, uid) -> None:
        if uid == self.current_uid:
            return
        self.current_uid = uid
        self.current_image_changed.emit(uid)

    # ------------------------------------------------------------------ INN-29 / FIX-08
    def calibration_store(self):
        from data.cal_records import CalibrationStore
        return CalibrationStore(self.root, enabled=bool(
            getattr(self.settings, "calibration_verification_enabled", False)))

    def refresh_calibration_check(self):
        """Stamp the open session with the calibration check it cites
        (``data.cal_records.apply_to_session``) and return the lookup, or
        None (no session / records unreadable).  Only a changed stamp marks
        the session dirty, so with the feature off nothing is ever written."""
        doc = self.session
        if doc is None:
            return None
        from data.cal_records import apply_to_session
        m = doc.meta
        try:
            lk = self.calibration_store().find_applicable_check(
                m.instrument, m.magnification,
                instruments=getattr(self.settings, "instruments", None))
        except (OSError, ValueError):
            return None
        before = (m.calibration_check_id, m.calibration_status, m.calibration_reason)
        apply_to_session(m, lk)
        if (m.calibration_check_id, m.calibration_status, m.calibration_reason) != before:
            doc.acquisition_dirty = True
            self._meta_dirty = True
            self.schedule_save()
        return lk

    def px_for(self, im: ImageDoc) -> float:
        if im.px_override > 0:
            return im.px_override
        return self.session.px_per_um if self.session else 0.0

    def scan_for(self, im: ImageDoc) -> Optional[tuple]:
        if im.scan_rect:
            return im.scan_rect
        return self.session.scan_rect if self.session else None

    def set_calibration(self, px_per_um: float, uid=None) -> None:
        """Global calibration, or a per-image override when ``uid`` is given."""
        if self.session is None:
            return
        if uid is None:
            self.session.px_per_um = float(px_per_um)
            self._meta_dirty = True
        else:
            im = self.session.image(uid)
            if im is None:
                return
            im.px_override = float(px_per_um)
            im.scale_source = "manual" if px_per_um > 0 else ""
            self._meta_dirty = True        # per-image fields travel with every save
        self.calibration_changed.emit()
        self.setup_changed.emit()
        self.schedule_save()

    def reset_image_calibration(self, uid) -> None:
        """Drop an image's own scale: it follows the session scale again
        (persisted as ``CLEAR``)."""
        self.set_calibration(0.0, uid)

    def reset_image_scan_rect(self, uid) -> None:
        """Drop an image's own scan area: it follows the session's again."""
        if self.session is None:
            return
        im = self.session.image(uid)
        if im is None or im.scan_rect is None:
            return
        im.scan_rect = None
        im.scan_source = ""
        self._meta_dirty = True
        self.calibration_changed.emit()
        self.setup_changed.emit()
        self.schedule_save()

    def set_scan_rect(self, rect, uid=None) -> None:
        if self.session is None:
            return
        rect = tuple(int(v) for v in rect) if rect else None
        if uid is None:
            self.session.scan_rect = rect
            self._meta_dirty = True
            if not self.session.filters_touched:
                # v2.3 discarded border grains whenever a scan area was set;
                # that is now the default of the visible toggle.
                self.session.filters.exclude_border = rect is not None
                self.filters_changed.emit()
        else:
            im = self.session.image(uid)
            if im is not None:
                im.scan_rect = rect
                im.scan_source = "manual" if rect else ""
                self._meta_dirty = True
        self.calibration_changed.emit()
        self.setup_changed.emit()
        self.schedule_save()

    def set_params(self, params: DetectionParams) -> None:
        if self.session is None:
            return
        d = params_to_dict(params)
        if d != self.session.params:
            self.session.params = d
            self._meta_dirty = True
            self.schedule_save()

    def set_image_status(self, uid, status: str, progress: int = 0, message: str = "") -> None:
        im = self.session.image(uid) if self.session else None
        if im is None:
            return
        im.status, im.progress, im.message = status, progress, message
        self.image_updated.emit(uid)

    def set_result(self, uid, raw: AnalysisResult) -> None:
        """A fresh detector result (raw).  Filters are applied off-thread;
        the image turns 'done' once its filtered result is ready."""
        im = self.session.image(uid) if self.session else None
        if im is None:
            return
        im.raw = raw
        im.manual = set()
        im.edits, im.detector_labels = [], None     # a new detection: hand edits start over
        im.excluded, im.counts = {}, {}
        im.status, im.progress, im.message = "running", 99, "Applying grain filters"
        self.image_updated.emit(uid)
        self.refilter(uid, first=True)

    # ------------------------------------------------------------------ filters
    def filter_options(self, uid=None) -> PostFilterOptions:
        if self.session is None:
            return PostFilterOptions()
        im = self.session.image(uid) if uid is not None else None
        if im is not None and im.filter_override is not None:
            return im.filter_override
        return self.session.filters

    def has_override(self, uid) -> bool:
        im = self.session.image(uid) if (self.session and uid is not None) else None
        return im is not None and im.filter_override is not None

    def set_filter_options(self, opts: PostFilterOptions, uid=None) -> None:
        """``uid=None``: change the session filters (all images without an
        override).  Otherwise: a per-image override."""
        if self.session is None:
            return
        opts = options_from_dict(options_to_dict(opts))
        if uid is None:
            self.session.filters = opts
            self.session.filters_touched = True
            targets = [im for im in self.session.images if im.filter_override is None]
        else:
            im = self.session.image(uid)
            if im is None:
                return
            im.filter_override = opts
            targets = [im]
        self._meta_dirty = True
        self.filters_changed.emit()
        for im in targets:
            if im.raw is not None:
                self.refilter(im.uid, preview=True)
        self.schedule_save()

    def apply_filters_to_all(self, opts: Optional[PostFilterOptions] = None) -> None:
        if self.session is None:
            return
        for im in self.session.images:
            im.filter_override = None
        self.set_filter_options(opts or self.session.filters, None)

    def is_filtering(self, uid=None) -> bool:
        return bool(self._filtering) if uid is None else uid in self._filtering

    def refilter(self, uid, first: bool = False, preview: bool = False) -> None:
        """Recompute the filtered result for one image off the GUI thread.

        ``preview=True`` (interactive toggles, manual removal) evaluates ASTM
        planimetrically only and schedules the full-method pass ~1 s after the
        last change.  Stale outcomes are dropped by a per-image generation
        counter."""
        doc = self.session
        im = doc.image(uid) if doc else None
        if im is None or im.raw is None:
            return
        im.filter_gen += 1
        gen = im.filter_gen
        opts = self.filter_options(uid)
        params = params_from_dict(doc.params)
        if preview:
            params = preview_params(params)
            self._final_pending.add(uid)
            self._final_timer.start()
        else:
            self._final_pending.discard(uid)
        if uid not in self._filtering:
            self._filtering.add(uid)
            self.filtering_changed.emit(uid, True)

        def done(out):
            if self.session is not doc or im.filter_gen != gen:
                return
            self._filtering.discard(uid)
            self.filtering_changed.emit(uid, False)
            im.result = out["result"]
            im.excluded = out["excluded"]
            im.counts = out["counts"]
            if first or im.status != "done":
                im.status, im.progress, im.message = "done", 100, ""
            if im.result is not None and im.result.overlay_image is not None:
                im.thumb = thumb_qimage(im.result.overlay_image)
            self._dirty.add(uid)
            self._meta_dirty = True
            self.result_edited.emit(uid)
            self.image_updated.emit(uid)
            self.schedule_save()

        def failed(msg):
            if im.filter_gen == gen:
                self._filtering.discard(uid)
                self.filtering_changed.emit(uid, False)
                if im.result is None:
                    im.result = im.raw
                    im.status = "done"
                    self.image_updated.emit(uid)
            self.message.emit("Grain filters failed", msg.splitlines()[0], "danger")

        run_task(filter_image, im.raw, im.image_bgr, opts, frozenset(im.manual), params,
                 on_done=done, on_error=failed)

    def _run_final_filters(self) -> None:
        for uid in list(self._final_pending):
            self.refilter(uid)

    # ------------------------------------------------------------------ editing
    def delete_grains(self, uid, grain_ids: List[int]) -> bool:
        """Remove grains by hand (undoable; feeds ``manual_excluded``)."""
        if not grain_ids or self.session is None:
            return False
        cmd = ExcludeGrainsCommand(self, uid, list(grain_ids))
        if cmd.is_empty():
            return False
        self.undo_stack.push(cmd)  # calls redo()
        return True

    def restore_grains(self, uid, grain_ids: List[int]) -> bool:
        """Put hand-removed grains back (undoable)."""
        if not grain_ids or self.session is None:
            return False
        cmd = RestoreGrainsCommand(self, uid, list(grain_ids))
        if cmd.is_empty():
            return False
        self.undo_stack.push(cmd)
        return True

    # -- UI-05 / INN-04: merge / split (label-image edits) ---------------
    def _edit_target(self, uid):
        from core.grain_edit import GrainEditError, label_offset
        im = self.session.image(uid) if (self.session and uid is not None) else None
        if im is None or im.raw is None or im.raw.label_image is None:
            raise GrainEditError("Analyse this image before editing its grains.")
        lab = im.raw.label_image
        shape = im.image_bgr.shape[:2] if im.image_bgr is not None else lab.shape[:2]
        off = label_offset(lab.shape, shape, getattr(im.raw, "auto_crop_rect", None))
        return im, off

    def kept_ids(self, uid) -> set:
        """Grains currently counted (not excluded by a filter or by hand)."""
        im = self.session.image(uid) if (self.session and uid is not None) else None
        if im is None or im.raw is None:
            return set()
        return {int(g.grain_id) for g in im.raw.grains} - {int(k) for k in im.excluded}

    def merge_grains(self, uid, grain_ids: List[int]) -> int:
        """Merge 2+ touching kept grains into one (undoable).  Returns the
        merged grain's id; raises ``core.grain_edit.GrainEditError`` with a
        user-facing message when the grains cannot be merged."""
        from core.grain_edit import GrainEditError, merge_grains, remeasure_after_edit
        im, _off = self._edit_target(uid)
        kept = self.kept_ids(uid)
        ids = sorted({int(i) for i in grain_ids if int(i) in kept})
        if len(ids) < 2:
            raise GrainEditError("Select at least two grains to merge.")
        out = merge_grains(im.raw.label_image, ids, valid_mask=im.raw.valid_mask)
        raw = remeasure_after_edit(im.raw, out, self._frame_shape(im))
        self.undo_stack.push(GrainGeometryCommand(self, uid, raw, out,
                                                  f"Merge {len(ids)} grains"))
        return int(out.op["into"])

    def split_grain(self, uid, line_xy, grain_id: Optional[int] = None) -> List[int]:
        """Split one kept grain along a cut line given in IMAGE (canvas)
        coordinates (undoable).  Returns the ids of the pieces."""
        from core.grain_edit import remeasure_after_edit, split_grain, to_label_coords
        im, off = self._edit_target(uid)
        line = to_label_coords(line_xy, off)
        out = split_grain(im.raw.label_image, line, grain_id, candidates=self.kept_ids(uid))
        raw = remeasure_after_edit(im.raw, out, self._frame_shape(im))
        self.undo_stack.push(GrainGeometryCommand(self, uid, raw, out,
                                                  f"Split grain #{out.op['id']}"))
        return [int(i) for i in out.changed]

    @staticmethod
    def _frame_shape(im: ImageDoc):
        return im.image_bgr.shape[:2] if im.image_bgr is not None else None

    def _geometry_changed(self, doc: ImageDoc) -> None:
        # Not marked dirty here: the refilter pass below saves labels, grains
        # and the edit list together, so the files on disk never disagree.
        doc.counts = _counts_from(doc.excluded)
        self.result_edited.emit(doc.uid)   # canvas shows the new outlines at once
        self.refilter(doc.uid, preview=True)

    def _manual_changed(self, doc: ImageDoc) -> None:
        doc.counts = _counts_from(doc.excluded)
        self.result_edited.emit(doc.uid)   # optimistic: canvas greys them at once
        self.refilter(doc.uid, preview=True)

    # ------------------------------------------------------------------ saving
    def schedule_save(self) -> None:
        if self.session is None:
            return
        self._set_save_state("unsaved", "")
        self._save_timer.start()

    def is_dirty(self) -> bool:
        return bool(self._dirty) or self._meta_dirty

    @staticmethod
    def _image_fields(im: ImageDoc) -> dict:
        """Per-image overrides as stored in the manifest (DATA-09): raw
        overrides only, ``CLEAR`` when the image follows the session."""
        return dict(scan_rect=tuple(im.scan_rect) if im.scan_rect else CLEAR,
                    px_per_um=float(im.px_override) if im.px_override > 0 else CLEAR,
                    filters_override=(options_to_dict(im.filter_override)
                                      if im.filter_override is not None else CLEAR),
                    manual_excluded=sorted(int(i) for i in im.manual),
                    grain_edits=[dict(op) for op in im.edits] if im.edits else CLEAR)

    def save_now(self) -> None:
        """Flush pending changes to disk off-thread (Ctrl+S / autosave)."""
        self._save_timer.stop()
        doc = self.session
        if doc is None or not self.is_dirty():
            if doc is not None and self.save_state == "unsaved":
                self._set_save_state("saved", self.last_saved)
            return
        if self._saving:
            self._save_again = True
            return
        if self._records_pending:
            # UX-09: a record is written only once its images are all in
            # memory (else its session-level values could be overwritten)
            self._save_timer.start()
            return
        with_result = set(self._dirty)
        jobs = []
        for rec in doc.records:
            entries, meta = self._record_payload(doc, rec, with_result)
            if entries or meta:
                jobs.append((rec.path, entries, meta))
            if "px_per_um" in meta:        # the record now follows the shared values
                rec.px_per_um, rec.scan_rect = doc.px_per_um, doc.scan_rect
                rec.filters = options_to_dict(doc.filters)
        if doc.acquisition_dirty:
            doc.acquisition_dirty = False
        self._dirty.clear()
        self._meta_dirty = False
        self._saving = True
        self._set_save_state("saving", "")

        def done(stamp):
            self._saving = False
            self.last_saved = stamp
            if self._save_again or self.is_dirty():
                self._save_again = False
                self.save_now()
            else:
                self._set_save_state("saved", stamp)

        def failed(msg):
            self._saving = False
            self._set_save_state("error", msg.splitlines()[0])
            self.message.emit("Autosave failed", msg.splitlines()[0], "danger")

        run_task(_persist_many, self.root, jobs,
                 on_done=done, on_error=failed, pool=serial_pool())

    def _record_payload(self, doc: SessionDoc, rec: RecordRef, with_result: set):
        """(entries, meta_updates) to write into one record's manifest."""
        entries = []
        mine = [im for im in doc.images + doc.removed if doc.record_for(im) is rec]
        rec_result = any(im.uid in with_result for im in mine)
        all_fields = self._meta_dirty or rec_result
        for im in mine:
            if im.loading:
                continue
            if im.uid in with_result and im.result is not None:
                snap = snapshot_result(im.result)
                # The saved label image keeps EVERY raw grain so filters can be
                # switched off again after reload; grains.json / summary.json /
                # overlay.png hold the filtered (reported) result.
                if im.raw is not None and im.raw.label_image is not None:
                    snap.label_image = im.raw.label_image.copy()
                base = (im.detector_labels.copy()
                        if im.edits and im.detector_labels is not None else None)
                entries.append(ImageEntry(filename=im.filename, image_bgr=im.image_bgr,
                                          result=snap, detector_label_image=base,
                                          **self._image_fields(im)))
            elif all_fields:
                # manifest-only update: overrides, filter override, manual ids
                entries.append(ImageEntry(filename=im.filename, **self._image_fields(im)))
        meta = {}
        if self._meta_dirty or rec_result:
            meta = dict(px_per_um=doc.px_per_um,
                        scan_rect=list(doc.scan_rect) if doc.scan_rect else CLEAR,
                        detection_params=dict(doc.params),
                        filters=options_to_dict(doc.filters),
                        detector_mode=doc.params.get("detection_mode", ""))
            from version import __version__
            meta["software_version"] = __version__
        if doc.acquisition_dirty and doc.records and rec is doc.records[0]:
            m = doc.meta
            meta.update(instrument=m.instrument, magnification=m.magnification,
                        accelerating_voltage_kv=m.accelerating_voltage_kv,
                        working_distance_mm=m.working_distance_mm,
                        # INN-29 / FIX-08 (CLEAR resets a stale id to null)
                        calibration_check_id=m.calibration_check_id or CLEAR,
                        calibration_status=m.calibration_status,
                        calibration_reason=m.calibration_reason)
        return entries, meta

    def flush(self, timeout_ms: int = 15000) -> None:
        """Synchronously wait for pending saves (close / session switch)."""
        self.about_to_flush.emit()
        if self._records_pending:
            from PySide6.QtCore import QThreadPool, QCoreApplication
            QThreadPool.globalInstance().waitForDone(timeout_ms)
            QCoreApplication.processEvents()
        if self._final_pending:
            self._final_timer.stop()
            self._run_final_filters()
            from PySide6.QtCore import QThreadPool, QCoreApplication
            QThreadPool.globalInstance().waitForDone(timeout_ms)
            QCoreApplication.processEvents()
        if self.session is not None and self.is_dirty():
            self.save_now()
        serial_pool().waitForDone(timeout_ms)
        from PySide6.QtCore import QCoreApplication
        QCoreApplication.processEvents()

    def _set_save_state(self, state: str, detail: str) -> None:
        self.save_state = state
        self.save_state_changed.emit(state, detail)


__all__ = [
    "AppState", "ImageDoc", "SessionDoc", "RecordRef", "NodeRef", "ExcludeGrainsCommand",
    "setup_probe", "records_under",
    "RestoreGrainsCommand",
    "DeleteGrainsCommand", "node_for_path", "node_chain", "node_display_name",
    "session_title", "load_ui_state", "save_ui_state", "ui_state_path",
    "params_to_dict", "params_from_dict", "KIND_ORDER",
]
