"""Building blocks shared by the v3 pages (composed from ui.widgets; no inline QSS)."""
from __future__ import annotations

from typing import Callable, List, Optional, Sequence

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from ui.design import icons
from ui.design.theme import ui_font
from ui.design.tokens import MOTION, RADII, SPACE, TYPE
from ui.widgets import AnimatedButton, Card, StatCard, label
from ui.widgets._base import ThemeAware, animate_value, lerp_color, qcolor, stop, tokens


class Panel(ThemeAware, QWidget):
    """Flat surface1 panel with a hairline on one edge (side panels, bars)."""

    def __init__(self, edge: str = "left", parent: Optional[QWidget] = None,
                 surface: str = "surface1") -> None:
        super().__init__(parent)
        self._edge = edge
        self._surface = surface
        self._connect_theme()

    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        p.fillRect(self.rect(), qcolor(getattr(t.surface, self._surface)))
        p.setPen(QPen(qcolor(t.border.subtle), 1))
        r = self.rect()
        if self._edge == "left":
            p.drawLine(0, 0, 0, r.height())
        elif self._edge == "right":
            p.drawLine(r.width() - 1, 0, r.width() - 1, r.height())
        elif self._edge == "bottom":
            p.drawLine(0, r.height() - 1, r.width(), r.height() - 1)
        elif self._edge == "top":
            p.drawLine(0, 0, r.width(), 0)


def scroll(inner: QWidget, horizontal: bool = False) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setWidget(inner)
    sa.setFrameShape(QFrame.NoFrame)
    if not horizontal:
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    return sa


class PageHeader(QWidget):
    """Overline + title + subtitle, with a trailing action row."""

    def __init__(self, overline: str = "", title: str = "", subtitle: str = "",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE.lg)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.overline = label(overline.upper(), "overline")
        self.overline.setVisible(bool(overline))
        self.title = label(title, "h1")
        self.title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.subtitle = label(subtitle, tone="secondary")
        self.subtitle.setWordWrap(True)
        self.subtitle.setVisible(bool(subtitle))
        col.addWidget(self.overline)
        col.addWidget(self.title)
        col.addWidget(self.subtitle)
        lay.addLayout(col, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(SPACE.sm)
        lay.addLayout(self.actions)

    def set_text(self, overline: str, title: str, subtitle: str = "") -> None:
        self.overline.setText(overline.upper())
        self.overline.setVisible(bool(overline))
        self.title.setText(title)
        self.subtitle.setText(subtitle)
        self.subtitle.setVisible(bool(subtitle))


class CardGrid(QWidget):
    """Responsive grid: equal-width columns of at least ``min_col`` px."""

    def __init__(self, min_col: int = 270, parent: Optional[QWidget] = None,
                 max_cols: int = 8, spacing: int = SPACE.lg) -> None:
        super().__init__(parent)
        self._min = min_col
        self._max_cols = max(1, min(8, max_cols))
        self._sp = spacing
        self._items: List[QWidget] = []
        self._cols = 0
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(spacing)
        self._grid.setVerticalSpacing(spacing)
        self._grid.setAlignment(Qt.AlignTop)

    def set_widgets(self, widgets: Sequence[QWidget]) -> None:
        for w in self._items:
            self._grid.removeWidget(w)
            w.setParent(None)
            w.deleteLater()
        self._items = list(widgets)
        self._cols = 0
        self._relayout(force=True)

    def adopt(self, widgets: Sequence[QWidget]) -> None:
        """Lay out long-lived widgets (never deleted, unlike set_widgets)."""
        for w in self._items:
            self._grid.removeWidget(w)
        self._items = list(widgets)
        self._cols = 0
        self._relayout(force=True)

    def columns(self) -> int:
        return self._cols

    def widgets(self) -> List[QWidget]:
        return list(self._items)

    def minimumSizeHint(self):
        # one column is always possible: the grid must never force its width
        hint = super().minimumSizeHint()
        if self._items:
            hint.setWidth(max(it.minimumSizeHint().width() for it in self._items))
        return hint

    def _relayout(self, force: bool = False) -> None:
        w = max(1, self.width())
        cols = max(1, min(self._max_cols, (w + self._sp) // (self._min + self._sp)))
        if cols == self._cols and not force:
            return
        self._cols = cols
        for it in self._items:
            self._grid.removeWidget(it)
        for i, it in enumerate(self._items):
            self._grid.addWidget(it, i // cols, i % cols)
        for c in range(8):
            self._grid.setColumnStretch(c, 1 if c < cols else 0)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._relayout()


class SelectableCard(Card):
    """Hoverable Card with a selected state (accent border) and double-click."""

    double_clicked = Signal()

    def __init__(self, title=None, subtitle=None, parent=None) -> None:
        super().__init__(title, subtitle, elevation=1, hoverable=True, parent=parent)
        self._selected = False
        self._sel = 0.0
        self._sel_anim = None
        self.setFocusPolicy(Qt.StrongFocus)

    def is_selected(self) -> bool:
        return self._selected

    def set_selected(self, on: bool) -> None:
        if on == self._selected:
            return
        self._selected = on
        stop(self._sel_anim)
        self._sel_anim = animate_value(self, self._sel, 1.0 if on else 0.0, MOTION.fast,
                                       self._set_sel)

    def _set_sel(self, v) -> None:
        self._sel = float(v)
        self.update()

    def mouseDoubleClickEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(e)

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.double_clicked.emit()
        elif e.key() == Qt.Key_Space:
            self.clicked.emit()
        else:
            super().keyPressEvent(e)

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self._sel > 0.001:
            c = qcolor(t.accent.base)
            c.setAlphaF(self._sel)
            p.setPen(QPen(c, 1.6))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), RADII.lg, RADII.lg)
        if self.hasFocus():
            p.setPen(QPen(qcolor(t.border.focus), 1.2, Qt.DashLine))
            p.drawRoundedRect(r.adjusted(2, 2, -2, -2), RADII.lg - 2, RADII.lg - 2)


class ThumbStrip(ThemeAware, QWidget):
    """Row of up to ``n`` rounded thumbnails (skeleton tint until loaded)."""

    def __init__(self, n: int = 3, height: int = 88, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._n = n
        self._imgs: List[QPixmap] = []
        self._extra = 0
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._connect_theme()

    def set_images(self, images: Sequence[QImage], total: int = 0) -> None:
        self._imgs = [QPixmap.fromImage(i) for i in images[: self._n] if i is not None and not i.isNull()]
        self._extra = max(0, total - len(self._imgs))
        self.update()

    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        gap = 6
        n = self._n
        w = (self.width() - gap * (n - 1)) / n
        h = self.height()
        for i in range(n):
            r = QRectF(i * (w + gap), 0, w, h)
            path = QPainterPath()
            path.addRoundedRect(r, RADII.md, RADII.md)
            p.fillPath(path, qcolor(t.surface.surface3 if i < len(self._imgs) else t.surface.surface2))
            if i < len(self._imgs):
                pm = self._imgs[i]
                # cover-fit
                s = max(r.width() / pm.width(), r.height() / pm.height())
                sw, sh = r.width() / s, r.height() / s
                src = QRectF((pm.width() - sw) / 2, (pm.height() - sh) / 2, sw, sh)
                p.save()
                p.setClipPath(path)
                p.drawPixmap(r, pm, src)
                p.restore()
            elif i == 0:
                ic = icons.pixmap("image", 22, t.text.disabled)
                p.drawPixmap(int(r.center().x() - 11), int(r.center().y() - 11), ic)
            if i == n - 1 and self._extra > 0 and i < len(self._imgs):
                shade = qcolor(t.surface.bg)
                shade.setAlphaF(0.62)
                p.fillPath(path, shade)
                p.setPen(qcolor(t.text.primary))
                p.setFont(ui_font(TYPE.body_strong))
                p.drawText(r, Qt.AlignCenter, f"+{self._extra}")
            p.setPen(QPen(qcolor(t.border.subtle), 1))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)


class MetricCard(StatCard):
    """StatCard that can also show a textual 'not available' value."""

    def set_label(self, text: str) -> None:
        self._label.setText(text.upper())

    def set_text(self, text: str) -> None:
        """Show a textual value (e.g. a date) instead of a number."""
        stop(self._count_anim)
        self.set_unit("")
        self._value.setText(text)
        self._text_shown = True

    def set_metric(self, value: Optional[float], unit: str = "", decimals: Optional[int] = None,
                   animate: bool = True) -> None:
        if decimals is not None:
            self._decimals = decimals
        self.set_unit(unit)
        if value is None:
            stop(self._count_anim)
            self._target = 0.0
            self._shown = 0.0
            self._value.setText("—")
            self._text_shown = True
            return
        if getattr(self, "_text_shown", False):
            # a text/"—" value was showing: redraw the number even when the
            # count-up has nothing to animate (same value as before)
            self._text_shown = False
            self._render(self._shown)
        self.set_value(float(value), animate=animate)


class ConfirmBar(Card):
    """Inline, non-blocking confirmation (e.g. 'Move lot to trash?')."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent=parent, elevation=2)
        self._cb: Optional[Callable[[], None]] = None
        row = QHBoxLayout()
        row.setSpacing(SPACE.md)
        self.icon = QLabel()
        self.icon.setFixedSize(22, 22)
        row.addWidget(self.icon, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title = label("", "body_strong")
        self.body = label("", tone="secondary")
        self.body.setWordWrap(True)
        col.addWidget(self.title)
        col.addWidget(self.body)
        row.addLayout(col, 1)
        self.cancel_btn = AnimatedButton("Cancel", None, "ghost")
        self.cancel_btn.setToolTip("Keep everything as it is")
        self.ok_btn = AnimatedButton("Confirm", "delete", "danger")
        self.cancel_btn.clicked.connect(self.hide_bar)
        self.ok_btn.clicked.connect(self._confirm)
        row.addWidget(self.cancel_btn, 0, Qt.AlignVCenter)
        row.addWidget(self.ok_btn, 0, Qt.AlignVCenter)
        self.body_layout().addLayout(row)
        self.hide()

    def ask(self, title: str, body: str, ok_text: str, on_ok: Callable[[], None],
            severity: str = "danger", ok_icon: str = "delete") -> None:
        t = tokens()
        self.title.setText(title)
        self.body.setText(body)
        self.ok_btn.setText(ok_text)
        self.ok_btn.set_variant("danger" if severity == "danger" else "primary")
        self.ok_btn.set_icon_name(ok_icon)
        self.ok_btn.setToolTip(ok_text)
        self.icon.setPixmap(icons.pixmap("warning", 22, t.semantic(
            "danger" if severity == "danger" else "warning").fg))
        self._cb = on_ok
        self.show()
        self.ok_btn.setFocus()

    def hide_bar(self) -> None:
        self._cb = None
        self.hide()

    def _confirm(self) -> None:
        cb = self._cb
        self.hide_bar()
        if cb:
            cb()


def section_label(text: str) -> QLabel:
    return label(text.upper(), "overline")


def hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    return f
