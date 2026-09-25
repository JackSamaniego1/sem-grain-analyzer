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
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.dml.color import RGBColor
from pptx.oxml import parse_xml
from pptx.oxml.ns import nsdecls, nsuri
from pptx.util import Emu, Inches, Pt

from reports.charts import (
    SERIES, build_bins, filter_range, normal_fit, resolve_chart_options, resolve_units,
    resolve_palette, series_for,
)
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

# FIX-11/FIX-12 layout geometry. python-pptx text boxes and tables never
# auto-shrink/repaginate on save (that only happens live inside PowerPoint,
# if at all) -- these renderers must size and paginate content themselves
# before writing, or long titles/parameter lists/image counts silently
# overflow past the footer bar. ``*_SAFE_BOTTOM_IN`` always leaves a clear
# gap above the footer bar (top at ``SLIDE_H - 0.32in`` = 7.18in).
TITLE_TOP_IN = 2.6
TITLE_W_IN = 11.7
TITLE_MAX_PT = 40
TITLE_MIN_PT = 22

METHODS_BOX_TOP_IN = 1.3
METHODS_BOX_W_IN = 11.5
METHODS_FONT_PT = 16
METHODS_SAFE_BOTTOM_IN = 6.9

EXEC_TABLE_ROW_IN = 0.4
EXEC_TABLE_SAFE_BOTTOM_IN = 6.95


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
        elif s.type == "lot_comparison":
            if s.enabled and s.payload.get("parts"):
                plan.append(("lot_comparison", s))
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

    palette = resolve_palette(model.theme, model.custom_palette)
    navy = _hexrgb(palette["accent"])
    accent2 = _hexrgb(palette["accent2"])
    series = series_for(model.theme, model.custom_palette)

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
                # FIX-12: paginate across continuation slides once the
                # table would otherwise run past the footer.
                show_tiles_first = bool(model.sample_statistics and
                                         model.is_enabled("sample_statistics", default=True))
                cap_first = _exec_table_capacity(2.35 if show_tiles_first else 1.3)
                cap_rest = _exec_table_capacity(1.3)
                pages = _paginate_images_for_table(images, cap_first, cap_rest)
                for i, chunk in enumerate(pages):
                    _exec_summary_slide(new_slide(), model, images, navy, page_images=chunk,
                                         show_tiles=(i == 0), show_combined=(i == len(pages) - 1),
                                         heading_suffix="" if i == 0 else " (cont'd)")
                    _add_footer(prs.slides[-1], model, page[0], navy)
            elif kind == "charts":
                opts = resolve_chart_options(model.chart_options)
                if opts["area"]["enabled"]:
                    _distribution_slide(new_slide(), model, images, kind="area", series=series,
                                        navy=navy)
                    _add_footer(prs.slides[-1], model, page[0], navy)
                if opts["diameter"]["enabled"]:
                    _distribution_slide(new_slide(), model, images, kind="diameter", series=series,
                                        navy=navy)
                    _add_footer(prs.slides[-1], model, page[0], navy)
            elif kind == "lot_comparison":
                for part in sec.payload.get("parts") or []:
                    _lot_comparison_slide(new_slide(), part, navy)
                    _add_footer(prs.slides[-1], model, page[0], navy)
            elif kind == "images":
                for img in images:
                    _image_slide(new_slide(), model, img, tmpdir, navy)
                    _add_footer(prs.slides[-1], model, page[0], navy)
            elif kind == "methods":
                # FIX-12: paginate across continuation slides once the
                # methods text would otherwise run past the footer.
                pages = _paginate_methods_lines(_methods_lines(model))
                for i, chunk in enumerate(pages):
                    _methods_slide(new_slide(), model, navy, lines=chunk,
                                    heading_suffix="" if i == 0 else " (cont'd)")
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


def _wrap_line_count(text: str, avail_width_in: float, font_pt: float,
                      avg_char_width_factor: float = 0.52) -> int:
    """Estimate how many lines ``text`` wraps to inside a box
    ``avail_width_in`` inches wide at ``font_pt`` points, using an average
    per-character-width heuristic (~0.52x font size for bold sans-serif --
    the same trick spreadsheet "auto-fit" tools use). python-pptx text
    boxes are never measured against the real font metrics on save, and
    bundling/measuring the actual embedded font would pull in more than
    this bug fix needs, so this stays a deliberately simple, offline-safe
    (D-14) estimate -- used by FIX-11 (title/subtitle overlap) and FIX-12
    (methods/table overflow past the footer) to size and paginate content
    *before* writing it.
    """
    text = text or ""
    words = text.split()
    if not words:
        return 1
    char_w_in = max(0.02, font_pt * avg_char_width_factor / 72.0)
    chars_per_line = max(1, int(avail_width_in / char_w_in))
    lines = 1
    cur = 0
    for w in words:
        wl = len(w) + 1
        if cur and cur + wl > chars_per_line:
            lines += 1
            cur = wl
        else:
            cur += wl
    return lines


def _fit_title_font(text: str, avail_width_in: float, start_pt: int = TITLE_MAX_PT,
                     min_pt: int = TITLE_MIN_PT, max_lines: int = 2) -> Tuple[int, int]:
    """FIX-11: shrink the title font (in 2pt steps) until it wraps to at
    most ``max_lines`` lines, so a long user-entered report title never
    grows into an unbounded number of lines. Returns ``(font_pt, n_lines)``
    at the chosen size."""
    pt = start_pt
    while pt > min_pt:
        n = _wrap_line_count(text, avail_width_in, pt)
        if n <= max_lines:
            return pt, n
        pt -= 2
    return min_pt, _wrap_line_count(text, avail_width_in, min_pt)


def _convert_series_to_line(chart, series_index: int, color_hex: str, width_pt: float = 2.25) -> None:
    """FIX-13: render one series of a bar chart as a smoothed line overlay
    (the "Normal Fit" curve) instead of a bar.

    python-pptx's ``add_chart`` can only build a single-type chart --
    every series in a ``CategoryChartData`` becomes the same chart type --
    so a combo bar+line chart needs a direct OOXML edit: move the target
    series's ``<c:ser>`` out of ``<c:barChart>`` into a sibling
    ``<c:lineChart>`` plot that shares the same category/value axes. This
    is the standard python-pptx combo-chart recipe (there is no public API
    for it); mirrors ``excel_renderer._write_hist_block``'s
    ``bar.combine(line)`` (xlsxwriter's native combo-chart call) so both
    renderers draw the same bars-with-a-line-overlay chart.
    """
    ns = {"c": nsuri("c")}
    plot_area = chart._chartSpace.find(".//c:plotArea", ns)
    bar_chart = plot_area.find("c:barChart", ns) if plot_area is not None else None
    if bar_chart is None:
        return
    sers = bar_chart.findall("c:ser", ns)
    if series_index >= len(sers):
        return
    ser = sers[series_index]
    ax_ids = [el.get("val") for el in bar_chart.findall("c:axId", ns)]
    bar_chart.remove(ser)

    line_chart = parse_xml(
        '<c:lineChart %s><c:grouping val="standard"/><c:varyColors val="0"/></c:lineChart>'
        % nsdecls("c")
    )
    bar_chart.addnext(line_chart)

    sp_pr = parse_xml(
        '<c:spPr %s><a:ln w="%d"><a:solidFill><a:srgbClr val="%s"/></a:solidFill></a:ln></c:spPr>'
        % (nsdecls("c", "a"), int(width_pt * 12700), color_hex.lstrip("#"))
    )
    marker = parse_xml('<c:marker %s><c:symbol val="none"/></c:marker>' % nsdecls("c"))
    smooth = parse_xml('<c:smooth %s val="1"/>' % nsdecls("c"))

    cat_el = ser.find("c:cat", ns)
    insert_at = list(ser).index(cat_el) if cat_el is not None else len(list(ser))
    ser.insert(insert_at, marker)
    ser.insert(insert_at, sp_pr)
    ser.append(smooth)

    line_chart.append(ser)
    for axid_val in ax_ids:
        line_chart.append(parse_xml('<c:axId %s val="%s"/>' % (nsdecls("c"), axid_val)))


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

    # FIX-11: a long user-entered report title wraps to 2+ lines, but the
    # title text box's declared height never grows to match -- the
    # subtitle lines below used to start at a fixed offset that assumed a
    # single line, so a wrapped title ran straight into them. Shrink the
    # font just enough to cap wrapping at 2 lines, then size the box (and
    # everything below it) from the *actual* resulting line count instead
    # of a guessed constant.
    title_text = model.title or "Grain Analysis Report"
    font_pt, n_lines = _fit_title_font(title_text, TITLE_W_IN)
    line_h_in = font_pt * 1.3 / 72.0
    title_h_in = max(1.2, n_lines * line_h_in + 0.15)
    _textbox(slide, Inches(0.8), Inches(TITLE_TOP_IN), Inches(TITLE_W_IN), Inches(title_h_in),
              title_text, size=font_pt, bold=True, color=navy)
    top = Inches(TITLE_TOP_IN) + Inches(title_h_in) + Inches(0.1)

    if model.hierarchy:
        for h in model.hierarchy:
            line = f"{h.get('label', '')}: {h.get('value', '')}"
            _textbox(slide, Inches(0.8), top, Inches(11.7), Inches(0.4), line, size=16, color=GREY)
            top += Inches(0.4)
        _textbox(slide, Inches(0.8), top, Inches(11.7), Inches(0.4), model.date, size=16, color=GREY)
        top += Inches(0.4)
    else:
        meta = f"Sample/Lot: {_sample_lot_summary(model)}    |    {model.date}"
        _textbox(slide, Inches(0.8), top, Inches(11.7), Inches(0.5), meta, size=16, color=GREY)
        top += Inches(0.5)
    who = f"Operator: {model.operator or '—'}    |    Organization: {model.organization or '—'}"
    _textbox(slide, Inches(0.8), top, Inches(11.7), Inches(0.5), who, size=16, color=GREY)
    if model.logo_path and os.path.exists(model.logo_path):
        try:
            slide.shapes.add_picture(model.logo_path, SLIDE_W - Inches(2.3), Inches(0.5), height=Inches(1.0))
        except Exception:
            pass
    _verdict_badge(slide, model)


_VERDICT_COLORS = {
    "pass": RGBColor(0x2E, 0x7D, 0x32),
    "fail": RGBColor(0xC6, 0x28, 0x28),
    "inconclusive": RGBColor(0xE9, 0xA4, 0x00),
}


def _verdict_badge(slide, model: ReportModel) -> None:
    """INN-02 title-slide conformity badge -- only drawn when
    ``model.verdict`` is set (a spec is attached); with no spec, nothing is
    added and the title slide is unchanged."""
    v = model.verdict
    if not v or v.get("overall") in (None, "no_spec"):
        return
    overall = str(v.get("overall", "")).lower()
    color = _VERDICT_COLORS.get(overall, _VERDICT_COLORS["inconclusive"])
    label = {"pass": "PASS", "fail": "FAIL", "inconclusive": "INCONCLUSIVE"}.get(overall, overall.upper())
    w, h = Inches(2.6), Inches(0.6)
    left, top = SLIDE_W - w - Inches(0.5), Inches(1.75)
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, w, h)
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()
    tf = shape.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = label
    run.font.size = Pt(20)
    run.font.bold = True
    run.font.color.rgb = WHITE


def _sample_lot_summary(model: ReportModel) -> str:
    samples = sorted({i.sample_id for i in model.images if i.sample_id})
    lots = sorted({i.lot_number for i in model.images if i.lot_number})
    s = ", ".join(samples) or "—"
    l = ", ".join(lots) or "—"
    return f"{s} / {l}"


EXEC_TABLE_W_IN = 12.1
EXEC_TABLE_IMAGE_COL_IN = 2.6
EXEC_TABLE_LEVEL_COL_IN = 1.3
EXEC_TABLE_STAT_COLS = 6  # Grains, Mean Diam, Std Diam, Mean Area, Coverage %, Circularity
EXEC_TABLE_STAT_MIN_IN = 0.9


def _exec_table_col_widths(n_level_cols: int) -> List[float]:
    """FIX-12: fixed, generous width for the free-text "Image"/hierarchy
    columns so normal-length values don't wrap (see caller); the short
    numeric/stat columns split whatever width is left."""
    stat_total = EXEC_TABLE_W_IN - EXEC_TABLE_IMAGE_COL_IN - EXEC_TABLE_LEVEL_COL_IN * n_level_cols
    stat_w = max(EXEC_TABLE_STAT_MIN_IN, stat_total / EXEC_TABLE_STAT_COLS)
    return [EXEC_TABLE_IMAGE_COL_IN] + [EXEC_TABLE_LEVEL_COL_IN] * n_level_cols + [stat_w] * EXEC_TABLE_STAT_COLS


def _style_body_cell(cell, size: int = 11) -> None:
    """FIX-12: an explicit, compact body font (vs. the ~18-24pt table-style
    default python-pptx falls back to when no size is set) keeps row
    height predictable so the FIX-12 pagination math holds."""
    for p in cell.text_frame.paragraphs:
        for run in p.runs:
            run.font.size = Pt(size)


def _exec_table_capacity(table_top_in: float) -> int:
    """FIX-12: max image rows (excluding the header and the Combined row)
    that fit in one executive-summary table before it would run past the
    footer, at the table's fixed ``EXEC_TABLE_ROW_IN`` row height."""
    avail_rows = int((EXEC_TABLE_SAFE_BOTTOM_IN - table_top_in) / EXEC_TABLE_ROW_IN)
    return max(1, avail_rows - 2)


def _paginate_images_for_table(images: List[ImageSummary], capacity_first: int,
                                capacity_rest: int) -> List[List[ImageSummary]]:
    """FIX-12: split ``images`` across as many executive-summary slides as
    needed so the table's rows never run past the footer -- a fixed
    ``rows * 0.4in`` table used to grow straight through the footer bar
    (and even off the bottom of the slide) once a lot had more than a
    handful of images. ``capacity_first``/``capacity_rest`` differ because
    the first page may also carry the INN-27 lot tiles, which push the
    table down and leave it less room."""
    if len(images) <= capacity_first:
        return [list(images)]
    pages = [images[:capacity_first]]
    remaining = images[capacity_first:]
    while remaining:
        pages.append(remaining[:capacity_rest])
        remaining = remaining[capacity_rest:]
    return pages


def _exec_summary_slide(slide, model: ReportModel, images: List[ImageSummary], navy: RGBColor = NAVY, *,
                         page_images: Optional[List[ImageSummary]] = None, show_tiles: bool = True,
                         show_combined: bool = True, heading_suffix: str = "") -> None:
    """One executive-summary table slide. ``images`` is always the *full*
    included-image list (used for the lot tiles and the Combined row);
    ``page_images`` (FIX-12 pagination) is the subset of rows drawn on
    *this* slide -- defaults to all of ``images`` when the whole table
    fits on one slide."""
    _slide_heading(slide, "Executive Summary" + heading_suffix, navy)
    if not images:
        _textbox(slide, Inches(0.8), Inches(1.5), Inches(11), Inches(0.5), "No images included.", size=14)
        return
    page_images = images if page_images is None else page_images

    table_top_in = 1.3
    if show_tiles and model.sample_statistics and model.is_enabled("sample_statistics", default=True):
        _lot_tiles(slide, model, navy)
        table_top_in = 2.35

    level_cols = model.level_columns() if model.hierarchy else []
    headers = (["Image"] + [label for _, label in level_cols] +
               ["Grains", "Mean Diam", "Std Diam", "Mean Area", "Coverage %", "Circularity"])
    rows = len(page_images) + 1 + (1 if show_combined else 0)  # header + page images (+ combined)
    cols = len(headers)
    table_shape = slide.shapes.add_table(rows, cols, Inches(0.6), Inches(table_top_in), Inches(12.1),
                                          Inches(EXEC_TABLE_ROW_IN) * rows)
    table = table_shape.table
    # FIX-12: an equal-width "Image" column left long filenames/display
    # names wrapping to 2 lines, which makes PowerPoint auto-expand that
    # row well past our budgeted EXEC_TABLE_ROW_IN -- exactly the overflow
    # this fix is meant to prevent. Give Image (and any hierarchy level
    # columns, which can also hold free text) generous fixed width so
    # normal-length values fit on one line; split the rest evenly across
    # the short numeric/stat columns.
    for c, w_in in enumerate(_exec_table_col_widths(len(level_cols))):
        table.columns[c].width = Inches(w_in)

    for c, h in enumerate(headers):
        cell = table.cell(0, c)
        cell.text = h
        _style_header_cell(cell, navy)

    for r, img in enumerate(page_images, start=1):
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
            cell = table.cell(r, c)
            cell.text = v
            _style_body_cell(cell)

    if not show_combined:
        return

    # Combined row always summarises *all* included images, not just the
    # rows drawn on this (last) page.
    all_grains = [g for img in images for g in img.grains]
    r = len(page_images) + 1
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


def _lot_tile_text(st: dict) -> Tuple[str, str]:
    """(big value, caption) for one INN-27 lot tile, e.g.
    ("G 7.40 ± 0.39", "Lot L-44A · 95 % CI · n = 5 of 5 · %RA 3.1 %")."""
    g, ci = st.get("G_mean"), st.get("G_ci95")
    if g is None:
        value = "G n/a"
    elif ci is None:
        value = f"G {g:.2f} ± n/a"
    else:
        value = f"G {g:.2f} ± {ci:.2f}"
    ra = st.get("RA_pct")
    parts = [f"{st.get('label') or st.get('scope', 'Lot')}", "95 % CI",
             f"n = {st.get('n_fields', 0)} of {st.get('n_needed', 0)}"]
    if ra is not None:
        parts.append(f"%RA {ra:.1f} %")
    if st.get("status"):
        parts.append(str(st["status"]))
    return value, " · ".join(parts)


def _lot_tiles(slide, model: ReportModel, navy: RGBColor = NAVY) -> None:
    """INN-27 "G ± CI" big-number tiles (max 3 lots) on the summary slide."""
    stats = model.sample_statistics[:3]
    w = Inches(12.1 / len(stats)) - Inches(0.1)
    for i, st in enumerate(stats):
        value, caption = _lot_tile_text(st)
        _metric_callout(slide, Inches(0.6) + i * (w + Inches(0.1)), Inches(1.2), w, Inches(0.95),
                        value, caption, navy)


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
    """UX-14: ``model.chart_options[kind]`` (min/max/title/colour) and the
    global ``normal_fit`` toggle — see ``reports.charts.
    resolve_chart_options`` — mirror the Excel renderer's ``_write_hist_block``
    so both exports agree."""
    label = "Grain Area" if kind == "area" else "Grain Diameter"
    _slide_heading(slide, f"Combined {label} Distribution", navy)
    opts = resolve_chart_options(model.chart_options)
    opt, show_fit = opts[kind], opts["normal_fit"]

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

    values = filter_range(values, opt.get("min"), opt.get("max"))
    n_bins = model.bins.get(kind, 0)
    labels, counts, edges = build_bins(values, n_bins)
    if not labels:
        _textbox(slide, Inches(0.8), Inches(1.5), Inches(11), Inches(0.5), "Not enough data for a histogram.",
                  size=14)
        return
    bin_labels = [f"{lbl} {unit}" for lbl in labels]

    chart_data = CategoryChartData()
    chart_data.categories = bin_labels
    chart_data.add_series("Count", counts)
    if show_fit:
        chart_data.add_series("Normal Fit", normal_fit(values, edges))

    x, y, cx, cy = Inches(0.8), Inches(1.2), Inches(11.7), Inches(5.8)
    gframe = slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, x, y, cx, cy, chart_data)
    chart = gframe.chart
    chart.has_title = True
    title = (opt.get("title") or "").strip() or f"{label} Distribution"
    chart.chart_title.text_frame.text = f"{title} (n={len(values)})"
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
            opt.get("color") or series["area_bar" if kind == "area" else "diameter_bar"])
    except Exception:
        pass
    if show_fit:
        # FIX-13: "Normal Fit" (series index 1) is a smoothed line overlay,
        # not a second set of bars -- matches the Excel renderer's
        # bar.combine(line).
        try:
            _convert_series_to_line(chart, series_index=1, color_hex=series["normal_fit"])
        except Exception:
            pass


def _image_slide(slide, model: ReportModel, img: ImageSummary, tmpdir: str, navy: RGBColor = NAVY) -> None:
    _slide_heading(slide, f"Image {img.order}: {img.display()}", navy)

    orig = _resized_png(tmpdir, img.image_path, max_w=900)
    ovl = _resized_png(tmpdir, img.overlay_path, max_w=900, blend_src=img.image_path,
                       opacity=model.overlay_opacity)
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


def _methods_lines(model: ReportModel) -> List[str]:
    params = model.metadata.get("detection_params") or {}
    lines: List[str] = []
    if model.hierarchy:
        lines += [f"{h.get('label', '')}: {h.get('value', '')}" for h in model.hierarchy]
    lines.append(f"Detection mode: {model.metadata.get('detection_mode', '—')}")
    for k, v in params.items():
        lines.append(f"{k}: {v}")
    lines.append(f"Calibrated: {'Yes' if any(i.has_calibration for i in model.images) else 'No'}")
    # FIX-07: mirror the Excel Methods sheet's INN-29 scale-verification
    # block (``ReportModel.calibration``) -- entirely optional, skipped
    # when no check was recorded so a report with the feature unused is
    # unchanged. Placed right after "Calibrated" like the Excel renderer.
    cal = model.calibration
    if cal and cal.get("text"):
        lines.append(f"Scale Verification: {cal.get('text')}")
        if cal.get("source"):
            lines.append(f"Verification Source: {cal.get('source')}")
        if cal.get("warnings"):
            lines.append("Verification Warnings: " + "; ".join(str(w) for w in cal.get("warnings")))
    lines.append(f"Instrument: {model.metadata.get('instrument', '—')}")
    lines.append(f"Software: Grain Analyzer v{APP_VERSION}")
    lines.append(f"Generated by: {model.operator or 'unknown operator'} / {model.organization or '—'} on {model.date}")
    if model.verdict and model.verdict.get("overall") not in (None, "no_spec"):
        spec_bits = " ".join(x for x in (model.verdict.get("spec_name"), model.verdict.get("spec_revision")) if x)
        lines.append(f"Specification: {spec_bits or '—'} ({model.verdict.get('decision_rule', '—')} acceptance)")
        if model.verdict.get("statement"):
            lines.append(str(model.verdict["statement"]))
    return lines


def _paginate_methods_lines(lines: List[str]) -> List[List[str]]:
    """FIX-12: split ``lines`` across as many Methods slides as needed so
    the text box never overflows past the footer -- a long
    ``detection_params`` dict or spec statement used to overflow a single
    fixed-height text box straight through the footer bar (python-pptx
    text boxes never grow/shrink to fit their text on save)."""
    avail_in = METHODS_SAFE_BOTTOM_IN - METHODS_BOX_TOP_IN
    line_h_in = METHODS_FONT_PT * 1.3 / 72.0
    max_lines = max(1, int(avail_in / line_h_in))
    pages: List[List[str]] = []
    page: List[str] = []
    used = 0
    for line in lines:
        n = max(1, _wrap_line_count(line, METHODS_BOX_W_IN, METHODS_FONT_PT))
        if page and used + n > max_lines:
            pages.append(page)
            page = []
            used = 0
        page.append(line)
        used += n
    pages.append(page)
    return pages


def _methods_slide(slide, model: ReportModel, navy: RGBColor = NAVY, *,
                    lines: Optional[List[str]] = None, heading_suffix: str = "") -> None:
    _slide_heading(slide, "Methods & Parameters" + heading_suffix, navy)
    if lines is None:
        lines = _methods_lines(model)
    _textbox(slide, Inches(0.8), Inches(METHODS_BOX_TOP_IN), Inches(METHODS_BOX_W_IN),
              Inches(METHODS_SAFE_BOTTOM_IN - METHODS_BOX_TOP_IN), "\n".join(lines), size=METHODS_FONT_PT)


def _lot_comparison_slide(slide, part: Dict[str, Any], navy: RGBColor = NAVY) -> None:
    """UX-13: one slide per part -- a per-lot G summary table, plus either
    the equivalence-vs-baseline table (when that part has a baseline lot)
    or the ΔG matrix (when it does not but has >= 2 lots). ``part`` is one
    entry of ``reports.multi_lot.build_multi_lot_report_model``'s
    ``Section(type="lot_comparison").payload["parts"]``."""
    title = f"Lot Comparison — {part.get('part', '')}"
    if part.get("job"):
        title += f" (Job {part['job']})"
    _slide_heading(slide, title, navy)
    cmp_ = part["comparison"]
    summaries = cmp_["summaries"]

    headers = ["Lot", "Fields (n)", "Mean G", "95 % CI (±)", "Std Dev G"]
    rows = len(summaries) + 1
    table_shape = slide.shapes.add_table(rows, len(headers), Inches(0.6), Inches(1.15),
                                         Inches(6.2), Inches(0.4) * rows)
    table = table_shape.table
    for c, h in enumerate(headers):
        cell = table.cell(0, c)
        cell.text = h
        _style_header_cell(cell, navy)
    for r, s in enumerate(summaries, start=1):
        ci = (s["mean"] - s["ci_low"]) if s.get("mean") is not None and s.get("ci_low") is not None \
            else None
        vals = [s["label"], str(s["n"]),
                f"{s['mean']:.2f}" if s.get("mean") is not None else "—",
                f"{ci:.2f}" if ci is not None else "—",
                f"{s['sd']:.2f}" if s.get("sd") is not None else "—"]
        for c, v in enumerate(vals):
            cell = table.cell(r, c)
            cell.text = v
            _style_body_cell(cell)

    baseline, equiv = part.get("baseline"), cmp_.get("equivalence") or {}
    right_x = Inches(7.1)
    if baseline and equiv:
        _textbox(slide, right_x, Inches(1.15), Inches(5.6), Inches(0.35),
                 f"Equivalence vs baseline ({baseline})", size=14, bold=True)
        headers2 = ["Lot", "ΔG", "90 % CI", "Verdict"]
        rows2 = len(equiv) + 1
        t2 = slide.shapes.add_table(rows2, len(headers2), right_x, Inches(1.55), Inches(5.6),
                                    Inches(0.4) * rows2).table
        for c, h in enumerate(headers2):
            cell = t2.cell(0, c)
            cell.text = h
            _style_header_cell(cell, navy)
        for r, (lot, eq) in enumerate(equiv.items(), start=1):
            ci_txt = (f"[{eq['ci_low']:.2f}, {eq['ci_high']:.2f}]"
                     if eq.get("ci_low") is not None and eq.get("ci_high") is not None else "—")
            vals = [lot, f"{eq['dG']:.2f}" if eq.get("dG") is not None else "—", ci_txt,
                    str(eq.get("verdict", "—"))]
            for c, v in enumerate(vals):
                cell = t2.cell(r, c)
                cell.text = v
                _style_body_cell(cell)
    elif len(summaries) >= 2:
        _textbox(slide, right_x, Inches(1.15), Inches(5.6), Inches(0.35),
                 "ΔG matrix (row → column; + = column finer)", size=14, bold=True)
        labels = [s["label"] for s in summaries]
        matrix = cmp_["matrix"]
        n = len(labels)
        t3 = slide.shapes.add_table(n + 1, n + 1, right_x, Inches(1.55), Inches(5.6),
                                    Inches(0.4) * (n + 1)).table
        t3.cell(0, 0).text = ""
        for j, lb in enumerate(labels, start=1):
            cell = t3.cell(0, j)
            cell.text = lb
            _style_header_cell(cell, navy)
        for i, lb in enumerate(labels, start=1):
            cell = t3.cell(i, 0)
            cell.text = lb
            _style_header_cell(cell, navy)
            for j in range(n):
                v = matrix[i - 1][j]["dG"]
                cell = t3.cell(i, j + 1)
                cell.text = f"{v:+.2f}" if v is not None else "—"
                _style_body_cell(cell)


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
