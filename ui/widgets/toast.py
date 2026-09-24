"""Non-blocking notifications: Toast + ToastManager (stacked bottom-right, slide+fade, auto-dismiss)."""
from __future__ import annotations

from typing import Callable, List, Optional

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QGraphicsOpacityEffect, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ui.design import icons
from ui.design.tokens import MOTION, RADII, SIZES, SPACE
from ui.widgets._base import ThemeAware, animate_property, animate_value, qcolor, tokens
from ui.widgets.buttons import AnimatedButton, IconButton

_SHADOW = 10  # px reserved around the card for the painted shadow


class Toast(ThemeAware, QWidget):
    """One notification card; severity in info/success/warning/danger."""

    action_triggered = Signal()
    close_requested = Signal()

    def __init__(self, title: str, message: str = "", severity: str = "info",
                 action_text: Optional[str] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.severity = severity
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_Hover)
        self.setFixedWidth(SIZES.toast_width + _SHADOW * 2)
        root = QHBoxLayout(self)
        root.setContentsMargins(_SHADOW + SPACE.lg, _SHADOW + SPACE.md,
                                _SHADOW + SPACE.sm, _SHADOW + SPACE.md)
        root.setSpacing(SPACE.md)
        self._icon = QLabel(self)
        self._icon.setFixedSize(20, 20)
        root.addWidget(self._icon, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title_label = QLabel(title, self)
        self.title_label.setProperty("role", "body_strong")
        self.title_label.setWordWrap(True)
        col.addWidget(self.title_label)
        self.message_label = QLabel(message, self)
        self.message_label.setProperty("tone", "secondary")
        self.message_label.setWordWrap(True)
        self.message_label.setVisible(bool(message))
        col.addWidget(self.message_label)
        if action_text:
            row = QHBoxLayout()
            row.setContentsMargins(0, SPACE.xs, 0, 0)
            self.action_button = AnimatedButton(action_text, variant="secondary", size="sm")
            self.action_button.clicked.connect(self.action_triggered)
            row.addWidget(self.action_button)
            row.addStretch(1)
            col.addLayout(row)
        else:
            self.action_button = None
        root.addLayout(col, 1)
        self.close_button = IconButton("close", "Dismiss", size=24)
        self.close_button.clicked.connect(self.close_requested)
        root.addWidget(self.close_button, 0, Qt.AlignTop)
        self.opacity = QGraphicsOpacityEffect(self)
        self.opacity.setOpacity(1.0)
        self.setGraphicsEffect(self.opacity)
        self._refresh_icon()
        self._connect_theme()
        self.adjustSize()

    def _refresh_icon(self) -> None:
        sem = tokens().semantic(self.severity)
        self._icon.setPixmap(icons.pixmap(self.severity, 20, sem.fg))

    def _on_theme_changed(self) -> None:
        self._refresh_icon()
        self.update()

    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        card = QRectF(self.rect()).adjusted(_SHADOW, _SHADOW, -_SHADOW, -_SHADOW)
        # soft painted shadow (graphics effect slot is used by the opacity fade)
        alpha = 0.10 if t.mode == "dark" else 0.035
        p.setPen(Qt.NoPen)
        for i in range(_SHADOW, 0, -2):
            p.setBrush(qcolor("#000000", alpha * (1 - i / (_SHADOW + 1))))
            p.drawRoundedRect(card.adjusted(-i, -i + 3, i, i + 3), RADII.lg + i, RADII.lg + i)
        p.setPen(QPen(qcolor(t.border.strong), 1))
        p.setBrush(qcolor(t.surface.elevated))
        p.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), RADII.lg, RADII.lg)
        # severity stripe
        sem = t.semantic(self.severity)
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(sem.solid))
        stripe = QRectF(card.left() + 5, card.top() + 10, 3, card.height() - 20)
        p.drawRoundedRect(stripe, 1.5, 1.5)


class ToastManager(QObject):
    """Owns toasts for a host window: stacking, entrance/exit motion, auto-dismiss, hover-pause."""

    toast_shown = Signal(object)
    toast_dismissed = Signal(object)

    def __init__(self, host: QWidget, max_visible: int = 4, margin: int = SPACE.lg) -> None:
        super().__init__(host)
        self._host = host
        self._toasts: List[Toast] = []
        self._timers = {}
        self._max = max_visible
        self._margin = margin
        host.installEventFilter(self)

    def count(self) -> int:
        """Number of live (not yet dismissed) toasts."""
        return len(self._toasts)

    def toasts(self) -> List[Toast]:
        """Live toasts, oldest first."""
        return list(self._toasts)

    def show_toast(self, title: str, message: str = "", severity: str = "info",
                   action_text: Optional[str] = None,
                   on_action: Optional[Callable[[], None]] = None,
                   timeout_ms: Optional[int] = None) -> Toast:
        """Show a toast; ``timeout_ms=0`` keeps it until dismissed."""
        toast = Toast(title, message, severity, action_text, self._host)
        toast.close_requested.connect(lambda: self.dismiss(toast))
        if on_action:
            toast.action_triggered.connect(on_action)
        toast.action_triggered.connect(lambda: self.dismiss(toast))
        toast.installEventFilter(self)
        self._toasts.append(toast)
        while len(self._toasts) > self._max:
            self.dismiss(self._toasts[0])
        toast.adjustSize()
        targets = self._targets()
        end = targets[toast]
        toast.move(end + QPoint(0, 16))
        toast.opacity.setOpacity(0.0)
        toast.show()
        toast.raise_()
        animate_property(toast.opacity, b"opacity", 0.0, 1.0, MOTION.base)
        animate_property(toast, b"pos", toast.pos(), end, MOTION.slow, curve=MOTION.emphasized)
        self._restack(exclude=toast)
        timeout = MOTION.toast_timeout if timeout_ms is None else timeout_ms
        if timeout > 0:
            timer = QTimer(toast)
            timer.setSingleShot(True)
            timer.setInterval(timeout)
            timer.timeout.connect(lambda: self.dismiss(toast))
            timer.start()
            self._timers[toast] = timer
        self.toast_shown.emit(toast)
        return toast

    def dismiss(self, toast: Toast) -> None:
        """Fade/slide a toast out and restack the rest."""
        if toast not in self._toasts:
            return
        self._toasts.remove(toast)
        timer = self._timers.pop(toast, None)
        if timer:
            timer.stop()
        toast.removeEventFilter(self)

        def finish():
            toast.hide()
            toast.deleteLater()
        animate_property(toast.opacity, b"opacity", toast.opacity.opacity(), 0.0, MOTION.fast)
        animate_property(toast, b"pos", toast.pos(), toast.pos() + QPoint(24, 0), MOTION.base,
                         on_finished=finish, curve=MOTION.ease_in)
        self._restack()
        self.toast_dismissed.emit(toast)

    def clear(self) -> None:
        """Dismiss every toast."""
        for t in list(self._toasts):
            self.dismiss(t)

    def _targets(self):
        out = {}
        hw, hh = self._host.width(), self._host.height()
        y = hh - self._margin + _SHADOW
        for t in reversed(self._toasts):
            h = t.sizeHint().height()
            y -= h
            out[t] = QPoint(hw - t.width() - self._margin + _SHADOW, y)
            y -= SPACE.sm - _SHADOW * 2
        return out

    def _restack(self, exclude: Optional[Toast] = None, animate: bool = True) -> None:
        for t, pos in self._targets().items():
            if t is exclude:
                continue
            if animate:
                animate_property(t, b"pos", t.pos(), pos, MOTION.base)
            else:
                t.move(pos)

    def eventFilter(self, obj, event) -> bool:
        et = event.type()
        if obj is self._host and et == QEvent.Resize:
            self._restack(animate=False)
        elif isinstance(obj, Toast):
            timer = self._timers.get(obj)
            if timer is not None:
                if et == QEvent.Enter:
                    timer.stop()
                elif et == QEvent.Leave:
                    timer.start(max(1500, timer.interval() // 2))
        return False
