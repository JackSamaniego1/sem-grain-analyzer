"""
Component gallery for visual QA of the design system.

    python -m ui.gallery              # interactive window (dark)
    python -m ui.gallery --light      # start in light theme
    python -m ui.gallery --capture scratch/ui   # offscreen PNGs incl. mid-transition frames
"""
from __future__ import annotations

import argparse
import os
import sys

from PySide6.QtCore import QElapsedTimer, Qt, QTimer
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QProgressBar, QPushButton, QRadioButton,
    QScrollArea, QSlider, QSpinBox, QStatusBar, QTableWidget, QTableWidgetItem, QTabWidget,
    QTreeView, QVBoxLayout, QWidget,
)

from ui.design.theme import apply_theme, current_mode, set_reduced_motion
from ui.design.tokens import SPACE
from ui.widgets import (
    AnimatedButton, Badge, Breadcrumb, Card, Chip, CollapsibleSection, Divider, EmptyState,
    FadeStackedWidget, IconButton, KeyValueList, NavRail, ProgressRing, SearchBox,
    SegmentedControl, ShortcutOverlay, Skeleton, Spinner, StatCard, ToastManager, label,
)

SHORTCUTS = {
    "Navigation": [("Ctrl+1", "Projects"), ("Ctrl+2", "Analyze"), ("Ctrl+3", "Review"),
                   ("Ctrl+4", "Reports"), ("Ctrl+F", "Search"), ("?", "Show shortcuts")],
    "Canvas": [("Ctrl+Z", "Undo"), ("Ctrl+Shift+Z / Ctrl+Y", "Redo"), ("Del", "Delete grain"),
               ("M", "Merge selection"), ("S", "Split grain"), ("F", "Fit to window")],
    "Analysis": [("F5", "Analyze current image"), ("Shift+F5", "Analyze all"),
                 ("Esc", "Cancel running job")],
    "Files": [("Ctrl+O", "Open images"), ("Ctrl+S", "Save session"), ("Ctrl+E", "Export report")],
}


def _row(*widgets, spacing=SPACE.sm, stretch=True) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    for w in widgets:
        lay.addWidget(w)
    if stretch:
        lay.addStretch(1)
    return lay


def _scroll(inner: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setWidget(inner)
    sa.setFrameShape(QScrollArea.NoFrame)
    return sa


class GalleryWindow(QMainWindow):
    """Window demonstrating every component; NavRail drives a FadeStackedWidget."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Grain Analyzer - Component Gallery")
        self.resize(1440, 920)
        central = QWidget()
        self.setCentralWidget(central)
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        self.rail = NavRail()
        for key, icon, text in (("overview", "dashboard", "Overview"),
                                ("controls", "tune", "Controls"),
                                ("states", "layers", "States & layout")):
            self.rail.add_page(key, icon, text)
        self.rail.add_page("settings", "settings", "Settings", bottom=True)
        h.addWidget(self.rail)

        main = QWidget()
        v = QVBoxLayout(main)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._top_bar())
        self.stack = FadeStackedWidget()
        self.pages = {
            "overview": self._overview_page(),
            "controls": self._controls_page(),
            "states": self._states_page(),
            "settings": self._settings_page(),
        }
        for w in self.pages.values():
            self.stack.addWidget(w)
        v.addWidget(self.stack, 1)
        h.addWidget(main, 1)
        self.rail.page_selected.connect(self._go)

        sb = QStatusBar()
        self.setStatusBar(sb)
        sb.addWidget(QLabel("Ready"))
        sb.addPermanentWidget(Badge("Calibrated 0.412 µm/px", "success", dot=True))
        sb.addPermanentWidget(Badge("CPU", "neutral", icon="cpu"))
        sb.addPermanentWidget(QLabel("Operator: A. Operator"))

        self.toasts = ToastManager(central)
        self.overlay = ShortcutOverlay(self, SHORTCUTS)

    # -- chrome -------------------------------------------------------------
    def _top_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(56)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(SPACE.xl, 0, SPACE.lg, 0)
        lay.setSpacing(SPACE.md)
        self.crumb = Breadcrumb(["Projects", "Alloy 718 qualification", "Sample S-014",
                                 "Lot 2026-0917-B"])
        self.crumb.segment_clicked.connect(
            lambda i, s: self.toasts.show_toast("Navigate", f"Breadcrumb → {s}", "info"))
        lay.addWidget(self.crumb, 1)
        self.search = SearchBox("Search samples, lots, images…")
        self.search.setFixedWidth(300)
        lay.addWidget(self.search)
        self.theme_switch = SegmentedControl(["Dark", "Light"], 0 if current_mode() == "dark" else 1)
        self.theme_switch.current_text_changed.connect(lambda m: apply_theme(None, m.lower()))
        lay.addWidget(self.theme_switch)
        help_btn = IconButton("keyboard", "Keyboard shortcuts (?)")
        help_btn.clicked.connect(lambda: self.overlay.toggle())
        lay.addWidget(help_btn)
        lay.addWidget(IconButton("notifications", "Notifications"))
        return bar

    def _go(self, key: str) -> None:
        self.stack.set_current_widget(self.pages[key])

    def _page(self, title: str, subtitle: str):
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(SPACE.xl, SPACE.sm, SPACE.xl, SPACE.xl)
        v.setSpacing(SPACE.lg)
        head = QVBoxLayout()
        head.setSpacing(SPACE.xxs)
        head.addWidget(label(title, "h1"))
        head.addWidget(label(subtitle, tone="secondary"))
        v.addLayout(head)
        return inner, v

    # -- pages --------------------------------------------------------------
    def _overview_page(self) -> QWidget:
        inner, v = self._page("Lot overview", "Alloy 718 · 12 images analysed · "
                                              "last run 14:32 by A. Operator")
        stats = QHBoxLayout()
        stats.setSpacing(SPACE.lg)
        self.stats = []
        for text, val, unit, dec, delta, trend, good, spark in (
            ("Grains detected", 1284, "", 0, "+6.2%", "up", True, [3, 4, 4, 6, 5, 7, 8, 8, 9]),
            ("Mean diameter", 12.41, "µm", 2, "-0.3 µm", "down", True, [9, 8, 8, 7, 7, 6, 6, 5, 5]),
            ("ASTM grain size", 8.2, "G", 1, "±0.0", "flat", True, [5, 5, 6, 5, 5, 5, 6, 5, 5]),
            ("Area coverage", 96.1, "%", 1, "-2.4%", "down", False, [9, 9, 8, 9, 7, 7, 6, 6, 5]),
        ):
            sc = StatCard(text, val, unit, dec)
            sc.set_delta(delta, trend, good)
            sc.set_sparkline(spark)
            self.stats.append(sc)
            stats.addWidget(sc)
        v.addLayout(stats)

        grid = QGridLayout()
        grid.setSpacing(SPACE.lg)
        # Buttons card
        bc = Card("Actions", "AnimatedButton variants, sizes and states")
        bc.add_action(IconButton("more", "More options", size=28))
        self.run_btn = AnimatedButton("Analyze all", "run", "primary")
        self.run_btn.clicked.connect(self._demo_loading)
        bc.body_layout().addLayout(_row(
            self.run_btn, AnimatedButton("Calibrate", "calibrate", "secondary"),
            AnimatedButton("Export", "export", "ghost")))
        loading = AnimatedButton("Analyzing…", None, "primary")
        loading.set_loading(True)
        dis = AnimatedButton("Disabled", "lock", "secondary")
        dis.setEnabled(False)
        bc.body_layout().addLayout(_row(
            AnimatedButton("Delete grain", "delete", "danger"),
            AnimatedButton("Approve", "check", "success"), loading, dis))
        bc.body_layout().addLayout(_row(
            AnimatedButton("Small", None, "secondary", "sm"),
            AnimatedButton("Large primary", "save", "primary", "lg"),
            IconButton("zoom_in", "Zoom in"), IconButton("zoom_out", "Zoom out"),
            IconButton("fit", "Fit to window"), IconButton("lasso", "Lasso select", checkable=True)))
        grid.addWidget(bc, 0, 0)

        # Status card
        sc = Card("Status", "Badge.for_status and Chip")
        sc.body_layout().addLayout(_row(*[Badge.for_status(s) for s in
                                          ("PASS", "FAIL", "INCONCLUSIVE", "Draft", "Approved",
                                           "In review")]))
        sc.body_layout().addLayout(_row(Badge("GPU", "accent", icon="gpu"),
                                        Badge("12 images", "neutral", icon="images"),
                                        Badge("3 warnings", "warning", icon="warning")))
        chips = [Chip("Equiaxed", checkable=True, checked=True), Chip("Twinned", checkable=True),
                 Chip("Edge grains", checkable=True), Chip("S-014", closable=True, icon="tag")]
        sc.body_layout().addLayout(_row(*chips))
        seg = SegmentedControl(["Histogram", "Cumulative", "Table"])
        sc.body_layout().addLayout(_row(label("View", tone="secondary"), seg))
        grid.addWidget(sc, 0, 1)

        # Progress card
        pc = Card("Progress", "Spinner, ProgressRing, Skeleton")
        self.ring = ProgressRing(76)
        self.ring.set_value(68, animate=False)
        self.ring.set_caption("8 of 12")
        ring2 = ProgressRing(76)
        ring2.set_value(100, animate=False)
        ring2.set_tone("success")
        ring2.set_label("✓")
        ring2.set_caption("done")
        spin_col = QVBoxLayout()
        spin_col.setSpacing(SPACE.sm)
        spin_col.addLayout(_row(Spinner(18), label("Segmenting with SAM…", tone="secondary")))
        bar = QProgressBar()
        bar.setValue(42)
        spin_col.addWidget(bar)
        spin_col.addWidget(Skeleton(shape="text"))
        spin_col.addWidget(Skeleton(width=180, shape="text"))
        r = QHBoxLayout()
        r.setSpacing(SPACE.lg)
        r.addWidget(self.ring)
        r.addWidget(ring2)
        r.addLayout(spin_col, 1)
        pc.body_layout().addLayout(r)
        grid.addWidget(pc, 1, 0)

        # Demo triggers card
        dc = Card("Feedback", "Toasts and motion triggers")
        b1 = AnimatedButton("Success toast", "success", "secondary")
        b1.clicked.connect(lambda: self.toasts.show_toast(
            "Analysis complete", "12 images · 1,284 grains · ASTM G 8.2", "success",
            "Open report"))
        b2 = AnimatedButton("Warning toast", "warning", "secondary")
        b2.clicked.connect(lambda: self.toasts.show_toast(
            "Scale bar not found", "Image 7 uses the lot calibration instead.", "warning"))
        b3 = AnimatedButton("Error toast", "danger", "secondary")
        b3.clicked.connect(lambda: self.toasts.show_toast(
            "Export failed", "Report file is open in another program.", "danger", "Retry"))
        b4 = AnimatedButton("Count up", "refresh", "ghost")
        b4.clicked.connect(lambda: [s.count_up() for s in self.stats])
        dc.body_layout().addLayout(_row(b1, b2, b3))
        dc.body_layout().addLayout(_row(b4, AnimatedButton("Shortcuts  ?", "keyboard", "ghost")))
        b4.clicked.connect(lambda: self.ring.set_value((self.ring.value() + 23) % 101))
        grid.addWidget(dc, 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        v.addLayout(grid)
        v.addStretch(1)
        return _scroll(inner)

    def _controls_page(self) -> QWidget:
        inner, v = self._page("Standard controls", "Qt widgets styled by the generated QSS")
        grid = QGridLayout()
        grid.setSpacing(SPACE.lg)
        form = QGroupBox("Detection parameters")
        fl = QGridLayout(form)
        fl.setHorizontalSpacing(SPACE.lg)
        fl.setVerticalSpacing(SPACE.md)
        fl.addWidget(QLabel("Sample ID"), 0, 0)
        fl.addWidget(QLineEdit("S-014"), 0, 1)
        fl.addWidget(QLabel("Min grain area (px)"), 1, 0)
        sp = QSpinBox()
        sp.setRange(0, 10000)
        sp.setValue(40)
        fl.addWidget(sp, 1, 1)
        fl.addWidget(QLabel("Pixel size (µm)"), 2, 0)
        dsp = QDoubleSpinBox()
        dsp.setDecimals(3)
        dsp.setValue(0.412)
        fl.addWidget(dsp, 2, 1)
        fl.addWidget(QLabel("Model"), 3, 0)
        cb = QComboBox()
        cb.addItems(["SAM ViT-B (bundled)", "Watershed (classic)", "Hybrid"])
        fl.addWidget(cb, 3, 1)
        fl.addWidget(QLabel("Sensitivity"), 4, 0)
        sl = QSlider(Qt.Horizontal)
        sl.setValue(62)
        fl.addWidget(sl, 4, 1)
        ch = QCheckBox("Exclude edge grains")
        ch.setChecked(True)
        fl.addWidget(ch, 5, 1)
        fl.addWidget(QCheckBox("Fill holes"), 6, 1)
        rr = QHBoxLayout()
        r1 = QRadioButton("Planimetric")
        r1.setChecked(True)
        rr.addWidget(r1)
        rr.setSpacing(SPACE.xl)
        rr.addWidget(QRadioButton("Intercept"))
        rr.addStretch(1)
        fl.addWidget(QLabel("ASTM E112 method"), 7, 0)
        fl.addLayout(rr, 7, 1)
        dis = QLineEdit("Read-only calibration")
        dis.setEnabled(False)
        fl.addWidget(QLabel("Disabled"), 8, 0)
        fl.addWidget(dis, 8, 1)
        bb = QHBoxLayout()
        bb.addStretch(1)
        bb.addWidget(QPushButton("Reset"))
        ok = QPushButton("Apply")
        ok.setProperty("variant", "primary")
        bb.addWidget(ok)
        fl.addLayout(bb, 9, 0, 1, 2)
        grid.addWidget(form, 0, 0)

        tabs = QTabWidget()
        table = QTableWidget(8, 5)
        table.setHorizontalHeaderLabels(["Grain", "Area (µm²)", "ECD (µm)",
                                         "Aspect", "Status"])
        import random
        rnd = random.Random(3)
        for rr_ in range(8):
            area = rnd.uniform(40, 320)
            vals = [f"G-{rr_ + 101}", f"{area:,.1f}", f"{(area / 3.1416) ** 0.5 * 2:.2f}",
                    f"{rnd.uniform(1, 2.4):.2f}", rnd.choice(["OK", "OK", "Edge", "Merged"])]
            for c, val in enumerate(vals):
                it = QTableWidgetItem(val)
                if c in (1, 2, 3):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table.setItem(rr_, c, it)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.selectRow(2)
        table.setShowGrid(False)
        tabs.addTab(table, "Grains")
        tree = QTreeView()
        model = QStandardItemModel()
        model.setHorizontalHeaderLabels(["Workspace", "Images"])
        for proj in ("Alloy 718 qualification", "Ti-6Al-4V AM study"):
            p = QStandardItem(proj)
            for s in ("Sample S-012", "Sample S-013", "Sample S-014"):
                p.appendRow([QStandardItem(s), QStandardItem("12")])
            model.appendRow([p, QStandardItem("36")])
        tree.setModel(model)
        tree.expandAll()
        tabs.addTab(tree, "Workspace")
        tabs.addTab(QWidget(), "Histogram")
        grid.addWidget(tabs, 0, 1)
        prog = QVBoxLayout()
        pb = QProgressBar()
        pb.setValue(73)
        pb.setProperty("labelled", "true")
        prog.addWidget(pb)
        pb2 = QProgressBar()
        pb2.setValue(100)
        pb2.setProperty("tone", "success")
        prog.addWidget(pb2)
        pw = QWidget()
        pw.setLayout(prog)
        grid.addWidget(pw, 1, 0, 1, 2)
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 3)
        v.addLayout(grid)
        v.addStretch(1)
        return _scroll(inner)

    def _states_page(self) -> QWidget:
        inner, v = self._page("States & layout", "CollapsibleSection, KeyValueList, Divider, "
                                                 "EmptyState, Skeleton")
        row = QHBoxLayout()
        row.setSpacing(SPACE.lg)
        meta = Card("Image metadata")
        s1 = CollapsibleSection("Acquisition", expanded=True)
        s1.header_trailing().addWidget(Badge("SEM", "neutral"))
        s1.add_widget(KeyValueList({"Instrument": "Zeiss Sigma 300", "Detector": "SE2",
                                    "Accelerating voltage": "15.0 kV",
                                    "Working distance": "8.6 mm", "Magnification": "1,000×"},
                                   mono_keys=("Accelerating voltage", "Working distance")))
        meta.add_widget(s1)
        meta.add_widget(Divider())
        s2 = CollapsibleSection("Calibration", expanded=True)
        s2.add_widget(KeyValueList([("Pixel size", "0.412 µm/px"),
                                    ("Source", "Scale bar (auto)"),
                                    ("Verified by", "A. Operator")], mono_keys=("Pixel size",)))
        meta.add_widget(s2)
        meta.add_widget(Divider())
        s3 = CollapsibleSection("Processing history", expanded=False)
        s3.add_widget(KeyValueList([("14:32", "Analyze all"), ("14:40", "Merged 3 grains")]))
        meta.add_widget(s3)
        meta.body_layout().addStretch(1)
        row.addWidget(meta, 2)

        empty = Card()
        empty.add_widget(EmptyState("images", "No images in this lot yet",
                                    "Import SEM images to start measuring grains. "
                                    "TIFF, PNG and JPEG are supported.",
                                    "Import images", "import"))
        row.addWidget(empty, 3)

        sk = Card("Loading", "Skeleton placeholders")
        for _ in range(3):
            r = QHBoxLayout()
            r.addWidget(Skeleton(36, 36, "circle"))
            c = QVBoxLayout()
            c.addWidget(Skeleton(shape="text"))
            c.addWidget(Skeleton(width=120, shape="text"))
            r.addLayout(c, 1)
            sk.body_layout().addLayout(r)
        sk.add_widget(Divider(text="Chart"))
        sk.add_widget(Skeleton(height=120))
        sk.body_layout().addStretch(1)
        row.addWidget(sk, 2)
        v.addLayout(row, 1)
        return _scroll(inner)

    def _settings_page(self) -> QWidget:
        inner, v = self._page("Settings", "Appearance")
        c = Card("Appearance")
        c.add_widget(label("Theme", tone="secondary"))
        seg = SegmentedControl(["Dark", "Light"], 0 if current_mode() == "dark" else 1)
        seg.current_text_changed.connect(lambda m: apply_theme(None, m.lower()))
        c.add_widget(seg)
        rm = QCheckBox("Reduce motion (animations complete instantly)")
        rm.toggled.connect(set_reduced_motion)
        c.add_widget(rm)
        v.addWidget(c)
        v.addStretch(1)
        return _scroll(inner)

    def _demo_loading(self) -> None:
        self.run_btn.set_loading(True)
        self.ring.set_value(0, animate=False)
        steps = iter(range(0, 101, 20))

        def tick():
            try:
                self.ring.set_value(next(steps))
            except StopIteration:
                timer.stop()
                self.run_btn.set_loading(False)
                self.toasts.show_toast("Analysis complete", "12 images processed", "success",
                                       "View results")
        timer = QTimer(self)
        timer.timeout.connect(tick)
        timer.start(400)


# ---------------------------------------------------------------------------
# Offscreen capture
# ---------------------------------------------------------------------------


def _pump(app: QApplication, ms: int) -> None:
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < ms:
        app.processEvents()


def capture(out_dir: str) -> list:
    """Render gallery pages in both themes (and mid-transition frames) to PNGs."""
    app = QApplication.instance()
    os.makedirs(out_dir, exist_ok=True)
    saved = []
    for mode in ("dark", "light"):
        apply_theme(app, mode)
        win = GalleryWindow()
        win.show()
        _pump(app, 400)
        for sc in win.stats:
            sc.count_up()
        _pump(app, 900)
        win.toasts.show_toast("Analysis complete", "12 images · 1,284 grains · ASTM G 8.2",
                              "success", "Open report", timeout_ms=0)
        win.toasts.show_toast("Scale bar not found", "Image 7 uses the lot calibration.",
                              "warning", timeout_ms=0)
        _pump(app, 600)
        path = os.path.join(out_dir, f"gallery_{mode}.png")
        win.grab().save(path)
        saved.append(path)
        win.toasts.clear()
        _pump(app, 400)
        for key in ("controls", "states"):
            win.rail.set_current(key)
            _pump(app, 600)
            p = os.path.join(out_dir, f"gallery_{mode}_{key}.png")
            win.grab().save(p)
            saved.append(p)
        win.rail.set_expanded(True)
        win.overlay.open_overlay()
        _pump(app, 600)
        p = os.path.join(out_dir, f"gallery_{mode}_overlay.png")
        win.grab().save(p)
        saved.append(p)
        win.overlay.close_overlay()
        win.rail.set_expanded(False)
        _pump(app, 500)
        if mode == "dark":
            # mid-transition frames: states -> overview, stepping the animation group
            win.rail.set_current("overview")
            group = win.stack.animation_group()
            if group is not None:
                group.pause()
                for ms in (0, 50, 140, 190):
                    group.setCurrentTime(ms)
                    app.processEvents()
                    p = os.path.join(out_dir, f"transition_{ms:03d}ms.png")
                    win.grab().save(p)
                    saved.append(p)
                group.resume()
            _pump(app, 500)
            # nav rail expansion midway
            win.rail.set_expanded(True)
            _pump(app, 110)
            p = os.path.join(out_dir, "rail_expanding.png")
            win.grab().save(p)
            saved.append(p)
        win.close()
        win.deleteLater()
        _pump(app, 100)
    return saved


def main(argv=None) -> int:
    """Entry point for ``python -m ui.gallery``."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--light", action="store_true", help="start in light theme")
    ap.add_argument("--capture", metavar="DIR", help="render PNGs offscreen into DIR and exit")
    args = ap.parse_args(argv)
    if args.capture:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication(sys.argv[:1])
    if args.capture:
        for p in capture(args.capture):
            print(p)
        return 0
    apply_theme(app, "light" if args.light else "dark")
    win = GalleryWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
