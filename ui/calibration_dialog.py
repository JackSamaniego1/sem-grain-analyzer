"""
Calibration Dialog - with zoom/pan for accurate 2-point clicking
"""

import cv2
import numpy as np

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QDoubleSpinBox, QComboBox, QGroupBox, QSizePolicy,
    QWidget, QMessageBox, QScrollBar
)
from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QPointF
from PyQt6.QtGui import (
    QPixmap, QPainter, QPen, QColor, QImage, QCursor, QWheelEvent
)


def _bgr_to_qpixmap(arr: np.ndarray) -> QPixmap:
    h, w = arr.shape[:2]
    rgb = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
    qi = QImage(rgb.data.tobytes(), w, h, w * 3, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qi)


class ZoomableCalibCanvas(QWidget):
    """
    Zoomable, pannable canvas for picking 2 calibration points.
    Scroll wheel to zoom, middle-click or Alt+drag to pan.
    Left-click to place points (max 2).
    """
    point_placed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._orig_pixmap: QPixmap | None = None
        self._zoom = 1.0
        self._pan_offset = QPointF(0, 0)
        self._pan_start = QPoint()
        self._is_panning = False
        self._points: list[tuple[int, int]] = []   # image coords
        self.setMinimumSize(700, 440)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_image(self, arr: np.ndarray):
        self._orig_pixmap = _bgr_to_qpixmap(arr)
        # Fit to widget initially
        if self._orig_pixmap.width() > 0:
            self._zoom = min(
                self.width()  / self._orig_pixmap.width(),
                self.height() / self._orig_pixmap.height(),
                1.0
            )
        self._pan_offset = QPointF(0, 0)
        self._points = []
        self.update()

    def reset_points(self):
        self._points = []
        self.update()

    def pixel_distance(self) -> float | None:
        if len(self._points) < 2:
            return None
        dx = self._points[1][0] - self._points[0][0]
        dy = self._points[1][1] - self._points[0][1]
        return float(np.sqrt(dx * dx + dy * dy))

    def point_count(self) -> int:
        return len(self._points)

    # ---- coordinate helpers ----

    def _img_origin(self) -> QPointF:
        """Top-left corner of the image in widget coords."""
        if self._orig_pixmap is None:
            return QPointF(0, 0)
        pw = self._orig_pixmap.width()  * self._zoom
        ph = self._orig_pixmap.height() * self._zoom
        ox = (self.width()  - pw) / 2 + self._pan_offset.x()
        oy = (self.height() - ph) / 2 + self._pan_offset.y()
        return QPointF(ox, oy)

    def _widget_to_image(self, wx: float, wy: float) -> tuple[int, int] | None:
        if self._orig_pixmap is None:
            return None
        o = self._img_origin()
        ix = (wx - o.x()) / self._zoom
        iy = (wy - o.y()) / self._zoom
        if 0 <= ix < self._orig_pixmap.width() and 0 <= iy < self._orig_pixmap.height():
            return int(ix), int(iy)
        return None

    def _image_to_widget(self, ix: int, iy: int) -> QPointF:
        o = self._img_origin()
        return QPointF(o.x() + ix * self._zoom, o.y() + iy * self._zoom)

    # ---- painting ----

    def paintEvent(self, event):
        if self._orig_pixmap is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor(20, 20, 26))

        o = self._img_origin()
        pw = int(self._orig_pixmap.width()  * self._zoom)
        ph = int(self._orig_pixmap.height() * self._zoom)
        painter.drawPixmap(int(o.x()), int(o.y()), pw, ph, self._orig_pixmap)

        # Draw crosshairs at each point
        colors = [QColor(255, 80, 80), QColor(80, 255, 80)]
        for i, (ix, iy) in enumerate(self._points):
            wp = self._image_to_widget(ix, iy)
            wx, wy = int(wp.x()), int(wp.y())
            c = colors[i]
            painter.setPen(QPen(c, 2))
            r = 10
            painter.drawEllipse(wx - r, wy - r, r * 2, r * 2)
            painter.drawLine(wx - r - 4, wy, wx + r + 4, wy)
            painter.drawLine(wx, wy - r - 4, wx, wy + r + 4)
            painter.setPen(QPen(c, 1))
            painter.drawText(wx + 14, wy - 6, f"P{i+1} ({ix}, {iy})")

        # Line between points
        if len(self._points) == 2:
            p1 = self._image_to_widget(*self._points[0])
            p2 = self._image_to_widget(*self._points[1])
            painter.setPen(QPen(QColor(255, 220, 0), 2, Qt.PenStyle.DashLine))
            painter.drawLine(p1.toPoint(), p2.toPoint())
            d = self.pixel_distance()
            mid = QPointF((p1.x() + p2.x()) / 2, (p1.y() + p2.y()) / 2)
            painter.setPen(QPen(QColor(255, 220, 0), 1))
            painter.drawText(int(mid.x()) + 6, int(mid.y()) - 6, f"{d:.1f} px")

        # Zoom level indicator
        painter.setPen(QPen(QColor(150, 150, 180), 1))
        painter.drawText(8, self.height() - 8, f"Zoom: {self._zoom*100:.0f}%  |  Scroll to zoom  |  Alt+drag to pan")

    # ---- interaction ----

    def wheelEvent(self, event: QWheelEvent):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        # Zoom around mouse position
        mouse_pos = event.position()
        o = self._img_origin()
        # image coord under mouse before zoom
        ix_before = (mouse_pos.x() - o.x()) / self._zoom
        iy_before = (mouse_pos.y() - o.y()) / self._zoom

        self._zoom = max(0.1, min(self._zoom * factor, 20.0))

        # Adjust pan so the same image point stays under the mouse
        new_ox = mouse_pos.x() - ix_before * self._zoom
        new_oy = mouse_pos.y() - iy_before * self._zoom
        if self._orig_pixmap:
            pw = self._orig_pixmap.width()  * self._zoom
            ph = self._orig_pixmap.height() * self._zoom
            center_ox = (self.width()  - pw) / 2
            center_oy = (self.height() - ph) / 2
            self._pan_offset = QPointF(new_ox - center_ox, new_oy - center_oy)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton or \
           (event.button() == Qt.MouseButton.LeftButton and
                event.modifiers() == Qt.KeyboardModifier.AltModifier):
            self._is_panning = True
            self._pan_start = event.pos() - self._pan_offset.toPoint()
            self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))

        elif event.button() == Qt.MouseButton.LeftButton:
            if len(self._points) >= 2:
                return
            coords = self._widget_to_image(event.pos().x(), event.pos().y())
            if coords:
                self._points.append(coords)
                self.update()
                self.point_placed.emit()

    def mouseMoveEvent(self, event):
        if self._is_panning:
            self._pan_offset = QPointF(event.pos() - self._pan_start)
            self.update()

    def mouseReleaseEvent(self, event):
        self._is_panning = False
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    def resizeEvent(self, event):
        super().resizeEvent(event)


class CalibrationDialog(QDialog):
    calibration_set = pyqtSignal(float)   # px_per_um

    def __init__(self, image_bgr: np.ndarray, auto_bar_px=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set Scale Bar — Scroll to zoom, click 2 points")
        self.setMinimumSize(860, 660)
        self.resize(960, 720)
        self._image_bgr = image_bgr
        self._px_distance: float | None = None
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        inst = QLabel(
            "🔍  Scroll wheel to zoom in for precision.  Alt+drag to pan.\n"
            "① Click the LEFT end of the scale bar line.  "
            "② Click the RIGHT end.  "
            "③ Enter the real-world length shown on the label → Apply."
        )
        inst.setStyleSheet("color: #ccccee; font-size: 12px; padding: 6px; "
                           "background: #1e1e2e; border-radius: 4px;")
        inst.setWordWrap(True)
        lay.addWidget(inst)

        # Zoomable canvas
        self.canvas = ZoomableCalibCanvas()
        self.canvas.set_image(self._image_bgr)
        self.canvas.point_placed.connect(self._on_point_placed)
        lay.addWidget(self.canvas, 1)

        # Controls
        ctrl_group = QGroupBox("Measurement")
        ctrl_lay = QHBoxLayout(ctrl_group)
        ctrl_lay.setSpacing(12)

        self.lbl_dist = QLabel("Pixel distance:  — px")
        self.lbl_dist.setStyleSheet("color: #aaaacc; font-size: 12px;")
        ctrl_lay.addWidget(self.lbl_dist)

        ctrl_lay.addStretch()

        ctrl_lay.addWidget(QLabel("Real-world length:"))
        self.length_spin = QDoubleSpinBox()
        self.length_spin.setRange(0.001, 100000.0)
        self.length_spin.setValue(50.0)
        self.length_spin.setDecimals(3)
        self.length_spin.setSingleStep(1.0)
        self.length_spin.setFixedWidth(110)
        ctrl_lay.addWidget(self.length_spin)

        self.unit_combo = QComboBox()
        self.unit_combo.addItems(["µm", "nm", "mm"])
        self.unit_combo.setFixedWidth(64)
        ctrl_lay.addWidget(self.unit_combo)

        self.lbl_result = QLabel("px/µm:  —")
        self.lbl_result.setStyleSheet("color: #00c8ff; font-size: 13px; font-weight: bold;")
        ctrl_lay.addWidget(self.lbl_result)

        self.length_spin.valueChanged.connect(self._update_result)
        self.unit_combo.currentIndexChanged.connect(self._update_result)

        lay.addWidget(ctrl_group)

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

        lay.addLayout(btn_row)

    def _on_point_placed(self):
        n = self.canvas.point_count()
        d = self.canvas.pixel_distance()
        if d is not None:
            self._px_distance = d
            self.lbl_dist.setText(f"Pixel distance:  {d:.1f} px")
            self.btn_apply.setEnabled(True)
            self._update_result()
        else:
            self.lbl_dist.setText(f"Point {n} placed — click point {n+1}")

    def _update_result(self):
        if self._px_distance is None:
            return
        length = self.length_spin.value()
        unit = self.unit_combo.currentText()
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
        self.canvas.reset_points()
        self._px_distance = None
        self.lbl_dist.setText("Pixel distance:  — px")
        self.lbl_result.setText("px/µm:  —")
        self.btn_apply.setEnabled(False)

    def _apply(self):
        if self._px_distance is None:
            QMessageBox.warning(self, "No measurement", "Please click 2 points first.")
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
            QMessageBox.warning(self, "Invalid", "Length must be > 0.")
            return
        px_per_um = self._px_distance / length_um
        self.calibration_set.emit(px_per_um)
        self.accept()
