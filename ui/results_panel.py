"""
Results Panel
=============
Right-side panel showing grain statistics and data table after analysis.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QGroupBox, QScrollArea, QFrame, QSizePolicy,
    QTabWidget, QHeaderView
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from core.grain_detector import AnalysisResult
import numpy as np


class StatCard(QFrame):
    """A small statistics card widget."""
    def __init__(self, label: str, value: str = "—", unit: str = "", parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "QFrame { background: #252534; border: 1px solid #3c3c4e; border-radius: 8px; }"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(2)

        self.value_lbl = QLabel(value)
        self.value_lbl.setStyleSheet("font-size: 16px; font-weight: 700; color: #00c8ff; border: none;")
        self.value_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.value_lbl)

        if unit:
            unit_lbl = QLabel(unit)
            unit_lbl.setStyleSheet("font-size: 10px; color: #7777aa; border: none;")
            unit_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(unit_lbl)

        name_lbl = QLabel(label)
        name_lbl.setStyleSheet("font-size: 10px; color: #9999bb; border: none;")
        name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_lbl.setWordWrap(True)
        lay.addWidget(name_lbl)

    def set_value(self, value: str):
        self.value_lbl.setText(value)


class ResultsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(320)
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(8)

        title = QLabel("Analysis Results")
        title.setObjectName("subheader")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        self.empty_label = QLabel("Run analysis to see results here.")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet("color: #555577; font-size: 12px; padding: 40px;")
        lay.addWidget(self.empty_label)

        # Stats cards grid (hidden until results)
        self.stats_widget = QWidget()
        self.stats_widget.setVisible(False)
        stats_lay = QVBoxLayout(self.stats_widget)
        stats_lay.setSpacing(8)

        # Row 1: count + coverage
        row1 = QHBoxLayout()
        self.card_count = StatCard("Grains Detected", "—")
        self.card_coverage = StatCard("Grain Coverage", "—", "%")
        row1.addWidget(self.card_count)
        row1.addWidget(self.card_coverage)
        stats_lay.addLayout(row1)

        # Row 2: mean area + mean diameter
        row2 = QHBoxLayout()
        self.card_mean_area = StatCard("Mean Area", "—", "µm²")
        self.card_mean_diam = StatCard("Mean Diameter", "—", "µm")
        row2.addWidget(self.card_mean_area)
        row2.addWidget(self.card_mean_diam)
        stats_lay.addLayout(row2)

        # Row 3: circularity + aspect ratio
        row3 = QHBoxLayout()
        self.card_circ = StatCard("Mean Circularity", "—", "(0–1)")
        self.card_ar = StatCard("Mean Aspect Ratio", "—")
        row3.addWidget(self.card_circ)
        row3.addWidget(self.card_ar)
        stats_lay.addLayout(row3)

        lay.addWidget(self.stats_widget)

        # Tabs: table + extra stats
        self.tabs = QTabWidget()
        self.tabs.setVisible(False)
        lay.addWidget(self.tabs)

        # Grain table tab
        table_widget = QWidget()
        table_lay = QVBoxLayout(table_widget)
        table_lay.setContentsMargins(0, 4, 0, 0)
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        table_lay.addWidget(self.table)
        self.tabs.addTab(table_widget, "Grain Table")

        # Full stats tab
        stats_scroll = QScrollArea()
        stats_scroll.setWidgetResizable(True)
        stats_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.full_stats_widget = QLabel()
        self.full_stats_widget.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.full_stats_widget.setWordWrap(True)
        self.full_stats_widget.setStyleSheet("padding: 10px; color: #dcdce6; font-family: monospace; font-size: 12px;")
        stats_scroll.setWidget(self.full_stats_widget)
        self.tabs.addTab(stats_scroll, "Statistics")

    def display_results(self, result: AnalysisResult):
        """Populate the results panel from an AnalysisResult."""
        self.empty_label.setVisible(False)
        self.stats_widget.setVisible(True)
        self.tabs.setVisible(True)

        has_cal = result.has_calibration

        # Stats cards
        self.card_count.set_value(str(result.grain_count))
        if has_cal:
            self.card_coverage.set_value(f"{result.grain_coverage_pct:.1f}")
            self.card_mean_area.set_value(f"{result.mean_area_um2:.3f}")
            self.card_mean_diam.set_value(f"{result.mean_diameter_um:.3f}")
        else:
            self.card_coverage.set_value("N/A")
            areas = [g.area_px for g in result.grains]
            self.card_mean_area.set_value(f"{np.mean(areas):.0f}" if areas else "—")
            self.card_mean_area.value_lbl.setToolTip("px² (no calibration)")
            diams = [g.equivalent_diameter_px for g in result.grains]
            self.card_mean_diam.set_value(f"{np.mean(diams):.1f}" if diams else "—")
        self.card_circ.set_value(f"{result.mean_circularity:.3f}")
        self.card_ar.set_value(f"{result.mean_aspect_ratio:.3f}")

        # Table
        self._populate_table(result)

        # Full stats text
        self._populate_stats_text(result)

    def _populate_table(self, result: AnalysisResult):
        has_cal = result.has_calibration
        if has_cal:
            headers = ["ID", "Area (µm²)", "Diam (µm)", "Circ.", "Aspect R."]
        else:
            headers = ["ID", "Area (px²)", "Diam (px)", "Circ.", "Aspect R."]

        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(result.grains))

        for row, grain in enumerate(result.grains):
            vals = [
                str(grain.grain_id),
                f"{grain.area_um2:.4f}" if has_cal else f"{grain.area_px:.0f}",
                f"{grain.equivalent_diameter_um:.4f}" if has_cal else f"{grain.equivalent_diameter_px:.1f}",
                f"{grain.circularity:.3f}",
                f"{grain.aspect_ratio:.3f}",
            ]
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, item)

    def _populate_stats_text(self, result: AnalysisResult):
        lines = []
        has_cal = result.has_calibration
        lines.append(f"GRAIN COUNT:   {result.grain_count}")
        lines.append("")
        if has_cal:
            lines.append(f"Calibration:   {result.px_per_um:.4f} px/µm")
            lines.append(f"Image area:    {result.total_analyzed_area_um2:.2f} µm²")
            lines.append(f"Grain coverage:{result.grain_coverage_pct:.2f}%")
            lines.append("")
            lines.append("── Area (µm²) ──────────────────")
            lines.append(f"  Mean:        {result.mean_area_um2:.5f}")
            lines.append(f"  Std Dev:     {result.std_area_um2:.5f}")
            lines.append(f"  Median:      {result.median_area_um2:.5f}")
            lines.append(f"  Min:         {result.min_area_um2:.5f}")
            lines.append(f"  Max:         {result.max_area_um2:.5f}")
            lines.append("")
            lines.append("── Diameter (µm) ───────────────")
            lines.append(f"  Mean:        {result.mean_diameter_um:.5f}")
            lines.append(f"  Std Dev:     {result.std_diameter_um:.5f}")
        else:
            areas = [g.area_px for g in result.grains]
            if areas:
                lines.append("── Area (px²) [No calibration] ─")
                lines.append(f"  Mean:        {np.mean(areas):.2f}")
                lines.append(f"  Std Dev:     {np.std(areas):.2f}")
                lines.append(f"  Median:      {np.median(areas):.2f}")
                lines.append(f"  Min:         {np.min(areas):.2f}")
                lines.append(f"  Max:         {np.max(areas):.2f}")
        lines.append("")
        lines.append("── Shape ───────────────────────")
        lines.append(f"  Mean Circularity:   {result.mean_circularity:.4f}")
        lines.append(f"  Mean Aspect Ratio:  {result.mean_aspect_ratio:.4f}")
        self.full_stats_widget.setText("\n".join(lines))

    def clear(self):
        self.empty_label.setVisible(True)
        self.stats_widget.setVisible(False)
        self.tabs.setVisible(False)
        self.table.setRowCount(0)
