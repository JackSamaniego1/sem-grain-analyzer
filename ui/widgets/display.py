"""Static display helpers: EmptyState, Divider, KeyValueList."""
from __future__ import annotations

from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple, Union

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ui.design import icons
from ui.design.theme import ui_font
from ui.design.tokens import SPACE, TYPE
from ui.widgets._base import ThemeAware, qcolor, tokens
from ui.widgets.buttons import AnimatedButton
from ui.widgets.cards import label

Items = Union[Mapping[str, str], Iterable[Tuple[str, str]]]


class _IconDisc(ThemeAware, QWidget):
    """Large icon on a soft disc (internal)."""

    def __init__(self, icon: str, size: int = 72, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.icon_name = icon
        self.setFixedSize(size, size)
        self._connect_theme()

    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(qcolor(t.border.subtle), 1))
        p.setBrush(qcolor(t.surface.surface2))
        p.drawEllipse(r)
        inner = r.adjusted(8, 8, -8, -8)
        p.setPen(QPen(qcolor(t.accent.subtle_border), 1))
        p.setBrush(qcolor(t.accent.subtle))
        p.drawEllipse(inner)
        isz = int(self.width() * 0.4)
        ir = QRectF(0, 0, isz, isz)
        ir.moveCenter(r.center())
        icons.icon(self.icon_name, t.accent.text).paint(p, ir.toRect())


class EmptyState(QWidget):
    """Centred icon, title, body and optional primary action; emits ``action_triggered``."""

    action_triggered = Signal()

    def __init__(self, icon: str, title: str, body: str = "", action_text: Optional[str] = None,
                 action_icon: Optional[str] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACE.xl, SPACE.xl, SPACE.xl, SPACE.xl)
        lay.setSpacing(SPACE.sm)
        lay.addStretch(1)
        self.disc = _IconDisc(icon, parent=self)
        lay.addWidget(self.disc, 0, Qt.AlignHCenter)
        lay.addSpacing(SPACE.sm)
        self.title_label = label(title, "h2")
        self.title_label.setAlignment(Qt.AlignHCenter)
        lay.addWidget(self.title_label, 0, Qt.AlignHCenter)
        self.body_label = label(body, tone="secondary")
        self.body_label.setWordWrap(True)
        self.body_label.setAlignment(Qt.AlignHCenter)
        self.body_label.setMaximumWidth(380)
        self.body_label.setVisible(bool(body))
        body_row = QHBoxLayout()  # no alignment flag -> word-wrap heightForWidth works
        body_row.addStretch(1)
        body_row.addWidget(self.body_label, 100)
        body_row.addStretch(1)
        lay.addLayout(body_row)
        if action_text:
            lay.addSpacing(SPACE.md)
            self.action_button = AnimatedButton(action_text, action_icon, "primary")
            self.action_button.clicked.connect(self.action_triggered)
            lay.addWidget(self.action_button, 0, Qt.AlignHCenter)
        else:
            self.action_button = None
        lay.addStretch(1)


class Divider(ThemeAware, QWidget):
    """1 px hairline (horizontal or vertical) with an optional overline label."""

    def __init__(self, orientation: Qt.Orientation = Qt.Horizontal, text: str = "",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._orientation = orientation
        self._text = text.upper()
        if orientation == Qt.Horizontal:
            self.setFixedHeight(18 if text else 9)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        else:
            self.setFixedWidth(9)
            self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self._connect_theme()

    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        pen = QPen(qcolor(t.border.subtle), 1)
        if self._orientation == Qt.Vertical:
            p.setPen(pen)
            x = self.width() // 2
            p.drawLine(x, 0, x, self.height())
            return
        y = self.height() // 2
        x0 = 0
        if self._text:
            f = ui_font(TYPE.overline)
            p.setFont(f)
            p.setPen(qcolor(t.text.tertiary))
            w = p.fontMetrics().horizontalAdvance(self._text)
            p.drawText(QRectF(0, 0, w + 2, self.height()), Qt.AlignVCenter | Qt.AlignLeft, self._text)
            x0 = w + SPACE.sm
        p.setPen(pen)
        p.drawLine(x0, y, self.width(), y)


class KeyValueList(QWidget):
    """Two-column metadata list (key in secondary tone, value selectable; optional mono values)."""

    def __init__(self, items: Optional[Items] = None, mono_keys: Sequence[str] = (),
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(SPACE.lg)
        self._grid.setVerticalSpacing(SPACE.sm)
        self._grid.setColumnStretch(1, 1)
        self._values: Dict[str, QLabel] = {}
        self._mono = set(mono_keys)
        if items:
            self.set_items(items)

    def set_items(self, items: Items) -> None:
        """Replace all rows."""
        while self._grid.count():
            w = self._grid.takeAt(0).widget()
            if w is not None:
                w.hide()  # deleteLater is deferred; never paint stale rows
                w.deleteLater()
        self._values.clear()
        pairs = list(items.items()) if isinstance(items, Mapping) else list(items)
        for row, (k, v) in enumerate(pairs):
            kl = label(k, tone="secondary")
            vl = label(str(v), "mono" if k in self._mono else None)
            vl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            vl.setWordWrap(True)
            self._grid.addWidget(kl, row, 0, Qt.AlignTop | Qt.AlignLeft)
            self._grid.addWidget(vl, row, 1, Qt.AlignTop)
            self._values[k] = vl

    def set_value(self, key: str, value: str) -> None:
        """Update one value (adds the row if missing)."""
        if key in self._values:
            self._values[key].setText(str(value))
        else:
            pairs = [(k, l.text()) for k, l in self._values.items()] + [(key, str(value))]
            self.set_items(pairs)

    def value(self, key: str) -> str:
        """Displayed value for a key."""
        return self._values[key].text()
