"""
Settings Panel - resizable left panel, clean layout
Updated for Grain Detection Engine v2.1 (boundary-first)
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QFormLayout, QSpinBox, QDoubleSpinBox,
    QCheckBox, QFrame, QScrollArea, QSizePolicy, QComboBox
)
from PyQt6.QtCore import Qt, pyqtSignal
from core.grain_detector import DetectionParams


class SettingsPanel(QScrollArea):
    params_changed  = pyqtSignal(object)
    run_analysis    = pyqtSignal()
    open_image      = pyqtSignal()
    set_calibration = pyqtSignal()
    set_scan_area   = pyqtSignal()
    export_excel    = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setMinimumWidth(320)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        self.setWidget(container)
        self._layout = QVBoxLayout(container)
        self._layout.setContentsMargins(12, 12, 12, 12)
        self._layout.setSpacing(10)
        self._build_ui()

    def _build_ui(self):
        lay = self._layout

        title = QLabel("SEM Grain Analyzer")
        title.setObjectName("header")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        ver = QLabel("v2.1  —  Boundary-first detection")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ver.setStyleSheet("color: #6666aa; font-size: 11px;")
        lay.addWidget(ver)

        self._sep()

        # -- Images --
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

        # -- Scale Bar --
        cal_group = QGroupBox("Scale Bar Calibration")
        cal_lay = QVBoxLayout(cal_group)
        cal_lay.setSpacing(6)

        self.btn_calibrate = QPushButton("📏  Set Scale Bar (Click 2 Points)")
        self.btn_calibrate.setMinimumHeight(36)
        self.btn_calibrate.clicked.connect(self.set_calibration.emit)
        cal_lay.addWidget(self.btn_calibrate)

        self.lbl_calibration = QLabel("Not calibrated  (pixel units)")
        self.lbl_calibration.setObjectName("status_warn")
        self.lbl_calibration.setWordWrap(True)
        self.lbl_calibration.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cal_lay.addWidget(self.lbl_calibration)

        note = QLabel("Click 2 points on the scale bar, enter the\nreal-world length → applies to all images.")
        note.setStyleSheet("color: #888899; font-size: 10px;")
        note.setWordWrap(True)
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cal_lay.addWidget(note)

        self.btn_scan_area = QPushButton("📐  Set Scan Area (Draw Rectangle)")
        self.btn_scan_area.setMinimumHeight(34)
        self.btn_scan_area.clicked.connect(self.set_scan_area.emit)
        cal_lay.addWidget(self.btn_scan_area)

        self.lbl_scan_area = QLabel("Full image (no crop)")
        self.lbl_scan_area.setStyleSheet("color: #888899; font-size: 10px;")
        self.lbl_scan_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cal_lay.addWidget(self.lbl_scan_area)

        lay.addWidget(cal_group)

        # ============================================================
        # -- Detection Mode (NEW v2.1) --
        # ============================================================
        mode_group = QGroupBox("Detection Mode")
        mode_lay = QVBoxLayout(mode_group)
        mode_lay.setSpacing(6)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Auto-detect (recommended)", "auto")
        self.mode_combo.addItem("Boundary-first (mosaic grains)", "boundary")
        self.mode_combo.addItem("Threshold-based (grains on background)", "threshold")
        self.mode_combo.setToolTip(
            "Auto: automatically chooses the best method for your image.\n\n"
            "Boundary-first: for images where grains fill the entire view\n"
            "and are separated by thin dark grooves (most SEM grain images).\n\n"
            "Threshold-based: for images with distinct bright grains on\n"
            "a dark background (or vice versa)."
        )
        mode_lay.addWidget(self.mode_combo)

        mode_hint = QLabel(
            "Auto selects boundary mode for dense grain mosaics\n"
            "and threshold mode for sparse grains on background."
        )
        mode_hint.setStyleSheet("color: #888899; font-size: 10px;")
        mode_hint.setWordWrap(True)
        mode_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mode_lay.addWidget(mode_hint)

        lay.addWidget(mode_group)

        # ============================================================
        # -- Enhancement --
        # ============================================================
        enh_group = QGroupBox("Image Enhancement")
        enh_lay = QFormLayout(enh_group)
        enh_lay.setSpacing(8)
        enh_lay.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self.clahe_cb = QCheckBox("CLAHE contrast boost")
        self.clahe_cb.setChecked(True)
        self.clahe_cb.setToolTip(
            "Contrast Limited Adaptive Histogram Equalization.\n"
            "Boosts local contrast so boundary grooves become more visible."
        )
        enh_lay.addRow("", self.clahe_cb)

        self.clahe_clip_spin = QDoubleSpinBox()
        self.clahe_clip_spin.setRange(0.5, 8.0)
        self.clahe_clip_spin.setValue(2.0)
        self.clahe_clip_spin.setSingleStep(0.5)
        self.clahe_clip_spin.setDecimals(1)
        self.clahe_clip_spin.setToolTip(
            "CLAHE clip limit. Higher = more aggressive contrast boost.\n"
            "Default 2.0 works for most images. Try 3-4 for very flat images."
        )
        enh_lay.addRow("CLAHE strength:", self.clahe_clip_spin)

        lay.addWidget(enh_group)

        # ============================================================
        # -- Detection Parameters --
        # ============================================================
        det_group = QGroupBox("Detection Parameters")
        det_lay = QFormLayout(det_group)
        det_lay.setSpacing(8)
        det_lay.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self.blur_spin = QDoubleSpinBox()
        self.blur_spin.setRange(0.0, 10.0)
        self.blur_spin.setValue(1.5)
        self.blur_spin.setSingleStep(0.5)
        self.blur_spin.setDecimals(1)
        self.blur_spin.setToolTip("Gaussian blur to suppress noise before detection.")
        det_lay.addRow("Blur (σ):", self.blur_spin)

        self.edge_sens_spin = QDoubleSpinBox()
        self.edge_sens_spin.setRange(0.1, 3.0)
        self.edge_sens_spin.setValue(1.0)
        self.edge_sens_spin.setSingleStep(0.1)
        self.edge_sens_spin.setDecimals(1)
        self.edge_sens_spin.setToolTip(
            "Edge/boundary sensitivity.\n"
            "Higher = detects more boundaries (may over-segment).\n"
            "Lower = detects fewer boundaries (may merge grains)."
        )
        det_lay.addRow("Edge sensitivity:", self.edge_sens_spin)

        self.thresh_spin = QDoubleSpinBox()
        self.thresh_spin.setRange(-0.5, 0.5)
        self.thresh_spin.setValue(0.0)
        self.thresh_spin.setSingleStep(0.02)
        self.thresh_spin.setDecimals(3)
        self.thresh_spin.setToolTip(
            "In boundary mode: adjusts the boundary detection threshold.\n"
            "Negative = more boundaries detected (splits more grains).\n"
            "Positive = fewer boundaries detected (merges more grains).\n\n"
            "In threshold mode: shifts the Otsu threshold up/down."
        )
        det_lay.addRow("Threshold offset:", self.thresh_spin)

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
        self.watershed_spin.setToolTip(
            "Minimum distance between grain centers for watershed.\n"
            "In boundary mode: used to split suspiciously large regions.\n"
            "Higher = fewer splits; Lower = more aggressive splitting."
        )
        det_lay.addRow("Watershed dist:", self.watershed_spin)

        self.dark_grains_cb = QCheckBox("Grains are dark (inverted)")
        self.dark_grains_cb.setToolTip(
            "Check if your grains are darker than the background.\n"
            "Only affects threshold mode; boundary mode detects regardless."
        )
        det_lay.addRow("", self.dark_grains_cb)

        self.watershed_cb = QCheckBox("Use watershed (split merged grains)")
        self.watershed_cb.setChecked(True)
        self.watershed_cb.setToolTip(
            "In boundary mode: splits suspiciously large regions.\n"
            "In threshold mode: separates touching grains."
        )
        det_lay.addRow("", self.watershed_cb)

        self.adaptive_cb = QCheckBox("Adaptive threshold (threshold mode only)")
        self.adaptive_cb.setChecked(True)
        self.adaptive_cb.setToolTip(
            "Uses local neighborhood comparison instead of a single\n"
            "global threshold. Only used in threshold detection mode."
        )
        det_lay.addRow("", self.adaptive_cb)

        lay.addWidget(det_group)

        # Connect all value-change signals
        for w in [self.blur_spin, self.thresh_spin, self.min_size_spin,
                  self.max_size_spin, self.watershed_spin,
                  self.clahe_clip_spin, self.edge_sens_spin]:
            w.valueChanged.connect(self._emit_params)
        for cb in [self.dark_grains_cb, self.watershed_cb,
                   self.clahe_cb, self.adaptive_cb]:
            cb.stateChanged.connect(self._emit_params)
        self.mode_combo.currentIndexChanged.connect(self._emit_params)

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

        info = QLabel(
            "Tip: Scroll wheel to zoom.\n"
            "Alt+drag to pan.\n"
            "Click grain → Delete to remove."
        )
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
        d = DetectionParams()
        self.mode_combo.setCurrentIndex(0)  # "auto"
        self.blur_spin.setValue(d.blur_sigma)
        self.thresh_spin.setValue(d.threshold_offset)
        self.min_size_spin.setValue(d.min_grain_size_px)
        self.max_size_spin.setValue(d.max_grain_size_px)
        self.watershed_spin.setValue(d.watershed_min_dist)
        self.dark_grains_cb.setChecked(d.dark_grains)
        self.watershed_cb.setChecked(d.use_watershed)
        self.clahe_cb.setChecked(d.use_clahe)
        self.clahe_clip_spin.setValue(d.clahe_clip_limit)
        self.adaptive_cb.setChecked(d.use_adaptive)
        self.edge_sens_spin.setValue(d.edge_sensitivity)

    def get_params(self) -> DetectionParams:
        return DetectionParams(
            blur_sigma=self.blur_spin.value(),
            threshold_offset=self.thresh_spin.value(),
            min_grain_size_px=self.min_size_spin.value(),
            max_grain_size_px=self.max_size_spin.value(),
            watershed_min_dist=self.watershed_spin.value(),
            dark_grains=self.dark_grains_cb.isChecked(),
            use_watershed=self.watershed_cb.isChecked(),
            edge_sensitivity=self.edge_sens_spin.value(),
            use_adaptive=self.adaptive_cb.isChecked(),
            use_clahe=self.clahe_cb.isChecked(),
            clahe_clip_limit=self.clahe_clip_spin.value(),
            detection_mode=self.mode_combo.currentData(),
        )

    def set_image_name(self, name: str):
        self.lbl_image.setText(name)

    def set_calibration_label(self, px_per_um: float):
        if px_per_um > 0:
            self.lbl_calibration.setText(
                f"✓  {px_per_um:.4f} px/µm\n({1/px_per_um*1000:.4f} nm/px)"
            )
            self.lbl_calibration.setObjectName("status_ok")
        else:
            self.lbl_calibration.setText("Not calibrated  (pixel units)")
            self.lbl_calibration.setObjectName("status_warn")
        self.lbl_calibration.style().unpolish(self.lbl_calibration)
        self.lbl_calibration.style().polish(self.lbl_calibration)

    def set_analyze_enabled(self, enabled: bool):
        self.btn_analyze.setEnabled(enabled)

    def set_scan_area_label(self, rect):
        if rect is None:
            self.lbl_scan_area.setText("Full image (no crop)")
        else:
            x, y, w, h = rect
            self.lbl_scan_area.setText(f"Scan area: {w}×{h}px at ({x},{y})")

    def set_export_enabled(self, enabled: bool):
        self.btn_export.setEnabled(enabled)
