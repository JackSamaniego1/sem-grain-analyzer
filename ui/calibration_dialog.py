"""
Calibration Dialog
==================
Lets the user set the scale bar calibration — either by:
  1. Clicking two points on the scale bar in the image
  2. Manually typing the pixel length and real-world length
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QDoubleSpinBox, QSpinBox, QGroupBox, QFormLayout, QDialogButtonBox,
    QFrame, QComboBox, QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap, QPainter, QColor, QPen, QCursor, QImage
import numpy as np
import cv2


def _bgr_to_pixmap(arr):
    if arr is None:
        return QPixmap()
    rgb = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb.shape
    qi = QImage(rgb.data.tobytes(), w, h, w * 3, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qi)


class CalibrationDialog(QDialog):
    calibration_set = pyqtSignal(float)  # emits px_per_um

    def __init__(self, image_bgr=None, auto_bar_px=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Scale Bar Calibration")
        self.setMinimumSize(520, 400)
        self._image_bgr = image_bgr
        self._auto_bar_px = auto_bar_px
        self._result_px_per_um = None
        self._setup_ui()

        if auto_bar_px and auto_bar_px > 0:
            self.bar_px_spin.setValue(auto_bar_px)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        # -- Header --
        header = QLabel("Set Pixel-to-Micron Calibration")
        header.setObjectName("header")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        desc = QLabel(
            "Measure the scale bar in your SEM image.\n"
            "Enter the scale bar length in pixels and its labeled length in µm."
        )
        desc.setWordWrap(True)
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setStyleSheet("color: #aaaacc; font-size: 12px;")
        layout.addWidget(desc)

        # -- Auto-detect notice --
        if self._auto_bar_px:
            notice = QLabel(f"✓  Auto-detected scale bar: {self._auto_bar_px} pixels wide")
            notice.setObjectName("status_ok")
            notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(notice)

        # -- Input form --
        form_group = QGroupBox("Calibration Values")
        form = QFormLayout(form_group)
        form.setSpacing(10)

        self.bar_px_spin = QSpinBox()
        self.bar_px_spin.setRange(1, 100000)
        self.bar_px_spin.setValue(self._auto_bar_px or 100)
        self.bar_px_spin.setSuffix("  px")
        self.bar_px_spin.setToolTip("Length of the scale bar in pixels")
        form.addRow("Scale bar length (pixels):", self.bar_px_spin)

        self.bar_um_spin = QDoubleSpinBox()
        self.bar_um_spin.setRange(0.001, 100000.0)
        self.bar_um_spin.setValue(1.0)
        self.bar_um_spin.setDecimals(4)
        self.bar_um_spin.setSuffix("  µm")
        self.bar_um_spin.setToolTip("The labeled length on the scale bar (in micrometers)")
        form.addRow("Scale bar label (µm):", self.bar_um_spin)

        # Common presets
        preset_layout = QHBoxLayout()
        preset_label = QLabel("Quick presets:")
        preset_label.setStyleSheet("color: #aaaacc; font-size: 11px;")
        preset_layout.addWidget(preset_label)
        for label_text, um_val in [("100 nm", 0.1), ("500 nm", 0.5), ("1 µm", 1.0),
                                    ("5 µm", 5.0), ("10 µm", 10.0), ("100 µm", 100.0)]:
            btn = QPushButton(label_text)
            btn.setFixedHeight(24)
            btn.setStyleSheet("font-size: 10px; padding: 2px 8px;")
            btn.clicked.connect(lambda checked, v=um_val: self.bar_um_spin.setValue(v))
            preset_layout.addWidget(btn)
        preset_layout.addStretch()
        form.addRow("", preset_layout)

        layout.addWidget(form_group)

        # -- Live result --
        self.result_label = QLabel()
        self.result_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result_label.setStyleSheet(
            "background: #1a2b4a; border-radius: 6px; padding: 10px; "
            "color: #4caf76; font-size: 13px; font-weight: 600;"
        )
        layout.addWidget(self.result_label)

        self.bar_px_spin.valueChanged.connect(self._update_result)
        self.bar_um_spin.valueChanged.connect(self._update_result)
        self._update_result()

        # -- Buttons --
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        ok_btn = btns.button(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setText("Apply Calibration")
        ok_btn.setObjectName("primary")
        layout.addWidget(btns)

    def _update_result(self):
        px = self.bar_px_spin.value()
        um = self.bar_um_spin.value()
        if um > 0:
            ppu = px / um
            self.result_label.setText(
                f"Calibration:  {ppu:.4f} px/µm   ({1/ppu*1000:.4f} nm/px)"
            )
            self._result_px_per_um = ppu
        else:
            self.result_label.setText("Enter a valid µm value")

    def _accept(self):
        if self._result_px_per_um and self._result_px_per_um > 0:
            self.calibration_set.emit(self._result_px_per_um)
            self.accept()
        else:
            QMessageBox.warning(self, "Invalid", "Please enter valid calibration values.")

    def get_px_per_um(self):
        return self._result_px_per_um
