"""
Image Canvas Widget
===================
Zoomable, pannable image display with grain overlay.
"""

from PyQt6.QtWidgets import QWidget, QScrollArea, QLabel, QSizePolicy, QApplication
from PyQt6.QtCore import Qt, QPoint, pyqtSignal, QRect
from PyQt6.QtGui import QPixmap, QPainter, QColor, QPen, QImage, QCursor, QWheelEvent
import numpy as np
import cv2


def numpy_bgr_to_qimage(arr: np.ndarray) -> QImage:
    """Convert a BGR numpy array to QImage."""
    if arr is None:
        return QImage()
    if len(arr.shape) == 2:
        # Grayscale
        h, w = arr.shape
        return QImage(arr.data, w, h, w, QImage.Format.Format_Grayscale8)
    h, w, ch = arr.shape
    rgb = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
    return QImage(rgb.data.tobytes(), w, h, w * 3, QImage.Format.Format_RGB888)


class ImageCanvas(QWidget):
    """
    Widget that displays an image with zoom/pan and optional grain overlay.
    """
    zoom_changed = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._zoom = 1.0
        self._pan_start = QPoint()
        self._pan_offset = QPoint(0, 0)
        self._is_panning = False
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(400, 300)
        self._show_overlay = True

    def set_image(self, arr: np.ndarray):
        """Display a numpy BGR image."""
        if arr is None:
            self._pixmap = QPixmap()
            self.update()
            return
        qi = numpy_bgr_to_qimage(arr)
        self._pixmap = QPixmap.fromImage(qi)
        self._zoom = 1.0
        self._pan_offset = QPoint(0, 0)
        self._fit_to_window()
        self.update()

    def _fit_to_window(self):
        if self._pixmap.isNull():
            return
        pw, ph = self._pixmap.width(), self._pixmap.height()
        ww, wh = self.width(), self.height()
        if pw == 0 or ph == 0:
            return
        scale_w = ww / pw
        scale_h = wh / ph
        self._zoom = min(scale_w, scale_h) * 0.96
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

    def set_zoom(self, zoom: float):
        self._zoom = max(0.05, min(zoom, 20.0))
        self.zoom_changed.emit(self._zoom)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor(20, 20, 26))

        if self._pixmap.isNull():
            painter.setPen(QColor(100, 100, 120))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Load an SEM image to begin")
            return

        pw = int(self._pixmap.width() * self._zoom)
        ph = int(self._pixmap.height() * self._zoom)
        x = (self.width() - pw) // 2 + self._pan_offset.x()
        y = (self.height() - ph) // 2 + self._pan_offset.y()
        painter.drawPixmap(x, y, pw, ph, self._pixmap)

    def wheelEvent(self, event: QWheelEvent):
        delta = event.angleDelta().y()
        if delta > 0:
            self.zoom_in()
        else:
            self.zoom_out()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton or \
           (event.button() == Qt.MouseButton.LeftButton and
            QApplication.keyboardModifiers() == Qt.KeyboardModifier.AltModifier):
            self._is_panning = True
            self._pan_start = event.pos() - self._pan_offset
            self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))

    def mouseMoveEvent(self, event):
        if self._is_panning:
            self._pan_offset = event.pos() - self._pan_start
            self.update()

    def mouseReleaseEvent(self, event):
        self._is_panning = False
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._pixmap.isNull():
            self._fit_to_window()
