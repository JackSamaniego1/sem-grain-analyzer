"""
"Move to…" destination picker (UI-09).

Layout
  header   "Move 3 images to another Lot" + one line on what moves with them
  filter   SearchBox (filters the list as you type)
  list     one row per valid destination, full path caption
           ("Job # 24-117 › Part Number 55-A › Lot B"), level icon;
           only folders of the level directly above the items are offered
           (images → other lots, lots → other part numbers, …) and never
           the folder the items are already in
  empty    "No other Lot to move to" (create one first)
  footer   Cancel · Move here (primary, disabled until a row is chosen)

Non-blocking: shown with ``open()``; ``destination_chosen(Path)`` fires on
Move here / double-click / Enter.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QListWidget, QListWidgetItem, QVBoxLayout

from ui import hierarchy_ui as hui
from ui.app_state import NodeRef, node_chain, node_display_name
from ui.design import icons
from ui.design.tokens import SPACE
from ui.widgets import AnimatedButton, EmptyState, FadeStackedWidget, SearchBox, label
from ui.widgets.selection_bar import MOVE_ICON


def destination_caption(root: Path, kind: str, path: Path, profile=None) -> str:
    """"Job # 24-117 › Part Number 55-A › Lot B" for a destination folder."""
    chain = node_chain(Path(root), NodeRef(kind, Path(path)))[1:]
    return " › ".join(node_display_name(n, profile, crumb=True) for n in chain) or Path(path).name


class MoveToDialog(QDialog):
    destination_chosen = Signal(object)        # Path

    def __init__(self, destinations: Sequence[Path], dest_kind: str, what: str,
                 root: Path, profile=None, parent=None) -> None:
        super().__init__(parent)
        self.dest_kind = dest_kind
        self.profile = profile
        word = hui.kind_label(profile, dest_kind)
        self.setWindowTitle(f"Move {what}")
        self.setMinimumSize(560, 460)
        v = QVBoxLayout(self)
        v.setContentsMargins(SPACE.xl, SPACE.lg, SPACE.xl, SPACE.lg)
        v.setSpacing(SPACE.md)
        self.title_lbl = label(f"Move {what} to another {word}", "h2")
        v.addWidget(self.title_lbl)
        intro = label(f"Only {hui.plural(word)} are offered — items stay at the same level. "
                      "Images, results and calibration move with them; nothing is copied or "
                      "deleted, and Undo is offered afterwards.", tone="secondary")
        intro.setWordWrap(True)
        v.addWidget(intro)
        self.filter = SearchBox(f"Filter {hui.plural(word).lower()}…", debounce_ms=0)
        self.filter.setToolTip("Type part of a name to narrow the list")
        self.filter.textChanged.connect(self._apply_filter)
        v.addWidget(self.filter)
        self.stack = FadeStackedWidget()
        self.list = QListWidget()
        self.list.setUniformItemSizes(True)
        self.list.setAlternatingRowColors(True)
        self.list.setToolTip(f"Choose the {word} to move into (double-click to move)")
        self.list.setAccessibleName(f"Destination {word}")
        ic = icons.icon(hui.KIND_ICON.get(dest_kind, "open"))
        for p in destinations:
            it = QListWidgetItem(ic, destination_caption(root, dest_kind, Path(p), profile))
            it.setData(Qt.UserRole, str(p))
            it.setToolTip(str(p))
            self.list.addItem(it)
        self.empty = EmptyState("open", f"No other {word} to move to",
                                f"Create another {word} first, then move the items there.")
        self.stack.addWidget(self.list)
        self.stack.addWidget(self.empty)
        self.stack.set_current_index(0 if destinations else 1)
        v.addWidget(self.stack, 1)
        foot = QHBoxLayout()
        foot.addStretch(1)
        self.btn_cancel = AnimatedButton("Cancel", None, "ghost")
        self.btn_cancel.setToolTip("Leave everything where it is (Esc)")
        self.btn_move = AnimatedButton("Move here", MOVE_ICON, "primary")
        self.btn_move.setToolTip(f"Move {what} into the chosen {word}")
        self.btn_move.setEnabled(False)
        self.btn_move.setDefault(True)
        foot.addWidget(self.btn_cancel)
        foot.addWidget(self.btn_move)
        v.addLayout(foot)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_move.clicked.connect(self._choose)
        self.list.itemSelectionChanged.connect(
            lambda: self.btn_move.setEnabled(self.selected() is not None))
        self.list.itemDoubleClicked.connect(lambda _it: self._choose())
        if destinations:
            self.list.setFocus()

    # ------------------------------------------------------------------
    def destinations(self) -> List[Path]:
        return [Path(self.list.item(i).data(Qt.UserRole)) for i in range(self.list.count())]

    def visible_destinations(self) -> List[Path]:
        return [Path(self.list.item(i).data(Qt.UserRole)) for i in range(self.list.count())
                if not self.list.item(i).isHidden()]

    def select(self, path: Path) -> None:
        for i in range(self.list.count()):
            if Path(self.list.item(i).data(Qt.UserRole)) == Path(path):
                self.list.setCurrentRow(i)
                return

    def selected(self) -> Optional[Path]:
        it = self.list.currentItem()
        if it is None or it.isHidden() or not it.isSelected():
            return None
        return Path(it.data(Qt.UserRole))

    def _apply_filter(self, text: str) -> None:
        t = text.strip().lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(t) and t not in it.text().lower())
        self.btn_move.setEnabled(self.selected() is not None)

    def _choose(self) -> None:
        dest = self.selected()
        if dest is None:
            return
        self.destination_chosen.emit(dest)
        self.accept()


__all__ = ["MoveToDialog", "destination_caption"]
