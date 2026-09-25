"""
AppShell (UI-03) — the v3 main window.

  ┌ rail ┬ top bar: breadcrumb · search · theme · operator ────────────┐
  │ Proj │                                                             │
  │ Anal │  FadeStackedWidget: Projects / Analyze / Review / Reports / │
  │ Revw │                     Settings                                │
  │ Rept │                                                             │
  │ Sett ├ status: message · progress · device · calibration · saved ─┤

Menus keep every v2.3 shortcut (Ctrl+O, F5, Ctrl+F5, Ctrl+E, Ctrl+K, Ctrl+R,
Delete) plus Ctrl+N, Ctrl+S, Ctrl+Z/Ctrl+Y, Ctrl+1…5, Ctrl+F and ``?``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QByteArray, QEvent, QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QProgressBar, QStatusBar, QVBoxLayout, QWidget,
)

from ui.app_state import AppState, NodeRef, node_display_name, session_title
from ui.design import icons
from ui.design.branding import install_app_icon
from ui.design.theme import apply_theme, current_mode, set_reduced_motion
from ui.design.tokens import SPACE
from ui.dialogs.new_session_wizard import NewSessionWizard
from ui.format import fmt_date_utc, fmt_int
from ui.pages.analyze_page import AnalyzePage
from ui.pages.lot_compare_page import LotComparePage
from ui.pages.common import Panel
from ui.pages.projects_page import ProjectsPage
from ui.pages.reports_page import ReportsPage
from ui.pages.review_page import ReviewPage
from ui.pages.settings_page import SettingsPage
from ui.widgets import (
    AnimatedButton, Badge, Breadcrumb, Card, FadeStackedWidget, IconButton, NavRail, SearchBox,
    ShortcutOverlay, ToastManager, label,
)
from ui.workers import IMAGE_FILTER, run_task
from version import APP_NAME, __version__

PAGES = [("projects", "projects", "Projects"), ("analyze", "analyze", "Analyze"),
         ("review", "review", "Review"), ("reports", "reports", "Reports")]

SHORTCUTS = {
    "Sessions": [("Ctrl+N", "New session"), ("Ctrl+O", "Add / open images"),
                 ("Ctrl+S", "Save now (autosave is on)"), ("Ctrl+E", "Export report to Excel"),
                 ("Ctrl+Shift+P", "Export report to PowerPoint"),
                 ("Ctrl+F", "Search all sessions")],
    "Analysis": [("F5", "Analyze all images"), ("Ctrl+F5", "Analyze current image"),
                 ("Ctrl+K", "Set scale bar"), ("Ctrl+R", "Set scan area")],
    "Review": [("Click / Ctrl+click", "Select grains"),
               ("L", "Lasso select (hold Ctrl to add)"),
               ("M", "Merge selected touching grains"),
               ("C", "Cut a grain with a line"),
               ("V / Esc", "Back to the select tool"),
               ("Delete", "Remove selected grains"),
               ("Ctrl+Z", "Undo"), ("Ctrl+Y", "Redo")],
    "Navigation": [("Ctrl+1 … Ctrl+5", "Projects … Settings"), ("Up / Down", "Previous / next image"),
                   ("F  /  1", "Fit  /  actual pixels"), ("Wheel", "Zoom about the cursor"),
                   ("?", "Show this sheet")],
}


DEVICE_TIP_CPU = ("AI-assisted detection runs on this computer's processor (CPU). "
                  "A supported NVIDIA graphics card (GPU) would make it faster. Boundary and "
                  "Threshold modes always use the processor. Everything runs on this PC; "
                  "nothing is sent anywhere.")
DEVICE_TIP_GPU = ("AI-assisted detection runs on the graphics card (GPU): {name}. This is "
                  "much faster than the processor. Everything runs on this PC; nothing is "
                  "sent anywhere.")


def device_chip_text(probe: str) -> tuple:
    """(chip text, tooltip, badge kind) for a ``_probe_device`` result."""
    if probe.startswith("GPU"):
        name = probe.split("·", 1)[1].strip() if "·" in probe else "GPU"
        return f"AI runs on: GPU ({name})", DEVICE_TIP_GPU.format(name=name), "accent"
    return "AI runs on: CPU", DEVICE_TIP_CPU, "neutral"


def _probe_device() -> str:
    try:
        import torch  # heavy; imported lazily and off the GUI thread
        if torch.cuda.is_available():
            return "GPU · " + torch.cuda.get_device_name(0)
    except Exception:
        pass
    return "CPU"


class SearchPopup(Card):
    """Results list shown under the top-bar search box (child widget, not a
    popup window, so typing never loses focus)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent=parent, elevation=3)
        self.title = label("", "overline")
        self.add_widget(self.title)
        self.list = QListWidget()
        self.list.setMinimumHeight(60)
        self.list.setToolTip("Enter or double-click opens the session")
        self.add_widget(self.list, 1)
        self.hide()

    def show_rows(self, rows: List[dict], text: str, profile=None) -> None:
        from ui import hierarchy_ui as hui
        self.list.clear()
        L = lambda k: hui.kind_label(profile, k)  # noqa: E731
        for r in rows[:30]:
            is_lot = (Path(r["path"]) / "lot.json").exists()
            name = r.get("label") or (f"{L('lot')} {r.get('lot_number')}" if is_lot else "")                 or r.get("session_id") or Path(r["path"]).name
            where = " › ".join(f"{L(k)} {r[key]}" for k, key in
                               (("project", "project"), ("sample", "sample_id"),
                                ("lot", "lot_number")) if r.get(key))
            detail = " · ".join(x for x in (fmt_date_utc(r.get("created_utc", "")), r.get("operator", ""),
                                            f"{r.get('image_count', 0)} images",
                                            f"{fmt_int(r.get('grain_count', 0))} grains") if x)
            it = QListWidgetItem(icons.icon("images"), f"{name}\n{where}  —  {detail}")
            it.setData(Qt.UserRole, r["path"])
            it.setSizeHint(QSize(0, 44))
            self.list.addItem(it)
        n = len(rows)
        what = "RESULT" if hui.lot_mode(profile) else "SESSION"
        self.title.setText(f"{n} {what}{'S' if n != 1 else ''} MATCH “{text.upper()}”" if n
                           else f"NO {what}S MATCH “{text.upper()}”")
        if rows:
            self.list.setCurrentRow(0)
        self.list.setFixedHeight(min(6, max(1, n)) * 46 + 6)
        self.adjustSize()
        self.show()
        self.raise_()


class AppShell(QMainWindow):
    def __init__(self, state: Optional[AppState] = None, probe_device: bool = True,
                 tour: Optional[bool] = None) -> None:
        """``tour``: auto-start the first-run guided tour when the window is
        first shown.  ``None`` = yes, except under pytest / offscreen renders /
        ``GRAIN_NO_TOUR=1`` (the tour must never block headless runs)."""
        super().__init__()
        self._tour_autostart = _tour_env_allows() if tour is None else bool(tour)
        self._tour_checked = False
        self.state = state or AppState()
        self.setWindowTitle(f"{APP_NAME}")
        self.setWindowIcon(install_app_icon())    # UI-08: window + taskbar icon
        self.setMinimumSize(1180, 640)   # UI-08: fits 1080p at 150 % (1280x~690 usable)
        self.resize(1600, 960)
        self._search_gen = 0
        self._meta_cal: list = []                 # INN-05 toasts, batched
        self._meta_cal_timer = QTimer(self)
        self._meta_cal_timer.setSingleShot(True)
        self._meta_cal_timer.setInterval(350)
        self._meta_cal_timer.timeout.connect(self._flush_metadata_calibration)
        self._build()
        self._build_menus()
        self._wire()
        self._restore_window()
        self.projects.reload()
        self._on_profile_changed()
        self._update_cal_chip()
        self._ensure_catalog()
        from ui.tour import TourController, tag_anchors
        tag_anchors(self)
        self.tour = TourController(self)
        if probe_device:
            QTimer.singleShot(1500, lambda: run_task(_probe_device, on_done=self._set_device))

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.rail = NavRail()
        for key, ic, text in PAGES:
            self.rail.add_page(key, ic, text)
        self.rail.add_page("settings", "settings", "Settings", bottom=True)
        for key in self.rail.keys():
            self.rail.item(key).setToolTip(self.rail.item(key).label)
        h.addWidget(self.rail)

        main = QWidget()
        v = QVBoxLayout(main)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._top_bar())
        self.stack = FadeStackedWidget()
        v.addWidget(self.stack, 1)
        h.addWidget(main, 1)

        self.toasts = ToastManager(central)
        self.projects = ProjectsPage(self.state, self.toasts)
        self.analyze = AnalyzePage(self.state, self.toasts)
        self.review = ReviewPage(self.state, self.toasts)
        self.reports = ReportsPage(self.state, self.toasts)
        self.settings_page = SettingsPage(self.state, self.toasts)
        self.pages = {"projects": self.projects, "analyze": self.analyze, "review": self.review,
                      "reports": self.reports, "settings": self.settings_page}
        for w in self.pages.values():
            self.stack.addWidget(w)
        # INN-43: a sub-page of Projects (the rail keeps "Projects" selected)
        self.compare = LotComparePage(self.state, self.toasts)
        self.stack.addWidget(self.compare)
        self.search_popup = SearchPopup(central)
        self.search_popup.setFixedWidth(460)

        sb = QStatusBar()
        self.setStatusBar(sb)
        self.status_msg = QLabel("Ready")
        sb.addWidget(self.status_msg, 1)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(180)
        self.progress.setRange(0, 100)
        self.progress.setToolTip("Overall analysis progress")
        self.progress.hide()
        sb.addPermanentWidget(self.progress)
        # UX-11: say in plain words where the AI-assisted detection runs
        self.chip_device = Badge("AI runs on: CPU", "neutral", icon="cpu")
        self.chip_device.setToolTip(DEVICE_TIP_CPU)
        self.chip_cal = Badge("Not calibrated", "warning", dot=True)
        self.chip_cal.setToolTip("Scale of the current image (Ctrl+K to set)")
        self.chip_save = Badge("No session", "neutral", icon="save")
        self.chip_save.setToolTip("Session save state — changes are saved automatically")
        # FIX-08 (INN-29, optional): calibration-check chip; hidden unless the
        # session's instrument has at least one recorded check
        from ui.dialogs.cal_check_dialog import CalStatusChip
        self.chip_calcheck = CalStatusChip()
        self._calcheck_timer = QTimer(self)
        self._calcheck_timer.setSingleShot(True)
        self._calcheck_timer.setInterval(300)
        self._calcheck_timer.timeout.connect(self._update_calcheck_chip)
        for c in (self.chip_device, self.chip_cal, self.chip_calcheck, self.chip_save):
            sb.addPermanentWidget(c)
        self.overlay = ShortcutOverlay(self, SHORTCUTS)

    def _top_bar(self) -> QWidget:
        bar = Panel("bottom", surface="bg")
        bar.setFixedHeight(56)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(SPACE.xl, 0, SPACE.lg, 0)
        lay.setSpacing(SPACE.md)
        self.crumb = Breadcrumb(["Workspace"])
        self.crumb.setToolTip("Where you are — click a level to browse it")
        lay.addWidget(self.crumb, 1)
        self.search = SearchBox("Search samples, lots, sessions, operators…")
        self.search.setFixedWidth(340)
        self.search.setToolTip("Search every session in the workspace (Ctrl+F)")
        self.search.installEventFilter(self)
        lay.addWidget(self.search)
        self.theme_btn = IconButton("theme_light", "Switch to light theme")
        lay.addWidget(self.theme_btn)
        self.help_btn = IconButton("keyboard", "Keyboard shortcuts (?)")
        lay.addWidget(self.help_btn)
        self.op_btn = AnimatedButton(self.state.operator() or "Operator", "user", "ghost", "sm")
        self.op_btn.setToolTip("Operator recorded with new sessions — click to change")
        lay.addWidget(self.op_btn)
        return bar

    def _act(self, menu, text, slot, shortcut=None, icon=None, tip=None) -> QAction:
        a = QAction(text, self)
        if icon:
            a.setIcon(icons.icon(icon))
        if shortcut:
            if isinstance(shortcut, (list, tuple)):
                a.setShortcuts([QKeySequence(s) for s in shortcut])
            else:
                a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setStatusTip(tip)
            a.setToolTip(tip)
        a.triggered.connect(slot)
        menu.addAction(a)
        return a

    def _build_menus(self) -> None:
        mb = self.menuBar()
        f = mb.addMenu("&File")
        self.act_new = self._act(f, "&New session…", self.new_session, "Ctrl+N", "add")
        self.act_open = self._act(f, "&Open images…", self.open_images, QKeySequence.Open, "open")
        self._act(f, "&Import folder of images…", self.projects.import_folder, None, "import")
        f.addSeparator()
        self.act_save = self._act(f, "&Save", self.save, QKeySequence.Save, "save")
        self._act(f, "Export report to &Excel", self.export_all_excel, "Ctrl+E", "excel",
                  "Excel workbook of the session's report, saved in the session's exports folder")
        self._act(f, "Export report to &PowerPoint", self.export_pptx, "Ctrl+Shift+P",
                  "powerpoint", "PowerPoint deck of the session's report")
        self._act(f, "Export &current image to Excel", self.export_current_excel, "Ctrl+Shift+E")
        self._act(f, "Open report &designer…", lambda: self.go("reports"), None, "reports")
        f.addSeparator()
        self._act(f, "&Close session", self.state.close_session)
        self._act(f, "&Quit", self.close, QKeySequence.Quit)
        e = mb.addMenu("&Edit")
        self.act_undo = self._act(e, "&Undo", self.state.undo_stack.undo, QKeySequence.Undo, "undo")
        self.act_redo = self._act(e, "&Redo", self.state.undo_stack.redo,
                                  ["Ctrl+Y", "Ctrl+Shift+Z"], "redo")
        self.act_undo.setEnabled(False)
        self.act_redo.setEnabled(False)
        self.act_delete = self._act(e, "Remove selected &grains", self.delete_selected,
                                    QKeySequence.Delete, "delete")
        e.addSeparator()
        self._act(e, "&Find session…", self.focus_search, "Ctrl+F", "search")
        a = mb.addMenu("&Analysis")
        self._act(a, "Set &scale bar…", self.open_calibration, "Ctrl+K", "calibrate")
        self._act(a, "Set scan &area…", self.open_scan_area, "Ctrl+R", "scan_area")
        a.addSeparator()
        self._act(a, "Analyze &all images", self.analyze_all, "F5", "run")
        self._act(a, "Analyze &current image", self.analyze_current, "Ctrl+F5")
        self._act(a, "Ca&ncel analysis", self.analyze.cancel, None, "stop")
        vm = mb.addMenu("&View")
        for i, (key, ic, text) in enumerate(PAGES + [("settings", "settings", "Settings")]):
            self._act(vm, text, lambda _=False, k=key: self.go(k), f"Ctrl+{i + 1}", ic)
        vm.addSeparator()
        self._act(vm, "Co&mpare lots…", lambda: self.show_compare(
            self.projects.selected_lot_paths()), None, "mdi6.compare-horizontal",
            "Compare the lots selected in Projects: grain-size differences and "
            "equivalence to the baseline lot")
        self._act(vm, "Toggle &theme", self.toggle_theme, "Ctrl+Shift+L")
        hm = mb.addMenu("&Help")
        self.act_tour = self._act(hm, "Show &tour", self.start_tour, None, "help",
                                  "Replay the guided tour of a basic analysis")
        self._act(hm, "&Keyboard shortcuts", self.overlay.toggle, None, "keyboard")
        hm.addSeparator()
        self.act_about = self._act(hm, f"&About {APP_NAME}", self.show_about, None, "info",
                                   "Version, privacy statement and third-party licences")

    def _wire(self) -> None:
        st = self.state
        self.rail.page_selected.connect(self._on_page)
        self.rail.expanded_changed.connect(self._on_rail_expanded)
        self.rail.reselected.connect(self._on_rail_reselected)
        self.crumb.segment_clicked.connect(self._on_crumb)
        self.search.search_changed.connect(self._run_search)
        self.search.submitted.connect(lambda _t: self._open_search_item())
        self.search_popup.list.itemActivated.connect(lambda _i: self._open_search_item())
        self.theme_btn.clicked.connect(self.toggle_theme)
        self.help_btn.clicked.connect(self.overlay.toggle)
        self.op_btn.clicked.connect(lambda: (self.go("settings"), self.settings_page.operator.setFocus()))
        st.node_changed.connect(lambda _n: self._update_breadcrumb())
        st.profile_changed.connect(self._on_profile_changed)
        st.metadata_calibration.connect(self._on_metadata_calibration)
        st.session_opened.connect(self._on_session_opened)
        st.session_closed.connect(self._on_session_closed)
        st.save_state_changed.connect(self._on_save_state)
        st.current_image_changed.connect(lambda _u: self._update_cal_chip())
        st.calibration_changed.connect(self._update_cal_chip)
        st.message.connect(lambda t, b, s: self.toasts.show_toast(t, b, s))
        st.settings_changed.connect(lambda: self.op_btn.setText(st.operator() or "Operator"))
        st.settings_changed.connect(self._calcheck_timer.start)
        st.sem_metadata_ready.connect(lambda _u: self._calcheck_timer.start())
        st.undo_stack.canUndoChanged.connect(self.act_undo.setEnabled)
        st.undo_stack.canRedoChanged.connect(self.act_redo.setEnabled)
        st.session_loading.connect(lambda p: self._status(f"Opening {Path(p).name}…"))
        self.projects.open_session_requested.connect(self.open_session)
        self.projects.open_image_requested.connect(
            lambda path, name: self.open_session(path, select=name))
        self.projects.new_session_requested.connect(lambda pre: self.new_session(prefill=pre))
        self.projects.import_requested.connect(lambda paths: self.new_session(images=paths))
        self.projects.choose_workspace_requested.connect(self.settings_page.choose_workspace)
        self.projects.compare_requested.connect(self.show_compare)
        self.compare.back_requested.connect(lambda: self.go("projects"))
        self.analyze.calibrate_requested.connect(self.open_calibration)
        self.analyze.scan_area_requested.connect(self.open_scan_area)
        self.analyze.new_session_requested.connect(self.new_session)
        self.analyze.open_projects_requested.connect(lambda: self.go("projects"))
        self.analyze.review_requested.connect(lambda: self.go("review"))
        self.analyze.add_images_requested.connect(self.open_images)
        self.analyze.progress_changed.connect(self._on_progress)
        self.analyze.setup_required.connect(self.show_setup_hint)
        self.projects.load_requested.connect(self.load_into_analyzer)
        self.review.export_requested.connect(self.export_all_excel)
        self.review.open_projects_requested.connect(lambda: self.go("projects"))
        self.reports.empty_session.action_triggered.connect(lambda: self.go("projects"))
        self.settings_page.theme_requested.connect(self.set_theme)
        self.settings_page.calibration_check_saved.connect(
            lambda _c: self._update_calcheck_chip())
        self.settings_page.defaults_from_analyze_requested.connect(
            lambda: self.settings_page.set_defaults(self.analyze.params.get_params()))

    # ------------------------------------------------------------------ navigation
    def go(self, key: str) -> None:
        if key in self.pages:
            self.rail.set_current(key)
            if self.stack.currentWidget() is not self.pages[key]:
                self._on_page(key)

    def show_compare(self, lot_paths=()) -> None:
        """INN-43: open the lot comparison sub-page for ``lot_paths``."""
        self.rail.set_current("projects", emit=False)
        self.search_popup.hide()
        self.compare.set_lots(list(lot_paths or []))
        self.stack.set_current_widget(self.compare)
        self._update_breadcrumb("projects")

    def _on_rail_expanded(self, on: bool) -> None:
        """UX-10: labels stay shown next time when the rail was expanded."""
        self.state.ui_state["rail_expanded"] = bool(on)

    def _on_rail_reselected(self, key: str) -> None:
        """Clicking "Projects" while on its Compare sub-page goes back."""
        if key in self.pages and self.stack.currentWidget() is not self.pages[key]:
            self._on_page(key)

    def current_page(self) -> str:
        w = self.stack.currentWidget()
        if w is getattr(self, "compare", None):
            return "compare"
        for k, p in self.pages.items():
            if p is w:
                return k
        return "projects"

    def _on_page(self, key: str) -> None:
        self.stack.set_current_widget(self.pages[key])
        if hasattr(self, "act_delete"):
            self.act_delete.setText("Move selected to &trash" if key == "projects"
                                    else "Remove selected &grains")
        self.search_popup.hide()
        self._update_breadcrumb(key)
        self.state.ui_state["last_page"] = key

    def _chain_for(self, page: Optional[str] = None) -> List[NodeRef]:
        page = page or self.current_page()
        st = self.state
        if page in ("analyze", "review", "reports") and st.session is not None:
            from ui.app_state import node_chain
            return node_chain(st.root, NodeRef("session", st.session.path))
        return st.chain()

    def _update_breadcrumb(self, page: Optional[str] = None) -> None:
        chain = self._chain_for(page)
        self._crumb_chain = chain
        prof = self.state.profile
        self.crumb.set_segments([node_display_name(n, prof, crumb=True) for n in chain])

    def _on_profile_changed(self) -> None:
        """HIER-01: relabel the chrome (breadcrumb, search, File menu)."""
        from ui import hierarchy_ui as hui
        p = self.state.profile
        L = lambda k: hui.kind_label(p, k)  # noqa: E731
        self._update_breadcrumb()
        if hui.lot_mode(p):
            self.search.setPlaceholderText(
                f"Search {hui.plural(L('sample')).lower()}, {hui.plural(L('lot')).lower()}, "
                "heat numbers, operators…")
            self.act_new.setText(f"&New {L('lot')} / add images…")
            self.act_new.setStatusTip(f"Create a {L('lot')} (or pick one) and add SEM images")
        else:
            self.search.setPlaceholderText("Search samples, lots, sessions, operators…")
            self.act_new.setText("&New session…")
            self.act_new.setStatusTip("Start a new analysis session")
        s = self.state.session
        if s is not None:
            self.setWindowTitle(f"{s.title} — {APP_NAME}")
        else:
            self.chip_save.set_text(f"No {self._rec_word()} open")

    def _on_crumb(self, idx: int, _text: str) -> None:
        chain = getattr(self, "_crumb_chain", [])
        if 0 <= idx < len(chain):
            self.go("projects")
            self.projects.select_node(chain[idx].path)

    # ------------------------------------------------------------------ search
    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def _run_search(self, text: str) -> None:
        text = text.strip()
        self._search_gen += 1
        gen = self._search_gen
        if not text:
            self.search_popup.hide()
            return
        root = self.state.root

        def work():
            from data.catalog import Catalog
            return Catalog(root).search(text)

        def done(rows):
            if gen == self._search_gen:
                self._place_popup()
                self.search_popup.show_rows(rows, text, self.state.profile)

        run_task(work, on_done=done)

    def _place_popup(self) -> None:
        c = self.centralWidget()
        p = self.search.mapTo(c, QPoint(self.search.width() - self.search_popup.width(),
                                        self.search.height() + 6))
        self.search_popup.move(p)

    def _open_search_item(self) -> None:
        it = self.search_popup.list.currentItem()
        if it is None or not self.search_popup.isVisible():
            return
        self.search_popup.hide()
        self.search.clear_search()
        self.open_session(Path(it.data(Qt.UserRole)))

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.search and ev.type() == QEvent.KeyPress and self.search_popup.isVisible():
            lst = self.search_popup.list
            if ev.key() in (Qt.Key_Down, Qt.Key_Up):
                r = lst.currentRow() + (1 if ev.key() == Qt.Key_Down else -1)
                lst.setCurrentRow(max(0, min(lst.count() - 1, r)))
                return True
            if ev.key() == Qt.Key_Escape:
                self.search_popup.hide()
                return True
        if obj is self.search and ev.type() == QEvent.FocusOut:
            QTimer.singleShot(200, lambda: (not self.search_popup.list.hasFocus())
                              and self.search_popup.hide())
        return super().eventFilter(obj, ev)

    # ------------------------------------------------------------------ sessions
    def new_session(self, prefill: Optional[dict] = None, images: Optional[List[str]] = None) -> None:
        if not prefill and self.state.current_node is not None:
            n = self.state.current_node
            lot = n.path if n.kind == "lot" else (n.path.parent if n.kind == "session" else None)
            if lot is not None:
                prefill = {"project_path": lot.parent.parent, "sample_path": lot.parent,
                           "lot_path": lot}
        dlg = NewSessionWizard(self.state, prefill=prefill or {}, images=images or [], parent=self)
        dlg.session_created.connect(self._on_wizard_created)
        self._wizard = dlg
        dlg.open()

    def _on_wizard_created(self, path) -> None:
        """Wizard finished: (re)open the lot / session, then read the SEM
        metadata of its images (INN-05 auto-calibration)."""
        path = Path(path)
        s = self.state.session
        if s is not None and s.path == path:
            self.state.close_session()      # images were appended on disk: reload
        self.open_session(path, prefer="analyze", probe=True)

    def open_session(self, path, prefer: Optional[str] = None, probe: bool = False,
                     select: Optional[str] = None) -> None:
        """Open a session / lot record; ``select`` (an image filename) makes
        that image current -- Review when it has a result, else Analyze."""
        path = Path(path)

        def done(ok):
            if not ok or self.state.session is None:
                return
            if probe:
                self.state.probe_metadata()
            has = any(im.result is not None for im in self.state.images())
            target = next((im for im in self.state.images() if im.filename == select),
                          None) if select else None
            if target is not None:
                self.state.set_current_image(target.uid)
                has = target.result is not None
            self.go(prefer or ("review" if has else "analyze"))
            self.projects.reload()
            n = len(self.state.images())
            self._status(f"Opened “{self.state.session.title}” — {n} image{'s' if n != 1 else ''}")

        self.state.open_session(path, on_done=done)

    def _on_session_opened(self) -> None:
        s = self.state.session
        self.setWindowTitle(f"{s.title} — {APP_NAME}" if s else APP_NAME)
        self._update_breadcrumb()
        self._update_cal_chip()
        self._update_calcheck_chip()

    def _on_session_closed(self) -> None:
        self.setWindowTitle(APP_NAME)
        self._update_breadcrumb()
        self._update_cal_chip()
        self._update_calcheck_chip()

    def open_images(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Open SEM images", "", IMAGE_FILTER)
        if not paths:
            return
        if self.state.session is None:
            self.new_session(images=paths)
        else:
            self.state.add_images(paths)
            self.go("analyze")

    def save(self) -> None:
        if self.state.session is None:
            self.toasts.show_toast("Nothing to save", "Open or create a session first.", "info")
            return
        self.state.save_now()
        if self.state.save_state == "saved":
            self.toasts.show_toast("All changes saved", str(self.state.session.path), "success")

    # ------------------------------------------------------------------ analysis
    def analyze_all(self) -> None:
        self.go("analyze")
        self.analyze.analyze_all()

    def analyze_current(self) -> None:
        self.go("analyze")
        self.analyze.analyze_current()

    def delete_selected(self) -> None:
        page = self.current_page()
        if page == "review":
            self.review.delete_selected()
        elif page == "analyze":
            ids = self.analyze.canvas.selected()
            if ids and self.state.delete_grains(self.state.current_uid, ids):
                self.analyze.canvas.clear_selection()
        elif page == "projects":
            self.projects.delete_pressed()

    def _need_image(self):
        im = self.state.current_image()
        if im is None or im.image_bgr is None:
            self.toasts.show_toast("No image selected", "Open a session and select an image first.",
                                   "info")
            return None
        return im

    def open_calibration(self) -> None:
        im = self._need_image()
        if im is None:
            return
        from ui.calibration_dialog import CalibrationDialog, suggest_bar_length_um
        dlg = CalibrationDialog(image_bgr=im.image_bgr, parent=self)
        bar = self._find_scale_bar(im)
        if bar:
            sug = im.cal_suggestion
            length = suggest_bar_length_um(bar.get("length_px", 0), float(sug[0])) \
                if sug else None
            dlg.prefill(bar, length)
        n = len(self.state.images())
        dlg.btn_apply.setText(f"Apply to all {n} images" if n > 1 else "Apply to all images")

        def apply(px):
            per_image = dlg.apply_scope == "image"
            analysed = any(x.result is not None for x in self.state.images())
            if per_image:
                self.state.set_calibration(px, im.uid)
                m = dlg.measured()
                if m:
                    im.bar_px, im.bar_um = float(m[0]), float(m[1])
                snap = None
            else:
                snap = self.state.set_calibration_all(px)
            self.toasts.show_toast(
                "Scale set", f"{px:.4f} px/µm — " + ("this image only" if per_image
                                                     else "all images")
                + (". Re-analyse to apply it to existing results." if analysed else "."),
                "success", "Undo" if snap else None,
                (lambda s=snap: self.state.restore_scales(s)) if snap else None)
        dlg.calibration_set.connect(apply)
        dlg.exec()

    @staticmethod
    def _find_scale_bar(im) -> Optional[dict]:
        """Scale-bar line inside the detected SEM info bar (DET-05)."""
        if im is None or im.image_bgr is None:
            return None
        try:
            from core.scale_bar import find_scale_bar_line
            return find_scale_bar_line(im.image_bgr)
        except Exception:
            return None

    # ------------------------------------------------------------------ INN-05
    def _on_metadata_calibration(self, uid, px: float, src: str, conf: str,
                                 prev: float) -> None:
        """Collect metadata scales for a moment, then one toast for all
        (applied → Undo; medium confidence → "Use")."""
        self._meta_cal.append((uid, float(px), src, conf, float(prev)))
        self._meta_cal_timer.start()

    def _flush_metadata_calibration(self) -> None:
        items, self._meta_cal = self._meta_cal, []
        applied = [x for x in items if x[3] == "high"]
        offered = [x for x in items if x[3] == "medium"]
        st = self.state

        def n_img(n):
            return f"{n} image{'s' if n != 1 else ''}"
        if applied:
            srcs = sorted({x[2] for x in applied})
            pxs = sorted({round(x[1], 4) for x in applied})
            px_txt = f"{pxs[0]:.4f} px/µm" if len(pxs) == 1 else f"{len(pxs)} scales"

            def undo(items=applied):
                for uid, _px, _s, _c, prev in items:
                    if prev > 0:
                        st.set_calibration(prev, uid)
                    else:
                        st.reset_image_calibration(uid)
                self.toasts.show_toast("Metadata scale undone",
                                       f"{n_img(len(items))} use the previous scale again.",
                                       "info")
            self.toasts.show_toast(
                "Scale read from image metadata",
                f"{px_txt} ({', '.join(srcs)}) applied to {n_img(len(applied))}. "
                "Read locally from the image files.",
                "success", "Undo", undo, timeout_ms=9000)
        if offered:
            def use(items=offered):
                for uid, px, _s, _c, _prev in items:
                    st.set_calibration(px, uid)
                self.toasts.show_toast("Metadata scale applied",
                                       f"{n_img(len(items))} calibrated from metadata.",
                                       "success")
            pxs = sorted({round(x[1], 4) for x in offered})
            self.toasts.show_toast(
                "Scale found in image metadata",
                (f"{pxs[0]:.4f} px/µm" if len(pxs) == 1 else f"{len(pxs)} scales")
                + f" for {n_img(len(offered))} — please check before using it.",
                "info", "Use it", use, timeout_ms=9000)

    def open_scan_area(self) -> None:
        im = self._need_image()
        if im is None:
            return
        from ui.scan_area_dialog import ScanAreaDialog
        dlg = ScanAreaDialog(image_bgr=im.image_bgr, current_rect=self.state.scan_for(im), parent=self)
        this_only = self.analyze.scan_this.isChecked()

        def apply(x, y, w, h):
            H, W = im.image_bgr.shape[:2]
            full = w >= W and h >= H
            # session: full frame = no scan area; this image only: an explicit
            # full-frame override (a reset to the session's area is a button)
            if this_only:
                rect = (0, 0, W, H) if full else (x, y, w, h)
                self.state.set_scan_rect(rect, im.uid)
                snap = None
            else:
                rect = None if full else (x, y, w, h)
                snap = self.state.set_scan_rect_all(rect)   # every image in the analyzer
            self.toasts.show_toast("Scan area set",
                                   ("Full image" if full else f"{w} × {h} px") + " — "
                                   + ("this image" if this_only else "all images") + "."
                                   + ("" if full else " Border grains will be excluded."),
                                   "success", "Undo" if snap else None,
                                   (lambda s=snap: self.state.restore_scans(s)) if snap else None)
        dlg.scan_area_set.connect(apply)
        dlg.exec()

    def _on_progress(self, pct: float, msg: str) -> None:
        if pct < 0:
            self.progress.hide()
            self._status("Ready")
            return
        self.progress.show()
        self.progress.setValue(int(pct))
        if msg:
            self._status(msg)

    # ------------------------------------------------------------------ export
    # Every export goes through the report pipeline (ReportModel → renderers):
    # the report is built on demand, refreshed if the results changed, and the
    # file lands in <session>/exports/.  Save-as lives on the Reports page.
    def export_all_excel(self, only_current: bool = False) -> None:
        if not any(im.result is not None for im in self.state.images()):
            self.toasts.show_toast("No results to export", "Analyse images first.", "info")
            return
        self.reports.quick_export("xlsx", only_current=only_current)

    def export_current_excel(self) -> None:
        self.export_all_excel(only_current=True)

    def export_pptx(self) -> None:
        if not any(im.result is not None for im in self.state.images()):
            self.toasts.show_toast("No results to export", "Analyse images first.", "info")
            return
        self.reports.quick_export("pptx")

    # ------------------------------------------------------------------ chrome state
    def _status(self, msg: str) -> None:
        self.status_msg.setText(msg)

    def _set_device(self, probe: str) -> None:
        text, tip, kind = device_chip_text(probe or "CPU")
        self.chip_device.set_text(text)
        self.chip_device.setToolTip(tip)
        self.chip_device.set_kind(kind)
        self.chip_device.updateGeometry()

    def _update_cal_chip(self) -> None:
        im = self.state.current_image()
        self.chip_cal.setVisible(self.state.session is not None)
        if self.state.session is None:
            return
        px = self.state.px_for(im) if im is not None else self.state.session.px_per_um
        if px > 0:
            self.chip_cal.set_text(f"Calibrated {1.0 / px:.4g} µm/px")
            self.chip_cal.set_kind("success")
        else:
            self.chip_cal.set_text("Not calibrated")
            self.chip_cal.set_kind("warning")
        self.chip_cal.updateGeometry()

    def _update_calcheck_chip(self) -> None:
        """FIX-08: stamp the open session with the check it cites
        (apply_to_session) and show the result in the status bar."""
        self._calcheck_timer.stop()
        s = self.state.session
        if s is None:
            self.chip_calcheck.refresh_session(None, None)
            return
        self.state.refresh_calibration_check()
        try:
            self.chip_calcheck.refresh_session(self.state.calibration_store(), s.meta,
                                               getattr(self.state.settings, "instruments", None))
        except (OSError, ValueError):
            self.chip_calcheck.hide()
        self.chip_calcheck.updateGeometry()

    def _rec_word(self) -> str:
        from ui import hierarchy_ui as hui
        return hui.record_word(self.state.profile)

    def _on_save_state(self, state: str, detail: str) -> None:
        text, kind = {"saved": (f"Saved ✓ {detail or self.state.last_saved}".strip(), "success"),
                      "saving": ("Saving…", "info"), "unsaved": ("Unsaved changes", "warning"),
                      "error": ("Save failed", "danger"),
                      "none": (f"No {self._rec_word()} open", "neutral")
                      }.get(state, (state, "neutral"))
        if state == "saved" and not (detail or self.state.last_saved):
            text = "Saved ✓"
        self.chip_save.set_text(text)
        self.chip_save.set_kind(kind)
        self.chip_save.updateGeometry()

    def set_theme(self, mode: str) -> None:
        apply_theme(QApplication.instance(), mode)
        self.state.settings.theme = mode
        self.state.save_settings()
        self.theme_btn.set_icon_name("theme_light" if mode == "dark" else "theme_dark")
        self.theme_btn.setToolTip("Switch to light theme" if mode == "dark" else "Switch to dark theme")
        self.settings_page.sync_theme(mode)

    def toggle_theme(self) -> None:
        self.set_theme("light" if current_mode() == "dark" else "dark")

    def _ensure_catalog(self) -> None:
        root = self.state.root
        if not (root / "catalog.sqlite").exists():
            def work():
                from data.catalog import Catalog
                return Catalog(root).rebuild()
            run_task(work)

    def _restore_window(self) -> None:
        ui = self.state.ui_state
        geo = ui.get("geometry")
        if geo:
            try:
                self.restoreGeometry(QByteArray.fromBase64(geo.encode("ascii")))
            except Exception:
                pass
        if ui.get("reduced_motion"):
            set_reduced_motion(True)
            self.settings_page.motion.setChecked(True)
        mode = current_mode()
        self.theme_btn.set_icon_name("theme_light" if mode == "dark" else "theme_dark")
        self.settings_page.sync_theme(mode)
        last = ui.get("last_page", "projects")
        if last in ("analyze", "review"):
            last = "projects"  # nothing is open yet at start-up
        self.rail.set_current(last if last in self.pages else "projects", emit=False, animate=False)
        if ui.get("rail_expanded"):
            self.rail.set_expanded(True, animate=False)
        self.stack.setCurrentWidget(self.pages.get(last, self.projects))

    def closeEvent(self, e) -> None:
        if self.analyze.queue.is_running():
            # The detector call in flight cannot be interrupted; tell the user
            # why closing takes a moment instead of appearing frozen.
            self.statusBar().showMessage("Finishing the current analysis before closing…")
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            QApplication.processEvents()
            self.analyze.queue.cancel()
            self.analyze.queue.wait(20000)
            QApplication.restoreOverrideCursor()
        self.state.flush()
        from ui.workers import shutdown_tasks
        shutdown_tasks()
        self.state.ui_state["geometry"] = bytes(self.saveGeometry().toBase64()).decode("ascii")
        self.state.ui_state["last_page"] = self.current_page()
        self.state.persist_ui_state()
        super().closeEvent(e)

    def show_about(self) -> None:
        """Help › About (UI-08) — window-modal, non-blocking."""
        from ui.dialogs.about_dialog import AboutDialog
        dlg = AboutDialog(self)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        self.about_dialog = dlg
        dlg.open()

    # ------------------------------------------------------------------ UX-02 / UX-09
    def show_setup_hint(self, title: str, text: str) -> None:
        """Spotlight the Analyze page's "Scan area & scale" tile exactly like
        the guided tour does, with the gate text."""
        from ui.tour import TourController
        from ui.tour.steps import TourStep
        if self.tour.is_active():
            return
        old = getattr(self, "setup_hint", None)
        if old is not None and old.is_active():
            old.finish()
        self.search_popup.hide()
        step = TourStep("setup_gate", title, text, kind="point", page="analyze",
                        targets=("tourSetupTile",), fallbacks=(("tourCalibration",),))
        self.setup_hint = TourController(self, [step], hint=True)
        self.setup_hint.start()

    def load_into_analyzer(self, paths) -> None:
        """UX-09: every image under the given jobs / parts / lots / sessions
        into the analyzer (folders found off-thread, pixels stream in)."""
        paths = [Path(p) for p in paths or []]
        if not paths:
            return
        if self.analyze.queue.is_running():
            self.toasts.show_toast("Analysis is running",
                                   "Wait for it to finish (or Cancel) before loading other "
                                   "images.", "info")
            return
        self._status("Finding images…")

        def found(recs):
            if not recs:
                self.toasts.show_toast("No images found",
                                       "The selected folders contain no images yet.", "info")
                self._status("Ready")
                return

            def done(ok):
                if ok and self.state.session is not None:
                    self.go("analyze")
                    self.projects.reload()
                    self._status(f"Loading {len(self.state.images())} images from "
                                 f"{len(recs)} folder{'s' if len(recs) != 1 else ''}…"
                                 if self.state.is_loading() else "Ready")
            self.state.open_records(recs, on_done=done)

        from ui.app_state import records_under
        run_task(records_under, self.state.root, paths, on_done=found,
                 on_error=lambda m: self.toasts.show_toast("Could not read the folders",
                                                           m.splitlines()[0], "danger"))

    # ------------------------------------------------------------------ tour (UI-10)
    def start_tour(self, index: int = 0) -> None:
        """Help › Show tour — always plays, whatever "don't show" says."""
        self.search_popup.hide()
        if self.overlay.is_open():
            self.overlay.close_overlay()
        self.tour.start(index)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if not self._tour_checked:
            self._tour_checked = True
            if self._tour_autostart and self.tour.should_autostart():
                QTimer.singleShot(700, lambda: self.isVisible() and not self.tour.is_active()
                                  and self.start_tour())

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if self.search_popup.isVisible():
            self._place_popup()


def _tour_env_allows() -> bool:
    """Auto-start the tour only in a real interactive session."""
    if os.environ.get("GRAIN_NO_TOUR", "") in ("1", "true", "yes"):
        return False
    if "pytest" in sys.modules or "PYTEST_CURRENT_TEST" in os.environ:
        return False
    plat = (QApplication.platformName() or os.environ.get("QT_QPA_PLATFORM", "")).lower()
    return plat not in ("offscreen", "minimal")


__all__ = ["AppShell", "SHORTCUTS"]
