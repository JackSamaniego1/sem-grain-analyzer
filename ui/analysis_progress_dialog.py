"""
Analysis Progress Dialog
=========================
Shows a progress window when analyzing multiple images.
One image at a time, with ETA, per-image status, and overall progress.
"""

import time
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QScrollArea, QWidget, QFrame
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QColor


class ImageStatusRow(QWidget):
    """One row per image showing name + status."""
    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)

        self.icon_lbl = QLabel("⏳")
        self.icon_lbl.setFixedWidth(22)
        lay.addWidget(self.icon_lbl)

        self.name_lbl = QLabel(name)
        self.name_lbl.setStyleSheet("font-size: 12px;")
        lay.addWidget(self.name_lbl, 1)

        self.status_lbl = QLabel("Waiting...")
        self.status_lbl.setStyleSheet("color: #888899; font-size: 11px;")
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        lay.addWidget(self.status_lbl)

    def set_running(self):
        self.icon_lbl.setText("🔄")
        self.status_lbl.setText("Analysing...")
        self.status_lbl.setStyleSheet("color: #00aaff; font-size: 11px;")

    def set_done(self, grain_count: int):
        self.icon_lbl.setText("✅")
        self.status_lbl.setText(f"{grain_count} grains")
        self.status_lbl.setStyleSheet("color: #00cc77; font-size: 11px;")

    def set_error(self):
        self.icon_lbl.setText("❌")
        self.status_lbl.setText("Error")
        self.status_lbl.setStyleSheet("color: #ff4444; font-size: 11px;")


class AnalysisProgressDialog(QDialog):
    """
    Non-modal-ish progress window that shows while images are being processed.
    Call mark_running(i), mark_done(i, grains), mark_error(i) from main_window.
    """

    cancelled = pyqtSignal()

    def __init__(self, image_names: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Analyzing Images...")
        self.setMinimumWidth(480)
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.WindowTitleHint |
            Qt.WindowType.CustomizeWindowHint
        )
        self._n = len(image_names)
        self._done = 0
        self._start_time = time.time()
        self._times = []   # seconds per completed image
        self._rows: list[ImageStatusRow] = []
        self._build_ui(image_names)

    def _build_ui(self, names: list[str]):
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        # Header
        self.hdr_lbl = QLabel(f"Analyzing {self._n} image(s)...")
        self.hdr_lbl.setStyleSheet("font-size: 14px; font-weight: bold;")
        lay.addWidget(self.hdr_lbl)

        # Overall progress bar
        self.overall_bar = QProgressBar()
        self.overall_bar.setRange(0, self._n)
        self.overall_bar.setValue(0)
        self.overall_bar.setFixedHeight(20)
        lay.addWidget(self.overall_bar)

        self.overall_lbl = QLabel(f"0 / {self._n} complete")
        self.overall_lbl.setStyleSheet("color: #aaaacc; font-size: 11px;")
        lay.addWidget(self.overall_lbl)

        # Current image sub-progress
        self.sub_lbl = QLabel("Starting...")
        self.sub_lbl.setStyleSheet("color: #ccccee; font-size: 12px;")
        lay.addWidget(self.sub_lbl)

        self.sub_bar = QProgressBar()
        self.sub_bar.setRange(0, 100)
        self.sub_bar.setValue(0)
        self.sub_bar.setFixedHeight(14)
        lay.addWidget(self.sub_bar)

        # ETA
        self.eta_lbl = QLabel("Estimated time remaining:  —")
        self.eta_lbl.setStyleSheet("color: #8888aa; font-size: 11px;")
        lay.addWidget(self.eta_lbl)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #3c3c48;")
        lay.addWidget(sep)

        # Per-image list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(min(40 * self._n + 20, 280))
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        list_widget = QWidget()
        list_lay = QVBoxLayout(list_widget)
        list_lay.setSpacing(2)
        list_lay.setContentsMargins(4, 4, 4, 4)

        for name in names:
            row = ImageStatusRow(name)
            self._rows.append(row)
            list_lay.addWidget(row)
        list_lay.addStretch()
        scroll.setWidget(list_widget)
        lay.addWidget(scroll)

        # Cancel button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self._on_cancel)
        btn_row.addWidget(self.btn_cancel)
        lay.addLayout(btn_row)

    def _on_cancel(self):
        self.cancelled.emit()
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setText("Cancelling...")

    # ------ called from main_window ------

    def mark_running(self, idx: int):
        if 0 <= idx < len(self._rows):
            self._rows[idx].set_running()
        name = self._rows[idx].name_lbl.text() if idx < len(self._rows) else f"Image {idx+1}"
        self.sub_lbl.setText(f"Current: {name}")
        self.sub_bar.setValue(0)

    def update_sub_progress(self, pct: int, msg: str):
        self.sub_bar.setValue(pct)
        self.sub_lbl.setText(msg)

    def mark_done(self, idx: int, grain_count: int):
        if 0 <= idx < len(self._rows):
            self._rows[idx].set_done(grain_count)
        self._done += 1
        elapsed = time.time() - self._start_time
        self._times.append(elapsed / self._done)
        avg = sum(self._times) / len(self._times)
        remaining = avg * (self._n - self._done)
        self.overall_bar.setValue(self._done)
        self.overall_lbl.setText(f"{self._done} / {self._n} complete")
        if remaining > 0 and self._done < self._n:
            mins = int(remaining // 60)
            secs = int(remaining % 60)
            if mins > 0:
                eta = f"{mins}m {secs}s"
            else:
                eta = f"{secs}s"
            self.eta_lbl.setText(f"Estimated time remaining:  {eta}")
        else:
            self.eta_lbl.setText("Almost done...")

    def mark_error(self, idx: int):
        if 0 <= idx < len(self._rows):
            self._rows[idx].set_error()
        self._done += 1
        self.overall_bar.setValue(self._done)
        self.overall_lbl.setText(f"{self._done} / {self._n} complete")

    def all_done(self):
        elapsed = time.time() - self._start_time
        mins = int(elapsed // 60)
        secs = int(elapsed % 60)
        self.hdr_lbl.setText(f"✅  All {self._n} images analysed!")
        self.sub_lbl.setText(f"Total time: {mins}m {secs}s")
        self.sub_bar.setValue(100)
        self.eta_lbl.setText("")
        self.overall_bar.setValue(self._n)
        self.overall_lbl.setText(f"{self._n} / {self._n} complete")
        self.btn_cancel.setText("Close")
        self.btn_cancel.setEnabled(True)
        self.btn_cancel.clicked.disconnect()
        self.btn_cancel.clicked.connect(self.accept)
