"""
Main Application Window - supports multiple images via tabs
"""

import os
import sys
import numpy as np
import cv2

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QStatusBar, QProgressBar, QLabel, QFileDialog, QMessageBox,
    QTabWidget, QFrame, QPushButton
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QAction, QKeySequence

from ui.image_canvas import ImageCanvas
from ui.settings_panel import SettingsPanel
from ui.results_panel import ResultsPanel
from ui.calibration_dialog import CalibrationDialog
from core.grain_detector import GrainDetector, DetectionParams, AnalysisResult
from core.scale_bar import detect_scale_bar_length_px
from utils.excel_export import export_to_excel


class AnalysisWorker(QObject):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, image_bgr, px_per_um, params):
        super().__init__()
        self.image_bgr = image_bgr
        self.px_per_um = px_per_um
        self.params = params

    def run(self):
        try:
            detector = GrainDetector()
            result = detector.analyze(
                self.image_bgr,
                px_per_um=self.px_per_um,
                params=self.params,
                progress_callback=self.progress.emit
            )
            self.finished.emit(result)
        except Exception as e:
            import traceback
            self.error.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


class ImageTab(QWidget):
    """A single image analysis tab."""
    def __init__(self, image_bgr, image_path, parent=None):
        super().__init__(parent)
        self.image_bgr = image_bgr
        self.image_path = image_path
        self.result = None
        self.current_view = "original"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # View toggle bar
        bar = QWidget()
        bar.setStyleSheet("background: #14141a; border-bottom: 1px solid #3c3c48;")
        bar.setFixedHeight(36)
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(8, 4, 8, 4)
        bar_lay.setSpacing(6)

        self.lbl_view = QLabel("Original")
        self.lbl_view.setStyleSheet("color: #7777aa; font-size: 11px;")
        bar_lay.addWidget(self.lbl_view)
        bar_lay.addStretch()

        for label, mode in [("Original", "original"), ("Grain Overlay", "overlay"), ("Binary Mask", "binary")]:
            btn = QPushButton(label)
            btn.setFixedHeight(24)
            btn.setStyleSheet("font-size: 11px; padding: 2px 10px;")
            btn.clicked.connect(lambda checked, m=mode: self.show_view(m))
            bar_lay.addWidget(btn)

        layout.addWidget(bar)

        self.canvas = ImageCanvas()
        self.canvas.set_image(image_bgr)
        layout.addWidget(self.canvas)

    def show_view(self, mode):
        self.current_view = mode
        if mode == "original":
            self.canvas.set_image(self.image_bgr)
            self.lbl_view.setText("Original Image")
        elif mode == "overlay" and self.result and self.result.overlay_image is not None:
            self.canvas.set_image(self.result.overlay_image)
            self.lbl_view.setText("Grain Overlay")
        elif mode == "binary" and self.result and self.result.binary_image is not None:
            binary_bgr = cv2.cvtColor(self.result.binary_image * 255, cv2.COLOR_GRAY2BGR)
            self.canvas.set_image(binary_bgr)
            self.lbl_view.setText("Binary Mask")
        else:
            self.canvas.set_image(self.image_bgr)
            self.lbl_view.setText("Original Image")

    def set_result(self, result):
        self.result = result
        self.show_view("overlay")


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("SEM Grain Analyzer")
        self.setMinimumSize(1100, 700)
        self.resize(1380, 860)

        self._px_per_um = 0.0
        self._worker = None
        self._thread = None
        self._image_tabs = []   # list of ImageTab

        self._build_ui()
        self._build_menu()
        self._build_statusbar()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_lay = QHBoxLayout(central)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        self.settings = SettingsPanel()
        self.settings.open_image.connect(self.open_images)
        self.settings.set_calibration.connect(self.open_calibration)
        self.settings.run_analysis.connect(self.run_analysis)
        self.settings.export_excel.connect(self.export_excel)
        root_lay.addWidget(self.settings)

        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setStyleSheet("color: #3c3c48;")
        root_lay.addWidget(div)

        # Centre: tabbed image area
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.tabCloseRequested.connect(self._close_tab)
        self.tab_widget.setStyleSheet("QTabWidget::pane { border: none; }")

        self.empty_label = QLabel("Open one or more SEM images to begin\n\nFile → Open Images  (Ctrl+O)")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet("color: #555577; font-size: 14px;")

        self.centre_stack = QWidget()
        stack_lay = QVBoxLayout(self.centre_stack)
        stack_lay.setContentsMargins(0, 0, 0, 0)
        stack_lay.addWidget(self.empty_label)
        stack_lay.addWidget(self.tab_widget)
        self.tab_widget.setVisible(False)

        self.results = ResultsPanel()

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.centre_stack)
        splitter.addWidget(self.results)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([950, 400])
        root_lay.addWidget(splitter, 1)

    def _build_menu(self):
        mb = self.menuBar()
        file_menu = mb.addMenu("&File")

        act_open = QAction("&Open Images...", self)
        act_open.setShortcut(QKeySequence.StandardKey.Open)
        act_open.triggered.connect(self.open_images)
        file_menu.addAction(act_open)

        act_export = QAction("&Export to Excel...", self)
        act_export.setShortcut(QKeySequence("Ctrl+E"))
        act_export.triggered.connect(self.export_excel)
        file_menu.addAction(act_export)

        act_export_all = QAction("Export &All to Excel...", self)
        act_export_all.setShortcut(QKeySequence("Ctrl+Shift+E"))
        act_export_all.triggered.connect(self.export_all_excel)
        file_menu.addAction(act_export_all)

        file_menu.addSeparator()
        act_quit = QAction("&Quit", self)
        act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        analysis_menu = mb.addMenu("&Analysis")
        act_run = QAction("&Run Analysis (current image)", self)
        act_run.setShortcut(QKeySequence("F5"))
        act_run.triggered.connect(self.run_analysis)
        analysis_menu.addAction(act_run)

        act_run_all = QAction("Run Analysis on &All Images", self)
        act_run_all.setShortcut(QKeySequence("Ctrl+F5"))
        act_run_all.triggered.connect(self.run_analysis_all)
        analysis_menu.addAction(act_run_all)

        act_cal = QAction("Set &Calibration...", self)
        act_cal.setShortcut(QKeySequence("Ctrl+K"))
        act_cal.triggered.connect(self.open_calibration)
        analysis_menu.addAction(act_cal)

        help_menu = mb.addMenu("&Help")
        act_about = QAction("&About", self)
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

    def _build_statusbar(self):
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(200)
        self.progress_bar.setFixedHeight(16)
        self.progress_bar.setVisible(False)
        self.status.addPermanentWidget(self.progress_bar)
        self.status_msg = QLabel("Ready — open one or more SEM images to begin.")
        self.status.addWidget(self.status_msg)

    # ------------------------------------------------------------------ Actions

    def open_images(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open SEM Images", "",
            "Image Files (*.tif *.tiff *.png *.jpg *.jpeg *.bmp);;All Files (*)"
        )
        if not paths:
            return
        for path in paths:
            self._load_image(path)

    def _load_image(self, path):
        try:
            img = cv2.imread(path, cv2.IMREAD_COLOR)
            if img is None:
                raise ValueError(f"Could not read: {path}")

            tab = ImageTab(img, path)
            self._image_tabs.append(tab)

            name = os.path.basename(path)
            idx = self.tab_widget.addTab(tab, name)
            self.tab_widget.setCurrentIndex(idx)

            self.empty_label.setVisible(False)
            self.tab_widget.setVisible(True)
            self.settings.set_image_name(f"{len(self._image_tabs)} image(s) loaded")
            self.settings.set_analyze_enabled(True)

            h, w = img.shape[:2]
            self._set_status(f"Loaded: {name}  ({w}x{h} px)")

            bar_px, _ = detect_scale_bar_length_px(img)
            if bar_px:
                tab._auto_bar_px = bar_px
        except Exception as e:
            QMessageBox.critical(self, "Error Loading Image", str(e))

    def _close_tab(self, index):
        self.tab_widget.removeTab(index)
        self._image_tabs.pop(index)
        if not self._image_tabs:
            self.empty_label.setVisible(True)
            self.tab_widget.setVisible(False)
            self.settings.set_analyze_enabled(False)
            self.settings.set_export_enabled(False)
        self.settings.set_image_name(f"{len(self._image_tabs)} image(s) loaded")

    def _current_tab(self):
        idx = self.tab_widget.currentIndex()
        if idx < 0 or idx >= len(self._image_tabs):
            return None
        return self._image_tabs[idx]

    def open_calibration(self):
        tab = self._current_tab()
        if tab is None:
            QMessageBox.information(self, "No Image", "Load an image first.")
            return
        auto_bar = getattr(tab, '_auto_bar_px', None)
        dlg = CalibrationDialog(image_bgr=tab.image_bgr, auto_bar_px=auto_bar, parent=self)
        dlg.calibration_set.connect(self._apply_calibration)
        dlg.exec()

    def _apply_calibration(self, px_per_um):
        self._px_per_um = px_per_um
        self.settings.set_calibration_label(px_per_um)
        self._set_status(f"Calibration set: {px_per_um:.4f} px/um")

    def run_analysis(self):
        tab = self._current_tab()
        if tab is None:
            QMessageBox.information(self, "No Image", "Please open an SEM image first.")
            return
        self._run_on_tab(tab)

    def run_analysis_all(self):
        if not self._image_tabs:
            QMessageBox.information(self, "No Images", "Please open SEM images first.")
            return
        # Queue all tabs - run first one, chain the rest
        self._pending_tabs = list(self._image_tabs)
        self._run_next_pending()

    def _run_next_pending(self):
        if not hasattr(self, '_pending_tabs') or not self._pending_tabs:
            self._set_status(f"All {len(self._image_tabs)} images analysed.")
            self.settings.set_export_enabled(True)
            return
        tab = self._pending_tabs.pop(0)
        self._run_on_tab(tab, on_done=self._run_next_pending)

    def _run_on_tab(self, tab, on_done=None):
        if self._thread and self._thread.isRunning():
            QMessageBox.information(self, "Busy", "Analysis already running, please wait.")
            return

        params = self.settings.get_params()
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.settings.btn_analyze.setEnabled(False)
        self._set_status(f"Analysing {os.path.basename(tab.image_path)}...")

        self._thread = QThread()
        self._worker = AnalysisWorker(tab.image_bgr, self._px_per_um, params)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(lambda r: self._on_done(r, tab, on_done))
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _on_progress(self, pct, msg):
        self.progress_bar.setValue(pct)
        self._set_status(msg)

    def _on_done(self, result, tab, on_done=None):
        tab.set_result(result)
        self.progress_bar.setVisible(False)
        self.settings.btn_analyze.setEnabled(True)
        self.settings.set_export_enabled(True)
        self.results.display_results(result)
        # Update tab title to show grain count
        idx = self._image_tabs.index(tab)
        name = os.path.basename(tab.image_path)
        self.tab_widget.setTabText(idx, f"{name} ({result.grain_count}g)")
        self._set_status(
            f"{os.path.basename(tab.image_path)}: {result.grain_count} grains"
            + (f", mean area {result.mean_area_um2:.3f} um2" if result.has_calibration else "")
        )
        if on_done:
            on_done()

    def _on_error(self, msg):
        self.progress_bar.setVisible(False)
        self.settings.btn_analyze.setEnabled(True)
        QMessageBox.critical(self, "Analysis Error", msg)

    def _cleanup_thread(self):
        if self._thread:
            self._thread.deleteLater()
        if self._worker:
            self._worker.deleteLater()
        self._thread = None
        self._worker = None

    def export_excel(self):
        tab = self._current_tab()
        if tab is None or tab.result is None:
            QMessageBox.information(self, "No Results", "Run analysis on this image first.")
            return
        default = os.path.splitext(os.path.basename(tab.image_path))[0] + "_grains.xlsx"
        path, _ = QFileDialog.getSaveFileName(self, "Save Excel Report", default, "Excel Files (*.xlsx)")
        if not path:
            return
        try:
            export_to_excel(tab.result, tab.image_path, path)
            self._set_status(f"Exported: {os.path.basename(path)}")
            if QMessageBox.question(self, "Exported", f"Saved to:\n{path}\n\nOpen now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
                import subprocess
                if sys.platform == "win32": os.startfile(path)
                elif sys.platform == "darwin": subprocess.run(["open", path])
                else: subprocess.run(["xdg-open", path])
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    def export_all_excel(self):
        analysed = [t for t in self._image_tabs if t.result is not None]
        if not analysed:
            QMessageBox.information(self, "No Results", "Run analysis on at least one image first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save Combined Excel Report",
            "all_images_grains.xlsx", "Excel Files (*.xlsx)")
        if not path:
            return
        try:
            import openpyxl
            wb = openpyxl.Workbook()
            wb.remove(wb.active)
            from utils.excel_export import _write_summary_sheet, _write_grains_sheet, _write_distribution_sheet
            for tab in analysed:
                sheet_name = os.path.splitext(os.path.basename(tab.image_path))[0][:28]
                # Write each image as its own set of sheets
                _write_summary_sheet(wb, tab.result, tab.image_path, None)
                # Rename last sheet to include image name
                last = wb.worksheets[-1]
                last.title = f"{sheet_name}-Summary"[:31]
                _write_grains_sheet(wb, tab.result)
                wb.worksheets[-1].title = f"{sheet_name}-Grains"[:31]
            wb.save(path)
            self._set_status(f"Exported {len(analysed)} images to {os.path.basename(path)}")
            if QMessageBox.question(self, "Exported", f"Combined report saved.\n\nOpen now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
                import subprocess
                if sys.platform == "win32": os.startfile(path)
                elif sys.platform == "darwin": subprocess.run(["open", path])
                else: subprocess.run(["xdg-open", path])
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    def _show_about(self):
        QMessageBox.about(self, "About SEM Grain Analyzer",
            "<h2>SEM Grain Analyzer v1.1</h2>"
            "<p>Multi-image grain analysis for SEM images.</p>"
            "<p>Open multiple images at once. Analyse each individually or all at once.</p>")

    def _set_status(self, msg):
        self.status_msg.setText(msg)
