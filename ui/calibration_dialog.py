"""
Calibration Dialog
==================
User clicks TWO points on the scale bar in the image,
then types the real-world distance → computes px/µm.
Applies to ALL open images.
"""

import cv2
import numpy as np

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QDoubleSpinBox, QComboBox, QGroupBox, QScrollArea, QSizePolicy,
    QWidget, QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QPoint
from PyQt6.QtGui import QPixmap, QPainter, QPen, QColor, QImage, QCursor


def _bgr_to_qpixmap(arr: np.ndarray) -> QPixmap:
    h, w = arr.shape[:2]
    rgb = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
    qi = QImage(rgb.data.tobytes(), w, h, w * 3, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qi)


class ClickableImageLabel(QLabel):
    """Label that emits image-pixel coordinates on click."""
    point_clicked = pyqtSignal(int, int)  # image x, image y

    def __init__(self, parent=None):
        super().__init__(parent)
        self._orig_pixmap = None
        self._scale = 1.0
        self._points = []          # list of (ix, iy) image coords
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    def set_pixmap_scaled(self, pix: QPixmap, max_w: int, max_h: int):
        self._orig_pixmap = pix
        self._scale = min(max_w / pix.width(), max_h / pix.height(), 1.0)
        self._redraw()

    def _redraw(self):
        if self._orig_pixmap is None:
            return
        sw = int(self._orig_pixmap.width() * self._scale)
        sh = int(self._orig_pixmap.height() * self._scale)
        pix = self._orig_pixmap.scaled(sw, sh,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)

        painter = QPainter(pix)
        for i, (ix, iy) in enumerate(self._points):
            wx = int(ix * self._scale)
            wy = int(iy * self._scale)
            color = QColor(255, 80, 80) if i == 0 else QColor(80, 255, 80)
            pen = QPen(color, 2)
            painter.setPen(pen)
            painter.drawEllipse(wx - 8, wy - 8, 16, 16)
            painter.drawLine(wx - 12, wy, wx + 12, wy)
            painter.drawLine(wx, wy - 12, wx, wy + 12)
            painter.setPen(QPen(color, 1))
            painter.drawText(wx + 10, wy - 6, f"P{i+1}")

        if len(self._points) == 2:
            x1 = int(self._points[0][0] * self._scale)
            y1 = int(self._points[0][1] * self._scale)
            x2 = int(self._points[1][0] * self._scale)
            y2 = int(self._points[1][1] * self._scale)
            painter.setPen(QPen(QColor(255, 220, 0), 2, Qt.PenStyle.DashLine))
            painter.drawLine(x1, y1, x2, y2)

        painter.end()
        self.setPixmap(pix)

    def mousePressEvent(self, event):
        if self._orig_pixmap is None:
            return
        if event.button() == Qt.MouseButton.LeftButton and len(self._points) < 2:
            lw, lh = self.width(), self.height()
            sw = int(self._orig_pixmap.width() * self._scale)
            sh = int(self._orig_pixmap.height() * self._scale)
            ox = (lw - sw) // 2
            oy = (lh - sh) // 2
            wx = event.pos().x() - ox
            wy = event.pos().y() - oy
            if 0 <= wx < sw and 0 <= wy < sh:
                ix = int(wx / self._scale)
                iy = int(wy / self._scale)
                self._points.append((ix, iy))
                self._redraw()
                self.point_clicked.emit(ix, iy)

    def reset_points(self):
        self._points = []
        self._redraw()

    def pixel_distance(self) -> float | None:
        if len(self._points) < 2:
            return None
        dx = self._points[1][0] - self._points[0][0]
        dy = self._points[1][1] - self._points[0][1]
        return float(np.sqrt(dx*dx + dy*dy))


class CalibrationDialog(QDialog):
    calibration_set = pyqtSignal(float)   # px_per_um

    def __init__(self, image_bgr: np.ndarray, auto_bar_px=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set Scale Bar — Click 2 Points")
        self.setMinimumSize(820, 620)
        self.resize(900, 680)
        self._image_bgr = image_bgr
        self._px_distance = None
        self._build_ui()

    def _build_ui(self):
        main_lay = QVBoxLayout(self)
        main_lay.setSpacing(10)

        # Instructions
        inst = QLabel(
            "Step 1: Click the START of the scale bar line on the image below.\n"
            "Step 2: Click the END of the scale bar line.\n"
            "Step 3: Enter the real-world length shown on the scale bar label, then click Apply."
        )
        inst.setStyleSheet("color: #ccccee; font-size: 12px; padding: 6px;")
        inst.setWordWrap(True)
        main_lay.addWidget(inst)

        # Image area
        self.img_label = ClickableImageLabel()
        self.img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.img_label.point_clicked.connect(self._on_point_clicked)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.img_label)
        main_lay.addWidget(scroll, 1)

        pix = _bgr_to_qpixmap(self._image_bgr)
        self.img_label.set_pixmap_scaled(pix, 860, 460)

        # Controls row
        ctrl_group = QGroupBox("Scale Bar Measurement")
        ctrl_lay = QHBoxLayout(ctrl_group)
        ctrl_lay.setSpacing(12)

        self.lbl_dist = QLabel("Pixel distance:  — px")
        self.lbl_dist.setStyleSheet("color: #aaaacc; font-size: 12px;")
        ctrl_lay.addWidget(self.lbl_dist)

        ctrl_lay.addStretch()

        ctrl_lay.addWidget(QLabel("Real-world length:"))
        self.length_spin = QDoubleSpinBox()
        self.length_spin.setRange(0.001, 100000.0)
        self.length_spin.setValue(1.0)
        self.length_spin.setDecimals(3)
        self.length_spin.setSingleStep(0.1)
        self.length_spin.setFixedWidth(100)
        ctrl_lay.addWidget(self.length_spin)

        self.unit_combo = QComboBox()
        self.unit_combo.addItems(["µm", "nm", "mm"])
        self.unit_combo.setFixedWidth(60)
        ctrl_lay.addWidget(self.unit_combo)

        self.lbl_result = QLabel("px/µm:  —")
        self.lbl_result.setStyleSheet("color: #00c8ff; font-size: 12px; font-weight: bold;")
        ctrl_lay.addWidget(self.lbl_result)

        self.length_spin.valueChanged.connect(self._update_result)
        self.unit_combo.currentIndexChanged.connect(self._update_result)

        main_lay.addWidget(ctrl_group)

        # Buttons
        btn_row = QHBoxLayout()

        self.btn_reset = QPushButton("↺  Reset Points")
        self.btn_reset.clicked.connect(self._reset)
        btn_row.addWidget(self.btn_reset)

        btn_row.addStretch()

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        self.btn_apply = QPushButton("✓  Apply to ALL Images")
        self.btn_apply.setObjectName("primary")
        self.btn_apply.setMinimumHeight(36)
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self._apply)
        btn_row.addWidget(self.btn_apply)

        main_lay.addLayout(btn_row)

    def _on_point_clicked(self, ix: int, iy: int):
        d = self.img_label.pixel_distance()
        if d is not None:
            self._px_distance = d
            self.lbl_dist.setText(f"Pixel distance:  {d:.1f} px")
            self.btn_apply.setEnabled(True)
            self._update_result()
        else:
            pts = len(self.img_label._points)
            self.lbl_dist.setText(f"Point {pts} set — click point {pts+1}")

    def _update_result(self):
        if self._px_distance is None:
            return
        length = self.length_spin.value()
        unit = self.unit_combo.currentText()
        # Convert to microns
        if unit == "nm":
            length_um = length / 1000.0
        elif unit == "mm":
            length_um = length * 1000.0
        else:
            length_um = length

        if length_um > 0:
            px_per_um = self._px_distance / length_um
            self.lbl_result.setText(f"px/µm:  {px_per_um:.4f}")

    def _reset(self):
        self.img_label.reset_points()
        self._px_distance = None
        self.lbl_dist.setText("Pixel distance:  — px")
        self.lbl_result.setText("px/µm:  —")
        self.btn_apply.setEnabled(False)

    def _apply(self):
        if self._px_distance is None:
            QMessageBox.warning(self, "No measurement", "Please click 2 points on the scale bar first.")
            return
        length = self.length_spin.value()
        unit = self.unit_combo.currentText()
        if unit == "nm":
            length_um = length / 1000.0
        elif unit == "mm":
            length_um = length * 1000.0
        else:
            length_um = length
        if length_um <= 0:
            QMessageBox.warning(self, "Invalid length", "Length must be greater than 0.")
            return
        px_per_um = self._px_distance / length_um
        self.calibration_set.emit(px_per_um)
        self.accept()
