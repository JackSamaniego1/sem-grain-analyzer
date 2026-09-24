"""SegmentedControl: mutually-exclusive options with an animated sliding indicator."""
from __future__ import annotations

from typing import List, Optional, Sequence

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ui.design.theme import ui_font
from ui.design.tokens import MOTION, RADII, SIZES, SPACE, TYPE, TypeStyle
from ui.widgets._base import ThemeAware, animate_value, qcolor, stop, tokens

_FONT = TypeStyle(12, 600, 16)


class SegmentedControl(ThemeAware, QWidget):
    """Row of equal-width segments; emits ``current_changed(int)`` and ``current_text_changed(str)``."""

    current_changed = Signal(int)
    current_text_changed = Signal(str)

    def __init__(self, options: Sequence[str], current: int = 0,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._options: List[str] = list(options)
        self._current = max(0, min(current, len(self._options) - 1))
        self._pos = float(self._current)
        self._hover_idx = -1
        self._anim = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(SIZES.control_md)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setAccessibleName("Segmented control")
        self._connect_theme()

    # -- API ----------------------------------------------------------------
    def options(self) -> List[str]:
        """Option labels."""
        return list(self._options)

    def current_index(self) -> int:
        """Selected index."""
        return self._current

    def current_text(self) -> str:
        """Selected label."""
        return self._options[self._current] if self._options else ""

    def indicator_position(self) -> float:
        """Painted indicator position in segment units (animates between indices)."""
        return self._pos

    def set_current_index(self, index: int, animate: bool = True) -> None:
        """Select a segment, sliding the indicator; emits signals on change."""
        if not self._options:
            return
        index = max(0, min(index, len(self._options) - 1))
        if index == self._current:
            return
        self._current = index
        stop(self._anim)
        self._anim = animate_value(self, self._pos, float(index),
                                   MOTION.base if animate else 0, self._set_pos)
        self.current_changed.emit(index)
        self.current_text_changed.emit(self._options[index])

    def _set_pos(self, v) -> None:
        self._pos = float(v)
        self.update()

    # -- geometry -----------------------------------------------------------
    def _seg_w(self) -> float:
        n = max(1, len(self._options))
        return (self.width() - 4) / n

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(ui_font(_FONT))
        w = max((fm.horizontalAdvance(o) for o in self._options), default=40) + SPACE.lg * 2
        return QSize(int(w * len(self._options) + 4), SIZES.control_md)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def _index_at(self, x: float) -> int:
        return int(max(0, min(len(self._options) - 1, (x - 2) // self._seg_w())))

    # -- events -------------------------------------------------------------
    def mouseMoveEvent(self, e) -> None:
        idx = self._index_at(e.position().x())
        if idx != self._hover_idx:
            self._hover_idx = idx
            self.update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e) -> None:
        self._hover_idx = -1
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self.set_current_index(self._index_at(e.position().x()))
        super().mousePressEvent(e)

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key_Left, Qt.Key_Up):
            self.set_current_index(self._current - 1)
        elif e.key() in (Qt.Key_Right, Qt.Key_Down):
            self.set_current_index(self._current + 1)
        elif e.key() == Qt.Key_Home:
            self.set_current_index(0)
        elif e.key() == Qt.Key_End:
            self.set_current_index(len(self._options) - 1)
        else:
            super().keyPressEvent(e)

    def focusInEvent(self, e) -> None:
        self.update()
        super().focusInEvent(e)

    def focusOutEvent(self, e) -> None:
        self.update()
        super().focusOutEvent(e)

    # -- paint --------------------------------------------------------------
    def paintEvent(self, _e) -> None:
        t = tokens()
        dark = t.mode == "dark"
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(qcolor(t.border.subtle), 1))
        p.setBrush(qcolor(t.surface.surface2 if dark else t.surface.surface3))
        p.drawRoundedRect(track, RADII.md, RADII.md)

        sw = self._seg_w()
        ind = QRectF(2 + self._pos * sw, 2, sw, self.height() - 4)
        p.setPen(QPen(qcolor(t.border.strong if dark else t.border.subtle), 1))
        p.setBrush(qcolor(t.surface.surface3 if dark else t.surface.surface1))
        p.drawRoundedRect(ind.adjusted(0.5, 0.5, -0.5, -0.5), RADII.md - 1, RADII.md - 1)
        if self.hasFocus():
            p.setPen(QPen(qcolor(t.border.focus), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(ind.adjusted(1, 1, -1, -1), RADII.md - 2, RADII.md - 2)

        p.setFont(ui_font(_FONT))
        for i, text in enumerate(self._options):
            r = QRectF(2 + i * sw, 0, sw, self.height())
            closeness = max(0.0, 1.0 - abs(self._pos - i))
            if closeness > 0.5 or i == self._hover_idx:
                col = t.text.primary
            else:
                col = t.text.secondary
            p.setPen(qcolor(col))
            fm = p.fontMetrics()
            p.drawText(r, Qt.AlignCenter, fm.elidedText(text, Qt.ElideRight, int(sw - 8)))
