"""Excel renderer (xlsxwriter) for ``ReportModel``.

Sheet order follows the designer's ``Section.order`` (drag order in the
outline), with two structural rules always enforced (matching the designer
and the PowerPoint renderer):
  * Overview       (navy)   — always first; folds the ``cover`` banner and
                               the ``overview_table`` grid into one sheet,
                               each independently toggle-able.
  * Raw - <name>    (grey)   — always last (the lab manager's requirement).
Between those, any mix/order of:
  * Summary Charts  (green)  — combined histograms, per-image mean-diameter
                                bar w/ error bars, grain-count-per-image bar.
  * Img n - <name>  (teal)   — original + overlay, stats, per-image histograms,
                                in ``ImageSummary.order``.
  * Methods         (amber)  — detection mode/params, calibration, version.
  * Notes - <title> (purple, #6A1B9A) — one per enabled ``custom_text``
                                section, word-wrapped body.
Every section's user-edited ``title`` is used where the model allows one
(Overview/Summary Charts/Methods/Notes sheet names); a disabled section is
skipped entirely. Chart colours, header bands and title bars follow the
designer's palette (``ReportModel.theme`` — see ``reports.charts.PALETTES``);
tab colours stay fixed/kind-coded regardless of palette.

Every embedded/resized image goes through a ``tempfile.TemporaryDirectory``
that is cleaned up before this function returns (D-14 — no leaked temp
files, unlike the legacy ``utils/excel_export.py::_simg``).
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from typing import Dict, List, Optional, Tuple

import numpy as np
import xlsxwriter
from xlsxwriter.utility import xl_rowcol_to_cell

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

from reports.charts import (
    SERIES, TAB_COLORS, build_bins, filter_range, normal_fit, resolve_chart_options,
    resolve_units, series_for,
)
from reports.model import ReportModel, ImageSummary, Section

try:
    from version import __version__ as APP_VERSION
except Exception:  # pragma: no cover
    APP_VERSION = "unknown"

INVALID_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")


def _autosize_width(header: str, values: List[object], min_w: int = 8, max_w: int = 40,
                     pad: int = 2) -> float:
    """Column width sized to the longest of header/content (character count
    plus padding), clamped to ``[min_w, max_w]``.

    Used for identifier-ish text columns (Image/Sample/Lot, hierarchy level
    columns) whose content length varies a lot between labs — a fixed width
    either truncates long lot numbers (FIX-14) or wastes space on short
    ones. The clamp keeps one pathological outlier from blowing out the
    whole sheet.
    """
    longest = len(str(header or ""))
    for v in values:
        if v is None:
            continue
        longest = max(longest, len(str(v)))
    return max(min_w, min(max_w, longest + pad))


def _safe_sheet_name(name: str, used: Dict[str, int]) -> str:
    clean = INVALID_SHEET_CHARS.sub("", name).strip() or "Sheet"
    clean = clean[:31]
    base = clean
    n = used.get(base, 0)
    if n:
        suffix = f" ({n})"
        clean = base[: 31 - len(suffix)] + suffix
    used[base] = n + 1
    return clean


def _resized_png(tmpdir: str, src_path: Optional[str], max_w: int = 800, _seq: List[int] = [0],
                  blend_src: Optional[str] = None, opacity: float = 1.0
                  ) -> Optional[Tuple[str, int, int]]:
    """Return (path, width, height) of a size-capped PNG copy, or None.

    Output filenames combine a content-derived hash with a monotonically
    increasing sequence number so repeated/near-identical paths never
    collide within one render (plain ``hash(path) % N`` can).

    UX-16: when ``blend_src`` (the plain original image) is given and
    ``opacity < 1``, the returned PNG is ``src_path`` (the overlay) alpha-
    blended down towards ``blend_src`` — ``ReportModel.overlay_opacity``.
    Pixels the detector did not touch are identical in both images, so this
    only fades the grain colouring/outlines, not the underlying micrograph.
    ``opacity == 1`` (the default — current behaviour) skips the blend
    entirely.
    """
    if not src_path or not os.path.exists(src_path) or cv2 is None:
        return None
    img = cv2.imread(src_path, cv2.IMREAD_COLOR)
    if img is None:
        return None
    if blend_src and opacity < 0.999 and os.path.exists(blend_src):
        base = cv2.imread(blend_src, cv2.IMREAD_COLOR)
        if base is not None:
            if base.shape[:2] != img.shape[:2]:
                base = cv2.resize(base, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_AREA)
            a = max(0.0, min(1.0, opacity))
            img = cv2.addWeighted(img, a, base, 1.0 - a, 0)
    h, w = img.shape[:2]
    if w > max_w:
        scale = max_w / w
        img = cv2.resize(img, (max_w, int(h * scale)), interpolation=cv2.INTER_AREA)
        h, w = img.shape[:2]
    _seq[0] += 1
    digest = hashlib.sha1(src_path.encode("utf-8", "ignore")).hexdigest()[:12]
    out_path = os.path.join(tmpdir, f"_r{digest}_{_seq[0]}.png")
    cv2.imwrite(out_path, img, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    return out_path, w, h


def _file_link_target(output_path: str, image_path: str) -> Optional[str]:
    """Local-file hyperlink target for the Overview "File" column.

    Relative (so the link survives moving the whole job folder) when the
    workbook and the image share a meaningful ancestor folder deeper than
    just the drive root; absolute otherwise. Never an http(s) link (D-14).
    Returns ``None`` when ``image_path`` is empty/unresolved.
    """
    if not image_path:
        return None
    out_dir = os.path.abspath(os.path.dirname(output_path) or ".")
    img_abs = os.path.abspath(image_path)
    out_drive, _ = os.path.splitdrive(out_dir)
    img_drive, _ = os.path.splitdrive(img_abs)
    if out_drive.lower() == img_drive.lower():
        try:
            common = os.path.commonpath([out_dir, img_abs])
        except ValueError:
            common = ""
        _, common_rest = os.path.splitdrive(common)
        if common_rest not in ("", os.sep):
            return os.path.relpath(img_abs, out_dir)
    return img_abs


def _write_file_url(ws, row: int, col: int, output_path: str, image_path: str, fmt, display: str) -> None:
    target = _file_link_target(output_path, image_path)
    if not target:
        ws.write(row, col, "—", fmt)
        return
    ws.write_url(row, col, f"external:{target}", fmt, string=display)


def _build_plan(model: ReportModel, images: List[ImageSummary]) -> List[Tuple[str, Optional[Section]]]:
    """Sheets between Overview and Raw data, in ``Section.order``.

    ``cover``/``overview_table`` fold into the Overview sheet (handled by
    ``_write_overview``) and ``raw_data`` is always pinned last (the lab
    manager's requirement) — both are excluded here. Per-image sheets are
    emitted as one ``("images", None)`` block at the position of the first
    ``image`` section encountered, since ``ImageSummary.order`` (kept in
    sync with the image sections by ``ui.pages.report_builder.apply_order``)
    governs the order *within* that block.
    """
    plan: List[Tuple[str, Optional[Section]]] = []
    images_emitted = False
    for s in sorted(model.sections, key=lambda s: s.order):
        if s.type in ("cover", "overview_table", "raw_data"):
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


def render_excel(model: ReportModel, output_path: str) -> str:
    with tempfile.TemporaryDirectory(prefix="grain_report_xlsx_") as tmpdir:
        wb = xlsxwriter.Workbook(output_path)
        series = series_for(model.theme, model.custom_palette)
        fmts = _build_formats(wb, series)
        used_names: Dict[str, int] = {}

        images = model.ordered_images(included_only=True)
        want_raw = model.is_enabled("raw_data", default=True)
        plan = _build_plan(model, images)

        # Precompute every sheet name up front (in final sheet order) so
        # Overview can hyperlink to the correct, already-unique names.
        overview_name = _safe_sheet_name(
            model.section_title("overview_table", default="Overview") or "Overview", used_names)
        sheet_names: Dict[int, str] = {}
        image_sheet_names: Dict[str, str] = {}
        for kind, sec in plan:
            if kind == "charts":
                sheet_names[id(sec)] = _safe_sheet_name(sec.title or "Summary Charts", used_names)
            elif kind == "methods":
                sheet_names[id(sec)] = _safe_sheet_name(sec.title or "Methods", used_names)
            elif kind == "custom_text":
                sheet_names[id(sec)] = _safe_sheet_name(f"Notes - {sec.title or 'Notes'}", used_names)
            elif kind == "images":
                for img in images:
                    display = img.display_name or os.path.splitext(os.path.basename(img.image_path))[0]
                    image_sheet_names[img.id] = _safe_sheet_name(f"Img {img.order} - {display}", used_names)
        raw_sheet_names: Dict[str, str] = {}
        if want_raw:
            for img in images:
                display = img.display_name or os.path.splitext(os.path.basename(img.image_path))[0]
                raw_sheet_names[img.id] = _safe_sheet_name(f"Raw - {display}", used_names)

        # 1. Overview (always first; cover banner + overview table, each
        #    independently toggle-able)
        _write_overview(wb, model, images, fmts, overview_name, image_sheet_names, output_path)

        # 2. Everything in designer order: Summary Charts / per-image sheets
        #    / Methods / Notes — any mix, any order the user picked.
        for kind, sec in plan:
            if kind == "charts":
                _write_summary_charts(wb, model, images, fmts, series, sheet_names[id(sec)])
            elif kind == "methods":
                _write_methods(wb, model, fmts, sheet_names[id(sec)])
            elif kind == "custom_text":
                _write_custom_text_sheet(wb, sec, fmts, sheet_names[id(sec)])
            elif kind == "images":
                for img in images:
                    _write_image_sheet(wb, model, img, image_sheet_names[img.id], fmts, series, tmpdir)

        # 3. Raw data (last, always — lab manager requirement)
        if want_raw:
            for img in images:
                _write_raw_sheet(wb, model, img, fmts, raw_sheet_names[img.id])

        wb.close()
    return output_path


# ---------------------------------------------------------------------------
# Formats
# ---------------------------------------------------------------------------

def _build_formats(wb: "xlsxwriter.Workbook", series: Dict[str, str] = SERIES
                    ) -> Dict[str, "xlsxwriter.format.Format"]:
    """``series`` is ``reports.charts.series_for(model.theme)`` — the palette
    the user picked in Document ▸ Palette. Tab colours (``TAB_COLORS``) are
    NOT part of this — they always stay kind-coded."""
    f = {}
    f["title"] = wb.add_format({"bold": True, "font_size": 18, "font_color": SERIES["white"],
                                 "bg_color": series["navy"], "align": "center", "valign": "vcenter"})
    f["subtitle"] = wb.add_format({"font_size": 10, "font_color": SERIES["white"],
                                    "bg_color": TAB_COLORS["overview"], "align": "left", "valign": "vcenter"})
    f["section"] = wb.add_format({"bold": True, "font_size": 12, "font_color": SERIES["white"],
                                   "bg_color": series["navy"], "align": "left", "valign": "vcenter", "indent": 1})
    f["header"] = wb.add_format({"bold": True, "font_size": 10, "font_color": SERIES["white"],
                                  "bg_color": series["header_bg"], "align": "center", "valign": "vcenter",
                                  "border": 1, "text_wrap": True})
    f["band0"] = wb.add_format({"bg_color": SERIES["band_alt"], "border": 1, "align": "center"})
    f["band1"] = wb.add_format({"bg_color": SERIES["band"], "border": 1, "align": "center"})
    f["band0_num2"] = wb.add_format({"bg_color": SERIES["band_alt"], "border": 1, "align": "center", "num_format": "0.00"})
    f["band1_num2"] = wb.add_format({"bg_color": SERIES["band"], "border": 1, "align": "center", "num_format": "0.00"})
    f["band0_pct"] = wb.add_format({"bg_color": SERIES["band_alt"], "border": 1, "align": "center", "num_format": "0.0\"%\""})
    f["band1_pct"] = wb.add_format({"bg_color": SERIES["band"], "border": 1, "align": "center", "num_format": "0.0\"%\""})
    f["band0_num4"] = wb.add_format({"bg_color": SERIES["band_alt"], "border": 1, "align": "center", "num_format": "0.0000"})
    f["band1_num4"] = wb.add_format({"bg_color": SERIES["band"], "border": 1, "align": "center", "num_format": "0.0000"})
    f["total_label"] = wb.add_format({"bold": True, "bg_color": "#D9DEE8", "border": 1, "align": "left", "indent": 1})
    f["total"] = wb.add_format({"bold": True, "bg_color": "#D9DEE8", "border": 1, "align": "center", "num_format": "0.00"})
    f["label"] = wb.add_format({"bold": True, "align": "left", "indent": 1, "border": 1})
    f["value"] = wb.add_format({"align": "center", "border": 1})
    f["value_num2"] = wb.add_format({"align": "center", "border": 1, "num_format": "0.00"})
    f["caption"] = wb.add_format({"italic": True, "text_wrap": True, "valign": "top"})
    f["hyperlink"] = wb.add_format({"font_color": "#1155CC", "underline": 1, "align": "left", "indent": 1,
                                     "border": 1})
    f["hyperlink_band"] = wb.add_format({"font_color": "#1155CC", "underline": 1, "align": "left", "indent": 1,
                                          "border": 1, "bg_color": SERIES["band"]})
    # INN-02: conformity verdict badge -- only ever used when
    # ``ReportModel.verdict`` is set (a spec is attached); a report with no
    # spec never touches these formats.
    f["verdict_pass"] = wb.add_format({"bold": True, "font_size": 14, "font_color": "#FFFFFF",
                                        "bg_color": "#2E7D32", "align": "center", "valign": "vcenter"})
    f["verdict_fail"] = wb.add_format({"bold": True, "font_size": 14, "font_color": "#FFFFFF",
                                        "bg_color": "#C62828", "align": "center", "valign": "vcenter"})
    f["verdict_inconclusive"] = wb.add_format({"bold": True, "font_size": 14, "font_color": "#FFFFFF",
                                                "bg_color": "#E9A400", "align": "center", "valign": "vcenter"})
    f["verdict_rule"] = wb.add_format({"font_size": 10, "align": "left", "indent": 1, "border": 1})
    return f


def _band(fmts, i, key="band"):
    return fmts[f"band{i % 2}"] if key == "band" else fmts[f"band{i % 2}_{key}"]


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

_OVERVIEW_COLS = ["#", "Image", "Sample", "Lot", "Grains", "Mean Area", "Median Area", "Std Area",
                   "Mean Diameter", "Std Diameter", "Units", "Coverage %", "Invalid %", "Mean Circularity",
                   "Mean Aspect Ratio", "ASTM G"]


def _combined_unit(model: ReportModel, images: List[ImageSummary]) -> Tuple[str, str]:
    if images and all(i.has_calibration for i in images):
        au, _, du, _ = resolve_units(images[0].px_per_um, model.units)
        return au, du
    return "px²", "px"


def _row_size_stats(model: ReportModel, img: ImageSummary) -> Tuple[str, str, float, float, float, float, float]:
    """Per-row (area_unit, diam_unit, mean_area, median_area, std_area, mean_diam, std_diam).

    ``core.metrics.compute_statistics`` zeroes the ``*_um2``/``*_um`` summary
    fields when an image has no calibration, so those rows must be derived
    straight from the per-grain ``area_px``/``diameter_px`` columns instead
    of the (zeroed) um summary fields — otherwise uncalibrated images show
    all-zero size stats on the Overview table.
    """
    if img.has_calibration:
        au, am, du, dm = resolve_units(img.px_per_um, model.units)
        return (au, du, img.mean_area_um2 * am, img.median_area_um2 * am, img.std_area_um2 * am,
                img.mean_diameter_um * dm, img.std_diameter_um * dm)
    if img.grains:
        areas = np.array([g["area_px"] for g in img.grains], dtype=float)
        diams = np.array([g["diameter_px"] for g in img.grains], dtype=float)
        return ("px²", "px", float(np.mean(areas)), float(np.median(areas)), float(np.std(areas)),
                float(np.mean(diams)), float(np.std(diams)))
    return "px²", "px", 0.0, 0.0, 0.0, 0.0, 0.0


def _write_overview(wb, model: ReportModel, images: List[ImageSummary], fmts, name: str,
                     image_sheet_names: Dict[str, str], output_path: str = "") -> None:
    ws = wb.add_worksheet(name)
    ws.set_tab_color(TAB_COLORS["overview"])
    ws.hide_gridlines(2)

    hier = bool(model.hierarchy)
    level_cols = model.level_columns() if hier else []
    # Lead columns: legacy layout is unchanged ("#", "Image", "Sample",
    # "Lot") so old report.json files render identically; a hierarchy adds
    # a "File" hyperlink column and swaps Sample/Lot for the user's levels.
    lead = ["#", "Image", "Sample", "Lot"] if not hier else \
        ["#", "Image", "File"] + [label for _, label in level_cols]
    tail = ["Grains", "Mean Area*", "Median Area*", "Std Area*", "Mean Diameter*", "Std Diameter*",
            "Units", "Coverage %", "Invalid %", "Mean Circularity", "Mean Aspect Ratio", "ASTM G"]
    ncols = len(lead) + len(tail)
    file_col = 2 if hier else None

    header_row = 0
    if model.is_enabled("cover", default=True):
        ws.merge_range(0, 0, 0, ncols - 1, model.title or "Grain Analysis Report", fmts["title"])
        ws.set_row(0, 30)
        sub = (f"Operator: {model.operator or '—'}   |   Organization: {model.organization or '—'}   |   "
               f"Date: {model.date}   |   {len(images)} image(s)   |   Grain Analyzer v{APP_VERSION}")
        ws.merge_range(1, 0, 1, ncols - 1, sub, fmts["subtitle"])
        if hier:
            ws.merge_range(2, 0, 2, ncols - 1, model.hierarchy_header(), fmts["subtitle"])
        header_row = 3

    # INN-02: only ever drawn when a spec is attached (``model.verdict`` is
    # not ``None``, see ``reports.model.normalize_verdict``) -- a report
    # with no spec has no verdict cell/rule table at all.
    if model.verdict and model.verdict.get("overall") not in (None, "no_spec"):
        header_row = _write_verdict_block(ws, model, fmts, header_row, ncols) + 1

    if model.sample_statistics and model.is_enabled("sample_statistics", default=True):
        header_row = _write_lot_block(ws, model, fmts, header_row, ncols) + 1

    if not model.is_enabled("overview_table", default=True) or not images:
        ws.set_column(0, ncols - 1, 16)
        return

    au, du = _combined_unit(model, images)
    tail_header = [
        "Grains", f"Mean Area ({au})*", f"Median Area ({au})*", f"Std Area ({au})*",
        f"Mean Diameter ({du})*", f"Std Diameter ({du})*", "Units", "Coverage %", "Invalid %",
        "Mean Circularity", "Mean Aspect Ratio", "ASTM G",
    ]
    header = lead + tail_header
    tail_start = len(lead)
    for c, h in enumerate(header):
        ws.write(header_row, c, h, fmts["header"])
    ws.set_row(header_row, 30)

    if images and images[0].has_calibration:
        _, am, _, dm = resolve_units(images[0].px_per_um, model.units)
    else:
        am, dm = 1.0, 1.0

    r = header_row + 1
    for i, img in enumerate(images):
        au_i, du_i, mean_a, med_a, std_a, mean_d, std_d = _row_size_stats(model, img)
        display = img.display()
        if hier:
            row_lead = [img.order, display, ""] + model.row_levels(img)
        else:
            row_lead = [img.order, display, img.sample_id, img.lot_number]
        row_tail = [
            img.grain_count, round(mean_a, 3), round(med_a, 3), round(std_a, 3), round(mean_d, 3),
            round(std_d, 3), f"{au_i} / {du_i}", round(img.grain_coverage_pct, 2),
            round(img.invalid_area_pct, 2), round(img.mean_circularity, 4), round(img.mean_aspect_ratio, 4),
            (img.astm_g if img.astm_g is not None else "—"),
        ]
        row = row_lead + row_tail
        for c, val in enumerate(row):
            if c == 1:
                target_sheet = image_sheet_names.get(img.id)
                fmt = fmts["hyperlink_band"] if i % 2 else fmts["hyperlink"]
                if target_sheet:
                    ws.write_url(r, c, f"internal:'{target_sheet}'!A1", fmt, string=str(val))
                else:
                    ws.write(r, c, val, fmt)
            elif file_col is not None and c == file_col:
                fmt = fmts["hyperlink_band"] if i % 2 else fmts["hyperlink"]
                _write_file_url(ws, r, c, output_path, img.image_path, fmt, "Open")
            elif c == tail_start:
                ws.write_number(r, c, val, _band(fmts, i))
            elif tail_start + 1 <= c <= tail_start + 5:
                ws.write_number(r, c, val, _band(fmts, i, "num2"))
            elif c in (tail_start + 7, tail_start + 8):
                ws.write_number(r, c, val, _band(fmts, i, "pct"))
            elif c in (tail_start + 9, tail_start + 10):
                ws.write_number(r, c, val, _band(fmts, i, "num4"))
            elif c == tail_start + 11 and isinstance(val, (int, float)):
                ws.write_number(r, c, val, _band(fmts, i, "num2"))
            else:
                ws.write(r, c, val, _band(fmts, i))
        r += 1

    # Combined row across all grains of all included images.
    all_grains = [g for img in images for g in img.grains]
    if all_grains:
        calibrated_all = all(i.has_calibration for i in images)
        if calibrated_all:
            areas = np.array([g["area_um2"] for g in all_grains]) * am
            diams = np.array([g["diameter_um"] for g in all_grains]) * dm
        else:
            areas = np.array([g["area_px"] for g in all_grains])
            diams = np.array([g["diameter_px"] for g in all_grains])
        total_grains = len(all_grains)
        mean_circ = float(np.mean([g["circularity"] for g in all_grains]))
        mean_ar = float(np.mean([g["aspect_ratio"] for g in all_grains]))
        mean_cov = float(np.mean([i.grain_coverage_pct for i in images]))
        mean_inv = float(np.mean([i.invalid_area_pct for i in images]))
        for c in range(tail_start):
            ws.write(r, c, "Combined (all images)" if c == 1 else "", fmts["total_label"])
        t = tail_start
        ws.write_number(r, t, total_grains, fmts["total"])
        ws.write_number(r, t + 1, round(float(np.mean(areas)), 3), fmts["total"])
        ws.write_number(r, t + 2, round(float(np.median(areas)), 3), fmts["total"])
        ws.write_number(r, t + 3, round(float(np.std(areas)), 3), fmts["total"])
        ws.write_number(r, t + 4, round(float(np.mean(diams)), 3), fmts["total"])
        ws.write_number(r, t + 5, round(float(np.std(diams)), 3), fmts["total"])
        ws.write(r, t + 6, f"{au} / {du}" if calibrated_all else "px² / px", fmts["total_label"])
        ws.write_number(r, t + 7, round(mean_cov, 2), fmts["total"])
        ws.write_number(r, t + 8, round(mean_inv, 2), fmts["total"])
        ws.write_number(r, t + 9, round(mean_circ, 4), fmts["total"])
        ws.write_number(r, t + 10, round(mean_ar, 4), fmts["total"])
        ws.write(r, t + 11, "—", fmts["total"])
        r += 1

    ws.freeze_panes(header_row + 1, 1)
    # FIX-14: size identifier columns (Image / Sample / Lot, or the
    # hierarchy's level columns) from their actual content instead of a
    # fixed width, so a long lot number/sample id/display name is no
    # longer truncated. Numeric/stat columns keep their fixed widths —
    # those are never free text.
    image_w = _autosize_width("Image", [img.display() for img in images], min_w=18, max_w=45)
    if hier:
        level_widths = [
            _autosize_width(label, [model.row_levels(img)[idx] for img in images], min_w=10, max_w=30)
            for idx, (_, label) in enumerate(level_cols)
        ]
        widths = [5, image_w, 16] + level_widths + [9, 13, 13, 13, 15, 14, 13, 11, 10, 15, 15, 9]
    else:
        sample_w = _autosize_width("Sample", [img.sample_id for img in images], min_w=10, max_w=30)
        lot_w = _autosize_width("Lot", [img.lot_number for img in images], min_w=10, max_w=30)
        widths = [5, image_w, sample_w, lot_w, 9, 13, 13, 13, 15, 14, 13, 11, 10, 15, 15, 9]
    for c, w in enumerate(widths):
        ws.set_column(c, c, w)


def _write_verdict_block(ws, model: ReportModel, fmts, top: int, ncols: int) -> int:
    """INN-02 conformity verdict banner (only called when ``model.verdict``
    is set -- see ``_write_overview``). Returns the first free row."""
    v = model.verdict
    overall = str(v.get("overall", "")).lower()
    fmt = fmts.get(f"verdict_{overall}", fmts["verdict_inconclusive"])
    label = {"pass": "PASS", "fail": "FAIL", "inconclusive": "INCONCLUSIVE"}.get(overall, overall.upper())
    spec_bits = " ".join(x for x in (v.get("spec_name"), v.get("spec_revision")) if x)
    headline = f"Conformity: {label}" + (f"  —  {spec_bits}" if spec_bits else "")
    ws.merge_range(top, 0, top, ncols - 1, headline, fmt)
    ws.set_row(top, 24)
    r = top + 1
    for rule in v.get("rules", []) or []:
        ws.merge_range(r, 0, r, ncols - 1, str(rule.get("text", "")), fmts["verdict_rule"])
        r += 1
    if v.get("statement"):
        ws.merge_range(r, 0, r, ncols - 1, str(v["statement"]), fmts["caption"])
        r += 1
    return r


_LOT_COLS = ["Lot", "Fields (n)", "Fields needed", "Mean G", "± 95% CI (G)", "G low", "G high",
             "%RA", "Mean ECD (µm)", "± 95% CI ECD (µm)", "Status"]


def _write_lot_block(ws, model: ReportModel, fmts, top: int, ncols: int) -> int:
    """INN-27 lot summary block (ASTM E112 sec. 15): one row per
    ``model.sample_statistics`` entry.  Returns the first free row."""
    width = max(ncols, len(_LOT_COLS))
    ws.merge_range(top, 0, top, width - 1,
                   "Lot statistics — ASTM E112 95 % confidence interval (Student t) and %RA",
                   fmts["section"])
    hdr = top + 1
    for c, h in enumerate(_LOT_COLS):
        ws.write(hdr, c, h, fmts["header"])
    ws.set_row(hdr, 30)
    r = hdr + 1
    for i, st in enumerate(model.sample_statistics):
        vals = [st.get("label") or st.get("scope", ""), st.get("n_fields", 0), st.get("n_needed", 0),
                st.get("G_mean"), st.get("G_ci95"), st.get("G_ci_low"), st.get("G_ci_high"),
                st.get("RA_pct"), st.get("ecd_mean_um"), st.get("ecd_ci95_um"), st.get("status", "")]
        for c, v in enumerate(vals):
            if 3 <= c <= 9:
                if isinstance(v, (int, float)):
                    ws.write_number(r, c, float(v), _band(fmts, i, "num2"))
                else:
                    ws.write(r, c, "n/a", _band(fmts, i))
            elif c in (1, 2):
                ws.write_number(r, c, int(v or 0), _band(fmts, i))
            else:
                ws.write(r, c, str(v), _band(fmts, i))
        r += 1
    return r


# ---------------------------------------------------------------------------
# Summary Charts
# ---------------------------------------------------------------------------

def _write_summary_charts(wb, model: ReportModel, images: List[ImageSummary], fmts, series: Dict[str, str],
                           name: str) -> None:
    ws = wb.add_worksheet(name)
    ws.set_tab_color(TAB_COLORS["charts"])
    ws.hide_gridlines(2)
    ws.merge_range(0, 0, 0, 7, "Combined Distributions (all images)", fmts["section"])

    all_grains = [g for img in images for g in img.grains]
    calibrated_all = all(i.has_calibration for i in images)
    au, du = _combined_unit(model, images)
    n_bins_area = model.bins.get("area", 0)
    n_bins_diam = model.bins.get("diameter", 0)
    opts = resolve_chart_options(model.chart_options)

    if calibrated_all:
        _, am, _, dm = resolve_units(images[0].px_per_um, model.units)
        area_vals = [g["area_um2"] * am for g in all_grains]
        diam_vals = [g["diameter_um"] * dm for g in all_grains]
    else:
        area_vals = [g["area_px"] for g in all_grains]
        diam_vals = [g["diameter_px"] for g in all_grains]

    row = 2
    if opts["area"]["enabled"]:
        row = _write_hist_block(ws, fmts, wb, row, area_vals, n_bins_area, au, "Grain Area",
                                opts["area"]["color"] or series["area_bar"], chart_anchor="J2",
                                fit_color=series["normal_fit"], opt=opts["area"],
                                show_fit=opts["normal_fit"])
        row += 2
    if opts["diameter"]["enabled"]:
        row = _write_hist_block(ws, fmts, wb, row, diam_vals, n_bins_diam, du, "Grain Diameter",
                                opts["diameter"]["color"] or series["diameter_bar"],
                                chart_anchor="J22", fit_color=series["normal_fit"],
                                opt=opts["diameter"], show_fit=opts["normal_fit"])

    # Per-image mean diameter bar w/ error bars + grain count bar.
    row += 2
    tbl_row = row
    ws.write(tbl_row, 0, "Image", fmts["header"])
    ws.write(tbl_row, 1, f"Mean Diameter ({du})", fmts["header"])
    ws.write(tbl_row, 2, f"Std Diameter ({du})", fmts["header"])
    ws.write(tbl_row, 3, "Grain Count", fmts["header"])
    for i, img in enumerate(images):
        _, _, _, dm_i = resolve_units(img.px_per_um, model.units)
        r = tbl_row + 1 + i
        ws.write(r, 0, img.display(), _band(fmts, i))
        ws.write_number(r, 1, round(img.mean_diameter_um * dm_i, 3), _band(fmts, i, "num2"))
        ws.write_number(r, 2, round(img.std_diameter_um * dm_i, 3), _band(fmts, i, "num2"))
        ws.write_number(r, 3, img.grain_count, _band(fmts, i))
    n = len(images)
    last = tbl_row + n

    diam_chart = wb.add_chart({"type": "column"})
    diam_chart.add_series({
        "name": f"Mean Diameter ({du})",
        "categories": [name, tbl_row + 1, 0, last, 0],
        "values": [name, tbl_row + 1, 1, last, 1],
        "fill": {"color": series["diameter_bar"]},
        "y_error_bars": {
            "type": "custom",
            "plus_values": [name, tbl_row + 1, 2, last, 2],
            "minus_values": [name, tbl_row + 1, 2, last, 2],
        },
        "gap": 40,
    })
    diam_chart.set_title({"name": "Mean Grain Diameter per Image"})
    diam_chart.set_x_axis({"name": "Image"})
    diam_chart.set_y_axis({"name": f"Mean Diameter ({du})", "num_format": "0.00",
                            "major_gridlines": {"visible": True, "line": {"color": SERIES["gridline"]}}})
    diam_chart.set_legend({"none": True})
    diam_chart.set_chartarea({"border": {"none": True}})
    diam_chart.set_size({"width": 480, "height": 300})
    ws.insert_chart(tbl_row, 5, diam_chart)

    count_chart = wb.add_chart({"type": "column"})
    count_chart.add_series({
        "name": "Grain Count",
        "categories": [name, tbl_row + 1, 0, last, 0],
        "values": [name, tbl_row + 1, 3, last, 3],
        "fill": {"color": series["count_bar"]},
        "gap": 40,
    })
    count_chart.set_title({"name": "Grain Count per Image"})
    count_chart.set_x_axis({"name": "Image"})
    count_chart.set_y_axis({"name": "Number of Grains", "num_format": "0",
                             "major_gridlines": {"visible": True, "line": {"color": SERIES["gridline"]}}})
    count_chart.set_legend({"none": True})
    count_chart.set_chartarea({"border": {"none": True}})
    count_chart.set_size({"width": 480, "height": 300})
    ws.insert_chart(tbl_row + 17, 5, count_chart)

    ws.set_column(0, 0, 26)
    ws.set_column(1, 3, 16)


def _write_hist_block(ws, fmts, wb, start_row, values, n_bins, unit, label, color, chart_anchor,
                       fit_color: str = SERIES["normal_fit"], opt: Optional[dict] = None,
                       show_fit: bool = True) -> int:
    """UX-14: ``opt`` is one metric's resolved ``chart_options`` entry
    (``reports.charts.resolve_chart_options``) — ``min``/``max`` filter the
    values before binning, ``title`` overrides the chart title (axis titles
    always keep their unit, so they are not overridable here). ``show_fit``
    is the options' global ``normal_fit`` toggle."""
    if isinstance(chart_anchor, tuple):
        chart_anchor = xl_rowcol_to_cell(chart_anchor[0], chart_anchor[1])
    opt = opt or {}
    values = filter_range(values, opt.get("min"), opt.get("max"))
    labels, counts, edges = build_bins(values, n_bins)
    if not labels:
        return start_row
    fit = normal_fit(values, edges) if show_fit else [0.0] * len(labels)
    nb = len(labels)

    ws.write(start_row, 0, "Bin Range", fmts["header"])
    ws.write(start_row, 1, "Count", fmts["header"])
    if show_fit:
        ws.write(start_row, 2, "Normal Fit", fmts["header"])
    for i in range(nb):
        r = start_row + 1 + i
        ws.write(r, 0, f"{labels[i]} {unit}", _band(fmts, i))
        ws.write_number(r, 1, int(counts[i]), _band(fmts, i))
        if show_fit:
            ws.write_number(r, 2, fit[i], _band(fmts, i, "num2"))

    bar = wb.add_chart({"type": "column"})
    bar.add_series({
        "name": "Count",
        "categories": [ws.get_name(), start_row + 1, 0, start_row + nb, 0],
        "values": [ws.get_name(), start_row + 1, 1, start_row + nb, 1],
        "fill": {"color": color},
        "gap": 0,
    })
    if show_fit:
        line = wb.add_chart({"type": "line"})
        line.add_series({
            "name": "Normal Fit",
            "categories": [ws.get_name(), start_row + 1, 0, start_row + nb, 0],
            "values": [ws.get_name(), start_row + 1, 2, start_row + nb, 2],
            "line": {"color": fit_color, "width": 2.25},
            "smooth": True,
        })
        bar.combine(line)
    bar.set_title({"name": (opt.get("title") or "").strip() or f"{label} Distribution"})
    bar.set_x_axis({"name": f"{label} ({unit})"})
    bar.set_y_axis({"name": "Number of Grains", "num_format": "0",
                     "major_gridlines": {"visible": True, "line": {"color": SERIES["gridline"]}}})
    bar.set_legend({"position": "bottom"})
    bar.set_chartarea({"border": {"none": True}})
    bar.set_size({"width": 480, "height": 300})
    ws.insert_chart(chart_anchor, bar)

    ws.set_column(0, 0, 18)
    ws.set_column(1, 2, 12)
    return start_row + nb + 1


# ---------------------------------------------------------------------------
# Per-image sheet
# ---------------------------------------------------------------------------

def _write_image_sheet(wb, model: ReportModel, img: ImageSummary, sheet_name, fmts,
                        series: Dict[str, str], tmpdir) -> None:
    ws = wb.add_worksheet(sheet_name)
    ws.set_tab_color(TAB_COLORS["image"])
    ws.hide_gridlines(2)
    ws.merge_range(0, 0, 0, 13, f"Image {img.order}: {img.display()}", fmts["title"])
    ws.set_row(0, 26)

    row_after_images = 3
    orig = _resized_png(tmpdir, img.image_path)
    if orig:
        path, w, h = orig
        ws.insert_image(2, 0, path, {"x_scale": 1, "y_scale": 1})
        row_after_images = max(row_after_images, 2 + int(h / 15) + 2)
    ovl = _resized_png(tmpdir, img.overlay_path, blend_src=img.image_path,
                       opacity=model.overlay_opacity)
    if ovl:
        path, w, h = ovl
        ws.insert_image(2, 6, path, {"x_scale": 1, "y_scale": 1})
        row_after_images = max(row_after_images, 2 + int(h / 15) + 2)

    stats_row = row_after_images + 1
    ws.merge_range(stats_row, 0, stats_row, 2, "Summary Statistics", fmts["section"])
    au, am, du, dm = resolve_units(img.px_per_um, model.units)
    r = stats_row + 1
    if model.hierarchy:
        stat_rows = [(label, (val or "—"), "") for (_, label), val in
                     zip(model.level_columns(), model.row_levels(img))]
    else:
        stat_rows = [("Sample", img.sample_id or "—", ""), ("Lot", img.lot_number or "—", "")]
    stat_rows += [("Total Grains", img.grain_count, "grains")]
    if img.has_calibration:
        stat_rows += [
            ("Mean Area", round(img.mean_area_um2 * am, 3), au),
            ("Std Dev Area", round(img.std_area_um2 * am, 3), au),
            ("Mean Diameter", round(img.mean_diameter_um * dm, 3), du),
            ("Std Dev Diameter", round(img.std_diameter_um * dm, 3), du),
        ]
    stat_rows += [
        ("Mean Circularity", round(img.mean_circularity, 4), "(0-1)"),
        ("Mean Aspect Ratio", round(img.mean_aspect_ratio, 4), "(1=equiaxed)"),
        ("Coverage", round(img.grain_coverage_pct, 2), "%"),
        ("Invalid Area", round(img.invalid_area_pct, 2), "%"),
        ("ASTM G", img.astm_g if img.astm_g is not None else "—", ""),
    ]
    for label, val, unit in stat_rows:
        ws.write(r, 0, label, fmts["label"])
        if isinstance(val, float):
            ws.write_number(r, 1, val, fmts["value_num2"])
        else:
            ws.write(r, 1, val, fmts["value"])
        ws.write(r, 2, unit, fmts["value"])
        r += 1

    hist_row = r + 2
    if img.has_calibration:
        area_vals = [g["area_um2"] * am for g in img.grains]
        diam_vals = [g["diameter_um"] * dm for g in img.grains]
    else:
        area_vals = [g["area_px"] for g in img.grains]
        diam_vals = [g["diameter_px"] for g in img.grains]
    opts = resolve_chart_options(model.chart_options)
    if opts["area"]["enabled"]:
        hist_row = _write_hist_block(ws, fmts, wb, hist_row, area_vals, model.bins.get("area", 0),
                                      au, "Grain Area", opts["area"]["color"] or series["area_bar"],
                                      chart_anchor=(hist_row, 5), fit_color=series["normal_fit"],
                                      opt=opts["area"], show_fit=opts["normal_fit"])
        hist_row += 2
    if opts["diameter"]["enabled"]:
        hist_row = _write_hist_block(ws, fmts, wb, hist_row, diam_vals,
                                      model.bins.get("diameter", 0), du, "Grain Diameter",
                                      opts["diameter"]["color"] or series["diameter_bar"],
                                      chart_anchor=(hist_row, 5), fit_color=series["normal_fit"],
                                      opt=opts["diameter"], show_fit=opts["normal_fit"])

    note_row = hist_row + 2
    ws.merge_range(note_row, 0, note_row, 2, "Caption / Notes", fmts["section"])
    ws.merge_range(note_row + 1, 0, note_row + 4, 4, (img.caption + ("\n" + img.notes if img.notes else "")).strip() or "—",
                    fmts["caption"])

    ws.set_column(0, 0, 20)
    ws.set_column(1, 2, 14)


# ---------------------------------------------------------------------------
# Methods
# ---------------------------------------------------------------------------

def _write_methods(wb, model: ReportModel, fmts, name: str) -> None:
    ws = wb.add_worksheet(name)
    ws.set_tab_color(TAB_COLORS["methods"])
    ws.hide_gridlines(2)
    ws.merge_range(0, 0, 0, 2, "Methods & Parameters", fmts["title"])
    ws.set_row(0, 26)

    r = 2
    if model.hierarchy:
        ws.merge_range(r, 0, r, 2, "Hierarchy", fmts["section"]); r += 1
        for h in model.hierarchy:
            ws.write(r, 0, str(h.get("label", h.get("key", ""))), fmts["label"])
            ws.write(r, 1, str(h.get("value", "")), fmts["value"])
            r += 1
        r += 1

    params = model.metadata.get("detection_params") or {}
    ws.merge_range(r, 0, r, 2, "Detection", fmts["section"]); r += 1
    rows = [("Mode", model.metadata.get("detection_mode", "—"))]
    rows += [(k, v) for k, v in params.items()]
    for label, val in rows:
        ws.write(r, 0, str(label), fmts["label"])
        ws.write(r, 1, str(val), fmts["value"])
        r += 1

    r += 1
    ws.merge_range(r, 0, r, 2, "Calibration", fmts["section"]); r += 1
    ws.write(r, 0, "Instrument", fmts["label"]); ws.write(r, 1, str(model.metadata.get("instrument", "—")), fmts["value"]); r += 1
    ws.write(r, 0, "Calibrated", fmts["label"])
    ws.write(r, 1, "Yes" if any(i.has_calibration for i in model.images) else "No", fmts["value"]); r += 1

    # FIX-07: INN-29 scale-verification check (``ReportModel.calibration``),
    # optional -- rows are skipped entirely when no check was recorded, so
    # a report with the feature unused is unchanged from before FIX-07.
    cal = model.calibration
    if cal and cal.get("text"):
        ws.write(r, 0, "Scale Verification", fmts["label"])
        ws.write(r, 1, str(cal.get("text")), fmts["value"]); r += 1
        if cal.get("source"):
            ws.write(r, 0, "Verification Source", fmts["label"])
            ws.write(r, 1, str(cal.get("source")), fmts["value"]); r += 1
        if cal.get("warnings"):
            ws.write(r, 0, "Verification Warnings", fmts["label"])
            ws.write(r, 1, "; ".join(str(w) for w in cal.get("warnings")), fmts["value"]); r += 1

    if model.verdict and model.verdict.get("overall") not in (None, "no_spec"):
        r += 1
        ws.merge_range(r, 0, r, 2, "Conformity", fmts["section"]); r += 1
        ws.write(r, 0, "Specification", fmts["label"])
        spec_bits = " ".join(x for x in (model.verdict.get("spec_name"), model.verdict.get("spec_revision")) if x)
        ws.write(r, 1, spec_bits or "—", fmts["value"]); r += 1
        ws.write(r, 0, "Decision rule", fmts["label"])
        ws.write(r, 1, str(model.verdict.get("decision_rule", "—")), fmts["value"]); r += 1
        ws.write(r, 0, "Statement", fmts["label"])
        ws.write(r, 1, str(model.verdict.get("statement", "—")), fmts["value"]); r += 1

    r += 1
    ws.merge_range(r, 0, r, 2, "Software", fmts["section"]); r += 1
    ws.write(r, 0, "Version", fmts["label"]); ws.write(r, 1, f"Grain Analyzer v{APP_VERSION}", fmts["value"]); r += 1
    ws.write(r, 0, "Generated by", fmts["label"])
    ws.write(r, 1, f"{model.operator or 'unknown operator'} / {model.organization or '—'} on {model.date}", fmts["value"])
    r += 1

    ws.set_column(0, 0, 24)
    ws.set_column(1, 1, 40)


# ---------------------------------------------------------------------------
# Custom text ("Notes") sheets — the designer's ``custom_text`` sections.
# Tab colour is a dedicated purple (``TAB_COLORS["custom_text"]``, #6A1B9A)
# — distinct from the five structural kinds so a Notes sheet is recognisable
# at a glance without being confused with Methods (amber) or Raw (grey).
# ---------------------------------------------------------------------------

def _write_custom_text_sheet(wb, sec: Section, fmts, name: str) -> None:
    ws = wb.add_worksheet(name)
    ws.set_tab_color(TAB_COLORS["custom_text"])
    ws.hide_gridlines(2)
    ws.merge_range(0, 0, 0, 6, sec.title or "Notes", fmts["title"])
    ws.set_row(0, 26)

    body_fmt = wb.add_format({"text_wrap": True, "valign": "top", "font_size": 11, "border": 0})
    body = str(sec.payload.get("body", "") or "").strip() or "—"
    ws.merge_range(2, 0, 30, 6, body, body_fmt)
    ws.set_column(0, 6, 18)


# ---------------------------------------------------------------------------
# Raw data
# ---------------------------------------------------------------------------

def _write_raw_sheet(wb, model: ReportModel, img: ImageSummary, fmts, name: str) -> None:
    ws = wb.add_worksheet(name)
    ws.set_tab_color(TAB_COLORS["raw"])
    ws.hide_gridlines(2)

    if img.has_calibration:
        headers = ["ID", "Area (µm²)", "Diameter (µm)", "Major (µm)", "Minor (µm)", "Perimeter (µm)",
                   "Circularity", "Aspect Ratio", "Eccentricity", "Cx", "Cy", "Note"]
        keys = ["id", "area_um2", "diameter_um", "major_um", "minor_um", "perimeter_um",
                "circularity", "aspect_ratio", "eccentricity", "centroid_x", "centroid_y", "note"]
    else:
        headers = ["ID", "Area (px²)", "Diameter (px)", "Perimeter (px)", "Circularity", "Aspect Ratio",
                   "Eccentricity", "Cx", "Cy", "Note"]
        keys = ["id", "area_px", "diameter_px", "perimeter_px", "circularity", "aspect_ratio",
                "eccentricity", "centroid_x", "centroid_y", "note"]

    top = 0
    if model.hierarchy:
        # Header block above the table (not leading data columns) so the
        # raw per-grain columns stay clean/uniform for pivoting.
        levels = model.row_levels(img)
        line = " | ".join(f"{label}: {val}" for (_, label), val in zip(model.level_columns(), levels))
        ws.merge_range(0, 0, 0, len(headers) - 1, line, fmts["subtitle"])
        top = 1

    ws.merge_range(top, 0, top, len(headers) - 1, f"Grain Data - {img.display()}", fmts["title"])
    for c, h in enumerate(headers):
        ws.write(top + 1, c, h, fmts["header"])
    ws.freeze_panes(top + 2, 0)

    for ri, g in enumerate(img.grains):
        r = top + 2 + ri
        for c, k in enumerate(keys):
            v = g.get(k, "" if k == "note" else None)
            fmt = _band(fmts, ri, "num4" if k in ("circularity", "aspect_ratio", "eccentricity") else
                        ("num2" if isinstance(v, float) else "band"))
            if isinstance(v, (int, float)):
                ws.write_number(r, c, v, fmt)
            else:
                ws.write(r, c, v or "", fmt)

    last_row = top + 1 + len(img.grains)
    ws.autofilter(top + 1, 0, max(last_row, top + 1), len(headers) - 1)
    last_col = len(headers) - 1
    for c in range(len(headers)):
        width = 30 if c == last_col else (14 if c else 8)
        ws.set_column(c, c, width)
