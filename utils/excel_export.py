"""
Excel Export
============
Exports grain analysis results to a formatted Excel workbook with:
  - Summary statistics sheet
  - Per-grain data sheet
  - Histogram data sheet
  - Charts
"""

import openpyxl
from openpyxl.styles import (
    PatternFill, Font, Alignment, Border, Side, GradientFill
)
from openpyxl.chart import BarChart, Reference
from openpyxl.chart.series import DataPoint
from openpyxl.utils import get_column_letter
import numpy as np
from datetime import datetime
import os


# Color palette
C_DARK_BLUE  = "1A2B4A"
C_MID_BLUE   = "2E5FA3"
C_LIGHT_BLUE = "C9DEFF"
C_ACCENT     = "E8A000"
C_GREEN      = "2D7D46"
C_LIGHT_GRAY = "F2F4F7"
C_WHITE      = "FFFFFF"
C_TEXT_DARK  = "1A1A2E"


def _header_fill(color=C_DARK_BLUE):
    return PatternFill("solid", fgColor=color)

def _cell_fill(color=C_LIGHT_GRAY):
    return PatternFill("solid", fgColor=color)

def _header_font(size=11, bold=True, color=C_WHITE):
    return Font(name="Calibri", size=size, bold=bold, color=color)

def _body_font(size=10, bold=False, color=C_TEXT_DARK):
    return Font(name="Calibri", size=size, bold=bold, color=color)

def _thin_border():
    thin = Side(style="thin", color="CCCCCC")
    return Border(left=thin, right=thin, top=thin, bottom=thin)

def _center():
    return Alignment(horizontal="center", vertical="center", wrap_text=False)

def _set_col_width(ws, col, width):
    ws.column_dimensions[get_column_letter(col)].width = width


def export_to_excel(result, image_path: str, output_path: str, params=None):
    """
    Export AnalysisResult to a formatted Excel file.

    Args:
        result: AnalysisResult from GrainDetector
        image_path: Original image file path (for metadata)
        output_path: Where to save the .xlsx
        params: DetectionParams (optional, for logging)
    """
    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # remove default sheet

    _write_summary_sheet(wb, result, image_path, params)
    _write_grains_sheet(wb, result)
    _write_distribution_sheet(wb, result)

    wb.save(output_path)
    return output_path


def _write_summary_sheet(wb, result, image_path, params):
    ws = wb.create_sheet("Summary")
    ws.sheet_view.showGridLines = False

    # ---- Title block ----
    ws.merge_cells("A1:H1")
    c = ws["A1"]
    c.value = "SEM Grain Analysis Report"
    c.font = Font(name="Calibri", size=18, bold=True, color=C_WHITE)
    c.fill = _header_fill(C_DARK_BLUE)
    c.alignment = _center()
    ws.row_dimensions[1].height = 36

    ws.merge_cells("A2:H2")
    c = ws["A2"]
    c.value = f"Generated: {datetime.now().strftime('%Y-%m-%d  %H:%M:%S')}    |    Image: {os.path.basename(image_path)}"
    c.font = Font(name="Calibri", size=10, color=C_WHITE)
    c.fill = _header_fill(C_MID_BLUE)
    c.alignment = _center()
    ws.row_dimensions[2].height = 20

    row = 4

    # ---- Calibration block ----
    def section_header(r, title, cols="A:D"):
        ws.merge_cells(f"{cols.split(':')[0]}{r}:{cols.split(':')[1]}{r}")
        c = ws.cell(r, 1, title)
        c.font = Font(name="Calibri", size=11, bold=True, color=C_WHITE)
        c.fill = _header_fill(C_MID_BLUE)
        c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        ws.row_dimensions[r].height = 22

    def stat_row(r, label, value, unit="", alt=False):
        fill = _cell_fill("EEF3FF") if not alt else _cell_fill(C_LIGHT_GRAY)
        lc = ws.cell(r, 1, label)
        lc.font = _body_font(bold=True)
        lc.fill = fill
        lc.border = _thin_border()
        lc.alignment = Alignment(horizontal="left", indent=1)

        vc = ws.cell(r, 2, value)
        vc.font = _body_font()
        vc.fill = fill
        vc.border = _thin_border()
        vc.alignment = _center()

        uc = ws.cell(r, 3, unit)
        uc.font = _body_font(color="666666")
        uc.fill = fill
        uc.border = _thin_border()
        uc.alignment = _center()
        ws.row_dimensions[r].height = 18

    section_header(row, "Calibration", "A:C")
    row += 1
    stat_row(row, "Calibration Status",
             "Calibrated" if result.has_calibration else "NOT CALIBRATED (pixel units only)", "")
    row += 1
    if result.has_calibration:
        stat_row(row, "Scale (pixels per µm)", f"{result.px_per_um:.4f}", "px/µm", True)
        row += 1
        stat_row(row, "Scale (µm per pixel)", f"{1/result.px_per_um:.6f}", "µm/px")
        row += 1

    row += 1
    section_header(row, "Grain Count & Coverage", "A:C")
    row += 1
    stat_row(row, "Total Grains Detected", result.grain_count, "grains")
    row += 1
    if result.has_calibration:
        stat_row(row, "Total Image Area", f"{result.total_analyzed_area_um2:.2f}", "µm²", True)
        row += 1
        stat_row(row, "Grain Coverage", f"{result.grain_coverage_pct:.2f}", "%")
        row += 1

    row += 1
    section_header(row, "Area Statistics (µm²)" if result.has_calibration else "Area Statistics (pixels²)", "A:C")
    row += 1
    if result.has_calibration:
        stat_row(row, "Mean Grain Area", f"{result.mean_area_um2:.4f}", "µm²"); row += 1
        stat_row(row, "Std Dev Area", f"{result.std_area_um2:.4f}", "µm²", True); row += 1
        stat_row(row, "Median Grain Area", f"{result.median_area_um2:.4f}", "µm²"); row += 1
        stat_row(row, "Min Grain Area", f"{result.min_area_um2:.4f}", "µm²", True); row += 1
        stat_row(row, "Max Grain Area", f"{result.max_area_um2:.4f}", "µm²"); row += 1
    else:
        areas_px = [g.area_px for g in result.grains]
        if areas_px:
            stat_row(row, "Mean Grain Area", f"{np.mean(areas_px):.1f}", "px²"); row += 1
            stat_row(row, "Std Dev Area", f"{np.std(areas_px):.1f}", "px²", True); row += 1
            stat_row(row, "Median Grain Area", f"{np.median(areas_px):.1f}", "px²"); row += 1
            stat_row(row, "Min Grain Area", f"{np.min(areas_px):.1f}", "px²", True); row += 1
            stat_row(row, "Max Grain Area", f"{np.max(areas_px):.1f}", "px²"); row += 1

    row += 1
    section_header(row, "Diameter Statistics" if result.has_calibration else "Diameter (px)", "A:C")
    row += 1
    if result.has_calibration:
        stat_row(row, "Mean Equiv. Diameter", f"{result.mean_diameter_um:.4f}", "µm"); row += 1
        stat_row(row, "Std Dev Diameter", f"{result.std_diameter_um:.4f}", "µm", True); row += 1
    else:
        diams = [g.equivalent_diameter_px for g in result.grains]
        if diams:
            stat_row(row, "Mean Equiv. Diameter", f"{np.mean(diams):.2f}", "px"); row += 1
            stat_row(row, "Std Dev Diameter", f"{np.std(diams):.2f}", "px", True); row += 1

    row += 1
    section_header(row, "Shape Statistics", "A:C")
    row += 1
    stat_row(row, "Mean Circularity", f"{result.mean_circularity:.4f}", "(0–1, 1=circle)"); row += 1
    stat_row(row, "Mean Aspect Ratio", f"{result.mean_aspect_ratio:.4f}", "(1=equiaxed)", True); row += 1

    # Set column widths
    _set_col_width(ws, 1, 32)
    _set_col_width(ws, 2, 20)
    _set_col_width(ws, 3, 22)
    for col in range(4, 9):
        _set_col_width(ws, col, 12)


def _write_grains_sheet(wb, result):
    ws = wb.create_sheet("Grain Data")
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = "A3"

    has_cal = result.has_calibration

    headers = [
        "Grain ID",
        "Area (µm²)" if has_cal else "Area (px²)",
        "Equiv. Diam. (µm)" if has_cal else "Equiv. Diam. (px)",
        "Major Axis (µm)" if has_cal else "Major Axis (px)",
        "Minor Axis (µm)" if has_cal else "Minor Axis (px)",
        "Perimeter (µm)" if has_cal else "Perimeter (px)",
        "Circularity",
        "Aspect Ratio",
        "Eccentricity",
        "Centroid X (px)",
        "Centroid Y (px)",
    ]

    # Title row
    ws.merge_cells(f"A1:{get_column_letter(len(headers))}1")
    c = ws["A1"]
    c.value = "Individual Grain Measurements"
    c.font = Font(name="Calibri", size=13, bold=True, color=C_WHITE)
    c.fill = _header_fill(C_DARK_BLUE)
    c.alignment = _center()
    ws.row_dimensions[1].height = 26

    # Header row
    for col, h in enumerate(headers, 1):
        c = ws.cell(2, col, h)
        c.font = _header_font(size=10)
        c.fill = _header_fill(C_MID_BLUE)
        c.alignment = _center()
        c.border = _thin_border()
    ws.row_dimensions[2].height = 20

    # Data rows
    for r_idx, grain in enumerate(result.grains, 3):
        alt = (r_idx % 2 == 0)
        fill = _cell_fill("EEF3FF") if not alt else _cell_fill(C_WHITE)

        if has_cal:
            row_data = [
                grain.grain_id,
                round(grain.area_um2, 4),
                round(grain.equivalent_diameter_um, 4),
                round(grain.major_axis_um, 4),
                round(grain.minor_axis_um, 4),
                round(grain.perimeter_um, 4),
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
                round(grain.major_axis_um if has_cal else grain.equivalent_diameter_px, 2),
                round(grain.minor_axis_um if has_cal else grain.equivalent_diameter_px * 0.8, 2),
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
            c.border = _thin_border()
            c.alignment = _center()
        ws.row_dimensions[r_idx].height = 16

    widths = [10, 14, 18, 16, 16, 16, 14, 14, 14, 14, 14]
    for i, w in enumerate(widths, 1):
        _set_col_width(ws, i, w)

    # Auto-filter
    ws.auto_filter.ref = f"A2:{get_column_letter(len(headers))}{2 + len(result.grains)}"


def _write_distribution_sheet(wb, result):
    ws = wb.create_sheet("Distribution")
    ws.sheet_view.showGridLines = False

    ws.merge_cells("A1:F1")
    c = ws["A1"]
    c.value = "Grain Size Distribution"
    c.font = Font(name="Calibri", size=13, bold=True, color=C_WHITE)
    c.fill = _header_fill(C_DARK_BLUE)
    c.alignment = _center()
    ws.row_dimensions[1].height = 26

    has_cal = result.has_calibration
    if not result.grains:
        ws["A3"].value = "No grains detected."
        return

    # Build histogram
    if has_cal:
        values = np.array([g.area_um2 for g in result.grains])
        unit_label = "Area (µm²)"
    else:
        values = np.array([g.area_px for g in result.grains])
        unit_label = "Area (px²)"

    n_bins = min(max(int(np.sqrt(len(values))), 5), 20)
    counts, edges = np.histogram(values, bins=n_bins)
    bin_labels = [f"{edges[i]:.3g}–{edges[i+1]:.3g}" for i in range(len(edges)-1)]

    # Headers
    for col, h in enumerate(["Bin Range", unit_label, "Count", "Frequency (%)", "Cumulative (%)"], 1):
        c = ws.cell(2, col, h)
        c.font = _header_font(size=10)
        c.fill = _header_fill(C_MID_BLUE)
        c.alignment = _center()
        c.border = _thin_border()
    ws.row_dimensions[2].height = 20

    total = sum(counts)
    cum = 0
    for i, (label, count) in enumerate(zip(bin_labels, counts), 3):
        freq = (count / total * 100) if total > 0 else 0
        cum += freq
        alt = (i % 2 == 0)
        fill = _cell_fill("EEF3FF") if not alt else _cell_fill(C_WHITE)
        for col, val in enumerate([label, f"{edges[i-3]:.4g}", count, round(freq, 2), round(cum, 2)], 1):
            c = ws.cell(i, col, val)
            c.font = _body_font()
            c.fill = fill
            c.border = _thin_border()
            c.alignment = _center()
        ws.row_dimensions[i].height = 16

    # Bar chart
    chart = BarChart()
    chart.type = "col"
    chart.title = "Grain Area Distribution"
    chart.y_axis.title = "Count"
    chart.x_axis.title = unit_label
    chart.style = 10
    chart.width = 18
    chart.height = 12

    data_ref = Reference(ws, min_col=3, min_row=2, max_row=2 + n_bins)
    chart.add_data(data_ref, titles_from_data=True)
    cat_ref = Reference(ws, min_col=1, min_row=3, max_row=2 + n_bins)
    chart.set_categories(cat_ref)
    chart.shape = 4

    ws.add_chart(chart, "G3")

    widths = [22, 14, 10, 16, 16]
    for i, w in enumerate(widths, 1):
        _set_col_width(ws, i, w)
