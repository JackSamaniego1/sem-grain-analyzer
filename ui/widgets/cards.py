"""Card (elevated surface with optional header/actions), StatCard (KPI tile) and Sparkline."""
from __future__ import annotations

from typing import Optional, Sequence

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from ui.design.tokens import ELEVATION, MOTION, RADII, SPACE
from ui.design.theme import current_mode
from ui.widgets._base import ThemeAware, animate_value, lerp_color, qcolor, stop, tokens
from ui.widgets.badges import Badge


def label(text: str = "", role: Optional[str] = None, tone: Optional[str] = None,
          parent: Optional[QWidget] = None) -> QLabel:
    """QLabel with a typography ``role`` and colour ``tone`` (styled by the theme QSS)."""
    lab = QLabel(text, parent)
    if role:
        lab.setProperty("role", role)
    if tone:
        lab.setProperty("tone", tone)
    return lab


class Card(ThemeAware, QFrame):
    """Rounded surface with elevation shadow, optional title/subtitle/actions and hover lift."""

    clicked = Signal()

    def __init__(self, title: Optional[str] = None, subtitle: Optional[str] = None,
                 elevation: int = 1, hoverable: bool = False,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._elevation = elevation
        self._hoverable = hoverable
        self._lift = 0.0
        self._anim = None
        self.setAttribute(Qt.WA_Hover, hoverable)
        if hoverable:
            self.setCursor(Qt.PointingHandCursor)
        self._shadow = QGraphicsDropShadowEffect(self)
        self.setGraphicsEffect(self._shadow)

        root = QVBoxLayout(self)
        root.setContentsMargins(SPACE.lg + 2, SPACE.lg, SPACE.lg + 2, SPACE.lg)
        root.setSpacing(SPACE.md)
        self._header = QWidget(self)
        hl = QHBoxLayout(self._header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(SPACE.sm)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self._title = label(title or "", "h3", parent=self._header)
        self._subtitle = label(subtitle or "", "caption", parent=self._header)
        self._subtitle.setVisible(bool(subtitle))
        titles.addWidget(self._title)
        titles.addWidget(self._subtitle)
        hl.addLayout(titles, 1)
        self._actions = QHBoxLayout()
        self._actions.setSpacing(SPACE.xs)
        hl.addLayout(self._actions)
        self._header.setVisible(bool(title))
        root.addWidget(self._header)
        self._body = QVBoxLayout()
        self._body.setSpacing(SPACE.md)
        root.addLayout(self._body, 1)
        self._apply_shadow()
        self._connect_theme()

    # -- API ----------------------------------------------------------------
    def body_layout(self) -> QVBoxLayout:
        """Layout that receives the card content."""
        return self._body

    def add_widget(self, w: QWidget, stretch: int = 0) -> QWidget:
        """Append a widget to the card body."""
        self._body.addWidget(w, stretch)
        return w

    def add_action(self, w: QWidget) -> QWidget:
        """Append a widget (usually IconButton) to the header's action area."""
        self._actions.addWidget(w)
        self._header.setVisible(True)
        return w

    def set_title(self, title: str, subtitle: Optional[str] = None) -> None:
        """Set header title (and optional subtitle)."""
        self._title.setText(title)
        self._header.setVisible(bool(title) or self._actions.count() > 0)
        if subtitle is not None:
            self._subtitle.setText(subtitle)
            self._subtitle.setVisible(bool(subtitle))

    def set_elevation(self, level: int) -> None:
        """Change resting elevation 0..3."""
        self._elevation = level
        self._apply_shadow()

    # -- internals ----------------------------------------------------------
    def _apply_shadow(self) -> None:
        mode = current_mode()
        a = ELEVATION.level(self._elevation)
        b = ELEVATION.level(self._elevation + 1)
        k = self._lift
        blur = a.blur + (b.blur - a.blur) * k
        y = a.y + (b.y - a.y) * k
        alpha = a.alpha(mode) + (b.alpha(mode) - a.alpha(mode)) * k
        self._shadow.setEnabled(blur > 0)
        self._shadow.setBlurRadius(blur)
        self._shadow.setOffset(0, y)
        self._shadow.setColor(QColor(0, 0, 0, int(alpha * 255)))

    def _set_lift(self, v) -> None:
        self._lift = float(v)
        self._apply_shadow()
        self.update()

    def _on_theme_changed(self) -> None:
        self._apply_shadow()
        self.update()

    def enterEvent(self, e) -> None:
        if self._hoverable:
            stop(self._anim)
            self._anim = animate_value(self, self._lift, 1.0, MOTION.base, self._set_lift)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        if self._hoverable:
            stop(self._anim)
            self._anim = animate_value(self, self._lift, 0.0, MOTION.slow, self._set_lift)
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if self._hoverable and e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        bg = qcolor(t.surface.surface1)
        border = lerp_color(qcolor(t.border.subtle), qcolor(t.border.strong), self._lift)
        p.setPen(QPen(border, 1))
        p.setBrush(bg)
        p.drawRoundedRect(r, RADII.lg, RADII.lg)


class Sparkline(ThemeAware, QWidget):
    """Tiny trend line with soft area fill; colour defaults to accent."""

    def __init__(self, values: Sequence[float] = (), color: Optional[str] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._values = list(values)
        self._color = color
        self.setMinimumHeight(28)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(32)
        self._connect_theme()

    def set_values(self, values: Sequence[float]) -> None:
        """Replace the series."""
        self._values = list(values)
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(120, 32)

    def paintEvent(self, _e) -> None:
        v = self._values
        if len(v) < 2:
            return
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 4, -4, -2)
        lo, hi = min(v), max(v)
        span = (hi - lo) or 1.0
        pts = [QPointF(r.left() + r.width() * i / (len(v) - 1),
                       r.bottom() - (x - lo) / span * r.height()) for i, x in enumerate(v)]
        col = qcolor(self._color or t.accent.text)
        area = QPainterPath(QPointF(pts[0].x(), r.bottom()))
        for pt in pts:
            area.lineTo(pt)
        area.lineTo(QPointF(pts[-1].x(), r.bottom()))
        area.closeSubpath()
        g = QLinearGradient(0, r.top(), 0, r.bottom())
        c0 = QColor(col); c0.setAlphaF(0.28)
        c1 = QColor(col); c1.setAlphaF(0.0)
        g.setColorAt(0, c0)
        g.setColorAt(1, c1)
        p.fillPath(area, g)
        line = QPainterPath(pts[0])
        for pt in pts[1:]:
            line.lineTo(pt)
        pen = QPen(col, 1.6)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(line)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawEllipse(pts[-1], 2.6, 2.6)


class StatCard(Card):
    """KPI tile: overline label, count-up value + unit, delta chip, sparkline slot."""

    def __init__(self, label_text: str, value: float = 0.0, unit: str = "", decimals: int = 0,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent=parent)
        self._decimals = decimals
        self._target = float(value)
        self._shown = float(value)
        self._count_anim = None
        body = self.body_layout()
        body.setSpacing(SPACE.xs)
        top = QHBoxLayout()
        top.setSpacing(SPACE.sm)
        self._label = label(label_text.upper(), "overline")
        top.addWidget(self._label, 1)
        self._delta: Optional[Badge] = None
        self._top = top
        body.addLayout(top)
        row = QHBoxLayout()
        row.setSpacing(SPACE.xs + 2)
        self._value = label("", "stat")
        self._unit = label(unit, "body", "secondary")
        self._unit.setVisible(bool(unit))
        row.addWidget(self._value, 0, Qt.AlignBottom)
        row.addWidget(self._unit, 0, Qt.AlignBottom)
        row.addStretch(1)
        body.addLayout(row)
        self._spark_slot = QHBoxLayout()
        self._spark_slot.setContentsMargins(0, SPACE.xs, 0, 0)
        body.addLayout(self._spark_slot)
        self._spark: Optional[Sparkline] = None
        self._render(self._shown)

    def value(self) -> float:
        """Target value."""
        return self._target

    def displayed_text(self) -> str:
        """Text currently shown in the value label."""
        return self._value.text()

    def set_value(self, value: float, animate: bool = True) -> None:
        """Set value, counting up/down from the current displayed number."""
        self._target = float(value)
        stop(self._count_anim)
        self._count_anim = animate_value(self, float(self._shown), float(value),
                                         MOTION.count_up if animate else 0, self._render,
                                         curve=MOTION.emphasized)

    def count_up(self) -> None:
        """Replay the count-up from zero to the current target."""
        self._render(0.0)
        self.set_value(self._target)

    def set_unit(self, unit: str) -> None:
        """Set the unit suffix (e.g. 'um', 'ASTM G')."""
        self._unit.setText(unit)
        self._unit.setVisible(bool(unit))

    def set_delta(self, text: str, trend: str = "up", good: bool = True) -> Badge:
        """Show a trend chip (trend up/down/flat; good selects success vs danger colouring)."""
        kind = "neutral" if trend == "flat" else ("success" if good else "danger")
        icon = {"up": "trend_up", "down": "trend_down"}.get(trend)
        if self._delta is not None:
            self._delta.setParent(None)
            self._delta.deleteLater()
        self._delta = Badge(text, kind, icon=icon)
        self._top.addWidget(self._delta, 0, Qt.AlignRight | Qt.AlignVCenter)
        return self._delta

    def sparkline_slot(self) -> QHBoxLayout:
        """Layout for a custom mini-chart widget."""
        return self._spark_slot

    def set_sparkline(self, values: Sequence[float], color: Optional[str] = None) -> Sparkline:
        """Create/update the built-in sparkline."""
        if self._spark is None:
            self._spark = Sparkline(values, color)
            self._spark_slot.addWidget(self._spark)
        else:
            self._spark.set_values(values)
        return self._spark

    def _render(self, v) -> None:
        self._shown = float(v)
        self._value.setText(f"{self._shown:,.{self._decimals}f}")
