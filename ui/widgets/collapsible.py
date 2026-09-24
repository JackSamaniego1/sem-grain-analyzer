"""CollapsibleSection: header with rotating chevron over an animated-height content area."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLayout, QSizePolicy, QVBoxLayout, QWidget

from ui.design.theme import ui_font
from ui.design.tokens import MOTION, RADII, SPACE, TYPE
from ui.widgets._base import ThemeAware, animate_value, lerp_color, qcolor, stop, tokens

QWIDGETSIZE_MAX = 16777215


class _Header(ThemeAware, QWidget):
    """Clickable section header (internal)."""

    activated = Signal()

    def __init__(self, title: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.title = title
        self.angle = 90.0
        self.hover = 0.0
        self._anim = None
        self.setFixedHeight(36)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_Hover)
        self.trailing = QHBoxLayout(self)
        self.trailing.setContentsMargins(0, 0, SPACE.sm, 0)
        self.trailing.addStretch(1)
        self._connect_theme()

    def _set_hover(self, v) -> None:
        self.hover = float(v)
        self.update()

    def enterEvent(self, e) -> None:
        stop(self._anim)
        self._anim = animate_value(self, self.hover, 1.0, MOTION.fast, self._set_hover)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        stop(self._anim)
        self._anim = animate_value(self, self.hover, 0.0, MOTION.base, self._set_hover)
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.activated.emit()
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self.activated.emit()
        else:
            super().keyPressEvent(e)

    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        bg = lerp_color(qcolor(t.surface.surface2, 0.0), qcolor(t.surface.surface2), self.hover)
        p.setPen(QPen(qcolor(t.border.focus), 1.5) if self.hasFocus() else Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, RADII.md, RADII.md)
        # chevron (points right at 0 deg, down at 90 deg)
        c = QPointF(SPACE.md + 6, r.center().y())
        p.save()
        p.translate(c)
        p.rotate(self.angle)
        path = QPainterPath(QPointF(-2.5, -5))
        path.lineTo(QPointF(2.5, 0))
        path.lineTo(QPointF(-2.5, 5))
        pen = QPen(qcolor(t.text.secondary), 1.6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.restore()
        p.setFont(ui_font(TYPE.body_strong))
        p.setPen(qcolor(t.text.primary))
        p.drawText(QRectF(SPACE.md + 20, 0, r.width() - 60, r.height()),
                   Qt.AlignVCenter | Qt.AlignLeft, self.title)


class CollapsibleSection(QWidget):
    """Titled section whose body expands/collapses with animated height and chevron rotation."""

    toggled = Signal(bool)

    def __init__(self, title: str, expanded: bool = True,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._expanded = expanded
        self._anims = []
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(SPACE.xs)
        self._header = _Header(title, self)
        self._header.angle = 90.0 if expanded else 0.0
        self._header.activated.connect(self.toggle)
        root.addWidget(self._header)
        self._content = QWidget(self)
        self._content.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(SPACE.md + 20, SPACE.xs, SPACE.sm, SPACE.sm)
        self._content_layout.setSpacing(SPACE.sm)
        root.addWidget(self._content)
        if not expanded:
            self._content.setMaximumHeight(0)
            self._content.setVisible(False)
        self._header.setAccessibleName(f"{title} section")

    # -- API ----------------------------------------------------------------
    def content_layout(self) -> QVBoxLayout:
        """Layout for section content."""
        return self._content_layout

    def content_widget(self) -> QWidget:
        """The animated content container."""
        return self._content

    def add_widget(self, w: QWidget) -> QWidget:
        """Append a widget to the content area."""
        self._content_layout.addWidget(w)
        return w

    def header_trailing(self) -> QHBoxLayout:
        """Layout at the right edge of the header (for a Badge/count/IconButton)."""
        return self._header.trailing

    def is_expanded(self) -> bool:
        """Current expanded state."""
        return self._expanded

    def toggle(self) -> None:
        """Flip expanded state."""
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded: bool, animate: bool = True) -> None:
        """Expand/collapse with height + chevron animation; emits ``toggled``."""
        expanded = bool(expanded)
        if expanded == self._expanded:
            return
        self._expanded = expanded
        for a in self._anims:
            stop(a)
        target_h = self._content.sizeHint().height()
        start_h = self._content.height() if self._content.isVisible() else 0
        ms = MOTION.slow if animate else 0
        if expanded:
            self._content.setMaximumHeight(start_h)
            self._content.setVisible(True)

        def set_h(v):
            self._content.setMaximumHeight(int(v))

        def done():
            if self._expanded:
                self._content.setMaximumHeight(QWIDGETSIZE_MAX)
            else:
                self._content.setVisible(False)

        def set_angle(v):
            self._header.angle = float(v)
            self._header.update()

        self._anims = [
            animate_value(self, start_h, target_h if expanded else 0, ms, set_h, done,
                          curve=MOTION.ease_in_out),
            animate_value(self, self._header.angle, 90.0 if expanded else 0.0, ms, set_angle),
        ]
        self.toggled.emit(expanded)

    def chevron_angle(self) -> float:
        """Current chevron rotation in degrees (0 collapsed, 90 expanded)."""
        return self._header.angle
