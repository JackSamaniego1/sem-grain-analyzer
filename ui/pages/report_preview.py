"""
Live previews for the report designer's centre column — one widget per
outline selection, built by :func:`make_preview`.  Each preview reads the
page's ``ReportModel`` and exposes ``refresh()`` so inspector edits (title,
units, bins …) re-render without rebuilding the widget.

The page object passed in must provide ``model``, ``state``, ``edited(what)``,
``image_doc(img)``, ``pixmaps(img)`` and ``set_grain_included(img_id, gid, on)``.
"""
from __future__ import annotations

import os
from typing import List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLineEdit, QPlainTextEdit, QScrollArea,
    QTableView, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from reports.charts import build_bins, resolve_units
from ui.design.tokens import SPACE
from ui.filtering import REASON_LABELS
from ui.format import smart_format
from ui.pages.charts import ThemedHistogram
from ui.pages.report_builder import (
    SECTION_COLORS, SECTION_TARGETS, combined_values, overview_table,
)
from ui.pages.report_widgets import (
    CoverSlide, ImageSlide, MethodsSlide, OverviewSheet, PerImageBars, Swatch, TextSlide,
)
from ui.widgets import Card, label

SORT_ROLE = Qt.UserRole + 20


# ======================================================================
# Frame shared by every preview
# ======================================================================

class SectionPreview(QWidget):
    """Header (colour chip · where it lands in Excel / PowerPoint · title)
    above a scrollable body."""

    def __init__(self, page, kind: str, title: str, parent=None) -> None:
        super().__init__(parent)
        self.page = page
        self.kind = kind
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(SPACE.xl, SPACE.lg, SPACE.xl, SPACE.sm)
        hl.setSpacing(SPACE.sm)
        hl.addWidget(Swatch(SECTION_COLORS.get(kind, "#888888"), 12), 0, Qt.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(0)
        xl, pp = SECTION_TARGETS.get(kind, ("", ""))
        self.where = label(f"EXCEL · {xl.upper()}    ·    POWERPOINT · {pp.upper()}", "overline")
        self.title = label(title, "h2")
        col.addWidget(self.where)
        col.addWidget(self.title)
        hl.addLayout(col, 1)
        self.head_actions = QHBoxLayout()
        hl.addLayout(self.head_actions)
        outer.addWidget(head)
        inner = QWidget()
        self.body = QVBoxLayout(inner)
        self.body.setContentsMargins(SPACE.xl, SPACE.sm, SPACE.xl, SPACE.xl)
        self.body.setSpacing(SPACE.lg)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setWidget(inner)
        outer.addWidget(self.scroll, 1)

    @property
    def model(self):
        return self.page.model

    def refresh(self) -> None:
        pass


def _wide(w: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidget(w)
    sa.setWidgetResizable(False)
    sa.setFrameShape(QScrollArea.NoFrame)
    sa.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    sa.setFixedHeight(w.sizeHint().height() + 16)
    return sa


# ======================================================================
# Cover / Overview / Charts / Methods / Raw / Text
# ======================================================================

class CoverPreview(SectionPreview):
    def __init__(self, page, section) -> None:
        super().__init__(page, "cover", "Cover")
        self.slide = CoverSlide(page.model)
        self.body.addWidget(self.slide)
        self.body.addWidget(label("Title, organization, operator, date and logo are edited in "
                                  "the Document panel on the right.", "caption"))
        self.body.addStretch(1)

    def refresh(self) -> None:
        self.slide.model = self.model
        self.slide.update()


class OverviewPreview(SectionPreview):
    def __init__(self, page, section) -> None:
        super().__init__(page, "overview_table", "Overview")
        from ui.pages.common import MetricCard
        kp = QHBoxLayout()
        kp.setSpacing(SPACE.md)
        self.k_imgs = MetricCard("Images in report", 0, "", 0)
        self.k_grains = MetricCard("Grains (combined)", 0, "", 0)
        self.k_diam = MetricCard("Mean diameter", 0, "", 2)
        self.k_g = MetricCard("Mean ASTM G", 0, "", 1)
        for c in (self.k_imgs, self.k_grains, self.k_diam, self.k_g):
            kp.addWidget(c)
        self.body.addLayout(kp)
        self.sheet = OverviewSheet()
        self.wrap = _wide(self.sheet)
        self.body.addWidget(self.wrap)
        self.note = label("", "caption")
        self.note.setWordWrap(True)
        self.body.addWidget(self.note)
        self.body.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        from version import __version__
        m = self.model
        header, rows, total = overview_table(m)
        sub = (f"Operator: {m.operator or '—'}   |   Organization: {m.organization or '—'}   |   "
               f"Date: {m.date}   |   {len(rows)} image(s)   |   Grain Analyzer v{__version__}")
        sec = m.get_section("overview_table")
        self.sheet.set_table(m.title or "Grain Analysis Report", sub, header, rows, total,
                             enabled=sec.enabled if sec else True,
                             hier_line=m.hierarchy_header())
        self.sheet.resize(self.sheet.sizeHint())
        imgs = m.ordered_images(included_only=True)
        self.k_imgs.set_metric(len(imgs), "", 0, animate=False)
        self.k_grains.set_metric(sum(i.grain_count for i in imgs), "", 0, animate=False)
        vals, unit = combined_values(m, "diameter")
        self.k_diam.set_metric(sum(vals) / len(vals) if vals else None, unit, 2, animate=False)
        gs = [i.astm_g for i in imgs if i.astm_g is not None]
        self.k_g.set_metric(sum(gs) / len(gs) if gs else None, "G" if gs else "", 1,
                            animate=False)
        self.wrap.setFixedHeight(self.sheet.sizeHint().height() + 16)
        self.note.setText("First sheet of the workbook (navy tab). Image names link to each "
                          "image's sheet" + ("; the File column links to the image file on "
                                             "disk" if m.hierarchy else "") +
                          ". The same rows appear on the PowerPoint executive summary slide.")

    def rows(self) -> int:
        return len(self.sheet.rows)


VERDICT_KIND = {"Equivalent": "success", "Not equivalent": "danger",
                "Inconclusive": "warning"}
BAND_KIND = {"green": "success", "amber": "warning", "red": "danger"}


def _fmt_signed(v, nd: int = 2) -> str:
    return "—" if v is None else f"{v:+.{nd}f}"


class LotComparisonPreview(SectionPreview):
    """UX-13: per part, the ΔG matrix (G of the column lot minus G of the
    row lot, coloured by |ΔG| band) and each lot's equivalence verdict vs the
    part's baseline lot -- exactly what the Lot Comparison sheet / slides
    show."""

    def __init__(self, page, section) -> None:
        super().__init__(page, "lot_comparison", "Lot comparison")
        self.section = section
        self.cards: List[QWidget] = []
        self.host = QVBoxLayout()
        self.host.setSpacing(SPACE.lg)
        self.body.addLayout(self.host)
        self.legend = label("", "caption")
        self.legend.setWordWrap(True)
        self.body.addWidget(self.legend)
        self.body.addStretch(1)
        self.refresh()

    def _clear(self) -> None:
        for c in self.cards:
            c.setParent(None)
            c.deleteLater()
        self.cards = []

    def refresh(self) -> None:
        from ui import hierarchy_ui as hui
        from ui.pages.report_builder import lot_comparison_rows
        self._clear()
        prof = getattr(self.page.state, "profile", None)

        def L(k):
            try:
                return hui.kind_label(prof, k)
            except Exception:       # noqa: BLE001
                return {"project": "Job", "sample": "Part", "lot": "Lot"}[k]
        parts = lot_comparison_rows(self.section)
        multi_job = len({p["job"] for p in parts}) > 1
        for p in parts:
            title = f"{L('sample')} {p['part']}" + (f"  ·  {L('project')} {p['job']}"
                                                   if multi_job else "")
            base = p["baseline"]
            an = p["anova"] or {}
            sub = [f"Baseline {L('lot').lower()}: {base}" if base else
                   f"No baseline {L('lot').lower()} set (star one on Projects ▸ Compare lots)"]
            if an.get("p") is not None:
                sub.append(f"Welch ANOVA p = {an['p']:.3g}")
            card = Card(title, "   ·   ".join(sub))
            card.add_widget(label("ΔG matrix (column − row)", "overline"))
            card.add_widget(self._matrix(p))
            card.add_widget(label("Equivalence vs baseline", "overline"))
            card.add_widget(self._verdicts(p))
            self.host.addWidget(card)
            self.cards.append(card)
        if not parts:
            empty = label("No lots to compare.", tone="secondary")
            self.host.addWidget(empty)
            self.cards.append(empty)
        self.legend.setText(
            "|ΔG| ≤ 0.25 green · ≤ 0.5 amber · > 0.5 red. Verdicts use a TOST equivalence "
            "test of each lot's mean ASTM G against the baseline lot (90 % CI within "
            "± the tolerance). Excel: the Lot Comparison sheet (blue tab); PowerPoint: one "
            "slide per part.")
        self.title.setText(self.section.title or "Lot comparison")

    def _table(self, rows: int, cols: int, heads: List[str]) -> QTableWidget:
        t = QTableWidget(rows, cols)
        t.setHorizontalHeaderLabels(heads)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionMode(QAbstractItemView.NoSelection)
        t.setFocusPolicy(Qt.NoFocus)
        t.setShowGrid(True)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        t.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        t.verticalHeader().setDefaultSectionSize(30)
        t.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        t.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        t.setFixedHeight(t.horizontalHeader().sizeHint().height() + 30 * rows + 4)
        return t

    def _matrix(self, p: dict) -> QTableWidget:
        from PySide6.QtGui import QBrush, QColor
        from ui.design.theme import current_tokens
        tok = current_tokens()
        lots = p["lots"]
        t = self._table(len(lots), len(lots), lots)
        t.setVerticalHeaderLabels(lots)
        t.setToolTip("ΔG = mean ASTM G of the column lot minus that of the row lot")
        for i, row in enumerate(p["matrix"]):
            for j, v in enumerate(row):
                it = QTableWidgetItem("0" if i == j and v is not None and abs(v) < 1e-12
                                      else _fmt_signed(v))
                it.setTextAlignment(Qt.AlignCenter)
                band = p["bands"][i][j] if i < len(p["bands"]) and j < len(p["bands"][i]) \
                    else "none"
                kind = BAND_KIND.get(band)
                if kind:
                    sem = tok.semantic(kind)
                    it.setBackground(QBrush(QColor(sem.bg)))
                    it.setForeground(QBrush(QColor(sem.fg)))
                t.setItem(i, j, it)
        return t

    def _verdicts(self, p: dict) -> QTableWidget:
        from PySide6.QtGui import QBrush, QColor
        from ui.design.theme import current_tokens
        tok = current_tokens()
        means = {lot: (m, n) for lot, m, n in p["means"]}
        ver = {v[0]: v for v in p["verdicts"]}
        lots = p["lots"]
        t = self._table(len(lots), 5, ["Lot", "Mean G (fields)", "ΔG vs baseline",
                                       "90 % CI", "Verdict"])
        t.verticalHeader().setVisible(False)
        for r, lot in enumerate(lots):
            m, n = means.get(lot, (None, None))
            v = ver.get(lot)
            is_base = lot == p["baseline"]
            cells = [lot + ("  (baseline)" if is_base else ""),
                     "—" if m is None else f"{m:.2f}  ({n or 0})",
                     "—" if v is None else _fmt_signed(v[2]),
                     "—" if v is None or v[3] is None or v[4] is None
                     else f"{v[3]:+.2f} … {v[4]:+.2f}",
                     "Baseline" if is_base else (v[1] if v else "—")]
            for c, txt in enumerate(cells):
                it = QTableWidgetItem(txt)
                it.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter if c in (0, 4)
                                    else Qt.AlignRight | Qt.AlignVCenter)
                if c == 4 and v is not None and not is_base:
                    kind = VERDICT_KIND.get(v[1])
                    if kind:
                        sem = tok.semantic(kind)
                        it.setBackground(QBrush(QColor(sem.bg)))
                        it.setForeground(QBrush(QColor(sem.fg)))
                t.setItem(r, c, it)
        return t

    def verdict_texts(self) -> List[str]:
        return [v[1] for p in lot_comparison_rows_safe(self.section) for v in p["verdicts"]]


def lot_comparison_rows_safe(section):
    from ui.pages.report_builder import lot_comparison_rows
    try:
        return lot_comparison_rows(section)
    except Exception:       # noqa: BLE001
        return []


class ChartsPreview(SectionPreview):
    def __init__(self, page, section) -> None:
        super().__init__(page, "combined_distribution", "Summary charts")
        self.cards = []
        self.h_area = ThemedHistogram(series=0)
        self.h_diam = ThemedHistogram(series=2)
        for title, h in (("Grain area distribution — all included images", self.h_area),
                         ("Grain diameter distribution — all included images", self.h_diam)):
            c = Card(title)
            h.setMinimumHeight(260)
            c.add_widget(h)
            self.body.addWidget(c)
            self.cards.append(c)
        c = Card("Per image", "Mean grain diameter with ±1 standard deviation")
        self.bars = PerImageBars()
        self.bars.setMinimumHeight(220)
        c.add_widget(self.bars)
        self.body.addWidget(c)
        self.off = label("", "caption")
        self.body.addWidget(self.off)
        self.body.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        from reports.charts import filter_range, resolve_chart_options
        m = self.model
        opts = resolve_chart_options(m.chart_options)
        for kind, h, card in (("area", self.h_area, self.cards[0]),
                              ("diameter", self.h_diam, self.cards[1])):
            o = opts[kind]
            card.setVisible(o["enabled"])
            if not o["enabled"]:
                continue
            card.set_title(o["title"] or f"Grain {kind} distribution — all included images")
            vals, unit = combined_values(m, kind)
            vals = filter_range(vals, o["min"], o["max"])
            labels, counts, edges = build_bins(vals, int(m.bins.get(kind, 0) or 0))
            h.set_binned(vals, edges, counts, [f"{lb} {unit}" for lb in labels],
                         f"Grain {kind} ({unit})", unit)
            h.set_show_fit(opts["normal_fit"])
        imgs = m.ordered_images(included_only=True)
        items = []
        du = "px"
        for img in imgs:
            _au, _am, du_i, dm = resolve_units(img.px_per_um, m.units)
            du = du_i
            if img.has_calibration:
                items.append((img.display(), img.mean_diameter_um * dm,
                              img.std_diameter_um * dm, img.grain_count))
            else:
                import numpy as np
                d = [g["diameter_px"] for g in img.grains]
                items.append((img.display(), float(np.mean(d)) if d else 0.0,
                              float(np.std(d)) if d else 0.0, img.grain_count))
        self.bars.set_items(items, du)
        sec = m.get_section("combined_distribution")
        self.off.setText("" if (sec is None or sec.enabled) else
                         "This section is switched off — tick it in the outline to include the "
                         "Summary Charts sheet and the two distribution slides.")


class MethodsPreview(SectionPreview):
    def __init__(self, page, section) -> None:
        super().__init__(page, "parameters", "Methods & parameters")
        self.slide = MethodsSlide(page.model, [])
        self.body.addWidget(self.slide)
        self.body.addWidget(label("Recorded automatically from the session: detection mode and "
                                  "parameters, grain filters, calibration and instrument.",
                                  "caption"))
        self.body.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        from version import __version__
        m = self.model
        lines = [("Detection mode", m.metadata.get("detection_mode", "—") or "—")]
        lines += [(k, v) for k, v in (m.metadata.get("detection_params") or {}).items()]
        lines += [("Calibrated", "Yes" if any(i.has_calibration for i in m.images) else "No"),
                  ("Instrument", m.metadata.get("instrument", "—") or "—"),
                  ("Software", f"Grain Analyzer v{__version__}"),
                  ("Generated by", f"{m.operator or 'unknown operator'} / "
                                   f"{m.organization or '—'} on {m.date}")]
        self.slide.model = m
        self.slide.lines = lines
        self.slide.update()


class RawPreview(SectionPreview):
    def __init__(self, page, section) -> None:
        super().__init__(page, "raw_data", "Raw data")
        c = Card("Raw grain sheets", "Placed after every other sheet — grey tabs, one per image")
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Sheet", "Grain rows", "Units", "Columns"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(220)
        c.add_widget(self.table)
        self.body.addWidget(c)
        self.body.addWidget(label("Each sheet has a frozen header row and an AutoFilter. "
                                  "PowerPoint gets a one-slide appendix pointing to the workbook "
                                  "instead of duplicating the numbers.", "caption"))
        self.body.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        m = self.model
        imgs = m.ordered_images(included_only=True)
        self.table.setRowCount(len(imgs))
        for r, img in enumerate(imgs):
            name = os.path.splitext(os.path.basename(img.image_path))[0]
            cal = img.has_calibration
            cols = ("ID, Area, Diameter, Major, Minor, Perimeter, Circularity, Aspect, "
                    "Eccentricity, Cx, Cy" if cal else
                    "ID, Area, Diameter, Perimeter, Circularity, Aspect, Eccentricity, Cx, Cy")
            vals = [f"Raw - {name}"[:31], f"{len(img.grains):,}", "µm² / µm" if cal else "px² / px",
                    cols]
            for c, v in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(v))


class TextPreview(SectionPreview):
    def __init__(self, page, section) -> None:
        super().__init__(page, "custom_text", section.title or "Text")
        self.section = section
        self.slide = TextSlide(page.model, section)
        self.body.addWidget(self.slide)
        c = Card("Slide text", "Plain text — each line becomes a paragraph")
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("e.g. Conclusions, sample preparation, acceptance criteria…")
        self.editor.setPlainText(str(section.payload.get("body", "") or ""))
        self.editor.setMinimumHeight(140)
        self.editor.setToolTip("Text printed on this slide")
        self.editor.textChanged.connect(self._on_text)
        c.add_widget(self.editor)
        self.body.addWidget(c)
        self.body.addWidget(label("Text sections are added to the PowerPoint deck at their "
                                  "position in the outline. The Excel workbook does not include "
                                  "them yet.", "caption"))
        self.body.addStretch(1)

    def _on_text(self) -> None:
        self.section.payload["body"] = self.editor.toPlainText()
        self.slide.update()
        self.page.edited("text")

    def refresh(self) -> None:
        self.title.setText(self.section.title or "Text")
        self.slide.model = self.model
        self.slide.update()


class ImagesRootPreview(SectionPreview):
    def __init__(self, page, section=None) -> None:
        super().__init__(page, "image", "Images")
        self.info = label("", tone="secondary")
        self.info.setWordWrap(True)
        self.body.addWidget(self.info)
        self.body.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        m = self.model
        k = len(m.ordered_images(included_only=True))
        self.info.setText(
            f"{k} of {len(m.images)} images are included. Select an image in the outline to "
            "preview its sheet and slide, write its caption, and choose which grains it reports. "
            "Drag images (or use Alt+Up / Alt+Down) to change their order — the Overview table, "
            "sheets and slides follow it.")


# ======================================================================
# Image preview with caption / notes / per-grain table
# ======================================================================

class GrainReportTable(QAbstractTableModel):
    """All detected grains of one image.  Column 0 ticks = "in the report"
    (unticking uses the app's manual exclusion, so Review agrees); the Note
    column annotates a grain in the report (appended to the image notes)."""

    HEAD = ["In report", "ID", "Area", "Diameter", "Circularity", "Aspect ratio", "Status",
            "Note"]

    def __init__(self, page, img, parent=None) -> None:
        super().__init__(parent)
        self.page = page
        self.img = img
        self.rows: List[dict] = []
        self.units = ("", "")
        self.reload()

    def reload(self) -> None:
        self.beginResetModel()
        m, img = self.page.model, self.img
        au, am, du, dm = resolve_units(img.px_per_um, m.units)
        cal = img.has_calibration
        self.units = (au, du)
        in_report = {int(g["id"]): g for g in img.grains}
        doc = self.page.image_doc(img)
        rows = []
        if doc is not None and doc.raw is not None:
            for g in doc.raw.grains:
                gid = int(g.grain_id)
                reasons = doc.excluded.get(gid) or []
                rows.append(dict(id=gid,
                                 area=g.area_um2 * am if cal else g.area_px,
                                 diam=g.equivalent_diameter_um * dm if cal else
                                 g.equivalent_diameter_px,
                                 circ=g.circularity, ar=g.aspect_ratio,
                                 kept=not reasons, reasons=list(reasons),
                                 editable_check=not [r for r in reasons if r != "manual"],
                                 grain=in_report.get(gid)))
        else:
            for gid, g in in_report.items():
                rows.append(dict(id=gid, area=g["area_um2"] * am if cal else g["area_px"],
                                 diam=g["diameter_um"] * dm if cal else g["diameter_px"],
                                 circ=g["circularity"], ar=g["aspect_ratio"], kept=True,
                                 reasons=[], editable_check=False, grain=g))
        rows.sort(key=lambda r: r["id"])
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEAD)

    def headerData(self, s, o, role=Qt.DisplayRole):  # noqa: N802
        if o == Qt.Horizontal and role == Qt.DisplayRole:
            h = self.HEAD[s]
            if s == 2:
                return f"Area ({self.units[0]})"
            if s == 3:
                return f"Diameter ({self.units[1]})"
            return h
        return None

    def flags(self, idx):
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if not idx.isValid():
            return f
        r = self.rows[idx.row()]
        if idx.column() == 0:
            f |= Qt.ItemIsUserCheckable
            if not r["editable_check"]:
                f &= ~Qt.ItemIsEnabled
        if idx.column() == 7 and r["grain"] is not None and r["kept"]:
            f |= Qt.ItemIsEditable
        return f

    def data(self, idx, role=Qt.DisplayRole):
        if not idx.isValid():
            return None
        r = self.rows[idx.row()]
        c = idx.column()
        if role == Qt.CheckStateRole and c == 0:
            return Qt.Checked if r["kept"] else Qt.Unchecked
        if role == SORT_ROLE:
            return [int(r["kept"]), r["id"], r["area"], r["diam"], r["circ"], r["ar"],
                    len(r["reasons"]), (r["grain"] or {}).get("note", "")][c]
        if role in (Qt.DisplayRole, Qt.EditRole):
            if c == 1:
                return str(r["id"])
            if c == 2:
                return smart_format(r["area"])
            if c == 3:
                return smart_format(r["diam"])
            if c == 4:
                return f"{r['circ']:.3f}"
            if c == 5:
                return f"{r['ar']:.2f}"
            if c == 6:
                if not r["reasons"]:
                    return "In report"
                return "Removed by hand" if r["reasons"] == ["manual"] else \
                    "Filtered — " + REASON_LABELS.get(r["reasons"][0], r["reasons"][0]).split(" —")[0]
            if c == 7:
                return (r["grain"] or {}).get("note", "")
            return None
        if role == Qt.ToolTipRole:
            if c == 0 and not r["editable_check"]:
                return ("Excluded by a grain filter — change the filters on the Review page "
                        "to bring it back.")
            if c == 0:
                return "Untick to leave this grain out of the report and every statistic"
            if c == 7:
                return "Annotation printed with the image's notes"
            if r["reasons"]:
                return "; ".join(REASON_LABELS.get(x, x) for x in r["reasons"])
        if role == Qt.TextAlignmentRole and c in (1, 2, 3, 4, 5):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ForegroundRole and not r["kept"]:
            from ui.widgets._base import qcolor, tokens
            return qcolor(tokens().text.tertiary)
        return None

    def setData(self, idx, value, role=Qt.EditRole) -> bool:  # noqa: N802
        if not idx.isValid():
            return False
        r = self.rows[idx.row()]
        if idx.column() == 0 and role == Qt.CheckStateRole and r["editable_check"]:
            on = Qt.CheckState(value) == Qt.Checked if not isinstance(value, bool) else value
            self.page.set_grain_included(self.img.id, r["id"], bool(on))
            r["kept"] = bool(on)
            r["reasons"] = [] if on else ["manual"]
            self.dataChanged.emit(idx.siblingAtColumn(0), idx.siblingAtColumn(7))
            return True
        if idx.column() == 7 and role == Qt.EditRole and r["grain"] is not None:
            text = str(value or "").strip()
            if text:
                r["grain"]["note"] = text
            else:
                r["grain"].pop("note", None)
            self.dataChanged.emit(idx, idx)
            self.page.edited("grain_note")
            return True
        return False


class ImagePreview(SectionPreview):
    def __init__(self, page, img) -> None:
        super().__init__(page, "image", img.display())
        self.img = img
        ready = page.is_pixmaps_ready(img)
        orig, ovl = page.request_pixmaps(img, lambda pm, src=img: self._on_pixmaps(src, pm))
        self.slide = ImageSlide(page.model, img, orig, ovl, self._number(), loading=not ready)
        self.body.addWidget(self.slide)

        text = Card("Caption & notes", "Printed under the images on the slide and on the "
                                       "image's sheet")
        self.caption = QLineEdit(img.caption)
        self.caption.setPlaceholderText("Caption — e.g. “Transverse section, rim, 500×, etched”")
        self.caption.setToolTip("One-line caption for this image")
        self.notes = QPlainTextEdit(img.notes)
        self.notes.setPlaceholderText("Notes — observations, anomalies, acceptance remarks…")
        self.notes.setFixedHeight(84)
        self.notes.setToolTip("Free-text notes for this image")
        text.add_widget(self.caption)
        text.add_widget(self.notes)
        self.caption.textChanged.connect(self._on_caption)
        self.notes.textChanged.connect(self._on_notes)
        self.body.addWidget(text)

        grains = Card("Grains in this report",
                      "Measurements are read-only. Untick a grain to leave it out; add a note to "
                      "annotate it.")
        self.info = label("Unticking a grain removes it from the analysis results — exactly like "
                          "Remove on the Review page — so the Review page, this report and every "
                          "export show the same numbers. Undo with Ctrl+Z.", "caption")
        self.info.setWordWrap(True)
        grains.add_widget(self.info)
        self.gmodel = GrainReportTable(page, img, self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.gmodel)
        self.proxy.setSortRole(SORT_ROLE)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(1, Qt.AscendingOrder)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                                   | QAbstractItemView.SelectedClicked)
        # UX-12: NOT QHeaderView.ResizeToContents — with real SEM images
        # (hundreds to thousands of grains) that mode makes Qt re-measure
        # every cell in every column on every layout pass, which is the
        # actual cause of the Images-tab freeze (profiled: tens of thousands
        # of model.data() calls for a table of a few dozen rows). Fixed,
        # user-resizable column widths make opening an image O(1) instead of
        # O(grains × columns).
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        for c, w in enumerate((78, 52, 104, 104, 96, 96, 160)):
            self.table.setColumnWidth(c, w)
        self.table.setMinimumHeight(300)
        self.table.setToolTip("Tick = included in the report. Double-click a Note cell to annotate.")
        grains.add_widget(self.table)
        self.count_lbl = label("", "caption")
        grains.add_widget(self.count_lbl)
        self.body.addWidget(grains)
        self.body.addStretch(1)
        self._update_count()

    def _number(self) -> int:
        inc = self.page.model.ordered_images(included_only=True)
        for n, i in enumerate(inc, 1):
            if i.id == self.img.id:
                return n
        return 0

    def _on_pixmaps(self, src, pm) -> None:
        """UX-12: the background decode landed — apply it only if this
        preview is still showing the image it was requested for (the user
        may have picked another image, or the report may have reloaded,
        while the file was decoding on the pool thread)."""
        if src.image_path != self.img.image_path or src.overlay_path != self.img.overlay_path:
            return
        self.slide.original, self.slide.overlay = pm
        self.slide.loading = False
        self.slide.update()

    def _on_caption(self, text: str) -> None:
        self.img.caption = text
        self.slide.update()
        self.page.edited("caption")

    def _on_notes(self) -> None:
        self.img.notes = self.notes.toPlainText()
        self.slide.update()
        self.page.edited("notes")

    def _update_count(self) -> None:
        n_all = len(self.gmodel.rows)
        n_in = sum(1 for r in self.gmodel.rows if r["kept"])
        self.count_lbl.setText(f"{n_in:,} of {n_all:,} detected grains are in the report")

    def set_image(self, img) -> None:
        """Numbers refreshed (new ImageSummary object for the same image)."""
        self.img = img
        self.slide.img = img
        self.gmodel.img = img
        self.refresh()

    def refresh(self) -> None:
        self.slide.model = self.page.model
        self.slide.number = self._number()
        ready = self.page.is_pixmaps_ready(self.img)
        orig, ovl = self.page.request_pixmaps(
            self.img, lambda pm, src=self.img: self._on_pixmaps(src, pm))
        self.slide.original, self.slide.overlay = orig, ovl
        self.slide.loading = not ready
        self.slide.update()
        self.gmodel.reload()
        self._update_count()
        for w, v in ((self.caption, self.img.caption),):
            if w.text() != v:
                w.blockSignals(True)
                w.setText(v)
                w.blockSignals(False)
        if self.notes.toPlainText() != self.img.notes:
            self.notes.blockSignals(True)
            self.notes.setPlainText(self.img.notes)
            self.notes.blockSignals(False)


# ======================================================================
# Factory
# ======================================================================

def make_preview(page, key) -> Optional[SectionPreview]:
    m = page.model
    if m is None or key is None:
        return None
    kind, ident = key
    if kind == "image":
        img = next((i for i in m.images if i.id == ident), None)
        return ImagePreview(page, img) if img is not None else None
    if ident == "images":
        return ImagesRootPreview(page)
    sec = next((s for s in m.sections if s.id == ident), None)
    if sec is None:
        return None
    cls = {"cover": CoverPreview, "overview_table": OverviewPreview,
           "lot_comparison": LotComparisonPreview,
           "combined_distribution": ChartsPreview, "parameters": MethodsPreview,
           "raw_data": RawPreview, "custom_text": TextPreview}.get(sec.type)
    return cls(page, sec) if cls else None


__all__ = ["make_preview", "SectionPreview", "ImagePreview", "OverviewPreview",
           "LotComparisonPreview",
           "GrainReportTable"]
