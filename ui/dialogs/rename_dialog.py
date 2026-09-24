"""
Rename prompt (UI-09) — one text field, used from the Projects browser
context menu for images, sessions and folders shown as cards.

Layout: title "Rename <thing>" · hint line · text field (pre-filled, text
selected) · inline error · Cancel / Rename (primary). Non-blocking
(``open()``); ``submitted(str)`` carries the new name.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLineEdit, QVBoxLayout

from ui.design.tokens import SPACE
from ui.widgets import AnimatedButton, label


class RenameDialog(QDialog):
    submitted = Signal(str)

    def __init__(self, what: str, current: str, hint: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Rename {what}")
        self.setMinimumWidth(440)
        v = QVBoxLayout(self)
        v.setContentsMargins(SPACE.xl, SPACE.lg, SPACE.xl, SPACE.lg)
        v.setSpacing(SPACE.md)
        v.addWidget(label(f"Rename {what}", "h2"))
        if hint:
            h = label(hint, tone="secondary")
            h.setWordWrap(True)
            v.addWidget(h)
        self.edit = QLineEdit(current)
        self.edit.setAccessibleName("New name")
        self.edit.setToolTip("Type the new name and press Enter")
        self.edit.selectAll()
        v.addWidget(self.edit)
        self.error = label("", tone="danger")
        self.error.hide()
        v.addWidget(self.error)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_cancel = AnimatedButton("Cancel", None, "ghost")
        self.btn_cancel.setToolTip("Keep the current name (Esc)")
        self.btn_ok = AnimatedButton("Rename", "edit", "primary")
        self.btn_ok.setToolTip("Apply the new name (Enter)")
        self.btn_ok.setDefault(True)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_ok)
        v.addLayout(row)
        self._current = current
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self._submit)
        self.edit.returnPressed.connect(self._submit)
        self.edit.setFocus()

    def _submit(self) -> None:
        new = self.edit.text().strip()
        if not new:
            self.error.setText("The name cannot be empty.")
            self.error.show()
            return
        if new != self._current:
            self.submitted.emit(new)
        self.accept()


__all__ = ["RenameDialog"]
