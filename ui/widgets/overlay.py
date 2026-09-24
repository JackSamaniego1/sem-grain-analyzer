"""ShortcutOverlay: modal-less keyboard-shortcut sheet over a host window, toggled by '?'."""
from __future__ import annotations

from typing import Mapping, Optional, Sequence, Tuple, Union

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from ui.design.tokens import MOTION, RADII, SPACE
from ui.widgets._base import ThemeAware, animate_property, qcolor, tokens
from ui.widgets.buttons import IconButton
from ui.widgets.cards import label

Shortcuts = Union[Mapping[str, Sequence[Tuple[str, str]]], Sequence[Tuple[str, str]]]


class _Panel(ThemeAware, QWidget):
    """Painted elevated card (internal)."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._connect_theme()

    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(qcolor(t.border.strong), 1))
        p.setBrush(qcolor(t.surface.elevated))
        p.drawRoundedRect(r, RADII.lg + 2, RADII.lg + 2)


def _keycaps(combo: str) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(3)
    for alt_i, alt in enumerate(combo.split(" / ")):
        if alt_i:
            lay.addWidget(label("or", "caption"))
        parts = alt.split("+") if alt != "+" else ["+"]
        for i, part in enumerate(parts):
            if i:
                lay.addWidget(label("+", "caption"))
            lay.addWidget(label(part.strip(), "kbd"))
    lay.addStretch(1)
    return w


class ShortcutOverlay(ThemeAware, QWidget):
    """Scrim + centred sheet listing shortcuts; '?' toggles, Esc or scrim click closes."""

    def __init__(self, host: QWidget, shortcuts: Shortcuts,
                 title: str = "Keyboard shortcuts", toggle_key: str = "?") -> None:
        super().__init__(host)
        self._host = host
        self._opening = False
        self.setFocusPolicy(Qt.StrongFocus)
        self.hide()
        self._fx = QGraphicsOpacityEffect(self)
        self._fx.setOpacity(0.0)
        self.setGraphicsEffect(self._fx)

        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignCenter)
        self.panel = _Panel(self)
        self.panel.setMaximumWidth(760)
        outer.addWidget(self.panel, 0, Qt.AlignCenter)
        pl = QVBoxLayout(self.panel)
        pl.setContentsMargins(SPACE.xl, SPACE.lg, SPACE.lg, SPACE.xl)
        pl.setSpacing(SPACE.lg)
        head = QHBoxLayout()
        head.addWidget(label(title, "h2"), 1)
        close = IconButton("close", "Close (Esc)", size=28)
        close.clicked.connect(self.close_overlay)
        head.addWidget(close)
        pl.addLayout(head)

        sections = shortcuts if isinstance(shortcuts, Mapping) else {"": shortcuts}
        grid = QGridLayout()
        grid.setHorizontalSpacing(SPACE.xxl)
        grid.setVerticalSpacing(SPACE.lg)
        for n, (name, rows) in enumerate(sections.items()):
            col = QVBoxLayout()
            col.setSpacing(SPACE.sm)
            if name:
                col.addWidget(label(name.upper(), "overline"))
            for combo, desc in rows:
                r = QHBoxLayout()
                r.setSpacing(SPACE.lg)
                d = label(desc)
                r.addWidget(d, 1)
                r.addWidget(_keycaps(combo), 0, Qt.AlignRight)
                col.addLayout(r)
            col.addStretch(1)
            grid.addLayout(col, n // 2, n % 2)
        pl.addLayout(grid)
        pl.addWidget(label(f"Press {toggle_key} to toggle this sheet", "caption"), 0, Qt.AlignLeft)

        self._shortcut = QShortcut(QKeySequence(toggle_key), host)
        self._shortcut.setContext(Qt.WindowShortcut)
        self._shortcut.activated.connect(self.toggle)
        host.installEventFilter(self)
        self._connect_theme()

    def is_open(self) -> bool:
        """True while shown (or fading in)."""
        return self.isVisible() and self._opening

    def toggle(self) -> None:
        """Open if closed, close if open."""
        if self.is_open():
            self.close_overlay()
        else:
            self.open_overlay()

    def open_overlay(self) -> None:
        """Fade in over the host."""
        from PySide6.QtWidgets import QApplication
        if not self.isVisible():
            self._return_focus = QApplication.focusWidget()
        self._opening = True
        self.setGeometry(self._host.rect())
        self.show()
        self.raise_()
        self.setFocus(Qt.ShortcutFocusReason)
        animate_property(self._fx, b"opacity", self._fx.opacity(), 1.0, MOTION.base)

    def close_overlay(self) -> None:
        """Fade out and hide."""
        if not self.isVisible():
            return
        self._opening = False

        def done():
            if not self._opening:
                self.hide()
                prev = getattr(self, "_return_focus", None)
                self._return_focus = None
                try:
                    if prev is not None and prev.isVisible():
                        prev.setFocus(Qt.OtherFocusReason)
                except RuntimeError:  # widget deleted meanwhile
                    pass
        animate_property(self._fx, b"opacity", self._fx.opacity(), 0.0, MOTION.fast,
                         on_finished=done)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape:
            self.close_overlay()
            return
        super().keyPressEvent(e)

    def mousePressEvent(self, e) -> None:
        if not self.panel.geometry().contains(e.position().toPoint()):
            self.close_overlay()
        super().mousePressEvent(e)

    def eventFilter(self, obj, event) -> bool:
        if obj is self._host and event.type() == QEvent.Resize and self.isVisible():
            self.setGeometry(self._host.rect())
        return False

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), qcolor(tokens().surface.scrim))
