"""
AttentionRing - a short accent-coloured pulse drawn around a control to show
the user where to type next (UPDATE 4 item 6: the scale-bar length box after
"Auto-find scan area & scale bar").

The ring is a transparent, mouse-transparent sibling painted over the target,
so the target's own style (QSS) is never touched.  Colours come from the
active theme tokens; under reduced motion the ring is shown steady for the
same time instead of pulsing.
"""
from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QAbstractAnimation, QEvent, QObject, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QWidget

from ui.design.theme import reduced_motion
from ui.design.tokens import RADII
from ui.widgets._base import qcolor, tokens

ATTENTION_MS = 1500         # total time the ring is visible
ATTENTION_PULSES = 3        # glow peaks within ATTENTION_MS
_PAD = 4                    # ring sits this many px outside the target


class AttentionRing(QWidget):
    """Pulsing focus-style ring around ``target``; deletes itself when done."""

    def __init__(self, target: QWidget, ms: int = ATTENTION_MS) -> None:
        super().__init__(target.parentWidget())
        self.setObjectName("attentionRing")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        self._target = target
        self._t = 0.0
        self._done = False
        self._anim: Optional[QVariantAnimation] = None
        target.installEventFilter(self)
        self._place()
        self.show()
        self.raise_()
        if reduced_motion():
            self._t = 1.0 / (2 * ATTENTION_PULSES)      # steady ring at a pulse peak
            self._timer = QTimer(self)
            self._timer.setSingleShot(True)
            self._timer.timeout.connect(self.finish)
            self._timer.start(ms)
        else:
            self._anim = QVariantAnimation(self)
            self._anim.setStartValue(0.0)
            self._anim.setEndValue(1.0)
            self._anim.setDuration(ms)
            self._anim.valueChanged.connect(self._set_t)
            self._anim.finished.connect(self.finish)
            self._anim.start(QAbstractAnimation.KeepWhenStopped)

    def _place(self) -> None:
        self.setGeometry(self._target.geometry().adjusted(-_PAD, -_PAD, _PAD, _PAD))

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        if obj is self._target and ev.type() in (QEvent.Move, QEvent.Resize):
            self._place()
        elif obj is self._target and ev.type() == QEvent.Hide:
            self.finish()
        return False

    def _set_t(self, v) -> None:
        self._t = float(v)
        self.update()

    def strength(self) -> float:
        """Current glow strength 0..1 (sine pulses that fade out at the end)."""
        pulse = 0.5 - 0.5 * math.cos(2.0 * math.pi * ATTENTION_PULSES * self._t)
        return max(0.0, min(1.0, pulse * (1.0 - 0.35 * self._t)))

    def is_active(self) -> bool:
        return not self._done

    def finish(self) -> None:
        if self._done:
            return
        self._done = True
        if self._anim is not None:
            self._anim.stop()
        try:
            self._target.removeEventFilter(self)
            if getattr(self._target, "_attention_ring", None) is self:
                self._target._attention_ring = None
        except RuntimeError:
            pass
        self.hide()
        self.deleteLater()

    def paintEvent(self, _e) -> None:
        s = self.strength()
        if s <= 0.01:
            return
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        rad = RADII.md + _PAD / 2
        p.setPen(QPen(qcolor(t.accent.base, 0.30 * s), 6.0))       # soft outer glow
        p.drawRoundedRect(r.adjusted(1.5, 1.5, -1.5, -1.5), rad, rad)
        p.setPen(QPen(qcolor(t.border.focus, 0.35 + 0.65 * s), 2.0))  # crisp ring
        p.drawRoundedRect(r.adjusted(1.5, 1.5, -1.5, -1.5), rad, rad)
        p.end()


def pulse_attention(target: QWidget, ms: int = ATTENTION_MS,
                    focus: bool = True) -> Optional[AttentionRing]:
    """Pulse an accent ring around ``target`` (and focus it) so the user sees
    where to act.  Returns the ring, or None when ``target`` is not visible."""
    if target is None or target.parentWidget() is None or not target.isVisible():
        return None
    old = getattr(target, "_attention_ring", None)
    if old is not None:
        try:
            old.finish()
        except RuntimeError:
            pass
    ring = AttentionRing(target, ms)
    target._attention_ring = ring
    if focus:
        target.setFocus(Qt.OtherFocusReason)
        if hasattr(target, "selectAll"):
            target.selectAll()
    return ring
