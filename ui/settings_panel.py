"""
Settings Panel - wider, cleaner, auto scale bar
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QFormLayout, QSpinBox, QDoubleSpinBox,
    QCheckBox, QFrame, QScrollArea
)
from PyQt6.QtCore import Qt, pyqtSignal
from core.grain_detector import DetectionParams


class SettingsPanel(QScrollArea):
    params_changed = pyqtSignal(object)
    run_analysis   = pyqtSignal()
    open_image     = pyqtSignal()
    set_calibration = pyqtSignal()
    export_excel   = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumWidth(310)
        self.setMaximumWidth(340)
        self.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        self.setWidget(container)
        self._layout = QVBoxLayout(container)
        self._layout.setContentsMargins(10, 10, 10, 10)
        self._layout.setSpacing(10)
        self._build_ui()

    def _build_ui(self):
        lay = self._layout

        title = QLabel("SEM Grain Analyzer")
        title.setObjectName("header")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        ver = QLabel("v1.1  —  Multi-image")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ver.setStyleSheet("color: #6666aa; font-size: 11px;")
        lay.addWidget(ver)

        self._sep()

        file_group = QGroupBox("Images")
        file_lay = QVBoxLayout(file_group)
        file_lay.setSpacing(6)
        self.btn_open = QPushButton("📂  Open SEM Images...")
        self.btn_open.setObjectName("primary")
        self.btn_open.setMinimumHeight(38)
        self.btn_open.clicked.connect(self.open_image.emit)
        file_lay.addWidget(self.btn_open)
        self.lbl_image = QLabel("No images loaded")
        self.lbl_image.setWordWrap(True)
        self.lbl_image.setStyleSheet("color: #aaaacc; font-size: 11px;")
        self.lbl_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        file_lay.addWidget(self.lbl_image)
        lay.addWidget(file_group)

        cal_group = QGroupBox("Scale Bar Calibration")
        cal_lay = QVBoxLayout(cal_group)
        cal_lay.setSpacing(6)
        self.btn_calibrate = QPushButton("📏  Auto-Detect Scale Bar")
        self.btn_calibrate.setMinimumHeight(34)
        self.btn_calibrate.clicked.connect(self.set_calibration.emit)
        cal_lay.addWidget(self.btn_calibrate)
        self.lbl_calibration = QLabel("Not calibrated  (pixel units)")
        self.lbl_calibration.setObjectName("status_warn")
        self.lbl_calibration.setWordWrap(True)
        self.lbl_calibration.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cal_lay.addWidget(self.lbl_calibration)
        note = QLabel("Auto-reads scale bar + label from image.\nApplies to all open images.")
        note.setStyleSheet("color: #888899; font-size: 10px;")
        note.setWordWrap(True)
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cal_lay.addWidget(note)
        lay.addWidget(cal_group)

        det_group = QGroupBox("Detection Parameters")
        det_lay = QFormLayout(det_group)
        det_lay.setSpacing(8)
        det_lay.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.blur_spin = QDoubleSpinBox()
        self.blur_spin.setRange(0.0, 10.0)
        self.blur_spin.setValue(1.5)
        self.blur_spin.setSingleStep(0.5)
        self.blur_spin.setDecimals(1)
        det_lay.addRow("Blur (σ):", self.blur_spin)
        self.thresh_spin = QDoubleSpinBox()
        self.thresh_spin.setRange(-0.5, 0.5)
        self.thresh_spin.setValue(0.0)
        self.thresh_spin.setSingleStep(0.02)
        self.thresh_spin.setDecimals(3)
        det_lay.addRow("Threshold:", self.thresh_spin)
        self.min_size_spin = QSpinBox()
        self.min_size_spin.setRange(1, 50000)
        self.min_size_spin.setValue(50)
        self.min_size_spin.setSuffix(" px²")
        det_lay.addRow("Min area:", self.min_size_spin)
        self.max_size_spin = QSpinBox()
        self.max_size_spin.setRange(0, 10000000)
        self.max_size_spin.setValue(0)
        self.max_size_spin.setSuffix(" px²")
        det_lay.addRow("Max area:", self.max_size_spin)
        self.watershed_spin = QSpinBox()
        self.watershed_spin.setRange(1, 100)
        self.watershed_spin.setValue(5)
        self.watershed_spin.setSuffix(" px")
        det_lay.addRow("Watershed:", self.watershed_spin)
        self.dark_grains_cb = QCheckBox("Grains are dark (inverted)")
        det_lay.addRow("", self.dark_grains_cb)
        self.watershed_cb = QCheckBox("Use watershed (separate touching grains)")
        self.watershed_cb.setChecked(True)
        det_lay.addRow("", self.watershed_cb)
        lay.addWidget(det_group)

        for w in [self.blur_spin, self.thresh_spin, self.min_size_spin, self.max_size_spin, self.watershed_spin]:
            w.valueChanged.connect(self._emit_params)
        for cb in [self.dark_grains_cb, self.watershed_cb]:
            cb.stateChanged.connect(self._emit_params)

        btn_reset = QPushButton("↺  Reset to Defaults")
        btn_reset.clicked.connect(self._reset_params)
        lay.addWidget(btn_reset)
        self._sep()

        self.btn_analyze = QPushButton("🔬  Analyze ALL Images")
        self.btn_analyze.setObjectName("primary")
        self.btn_analyze.setMinimumHeight(44)
        self.btn_analyze.setEnabled(False)
        f = self.btn_analyze.font()
        f.setPointSize(12)
        f.setBold(True)
        self.btn_analyze.setFont(f)
        self.btn_analyze.clicked.connect(self.run_analysis.emit)
        lay.addWidget(self.btn_analyze)
        self._sep()

        self.btn_export = QPushButton("📊  Export to Excel")
        self.btn_export.setObjectName("success")
        self.btn_export.setMinimumHeight(36)
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self.export_excel.emit)
        lay.addWidget(self.btn_export)
        lay.addStretch()

        info = QLabel("Tip: Scroll wheel to zoom.\nAlt+drag to pan.\nClick grain then Delete to remove.")
        info.setWordWrap(True)
        info.setStyleSheet("color: #555577; font-size: 10px;")
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(info)

    def _sep(self):
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #3c3c48;")
        self._layout.addWidget(line)

    def _emit_params(self): self.params_changed.emit(self.get_params())

    def _reset_params(self):
        d = DetectionParams()
        self.blur_spin.setValue(d.blur_sigma)
        self.thresh_spin.setValue(d.threshold_offset)
        self.min_size_spin.setValue(d.min_grain_size_px)
        self.max_size_spin.setValue(d.max_grain_size_px)
        self.watershed_spin.setValue(d.watershed_min_dist)
        self.dark_grains_cb.setChecked(d.dark_grains)
        self.watershed_cb.setChecked(d.use_watershed)

    def get_params(self):
        return DetectionParams(
            blur_sigma=self.blur_spin.value(),
            threshold_offset=self.thresh_spin.value(),
            min_grain_size_px=self.min_size_spin.value(),
            max_grain_size_px=self.max_size_spin.value(),
            watershed_min_dist=self.watershed_spin.value(),
            dark_grains=self.dark_grains_cb.isChecked(),
            use_watershed=self.watershed_cb.isChecked(),
        )

    def set_image_name(self, name): self.lbl_image.setText(name)

    def set_calibration_label(self, px_per_um):
        if px_per_um > 0:
            self.lbl_calibration.setText(f"✓  {px_per_um:.4f} px/µm\n({1/px_per_um*1000:.4f} nm/px)")
            self.lbl_calibration.setObjectName("status_ok")
        else:
            self.lbl_calibration.setText("Not calibrated  (pixel units)")
            self.lbl_calibration.setObjectName("status_warn")
        self.lbl_calibration.style().unpolish(self.lbl_calibration)
        self.lbl_calibration.style().polish(self.lbl_calibration)

    def set_analyze_enabled(self, e): self.btn_analyze.setEnabled(e)
    def set_export_enabled(self, e): self.btn_export.setEnabled(e)
