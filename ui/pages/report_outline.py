"""
Report outline (left column of the designer).

    ■ Cover
    ■ Overview table          [x]
    ■ Summary charts          [x]
    ▾ Images (3 of 4)
        ■ s014_t_1.png        [x]   ← drag to reorder, tick to include
        ...
    ■ Notes (text)            [x]   ← custom text: drag anywhere between
    ■ Methods                 [x]      Cover and Raw data
    ■ Raw data                [x]   ← always last

Colour chips match the workbook's tab colours.  Structural rules the
renderers depend on (Cover first, Raw data last, images stay in the Images
block) are enforced on drop; Alt+Up / Alt+Down move the selection by keyboard.
"""
from __future__ import annotations

import os
from typing import List, Optional, Tuple

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QTreeWidget, QTreeWidgetItem

from ui.pages.report_builder import SECTION_COLORS, SECTION_LABELS, outline_order
from ui.pages.report_widgets import swatch_icon

KEY_ROLE = Qt.UserRole + 1
FIXED_LOCKED = ("cover", "raw_data")   # cannot be dragged
NOT_TOGGLEABLE = ("cover",)            # always rendered by both renderers


class ReportOutline(QTreeWidget):
    selection_key_changed = Signal(object)        # ("section", id) | ("image", id) | None
    order_changed = Signal(list, list)            # top-level ids, image ids
    toggled = Signal(object, bool)                # key, checked

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setColumnCount(1)
        self.setIndentation(18)
        self.setIconSize(QSize(14, 14))
        self.setUniformRowHeights(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setAnimated(True)
        self.setToolTip("Report outline — drag images or text sections to reorder; "
                        "tick to include. Alt+Up / Alt+Down move the selection.")
        self._filling = False
        self.images_root: Optional[QTreeWidgetItem] = None
        self.itemChanged.connect(self._on_item_changed)
        self.currentItemChanged.connect(lambda cur, _prev: self._emit_selection(cur))

    # ------------------------------------------------------------------ fill
    def populate(self, model, keep_key=None) -> None:
        keep_key = keep_key or self.current_key()
        self._filling = True
        self.clear()
        secs = {s.id: s for s in model.sections}
        img_secs = {s.payload.get("image_id"): s for s in model.sections if s.type == "image"}
        images = sorted(model.images, key=lambda i: i.order)
        for sid in outline_order(model):
            if sid == "images":
                it = QTreeWidgetItem([self._images_label(model)])
                it.setData(0, KEY_ROLE, ("images", "images"))
                it.setIcon(0, swatch_icon(SECTION_COLORS["images"]))
                it.setFlags((it.flags() | Qt.ItemIsDropEnabled) & ~Qt.ItemIsDragEnabled
                            & ~Qt.ItemIsUserCheckable)
                it.setToolTip(0, "One sheet and one slide per included image (teal tabs)")
                self.addTopLevelItem(it)
                self.images_root = it
                for img in images:
                    ch = QTreeWidgetItem([img.display() or img.id])
                    ch.setToolTip(0, img.image_path)
                    ch.setData(0, KEY_ROLE, ("image", img.id))
                    ch.setIcon(0, swatch_icon(SECTION_COLORS["image"]))
                    ch.setFlags((ch.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
                                & ~Qt.ItemIsDropEnabled)
                    ch.setCheckState(0, Qt.Checked if img.include else Qt.Unchecked)
                    ch.setToolTip(0, "Tick to include this image; drag to change its position")
                    it.addChild(ch)
                it.setExpanded(True)
                continue
            s = secs.get(sid)
            if s is None:
                continue
            text = s.title if s.type == "custom_text" else SECTION_LABELS.get(s.type, s.title)
            it = QTreeWidgetItem([text or SECTION_LABELS.get(s.type, s.id)])
            it.setData(0, KEY_ROLE, ("section", s.id))
            it.setIcon(0, swatch_icon(SECTION_COLORS.get(s.type, "#888888")))
            flags = it.flags() & ~Qt.ItemIsDropEnabled
            if s.type in FIXED_LOCKED or s.type not in ("custom_text",):
                flags &= ~Qt.ItemIsDragEnabled
            else:
                flags |= Qt.ItemIsDragEnabled
            if s.type in NOT_TOGGLEABLE:
                flags &= ~Qt.ItemIsUserCheckable
                it.setToolTip(0, "Always included (title slide and the Overview sheet header)")
            else:
                flags |= Qt.ItemIsUserCheckable
                it.setCheckState(0, Qt.Checked if s.enabled else Qt.Unchecked)
                it.setToolTip(0, "Tick to include this section" +
                              (" — drag to move it" if s.type == "custom_text" else ""))
            if s.type == "lot_comparison":
                it.setToolTip(0, "Lot comparison — ΔG matrix and equivalence verdicts of each "
                                 "lot vs the baseline lot. Tick to include this section")
            it.setFlags(flags)
            self.addTopLevelItem(it)
        self._filling = False
        if keep_key:
            self.select_key(keep_key, emit=False)

    @staticmethod
    def _images_label(model) -> str:
        n = len(model.images)
        k = sum(1 for i in model.images if i.include)
        return f"Images  ({k} of {n})" if k != n else f"Images  ({n})"

    def refresh_labels(self, model) -> None:
        """Update texts/checks in place (no rebuild — keeps scroll/selection)."""
        self._filling = True
        secs = {s.id: s for s in model.sections}
        imgs = {i.id: i for i in model.images}
        for it in self._all_items():
            kind, key = it.data(0, KEY_ROLE)
            if kind == "images":
                it.setText(0, self._images_label(model))
            elif kind == "image" and key in imgs:
                it.setCheckState(0, Qt.Checked if imgs[key].include else Qt.Unchecked)
            elif kind == "section" and key in secs:
                s = secs[key]
                if s.type == "custom_text":
                    it.setText(0, s.title or "Text")
                if it.flags() & Qt.ItemIsUserCheckable:
                    it.setCheckState(0, Qt.Checked if s.enabled else Qt.Unchecked)
        self._filling = False

    # ------------------------------------------------------------------ query
    def _all_items(self) -> List[QTreeWidgetItem]:
        out = []
        for i in range(self.topLevelItemCount()):
            it = self.topLevelItem(i)
            out.append(it)
            for j in range(it.childCount()):
                out.append(it.child(j))
        return out

    def current_key(self):
        it = self.currentItem()
        if it is None:
            return None
        kind, key = it.data(0, KEY_ROLE)
        return ("section", "images") if kind == "images" else (kind, key)

    def item_for(self, key) -> Optional[QTreeWidgetItem]:
        for it in self._all_items():
            kind, k = it.data(0, KEY_ROLE)
            if (kind, k) == tuple(key) or (kind == "images" and tuple(key) == ("section", "images")):
                return it
        return None

    def select_key(self, key, emit: bool = True) -> None:
        it = self.item_for(key)
        if it is None:
            return
        if not emit:
            self.blockSignals(True)
        self.setCurrentItem(it)
        self.blockSignals(False)

    def order(self) -> Tuple[List[str], List[str]]:
        top, imgs = [], []
        for i in range(self.topLevelItemCount()):
            it = self.topLevelItem(i)
            kind, key = it.data(0, KEY_ROLE)
            top.append("images" if kind == "images" else key)
            if kind == "images":
                imgs = [it.child(j).data(0, KEY_ROLE)[1] for j in range(it.childCount())]
        return top, imgs

    # ------------------------------------------------------------------ signals
    def _emit_selection(self, cur) -> None:
        if self._filling:
            return
        self.selection_key_changed.emit(self.current_key() if cur is not None else None)

    def _on_item_changed(self, it, _col) -> None:
        if self._filling or not (it.flags() & Qt.ItemIsUserCheckable):
            return
        kind, key = it.data(0, KEY_ROLE)
        self.toggled.emit((kind, key), it.checkState(0) == Qt.Checked)

    # ------------------------------------------------------------------ drag & drop
    def _drop_allowed(self, dragged: QTreeWidgetItem, target: Optional[QTreeWidgetItem],
                      pos) -> bool:
        kind = dragged.data(0, KEY_ROLE)[0]
        if target is None:
            return False
        tkind, tkey = target.data(0, KEY_ROLE)
        if kind == "image":
            if tkind == "images":
                return pos == QAbstractItemView.OnItem
            return tkind == "image" and pos in (QAbstractItemView.AboveItem,
                                                QAbstractItemView.BelowItem)
        if kind == "section":
            if target.parent() is not None or pos == QAbstractItemView.OnItem:
                return False
            if tkey == "cover" and pos == QAbstractItemView.AboveItem:
                return False
            if tkey == "raw_data" and pos == QAbstractItemView.BelowItem:
                return False
            if tkey == "cover" and pos == QAbstractItemView.BelowItem:
                return True
            return True
        return False

    def dragMoveEvent(self, e) -> None:  # noqa: N802
        super().dragMoveEvent(e)
        dragged = self.currentItem()
        if dragged is None or not self._drop_allowed(dragged, self.itemAt(e.position().toPoint()),
                                                     self.dropIndicatorPosition()):
            e.ignore()

    def dropEvent(self, e) -> None:  # noqa: N802
        dragged = self.currentItem()
        target = self.itemAt(e.position().toPoint())
        if dragged is None or not self._drop_allowed(dragged, target, self.dropIndicatorPosition()):
            e.ignore()
            return
        key = self.current_key()
        super().dropEvent(e)
        self._after_move(key)

    def _after_move(self, key) -> None:
        top, imgs = self.order()
        self.order_changed.emit(top, imgs)
        if key:
            self.select_key(key, emit=False)

    def move_current(self, delta: int) -> bool:
        """Keyboard / button reordering within the allowed region."""
        it = self.currentItem()
        if it is None:
            return False
        kind, _key = it.data(0, KEY_ROLE)
        parent = it.parent()
        if kind == "image" and parent is not None:
            i = parent.indexOfChild(it)
            j = i + delta
            if not 0 <= j < parent.childCount():
                return False
            k = self.current_key()
            self._filling = True
            parent.takeChild(i)
            parent.insertChild(j, it)
            self._filling = False
            self._after_move(k)
            return True
        if kind == "section" and bool(it.flags() & Qt.ItemIsDragEnabled):
            i = self.indexOfTopLevelItem(it)
            j = i + delta
            if not 1 <= j <= self.topLevelItemCount() - 2:   # stay between Cover and Raw data
                return False
            k = self.current_key()
            self._filling = True
            self.takeTopLevelItem(i)
            self.insertTopLevelItem(j, it)
            self._filling = False
            self._after_move(k)
            return True
        return False

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.modifiers() & Qt.AltModifier and e.key() in (Qt.Key_Up, Qt.Key_Down):
            self.move_current(-1 if e.key() == Qt.Key_Up else 1)
            return
        super().keyPressEvent(e)


__all__ = ["ReportOutline", "KEY_ROLE"]
