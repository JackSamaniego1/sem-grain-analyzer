"""
Tour overlay (UI-10): dimmed scrim with an animated spotlight cut-out, a glow
ring (+ pulse on "click here" steps) and a callout card that places itself
next to the target without covering it.

Painted with QPainterPath; motion via the widget-library helpers, so the
global reduced-motion setting makes every transition instant.
"""
from __future__ import annotations

from typing import Optional, Tuple

from PySide6.QtCore import (
    QAbstractAnimation, QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF, QRegion
from PySide6.QtWidgets import (
    QAbstractButton, QCheckBox, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QSizePolicy,
    QVBoxLayout, QWidget,
)

from ui.design import icons
from ui.design.theme import reduced_motion
from ui.design.tokens import MOTION, RADII, SPACE
from ui.widgets import AnimatedButton, label
from ui.widgets._base import ThemeAware, animate_value, qcolor, stop, tokens

SPOT_MS = 300           # spotlight geometry tween (spec: ~300 ms, ease-in-out)
SPOT_PAD = 6            # spotlight breathing room around the target
SPOT_RADIUS = RADII.lg + 2
EDGE = 14               # callout margin reserved for the arrow + soft shadow
ARROW = 9               # arrow length
GAP = 10                # space between the ring and the arrow tip
POINT_W = 380           # callout width, pointing steps
CENTRE_W = 500          # callout width, welcome / finish


class _Dots(QWidget):
    """Step-progress dots; the current step is a short accent pill."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._n, self._i = 0, 0
        self.setFixedHeight(8)

    def set_progress(self, i: int, n: int) -> None:
        self._i, self._n = i, n
        self.setFixedWidth(max(0, n * 10 + 10))
        self.update()

    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        x = 0.0
        for k in range(self._n):
            cur = k == self._i
            w = 16.0 if cur else 6.0
            p.setBrush(qcolor(t.accent.base if cur else
                              (t.accent.subtle_border if k < self._i else t.border.strong)))
            p.drawRoundedRect(QRectF(x, 1, w, 6), 3, 3)
            x += w + 4


class TourCallout(ThemeAware, QWidget):
    """Elevated card with title, text, progress and Back / Next / Skip."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("tourCallout")
        self._side: Optional[str] = None      # arrow side: left/right/top/bottom
        self._arrow_at = 0.0                  # arrow centre along that edge (widget px)
        self._centred = False
        self._fx = QGraphicsOpacityEffect(self)
        self._fx.setOpacity(0.0)
        self.setGraphicsEffect(self._fx)

        v = QVBoxLayout(self)
        v.setSpacing(SPACE.sm)
        self.icon = QLabel()
        self.icon.setFixedSize(56, 56)
        self.icon.setAlignment(Qt.AlignCenter)
        v.addWidget(self.icon, 0, Qt.AlignHCenter)
        head = QHBoxLayout()
        head.setSpacing(SPACE.sm)
        self.step_lbl = label("", "overline")
        self.step_lbl.setObjectName("tourStepLabel")
        head.addWidget(self.step_lbl, 1)
        self.dots = _Dots()
        head.addWidget(self.dots, 0, Qt.AlignVCenter)
        v.addLayout(head)
        self.title = label("", "h2")
        self.title.setWordWrap(True)
        v.addWidget(self.title)
        self.body = label("", "body", "secondary")
        self.body.setWordWrap(True)
        self.body.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        v.addWidget(self.body)
        v.addSpacing(SPACE.xs)
        self.dont_show = QCheckBox("Don't show this on startup")
        self.dont_show.setToolTip("You can always replay the tour from Help › Show tour")
        v.addWidget(self.dont_show)
        v.addSpacing(SPACE.xs)
        row = QHBoxLayout()
        row.setSpacing(SPACE.sm)
        self.btn_skip = AnimatedButton("Skip tour", None, "ghost", "sm")
        self.btn_skip.setToolTip("Close the tour (Esc)")
        row.addWidget(self.btn_skip)
        row.addStretch(1)
        self.btn_back = AnimatedButton("Back", "chevron_left", "secondary", "sm")
        self.btn_back.setToolTip("Previous step (Left arrow)")
        self.btn_next = AnimatedButton("Next", None, "primary", "sm")
        self.btn_next.setToolTip("Next step (Right arrow or Enter)")
        row.addWidget(self.btn_back)
        row.addWidget(self.btn_next)
        v.addLayout(row)
        for b in (self.btn_skip, self.btn_back, self.btn_next):
            b.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self._connect_theme()

    # ------------------------------------------------------------------ content
    def set_content(self, title: str, body: str, step_text: str, dots: Tuple[int, int],
                    centred: bool, icon: Optional[str], first: bool, last: bool,
                    show_check: bool) -> None:
        self._centred = centred
        self.title.setText(title)
        self.body.setText(body)
        self.step_lbl.setText(step_text.upper())
        self.step_lbl.setVisible(bool(step_text))
        self.dots.set_progress(*dots)
        self.dots.setVisible(dots[1] > 0)
        self._icon_name = icon if centred else None
        self._paint_icon()
        self.icon.setVisible(self._icon_name is not None)
        self.title.setAlignment(Qt.AlignHCenter if centred else Qt.AlignLeft)
        self.body.setAlignment(Qt.AlignHCenter if centred else Qt.AlignLeft)
        self.step_lbl.setAlignment(Qt.AlignHCenter if centred else Qt.AlignLeft)
        self.dont_show.setVisible(show_check)
        self.btn_back.setVisible(not first)
        self.btn_skip.setVisible(not last)
        self.btn_next.setText("Start tour" if first else ("Finish" if last else "Next"))
        for b in (self.btn_skip, self.btn_back, self.btn_next):
            b.setMinimumWidth(b.sizeHint().width())
        m = EDGE + (SPACE.xxl if centred else SPACE.xl)
        self.layout().setContentsMargins(m, EDGE + (SPACE.xxl if centred else SPACE.lg + 2),
                                         m, EDGE + SPACE.lg)
        w = (CENTRE_W if centred else POINT_W) + 2 * EDGE
        self.setFixedWidth(w)
        self.layout().activate()
        h = self.heightForWidth(w) if self.layout().hasHeightForWidth() else -1
        self.setFixedHeight(h if h > 0 else self.sizeHint().height())

    def _paint_icon(self) -> None:
        name = getattr(self, "_icon_name", None)
        if name:
            self.icon.setPixmap(icons.pixmap(name, 28, tokens().accent.text))

    def _on_theme_changed(self) -> None:
        self._paint_icon()
        self.update()

    def set_arrow(self, side: Optional[str], at: float) -> None:
        self._side, self._arrow_at = side, at
        self.update()

    def body_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(EDGE, EDGE, -EDGE, -EDGE)

    def opacity_effect(self) -> QGraphicsOpacityEffect:
        return self._fx

    # ------------------------------------------------------------------ paint
    def _shape(self) -> QPainterPath:
        r = self.body_rect()
        path = QPainterPath()
        path.addRoundedRect(r, RADII.lg + 2, RADII.lg + 2)
        s, a = self._side, self._arrow_at
        if s:
            h = ARROW
            if s == "left":
                tri = [QPointF(r.left() + 1, a - h), QPointF(r.left() - h, a),
                       QPointF(r.left() + 1, a + h)]
            elif s == "right":
                tri = [QPointF(r.right() - 1, a - h), QPointF(r.right() + h, a),
                       QPointF(r.right() - 1, a + h)]
            elif s == "top":
                tri = [QPointF(a - h, r.top() + 1), QPointF(a, r.top() - h),
                       QPointF(a + h, r.top() + 1)]
            else:
                tri = [QPointF(a - h, r.bottom() - 1), QPointF(a, r.bottom() + h),
                       QPointF(a + h, r.bottom() - 1)]
            tp = QPainterPath()
            tp.addPolygon(QPolygonF(tri))
            tp.closeSubpath()
            path = path.united(tp)
        return path

    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        shape = self._shape()
        # soft shadow: stacked translucent strokes (no nested QGraphicsEffect)
        p.setBrush(Qt.NoBrush)
        dark = t.mode == "dark"
        for i, a in enumerate((0.10, 0.07, 0.05, 0.03, 0.02)):
            p.setPen(QPen(QColor(0, 0, 0, int(255 * a * (1.6 if dark else 1.0))), 2 + 2 * i))
            p.drawPath(shape.translated(0, 2))
        p.setPen(QPen(qcolor(t.border.strong), 1))
        p.setBrush(qcolor(t.surface.elevated))
        p.drawPath(shape)
        r = self.body_rect()
        if self._centred:
            # accent hairline across the top of welcome / finish cards
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(t.accent.base))
            clip = QPainterPath()
            clip.addRoundedRect(r, RADII.lg + 2, RADII.lg + 2)
            p.setClipPath(clip)
            p.drawRect(QRectF(r.left(), r.top(), r.width(), 3))
            p.setClipping(False)
            if self.icon.isVisible():
                g = self.icon.geometry()
                p.setBrush(qcolor(t.accent.subtle))
                p.setPen(QPen(qcolor(t.accent.subtle_border), 1))
                p.drawEllipse(QRectF(g).adjusted(0.5, 0.5, -0.5, -0.5))


class TourOverlay(ThemeAware, QWidget):
    """Full-window scrim with a spotlight hole; hosts the callout."""

    next_requested = Signal()
    back_requested = Signal()
    skip_requested = Signal()

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setObjectName("tourOverlay")
        self._host = host
        self._spot = QRectF()
        self._level = 0.0
        self._pulse = 0.0
        self._pulsing = False
        self._spot_anim: Optional[QVariantAnimation] = None
        self._level_anim = None
        self._callout_anims: list = []
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.callout = TourCallout(self)
        self.callout.btn_next.clicked.connect(self.next_requested)
        self.callout.btn_back.clicked.connect(self.back_requested)
        self.callout.btn_skip.clicked.connect(self.skip_requested)
        self._pulse_anim = QVariantAnimation(self)
        self._pulse_anim.setStartValue(0.0)
        self._pulse_anim.setEndValue(1.0)
        self._pulse_anim.setDuration(1600)
        self._pulse_anim.setLoopCount(-1)
        self._pulse_anim.valueChanged.connect(self._on_pulse)
        host.installEventFilter(self)
        self.setGeometry(host.rect())
        self.hide()
        self._connect_theme()

    # ------------------------------------------------------------------ state
    def spot(self) -> QRectF:
        return QRectF(self._spot)

    def level(self) -> float:
        return self._level

    def is_animating(self) -> bool:
        try:
            return self._spot_anim is not None and \
                self._spot_anim.state() == QAbstractAnimation.Running
        except RuntimeError:            # deleted when it stopped
            self._spot_anim = None
            return False

    def fade_in(self) -> None:
        self.setGeometry(self._host.rect())
        self.show()
        self.raise_()
        self._animate_level(1.0, MOTION.slow)

    def fade_out(self, on_done) -> None:
        self.set_pulsing(False)
        self._animate_level(0.0, MOTION.base, on_done)
        animate_value(self, self.callout.opacity_effect().opacity(), 0.0, MOTION.fast,
                      self.callout.opacity_effect().setOpacity)

    def _animate_level(self, end: float, ms: int, on_done=None) -> None:
        stop(self._level_anim)

        def val(v):
            self._level = float(v)
            self.update()
        self._level_anim = animate_value(self, float(self._level), float(end), ms, val,
                                         on_done, MOTION.ease_out)

    def set_spot(self, rect: QRectF, animate: bool = True) -> None:
        """Move the spotlight to ``rect`` (overlay coords; empty = centred, none)."""
        stop(self._spot_anim)
        self._spot_anim = None
        if not rect.isEmpty():          # keep the ring on screen (rail items touch x=0)
            rect = rect.intersected(QRectF(self.rect()).adjusted(3, 3, -3, -3))
        if rect.isEmpty():
            c = QRectF(self.rect()).center()
            rect = QRectF(c, QSize(0, 0))
        start = QRectF(self._spot)
        if start.isNull() or start.isEmpty() and rect.isEmpty():
            animate = False
        if start.isEmpty() and not rect.isEmpty():
            # grow out of the target's own centre rather than sliding in
            start = QRectF(rect.center(), QSize(0, 0))
        if not animate or reduced_motion():
            self._apply_spot(rect)
            return
        anim = QVariantAnimation(self)
        anim.setStartValue(start)
        anim.setEndValue(QRectF(rect))
        anim.setDuration(SPOT_MS)
        from ui.widgets._base import easing
        anim.setEasingCurve(easing(MOTION.ease_in_out))
        anim.valueChanged.connect(self._apply_spot)
        anim.finished.connect(self._spot_done)
        anim.start(QAbstractAnimation.DeleteWhenStopped)
        self._spot_anim = anim

    def _spot_done(self) -> None:
        self._spot_anim = None

    def _apply_spot(self, r) -> None:
        self._spot = QRectF(r)
        self._update_mask()
        self.update()

    def _update_mask(self) -> None:
        """Clicks inside the spotlight reach the highlighted control."""
        full = QRegion(self.rect())
        s = self._spot
        if s.width() > 8 and s.height() > 8:
            full = full.subtracted(QRegion(s.adjusted(3, 3, -3, -3).toAlignedRect()))
        self.setMask(full)

    def set_pulsing(self, on: bool) -> None:
        on = on and not reduced_motion()
        self._pulsing = on
        if on and self._pulse_anim.state() != QAbstractAnimation.Running:
            self._pulse_anim.start()
        elif not on:
            self._pulse_anim.stop()
            self._pulse = 0.0
            self.update()

    def _on_pulse(self, v) -> None:
        self._pulse = float(v)
        if self._spot.width() > 2:
            pad = 24
            self.update(self._spot.adjusted(-pad, -pad, pad, pad).toAlignedRect())

    # ------------------------------------------------------------------ callout
    def show_callout(self, title: str, body: str, step_text: str, dots, centred: bool,
                     icon: Optional[str], first: bool, last: bool, show_check: bool,
                     target: QRectF, animate: bool = True) -> None:
        c = self.callout
        for a in self._callout_anims:
            stop(a)
        self._callout_anims = []
        c.set_content(title, body, step_text, dots, centred, icon, first, last, show_check)
        pos, side, at = self.place(QSize(c.width(), c.height()), target, centred)
        c.set_arrow(side, at)
        fx = c.opacity_effect()
        c.show()
        c.raise_()
        if not animate or reduced_motion():
            c.move(pos)
            fx.setOpacity(1.0)
            return
        # fade + short slide from the side the arrow points to
        dx, dy = {"left": (-8, 0), "right": (8, 0), "top": (0, -8),
                  "bottom": (0, 8)}.get(side or "", (0, 10))
        start = QPoint(pos.x() + dx, pos.y() + dy)
        c.move(start)
        fx.setOpacity(0.0)
        self._callout_anims = [
            animate_value(c, 0.0, 1.0, MOTION.slow, fx.setOpacity),
            animate_value(c, start, pos, MOTION.slow, c.move, None, MOTION.emphasized),
        ]

    def hide_callout(self) -> None:
        """Quick fade-out while the next step's page is switching in."""
        for a in self._callout_anims:
            stop(a)
        fx = self.callout.opacity_effect()
        self._callout_anims = [animate_value(self.callout, fx.opacity(), 0.0, MOTION.fast,
                                             fx.setOpacity)]

    def reposition_callout(self, target: QRectF) -> None:
        c = self.callout
        if not c.isVisible():
            return
        pos, side, at = self.place(QSize(c.width(), c.height()), target, c._centred)
        c.set_arrow(side, at)
        c.move(pos)

    def place(self, size: QSize, target: QRectF, centred: bool
              ) -> Tuple[QPoint, Optional[str], float]:
        """Callout top-left, arrow side and arrow position for ``target``."""
        bounds = QRectF(self.rect()).adjusted(8, 8, -8, -8)
        W, H = size.width(), size.height()
        if centred or target.isEmpty():
            return (QPoint(int(bounds.center().x() - W / 2),
                           int(bounds.center().y() - H / 2)), None, 0.0)
        ring = target.adjusted(-SPOT_PAD, -SPOT_PAD, SPOT_PAD, SPOT_PAD)
        off = GAP + ARROW - EDGE                     # widget edge → ring distance
        cands = {
            "left": QRectF(ring.right() + off, ring.center().y() - H / 2, W, H),
            "right": QRectF(ring.left() - off - W, ring.center().y() - H / 2, W, H),
            "top": QRectF(ring.center().x() - W / 2, ring.bottom() + off, W, H),
            "bottom": QRectF(ring.center().x() - W / 2, ring.top() - off - H, W, H),
        }
        best, best_score = None, None
        for side in ("left", "right", "top", "bottom"):
            r = QRectF(cands[side])
            # slide along the edge to stay on screen, never across the target
            if side in ("left", "right"):
                r.moveTop(max(bounds.top(), min(r.top(), bounds.bottom() - H)))
            else:
                r.moveLeft(max(bounds.left(), min(r.left(), bounds.right() - W)))
            inside = bounds.contains(r)
            body = r.adjusted(EDGE, EDGE, -EDGE, -EDGE)
            overlap = body.intersected(ring)
            score = (0 if inside else 1, overlap.width() * overlap.height(),
                     ("left", "right", "top", "bottom").index(side))
            if best_score is None or score < best_score:
                best, best_score = (side, r), score
        side, r = best
        if best_score[0]:
            r.moveLeft(max(bounds.left(), min(r.left(), bounds.right() - W)))
            r.moveTop(max(bounds.top(), min(r.top(), bounds.bottom() - H)))
        c = ring.center()
        lo = EDGE + RADII.lg + ARROW + 2
        if side in ("left", "right"):
            at = max(lo, min(c.y() - r.top(), H - lo))
        else:
            at = max(lo, min(c.x() - r.left(), W - lo))
        return QPoint(int(r.left()), int(r.top())), side, float(at)

    # ------------------------------------------------------------------ events
    def eventFilter(self, obj, ev) -> bool:
        if obj is self._host and ev.type() == QEvent.Resize and self.isVisible():
            self.setGeometry(self._host.rect())
            self._update_mask()
        return False

    def keyPressEvent(self, e) -> None:
        k = e.key()
        if k == Qt.Key_Escape:
            self.skip_requested.emit()
        elif k in (Qt.Key_Right, Qt.Key_PageDown):
            self.next_requested.emit()
        elif k in (Qt.Key_Left, Qt.Key_PageUp):
            if self.callout.btn_back.isVisible():
                self.back_requested.emit()
        elif k in (Qt.Key_Return, Qt.Key_Enter):
            from PySide6.QtWidgets import QApplication
            fw = QApplication.focusWidget()
            if isinstance(fw, QAbstractButton) and self.callout.isAncestorOf(fw):
                fw.click()
            else:
                self.next_requested.emit()
        else:
            super().keyPressEvent(e)

    def mousePressEvent(self, e) -> None:
        e.accept()          # the scrim swallows clicks outside the spotlight

    def paintEvent(self, _e) -> None:
        if self._level <= 0.001:
            return
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        scrim = qcolor(t.surface.scrim)
        scrim.setAlphaF(max(scrim.alphaF(), 0.58) * self._level)
        s = self._spot
        path = QPainterPath()
        path.setFillRule(Qt.OddEvenFill)
        path.addRect(QRectF(self.rect()))
        has_spot = s.width() > 2 and s.height() > 2
        if has_spot:
            path.addRoundedRect(s, SPOT_RADIUS, SPOT_RADIUS)
        p.fillPath(path, scrim)
        if not has_spot:
            return
        ring = QColor(t.border.focus)
        # soft glow, then a crisp 2 px ring
        p.setBrush(Qt.NoBrush)
        for w, a in ((10, 0.08), (6, 0.14), (3.5, 0.24)):
            ring.setAlphaF(a * self._level)
            p.setPen(QPen(ring, w))
            p.drawRoundedRect(s.adjusted(-w / 2, -w / 2, w / 2, w / 2),
                              SPOT_RADIUS + w / 2, SPOT_RADIUS + w / 2)
        ring.setAlphaF(self._level)
        p.setPen(QPen(ring, 2))
        p.drawRoundedRect(s, SPOT_RADIUS, SPOT_RADIUS)
        if self._pulsing and self._pulse > 0:
            g = 4 + 14 * self._pulse
            ring.setAlphaF(0.55 * (1.0 - self._pulse) * self._level)
            p.setPen(QPen(ring, 2))
            p.drawRoundedRect(s.adjusted(-g, -g, g, g), SPOT_RADIUS + g, SPOT_RADIUS + g)


__all__ = ["TourOverlay", "TourCallout", "SPOT_PAD", "SPOT_MS"]
