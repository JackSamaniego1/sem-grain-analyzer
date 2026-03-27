"""
Settings Panel
==============
Left-side panel with all detection parameter controls.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QFormLayout, QSlider, QSpinBox, QDoubleSpinBox,
    QCheckBox, QFrame, QSizePolicy, QScrollArea
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from core.grain_detector import DetectionParams


class SettingsPanel(QScrollArea):
    params_changed = pyqtSignal(object)  # DetectionParams
    run_analysis = pyqtSignal()
    open_image = pyqtSignal()
    set_calibration = pyqtSignal()
    export_excel = pyqtSignal()
    reset_params = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFixedWidth(270)
        self.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        self.setWidget(container)
        self._layout = QVBoxLayout(container)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(10)

        self._build_ui()

    def _build_ui(self):
        lay = self._layout

        # App title
        title = QLabel("SEM Grain Analyzer")
        title.setObjectName("header")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        ver = QLabel("v1.0")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ver.setStyleSheet("color: #6666aa; font-size: 11px;")
        lay.addWidget(ver)

        self._sep()

        # -- File group --
        file_group = QGroupBox("Image")
        file_lay = QVBoxLayout(file_group)
        file_lay.setSpacing(6)

        self.btn_open = QPushButton("📂  Open SEM Image...")
        self.btn_open.setObjectName("primary")
        self.btn_open.setMinimumHeight(36)
        self.btn_open.clicked.connect(self.open_image.emit)
        file_lay.addWidget(self.btn_open)

        self.lbl_image = QLabel("No image loaded")
        self.lbl_image.setWordWrap(True)
        self.lbl_image.setStyleSheet("color: #aaaacc; font-size: 11px;")
        self.lbl_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        file_lay.addWidget(self.lbl_image)
        lay.addWidget(file_group)

        # -- Calibration group --
        cal_group = QGroupBox("Scale Bar Calibration")
        cal_lay = QVBoxLayout(cal_group)
        cal_lay.setSpacing(6)

        self.btn_calibrate = QPushButton("📏  Set Scale Bar...")
        self.btn_calibrate.clicked.connect(self.set_calibration.emit)
        cal_lay.addWidget(self.btn_calibrate)

        self.lbl_calibration = QLabel("Not calibrated (pixel units)")
        self.lbl_calibration.setObjectName("status_warn")
        self.lbl_calibration.setWordWrap(True)
        self.lbl_calibration.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cal_lay.addWidget(self.lbl_calibration)
        lay.addWidget(cal_group)

        # -- Detection Parameters --
        det_group = QGroupBox("Detection Parameters")
        det_lay = QFormLayout(det_group)
        det_lay.setSpacing(8)

        # Blur
        self.blur_spin = QDoubleSpinBox()
        self.blur_spin.setRange(0.0, 10.0)
        self.blur_spin.setValue(1.5)
        self.blur_spin.setSingleStep(0.5)
        self.blur_spin.setDecimals(1)
        self.blur_spin.setToolTip("Gaussian blur — reduces noise. Higher = smoother threshold.")
        det_lay.addRow("Blur (σ):", self.blur_spin)

        # Threshold offset
        self.thresh_spin = QDoubleSpinBox()
        self.thresh_spin.setRange(-0.5, 0.5)
        self.thresh_spin.setValue(0.0)
        self.thresh_spin.setSingleStep(0.02)
        self.thresh_spin.setDecimals(3)
        self.thresh_spin.setToolTip(
            "Offset added to Otsu auto-threshold.\n"
            "Negative = detect more (lower threshold).\n"
            "Positive = detect less (higher threshold)."
        )
        det_lay.addRow("Threshold offset:", self.thresh_spin)

        # Min grain size
        self.min_size_spin = QSpinBox()
        self.min_size_spin.setRange(1, 50000)
        self.min_size_spin.setValue(50)
        self.min_size_spin.setSuffix(" px²")
        self.min_size_spin.setToolTip("Minimum grain area — smaller regions are ignored as noise.")
        det_lay.addRow("Min grain area:", self.min_size_spin)

        # Max grain size
        self.max_size_spin = QSpinBox()
        self.max_size_spin.setRange(0, 10000000)
        self.max_size_spin.setValue(0)
        self.max_size_spin.setSuffix(" px²")
        self.max_size_spin.setToolTip("Maximum grain area (0 = no limit).")
        det_lay.addRow("Max grain area:", self.max_size_spin)

        # Watershed min distance
        self.watershed_spin = QSpinBox()
        self.watershed_spin.setRange(1, 100)
        self.watershed_spin.setValue(5)
        self.watershed_spin.setSuffix(" px")
        self.watershed_spin.setToolTip(
            "Watershed marker minimum distance.\n"
            "Increase if touching grains are not being separated.\n"
            "Decrease if grains are being over-split."
        )
        det_lay.addRow("Watershed sep.:", self.watershed_spin)

        # Dark grains
        self.dark_grains_cb = QCheckBox("Grains are dark (inverted)")
        self.dark_grains_cb.setToolTip(
            "Check if your grains appear darker than the background.\n"
            "Most SEM images have bright grains on dark background."
        )
        det_lay.addRow("", self.dark_grains_cb)

        # Use watershed
        self.watershed_cb = QCheckBox("Use watershed (separate touching grains)")
        self.watershed_cb.setChecked(True)
        self.watershed_cb.setToolTip(
            "Watershed algorithm separates grains that are touching each other.\n"
            "Disable if grains are well-separated for faster processing."
        )
        det_lay.addRow("", self.watershed_cb)

        lay.addWidget(det_group)

        # Connect changes
        for widget in [self.blur_spin, self.thresh_spin, self.min_size_spin,
                       self.max_size_spin, self.watershed_spin]:
            widget.valueChanged.connect(self._emit_params)
        for cb in [self.dark_grains_cb, self.watershed_cb]:
            cb.stateChanged.connect(self._emit_params)

        # -- Reset button --
        btn_reset = QPushButton("↺  Reset to Defaults")
        btn_reset.clicked.connect(self._reset_params)
        lay.addWidget(btn_reset)

        self._sep()

        # -- Analyze button --
        self.btn_analyze = QPushButton("🔬  Analyze Grains")
        self.btn_analyze.setObjectName("primary")
        self.btn_analyze.setMinimumHeight(42)
        self.btn_analyze.setEnabled(False)
        font = self.btn_analyze.font()
        font.setPointSize(12)
        font.setBold(True)
        self.btn_analyze.setFont(font)
        self.btn_analyze.clicked.connect(self.run_analysis.emit)
        lay.addWidget(self.btn_analyze)

        self._sep()

        # -- Export button --
        self.btn_export = QPushButton("📊  Export to Excel")
        self.btn_export.setObjectName("success")
        self.btn_export.setMinimumHeight(36)
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self.export_excel.emit)
        lay.addWidget(self.btn_export)

        lay.addStretch()

        # -- Info footer --
        info = QLabel("Tip: Zoom with scroll wheel.\nAlt+drag to pan image.")
        info.setWordWrap(True)
        info.setStyleSheet("color: #555577; font-size: 10px;")
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(info)

    def _sep(self):
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #3c3c48;")
        self._layout.addWidget(line)

    def _emit_params(self):
        self.params_changed.emit(self.get_params())

    def _reset_params(self):
        defaults = DetectionParams()
        self.blur_spin.setValue(defaults.blur_sigma)
        self.thresh_spin.setValue(defaults.threshold_offset)
        self.min_size_spin.setValue(defaults.min_grain_size_px)
        self.max_size_spin.setValue(defaults.max_grain_size_px)
        self.watershed_spin.setValue(defaults.watershed_min_dist)
        self.dark_grains_cb.setChecked(defaults.dark_grains)
        self.watershed_cb.setChecked(defaults.use_watershed)

    def get_params(self) -> DetectionParams:
        return DetectionParams(
            blur_sigma=self.blur_spin.value(),
            threshold_offset=self.thresh_spin.value(),
            min_grain_size_px=self.min_size_spin.value(),
            max_grain_size_px=self.max_size_spin.value(),
            watershed_min_dist=self.watershed_spin.value(),
            dark_grains=self.dark_grains_cb.isChecked(),
            use_watershed=self.watershed_cb.isChecked(),
        )

    def set_image_name(self, name: str):
        self.lbl_image.setText(name)

    def set_calibration_label(self, px_per_um: float):
        if px_per_um > 0:
            self.lbl_calibration.setText(f"✓ {px_per_um:.4f} px/µm\n({1/px_per_um*1000:.4f} nm/px)")
            self.lbl_calibration.setObjectName("status_ok")
        else:
            self.lbl_calibration.setText("Not calibrated (pixel units)")
            self.lbl_calibration.setObjectName("status_warn")
        # Force style refresh
        self.lbl_calibration.style().unpolish(self.lbl_calibration)
        self.lbl_calibration.style().polish(self.lbl_calibration)

    def set_analyze_enabled(self, enabled: bool):
        self.btn_analyze.setEnabled(enabled)

    def set_export_enabled(self, enabled: bool):
        self.btn_export.setEnabled(enabled)
