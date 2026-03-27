"""
Main Application Window v1.3
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
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject, QTimer
from PyQt6.QtGui import QAction, QKeySequence

from ui.image_canvas import ImageCanvas
from ui.settings_panel import SettingsPanel
from ui.results_panel import ResultsPanel
from ui.calibration_dialog import CalibrationDialog
from ui.scan_area_dialog import ScanAreaDialog
from ui.analysis_progress_dialog import AnalysisProgressDialog
from core.grain_detector import GrainDetector, DetectionParams, AnalysisResult
from utils.excel_export import export_to_excel


class AnalysisWorker(QObject):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object)
    error    = pyqtSignal(str)

    def __init__(self, image_bgr, px_per_um, params, scan_rect=None):
        super().__init__()
        self.image_bgr = image_bgr
        self.px_per_um = px_per_um
        self.params    = params
        self.scan_rect = scan_rect   # (x,y,w,h) or None

    def run(self):
        try:
            # Crop to scan area if set
            img = self.image_bgr
            offset_x, offset_y = 0, 0
            if self.scan_rect:
                x, y, w, h = self.scan_rect
                x  = max(0, x);  y  = max(0, y)
                w  = min(w, img.shape[1] - x)
                h  = min(h, img.shape[0] - y)
                if w > 10 and h > 10:
                    img = img[y:y+h, x:x+w]
                    offset_x, offset_y = x, y

            detector = GrainDetector()
            result = detector.analyze(
                img,
                px_per_um=self.px_per_um,
                params=self.params,
                progress_callback=self.progress.emit
            )

            # Shift grain centroids back to full-image coords
            if (offset_x or offset_y) and result.grains:
                for g in result.grains:
                    g.centroid_x += offset_x
                    g.centroid_y += offset_y
                # Pad overlay and label images back to full size
                if result.overlay_image is not None:
                    full_overlay = self.image_bgr.copy()
                    full_overlay[offset_y:offset_y+img.shape[0],
                                 offset_x:offset_x+img.shape[1]] = result.overlay_image
                    result.overlay_image = full_overlay
                if result.label_image is not None:
                    full_label = np.zeros(self.image_bgr.shape[:2], dtype=result.label_image.dtype)
                    full_label[offset_y:offset_y+img.shape[0],
                               offset_x:offset_x+img.shape[1]] = result.label_image
                    result.label_image = full_label
                if result.binary_image is not None:
                    full_bin = np.zeros(self.image_bgr.shape[:2], dtype=result.binary_image.dtype)
                    full_bin[offset_y:offset_y+img.shape[0],
                             offset_x:offset_x+img.shape[1]] = result.binary_image
                    result.binary_image = full_bin

            self.finished.emit(result)
        except Exception as e:
            import traceback
            self.error.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


class ImageTab(QWidget):
    grain_deleted = pyqtSignal(int)

    def __init__(self, image_bgr, image_path, parent=None):
        super().__init__(parent)
        self.image_bgr  = image_bgr
        self.image_path = image_path
        self.result     = None
        self.scan_rect  = None   # (x,y,w,h) or None
        self._current_view = "original"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

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
        self.canvas.grain_clicked.connect(self._on_grain_clicked)
        layout.addWidget(self.canvas)

    def _on_grain_clicked(self, ix, grain_id):
        if ix == -1:
            self._delete_grain(grain_id)

    def _delete_grain(self, grain_id: int):
        if self.result is None:
            return
        before = len(self.result.grains)
        self.result.grains = [g for g in self.result.grains if g.grain_id != grain_id]
        self.result.grain_count = len(self.result.grains)
        if len(self.result.grains) == before:
            return
        if self.result.label_image is not None:
            self.result.label_image[self.result.label_image == grain_id] = 0
        self._recompute_stats()
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
            r.mean_area_um2   = float(_np.mean(areas))
            r.std_area_um2    = float(_np.std(areas))
            r.median_area_um2 = float(_np.median(areas))
            r.min_area_um2    = float(_np.min(areas))
            r.max_area_um2    = float(_np.max(areas))
            r.mean_diameter_um = float(_np.mean(diams))
            r.std_diameter_um  = float(_np.std(diams))
            if r.total_analyzed_area_um2 > 0:
                r.grain_coverage_pct = float(_np.sum(areas)) / r.total_analyzed_area_um2 * 100
        r.mean_circularity  = float(_np.mean(_np.array([g.circularity  for g in r.grains])))
        r.mean_aspect_ratio = float(_np.mean(_np.array([g.aspect_ratio for g in r.grains])))

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
        self.setMinimumSize(1100, 700)
        self.resize(1440, 880)

        self._px_per_um    = 0.0
        self._scan_rect    = None   # shared across all images unless per-image override
        self._worker       = None
        self._thread       = None
        self._image_tabs: list[ImageTab] = []
        self._pending_tabs: list[ImageTab] = []
        self._pending_indices: list[int]   = []
        self._progress_dlg: AnalysisProgressDialog | None = None
        self._cancelled    = False
        self._switching_tab = False   # guard against crash during tab switch

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
        self.settings.set_scan_area.connect(self.open_scan_area)
        self.settings.run_analysis.connect(self.run_analysis_all)
        self.settings.run_analysis_current.connect(self.run_analysis_current)
        self.settings.export_excel.connect(self.export_all_excel)

        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setStyleSheet("color: #3c3c48;")

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

        right_splitter = QSplitter(Qt.Orientation.Horizontal)
        right_splitter.addWidget(self.centre_stack)
        right_splitter.addWidget(self.results)
        right_splitter.setStretchFactor(0, 3)
        right_splitter.setStretchFactor(1, 1)
        right_splitter.setSizes([980, 420])

        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_splitter.addWidget(self.settings)
        main_splitter.addWidget(div)
        main_splitter.addWidget(right_splitter)
        main_splitter.setStretchFactor(0, 0)
        main_splitter.setStretchFactor(1, 0)
        main_splitter.setStretchFactor(2, 1)
        main_splitter.setSizes([340, 1, 1099])

        root_lay.addWidget(main_splitter)

    def _build_menu(self):
        mb = self.menuBar()
        fm = mb.addMenu("&File")

        a = QAction("&Open Images...", self)
        a.setShortcut(QKeySequence.StandardKey.Open)
        a.triggered.connect(self.open_images)
        fm.addAction(a)

        a = QAction("Export &All to Excel...", self)
        a.setShortcut(QKeySequence("Ctrl+E"))
        a.triggered.connect(self.export_all_excel)
        fm.addAction(a)

        a = QAction("Export &Current to Excel...", self)
        a.setShortcut(QKeySequence("Ctrl+Shift+E"))
        a.triggered.connect(self.export_current_excel)
        fm.addAction(a)

        fm.addSeparator()
        a = QAction("&Quit", self)
        a.setShortcut(QKeySequence.StandardKey.Quit)
        a.triggered.connect(self.close)
        fm.addAction(a)

        am = mb.addMenu("&Analysis")

        a = QAction("Set &Scale Bar...", self)
        a.setShortcut(QKeySequence("Ctrl+K"))
        a.triggered.connect(self.open_calibration)
        am.addAction(a)

        a = QAction("Set &Scan Area...", self)
        a.setShortcut(QKeySequence("Ctrl+R"))
        a.triggered.connect(self.open_scan_area)
        am.addAction(a)

        a = QAction("Analyze &All Images", self)
        a.setShortcut(QKeySequence("F5"))
        a.triggered.connect(self.run_analysis_all)
        am.addAction(a)

        a = QAction("Analyze &Current Image", self)
        a.setShortcut(QKeySequence("Ctrl+F5"))
        a.triggered.connect(self.run_analysis_current)
        am.addAction(a)

        hm = mb.addMenu("&Help")
        a = QAction("&About", self)
        a.triggered.connect(self._show_about)
        hm.addAction(a)

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

    # ------------------------------------------------------------------ Images

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
        if self._switching_tab:
            return
        self._switching_tab = True
        # Clear results panel immediately to avoid loading large stale data
        self.results.clear()
        # Use a short timer to load the new tab's results AFTER the UI has settled
        QTimer.singleShot(150, lambda: self._load_tab_results(idx))

    def _load_tab_results(self, idx: int):
        self._switching_tab = False
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

    # ------------------------------------------------------------------ Calibration

    def open_calibration(self):
        tab = self._current_tab()
        if tab is None:
            QMessageBox.information(self, "No Image", "Open an image first.")
            return
        dlg = CalibrationDialog(image_bgr=tab.image_bgr, parent=self)
        dlg.calibration_set.connect(self._apply_calibration)
        dlg.exec()

    def _apply_calibration(self, px_per_um: float):
        self._px_per_um = px_per_um
        self.settings.set_calibration_label(px_per_um)
        self._set_status(f"Calibration set: {px_per_um:.4f} px/µm — applied to all images.")

    # ------------------------------------------------------------------ Scan area

    def open_scan_area(self):
        tab = self._current_tab()
        if tab is None:
            QMessageBox.information(self, "No Image", "Open an image first.")
            return
        dlg = ScanAreaDialog(
            image_bgr=tab.image_bgr,
            current_rect=self._scan_rect,
            parent=self
        )
        dlg.scan_area_set.connect(self._apply_scan_area)
        dlg.exec()

    def _apply_scan_area(self, x: int, y: int, w: int, h: int):
        img = self._current_tab().image_bgr if self._current_tab() else None
        if img is not None and w >= img.shape[1] and h >= img.shape[0]:
            self._scan_rect = None
            self.settings.set_scan_area_label(None)
            self._set_status("Scan area cleared — using full image.")
        else:
            self._scan_rect = (x, y, w, h)
            self.settings.set_scan_area_label(self._scan_rect)
            self._set_status(f"Scan area set: {w}×{h} px at ({x},{y}) — applied to all images.")

    # ------------------------------------------------------------------ Analysis

    def run_analysis_all(self):
        if not self._image_tabs:
            QMessageBox.information(self, "No Images", "Open images first.")
            return
        if self._thread and self._thread.isRunning():
            QMessageBox.information(self, "Busy", "Analysis already running.")
            return
        self._cancelled = False
        names = [os.path.basename(t.image_path) for t in self._image_tabs]
        self._pending_tabs    = list(self._image_tabs)
        self._pending_indices = list(range(len(self._image_tabs)))

        self._progress_dlg = AnalysisProgressDialog(names, parent=self)
        self._progress_dlg.cancelled.connect(self._cancel_analysis)
        self._progress_dlg.setModal(False)
        self._progress_dlg.show()

        self.settings.set_analyze_enabled(False)
        self._run_next_pending()

    def run_analysis_current(self):
        tab = self._current_tab()
        if tab is None:
            QMessageBox.information(self, "No Image", "Open an image first.")
            return
        if self._thread and self._thread.isRunning():
            QMessageBox.information(self, "Busy", "Analysis already running.")
            return
        self._cancelled = False
        idx = self._image_tabs.index(tab)
        self._pending_tabs    = [tab]
        self._pending_indices = [idx]
        self._progress_dlg    = None
        self.settings.set_analyze_enabled(False)
        self._run_next_pending()

    def _cancel_analysis(self):
        self._cancelled = True
        self._pending_tabs.clear()
        self._pending_indices.clear()
        if self._thread and self._thread.isRunning():
            self._thread.quit()
        self.settings.set_analyze_enabled(True)
        self._set_status("Analysis cancelled.")

    def _run_next_pending(self):
        if self._cancelled or not self._pending_tabs:
            self.settings.set_analyze_enabled(True)
            self.settings.set_export_enabled(bool(any(t.result for t in self._image_tabs)))
            total = sum(t.result.grain_count for t in self._image_tabs if t.result)
            if not self._cancelled:
                self._set_status(f"All images analysed — {total} total grains.")
                if self._progress_dlg:
                    self._progress_dlg.all_done()
            return

        tab = self._pending_tabs[0]
        idx = self._pending_indices[0]

        if self._progress_dlg:
            self._progress_dlg.mark_running(idx)

        total = len(self._image_tabs)
        done  = total - len(self._pending_tabs)
        self._set_status(
            f"Analysing {os.path.basename(tab.image_path)} ({done+1}/{total})..."
        )
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        # Use per-tab scan rect if set, otherwise global
        scan_rect = tab.scan_rect if tab.scan_rect else self._scan_rect

        self._thread = QThread()
        self._worker = AnalysisWorker(
            tab.image_bgr, self._px_per_um,
            self.settings.get_params(), scan_rect
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(lambda r, t=tab, i=idx: self._on_done(r, t, i))
        self._worker.error.connect(lambda msg, i=idx: self._on_error(msg, i))
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _on_progress(self, pct: int, msg: str):
        self.progress_bar.setValue(pct)
        self._set_status(msg)
        if self._progress_dlg:
            self._progress_dlg.update_sub_progress(pct, msg)

    def _on_done(self, result: AnalysisResult, tab: ImageTab, tab_idx: int):
        tab.set_result(result)
        self.progress_bar.setVisible(False)

        name = os.path.basename(tab.image_path)
        self.tab_widget.setTabText(tab_idx, f"{name} ({result.grain_count}g)")

        if tab == self._current_tab():
            self.results.display_results(result)

        self._set_status(
            f"{name}: {result.grain_count} grains"
            + (f", mean {result.mean_area_um2:.3f} µm²" if result.has_calibration else "")
        )

        if self._progress_dlg:
            self._progress_dlg.mark_done(tab_idx, result.grain_count)

        if self._pending_tabs:
            self._pending_tabs.pop(0)
        if self._pending_indices:
            self._pending_indices.pop(0)

        # 400ms pause between images to let UI breathe
        QTimer.singleShot(400, self._run_next_pending)

    def _on_error(self, msg: str, tab_idx: int):
        self.progress_bar.setVisible(False)
        if self._progress_dlg:
            self._progress_dlg.mark_error(tab_idx)
        QMessageBox.critical(self, "Analysis Error", msg)
        if self._pending_tabs:
            self._pending_tabs.pop(0)
        if self._pending_indices:
            self._pending_indices.pop(0)
        QTimer.singleShot(400, self._run_next_pending)

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
        QMessageBox.about(self, "About SEM Grain Analyzer",
            "<h2>SEM Grain Analyzer v1.3</h2>"
            "<ul>"
            "<li>Open multiple images (hold Ctrl)</li>"
            "<li>Set scale bar: zoom in, click 2 points</li>"
            "<li>Set scan area: draw rectangle to exclude legend</li>"
            "<li>Analyze all with progress window</li>"
            "<li>Click grain → Delete to remove</li>"
            "</ul>")

    def _set_status(self, msg: str):
        self.status_msg.setText(msg) 
