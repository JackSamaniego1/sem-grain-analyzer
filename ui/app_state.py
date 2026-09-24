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
from data.session_io import load_session, update_session
from data.workspace import Workspace
from ui.filtering import (
    PostFilterOptions, default_options, filter_image, options_from_dict, options_to_dict,
    preview_params, remeasure_excluded,
)
from ui.canvas.layers import kept_labels
from ui.workers import read_image, run_task, serial_pool, snapshot_result, thumb_qimage

_uid_counter = itertools.count(1)

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


def node_display_name(node: NodeRef) -> str:
    """Human label from the node's metadata file (cheap: one small json)."""
    if node.kind == "workspace":
        return "Workspace"
    mf = META_FILES.get(node.kind)
    try:
        d = read_json(node.path / mf) if mf else {}
    except Exception:
        d = {}
    if node.kind == "project":
        return d.get("name") or node.name
    if node.kind == "sample":
        return d.get("sample_id") or node.name
    if node.kind == "lot":
        n = d.get("lot_number") or node.name
        return f"Lot {n}"
    if node.kind == "session":
        return d.get("label") or session_title(d.get("created_local", ""), node.name)
    return node.name


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
    raw: Optional[AnalysisResult] = None       # detector output (never edited)
    result: Optional[AnalysisResult] = None    # filtered result: stats, export, reports
    excluded: Dict[int, List[str]] = field(default_factory=dict)   # grain id -> reasons
    counts: Dict[str, int] = field(default_factory=dict)           # reason -> n
    manual: set = field(default_factory=set)                       # hand-removed grain ids
    filter_override: Optional[PostFilterOptions] = None            # None -> session filters
    filter_gen: int = 0
    scan_rect: Optional[tuple] = None     # per-image override (None -> session)
    px_override: float = 0.0              # per-image calibration (0 -> session)
    status: str = "pending"               # pending|queued|running|done|error
    progress: int = 0
    message: str = ""
    uid: int = field(default_factory=lambda: next(_uid_counter))

    @property
    def name(self) -> str:
        return self.filename


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
    project_meta: dict = field(default_factory=dict)
    sample_meta: dict = field(default_factory=dict)
    lot_meta: dict = field(default_factory=dict)

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
    def title(self) -> str:
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
    return dict(filename=si.filename, path=si.path, bgr=bgr, result=res, raw=raw,
                excluded=excluded, manual=manual, override=override,
                thumb=thumb_qimage(bgr), scan_rect=entry.scan_rect,
                px=float(entry.px_per_um or 0.0), notes=entry.notes)


def _load_session_bundle(path: Path) -> dict:
    """Worker-thread: read manifest, every image, every saved result."""
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
    """Worker-thread autosave (DATA-08)."""
    update_session(session_path, images=entries or None,
                   meta_updates=meta_updates or None, catalog=Catalog(root))
    return datetime.now().strftime("%H:%M")


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
        self.workspace_changed.emit()

    def operator(self) -> str:
        from data.models import default_operator
        return self.settings.operator or default_operator()

    def default_params(self) -> DetectionParams:
        return params_from_dict(self.ui_state.get("default_params"))

    # ------------------------------------------------------------------ nodes
    def set_node(self, node: Optional[NodeRef]) -> None:
        self.current_node = node
        self.node_changed.emit(node)

    def chain(self) -> List[NodeRef]:
        return node_chain(self.root, self.current_node)

    # ------------------------------------------------------------------ session
    def open_session(self, path, on_done=None) -> None:
        path = Path(path)
        if self.session is not None and self.session.path == path:
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
        for d in bundle["images"]:
            doc.images.append(self._make_image(doc, d))
        self.session = doc
        self.undo_stack.clear()
        self._dirty.clear()
        self._meta_dirty = False
        self.current_uid = doc.images[0].uid if doc.images else None
        self.settings = data_settings.add_recent_session(
            self.settings, str(path), self.settings_path, save=False)
        self.save_settings()
        self.current_node = NodeRef("session", path)
        self._set_save_state("saved", "")
        self.node_changed.emit(self.current_node)
        self.session_opened.emit()
        self.images_changed.emit()
        self.current_image_changed.emit(self.current_uid)
        self.calibration_changed.emit()
        self.filters_changed.emit()

    def _make_image(self, doc: SessionDoc, d: dict) -> ImageDoc:
        res = d.get("result")
        px = float(d.get("px") or 0.0)
        override = px if (px > 0 and doc.px_per_um > 0 and abs(px - doc.px_per_um) > 1e-9) else 0.0
        if px > 0 and doc.px_per_um <= 0:
            override = px
        excluded = d.get("excluded") or {}
        im = ImageDoc(filename=d["filename"], path=d.get("path"), image_bgr=d.get("bgr"),
                      thumb=d.get("thumb"), result=res, raw=d.get("raw") or res,
                      excluded=excluded, counts=_counts_from(excluded),
                      manual=set(d.get("manual") or []),
                      filter_override=options_from_dict(d["override"]) if d.get("override") else None,
                      scan_rect=tuple(d["scan_rect"]) if d.get("scan_rect") else None,
                      px_override=override,
                      status="done" if res is not None else "pending")
        if im.image_bgr is None:
            im.status, im.message = "error", "Image file is missing or unreadable"
        if im.scan_rect is not None and im.scan_rect == doc.scan_rect:
            im.scan_rect = None
        return im

    def close_session(self) -> None:
        if self.session is None:
            return
        self.flush()
        self.session = None
        self.current_uid = None
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
        known = {im.filename for im in doc.images}
        entries = [ImageEntry(source_path=str(p)) for p in paths]
        root = self.root

        def work():
            update_session(doc.path, images=entries, catalog=Catalog(root))
            return _load_new_images(doc.path, known)

        def done(new):
            if self.session is not doc:
                return
            for d in new:
                doc.images.append(self._make_image(doc, d))
            if self.current_uid is None and doc.images:
                self.current_uid = doc.images[0].uid
                self.current_image_changed.emit(self.current_uid)
            self.images_changed.emit()
            self.message.emit("Images added", f"{len(new)} image(s) copied into the session.",
                              "success")
            if on_done:
                on_done(len(new))

        run_task(work, on_done=done, pool=serial_pool(),
                 on_error=lambda m: self.message.emit("Could not add images",
                                                      m.splitlines()[0], "danger"))

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
            self._meta_dirty = True        # per-image fields travel with every save
        self.calibration_changed.emit()
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
        self._meta_dirty = True
        self.calibration_changed.emit()
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
                self._meta_dirty = True
        self.calibration_changed.emit()
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
                    manual_excluded=sorted(int(i) for i in im.manual))

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
        entries = []
        with_result = set(self._dirty)
        all_fields = self._meta_dirty or bool(with_result)
        for im in doc.images:
            if im.uid in with_result and im.result is not None:
                snap = snapshot_result(im.result)
                # The saved label image keeps EVERY raw grain so filters can be
                # switched off again after reload; grains.json / summary.json /
                # overlay.png hold the filtered (reported) result.
                if im.raw is not None and im.raw.label_image is not None:
                    snap.label_image = im.raw.label_image.copy()
                entries.append(ImageEntry(filename=im.filename, image_bgr=im.image_bgr,
                                          result=snap, **self._image_fields(im)))
            elif all_fields:
                # manifest-only update: overrides, filter override, manual ids
                entries.append(ImageEntry(filename=im.filename, **self._image_fields(im)))
        meta = {}
        if self._meta_dirty or with_result:
            meta = dict(px_per_um=doc.px_per_um,
                        scan_rect=list(doc.scan_rect) if doc.scan_rect else CLEAR,
                        detection_params=dict(doc.params),
                        filters=options_to_dict(doc.filters),
                        detector_mode=doc.params.get("detection_mode", ""))
            from version import __version__
            meta["software_version"] = __version__
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

        run_task(_persist, doc.path, self.root, entries, meta,
                 on_done=done, on_error=failed, pool=serial_pool())

    def flush(self, timeout_ms: int = 15000) -> None:
        """Synchronously wait for pending saves (close / session switch)."""
        self.about_to_flush.emit()
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
    "AppState", "ImageDoc", "SessionDoc", "NodeRef", "ExcludeGrainsCommand",
    "RestoreGrainsCommand",
    "DeleteGrainsCommand", "node_for_path", "node_chain", "node_display_name",
    "session_title", "load_ui_state", "save_ui_state", "ui_state_path",
    "params_to_dict", "params_from_dict", "KIND_ORDER",
]
