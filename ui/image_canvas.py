"""
Image Canvas Widget
===================
Zoomable, pannable image display.
Supports click-to-select a grain and Delete key to remove it.
"""

import numpy as np
import cv2

from PyQt6.QtWidgets import QWidget, QSizePolicy, QApplication
from PyQt6.QtCore import Qt, QPoint, pyqtSignal
from PyQt6.QtGui import QPixmap, QPainter, QColor, QPen, QImage, QCursor, QWheelEvent


def numpy_bgr_to_qimage(arr):
    if arr is None:
        return QImage()
    if len(arr.shape) == 2:
        h, w = arr.shape
        return QImage(arr.data, w, h, w, QImage.Format.Format_Grayscale8)
    h, w, ch = arr.shape
    rgb = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
    return QImage(rgb.data.tobytes(), w, h, w * 3, QImage.Format.Format_RGB888)


class ImageCanvas(QWidget):
    zoom_changed   = pyqtSignal(float)
    grain_clicked  = pyqtSignal(int, int)  # ix, iy  OR  -1, grain_id (delete)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap            = QPixmap()
        self._zoom              = 1.0
        self._pan_start         = QPoint()
        self._pan_offset        = QPoint(0, 0)
        self._is_panning        = False
        self._selected_grain_id = None
        self._label_image       = None
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(400, 300)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_image(self, arr):
        if arr is None:
            self._pixmap = QPixmap()
            self.update()
            return
        qi = numpy_bgr_to_qimage(arr)
        self._pixmap = QPixmap.fromImage(qi)
        self._fit_to_window()
        self.update()

    def set_label_image(self, label_arr):
        self._label_image = label_arr

    def clear_selection(self):
        self._selected_grain_id = None
        self.update()

    def _fit_to_window(self):
        if self._pixmap.isNull():
            return
        pw, ph = self._pixmap.width(), self._pixmap.height()
        ww, wh = self.width(), self.height()
        if pw == 0 or ph == 0:
            return
        self._zoom = min(ww / pw, wh / ph) * 0.96
        self._pan_offset = QPoint(0, 0)
        self.zoom_changed.emit(self._zoom)

    def fit_to_window(self):
        self._fit_to_window()
        self.update()

    def zoom_in(self):
        self._zoom = min(self._zoom * 1.25, 20.0)
        self.zoom_changed.emit(self._zoom)
        self.update()

    def zoom_out(self):
        self._zoom = max(self._zoom / 1.25, 0.05)
        self.zoom_changed.emit(self._zoom)
        self.update()

    def _widget_to_image(self, wx, wy):
        if self._pixmap.isNull():
            return None, None
        pw = int(self._pixmap.width()  * self._zoom)
        ph = int(self._pixmap.height() * self._zoom)
        ox = (self.width()  - pw) // 2 + self._pan_offset.x()
        oy = (self.height() - ph) // 2 + self._pan_offset.y()
        ix = int((wx - ox) / self._zoom)
        iy = int((wy - oy) / self._zoom)
        if 0 <= ix < self._pixmap.width() and 0 <= iy < self._pixmap.height():
            return ix, iy
        return None, None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor(20, 20, 26))
        if self._pixmap.isNull():
            painter.setPen(QColor(100, 100, 120))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Load an SEM image to begin")
            return
        pw = int(self._pixmap.width()  * self._zoom)
        ph = int(self._pixmap.height() * self._zoom)
        x  = (self.width()  - pw) // 2 + self._pan_offset.x()
        y  = (self.height() - ph) // 2 + self._pan_offset.y()
        painter.drawPixmap(x, y, pw, ph, self._pixmap)
        if self._selected_grain_id is not None:
            pen = QPen(QColor(255, 80, 80), 2)
            painter.setPen(pen)
            painter.drawText(x + 6, y + 20,
                             f"Grain {self._selected_grain_id} selected  —  press Delete to remove")

    def wheelEvent(self, event):
        if event.angleDelta().y() > 0:
            self.zoom_in()
        else:
            self.zoom_out()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton or \
           (event.button() == Qt.MouseButton.LeftButton and
                QApplication.keyboardModifiers() == Qt.KeyboardModifier.AltModifier):
            self._is_panning = True
            self._pan_start  = event.pos() - self._pan_offset
            self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
        elif event.button() == Qt.MouseButton.LeftButton:
            ix, iy = self._widget_to_image(event.pos().x(), event.pos().y())
            if ix is not None and self._label_image is not None:
                label_val = int(self._label_image[iy, ix])
                if label_val > 0:
                    self._selected_grain_id = label_val
                    self.grain_clicked.emit(ix, iy)
                else:
                    self._selected_grain_id = None
                self.update()
                self.setFocus()

    def mouseMoveEvent(self, event):
        if self._is_panning:
            self._pan_offset = event.pos() - self._pan_start
            self.update()

    def mouseReleaseEvent(self, event):
        self._is_panning = False
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Delete and self._selected_grain_id is not None:
            gid = self._selected_grain_id
            self._selected_grain_id = None
            self.update()
            self.grain_clicked.emit(-1, gid)
        else:
            super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._pixmap.isNull():
            self._fit_to_window()
