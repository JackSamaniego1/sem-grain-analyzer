"""Vertical image filmstrip with per-image status and inline progress."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFontMetricsF, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout, QScrollArea, QSizePolicy, QVBoxLayout, QWidget, QFrame,
)

from ui.design import icons
from ui.design.theme import ui_font
from ui.design.tokens import MOTION, RADII, SPACE, TYPE, TypeStyle
from ui.format import fmt_int
from ui.widgets import IconButton, label
from ui.widgets._base import ThemeAware, animate_value, lerp_color, qcolor, stop, tokens
from ui.workers import IMAGE_EXTS

_STATUS = {
    "pending": ("neutral", "Not analysed"),
    "queued": ("warning", "Queued"),
    "running": ("info", "Analysing"),
    "done": ("success", ""),
    "error": ("danger", "Error"),
}


def status_text(im) -> tuple:
    kind, text = _STATUS.get(im.status, ("neutral", im.status))
    if im.status == "done" and im.result is not None:
        text = f"{fmt_int(im.result.grain_count)} grains"
    elif im.status == "running":
        text = f"Analysing {im.progress} %"
    return kind, text


class FilmItem(ThemeAware, QWidget):
    clicked = Signal(object)

    def __init__(self, im, index: int, compact: bool = False,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.im = im
        self.index = index
        self._compact = compact
        self._selected = False
        self._hover = 0.0
        self._anim = None
        self._pm: Optional[QPixmap] = None
        self._pm_key = None
        self.setAttribute(Qt.WA_Hover)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(128 if compact else 150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName(f"Image {index + 1}: {im.filename}")
        self.refresh()
        self._connect_theme()

    def refresh(self) -> None:
        th = self.im.thumb
        key = th.cacheKey() if th is not None and not th.isNull() else None
        if key != self._pm_key:
            self._pm = QPixmap.fromImage(th) if key is not None else None
            self._pm_key = key
        kind, text = status_text(self.im)
        tip = f"{self.im.filename}\n{text}"
        if self.im.status == "error" and self.im.message:
            tip += f"\n{self.im.message.splitlines()[0]}"
        self.setToolTip(tip)
        self.update()

    def set_selected(self, on: bool) -> None:
        self._selected = on
        self.update()

    def enterEvent(self, e) -> None:
        stop(self._anim)
        self._anim = animate_value(self, self._hover, 1.0, MOTION.fast, self._set_h)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        stop(self._anim)
        self._anim = animate_value(self, self._hover, 0.0, MOTION.base, self._set_h)
        super().leaveEvent(e)

    def _set_h(self, v) -> None:
        self._hover = float(v)
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.im.uid)

    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        base = qcolor(t.surface.surface1)
        bg = lerp_color(base, qcolor(t.surface.surface2), self._hover)
        if self._selected:
            bg = qcolor(t.accent.subtle)
        p.setPen(QPen(qcolor(t.accent.base if self._selected else t.border.subtle),
                      1.4 if self._selected else 1))
        p.setBrush(bg)
        p.drawRoundedRect(r, RADII.lg, RADII.lg)

        pad = 8
        th_h = r.height() - 46
        tr = QRectF(r.left() + pad, r.top() + pad, r.width() - 2 * pad, th_h - pad)
        path = QPainterPath()
        path.addRoundedRect(tr, RADII.md, RADII.md)
        p.fillPath(path, qcolor(t.surface.bg))
        if self._pm is not None:
            pm = self._pm
            s = min(tr.width() / pm.width(), tr.height() / pm.height())
            dw, dh = pm.width() * s, pm.height() * s
            dst = QRectF(tr.center().x() - dw / 2, tr.center().y() - dh / 2, dw, dh)
            p.save()
            p.setClipPath(path)
            p.drawPixmap(dst, pm, QRectF(pm.rect()))
            p.restore()
        # index chip
        f = ui_font(TypeStyle(10, 600, 14))
        p.setFont(f)
        chip = QRectF(tr.left() + 6, tr.top() + 6, 24, 16)
        cb = qcolor(t.surface.elevated)
        cb.setAlphaF(0.9)
        p.setPen(Qt.NoPen)
        p.setBrush(cb)
        p.drawRoundedRect(chip, 4, 4)
        p.setPen(qcolor(t.text.secondary))
        p.drawText(chip, Qt.AlignCenter, f"{self.index + 1:02d}")
        # progress along the thumbnail bottom
        if self.im.status == "running":
            bar = QRectF(tr.left(), tr.bottom() - 4, tr.width(), 4)
            p.setBrush(qcolor(t.surface.surface3))
            p.drawRoundedRect(bar, 2, 2)
            done = QRectF(bar.left(), bar.top(), bar.width() * max(0.03, self.im.progress / 100), 4)
            p.setBrush(qcolor(t.accent.base))
            p.drawRoundedRect(done, 2, 2)
        p.setPen(QPen(qcolor(t.border.subtle), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

        # text
        ty = tr.bottom() + 6
        fn = ui_font(TypeStyle(12, 600, 16))
        p.setFont(fn)
        fm = QFontMetricsF(fn)
        name = fm.elidedText(self.im.filename, Qt.ElideMiddle, tr.width())
        p.setPen(qcolor(t.text.primary))
        p.drawText(QRectF(tr.left(), ty, tr.width(), 16), Qt.AlignLeft | Qt.AlignVCenter, name)
        kind, text = status_text(self.im)
        sem = t.semantic(kind)
        fs = ui_font(TYPE.caption)
        p.setFont(fs)
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(sem.solid if kind != "neutral" else t.text.tertiary))
        p.drawEllipse(QRectF(tr.left() + 1, ty + 22, 7, 7))
        p.setPen(qcolor(sem.fg if kind != "neutral" else t.text.secondary))
        p.drawText(QRectF(tr.left() + 13, ty + 17, tr.width() - 13, 16),
                   Qt.AlignLeft | Qt.AlignVCenter, text)


class Filmstrip(QWidget):
    """Scrollable list of FilmItems; drop image files onto it to add them."""

    current_changed = Signal(object)   # uid
    files_dropped = Signal(list)
    add_requested = Signal()

    def __init__(self, compact: bool = False, title: str = "Images",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._compact = compact
        self._items: Dict[object, FilmItem] = {}
        self._order: List[object] = []
        self._current = None
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        v = QVBoxLayout(self)
        v.setContentsMargins(SPACE.md, SPACE.md, SPACE.sm, SPACE.md)
        v.setSpacing(SPACE.sm)
        head = QHBoxLayout()
        self.title = label(title.upper(), "overline")
        self.count = label("", "caption")
        head.addWidget(self.title)
        head.addWidget(self.count)
        head.addStretch(1)
        self.add_btn = IconButton("add", "Add images to this session (Ctrl+O). "
                                         "You can also drop files here.", size=26)
        self.add_btn.clicked.connect(self.add_requested)
        head.addWidget(self.add_btn)
        v.addLayout(head)
        self._inner = QWidget()
        self._list = QVBoxLayout(self._inner)
        self._list.setContentsMargins(0, 0, SPACE.xs, 0)
        self._list.setSpacing(SPACE.sm)
        self._list.addStretch(1)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setWidget(self._inner)
        v.addWidget(self._scroll, 1)
        self.hint = label("Drop SEM images here", "caption")
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setWordWrap(True)
        v.addWidget(self.hint)
        self.setToolTip("Images in this session. Click to view; Up/Down to step through.")

    def sizeHint(self) -> QSize:
        return QSize(180 if self._compact else 214, 600)

    def set_images(self, images) -> None:
        for it in self._items.values():
            self._list.removeWidget(it)
            it.deleteLater()
        self._items, self._order = {}, []
        for i, im in enumerate(images):
            it = FilmItem(im, i, self._compact)
            it.clicked.connect(self._on_click)
            self._list.insertWidget(self._list.count() - 1, it)
            self._items[im.uid] = it
            self._order.append(im.uid)
        self.count.setText(str(len(images)) if images else "")
        self.hint.setVisible(not images)
        if self._current in self._items:
            self._items[self._current].set_selected(True)

    def update_item(self, uid) -> None:
        it = self._items.get(uid)
        if it is not None:
            it.refresh()

    def refresh_all(self) -> None:
        for it in self._items.values():
            it.refresh()

    def set_current(self, uid) -> None:
        if self._current in self._items:
            self._items[self._current].set_selected(False)
        self._current = uid
        it = self._items.get(uid)
        if it is not None:
            it.set_selected(True)
            self._scroll.ensureWidgetVisible(it, 0, 12)

    def item(self, uid) -> Optional[FilmItem]:
        return self._items.get(uid)

    def _on_click(self, uid) -> None:
        self.setFocus()
        self.current_changed.emit(uid)

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key_Up, Qt.Key_Down) and self._order:
            i = self._order.index(self._current) if self._current in self._order else -1
            i = max(0, min(len(self._order) - 1, i + (1 if e.key() == Qt.Key_Down else -1)))
            self.current_changed.emit(self._order[i])
            return
        super().keyPressEvent(e)

    # drag & drop
    @staticmethod
    def _paths(md) -> List[str]:
        out = []
        for u in md.urls():
            if u.isLocalFile():
                p = Path(u.toLocalFile())
                if p.suffix.lower() in IMAGE_EXTS:
                    out.append(str(p))
        return out

    def dragEnterEvent(self, e) -> None:
        if self._paths(e.mimeData()):
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        paths = self._paths(e.mimeData())
        if paths:
            self.files_dropped.emit(paths)
            e.acceptProposedAction()
