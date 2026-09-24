"""
Review page (UI-05-lite, DET-03 UI).

Layout
  left   : compact filmstrip
  centre : toolbar (Original / Overlay / Mask / Excluded, undo/redo, remove,
           zoom) · canvas · "All images in this session" comparison table
  right  : StatCards · grain filters · tabs (area / diameter histograms,
           grain table, full statistics)
Selection is two-way: click a grain on the canvas ↔ its row in the table.
Delete removes the selection (undoable; non-destructive manual exclusion).
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np
from PySide6.QtCore import (
    QAbstractTableModel, QItemSelection, QItemSelectionModel, QModelIndex, QSortFilterProxyModel,
    Qt, Signal,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QGridLayout, QHBoxLayout, QHeaderView, QSpinBox, QSplitter,
    QTableView, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from ui.canvas import GrainCanvas
from ui.design.tokens import SPACE
from ui.filtering import REASON_LABELS
from ui.format import (
    area_value, astm_g, diam_value, fmt_int, fmt_opt, smart_format, units_for,
)
from ui.pages.charts import ThemedHistogram
from ui.pages.common import MetricCard, Panel, scroll
from ui.pages.filmstrip import Filmstrip, status_text
from ui.pages.filter_card import FilterCard, Reveal
from ui.widgets import (
    AnimatedButton, Card, EmptyState, FadeStackedWidget, IconButton, KeyValueList,
    SegmentedControl, label,
)

VIEW_KEYS = ("original", "overlay", "mask", "excluded")
SORT_ROLE = Qt.UserRole + 10
ID_ROLE = Qt.UserRole + 11


class GrainTableModel(QAbstractTableModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list = []     # (grain, reasons|None)
        self._result = None
        self._headers = ["ID", "Area", "Diameter", "Circularity", "Aspect", "Status"]

    def set_data(self, result, raw=None, excluded=None, show_excluded=False) -> None:
        self.beginResetModel()
        self._result = result
        rows = [(g, None) for g in (result.grains if result else [])]
        if show_excluded and raw is not None and excluded:
            by = {g.grain_id: g for g in raw.grains}
            rows += [(by[i], r) for i, r in excluded.items() if i in by]
        rows.sort(key=lambda t: t[0].grain_id)
        self._rows = rows
        au, _, du, _ = units_for(result)
        self._headers = ["ID", f"Area ({au})", f"Diameter ({du})", "Circularity", "Aspect ratio",
                         "Status"]
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else 6

    def headerData(self, s, o, role=Qt.DisplayRole):  # noqa: N802
        if o == Qt.Horizontal and role == Qt.DisplayRole:
            return self._headers[s]
        return None

    def grain_id(self, row: int) -> int:
        return self._rows[row][0].grain_id

    def row_of(self, gid: int) -> int:
        for i, (g, _r) in enumerate(self._rows):
            if g.grain_id == gid:
                return i
        return -1

    def data(self, idx, role=Qt.DisplayRole):
        if not idx.isValid():
            return None
        g, reasons = self._rows[idx.row()]
        c = idx.column()
        r = self._result
        vals = (g.grain_id, area_value(r, g), diam_value(r, g), g.circularity, g.aspect_ratio,
                0 if reasons is None else 1)
        if role == SORT_ROLE:
            return vals[c]
        if role == ID_ROLE:
            return g.grain_id
        if role == Qt.DisplayRole:
            if c == 0:
                return str(g.grain_id)
            if c in (1, 2):
                return smart_format(vals[c])
            if c == 3:
                return f"{g.circularity:.3f}"
            if c == 4:
                return f"{g.aspect_ratio:.2f}"
            return "Kept" if reasons is None else "Excluded — " + ", ".join(
                REASON_LABELS.get(x, x).split(" —")[0] for x in reasons)
        if role == Qt.TextAlignmentRole:
            return int(Qt.AlignLeft | Qt.AlignVCenter) if c == 5 else int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole and reasons:
            return "; ".join(REASON_LABELS.get(x, x) for x in reasons)
        if role == Qt.ForegroundRole and reasons:
            from ui.widgets._base import qcolor, tokens
            return qcolor(tokens().text.tertiary)
        return None


class ReviewPage(QWidget):
    export_requested = Signal()
    open_projects_requested = Signal()
    analyze_requested = Signal()

    def __init__(self, state, toasts=None, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.toasts = toasts
        self._syncing = False
        self._build()
        self._wire()

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.stack = FadeStackedWidget()
        outer.addWidget(self.stack)
        self.empty = EmptyState("review", "Nothing to review yet",
                                "Open a session from Projects, or analyse images on the "
                                "Analyze page — results appear here.",
                                "Open from Projects", "projects")
        self.empty.action_triggered.connect(self.open_projects_requested)
        self.stack.addWidget(self.empty)

        content = QWidget()
        h = QHBoxLayout(content)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        fp = Panel("right")
        fl = QVBoxLayout(fp)
        fl.setContentsMargins(0, 0, 0, 0)
        self.film = Filmstrip(compact=True)
        self.film.setMinimumWidth(170)
        self.film.setMaximumWidth(200)
        fl.addWidget(self.film)
        h.addWidget(fp)

        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        top = QWidget()
        tv = QVBoxLayout(top)
        tv.setContentsMargins(SPACE.lg, SPACE.md, SPACE.lg, SPACE.sm)
        tv.setSpacing(SPACE.sm)
        tb = QHBoxLayout()
        tb.setSpacing(SPACE.sm)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.img_title = label("", "h3")
        self.img_sub = label("", "caption")
        col.addWidget(self.img_title)
        col.addWidget(self.img_sub)
        tb.addLayout(col, 1)
        self.view_seg = SegmentedControl(["Original", "Overlay", "Mask", "Excluded"], 1)
        self.view_seg.setToolTip("Original image · grain overlay · grain mask · areas not analysed")
        self.view_seg.setFixedWidth(330)
        tb.addWidget(self.view_seg)
        self.btn_undo = IconButton("undo", "Undo (Ctrl+Z)")
        self.btn_redo = IconButton("redo", "Redo (Ctrl+Y)")
        self.btn_del = AnimatedButton("Remove", "delete", "ghost", "sm")
        self.btn_del.setToolTip("Remove the selected grains from the results (Delete). "
                                "Undo with Ctrl+Z.")
        self.btn_del.setEnabled(False)
        tb.addWidget(self.btn_undo)
        tb.addWidget(self.btn_redo)
        tb.addWidget(self.btn_del)
        self.btn_zo = IconButton("zoom_out", "Zoom out (−)")
        self.btn_zi = IconButton("zoom_in", "Zoom in (+)")
        self.btn_fit = IconButton("fit", "Fit to window (F)")
        self.btn_11 = IconButton("target", "Actual pixels, 1:1 (1)")
        for b in (self.btn_zo, self.btn_zi, self.btn_fit, self.btn_11):
            tb.addWidget(b)
        tv.addLayout(tb)
        self.canvas = GrainCanvas(placeholder="Select an analysed image")
        tv.addWidget(self.canvas, 1)
        hint = label("Click a grain to select · Ctrl+click adds · Delete removes · "
                     "drag to pan · wheel zooms about the cursor · minimap bottom-right", "caption")
        tv.addWidget(hint)
        split.addWidget(top)

        bottom = QWidget()
        bv = QVBoxLayout(bottom)
        bv.setContentsMargins(SPACE.lg, SPACE.sm, SPACE.lg, SPACE.md)
        cmp_card = Card("All images in this session",
                        "One row per image — click a row to open that image")
        self.btn_export = AnimatedButton("Export report", "excel", "secondary", "sm")
        self.btn_export.setToolTip("Excel report of every analysed image, saved in the session's "
                                   "exports folder (Ctrl+E). Edit it on the Reports page.")
        self.btn_export.clicked.connect(self.export_requested)
        cmp_card.add_action(self.btn_export)
        self.cmp = QTableWidget(0, 10)
        self.cmp.setHorizontalHeaderLabels(["Image", "Status", "Grains", "Excluded", "Mean area",
                                            "Mean diameter", "Coverage", "Not analysed",
                                            "ASTM G", "Scale"])
        self.cmp.verticalHeader().setVisible(False)
        self.cmp.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.cmp.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.cmp.setSelectionMode(QAbstractItemView.SingleSelection)
        self.cmp.setAlternatingRowColors(True)
        self.cmp.setShowGrid(False)
        self.cmp.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.cmp.horizontalHeader().setStretchLastSection(True)
        self.cmp.setToolTip("Every image of the session side by side")
        cmp_card.add_widget(self.cmp, 1)
        bv.addWidget(cmp_card)
        split.addWidget(bottom)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([620, 240])
        h.addWidget(split, 1)

        side = Panel("left")
        side.setMinimumWidth(400)
        side.setMaximumWidth(460)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 0)
        inner = QWidget()
        iv = QVBoxLayout(inner)
        iv.setContentsMargins(SPACE.lg, SPACE.lg, SPACE.lg, SPACE.lg)
        iv.setSpacing(SPACE.md)
        g = QGridLayout()
        g.setSpacing(SPACE.sm)
        self.c_count = MetricCard("Grains", 0, "", 0)
        self.c_area = MetricCard("Mean area", 0, "µm²", 2)
        self.c_diam = MetricCard("Mean diameter", 0, "µm", 2)
        self.c_cov = MetricCard("Coverage", 0, "%", 1)
        self.c_inv = MetricCard("Not analysed", 0, "%", 1)
        self.c_g = MetricCard("ASTM grain size", 0, "G", 1)
        self.c_cov.setToolTip("Grain area ÷ analysed (valid) area")
        self.c_inv.setToolTip("Share of the frame excluded from analysis (black regions, "
                              "outside the scan area)")
        self.c_g.setToolTip("ASTM E112 grain size number (needs a calibrated image)")
        for i, c in enumerate((self.c_count, self.c_area, self.c_diam, self.c_cov, self.c_inv,
                               self.c_g)):
            g.addWidget(c, i // 2, i % 2)
        iv.addLayout(g)
        self.filters = FilterCard()
        self.filters_host = Reveal(self.filters)
        iv.addWidget(self.filters_host)

        tabs_card = Card()
        self.tabs = QTabWidget()
        self.tabs.setMinimumHeight(360)
        # area
        aw = QWidget()
        al = QVBoxLayout(aw)
        al.setContentsMargins(0, SPACE.sm, 0, 0)
        ab = QHBoxLayout()
        ab.addWidget(label("Bins", tone="secondary"))
        self.bins_area = QSpinBox()
        self.bins_area.setRange(0, 100)
        self.bins_area.setSpecialValueText("Auto")   # B16: 0 = automatic, no silent clamp
        self.bins_area.setToolTip("Number of histogram bins (Auto = square-root rule)")
        ab.addWidget(self.bins_area)
        ab.addStretch(1)
        al.addLayout(ab)
        self.hist_area = ThemedHistogram(series=0)
        al.addWidget(self.hist_area, 1)
        self.tabs.addTab(aw, "Area")
        # diameter
        dw = QWidget()
        dl = QVBoxLayout(dw)
        dl.setContentsMargins(0, SPACE.sm, 0, 0)
        db = QHBoxLayout()
        db.addWidget(label("Bins", tone="secondary"))
        self.bins_diam = QSpinBox()
        self.bins_diam.setRange(0, 100)
        self.bins_diam.setSpecialValueText("Auto")
        self.bins_diam.setToolTip("Number of histogram bins (Auto = square-root rule)")
        db.addWidget(self.bins_diam)
        db.addStretch(1)
        dl.addLayout(db)
        self.hist_diam = ThemedHistogram(series=2)
        dl.addWidget(self.hist_diam, 1)
        self.tabs.addTab(dw, "Diameter")
        # grains
        gw = QWidget()
        gl = QVBoxLayout(gw)
        gl.setContentsMargins(0, SPACE.sm, 0, 0)
        self.show_ex_rows = QCheckBox("Include excluded grains (with reason)")
        self.show_ex_rows.setToolTip("List grains removed by filters or by hand as well")
        gl.addWidget(self.show_ex_rows)
        self.gmodel = GrainTableModel(self)
        self.gproxy = QSortFilterProxyModel(self)
        self.gproxy.setSourceModel(self.gmodel)
        self.gproxy.setSortRole(SORT_ROLE)
        self.gtable = QTableView()
        self.gtable.setModel(self.gproxy)
        self.gtable.setSortingEnabled(True)
        self.gtable.sortByColumn(0, Qt.AscendingOrder)
        self.gtable.verticalHeader().setVisible(False)
        self.gtable.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.gtable.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.gtable.setAlternatingRowColors(True)
        self.gtable.setShowGrid(False)
        self.gtable.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.gtable.horizontalHeader().setStretchLastSection(True)
        self.gtable.setToolTip("Click a row to highlight the grain; Ctrl/Shift for several; "
                               "Delete removes them")
        gl.addWidget(self.gtable, 1)
        self.tabs.addTab(gw, "Grains")
        # statistics
        self.stats_kv = KeyValueList(mono_keys=())
        self.tabs.addTab(scroll(self._wrap(self.stats_kv)), "Statistics")
        tabs_card.add_widget(self.tabs, 1)
        iv.addWidget(tabs_card, 1)
        sv.addWidget(scroll(inner))
        h.addWidget(side)
        self.stack.addWidget(content)

    @staticmethod
    def _wrap(w: QWidget) -> QWidget:
        host = QWidget()
        v = QVBoxLayout(host)
        v.setContentsMargins(SPACE.sm, SPACE.md, SPACE.sm, SPACE.md)
        v.addWidget(w)
        v.addStretch(1)
        return host

    def _wire(self) -> None:
        st = self.state
        st.session_opened.connect(self._on_session)
        st.session_closed.connect(self._on_session)
        st.images_changed.connect(self._on_images)
        st.image_updated.connect(self._on_image_updated)
        st.result_edited.connect(self._on_result_edited)
        st.current_image_changed.connect(self._on_current)
        st.filters_changed.connect(self._refresh_filters)
        st.filtering_changed.connect(
            lambda uid, busy: uid == st.current_uid and self.filters.set_busy(busy))
        self.film.current_changed.connect(st.set_current_image)
        self.film.files_dropped.connect(st.add_images)
        self.view_seg.current_changed.connect(lambda i: self.canvas.set_view(VIEW_KEYS[i]))
        self.canvas.view_changed.connect(self._sync_view)
        self.canvas.selection_changed.connect(self._on_canvas_selection)
        self.canvas.delete_requested.connect(self.delete_selected)
        self.btn_del.clicked.connect(self.delete_selected)
        self.btn_undo.clicked.connect(st.undo_stack.undo)
        self.btn_redo.clicked.connect(st.undo_stack.redo)
        st.undo_stack.canUndoChanged.connect(self.btn_undo.setEnabled)
        st.undo_stack.canRedoChanged.connect(self.btn_redo.setEnabled)
        self.btn_undo.setEnabled(False)
        self.btn_redo.setEnabled(False)
        self.btn_zo.clicked.connect(lambda: self.canvas.zoom_by(0.8))
        self.btn_zi.clicked.connect(lambda: self.canvas.zoom_by(1.25))
        self.btn_fit.clicked.connect(self.canvas.fit)
        self.btn_11.clicked.connect(self.canvas.actual_size)
        self.cmp.itemSelectionChanged.connect(self._on_cmp_selected)
        self.gtable.selectionModel().selectionChanged.connect(self._on_table_selection)
        self.show_ex_rows.toggled.connect(lambda _v: self._fill_grain_table())
        self.bins_area.valueChanged.connect(lambda v: self.hist_area.set_bin_count(v))
        self.bins_diam.valueChanged.connect(lambda v: self.hist_diam.set_bin_count(v))
        self.filters.options_changed.connect(
            lambda o, scope: st.set_filter_options(o, st.current_uid if scope == "image" else None))
        self.filters.apply_all_requested.connect(st.apply_filters_to_all)
        self.filters.show_excluded_toggled.connect(self.canvas.set_show_excluded_grains)

    # ------------------------------------------------------------------ sync
    def _on_session(self) -> None:
        self.stack.set_current_index(1 if self.state.session is not None else 0)
        self._on_images()

    def _on_images(self) -> None:
        self.film.set_images(self.state.images())
        self.film.set_current(self.state.current_uid)
        self._fill_comparison()
        self._on_current(self.state.current_uid)

    def _on_image_updated(self, uid) -> None:
        self.film.update_item(uid)
        self._fill_comparison()
        im = self.state.current_image()
        if uid == self.state.current_uid and im is not None and (
                self.canvas.result() is not im.result or self.canvas.excluded() != im.excluded):
            self._show_current(keep_view=True)

    def _on_result_edited(self, uid) -> None:
        self._fill_comparison()
        im = self.state.current_image()
        if uid == self.state.current_uid and im is not None and (
                self.canvas.result() is not im.result or self.canvas.excluded() != im.excluded):
            self._show_current(keep_view=True)

    def _on_current(self, uid) -> None:
        self.film.set_current(uid)
        self._select_cmp_row(uid)
        im = self.state.current_image()
        if im is None:
            self.canvas.set_image(None)
            self._show_stats(None)
            return
        self.canvas.set_image(im.image_bgr, im.result, raw=im.raw, excluded=im.excluded)
        self.canvas.set_scan_rect(self.state.scan_for(im))
        self.canvas.set_view(VIEW_KEYS[self.view_seg.current_index()]
                             if im.result is not None else "original")
        self._show_current(keep_view=True, image_changed=True)

    def _show_current(self, keep_view=True, image_changed=False) -> None:
        im = self.state.current_image()
        if im is None:
            return
        if not image_changed:
            self.canvas.set_result(im.result, raw=im.raw, excluded=im.excluded)
        self.img_title.setText(im.filename)
        kind, text = status_text(im)
        extra = []
        if im.excluded:
            extra.append(f"{len(im.excluded)} excluded")
        self.img_sub.setText("  ·  ".join([text] + extra))
        self._show_stats(im)
        self._fill_grain_table()
        self._refresh_filters()

    def _sync_view(self, view: str) -> None:
        i = VIEW_KEYS.index(view) if view in VIEW_KEYS else 1
        if i != self.view_seg.current_index():
            self.view_seg.blockSignals(True)
            self.view_seg.set_current_index(i)
            self.view_seg.blockSignals(False)

    def _refresh_filters(self) -> None:
        im = self.state.current_image()
        has = im is not None and im.raw is not None
        self.filters_host.reveal(has)
        if has:
            self.filters.set_state(self.state.filter_options(im.uid), im.counts,
                                   self.state.px_for(im), self.state.has_override(im.uid),
                                   im.result.grain_count if im.result is not None else None)

    # ------------------------------------------------------------------ stats
    def _show_stats(self, im) -> None:
        r = im.result if im is not None else None
        cards = (self.c_count, self.c_area, self.c_diam, self.c_cov, self.c_inv, self.c_g)
        if r is None:
            for c in cards:
                c.set_metric(None, "")
            self.hist_area.clear_data()
            self.hist_diam.clear_data()
            self.stats_kv.set_items([("Status", "Not analysed yet")])
            return
        au, am, du, dm = units_for(r)
        self.c_count.set_metric(r.grain_count, "", 0)
        if r.has_calibration:
            self.c_area.set_metric(r.mean_area_um2 * am, au, 2)
            self.c_diam.set_metric(r.mean_diameter_um * dm, du, 2)
        else:
            areas = [g.area_px for g in r.grains]
            diams = [g.equivalent_diameter_px for g in r.grains]
            self.c_area.set_metric(float(np.mean(areas)) if areas else None, "px²", 0)
            self.c_diam.set_metric(float(np.mean(diams)) if diams else None, "px", 1)
        self.c_cov.set_metric(r.grain_coverage_pct, "%", 1)
        self.c_inv.set_metric(getattr(r, "invalid_area_pct", 0.0), "%", 1)
        g = astm_g(r)
        self.c_g.set_metric(g, "G" if g is not None else "needs calibration", 1)
        areas = np.array([area_value(r, x) for x in r.grains])
        diams = np.array([diam_value(r, x) for x in r.grains])
        self.hist_area.set_data(areas, f"Grain area ({au})", au, self.bins_area.value())
        self.hist_diam.set_data(diams, f"Grain diameter, ECD ({du})", du, self.bins_diam.value())
        rows = [("Grains counted", fmt_int(r.grain_count)),
                ("Excluded grains", fmt_int(len(im.excluded)))]
        if len(areas):
            rows += [(f"Mean area ({au})", smart_format(float(areas.mean()))),
                     (f"Std. dev. area ({au})", smart_format(float(areas.std()))),
                     (f"Median area ({au})", smart_format(float(np.median(areas)))),
                     (f"Min / max area ({au})", f"{smart_format(float(areas.min()))} / "
                                                f"{smart_format(float(areas.max()))}"),
                     (f"Mean diameter ({du})", smart_format(float(diams.mean()))),
                     (f"Std. dev. diameter ({du})", smart_format(float(diams.std())))]
        rows += [("Mean circularity", f"{r.mean_circularity:.3f}"),
                 ("Mean aspect ratio", f"{r.mean_aspect_ratio:.3f}"),
                 ("Grain coverage", f"{r.grain_coverage_pct:.2f} % of analysed area"),
                 ("Not analysed", f"{getattr(r, 'invalid_area_pct', 0.0):.2f} % of frame")]
        if r.has_calibration:
            rows += [("Analysed area", f"{smart_format(getattr(r, 'valid_area_um2', 0.0))} µm²"),
                     ("Calibration", f"{r.px_per_um:.4g} px/µm")]
        else:
            rows.append(("Calibration", "Not calibrated — sizes in pixels"))
        if g is not None:
            rows.append(("ASTM E112 grain size", f"G {g:.2f}"))
            method = (getattr(r, "astm", {}) or {}).get("method")
            if method:
                rows.append(("ASTM method", str(method)))
        self.stats_kv.set_items(rows)

    def _fill_grain_table(self) -> None:
        im = self.state.current_image()
        if im is None:
            self.gmodel.set_data(None)
            return
        self.gmodel.set_data(im.result, im.raw, im.excluded, self.show_ex_rows.isChecked())
        self._on_canvas_selection(self.canvas.selected())

    def _fill_comparison(self) -> None:
        imgs = self.state.images()
        self.cmp.blockSignals(True)
        self.cmp.setRowCount(len(imgs))
        for row, im in enumerate(imgs):
            r = im.result
            kind, text = status_text(im)
            vals = [im.filename, text if r is None else "Analysed"]
            if r is not None:
                au, am, du, dm = units_for(r)
                g = astm_g(r)
                if r.has_calibration:
                    area = f"{smart_format(r.mean_area_um2 * am)} {au}"
                    diam = f"{smart_format(r.mean_diameter_um * dm)} {du}"
                else:
                    ar = [x.area_px for x in r.grains]
                    dr = [x.equivalent_diameter_px for x in r.grains]
                    area = f"{smart_format(float(np.mean(ar)))} px²" if ar else "—"
                    diam = f"{smart_format(float(np.mean(dr)))} px" if dr else "—"
                vals += [fmt_int(r.grain_count), fmt_int(len(im.excluded)), area, diam,
                         f"{r.grain_coverage_pct:.1f} %",
                         f"{getattr(r, 'invalid_area_pct', 0.0):.1f} %",
                         fmt_opt(g, 1), f"{self.state.px_for(im):.4g} px/µm"
                         if self.state.px_for(im) > 0 else "—"]
            else:
                vals += ["—"] * 8
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(ID_ROLE, im.uid)
                if c >= 2:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.cmp.setItem(row, c, it)
        self.cmp.blockSignals(False)
        self._select_cmp_row(self.state.current_uid)

    def comparison_rows(self) -> int:
        return self.cmp.rowCount()

    def _select_cmp_row(self, uid) -> None:
        self.cmp.blockSignals(True)
        self.cmp.clearSelection()
        for row in range(self.cmp.rowCount()):
            it = self.cmp.item(row, 0)
            if it is not None and it.data(ID_ROLE) == uid:
                self.cmp.selectRow(row)
                break
        self.cmp.blockSignals(False)

    def _on_cmp_selected(self) -> None:
        items = self.cmp.selectedItems()
        if items:
            self.state.set_current_image(items[0].data(ID_ROLE))

    # ------------------------------------------------------------------ selection
    def _on_canvas_selection(self, ids: List[int]) -> None:
        self.btn_del.setEnabled(bool(ids))
        self.btn_del.setText(f"Remove {len(ids)}" if len(ids) > 1 else "Remove")
        if self._syncing:
            return
        self._syncing = True
        sm = self.gtable.selectionModel()
        sel = QItemSelection()
        first = None
        for gid in ids:
            r = self.gmodel.row_of(gid)
            if r < 0:
                continue
            src = self.gmodel.index(r, 0)
            p = self.gproxy.mapFromSource(src)
            sel.select(p, p.siblingAtColumn(5))
            first = first or p
        sm.select(sel, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        if first is not None:
            self.gtable.scrollTo(first)
        self._syncing = False

    def _on_table_selection(self, *_a) -> None:
        if self._syncing:
            return
        rows = {self.gproxy.mapToSource(i).row() for i in self.gtable.selectionModel().selectedRows()}
        ids = [self.gmodel.grain_id(r) for r in sorted(rows)]
        self._syncing = True
        self.canvas.select(ids, center=len(ids) == 1)
        self._syncing = False
        self.btn_del.setEnabled(bool(self.canvas.selected()))

    def delete_selected(self, ids=None) -> None:
        ids = list(ids) if ids else self.canvas.selected()
        if not ids:
            return
        if self.state.delete_grains(self.state.current_uid, ids):
            self.canvas.clear_selection()
            if self.toasts is not None:
                self.toasts.show_toast(f"Removed {len(ids)} grain{'s' if len(ids) != 1 else ''}",
                                       "Statistics updated. Press Ctrl+Z to undo.", "info",
                                       "Undo", self.state.undo_stack.undo)
