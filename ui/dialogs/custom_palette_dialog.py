"""
Custom-palette dialog (UX-15) — pick 3 colours (hex text or the OS colour
picker) and a name; the report designer derives the rest of a full palette
from them (``reports.charts.derive_custom_palette``) and saves the 3 base
colours locally (``AppSettings.custom_palettes``) so the palette is offered
next to the 4 built-ins on every report from then on.

Layout: title · hint · Name field · 3x (swatch · hex field · "Pick..."
colour-wheel button) · inline error · Cancel / Save palette. Non-blocking
(``open()``); ``submitted(name, [hex, hex, hex])`` carries the result —
callers derive the full palette and persist it.
"""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QDialog, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from reports.charts import normalize_hex
from ui.design.tokens import SPACE
from ui.pages.report_widgets import Swatch
from ui.widgets import AnimatedButton, label

DEFAULT_COLORS = ["#2E5FA3", "#48B07A", "#DC3278"]
FIELD_LABELS = ("Colour 1 — area bars & header", "Colour 2 — diameter bars & accent",
                "Colour 3 — normal-fit line")


class _ColorField(QWidget):
    """Live swatch + hex text field + "Pick..." button (QColorDialog)."""

    def __init__(self, initial: str, parent=None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE.xs)
        self.swatch = Swatch(initial, size=20)
        self.edit = QLineEdit(initial)
        self.edit.setMaxLength(7)
        self.edit.setPlaceholderText("#RRGGBB")
        self.edit.setToolTip("6-digit hex colour, e.g. #2E5FA3")
        self.btn = AnimatedButton("Pick...", None, "ghost", "sm")
        self.btn.setToolTip("Choose from the colour wheel")
        row.addWidget(self.swatch, 0)
        row.addWidget(self.edit, 1)
        row.addWidget(self.btn, 0)
        self.edit.textChanged.connect(self._on_text)
        self.btn.clicked.connect(self._pick)

    def _on_text(self, text: str) -> None:
        hexv = normalize_hex(text)
        if hexv:
            self.swatch.color = QColor(hexv)
            self.swatch.update()

    def _pick(self) -> None:
        start = normalize_hex(self.edit.text()) or "#808080"
        c = QColorDialog.getColor(QColor(start), self, "Choose a colour")
        if c.isValid():
            self.edit.setText(c.name().upper())

    def hex(self) -> Optional[str]:
        return normalize_hex(self.edit.text())


class CustomPaletteDialog(QDialog):
    """Modal: name + 3 colour pickers -> ``submitted(name, [c1, c2, c3])``."""

    submitted = Signal(str, list)

    def __init__(self, initial_colors: Optional[List[str]] = None,
                 initial_name: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New custom palette")
        self.setMinimumWidth(440)
        colors = list(initial_colors or DEFAULT_COLORS)
        v = QVBoxLayout(self)
        v.setContentsMargins(SPACE.xl, SPACE.lg, SPACE.xl, SPACE.lg)
        v.setSpacing(SPACE.md)
        v.addWidget(label("New custom palette", "h2"))
        hint = label("Pick 3 colours — type a hex code or use the colour picker. Headers, "
                     "accents and tab colours are derived automatically so the report reads as "
                     "one system.", tone="secondary")
        hint.setWordWrap(True)
        v.addWidget(hint)
        v.addWidget(label("Name", tone="secondary"))
        self.name = QLineEdit(initial_name)
        self.name.setPlaceholderText('Palette name, e.g. "Corporate blue"')
        self.name.setAccessibleName("Palette name")
        v.addWidget(self.name)
        self.fields: List[_ColorField] = []
        for lbl, c in zip(FIELD_LABELS, colors):
            v.addWidget(label(lbl, tone="secondary"))
            f = _ColorField(c)
            self.fields.append(f)
            v.addWidget(f)
        self.error = label("", tone="danger")
        self.error.hide()
        v.addWidget(self.error)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_cancel = AnimatedButton("Cancel", None, "ghost")
        self.btn_cancel.setToolTip("Discard (Esc)")
        self.btn_ok = AnimatedButton("Save palette", "save", "primary")
        self.btn_ok.setToolTip("Save and apply to this report")
        self.btn_ok.setDefault(True)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_ok)
        v.addLayout(row)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self._submit)
        self.name.setFocus()
        self.name.selectAll()

    def _submit(self) -> None:
        name = self.name.text().strip() or "Custom"
        colors = [f.hex() for f in self.fields]
        if any(c is None for c in colors):
            self.error.setText("Every colour must be a valid #RRGGBB hex code.")
            self.error.show()
            return
        self.submitted.emit(name, colors)
        self.accept()


__all__ = ["CustomPaletteDialog", "DEFAULT_COLORS"]
