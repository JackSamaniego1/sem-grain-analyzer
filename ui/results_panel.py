"""
Results Panel v2.2
==================
- Grain Size Distribution tab (default) with adjustable bin count
- Grain area on X axis, normal curve overlay
- Dark background with light axis edges
- Bin spinner live-updates the histogram
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QFrame, QSizePolicy,
    QTabWidget, QHeaderView, QScrollArea, QSpinBox
)
from PyQt6.QtCore import Qt, QRectF, QPointF, pyqtSignal
from PyQt6.QtGui import (
    QColor, QFont, QPainter, QPen, QBrush, QPainterPath, QFontMetrics
)
from core.grain_detector import AnalysisResult
import numpy as np
import math


class HistogramWidget(QWidget):
    """Custom-painted histogram — grain area, dark bg, normal curve."""

    BAR_COLOR = QColor(100, 120, 220)
    CURVE_COLOR = QColor(220, 50, 120)
    BG_COLOR = QColor(42, 42, 52)
    PLOT_BG = QColor(50, 50, 62)
    AXIS_COLOR = QColor(140, 140, 170)
    GRID_COLOR = QColor(65, 65, 78)
    TEXT_COLOR = QColor(200, 200, 220)
    LABEL_COLOR = QColor(220, 220, 235)

    MARGIN_LEFT = 58
    MARGIN_RIGHT = 15
    MARGIN_TOP = 20
    MARGIN_BOTTOM = 55

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(250)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._values = np.array([])
        self._xlabel = "Grain Area"
        self._unit = ""
        self._n_bins = 0  # 0 = auto
        self._bins = []
        self._counts = []
        self._bin_labels = []
        self._mu = 0.0
        self._sigma = 0.0
        self._has_data = False

    def set_data(self, values, xlabel="Grain Area", unit="", n_bins=0):
        self._values = values
        self._xlabel = xlabel
        self._unit = unit
        self._n_bins = n_bins
        self._recompute()
        self.update()

    def set_bin_count(self, n_bins):
        self._n_bins = n_bins
        if len(self._values) >= 2:
            self._recompute()
            self.update()

    def get_bin_count(self):
        return self._actual_bins

    def _recompute(self):
        values = self._values
        if len(values) < 2:
            self._has_data = False
            return
        self._has_data = True
        self._mu = float(np.mean(values))
        self._sigma = float(np.std(values))

        if self._n_bins > 0:
            nb = self._n_bins
        else:
            nb = min(max(int(math.sqrt(len(values))), 5), 30)
        self._actual_bins = nb

        counts, edges = np.histogram(values, bins=nb)
        self._counts = counts.tolist()
        self._bins = edges.tolist()

        def fmt(v):
            if abs(v) >= 1000: return f"{v:.0f}"
            elif abs(v) >= 10: return f"{v:.1f}"
            elif abs(v) >= 1: return f"{v:.1f}"
            else: return f"{v:.2f}"

        self._bin_labels = [
            f"{fmt(edges[i])}-{fmt(edges[i+1])}{self._unit}"
            for i in range(len(edges) - 1)
        ]

    def clear_data(self):
        self._has_data = False
        self._values = np.array([])
        self._counts = []
        self._bins = []
        self._bin_labels = []
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, self.BG_COLOR)

        if not self._has_data or not self._counts:
            p.setPen(QPen(QColor(100, 100, 130)))
            p.setFont(QFont("Arial", 12))
            p.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter,
                       "Run analysis to see\ngrain size distribution")
            p.end()
            return

        ml, mr, mt, mb = self.MARGIN_LEFT, self.MARGIN_RIGHT, self.MARGIN_TOP, self.MARGIN_BOTTOM
        px, py, pw, ph = ml, mt, w - ml - mr, h - mt - mb
        if pw < 20 or ph < 20:
            p.end(); return

        p.fillRect(int(px), int(py), int(pw), int(ph), self.PLOT_BG)
        p.setPen(QPen(self.AXIS_COLOR, 1))
        p.drawRect(int(px), int(py), int(pw), int(ph))

        mc = max(self._counts)
        if mc == 0:
            p.end(); return

        x_min, x_max = self._bins[0], self._bins[-1]
        x_range = x_max - x_min
        if x_range <= 0:
            p.end(); return

        y_max = self._nice_ceil(mc * 1.1)

        def tx(val): return px + (val - x_min) / x_range * pw
        def ty(val): return py + ph - (val / y_max) * ph

        # Grid
        p.setPen(QPen(self.GRID_COLOR, 1, Qt.PenStyle.DashLine))
        nyt = min(6, max(2, int(y_max)))
        ys = y_max / nyt
        for i in range(1, nyt + 1):
            yp = ty(i * ys)
            p.drawLine(QPointF(px, yp), QPointF(px + pw, yp))

        # Bars — touching, no gap
        p.setPen(QPen(QColor(30, 30, 45), 1))
        p.setBrush(QBrush(self.BAR_COLOR))
        for i in range(len(self._counts)):
            bx0, bx1 = tx(self._bins[i]), tx(self._bins[i + 1])
            bt, bb = ty(self._counts[i]), ty(0)
            p.drawRect(QRectF(bx0, bt, bx1 - bx0, bb - bt))

        # Normal curve
        if self._sigma > 0 and len(self._values) > 1:
            p.setPen(QPen(self.CURVE_COLOR, 2.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            bw = self._bins[1] - self._bins[0]
            path = QPainterPath()
            started = False
            for j in range(200):
                xv = x_min + x_range * j / 199
                yv = ((1.0 / (self._sigma * math.sqrt(2 * math.pi))) *
                      math.exp(-0.5 * ((xv - self._mu) / self._sigma) ** 2))
                yv *= bw * len(self._values)
                xp, yp = tx(xv), max(py, min(py + ph, ty(yv)))
                if not started: path.moveTo(xp, yp); started = True
                else: path.lineTo(xp, yp)
            p.drawPath(path)

        # Y ticks outside
        p.setPen(QPen(self.TEXT_COLOR))
        tf = QFont("Arial", 9); p.setFont(tf); fm = QFontMetrics(tf)
        for i in range(nyt + 1):
            yv = i * ys; yp = ty(yv)
            p.drawLine(QPointF(px - 4, yp), QPointF(px, yp))
            lbl = str(int(round(yv)))
            p.drawText(QPointF(px - fm.horizontalAdvance(lbl) - 7, yp + fm.ascent() / 2 - 1), lbl)

        # X bin labels rotated
        sf = QFont("Arial", 7); p.setFont(sf); sfm = QFontMetrics(sf)
        for i in range(len(self._bin_labels)):
            cx = (tx(self._bins[i]) + tx(self._bins[i + 1])) / 2
            p.save()
            p.translate(cx, py + ph + 6)
            p.rotate(45)
            p.drawText(QPointF(0, sfm.ascent()), self._bin_labels[i])
            p.restore()

        # Axis labels
        lf = QFont("Arial", 10, QFont.Weight.Bold); p.setFont(lf); p.setPen(QPen(self.LABEL_COLOR))
        lfm = QFontMetrics(lf)
        xtw = lfm.horizontalAdvance(self._xlabel)
        p.drawText(QPointF(px + pw / 2 - xtw / 2, h - 3), self._xlabel)
        p.save(); p.translate(13, py + ph / 2); p.rotate(-90)
        ylbl = "Number of Grains"; ytw = lfm.horizontalAdvance(ylbl)
        p.drawText(QPointF(-ytw / 2, 0), ylbl)
        p.restore()
        p.end()

    @staticmethod
    def _nice_ceil(value):
        if value <= 0: return 1
        mag = 10 ** math.floor(math.log10(value))
        r = value / mag
        if r <= 1: return mag
        elif r <= 2: return 2 * mag
        elif r <= 5: return 5 * mag
        return 10 * mag


class StatCard(QFrame):
    def __init__(self, label, value="—", unit="", parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("QFrame{background:#252534;border:1px solid #3c3c4e;border-radius:8px;}")
        lay = QVBoxLayout(self); lay.setContentsMargins(10, 8, 10, 8); lay.setSpacing(2)
        self.value_lbl = QLabel(value)
        self.value_lbl.setStyleSheet("font-size:16px;font-weight:700;color:#00c8ff;border:none;")
        self.value_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.value_lbl)
        if unit:
            u = QLabel(unit); u.setStyleSheet("font-size:10px;color:#7777aa;border:none;")
            u.setAlignment(Qt.AlignmentFlag.AlignCenter); lay.addWidget(u)
        n = QLabel(label); n.setStyleSheet("font-size:10px;color:#9999bb;border:none;")
        n.setAlignment(Qt.AlignmentFlag.AlignCenter); n.setWordWrap(True); lay.addWidget(n)

    def set_value(self, v): self.value_lbl.setText(v)


class ResultsPanel(QWidget):
    # Signal emitted when user changes bin count — main_window can use this
    bin_count_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(320)
        self._current_result = None
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self); lay.setContentsMargins(4, 4, 4, 4); lay.setSpacing(8)

        title = QLabel("Analysis Results"); title.setObjectName("subheader")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter); lay.addWidget(title)

        self.empty_label = QLabel("Run analysis to see results here.")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet("color:#555577;font-size:12px;padding:40px;")
        lay.addWidget(self.empty_label)

        # Stats cards
        self.stats_widget = QWidget(); self.stats_widget.setVisible(False)
        sl = QVBoxLayout(self.stats_widget); sl.setSpacing(8)
        r1 = QHBoxLayout()
        self.card_count = StatCard("Grains Detected"); self.card_coverage = StatCard("Grain Coverage", "—", "%")
        r1.addWidget(self.card_count); r1.addWidget(self.card_coverage); sl.addLayout(r1)
        r2 = QHBoxLayout()
        self.card_mean_area = StatCard("Mean Area", "—", "µm²"); self.card_mean_diam = StatCard("Mean Diameter", "—", "µm")
        r2.addWidget(self.card_mean_area); r2.addWidget(self.card_mean_diam); sl.addLayout(r2)
        r3 = QHBoxLayout()
        self.card_circ = StatCard("Mean Circularity", "—", "(0–1)"); self.card_ar = StatCard("Mean Aspect Ratio")
        r3.addWidget(self.card_circ); r3.addWidget(self.card_ar); sl.addLayout(r3)
        lay.addWidget(self.stats_widget)

        self.tabs = QTabWidget(); self.tabs.setVisible(False); lay.addWidget(self.tabs)

        # Histogram tab with bin spinner
        hist_container = QWidget()
        hlay = QVBoxLayout(hist_container); hlay.setContentsMargins(0, 0, 0, 0); hlay.setSpacing(4)

        # Bin count control bar
        bin_bar = QHBoxLayout()
        bin_label = QLabel("Bins:")
        bin_label.setStyleSheet("color:#aaaacc;font-size:11px;")
        bin_bar.addWidget(bin_label)
        self.bin_spin = QSpinBox()
        self.bin_spin.setRange(3, 100)
        self.bin_spin.setValue(0)  # 0 = auto (will set to actual when data arrives)
        self.bin_spin.setToolTip("Number of histogram bins. Changing this updates the chart instantly.")
        self.bin_spin.setFixedWidth(70)
        self.bin_spin.valueChanged.connect(self._on_bin_changed)
        bin_bar.addWidget(self.bin_spin)
        bin_bar.addStretch()
        hlay.addLayout(bin_bar)

        self.histogram = HistogramWidget()
        hlay.addWidget(self.histogram)
        self.tabs.addTab(hist_container, "Grain Size Distribution")

        # Table tab
        tw = QWidget(); tl = QVBoxLayout(tw); tl.setContentsMargins(0, 4, 0, 0)
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        tl.addWidget(self.table); self.tabs.addTab(tw, "Grain Table")

        # Stats tab
        ss = QScrollArea(); ss.setWidgetResizable(True); ss.setFrameShape(QFrame.Shape.NoFrame)
        self.full_stats_widget = QLabel()
        self.full_stats_widget.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.full_stats_widget.setWordWrap(True)
        self.full_stats_widget.setStyleSheet("padding:10px;color:#dcdce6;font-family:monospace;font-size:12px;")
        ss.setWidget(self.full_stats_widget); self.tabs.addTab(ss, "Statistics")

    def _on_bin_changed(self, val):
        self.histogram.set_bin_count(val)
        self.bin_count_changed.emit(val)

    def get_bin_count(self):
        """Return user-selected bin count (for Excel export)."""
        if self._current_result and self._current_result.grains:
            return self.histogram.get_bin_count()
        return 0

    def display_results(self, result: AnalysisResult):
        self._current_result = result
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

        self._update_histogram(result)
        self._populate_table(result)
        self._populate_stats_text(result)
        self.tabs.setCurrentIndex(0)

    def _update_histogram(self, result):
        if not result.grains:
            self.histogram.clear_data(); return
        has_cal = result.has_calibration
        if has_cal:
            nm_per_px = 1000.0 / result.px_per_um
            if nm_per_px < 50:
                values = np.array([g.area_um2 * 1e6 for g in result.grains])
                xlabel, unit = "Grain Area (nm²)", "nm²"
            else:
                values = np.array([g.area_um2 for g in result.grains])
                xlabel, unit = "Grain Area (µm²)", "µm²"
        else:
            values = np.array([g.area_px for g in result.grains])
            xlabel, unit = "Grain Area (px²)", "px²"

        # Use current spinner value (0 = auto)
        n_bins = self.bin_spin.value()
        if n_bins < 3:
            n_bins = 0  # auto

        self.histogram.set_data(values, xlabel, unit, n_bins)

        # Update spinner to show actual bin count used
        actual = self.histogram.get_bin_count()
        self.bin_spin.blockSignals(True)
        self.bin_spin.setValue(actual)
        self.bin_spin.blockSignals(False)

    def _populate_table(self, result):
        has_cal = result.has_calibration
        hdrs = (["ID", "Area (µm²)", "Diam (µm)", "Circ.", "AR"]
                if has_cal else ["ID", "Area (px²)", "Diam (px)", "Circ.", "AR"])
        self.table.setColumnCount(len(hdrs))
        self.table.setHorizontalHeaderLabels(hdrs)
        self.table.setRowCount(len(result.grains))
        for row, g in enumerate(result.grains):
            vals = [str(g.grain_id),
                    f"{g.area_um2:.4f}" if has_cal else f"{g.area_px:.0f}",
                    f"{g.equivalent_diameter_um:.4f}" if has_cal else f"{g.equivalent_diameter_px:.1f}",
                    f"{g.circularity:.3f}", f"{g.aspect_ratio:.3f}"]
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, item)

    def _populate_stats_text(self, result):
        lines = [f"GRAIN COUNT:   {result.grain_count}", ""]
        has_cal = result.has_calibration
        if has_cal:
            lines += [f"Calibration:   {result.px_per_um:.4f} px/µm",
                      f"Image area:    {result.total_analyzed_area_um2:.2f} µm²",
                      f"Grain coverage:{result.grain_coverage_pct:.2f}%", "",
                      "── Area (µm²) ──────────────────",
                      f"  Mean:        {result.mean_area_um2:.5f}",
                      f"  Std Dev:     {result.std_area_um2:.5f}",
                      f"  Median:      {result.median_area_um2:.5f}",
                      f"  Min:         {result.min_area_um2:.5f}",
                      f"  Max:         {result.max_area_um2:.5f}", "",
                      "── Diameter (µm) ───────────────",
                      f"  Mean:        {result.mean_diameter_um:.5f}",
                      f"  Std Dev:     {result.std_diameter_um:.5f}"]
        else:
            areas = [g.area_px for g in result.grains]
            if areas:
                lines += ["── Area (px²) ──────────────────",
                          f"  Mean:        {np.mean(areas):.2f}",
                          f"  Std Dev:     {np.std(areas):.2f}",
                          f"  Median:      {np.median(areas):.2f}",
                          f"  Min:         {np.min(areas):.2f}",
                          f"  Max:         {np.max(areas):.2f}"]
        lines += ["", "── Shape ───────────────────────",
                   f"  Mean Circularity:   {result.mean_circularity:.4f}",
                   f"  Mean Aspect Ratio:  {result.mean_aspect_ratio:.4f}"]
        self.full_stats_widget.setText("\n".join(lines))

    def clear(self):
        self._current_result = None
        self.empty_label.setVisible(True)
        self.stats_widget.setVisible(False)
        self.tabs.setVisible(False)
        self.table.setRowCount(0)
        self.histogram.clear_data()
