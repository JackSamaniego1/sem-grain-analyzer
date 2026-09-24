"""
Projects page (DATA-06, HIER-01) — Keyence-style data browser.

Layout
  left   : workspace tree in the workspace's own hierarchy (e.g. Job # › Part
           Number › Lot, or Project › Sample › Lot) (+ New / Rename / Edit / Trash)
  centre : header for the selected node, filter/sort bar, card grid of its
           children (with thumbnails for lots / sessions)
  right  : details panel (editable metadata for the selected node — the
           level's own fields from the hierarchy profile, typed editors)
States     : first-run empty state, loading skeletons, empty folder, populated.
With images stored in the lot (profile ``images_location == "lot"``) there is
no Session level: a lot opens directly and adding images appends to it.
All folder scans and thumbnail loads run on the thread pool.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QModelIndex, QSize, Qt, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QMenu,
    QSplitter, QTreeView, QVBoxLayout, QWidget,
)

from data.catalog import Catalog
from data.hierarchy import FieldDef
from data.models import read_json
from data.session_io import import_loose_images
from data.workspace import Workspace
from ui import hierarchy_ui as hui
from ui.app_state import NodeRef, node_display_name, node_for_path
from ui.design import icons
from ui.design.tokens import SPACE
from ui.format import fmt_date_utc, fmt_int, fmt_opt, smart_format
from ui.pages.common import (
    CardGrid, ConfirmBar, MetricCard, PageHeader, Panel, SelectableCard, ThumbStrip, scroll,
)
from ui.widgets import (
    AnimatedButton, Badge, Card, Divider, EmptyState, FadeStackedWidget, IconButton,
    KeyValueList, SearchBox, Skeleton, label,
)
from ui.widgets.field_editors import editor_value, make_editor, mark_invalid
from ui.workers import IMAGE_EXTS, load_thumb_file, run_task

KIND_ROLE = Qt.UserRole + 1
PATH_ROLE = Qt.UserRole + 2

_KIND_ICON = hui.KIND_ICON


def _id_field(profile, kind: str) -> FieldDef:
    return FieldDef(hui.ID_KEYS.get(kind, "label"), hui.id_label(profile, kind), required=True)


def form_fields(profile, kind: str) -> List[FieldDef]:
    """Identifier + the level's metadata fields (sessions: label, operator …)."""
    if kind == "session":
        return hui.level_fields(profile, "session")
    return [_id_field(profile, kind)] + hui.level_fields(profile, kind)


# ======================================================================
# worker-side scanning (no widgets!)
# ======================================================================

def _safe_json(p: Path) -> dict:
    try:
        return read_json(p) if p.exists() else {}
    except Exception:
        return {}


def _session_dirs(lot: Path) -> List[Path]:
    """Timestamped run folders of a lot (never the lot's own images/, results/ …)."""
    if not lot.exists():
        return []
    return sorted([d for d in lot.iterdir() if d.is_dir() and d.name not in hui_reserved()
                   and (d / "manifest.json").exists()], reverse=True)


def hui_reserved():
    from data.hierarchy import RESERVED_LOT_SUBDIRS
    return RESERVED_LOT_SUBDIRS


def _lot_record(lot: Path) -> bool:
    return (lot / "manifest.json").exists()


def _lot_image_count(lot: Path) -> int:
    n = len(_safe_json(lot / "manifest.json").get("images", []) or []) if _lot_record(lot) else 0
    for sd in _session_dirs(lot):
        n += len(_safe_json(sd / "manifest.json").get("images", []) or [])
    return n


def scan_tree(root: str) -> List[dict]:
    ws = Workspace(root)
    lot_mode = hui.lot_mode(ws.profile)
    out = []
    for pm in ws.list_projects():
        pp = Path(pm.path)
        pd = {"kind": "project", "path": pp, "meta": _safe_json(pp / "project.json"),
              "children": []}
        for sm in ws.list_samples(pp):
            sp = Path(sm.path)
            sd = {"kind": "sample", "path": sp, "meta": _safe_json(sp / "sample.json"),
                  "children": []}
            for lm in ws.list_lots(pp, sp):
                lp = Path(lm.path)
                sd["children"].append({"kind": "lot", "path": lp,
                                       "meta": _safe_json(lp / "lot.json"),
                                       "n_sessions": len(_session_dirs(lp)),
                                       "n_images": _lot_image_count(lp) if lot_mode else 0,
                                       "children": []})
            pd["children"].append(sd)
        out.append(pd)
    return out


def session_summary(sdir: Path, n_thumbs: int = 3) -> dict:
    m = _safe_json(sdir / "manifest.json")
    imgs = m.get("images", []) or []
    analysed = [i for i in imgs if i.get("has_result")]
    diams, gs, cal = [], [], False
    for i in analysed:
        s = _safe_json(sdir / "results" / f"{Path(i.get('filename', '')).stem}.summary.json")
        if s.get("has_calibration"):
            cal = True
            if s.get("mean_diameter_um"):
                diams.append(float(s["mean_diameter_um"]))
        if s.get("astm_g") is not None:
            try:
                gs.append(float(s["astm_g"]))
            except (TypeError, ValueError):
                pass
    thumbs = []
    for i in imgs[:n_thumbs]:
        tp = sdir / "thumbs" / f"{Path(i.get('filename', '')).stem}.jpg"
        thumbs.append(load_thumb_file(tp))
    if not imgs:
        status = ("Empty", "neutral")
    elif len(analysed) == len(imgs):
        status = ("Analysed", "success")
    elif analysed:
        status = ("Partly analysed", "warning")
    else:
        status = ("Not analysed", "neutral")
    return {"kind": "session", "path": sdir, "meta": m, "n_images": len(imgs),
            "n_analysed": len(analysed),
            "grains": int(sum(int(i.get("grain_count", 0)) for i in analysed)),
            "mean_diam_um": (sum(diams) / len(diams)) if diams else None,
            "g": (sum(gs) / len(gs)) if gs else None, "calibrated": cal,
            "thumbs": thumbs, "status": status, "record": (sdir / "lot.json").exists(),
            "created": m.get("created_utc", ""), "operator": m.get("operator", "")}


def load_contents(kind: str, path: str, root: str) -> dict:
    """Children of ``path`` with light summaries (runs on the thread pool)."""
    ws = Workspace(root)
    lot_mode = hui.lot_mode(ws.profile)
    p = Path(path)
    items: List[dict] = []

    def lot_counts(lp: Path):
        n = len(_session_dirs(lp)) + (1 if lot_mode and _lot_record(lp) else 0)
        return n, (_lot_image_count(lp) if lot_mode else None)

    if kind == "workspace":
        for pm in ws.list_projects():
            pp = Path(pm.path)
            samples = ws.list_samples(pp)
            lots = [Path(l.path) for s in samples for l in ws.list_lots(pp, Path(s.path))]
            n_sess = sum(lot_counts(l)[0] for l in lots)
            items.append({"kind": "project", "path": pp, "meta": _safe_json(pp / "project.json"),
                          "n_samples": len(samples), "n_lots": len(lots),
                          "n_sessions": n_sess, "created": pm.created_utc,
                          "n_images": sum(_lot_image_count(l) for l in lots) if lot_mode else 0})
    elif kind == "project":
        for sm in ws.list_samples(p):
            sp = Path(sm.path)
            lots = [Path(l.path) for l in ws.list_lots(p, sp)]
            n_sess = sum(lot_counts(l)[0] for l in lots)
            items.append({"kind": "sample", "path": sp, "meta": _safe_json(sp / "sample.json"),
                          "n_lots": len(lots), "n_sessions": n_sess, "created": sm.created_utc,
                          "n_images": sum(_lot_image_count(l) for l in lots) if lot_mode else 0})
    elif kind == "sample":
        for lm in ws.list_lots(p.parent, p):
            lp = Path(lm.path)
            sess = _session_dirs(lp)
            n_img, latest, thumbs, analysed = 0, "", [], 0
            recs = ([lp] if lot_mode and _lot_record(lp) else []) + sess
            for sd in recs:
                mm = _safe_json(sd / "manifest.json")
                ims = mm.get("images", []) or []
                n_img += len(ims)
                analysed += sum(1 for i in ims if i.get("has_result"))
                latest = max(latest, mm.get("created_utc", ""))
            if recs:
                thumbs = session_summary(recs[0])["thumbs"]
            items.append({"kind": "lot", "path": lp, "meta": _safe_json(lp / "lot.json"),
                          "n_sessions": len(sess), "n_images": n_img, "n_analysed": analysed,
                          "created": latest or lm.created_utc, "thumbs": thumbs,
                          "lot_mode": lot_mode})
    elif kind == "lot":
        if lot_mode and _lot_record(p):
            items.append(session_summary(p))
        for sd in _session_dirs(p):
            items.append(session_summary(sd))
    meta_file = hui.META_FILES.get(kind)
    meta = _safe_json(p / meta_file) if meta_file else {}
    acq = _safe_json(p / "manifest.json") if (kind == "lot" and lot_mode) else {}
    return {"kind": kind, "path": p, "items": items, "meta": meta, "acquisition": acq}


def trash_node(ws: Workspace, root: Path, kind: str, path: Path) -> Path:
    """Move a session / lot / sample / project into ``<root>/.trash`` through
    the data layer (containment-checked, catalog kept in sync)."""
    cat = Catalog(root)
    path = Path(path)
    if kind == "session":
        dest = ws.delete_session(path)
        cat.remove(path)
        return dest
    if kind == "project":
        return ws.trash_project(path, catalog=cat)
    if kind == "sample":
        return ws.trash_sample(path.parent, path, catalog=cat)
    if kind == "lot":
        return ws.trash_lot(path.parent.parent, path.parent, path, catalog=cat)
    raise ValueError(f"Cannot move a {kind} to the trash")


def _reconnect(signal, slot) -> None:
    try:
        signal.disconnect()
    except (RuntimeError, TypeError):
        pass
    signal.connect(slot)


# ======================================================================
# widgets
# ======================================================================

class NodeCard(SelectableCard):
    """Card for a project / sample / lot / session (labels from the profile)."""

    def __init__(self, item: dict, profile=None, parent=None) -> None:
        super().__init__(parent=parent)
        self.item = item
        self.profile = profile
        kind = item["kind"]
        body = self.body_layout()
        body.setSpacing(SPACE.sm)
        if kind in ("session", "lot"):
            self.thumbs = ThumbStrip(3, 84 if kind == "session" else 64)
            self.thumbs.set_images(item.get("thumbs", []),
                                   item.get("n_images", 0) if kind == "session" else 0)
            body.addWidget(self.thumbs)
        top = QHBoxLayout()
        top.setSpacing(SPACE.sm)
        ic = label()
        ic.setPixmap(icons.pixmap(_KIND_ICON[kind], 16))
        top.addWidget(ic, 0, Qt.AlignVCenter)
        self.title_lbl = label(self.title_text(), "h3")
        self.title_lbl.setWordWrap(False)
        top.addWidget(self.title_lbl, 1)
        if kind == "session" or (kind == "lot" and item.get("lot_mode") and item.get("n_images")):
            st, sk = item.get("status", ("", "neutral")) if kind == "session" else \
                self._lot_status()
            top.addWidget(Badge(st, sk, dot=True), 0, Qt.AlignVCenter)
        body.addLayout(top)
        sub = self.subtitle_text()
        if sub:
            s = label(sub, "caption")
            s.setWordWrap(True)
            body.addWidget(s)
        row = QHBoxLayout()
        row.setSpacing(SPACE.xs)
        for text, k, icn in self._badges():
            row.addWidget(Badge(text, k, icon=icn))
        row.addStretch(1)
        body.addLayout(row)
        metric = self._metric()
        if metric:
            body.addWidget(label(metric, "caption"))
        self.setToolTip(self._tooltip())
        self.setAccessibleName(f"{self.kind_label(kind)} {self.title_text()}")

    def kind_label(self, kind: str) -> str:
        if kind == "session" and self.item.get("record"):
            return hui.kind_label(self.profile, "lot")
        return hui.kind_label(self.profile, kind)

    def _lot_status(self):
        n, a = self.item.get("n_images", 0), self.item.get("n_analysed", 0)
        if a and a == n:
            return "Analysed", "success"
        if a:
            return "Partly analysed", "warning"
        return "Not analysed", "neutral"

    def title_text(self) -> str:
        it, m = self.item, self.item.get("meta", {})
        k = it["kind"]
        if k == "session" and it.get("record"):
            return "Images in this " + hui.kind_label(self.profile, "lot").lower()
        return hui.node_caption(self.profile, k, m, it["path"])

    def subtitle_text(self) -> str:
        it, m = self.item, self.item.get("meta", {})
        k = it["kind"]
        if k in hui.LEVELS:
            parts = []
            for fd in hui.level_fields(self.profile, k):
                v = m.get(fd.key)
                if v in (None, "") or fd.kind == "multiline":
                    continue
                parts.append(f"{fd.label.lower()} {v}" if fd.kind == "date" else str(v))
            return " · ".join(parts[:3])
        parts = [fmt_date_utc(m.get("created_utc", "")), m.get("operator", "")]
        return " · ".join(x for x in parts if x)

    def _badges(self):
        it, p = self.item, self.profile
        k = it["kind"]
        lot_mode = hui.lot_mode(p)
        runs = lambda n: (f"{n} session{'s' if n != 1 else ''}", "neutral", "images")  # noqa: E731
        imgs = lambda n: (f"{n} image{'s' if n != 1 else ''}", "neutral", "images")  # noqa: E731
        if k == "project":
            return [(hui.count_text(it.get("n_samples", 0), hui.kind_label(p, "sample")),
                     "neutral", "sample"),
                    imgs(it.get("n_images", 0)) if lot_mode else runs(it.get("n_sessions", 0))]
        if k == "sample":
            return [(hui.count_text(it.get("n_lots", 0), hui.kind_label(p, "lot")),
                     "neutral", "tag"),
                    imgs(it.get("n_images", 0)) if lot_mode else runs(it.get("n_sessions", 0))]
        if k == "lot":
            if lot_mode:
                b = [imgs(it.get("n_images", 0))]
                if it.get("n_sessions"):
                    b.append((f"{it['n_sessions']} earlier run"
                              f"{'s' if it['n_sessions'] != 1 else ''}", "neutral", "history"))
                return b
            return [(f"{it.get('n_sessions', 0)} sessions", "neutral", "history"),
                    imgs(it.get("n_images", 0))]
        b = [imgs(it.get("n_images", 0))]
        if it.get("n_analysed"):
            b.append((f"{fmt_int(it.get('grains', 0))} grains", "accent", "grains"))
        return b

    def _metric(self) -> str:
        it = self.item
        if it["kind"] == "session" and it.get("n_analysed"):
            parts = []
            if it.get("mean_diam_um"):
                parts.append(f"Mean ECD {smart_format(it['mean_diam_um'])} µm")
            if it.get("g") is not None:
                parts.append(f"ASTM G {fmt_opt(it['g'], 1)}")
            if not it.get("calibrated"):
                parts.append("uncalibrated (px units)")
            return "  ·  ".join(parts)
        if it["kind"] in ("project", "sample", "lot") and it.get("created"):
            return ("Last activity " if it["kind"] == "lot" else "Created ") + \
                fmt_date_utc(it["created"], with_time=False)
        return ""

    def _tooltip(self) -> str:
        k = self.item["kind"]
        if k == "session":
            if self.item.get("record"):
                return "Double-click (or Enter) to open the images and results of this " + \
                    hui.kind_label(self.profile, "lot").lower()
            return "Double-click (or Enter) to open this session in Analyze / Review"
        if k == "lot" and hui.lot_mode(self.profile):
            return (f"Double-click to open this {hui.kind_label(self.profile, 'lot')} — its "
                    "images and results are stored in the folder itself")
        return f"Double-click to open this {hui.kind_label(self.profile, k)}"


class SkeletonCard(Card):
    def __init__(self, parent=None) -> None:
        super().__init__(parent=parent)
        self.add_widget(Skeleton(height=78))
        self.add_widget(Skeleton(width=160, shape="text"))
        self.add_widget(Skeleton(width=110, shape="text"))


class NodeForm(Card):
    """Inline create form (no modal popups) — fields from the hierarchy profile."""

    submitted = Signal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__("New", parent=parent, elevation=2)
        self._form = QFormLayout()
        self._form.setHorizontalSpacing(SPACE.lg)
        self._form.setVerticalSpacing(SPACE.sm)
        self.body_layout().addLayout(self._form)
        self.error = label("", tone="danger")
        self.error.hide()
        self.body_layout().addWidget(self.error)
        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel = AnimatedButton("Cancel", None, "ghost")
        self.cancel.clicked.connect(self.hide)
        self.ok = AnimatedButton("Create", "add", "primary")
        self.ok.clicked.connect(self._submit)
        row.addWidget(self.cancel)
        row.addWidget(self.ok)
        self.body_layout().addLayout(row)
        self._edits: Dict[str, QWidget] = {}
        self._fields: List[FieldDef] = []
        self.kind = ""
        self.hide()

    def open_for(self, kind: str, where: str, profile=None) -> None:
        self.kind = kind
        name = hui.kind_label(profile, kind)
        self.set_title(f"New {name}", where)
        while self._form.rowCount():
            self._form.removeRow(0)
        self._edits = {}
        self._fields = form_fields(profile, kind)
        for fd in self._fields:
            w = make_editor(fd, "")
            self._edits[fd.key] = w
            self._form.addRow(fd.label + ("  *" if fd.required else ""), w)
        self.error.hide()
        self.ok.setText(f"Create {name}")
        self.show()
        first = self._edits[self._fields[0].key]
        first.setFocus()
        if hasattr(first, "returnPressed"):
            first.returnPressed.connect(self._submit)

    def values(self) -> dict:
        return {k: editor_value(w) for k, w in self._edits.items()}

    def set_value(self, key: str, value: str) -> None:
        from ui.widgets.field_editors import set_editor_value
        w = self._edits.get(key)
        if w is not None:
            set_editor_value(w, value)

    def _submit(self) -> None:
        v = self.values()
        for fd in self._fields:
            w = self._edits[fd.key]
            missing = fd.required and not v.get(fd.key)
            mark_invalid(w, missing)
            if missing:
                self.error.setText(f"{fd.label} is required.")
                self.error.show()
                w.setFocus()
                return
        self.submitted.emit(v)


class DetailsPanel(Panel):
    """Right-hand metadata panel with in-place editing (typed, profile fields)."""

    open_session = Signal(object)
    saved = Signal(object, dict)   # NodeRef, values

    def __init__(self, parent=None) -> None:
        super().__init__("left", parent)
        self.setMinimumWidth(270)
        self.setMaximumWidth(360)
        self.profile = None
        v = QVBoxLayout(self)
        v.setContentsMargins(SPACE.lg, SPACE.lg, SPACE.lg, SPACE.lg)
        v.setSpacing(SPACE.md)
        head = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(2)
        self.kind_lbl = label("DETAILS", "overline")
        self.title = label("Nothing selected", "h2")
        self.title.setWordWrap(True)
        col.addWidget(self.kind_lbl)
        col.addWidget(self.title)
        head.addLayout(col, 1)
        self.edit_btn = IconButton("edit", "Edit metadata")
        self.edit_btn.clicked.connect(self.start_edit)
        head.addWidget(self.edit_btn, 0, Qt.AlignTop)
        v.addLayout(head)
        v.addWidget(Divider())
        self.kv = KeyValueList()
        v.addWidget(self.kv)
        self.form_host = QWidget()
        self.form = QFormLayout(self.form_host)
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.setVerticalSpacing(SPACE.sm)
        self.form_host.hide()
        v.addWidget(self.form_host)
        self.form_error = label("", tone="danger")
        self.form_error.setWordWrap(True)
        self.form_error.hide()
        v.addWidget(self.form_error)
        row = QHBoxLayout()
        self.cancel_btn = AnimatedButton("Cancel", None, "ghost")
        self.save_btn = AnimatedButton("Save", "save", "primary")
        self.cancel_btn.clicked.connect(self.stop_edit)
        self.save_btn.clicked.connect(self._save)
        row.addStretch(1)
        row.addWidget(self.cancel_btn)
        row.addWidget(self.save_btn)
        self.edit_row = QWidget()
        self.edit_row.setLayout(row)
        self.edit_row.hide()
        v.addWidget(self.edit_row)
        self.open_btn = AnimatedButton("Open session", "open", "primary")
        self.open_btn.setToolTip("Load all images and results of this session (Enter)")
        self.open_btn.clicked.connect(lambda: self.node and self.open_session.emit(self.node.path))
        self.open_btn.hide()
        v.addWidget(self.open_btn)
        v.addStretch(1)
        self.path_lbl = label("", "caption")
        self.path_lbl.setWordWrap(True)
        self.path_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.path_lbl)
        self.node: Optional[NodeRef] = None
        self._root = ""
        self.meta: dict = {}
        self._edits: Dict[str, QWidget] = {}
        self._fields: List[FieldDef] = []

    def _editable(self, node: Optional[NodeRef]) -> bool:
        return node is not None and node.kind in ("project", "sample", "lot", "session")

    def _opens(self, node: NodeRef) -> bool:
        return node.kind == "session" or (node.kind == "lot" and hui.lot_mode(self.profile))

    def show_node(self, node: Optional[NodeRef], meta: dict, extra: Optional[dict] = None,
                  acquisition: Optional[dict] = None) -> None:
        self.stop_edit()
        self.node, self.meta = node, dict(meta or {})
        if node is None:
            self.title.setText("Nothing selected")
            self.kv.set_items([])
            self.edit_btn.hide()
            self.open_btn.hide()
            self.path_lbl.setText("")
            return
        p = self.profile
        self.kind_lbl.setText(hui.kind_label(p, node.kind).upper())
        self.title.setText(node_display_name(node, p) if node.kind != "workspace" else "Workspace")
        rows = []
        for fd in form_fields(p, node.kind):
            val = self.meta.get(fd.key, "")
            rows.append((fd.label + (" *" if fd.required and node.kind != "session" else ""),
                         str(val) if val not in (None, "") else "—"))
        acq = self.meta if node.kind == "session" else (acquisition or None)
        if acq:
            rows += self._acquisition_rows(acq, with_basics=node.kind != "session")
        elif node.kind not in ("workspace", "session"):
            rows.append(("Created", fmt_date_utc(self.meta.get("created_utc", ""))))
        for k, v in (extra or {}).items():
            rows.append((k, v))
        self.kv.set_items(rows)
        self.edit_btn.setVisible(self._editable(node))
        self.edit_btn.setToolTip(f"Edit the {hui.kind_label(p, node.kind)} metadata")
        opens = self._opens(node)
        self.open_btn.setVisible(opens)
        if opens:
            what = hui.kind_label(p, "lot") if node.kind == "lot" else "session"
            self.open_btn.setText(f"Open {what}")
            self.open_btn.setToolTip(f"Load all images and results of this {what} (Enter)")
        try:
            rel = node.path.relative_to(Path(self._root)) if self._root else node.path
        except ValueError:
            rel = node.path
        self.path_lbl.setText("Folder: " + (str(rel) if str(rel) != "." else str(node.path)))
        self.path_lbl.setToolTip(str(node.path))

    @staticmethod
    def _acquisition_rows(m: dict, with_basics: bool = True) -> list:
        """Acquisition details (INN-05 fills instrument / kV / WD / mag from
        the SEM metadata).  A session's operator / instrument / mag are
        already form rows, so only the rest is added for sessions."""
        kv = m.get("accelerating_voltage_kv")
        wd = m.get("working_distance_mm")
        rows = [("Created", fmt_date_utc(m.get("created_utc", "")))]
        if with_basics:
            rows += [("Operator", m.get("operator") or "—"),
                     ("Instrument", m.get("instrument") or "—"),
                     ("Magnification", m.get("magnification") or "—")]
        rows += [("Accelerating voltage", f"{kv:g} kV" if kv else "—"),
                 ("Working distance", f"{wd:g} mm" if wd else "—"),
                 ("Calibration", f"{m['px_per_um']:.4g} px/µm" if m.get("px_per_um") else "Not set"),
                 ("Detection mode", m.get("detector_mode") or "—")]
        return rows

    def start_edit(self) -> None:
        if not self._editable(self.node):
            return
        while self.form.rowCount():
            self.form.removeRow(0)
        self._edits = {}
        self._fields = form_fields(self.profile, self.node.kind)
        for fd in self._fields:
            w = make_editor(fd, self.meta.get(fd.key, ""))
            self._edits[fd.key] = w
            self.form.addRow(fd.label + ("  *" if fd.required else ""), w)
        self.form_error.hide()
        self.kv.hide()
        self.form_host.show()
        self.edit_row.show()
        self.edit_btn.hide()

    def stop_edit(self) -> None:
        self.form_host.hide()
        self.edit_row.hide()
        self.form_error.hide()
        self.kv.show()
        if self.node is not None:
            self.edit_btn.setVisible(self._editable(self.node))

    def _save(self) -> None:
        vals = {k: editor_value(w) for k, w in self._edits.items()}
        for fd in self._fields:
            missing = fd.required and not vals.get(fd.key)
            mark_invalid(self._edits[fd.key], missing)
            if missing:
                self.form_error.setText(f"{fd.label} is required.")
                self.form_error.show()
                return
        self.saved.emit(self.node, vals)


# ======================================================================
# The page
# ======================================================================

class ProjectsPage(QWidget):
    open_session_requested = Signal(object)          # Path
    new_session_requested = Signal(object)           # dict prefill
    import_requested = Signal(list)                  # image paths (no lot selected)
    choose_workspace_requested = Signal()

    def __init__(self, state, toasts=None, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.toasts = toasts
        self._gen = 0
        self._tree_gen = 0
        self._contents: Optional[dict] = None
        self._node: Optional[NodeRef] = None
        self._pending_select: Optional[Path] = None
        self._selected_card: Optional[NodeCard] = None
        self._loading = False
        self._build()
        state.workspace_changed.connect(self.reload)
        state.node_changed.connect(self._on_state_node)
        state.profile_changed.connect(self._on_profile_changed)
        self._apply_profile_labels()

    # ------------------------------------------------------------------ profile
    @property
    def profile(self):
        return self.state.profile

    def lbl(self, kind: str) -> str:
        return hui.kind_label(self.profile, kind)

    def _on_profile_changed(self) -> None:
        self._apply_profile_labels()
        self.reload()

    def _apply_profile_labels(self) -> None:
        p = self.profile
        self.details.profile = p
        top = self.lbl("project")
        chain = " › ".join(self.lbl(k) for k in hui.LEVELS)
        self.btn_new_project.setToolTip(f"New {top}…")
        self.btn_new_project.setAccessibleName(f"New {top}")
        self.tree.setToolTip(f"{chain}. F2 renames; right-click for more."
                             + (f" Double-click a {self.lbl('lot')} to open it."
                                if hui.lot_mode(p) else ""))
        self.btn_import.setToolTip("Bring an existing folder of SEM images into the workspace "
                                   f"(into the selected {self.lot_word()}, or a new one)")
        self.filter.setPlaceholderText("Filter by name, operator, notes…")

    def lot_word(self) -> str:
        return self.lbl("lot")

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        root.addWidget(split)

        # --- tree panel
        left = Panel("right")
        left.setMinimumWidth(240)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(SPACE.md, SPACE.lg, SPACE.md, SPACE.md)
        lv.setSpacing(SPACE.sm)
        th = QHBoxLayout()
        th.addWidget(label("WORKSPACE", "overline"), 1)
        self.btn_new_project = IconButton("new_folder", "New project")
        self.btn_new_project.clicked.connect(lambda: self.start_create("project"))
        self.btn_refresh = IconButton("refresh", "Rescan the workspace folder")
        self.btn_refresh.clicked.connect(self.reload)
        th.addWidget(self.btn_new_project)
        th.addWidget(self.btn_refresh)
        lv.addLayout(th)
        self.tree = QTreeView()
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setEditTriggers(QAbstractItemView.EditKeyPressed)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        self.tree.setIconSize(QSize(16, 16))
        self.model = QStandardItemModel(self)
        self.model.itemChanged.connect(self._on_item_renamed)
        self.tree.setModel(self.model)
        self.tree.selectionModel().currentChanged.connect(self._on_tree_current)
        self.tree.doubleClicked.connect(self._on_tree_double)
        lv.addWidget(self.tree, 1)
        self.ws_path = label("", "caption")
        self.ws_path.setWordWrap(True)
        lv.addWidget(self.ws_path)
        self.btn_import = AnimatedButton("Import folder of images…", "import", "secondary")
        self.btn_import.clicked.connect(self.import_folder)
        lv.addWidget(self.btn_import)
        split.addWidget(left)

        # --- centre
        centre = QWidget()
        cv = QVBoxLayout(centre)
        cv.setContentsMargins(SPACE.xl, SPACE.lg, SPACE.xl, SPACE.md)
        cv.setSpacing(SPACE.md)
        self.header = PageHeader("Workspace", "Workspace")
        self.btn_primary = AnimatedButton("New project", "add", "primary")
        self.btn_primary.clicked.connect(self._primary_action)
        self.btn_secondary = AnimatedButton("Import images…", "import", "secondary")
        self.btn_secondary.clicked.connect(self.import_files)
        self.btn_more = IconButton("more", "More actions for this item")
        self.btn_more.clicked.connect(lambda: self._node_menu(self._node, self.btn_more))
        self.header.actions.addWidget(self.btn_secondary)
        self.header.actions.addWidget(self.btn_primary)
        self.header.actions.addWidget(self.btn_more)
        cv.addWidget(self.header)
        sr = QHBoxLayout()
        sr.setSpacing(SPACE.md)
        self.metrics = [MetricCard("", 0, "", 0) for _ in range(4)]
        for m in self.metrics:
            m.setMaximumHeight(92)
            sr.addWidget(m)
        cv.addLayout(sr)
        self.confirm = ConfirmBar()
        cv.addWidget(self.confirm)
        self.form = NodeForm()
        self.form.submitted.connect(self._create_node)
        cv.addWidget(self.form)
        fb = QHBoxLayout()
        fb.setSpacing(SPACE.sm)
        self.filter = SearchBox("Filter by name, operator, notes…", debounce_ms=150)
        self.filter.setToolTip("Filter the cards below (does not search other folders — "
                               "use the search box in the top bar for that)")
        self.filter.search_changed.connect(lambda _t: self._render_items())
        self.sort = QComboBox()
        self.sort.addItems(["Newest first", "Oldest first", "Name A–Z", "Operator A–Z",
                            "Most grains"])
        self.sort.setToolTip("Sort order")
        self.sort.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.sort.currentIndexChanged.connect(lambda _i: self._render_items())
        self.op_filter = QComboBox()
        self.op_filter.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.op_filter.setToolTip("Show sessions by one operator only")
        self.op_filter.currentIndexChanged.connect(lambda _i: self._render_items())
        self.count_lbl = label("", "caption")
        fb.addWidget(self.filter, 1)
        fb.addWidget(self.op_filter)
        fb.addWidget(self.sort)
        fb.addWidget(self.count_lbl)
        cv.addLayout(fb)

        self.stack = FadeStackedWidget()
        # loading
        self.loading_grid = CardGrid(260)
        self.loading_grid.set_widgets([SkeletonCard() for _ in range(6)])
        lw = QWidget()
        lwl = QVBoxLayout(lw)
        lwl.setContentsMargins(4, 4, 4, 4)
        lwl.addWidget(self.loading_grid)
        lwl.addStretch(1)
        self.stack.addWidget(lw)
        # grid
        self.grid = CardGrid(260)
        gw = QWidget()
        gwl = QVBoxLayout(gw)
        gwl.setContentsMargins(4, 4, 4, 12)
        gwl.addWidget(self.grid)
        gwl.addStretch(1)
        self.grid_scroll = scroll(gw)
        self.stack.addWidget(self.grid_scroll)
        # empty
        self.empty = EmptyState("projects", "", "", "Create", "add")
        self.empty.action_triggered.connect(self._primary_action)
        self.empty_second = AnimatedButton("Choose a different workspace folder…", "open", "ghost")
        self.empty_second.clicked.connect(self.choose_workspace_requested)
        self.empty.layout().insertWidget(self.empty.layout().count() - 1, self.empty_second, 0,
                                         Qt.AlignHCenter)
        self.stack.addWidget(self.empty)
        cv.addWidget(self.stack, 1)
        split.addWidget(centre)

        self.details = DetailsPanel()
        self.details.open_session.connect(self.open_session_requested)
        self.details.saved.connect(self._save_meta)
        split.addWidget(self.details)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        split.setSizes([270, 900, 300])

    # ------------------------------------------------------------------ tree
    def reload(self) -> None:
        """Rescan the tree (off-thread) and reload the current node."""
        self._tree_gen += 1
        gen = self._tree_gen
        root = str(self.state.root)
        fm = self.ws_path.fontMetrics()
        self.ws_path.setText("Stored in  " + fm.elidedText(root, Qt.ElideMiddle, 210))
        self.ws_path.setToolTip(root)
        self.details.profile = self.profile
        run_task(scan_tree, root, on_done=lambda d: self._fill_tree(gen, d),
                 on_error=lambda m: self._toast("Could not read workspace", m.splitlines()[0], "danger"))
        node = self._node or NodeRef("workspace", self.state.root)
        if not node.path.exists():
            node = NodeRef("workspace", self.state.root)
        self.show_node(node)

    def _fill_tree(self, gen: int, data: List[dict]) -> None:
        if gen != self._tree_gen:
            return
        self._filling = True
        self.model.clear()
        p = self.profile
        rootitem = self._make_item("workspace", self.state.root,
                                   f"All {hui.plural(self.lbl('project')).lower()}")
        rootitem.setEditable(False)
        self.model.appendRow(rootitem)

        def add(parent, nodes):
            for n in nodes:
                text = hui.node_caption(p, n["kind"], n["meta"], n["path"])
                if n["kind"] == "lot":
                    cnt = n.get("n_images") if hui.lot_mode(p) else n.get("n_sessions")
                    if cnt:
                        text += f"   ·  {cnt}"
                it = self._make_item(n["kind"], n["path"], text,
                                     hui.crumb_caption(p, n["kind"], n["meta"], n["path"]))
                parent.appendRow(it)
                add(it, n.get("children", []))
        add(rootitem, data)
        self._filling = False
        self.tree.expandToDepth(1)
        target = self._pending_select or (self._node.path if self._node else None)
        self._pending_select = None
        if target is not None:
            self._select_tree_path(Path(target))

    def _make_item(self, kind: str, path: Path, text: str, tip: str = "") -> QStandardItem:
        it = QStandardItem(icons.icon(_KIND_ICON[kind]), text)
        it.setData(kind, KIND_ROLE)
        it.setData(str(path), PATH_ROLE)
        it.setEditable(kind in ("project", "sample", "lot"))
        it.setToolTip(f"{tip}\n{path}" if tip else str(path))
        return it

    def tree_items(self) -> List[QStandardItem]:
        out = []

        def walk(item):
            out.append(item)
            for r in range(item.rowCount()):
                walk(item.child(r))
        for r in range(self.model.rowCount()):
            walk(self.model.item(r))
        return out

    def _find_item(self, path: Path) -> Optional[QStandardItem]:
        target = str(path)
        for it in self.tree_items():
            if it.data(PATH_ROLE) == target:
                return it
        return None

    def _select_tree_path(self, path: Path) -> None:
        it = self._find_item(path)
        if it is None and path.parent != path:
            it = self._find_item(path.parent)  # session -> its lot
        if it is None:
            return
        idx = self.model.indexFromItem(it)
        self.tree.selectionModel().blockSignals(True)
        self.tree.setCurrentIndex(idx)
        self.tree.selectionModel().blockSignals(False)
        self.tree.scrollTo(idx)

    def _on_tree_current(self, cur: QModelIndex, _prev) -> None:
        if not cur.isValid():
            return
        kind = cur.data(KIND_ROLE)
        path = Path(cur.data(PATH_ROLE))
        self.state.set_node(NodeRef(kind, path))

    def _on_tree_double(self, idx: QModelIndex) -> None:
        if idx.isValid() and idx.data(KIND_ROLE) == "lot" and hui.lot_mode(self.profile):
            self.open_session_requested.emit(Path(idx.data(PATH_ROLE)))

    def _on_state_node(self, node: Optional[NodeRef]) -> None:
        if node is None:
            node = NodeRef("workspace", self.state.root)
        if node.kind == "session":
            lot = NodeRef("lot", node.path.parent)
            if self._node != lot:
                self.show_node(lot, select_session=node.path)
            else:
                self._select_card_path(node.path)
            self._select_tree_path(lot.path)
            return
        if node != self._node:
            self.show_node(node)
        self._select_tree_path(node.path)

    def select_node(self, path) -> None:
        """Programmatic navigation (breadcrumb, tests)."""
        node = node_for_path(self.state.root, Path(path))
        self.state.set_node(node)

    def _on_item_renamed(self, item: QStandardItem) -> None:
        if getattr(self, "_filling", False):
            return
        kind = item.data(KIND_ROLE)
        path = Path(item.data(PATH_ROLE))
        new = item.text().strip().split("   ·")[0].strip()
        prefix = self.lbl("lot") + " "
        if kind == "lot" and new.startswith(prefix):
            new = new[len(prefix):].strip()
        if not new:
            self.reload()
            return
        self._rename(NodeRef(kind, path), new)

    def _rename(self, node: NodeRef, new: str) -> None:
        ws = self.state.workspace
        self._release_open_session(node.path)
        try:
            if node.kind not in hui.LEVELS:
                return
            newp = hui.rename_level(ws, node.kind, node.path, new)
        except Exception as e:
            self._toast("Rename failed", str(e), "danger")
            self.reload()
            return
        self._toast("Renamed", f"{self.lbl(node.kind)} renamed to “{new}”.", "success")
        run_task(lambda: Catalog(self.state.root).rebuild())
        self._pending_select = newp
        self._node = NodeRef(node.kind, newp)
        self.state.set_node(self._node)
        self.reload()

    def _release_open_session(self, path: Path) -> None:
        s = self.state.session
        if s is not None:
            try:
                s.path.relative_to(path)
            except ValueError:
                return
            self.state.close_session()

    # ------------------------------------------------------------------ menus
    def _tree_menu(self, pos) -> None:
        idx = self.tree.indexAt(pos)
        if not idx.isValid():
            return
        node = NodeRef(idx.data(KIND_ROLE), Path(idx.data(PATH_ROLE)))
        self._node_menu(node, self.tree.viewport(), pos)

    def menu_actions(self, node: NodeRef) -> List[tuple]:
        """(text, icon, callback) for the node's context menu (None = separator)."""
        acts: List[Optional[tuple]] = []
        child = hui.child_kind(self.profile, node.kind)
        lot_mode = hui.lot_mode(self.profile)
        if child and child != "session":
            acts.append((f"New {self.lbl(child)}…", "add",
                         lambda: self._create_under(node, child)))
        if node.kind == "lot":
            if lot_mode:
                acts.append((f"Open {self.lbl('lot')}", "open",
                             lambda: self.open_session_requested.emit(node.path)))
                acts.append((f"Add images to this {self.lbl('lot')}…", "import",
                             self.import_files))
            else:
                acts.append((f"New session in this {self.lbl('lot')}…", "add",
                             lambda: self.new_session_requested.emit(self._prefill(node))))
                acts.append((f"Import images into this {self.lbl('lot')}…", "import",
                             self.import_files))
        if node.kind == "session":
            acts.append(("Open session", "open", lambda: self.open_session_requested.emit(node.path)))
        if node.kind in ("project", "sample", "lot", "session"):
            acts.append(("Edit metadata", "edit", lambda: self._edit(node)))
        if node.kind in hui.LEVELS:
            acts.append(("Rename  (F2)", "edit", lambda: self._start_rename(node)))
        acts.append(("Show in File Explorer", "open", lambda: self._reveal(node.path)))
        if node.kind != "workspace":
            acts.append(None)
            acts.append(("Move to trash…", "delete", lambda: self.ask_delete(node)))
        return acts

    def _node_menu(self, node: Optional[NodeRef], anchor: QWidget, pos=None) -> None:
        if node is None:
            return
        m = QMenu(self)
        for a in self.menu_actions(node):
            if a is None:
                m.addSeparator()
            else:
                m.addAction(icons.icon(a[1]), a[0], a[2])
        gp = anchor.mapToGlobal(pos) if pos is not None else anchor.mapToGlobal(anchor.rect().bottomLeft())
        m.exec(gp)

    def _start_rename(self, node: NodeRef) -> None:
        it = self._find_item(node.path)
        if it is not None:
            if node.kind == "lot":
                self._filling = True
                it.setText(it.text().split("   ·")[0])
                self._filling = False
            self.tree.edit(self.model.indexFromItem(it))

    def _edit(self, node: NodeRef) -> None:
        self.state.set_node(node)
        self.details.start_edit()

    @staticmethod
    def _reveal(path: Path) -> None:
        try:
            if sys.platform == "win32":
                os.startfile(str(path))  # local Explorer window only
        except OSError:
            pass

    # ------------------------------------------------------------------ contents
    def show_node(self, node: NodeRef, select_session: Optional[Path] = None) -> None:
        self._node = node
        self.details._root = str(self.state.root)
        self.details.profile = self.profile
        self._gen += 1
        gen = self._gen
        self._loading = True
        self.confirm.hide_bar()
        self.form.hide()
        self._update_header(node, {})
        self.stack.set_current_index(0)
        self._pending_card = select_session
        run_task(load_contents, node.kind, str(node.path), str(self.state.root),
                 on_done=lambda d: self._on_contents(gen, d),
                 on_error=lambda m: self._on_contents_error(gen, m))

    def is_loading(self) -> bool:
        return self._loading

    def _on_contents_error(self, gen, msg) -> None:
        if gen != self._gen:
            return
        self._loading = False
        self._contents = {"kind": self._node.kind, "path": self._node.path, "items": [], "meta": {}}
        self._toast("Could not open folder", msg.splitlines()[0], "danger")
        self._render_items()

    def _contains_text(self, kind: str, n: int) -> str:
        child = hui.child_kind(self.profile, kind)
        if child is None or child == "session":
            return f"{n} record{'s' if n != 1 else ''}" if hui.lot_mode(self.profile) else \
                f"{n} session{'s' if n != 1 else ''}"
        return hui.count_text(n, self.lbl(child))

    def _on_contents(self, gen: int, data: dict) -> None:
        if gen != self._gen:
            return
        self._loading = False
        self._contents = data
        self._update_header(self._node, data.get("meta", {}))
        ops = sorted({it.get("operator", "") for it in data["items"] if it.get("operator")})
        self.op_filter.blockSignals(True)
        self.op_filter.clear()
        self.op_filter.addItem("All operators", "")
        for o in ops:
            self.op_filter.addItem(o, o)
        self.op_filter.blockSignals(False)
        self.op_filter.setVisible(self._node.kind == "lot" and len(ops) > 1)
        self._render_items()
        self._update_metrics(data)
        extra = None
        if self._node.kind != "session":
            extra = {"Contains": self._contains_text(self._node.kind, len(data["items"]))}
        self.details.show_node(self._node, data.get("meta", {}), extra,
                               acquisition=data.get("acquisition") or None)
        if self._pending_card is not None:
            self._select_card_path(self._pending_card)
            self._pending_card = None

    def _update_metrics(self, data: dict) -> None:
        items, kind = data["items"], data["kind"]
        n = len(items)
        s = lambda k: sum(int(it.get(k, 0) or 0) for it in items)  # noqa: E731
        L = lambda k: hui.plural(self.lbl(k))  # noqa: E731
        lot_mode = hui.lot_mode(self.profile)
        if kind == "lot":
            gs = [it["g"] for it in items if it.get("g") is not None]
            vals = [("Images", s("n_images"), "", 0),
                    ("Analysed", s("n_analysed"), "", 0),
                    ("Grains measured", s("grains"), "", 0),
                    ("Mean ASTM grain size", (sum(gs) / len(gs)) if gs else None,
                     "G" if gs else "n/a", 1)]
            if not lot_mode:
                vals[1] = ("Sessions", n, "", 0)
        elif kind == "sample":
            vals = [(L("lot"), n, "", 0),
                    ("Analysed images" if lot_mode else "Sessions",
                     s("n_analysed") if lot_mode else s("n_sessions"), "", 0),
                    ("Images", s("n_images"), "", 0), ("Latest activity",
                     fmt_date_utc(max((it.get("created", "") for it in items), default=""),
                                  with_time=False) or "—", "", 0)]
        elif kind == "project":
            vals = [(L("sample"), n, "", 0), (L("lot"), s("n_lots"), "", 0),
                    ("Images" if lot_mode else "Sessions",
                     s("n_images") if lot_mode else s("n_sessions"), "", 0),
                    ("Created", fmt_date_utc(data.get("meta", {}).get("created_utc", ""),
                                             with_time=False) or "—", "", 0)]
        else:
            vals = [(L("project"), n, "", 0), (L("sample"), s("n_samples"), "", 0),
                    (L("lot"), s("n_lots"), "", 0),
                    ("Images" if lot_mode else "Sessions",
                     s("n_images") if lot_mode else s("n_sessions"), "", 0)]
        for card, (lab, v, unit, dec) in zip(self.metrics, vals):
            card.set_label(lab)
            if isinstance(v, str):
                card.set_text(v)
            else:
                card.set_metric(v, unit, dec)

    def _update_header(self, node: NodeRef, meta: dict) -> None:
        kind = node.kind
        p = self.profile
        lot_mode = hui.lot_mode(p)
        title = f"All {hui.plural(self.lbl('project')).lower()}" if kind == "workspace" \
            else node_display_name(node, p)
        sub = ""
        if kind == "workspace":
            sub = f"Every analysis is stored locally in {self.state.root}"
        elif kind in hui.LEVELS:
            parts = []
            for fd in hui.level_fields(p, kind):
                v = meta.get(fd.key)
                if v not in (None, ""):
                    parts.append(f"{fd.label} {v}" if fd.kind in ("date", "number") else str(v))
            sub = " · ".join(parts)
        self.header.set_text(self.lbl(kind), title, sub)
        child = hui.child_kind(p, kind)
        self.btn_primary.set_icon_name("add")
        if kind == "lot" and lot_mode:
            self.btn_primary.setText(f"Open {self.lbl('lot')}")
            self.btn_primary.set_icon_name("open")
            self.btn_primary.setToolTip(f"Open this {self.lbl('lot')}'s images and results "
                                        "in Analyze / Review")
        elif kind == "lot":
            self.btn_primary.setText("New session")
            self.btn_primary.setToolTip(f"Start a new analysis session in this {self.lbl('lot')} "
                                        "(Ctrl+N)")
        elif child:
            self.btn_primary.setText(f"New {self.lbl(child)}")
            self.btn_primary.setToolTip(f"Create a {self.lbl(child)} here")
        self.btn_secondary.setVisible(kind == "lot")
        self.btn_secondary.setText("Add images…" if lot_mode else "Import images…")
        self.btn_secondary.setToolTip(
            f"Copy SEM image files into this {self.lbl('lot')} (added to its images)"
            if lot_mode else f"Copy SEM image files into a new session in this {self.lbl('lot')}")
        self.btn_more.setVisible(kind != "workspace")

    def _filtered_items(self) -> List[dict]:
        if not self._contents:
            return []
        items = list(self._contents["items"])
        text = self.filter.text().strip().lower()
        if text:
            def hay(it):
                m = it.get("meta", {})
                return " ".join(str(v) for v in m.values() if isinstance(v, (str, int, float))).lower()
            items = [it for it in items if text in hay(it)]
        op = self.op_filter.currentData() if self.op_filter.isVisible() else ""
        if op:
            items = [it for it in items if it.get("operator") == op]
        mode = self.sort.currentIndex()
        if mode == 0:
            items.sort(key=lambda it: it.get("created", ""), reverse=True)
        elif mode == 1:
            items.sort(key=lambda it: it.get("created", ""))
        elif mode == 2:
            items.sort(key=lambda it: str(it["meta"].get("name") or it["meta"].get("sample_id") or
                                          it["meta"].get("lot_number") or it["meta"].get("label")
                                          or it["path"].name).lower())
        elif mode == 3:
            items.sort(key=lambda it: (it.get("operator") or "~").lower())
        elif mode == 4:
            items.sort(key=lambda it: it.get("grains", 0), reverse=True)
        items.sort(key=lambda it: not it.get("record"))       # the lot's own record first
        return items

    def _render_items(self) -> None:
        if self._contents is None or self._loading:
            return
        items = self._filtered_items()
        total = len(self._contents["items"])
        kind = self._node.kind
        self.count_lbl.setText(f"{len(items)} of {total}" if len(items) != total else
                               self._contains_text(kind, total))
        self._selected_card = None
        if not items:
            self._set_empty(kind, filtered=total > 0)
            self.stack.set_current_index(2)
            return
        cards = []
        for it in items:
            c = NodeCard(it, self.profile)
            c.clicked.connect(lambda c=c: self._card_clicked(c))
            c.double_clicked.connect(lambda c=c: self._card_open(c))
            cards.append(c)
        self.grid.set_widgets(cards)
        self.stack.set_current_index(1)

    def _set_empty(self, kind: str, filtered: bool) -> None:
        e = self.empty
        L = self.lbl
        if filtered:
            e.title_label.setText("Nothing matches the filter")
            e.body_label.setText("Clear the filter box to see everything in this folder.")
            e.action_button.setText("Clear filter")
            e.action_button.set_icon_name("clear")
            _reconnect(e.action_button.clicked, self.filter.clear_search)
            self.empty_second.hide()
            return
        _reconnect(e.action_button.clicked, e.action_triggered)
        e.action_button.set_icon_name("add")
        self.empty_second.setVisible(kind == "workspace")
        chain = " › ".join(L(k) for k in hui.LEVELS)
        if kind == "workspace":
            e.title_label.setText("Set up your lab workspace")
            e.body_label.setText(f"Analyses are stored in labelled folders — {chain} — "
                                 f"on this computer:\n{self.state.root}\n\nStart by creating your "
                                 f"first {L('project')}. The folder structure and names can be "
                                 "changed in Settings ▸ Folder structure & naming.")
            e.action_button.setText(f"Create first {L('project')}")
        elif kind == "project":
            e.title_label.setText(f"No {hui.plural(L('sample'))} in this {L('project')} yet")
            e.body_label.setText(f"Add a {L('sample')} by its {hui.id_label(self.profile, 'sample')}.")
            e.action_button.setText(f"New {L('sample')}")
        elif kind == "sample":
            e.title_label.setText(f"No {hui.plural(L('lot'))} for this {L('sample')} yet")
            e.body_label.setText(f"Add a {L('lot')} by its {hui.id_label(self.profile, 'lot')}.")
            e.action_button.setText(f"New {L('lot')}")
        elif hui.lot_mode(self.profile):
            e.title_label.setText(f"No images in this {L('lot')} yet")
            e.body_label.setText(f"The SEM images of this {L('lot')}, their calibration and "
                                 "results are stored in the folder itself.")
            e.action_button.setText("Add images")
            e.action_button.set_icon_name("import")
            _reconnect(e.action_button.clicked, self.import_files)
        else:
            e.title_label.setText(f"No sessions in this {L('lot')} yet")
            e.body_label.setText("A session holds the SEM images of one sitting at the microscope, "
                                 "their calibration and results.")
            e.action_button.setText("New session")

    def cards(self) -> List[NodeCard]:
        return [w for w in self.grid.widgets() if isinstance(w, NodeCard)]

    def _card_clicked(self, card: NodeCard) -> None:
        if self._selected_card is not None and self._selected_card is not card:
            try:
                self._selected_card.set_selected(False)
            except RuntimeError:
                pass
        self._selected_card = card
        card.set_selected(True)
        it = card.item
        node = NodeRef(it["kind"], it["path"])
        extra = None
        if it["kind"] == "session":
            if it.get("record"):
                node = NodeRef("lot", it["path"])
            extra = {"Images": f"{it['n_analysed']} of {it['n_images']} analysed",
                     "Grains": fmt_int(it.get("grains", 0))}
            if it.get("g") is not None:
                extra["ASTM grain size"] = f"G {fmt_opt(it['g'], 1)}"
        meta = it.get("meta", {})
        acq = None
        if node.kind == "lot":
            meta = hui.read_meta("lot", node.path)
            acq = hui.read_meta("session", node.path) if hui.lot_mode(self.profile) else None
        self.details.show_node(node, meta, extra, acquisition=acq)

    def _select_card_path(self, path: Path) -> None:
        for c in self.cards():
            if Path(c.item["path"]) == Path(path):
                self._card_clicked(c)
                self.grid_scroll.ensureWidgetVisible(c)
                return

    def _card_open(self, card: NodeCard) -> None:
        it = card.item
        if it["kind"] == "session" or (it["kind"] == "lot" and hui.lot_mode(self.profile)):
            self.open_session_requested.emit(it["path"])
        else:
            self.state.set_node(NodeRef(it["kind"], it["path"]))

    # ------------------------------------------------------------------ actions
    def _primary_action(self) -> None:
        node = self._node or NodeRef("workspace", self.state.root)
        if node.kind == "lot":
            if hui.lot_mode(self.profile):
                self.open_session_requested.emit(node.path)
            else:
                self.new_session_requested.emit(self._prefill(node))
        elif hui.child_kind(self.profile, node.kind):
            self._create_under(node, hui.child_kind(self.profile, node.kind))

    def start_create(self, kind: str) -> None:
        node = self._node or NodeRef("workspace", self.state.root)
        if kind == "project":
            self.state.set_node(NodeRef("workspace", self.state.root))
            node = NodeRef("workspace", self.state.root)
        self._create_under(node, kind)

    def _create_under(self, parent: NodeRef, kind: str) -> None:
        if self._node != parent:
            self.state.set_node(parent)
        where = "in " + (node_display_name(parent, self.profile, crumb=True)
                         if parent.kind != "workspace" else "the workspace")
        self.form.open_for(kind, where, self.profile)
        self._create_parent = parent

    def _create_node(self, vals: dict) -> None:
        parent = getattr(self, "_create_parent", None) or NodeRef("workspace", self.state.root)
        ws = self.state.workspace
        kind = self.form.kind
        vals = dict(vals)
        try:
            ident = vals.pop(hui.ID_KEYS[kind])
            if kind == "project":
                newp = ws.create_project(ident, **vals)
            elif kind == "sample":
                newp = ws.create_sample(parent.path, ident, **vals)
            else:
                newp = ws.create_lot(parent.path.parent, parent.path, ident, **vals)
        except Exception as e:
            self.form.error.setText(str(e))
            self.form.error.show()
            return
        self.form.hide()
        self._toast(f"{self.lbl(kind)} created", str(newp.name), "success")
        self._pending_select = newp
        self._node = NodeRef(kind, newp)
        self.state.set_node(self._node)
        self.reload()

    def _prefill(self, node: NodeRef) -> dict:
        lot = node.path if node.kind == "lot" else None
        if lot is None:
            return {}
        return {"project_path": lot.parent.parent, "sample_path": lot.parent, "lot_path": lot}

    def ask_delete(self, node: Optional[NodeRef] = None) -> None:
        node = node or self._node
        if node is None or node.kind == "workspace":
            return
        name = node_display_name(node, self.profile)
        L = self.lbl
        inner = "its images and results" if hui.lot_mode(self.profile) else "every session in it"
        what = {"session": "this session with all its images and results",
                "lot": f"this {L('lot')} and {inner}",
                "sample": f"this {L('sample')} and everything inside it",
                "project": f"this {L('project')} and everything inside it"}[node.kind]
        self.confirm.ask(f"Move “{name}” to the trash?",
                         f"This moves {what} into the workspace’s .trash folder. Nothing is "
                         "permanently deleted — Undo on the next message restores it.",
                         "Move to trash", lambda: self._delete(node))

    def _delete(self, node: NodeRef) -> None:
        self._release_open_session(node.path)
        ws = self.state.workspace
        root = self.state.root

        def work():
            return trash_node(ws, root, node.kind, node.path)

        def done(dest):
            can_undo = node.kind in ("project", "sample", "lot", "session")
            self._toast("Moved to trash", node.path.name, "success",
                        "Undo" if can_undo else None,
                        (lambda: self.restore_from_trash(dest)) if can_undo else None)
            parent = node_for_path(root, node.path.parent)
            self._node = parent
            self._pending_select = parent.path
            self.state.set_node(parent)
            self.reload()

        run_task(work, on_done=done,
                 on_error=lambda m: self._toast("Could not move to trash", m.splitlines()[0], "danger"))

    def restore_from_trash(self, trash_path) -> None:
        ws = self.state.workspace
        root = self.state.root

        def work():
            return ws.restore_from_trash(trash_path, catalog=Catalog(root))

        def done(target):
            self._toast("Restored", Path(target).name, "success")
            self._pending_select = Path(target)
            self.reload()

        def failed(msg):
            first = msg.splitlines()[0]
            if first.startswith("FileExistsError"):
                first = ("Something with the same name now exists in its original place. "
                         "Rename it, then undo again from the .trash folder.")
            self._toast("Could not restore", first, "danger")

        run_task(work, on_done=done, on_error=failed)

    def _save_meta(self, node: NodeRef, vals: dict) -> None:
        ws = self.state.workspace
        try:
            if node.kind in hui.LEVELS:
                key = hui.ID_KEYS[node.kind]
                new_id = vals.get(key, "")
                hui.update_level_meta(ws, node.kind, node.path, vals)
                if new_id and new_id != (self.details.meta.get(key) or ""):
                    self._rename(node, new_id)
                    return
            elif node.kind == "session":
                ws.update_session_meta(node.path, **vals)
                root = self.state.root
                run_task(lambda: Catalog(root).index_session(node.path))
                s = self.state.session
                if s is not None and s.path == node.path:
                    for k, v in vals.items():
                        setattr(s.meta, k, v)
        except Exception as e:
            self._toast("Could not save", str(e), "danger")
            return
        self.details.stop_edit()
        self._toast("Details saved", node_display_name(node, self.profile), "success")
        self.show_node(self._node, select_session=node.path if node.kind == "session" else None)
        if node.kind != "session":
            self.reload()

    def import_files(self) -> None:
        """Pick image files and import them into the selected lot."""
        paths, _ = QFileDialog.getOpenFileNames(
            self, f"Add SEM images to this {self.lbl('lot')}", "",
            "SEM images (*.tif *.tiff *.png *.jpg *.jpeg *.bmp)")
        if paths:
            self._import(paths, label="Imported")

    def import_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose a folder of SEM images")
        if not d:
            return
        paths = sorted(str(p) for p in Path(d).iterdir()
                       if p.is_file() and p.suffix.lower() in IMAGE_EXTS)
        if not paths:
            self._toast("No images found", f"{d} contains no TIFF/PNG/JPEG/BMP files.", "warning")
            return
        if self._node is not None and self._node.kind == "lot":
            self._import(paths, label=Path(d).name)
        else:
            self.import_requested.emit(paths)

    def _import(self, paths: List[str], label: str) -> None:
        node = self._node
        if node is None or node.kind != "lot":
            self.import_requested.emit(paths)
            return
        lot = node.path
        s = self.state.session
        if s is not None and s.path == lot:
            # the lot is open: append through the open record (stays in sync)
            self.state.add_images(paths)
            return
        pm = read_json(lot.parent.parent / "project.json")
        sm = read_json(lot.parent / "sample.json")
        lm = read_json(lot / "lot.json")
        root = self.state.root
        lot_mode = hui.lot_mode(self.profile)
        run_label = None if lot_mode else label

        def work():
            ws = Workspace(root)
            return import_loose_images(ws, paths, pm.get("name") or lot.parent.parent.name,
                                       sm.get("sample_id") or lot.parent.name,
                                       lm.get("lot_number") or lot.name, run_label,
                                       catalog=Catalog(root))

        def done(ref):
            where = f"{self.lbl('lot')} {lm.get('lot_number', '')}" if lot_mode \
                else f"new session “{label}”"
            self._toast("Images imported", f"{len(paths)} image(s) → {where}.",
                        "success", "Open", lambda: self.open_session_requested.emit(ref.path))
            self.show_node(node, select_session=ref.path)
            self.reload()

        self._toast("Importing…", f"Copying {len(paths)} image(s) into "
                                  f"{self.lbl('lot')} {lm.get('lot_number', '')}", "info")
        run_task(work, on_done=done,
                 on_error=lambda m: self._toast("Import failed", m.splitlines()[0], "danger"))

    def _toast(self, title, body="", sev="info", action=None, on_action=None) -> None:
        if self.toasts is not None:
            self.toasts.show_toast(title, body, sev, action, on_action)
