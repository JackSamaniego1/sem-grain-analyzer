"""
Properties inspector (right column of the report designer).

  DOCUMENT   title · organization · operator · date · logo · units · bins · palette
  SELECTION  depends on the outline item (section switch / text title /
             image include + facts + move)
  CHECKS     ``ReportModel.validate()`` findings with a fix hint each
  EXPORTS    history recorded in the report (open / show in folder)
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QSpinBox, QVBoxLayout,
    QWidget,
)

from ui.design import icons
from ui.design.tokens import SPACE
from ui.pages.report_builder import SECTION_LABELS, SECTION_TARGETS
from ui.widgets import (
    AnimatedButton, CollapsibleSection, IconButton, KeyValueList, SegmentedControl, label,
)
from ui.widgets._base import tokens

UNITS = ("auto", "um", "nm")
PALETTES = [("default", "Corporate navy")]


def _cap(text: str):
    lab = label(text, "caption")
    lab.setWordWrap(True)
    return lab


def _clear_layout(lay) -> None:
    while lay.count():
        it = lay.takeAt(0)
        w = it.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        elif it.layout() is not None:
            _clear_layout(it.layout())


class ReportInspector(QWidget):
    doc_changed = Signal(str)          # field name

    def __init__(self, page, parent=None) -> None:
        super().__init__(parent)
        self.page = page
        self._filling = False
        v = QVBoxLayout(self)
        v.setContentsMargins(SPACE.lg, SPACE.lg, SPACE.lg, SPACE.lg)
        v.setSpacing(SPACE.md)

        # ---------------------------------------------------------- document
        self.sec_doc = CollapsibleSection("Document", True)
        g = QGridLayout()
        g.setHorizontalSpacing(SPACE.sm)
        g.setVerticalSpacing(SPACE.sm)
        g.setColumnStretch(1, 1)
        self.title = QLineEdit()
        self.title.setToolTip("Report title — cover, sheet headers and slide footers")
        self.org = QLineEdit()
        self.org.setPlaceholderText("Organization / laboratory")
        self.org.setToolTip("Printed on the cover and the Overview sheet")
        self.operator = QLineEdit()
        self.operator.setToolTip("Operator named on the report (defaults to Settings ▸ Operator)")
        self.date = QLineEdit()
        self.date.setToolTip("Report date as printed")
        self.btn_now = IconButton("calendar", "Set to now")
        drow = QHBoxLayout()
        drow.setSpacing(SPACE.xs)
        drow.addWidget(self.date, 1)
        drow.addWidget(self.btn_now)
        self.logo_name = label("No logo", tone="secondary")
        self.logo_name.setMinimumWidth(40)
        self.logo_name.setToolTip("Logo on the title slide (copied into the session folder)")
        self.btn_logo = AnimatedButton("Choose…", None, "secondary", "sm")
        self.btn_logo.setToolTip("Pick a PNG/JPG logo from this PC")
        self.btn_logo_clear = IconButton("close", "Remove the logo")
        lrow = QHBoxLayout()
        lrow.setSpacing(SPACE.xs)
        lrow.addWidget(self.logo_name, 1)
        lrow.addWidget(self.btn_logo)
        lrow.addWidget(self.btn_logo_clear)
        self.units = SegmentedControl(["Auto", "µm", "nm"], 0)
        self.units.setToolTip("Length units (Auto picks nm for very fine grains)")
        self.bins_area = QSpinBox()
        self.bins_diam = QSpinBox()
        for sb, what in ((self.bins_area, "area"), (self.bins_diam, "diameter")):
            sb.setRange(0, 60)
            sb.setSpecialValueText("Auto")
            sb.setToolTip(f"Histogram bins for grain {what} (Auto = square-root rule)")
        self.palette = QComboBox()
        for key, text in PALETTES:
            self.palette.addItem(text, key)
        self.palette.setToolTip("Colour scheme of the exported workbook and deck (navy titles, "
                                "colour-coded sheet tabs)")
        rows = [("Title", self.title), ("Organization", self.org), ("Operator", self.operator),
                ("Date", drow), ("Logo", lrow), ("Units", self.units),
                ("Area bins", self.bins_area), ("Diameter bins", self.bins_diam),
                ("Palette", self.palette)]
        for r, (text, w) in enumerate(rows):
            g.addWidget(label(text, tone="secondary"), r, 0, Qt.AlignVCenter)
            if isinstance(w, QWidget):
                g.addWidget(w, r, 1)
            else:
                g.addLayout(w, r, 1)
        host = QWidget()
        host.setLayout(g)
        self.sec_doc.add_widget(host)
        v.addWidget(self.sec_doc)

        # ---------------------------------------------------------- selection
        self.sec_sel = CollapsibleSection("Selection", True)
        self.sel_host = QWidget()
        self.sel_lay = QVBoxLayout(self.sel_host)
        self.sel_lay.setContentsMargins(0, 0, 0, 0)
        self.sel_lay.setSpacing(SPACE.sm)
        self.sec_sel.add_widget(self.sel_host)
        v.addWidget(self.sec_sel)

        # ---------------------------------------------------------- checks
        self.sec_checks = CollapsibleSection("Checks", True)
        self.checks_host = QWidget()
        self.checks_lay = QVBoxLayout(self.checks_host)
        self.checks_lay.setContentsMargins(0, 0, 0, 0)
        self.checks_lay.setSpacing(SPACE.sm)
        self.sec_checks.add_widget(self.checks_host)
        v.addWidget(self.sec_checks)

        # ---------------------------------------------------------- exports
        self.sec_exports = CollapsibleSection("Exports", True)
        self.exp_host = QWidget()
        self.exp_lay = QVBoxLayout(self.exp_host)
        self.exp_lay.setContentsMargins(0, 0, 0, 0)
        self.exp_lay.setSpacing(SPACE.xs)
        self.sec_exports.add_widget(self.exp_host)
        v.addWidget(self.sec_exports)
        v.addStretch(1)

        self.title.textEdited.connect(lambda t: self._set("title", t))
        self.org.textEdited.connect(lambda t: self._set("organization", t))
        self.operator.textEdited.connect(lambda t: self._set("operator", t))
        self.date.textEdited.connect(lambda t: self._set("date", t))
        self.btn_now.clicked.connect(self._now)
        self.btn_logo.clicked.connect(page.choose_logo)
        self.btn_logo_clear.clicked.connect(page.clear_logo)
        self.units.current_changed.connect(lambda i: self._set("units", UNITS[i]))
        self.bins_area.valueChanged.connect(lambda n: self._set_bins("area", n))
        self.bins_diam.valueChanged.connect(lambda n: self._set_bins("diameter", n))
        self.palette.currentIndexChanged.connect(
            lambda _i: self._set("theme", self.palette.currentData()))

    # ------------------------------------------------------------------ document
    @property
    def model(self):
        return self.page.model

    def _set(self, field: str, value) -> None:
        if self._filling or self.model is None:
            return
        setattr(self.model, field, value)
        if field == "title":
            sec = self.model.get_section("cover")
            if sec is not None:
                sec.title = value
        self.doc_changed.emit(field)

    def _set_bins(self, kind: str, n: int) -> None:
        if self._filling or self.model is None:
            return
        self.model.bins[kind] = int(n)
        self.doc_changed.emit("bins")

    def _now(self) -> None:
        self.date.setText(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self._set("date", self.date.text())

    def load(self) -> None:
        m = self.model
        if m is None:
            return
        self._filling = True
        for w, v in ((self.title, m.title), (self.org, m.organization),
                     (self.operator, m.operator), (self.date, m.date)):
            if w.text() != (v or ""):
                w.setText(v or "")
        self.units.set_current_index(UNITS.index(m.units) if m.units in UNITS else 0,
                                     animate=False)
        self.bins_area.setValue(int(m.bins.get("area", 0) or 0))
        self.bins_diam.setValue(int(m.bins.get("diameter", 0) or 0))
        i = self.palette.findData(m.theme)
        self.palette.setCurrentIndex(max(0, i))
        self.set_logo_name(m.logo_path)
        self._filling = False
        self.load_exports()

    def set_logo_name(self, path: Optional[str]) -> None:
        ok = bool(path) and os.path.exists(path)
        self.logo_name.setText(os.path.basename(path) if path else "No logo")
        self.logo_name.setProperty("tone", "secondary" if ok or not path else "danger")
        self.logo_name.style().unpolish(self.logo_name)
        self.logo_name.style().polish(self.logo_name)
        self.btn_logo_clear.setEnabled(bool(path))

    # ------------------------------------------------------------------ selection
    def show_selection(self, key) -> None:
        _clear_layout(self.sel_lay)
        m = self.model
        if m is None or key is None:
            self.sel_lay.addWidget(label("Select a section or an image in the outline.",
                                         tone="secondary"))
            return
        kind, ident = key
        if kind == "image":
            img = next((i for i in m.images if i.id == ident), None)
            if img is None:
                return
            self.sec_sel.setToolTip("")
            self.sel_lay.addWidget(label(os.path.basename(img.image_path), "body_strong"))
            cb = QCheckBox("Include this image")
            cb.setChecked(img.include)
            cb.setToolTip("Untick to leave this image out of every sheet and slide")
            cb.toggled.connect(lambda on, i=img.id: self.page.set_image_included(i, on))
            self.sel_lay.addWidget(cb)
            self.include_box = cb
            au = "calibrated" if img.has_calibration else "not calibrated — sizes in px"
            kv = KeyValueList([("Sample", img.sample_id or "—"), ("Lot", img.lot_number or "—"),
                               ("Grains", f"{img.grain_count:,}"),
                               ("Scale", f"{img.px_per_um:.4g} px/µm ({au})"
                                if img.px_per_um > 0 else au),
                               ("ASTM G", f"{img.astm_g:.2f}" if img.astm_g is not None else "—")])
            self.sel_lay.addWidget(kv)
            self.sel_lay.addLayout(self._move_row())
            return
        if ident == "images":
            self.sel_lay.addWidget(label("Images", "body_strong"))
            self.sel_lay.addWidget(label("Tick images in the outline to include them; drag them "
                                         "to set the order of sheets and slides.",
                                         tone="secondary"))
            return
        sec = next((s for s in m.sections if s.id == ident), None)
        if sec is None:
            return
        xl, pp = SECTION_TARGETS.get(sec.type, ("", ""))
        if sec.type == "custom_text":
            self.sel_lay.addWidget(label("Text section", "body_strong"))
            t = QLineEdit(sec.title)
            t.setToolTip("Heading of the text slide")
            t.textEdited.connect(lambda text, s=sec: self.page.rename_section(s.id, text))
            self.sel_lay.addWidget(label("Slide title", tone="secondary"))
            self.sel_lay.addWidget(t)
            self.text_title = t
        else:
            self.sel_lay.addWidget(label(SECTION_LABELS.get(sec.type, sec.title), "body_strong"))
        self.sel_lay.addWidget(_cap(f"Excel: {xl}\nPowerPoint: {pp}"))
        if sec.type != "cover":
            cb = QCheckBox("Include in the report")
            cb.setChecked(sec.enabled)
            cb.setToolTip("Untick to leave this section out")
            cb.toggled.connect(lambda on, s=sec: self.page.set_section_enabled(s.id, on))
            self.sel_lay.addWidget(cb)
            self.include_box = cb
        if sec.type == "raw_data":
            self.sel_lay.addWidget(_cap("Raw data sheets are always placed at the end of the "
                                         "workbook."))
        if sec.type == "custom_text":
            self.sel_lay.addLayout(self._move_row())
            rm = AnimatedButton("Delete text section", "delete", "ghost", "sm")
            rm.setToolTip("Remove this text section from the report")
            rm.clicked.connect(lambda _=False, s=sec: self.page.delete_section(s.id))
            self.sel_lay.addWidget(rm, 0, Qt.AlignLeft)

    def _move_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(SPACE.xs)
        up = AnimatedButton("Move up", "arrow_up", "ghost", "sm")
        up.setToolTip("Move earlier in the report (Alt+Up)")
        dn = AnimatedButton("Move down", "arrow_down", "ghost", "sm")
        dn.setToolTip("Move later in the report (Alt+Down)")
        up.clicked.connect(lambda: self.page.move_selected(-1))
        dn.clicked.connect(lambda: self.page.move_selected(1))
        row.addWidget(up)
        row.addWidget(dn)
        row.addStretch(1)
        return row

    # ------------------------------------------------------------------ checks
    def show_checks(self, problems: List[tuple]) -> None:
        _clear_layout(self.checks_lay)
        t = tokens()
        if not problems:
            row = QHBoxLayout()
            ic = QLabel()
            ic.setPixmap(icons.pixmap("success", 16, t.success.fg))
            row.addWidget(ic, 0, Qt.AlignTop)
            row.addWidget(label("Ready to export — no problems found.", tone="success"), 1)
            self.checks_lay.addLayout(row)
            return
        for sev, text, hint in problems:
            row = QHBoxLayout()
            row.setSpacing(SPACE.sm)
            ic = QLabel()
            ic.setPixmap(icons.pixmap(sev, 16, t.semantic(sev).fg))
            row.addWidget(ic, 0, Qt.AlignTop)
            col = QVBoxLayout()
            col.setSpacing(0)
            a = label(text, "body_strong")
            a.setWordWrap(True)
            b = _cap(hint)
            b.setWordWrap(True)
            col.addWidget(a)
            col.addWidget(b)
            row.addLayout(col, 1)
            self.checks_lay.addLayout(row)

    # ------------------------------------------------------------------ exports
    def load_exports(self) -> None:
        _clear_layout(self.exp_lay)
        hist = list((self.model.metadata.get("exports") if self.model else None) or [])
        if not hist:
            self.exp_lay.addWidget(_cap("Nothing exported yet. Files are written to the "
                                         "session's exports folder."))
            return
        for e in reversed(hist[-6:]):
            row = QHBoxLayout()
            row.setSpacing(SPACE.xs)
            ic = QLabel()
            ic.setPixmap(icons.pixmap("powerpoint" if e.get("kind") == "PowerPoint" else "excel",
                                      16))
            row.addWidget(ic)
            col = QVBoxLayout()
            col.setSpacing(0)
            name = label(e.get("file", ""), "body_strong")
            name.setToolTip(e.get("path", ""))
            col.addWidget(name)
            col.addWidget(_cap(f"{e.get('time', '')} · {e.get('operator', '')}"))
            row.addLayout(col, 1)
            path = e.get("path", "")
            exists = bool(path) and os.path.exists(path)
            b1 = IconButton("open", "Open file" if exists else "File no longer exists", size=26)
            b1.setEnabled(exists)
            b1.clicked.connect(lambda _=False, p=path: self.page.open_file(p))
            b2 = IconButton("mdi6.folder-search-outline", "Show in folder", size=26)
            b2.setEnabled(exists)
            b2.clicked.connect(lambda _=False, p=path: self.page.show_in_folder(p))
            row.addWidget(b1)
            row.addWidget(b2)
            self.exp_lay.addLayout(row)


__all__ = ["ReportInspector"]
