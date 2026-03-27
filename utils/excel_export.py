"""
Excel Export v2.0
==================
New layout:
  Page 1: Combined grain size distribution histogram + summary table
  Per image (2 pages each):
    Page A: Original image, grain overlay, histogram, summary stats
    Page B: Raw grain data table
"""

import openpyxl
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.chart import BarChart, Reference
from openpyxl.utils import get_column_letter
from openpyxl.drawing.image import Image as XlImage
import numpy as np
from datetime import datetime
import os
import cv2
import tempfile

# Colors
C_DARK_BLUE = "1A2B4A"
C_MID_BLUE = "2E5FA3"
C_LIGHT_GRAY = "F2F4F7"
C_WHITE = "FFFFFF"
C_TEXT_DARK = "1A1A2E"

_thin = Side(style="thin", color="CCCCCC")
_border = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)
_center = Alignment(horizontal="center", vertical="center")
_left_indent = Alignment(horizontal="left", vertical="center", indent=1)


def _hdr_fill(color=C_DARK_BLUE):
    return PatternFill("solid", fgColor=color)

def _hdr_font(size=11, bold=True, color=C_WHITE):
    return Font(name="Arial", size=size, bold=bold, color=color)

def _body_font(size=10, bold=False, color=C_TEXT_DARK):
    return Font(name="Arial", size=size, bold=bold, color=color)

def _alt_fill(alt):
    return PatternFill("solid", fgColor="EEF3FF" if not alt else C_LIGHT_GRAY)

def _set_col_w(ws, col, width):
    ws.column_dimensions[get_column_letter(col)].width = width


def _choose_area_unit(px_per_um):
    """Pick the largest sensible unit so values aren't tiny decimals."""
    if px_per_um <= 0:
        return "px²", 1.0, "px", 1.0
    um_per_px = 1.0 / px_per_um
    # If 1 pixel < 10nm, we're at nm scale
    nm_per_px = um_per_px * 1000.0
    if nm_per_px < 50:
        # nm scale: report areas in nm², diameters in nm
        return "nm²", (1000.0 / px_per_um) ** 2, "nm", 1000.0 / px_per_um
    else:
        return "µm²", (1.0 / px_per_um) ** 2, "µm", 1.0 / px_per_um


def _save_image_temp(image_bgr, max_width=400):
    """Save a BGR image to a temp PNG file, scaled to max_width."""
    h, w = image_bgr.shape[:2]
    if w > max_width:
        scale = max_width / w
        image_bgr = cv2.resize(image_bgr, (max_width, int(h * scale)))
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    cv2.imwrite(tmp.name, image_bgr)
    tmp.close()
    return tmp.name


def _build_histogram_data(grains, px_per_um):
    """Build histogram bins and counts. Returns (bin_labels, counts, unit_label, areas)."""
    area_unit, area_scale, _, _ = _choose_area_unit(px_per_um)

    if px_per_um > 0:
        areas = np.array([g.area_um2 * (area_scale / ((1.0 / px_per_um) ** 2))
                          if area_unit == "nm²"
                          else g.area_um2
                          for g in grains])
        if area_unit == "nm²":
            areas = np.array([g.area_px * ((1000.0 / px_per_um) ** 2) for g in grains])
        else:
            areas = np.array([g.area_um2 for g in grains])
    else:
        areas = np.array([g.area_px for g in grains])

    if len(areas) == 0:
        return [], [], f"Area ({area_unit})", areas

    n_bins = min(max(int(np.sqrt(len(areas))), 5), 25)
    counts, edges = np.histogram(areas, bins=n_bins)
    labels = [f"{edges[i]:.3g}–{edges[i + 1]:.3g}" for i in range(len(edges) - 1)]
    return labels, counts, f"Area ({area_unit})", areas


def _write_histogram_chart(ws, grains, px_per_um, start_row, start_col=1):
    """Write histogram data + chart starting at (start_row, start_col)."""
    labels, counts, unit_label, areas = _build_histogram_data(grains, px_per_um)
    if len(labels) == 0:
        return start_row

    # Write data for chart
    data_row = start_row
    ws.cell(data_row, start_col, "Bin Range").font = _hdr_font(10)
    ws.cell(data_row, start_col).fill = _hdr_fill(C_MID_BLUE)
    ws.cell(data_row, start_col).alignment = _center
    ws.cell(data_row, start_col + 1, "Count").font = _hdr_font(10)
    ws.cell(data_row, start_col + 1).fill = _hdr_fill(C_MID_BLUE)
    ws.cell(data_row, start_col + 1).alignment = _center

    for i, (label, count) in enumerate(zip(labels, counts)):
        r = data_row + 1 + i
        ws.cell(r, start_col, label).font = _body_font()
        ws.cell(r, start_col, label).border = _border
        ws.cell(r, start_col + 1, int(count)).font = _body_font()
        ws.cell(r, start_col + 1).border = _border
        ws.cell(r, start_col + 1).alignment = _center

    # Chart
    chart = BarChart()
    chart.type = "col"
    chart.title = "Grain Size Distribution"
    chart.y_axis.title = "Number of Grains"
    chart.x_axis.title = unit_label
    chart.style = 10
    chart.width = 18
    chart.height = 12

    data_ref = Reference(ws, min_col=start_col + 1,
                         min_row=data_row, max_row=data_row + len(labels))
    chart.add_data(data_ref, titles_from_data=True)
    cat_ref = Reference(ws, min_col=start_col,
                        min_row=data_row + 1, max_row=data_row + len(labels))
    chart.set_categories(cat_ref)

    chart_col = get_column_letter(start_col + 3)
    ws.add_chart(chart, f"{chart_col}{data_row}")

    return data_row + len(labels) + 1


def _write_summary_table(ws, result, image_path, start_row, start_col=1):
    """Write summary statistics table. Returns next available row."""
    area_unit, area_scale, diam_unit, diam_scale = _choose_area_unit(result.px_per_um)
    has_cal = result.has_calibration
    r = start_row

    def stat_row(label, value, unit="", alt=False):
        nonlocal r
        fill = _alt_fill(alt)
        c = ws.cell(r, start_col, label)
        c.font = _body_font(bold=True)
        c.fill = fill
        c.border = _border
        c.alignment = _left_indent
        c = ws.cell(r, start_col + 1, value)
        c.font = _body_font()
        c.fill = fill
        c.border = _border
        c.alignment = _center
        c = ws.cell(r, start_col + 2, unit)
        c.font = _body_font(color="666666")
        c.fill = fill
        c.border = _border
        c.alignment = _center
        r += 1

    # Section header
    for col in range(start_col, start_col + 3):
        c = ws.cell(r, col)
        c.fill = _hdr_fill(C_MID_BLUE)
        c.font = _hdr_font(10)
        c.alignment = _center
    ws.cell(r, start_col, "Statistic")
    ws.cell(r, start_col + 1, "Value")
    ws.cell(r, start_col + 2, "Unit")
    r += 1

    stat_row("Image", os.path.basename(image_path) if image_path else "N/A")
    stat_row("Total Grains", result.grain_count, "grains", True)

    if has_cal:
        if area_unit == "nm²":
            mean_area = result.mean_area_um2 * 1e6
            std_area = result.std_area_um2 * 1e6
            mean_diam = result.mean_diameter_um * 1000
            std_diam = result.std_diameter_um * 1000
        else:
            mean_area = result.mean_area_um2
            std_area = result.std_area_um2
            mean_diam = result.mean_diameter_um
            std_diam = result.std_diameter_um

        stat_row("Mean Area", f"{mean_area:.4g}", area_unit)
        stat_row("Std Dev Area", f"{std_area:.4g}", area_unit, True)
        stat_row("Mean Diameter", f"{mean_diam:.4g}", diam_unit)
        stat_row("Std Dev Diameter", f"{std_diam:.4g}", diam_unit, True)
    else:
        areas_px = [g.area_px for g in result.grains] if result.grains else []
        if areas_px:
            stat_row("Mean Area", f"{np.mean(areas_px):.1f}", "px²")
            stat_row("Std Dev Area", f"{np.std(areas_px):.1f}", "px²", True)

    stat_row("Mean Circularity", f"{result.mean_circularity:.4f}", "(0–1)")
    stat_row("Mean Aspect Ratio", f"{result.mean_aspect_ratio:.4f}", "(1=equiaxed)", True)

    if has_cal and result.total_analyzed_area_um2 > 0:
        stat_row("Grain Coverage", f"{result.grain_coverage_pct:.2f}", "%")

    return r


# ==================================================================
# Main export functions
# ==================================================================

def export_to_excel(result, image_path, output_path, params=None,
                    image_bgr=None):
    """Single-image export."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    _write_overview_sheet(wb, [(result, image_path, image_bgr)])
    _write_image_summary_sheet(wb, result, image_path, image_bgr, 1)
    _write_image_data_sheet(wb, result, image_path, 1)

    wb.save(output_path)
    return output_path


def export_multi_to_excel(analysed_tabs, output_path):
    """
    Multi-image export.
    analysed_tabs: list of (result, image_path, image_bgr) tuples
    """
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    _write_overview_sheet(wb, analysed_tabs)

    for idx, (result, image_path, image_bgr) in enumerate(analysed_tabs, 1):
        _write_image_summary_sheet(wb, result, image_path, image_bgr, idx)
        _write_image_data_sheet(wb, result, image_path, idx)

    wb.save(output_path)
    return output_path


# ==================================================================
# Sheet writers
# ==================================================================

def _write_overview_sheet(wb, analysed_tabs):
    """Page 1: Combined histogram + overall summary table."""
    ws = wb.create_sheet("Overview")
    ws.sheet_view.showGridLines = False

    # Title
    ws.merge_cells("A1:H1")
    c = ws["A1"]
    c.value = "SEM Grain Analysis Report"
    c.font = Font(name="Arial", size=18, bold=True, color=C_WHITE)
    c.fill = _hdr_fill(C_DARK_BLUE)
    c.alignment = _center
    ws.row_dimensions[1].height = 36

    ws.merge_cells("A2:H2")
    c = ws["A2"]
    c.value = f"Generated: {datetime.now().strftime('%Y-%m-%d  %H:%M:%S')}    |    {len(analysed_tabs)} image(s)"
    c.font = Font(name="Arial", size=10, color=C_WHITE)
    c.fill = _hdr_fill(C_MID_BLUE)
    c.alignment = _center

    # Combine all grains for the overview histogram
    all_grains = []
    px_per_um = 0.0
    for result, path, img in analysed_tabs:
        all_grains.extend(result.grains)
        if result.has_calibration:
            px_per_um = result.px_per_um

    row = 4

    if all_grains:
        # Build a combined result for the summary table
        combined = type(analysed_tabs[0][0])()
        combined.grains = all_grains
        combined.grain_count = len(all_grains)
        combined.px_per_um = px_per_um
        combined.has_calibration = px_per_um > 0

        if combined.has_calibration:
            areas = np.array([g.area_um2 for g in all_grains])
            diams = np.array([g.equivalent_diameter_um for g in all_grains])
            combined.mean_area_um2 = float(np.mean(areas))
            combined.std_area_um2 = float(np.std(areas))
            combined.mean_diameter_um = float(np.mean(diams))
            combined.std_diameter_um = float(np.std(diams))

            total_area = sum(r.total_analyzed_area_um2
                             for r, p, i in analysed_tabs
                             if r.total_analyzed_area_um2 > 0)
            combined.total_analyzed_area_um2 = total_area
            if total_area > 0:
                combined.grain_coverage_pct = float(np.sum(areas)) / total_area * 100.0

        combined.mean_circularity = float(np.mean(
            [g.circularity for g in all_grains]))
        combined.mean_aspect_ratio = float(np.mean(
            [g.aspect_ratio for g in all_grains]))

        # Summary table
        ws.merge_cells(f"A{row}:C{row}")
        c = ws.cell(row, 1, "Combined Summary (All Images)")
        c.font = _hdr_font(12)
        c.fill = _hdr_fill(C_DARK_BLUE)
        c.alignment = _center
        row += 1

        row = _write_summary_table(ws, combined, "All Images", row)
        row += 1

        # Histogram
        ws.merge_cells(f"A{row}:C{row}")
        c = ws.cell(row, 1, "Grain Size Distribution (All Images)")
        c.font = _hdr_font(12)
        c.fill = _hdr_fill(C_DARK_BLUE)
        c.alignment = _center
        row += 1

        row = _write_histogram_chart(ws, all_grains, px_per_um, row)

    _set_col_w(ws, 1, 28)
    _set_col_w(ws, 2, 18)
    _set_col_w(ws, 3, 16)


def _write_image_summary_sheet(wb, result, image_path, image_bgr, idx):
    """Per-image page A: images, histogram, summary."""
    base = os.path.splitext(os.path.basename(image_path))[0][:18]
    ws = wb.create_sheet(f"{base}-Summary"[:31])
    ws.sheet_view.showGridLines = False

    # Title
    ws.merge_cells("A1:H1")
    c = ws["A1"]
    c.value = f"Image {idx}: {os.path.basename(image_path)}"
    c.font = Font(name="Arial", size=14, bold=True, color=C_WHITE)
    c.fill = _hdr_fill(C_DARK_BLUE)
    c.alignment = _center
    ws.row_dimensions[1].height = 30

    row = 3
    temp_files = []

    # Embed original + overlay images side by side
    if image_bgr is not None:
        try:
            tmp_orig = _save_image_temp(image_bgr, max_width=380)
            temp_files.append(tmp_orig)
            img_orig = XlImage(tmp_orig)
            ws.add_image(img_orig, f"A{row}")

            if result.overlay_image is not None:
                tmp_overlay = _save_image_temp(result.overlay_image, max_width=380)
                temp_files.append(tmp_overlay)
                img_overlay = XlImage(tmp_overlay)
                ws.add_image(img_overlay, f"F{row}")

            # Estimate rows consumed by images (~20 rows per 300px height)
            h_img = image_bgr.shape[0]
            w_img = image_bgr.shape[1]
            scale = min(380 / w_img, 1.0)
            img_rows = max(15, int(h_img * scale / 15))
            row += img_rows
        except Exception as e:
            logger_msg = f"Could not embed images: {e}"
            ws.cell(row, 1, f"(Images not embedded: {e})")
            row += 2

    row += 1

    # Summary table
    ws.merge_cells(f"A{row}:C{row}")
    c = ws.cell(row, 1, "Summary Statistics")
    c.font = _hdr_font(11)
    c.fill = _hdr_fill(C_DARK_BLUE)
    c.alignment = _center
    row += 1
    row = _write_summary_table(ws, result, image_path, row)
    row += 1

    # Histogram
    ws.merge_cells(f"A{row}:C{row}")
    c = ws.cell(row, 1, "Grain Size Distribution")
    c.font = _hdr_font(11)
    c.fill = _hdr_fill(C_DARK_BLUE)
    c.alignment = _center
    row += 1
    if result.grains:
        _write_histogram_chart(ws, result.grains, result.px_per_um, row)

    _set_col_w(ws, 1, 28)
    _set_col_w(ws, 2, 18)
    _set_col_w(ws, 3, 16)

    # Clean up temp files after save (they persist until wb.save)
    # We can't delete now since openpyxl reads them at save time
    # Store them on the worksheet object for later cleanup
    ws._temp_files = temp_files


def _write_image_data_sheet(wb, result, image_path, idx):
    """Per-image page B: raw grain data table."""
    base = os.path.splitext(os.path.basename(image_path))[0][:18]
    ws = wb.create_sheet(f"{base}-Data"[:31])
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = "A3"

    has_cal = result.has_calibration
    area_unit, _, diam_unit, _ = _choose_area_unit(result.px_per_um)

    # Determine scale factors
    if has_cal and area_unit == "nm²":
        a_mult = 1e6  # um² → nm²
        d_mult = 1000  # um → nm
    else:
        a_mult = 1.0
        d_mult = 1.0

    if has_cal:
        headers = [
            "Grain ID",
            f"Area ({area_unit})",
            f"Equiv. Diameter ({diam_unit})",
            f"Major Axis ({diam_unit})",
            f"Minor Axis ({diam_unit})",
            f"Perimeter ({diam_unit})",
            "Circularity",
            "Aspect Ratio",
            "Eccentricity",
            "Centroid X (px)",
            "Centroid Y (px)",
        ]
    else:
        headers = [
            "Grain ID",
            "Area (px²)",
            "Equiv. Diameter (px)",
            "Perimeter (px)",
            "Circularity",
            "Aspect Ratio",
            "Eccentricity",
            "Centroid X (px)",
            "Centroid Y (px)",
        ]

    # Title
    ws.merge_cells(f"A1:{get_column_letter(len(headers))}1")
    c = ws["A1"]
    c.value = f"Grain Data — {os.path.basename(image_path)}"
    c.font = Font(name="Arial", size=13, bold=True, color=C_WHITE)
    c.fill = _hdr_fill(C_DARK_BLUE)
    c.alignment = _center

    # Headers
    for col, h in enumerate(headers, 1):
        c = ws.cell(2, col, h)
        c.font = _hdr_font(10)
        c.fill = _hdr_fill(C_MID_BLUE)
        c.alignment = _center
        c.border = _border

    # Data
    for r_idx, grain in enumerate(result.grains, 3):
        alt = (r_idx % 2 == 0)
        fill = _alt_fill(alt)

        if has_cal:
            row_data = [
                grain.grain_id,
                round(grain.area_um2 * a_mult, 4),
                round(grain.equivalent_diameter_um * d_mult, 4),
                round(grain.major_axis_um * d_mult, 4),
                round(grain.minor_axis_um * d_mult, 4),
                round(grain.perimeter_um * d_mult, 4),
                round(grain.circularity, 4),
                round(grain.aspect_ratio, 4),
                round(grain.eccentricity, 4),
                round(grain.centroid_x, 1),
                round(grain.centroid_y, 1),
            ]
        else:
            row_data = [
                grain.grain_id,
                round(grain.area_px, 1),
                round(grain.equivalent_diameter_px, 2),
                round(grain.perimeter_px, 2),
                round(grain.circularity, 4),
                round(grain.aspect_ratio, 4),
                round(grain.eccentricity, 4),
                round(grain.centroid_x, 1),
                round(grain.centroid_y, 1),
            ]

        for col, val in enumerate(row_data, 1):
            c = ws.cell(r_idx, col, val)
            c.font = _body_font()
            c.fill = fill
            c.border = _border
            c.alignment = _center

    # Column widths
    widths_cal = [10, 16, 18, 16, 16, 16, 14, 14, 14, 14, 14]
    widths_nocal = [10, 14, 18, 14, 14, 14, 14, 14, 14]
    widths = widths_cal if has_cal else widths_nocal
    for i, w in enumerate(widths, 1):
        _set_col_w(ws, i, w)

    # Auto-filter
    ws.auto_filter.ref = f"A2:{get_column_letter(len(headers))}{2 + len(result.grains)}"
