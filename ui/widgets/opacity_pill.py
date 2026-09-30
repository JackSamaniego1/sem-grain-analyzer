"""
OpacityPill (UPDATE 4 item 9) - compact overlay-opacity slider that floats
in the top-right corner of the image canvas.

A semi-transparent rounded pill (same surface as the canvas HUD pills) with
an icon, a slider (0-100 %) and a live percentage readout.

* ``value_changed(float)`` fires live while dragging / stepping (0..1).
* ``value_committed(float)`` fires when the value settles: on slider release,
  or shortly after the last keyboard step (so settings are not written to
  disk on every key repeat).
* Keyboard: Tab reaches the slider; Left/Right step 5 %, PageUp/PageDown
  10 %, Home/End 0 / 100 %.
* The mouse wheel is passed through to the canvas (zooming the image never
  changes the opacity by accident).
* ``set_shown(bool)`` fades the pill in/out (150 ms, instant under reduced
  motion).
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect, QHBoxLayout, QLabel, QSlider, QWidget,
)

from ui.design import icons
from ui.design.tokens import MOTION, SPACE
from ui.widgets._base import ThemeAware, animate_value, qcolor, stop, tokens
from ui.widgets.cards import label

PILL_H = 32
SLIDER_W = 104
COMMIT_DELAY_MS = 400


class _Slider(QSlider):
    def wheelEvent(self, e) -> None:          # let the canvas zoom instead
        e.ignore()


class OpacityPill(ThemeAware, QWidget):
    value_changed = Signal(float)       # 0..1, live
    value_committed = Signal(float)     # 0..1, settled

    def __init__(self, parent: Optional[QWidget] = None, title: str = "Overlay opacity") -> None:
        super().__init__(parent)
        self.setObjectName("opacityPill")
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setFixedHeight(PILL_H)
        self._shown = False
        self._anim = None
        self._fx = QGraphicsOpacityEffect(self)
        self._fx.setOpacity(0.0)
        self.setGraphicsEffect(self._fx)
        tip = ("Overlay opacity: how strongly the detected grains are drawn over the image "
               "(0 % = plain image). Left/Right arrows change it in 5 % steps. "
               "Report images start from the same setting.")
        self.setToolTip(tip)

        h = QHBoxLayout(self)
        h.setContentsMargins(SPACE.md, 0, SPACE.md, 0)
        h.setSpacing(SPACE.sm)
        self.icon = QLabel()
        self.icon.setToolTip(tip)
        h.addWidget(self.icon)
        self.slider = _Slider(Qt.Horizontal)
        self.slider.setObjectName("opacityPillSlider")
        self.slider.setRange(0, 100)
        self.slider.setSingleStep(5)
        self.slider.setPageStep(10)
        self.slider.setValue(100)
        self.slider.setFixedWidth(SLIDER_W)
        self.slider.setFocusPolicy(Qt.StrongFocus)
        self.slider.setToolTip(tip)
        self.slider.setAccessibleName(title)
        self.slider.setAccessibleDescription("Opacity of the grain overlay, in percent")
        h.addWidget(self.slider)
        self.readout = label("100 %", "caption", "secondary")
        self.readout.setObjectName("opacityPillValue")
        self.readout.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.readout.setToolTip(tip)
        h.addWidget(self.readout)

        self._commit = QTimer(self)
        self._commit.setSingleShot(True)
        self._commit.setInterval(COMMIT_DELAY_MS)
        self._commit.timeout.connect(self._emit_commit)
        self.slider.valueChanged.connect(self._on_value)
        self.slider.sliderReleased.connect(self._emit_commit)
        self.slider.sliderPressed.connect(self._commit.stop)     # a drag commits on release
        self._style()
        self.hide()
        self._connect_theme()

    # ------------------------------------------------------------------ API
    def value(self) -> float:
        return self.slider.value() / 100.0

    def set_value(self, v: float) -> None:
        """Programmatic update (no signals)."""
        iv = int(round(max(0.0, min(1.0, float(v))) * 100))
        if iv != self.slider.value():
            self.slider.blockSignals(True)
            self.slider.setValue(iv)
            self.slider.blockSignals(False)
        self.readout.setText(f"{iv} %")

    def is_shown(self) -> bool:
        """The state the pill is shown/fading towards."""
        return self._shown

    def set_shown(self, on: bool) -> None:
        on = bool(on)
        if on == self._shown:
            return
        self._shown = on
        stop(self._anim)
        self._set_click_through(not on)      # fading out: clicks go to the image
        if on:
            self.show()
            self.raise_()
            self._anim = animate_value(self, self._fx.opacity(), 1.0, MOTION.fast,
                                       self._fx.setOpacity)
        else:
            self._anim = animate_value(self, self._fx.opacity(), 0.0, MOTION.fast,
                                       self._fx.setOpacity, self._hide_if_off)

    def sizeHint(self) -> QSize:
        return QSize(self._width(), PILL_H)

    # ------------------------------------------------------------------ mouse
    # Clicks on the pill's padding / icon / readout must never reach the
    # canvas underneath (it would clear the grain selection, start a lasso or
    # cut stroke, or pan).  Only the wheel is passed on (zoom).
    def mousePressEvent(self, e) -> None:
        e.accept()

    def mouseMoveEvent(self, e) -> None:
        e.accept()

    def mouseReleaseEvent(self, e) -> None:
        e.accept()

    def mouseDoubleClickEvent(self, e) -> None:
        e.accept()

    def wheelEvent(self, e) -> None:
        e.ignore()

    def _set_click_through(self, on: bool) -> None:
        for w in (self, self.icon, self.slider, self.readout):
            w.setAttribute(Qt.WA_TransparentForMouseEvents, on)

    def is_click_through(self) -> bool:
        return self.testAttribute(Qt.WA_TransparentForMouseEvents)

    # ------------------------------------------------------------------ internals
    def _hide_if_off(self) -> None:
        if not self._shown:
            self.hide()

    def _width(self) -> int:
        self.readout.ensurePolished()           # QSS "caption" font applied
        fm = QFontMetrics(self.readout.font())
        self.readout.setFixedWidth(fm.horizontalAdvance("100 %") + 2)
        return SPACE.md * 2 + 16 + SLIDER_W + self.readout.width() + SPACE.sm * 2

    def _on_value(self, iv: int) -> None:
        self.readout.setText(f"{iv} %")
        self.value_changed.emit(iv / 100.0)
        if not self.slider.isSliderDown():
            self._commit.start()

    def _emit_commit(self) -> None:
        self._commit.stop()
        self.value_committed.emit(self.value())

    def _style(self) -> None:
        t = tokens()
        self.icon.setPixmap(icons.pixmap("mdi6.circle-opacity", 16, t.text.secondary))
        self.setFixedWidth(self._width())

    def _on_theme_changed(self) -> None:
        self._style()
        self.update()

    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(qcolor(t.border.strong), 1))
        p.setBrush(qcolor(t.surface.elevated, 0.92))
        rad = r.height() / 2
        p.drawRoundedRect(r, rad, rad)
        p.end()


__all__ = ["OpacityPill"]
