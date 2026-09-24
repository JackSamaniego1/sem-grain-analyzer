"""Loading indicators: Spinner, ProgressRing, Skeleton (shimmer)."""
from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QAbstractAnimation, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ui.design.theme import reduced_motion, ui_font
from ui.design.tokens import MOTION, RADII, TYPE, mix
from ui.widgets._base import ThemeAware, animate_value, qcolor, stop, tokens


def paint_spinner(p: QPainter, rect: QRectF, phase: float, color: QColor,
                  width: float = 2.0) -> None:
    """Draw one frame of the indeterminate arc (phase 0..1) inside ``rect``."""
    pen = QPen(color, width)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    r = rect.adjusted(width / 2, width / 2, -width / 2, -width / 2)
    if reduced_motion():
        span = 90.0
    else:
        span = 40.0 + 200.0 * math.sin(math.pi * phase) ** 2
    start = -phase * 720.0 - span / 2
    p.drawArc(r, int(start * 16), int(span * 16))


def _loop(owner, ms: int, on_value) -> QVariantAnimation:
    anim = QVariantAnimation(owner)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setDuration(ms)
    anim.setLoopCount(-1)
    anim.valueChanged.connect(on_value)
    return anim


class Spinner(ThemeAware, QWidget):
    """Smooth indeterminate arc spinner; runs while visible."""

    def __init__(self, size: int = 20, line_width: float = 2.0, color: Optional[str] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._size, self._lw, self._color = size, line_width, color
        self._phase = 0.0
        self._anim = _loop(self, int(MOTION.spin * 1.3), self._set_phase)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._connect_theme()

    def _set_phase(self, v) -> None:
        self._phase = float(v)
        self.update()

    def is_running(self) -> bool:
        """True while the loop animation runs."""
        return self._anim.state() == QAbstractAnimation.Running

    def showEvent(self, e) -> None:
        self._anim.start()
        super().showEvent(e)

    def hideEvent(self, e) -> None:
        self._anim.stop()
        super().hideEvent(e)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        col = qcolor(self._color or tokens().accent.text)
        paint_spinner(p, QRectF(self.rect()), self._phase, col, self._lw)


class ProgressRing(ThemeAware, QWidget):
    """Determinate circular progress (0-100) with centred label and caption."""

    def __init__(self, size: int = 72, thickness: float = 6.0,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._thick = thickness
        self._value = 0.0
        self._target = 0.0
        self._label: Optional[str] = None
        self._caption = ""
        self._tone = "accent"
        self._anim = None
        self.setFixedSize(size, size)
        self._connect_theme()

    def value(self) -> float:
        """Target value (0-100)."""
        return self._target

    def displayed_value(self) -> float:
        """Currently painted (possibly mid-animation) value."""
        return self._value

    def set_value(self, value: float, animate: bool = True) -> None:
        """Set progress 0-100, tweening from the current value."""
        value = max(0.0, min(100.0, float(value)))
        self._target = value
        stop(self._anim)
        self._anim = animate_value(self, self._value, value,
                                   MOTION.slow if animate else 0, self._set_display)

    def _set_display(self, v) -> None:
        self._value = float(v)
        self.update()

    def set_label(self, text: Optional[str]) -> None:
        """Override the centre text (None -> percentage)."""
        self._label = text
        self.update()

    def set_caption(self, text: str) -> None:
        """Small caption under the centre value."""
        self._caption = text
        self.update()

    def set_tone(self, tone: str) -> None:
        """Arc colour: accent/success/warning/danger/info."""
        self._tone = tone
        self.update()

    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self._thick
        r = QRectF(self.rect()).adjusted(w / 2 + 1, w / 2 + 1, -w / 2 - 1, -w / 2 - 1)
        track = QPen(qcolor(t.surface.surface3), w)
        p.setPen(track)
        p.drawEllipse(r)
        arc_col = t.accent.base if self._tone == "accent" else t.semantic(self._tone).solid
        pen = QPen(qcolor(arc_col), w)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        span = -self._value / 100.0 * 360.0
        if abs(span) > 0.1:
            p.drawArc(r, 90 * 16, int(span * 16))
        text = self._label if self._label is not None else f"{self._value:.0f}%"
        f = ui_font(TYPE.h3 if self.width() < 80 else TYPE.h2)
        p.setFont(f)
        p.setPen(qcolor(t.text.primary))
        if self._caption:
            top = QRectF(self.rect()).adjusted(0, 0, 0, -self.height() * 0.18)
            p.drawText(top, Qt.AlignCenter, text)
            p.setFont(ui_font(TYPE.caption))
            p.setPen(qcolor(t.text.secondary))
            bot = QRectF(self.rect()).adjusted(0, self.height() * 0.30, 0, 0)
            p.drawText(bot, Qt.AlignCenter, self._caption)
        else:
            p.drawText(QRectF(self.rect()), Qt.AlignCenter, text)


class Skeleton(ThemeAware, QWidget):
    """Shimmering placeholder block; shape is 'rect', 'text' or 'circle'."""

    def __init__(self, width: Optional[int] = None, height: Optional[int] = None,
                 shape: str = "rect", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._shape = shape
        self._phase = 0.0
        self._anim = _loop(self, MOTION.shimmer, self._set_phase)
        default_h = 12 if shape == "text" else 40
        h = height or default_h
        if shape == "circle":
            self.setFixedSize(width or h, width or h)
        else:
            self.setFixedHeight(h)
            if width:
                self.setFixedWidth(width)
            else:
                self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._connect_theme()

    def sizeHint(self) -> QSize:
        return QSize(160, self.height())

    def _set_phase(self, v) -> None:
        self._phase = float(v)
        self.update()

    def showEvent(self, e) -> None:
        if not reduced_motion():
            self._anim.start()
        super().showEvent(e)

    def hideEvent(self, e) -> None:
        self._anim.stop()
        super().hideEvent(e)

    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        path = QPainterPath()
        if self._shape == "circle":
            path.addEllipse(rect)
        else:
            rad = RADII.sm if self._shape == "text" else RADII.md
            path.addRoundedRect(rect, rad, rad)
        base = t.surface.surface3 if t.mode == "dark" else t.surface.surface3
        p.fillPath(path, qcolor(base))
        if reduced_motion():
            return
        hi = qcolor(mix(base, t.text.tertiary if t.mode == "dark" else "#FFFFFF", 0.22))
        w = max(rect.width(), 160.0)
        band = w * 0.45
        x = -band + (w + band * 2) * self._phase
        g = QLinearGradient(x, 0, x + band, 0)
        transparent = QColor(hi)
        transparent.setAlphaF(0.0)
        g.setColorAt(0.0, transparent)
        g.setColorAt(0.5, hi)
        g.setColorAt(1.0, transparent)
        p.fillPath(path, g)
