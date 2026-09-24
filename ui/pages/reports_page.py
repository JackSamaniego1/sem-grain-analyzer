"""Reports page — Stage 1 placeholder.  The in-app report designer (REP-*)
lands in Stage 2; until then the legacy Excel export stays one click away."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ui.design.tokens import SPACE
from ui.pages.common import PageHeader
from ui.widgets import AnimatedButton, Card, EmptyState, label


class ReportsPage(QWidget):
    export_all_requested = Signal()
    export_current_requested = Signal()

    def __init__(self, state, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        v = QVBoxLayout(self)
        v.setContentsMargins(SPACE.xl, SPACE.lg, SPACE.xl, SPACE.xl)
        v.setSpacing(SPACE.lg)
        self.header = PageHeader("Reports", "Reports",
                                 "Export the open session's results")
        v.addWidget(self.header)
        card = Card()
        self.empty = EmptyState(
            "reports", "Report designer is coming in the next update",
            "You will be able to edit titles, captions and sections here and export a "
            "formatted Excel workbook and a PowerPoint deck. For now, export the session to "
            "Excel (one sheet per image, histograms and raw grain data).",
            "Export session to Excel", "excel")
        self.empty.action_triggered.connect(self.export_all_requested)
        row = QHBoxLayout()
        row.addStretch(1)
        b = AnimatedButton("Export current image only", "export", "ghost")
        b.setToolTip("Excel workbook for the selected image (Ctrl+Shift+E)")
        b.clicked.connect(self.export_current_requested)
        row.addWidget(b)
        row.addStretch(1)
        self.empty.layout().insertLayout(self.empty.layout().count() - 1, row)
        self.empty.action_button.setToolTip("Excel workbook for every analysed image (Ctrl+E)")
        card.add_widget(self.empty, 1)
        v.addWidget(card, 1)
        self.note = label("Exports use the filtered results you see on the Review page.",
                          "caption")
        v.addWidget(self.note, 0, Qt.AlignHCenter)
        state.session_opened.connect(self._refresh)
        state.session_closed.connect(self._refresh)
        self._refresh()

    def _refresh(self) -> None:
        s = self.state.session
        self.header.subtitle.setText(f"Session: {s.title}" if s else "Open a session to export it")
        self.header.subtitle.setVisible(True)
        self.empty.action_button.setEnabled(s is not None)
