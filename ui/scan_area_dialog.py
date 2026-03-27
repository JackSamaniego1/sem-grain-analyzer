"""
Scan Area Dialog
================
User draws a rectangle on the image to define the region to analyze.
Anything outside the rectangle (e.g. the bottom legend bar) is excluded.
"""

import cv2
import numpy as np

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QWidget, QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QRect, QRectF, QPointF
from PyQt6.QtGui import (
    QPixmap, QPainter, QPen, QColor, QImage, QCursor,
    QBrush, QWheelEvent
)


def _bgr_to_qpixmap(arr: np.ndarray) -> QPixmap:
    h, w = arr.shape[:2]
    rgb = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
    qi = QImage(rgb.data.tobytes(), w, h, w * 3, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qi)


class RectDrawCanvas(QWidget):
    """Canvas that lets the user drag a rectangle and zoom/pan."""
    rect_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._orig_pixmap: QPixmap | None = None
        self._zoom = 1.0
        self._pan_offset = QPointF(0, 0)
        self._pan_start = QPoint()
        self._is_panning = False
        self._drag_start: tuple[int, int] | None = None   # image coords
        self._drag_end:   tuple[int, int] | None = None
        self._rect: tuple[int, int, int, int] | None = None  # x,y,w,h image coords
        self.setMinimumSize(700, 480)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    def set_image(self, arr: np.ndarray):
        self._orig_pixmap = _bgr_to_qpixmap(arr)
        self._zoom = min(
            self.width()  / self._orig_pixmap.width(),
            self.height() / self._orig_pixmap.height(),
            1.0
        )
        self._pan_offset = QPointF(0, 0)
        self._rect = None
        self._drag_start = None
        self._drag_end = None
        self.update()

    def get_rect(self) -> tuple[int, int, int, int] | None:
        """Returns (x, y, w, h) in image pixel coordinates, or None."""
        return self._rect

    def clear_rect(self):
        self._rect = None
        self._drag_start = None
        self._drag_end = None
        self.update()

    def _img_origin(self) -> QPointF:
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
        ix = max(0, min(int(ix), self._orig_pixmap.width()  - 1))
        iy = max(0, min(int(iy), self._orig_pixmap.height() - 1))
        return ix, iy

    def _image_to_widget(self, ix: int, iy: int) -> QPointF:
        o = self._img_origin()
        return QPointF(o.x() + ix * self._zoom, o.y() + iy * self._zoom)

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

        # Shade outside the rect if we have one
        rect = self._rect
        if rect is None and self._drag_start and self._drag_end:
            x1 = min(self._drag_start[0], self._drag_end[0])
            y1 = min(self._drag_start[1], self._drag_end[1])
            x2 = max(self._drag_start[0], self._drag_end[0])
            y2 = max(self._drag_start[1], self._drag_end[1])
            rect = (x1, y1, x2 - x1, y2 - y1)

        if rect:
            rx, ry, rw, rh = rect
            p1 = self._image_to_widget(rx, ry)
            p2 = self._image_to_widget(rx + rw, ry + rh)
            wRect = QRectF(p1, p2).toRect()

            # Dark overlay outside rect
            shade = QColor(0, 0, 0, 120)
            painter.fillRect(int(o.x()), int(o.y()), pw, wRect.top() - int(o.y()), shade)
            painter.fillRect(int(o.x()), wRect.bottom(), pw, int(o.y()) + ph - wRect.bottom(), shade)
            painter.fillRect(int(o.x()), wRect.top(), wRect.left() - int(o.x()), wRect.height(), shade)
            painter.fillRect(wRect.right(), wRect.top(), int(o.x()) + pw - wRect.right(), wRect.height(), shade)

            # Green border
            painter.setPen(QPen(QColor(0, 255, 100), 2, Qt.PenStyle.SolidLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(wRect)
            painter.setPen(QPen(QColor(0, 255, 100), 1))
            painter.drawText(wRect.left() + 4, wRect.top() + 16,
                             f"Analysis area: {rect[2]}×{rect[3]} px")

        painter.setPen(QPen(QColor(150, 150, 180), 1))
        painter.drawText(8, self.height() - 8,
                         f"Zoom: {self._zoom*100:.0f}%  |  Drag to draw rectangle  |  Alt+drag to pan  |  Scroll to zoom")

    def wheelEvent(self, event: QWheelEvent):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        mouse_pos = event.position()
        o = self._img_origin()
        ix_before = (mouse_pos.x() - o.x()) / self._zoom
        iy_before = (mouse_pos.y() - o.y()) / self._zoom
        self._zoom = max(0.1, min(self._zoom * factor, 20.0))
        if self._orig_pixmap:
            pw = self._orig_pixmap.width()  * self._zoom
            ph = self._orig_pixmap.height() * self._zoom
            new_ox = mouse_pos.x() - ix_before * self._zoom
            new_oy = mouse_pos.y() - iy_before * self._zoom
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
            self._drag_start = self._widget_to_image(event.pos().x(), event.pos().y())
            self._drag_end = self._drag_start
            self._rect = None
            self.update()

    def mouseMoveEvent(self, event):
        if self._is_panning:
            self._pan_offset = QPointF(event.pos() - self._pan_start)
            self.update()
        elif self._drag_start and event.buttons() & Qt.MouseButton.LeftButton:
            self._drag_end = self._widget_to_image(event.pos().x(), event.pos().y())
            self.update()

    def mouseReleaseEvent(self, event):
        if self._is_panning:
            self._is_panning = False
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        elif event.button() == Qt.MouseButton.LeftButton and self._drag_start and self._drag_end:
            x1 = min(self._drag_start[0], self._drag_end[0])
            y1 = min(self._drag_start[1], self._drag_end[1])
            x2 = max(self._drag_start[0], self._drag_end[0])
            y2 = max(self._drag_start[1], self._drag_end[1])
            if x2 - x1 > 10 and y2 - y1 > 10:
                self._rect = (x1, y1, x2 - x1, y2 - y1)
                self._drag_start = None
                self._drag_end = None
                self.rect_changed.emit()
            self.update()


class ScanAreaDialog(QDialog):
    """Dialog for setting the analysis scan rectangle."""
    scan_area_set = pyqtSignal(int, int, int, int)   # x, y, w, h  (image coords)

    def __init__(self, image_bgr: np.ndarray, current_rect=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set Scan Area — Drag a rectangle over the analysis region")
        self.setMinimumSize(860, 680)
        self.resize(960, 740)
        self._image_bgr = image_bgr
        self._current_rect = current_rect
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        inst = QLabel(
            "Drag a rectangle over the area you want to analyze.\n"
            "Everything outside (e.g. the bottom legend bar) will be excluded.\n"
            "Scroll to zoom, Alt+drag to pan."
        )
        inst.setStyleSheet("color: #ccccee; font-size: 12px; padding: 6px; "
                           "background: #1e1e2e; border-radius: 4px;")
        inst.setWordWrap(True)
        lay.addWidget(inst)

        self.canvas = RectDrawCanvas()
        self.canvas.set_image(self._image_bgr)
        self.canvas.rect_changed.connect(self._on_rect_changed)
        lay.addWidget(self.canvas, 1)

        self.lbl_rect = QLabel("No area set — drag a rectangle on the image")
        self.lbl_rect.setStyleSheet("color: #aaaacc; font-size: 11px;")
        self.lbl_rect.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_rect)

        btn_row = QHBoxLayout()

        btn_reset = QPushButton("↺  Reset (use full image)")
        btn_reset.clicked.connect(self._reset)
        btn_row.addWidget(btn_reset)

        btn_row.addStretch()

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        self.btn_apply = QPushButton("✓  Set Scan Area")
        self.btn_apply.setObjectName("primary")
        self.btn_apply.setMinimumHeight(36)
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self._apply)
        btn_row.addWidget(self.btn_apply)

        lay.addLayout(btn_row)

    def _on_rect_changed(self):
        r = self.canvas.get_rect()
        if r:
            x, y, w, h = r
            self.lbl_rect.setText(f"Scan area: x={x}, y={y}, width={w}, height={h} px")
            self.btn_apply.setEnabled(True)

    def _reset(self):
        self.canvas.clear_rect()
        self.lbl_rect.setText("No area set — drag a rectangle on the image")
        self.btn_apply.setEnabled(False)
        # Emit full image size
        h, w = self._image_bgr.shape[:2]
        self.scan_area_set.emit(0, 0, w, h)
        self.accept()

    def _apply(self):
        r = self.canvas.get_rect()
        if r is None:
            QMessageBox.warning(self, "No area", "Please drag a rectangle first.")
            return
        x, y, w, h = r
        self.scan_area_set.emit(x, y, w, h)
        self.accept()
