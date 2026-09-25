"""
Compare lots page (INN-43) -- a sub-page of Projects.

Layout spec
  header  : overline "Projects › Compare", title "Compare lots", subtitle
            (n lots · materials · baseline); actions: "Equivalence tolerance
            ± [0.50] G" spin box, "Back to Projects".
  banner  : one plain-words sentence from the Welch ANOVA across all lots.
  lots    : one row per lot -- star (set / clear baseline for its material),
            name + sample/material/fields, "G 7.02 ± 0.08", verdict badge and
            the equivalence sentence vs that material's baseline lot.
  bottom  : ΔG matrix (cells coloured green / amber / red by |ΔG|, CI in the
            tooltip) beside the overlaid ECD distribution chart.
  primary : read the verdicts; secondary: set the baseline, change tolerance.
  states  : empty (< 2 lots picked) · loading (skeletons) · error · not
            enough calibrated lots · populated.

Reached from Projects: select two or more lot cards → "Compare" in the
selection bar.  All folder IO runs on the thread pool; the statistics
(``core.lot_compare``) are tiny and run on the GUI thread.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QDoubleSpinBox, QHBoxLayout, QHeaderView, QSizePolicy, QTableWidget,
    QTableWidgetItem, QToolTip, QVBoxLayout, QWidget,
)

from core.lot_compare import (
    DEFAULT_MARGIN_G, Band, Equivalence, LotSummary, Verdict, compare_lots, tost_equivalence,
)
from ui.design.theme import ui_font
from ui.design.tokens import SPACE, TypeStyle
from ui.pages.common import PageHeader, scroll
from ui.widgets import (
    AnimatedButton, Badge, Card, EmptyState, FadeStackedWidget, IconButton, Skeleton, label,
)
from ui.widgets._base import ThemeAware, qcolor, tokens
from ui.workers import run_task

COMPARE_ICON = "mdi6.compare-horizontal"
STAR_ON, STAR_OFF = "mdi6.star", "mdi6.star-outline"

VERDICT_BADGE = {
    Verdict.EQUIVALENT: ("Equivalent", "success"),
    Verdict.NOT_EQUIVALENT: ("Not equivalent", "danger"),
    Verdict.INCONCLUSIVE: ("Can't tell yet", "warning"),
}
BAND_KIND = {Band.GREEN: "success", Band.AMBER: "warning", Band.RED: "danger"}
BAND_WORDS = {Band.GREEN: "within 0.25 G", Band.AMBER: "0.25 to 0.5 G apart",
              Band.RED: "more than 0.5 G apart"}


def _g(x: Optional[float], sign: bool = False) -> str:
    if x is None:
        return "—"
    return (f"{x:+.2f}" if sign else f"{x:.2f}").replace("-", "−")


# ======================================================================
# worker thread
# ======================================================================

def load_compare_data(root: str, lot_paths: Sequence[str]) -> dict:
    """Thread-pool: lot + sample metadata and every field of each lot (grain
    filters / manual removals honoured, as on the lot result card).  The
    baseline lot of each material is added when it isn't already picked."""
    from data.catalog import Catalog
    from data.models import read_json
    from data.workspace import Workspace
    from ui import hierarchy_ui as hui
    from ui.pages.lot_results import refilter_saved_field

    ws = Workspace(root)
    paths: List[Path] = []
    for p in lot_paths:
        p = Path(p)
        if p not in paths:
            paths.append(p)
    added: List[Path] = []
    for p in list(paths):
        base = ws.baseline_lot_for(p)
        if base is not None and base not in paths:
            paths.append(base)
            added.append(base)
    cat = Catalog(root)
    lots = []
    for p in paths:
        def meta(f):
            try:
                return read_json(f) if f.exists() else {}
            except (OSError, ValueError):
                return {}
        lm, sm = meta(p / "lot.json"), meta(p.parent / "sample.json")
        lots.append({
            "path": p,
            "id": hui.id_value("lot", lm, p),
            "sample": hui.id_value("sample", sm, p.parent),
            "material": str(sm.get("material") or "").strip(),
            "group": ws.baseline_group(p),
            "is_baseline": bool(lm.get("is_baseline")),
            "fields": cat.fields_for_lot(p, refilter=refilter_saved_field),
            "added": p in added,
        })
    # unique display names (the same lot number can exist under two samples)
    counts: Dict[str, int] = {}
    for l in lots:
        counts[l["id"]] = counts.get(l["id"], 0) + 1
    for l in lots:
        l["name"] = l["id"] if counts[l["id"]] == 1 else f"{l['sample']} › {l['id']}"
    return {"lots": lots}


# ======================================================================
# widgets
# ======================================================================

class DeltaMatrix(ThemeAware, QTableWidget):
    """ΔG = G(column) − G(row); cells coloured by |ΔG| band (design tokens)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._matrix = []
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setShowGrid(False)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.verticalHeader().setDefaultSectionSize(40)
        self.setWordWrap(False)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName("Grain size difference matrix")
        self._connect_theme()

    def _on_theme_changed(self) -> None:
        self._paint_cells()

    def set_matrix(self, names: List[str], matrix) -> None:
        self._matrix = matrix
        n = len(names)
        self.clear()
        self.setRowCount(n)
        self.setColumnCount(n)
        self.setHorizontalHeaderLabels(names)
        self.setVerticalHeaderLabels(names)
        for i in range(n):
            for j in range(n):
                c = matrix[i][j]
                it = QTableWidgetItem("—" if i == j or c.dG is None else _g(c.dG, sign=True))
                it.setTextAlignment(Qt.AlignCenter)
                it.setToolTip(self._tip(c, i == j))
                it.setData(Qt.UserRole, c.band.value)
                self.setItem(i, j, it)
        self._paint_cells()
        self.setFixedHeight(self.horizontalHeader().sizeHint().height() + 40 * n + 4)

    @staticmethod
    def _tip(c, diagonal: bool) -> str:
        if diagonal:
            return f"{c.label_i}: same lot"
        if c.dG is None:
            return f"{c.label_j} vs {c.label_i}: one of the lots has no calibrated G"
        word = "finer" if c.dG > 0 else "coarser"
        s = (f"{c.label_j} vs {c.label_i}: ΔG {_g(c.dG, True)} — {c.label_j} is "
             f"{word} ({BAND_WORDS.get(c.band, '')})")
        if c.ci_low is not None:
            s += (f"\n{round(c.conf * 100)} % confidence range: {_g(c.ci_low, True)} "
                  f"to {_g(c.ci_high, True)} G")
        else:
            s += "\nNo range: a lot has fewer than 2 measured fields"
        return s

    def band_at(self, i: int, j: int) -> str:
        it = self.item(i, j)
        return it.data(Qt.UserRole) if it else ""

    def _paint_cells(self) -> None:
        t = tokens()
        for i in range(self.rowCount()):
            for j in range(self.columnCount()):
                it = self.item(i, j)
                if it is None:
                    continue
                kind = None if i == j else BAND_KIND.get(Band(it.data(Qt.UserRole)))
                if kind:
                    sem = t.semantic(kind)
                    it.setBackground(QBrush(qcolor(sem.bg)))
                    it.setForeground(QBrush(qcolor(sem.fg)))
                    f = ui_font(TypeStyle(13, 600, 20))
                else:
                    it.setBackground(QBrush(qcolor(t.surface.surface2)))
                    it.setForeground(QBrush(qcolor(t.text.tertiary)))
                    f = ui_font(TypeStyle(13, 400, 20))
                it.setFont(f)


class EcdOverlayChart(ThemeAware, QWidget):
    """Overlaid ECD distributions (share of grains per bin, one line per lot),
    shared bins so the curves are comparable; hover shows each lot's share."""

    ML, MR, MT, MB = 48, 16, 34, 44

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(280)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAccessibleName("Grain size distributions of the compared lots")
        self._series: List[Tuple[str, np.ndarray, int]] = []
        self._edges = np.array([])
        self._shares: List[np.ndarray] = []
        self._connect_theme()

    def set_series(self, series: List[Tuple[str, Sequence[float]]]) -> None:
        """``series``: [(lot name, ECD values in µm)] -- empty lists allowed."""
        self._series = [(n, np.asarray(v, dtype=float), i) for i, (n, v) in enumerate(series)]
        pooled = np.concatenate([v for _n, v, _i in self._series if v.size] or [np.array([])])
        pooled = pooled[np.isfinite(pooled)]
        self._shares = []
        if pooled.size < 2:
            self._edges = np.array([])
        else:
            hi = float(np.percentile(pooled, 99.5))
            nb = int(min(24, max(8, math.sqrt(pooled.size) / 1.5)))
            self._edges = np.linspace(0.0, hi if hi > 0 else 1.0, nb + 1)
            for _n, v, _i in self._series:
                if v.size:
                    c, _ = np.histogram(np.clip(v, 0, self._edges[-1]), bins=self._edges)
                    self._shares.append(100.0 * c / max(1, v.size))
                else:
                    self._shares.append(np.zeros(nb))
        self.update()

    def has_data(self) -> bool:
        return self._edges.size > 0

    def _plot_rect(self) -> QRectF:
        return QRectF(self.ML, self.MT, self.width() - self.ML - self.MR,
                      self.height() - self.MT - self.MB)

    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), qcolor(t.surface.surface1))
        if not self.has_data():
            p.setPen(qcolor(t.text.tertiary))
            p.setFont(ui_font(TypeStyle(13, 400, 20)))
            p.drawText(self.rect(), Qt.AlignCenter, "No calibrated grain sizes to plot")
            return
        r = self._plot_rect()
        if r.width() < 40 or r.height() < 40:
            return
        ymax = max(float(s.max()) for s in self._shares) if self._shares else 1.0
        ymax = max(1.0, math.ceil(ymax * 1.15 / 5.0) * 5.0)
        x0, x1 = float(self._edges[0]), float(self._edges[-1])
        tx = lambda v: r.left() + (v - x0) / (x1 - x0) * r.width()   # noqa: E731
        ty = lambda v: r.bottom() - v / ymax * r.height()             # noqa: E731
        small = ui_font(TypeStyle(10, 400, 14))
        p.setFont(small)
        fm = QFontMetricsF(small)
        for k in range(6):
            yv = ymax * k / 5
            y = ty(yv)
            p.setPen(QPen(qcolor(t.border.subtle), 1, Qt.SolidLine if k == 0 else Qt.DotLine))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(qcolor(t.text.tertiary))
            p.drawText(QRectF(0, y - 8, r.left() - 6, 16), Qt.AlignRight | Qt.AlignVCenter,
                       f"{yv:g}")
        for k in range(6):
            xv = x0 + (x1 - x0) * k / 5
            x = tx(xv)
            p.drawText(QRectF(x - 30, r.bottom() + 4, 60, 16), Qt.AlignHCenter | Qt.AlignTop,
                       f"{xv:.3g}")
        p.setPen(qcolor(t.text.secondary))
        p.setFont(ui_font(TypeStyle(11, 600, 16)))
        p.drawText(QRectF(r.left(), self.height() - 20, r.width(), 16), Qt.AlignCenter,
                   "Equivalent circle diameter (µm)")
        p.save()
        p.translate(12, r.center().y())
        p.rotate(-90)
        p.drawText(QRectF(-r.height() / 2, -8, r.height(), 16), Qt.AlignCenter, "Share of grains (%)")
        p.restore()
        # series: frequency polygon through the bin centres, light fill
        centres = (self._edges[:-1] + self._edges[1:]) / 2.0
        for (name, v, i), share in zip(self._series, self._shares):
            if not v.size:
                continue
            col = QColor(t.dataviz[i % len(t.dataviz)])
            line = QPainterPath(QPointF(tx(centres[0]), ty(share[0])))
            for c, s in zip(centres[1:], share[1:]):
                line.lineTo(tx(c), ty(s))
            area = QPainterPath(line)
            area.lineTo(tx(centres[-1]), ty(0))
            area.lineTo(tx(centres[0]), ty(0))
            area.closeSubpath()
            fill = QColor(col)
            fill.setAlphaF(0.10)
            p.fillPath(area, fill)
            p.setPen(QPen(col, 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(line)
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            for c, s in zip(centres, share):
                p.drawEllipse(QPointF(tx(c), ty(s)), 2.2, 2.2)
        # legend (top, left-aligned, wraps onto the header strip)
        p.setFont(small)
        x, y = r.left(), 8.0
        for name, v, i in self._series:
            text = f"{name} ({v.size} grains)" if v.size else f"{name} (no data)"
            w = fm.horizontalAdvance(text) + 22
            if x + w > r.right() and x > r.left():
                break
            col = QColor(t.dataviz[i % len(t.dataviz)])
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(x, y + 3, 12, 12), 3, 3)
            p.setPen(qcolor(t.text.secondary))
            p.drawText(QPointF(x + 16, y + 3 + fm.ascent() - 1), text)
            x += w + 8

    def mouseMoveEvent(self, e) -> None:
        if not self.has_data():
            return
        r = self._plot_rect()
        pos = e.position()
        if not r.contains(pos):
            QToolTip.hideText()
            return
        x0, x1 = float(self._edges[0]), float(self._edges[-1])
        xv = x0 + (pos.x() - r.left()) / r.width() * (x1 - x0)
        b = int(min(len(self._edges) - 2, max(0, np.searchsorted(self._edges, xv) - 1)))
        lines = [f"{self._edges[b]:.3g}–{self._edges[b + 1]:.3g} µm"]
        for (name, v, _i), share in zip(self._series, self._shares):
            if v.size:
                lines.append(f"{name}: {share[b]:.1f} % of grains")
        QToolTip.showText(e.globalPosition().toPoint(), "\n".join(lines), self)


class LotRow(QWidget):
    """One lot: star · name/caption · G ± CI · verdict badge + sentence."""

    star_clicked = Signal(object)          # the lot dict

    def __init__(self, lot: dict, parent=None) -> None:
        super().__init__(parent)
        self.lot = lot
        h = QHBoxLayout(self)
        h.setContentsMargins(0, SPACE.sm, 0, SPACE.sm)
        h.setSpacing(SPACE.md)
        self.star = IconButton(STAR_OFF, "Set as baseline", checkable=True)
        self.star.clicked.connect(lambda: self.star_clicked.emit(self.lot))
        h.addWidget(self.star, 0, Qt.AlignTop)
        names = QVBoxLayout()
        names.setSpacing(0)
        self.name = label(lot["name"], "body_strong")
        self.caption = label("", "caption")
        names.addWidget(self.name)
        names.addWidget(self.caption)
        nw = QWidget()
        nw.setLayout(names)
        nw.setFixedWidth(230)
        h.addWidget(nw, 0, Qt.AlignTop)
        gcol = QVBoxLayout()
        gcol.setSpacing(0)
        self.g = label("", "h3")
        self.g_caption = label("", "caption")
        gcol.addWidget(self.g)
        gcol.addWidget(self.g_caption)
        gw = QWidget()
        gw.setLayout(gcol)
        gw.setFixedWidth(180)
        h.addWidget(gw, 0, Qt.AlignTop)
        vcol = QVBoxLayout()
        vcol.setSpacing(SPACE.xs)
        self.badge = Badge("", "neutral", dot=True)
        self.sentence = label("", tone="secondary")
        self.sentence.setWordWrap(True)
        self.sentence.setTextInteractionFlags(Qt.TextSelectableByMouse)
        vcol.addWidget(self.badge, 0, Qt.AlignLeft)
        vcol.addWidget(self.sentence)
        h.addLayout(vcol, 1)

    def set_state(self, summary: LotSummary, group_name: str, eq: Optional[Equivalence],
                  has_baseline: bool) -> None:
        lot = self.lot
        on = lot["is_baseline"]
        self.star.set_icon_name(STAR_ON if on else STAR_OFF)
        self.star.setChecked(on)
        tip = (f"Baseline for {group_name} — click to clear" if on
               else f"Set as the baseline lot for {group_name} (replaces any other)")
        self.star.setToolTip(tip)
        self.star.setAccessibleName(tip)
        mat = lot["material"] or "no material set"
        n_all = summary.n + summary.n_dropped
        cap = f"{lot['sample']} · {mat} · {summary.n} of {n_all} field{'s' if n_all != 1 else ''}"
        if lot.get("added"):
            cap += " · added as baseline"
        self.caption.setText(cap)
        self.caption.setToolTip(f"Sample {lot['sample']}, material {mat}. {summary.n} field(s) "
                                f"with a calibrated G; {summary.n_dropped} excluded or "
                                "uncalibrated.")
        if summary.mean is None:
            self.g.setText("G —")
            self.g_caption.setText("not calibrated")
            self._badge("No calibrated results", "neutral")
            self.sentence.setText("None of this lot's analysed images has a scale set, so it is "
                                  "left out of the comparison. Set the scale bar (Ctrl+K) and "
                                  "analyse again.")
            return
        if summary.ci_low is not None:
            self.g.setText(f"G {_g(summary.mean)} ± {_g(summary.ci_high - summary.mean)}")
            self.g_caption.setText(f"{round(summary.conf * 100)} % confidence range")
        else:
            self.g.setText(f"G {_g(summary.mean)}")
            self.g_caption.setText("1 field — no range yet")
        if on:
            self._badge("Baseline", "accent")
            self.sentence.setText(f"Reference lot for {group_name}. Other lots of this material "
                                  "are checked against it.")
        elif eq is not None:
            text, kind = VERDICT_BADGE[eq.verdict]
            self._badge(text, kind)
            self.sentence.setText(eq.sentence)
        elif has_baseline:
            self._badge("No baseline", "neutral")
            self.sentence.setText(f"The baseline lot for {group_name} has no calibrated results.")
        else:
            self._badge("No baseline", "neutral")
            self.sentence.setText(f"No baseline lot for {group_name} yet — click the star on the "
                                  "qualified lot to make it the reference.")

    def _badge(self, text: str, kind: str) -> None:
        self.badge.set_kind(kind)
        self.badge.set_text(text)

    def verdict_text(self) -> str:
        return self.badge.text()


# ======================================================================
# page
# ======================================================================

class LotComparePage(QWidget):
    """INN-43 comparison page (see module docstring)."""

    back_requested = Signal()

    def __init__(self, state, toasts=None, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.toasts = toasts
        self.lot_paths: List[Path] = []
        self.lots: List[dict] = []
        self.rows: List[LotRow] = []
        self.summaries: Dict[str, LotSummary] = {}
        self.comparison = None
        self._gen = 0
        self._loading = False
        self._build()

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(SPACE.xl, SPACE.xl, SPACE.xl, 0)
        v.setSpacing(SPACE.lg)
        self.header = PageHeader("Projects › Compare", "Compare lots", "")
        tol = QHBoxLayout()
        tol.setSpacing(SPACE.xs)
        tol.addWidget(label("Equivalence tolerance  ±", tone="secondary"))
        self.margin = QDoubleSpinBox()
        self.margin.setRange(0.05, 3.0)
        self.margin.setSingleStep(0.05)
        self.margin.setDecimals(2)
        self.margin.setSuffix(" G")
        self.margin.setValue(float(self.state.ui_state.get("compare_margin_G",
                                                          DEFAULT_MARGIN_G) or DEFAULT_MARGIN_G))
        self.margin.setToolTip("How far a lot's average ASTM G may be from the baseline and "
                               "still count as the same (default ± 0.5 G)")
        self.margin.setAccessibleName("Equivalence tolerance in G")
        self.margin.valueChanged.connect(self._on_margin)
        tol.addWidget(self.margin)
        tw = QWidget()
        tw.setLayout(tol)
        self.header.actions.addWidget(tw)
        self.back_btn = AnimatedButton("Back to Projects", "mdi6.arrow-left", "secondary")
        self.back_btn.setToolTip("Return to the Projects page (Esc)")
        self.back_btn.clicked.connect(self.back_requested)
        self.header.actions.addWidget(self.back_btn)
        v.addWidget(self.header)

        self.stack = FadeStackedWidget()
        v.addWidget(self.stack, 1)
        self.empty = EmptyState(
            COMPARE_ICON, "Pick two or more lots to compare",
            "In Projects, open a sample, Ctrl+click two or more lot cards and choose Compare "
            "in the selection bar.", "Go to Projects", "projects")
        self.empty.action_triggered.connect(self.back_requested)
        self.stack.addWidget(self.empty)

        load = QWidget()
        lv = QVBoxLayout(load)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(SPACE.md)
        for h in (48, 180, 260):
            lv.addWidget(Skeleton(height=h))
        lv.addStretch(1)
        self.loading = load
        self.stack.addWidget(load)

        self.content = QWidget()
        cv = QVBoxLayout(self.content)
        cv.setContentsMargins(0, 0, SPACE.sm, SPACE.xl)
        cv.setSpacing(SPACE.lg)
        self.anova_card = Card(elevation=1)
        ah = QHBoxLayout()
        ah.setSpacing(SPACE.md)
        self.anova_badge = Badge("", "neutral", dot=True)
        self.anova_text = label("")
        self.anova_text.setWordWrap(True)
        ah.addWidget(self.anova_badge, 0, Qt.AlignTop)
        ah.addWidget(self.anova_text, 1)
        self.anova_card.body_layout().addLayout(ah)
        cv.addWidget(self.anova_card)
        self.lots_card = Card("Lots", "Star = baseline (qualified reference) lot of the material")
        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(0)
        self.lots_card.body_layout().addLayout(self.rows_box)
        cv.addWidget(self.lots_card)
        bottom = QHBoxLayout()
        bottom.setSpacing(SPACE.lg)
        self.matrix_card = Card("Grain size differences (ΔG)", "Column lot minus row lot")
        self.matrix_note = label("Positive = the column lot has finer grains. Hover a cell for "
                                 "its 95 % confidence range.", "caption")
        self.matrix_note.setWordWrap(True)
        self.matrix_card.add_widget(self.matrix_note)
        self.matrix = DeltaMatrix()
        self.matrix_card.add_widget(self.matrix)
        leg = QHBoxLayout()
        leg.setSpacing(SPACE.sm)
        self.legend = leg
        for band in (Band.GREEN, Band.AMBER, Band.RED):
            b = Badge(BAND_WORDS[band].capitalize(), BAND_KIND[band], dot=True)
            leg.addWidget(b)
        leg.addStretch(1)
        self.matrix_card.body_layout().addLayout(leg)
        self.matrix_card.body_layout().addStretch(1)
        self.chart_card = Card("Grain size distributions", "Share of each lot's grains")
        self.chart = EcdOverlayChart()
        self.chart_card.add_widget(self.chart, 1)
        bottom.addWidget(self.matrix_card, 1)
        bottom.addWidget(self.chart_card, 1)
        cv.addLayout(bottom)
        cv.addStretch(1)
        self.content_scroll = scroll(self.content)
        self.stack.addWidget(self.content_scroll)

        self.problem = EmptyState("warning", "Not enough calibrated results", "",
                                  "Back to Projects", "mdi6.arrow-left")
        self.problem.action_triggered.connect(self.back_requested)
        self.stack.addWidget(self.problem)
        self.stack.setCurrentWidget(self.empty)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape:
            self.back_requested.emit()
            return
        super().keyPressEvent(e)

    # ------------------------------------------------------------------ API
    def state_name(self) -> str:
        w = self.stack.currentWidget()
        if w is self.empty:
            return "empty"
        if w is self.loading:
            return "loading"
        if w is self.problem:
            return "problem"
        if w is None:
            return "empty"
        return "populated"

    def is_loading(self) -> bool:
        return self._loading

    def set_lots(self, paths: Sequence) -> None:
        """Compare these lot folders (loads off the GUI thread)."""
        self.lot_paths = [Path(p) for p in paths]
        self._gen += 1
        gen = self._gen
        if len(self.lot_paths) < 2:
            self._loading = False
            self.lots = []
            self.header.subtitle.setVisible(False)
            self.stack.set_current_widget(self.empty)
            return
        self._loading = True
        self.header.subtitle.setText(f"Loading {len(self.lot_paths)} lots…")
        self.header.subtitle.setVisible(True)
        self.stack.set_current_widget(self.loading)

        def done(data):
            if gen != self._gen:
                return
            self._loading = False
            self.set_data(data)

        def failed(msg):
            if gen != self._gen:
                return
            self._loading = False
            self.problem.set_texts("Couldn't load the lots", msg.splitlines()[0])
            self.stack.set_current_widget(self.problem)

        run_task(load_compare_data, str(self.state.root), [str(p) for p in self.lot_paths],
                 on_done=done, on_error=failed)

    def set_data(self, data: dict) -> None:
        # baseline lot(s) first: the reference reads as the first row / column
        self.lots = sorted(data["lots"], key=lambda l: not l["is_baseline"])
        usable = [l for l in self.lots
                  if any(getattr(f, "included", True) and getattr(f, "G", None) is not None
                         for f in l["fields"])]
        if len(usable) < 2:
            missing = [l["name"] for l in self.lots if l not in usable]
            body = ("Two or more lots need analysed images with a scale set. "
                    + (f"Not calibrated yet: {', '.join(missing)}. " if missing else "")
                    + "Set the scale bar (Ctrl+K), analyse, then compare again.")
            self.problem.set_texts("Not enough calibrated results", body)
            self.stack.set_current_widget(self.problem)
            return
        self._rebuild_rows()
        self._recompute()
        self.stack.set_current_widget(self.content_scroll)

    # ------------------------------------------------------------------ compute
    def _group_name(self, lot: dict) -> str:
        return lot["material"] or f"sample {lot['sample']}"

    def _rebuild_rows(self) -> None:
        while self.rows_box.count():
            it = self.rows_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.rows = []
        for k, lot in enumerate(self.lots):
            if k:
                self.rows_box.addWidget(_Rule())
            row = LotRow(lot)
            row.star_clicked.connect(self._on_star)
            self.rows_box.addWidget(row)
            self.rows.append(row)

    def _recompute(self) -> None:
        comp = compare_lots({l["name"]: l["fields"] for l in self.lots})
        self.comparison = comp
        self.summaries = {s.label: s for s in comp.summaries}
        margin = float(self.margin.value())
        calibrated = [s for s in comp.summaries if s.mean is not None]
        names = [s.label for s in calibrated]
        idx = [comp.summaries.index(s) for s in calibrated]
        self.matrix.set_matrix(names, [[comp.matrix[i][j] for j in idx] for i in idx])
        self._set_anova(comp.anova, len(calibrated))
        self.verdicts: Dict[str, Equivalence] = {}
        for row in self.rows:
            lot = row.lot
            s = self.summaries[lot["name"]]
            base = next((b for b in self.lots if b["is_baseline"] and b["group"] == lot["group"]),
                        None)
            eq = None
            if base is not None and base is not lot and s.mean is not None \
                    and self.summaries[base["name"]].mean is not None:
                eq = tost_equivalence(s, self.summaries[base["name"]], margin)
                self.verdicts[lot["name"]] = eq
            row.set_state(s, self._group_name(lot), eq, base is not None)
        self.chart.set_series([
            (l["name"], [x for f in l["fields"] if getattr(f, "included", True)
                         and getattr(f, "G", None) is not None for x in (f.ecds_um or [])])
            for l in self.lots if self.summaries[l["name"]].mean is not None])
        mats = sorted({l["material"] for l in self.lots if l["material"]})
        bases = [l["name"] for l in self.lots if l["is_baseline"]]
        sub = f"{len(self.lots)} lots"
        if mats:
            sub += " · " + ", ".join(mats)
        sub += (" · baseline " + ", ".join(bases)) if bases else " · no baseline set"
        self.header.subtitle.setText(sub)
        self.header.subtitle.setVisible(True)

    def _set_anova(self, a, k: int) -> None:
        if a.p is None:
            self.anova_badge.set_kind("neutral")
            self.anova_badge.set_text("Overall check")
            why = {"need at least 2 lots with 2 or more measured fields each":
                   "it needs at least two lots with two or more measured fields each",
                   "a lot has zero field-to-field scatter; Welch ANOVA undefined":
                   "a lot has identical results on every field"}.get(a.note, a.note)
            self.anova_text.setText(f"The overall check across all lots isn't available: {why}.")
            self.anova_text.setToolTip("Welch ANOVA (unequal variances) on the per-field G values")
            return
        p_txt = "p < 0.001" if a.p < 0.001 else f"p = {a.p:.3f}"
        if a.p < 0.05:
            self.anova_badge.set_kind("warning")
            self.anova_badge.set_text("Lots differ")
            text = (f"Across these {a.k} lots, at least one lot's average grain size is "
                    f"different from the others ({p_txt}). The matrix below shows which.")
        else:
            self.anova_badge.set_kind("success")
            self.anova_badge.set_text("No clear difference")
            text = (f"No clear difference in average grain size across these {a.k} lots "
                    f"({p_txt}). This alone does not prove they are the same — the baseline "
                    "check does.")
        if a.note:
            text += f" Note: {a.note}."
        self.anova_text.setText(text)
        self.anova_text.setToolTip(
            f"Welch ANOVA on per-field G: F = {a.F:.2f}, df = {a.df1:.0f} and {a.df2:.1f}, "
            f"{p_txt}")

    # ------------------------------------------------------------------ actions
    def _on_margin(self, value: float) -> None:
        self.state.ui_state["compare_margin_G"] = float(value)
        if self.lots and self.state_name() == "populated":
            self._recompute()

    def _on_star(self, lot: dict) -> None:
        on = not lot["is_baseline"]
        prev = next((l for l in self.lots if l["is_baseline"] and l["group"] == lot["group"]
                     and l is not lot), None)
        self.set_baseline(lot, on, prev_path=prev["path"] if prev else None)

    def set_baseline(self, lot: dict, on: bool, prev_path: Optional[Path] = None,
                     announce: bool = True) -> None:
        """Persist the flag (thread pool), then update the view."""
        ws = self.state.workspace

        def done(_changed):
            for l in self.lots:
                if on and l["group"] == lot["group"]:
                    l["is_baseline"] = l is lot
                elif l is lot:
                    l["is_baseline"] = False
            if self.lots and self.state_name() == "populated":
                self._recompute()
            if announce and self.toasts is not None:
                grp = self._group_name(lot)
                msg = (f"{lot['name']} is now the baseline for {grp}." if on
                       else f"{grp} has no baseline lot now.")
                undo = (lambda: self._undo_baseline(lot, on, prev_path))
                self.toasts.show_toast("Baseline updated", msg, "success",
                                       action_text="Undo", on_action=undo)

        def failed(msg):
            if self.toasts is not None:
                self.toasts.show_toast("Couldn't change the baseline", msg.splitlines()[0],
                                       "error")

        run_task(ws.set_baseline_lot, lot["path"], on, on_done=done, on_error=failed)

    def _undo_baseline(self, lot: dict, was_on: bool, prev_path: Optional[Path]) -> None:
        if prev_path is not None:
            prev = next((l for l in self.lots if l["path"] == prev_path), None)
            if prev is not None:
                self.set_baseline(prev, True, announce=False)
                return
            run_task(self.state.workspace.set_baseline_lot, prev_path, True)
        self.set_baseline(lot, not was_on, announce=False)

    def row_for(self, name: str) -> Optional[LotRow]:
        return next((r for r in self.rows if r.lot["name"] == name), None)


class _Rule(ThemeAware, QWidget):
    """1 px separator between lot rows (token colour)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(1)
        self._connect_theme()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), qcolor(tokens().border.subtle))
