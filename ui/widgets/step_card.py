"""Step wizard building blocks (UPDATE 4 batch 4, D-38).

``StepCard``      one tiered step: number disc + title + status line + body.
                  States: ``locked`` (previous step not done: body disabled,
                  muted, tooltip "Finish step N first"), ``active`` (accent
                  border, lit buttons) and ``done`` (check badge, still
                  clickable so the operator can redo it).  The border and the
                  number disc tween between states (200 ms, ease-out).
``StepConnector`` the small arrow between two steps; lit (accent) once the
                  step above it is done.

Both are data-free: the page derives each step's state from its own data
and calls :meth:`StepCard.set_state`.
"""
from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from ui.design.theme import ui_font
from ui.design.tokens import MOTION, RADII, SPACE, TYPE
from ui.widgets._base import ThemeAware, animate_value, lerp_color, qcolor, stop, tokens
from ui.widgets.badges import Badge
from ui.widgets.cards import label

__all__ = ["StepCard", "StepConnector", "STEP_STATES", "locked_tip"]

STEP_STATES = ("locked", "active", "done")


def locked_tip(prev_number: Optional[int]) -> str:
    """Tooltip of a greyed step."""
    return f"Finish step {prev_number} first" if prev_number else "Open a session with images first"


class _StepNumber(ThemeAware, QWidget):
    """The round number disc; shows a check mark once the step is done."""

    def __init__(self, number: Optional[int], parent=None) -> None:
        super().__init__(parent)
        self.number = number
        self.done = False
        self.lit = 0.0                       # 0 = locked look, 1 = active/done look
        self.setFixedSize(26, 26)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._connect_theme()

    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.done:
            fill, fg, edge = qcolor(t.success.solid), qcolor(t.success.on_solid), qcolor(t.success.solid)
        else:
            fill = lerp_color(qcolor(t.surface.surface2), qcolor(t.accent.base), self.lit)
            fg = lerp_color(qcolor(t.text.disabled), qcolor(t.accent.fg), self.lit)
            edge = lerp_color(qcolor(t.border.strong), qcolor(t.accent.base), self.lit)
        p.setPen(QPen(edge, 1))
        p.setBrush(fill)
        p.drawEllipse(r)
        if self.done:
            pen = QPen(fg, 2.0)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            c = r.center()
            path = QPainterPath(QPointF(c.x() - 5, c.y()))
            path.lineTo(QPointF(c.x() - 1.5, c.y() + 3.5))
            path.lineTo(QPointF(c.x() + 5, c.y() - 3.5))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        elif self.number is not None:
            p.setPen(fg)
            p.setFont(ui_font(TYPE.body_strong))
            p.drawText(r, Qt.AlignCenter, str(self.number))


class StepCard(ThemeAware, QFrame):
    """One step of the sidebar wizard (see module docstring)."""

    state_changed = Signal(str)

    def __init__(self, number: Optional[int], title: str, subtitle: str = "",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.number = number
        self._state = "locked" if number else "active"
        self._lit = 0.0 if number else 1.0       # tweened 0..1 (locked -> active/done)
        self._anim = None
        self._lock_tip = locked_tip(number - 1 if number and number > 1 else None)
        self._tips: Dict[int, tuple] = {}         # id -> (widget, normal tooltip)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACE.md, SPACE.md, SPACE.md, SPACE.md)
        root.setSpacing(SPACE.sm)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(SPACE.sm)
        self.disc = _StepNumber(number, self)
        self.disc.lit = self._lit
        self.disc.setVisible(number is not None)
        head.addWidget(self.disc, 0, Qt.AlignTop)
        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(0)
        self.title = label(title, "body_strong")
        self.title.setObjectName("stepTitle")
        self.status = label(subtitle, "caption")
        self.status.setObjectName("stepStatus")
        self.status.setWordWrap(True)
        self.status.setVisible(bool(subtitle))
        titles.addWidget(self.title)
        titles.addWidget(self.status)
        head.addLayout(titles, 1)
        self._trailing = QHBoxLayout()
        self._trailing.setContentsMargins(0, 0, 0, 0)
        self._trailing.setSpacing(SPACE.xs)
        head.addLayout(self._trailing)
        self.check = Badge("Done", "success", icon="check")
        self.check.setObjectName("stepDoneBadge")
        self.check.setToolTip("This step is done. You can redo it at any time.")
        self.check.hide()
        head.addWidget(self.check, 0, Qt.AlignTop)
        root.addLayout(head)
        self.body = QWidget(self)
        self.body.setObjectName("stepBody")
        self._body = QVBoxLayout(self.body)
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSpacing(SPACE.sm)
        root.addWidget(self.body)
        self.setAccessibleName(title if number is None else f"Step {number}: {title}")
        self._connect_theme()
        self._apply_state(animate=False)

    # -- API ----------------------------------------------------------------
    def body_layout(self) -> QVBoxLayout:
        return self._body

    def add_widget(self, w: QWidget, stretch: int = 0) -> QWidget:
        self._body.addWidget(w, stretch)
        return w

    def add_layout(self, lay) -> None:
        self._body.addLayout(lay)

    def header_trailing(self) -> QHBoxLayout:
        """Layout at the right end of the header (e.g. a switch)."""
        return self._trailing

    def set_status(self, text: str, tone: Optional[str] = None) -> None:
        self.status.setText(text)
        self.status.setVisible(bool(text))
        if self.status.property("tone") != tone:
            self.status.setProperty("tone", tone)
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)

    def set_locked_tip(self, text: str) -> None:
        self._lock_tip = text
        if self._state == "locked":
            self._apply_tips()

    def locked_tip(self) -> str:
        return self._lock_tip

    def register(self, *widgets: QWidget) -> None:
        """Widgets whose tooltip reads "Finish step N first" while locked."""
        for w in widgets:
            self._tips[id(w)] = (w, w.toolTip())
        self._apply_tips()

    def set_tooltip_for(self, w: QWidget, tip: str) -> None:
        """Change a registered widget's normal tooltip."""
        self._tips[id(w)] = (w, tip)
        if self._state != "locked":
            w.setToolTip(tip)

    def set_optional(self, on: bool) -> None:
        """An optional step (no number): neutral border instead of accent."""
        self._optional = bool(on)
        self.update()

    def is_optional(self) -> bool:
        return getattr(self, "_optional", False)

    def set_collapse_when_locked(self, on: bool) -> None:
        """Locked: show only the header row (the body is hidden, not just
        greyed) -- for steps whose body is large, e.g. the mode tiles."""
        self._collapse = bool(on)
        self._apply_state(animate=False)

    def collapses_when_locked(self) -> bool:
        return getattr(self, "_collapse", False)

    def state(self) -> str:
        return self._state

    def is_locked(self) -> bool:
        return self._state == "locked"

    def is_active(self) -> bool:
        return self._state == "active"

    def is_done(self) -> bool:
        return self._state == "done"

    def set_state(self, state: str, animate: bool = True) -> None:
        if state not in STEP_STATES:
            raise ValueError(state)
        if state == self._state:
            return
        self._state = state
        self._apply_state(animate)
        self.state_changed.emit(state)

    # -- internals ------------------------------------------------------------
    def _apply_tips(self) -> None:
        locked = self._state == "locked"
        for w, tip in list(self._tips.values()):
            try:
                w.setToolTip(self._lock_tip if locked else tip)
            except RuntimeError:
                pass
        self.setToolTip(self._lock_tip if locked else "")

    def _apply_state(self, animate: bool) -> None:
        locked = self._state == "locked"
        self.body.setEnabled(not locked)
        self.body.setVisible(not (locked and self.collapses_when_locked()))
        self.title.setEnabled(not locked)
        self.status.setEnabled(not locked)
        self.check.setVisible(self._state == "done")
        self.disc.done = self._state == "done"
        self.setProperty("stepState", self._state)
        self._apply_tips()
        stop(self._anim)
        self._anim = animate_value(self, self._lit, 0.0 if locked else 1.0,
                                   MOTION.base if animate else 0, self._set_lit)

    def _set_lit(self, v) -> None:
        self._lit = float(v)
        self.disc.lit = self._lit
        self.disc.update()
        self.update()

    def lit(self) -> float:
        return self._lit

    def sizeHint(self) -> QSize:
        s = super().sizeHint()
        return QSize(max(s.width(), 240), s.height())

    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        if self._state == "done":
            edge, width = qcolor(t.success.border), 1.0
            bg = qcolor(t.surface.surface1)
        elif self.is_optional():
            edge, width = qcolor(t.border.subtle), 1.0
            bg = lerp_color(qcolor(t.surface.bg), qcolor(t.surface.surface1), self._lit)
            p.setPen(QPen(edge, width, Qt.DashLine))
            p.setBrush(bg)
            p.drawRoundedRect(r, RADII.lg, RADII.lg)
            return
        else:
            edge = lerp_color(qcolor(t.border.subtle), qcolor(t.accent.base), self._lit)
            width = 1.0 + 0.6 * self._lit
            bg = lerp_color(qcolor(t.surface.bg), qcolor(t.surface.surface1), self._lit)
        p.setPen(QPen(edge, width))
        p.setBrush(bg)
        p.drawRoundedRect(r, RADII.lg, RADII.lg)


class StepConnector(ThemeAware, QWidget):
    """Short vertical arrow between two steps (lit once the step above is done)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._on = 0.0
        self._target = False
        self._anim = None
        self.setFixedHeight(18)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._connect_theme()

    def is_lit(self) -> bool:
        return self._target

    def set_lit(self, on: bool, animate: bool = True) -> None:
        on = bool(on)
        if on == self._target:
            return
        self._target = on
        stop(self._anim)
        self._anim = animate_value(self, self._on, 1.0 if on else 0.0,
                                   MOTION.base if animate else 0, self._set_on)

    def _set_on(self, v) -> None:
        self._on = float(v)
        self.update()

    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = lerp_color(qcolor(t.border.strong), qcolor(t.accent.text), self._on)
        pen = QPen(c, 1.6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        x = self.width() / 2.0
        h = float(self.height())
        p.drawLine(QPointF(x, 1), QPointF(x, h - 4))
        path = QPainterPath(QPointF(x - 4.5, h - 8))
        path.lineTo(QPointF(x, h - 3.5))
        path.lineTo(QPointF(x + 4.5, h - 8))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
