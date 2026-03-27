"""
Results Panel v2.3
==================
- Grain Size Distribution tab with adjustable bin count
- Bins start at 0, whole-number increments (0-10, 10-20, etc.)
- Smart unit auto-scaling to avoid tiny decimals
- Grain area on X axis
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


def smart_unit(px_per_um):
    """Pick units so values are close to whole numbers, never tiny exponents.
    Returns (area_unit, area_mult, diam_unit, diam_mult)."""
    if px_per_um <= 0:
        return "px²", 1.0, "px", 1.0
    um_per_px = 1.0 / px_per_um
    nm_per_px = um_per_px * 1000.0
    if nm_per_px < 50:
        return "nm²", 1e6, "nm", 1000.0
    return "µm²", 1.0, "µm", 1.0


def smart_format(val):
    """Format number as close to whole number as possible, no exponents."""
    if val == 0:
        return "0"
    av = abs(val)
    if av >= 1000:
        return f"{val:.0f}"
    if av >= 100:
        return f"{val:.0f}"
    if av >= 10:
        return f"{val:.1f}"
    if av >= 1:
        return f"{val:.2f}"
    if av >= 0.1:
        return f"{val:.3f}"
    if av >= 0.01:
        return f"{val:.4f}"
    return f"{val:.3g}"


class HistogramWidget(QWidget):
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
        self._n_bins = 0
        self._bins = []
        self._counts = []
        self._bin_labels = []
        self._mu = 0.0
        self._sigma = 0.0
        self._has_data = False
        self._actual_bins = 0

    def set_data(self, values, xlabel="Grain Area", unit="", n_bins=0):
        self._values = values
        self._xlabel = xlabel
        self._unit = unit
        self._n_bins = n_bins
        self._recompute()
        self.update()

    def set_bin_count(self, n):
        self._n_bins = n
        if len(self._values) >= 2:
            self._recompute()
            self.update()

    def get_bin_count(self):
        return self._actual_bins

    def _recompute(self):
        v = self._values
        if len(v) < 2:
            self._has_data = False; return
        self._has_data = True
        self._mu = float(np.mean(v))
        self._sigma = float(np.std(v))

        vmax = float(np.max(v))
        # Determine nice bin width (whole number)
        if self._n_bins > 0:
            nb = self._n_bins
            bw = max(1, math.ceil(vmax / nb))
        else:
            nb = min(max(int(math.sqrt(len(v))), 5), 30)
            bw = max(1, math.ceil(vmax / nb))

        # Bins: 0, bw, 2*bw, ... up past vmax
        edges = []
        e = 0
        while e <= vmax + bw:
            edges.append(e)
            e += bw
        edges = np.array(edges, dtype=float)

        counts, _ = np.histogram(v, bins=edges)
        # Remove trailing empty bins
        while len(counts) > 1 and counts[-1] == 0:
            counts = counts[:-1]
            edges = edges[:len(counts) + 1]

        self._actual_bins = len(counts)
        self._counts = counts.tolist()
        self._bins = edges.tolist()
        self._bin_labels = [
            f"{int(edges[i])}-{int(edges[i+1])}{self._unit}"
            for i in range(len(counts))
        ]

    def clear_data(self):
        self._has_data = False
        self._values = np.array([])
        self._counts = []; self._bins = []; self._bin_labels = []
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
            p.end(); return

        ml, mr, mt, mb = self.MARGIN_LEFT, self.MARGIN_RIGHT, self.MARGIN_TOP, self.MARGIN_BOTTOM
        px, py, pw, ph = ml, mt, w - ml - mr, h - mt - mb
        if pw < 20 or ph < 20: p.end(); return

        p.fillRect(int(px), int(py), int(pw), int(ph), self.PLOT_BG)
        p.setPen(QPen(self.AXIS_COLOR, 1))
        p.drawRect(int(px), int(py), int(pw), int(ph))

        mc = max(self._counts)
        if mc == 0: p.end(); return
        x_min, x_max = self._bins[0], self._bins[-1]
        x_range = x_max - x_min
        if x_range <= 0: p.end(); return
        y_max = self._nice_ceil(mc * 1.1)

        def tx(val): return px + (val - x_min) / x_range * pw
        def ty(val): return py + ph - (val / y_max) * ph

        # Grid
        p.setPen(QPen(self.GRID_COLOR, 1, Qt.PenStyle.DashLine))
        nyt = min(6, max(2, int(y_max)))
        ys = y_max / nyt
        for i in range(1, nyt + 1):
            p.drawLine(QPointF(px, ty(i * ys)), QPointF(px + pw, ty(i * ys)))

        # Bars
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
            path = QPainterPath(); started = False
            for j in range(200):
                xv = x_min + x_range * j / 199
                yv = ((1.0 / (self._sigma * math.sqrt(2 * math.pi))) *
                      math.exp(-0.5 * ((xv - self._mu) / self._sigma) ** 2))
                yv *= bw * len(self._values)
                xp, yp = tx(xv), max(py, min(py + ph, ty(yv)))
                if not started: path.moveTo(xp, yp); started = True
                else: path.lineTo(xp, yp)
            p.drawPath(path)

        # Y ticks
        p.setPen(QPen(self.TEXT_COLOR))
        tf = QFont("Arial", 9); p.setFont(tf); fm = QFontMetrics(tf)
        for i in range(nyt + 1):
            yv = i * ys; yp = ty(yv)
            p.drawLine(QPointF(px - 4, yp), QPointF(px, yp))
            lbl = str(int(round(yv)))
            p.drawText(QPointF(px - fm.horizontalAdvance(lbl) - 7, yp + fm.ascent() / 2 - 1), lbl)

        # X bin labels
        sf = QFont("Arial", 7); p.setFont(sf); sfm = QFontMetrics(sf)
        for i in range(len(self._bin_labels)):
            cx = (tx(self._bins[i]) + tx(self._bins[i + 1])) / 2
            p.save(); p.translate(cx, py + ph + 6); p.rotate(45)
            p.drawText(QPointF(0, sfm.ascent()), self._bin_labels[i])
            p.restore()

        # Axis labels
        lf = QFont("Arial", 10, QFont.Weight.Bold); p.setFont(lf); p.setPen(QPen(self.LABEL_COLOR))
        lfm = QFontMetrics(lf)
        xtw = lfm.horizontalAdvance(self._xlabel)
        p.drawText(QPointF(px + pw / 2 - xtw / 2, h - 3), self._xlabel)
        p.save(); p.translate(13, py + ph / 2); p.rotate(-90)
        ylbl = "Number of Grains"; ytw = lfm.horizontalAdvance(ylbl)
        p.drawText(QPointF(-ytw / 2, 0), ylbl); p.restore()
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
        self.value_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter); lay.addWidget(self.value_lbl)
        self.unit_lbl = None
        if unit:
            self.unit_lbl = QLabel(unit)
            self.unit_lbl.setStyleSheet("font-size:10px;color:#7777aa;border:none;")
            self.unit_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter); lay.addWidget(self.unit_lbl)
        n = QLabel(label); n.setStyleSheet("font-size:10px;color:#9999bb;border:none;")
        n.setAlignment(Qt.AlignmentFlag.AlignCenter); n.setWordWrap(True); lay.addWidget(n)

    def set_value(self, v): self.value_lbl.setText(v)
    def set_unit(self, u):
        if self.unit_lbl: self.unit_lbl.setText(u)


class ResultsPanel(QWidget):
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

        hc = QWidget(); hl = QVBoxLayout(hc); hl.setContentsMargins(0, 0, 0, 0); hl.setSpacing(4)
        bb = QHBoxLayout()
        bl = QLabel("Bins:"); bl.setStyleSheet("color:#aaaacc;font-size:11px;"); bb.addWidget(bl)
        self.bin_spin = QSpinBox(); self.bin_spin.setRange(3, 100); self.bin_spin.setValue(0)
        self.bin_spin.setToolTip("Number of histogram bins"); self.bin_spin.setFixedWidth(70)
        self.bin_spin.valueChanged.connect(self._on_bin_changed); bb.addWidget(self.bin_spin)
        bb.addStretch(); hl.addLayout(bb)
        self.histogram = HistogramWidget(); hl.addWidget(self.histogram)
        self.tabs.addTab(hc, "Grain Size Distribution")

        tw = QWidget(); tl = QVBoxLayout(tw); tl.setContentsMargins(0, 4, 0, 0)
        self.table = QTableWidget(); self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        tl.addWidget(self.table); self.tabs.addTab(tw, "Grain Table")

        ss = QScrollArea(); ss.setWidgetResizable(True); ss.setFrameShape(QFrame.Shape.NoFrame)
        self.full_stats = QLabel()
        self.full_stats.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.full_stats.setWordWrap(True)
        self.full_stats.setStyleSheet("padding:10px;color:#dcdce6;font-family:monospace;font-size:12px;")
        ss.setWidget(self.full_stats); self.tabs.addTab(ss, "Statistics")

    def _on_bin_changed(self, val):
        self.histogram.set_bin_count(val); self.bin_count_changed.emit(val)

    def get_bin_count(self):
        if self._current_result and self._current_result.grains:
            return self.histogram.get_bin_count()
        return 0

    def display_results(self, result: AnalysisResult):
        self._current_result = result
        self.empty_label.setVisible(False)
        self.stats_widget.setVisible(True); self.tabs.setVisible(True)

        _, am, _, dm = smart_unit(result.px_per_um)
        au_label, du_label = ("nm²", "nm") if am > 1 else ("µm²", "µm")
        has_cal = result.has_calibration

        self.card_count.set_value(str(result.grain_count))
        if has_cal:
            self.card_coverage.set_value(f"{result.grain_coverage_pct:.1f}")
            self.card_mean_area.set_value(smart_format(result.mean_area_um2 * am))
            self.card_mean_area.set_unit(au_label)
            self.card_mean_diam.set_value(smart_format(result.mean_diameter_um * dm))
            self.card_mean_diam.set_unit(du_label)
        else:
            self.card_coverage.set_value("N/A")
            areas = [g.area_px for g in result.grains]
            self.card_mean_area.set_value(f"{np.mean(areas):.0f}" if areas else "—")
            self.card_mean_area.set_unit("px²")
            diams = [g.equivalent_diameter_px for g in result.grains]
            self.card_mean_diam.set_value(f"{np.mean(diams):.1f}" if diams else "—")
            self.card_mean_diam.set_unit("px")
        self.card_circ.set_value(f"{result.mean_circularity:.3f}")
        self.card_ar.set_value(f"{result.mean_aspect_ratio:.3f}")

        self._update_histogram(result)
        self._populate_table(result, au_label, du_label, am, dm)
        self._populate_stats(result, au_label, du_label, am, dm)
        self.tabs.setCurrentIndex(0)

    def _update_histogram(self, result):
        if not result.grains: self.histogram.clear_data(); return
        _, am, _, _ = smart_unit(result.px_per_um)
        au = "nm²" if am > 1 else "µm²"
        if result.has_calibration:
            values = np.array([g.area_um2 * am for g in result.grains])
            xlabel = f"Grain Area ({au})"
        else:
            values = np.array([g.area_px for g in result.grains])
            xlabel = "Grain Area (px²)"; au = "px²"
        nb = self.bin_spin.value()
        if nb < 3: nb = 0
        self.histogram.set_data(values, xlabel, au, nb)
        actual = self.histogram.get_bin_count()
        self.bin_spin.blockSignals(True); self.bin_spin.setValue(actual); self.bin_spin.blockSignals(False)

    def _populate_table(self, result, au, du, am, dm):
        hc = result.has_calibration
        hdrs = (["ID", f"Area ({au})", f"Diam ({du})", "Circ.", "AR"]
                if hc else ["ID", "Area (px²)", "Diam (px)", "Circ.", "AR"])
        self.table.setColumnCount(len(hdrs)); self.table.setHorizontalHeaderLabels(hdrs)
        self.table.setRowCount(len(result.grains))
        for row, g in enumerate(result.grains):
            if hc:
                vals = [str(g.grain_id), smart_format(g.area_um2 * am),
                        smart_format(g.equivalent_diameter_um * dm),
                        f"{g.circularity:.3f}", f"{g.aspect_ratio:.3f}"]
            else:
                vals = [str(g.grain_id), f"{g.area_px:.0f}",
                        f"{g.equivalent_diameter_px:.1f}",
                        f"{g.circularity:.3f}", f"{g.aspect_ratio:.3f}"]
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, item)

    def _populate_stats(self, result, au, du, am, dm):
        lines = [f"GRAIN COUNT:   {result.grain_count}", ""]
        if result.has_calibration:
            lines += [f"Calibration:   {result.px_per_um:.4f} px/µm",
                      f"Grain coverage:{result.grain_coverage_pct:.2f}%", "",
                      f"── Area ({au}) ──────────────",
                      f"  Mean:    {smart_format(result.mean_area_um2 * am)}",
                      f"  Std Dev: {smart_format(result.std_area_um2 * am)}",
                      f"  Median:  {smart_format(result.median_area_um2 * am)}",
                      f"  Min:     {smart_format(result.min_area_um2 * am)}",
                      f"  Max:     {smart_format(result.max_area_um2 * am)}", "",
                      f"── Diameter ({du}) ──────────",
                      f"  Mean:    {smart_format(result.mean_diameter_um * dm)}",
                      f"  Std Dev: {smart_format(result.std_diameter_um * dm)}"]
        else:
            areas = [g.area_px for g in result.grains]
            if areas:
                lines += ["── Area (px²) ──────────────",
                          f"  Mean:    {np.mean(areas):.1f}",
                          f"  Std Dev: {np.std(areas):.1f}",
                          f"  Median:  {np.median(areas):.1f}"]
        lines += ["", "── Shape ───────────────────",
                   f"  Circularity:    {result.mean_circularity:.4f}",
                   f"  Aspect Ratio:   {result.mean_aspect_ratio:.4f}"]
        self.full_stats.setText("\n".join(lines))

    def clear(self):
        self._current_result = None
        self.empty_label.setVisible(True); self.stats_widget.setVisible(False)
        self.tabs.setVisible(False); self.table.setRowCount(0); self.histogram.clear_data()
