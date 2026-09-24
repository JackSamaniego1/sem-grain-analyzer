"""Buttons: AnimatedButton (variants, hover tween, press ripple, loading) and IconButton."""
from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QAbstractAnimation, QPointF, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QPushButton, QSizePolicy, QToolButton, QWidget

from ui.design import icons
from ui.design.theme import button_colors, ui_font
from ui.design.tokens import MOTION, RADII, SIZES, SPACE, TYPE
from ui.widgets._base import ThemeAware, animate_value, lerp_color, qcolor, stop, tokens
from ui.widgets.loading import paint_spinner

_KEYBOARD_REASONS = (Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason)
_HEIGHTS = {"sm": SIZES.control_sm, "md": SIZES.control_md, "lg": SIZES.control_lg}


class AnimatedButton(ThemeAware, QPushButton):
    """Push button with variants (primary/secondary/ghost/danger/success), motion and a loading state."""

    def __init__(self, text: str = "", icon: Optional[str] = None, variant: str = "secondary",
                 size: str = "md", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self._variant = variant
        self._icon_name = icon
        self._size = size
        self._hover = 0.0
        self._press = 0.0
        self._ripple = 1.0
        self._ripple_pos = QPointF()
        self._loading = False
        self._phase = 0.0
        self._kbd_focus = False
        self._anims = {}
        self._spin = QVariantAnimation(self)
        self._spin.setStartValue(0.0)
        self._spin.setEndValue(1.0)
        self._spin.setDuration(int(MOTION.spin * 1.3))
        self._spin.setLoopCount(-1)
        self._spin.valueChanged.connect(self._set_phase)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.setFixedHeight(_HEIGHTS.get(size, SIZES.control_md))
        self.setAttribute(Qt.WA_Hover)
        self._connect_theme()

    # -- public API ---------------------------------------------------------
    def variant(self) -> str:
        """Current variant name."""
        return self._variant

    def set_variant(self, variant: str) -> None:
        """Switch variant (primary/secondary/ghost/danger/success)."""
        self._variant = variant
        self.update()

    def set_icon_name(self, name: Optional[str]) -> None:
        """Set/clear the leading icon by semantic name."""
        self._icon_name = name
        self.updateGeometry()
        self.update()

    def is_loading(self) -> bool:
        """True while the loading spinner is shown (clicks are ignored)."""
        return self._loading

    def set_loading(self, loading: bool) -> None:
        """Show a spinner in place of the icon and block clicks."""
        self._loading = bool(loading)
        if self._loading:
            self._spin.start()
            self.setCursor(Qt.BusyCursor)
        else:
            self._spin.stop()
            self.setCursor(Qt.PointingHandCursor)
        self.updateGeometry()
        self.update()

    # -- animation plumbing -------------------------------------------------
    def _tween(self, name: str, end: float, ms: int) -> None:
        stop(self._anims.get(name))
        start = getattr(self, "_" + name)

        def setter(v, n=name):
            setattr(self, "_" + n, float(v))
            self.update()
        self._anims[name] = animate_value(self, start, end, ms, setter)

    def _set_phase(self, v) -> None:
        self._phase = float(v)
        self.update()

    # -- events -------------------------------------------------------------
    def enterEvent(self, e) -> None:
        self._tween("hover", 1.0, MOTION.fast)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self._tween("hover", 0.0, MOTION.base)
        super().leaveEvent(e)

    def mousePressEvent(self, e) -> None:
        if self._loading:
            e.ignore()
            return
        if e.button() == Qt.LeftButton:
            self._ripple_pos = e.position()
            self._ripple = 0.0
            self._tween("ripple", 1.0, MOTION.slow * 2)
            self._tween("press", 1.0, MOTION.fast // 2)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if self._loading:
            e.ignore()
            return
        self._tween("press", 0.0, MOTION.fast)
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e) -> None:
        if self._loading and e.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            return
        super().keyPressEvent(e)

    def focusInEvent(self, e) -> None:
        self._kbd_focus = e.reason() in _KEYBOARD_REASONS
        super().focusInEvent(e)

    def focusOutEvent(self, e) -> None:
        self._kbd_focus = False
        super().focusOutEvent(e)

    # -- geometry / paint ---------------------------------------------------
    def _content_font(self):
        return ui_font(TYPE.body_strong)

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(self._content_font())
        w = fm.horizontalAdvance(self.text()) if self.text() else 0
        has_icon = self._icon_name or self._loading
        pad = SPACE.lg if self.text() else SPACE.sm
        if has_icon:
            w += SIZES.icon_md + (SPACE.sm if self.text() else 0)
        h = _HEIGHTS.get(self._size, SIZES.control_md)
        return QSize(max(w + pad * 2 + 2, h), h)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _e) -> None:
        t = tokens()
        c = button_colors(t, self._variant)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        enabled = self.isEnabled()
        if enabled:
            bg = lerp_color(qcolor(c["bg"]), qcolor(c["bg_hover"]), self._hover)
            bg = lerp_color(bg, qcolor(c["bg_pressed"]), self._press)
            border = lerp_color(qcolor(c["border"]), qcolor(c["border_hover"]), self._hover)
            fg = lerp_color(qcolor(c["fg"]), qcolor(c["fg_hover"]), self._hover)
        else:
            ghost = self._variant == "ghost"
            bg = qcolor("#00000000" if ghost else t.surface.surface2)
            border = qcolor("#00000000" if ghost else t.border.subtle)
            fg = qcolor(t.text.disabled)

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        scale = 1.0 - 0.03 * self._press
        cx, cy = rect.center().x(), rect.center().y()
        p.translate(cx, cy)
        p.scale(scale, scale)
        p.translate(-cx, -cy)

        path = QPainterPath()
        path.addRoundedRect(rect, RADII.md, RADII.md)
        p.fillPath(path, bg)
        if border.alpha():
            p.setPen(QPen(border, 1))
            p.drawPath(path)

        if self._ripple < 1.0 and enabled:
            p.save()
            p.setClipPath(path)
            maxr = math.hypot(rect.width(), rect.height())
            rc = qcolor(c["fg"], 0.16 * (1.0 - self._ripple))
            p.setPen(Qt.NoPen)
            p.setBrush(rc)
            rad = maxr * (0.2 + 0.8 * self._ripple)
            p.drawEllipse(self._ripple_pos, rad, rad)
            p.restore()

        if self.hasFocus() and self._kbd_focus:
            ring = qcolor(t.accent.fg if self._variant in ("primary", "danger", "success")
                          else t.border.focus)
            p.setPen(QPen(ring, 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect.adjusted(2, 2, -2, -2), RADII.md - 1, RADII.md - 1)

        # content
        font = self._content_font()
        p.setFont(font)
        fm = QFontMetrics(font)
        text = self.text()
        isz = SIZES.icon_md
        has_icon = bool(self._icon_name) or self._loading
        tw = fm.horizontalAdvance(text) if text else 0
        gap = SPACE.sm if (text and has_icon) else 0
        total = tw + (isz + gap if has_icon else 0)
        avail = self.width() - (SPACE.lg if text else SPACE.sm) * 2 + 2
        if total > avail and text:
            text = fm.elidedText(text, Qt.ElideRight, int(max(0, avail - (isz + gap if has_icon else 0))))
            tw = fm.horizontalAdvance(text)
            total = tw + (isz + gap if has_icon else 0)
        x = rect.center().x() - total / 2
        if has_icon:
            ir = QRectF(x, cy - isz / 2, isz, isz)
            if self._loading:
                paint_spinner(p, ir.adjusted(1, 1, -1, -1), self._phase, fg, 1.8)
            else:
                icons.icon(self._icon_name, fg.name()).paint(p, ir.toRect())
            x += isz + gap
        if text:
            p.setPen(fg)
            p.drawText(QRectF(x, rect.top(), tw + 2, rect.height()), Qt.AlignVCenter | Qt.AlignLeft, text)


class IconButton(ThemeAware, QToolButton):
    """Square icon-only button; a tooltip is mandatory for accessibility."""

    def __init__(self, icon: str, tooltip: str, size: int = SIZES.control_md,
                 checkable: bool = False, parent: Optional[QWidget] = None) -> None:
        if not tooltip:
            raise ValueError("IconButton requires a tooltip")
        super().__init__(parent)
        self._icon_name = icon
        self._hover = 0.0
        self._press = 0.0
        self._anims = {}
        self._kbd_focus = False
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setCheckable(checkable)
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)
        self.setFocusPolicy(Qt.StrongFocus)
        self._connect_theme()

    def set_icon_name(self, name: str) -> None:
        """Change the icon by semantic name."""
        self._icon_name = name
        self.update()

    def icon_name(self) -> str:
        """Current semantic icon name."""
        return self._icon_name

    def _tween(self, name: str, end: float, ms: int) -> None:
        stop(self._anims.get(name))
        start = getattr(self, "_" + name)

        def setter(v, n=name):
            setattr(self, "_" + n, float(v))
            self.update()
        self._anims[name] = animate_value(self, start, end, ms, setter)

    def enterEvent(self, e) -> None:
        self._tween("hover", 1.0, MOTION.fast)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self._tween("hover", 0.0, MOTION.base)
        super().leaveEvent(e)

    def mousePressEvent(self, e) -> None:
        self._tween("press", 1.0, MOTION.fast // 2)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        self._tween("press", 0.0, MOTION.fast)
        super().mouseReleaseEvent(e)

    def focusInEvent(self, e) -> None:
        self._kbd_focus = e.reason() in _KEYBOARD_REASONS
        super().focusInEvent(e)

    def focusOutEvent(self, e) -> None:
        self._kbd_focus = False
        super().focusOutEvent(e)

    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        checked = self.isChecked()
        if checked:
            bg = qcolor(t.accent.subtle)
            fg = qcolor(t.accent.text)
        else:
            bg = lerp_color(qcolor(t.surface.surface2, 0.0), qcolor(t.surface.surface2), self._hover)
            bg = lerp_color(bg, qcolor(t.surface.surface3), self._press)
            fg = lerp_color(qcolor(t.text.secondary), qcolor(t.text.primary), self._hover)
        if not self.isEnabled():
            fg = qcolor(t.text.disabled)
        s = 1.0 - 0.06 * self._press
        p.translate(rect.center())
        p.scale(s, s)
        p.translate(-rect.center())
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(rect, RADII.md, RADII.md)
        if self.hasFocus() and self._kbd_focus:
            p.setPen(QPen(qcolor(t.border.focus), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), RADII.md, RADII.md)
        isz = SIZES.icon_lg if self.width() >= 32 else SIZES.icon_md
        ir = QRectF(0, 0, isz, isz)
        ir.moveCenter(rect.center())
        icons.icon(self._icon_name, fg.name()).paint(p, ir.toRect())
