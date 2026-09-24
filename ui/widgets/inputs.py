"""SearchBox: line edit with search icon, clear button and debounced ``search_changed``."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QLineEdit, QWidget

from ui.design import icons
from ui.design.theme import theme_manager
from ui.widgets._base import tokens


class SearchBox(QLineEdit):
    """Search field; emits ``search_changed(text)`` after ``debounce_ms`` idle and ``submitted`` on Enter."""

    search_changed = Signal(str)
    submitted = Signal(str)

    def __init__(self, placeholder: str = "Search…", debounce_ms: int = 250,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setProperty("role", "search")
        self.setAccessibleName(placeholder.rstrip("…."))
        self.setClearButtonEnabled(False)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(debounce_ms)
        self._timer.timeout.connect(lambda: self.search_changed.emit(self.text()))
        self._lead = self.addAction(icons.icon("search"), QLineEdit.LeadingPosition)
        self._clear = self.addAction(icons.icon("clear"), QLineEdit.TrailingPosition)
        self._clear.setToolTip("Clear search")
        self._clear.triggered.connect(self.clear_search)
        self._clear.setVisible(False)
        self.textChanged.connect(self._on_text)
        self.returnPressed.connect(lambda: self.submitted.emit(self.text()))
        theme_manager().theme_changed.connect(self._refresh_icons)
        self.setMinimumWidth(220)

    def debounce_ms(self) -> int:
        """Debounce interval."""
        return self._timer.interval()

    def clear_search(self) -> None:
        """Clear text and emit ``search_changed('')`` immediately."""
        self.clear()
        self._timer.stop()
        self.search_changed.emit("")

    def _on_text(self, text: str) -> None:
        self._clear.setVisible(bool(text))
        self._timer.start()

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape and self.text():
            self.clear_search()
            return
        super().keyPressEvent(e)

    def _refresh_icons(self, _mode: str = "") -> None:
        t = tokens()
        self._lead.setIcon(icons.icon("search", t.text.tertiary))
        self._clear.setIcon(icons.icon("clear", t.text.tertiary, t.text.secondary))

    def showEvent(self, e) -> None:
        self._refresh_icons()
        super().showEvent(e)
