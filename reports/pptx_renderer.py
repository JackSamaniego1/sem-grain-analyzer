"""PowerPoint renderer (python-pptx) for ``ReportModel``.

16:9 deck built in ``Section.order`` (the designer's outline order), with
structural rules always enforced (matching the Excel renderer and the
designer): a ``cover`` section (if enabled) is the title slide; an
``overview_table`` section (if enabled) is, in order, (1) a part-summary
table slide -- one row per PART, averaged across its lots (D-30 /
REP-DESIGN-01, see ``_part_summary_rows``) -- (2) three native bar charts
(ASTM G / mean diameter / mean area, one bar per part), and (3) the
per-image data-table slides, split so a part's rows never continue onto the
next part's slide (``_plan_image_table_slides``); combined distribution
slides (NATIVE, editable charts) follow; one slide per included image
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

import math
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
    SERIES, build_bins, convert_bound, filter_range, normal_fit, resolve_chart_options,
    resolve_units, resolve_palette, series_for,
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

TABLE_ROW_IN = 0.37
TABLE_SAFE_BOTTOM_IN = 6.95

# D-30 / REP-DESIGN-01 (+ coordinator layout-review fixes): part-summary
# table and per-image data-table caps.
MAX_SUMMARY_ROWS = 14
MAX_DATA_ROWS = 14
# Coordinator review: table + 3 bar charts share slide 2 only while they
# comfortably fit one compact table on top of 3 side-by-side charts; beyond
# this many parts the table stays on slide 2 (paginated as needed) and the
# 3 charts move to their own slide right after.
MAX_PARTS_COMBINED = 6

# Coordinator review (layout defects): every content shape (table/chart/
# picture -- not the intentional full-bleed header/footer bars) must stay
# inside this margin on all four sides. ``CONTENT_LEFT_IN``/``CONTENT_WIDTH_IN``
# is the standard usable horizontal band content tables/charts are laid out
# in so they can never run past the right edge (the bug that clipped the
# summary table's last column).
MARGIN_IN = 0.5
CONTENT_LEFT_IN = MARGIN_IN
CONTENT_RIGHT_IN = 13.333 - MARGIN_IN
CONTENT_WIDTH_IN = CONTENT_RIGHT_IN - CONTENT_LEFT_IN

# Coordinator review: the fixed 14-character image-name truncation was too
# aggressive for the widened Image column -- truncate only when the name
# actually doesn't fit the column at the rendered font size.
IMAGE_CELL_TEXT_MARGIN_IN = 0.2   # cell left+right internal padding allowance
IMAGE_CHAR_WIDTH_FACTOR = 0.5     # average glyph width vs font pt, regular weight

# Coordinator review: a footer that lists every distinct part/lot name used
# to wrap to 2 lines and get clipped by the footer bar -- cap the "named"
# form's length and fall back to counts ("3 parts · 9 lots") beyond it.
FOOTER_MAX_CHARS = 80


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
                # D-30 / REP-DESIGN-01 (+ coordinator layout-review fixes):
                # the old per-image "Executive Summary" table (+ Combined
                # row + INN-27 KPI tiles) is replaced by, always in this
                # order right after the cover: (1) the part-summary table
                # (one row per PART, averaged over its lots -- see
                # ``_part_summary_rows``) together with the three native bar
                # charts (G / diameter / area, one bar per part) on slide 2
                # -- table across the top, charts side by side underneath --
                # when there are few enough parts for that to stay legible
                # (``MAX_PARTS_COMBINED``); beyond that, the table alone
                # (paginated as needed) stays on slide 2 and the 3 charts
                # move to their own slide right after. (2) the restyled
                # per-image data tables, split so a part's rows never
                # continue onto the next part's slide.
                if images:
                    rows, au, du = _part_summary_rows(model, images)
                    part_label = _part_axis_label(model)
                    if len(rows) <= MAX_PARTS_COMBINED:
                        _summary_and_charts_slide(new_slide(), rows, au, du, part_label, navy, series)
                        _add_footer(prs.slides[-1], model, page[0], navy)
                    else:
                        summary_pages = _chunk(rows, MAX_SUMMARY_ROWS)
                        for i, chunk in enumerate(summary_pages):
                            suffix = ("" if len(summary_pages) <= 1
                                     else f" (cont'd {i + 1}/{len(summary_pages)})")
                            _part_summary_slide(new_slide(), chunk, au, du, navy, heading_suffix=suffix)
                            _add_footer(prs.slides[-1], model, page[0], navy)
                        _charts_only_slide(new_slide(), rows, au, du, part_label, navy, series)
                        _add_footer(prs.slides[-1], model, page[0], navy)

                    for part, idx, total, page_rows in _plan_image_table_slides(model, images):
                        _image_data_table_slide(new_slide(), model, part, idx, total, page_rows, navy)
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
             align=PP_ALIGN.LEFT, font="Calibri", wrap=True):
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = wrap
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


def _distinct_level_values(model: ReportModel, key: str) -> List[str]:
    seen: List[str] = []
    for img in model.images:
        v = img.level_value(key, "")
        if v and v not in seen:
            seen.append(v)
    return seen


_LEVEL_PLURAL_WORD = {"project": "jobs", "sample": "parts", "part": "parts", "lot": "lots"}


def _level_plural_word(h: Dict[str, str]) -> str:
    word = _LEVEL_PLURAL_WORD.get(h.get("key", ""))
    if word:
        return word
    label = (h.get("label") or h.get("key") or "item").strip().lower()
    return label if label.endswith("s") else label + "s"


def _footer_text(model: ReportModel, page_num: int) -> str:
    """'<Job #> 24-117 · <Part Number> 7718-A · <Lot> L-44A · page n' when a
    hierarchy is set and the names are short enough to fit one line;
    otherwise (coordinator layout-review fix -- a report spanning many
    parts/lots used to list every distinct name and wrap to 2 lines,
    clipped by the footer bar) each multi-value level collapses to a count,
    e.g. 'Job # 24-117 · 3 parts · 9 lots · page n', so the footer always
    stays a single line under ``FOOTER_MAX_CHARS``. Legacy no-hierarchy
    models keep the '<title> ... page' footer."""
    if not model.hierarchy:
        return model.title or "Grain Analysis Report"

    named_bits = [
        f"{h.get('label', '')} {h.get('value') or ', '.join(_distinct_level_values(model, h.get('key', '')))}".strip()
        for h in model.hierarchy
    ]
    named_line = " · ".join(b for b in named_bits if b)
    if len(named_line) <= FOOTER_MAX_CHARS:
        return f"{named_line} · page {page_num}" if named_line else f"page {page_num}"

    counted_bits = []
    for h in model.hierarchy:
        label = h.get("label", "")
        if h.get("value"):
            counted_bits.append(f"{label} {h['value']}".strip())
        else:
            counted_bits.append(f"{len(_distinct_level_values(model, h.get('key', '')))} {_level_plural_word(h)}")
    line = " · ".join(b for b in counted_bits if b)
    return f"{line} · page {page_num}" if line else f"page {page_num}"


def _add_footer(slide, model: ReportModel, page_num: int, navy: RGBColor = NAVY) -> None:
    _fill_rect(slide, 0, SLIDE_H - Inches(0.32), SLIDE_W, Inches(0.32), navy)
    # Coordinator layout-review fix: word_wrap=False so the footer can never
    # wrap to a second line and get clipped by the footer bar's fixed
    # height -- ``_footer_text`` already keeps it short enough to fit.
    _textbox(slide, Inches(0.3), SLIDE_H - Inches(0.32), Inches(10.5), Inches(0.32),
              _footer_text(model, page_num), size=10, color=WHITE, align=PP_ALIGN.LEFT, wrap=False)
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
            line = f"{h.get('label', '')}: {model.hierarchy_value(h)}"
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


def _style_body_cell(cell, size: int = 11) -> None:
    """FIX-12: an explicit, compact body font (vs. the ~18-24pt table-style
    default python-pptx falls back to when no size is set) keeps row
    height predictable so the FIX-12 pagination math holds."""
    for p in cell.text_frame.paragraphs:
        for run in p.runs:
            run.font.size = Pt(size)


# ---------------------------------------------------------------------------
# D-30 / REP-DESIGN-01: part-level summary slide + restyled per-image data
# tables (replaces the old per-image "Executive Summary" + Combined row +
# INN-27 KPI tiles). "Part" is the hierarchy "sample"/"part" level (falling
# back to ``ImageSummary.sample_id``); "Lot" is the hierarchy "lot" level
# (falling back to ``lot_number``). Parts/lots with no value are grouped
# under "—" so a report with no hierarchy at all still gets one part.
# ---------------------------------------------------------------------------

def _hier_level(model: ReportModel, keys: Tuple[str, ...]) -> Optional[Dict[str, str]]:
    for h in model.hierarchy:
        if h.get("key") in keys:
            return h
    return None


def _part_axis_label(model: ReportModel) -> str:
    lvl = _hier_level(model, ("sample", "part"))
    return (lvl.get("label") or "Part Number") if lvl else "Part Number"


def _image_part_lot(model: ReportModel, img: ImageSummary) -> Tuple[str, str]:
    part_lvl = _hier_level(model, ("sample", "part"))
    if part_lvl is not None:
        part = img.level_value(part_lvl.get("key", ""), part_lvl.get("value", ""))
    else:
        part = img.sample_id
    lot_lvl = _hier_level(model, ("lot",))
    if lot_lvl is not None:
        lot = img.level_value(lot_lvl.get("key", ""), lot_lvl.get("value", ""))
    else:
        lot = img.lot_number
    return (part or "—"), (lot or "—")


def _group_by_part(model: ReportModel,
                    images: List[ImageSummary]) -> Dict[str, Dict[str, List[ImageSummary]]]:
    groups: Dict[str, Dict[str, List[ImageSummary]]] = {}
    for img in images:
        part, lot = _image_part_lot(model, img)
        groups.setdefault(part, {}).setdefault(lot, []).append(img)
    return groups


def _global_unit(model: ReportModel, images: List[ImageSummary]) -> Tuple[str, float, str, float, bool]:
    """One (area_unit, area_mult, length_unit, length_mult, calibrated) for
    the whole part-summary table/charts, so every part's row/bar is
    comparable -- mirrors the Excel renderer's "Combined" row unit."""
    if images and all(i.has_calibration for i in images):
        au, am, du, dm = resolve_units(images[0].px_per_um, model.units)
        return au, am, du, dm, True
    return "px²", 1.0, "px", 1.0, False


def _image_mean_area_diam(img: ImageSummary, am: float, dm: float, calibrated: bool) -> Tuple[float, float]:
    if calibrated:
        return img.mean_area_um2 * am, img.mean_diameter_um * dm
    areas = [g["area_px"] for g in img.grains]
    diams = [g["diameter_px"] for g in img.grains]
    return (float(np.mean(areas)) if areas else 0.0, float(np.mean(diams)) if diams else 0.0)


def _ci95_halfwidth(sd: Optional[float], n: int) -> Optional[float]:
    """Two-sided 95% CI half-width (Student-t) for a sample of size ``n``
    with standard deviation ``sd``; ``None`` when undefined (n < 2)."""
    if sd is None or n < 2:
        return None
    from scipy import stats as _st
    t = float(_st.t.ppf(0.975, n - 1))
    return t * sd / math.sqrt(n)


def _part_summary_rows(model: ReportModel,
                        images: List[ImageSummary]) -> Tuple[List[Dict[str, Any]], str, str]:
    """One row per part -- ``[{"part", "n_lots", "n_images", "G_mean",
    "G_ci", "diam_mean", "diam_sd", "area_mean", "area_sd"}, ...]`` plus the
    (area_unit, length_unit) every row/bar is expressed in.

    Averaging rule: each lot's mean is computed first (mean of its images'
    per-image means), then the part value is the mean *of the lot means* --
    so every lot counts equally regardless of how many images it has. SD/CI
    are taken across lot means when the part has >= 2 lots; with exactly one
    lot, they fall back to the spread across that lot's images (a single lot
    mean has no spread of its own to measure).
    """
    au, am, du, dm, calibrated = _global_unit(model, images)
    groups = _group_by_part(model, images)
    rows: List[Dict[str, Any]] = []
    for part, lots in groups.items():
        n_lots = len(lots)
        n_images = sum(len(v) for v in lots.values())
        lot_G_means: List[Optional[float]] = []
        lot_diam_means: List[float] = []
        lot_area_means: List[float] = []
        single_lot_imgs: Optional[List[ImageSummary]] = None
        for lot, imgs in lots.items():
            gs = [i.astm_g for i in imgs if i.astm_g is not None]
            pairs = [_image_mean_area_diam(i, am, dm, calibrated) for i in imgs]
            lot_G_means.append(float(np.mean(gs)) if gs else None)
            lot_diam_means.append(float(np.mean([d for _a, d in pairs])) if pairs else 0.0)
            lot_area_means.append(float(np.mean([a for a, _d in pairs])) if pairs else 0.0)
            if n_lots == 1:
                single_lot_imgs = imgs

        valid_lot_G = [g for g in lot_G_means if g is not None]
        part_G = float(np.mean(valid_lot_G)) if valid_lot_G else None
        part_diam = float(np.mean(lot_diam_means)) if lot_diam_means else 0.0
        part_area = float(np.mean(lot_area_means)) if lot_area_means else 0.0

        if n_lots >= 2:
            g_sample, diam_sample, area_sample = valid_lot_G, lot_diam_means, lot_area_means
        else:
            imgs = single_lot_imgs or []
            g_sample = [i.astm_g for i in imgs if i.astm_g is not None]
            pairs = [_image_mean_area_diam(i, am, dm, calibrated) for i in imgs]
            area_sample = [a for a, _d in pairs]
            diam_sample = [d for _a, d in pairs]

        g_sd = float(np.std(g_sample, ddof=1)) if len(g_sample) >= 2 else None
        diam_sd = float(np.std(diam_sample, ddof=1)) if len(diam_sample) >= 2 else None
        area_sd = float(np.std(area_sample, ddof=1)) if len(area_sample) >= 2 else None
        g_ci = _ci95_halfwidth(g_sd, len(g_sample))

        rows.append({
            "part": part, "n_lots": n_lots, "n_images": n_images,
            "G_mean": part_G, "G_ci": g_ci, "G_sd": g_sd,
            "diam_mean": part_diam, "diam_sd": diam_sd,
            "area_mean": part_area, "area_sd": area_sd,
        })
    return rows, au, du


def _fmt_mean_sd(mean: float, sd: Optional[float], unit: str) -> str:
    if sd is None:
        return f"{mean:.2f} {unit}"
    return f"{mean:.2f} ± {sd:.2f} {unit}"


def _fmt_g_ci(mean: Optional[float], ci: Optional[float]) -> str:
    if mean is None:
        return "n/a"
    if ci is None:
        return f"{mean:.2f} ± n/a"
    return f"{mean:.2f} ± {ci:.2f}"


def _chunk(seq: List[Any], n: int) -> List[List[Any]]:
    if not seq:
        return [[]]
    return [seq[i:i + n] for i in range(0, len(seq), n)]


def _style_data_row(table, r: int, text_col_count: int, size: int = 11) -> None:
    """Zebra-striped body row (even rows tinted) + right-aligned numeric
    columns / left-aligned text columns -- the approved table style shared
    by the part-summary and per-image data tables."""
    fill = LIGHT_BAND if (r % 2 == 0) else WHITE
    for c in range(len(table.columns)):
        cell = table.cell(r, c)
        cell.fill.solid()
        cell.fill.fore_color.rgb = fill
        for p in cell.text_frame.paragraphs:
            p.alignment = PP_ALIGN.LEFT if c < text_col_count else PP_ALIGN.RIGHT
            for run in p.runs:
                run.font.size = Pt(size)
                run.font.color.rgb = TEXT_DARK


_SUMMARY_HEADERS = ["Part", "Lots", "Images", "ASTM G", "Mean Diameter", "Mean Area"]
# Sums to CONTENT_WIDTH_IN (12.333in) exactly -- the standalone (>MAX_PARTS_COMBINED
# parts) summary-table slide.
_SUMMARY_COL_WIDTHS_IN = [2.933, 1.1, 1.1, 2.3, 2.4, 2.5]
# Compact variant used on the combined slide 2 (table + 3 charts) -- a bit
# narrower on the identity columns so the table reads as a "header strip"
# above the charts.
_SUMMARY_COMBINED_COL_WIDTHS_IN = [3.033, 1.0, 1.0, 2.4, 2.4, 2.5]
SUMMARY_COMBINED_ROW_IN = 0.3


def _fill_summary_table(table, rows: List[Dict[str, Any]], au: str, du: str, navy: RGBColor,
                        header_size: int, body_size: int) -> None:
    for c, h in enumerate(_SUMMARY_HEADERS):
        cell = table.cell(0, c)
        cell.text = h
        _style_header_cell(cell, navy, size=header_size)
    for r, row in enumerate(rows, start=1):
        vals = [
            row["part"], str(row["n_lots"]), str(row["n_images"]),
            _fmt_g_ci(row["G_mean"], row["G_ci"]),
            _fmt_mean_sd(row["diam_mean"], row["diam_sd"], du),
            _fmt_mean_sd(row["area_mean"], row["area_sd"], au),
        ]
        for c, v in enumerate(vals):
            table.cell(r, c).text = v
        _style_data_row(table, r, text_col_count=1, size=body_size)


def _part_summary_slide(slide, rows: List[Dict[str, Any]], au: str, du: str, navy: RGBColor = NAVY,
                         heading_suffix: str = "") -> None:
    """Standalone part-summary table (only used beyond ``MAX_PARTS_COMBINED``
    parts, where the table alone -- paginated as needed -- fills slide 2 and
    the 3 bar charts move to their own slide right after). One row per
    part, averaged across its lots -- no Grains column, no KPI tiles, no
    "higher = finer" hint, no red rows."""
    _slide_heading(slide, "Grain Size Summary" + heading_suffix, navy)
    n_rows = len(rows) + 1
    table_shape = slide.shapes.add_table(n_rows, len(_SUMMARY_HEADERS), Inches(CONTENT_LEFT_IN),
                                          Inches(1.15), Inches(CONTENT_WIDTH_IN),
                                          Inches(TABLE_ROW_IN) * n_rows)
    table = table_shape.table
    for c, w in enumerate(_SUMMARY_COL_WIDTHS_IN):
        table.columns[c].width = Inches(w)
    _fill_summary_table(table, rows, au, du, navy, header_size=11, body_size=11)


def _three_chart_geometry() -> Tuple[float, List[float]]:
    """(chart_width_in, [x0, x1, x2]) for 3 equal-width charts, side by
    side, spanning the full ``CONTENT_WIDTH_IN`` band with a small gap
    between them -- the last chart's right edge lands exactly on
    ``CONTENT_RIGHT_IN`` (the 0.5in margin), never past it."""
    gap = 0.15
    cw = (CONTENT_WIDTH_IN - 2 * gap) / 3
    x0 = CONTENT_LEFT_IN
    x1 = x0 + cw + gap
    x2 = x1 + cw + gap
    return cw, [x0, x1, x2]


def _draw_part_bar_chart(slide, x_in: float, y_in: float, cx_in: float, cy_in: float,
                          bar_color_hex: str, chart_title: str, value_axis_title: str,
                          category_axis_title: str, categories: List[str], values: List[float]):
    """One native, editable bar chart -- one bar per PART -- at an explicit
    position/size (so 3 can sit side by side on one slide)."""
    chart_data = CategoryChartData()
    chart_data.categories = categories or ["—"]
    chart_data.add_series(chart_title, values or [0.0])
    gframe = slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(x_in), Inches(y_in),
                                    Inches(cx_in), Inches(cy_in), chart_data)
    chart = gframe.chart
    chart.has_title = True
    chart.chart_title.text_frame.text = chart_title
    chart.has_legend = False  # single series -- a legend would just be clutter
    cat_axis = chart.category_axis
    cat_axis.axis_title.text_frame.text = category_axis_title
    val_axis = chart.value_axis
    val_axis.axis_title.text_frame.text = value_axis_title
    val_axis.has_major_gridlines = True
    try:
        chart.plots[0].series[0].format.fill.solid()
        chart.plots[0].series[0].format.fill.fore_color.rgb = _hexrgb(bar_color_hex)
    except Exception:
        pass
    return chart


def _draw_three_part_charts(slide, rows: List[Dict[str, Any]], au: str, du: str, part_label: str,
                             series: Dict[str, str], top_in: float, height_in: float) -> None:
    categories = [r["part"] for r in rows]
    cw, xs = _three_chart_geometry()
    g_vals = [r["G_mean"] if r["G_mean"] is not None else 0.0 for r in rows]
    diam_vals = [r["diam_mean"] for r in rows]
    area_vals = [r["area_mean"] for r in rows]
    _draw_part_bar_chart(slide, xs[0], top_in, cw, height_in, series["count_bar"],
                        "ASTM Grain Size Number by Part", "ASTM G Number", part_label,
                        categories, g_vals)
    _draw_part_bar_chart(slide, xs[1], top_in, cw, height_in, series["diameter_bar"],
                        "Mean Diameter by Part", f"Mean Diameter ({du})", part_label,
                        categories, diam_vals)
    _draw_part_bar_chart(slide, xs[2], top_in, cw, height_in, series["area_bar"],
                        "Mean Area by Part", f"Mean Area ({au})", part_label,
                        categories, area_vals)


def _summary_and_charts_slide(slide, rows: List[Dict[str, Any]], au: str, du: str, part_label: str,
                               navy: RGBColor, series: Dict[str, str]) -> None:
    """SLIDE 2 (index 1) for <= ``MAX_PARTS_COMBINED`` parts (the Option A
    approved layout): the part-summary table across the top (compact rows)
    and the three bar charts side by side underneath, all on one slide."""
    _slide_heading(slide, "Grain Size Summary", navy)
    table_top_in = 1.05
    n_rows = len(rows) + 1
    table_shape = slide.shapes.add_table(n_rows, len(_SUMMARY_HEADERS), Inches(CONTENT_LEFT_IN),
                                          Inches(table_top_in), Inches(CONTENT_WIDTH_IN),
                                          Inches(SUMMARY_COMBINED_ROW_IN) * n_rows)
    table = table_shape.table
    for c, w in enumerate(_SUMMARY_COMBINED_COL_WIDTHS_IN):
        table.columns[c].width = Inches(w)
    _fill_summary_table(table, rows, au, du, navy, header_size=11, body_size=10)

    table_bottom_in = table_top_in + SUMMARY_COMBINED_ROW_IN * n_rows
    charts_top_in = table_bottom_in + 0.15
    charts_height_in = min(4.5, (SLIDE_H.inches - MARGIN_IN) - charts_top_in)
    _draw_three_part_charts(slide, rows, au, du, part_label, series, charts_top_in, charts_height_in)


def _charts_only_slide(slide, rows: List[Dict[str, Any]], au: str, du: str, part_label: str,
                        navy: RGBColor, series: Dict[str, str]) -> None:
    """Slide right after the (paginated) standalone summary table when there
    are more than ``MAX_PARTS_COMBINED`` parts -- the same three bar charts,
    side by side, with the full slide height to themselves."""
    _slide_heading(slide, "Grain Size Summary — Charts", navy)
    top_in = 1.15
    height_in = min(5.6, (SLIDE_H.inches - MARGIN_IN) - top_in)
    _draw_three_part_charts(slide, rows, au, du, part_label, series, top_in, height_in)


# ---------------------------------------------------------------------------
# Per-image data-table slides -- restyled in the same table format as the
# part summary, split so a part's rows never continue onto the next part's
# slide, max ``MAX_DATA_ROWS`` rows per slide.
# ---------------------------------------------------------------------------

DATA_TABLE_HEADERS = ["Part", "Lot", "Image", "Grains", "Mean Diam", "Std Diam",
                      "Mean Area", "Median Area", "Coverage %", "Circularity", "ASTM G"]
# Part/Lot are short identifiers (narrowed); Image gets the reclaimed width
# so normal-length display names aren't truncated. Sums to CONTENT_WIDTH_IN
# (12.333in) exactly.
_DATA_COL_WIDTHS_IN = [1.0, 0.85, 2.483, 0.8, 1.1, 1.1, 1.1, 1.1, 1.0, 1.0, 0.8]
_DATA_IMAGE_COL_IN = _DATA_COL_WIDTHS_IN[2]
_DATA_BODY_FONT_PT = 11


def _max_chars_for_width(col_width_in: float, font_pt: int) -> int:
    """How many characters of body text fit ``col_width_in`` wide at
    ``font_pt`` -- an average-glyph-width estimate (same trick as
    ``_wrap_line_count``), used to truncate only when a name really
    doesn't fit its column instead of at a fixed character count."""
    avail_in = max(0.1, col_width_in - IMAGE_CELL_TEXT_MARGIN_IN)
    char_w_in = max(0.01, font_pt * IMAGE_CHAR_WIDTH_FACTOR / 72.0)
    return max(3, int(avail_in / char_w_in))


def _truncate_to_fit(name: str, col_width_in: float, font_pt: int = _DATA_BODY_FONT_PT) -> str:
    maxlen = _max_chars_for_width(col_width_in, font_pt)
    if len(name) <= maxlen:
        return name
    return name[: max(1, maxlen - 1)] + "…"


def _plan_image_table_slides(model: ReportModel, images: List[ImageSummary]
                              ) -> List[Tuple[str, int, int, List[Tuple[str, str, ImageSummary]]]]:
    """``[(part, page_idx (1-based), total_pages, [(part, lot, image), ...]), ...]``
    -- one entry per data-table slide, grouped by part (a part never shares
    a slide with the next part's rows) and paginated at ``MAX_DATA_ROWS``."""
    groups = _group_by_part(model, images)
    plan: List[Tuple[str, int, int, List[Tuple[str, str, ImageSummary]]]] = []
    for part, lots in groups.items():
        rows = [(part, lot, img) for lot, imgs in lots.items() for img in imgs]
        pages = _chunk(rows, MAX_DATA_ROWS)
        total = len(pages)
        for idx, page_rows in enumerate(pages, start=1):
            plan.append((part, idx, total, page_rows))
    return plan


def _image_data_table_slide(slide, model: ReportModel, part: str, idx: int, total: int,
                             rows: List[Tuple[str, str, ImageSummary]], navy: RGBColor = NAVY) -> None:
    # Coordinator layout-review fix: even the *first* slide of a part that
    # continues onto another slide carries the "(continued i/N)" suffix
    # (not just the later ones), so it is never ambiguous which slide the
    # reader is on.
    heading = part if total <= 1 else f"{part} (continued {idx}/{total})"
    _slide_heading(slide, heading, navy)
    n_rows = len(rows) + 1
    table_shape = slide.shapes.add_table(n_rows, len(DATA_TABLE_HEADERS), Inches(CONTENT_LEFT_IN),
                                          Inches(1.05), Inches(CONTENT_WIDTH_IN),
                                          Inches(TABLE_ROW_IN) * n_rows)
    table = table_shape.table
    for c, w in enumerate(_DATA_COL_WIDTHS_IN):
        table.columns[c].width = Inches(w)
    for c, h in enumerate(DATA_TABLE_HEADERS):
        cell = table.cell(0, c)
        cell.text = h
        _style_header_cell(cell, navy, size=11)

    truncated: Dict[str, str] = {}
    for r, (part_v, lot_v, img) in enumerate(rows, start=1):
        au, du, mean_a, med_a, std_a, mean_d, std_d = _row_size_stats(model, img)
        disp = img.display()
        shown = _truncate_to_fit(disp, _DATA_IMAGE_COL_IN, _DATA_BODY_FONT_PT)
        if shown != disp:
            truncated[shown] = disp
        vals = [
            part_v, lot_v, shown, str(img.grain_count),
            f"{mean_d:.2f} {du}", f"{std_d:.2f} {du}",
            f"{mean_a:.2f} {au}", f"{med_a:.2f} {au}",
            f"{img.grain_coverage_pct:.1f}", f"{img.mean_circularity:.3f}",
            f"{img.astm_g:.2f}" if img.astm_g is not None else "—",
        ]
        for c, v in enumerate(vals):
            table.cell(r, c).text = v
        _style_data_row(table, r, text_col_count=3, size=_DATA_BODY_FONT_PT)

    if truncated:
        notes = slide.notes_slide
        notes.notes_text_frame.text = "Full image names: " + "; ".join(
            f"{k} = {v}" for k, v in truncated.items())


def _style_header_cell(cell, navy: RGBColor = NAVY, size: int = 12) -> None:
    cell.fill.solid()
    cell.fill.fore_color.rgb = navy
    for p in cell.text_frame.paragraphs:
        p.alignment = PP_ALIGN.CENTER
        for run in p.runs:
            run.font.color.rgb = WHITE
            run.font.bold = True
            run.font.size = Pt(size)


def _slide_heading(slide, text: str, navy: RGBColor = NAVY) -> None:
    _fill_rect(slide, 0, 0, SLIDE_W, Inches(0.9), navy)
    _textbox(slide, Inches(0.5), Inches(0.15), Inches(12), Inches(0.6), text, size=26, bold=True, color=WHITE)


def _distribution_slide(slide, model: ReportModel, images: List[ImageSummary], kind: str,
                         series: Dict[str, str] = SERIES, navy: RGBColor = NAVY) -> None:
    """UX-14: ``model.chart_options[kind]`` (min/max/title/colour) and the
    global ``normal_fit`` toggle — see ``reports.charts.
    resolve_chart_options`` — mirror the Excel renderer's ``_write_hist_block``
    so both exports agree. UX-14 fix: ``min``/``max`` are stored in
    ``opt["bound_unit"]`` and converted to ``unit`` (this slide's render
    unit) via ``reports.charts.convert_bound`` before filtering, so a bound
    typed in µm still selects the same grains after a unit switch."""
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

    lo = convert_bound(opt.get("min"), opt.get("bound_unit"), unit, kind)
    hi = convert_bound(opt.get("max"), opt.get("bound_unit"), unit, kind)
    values = filter_range(values, lo, hi)
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
        lines += [f"{h.get('label', '')}: {model.hierarchy_value(h)}" for h in model.hierarchy]
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
