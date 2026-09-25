"""
Reports page — the in-app report designer (REP-05, REP-06, REP-07 UI).

Layout
  header : "Report designer" · save state · Rebuild · Reload saved ·
           Excel · PowerPoint · Export both · ⋯ (Save as…, open folder)
  banner : "Results changed since this report was built — Refresh numbers"
  body   : outline (sections + images, drag / tick)  |  live preview of the
           selected section (fade-through on switch)  |  inspector
States
  no session → empty · session without results → empty (go analyse) ·
  results but no report → "Build report" · building → skeleton ·
  report → designer.  report.json is autosaved (debounced) so a report stays
  editable after the app closes; exports run on a pool thread.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFileDialog, QGridLayout, QHBoxLayout, QMenu, QProgressBar, QSplitter, QToolButton,
    QVBoxLayout, QWidget,
)

from reports.model import ReportModel
from ui.design.tokens import SPACE
from ui.pages import report_builder as rb
from ui.pages.common import PageHeader, Panel, scroll
from ui.pages.report_inspector import ReportInspector
from ui.pages.report_outline import ReportOutline
from ui.pages.report_preview import ImagePreview, TextPreview, make_preview
from ui.pages.report_widgets import Banner, arr_thumb_qimage, file_thumb_qimage
from ui.widgets import (
    AnimatedButton, Badge, EmptyState, FadeStackedWidget, IconButton, Skeleton, label,
)
from ui.workers import run_task, serial_pool

KIND_TEXT = {"xlsx": "Excel workbook", "pptx": "PowerPoint deck"}
KIND_FILTER = {"xlsx": "Excel workbook (*.xlsx)", "pptx": "PowerPoint presentation (*.pptx)"}


class ReportsPage(QWidget):
    report_changed = Signal()          # model replaced (built / reloaded / refreshed)
    exported = Signal(list)            # paths written
    _rec = "session"                   # HIER-01: what one record is called ("lot")

    AUTOSAVE_MS = 800

    def __init__(self, state, toasts=None, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.toasts = toasts
        self.model: Optional[ReportModel] = None
        self._busy = ""                    # "", "build", "refresh", "load", "export"
        self._gen = 0                      # drops outcomes of superseded async work
        self._stale = False
        self._self_edits: set = set()      # uids whose grains were toggled here
        self._own_cmds: list = []          # undo commands pushed from this page
        self._undo_index = 0
        self._pix: Dict[tuple, tuple] = {}
        self._pix_pending: set = set()   # UX-12: keys with a decode already in flight
        self._after_ready: List[Callable[[], None]] = []
        self._current_key = None
        self._model_session = None
        self._save_pending = False
        self._saving = False
        self._build()
        self._wire()
        self._update_view()

    # ================================================================== build
    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        top = QWidget()
        tv = QVBoxLayout(top)
        tv.setContentsMargins(SPACE.xl, SPACE.lg, SPACE.xl, SPACE.md)
        tv.setSpacing(SPACE.sm)
        self.header = PageHeader("Reports", "Report designer", "")
        self.save_badge = Badge("", "neutral", icon="save")
        self.save_badge.setToolTip("Report edits are saved to the session automatically "
                                   "(report.json)")
        self.save_badge.hide()
        self.issues_badge = Badge("", "warning", dot=True)
        self.issues_badge.setToolTip("Problems found by the report checks — see Checks on the right")
        self.issues_badge.hide()
        self.btn_rebuild = AnimatedButton("Rebuild", "refresh", "ghost", "sm")
        self.btn_rebuild.setToolTip("Start over: a fresh report from the session's current "
                                    "results (discards report edits)")
        self.btn_reload = AnimatedButton("Reload saved", "history", "ghost", "sm")
        self.btn_reload.setToolTip("Reload the report last saved with this session")
        self.btn_xlsx = AnimatedButton("Excel", "excel", "secondary", "sm")
        self.btn_xlsx.setToolTip("Export the Excel workbook to the session's exports folder")
        self.btn_pptx = AnimatedButton("PowerPoint", "powerpoint", "secondary", "sm")
        self.btn_pptx.setToolTip("Export the PowerPoint deck to the session's exports folder")
        self.btn_both = AnimatedButton("Export both", "export", "primary", "sm")
        self.btn_both.setToolTip("Export the workbook and the deck together")
        self.btn_more = IconButton("more", "More export options", size=28)
        self.btn_more.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.btn_more)
        self.act_xlsx_as = menu.addAction("Save Excel workbook as…")
        self.act_pptx_as = menu.addAction("Save PowerPoint deck as…")
        menu.addSeparator()
        self.act_folder = menu.addAction("Open the exports folder")
        self.btn_more.setMenu(menu)
        for w in (self.save_badge, self.issues_badge, self.btn_rebuild, self.btn_reload,
                  self.btn_xlsx, self.btn_pptx, self.btn_both, self.btn_more):
            self.header.actions.addWidget(w, 0, Qt.AlignVCenter)
        tv.addWidget(self.header)

        self.banner = Banner("warning")
        self.banner_btn = self.banner.add_action(
            "Refresh numbers", "refresh", lambda: self.refresh_numbers(), "primary")
        self.banner_btn.setToolTip("Recalculate every number from the current results — "
                                   "your captions, order and settings are kept")
        tv.addWidget(self.banner)
        prow = QHBoxLayout()
        prow.setSpacing(SPACE.md)
        self.progress_lbl = label("", "caption")
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        prow.addWidget(self.progress_lbl)
        prow.addWidget(self.progress, 1)
        self.progress_host = QWidget()
        self.progress_host.setLayout(prow)
        self.progress_host.hide()
        tv.addWidget(self.progress_host)
        v.addWidget(top)

        self.stack = FadeStackedWidget()
        v.addWidget(self.stack, 1)
        self.empty_session = EmptyState(
            "reports", "No session open",
            "Open a session from Projects to design its report — editable titles, captions, "
            "sections and image order, exported to Excel and PowerPoint.",
            "Open from Projects", "projects")
        self.empty_results = EmptyState(
            "analyze", "Nothing to report yet",
            "Analyse the session's images first; the report is built from the filtered "
            "results you see on the Review page.", None)
        self.empty_build = EmptyState(
            "reports", "Build the report",
            "Creates an editable report from this session's filtered results: an overview "
            "table with one row per image, summary charts, one page per image, methods and "
            "raw data (always last). Nothing is exported until you choose to.",
            "Build report from session", "add")
        for w in (self.empty_session, self.empty_results, self.empty_build):
            self.stack.addWidget(w)
        self.skeleton = self._skeleton()
        self.stack.addWidget(self.skeleton)

        self.designer = QSplitter(Qt.Horizontal)
        self.designer.setChildrenCollapsible(False)
        left = Panel("right")
        lv = QVBoxLayout(left)
        lv.setContentsMargins(SPACE.md, SPACE.md, SPACE.md, SPACE.md)
        lv.setSpacing(SPACE.sm)
        lh = QHBoxLayout()
        lh.addWidget(label("OUTLINE", "overline"), 1)
        self.btn_up = IconButton("arrow_up", "Move up (Alt+Up)", size=26)
        self.btn_down = IconButton("arrow_down", "Move down (Alt+Down)", size=26)
        lh.addWidget(self.btn_up)
        lh.addWidget(self.btn_down)
        lv.addLayout(lh)
        self.outline = ReportOutline()
        lv.addWidget(self.outline, 1)
        self.btn_add_text = AnimatedButton("Add text section", "add", "ghost", "sm")
        self.btn_add_text.setToolTip("Add a slide of free text (conclusions, preparation, "
                                     "acceptance criteria…) after the selected section")
        lv.addWidget(self.btn_add_text)
        hint = label("Colours match the workbook's sheet tabs. Raw data always comes last.",
                     "caption")
        hint.setWordWrap(True)
        lv.addWidget(hint)
        left.setMinimumWidth(230)
        left.setMaximumWidth(320)
        self.designer.addWidget(left)

        self.preview_stack = FadeStackedWidget()
        self.designer.addWidget(self.preview_stack)
        right = Panel("left")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.inspector = ReportInspector(self)
        rv.addWidget(scroll(self.inspector))
        right.setMinimumWidth(340)
        right.setMaximumWidth(420)
        self.designer.addWidget(right)
        self.designer.setStretchFactor(1, 1)
        self.designer.setSizes([260, 900, 360])
        self.stack.addWidget(self.designer)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(self.AUTOSAVE_MS)
        self._save_timer.timeout.connect(self.save_now)
        self._check_timer = QTimer(self)
        self._check_timer.setSingleShot(True)
        self._check_timer.setInterval(350)
        self._check_timer.timeout.connect(self._check_stale)
        self._validate_timer = QTimer(self)
        self._validate_timer.setSingleShot(True)
        self._validate_timer.setInterval(400)
        self._validate_timer.timeout.connect(self.validate)

    def _skeleton(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(SPACE.xl, SPACE.lg, SPACE.xl, SPACE.xl)
        g.setHorizontalSpacing(SPACE.xl)
        col = QVBoxLayout()
        col.setSpacing(SPACE.md)
        for i in range(9):
            col.addWidget(Skeleton(width=200 - (i % 3) * 30, shape="text"))
        col.addStretch(1)
        g.addLayout(col, 0, 0)
        mid = QVBoxLayout()
        mid.setSpacing(SPACE.md)
        mid.addWidget(Skeleton(width=260, shape="text"))
        mid.addWidget(Skeleton(height=380))
        row = QHBoxLayout()
        for _ in range(4):
            row.addWidget(Skeleton(height=64))
        mid.addLayout(row)
        mid.addStretch(1)
        g.addLayout(mid, 0, 1)
        col2 = QVBoxLayout()
        col2.setSpacing(SPACE.md)
        for i in range(8):
            col2.addWidget(Skeleton(width=240 - (i % 2) * 60, height=24 if i % 2 else 12,
                                    shape="rect" if i % 2 else "text"))
        col2.addStretch(1)
        g.addLayout(col2, 0, 2)
        g.setColumnStretch(1, 1)
        self.skeleton_caption = label("Building the report…", "caption")
        g.addWidget(self.skeleton_caption, 1, 1, Qt.AlignHCenter)
        return w

    def _wire(self) -> None:
        st = self.state
        st.session_opened.connect(self._on_session_opened)
        st.session_closed.connect(self._on_session_closed)
        for sig in (st.result_edited, st.image_updated):
            sig.connect(lambda _uid: self._check_timer.start())
        st.images_changed.connect(lambda: (self._check_timer.start(), self._update_view()))
        st.about_to_flush.connect(self.flush)
        st.profile_changed.connect(self.on_profile_changed)
        self._relabel()
        st.undo_stack.indexChanged.connect(self._on_undo_index)
        self.empty_build.action_triggered.connect(self.build_from_session)
        self.btn_rebuild.clicked.connect(self._ask_rebuild)
        self.btn_reload.clicked.connect(self.reload_saved)
        self.btn_xlsx.clicked.connect(lambda: self.export(["xlsx"]))
        self.btn_pptx.clicked.connect(lambda: self.export(["pptx"]))
        self.btn_both.clicked.connect(lambda: self.export(["xlsx", "pptx"]))
        self.act_xlsx_as.triggered.connect(lambda: self.export(["xlsx"], ask=True))
        self.act_pptx_as.triggered.connect(lambda: self.export(["pptx"], ask=True))
        self.act_folder.triggered.connect(self.open_exports_folder)
        self.outline.selection_key_changed.connect(self._on_select)
        self.outline.order_changed.connect(self._on_order)
        self.outline.toggled.connect(self._on_toggled)
        self.btn_up.clicked.connect(lambda: self.move_selected(-1))
        self.btn_down.clicked.connect(lambda: self.move_selected(1))
        self.btn_add_text.clicked.connect(self.add_text_section)
        self.inspector.doc_changed.connect(self._on_doc_changed)
        self.preview_stack.transition_finished.connect(lambda _i: self._drop_old_previews())

    # ================================================================== view state
    def is_busy(self) -> bool:
        return bool(self._busy)

    def _update_view(self) -> None:
        s = self.state.session
        has_model = self.model is not None
        if s is None:
            self.header.subtitle.setText(f"Open a {self._rec} to design its report")
            target = self.empty_session
        elif self._busy in ("build", "load") and not has_model:
            target = self.skeleton
        elif has_model:
            target = self.designer
        elif rb.analysed_count(self.state) == 0:
            target = self.empty_results
        else:
            target = self.empty_build
        if s is not None:
            n = len(self.model.ordered_images()) if has_model else rb.analysed_count(self.state)
            self.header.subtitle.setText(
                f"{s.title} — {n} image{'s' if n != 1 else ''} in the report" if has_model
                else f"{s.title} — {n} analysed image{'s' if n != 1 else ''}")
        self.header.subtitle.setVisible(True)
        self.stack.set_current_widget(target)
        busy = bool(self._busy)
        for b in (self.btn_xlsx, self.btn_pptx, self.btn_both, self.btn_more):
            b.setEnabled(has_model and not busy)
        self.btn_rebuild.setEnabled(s is not None and rb.analysed_count(self.state) > 0 and not busy)
        self.btn_reload.setEnabled(s is not None and not busy)
        self.btn_rebuild.setVisible(has_model)
        self.btn_reload.setVisible(s is not None)
        self.banner_btn.setEnabled(not busy)
        if not has_model:
            self.banner.hide_banner()
            self.save_badge.hide()
            self.issues_badge.hide()

    def _set_busy(self, what: str, text: str = "", determinate: Optional[Tuple[int, int]] = None
                  ) -> None:
        self._busy = what
        if what:
            self.progress_lbl.setText(text)
            if determinate:
                self.progress.setRange(0, determinate[1])
                self.progress.setValue(determinate[0])
            else:
                self.progress.setRange(0, 0)
            self.progress_host.show()
        else:
            self.progress_host.hide()
        self._update_view()

    # ================================================================== session
    def _on_session_opened(self) -> None:
        s = self.state.session
        if s is None:
            return
        if self.model is not None and getattr(self, "_model_session", None) == s.path:
            return
        self._gen += 1
        gen = self._gen
        self._set_model(None)
        self._model_session = s.path
        self._set_busy("load", "Loading the saved report…")

        def done(d):
            if gen != self._gen:
                return
            self._set_busy("")
            if d:
                self._install(ReportModel.from_dict(d), select=("section", "overview_table"))
            else:
                self._update_view()
            self._run_after_ready()

        def failed(msg):
            if gen == self._gen:
                self._set_busy("")
                self._toast("Could not read the saved report", msg.splitlines()[0], "danger")

        run_task(rb.load_report, s.path, on_done=done, on_error=failed)

    def _on_session_closed(self) -> None:
        self._gen += 1
        self._own_cmds.clear()
        self._undo_index = 0
        self._save_timer.stop()
        self._model_session = None
        self._busy = ""
        self.progress_host.hide()
        self._set_model(None)
        self._pix.clear()
        self._pix_pending.clear()
        self._update_view()

    def _set_model(self, model: Optional[ReportModel]) -> None:
        self.model = model
        self._stale = False
        if model is None:
            self.outline.clear()
            self._clear_previews()

    def _install(self, model: ReportModel, select=None, animate: bool = True) -> None:
        """Adopt a (new) model: outline, inspector, preview, checks."""
        rb.normalize(model)
        keep = select or self._current_key
        self.model = model
        self._stale = False
        self.outline.populate(model, keep_key=keep)
        if self.outline.currentItem() is None:
            self.outline.select_key(("section", "overview_table"), emit=False)
        self.inspector.load()
        self._current_key = self.outline.current_key()
        self.inspector.show_selection(self._current_key)
        self._rebind_preview(animate)
        self.validate()
        self.banner.hide_banner()
        self._update_view()
        self.save_badge.show()
        self._check_timer.start()
        self.report_changed.emit()

    # ================================================================== build / reload / refresh
    def _build_args(self):
        st = self.state
        s = st.session
        defaults = st.ui_state.get("report_defaults", {}) or {}
        hd = rb.hierarchy_defaults(st)       # HIER-01: labels, title, export name
        return dict(title=hd["title"], operator=st.operator(),
                    organization=defaults.get("organization", ""),
                    metadata=rb.session_metadata(st),
                    asset_dir=str(s.path / rb.REPORT_ASSETS),
                    fingerprint=rb.results_fingerprint(st),
                    hierarchy=hd["hierarchy"], export_basename=hd["export_basename"],
                    overlay_opacity=rb.overlay_opacity_arg(st))

    def _relabel(self) -> None:
        from ui import hierarchy_ui as hui
        rec = self._rec = hui.record_word(self.state.profile)
        self.empty_session.set_texts(
            f"No {rec} open",
            f"Open a {rec} from Projects to design its report — editable titles, captions, "
            "sections and image order, exported to Excel and PowerPoint.")
        self.empty_results.set_texts(
            "Nothing to report yet",
            f"Analyse the {rec}'s images first; the report is built from the filtered "
            "results you see on the Review page.")
        self.empty_build.set_texts(
            "Build the report",
            f"Creates an editable report from this {rec}'s filtered results: an overview "
            "table with one row per image, summary charts, one page per image, methods and "
            "raw data (always last). Nothing is exported until you choose to.",
            f"Build report from {rec}")
        self.save_badge.setToolTip(f"Report edits are saved to the {rec} automatically")
        self.btn_xlsx.setToolTip(f"Export the Excel workbook to the {rec}'s exports folder")
        self.btn_pptx.setToolTip(f"Export the PowerPoint deck to the {rec}'s exports folder")

    def on_profile_changed(self) -> None:
        """HIER-01: levels renamed / templates edited in Settings — relabel
        the open report (title and file name only while not hand-edited)."""
        self._relabel()
        if self.model is None or self.state.session is None or self._busy:
            return
        if rb.apply_profile(self.model, rb.hierarchy_defaults(self.state)):
            self.inspector.load()
            self.inspector.show_selection(self._current_key)
            w = self.preview_stack.currentWidget()
            if w is not None and hasattr(w, "refresh"):
                w.refresh()
            self.edited("profile")

    def build_from_session(self, then: Optional[Callable[[], None]] = None) -> None:
        s = self.state.session
        if s is None or self._busy:
            return
        inputs = rb.collect_inputs(self.state)
        if not inputs:
            self._toast("No results to report", "Analyse the images first.", "info")
            return
        if then:
            self._after_ready.append(then)
        self._gen += 1
        gen = self._gen
        kw = self._build_args()
        kw["sample_statistics"] = rb.sample_statistics_arg(self.state, inputs)
        kw["extras"] = rb.report_extras_arg(self.state, inputs)
        self._set_model(None)
        self.skeleton_caption.setText(f"Building the report from {len(inputs)} image"
                                      f"{'s' if len(inputs) != 1 else ''}…")
        self._set_busy("build", "Building the report…")

        def done(model):
            if gen != self._gen:
                return
            self._set_busy("")
            self._install(model, select=("section", "overview_table"))
            self.save_now()
            self._run_after_ready()

        def failed(msg):
            if gen == self._gen:
                self._after_ready.clear()
                self._set_busy("")
                self._toast("Could not build the report", msg.splitlines()[0], "danger")

        run_task(rb.build_model, inputs, on_done=done, on_error=failed, **kw)

    def _ask_rebuild(self) -> None:
        if self.toasts is None:
            self.build_from_session()
            return
        self._toast("Rebuild the report?", "This discards captions, order and section choices "
                    "and starts from the session's current results.", "warning",
                    "Rebuild", self.build_from_session)

    def reload_saved(self) -> None:
        s = self.state.session
        if s is None or self._busy:
            return
        self._save_timer.stop()
        self._gen += 1
        gen = self._gen
        self._set_busy("load", "Loading the saved report…")

        def done(d):
            if gen != self._gen:
                return
            self._set_busy("")
            if d:
                self._install(ReportModel.from_dict(d))
                self._toast("Saved report reloaded", "", "success")
            else:
                self._toast("No saved report", "Build a report from the session first.", "info")

        run_task(rb.load_report, s.path, on_done=done,
                 on_error=lambda m: (self._set_busy(""),
                                     self._toast("Could not read the report", m.splitlines()[0],
                                                 "danger")))

    def refresh_numbers(self, silent: bool = False, then: Optional[Callable[[], None]] = None
                        ) -> None:
        """Rebuild the numbers from the current results, keeping every edit."""
        if self.model is None or self.state.session is None:
            return
        if self._busy:
            if then:
                self._after_ready.append(then)
            return
        inputs = rb.collect_inputs(self.state)
        if then:
            self._after_ready.append(then)
        self._gen += 1
        gen = self._gen
        kw = self._build_args()
        kw["sample_statistics"] = rb.sample_statistics_arg(self.state, inputs)
        kw["extras"] = rb.report_extras_arg(self.state, inputs)
        old = ReportModel.from_dict(self.model.to_dict())
        self._set_busy("refresh", "Refreshing the numbers…")

        def done(new):
            if gen != self._gen:
                return
            merged = rb.merge_refresh(old, new)
            self._pix.clear()
            self._pix_pending.clear()
            self._set_busy("")
            # edits made while the refresh ran would be lost; re-apply cheap ones
            self._install(merged, animate=False)
            self.save_now()
            if not silent:
                self._toast("Numbers refreshed", "Captions, order and settings were kept.",
                            "success")
            self._run_after_ready()

        def failed(msg):
            if gen == self._gen:
                self._after_ready.clear()
                self._set_busy("")
                self._toast("Could not refresh the report", msg.splitlines()[0], "danger")

        run_task(rb.build_model, inputs, on_done=done, on_error=failed, **kw)

    def _run_after_ready(self) -> None:
        cbs, self._after_ready = self._after_ready, []
        for cb in cbs:
            cb()

    def _check_stale(self) -> None:
        if self.model is None or self.state.session is None:
            if not self._busy:
                self._update_view()
            return
        if self._busy or self.state.is_filtering():
            self._check_timer.start()
            return
        fp = rb.results_fingerprint(self.state)
        if fp == self.model.metadata.get("results_fingerprint"):
            self._self_edits.clear()
            self._stale = False
            self.banner.hide_banner()
            return
        if self._self_edits:
            # grains ticked / unticked on this page: follow along silently
            self._self_edits.clear()
            self.refresh_numbers(silent=True)
            return
        self._stale = True
        self.banner.show_message(
            "Results changed since this report was built",
            "Re-analysis or grain-filter edits changed the numbers. Refresh numbers keeps your "
            "captions, order and settings.")

    def is_stale(self) -> bool:
        return self._stale

    # ================================================================== editing
    def edited(self, what: str) -> None:
        """Any model edit: autosave, outline labels, checks."""
        if self.model is None:
            return
        self.outline.refresh_labels(self.model)
        self.schedule_save()
        self._validate_timer.start()
        if what in ("include", "section", "order"):
            self._update_view()

    def _on_doc_changed(self, field: str) -> None:
        if field == "organization":
            d = dict(self.state.ui_state.get("report_defaults", {}) or {})
            d["organization"] = self.model.organization
            self.state.ui_state["report_defaults"] = d
        self.edited(field)
        w = self.preview_stack.currentWidget()
        if w is not None and hasattr(w, "refresh"):
            w.refresh()

    def _on_select(self, key) -> None:
        if key == self._current_key:
            return
        self._current_key = key
        self.inspector.show_selection(key)
        self._show_preview(key, animate=True)

    def select(self, key) -> None:
        self.outline.select_key(key)

    def _on_toggled(self, key, on: bool) -> None:
        kind, ident = key
        if kind == "image":
            self.set_image_included(ident, on)
        elif kind == "section":
            self.set_section_enabled(ident, on)

    def set_image_included(self, image_id: str, on: bool) -> None:
        img = next((i for i in self.model.images if i.id == image_id), None) if self.model else None
        if img is None or img.include == on:
            return
        img.include = on
        self.edited("include")
        self._sync_include_box(("image", image_id), on)
        self._refresh_preview()

    def set_section_enabled(self, section_id: str, on: bool) -> None:
        sec = next((s for s in self.model.sections if s.id == section_id), None) \
            if self.model else None
        if sec is None or sec.enabled == on:
            return
        sec.enabled = on
        self.edited("section")
        self._sync_include_box(("section", section_id), on)
        self._refresh_preview()

    def _sync_include_box(self, key, on: bool) -> None:
        box = getattr(self.inspector, "include_box", None)
        if key == self._current_key and box is not None:
            try:
                box.blockSignals(True)
                box.setChecked(on)
                box.blockSignals(False)
            except RuntimeError:
                pass

    def rename_section(self, section_id: str, title: str) -> None:
        sec = next((s for s in self.model.sections if s.id == section_id), None)
        if sec is None:
            return
        sec.title = title
        self.edited("title")
        self._refresh_preview()

    def add_text_section(self) -> None:
        if self.model is None:
            return
        after = None
        k = self._current_key
        if k and k[0] == "section":
            after = k[1]
        elif k and k[0] == "image":
            after = "images"
        sec = rb.add_custom_text(self.model, after=after)
        self.outline.populate(self.model, keep_key=("section", sec.id))
        self.edited("order")
        self.outline.select_key(("section", sec.id))

    def delete_section(self, section_id: str) -> None:
        rb.remove_section(self.model, section_id)
        self.outline.populate(self.model, keep_key=("section", "parameters"))
        self.edited("order")
        self._current_key = None
        self._on_select(self.outline.current_key())

    def move_selected(self, delta: int) -> None:
        self.outline.move_current(delta)

    def _on_order(self, top: list, imgs: list) -> None:
        rb.apply_order(self.model, top, imgs)
        self.edited("order")
        self._refresh_preview()

    def move_image(self, image_id: str, new_index: int) -> None:
        """Programmatic reorder (tests / keyboard helpers)."""
        top, imgs = self.outline.order()
        imgs = [i for i in imgs if i != image_id]
        imgs.insert(max(0, min(new_index, len(imgs))), image_id)
        rb.apply_order(self.model, top, imgs)
        self.outline.populate(self.model, keep_key=self._current_key)
        self.edited("order")
        self._refresh_preview()

    # ---------------------------------------------------------------- grains
    def image_doc(self, img):
        name = os.path.basename(img.image_path or "")
        for im in self.state.images():
            if im.filename == name:
                return im
        return None

    def set_grain_included(self, image_id: str, grain_id: int, on: bool) -> bool:
        """Report ⇄ analysis: (un)tick = the app's manual exclusion (undoable)."""
        img = next((i for i in self.model.images if i.id == image_id), None) if self.model else None
        doc = self.image_doc(img) if img is not None else None
        if doc is None:
            return False
        ok = (self.state.restore_grains(doc.uid, [grain_id]) if on
              else self.state.delete_grains(doc.uid, [grain_id]))
        if ok:
            self._self_edits.add(doc.uid)
            us = self.state.undo_stack
            cmd = us.command(us.index() - 1)
            if cmd is not None:
                self._own_cmds = [c for c in self._own_cmds[-200:]] + [cmd]
        return ok

    def _on_undo_index(self, idx: int) -> None:
        """Undo / redo of a grain toggle made on this page: follow it silently
        (edits made elsewhere raise the "results changed" banner instead)."""
        prev, self._undo_index = self._undo_index, idx
        if idx == prev:
            return
        try:
            cmd = self.state.undo_stack.command(min(prev, idx))
        except RuntimeError:          # stack destroyed at shutdown
            return
        if cmd is not None and any(cmd is c for c in self._own_cmds):
            self._self_edits.add(getattr(cmd, "uid", None))

    def is_pixmaps_ready(self, img) -> bool:
        return (img.image_path, img.overlay_path) in self._pix

    def pixmaps(self, img):
        """Cached pixmaps for ``img``, or a blank placeholder if they have
        not been decoded yet (never decodes on the GUI thread — see
        ``request_pixmaps``)."""
        return self._pix.get((img.image_path, img.overlay_path)) or (QPixmap(), QPixmap())

    def request_pixmaps(self, img, on_ready: Callable[[tuple], None]):
        """UX-12: the Images tab / per-image preview must never block the
        GUI thread. Returns whatever is cached right now (or a blank
        placeholder); if the real pixmaps are not cached yet, decodes the
        file(s) on a pool thread (``ui.workers.read_image`` + downsample —
        both safe off-thread since they only touch numpy/``QImage``) and
        calls ``on_ready(pixmaps)`` exactly once when the ``QPixmap`` objects
        are built back on the GUI thread. Results are cached by
        ``(image_path, overlay_path)`` so revisiting an image is instant."""
        key = (img.image_path, img.overlay_path)
        hit = self._pix.get(key)
        if hit is not None:
            return hit
        if key not in self._pix_pending:
            self._pix_pending.add(key)

            def work():
                doc = self.image_doc(img)
                orig_qi = (arr_thumb_qimage(doc.image_bgr)
                           if doc is not None and doc.image_bgr is not None
                           else file_thumb_qimage(img.image_path))
                ovl_qi = file_thumb_qimage(img.overlay_path)
                return orig_qi, ovl_qi

            def done(qimages):
                self._pix_pending.discard(key)
                pm = (QPixmap.fromImage(qimages[0]), QPixmap.fromImage(qimages[1]))
                self._pix[key] = pm
                on_ready(pm)

            def failed(_msg):
                self._pix_pending.discard(key)
                pm = (QPixmap(), QPixmap())
                self._pix[key] = pm
                on_ready(pm)

            run_task(work, on_done=done, on_error=failed)
        return (QPixmap(), QPixmap())

    # ================================================================== previews
    def _show_preview(self, key, animate: bool = True) -> None:
        w = make_preview(self, key)
        if w is None:
            w = QWidget()
        self.preview_stack.addWidget(w)
        if animate:
            self.preview_stack.set_current_widget(w)
        else:
            self.preview_stack.setCurrentIndex(self.preview_stack.indexOf(w))
        if not self.preview_stack.is_animating():
            self._drop_old_previews()

    def _drop_old_previews(self) -> None:
        cur = self.preview_stack.currentWidget()
        for i in reversed(range(self.preview_stack.count())):
            w = self.preview_stack.widget(i)
            if w is not cur:
                self.preview_stack.removeWidget(w)
                w.deleteLater()

    def _clear_previews(self) -> None:
        for i in reversed(range(self.preview_stack.count())):
            w = self.preview_stack.widget(i)
            self.preview_stack.removeWidget(w)
            w.deleteLater()

    def current_preview(self):
        return self.preview_stack.currentWidget()

    def _refresh_preview(self) -> None:
        w = self.preview_stack.currentWidget()
        if w is not None and hasattr(w, "refresh"):
            w.refresh()

    def _rebind_preview(self, animate: bool) -> None:
        """New model object: keep the current preview if it can rebind,
        otherwise rebuild it (keeps table scroll + editor focus on refresh)."""
        w = self.preview_stack.currentWidget()
        key = self._current_key
        if isinstance(w, ImagePreview) and key and key[0] == "image":
            img = next((i for i in self.model.images if i.id == key[1]), None)
            if img is not None:
                bar = w.table.verticalScrollBar().value()
                outer = w.scroll.verticalScrollBar().value()
                w.set_image(img)
                w.table.verticalScrollBar().setValue(bar)
                w.scroll.verticalScrollBar().setValue(outer)
                return
        if isinstance(w, TextPreview) and key and key[0] == "section":
            sec = next((s for s in self.model.sections if s.id == key[1]), None)
            if sec is not None:
                w.section = sec
                w.slide.section = sec
                w.refresh()
                return
        if w is not None and key is not None and hasattr(w, "refresh") \
                and not isinstance(w, (ImagePreview, TextPreview)) and w.__class__ is not QWidget:
            w.refresh()
            return
        self._clear_previews()
        self._show_preview(key, animate=False)

    # ================================================================== checks
    def validate(self) -> List[tuple]:
        if self.model is None:
            return []
        probs = rb.problem_hints(self.model)
        self.inspector.show_checks(probs)
        self.inspector.set_logo_name(self.model.logo_path)
        n = len(probs)
        self.issues_badge.set_text(f"{n} issue{'s' if n != 1 else ''}")
        self.issues_badge.set_kind("danger" if any(p[0] == "danger" for p in probs) else "warning")
        self.issues_badge.setVisible(n > 0)
        self.issues_badge.updateGeometry()
        return probs

    # ================================================================== logo
    def choose_logo(self) -> None:
        s = self.state.session
        if s is None or self.model is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Choose a logo", "",
                                              "Images (*.png *.jpg *.jpeg *.bmp)")
        if path:
            self.set_logo(path)

    def set_logo(self, path: str) -> None:
        try:
            self.model.logo_path = rb.copy_logo(path, self.state.session.path)
        except OSError as e:
            self._toast("Could not use that logo", str(e), "danger")
            return
        self.inspector.set_logo_name(self.model.logo_path)
        self._on_doc_changed("logo_path")
        self.validate()

    def clear_logo(self) -> None:
        if self.model is None:
            return
        self.model.logo_path = None
        self.inspector.set_logo_name(None)
        self._on_doc_changed("logo_path")
        self.validate()

    # ================================================================== saving
    def schedule_save(self) -> None:
        self._save_pending = True
        self.save_badge.set_text("Unsaved edits")
        self.save_badge.set_kind("warning")
        self.save_badge.updateGeometry()
        self._save_timer.start()

    def save_now(self) -> None:
        self._save_timer.stop()
        s = self.state.session
        if self.model is None or s is None:
            return
        self._save_pending = False
        self.save_badge.set_text("Saving…")
        self.save_badge.set_kind("info")
        data = self.model.to_dict()

        def done(stamp):
            if not self._save_pending:
                self.save_badge.set_text(f"Report saved {stamp}")
                self.save_badge.set_kind("success")
                self.save_badge.updateGeometry()

        run_task(rb.save_report, s.path, data, on_done=done, pool=serial_pool(),
                 on_error=lambda m: self._toast("Report autosave failed", m.splitlines()[0],
                                                "danger"))

    def flush(self) -> None:
        """AppState.flush / close: write pending edits before the session goes."""
        if self._save_pending or self._save_timer.isActive():
            self.save_now()

    # ================================================================== export
    def export(self, kinds: Sequence[str], ask: bool = False, only_uid=None,
               refresh_first: bool = True) -> None:
        s = self.state.session
        if s is None:
            self._toast("No session open", "Open a session first.", "info")
            return
        if self.model is None:
            if rb.analysed_count(self.state) == 0:
                self._toast("No results to export", "Analyse images first.", "info")
                return
            self.build_from_session(then=lambda: self.export(kinds, ask, only_uid, False))
            return
        if self._busy:
            self._after_ready.append(lambda: self.export(kinds, ask, only_uid, refresh_first))
            return
        if refresh_first and rb.results_fingerprint(self.state) != \
                self.model.metadata.get("results_fingerprint"):
            self.refresh_numbers(silent=True,
                                 then=lambda: self.export(kinds, ask, only_uid, False))
            return
        model = self.model
        stem_title = model.title
        basename = model.export_basename or ""
        if only_uid is not None:
            im = self.state.session.image(only_uid)
            if im is None or im.result is None:
                self._toast("No results for this image", "Analyse it first.", "info")
                return
            model = rb.only_image_model(model, im.filename)
            stem_title = Path(im.filename).stem
            if basename:
                basename = f"{basename}_{im.display_name}"
        jobs: List[Tuple[str, str]] = []
        for k in kinds:
            p = rb.default_export_path(s.path, stem_title, k, basename=basename)
            if ask:
                start = self.state.settings.last_export_dir or str(p.parent)
                chosen, _ = QFileDialog.getSaveFileName(
                    self, f"Save {KIND_TEXT[k]} as", str(Path(start) / p.name), KIND_FILTER[k])
                if not chosen:
                    return
                if not chosen.lower().endswith("." + k):
                    chosen += "." + k
                self.state.settings.last_export_dir = str(Path(chosen).parent)
                self.state.save_settings()
                p = Path(chosen)
            jobs.append((k, str(p)))
        self.save_now()
        extras = rb.report_extras_arg(self.state, [])
        if extras is not None:
            extras["px_per_um"] = next((i.px_per_um for i in model.images
                                        if i.has_calibration and i.px_per_um), 0.0)
        self._run_export(model.to_dict(), jobs, extras)

    def _run_export(self, model_dict: dict, jobs: List[Tuple[str, str]],
                    extras: Optional[dict] = None) -> None:
        total = len(jobs)
        written: List[str] = []
        btns = {"xlsx": self.btn_xlsx, "pptx": self.btn_pptx}

        def step(i: int) -> None:
            if i >= total:
                finish()
                return
            kind, path = jobs[i]
            self._set_busy("export", f"Writing {KIND_TEXT[kind]} — {Path(path).name}",
                           determinate=(i, total))
            b = self.btn_both if total > 1 else btns[kind]
            b.set_loading(True)

            def done(paths):
                written.extend(paths)
                step(i + 1)

            def failed(msg):
                for x in (self.btn_both, self.btn_xlsx, self.btn_pptx):
                    x.set_loading(False)
                self._set_busy("")
                first = msg.splitlines()[0] if msg else "Unknown error"
                if first.startswith("PermissionError") or "Permission denied" in first:
                    first = ("The file is open in another program (e.g. Excel or "
                             "PowerPoint). Close it and export again, or use Save as.")
                self._toast("Export failed", first, "danger")

            run_task(rb.render_outputs, model_dict, [jobs[i]], extras=extras,
                     on_done=done, on_error=failed)

        def finish() -> None:
            for x in (self.btn_both, self.btn_xlsx, self.btn_pptx):
                x.set_loading(False)
            self.progress.setValue(total)
            self._set_busy("")
            if self.model is not None:
                rb.record_export(self.model, written, self.state.operator())
                self.inspector.load_exports()
                self.save_now()
            names = ", ".join(Path(p).name for p in written)
            first = written[0] if written else ""
            self._toast("Report exported", f"{names} — in {Path(first).parent.name}/",
                        "success", "Open file", lambda: self.open_file(first))
            self.exported.emit(list(written))

        step(0)

    def quick_export(self, kind: str = "xlsx", only_current: bool = False) -> None:
        """Menu / Review-page entry points (Ctrl+E, Ctrl+Shift+E)."""
        uid = self.state.current_uid if only_current else None
        self.export([kind], only_uid=uid)

    # ================================================================== files (local only)
    @staticmethod
    def open_file(path: str) -> None:
        if path and os.path.exists(path) and sys.platform == "win32":
            os.startfile(path)  # opens locally (Excel / PowerPoint); no network

    @staticmethod
    def show_in_folder(path: str) -> None:
        if not path or not os.path.exists(path):
            return
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])

    def open_exports_folder(self) -> None:
        s = self.state.session
        if s is None:
            return
        d = s.path / "exports"
        d.mkdir(exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(d))

    def _toast(self, title, body="", sev="info", action=None, cb=None) -> None:
        if self.toasts is not None:
            self.toasts.show_toast(title, body, sev, action, cb)


__all__ = ["ReportsPage"]
