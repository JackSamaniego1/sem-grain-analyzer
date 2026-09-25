"""
Results table of the Analyze page (UX-09).

One row per image with its Job / Part / Lot (the workspace's own level
names), analysis status, scan area, scale and the headline results.  "Group
by" nests the rows under Job, Part or Lot (group rows carry the totals) or
shows everything together; every column sorts by clicking its header.  A
double-click opens the image.  The right end of the toolbar is reserved for
the multi-lot report export (a later wave).
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from ui import hierarchy_ui as hui
from ui.design.tokens import SPACE
from ui.format import astm_g, fmt_int
from ui.pages.image_tree import image_status
from ui.widgets import label

GROUP_LEVELS = ("", "project", "sample", "lot")      # "" = everything together
COL_IMAGE, COL_PROJECT, COL_SAMPLE, COL_LOT, COL_STATUS, COL_SCAN, COL_SCALE, \
    COL_GRAINS, COL_DIAM, COL_G = range(10)
SORT_ROLE = Qt.UserRole + 20
UID_ROLE = Qt.UserRole + 21


class _Item(QTreeWidgetItem):
    """Sorts numerically on SORT_ROLE when present (blank last)."""

    def __lt__(self, other) -> bool:
        tree = self.treeWidget()
        col = tree.sortColumn() if tree is not None else 0
        a, b = self.data(col, SORT_ROLE), other.data(col, SORT_ROLE)
        if a is None and b is None:
            return (self.text(col) or "").lower() < (other.text(col) or "").lower()
        if a is None:
            return False
        if b is None:
            return True
        try:
            return a < b
        except TypeError:
            return str(a) < str(b)


def level_ids(rec) -> Dict[str, str]:
    """{project, sample, lot, session} ids of a record (no disk access)."""
    path = Path(rec.path)
    if rec.is_lot:
        paths = {"project": path.parent.parent, "sample": path.parent, "lot": path}
    else:
        paths = {"project": path.parent.parent.parent, "sample": path.parent.parent,
                 "lot": path.parent}
    metas = {"project": rec.project_meta, "sample": rec.sample_meta, "lot": rec.lot_meta}
    out = {}
    for k, p in paths.items():
        try:
            out[k] = hui.id_value(k, metas[k] or {}, p) or p.name
        except Exception:
            out[k] = p.name
    return out


class ResultsTable(QWidget):
    """Per-image results, groupable by level."""

    open_image = Signal(object)          # uid

    def __init__(self, state, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self._dirty = True
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self.rebuild)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(SPACE.sm)
        bar = QHBoxLayout()
        bar.setSpacing(SPACE.sm)
        bar.addWidget(label("Group by", tone="secondary"))
        self.group = QComboBox()
        self.group.setToolTip("Nest the images under a level (with totals per group), "
                              "or list every image together")
        self.group.currentIndexChanged.connect(lambda _i: self.rebuild())
        bar.addWidget(self.group)
        self.summary = label("", "caption")
        bar.addWidget(self.summary, 1)
        # UX-13 (later wave): the "Export multi-lot report" button goes here,
        # right-aligned next to the grouping controls.
        self.actions = QHBoxLayout()
        self.actions.setSpacing(SPACE.sm)
        bar.addLayout(self.actions)
        v.addLayout(bar)
        self.tree = QTreeWidget()
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSortingEnabled(True)
        self.tree.header().setSortIndicator(COL_IMAGE, Qt.AscendingOrder)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.itemDoubleClicked.connect(self._on_double)
        self.tree.setToolTip("Click a column header to sort; double-click an image to open it")
        v.addWidget(self.tree, 1)
        self.relabel()

    # ------------------------------------------------------------------ labels
    def relabel(self) -> None:
        p = self.state.profile
        L = lambda k: hui.kind_label(p, k)  # noqa: E731
        heads = ["Image", L("project"), L("sample"), L("lot"), "Status", "Scan area",
                 "µm/px", "Grains", "Mean diam. (µm)", "ASTM G"]
        self.tree.setHeaderLabels(heads)
        hdr = self.tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(QHeaderView.ResizeToContents)
        cur = self.group.currentIndex()
        self.group.blockSignals(True)
        self.group.clear()
        self.group.addItem("Everything together", "")
        for k in ("project", "sample", "lot"):
            self.group.addItem(L(k), k)
        self.group.setCurrentIndex(cur if cur >= 0 else 3)
        self.group.blockSignals(False)

    def set_group_level(self, level: str) -> None:
        i = self.group.findData(level)
        if i >= 0:
            self.group.setCurrentIndex(i)

    def group_level(self) -> str:
        return self.group.currentData() or ""

    # ------------------------------------------------------------------ data
    def mark_dirty(self) -> None:
        self._dirty = True
        if self.isVisible():
            self._timer.start()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if self._dirty:
            self.rebuild()

    def rows(self) -> List[dict]:
        st = self.state
        doc = st.session
        out = []
        if doc is None:
            return out
        ids_cache: Dict[int, Dict[str, str]] = {}
        for im in st.images():
            rec = doc.record_for(im)
            key = id(rec)
            if key not in ids_cache:
                ids_cache[key] = level_ids(rec) if rec is not None else {}
            ids = ids_cache[key]
            r = im.result
            px = st.px_for(im)
            scan = st.scan_for(im)
            out.append(dict(uid=im.uid, image=im.display_name, project=ids.get("project", ""),
                            sample=ids.get("sample", ""), lot=ids.get("lot", ""),
                            status=("Analysed" if im.result is not None and
                                    im.status == "done" else image_status(im)[1]),
                            scan=(f"{scan[2]} × {scan[3]} px" if scan else "Not set"),
                            scale=(1.0 / px if px > 0 else None),
                            grains=(r.grain_count if r is not None else None),
                            diam=(r.mean_diameter_um if r is not None and r.has_calibration
                                  and r.mean_diameter_um else None),
                            g=(astm_g(r) if r is not None else None)))
        return out

    def rebuild(self) -> None:
        self._dirty = False
        rows = self.rows()
        level = self.group_level()
        sort_col = self.tree.sortColumn()
        order = self.tree.header().sortIndicatorOrder()
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        groups: Dict[str, QTreeWidgetItem] = {}
        members: Dict[str, List[dict]] = {}
        for row in rows:
            it = self._row_item(row)
            if level:
                key = row[level]
                g = groups.get(key)
                if g is None:
                    g = _Item()
                    g.setFirstColumnSpanned(False)
                    groups[key] = g
                    members[key] = []
                    self.tree.addTopLevelItem(g)
                g.addChild(it)
                members[key].append(row)
            else:
                self.tree.addTopLevelItem(it)
        L = hui.kind_label(self.state.profile, level) if level else ""
        for key, g in groups.items():
            self._fill_group(g, f"{L} {key}", members[key], level)
        self.tree.expandAll()
        self.tree.setSortingEnabled(True)
        self.tree.sortItems(sort_col if sort_col >= 0 else COL_IMAGE, order)
        done = sum(1 for r in rows if r["grains"] is not None)
        lots = len({(r["project"], r["sample"], r["lot"]) for r in rows})
        self.summary.setText(f"{len(rows)} images · {done} analysed · {lots} "
                             f"{hui.kind_label(self.state.profile, 'lot').lower()}"
                             f"{'s' if lots != 1 else ''}")

    def _row_item(self, row: dict) -> QTreeWidgetItem:
        it = _Item()
        it.setData(0, UID_ROLE, row["uid"])
        vals = [row["image"], row["project"], row["sample"], row["lot"], row["status"],
                row["scan"],
                f"{row['scale']:.4g}" if row["scale"] else "Not set",
                fmt_int(row["grains"]) if row["grains"] is not None else "—",
                f"{row['diam']:.2f}" if row["diam"] else "—",
                f"{row['g']:.1f}" if row["g"] is not None else "—"]
        for c, v in enumerate(vals):
            it.setText(c, v)
        for c, v in ((COL_SCALE, row["scale"]), (COL_GRAINS, row["grains"]),
                     (COL_DIAM, row["diam"]), (COL_G, row["g"])):
            it.setData(c, SORT_ROLE, v)
            it.setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
        it.setToolTip(COL_IMAGE, "Double-click to open this image")
        return it

    def _fill_group(self, g: QTreeWidgetItem, title: str, rows: List[dict], level: str) -> None:
        n = len(rows)
        done = [r for r in rows if r["grains"] is not None]
        grains = sum(r["grains"] for r in done)
        gs = [r["g"] for r in done if r["g"] is not None]
        ds = [r["diam"] for r in done if r["diam"]]
        g.setText(COL_IMAGE, f"{title}  ({n} image{'s' if n != 1 else ''})")
        first = rows[0] if rows else {}
        for c, k in ((COL_PROJECT, "project"), (COL_SAMPLE, "sample"), (COL_LOT, "lot")):
            if level in ("project", "sample", "lot") and \
                    GROUP_LEVELS.index(k) <= GROUP_LEVELS.index(level):
                g.setText(c, first.get(k, ""))
        g.setText(COL_STATUS, f"{len(done)} of {n} analysed")
        g.setText(COL_GRAINS, fmt_int(grains) if done else "—")
        g.setText(COL_DIAM, f"{sum(ds) / len(ds):.2f}" if ds else "—")
        g.setText(COL_G, f"{sum(gs) / len(gs):.1f}" if gs else "—")
        g.setData(COL_IMAGE, SORT_ROLE, None)
        for c, v in ((COL_GRAINS, grains if done else None),
                     (COL_DIAM, sum(ds) / len(ds) if ds else None),
                     (COL_G, sum(gs) / len(gs) if gs else None)):
            g.setData(c, SORT_ROLE, v)
            g.setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
        f = g.font(0)
        f.setBold(True)
        for c in range(self.tree.columnCount()):
            g.setFont(c, f)
        g.setToolTip(COL_IMAGE, "Mean diameter and ASTM G are the means of the analysed "
                                "images in this group")

    def group_items(self) -> List[QTreeWidgetItem]:
        return [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]

    def _on_double(self, it, _col) -> None:
        uid = it.data(0, UID_ROLE)
        if uid is not None:
            self.open_image.emit(uid)


__all__ = ["ResultsTable", "level_ids", "GROUP_LEVELS"]
