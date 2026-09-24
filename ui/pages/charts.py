"""Themed histogram with the v2.3 binning (whole-number bins starting at 0)
and token colours.  ``set_binned`` accepts bins computed elsewhere (the report
preview passes ``reports.charts.build_bins`` output so the in-app chart uses
exactly the bins the Excel / PowerPoint export will use)."""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ui.design.theme import theme_manager, ui_font
from ui.design.tokens import TYPE, TypeStyle
from ui.widgets._base import qcolor, tokens


class HistogramBase(QWidget):
    """Data + binning half of the v2.3 ``HistogramWidget`` (painting lives in
    :class:`ThemedHistogram`)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._values = np.array([])
        self._xlabel = "Grain Area"
        self._unit = ""
        self._n_bins = 0
        self._bins: list = []
        self._counts: list = []
        self._bin_labels: list = []
        self._mu = 0.0
        self._sigma = 0.0
        self._has_data = False
        self._actual_bins = 0

    def set_data(self, values, xlabel="Grain Area", unit="", n_bins=0) -> None:
        self._values = np.asarray(values, dtype=float)
        self._xlabel = xlabel
        self._unit = unit
        self._n_bins = n_bins
        self._recompute()
        self.update()

    def set_binned(self, values: Sequence[float], edges: Sequence[float],
                   counts: Sequence[int], labels: Sequence[str], xlabel: str = "",
                   unit: str = "") -> None:
        """Show precomputed bins (edges has len(counts)+1 entries)."""
        self._values = np.asarray(list(values), dtype=float)
        self._xlabel, self._unit = xlabel, unit
        self._has_data = len(counts) > 0 and len(self._values) >= 2
        self._counts = [int(c) for c in counts]
        self._bins = [float(e) for e in edges]
        self._bin_labels = list(labels)
        self._actual_bins = len(self._counts)
        if self._has_data:
            self._mu = float(np.mean(self._values))
            self._sigma = float(np.std(self._values))
        self.update()

    def set_bin_count(self, n) -> None:
        self._n_bins = n
        if len(self._values) >= 2:
            self._recompute()
            self.update()

    def get_bin_count(self) -> int:
        return self._actual_bins

    def _recompute(self) -> None:
        v = self._values
        if len(v) < 2:
            self._has_data = False
            return
        self._has_data = True
        self._mu = float(np.mean(v))
        self._sigma = float(np.std(v))
        vmax = float(np.max(v))
        nb = self._n_bins if self._n_bins > 0 else min(max(int(math.sqrt(len(v))), 5), 30)
        bw = max(1, math.ceil(vmax / nb))
        edges = []
        e = 0
        while e <= vmax + bw:
            edges.append(e)
            e += bw
        edges = np.array(edges, dtype=float)
        counts, _ = np.histogram(v, bins=edges)
        while len(counts) > 1 and counts[-1] == 0:
            counts = counts[:-1]
            edges = edges[:len(counts) + 1]
        self._actual_bins = len(counts)
        self._counts = counts.tolist()
        self._bins = edges.tolist()
        self._bin_labels = [f"{int(edges[i])}-{int(edges[i + 1])}{self._unit}"
                            for i in range(len(counts))]

    def clear_data(self) -> None:
        self._has_data = False
        self._values = np.array([])
        self._counts, self._bins, self._bin_labels = [], [], []
        self.update()

    @staticmethod
    def _nice_ceil(value):
        if value <= 0:
            return 1
        mag = 10 ** math.floor(math.log10(value))
        r = value / mag
        if r <= 1:
            return mag
        if r <= 2:
            return 2 * mag
        if r <= 5:
            return 5 * mag
        return 10 * mag


class ThemedHistogram(HistogramBase):
    MARGIN_LEFT = 46
    MARGIN_RIGHT = 12
    MARGIN_TOP = 14
    MARGIN_BOTTOM = 58

    def __init__(self, parent=None, series: int = 0) -> None:
        super().__init__(parent)
        self._series = series
        self.setMinimumHeight(220)
        theme_manager().theme_changed.connect(self._on_theme)

    def _on_theme(self, _mode: str) -> None:
        self.update()

    def counts(self):
        return list(self._counts)

    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, qcolor(t.surface.surface1))
        if not self._has_data or not self._counts:
            p.setPen(qcolor(t.text.tertiary))
            p.setFont(ui_font(TYPE.body))
            p.drawText(QRectF(0, 0, w, h), Qt.AlignCenter,
                       "Analyse the image to see the distribution")
            return
        ml, mr, mt, mb = self.MARGIN_LEFT, self.MARGIN_RIGHT, self.MARGIN_TOP, self.MARGIN_BOTTOM
        px, py, pw, ph = ml, mt, w - ml - mr, h - mt - mb
        if pw < 20 or ph < 20:
            return
        mc = max(self._counts)
        x_min, x_max = self._bins[0], self._bins[-1]
        x_range = x_max - x_min
        if mc == 0 or x_range <= 0:
            return
        y_max = self._nice_ceil(mc * 1.1)

        def tx(v):
            return px + (v - x_min) / x_range * pw

        def ty(v):
            return py + ph - (v / y_max) * ph

        grid = qcolor(t.border.subtle)
        f = ui_font(TypeStyle(10, 400, 14))
        p.setFont(f)
        fm = QFontMetricsF(f)
        nyt = min(5, max(2, int(y_max)))
        ys = y_max / nyt
        for i in range(0, nyt + 1):
            yv = i * ys
            yp = ty(yv)
            p.setPen(QPen(grid, 1, Qt.SolidLine if i == 0 else Qt.DotLine))
            p.drawLine(QPointF(px, yp), QPointF(px + pw, yp))
            lbl = str(int(round(yv)))
            p.setPen(qcolor(t.text.tertiary))
            p.drawText(QRectF(0, yp - 8, px - 8, 16), Qt.AlignRight | Qt.AlignVCenter, lbl)

        col = QColor(t.dataviz[self._series % len(t.dataviz)])
        for i, c in enumerate(self._counts):
            x0, x1 = tx(self._bins[i]), tx(self._bins[i + 1])
            top, bot = ty(c), ty(0)
            r = QRectF(x0 + 1, top, max(1.0, x1 - x0 - 2), bot - top)
            g = QLinearGradient(r.topLeft(), r.bottomLeft())
            c0 = QColor(col); c0.setAlphaF(0.95)
            c1 = QColor(col); c1.setAlphaF(0.55)
            g.setColorAt(0, c0)
            g.setColorAt(1, c1)
            path = QPainterPath()
            path.addRoundedRect(r, 2, 2)
            p.fillPath(path, g)

        if self._sigma > 0 and len(self._values) > 1:
            curve = QColor(t.dataviz[(self._series + 1) % len(t.dataviz)])
            pen = QPen(curve, 2.0)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            bw = self._bins[1] - self._bins[0]
            path = QPainterPath()
            for j in range(160):
                xv = x_min + x_range * j / 159
                yv = ((1.0 / (self._sigma * math.sqrt(2 * math.pi))) *
                      math.exp(-0.5 * ((xv - self._mu) / self._sigma) ** 2)) * bw * len(self._values)
                pt = QPointF(tx(xv), max(py, min(py + ph, ty(yv))))
                if j == 0:
                    path.moveTo(pt)
                else:
                    path.lineTo(pt)
            p.drawPath(path)

        # x labels (rotated, thinned when crowded)
        p.setPen(qcolor(t.text.tertiary))
        step = max(1, int(math.ceil(len(self._bin_labels) * 14 / max(1, pw))))
        for i in range(0, len(self._bin_labels), step):
            cx = (tx(self._bins[i]) + tx(self._bins[i + 1])) / 2
            p.save()
            p.translate(cx - 3, py + ph + 6)
            p.rotate(45)
            p.drawText(QPointF(0, fm.ascent()), self._bin_labels[i])
            p.restore()
        fa = ui_font(TypeStyle(11, 600, 16))
        p.setFont(fa)
        p.setPen(qcolor(t.text.secondary))
        p.drawText(QRectF(px, h - 18, pw, 16), Qt.AlignCenter, self._xlabel)
        p.save()
        p.translate(12, py + ph / 2)
        p.rotate(-90)
        p.drawText(QRectF(-ph / 2, -8, ph, 16), Qt.AlignCenter, "Number of grains")
        p.restore()
