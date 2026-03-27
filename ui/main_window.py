

"""
Main Application Window v1.1
- Multiple images via tabs
- Auto scale bar detection (no manual input)
- Analyze ALL images simultaneously
- Click grain + Delete to remove from dataset
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
from core.scale_bar import auto_detect_scale_bar, detect_scale_bar_length_px
from utils.excel_export import export_to_excel


class AnalysisWorker(QObject):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object)
    error    = pyqtSignal(str)

    def __init__(self, image_bgr, px_per_um, params):
        super().__init__()
        self.image_bgr = image_bgr
        self.px_per_um = px_per_um
        self.params    = params

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
    """One tab = one SEM image."""

    grain_deleted = pyqtSignal(int)   # grain_id deleted

    def __init__(self, image_bgr, image_path, parent=None):
        super().__init__(parent)
        self.image_bgr  = image_bgr
        self.image_path = image_path
        self.result     = None
        self._current_view = "original"

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

        for label, mode in [("Original", "original"),
                             ("Grain Overlay", "overlay"),
                             ("Binary Mask", "binary")]:
            btn = QPushButton(label)
            btn.setFixedHeight(24)
            btn.setStyleSheet("font-size: 11px; padding: 2px 10px;")
            btn.clicked.connect(lambda checked, m=mode: self.show_view(m))
            bar_lay.addWidget(btn)

        layout.addWidget(bar)

        self.canvas = ImageCanvas()
        self.canvas.set_image(image_bgr)
        # Connect grain click → delete handler
        self.canvas.grain_clicked.connect(self._on_grain_clicked)
        layout.addWidget(self.canvas)

    def _on_grain_clicked(self, ix, grain_id_or_y):
        """ix == -1 means Delete was pressed; grain_id_or_y is then the grain_id."""
        if ix == -1:
            grain_id = grain_id_or_y
            self._delete_grain(grain_id)

    def _delete_grain(self, grain_id: int):
        if self.result is None:
            return
        # Remove from grains list
        before = len(self.result.grains)
        self.result.grains = [g for g in self.result.grains if g.grain_id != grain_id]
        self.result.grain_count = len(self.result.grains)

        if len(self.result.grains) == before:
            return  # nothing removed

        # Zero out that label in label_image so overlay redraws cleanly
        if self.result.label_image is not None:
            self.result.label_image[self.result.label_image == grain_id] = 0

        # Recompute statistics
        self._recompute_stats()

        # Redraw overlay
        detector = GrainDetector()
        self.result.overlay_image = detector._draw_overlay(
            self.image_bgr, self.result.label_image, self.result.grains
        )

        self.show_view("overlay")
        self.grain_deleted.emit(grain_id)

    def _recompute_stats(self):
        import numpy as _np
        r = self.result
        if not r.grains:
            return
        if r.has_calibration:
            areas = _np.array([g.area_um2 for g in r.grains])
            diams = _np.array([g.equivalent_diameter_um for g in r.grains])
            r.mean_area_um2    = float(_np.mean(areas))
            r.std_area_um2     = float(_np.std(areas))
            r.median_area_um2  = float(_np.median(areas))
            r.min_area_um2     = float(_np.min(areas))
            r.max_area_um2     = float(_np.max(areas))
            r.mean_diameter_um = float(_np.mean(diams))
            r.std_diameter_um  = float(_np.std(diams))
            if r.total_analyzed_area_um2 > 0:
                r.grain_coverage_pct = float(_np.sum(areas)) / r.total_analyzed_area_um2 * 100
        circs   = _np.array([g.circularity  for g in r.grains])
        aspects = _np.array([g.aspect_ratio for g in r.grains])
        r.mean_circularity  = float(_np.mean(circs))
        r.mean_aspect_ratio = float(_np.mean(aspects))

    def show_view(self, mode: str):
        self._current_view = mode
        if mode == "original":
            self.canvas.set_image(self.image_bgr)
            self.canvas.set_label_image(None)
            self.lbl_view.setText("Original Image")
        elif mode == "overlay" and self.result and self.result.overlay_image is not None:
            self.canvas.set_image(self.result.overlay_image)
            self.canvas.set_label_image(self.result.label_image)
            self.lbl_view.setText("Grain Overlay  —  click a grain, then Delete to remove")
        elif mode == "binary" and self.result and self.result.binary_image is not None:
            binary_bgr = cv2.cvtColor(self.result.binary_image * 255, cv2.COLOR_GRAY2BGR)
            self.canvas.set_image(binary_bgr)
            self.canvas.set_label_image(None)
            self.lbl_view.setText("Binary Mask")
        else:
            self.canvas.set_image(self.image_bgr)
            self.canvas.set_label_image(None)

    def set_result(self, result: AnalysisResult):
        self.result = result
        self.show_view("overlay")


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("SEM Grain Analyzer")
        self.setMinimumSize(1200, 720)
        self.resize(1440, 880)

        self._px_per_um   = 0.0
        self._worker      = None
        self._thread      = None
        self._image_tabs: list[ImageTab] = []
        self._pending_tabs: list[ImageTab] = []

        self._build_ui()
        self._build_menu()
        self._build_statusbar()

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_lay = QHBoxLayout(central)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        self.settings = SettingsPanel()
        self.settings.open_image.connect(self.open_images)
        self.settings.set_calibration.connect(self.auto_detect_scale_bar)
        self.settings.run_analysis.connect(self.run_analysis_all)
        self.settings.export_excel.connect(self.export_all_excel)
        root_lay.addWidget(self.settings)

        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setStyleSheet("color: #3c3c48;")
        root_lay.addWidget(div)

        # Centre: tabs
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.tabCloseRequested.connect(self._close_tab)
        self.tab_widget.currentChanged.connect(self._on_tab_changed)

        self.empty_label = QLabel(
            "Open one or more SEM images to begin\n\n"
            "File → Open Images  (Ctrl+O)\n\n"
            "Hold Ctrl while selecting to open multiple images at once"
        )
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet("color: #555577; font-size: 13px;")

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
        splitter.setSizes([980, 420])
        root_lay.addWidget(splitter, 1)

    def _build_menu(self):
        mb = self.menuBar()

        file_menu = mb.addMenu("&File")
        act_open = QAction("&Open Images...", self)
        act_open.setShortcut(QKeySequence.StandardKey.Open)
        act_open.triggered.connect(self.open_images)
        file_menu.addAction(act_open)

        act_export_all = QAction("Export &All to Excel...", self)
        act_export_all.setShortcut(QKeySequence("Ctrl+E"))
        act_export_all.triggered.connect(self.export_all_excel)
        file_menu.addAction(act_export_all)

        act_export_cur = QAction("Export &Current to Excel...", self)
        act_export_cur.setShortcut(QKeySequence("Ctrl+Shift+E"))
        act_export_cur.triggered.connect(self.export_current_excel)
        file_menu.addAction(act_export_cur)

        file_menu.addSeparator()
        act_quit = QAction("&Quit", self)
        act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        analysis_menu = mb.addMenu("&Analysis")

        act_scalebar = QAction("Auto-Detect &Scale Bar", self)
        act_scalebar.setShortcut(QKeySequence("Ctrl+K"))
        act_scalebar.triggered.connect(self.auto_detect_scale_bar)
        analysis_menu.addAction(act_scalebar)

        act_run_all = QAction("Analyze &All Images", self)
        act_run_all.setShortcut(QKeySequence("F5"))
        act_run_all.triggered.connect(self.run_analysis_all)
        analysis_menu.addAction(act_run_all)

        act_run_cur = QAction("Analyze &Current Image", self)
        act_run_cur.setShortcut(QKeySequence("Ctrl+F5"))
        act_run_cur.triggered.connect(self.run_analysis_current)
        analysis_menu.addAction(act_run_cur)

        help_menu = mb.addMenu("&Help")
        act_about = QAction("&About", self)
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

    def _build_statusbar(self):
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(220)
        self.progress_bar.setFixedHeight(16)
        self.progress_bar.setVisible(False)
        self.status.addPermanentWidget(self.progress_bar)
        self.status_msg = QLabel("Ready — open one or more SEM images to begin.")
        self.status.addWidget(self.status_msg)

    # ------------------------------------------------------------------ Image loading

    def open_images(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open SEM Images", "",
            "Image Files (*.tif *.tiff *.png *.jpg *.jpeg *.bmp);;All Files (*)"
        )
        if not paths:
            return
        for path in paths:
            self._load_image(path)
        self._set_status(f"{len(self._image_tabs)} image(s) loaded.")

    def _load_image(self, path: str):
        try:
            img = cv2.imread(path, cv2.IMREAD_COLOR)
            if img is None:
                raise ValueError(f"Could not read: {path}")

            tab = ImageTab(img, path)
            tab.grain_deleted.connect(self._on_grain_deleted)
            self._image_tabs.append(tab)

            name = os.path.basename(path)
            idx = self.tab_widget.addTab(tab, name)
            self.tab_widget.setCurrentIndex(idx)

            self.empty_label.setVisible(False)
            self.tab_widget.setVisible(True)
            self.settings.set_image_name(f"{len(self._image_tabs)} image(s) loaded")
            self.settings.set_analyze_enabled(True)

        except Exception as e:
            QMessageBox.critical(self, "Error Loading Image", str(e))

    def _close_tab(self, index: int):
        self.tab_widget.removeTab(index)
        if index < len(self._image_tabs):
            self._image_tabs.pop(index)
        if not self._image_tabs:
            self.empty_label.setVisible(True)
            self.tab_widget.setVisible(False)
            self.settings.set_analyze_enabled(False)
            self.settings.set_export_enabled(False)
        self.settings.set_image_name(f"{len(self._image_tabs)} image(s) loaded")

    def _current_tab(self) -> ImageTab | None:
        idx = self.tab_widget.currentIndex()
        if idx < 0 or idx >= len(self._image_tabs):
            return None
        return self._image_tabs[idx]

    def _on_tab_changed(self, idx: int):
        if 0 <= idx < len(self._image_tabs):
            tab = self._image_tabs[idx]
            if tab.result:
                self.results.display_results(tab.result)

    def _on_grain_deleted(self, grain_id: int):
        tab = self._current_tab()
        if tab and tab.result:
            self.results.display_results(tab.result)
            idx = self._image_tabs.index(tab)
            name = os.path.basename(tab.image_path)
            self.tab_widget.setTabText(idx, f"{name} ({tab.result.grain_count}g)")
            self._set_status(f"Grain {grain_id} removed. {tab.result.grain_count} grains remain.")

    # ------------------------------------------------------------------ Scale bar

    def auto_detect_scale_bar(self):
        if not self._image_tabs:
            QMessageBox.information(self, "No Images", "Load images first.")
            return

        self._set_status("Auto-detecting scale bar on all images...")
        found_any = False

        for tab in self._image_tabs:
            px_per_um, annotated = auto_detect_scale_bar(tab.image_bgr)
            if px_per_um and px_per_um > 0:
                # Store annotated version so we can show it
                tab._annotated_image = annotated
                found_any = True

        if found_any:
            # Use the value from the first successful detection
            for tab in self._image_tabs:
                px_per_um, _ = auto_detect_scale_bar(tab.image_bgr)
                if px_per_um and px_per_um > 0:
                    self._px_per_um = px_per_um
                    break

            self.settings.set_calibration_label(self._px_per_um)

            # Show annotated images in each tab
            for i, tab in enumerate(self._image_tabs):
                ann = getattr(tab, '_annotated_image', None)
                if ann is not None:
                    tab.canvas.set_image(ann)
                    tab.lbl_view.setText("Scale bar detected (highlighted in green)")

            self._set_status(
                f"Scale bar auto-detected: {self._px_per_um:.4f} px/µm  "
                f"Applied to all {len(self._image_tabs)} images."
            )
        else:
            # Fallback to manual dialog for first image
            tab = self._current_tab()
            if tab:
                auto_bar, _ = detect_scale_bar_length_px(tab.image_bgr)
                dlg = CalibrationDialog(
                    image_bgr=tab.image_bgr,
                    auto_bar_px=auto_bar,
                    parent=self
                )
                dlg.calibration_set.connect(self._apply_calibration)
                QMessageBox.information(
                    self, "Auto-detect failed",
                    "Could not automatically read the scale bar label.\n\n"
                    "Please enter the values manually in the dialog.\n"
                    "(Tip: install pytesseract for better OCR support)"
                )
                dlg.exec()

    def _apply_calibration(self, px_per_um: float):
        self._px_per_um = px_per_um
        self.settings.set_calibration_label(px_per_um)
        self._set_status(f"Calibration set: {px_per_um:.4f} px/µm — applied to all images.")

    # ------------------------------------------------------------------ Analysis

    def run_analysis_all(self):
        if not self._image_tabs:
            QMessageBox.information(self, "No Images", "Open images first.")
            return
        self._pending_tabs = list(self._image_tabs)
        self._run_next_pending()

    def run_analysis_current(self):
        tab = self._current_tab()
        if tab is None:
            QMessageBox.information(self, "No Image", "Open an image first.")
            return
        self._pending_tabs = [tab]
        self._run_next_pending()

    def _run_next_pending(self):
        if not self._pending_tabs:
            self._set_status(
                f"All images analysed — "
                f"{sum(t.result.grain_count for t in self._image_tabs if t.result)} total grains."
            )
            self.settings.set_export_enabled(True)
            return
        tab = self._pending_tabs.pop(0)
        self._run_on_tab(tab)

    def _run_on_tab(self, tab: ImageTab):
        if self._thread and self._thread.isRunning():
            # Queue and wait
            self._pending_tabs.insert(0, tab)
            return

        params = self.settings.get_params()
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.settings.btn_analyze.setEnabled(False)

        remaining = len(self._pending_tabs) + 1
        total = len(self._image_tabs)
        self._set_status(
            f"Analysing {os.path.basename(tab.image_path)} "
            f"({total - remaining + 1}/{total})..."
        )

        self._thread = QThread()
        self._worker = AnalysisWorker(tab.image_bgr, self._px_per_um, params)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(lambda r, t=tab: self._on_done(r, t))
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _on_progress(self, pct: int, msg: str):
        self.progress_bar.setValue(pct)
        self._set_status(msg)

    def _on_done(self, result: AnalysisResult, tab: ImageTab):
        tab.set_result(result)
        self.progress_bar.setVisible(False)
        self.settings.btn_analyze.setEnabled(True)
        self.settings.set_export_enabled(True)

        idx = self._image_tabs.index(tab)
        name = os.path.basename(tab.image_path)
        self.tab_widget.setTabText(idx, f"{name} ({result.grain_count}g)")

        if tab == self._current_tab():
            self.results.display_results(result)

        self._set_status(
            f"{name}: {result.grain_count} grains"
            + (f", mean {result.mean_area_um2:.3f} µm²" if result.has_calibration else "")
        )
        self._run_next_pending()

    def _on_error(self, msg: str):
        self.progress_bar.setVisible(False)
        self.settings.btn_analyze.setEnabled(True)
        QMessageBox.critical(self, "Analysis Error", msg)
        self._run_next_pending()

    def _cleanup_thread(self):
        if self._thread:
            self._thread.deleteLater()
        if self._worker:
            self._worker.deleteLater()
        self._thread = None
        self._worker = None

    # ------------------------------------------------------------------ Export

    def export_all_excel(self):
        analysed = [t for t in self._image_tabs if t.result is not None]
        if not analysed:
            QMessageBox.information(self, "No Results", "Run analysis first.")
            return

        if len(analysed) == 1:
            self.export_current_excel()
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Save Combined Excel Report",
            "all_images_grains.xlsx", "Excel Files (*.xlsx)"
        )
        if not path:
            return
        try:
            import openpyxl
            from utils.excel_export import (
                _write_summary_sheet, _write_grains_sheet, _write_distribution_sheet
            )
            wb = openpyxl.Workbook()
            wb.remove(wb.active)
            for tab in analysed:
                base = os.path.splitext(os.path.basename(tab.image_path))[0][:20]
                _write_summary_sheet(wb, tab.result, tab.image_path, None)
                wb.worksheets[-1].title = f"{base}-Summary"[:31]
                _write_grains_sheet(wb, tab.result)
                wb.worksheets[-1].title = f"{base}-Grains"[:31]
                _write_distribution_sheet(wb, tab.result)
                wb.worksheets[-1].title = f"{base}-Dist"[:31]
            wb.save(path)
            self._set_status(f"Exported {len(analysed)} images → {os.path.basename(path)}")
            self._open_file(path)
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    def export_current_excel(self):
        tab = self._current_tab()
        if tab is None or tab.result is None:
            QMessageBox.information(self, "No Results", "Run analysis on this image first.")
            return
        default = os.path.splitext(os.path.basename(tab.image_path))[0] + "_grains.xlsx"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Excel Report", default, "Excel Files (*.xlsx)"
        )
        if not path:
            return
        try:
            export_to_excel(tab.result, tab.image_path, path)
            self._set_status(f"Exported: {os.path.basename(path)}")
            self._open_file(path)
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    def _open_file(self, path: str):
        import subprocess
        if QMessageBox.question(
            self, "Export Complete",
            f"Saved to:\n{path}\n\nOpen it now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.run(["open", path])
            else:
                subprocess.run(["xdg-open", path])

    def _show_about(self):
        QMessageBox.about(
            self, "About SEM Grain Analyzer",
            "<h2>SEM Grain Analyzer v1.1</h2>"
            "<p>Multi-image SEM grain analysis tool.</p>"
            "<ul>"
            "<li>Open multiple images at once (hold Ctrl)</li>"
            "<li>Auto-detect scale bar from image</li>"
            "<li>Analyze all images simultaneously</li>"
            "<li>Click a grain → press Delete to remove it</li>"
            "<li>Export combined Excel report</li>"
            "</ul>"
        )

    def _set_status(self, msg: str):
        self.status_msg.setText(msg)
