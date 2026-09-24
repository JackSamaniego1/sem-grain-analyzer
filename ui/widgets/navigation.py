"""Navigation: NavRail (icon rail + animated pill), Breadcrumb, FadeStackedWidget."""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from PySide6.QtCore import (
    QAbstractAnimation, QEasingCurve, QParallelAnimationGroup, QPoint, QPropertyAnimation, QRectF,
    QSize, Qt, QTimer, Signal,
)
from PySide6.QtGui import QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect, QHBoxLayout, QLabel, QSizePolicy, QStackedWidget, QToolButton,
    QVBoxLayout, QWidget,
)

from ui.design import icons
from ui.design.theme import reduced_motion, ui_font
from ui.design.tokens import MOTION, RADII, SIZES, SPACE, TYPE
from ui.widgets._base import (
    ThemeAware, animate_value, easing, lerp_color, qcolor, repolish, stop, tokens,
)
from ui.widgets.buttons import IconButton


# ---------------------------------------------------------------------------
# NavRail
# ---------------------------------------------------------------------------


class _NavItem(ThemeAware, QWidget):
    """One rail entry (internal)."""

    activated = Signal(str)

    def __init__(self, key: str, icon: str, label: str, rail: "NavRail") -> None:
        super().__init__(rail)
        self.key, self.icon_name, self.label = key, icon, label
        self.rail = rail
        self.hover = 0.0
        self._anim = None
        self.setFixedHeight(SIZES.rail_item)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_Hover)
        self.setToolTip(label)
        self.setAccessibleName(label)
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
            self.activated.emit(self.key)
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self.activated.emit(self.key)
        elif e.key() in (Qt.Key_Up, Qt.Key_Down):
            self.rail._focus_step(self, -1 if e.key() == Qt.Key_Up else 1)
        else:
            super().keyPressEvent(e)

    def focusInEvent(self, e) -> None:
        self.update()
        super().focusInEvent(e)

    def focusOutEvent(self, e) -> None:
        self.update()
        super().focusOutEvent(e)

    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        selected = self.rail.current() == self.key
        r = QRectF(self.rect()).adjusted(SPACE.sm, 2, -SPACE.sm, -2)
        if not selected and self.hover > 0:
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(t.surface.surface2, self.hover))
            p.drawRoundedRect(r, RADII.md, RADII.md)
        if self.hasFocus():
            p.setPen(QPen(qcolor(t.border.focus), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), RADII.md, RADII.md)
        if selected:
            fg = qcolor(t.accent.text)
        else:
            fg = lerp_color(qcolor(t.text.secondary), qcolor(t.text.primary), self.hover)
        isz = SIZES.icon_lg
        cx = SIZES.rail_collapsed / 2
        ir = QRectF(cx - isz / 2, r.center().y() - isz / 2, isz, isz)
        icons.icon(self.icon_name, fg.name()).paint(p, ir.toRect())
        prog = self.rail.expand_progress()
        if prog > 0.02:
            col = qcolor(t.text.primary if selected else fg.name(), prog)
            p.setPen(col)
            p.setFont(ui_font(TYPE.body_strong if selected else TYPE.body))
            x = SIZES.rail_collapsed - 4
            p.drawText(QRectF(x, r.top(), self.width() - x - SPACE.md, r.height()),
                       Qt.AlignVCenter | Qt.AlignLeft,
                       QFontMetrics(p.font()).elidedText(self.label, Qt.ElideRight,
                                                        int(self.width() - x - SPACE.md)))


class NavRail(ThemeAware, QWidget):
    """Vertical icon rail with animated selection pill; expands to show labels. Emits ``page_selected(key)``."""

    page_selected = Signal(str)
    expanded_changed = Signal(bool)

    def __init__(self, parent: Optional[QWidget] = None, expand_on_hover: bool = False,
                 show_toggle: bool = True) -> None:
        super().__init__(parent)
        self._items: Dict[str, _NavItem] = {}
        self._order: List[str] = []
        self._current: Optional[str] = None
        self._expanded = False
        self._progress = 0.0
        self._pill_from: Optional[float] = None
        self._pill_t = 1.0
        self._anims = {}
        self._hover_expand = expand_on_hover
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(350)
        self._hover_timer.timeout.connect(lambda: self.set_expanded(True))
        self.setFixedWidth(SIZES.rail_collapsed)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, SPACE.md, 0, SPACE.md)
        root.setSpacing(2)
        if show_toggle:
            row = QHBoxLayout()
            row.setContentsMargins((SIZES.rail_collapsed - SIZES.control_lg) // 2, 0, 0, SPACE.sm)
            self._toggle = IconButton("menu", "Expand navigation", size=SIZES.control_lg)
            self._toggle.clicked.connect(lambda: self.set_expanded(not self._expanded))
            row.addWidget(self._toggle)
            row.addStretch(1)
            root.addLayout(row)
        else:
            self._toggle = None
        self._top = QVBoxLayout()
        self._top.setSpacing(2)
        root.addLayout(self._top)
        root.addStretch(1)
        self._bottom = QVBoxLayout()
        self._bottom.setSpacing(2)
        root.addLayout(self._bottom)
        self._connect_theme()

    # -- API ----------------------------------------------------------------
    def add_page(self, key: str, icon: str, label: str, bottom: bool = False) -> None:
        """Add an entry; the first entry becomes current."""
        item = _NavItem(key, icon, label, self)
        item.activated.connect(self._on_activated)
        (self._bottom if bottom else self._top).addWidget(item)
        self._items[key] = item
        self._order.append(key)
        if self._current is None:
            self._current = key

    def keys(self) -> List[str]:
        """Entry keys in insertion order."""
        return list(self._order)

    def current(self) -> Optional[str]:
        """Selected key."""
        return self._current

    def item(self, key: str) -> QWidget:
        """Entry widget for a key (tests / focus handling)."""
        return self._items[key]

    def set_current(self, key: str, emit: bool = True, animate: bool = True) -> None:
        """Select an entry, sliding the pill; emits ``page_selected`` if ``emit``."""
        if key not in self._items or key == self._current:
            return
        old = self._items.get(self._current) if self._current else None
        self._pill_from = float(old.geometry().top()) if old is not None else None
        self._current = key
        stop(self._anims.get("pill"))
        self._pill_t = 0.0
        self._anims["pill"] = animate_value(self, 0.0, 1.0, MOTION.slow if animate else 0,
                                            self._set_pill_t, curve=MOTION.emphasized)
        for it in self._items.values():
            it.update()
        if emit:
            self.page_selected.emit(key)

    def is_expanded(self) -> bool:
        """True when labels are shown."""
        return self._expanded

    def expand_progress(self) -> float:
        """0 collapsed .. 1 expanded (animated)."""
        return self._progress

    def set_expanded(self, on: bool, animate: bool = True) -> None:
        """Animate width between collapsed and expanded."""
        on = bool(on)
        if on == self._expanded:
            return
        self._expanded = on
        if self._toggle is not None:
            self._toggle.setToolTip("Collapse navigation" if on else "Expand navigation")
        stop(self._anims.get("expand"))
        self._anims["expand"] = animate_value(self, self._progress, 1.0 if on else 0.0,
                                              MOTION.slow if animate else 0, self._set_progress,
                                              curve=MOTION.ease_in_out)
        self.expanded_changed.emit(on)

    # -- internals ----------------------------------------------------------
    def _set_progress(self, v) -> None:
        self._progress = float(v)
        w = SIZES.rail_collapsed + (SIZES.rail_expanded - SIZES.rail_collapsed) * self._progress
        self.setFixedWidth(int(round(w)))
        for it in self._items.values():
            it.update()
        self.update()

    def _set_pill_t(self, v) -> None:
        self._pill_t = float(v)
        self.update()

    def _on_activated(self, key: str) -> None:
        self.set_current(key)

    def _focus_step(self, item: _NavItem, step: int) -> None:
        i = self._order.index(item.key)
        nxt = self._order[(i + step) % len(self._order)]
        self._items[nxt].setFocus(Qt.TabFocusReason)

    def enterEvent(self, e) -> None:
        if self._hover_expand:
            self._hover_timer.start()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        if self._hover_expand:
            self._hover_timer.stop()
            self.set_expanded(False)
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), qcolor(t.surface.surface1))
        p.setPen(QPen(qcolor(t.border.subtle), 1))
        p.drawLine(self.width() - 1, 0, self.width() - 1, self.height())
        item = self._items.get(self._current) if self._current else None
        if item is None or not item.isVisible():
            return
        target = float(item.geometry().top())
        y = target
        if self._pill_from is not None and self._pill_t < 1.0:
            y = self._pill_from + (target - self._pill_from) * self._pill_t
        h = item.height()
        pill = QRectF(SPACE.sm, y + 2, self.width() - SPACE.sm * 2, h - 4)
        p.setPen(QPen(qcolor(t.accent.subtle_border), 1))
        p.setBrush(qcolor(t.accent.subtle))
        p.drawRoundedRect(pill.adjusted(0.5, 0.5, -0.5, -0.5), RADII.md, RADII.md)
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(t.accent.base))
        p.drawRoundedRect(QRectF(0, y + h / 2 - 10, 3, 20), 1.5, 1.5)


# ---------------------------------------------------------------------------
# Breadcrumb
# ---------------------------------------------------------------------------


class Breadcrumb(ThemeAware, QWidget):
    """Path of clickable segments separated by chevrons; emits ``segment_clicked(index, text)``."""

    segment_clicked = Signal(int, str)

    def __init__(self, segments: Sequence[str] = (), max_segment_width: int = 180,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._max_w = max_segment_width
        self._segments: List[str] = []
        self._buttons: List[QToolButton] = []
        self._seps: List[QLabel] = []
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._connect_theme()
        self.set_segments(segments)

    def segments(self) -> List[str]:
        """Current path segments."""
        return list(self._segments)

    def buttons(self) -> List[QToolButton]:
        """Segment buttons (last one is the current, non-navigating segment)."""
        return list(self._buttons)

    def set_segments(self, segments: Sequence[str]) -> None:
        """Replace the path."""
        while self._layout.count():
            w = self._layout.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self._segments = list(segments)
        self._buttons, self._seps = [], []
        font = ui_font(TYPE.body)
        fm = QFontMetrics(font)
        last = len(self._segments) - 1
        for i, text in enumerate(self._segments):
            if i:
                sep = QLabel(self)
                sep.setFixedWidth(16)
                sep.setAlignment(Qt.AlignCenter)
                self._seps.append(sep)
                self._layout.addWidget(sep)
            b = QToolButton(self)
            b.setProperty("role", "crumb")
            b.setProperty("current", "true" if i == last else "false")
            b.setText(fm.elidedText(text, Qt.ElideMiddle, self._max_w))
            b.setToolTip(text)
            b.setAutoRaise(True)
            b.setCursor(Qt.ArrowCursor if i == last else Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, idx=i: self._clicked(idx))
            self._buttons.append(b)
            self._layout.addWidget(b)
        self._layout.addStretch(1)
        self._refresh_separators()

    def _clicked(self, idx: int) -> None:
        if idx < len(self._segments) - 1:
            self.segment_clicked.emit(idx, self._segments[idx])

    def _refresh_separators(self) -> None:
        pm = icons.pixmap("chevron_right", 14, tokens().text.tertiary)
        for s in self._seps:
            s.setPixmap(pm)

    def _on_theme_changed(self) -> None:
        self._refresh_separators()
        for b in self._buttons:
            repolish(b)


# ---------------------------------------------------------------------------
# FadeStackedWidget
# ---------------------------------------------------------------------------


class FadeStackedWidget(QStackedWidget):
    """QStackedWidget that cross-fades + slides (12 px) between pages."""

    transition_finished = Signal(int)

    SLIDE_PX = 14

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._group: Optional[QParallelAnimationGroup] = None
        self._overlay: Optional[QLabel] = None
        self._incoming: Optional[QWidget] = None

    def is_animating(self) -> bool:
        """True while a transition runs."""
        return self._group is not None and self._group.state() != QAbstractAnimation.Stopped

    def animation_group(self) -> Optional[QParallelAnimationGroup]:
        """Running transition group (for frame capture / tests)."""
        return self._group

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802 (Qt override)
        """Animated page switch."""
        self.set_current_index(index)

    def setCurrentWidget(self, w: QWidget) -> None:  # noqa: N802 (Qt override)
        """Animated page switch by widget."""
        self.set_current_index(self.indexOf(w))

    def set_current_widget(self, w: QWidget) -> None:
        """Animated page switch by widget."""
        self.set_current_index(self.indexOf(w))

    def set_current_index(self, index: int) -> None:
        """Animated page switch by index."""
        if index < 0 or index >= self.count() or index == self.currentIndex():
            return
        self._finish()
        old_idx = self.currentIndex()
        old = self.currentWidget()
        if old is None or reduced_motion() or not self.isVisible():
            QStackedWidget.setCurrentIndex(self, index)
            self.transition_finished.emit(index)
            return
        snap = old.grab()
        QStackedWidget.setCurrentIndex(self, index)
        new = self.currentWidget()

        overlay = QLabel(self)
        overlay.setAttribute(Qt.WA_TransparentForMouseEvents)
        overlay.setPixmap(snap)
        overlay.setGeometry(old.geometry())
        ov_fx = QGraphicsOpacityEffect(overlay)
        overlay.setGraphicsEffect(ov_fx)
        overlay.show()
        overlay.raise_()
        self._overlay = overlay

        new_fx = QGraphicsOpacityEffect(new)
        new_fx.setOpacity(0.0)
        new.setGraphicsEffect(new_fx)
        self._incoming = new
        direction = 1 if index > old_idx else -1
        end_pos = new.pos()
        start_pos = end_pos + QPoint(self.SLIDE_PX * direction, 0)
        new.move(start_pos)

        group = QParallelAnimationGroup(self)
        a_out = QPropertyAnimation(ov_fx, b"opacity")
        a_out.setStartValue(1.0)
        a_out.setEndValue(0.0)
        a_out.setDuration(int(MOTION.slow * 0.4))
        a_out.setEasingCurve(easing(MOTION.ease_out))
        # "Fade-through": the incoming page starts once the outgoing one has
        # gone, so the two pages never read as a double exposure.
        a_in = QPropertyAnimation(new_fx, b"opacity")
        a_in.setDuration(MOTION.slow)
        a_in.setKeyValueAt(0.0, 0.0)
        a_in.setKeyValueAt(0.4, 0.0)
        a_in.setKeyValueAt(0.75, 0.9)
        a_in.setKeyValueAt(1.0, 1.0)
        a_pos = QPropertyAnimation(new, b"pos")
        a_pos.setDuration(MOTION.slow)
        a_pos.setKeyValueAt(0.0, start_pos)
        a_pos.setKeyValueAt(0.4, start_pos)
        a_pos.setKeyValueAt(0.75, end_pos + QPoint(int(self.SLIDE_PX * 0.2) * direction, 0))
        a_pos.setKeyValueAt(1.0, end_pos)
        for a in (a_out, a_in, a_pos):
            group.addAnimation(a)
        group.finished.connect(self._finish)
        self._group = group
        group.start()

    def _finish(self) -> None:
        g = self._group
        if g is None:
            return
        self._group = None
        if g.state() != QAbstractAnimation.Stopped:
            g.stop()
        new = self._incoming
        self._incoming = None
        if new is not None:
            new.setGraphicsEffect(None)
            new.setGeometry(self.contentsRect())
        if self._overlay is not None:
            self._overlay.hide()
            self._overlay.deleteLater()
            self._overlay = None
        g.deleteLater()
        self.transition_finished.emit(self.currentIndex())
