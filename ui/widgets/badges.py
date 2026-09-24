"""Badge (status pill) and Chip (checkable / closable filter pill)."""
from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ui.design import icons
from ui.design.theme import ui_font
from ui.design.tokens import MOTION, TYPE, TypeStyle
from ui.widgets._base import ThemeAware, animate_value, lerp_color, qcolor, stop, tokens

#: Canonical status words -> semantic kind.  Case-insensitive lookup.
STATUS_KINDS: Dict[str, str] = {
    "pass": "success", "passed": "success", "ok": "success", "approved": "success",
    "complete": "success", "completed": "success", "calibrated": "success",
    "fail": "danger", "failed": "danger", "error": "danger", "rejected": "danger",
    "inconclusive": "warning", "warning": "warning", "uncalibrated": "warning",
    "needs review": "warning", "in review": "info", "running": "info", "analyzing": "info",
    "draft": "neutral", "pending": "neutral", "archived": "neutral",
}

_BADGE_FONT = TypeStyle(11, 600, 16, 2.0)


class Badge(ThemeAware, QWidget):
    """Compact semantic pill: kind in neutral/accent/success/warning/danger/info."""

    def __init__(self, text: str, kind: str = "neutral", icon: Optional[str] = None,
                 dot: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._text, self._kind, self._icon, self._dot = text, kind, icon, dot
        self._height = 20
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setAccessibleName(text)
        self._connect_theme()

    @classmethod
    def for_status(cls, status: str, parent: Optional[QWidget] = None) -> "Badge":
        """Badge whose kind is inferred from a status word (PASS, FAIL, Draft, ...)."""
        return cls(status, STATUS_KINDS.get(status.strip().lower(), "neutral"), dot=True,
                   parent=parent)

    def text(self) -> str:
        """Badge text."""
        return self._text

    def kind(self) -> str:
        """Semantic kind."""
        return self._kind

    def set_text(self, text: str) -> None:
        """Change text (resizes)."""
        self._text = text
        self.setAccessibleName(text)
        self.updateGeometry()
        self.update()

    def set_kind(self, kind: str) -> None:
        """Change semantic kind."""
        self._kind = kind
        self.update()

    def _font(self):
        return ui_font(_BADGE_FONT)

    def _lead(self) -> int:
        if self._icon:
            return 14 + 4
        if self._dot:
            return 6 + 6
        return 0

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(self._font())
        return QSize(fm.horizontalAdvance(self._text) + 16 + self._lead() + self._extra(),
                     self._height)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def _extra(self) -> int:
        return 0

    def _colors(self):
        sem = tokens().semantic(self._kind)
        return qcolor(sem.bg), qcolor(sem.border), qcolor(sem.fg)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bg, border, fg = self._colors()
        h = self._height
        r = QRectF(0.5, (self.height() - h) / 2 + 0.5, self.width() - 1, h - 1)
        p.setPen(QPen(border, 1))
        p.setBrush(bg)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        x = r.left() + 8
        if self._icon:
            ir = QRectF(x, r.center().y() - 7, 14, 14)
            icons.icon(self._icon, fg.name()).paint(p, ir.toRect())
            x += 18
        elif self._dot:
            p.setPen(Qt.NoPen)
            p.setBrush(fg)
            p.drawEllipse(QRectF(x, r.center().y() - 3, 6, 6))
            x += 12
        p.setFont(self._font())
        p.setPen(fg)
        p.drawText(QRectF(x, r.top(), r.right() - x, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                   self._text)
        self._paint_extra(p, r, fg)

    def _paint_extra(self, p, r, fg) -> None:
        pass


class Chip(Badge):
    """Interactive pill: optionally checkable (filter) and/or closable (tag)."""

    toggled = Signal(bool)
    closed = Signal()

    def __init__(self, text: str, checkable: bool = False, closable: bool = False,
                 checked: bool = False, icon: Optional[str] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(text, "neutral", icon, False, parent)
        self._checkable, self._closable = checkable, closable
        self._checked = checked and checkable
        self._hover = 0.0
        self._anim = None
        self._height = 26
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus if (checkable or closable) else Qt.NoFocus)
        self.setAttribute(Qt.WA_Hover)

    def is_checked(self) -> bool:
        """Checked state (checkable chips only)."""
        return self._checked

    def set_checked(self, on: bool) -> None:
        """Set checked state and emit ``toggled`` on change."""
        on = bool(on) and self._checkable
        if on != self._checked:
            self._checked = on
            self.update()
            self.toggled.emit(on)

    def _font(self):
        return ui_font(TypeStyle(12, 500, 16))

    def _extra(self) -> int:
        return 18 if self._closable else 0

    def _close_rect(self) -> QRectF:
        return QRectF(self.width() - 22, (self.height() - 14) / 2, 14, 14)

    def _colors(self):
        t = tokens()
        if self._checked:
            return qcolor(t.accent.subtle), qcolor(t.accent.subtle_border), qcolor(t.accent.text)
        bg = lerp_color(qcolor(t.surface.surface2), qcolor(t.surface.surface3), self._hover)
        return bg, qcolor(t.border.strong), qcolor(t.text.primary if self._hover else t.text.secondary)

    def _paint_extra(self, p, r, fg) -> None:
        if self._closable:
            icons.icon("close", fg.name()).paint(p, self._close_rect().toRect())
        if self.hasFocus():
            p.setPen(QPen(qcolor(tokens().border.focus), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), r.height() / 2 - 1, r.height() / 2 - 1)

    def _set_hover(self, v) -> None:
        self._hover = float(v)
        self.update()

    def enterEvent(self, e) -> None:
        stop(self._anim)
        self._anim = animate_value(self, self._hover, 1.0, MOTION.fast, self._set_hover)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        stop(self._anim)
        self._anim = animate_value(self, self._hover, 0.0, MOTION.base, self._set_hover)
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            if self._closable and self._close_rect().adjusted(-3, -3, 3, 3).contains(e.position()):
                self.closed.emit()
            elif self._checkable:
                self.set_checked(not self._checked)
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter) and self._checkable:
            self.set_checked(not self._checked)
        elif e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self._closable:
            self.closed.emit()
        else:
            super().keyPressEvent(e)
