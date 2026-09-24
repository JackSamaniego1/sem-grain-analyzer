"""
Calibration canvas (UI-11) - zoomable image on which the user measures the
scale bar in one of three modes:

``"rect"``   Rectangle: drag a box over the bar; only its WIDTH is the bar
             length.  8 resize handles, drag inside to move, arrow keys nudge
             1 px (Shift: 10 px).  After drawing/resizing the vertical edges
             snap to the bar ends found inside the box.
``"level"``  Level line (default): two clicks, the line is locked horizontal;
             the ends stay draggable left/right only and snap to a bar end
             within ~6 px.  A 4x magnifier loupe follows the cursor while
             placing or dragging an end.
``"free"``   Free line: the original two-click method with a tilt readout.

Snapping is skipped while Alt is held.  Pan with the middle or right mouse
button, zoom with the wheel (about the cursor).

Image coordinates are continuous: pixel ``i`` covers ``[i, i + 1)``, so a bar
occupying columns ``x .. x + w - 1`` measures ``w`` px from end to end (the
same convention as ``core.scale_bar.find_scale_bar_line``).
"""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QColor, QCursor, QFont, QImage, QPainter, QPainterPath,
                           QPen, QPixmap, QWheelEvent)
from PySide6.QtWidgets import QSizePolicy, QWidget

from core.scale_bar_snap import find_bar_ends_in_roi, snap_x
from ui.design.theme import current_tokens
from ui.design.tokens import MOTION
from ui.widgets._base import animate_value, stop

MODES = ("rect", "level", "free")
MODE_LABELS = {"rect": "Rectangle", "level": "Level line", "free": "Free line"}
DEFAULT_MODE = "level"
SNAP_TOL_PX = 6.0          # image px: an end snaps to a bar end this close
TILT_WARN_DEG = 1.0        # free line: warn above this tilt
LOUPE_SIZE = 136           # widget px
LOUPE_FACTOR = 4.0         # magnification relative to the current view
_HANDLE_HIT = 9            # widget px
_RECT_PAD = 6.0            # image px above/below a bar when a box is built

# Measurement overlay colours: fixed, high-contrast on grey SEM images in
# both themes (the chrome around the image follows the theme tokens).
_C_LINE = QColor(255, 204, 0)
_C_P1 = QColor(255, 96, 96)
_C_P2 = QColor(96, 230, 120)
_C_SNAP = QColor(64, 220, 140)
_C_WARN = QColor(255, 170, 40)
_C_SHADOW = QColor(0, 0, 0, 170)


def _bgr_to_qpixmap(arr: np.ndarray) -> QPixmap:
    h, w = arr.shape[:2]
    if arr.ndim == 2:
        arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    rgb = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
    qi = QImage(rgb.data.tobytes(), w, h, w * 3, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qi)


class CalibrationCanvas(QWidget):
    """Zoomable, pannable canvas for measuring the scale bar (see module doc).

    ``point_placed`` fires whenever the measurement changes (a point is
    placed, an end or the box is dragged/nudged, snapping is applied or
    undone); ``snap_changed(bool)`` when the snapped state flips.
    """
    point_placed = Signal()
    snap_changed = Signal(bool)
    mode_changed = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None, mode: str = DEFAULT_MODE):
        super().__init__(parent)
        self._orig_pixmap: Optional[QPixmap] = None
        self._gray: Optional[np.ndarray] = None
        self._zoom = 1.0
        self._pan_offset = QPointF(0, 0)
        self._pan_start = QPointF()
        self._is_panning = False
        self._mode = mode if mode in MODES else DEFAULT_MODE
        self._pts: List[List[float]] = []             # line modes, image coords
        self._rect: Optional[List[float]] = None      # [x0, y0, x1, y1]
        self._end_snapped = [False, False]            # left/first, right/second
        self._pre_snap = None                         # geometry before last snap
        self._snap_enabled = True
        self._active_end = 1                          # end nudged by arrow keys
        self._drag: Optional[dict] = None
        self._hover: Optional[Tuple[float, float]] = None   # image coords
        self._pulse = 0.0
        self._pulse_anim = None
        self.setMinimumSize(700, 440)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAccessibleName("Scale bar measurement canvas")
        self.setToolTip("Scroll to zoom, middle- or right-drag to pan. Hold Alt to place "
                        "without snapping. Arrow keys nudge (Shift: 10 px).")

    # ------------------------------------------------------------------ image
    def set_image(self, arr: np.ndarray) -> None:
        arr = np.asarray(arr)
        self._orig_pixmap = _bgr_to_qpixmap(arr)
        self._gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY) if arr.ndim == 3 else arr
        self._fit()
        self._pts, self._rect = [], None
        self._clear_snap(emit=False)
        self.update()

    def _fit(self) -> None:
        if self._orig_pixmap is None or self._orig_pixmap.width() <= 0:
            return
        w = max(self.width(), 1)
        h = max(self.height(), 1)
        self._zoom = min(w / self._orig_pixmap.width(), h / self._orig_pixmap.height(), 1.0)
        self._pan_offset = QPointF(0, 0)

    def zoom(self) -> float:
        return self._zoom

    def set_zoom(self, z: float) -> None:
        self._zoom = max(0.05, min(float(z), 40.0))
        self.update()

    def center_on(self, ix: float, iy: float) -> None:
        """Pan so image point ``(ix, iy)`` is at the widget centre."""
        if self._orig_pixmap is None:
            return
        pw = self._orig_pixmap.width() * self._zoom
        ph = self._orig_pixmap.height() * self._zoom
        ox = self.width() / 2 - ix * self._zoom
        oy = self.height() / 2 - iy * self._zoom
        self._pan_offset = QPointF(ox - (self.width() - pw) / 2, oy - (self.height() - ph) / 2)
        self.update()

    # ------------------------------------------------------------------- mode
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        """Switch mode, carrying the current measurement over (a box becomes a
        level line through its middle and vice versa)."""
        if mode not in MODES or mode == self._mode:
            return
        old, self._mode = self._mode, mode
        self._drag = None
        if mode == "rect":
            if len(self._pts) == 2:
                (xa, ya), (xb, yb) = self._pts
                cy = (ya + yb) / 2.0
                self._rect = [min(xa, xb), cy - _RECT_PAD, max(xa, xb), cy + _RECT_PAD]
                if xa > xb:
                    self._end_snapped.reverse()
            else:
                self._rect = None
            self._pts = []
        else:
            if old == "rect":
                if self._rect is not None:
                    x0, y0, x1, y1 = self._rect
                    cy = (y0 + y1) / 2.0
                    self._pts = [[x0, cy], [x1, cy]]
                else:
                    self._pts = []
                self._rect = None
            if mode == "level" and self._pts:
                y = (sum(p[1] for p in self._pts) / len(self._pts))
                for p in self._pts:
                    p[1] = y
        self._pre_snap = None
        self.mode_changed.emit(mode)
        self.point_placed.emit()
        self.update()

    # ---------------------------------------------------------------- snapping
    def set_snap_enabled(self, on: bool) -> None:
        self._snap_enabled = bool(on)

    def snap_enabled(self) -> bool:
        return self._snap_enabled

    def is_snapped(self) -> bool:
        """Both ends sit on detected bar ends."""
        n = self.point_count()
        return n == 2 and all(self._end_snapped)

    def can_undo_snap(self) -> bool:
        return self._pre_snap is not None

    def _geometry(self):
        return ([list(p) for p in self._pts],
                list(self._rect) if self._rect else None,
                list(self._end_snapped))

    def _restore(self, g) -> None:
        pts, rect, snapped = g
        self._pts = [list(p) for p in pts]
        self._rect = list(rect) if rect else None
        self._end_snapped = list(snapped)

    def undo_snap(self) -> bool:
        """Put the geometry back to where it was before the last snap."""
        if self._pre_snap is None:
            return False
        self._restore(self._pre_snap)
        self._pre_snap = None
        self.snap_changed.emit(self.is_snapped())
        self.point_placed.emit()
        self.update()
        return True

    def _clear_snap(self, emit: bool = True) -> None:
        was = self.is_snapped() if (self._pts or self._rect) else False
        self._end_snapped = [False, False]
        self._pre_snap = None
        if emit and was:
            self.snap_changed.emit(False)

    def _flash(self) -> None:
        stop(self._pulse_anim)
        self._pulse_anim = animate_value(self, 1.0, 0.0, MOTION.slow * 2, self._set_pulse)

    def _set_pulse(self, v) -> None:
        self._pulse = float(v)
        self.update()

    def snap_rect(self) -> bool:
        """Snap the box's vertical edges to the bar ends found inside it."""
        if self._rect is None or self._gray is None:
            return False
        x0, y0, x1, y1 = self._rect
        found = find_bar_ends_in_roi(self._gray, (x0, y0, x1 - x0, y1 - y0))
        if not found:
            return False
        before = self._geometry()
        self._rect[0], self._rect[2] = found["x0"], found["x1"]
        # keep the box around the bar vertically if it was drawn too flat
        self._rect[1] = min(y0, found["y0"] - 2)
        self._rect[3] = max(y1, found["y1"] + 2)
        self._end_snapped = [True, True]
        self._pre_snap = before
        self.snap_changed.emit(True)
        self._flash()
        return True

    def _snap_end(self, i: int) -> bool:
        """Level line: snap end ``i`` to a bar end within SNAP_TOL_PX."""
        if self._gray is None or i >= len(self._pts):
            return False
        x, y = self._pts[i]
        hit = snap_x(self._gray, x, y, SNAP_TOL_PX)
        if hit is None:
            self._end_snapped[i] = False
            return False
        self._pts[i][0] = hit[0]
        if len(self._pts) == 1 or not self._end_snapped[1 - i]:
            # centre the line on the bar vertically
            for p in self._pts:
                p[1] = hit[1]
        self._end_snapped[i] = True
        return True

    # ------------------------------------------------------ measurement API
    def reset_points(self) -> None:
        self._pts, self._rect = [], None
        self._drag = None
        self._clear_snap(emit=False)
        self.snap_changed.emit(False)
        self.update()

    def set_points(self, p1, p2) -> None:
        """Place both ends programmatically.  Level mode keeps ``p1``'s y;
        rectangle mode builds a box spanning the two x positions."""
        (xa, ya), (xb, yb) = (float(p1[0]), float(p1[1])), (float(p2[0]), float(p2[1]))
        self._clear_snap(emit=False)
        if self._mode == "rect":
            cy = (ya + yb) / 2.0
            self._rect = [min(xa, xb), cy - _RECT_PAD, max(xa, xb), cy + _RECT_PAD]
            self._pts = []
        else:
            if self._mode == "level":
                yb = ya
            self._pts = [[xa, ya], [xb, yb]]
            self._rect = None
        self.update()
        self.point_placed.emit()

    def set_rect(self, x0: float, y0: float, x1: float, y1: float, snap: bool = False) -> None:
        """Rectangle mode: set the box (image coords); optionally snap it."""
        self._rect = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
        self._pts = []
        self._clear_snap(emit=False)
        if snap and self._snap_enabled:
            self.snap_rect()
        self.update()
        self.point_placed.emit()

    def set_bar(self, rect) -> None:
        """Show an automatically found bar ``(x, y, w, h)`` as an editable box
        or level line (depending on the mode); its ends count as snapped."""
        x, y, w, h = (float(v) for v in rect)
        if self._mode == "rect":
            self._rect = [x, y - _RECT_PAD, x + w, y + h + _RECT_PAD]
            self._pts = []
        else:
            cy = y + h / 2.0
            self._pts = [[x, cy], [x + w, cy]]
            self._rect = None
        self._end_snapped = [True, True]
        self._pre_snap = None
        self.snap_changed.emit(True)
        self.update()
        self.point_placed.emit()

    def box(self) -> Optional[Tuple[float, float, float, float]]:
        """The box ``(x0, y0, x1, y1)`` in image coords (rectangle mode)."""
        return tuple(self._rect) if self._rect else None

    def points(self) -> List[Tuple[float, float]]:
        return [tuple(p) for p in self._pts]

    def point_count(self) -> int:
        if self._mode == "rect":
            return 2 if self._rect is not None and self._rect[2] > self._rect[0] else 0
        return len(self._pts)

    def pixel_distance(self) -> Optional[float]:
        """Bar length in image px: box width (rectangle) or line length."""
        if self._mode == "rect":
            if self._rect is None:
                return None
            w = self._rect[2] - self._rect[0]
            return float(w) if w > 0 else None
        if len(self._pts) < 2:
            return None
        dx = self._pts[1][0] - self._pts[0][0]
        dy = self._pts[1][1] - self._pts[0][1]
        d = math.hypot(dx, dy)
        return d if d > 0 else None

    def tilt_deg(self) -> Optional[float]:
        """Angle of the line from horizontal, 0-90 deg (None for a box)."""
        if self._mode == "rect":
            return 0.0 if self._rect is not None else None
        if len(self._pts) < 2:
            return None
        dx = abs(self._pts[1][0] - self._pts[0][0])
        dy = abs(self._pts[1][1] - self._pts[0][1])
        if dx == 0 and dy == 0:
            return None
        return math.degrees(math.atan2(dy, dx))

    def level_line(self) -> bool:
        """Free line: make it horizontal about its mid-height."""
        if len(self._pts) < 2:
            return False
        y = (self._pts[0][1] + self._pts[1][1]) / 2.0
        self._pts[0][1] = self._pts[1][1] = y
        self.update()
        self.point_placed.emit()
        return True

    def uncertainty_px(self) -> Optional[float]:
        """Reading uncertainty of the length: +-0.5 px per detected end,
        otherwise the larger of 0.5 px and one screen pixel at the current zoom."""
        if self.pixel_distance() is None:
            return None
        manual = max(0.5, 1.0 / max(self._zoom, 1e-6))
        return sum(0.5 if s else manual for s in self._end_snapped)

    # ----------------------------------------------------- coordinate helpers
    def _img_origin(self) -> QPointF:
        if self._orig_pixmap is None:
            return QPointF(0, 0)
        pw = self._orig_pixmap.width() * self._zoom
        ph = self._orig_pixmap.height() * self._zoom
        return QPointF((self.width() - pw) / 2 + self._pan_offset.x(),
                       (self.height() - ph) / 2 + self._pan_offset.y())

    def _to_img(self, wx: float, wy: float, clamp: bool = False) -> Optional[Tuple[float, float]]:
        if self._orig_pixmap is None:
            return None
        o = self._img_origin()
        ix = (wx - o.x()) / self._zoom
        iy = (wy - o.y()) / self._zoom
        W, H = self._orig_pixmap.width(), self._orig_pixmap.height()
        if clamp:
            return min(max(ix, 0.0), float(W)), min(max(iy, 0.0), float(H))
        if 0 <= ix < W and 0 <= iy < H:
            return ix, iy
        return None

    def _to_widget(self, ix: float, iy: float) -> QPointF:
        o = self._img_origin()
        return QPointF(o.x() + ix * self._zoom, o.y() + iy * self._zoom)

    # compatibility with the v2 helper names
    def _widget_to_image(self, wx, wy):
        p = self._to_img(wx, wy)
        return (int(p[0]), int(p[1])) if p else None

    def _image_to_widget(self, ix, iy) -> QPointF:
        return self._to_widget(ix, iy)

    def _rect_handles(self) -> List[Tuple[str, QPointF]]:
        if self._rect is None:
            return []
        x0, y0, x1, y1 = self._rect
        xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
        spec = [("tl", x0, y0), ("t", xm, y0), ("tr", x1, y0), ("r", x1, ym),
                ("br", x1, y1), ("b", xm, y1), ("bl", x0, y1), ("l", x0, ym)]
        return [(n, self._to_widget(x, y)) for n, x, y in spec]

    def _hit_handle(self, pos: QPointF):
        if self._mode == "rect":
            best, bd = None, _HANDLE_HIT + 1.0
            for name, hp in self._rect_handles():
                d = math.hypot(hp.x() - pos.x(), hp.y() - pos.y())
                if d < bd:
                    best, bd = name, d
            if best is None and self._rect is not None:
                # anywhere on a vertical edge resizes that edge
                x0, y0, x1, y1 = self._rect
                a, b = self._to_widget(x0, y0), self._to_widget(x1, y1)
                if a.y() - 4 <= pos.y() <= b.y() + 4:
                    if abs(pos.x() - a.x()) <= 5:
                        best = "l"
                    elif abs(pos.x() - b.x()) <= 5:
                        best = "r"
            return best
        for i, (x, y) in enumerate(self._pts):
            wp = self._to_widget(x, y)
            if math.hypot(wp.x() - pos.x(), wp.y() - pos.y()) <= _HANDLE_HIT + 3:
                return i
        return None

    def _inside_rect(self, pos: QPointF) -> bool:
        if self._rect is None:
            return False
        a = self._to_widget(self._rect[0], self._rect[1])
        b = self._to_widget(self._rect[2], self._rect[3])
        return a.x() <= pos.x() <= b.x() and a.y() <= pos.y() <= b.y()

    # ------------------------------------------------------------------ paint
    def paintEvent(self, event):
        if self._orig_pixmap is None:
            return
        tk = current_tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(tk.surface.bg))
        o = self._img_origin()
        pm = self._orig_pixmap
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self._zoom < 2.0)
        p.drawPixmap(QRectF(o.x(), o.y(), pm.width() * self._zoom, pm.height() * self._zoom),
                     pm, QRectF(0, 0, pm.width(), pm.height()))

        if self._mode == "rect":
            self._paint_rect(p)
        else:
            self._paint_line(p)

        p.setPen(QPen(QColor(tk.text.tertiary), 1))
        p.drawText(8, self.height() - 8,
                   f"Zoom {self._zoom * 100:.0f}%   |   Scroll to zoom   |   "
                   "Middle/right-drag to pan   |   Alt: no snapping")
        self._paint_loupe(p)
        p.end()

    @staticmethod
    def _tag(p: QPainter, x: float, y: float, text: str, color: QColor,
             check: bool = False) -> None:
        """Small dark label; ``check`` prefixes a drawn tick (no font glyph
        needed)."""
        f = QFont(p.font())
        f.setPixelSize(12)
        f.setBold(True)
        p.setFont(f)
        fm = p.fontMetrics()
        tick = 14 if check else 0
        r = QRectF(x, y - fm.height() - 4, fm.horizontalAdvance(text) + 12 + tick,
                   fm.height() + 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_C_SHADOW)
        p.drawRoundedRect(r, 4, 4)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(color, 2 if check else 1))
        if check:
            cy = r.center().y()
            p.drawPolyline([QPointF(r.x() + 6, cy), QPointF(r.x() + 9, cy + 3),
                            QPointF(r.x() + 15, cy - 4)])
            p.setPen(QPen(color, 1))
        p.drawText(r.adjusted(tick, 0, 0, 0), Qt.AlignmentFlag.AlignCenter, text)

    def _paint_rect(self, p: QPainter) -> None:
        if self._rect is None:
            return
        x0, y0, x1, y1 = self._rect
        a, b = self._to_widget(x0, y0), self._to_widget(x1, y1)
        r = QRectF(a, b)
        fill = QColor(_C_LINE)
        fill.setAlpha(28)
        p.fillRect(r, fill)
        p.setPen(QPen(QColor(255, 255, 255, 150), 1, Qt.PenStyle.DashLine))
        p.drawLine(a, QPointF(b.x(), a.y()))
        p.drawLine(QPointF(a.x(), b.y()), b)
        # the two vertical edges ARE the measurement
        for i, xw in enumerate((a.x(), b.x())):
            c = _C_SNAP if self._end_snapped[i] else _C_LINE
            width = 2.0 + 3.0 * self._pulse
            p.setPen(QPen(c, width))
            p.drawLine(QPointF(xw, a.y()), QPointF(xw, b.y()))
        # width dimension arrow
        ym = (a.y() + b.y()) / 2
        p.setPen(QPen(_C_LINE, 1, Qt.PenStyle.DotLine))
        p.drawLine(QPointF(a.x(), ym), QPointF(b.x(), ym))
        for name, hp in self._rect_handles():
            major = name in ("l", "r")
            s = 5.0 if major else 3.5
            p.setPen(QPen(QColor(20, 20, 20), 1))
            p.setBrush(_C_LINE if major else QColor(255, 255, 255))
            p.drawRect(QRectF(hp.x() - s, hp.y() - s, 2 * s, 2 * s))
        p.setBrush(Qt.BrushStyle.NoBrush)
        d = self.pixel_distance()
        if d is not None:
            self._tag(p, a.x(), a.y() - 4, f"width {d:.1f} px", _C_LINE)
            if self.is_snapped():
                self._tag(p, max(a.x(), b.x() - 90), b.y() + 24, "Snapped", _C_SNAP, check=True)

    def _paint_line(self, p: QPainter) -> None:
        pts = [self._to_widget(x, y) for x, y in self._pts]
        if len(pts) == 1 and self._hover is not None and self._drag is None:
            hx, hy = self._hover
            if self._mode == "level":
                hy = self._pts[0][1]
            h = self._to_widget(hx, hy)
            p.setPen(QPen(_C_LINE, 1.5, Qt.PenStyle.DashLine))
            p.drawLine(pts[0], h)
        if len(pts) == 2:
            p.setPen(QPen(_C_LINE, 2, Qt.PenStyle.SolidLine if self._mode == "level"
                          else Qt.PenStyle.DashLine))
            p.drawLine(pts[0], pts[1])
        colors = (_C_P1, _C_P2)
        for i, wp in enumerate(pts):
            c = _C_SNAP if self._end_snapped[i] else colors[i]
            p.setPen(QPen(c, 2 + 2 * self._pulse))
            if self._mode == "level":
                p.drawLine(QPointF(wp.x(), wp.y() - 12), QPointF(wp.x(), wp.y() + 12))
                p.setBrush(QColor(c.red(), c.green(), c.blue(), 90))
                p.drawEllipse(wp, 5, 5)
                p.setBrush(Qt.BrushStyle.NoBrush)
            else:
                r = 9
                p.drawEllipse(wp, r, r)
                p.drawLine(QPointF(wp.x() - r - 4, wp.y()), QPointF(wp.x() + r + 4, wp.y()))
                p.drawLine(QPointF(wp.x(), wp.y() - r - 4), QPointF(wp.x(), wp.y() + r + 4))
        d = self.pixel_distance()
        if d is not None and len(pts) == 2:
            # below the line: SEM labels usually sit above the bar
            mid = QPointF((pts[0].x() + pts[1].x()) / 2, max(pts[0].y(), pts[1].y()) + 34)
            text = f"{d:.1f} px"
            tilt = self.tilt_deg()
            color = _C_LINE
            if self._mode == "free" and tilt is not None:
                text += f"   tilt {tilt:.1f}°"
                if tilt > TILT_WARN_DEG:
                    color = _C_WARN
            self._tag(p, mid.x() - 40, mid.y(), text, color)
            if self.is_snapped():
                self._tag(p, pts[1].x() + 12, max(pts[0].y(), pts[1].y()) + 34,
                          "Snapped", _C_SNAP, check=True)

    def _loupe_active(self) -> bool:
        if self._hover is None or self._is_panning:
            return False
        if self._drag is not None:
            return self._drag["kind"] in ("end", "handle", "draw")
        return self._mode != "rect" and len(self._pts) < 2 and self.underMouse()

    def _paint_loupe(self, p: QPainter) -> None:
        if not self._loupe_active() or self._orig_pixmap is None:
            return
        ix, iy = self._hover
        scale = min(max(LOUPE_FACTOR * self._zoom, 3.0), 32.0)
        L = float(LOUPE_SIZE)
        span = L / scale
        cw = self._to_widget(ix, iy)
        lx, ly = cw.x() + 28, cw.y() + 28
        if lx + L > self.width() - 4:
            lx = cw.x() - 28 - L
        if ly + L > self.height() - 4:
            ly = cw.y() - 28 - L
        target = QRectF(lx, ly, L, L)
        src = QRectF(ix - span / 2, iy - span / 2, span, span)
        p.save()
        path = QPainterPath()
        path.addRoundedRect(target, 8, 8)
        p.setClipPath(path)
        p.fillRect(target, QColor(0, 0, 0))
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.drawPixmap(target, self._orig_pixmap, src)

        def to_l(x, y):
            return QPointF(lx + (x - src.x()) * scale, ly + (y - src.y()) * scale)

        # the measurement inside the loupe
        p.setPen(QPen(_C_LINE, 1.5))
        if self._mode == "rect" and self._rect is not None:
            x0, y0, x1, y1 = self._rect
            p.drawRect(QRectF(to_l(x0, y0), to_l(x1, y1)))
        elif len(self._pts) == 2:
            p.drawLine(to_l(*self._pts[0]), to_l(*self._pts[1]))
        c = QPointF(lx + L / 2, ly + L / 2)
        p.setPen(QPen(QColor(255, 255, 255, 220), 1))
        p.drawLine(QPointF(c.x(), ly), QPointF(c.x(), c.y() - 5))
        p.drawLine(QPointF(c.x(), c.y() + 5), QPointF(c.x(), ly + L))
        p.drawLine(QPointF(lx, c.y()), QPointF(c.x() - 5, c.y()))
        p.drawLine(QPointF(c.x() + 5, c.y()), QPointF(lx + L, c.y()))
        p.setClipping(False)
        p.setPen(QPen(QColor(current_tokens().border.focus), 2))
        p.drawRoundedRect(target, 8, 8)
        p.restore()
        self._tag(p, lx, ly + L + 20, f"x {ix:.0f}  y {iy:.0f}  ×{scale / self._zoom:.0f}",
                  QColor(255, 255, 255))

    # ------------------------------------------------------------ interaction
    def wheelEvent(self, event: QWheelEvent):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        pos = event.position()
        o = self._img_origin()
        ixb = (pos.x() - o.x()) / self._zoom
        iyb = (pos.y() - o.y()) / self._zoom
        self._zoom = max(0.05, min(self._zoom * factor, 40.0))
        if self._orig_pixmap:
            pw = self._orig_pixmap.width() * self._zoom
            ph = self._orig_pixmap.height() * self._zoom
            self._pan_offset = QPointF(pos.x() - ixb * self._zoom - (self.width() - pw) / 2,
                                       pos.y() - iyb * self._zoom - (self.height() - ph) / 2)
        self.update()

    def _no_snap(self, event) -> bool:
        return (not self._snap_enabled) or bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)

    def mousePressEvent(self, event):
        pos = event.position()
        btn = event.button()
        if btn in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            self._is_panning = True
            self._pan_start = pos - self._pan_offset
            self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            return
        if btn != Qt.MouseButton.LeftButton or self._orig_pixmap is None:
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        no_snap = self._no_snap(event)
        hit = self._hit_handle(pos)
        if self._mode == "rect":
            if hit is not None:
                self._drag = {"kind": "handle", "h": hit, "nosnap": no_snap}
            elif self._inside_rect(pos):
                self._drag = {"kind": "move", "start": self._to_img(pos.x(), pos.y(), True),
                              "orig": list(self._rect)}
                self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
            else:
                start = self._to_img(pos.x(), pos.y())
                if start is None:
                    return
                self._drag = {"kind": "draw", "start": start, "prev": self._geometry(),
                              "wstart": QPointF(pos), "nosnap": no_snap}
            self._hover = self._to_img(pos.x(), pos.y(), True)
            return
        # line modes
        if hit is not None:
            self._drag = {"kind": "end", "i": hit, "nosnap": no_snap}
            self._active_end = hit
            self._hover = tuple(self._pts[hit])
            self.update()
            return
        if len(self._pts) >= 2:
            return
        c = self._to_img(pos.x(), pos.y())
        if c is None:
            return
        x, y = c
        if self._mode == "level" and self._pts:
            y = self._pts[0][1]
        self._pts.append([x, y])
        i = len(self._pts) - 1
        self._end_snapped[i] = False
        if self._mode == "level" and not no_snap:
            self._snap_end(i)
        self._active_end = i
        self._drag = {"kind": "end", "i": i, "nosnap": no_snap}
        self._hover = tuple(self._pts[i])
        self._after_edit()

    def mouseMoveEvent(self, event):
        pos = event.position()
        if self._is_panning:
            self._pan_offset = pos - self._pan_start
            self.update()
            return
        cur = self._to_img(pos.x(), pos.y(), clamp=True)
        self._hover = cur
        d = self._drag
        if d is None:
            if self._mode == "rect":
                h = self._hit_handle(pos)
                shapes = {"l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor,
                          "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
                          "tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
                          "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor}
                if h in shapes:
                    self.setCursor(QCursor(shapes[h]))
                elif self._inside_rect(pos):
                    self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
                else:
                    self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
            elif self._hit_handle(pos) is not None:
                self.setCursor(QCursor(Qt.CursorShape.SizeHorCursor if self._mode == "level"
                                       else Qt.CursorShape.SizeAllCursor))
            else:
                self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
            self.update()
            return
        x, y = cur
        kind = d["kind"]
        if kind == "end":
            i = d["i"]
            if self._mode == "level":
                self._pts[i][0] = x                     # locked horizontal
                self._end_snapped[i] = False
                if not (d["nosnap"] or self._no_snap(event)):
                    yy = self._pts[i][1]
                    hit = snap_x(self._gray, x, yy, SNAP_TOL_PX) if self._gray is not None else None
                    if hit is not None:
                        self._pts[i][0] = hit[0]
                        self._end_snapped[i] = True
            else:
                self._pts[i] = [x, y]
                self._end_snapped[i] = False
            self._pre_snap = None
            self._hover = tuple(self._pts[i])
        elif kind == "draw":
            sx, sy = d["start"]
            if (pos - d["wstart"]).manhattanLength() < 3:
                return
            self._rect = [min(sx, x), min(sy, y), max(sx, x), max(sy, y)]
            self._end_snapped = [False, False]
            self._pre_snap = None
        elif kind == "move":
            sx, sy = d["start"]
            self._move_rect_to(d["orig"], x - sx, y - sy)
            self._end_snapped = [False, False]
            self._pre_snap = None
        elif kind == "handle":
            self._resize_rect(d["h"], x, y)
            self._pre_snap = None
        self._after_edit()

    def mouseReleaseEvent(self, event):
        if self._is_panning:
            self._is_panning = False
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
            return
        d, self._drag = self._drag, None
        if d is None:
            return
        no_snap = d.get("nosnap") or self._no_snap(event)
        if d["kind"] == "draw":
            if self._rect is None or (self._rect[2] - self._rect[0]) * self._zoom < 3:
                self._restore(d["prev"])       # a click, not a drag: keep the old box
            elif not no_snap:
                self.snap_rect()
        elif d["kind"] == "handle" and d["h"] not in ("t", "b") and not no_snap:
            self.snap_rect()
        elif d["kind"] == "end" and self._end_snapped[d["i"]]:
            self._flash()
        self.snap_changed.emit(self.is_snapped())
        self._after_edit()

    def leaveEvent(self, event):
        if self._drag is None:
            self._hover = None
            self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        key = event.key()
        steps = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0),
                 Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
        if key in steps:
            dx, dy = steps[key]
            n = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            if self.nudge(dx * n, dy * n):
                return
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal, Qt.Key.Key_Minus):
            self.set_zoom(self._zoom * (1.25 if key != Qt.Key.Key_Minus else 0.8))
            return
        super().keyPressEvent(event)

    def nudge(self, dx: float, dy: float) -> bool:
        """Arrow keys: move the box, or the last-touched line end (level line:
        left/right only)."""
        if self._mode == "rect":
            if self._rect is None:
                return False
            self._move_rect_to(list(self._rect), dx, dy)
        else:
            if not self._pts:
                return False
            i = min(self._active_end, len(self._pts) - 1)
            self._pts[i][0] += dx
            if self._mode == "free":
                self._pts[i][1] += dy
            self._end_snapped[i] = False
        was = self.is_snapped()
        if self._mode == "rect":
            self._end_snapped = [False, False]
        self._pre_snap = None
        if was:
            self.snap_changed.emit(False)
        self._after_edit()
        return True

    # ------------------------------------------------------------ rect edits
    def _move_rect_to(self, orig, dx, dy) -> None:
        W, H = self._orig_pixmap.width(), self._orig_pixmap.height()
        x0, y0, x1, y1 = orig
        dx = min(max(dx, -x0), W - x1)
        dy = min(max(dy, -y0), H - y1)
        self._rect = [x0 + dx, y0 + dy, x1 + dx, y1 + dy]

    def _resize_rect(self, h: str, x: float, y: float) -> None:
        x0, y0, x1, y1 = self._rect
        if "l" in h:
            x0 = min(x, x1 - 1)
            self._end_snapped[0] = False
        if "r" in h:
            x1 = max(x, x0 + 1)
            self._end_snapped[1] = False
        if "t" in h:
            y0 = min(y, y1 - 1)
        if "b" in h:
            y1 = max(y, y0 + 1)
        self._rect = [x0, y0, x1, y1]

    def _after_edit(self) -> None:
        self.update()
        self.point_placed.emit()


# v2 name kept for callers/tests
ZoomableCalibCanvas = CalibrationCanvas
