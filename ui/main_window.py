"""
Main Application Window
=======================
Orchestrates all panels and connects signals/slots.
"""

import os
import sys
import numpy as np
import cv2

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QStatusBar, QProgressBar, QLabel, QFileDialog, QMessageBox,
    QToolBar, QFrame
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject, QTimer
from PyQt6.QtGui import QAction, QKeySequence

from ui.image_canvas import ImageCanvas
from ui.settings_panel import SettingsPanel
from ui.results_panel import ResultsPanel
from ui.calibration_dialog import CalibrationDialog
from core.grain_detector import GrainDetector, DetectionParams, AnalysisResult
from core.scale_bar import detect_scale_bar_length_px
from utils.excel_export import export_to_excel


class AnalysisWorker(QObject):
    """Runs grain detection in a background thread."""
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object)   # AnalysisResult
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


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("SEM Grain Analyzer")
        self.setMinimumSize(1100, 700)
        self.resize(1300, 820)

        self._image_bgr = None
        self._image_path = ""
        self._px_per_um = 0.0
        self._last_result: AnalysisResult = None
        self._worker = None
        self._thread = None

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

        # Left panel
        self.settings = SettingsPanel()
        self.settings.open_image.connect(self.open_image)
        self.settings.set_calibration.connect(self.open_calibration)
        self.settings.run_analysis.connect(self.run_analysis)
        self.settings.export_excel.connect(self.export_excel)
        root_lay.addWidget(self.settings)

        # Divider
        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setStyleSheet("color: #3c3c48;")
        root_lay.addWidget(div)

        # Center: image canvas
        center_widget = QWidget()
        center_lay = QVBoxLayout(center_widget)
        center_lay.setContentsMargins(0, 0, 0, 0)
        center_lay.setSpacing(0)

        # Canvas toolbar
        canvas_bar = QWidget()
        canvas_bar.setStyleSheet("background: #14141a; border-bottom: 1px solid #3c3c48;")
        canvas_bar.setFixedHeight(38)
        canvas_bar_lay = QHBoxLayout(canvas_bar)
        canvas_bar_lay.setContentsMargins(8, 4, 8, 4)
        canvas_bar_lay.setSpacing(6)

        self.lbl_view_mode = QLabel("Original")
        self.lbl_view_mode.setStyleSheet("color: #7777aa; font-size: 11px;")
        canvas_bar_lay.addWidget(self.lbl_view_mode)

        canvas_bar_lay.addStretch()

        from PyQt6.QtWidgets import QPushButton
        btn_orig = QPushButton("Original")
        btn_overlay = QPushButton("Grain Overlay")
        btn_binary = QPushButton("Binary Mask")
        for btn in [btn_orig, btn_overlay, btn_binary]:
            btn.setFixedHeight(26)
            btn.setStyleSheet("font-size: 11px; padding: 2px 10px;")
            canvas_bar_lay.addWidget(btn)

        btn_orig.clicked.connect(lambda: self._show_view("original"))
        btn_overlay.clicked.connect(lambda: self._show_view("overlay"))
        btn_binary.clicked.connect(lambda: self._show_view("binary"))

        btn_fit = QPushButton("⊡ Fit")
        btn_fit.setFixedHeight(26)
        btn_fit.setStyleSheet("font-size: 11px; padding: 2px 10px;")
        btn_fit.clicked.connect(self.canvas.fit_to_window if hasattr(self, 'canvas') else lambda: None)
        canvas_bar_lay.addWidget(btn_fit)
        self._btn_fit = btn_fit

        center_lay.addWidget(canvas_bar)

        self.canvas = ImageCanvas()
        btn_fit.clicked.disconnect()
        btn_fit.clicked.connect(self.canvas.fit_to_window)
        center_lay.addWidget(self.canvas)

        # Right: results
        self.results = ResultsPanel()

        # Splitter for center + right
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(center_widget)
        splitter.addWidget(self.results)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([900, 380])

        root_lay.addWidget(splitter, 1)

        self._current_view = "original"

    def _build_menu(self):
        mb = self.menuBar()

        file_menu = mb.addMenu("&File")
        act_open = QAction("&Open Image...", self)
        act_open.setShortcut(QKeySequence.StandardKey.Open)
        act_open.triggered.connect(self.open_image)
        file_menu.addAction(act_open)

        act_export = QAction("&Export to Excel...", self)
        act_export.setShortcut(QKeySequence("Ctrl+E"))
        act_export.triggered.connect(self.export_excel)
        file_menu.addAction(act_export)

        file_menu.addSeparator()
        act_quit = QAction("&Quit", self)
        act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        analysis_menu = mb.addMenu("&Analysis")
        act_run = QAction("&Run Analysis", self)
        act_run.setShortcut(QKeySequence("F5"))
        act_run.triggered.connect(self.run_analysis)
        analysis_menu.addAction(act_run)

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

        self.status_msg = QLabel("Ready — open an SEM image to begin.")
        self.status.addWidget(self.status_msg)

    # ------------------------------------------------------------------ Actions

    def open_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open SEM Image", "",
            "Image Files (*.tif *.tiff *.png *.jpg *.jpeg *.bmp *.tga);;All Files (*)"
        )
        if not path:
            return
        self._load_image(path)

    def _load_image(self, path: str):
        try:
            img = cv2.imread(path, cv2.IMREAD_COLOR)
            if img is None:
                raise ValueError("Could not read image file.")
            self._image_bgr = img
            self._image_path = path
            self._last_result = None
            self._current_view = "original"

            self.canvas.set_image(img)
            self.results.clear()
            self.settings.set_image_name(os.path.basename(path))
            self.settings.set_analyze_enabled(True)
            self.settings.set_export_enabled(False)

            h, w = img.shape[:2]
            self._set_status(f"Loaded: {os.path.basename(path)}  ({w}×{h} px)")

            # Attempt auto scale bar detection
            bar_px, debug_img = detect_scale_bar_length_px(img)
            if bar_px:
                self._set_status(
                    f"Loaded ({w}×{h} px) — Scale bar detected: {bar_px} px wide. "
                    f"Set calibration to finish."
                )
                self._auto_bar_px = bar_px
            else:
                self._auto_bar_px = None

        except Exception as e:
            QMessageBox.critical(self, "Error Loading Image", str(e))

    def open_calibration(self):
        if self._image_bgr is None:
            QMessageBox.information(self, "No Image", "Load an image first.")
            return
        dlg = CalibrationDialog(
            image_bgr=self._image_bgr,
            auto_bar_px=getattr(self, '_auto_bar_px', None),
            parent=self
        )
        dlg.calibration_set.connect(self._apply_calibration)
        dlg.exec()

    def _apply_calibration(self, px_per_um: float):
        self._px_per_um = px_per_um
        self.settings.set_calibration_label(px_per_um)
        self._set_status(f"Calibration set: {px_per_um:.4f} px/µm")

    def run_analysis(self):
        if self._image_bgr is None:
            QMessageBox.information(self, "No Image", "Please open an SEM image first.")
            return
        if self._thread and self._thread.isRunning():
            return

        params = self.settings.get_params()
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.settings.btn_analyze.setEnabled(False)
        self._set_status("Analyzing...")

        self._thread = QThread()
        self._worker = AnalysisWorker(self._image_bgr, self._px_per_um, params)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_analysis_done)
        self._worker.error.connect(self._on_analysis_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)

        self._thread.start()

    def _on_progress(self, pct: int, msg: str):
        self.progress_bar.setValue(pct)
        self._set_status(msg)

    def _on_analysis_done(self, result: AnalysisResult):
        self._last_result = result
        self.progress_bar.setVisible(False)
        self.settings.btn_analyze.setEnabled(True)
        self.settings.set_export_enabled(True)

        self.results.display_results(result)
        self._show_view("overlay")

        self._set_status(
            f"Analysis complete — {result.grain_count} grains detected."
            + (f"  Mean area: {result.mean_area_um2:.4f} µm²" if result.has_calibration else "")
        )

    def _on_analysis_error(self, msg: str):
        self.progress_bar.setVisible(False)
        self.settings.btn_analyze.setEnabled(True)
        QMessageBox.critical(self, "Analysis Error", msg)
        self._set_status("Analysis failed.")

    def _cleanup_thread(self):
        if self._thread:
            self._thread.deleteLater()
        if self._worker:
            self._worker.deleteLater()
        self._thread = None
        self._worker = None

    def _show_view(self, mode: str):
        self._current_view = mode
        if mode == "original" and self._image_bgr is not None:
            self.canvas.set_image(self._image_bgr)
            self.lbl_view_mode.setText("Original Image")
        elif mode == "overlay" and self._last_result and self._last_result.overlay_image is not None:
            self.canvas.set_image(self._last_result.overlay_image)
            self.lbl_view_mode.setText("Grain Overlay")
        elif mode == "binary" and self._last_result and self._last_result.binary_image is not None:
            binary_bgr = cv2.cvtColor(
                self._last_result.binary_image * 255, cv2.COLOR_GRAY2BGR
            )
            self.canvas.set_image(binary_bgr)
            self.lbl_view_mode.setText("Binary Mask")
        elif self._image_bgr is not None:
            self.canvas.set_image(self._image_bgr)
            self.lbl_view_mode.setText("Original Image")

    def export_excel(self):
        if self._last_result is None:
            QMessageBox.information(self, "No Results", "Run analysis first.")
            return

        default_name = os.path.splitext(os.path.basename(self._image_path))[0] + "_grains.xlsx"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Excel Report", default_name,
            "Excel Files (*.xlsx)"
        )
        if not path:
            return

        try:
            export_to_excel(
                self._last_result,
                image_path=self._image_path,
                output_path=path,
                params=self.settings.get_params()
            )
            self._set_status(f"Exported: {os.path.basename(path)}")
            reply = QMessageBox.question(
                self, "Export Complete",
                f"Report saved to:\n{path}\n\nOpen it now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                import subprocess, sys
                if sys.platform == "win32":
                    os.startfile(path)
                elif sys.platform == "darwin":
                    subprocess.run(["open", path])
                else:
                    subprocess.run(["xdg-open", path])
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    def _show_about(self):
        QMessageBox.about(
            self,
            "About SEM Grain Analyzer",
            "<h2>SEM Grain Analyzer v1.0</h2>"
            "<p>Automatic grain detection and measurement tool for "
            "Scanning Electron Microscope images.</p>"
            "<p><b>Detection method:</b> Otsu thresholding + Watershed segmentation (scikit-image)</p>"
            "<p><b>Libraries:</b> PyQt6, OpenCV, scikit-image, NumPy, openpyxl</p>"
        )

    def _set_status(self, msg: str):
        self.status_msg.setText(msg)
