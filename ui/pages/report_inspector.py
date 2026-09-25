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
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QSpinBox, QVBoxLayout,
    QWidget,
)

from reports.charts import (
    convert_bound, derive_custom_palette, new_custom_palette_id, normalize_hex, palette_choices,
    resolve_chart_options, resolve_units,
)
from ui.design import icons
from ui.design.tokens import SPACE
from ui.pages.report_builder import SECTION_LABELS, SECTION_TARGETS
from ui.pages.report_widgets import Swatch
from ui.widgets import (
    AnimatedButton, CollapsibleSection, IconButton, KeyValueList, SegmentedControl, label,
)
from ui.widgets._base import tokens

UNITS = ("auto", "um", "nm")
# Single source of truth: reports/charts.py (shared with the Excel/PowerPoint
# renderers so the palette a user picks here is exactly what they get).
PALETTES = palette_choices()
# UX-15: trailing combo entry that opens CustomPaletteDialog instead of
# setting a theme directly.
NEW_CUSTOM_PALETTE = "__new_custom_palette__"
CHART_METRICS = (("area", "Area"), ("diameter", "Diameter"))
CHART_METRIC_LABELS = dict(CHART_METRICS)


def _cap(text: str):
    lab = label(text, "caption")
    lab.setWordWrap(True)
    return lab


def _parse_optional_float(text: str) -> Optional[float]:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _fmt_optional_float(value) -> str:
    return "" if value is None else f"{float(value):g}"


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
        self.title.setToolTip("Report title — cover, sheet headers and slide footers\n"
                              "(default from Settings ▸ Folder structure & naming)")
        self.export_name = QLineEdit()
        self.export_name.setPlaceholderText("Title and date")
        self.export_name.setToolTip("File name of the Excel / PowerPoint export (without "
                                    "extension).\nDefault from Settings ▸ Folder structure & "
                                    "naming ▸ Export file name")
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
        self._reload_palette_combo()
        self.palette.setToolTip("Colour scheme of the exported workbook and deck (navy titles, "
                                "colour-coded sheet tabs). \"New custom palette...\" picks 3 "
                                "colours and derives the rest.")
        rows = [("Title", self.title), ("File name", self.export_name),
                ("Organization", self.org), ("Operator", self.operator),
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

        # ---------------------------------------------------------- charts (UX-14)
        self.sec_charts = CollapsibleSection("Charts", False)
        chart_host = QWidget()
        cv = QVBoxLayout(chart_host)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(SPACE.sm)
        self.normal_fit_cb = QCheckBox("Normal-fit overlay")
        self.normal_fit_cb.setToolTip("Smoothed normal-fit line on the area and diameter "
                                      "distribution charts (Excel + PowerPoint)")
        cv.addWidget(self.normal_fit_cb)
        self.metric_rows: dict = {}
        for metric, metric_label in CHART_METRICS:
            cv.addWidget(label(metric_label, "body_strong"))
            g = QGridLayout()
            g.setHorizontalSpacing(SPACE.sm)
            g.setVerticalSpacing(SPACE.xs)
            g.setColumnStretch(1, 1)
            enabled_cb = QCheckBox("Show this chart")
            g.addWidget(enabled_cb, 0, 0, 1, 2)
            min_edit, max_edit = QLineEdit(), QLineEdit()
            min_edit.setPlaceholderText("Auto")
            max_edit.setPlaceholderText("Auto")
            min_edit.setToolTip(f"Only chart {metric_label.lower()} values >= this")
            max_edit.setToolTip(f"Only chart {metric_label.lower()} values <= this")
            range_row = QHBoxLayout()
            range_row.setSpacing(SPACE.xs)
            range_row.addWidget(min_edit)
            range_row.addWidget(label("to", tone="secondary"))
            range_row.addWidget(max_edit)
            g.addWidget(label("Range", tone="secondary"), 1, 0)
            g.addLayout(range_row, 1, 1)
            title_edit = QLineEdit()
            title_edit.setPlaceholderText(f"Grain {metric_label} Distribution")
            title_edit.setToolTip("Chart title (axis titles always keep their unit)")
            g.addWidget(label("Chart title", tone="secondary"), 2, 0)
            g.addWidget(title_edit, 2, 1)
            swatch = Swatch("#888888", 16)
            color_edit = QLineEdit()
            color_edit.setPlaceholderText("Palette colour")
            color_edit.setToolTip("Bar colour override, e.g. #2E5FA3 (blank = palette colour)")
            color_row = QHBoxLayout()
            color_row.setSpacing(SPACE.xs)
            color_row.addWidget(swatch, 0, Qt.AlignVCenter)
            color_row.addWidget(color_edit, 1)
            g.addWidget(label("Bar colour", tone="secondary"), 3, 0)
            g.addLayout(color_row, 3, 1)
            cv.addLayout(g)
            self.metric_rows[metric] = dict(enabled=enabled_cb, min=min_edit, max=max_edit,
                                            title=title_edit, color=color_edit, swatch=swatch)
        self.btn_chart_default = AnimatedButton("Save as my default", "save", "ghost", "sm")
        self.btn_chart_default.setToolTip("Use these chart options for every new report on this PC")
        cv.addWidget(self.btn_chart_default, 0, Qt.AlignLeft)
        self.sec_charts.add_widget(chart_host)
        v.addWidget(self.sec_charts)

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
        self.export_name.textEdited.connect(lambda t: self._set("export_basename", t.strip()))
        self.org.textEdited.connect(lambda t: self._set("organization", t))
        self.operator.textEdited.connect(lambda t: self._set("operator", t))
        self.date.textEdited.connect(lambda t: self._set("date", t))
        self.btn_now.clicked.connect(self._now)
        self.btn_logo.clicked.connect(page.choose_logo)
        self.btn_logo_clear.clicked.connect(page.clear_logo)
        self.units.current_changed.connect(lambda i: self._set("units", UNITS[i]))
        self.bins_area.valueChanged.connect(lambda n: self._set_bins("area", n))
        self.bins_diam.valueChanged.connect(lambda n: self._set_bins("diameter", n))
        self.palette.currentIndexChanged.connect(self._on_palette_changed)
        self.normal_fit_cb.toggled.connect(lambda on: self._set_chart_option("normal_fit", on))
        for metric, w in self.metric_rows.items():
            w["enabled"].toggled.connect(
                lambda on, m=metric: self._set_metric_option(m, "enabled", on))
            w["min"].editingFinished.connect(lambda m=metric: self._commit_metric_range(m))
            w["max"].editingFinished.connect(lambda m=metric: self._commit_metric_range(m))
            w["title"].textEdited.connect(lambda t, m=metric: self._set_metric_option(m, "title", t))
            w["color"].textEdited.connect(lambda t, m=metric: self._on_metric_color_edited(m, t))
        self.btn_chart_default.clicked.connect(self._save_chart_defaults)

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
        if field == "units":
            # UX-14 fix: the min/max text fields show values in the unit
            # that is currently rendering (``_bound_display_unit``) -- that
            # unit just changed, so redraw them (the underlying stored
            # value + its recorded ``bound_unit`` are untouched; only the
            # *display* is converted).
            self._load_chart_options(self.model)
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
        for w, v in ((self.title, m.title), (self.export_name, m.export_basename),
                     (self.org, m.organization),
                     (self.operator, m.operator), (self.date, m.date)):
            if w.text() != (v or ""):
                w.setText(v or "")
                w.setCursorPosition(0)
        self.units.set_current_index(UNITS.index(m.units) if m.units in UNITS else 0,
                                     animate=False)
        self.bins_area.setValue(int(m.bins.get("area", 0) or 0))
        self.bins_diam.setValue(int(m.bins.get("diameter", 0) or 0))
        self._reload_palette_combo(select=m.theme)
        self._load_chart_options(m)
        self.set_logo_name(m.logo_path)
        self._filling = False
        self.load_exports()

    def _load_chart_options(self, m) -> None:
        opts = resolve_chart_options(m.chart_options)
        self.normal_fit_cb.setChecked(opts["normal_fit"])
        for metric, w in self.metric_rows.items():
            o = opts[metric]
            unit = self._bound_display_unit(metric)
            # UX-14 fix: show min/max converted from the unit they were
            # entered in (``bound_unit``) into the unit this chart is
            # currently rendering in -- ``None``/old-file bounds convert to
            # themselves (treated as already being in the current unit), so
            # this is a no-op for report.json files saved before the fix.
            lo = convert_bound(o["min"], o.get("bound_unit"), unit, metric)
            hi = convert_bound(o["max"], o.get("bound_unit"), unit, metric)
            w["enabled"].setChecked(o["enabled"])
            w["min"].setText(_fmt_optional_float(lo))
            w["max"].setText(_fmt_optional_float(hi))
            metric_label = CHART_METRIC_LABELS.get(metric, metric.title())
            w["min"].setToolTip(f"Only chart {metric_label.lower()} values >= this ({unit})")
            w["max"].setToolTip(f"Only chart {metric_label.lower()} values <= this ({unit})")
            if w["title"].text() != (o["title"] or ""):
                w["title"].setText(o["title"] or "")
            if w["color"].text() != (o["color"] or ""):
                w["color"].setText(o["color"] or "")
            w["swatch"].color = QColor(o["color"] or "#888888")
            w["swatch"].update()

    def _bound_display_unit(self, metric: str) -> str:
        """UX-14 fix: the unit chart-option min/max should be shown/typed in
        for ``metric`` ("area"|"diameter") -- the same unit the exported
        charts render in. Mirrors ``reports.excel_renderer._combined_unit``:
        every included image calibrated -> ``resolve_units`` of the first
        one (matches the report's ``units`` auto/um/nm preference);
        otherwise the uncalibrated px unit, since there is nothing to scale."""
        m = self.model
        images = m.ordered_images(included_only=True) if m is not None else []
        if images and all(i.has_calibration for i in images):
            au, _, du, _ = resolve_units(images[0].px_per_um, m.units)
            return au if metric == "area" else du
        return "px²" if metric == "area" else "px"

    # ------------------------------------------------------------------ palette (UX-15)
    def _reload_palette_combo(self, select: Optional[str] = None) -> None:
        """Rebuild the combo from the 4 built-ins + this PC's saved custom
        palettes (``AppSettings.custom_palettes``) + a trailing "New custom
        palette..." entry, then select ``select`` (or keep the current
        selection)."""
        keep = select if select is not None else self.palette.currentData()
        self.palette.blockSignals(True)
        self.palette.clear()
        for key, text in PALETTES:
            self.palette.addItem(text, key)
        customs = list(getattr(self.page.state.settings, "custom_palettes", None) or [])
        if customs:
            self.palette.insertSeparator(self.palette.count())
            for rec in customs:
                self.palette.addItem(rec.get("name") or "Custom", f"custom:{rec.get('id')}")
        self.palette.insertSeparator(self.palette.count())
        self.palette.addItem("New custom palette...", NEW_CUSTOM_PALETTE)
        i = self.palette.findData(keep) if keep else -1
        self.palette.setCurrentIndex(max(0, i))
        self.palette.blockSignals(False)

    def _on_palette_changed(self, _i: int) -> None:
        if self._filling or self.model is None:
            return
        key = self.palette.currentData()
        if key == NEW_CUSTOM_PALETTE:
            self._open_new_custom_palette_dialog()
            return
        custom = None
        if isinstance(key, str) and key.startswith("custom:"):
            pid = key[len("custom:"):]
            rec = next((p for p in (getattr(self.page.state.settings, "custom_palettes", None)
                                    or []) if str(p.get("id")) == pid), None)
            if rec is None:               # saved palette vanished (e.g. edited elsewhere)
                self._reload_palette_combo(select=self.model.theme)
                return
            custom = derive_custom_palette(rec.get("colors") or [], rec.get("name") or "Custom")
        self.model.theme = key
        self.model.custom_palette = custom
        self.doc_changed.emit("theme")

    def _open_new_custom_palette_dialog(self) -> None:
        from ui.dialogs.custom_palette_dialog import CustomPaletteDialog
        dlg = CustomPaletteDialog(parent=self)

        def on_submit(name: str, colors: list) -> None:
            settings = self.page.state.settings
            existing = list(getattr(settings, "custom_palettes", None) or [])
            pid = new_custom_palette_id(existing)
            settings.custom_palettes = existing + [{"id": pid, "name": name, "colors": colors}]
            self.page.state.save_settings()
            self.model.theme = f"custom:{pid}"
            self.model.custom_palette = derive_custom_palette(colors, name)
            self.doc_changed.emit("theme")

        dlg.submitted.connect(on_submit)
        dlg.exec()
        # Whether saved or cancelled: reflect the (possibly unchanged) model
        # theme, never leave the combo parked on the sentinel entry.
        self._reload_palette_combo(select=self.model.theme if self.model is not None else None)

    # ------------------------------------------------------------------ charts (UX-14)
    def _chart_opts(self) -> dict:
        """Full-shape, mutable ``model.chart_options`` -- resolving fills in
        every default so a partial/empty dict can be edited in place, and
        the resolved dict is written straight back onto the model."""
        self.model.chart_options = resolve_chart_options(self.model.chart_options)
        return self.model.chart_options

    def _set_chart_option(self, key: str, value) -> None:
        if self._filling or self.model is None:
            return
        self._chart_opts()[key] = value
        self.doc_changed.emit("chart_options")

    def _set_metric_option(self, metric: str, key: str, value) -> None:
        if self._filling or self.model is None:
            return
        self._chart_opts()[metric][key] = value
        self.doc_changed.emit("chart_options")

    def _commit_metric_range(self, metric: str) -> None:
        if self._filling or self.model is None:
            return
        w = self.metric_rows[metric]
        lo, hi = _parse_optional_float(w["min"].text()), _parse_optional_float(w["max"].text())
        opts = self._chart_opts()
        opts[metric]["min"], opts[metric]["max"] = lo, hi
        # UX-14 fix: record the unit these numbers were just typed in (the
        # field's current display unit) so renderers/the preview can convert
        # them correctly even if the report's units preference changes, or
        # "Auto" resolves a different unit per image, later. No bound left
        # -> no unit to record either.
        opts[metric]["bound_unit"] = self._bound_display_unit(metric) if (lo is not None or
                                                                           hi is not None) else None
        # Reflect back-parsed/cleared text (e.g. "abc" -> "") without
        # re-triggering editingFinished.
        w["min"].blockSignals(True)
        w["max"].blockSignals(True)
        w["min"].setText(_fmt_optional_float(lo))
        w["max"].setText(_fmt_optional_float(hi))
        w["min"].blockSignals(False)
        w["max"].blockSignals(False)
        self.doc_changed.emit("chart_options")

    def _on_metric_color_edited(self, metric: str, text: str) -> None:
        if self._filling or self.model is None:
            return
        hexv = normalize_hex(text) if text.strip() else None
        w = self.metric_rows[metric]
        w["swatch"].color = QColor(hexv or "#888888")
        w["swatch"].update()
        opts = self._chart_opts()
        opts[metric]["color"] = hexv or ""
        self.doc_changed.emit("chart_options")

    def _save_chart_defaults(self) -> None:
        if self.model is None:
            return
        self.page.state.settings.default_chart_options = dict(self._chart_opts())
        self.page.state.save_settings()
        self.page._toast("Saved as your default chart options", "Applied to new reports on "
                         "this PC.", "success")

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
            name = label(img.display(), "body_strong")
            name.setToolTip(img.image_path)
            self.sel_lay.addWidget(name)
            cb = QCheckBox("Include this image")
            cb.setChecked(img.include)
            cb.setToolTip("Untick to leave this image out of every sheet and slide")
            cb.toggled.connect(lambda on, i=img.id: self.page.set_image_included(i, on))
            self.sel_lay.addWidget(cb)
            self.include_box = cb
            au = "calibrated" if img.has_calibration else "not calibrated — sizes in px"
            levels = [(lab, val or "—") for (_, lab), val in
                      zip(m.level_columns(), m.row_levels(img))]
            if os.path.basename(img.image_path) != img.display():
                levels.append(("File", os.path.basename(img.image_path)))
            kv = KeyValueList(levels + [("Grains", f"{img.grain_count:,}"),
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
