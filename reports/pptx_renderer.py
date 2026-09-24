"""PowerPoint renderer (python-pptx) for ``ReportModel``.

16:9 deck built in ``Section.order`` (the designer's outline order), with
two structural rules always enforced (matching the Excel renderer and the
designer): a ``cover`` section (if enabled) is the title slide and an
``overview_table`` section (if enabled) is the executive summary — combined
distribution slides (NATIVE, editable charts), one slide per included image
(original + overlay side by side with key-metric callouts, in
``ImageSummary.order``), a methods slide and ``custom_text`` slides can
appear in any order/mix the designer picked; the appendix slide (pointing at
the Excel workbook for raw data) is always last, mirroring "raw data always
last" in Excel. Every section's ``enabled``/``title`` is honoured. Chart
colours and navy accents follow the designer's palette (``ReportModel.theme``
— see ``reports.charts.PALETTES``), the same one the Excel renderer uses.

Supports an optional corporate ``template_path`` (``Presentation(template)``)
— when given, its layouts are reused; slide content is still built with
explicit shapes so the result is template-agnostic either way.
"""
from __future__ import annotations

import os
import tempfile
from typing import Dict, List, Optional, Tuple

import numpy as np
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.dml.color import RGBColor
from pptx.util import Emu, Inches, Pt

from reports.charts import SERIES, build_bins, normal_fit, resolve_units, resolve_palette, series_for
from reports.model import ReportModel, ImageSummary, Section
from reports.excel_renderer import _resized_png, _row_size_stats

try:
    from version import __version__ as APP_VERSION
except Exception:  # pragma: no cover
    APP_VERSION = "unknown"

SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)

NAVY = RGBColor(0x1A, 0x2B, 0x4A)
TEAL = RGBColor(0x00, 0x79, 0x6C)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
GREY = RGBColor(0x75, 0x75, 0x75)
TEXT_DARK = RGBColor(0x1A, 0x1A, 0x2E)
LIGHT_BAND = RGBColor(0xF2, 0xF4, 0xF7)


def _hexrgb(h: str) -> RGBColor:
    h = h.lstrip("#")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _blank_layout(prs: Presentation):
    for layout in prs.slide_layouts:
        if layout.name.lower() in ("blank",):
            return layout
    return prs.slide_layouts[min(6, len(prs.slide_layouts) - 1)]


def _build_plan(model: ReportModel, images: List[ImageSummary]) -> List[Tuple[str, Optional[Section]]]:
    """Slide order following ``Section.order`` (the designer's outline).

    ``raw_data`` (→ the appendix slide) is excluded here and always added
    last by ``render_pptx`` — the same "raw data at the end" rule the Excel
    renderer enforces. Per-image slides are one ``("images", None)`` entry
    at the position of the first ``image`` section; ``ImageSummary.order``
    governs the order within that block.
    """
    plan: List[Tuple[str, Optional[Section]]] = []
    images_emitted = False
    for s in sorted(model.sections, key=lambda s: s.order):
        if s.type == "raw_data":
            continue
        if s.type == "cover":
            if s.enabled:
                plan.append(("cover", s))
            continue
        if s.type == "overview_table":
            if s.enabled:
                plan.append(("overview_table", s))
            continue
        if s.type == "image":
            if not images_emitted:
                plan.append(("images", None))
                images_emitted = True
            continue
        if s.type == "combined_distribution":
            if s.enabled and images:
                plan.append(("charts", s))
        elif s.type == "parameters":
            if s.enabled:
                plan.append(("methods", s))
        elif s.type == "custom_text":
            if s.enabled:
                plan.append(("custom_text", s))
    if images and not images_emitted:
        plan.append(("images", None))
    return plan


def render_pptx(model: ReportModel, output_path: str, template_path: Optional[str] = None) -> str:
    if template_path and os.path.exists(template_path):
        prs = Presentation(template_path)
    else:
        prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H
    layout = _blank_layout(prs)

    images = model.ordered_images(included_only=True)
    want_raw = model.is_enabled("raw_data", default=True)
    plan = _build_plan(model, images)

    palette = resolve_palette(model.theme)
    navy = _hexrgb(palette["accent"])
    accent2 = _hexrgb(palette["accent2"])
    series = series_for(model.theme)

    with tempfile.TemporaryDirectory(prefix="grain_report_pptx_") as tmpdir:
        page = [0]

        def new_slide():
            s = prs.slides.add_slide(layout)
            page[0] += 1
            return s

        for kind, sec in plan:
            if kind == "cover":
                _title_slide(new_slide(), model, navy, accent2)
                _add_footer(prs.slides[-1], model, page[0], navy)
            elif kind == "overview_table":
                _exec_summary_slide(new_slide(), model, images, navy)
                _add_footer(prs.slides[-1], model, page[0], navy)
            elif kind == "charts":
                _distribution_slide(new_slide(), model, images, kind="area", series=series, navy=navy)
                _add_footer(prs.slides[-1], model, page[0], navy)
                _distribution_slide(new_slide(), model, images, kind="diameter", series=series, navy=navy)
                _add_footer(prs.slides[-1], model, page[0], navy)
            elif kind == "images":
                for img in images:
                    _image_slide(new_slide(), model, img, tmpdir, navy)
                    _add_footer(prs.slides[-1], model, page[0], navy)
            elif kind == "methods":
                _methods_slide(new_slide(), model, navy)
                _add_footer(prs.slides[-1], model, page[0], navy)
            elif kind == "custom_text":
                _text_slide(new_slide(), model, sec, navy)
                _add_footer(prs.slides[-1], model, page[0], navy)

        if want_raw:
            _appendix_slide(new_slide(), model, navy)
            _add_footer(prs.slides[-1], model, page[0], navy)

    prs.save(output_path)
    return output_path


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _textbox(slide, left, top, width, height, text, *, size=18, bold=False, color=TEXT_DARK,
             align=PP_ALIGN.LEFT, font="Calibri"):
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = font
    return box


def _fill_rect(slide, left, top, width, height, color):
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()
    return shape


def _footer_text(model: ReportModel, page_num: int) -> str:
    """'<Job #> 24-117 · <Part Number> 7718-A · <Lot> L-44A · page n' when a
    hierarchy is set; otherwise the legacy '<title> ... page' footer."""
    if model.hierarchy:
        levels = " · ".join(f"{h.get('label', '')} {h.get('value', '')}".strip()
                                  for h in model.hierarchy)
        return f"{levels} · page {page_num}" if levels else f"page {page_num}"
    return model.title or "Grain Analysis Report"


def _add_footer(slide, model: ReportModel, page_num: int, navy: RGBColor = NAVY) -> None:
    _fill_rect(slide, 0, SLIDE_H - Inches(0.32), SLIDE_W, Inches(0.32), navy)
    _textbox(slide, Inches(0.3), SLIDE_H - Inches(0.32), Inches(9), Inches(0.32),
              _footer_text(model, page_num), size=10, color=WHITE, align=PP_ALIGN.LEFT)
    _textbox(slide, SLIDE_W - Inches(1.3), SLIDE_H - Inches(0.32), Inches(1.0), Inches(0.32),
              str(page_num), size=10, color=WHITE, align=PP_ALIGN.RIGHT)


def _metric_callout(slide, left, top, width, height, value: str, label: str, navy: RGBColor = NAVY):
    _fill_rect(slide, left, top, width, height, LIGHT_BAND)
    _textbox(slide, left, top + Inches(0.06), width, Inches(0.5), value, size=22, bold=True,
              color=navy, align=PP_ALIGN.CENTER)
    _textbox(slide, left, top + height - Inches(0.35), width, Inches(0.3), label, size=10,
              color=GREY, align=PP_ALIGN.CENTER)


# ---------------------------------------------------------------------------
# Slides
# ---------------------------------------------------------------------------

def _title_slide(slide, model: ReportModel, navy: RGBColor = NAVY, accent2: RGBColor = TEAL) -> None:
    _fill_rect(slide, 0, 0, SLIDE_W, SLIDE_H, WHITE)
    _fill_rect(slide, 0, 0, SLIDE_W, Inches(0.18), navy)
    _fill_rect(slide, 0, Inches(0.18), SLIDE_W, Inches(0.06), accent2)
    _textbox(slide, Inches(0.8), Inches(2.6), Inches(11.7), Inches(1.2), model.title or "Grain Analysis Report",
              size=40, bold=True, color=navy)
    if model.hierarchy:
        top = Inches(3.7)
        for h in model.hierarchy:
            line = f"{h.get('label', '')}: {h.get('value', '')}"
            _textbox(slide, Inches(0.8), top, Inches(11.7), Inches(0.4), line, size=16, color=GREY)
            top += Inches(0.4)
        _textbox(slide, Inches(0.8), top, Inches(11.7), Inches(0.4), model.date, size=16, color=GREY)
        top += Inches(0.4)
    else:
        meta = f"Sample/Lot: {_sample_lot_summary(model)}    |    {model.date}"
        _textbox(slide, Inches(0.8), Inches(3.7), Inches(11.7), Inches(0.5), meta, size=16, color=GREY)
        top = Inches(4.2)
    who = f"Operator: {model.operator or '—'}    |    Organization: {model.organization or '—'}"
    _textbox(slide, Inches(0.8), top, Inches(11.7), Inches(0.5), who, size=16, color=GREY)
    if model.logo_path and os.path.exists(model.logo_path):
        try:
            slide.shapes.add_picture(model.logo_path, SLIDE_W - Inches(2.3), Inches(0.5), height=Inches(1.0))
        except Exception:
            pass


def _sample_lot_summary(model: ReportModel) -> str:
    samples = sorted({i.sample_id for i in model.images if i.sample_id})
    lots = sorted({i.lot_number for i in model.images if i.lot_number})
    s = ", ".join(samples) or "—"
    l = ", ".join(lots) or "—"
    return f"{s} / {l}"


def _exec_summary_slide(slide, model: ReportModel, images: List[ImageSummary], navy: RGBColor = NAVY) -> None:
    _slide_heading(slide, "Executive Summary", navy)
    if not images:
        _textbox(slide, Inches(0.8), Inches(1.5), Inches(11), Inches(0.5), "No images included.", size=14)
        return

    level_cols = model.level_columns() if model.hierarchy else []
    headers = (["Image"] + [label for _, label in level_cols] +
               ["Grains", "Mean Diam", "Std Diam", "Mean Area", "Coverage %", "Circularity"])
    rows = len(images) + 2  # header + images + combined
    cols = len(headers)
    table_shape = slide.shapes.add_table(rows, cols, Inches(0.6), Inches(1.3), Inches(12.1), Inches(0.4) * rows)
    table = table_shape.table
    for c, h in enumerate(headers):
        cell = table.cell(0, c)
        cell.text = h
        _style_header_cell(cell, navy)

    all_grains = []
    for r, img in enumerate(images, start=1):
        au, du, mean_a, med_a, std_a, mean_d, std_d = _row_size_stats(model, img)
        vals = (
            [img.display()] + model.row_levels(img) + [
                str(img.grain_count),
                f"{mean_d:.2f} {du}",
                f"{std_d:.2f} {du}",
                f"{mean_a:.2f} {au}",
                f"{img.grain_coverage_pct:.1f}",
                f"{img.mean_circularity:.3f}",
            ]
        ) if model.hierarchy else [
            img.display(),
            str(img.grain_count),
            f"{mean_d:.2f} {du}",
            f"{std_d:.2f} {du}",
            f"{mean_a:.2f} {au}",
            f"{img.grain_coverage_pct:.1f}",
            f"{img.mean_circularity:.3f}",
        ]
        for c, v in enumerate(vals):
            table.cell(r, c).text = v
        all_grains.extend(img.grains)

    r = len(images) + 1
    calibrated_all = all(i.has_calibration for i in images)
    if calibrated_all and images:
        au, am, du, dm = resolve_units(images[0].px_per_um, model.units)
        diams = np.array([g["diameter_um"] for g in all_grains]) * dm if all_grains else np.array([0.0])
        areas = np.array([g["area_um2"] for g in all_grains]) * am if all_grains else np.array([0.0])
    else:
        au, du = "px²", "px"
        diams = np.array([g["diameter_px"] for g in all_grains]) if all_grains else np.array([0.0])
        areas = np.array([g["area_px"] for g in all_grains]) if all_grains else np.array([0.0])
    combined = (
        ["Combined"] + ([""] * len(level_cols)) + [
            str(len(all_grains)), f"{float(np.mean(diams)):.2f} {du}", f"{float(np.std(diams)):.2f} {du}",
            f"{float(np.mean(areas)):.2f} {au}", f"{float(np.mean([i.grain_coverage_pct for i in images])):.1f}",
            f"{float(np.mean([g['circularity'] for g in all_grains])) if all_grains else 0.0:.3f}",
        ]
    )
    for c, v in enumerate(combined):
        cell = table.cell(r, c)
        cell.text = v
        _style_total_cell(cell)


def _style_header_cell(cell, navy: RGBColor = NAVY) -> None:
    cell.fill.solid()
    cell.fill.fore_color.rgb = navy
    for p in cell.text_frame.paragraphs:
        p.alignment = PP_ALIGN.CENTER
        for run in p.runs:
            run.font.color.rgb = WHITE
            run.font.bold = True
            run.font.size = Pt(12)


def _style_total_cell(cell) -> None:
    cell.fill.solid()
    cell.fill.fore_color.rgb = RGBColor(0xD9, 0xDE, 0xE8)
    for p in cell.text_frame.paragraphs:
        for run in p.runs:
            run.font.bold = True
            run.font.size = Pt(11)


def _slide_heading(slide, text: str, navy: RGBColor = NAVY) -> None:
    _fill_rect(slide, 0, 0, SLIDE_W, Inches(0.9), navy)
    _textbox(slide, Inches(0.5), Inches(0.15), Inches(12), Inches(0.6), text, size=26, bold=True, color=WHITE)


def _distribution_slide(slide, model: ReportModel, images: List[ImageSummary], kind: str,
                         series: Dict[str, str] = SERIES, navy: RGBColor = NAVY) -> None:
    label = "Grain Area" if kind == "area" else "Grain Diameter"
    _slide_heading(slide, f"Combined {label} Distribution", navy)

    all_grains = [g for img in images for g in img.grains]
    calibrated_all = all(i.has_calibration for i in images)
    if calibrated_all and images:
        au, am, du, dm = resolve_units(images[0].px_per_um, model.units)
        if kind == "area":
            values, unit = [g["area_um2"] * am for g in all_grains], au
        else:
            values, unit = [g["diameter_um"] * dm for g in all_grains], du
    else:
        if kind == "area":
            values, unit = [g["area_px"] for g in all_grains], "px²"
        else:
            values, unit = [g["diameter_px"] for g in all_grains], "px"

    n_bins = model.bins.get(kind, 0)
    labels, counts, edges = build_bins(values, n_bins)
    if not labels:
        _textbox(slide, Inches(0.8), Inches(1.5), Inches(11), Inches(0.5), "Not enough data for a histogram.",
                  size=14)
        return
    fit = normal_fit(values, edges)
    bin_labels = [f"{lbl} {unit}" for lbl in labels]

    chart_data = CategoryChartData()
    chart_data.categories = bin_labels
    chart_data.add_series("Count", counts)
    chart_data.add_series("Normal Fit", fit)

    x, y, cx, cy = Inches(0.8), Inches(1.2), Inches(11.7), Inches(5.8)
    gframe = slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, x, y, cx, cy, chart_data)
    chart = gframe.chart
    chart.has_title = True
    chart.chart_title.text_frame.text = f"{label} Distribution (n={len(all_grains)})"
    chart.has_legend = True
    chart.legend.position = XL_LEGEND_POSITION.BOTTOM
    chart.legend.include_in_layout = False
    cat_axis = chart.category_axis
    cat_axis.axis_title.text_frame.text = f"{label} ({unit})"
    val_axis = chart.value_axis
    val_axis.axis_title.text_frame.text = "Number of Grains"
    val_axis.has_major_gridlines = True
    try:
        chart.plots[0].series[0].format.fill.solid()
        chart.plots[0].series[0].format.fill.fore_color.rgb = _hexrgb(
            series["area_bar" if kind == "area" else "diameter_bar"])
    except Exception:
        pass


def _image_slide(slide, model: ReportModel, img: ImageSummary, tmpdir: str, navy: RGBColor = NAVY) -> None:
    _slide_heading(slide, f"Image {img.order}: {img.display()}", navy)

    orig = _resized_png(tmpdir, img.image_path, max_w=900)
    ovl = _resized_png(tmpdir, img.overlay_path, max_w=900)
    pic_top = Inches(1.05)
    pic_h = Inches(3.6)
    if orig:
        path, w, h = orig
        slide.shapes.add_picture(path, Inches(0.5), pic_top, height=pic_h)
    if ovl:
        path, w, h = ovl
        slide.shapes.add_picture(path, Inches(6.9), pic_top, height=pic_h)
    elif not orig:
        _textbox(slide, Inches(0.5), pic_top, Inches(12), Inches(0.5), "(image not available)", size=14, color=GREY)

    au, du, mean_a, med_a, std_a, mean_d, std_d = _row_size_stats(model, img)
    metrics = [
        (str(img.grain_count), "Grains"),
        (f"{mean_d:.2f} {du}", "Mean Diameter"),
        (f"{mean_a:.2f} {au}", "Mean Area"),
        (f"{img.grain_coverage_pct:.1f}%", "Coverage"),
        (f"{img.mean_circularity:.3f}", "Mean Circularity"),
        (f"{img.mean_aspect_ratio:.3f}", "Mean Aspect Ratio"),
    ]
    top = Inches(4.85)
    cw = Inches(1.95)
    for i, (val, lbl) in enumerate(metrics):
        _metric_callout(slide, Inches(0.5) + cw * i, top, cw - Inches(0.08), Inches(0.9), val, lbl, navy)

    cap = (img.caption + ("\n" + img.notes if img.notes else "")).strip()
    if cap:
        _textbox(slide, Inches(0.5), top + Inches(1.05), Inches(11.7), Inches(0.8), cap, size=12, color=GREY)


def _methods_slide(slide, model: ReportModel, navy: RGBColor = NAVY) -> None:
    _slide_heading(slide, "Methods & Parameters", navy)
    params = model.metadata.get("detection_params") or {}
    lines = []
    if model.hierarchy:
        lines += [f"{h.get('label', '')}: {h.get('value', '')}" for h in model.hierarchy]
    lines.append(f"Detection mode: {model.metadata.get('detection_mode', '—')}")
    for k, v in params.items():
        lines.append(f"{k}: {v}")
    lines.append(f"Calibrated: {'Yes' if any(i.has_calibration for i in model.images) else 'No'}")
    lines.append(f"Instrument: {model.metadata.get('instrument', '—')}")
    lines.append(f"Software: Grain Analyzer v{APP_VERSION}")
    lines.append(f"Generated by: {model.operator or 'unknown operator'} / {model.organization or '—'} on {model.date}")
    _textbox(slide, Inches(0.8), Inches(1.3), Inches(11.5), Inches(5.5), "\n".join(lines), size=16)


def _appendix_slide(slide, model: ReportModel, navy: RGBColor = NAVY) -> None:
    _slide_heading(slide, "Appendix", navy)
    _textbox(slide, Inches(0.8), Inches(1.5), Inches(11.5), Inches(2.0),
              "Full per-grain raw measurement data for every image is provided in the "
              "companion Excel workbook (sheets named \"Raw - <image>\"), not duplicated here.",
              size=16)


def _text_slide(slide, model: ReportModel, sec: Section, navy: RGBColor = NAVY) -> None:
    """Native rendering of a designer ``custom_text`` section — a clean
    heading + word-wrapped body, one paragraph per line. Replaces the old
    post-processing hack (``ui.pages.report_builder.add_custom_text_slides``)
    that poked this module's private helpers from the UI layer."""
    _slide_heading(slide, sec.title or "Notes", navy)
    box = slide.shapes.add_textbox(Inches(0.8), Inches(1.3), Inches(11.7), Inches(5.4))
    tf = box.text_frame
    tf.word_wrap = True
    body = str(sec.payload.get("body", "") or "")
    lines = body.split("\n") or [""]
    for n, line in enumerate(lines):
        p = tf.paragraphs[0] if n == 0 else tf.add_paragraph()
        run = p.add_run()
        run.text = line
        run.font.size = Pt(16)
        run.font.name = "Calibri"
        run.font.color.rgb = TEXT_DARK
