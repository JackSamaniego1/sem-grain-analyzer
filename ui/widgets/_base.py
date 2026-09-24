"""Shared animation + theming helpers for ui.widgets (internal)."""
from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtCore import (
    QAbstractAnimation, QEasingCurve, QObject, QPropertyAnimation, QVariantAnimation,
)
from PySide6.QtGui import QColor

from ui.design.theme import current_tokens, reduced_motion, theme_manager
from ui.design.tokens import MOTION, ThemeTokens


def tokens() -> ThemeTokens:
    """Active colour tokens (shorthand)."""
    return current_tokens()


def qcolor(hex_color: str, alpha: Optional[float] = None) -> QColor:
    """QColor from a token hex string, optionally overriding alpha (0..1)."""
    c = QColor(hex_color)
    if alpha is not None:
        c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def lerp_color(a: QColor, b: QColor, t: float) -> QColor:
    """Blend two QColors including alpha."""
    t = max(0.0, min(1.0, t))
    return QColor.fromRgbF(
        a.redF() + (b.redF() - a.redF()) * t,
        a.greenF() + (b.greenF() - a.greenF()) * t,
        a.blueF() + (b.blueF() - a.blueF()) * t,
        a.alphaF() + (b.alphaF() - a.alphaF()) * t,
    )


def easing(name: str = MOTION.ease_out) -> QEasingCurve:
    """QEasingCurve from a MOTION easing name."""
    return QEasingCurve(getattr(QEasingCurve.Type, name))


def duration(ms: int) -> int:
    """Duration honouring the global reduced-motion flag."""
    return 0 if reduced_motion() else ms


def animate_value(owner: QObject, start: Any, end: Any, ms: int,
                  on_value: Callable[[Any], None],
                  on_finished: Optional[Callable[[], None]] = None,
                  curve: str = MOTION.ease_out) -> Optional[QVariantAnimation]:
    """Tween ``start``->``end`` calling ``on_value``; instant (returns None) under reduced motion."""
    if reduced_motion() or ms <= 0:
        on_value(end)
        if on_finished:
            on_finished()
        return None
    anim = QVariantAnimation(owner)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setDuration(ms)
    anim.setEasingCurve(easing(curve))
    anim.valueChanged.connect(on_value)
    if on_finished:
        anim.finished.connect(on_finished)
    anim.start(QAbstractAnimation.DeleteWhenStopped)
    return anim


def animate_property(target: QObject, prop: bytes, start: Any, end: Any, ms: int,
                     on_finished: Optional[Callable[[], None]] = None,
                     curve: str = MOTION.ease_out) -> Optional[QPropertyAnimation]:
    """QPropertyAnimation on ``target.prop``; instant (returns None) under reduced motion."""
    if reduced_motion() or ms <= 0:
        target.setProperty(prop.decode(), end)
        if on_finished:
            on_finished()
        return None
    anim = QPropertyAnimation(target, prop, target)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setDuration(ms)
    anim.setEasingCurve(easing(curve))
    if on_finished:
        anim.finished.connect(on_finished)
    anim.start(QAbstractAnimation.DeleteWhenStopped)
    return anim


def stop(anim: Optional[QAbstractAnimation]) -> None:
    """Stop an animation if still alive."""
    if anim is None:
        return
    try:
        anim.stop()
    except RuntimeError:  # already deleted
        pass


class ThemeAware:
    """Mixin: calls ``self._on_theme_changed()`` (default ``update()``) on theme switches."""

    def _connect_theme(self) -> None:
        """Subscribe to theme changes (call once in __init__)."""
        theme_manager().theme_changed.connect(self._theme_slot)

    def _theme_slot(self, _mode: str) -> None:
        try:
            self._on_theme_changed()
        except RuntimeError:  # C++ object already destroyed
            pass

    def _on_theme_changed(self) -> None:
        self.update()


def repolish(widget) -> None:
    """Force QSS re-evaluation after a dynamic property change."""
    st = widget.style()
    st.unpolish(widget)
    st.polish(widget)
    widget.update()
