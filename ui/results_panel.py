"""
Results Panel v2.0
==================
Right-side panel with tabs:
  - Histogram (default) — grain size distribution with normal curve, MATLAB style
  - Grain Table — per-grain data
  - Statistics — full text stats

Histogram is drawn natively with QPainter — no matplotlib needed.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QFrame, QSizePolicy,
    QTabWidget, QHeaderView, QScrollArea
)
from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import (
    QColor, QFont, QPainter, QPen, QBrush, QPainterPath, QFontMetrics
)
from core.grain_detector import AnalysisResult
import numpy as np
import math


# ==================================================================
# Native histogram widget (MATLAB-style)
# ==================================================================

class HistogramWidget(QWidget):
    """Custom-painted histogram with normal curve overlay, styled like MATLAB."""

    # Colors matching MATLAB default style
    BAR_COLOR = QColor(0, 114, 189)          # MATLAB blue
    CURVE_COLOR = QColor(217, 83, 25)        # MATLAB orange
    BG_COLOR = QColor(255, 255, 255)         # white plot area
    AXIS_COLOR = QColor(38, 38, 38)          # near-black axes
    GRID_COLOR = QColor(210, 210, 210)       # light gray grid
    TEXT_COLOR = QColor(38, 38, 38)
    OUTER_BG = QColor(240, 240, 240)         # area outside plot

    MARGIN_LEFT = 60
    MARGIN_RIGHT = 20
    MARGIN_TOP = 25
    MARGIN_BOTTOM = 50

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(250)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._bins = []
        self._counts = []
        self._mu = 0.0
        self._sigma = 0.0
        self._n_total = 0
        self._xlabel = "Equivalent Diameter"
        self._has_data = False

    def set_data(self, values: np.ndarray, xlabel: str = "Equivalent Diameter"):
        """Set histogram data from an array of values."""
        self._xlabel = xlabel
        if len(values) < 2:
            self._has_data = False
            self.update()
            return

        self._has_data = True
        self._n_total = len(values)
        self._mu = float(np.mean(values))
        self._sigma = float(np.std(values))

        n_bins = min(max(int(math.sqrt(len(values))), 5), 30)
        counts, edges = np.histogram(values, bins=n_bins)
        self._counts = counts.tolist()
        self._bins = edges.tolist()
        self.update()

    def clear_data(self):
        self._has_data = False
        self._counts = []
        self._bins = []
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()

        # Outer background
        p.fillRect(0, 0, w, h, self.OUTER_BG)

        if not self._has_data or not self._counts:
            p.setPen(QPen(QColor(120, 120, 140)))
            f = QFont("Arial", 12)
            p.setFont(f)
            p.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter,
                       "Run analysis to see\ngrain size distribution")
            p.end()
            return

        ml, mr, mt, mb = self.MARGIN_LEFT, self.MARGIN_RIGHT, self.MARGIN_TOP, self.MARGIN_BOTTOM
        plot_x = ml
        plot_y = mt
        plot_w = w - ml - mr
        plot_h = h - mt - mb

        if plot_w < 20 or plot_h < 20:
            p.end()
            return

        # White plot area with border
        p.fillRect(int(plot_x), int(plot_y), int(plot_w), int(plot_h), self.BG_COLOR)
        p.setPen(QPen(self.AXIS_COLOR, 1))
        p.drawRect(int(plot_x), int(plot_y), int(plot_w), int(plot_h))

        max_count = max(self._counts)
        if max_count == 0:
            p.end()
            return

        x_min = self._bins[0]
        x_max = self._bins[-1]
        x_range = x_max - x_min
        if x_range <= 0:
            p.end()
            return

        # Round up y-axis max for clean ticks
        y_max = self._nice_ceil(max_count)

        def to_px_x(val):
            return plot_x + (val - x_min) / x_range * plot_w

        def to_px_y(val):
            return plot_y + plot_h - (val / y_max) * plot_h

        # Grid lines
        p.setPen(QPen(self.GRID_COLOR, 1, Qt.PenStyle.DashLine))
        n_yticks = min(6, int(y_max))
        if n_yticks < 2:
            n_yticks = 2
        y_step = y_max / n_yticks
        for i in range(1, n_yticks + 1):
            yv = i * y_step
            py = to_px_y(yv)
            p.drawLine(QPointF(plot_x, py), QPointF(plot_x + plot_w, py))

        # Draw histogram bars
        bar_pen = QPen(QColor(20, 20, 30), 1)
        bar_brush = QBrush(self.BAR_COLOR)
        p.setPen(bar_pen)
        p.setBrush(bar_brush)

        for i in range(len(self._counts)):
            bx0 = to_px_x(self._bins[i])
            bx1 = to_px_x(self._bins[i + 1])
            by_top = to_px_y(self._counts[i])
            by_bot = to_px_y(0)
            bar_rect = QRectF(bx0, by_top, bx1 - bx0, by_bot - by_top)
            p.drawRect(bar_rect)

        # Normal curve overlay
        if self._sigma > 0 and self._n_total > 1:
            curve_pen = QPen(self.CURVE_COLOR, 2.5)
            p.setPen(curve_pen)
            p.setBrush(Qt.BrushStyle.NoBrush)

            bin_width = self._bins[1] - self._bins[0]
            path = QPainterPath()
            n_pts = 200
            started = False
            for j in range(n_pts):
                xv = x_min + (x_max - x_min) * j / (n_pts - 1)
                yv_norm = ((1.0 / (self._sigma * math.sqrt(2 * math.pi))) *
                           math.exp(-0.5 * ((xv - self._mu) / self._sigma) ** 2))
                yv = yv_norm * bin_width * self._n_total
                px = to_px_x(xv)
                py = to_px_y(yv)
                py = max(plot_y, min(plot_y + plot_h, py))
                if not started:
                    path.moveTo(px, py)
                    started = True
                else:
                    path.lineTo(px, py)
            p.drawPath(path)

        # Axes ticks and labels
        p.setPen(QPen(self.TEXT_COLOR))
        tick_font = QFont("Arial", 9)
        p.setFont(tick_font)
        fm = QFontMetrics(tick_font)

        # Y-axis ticks
        for i in range(n_yticks + 1):
            yv = i * y_step
            py = to_px_y(yv)
            p.drawLine(QPointF(plot_x - 4, py), QPointF(plot_x, py))
            label = str(int(round(yv)))
            tw = fm.horizontalAdvance(label)
            p.drawText(QPointF(plot_x - tw - 8, py + fm.height() / 3), label)

        # X-axis ticks — pick ~5-7 nice values
        x_ticks = self._nice_ticks(x_min, x_max, 6)
        for xv in x_ticks:
            px = to_px_x(xv)
            if px < plot_x or px > plot_x + plot_w:
                continue
            p.drawLine(QPointF(px, plot_y + plot_h), QPointF(px, plot_y + plot_h + 4))
            label = f"{xv:.3g}"
            tw = fm.horizontalAdvance(label)
            p.drawText(QPointF(px - tw / 2, plot_y + plot_h + 16), label)

        # Axis labels
        label_font = QFont("Arial", 10, QFont.Weight.Bold)
        p.setFont(label_font)

        # X label
        xlbl = self._xlabel
        tw = QFontMetrics(label_font).horizontalAdvance(xlbl)
        p.drawText(QPointF(plot_x + plot_w / 2 - tw / 2, h - 5), xlbl)

        # Y label (rotated)
        p.save()
        p.translate(14, plot_y + plot_h / 2)
        p.rotate(-90)
        ylbl = "Number of Grains"
        tw = QFontMetrics(label_font).horizontalAdvance(ylbl)
        p.drawText(QPointF(-tw / 2, 0), ylbl)
        p.restore()

        p.end()

    @staticmethod
    def _nice_ceil(value):
        """Round up to a nice number for axis max."""
        if value <= 0:
            return 1
        mag = 10 ** math.floor(math.log10(value))
        residual = value / mag
        if residual <= 1:
            return mag
        elif residual <= 2:
            return 2 * mag
        elif residual <= 5:
            return 5 * mag
        else:
            return 10 * mag

    @staticmethod
    def _nice_ticks(lo, hi, target_count=6):
        """Generate nice round tick values."""
        r = hi - lo
        if r <= 0:
            return [lo]
        step = r / target_count
        mag = 10 ** math.floor(math.log10(step))
        residual = step / mag
        if residual < 1.5:
            nice_step = mag
        elif residual < 3.5:
            nice_step = 2 * mag
        elif residual < 7.5:
            nice_step = 5 * mag
        else:
            nice_step = 10 * mag
        start = math.ceil(lo / nice_step) * nice_step
        ticks = []
        v = start
        while v <= hi + nice_step * 0.01:
            ticks.append(v)
            v += nice_step
        return ticks


# ==================================================================
# Stat cards
# ==================================================================

class StatCard(QFrame):
    def __init__(self, label, value="—", unit="", parent=None):
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

    def set_value(self, value):
        self.value_lbl.setText(value)


# ==================================================================
# Main Results Panel
# ==================================================================

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

        # Stats cards grid
        self.stats_widget = QWidget()
        self.stats_widget.setVisible(False)
        stats_lay = QVBoxLayout(self.stats_widget)
        stats_lay.setSpacing(8)

        row1 = QHBoxLayout()
        self.card_count = StatCard("Grains Detected", "—")
        self.card_coverage = StatCard("Grain Coverage", "—", "%")
        row1.addWidget(self.card_count)
        row1.addWidget(self.card_coverage)
        stats_lay.addLayout(row1)

        row2 = QHBoxLayout()
        self.card_mean_area = StatCard("Mean Area", "—", "µm²")
        self.card_mean_diam = StatCard("Mean Diameter", "—", "µm")
        row2.addWidget(self.card_mean_area)
        row2.addWidget(self.card_mean_diam)
        stats_lay.addLayout(row2)

        row3 = QHBoxLayout()
        self.card_circ = StatCard("Mean Circularity", "—", "(0–1)")
        self.card_ar = StatCard("Mean Aspect Ratio", "—")
        row3.addWidget(self.card_circ)
        row3.addWidget(self.card_ar)
        stats_lay.addLayout(row3)

        lay.addWidget(self.stats_widget)

        # Tabs
        self.tabs = QTabWidget()
        self.tabs.setVisible(False)
        lay.addWidget(self.tabs)

        # Histogram tab (DEFAULT — first tab)
        self.histogram = HistogramWidget()
        self.tabs.addTab(self.histogram, "Histogram")

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
        self.empty_label.setVisible(False)
        self.stats_widget.setVisible(True)
        self.tabs.setVisible(True)

        has_cal = result.has_calibration

        self.card_count.set_value(str(result.grain_count))
        if has_cal:
            self.card_coverage.set_value(f"{result.grain_coverage_pct:.1f}")
            self.card_mean_area.set_value(f"{result.mean_area_um2:.3f}")
            self.card_mean_diam.set_value(f"{result.mean_diameter_um:.3f}")
        else:
            self.card_coverage.set_value("N/A")
            areas = [g.area_px for g in result.grains]
            self.card_mean_area.set_value(f"{np.mean(areas):.0f}" if areas else "—")
            diams = [g.equivalent_diameter_px for g in result.grains]
            self.card_mean_diam.set_value(f"{np.mean(diams):.1f}" if diams else "—")
        self.card_circ.set_value(f"{result.mean_circularity:.3f}")
        self.card_ar.set_value(f"{result.mean_aspect_ratio:.3f}")

        # Histogram
        self._update_histogram(result)

        # Table
        self._populate_table(result)

        # Stats text
        self._populate_stats_text(result)

        # Show histogram tab by default
        self.tabs.setCurrentIndex(0)

    def _update_histogram(self, result: AnalysisResult):
        if not result.grains:
            self.histogram.clear_data()
            return

        has_cal = result.has_calibration
        if has_cal:
            px_per_um = result.px_per_um
            um_per_px = 1.0 / px_per_um
            nm_per_px = um_per_px * 1000.0
            if nm_per_px < 50:
                values = np.array([g.equivalent_diameter_um * 1000 for g in result.grains])
                xlabel = "Equivalent Diameter (nm)"
            else:
                values = np.array([g.equivalent_diameter_um for g in result.grains])
                xlabel = "Equivalent Diameter (µm)"
        else:
            values = np.array([g.equivalent_diameter_px for g in result.grains])
            xlabel = "Equivalent Diameter (px)"

        self.histogram.set_data(values, xlabel)

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
        self.histogram.clear_data()
