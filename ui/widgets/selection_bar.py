"""SelectionBar — contextual action bar shown while items are selected (UI-09).

Layout: [check] "N selected" · Select all ····· Compare · Load into analyzer ·
        Move to… · Delete · Clear
(Compare -- INN-43 -- is shown only when two or more lots are selected;
"Load into analyzer" -- UX-09 -- for jobs / parts / lots / sessions.)
Slides open (animated height, 200 ms ease-out) when the first item is
selected and collapses when the selection is cleared. The Delete button is
the danger variant so deleting is always visible, never hidden in a menu.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from ui.design import icons
from ui.design.tokens import MOTION, SPACE
from ui.widgets._base import animate_value, stop, tokens
from ui.widgets.buttons import AnimatedButton
from ui.widgets.cards import Card, label

MOVE_ICON = "mdi6.folder-move-outline"
COMPARE_ICON = "mdi6.compare-horizontal"


class SelectionBar(Card):
    """Count + bulk actions for the current selection."""

    delete_requested = Signal()
    move_requested = Signal()
    clear_requested = Signal()
    select_all_requested = Signal()
    compare_requested = Signal()
    load_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent=parent, elevation=2)
        self._count = 0
        self._anim = None
        self._open = False
        self.layout().setContentsMargins(SPACE.lg, SPACE.sm, SPACE.md, SPACE.sm)
        row = QHBoxLayout()
        row.setSpacing(SPACE.sm)
        self.icon = QLabel()
        self.icon.setFixedSize(20, 20)
        row.addWidget(self.icon, 0, Qt.AlignVCenter)
        self.count_label = label("", "body_strong")
        self.count_label.setAccessibleName("Selected items")
        row.addWidget(self.count_label, 0, Qt.AlignVCenter)
        self.select_all_btn = AnimatedButton("Select all", "mdi6.select-all", "ghost", "sm")
        self.select_all_btn.setToolTip("Select every item shown below (Ctrl+A)")
        row.addWidget(self.select_all_btn, 0, Qt.AlignVCenter)
        row.addStretch(1)
        self.compare_btn = AnimatedButton("Compare", COMPARE_ICON, "primary", "sm")
        self.compare_btn.setToolTip("Compare the selected lots: grain-size differences and "
                                    "equivalence to the baseline lot")
        self.compare_btn.hide()
        row.addWidget(self.compare_btn, 0, Qt.AlignVCenter)
        self.load_btn = AnimatedButton("Load into analyzer", "analyze", "secondary", "sm")
        self.load_btn.setToolTip("Open every image of the selected items in the analyzer, "
                                 "grouped by job, part and lot")
        self.load_btn.hide()
        row.addWidget(self.load_btn, 0, Qt.AlignVCenter)
        self.move_btn = AnimatedButton("Move to…", MOVE_ICON, "secondary", "sm")
        self.move_btn.setToolTip("Move the selected items to another folder of the same level")
        self.delete_btn = AnimatedButton("Delete", "delete", "danger", "sm")
        self.delete_btn.setToolTip("Move the selected items to the trash (Delete) — "
                                   "Undo on the next message restores them")
        self.clear_btn = AnimatedButton("Clear", "close", "ghost", "sm")
        self.clear_btn.setToolTip("Clear the selection (Esc)")
        for b in (self.move_btn, self.delete_btn, self.clear_btn):
            row.addWidget(b, 0, Qt.AlignVCenter)
        self.body_layout().addLayout(row)
        self.select_all_btn.clicked.connect(self.select_all_requested)
        self.move_btn.clicked.connect(self.move_requested)
        self.compare_btn.clicked.connect(self.compare_requested)
        self.load_btn.clicked.connect(self.load_requested)
        self.delete_btn.clicked.connect(self.delete_requested)
        self.clear_btn.clicked.connect(self.clear_requested)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setMaximumHeight(0)
        self.hide()
        self._refresh_icon()

    # -- API ----------------------------------------------------------------
    def count(self) -> int:
        return self._count

    def text(self) -> str:
        return self.count_label.text()

    def set_count(self, n: int, noun: str = "") -> None:
        """Show ``N selected`` (open the bar) or collapse it for 0."""
        self._count = n
        what = f" {noun}" if noun else ""
        self.count_label.setText(f"{n}{what} selected")
        self.delete_btn.setText("Delete" if n <= 1 else f"Delete {n}")
        if n > 0 and not self._open:
            self._slide(True)
        elif n == 0 and self._open:
            self._slide(False)

    def set_move_enabled(self, on: bool, reason: str = "") -> None:
        self.move_btn.setEnabled(on)
        self.move_btn.setToolTip("Move the selected items to another folder of the same level"
                                 if on else (reason or "These items cannot be moved"))

    def set_compare_visible(self, on: bool) -> None:
        """INN-43: offer Compare only for a selection of two or more lots."""
        self.compare_btn.setVisible(on)

    def set_load_visible(self, on: bool) -> None:
        """UX-09: offer "Load into analyzer" for jobs / parts / lots / sessions."""
        self.load_btn.setVisible(on)

    def is_open(self) -> bool:
        return self._open

    # -- internals ----------------------------------------------------------
    def _refresh_icon(self) -> None:
        self.icon.setPixmap(icons.pixmap("mdi6.checkbox-marked-outline", 20, tokens().accent.base))

    def _on_theme_changed(self) -> None:
        super()._on_theme_changed()
        self._refresh_icon()

    def _slide(self, opening: bool) -> None:
        self._open = opening
        stop(self._anim)
        target = self.sizeHint().height() if opening else 0
        if opening:
            self.show()

        def done():
            if not self._open:
                self.hide()
            else:
                self.setMaximumHeight(16777215)

        self._anim = animate_value(self, 0 if opening else self.height(), target, MOTION.base,
                                   lambda v: self.setMaximumHeight(int(v)), done)
