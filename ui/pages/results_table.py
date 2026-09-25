"""
Results table of the Analyze page (UX-09).

One row per image with its Job / Part / Lot (the workspace's own level
names), analysis status, scan area, scale and the headline results.  "Group
by" nests the rows under Job, Part or Lot (group rows carry the totals) or
shows everything together; every column sorts by clicking its header.  A
double-click opens the image.  The right end of the toolbar holds "Export
report…" (UX-13): one report over everything loaded, or over the lots of the
selected rows / groups -- opened in the report designer (edit charts,
palette, captions first) or exported straight to Excel / PowerPoint.
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
from ui.widgets import AnimatedButton, label

GROUP_LEVELS = ("", "project", "sample", "lot")      # "" = everything together
COL_IMAGE, COL_PROJECT, COL_SAMPLE, COL_LOT, COL_STATUS, COL_SCAN, COL_SCALE, \
    COL_GRAINS, COL_DIAM, COL_G = range(10)
# default look: results first, then status and set-up details
VISUAL_ORDER = (COL_IMAGE, COL_PROJECT, COL_SAMPLE, COL_LOT, COL_GRAINS, COL_DIAM, COL_G,
                COL_STATUS, COL_SCALE, COL_SCAN)
DEFAULT_WIDTHS = {COL_IMAGE: 140, COL_PROJECT: 80, COL_SAMPLE: 90, COL_LOT: 65,
                  COL_GRAINS: 55, COL_DIAM: 95, COL_G: 55, COL_STATUS: 90, COL_SCALE: 55,
                  COL_SCAN: 90}
# Job / Part columns hide themselves while every row has the same value
# (it is in the title and the image tree) unless the user shows them
AUTO_COLS = (COL_PROJECT, COL_SAMPLE)
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
    # UX-13: (scope = lot keys | None for everything, action =
    # "designer" | "xlsx" | "pptx" | "both")
    report_requested = Signal(object, str)

    def __init__(self, state, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self._dirty = True
        self._lot_of: Dict[object, str] = {}        # uid -> lot key
        self._analysed: set = set()
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
        # UX-13: multi-lot report, right-aligned next to the grouping controls
        self.actions = QHBoxLayout()
        self.actions.setSpacing(SPACE.sm)
        bar.addLayout(self.actions)
        self.report_btn = AnimatedButton("Export report…", "mdi6.file-chart-outline",
                                         "secondary", "sm")
        self.report_btn.setToolTip(
            "One report for everything loaded — or only the lots of the selected rows / "
            "groups: per-lot summary, lot comparison (ΔG and equivalence vs the baseline "
            "lot), charts, image pages and raw data. Open it in the report designer or "
            "export Excel / PowerPoint directly.")
        self.report_btn.clicked.connect(
            lambda: self.show_report_menu(self.report_btn.mapToGlobal(
                self.report_btn.rect().bottomLeft())))
        self.actions.addWidget(self.report_btn)
        self.columns_btn = AnimatedButton("Columns", "mdi6.table-column", "ghost", "sm")
        self.columns_btn.setToolTip("Show or hide columns (also: right-click a column header)")
        self.columns_btn.clicked.connect(
            lambda: self._columns_menu(self.columns_btn.mapToGlobal(
                self.columns_btn.rect().bottomLeft())))
        bar.addWidget(self.columns_btn)
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
        hdr = self.tree.header()
        hdr.setContextMenuPolicy(Qt.CustomContextMenu)
        hdr.customContextMenuRequested.connect(lambda pos: self._columns_menu(
            hdr.mapToGlobal(pos)))
        hdr.setSectionsMovable(True)
        v.addWidget(self.tree, 1)
        self.relabel()
        # results first, setup details last; the last column fills the rest
        for vis, col in enumerate(VISUAL_ORDER):
            hdr.moveSection(hdr.visualIndex(col), vis)
        for col, w in DEFAULT_WIDTHS.items():
            hdr.resizeSection(col, w)
        self._apply_hidden()

    # ------------------------------------------------------------------ labels
    def relabel(self) -> None:
        p = self.state.profile
        L = lambda k: hui.kind_label(p, k)  # noqa: E731
        heads = ["Image", L("project"), L("sample"), L("lot"), "Status", "Scan area",
                 "µm/px", "Grains", "Mean diam. (µm)", "ASTM G"]
        self.tree.setHeaderLabels(heads)
        hdr = self.tree.header()
        hdr.setStretchLastSection(True)
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setMinimumSectionSize(48)
        cur = self.group.currentIndex()
        self.group.blockSignals(True)
        self.group.clear()
        self.group.addItem("Everything together", "")
        for k in ("project", "sample", "lot"):
            self.group.addItem(L(k), k)
        self.group.setCurrentIndex(cur if cur >= 0 else 3)
        self.group.blockSignals(False)

    # ------------------------------------------------------------------ columns
    def _ui_list(self, key: str) -> List[int]:
        v = self.state.ui_state.get(key)
        return [int(c) for c in v] if isinstance(v, list) else []

    def _apply_hidden(self, rows: Optional[List[dict]] = None) -> None:
        hidden = set(self._ui_list("results_table_hidden"))
        shown = set(self._ui_list("results_table_shown"))
        if rows is not None:
            self._redundant = {c for c, k in ((COL_PROJECT, "project"), (COL_SAMPLE, "sample"))
                               if len({r[k] for r in rows}) <= 1}
        for c in range(self.tree.columnCount()):
            auto = c in getattr(self, "_redundant", set(AUTO_COLS)) and c not in shown
            self.tree.setColumnHidden(c, c != COL_IMAGE and (c in hidden or auto))

    def set_column_visible(self, col: int, on: bool) -> None:
        """Show / hide a column (remembered in the local ui_state)."""
        if col == COL_IMAGE:
            return                          # the image name always shows
        hidden = set(self._ui_list("results_table_hidden"))
        shown = set(self._ui_list("results_table_shown"))
        (shown.add if on else shown.discard)(col)
        (hidden.discard if on else hidden.add)(col)
        self.state.ui_state["results_table_hidden"] = sorted(hidden)
        self.state.ui_state["results_table_shown"] = sorted(shown)
        self.state.persist_ui_state()
        self._apply_hidden()

    def _columns_menu(self, gpos) -> None:
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        hdr = self.tree.header()
        for vis in range(hdr.count()):
            col = hdr.logicalIndex(vis)
            a = m.addAction(self.tree.headerItem().text(col))
            a.setCheckable(True)
            a.setChecked(not self.tree.isColumnHidden(col))
            a.setEnabled(col != COL_IMAGE)
            a.toggled.connect(lambda on, c=col: self.set_column_visible(c, on))
        m.exec(gpos)

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

    # ------------------------------------------------------------------ UX-13 report
    def selected_scope(self) -> Optional[List[str]]:
        """Lot keys of the selected rows / group rows (None = no selection:
        the report covers everything loaded)."""
        keys: List[str] = []
        for it in self.tree.selectedItems():
            uid = it.data(0, UID_ROLE)
            uids = [uid] if uid is not None else \
                [it.child(i).data(0, UID_ROLE) for i in range(it.childCount())]
            for u in uids:
                k = self._lot_of.get(u)
                if k is not None and k not in keys:
                    keys.append(k)
        return keys or None

    def scope_summary(self, scope: Optional[List[str]]) -> tuple:
        """(lots, analysed images) a report over ``scope`` would cover."""
        want = set(scope) if scope else None
        uids = [u for u, k in self._lot_of.items() if want is None or k in want]
        done = [u for u in uids if u in self._analysed]
        lots = {self._lot_of[u] for u in done}
        return len(lots), len(done)

    def show_report_menu(self, gpos=None) -> Optional[object]:
        from PySide6.QtWidgets import QMenu
        from ui.design.icons import icon as _icon
        if self._dirty:
            self.rebuild()
        scope = self.selected_scope()
        n_lots, n_imgs = self.scope_summary(scope)
        lot_w = hui.kind_label(self.state.profile, "lot").lower()
        m = QMenu(self)
        m.setToolTipsVisible(True)
        if n_imgs:
            what = "Selected" if scope else "Everything loaded"
            head = m.addAction(f"{what}: {n_lots} {lot_w}{'s' if n_lots != 1 else ''} · "
                               f"{n_imgs} analysed image{'s' if n_imgs != 1 else ''}")
        else:
            head = m.addAction("Analyse images first — nothing to report yet")
        head.setEnabled(False)
        m.addSeparator()
        items = (("designer", "mdi6.pencil-ruler", "Open in report designer…",
                  "Edit charts, palette, captions and sections, then export"),
                 ("xlsx", "mdi6.microsoft-excel", "Export Excel workbook",
                  "Write the .xlsx to the exports folder now"),
                 ("pptx", "mdi6.microsoft-powerpoint", "Export PowerPoint deck",
                  "Write the .pptx to the exports folder now"),
                 ("both", "mdi6.file-multiple-outline", "Export Excel + PowerPoint",
                  "Write both files to the exports folder now"))
        for n, (act, ic, text, tip) in enumerate(items):
            if n == 1:
                m.addSeparator()
            try:
                a = m.addAction(_icon(ic), text)
            except Exception:           # noqa: BLE001 -- icon font missing
                a = m.addAction(text)
            a.setToolTip(tip)
            a.setEnabled(bool(n_imgs))
            a.triggered.connect(lambda _c=False, x=act, sc=scope:
                                self.report_requested.emit(sc, x))
        self._report_menu = m
        if gpos is not None:
            m.exec(gpos)
        return m

    def set_report_busy(self, on: bool) -> None:
        self.report_btn.set_loading(bool(on))

    def rebuild(self) -> None:
        self._dirty = False
        rows = self.rows()
        from ui.pages.report_builder import lot_key
        self._lot_of = {r["uid"]: lot_key(r) for r in rows}
        self._analysed = {r["uid"] for r in rows if r["grains"] is not None}
        self.report_btn.setEnabled(bool(self._analysed))
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
        self._apply_hidden(rows)
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
