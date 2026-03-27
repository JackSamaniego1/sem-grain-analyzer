"""
Excel Export v2.2 — grain area histogram with normal curve,
bin range labels, no gaps, chart beside images, configurable bins.
"""

import openpyxl
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.utils import get_column_letter
from openpyxl.drawing.image import Image as XlImage
import numpy as np
from datetime import datetime
import os, cv2, tempfile, math

C_DARK_BLUE = "1A2B4A"
C_MID_BLUE = "2E5FA3"
C_LIGHT_GRAY = "F2F4F7"
C_WHITE = "FFFFFF"
C_TEXT_DARK = "1A1A2E"

_thin = Side(style="thin", color="CCCCCC")
_border = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)
_center = Alignment(horizontal="center", vertical="center")
_left_indent = Alignment(horizontal="left", vertical="center", indent=1)

def _hdr_fill(c=C_DARK_BLUE): return PatternFill("solid", fgColor=c)
def _hdr_font(size=11, bold=True, color=C_WHITE): return Font(name="Arial", size=size, bold=bold, color=color)
def _body_font(size=10, bold=False, color=C_TEXT_DARK): return Font(name="Arial", size=size, bold=bold, color=color)
def _alt_fill(alt): return PatternFill("solid", fgColor="EEF3FF" if not alt else C_LIGHT_GRAY)
def _set_col_w(ws, col, w): ws.column_dimensions[get_column_letter(col)].width = w

def _choose_unit(px_per_um):
    if px_per_um <= 0: return "px²", 1.0, "px", 1.0
    if 1000.0 / px_per_um < 50: return "nm²", 1e6, "nm", 1000.0
    return "µm²", 1.0, "µm", 1.0

def _fmt_num(val):
    if abs(val) < 0.01 and val != 0: return f"{val:.4g}"
    if abs(val) >= 1000: return f"{val:.0f}"
    if abs(val) >= 10: return f"{val:.1f}"
    if abs(val) >= 1: return f"{val:.2f}"
    return f"{val:.3f}"

def _save_img(bgr, max_w=320):
    h, w = bgr.shape[:2]
    if w > max_w: s = max_w / w; bgr = cv2.resize(bgr, (max_w, int(h * s)))
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    cv2.imwrite(tmp.name, bgr); tmp.close()
    return tmp.name


def _write_histogram(ws, grains, px_per_um, start_row, start_col=1,
                     chart_anchor=None, n_bins=0):
    """Histogram: grain area, no bar gaps, bin labels, normal curve."""
    au, am, _, _ = _choose_unit(px_per_um)
    if px_per_um > 0:
        vals = np.array([g.area_um2 * am for g in grains])
    else:
        vals = np.array([g.area_px for g in grains])
    if len(vals) < 2: return start_row

    if n_bins < 3:
        n_bins = min(max(int(math.sqrt(len(vals))), 5), 25)
    counts, edges = np.histogram(vals, bins=n_bins)
    nb = len(counts)

    def fmt(v):
        if abs(v) >= 1000: return f"{v:.0f}"
        elif abs(v) >= 10: return f"{v:.1f}"
        elif abs(v) >= 1: return f"{v:.1f}"
        return f"{v:.2f}"

    labels = [f"{fmt(edges[i])}-{fmt(edges[i+1])}{au}" for i in range(nb)]
    mu, sigma = float(np.mean(vals)), float(np.std(vals))
    bw = edges[1] - edges[0]
    nt = len(vals)

    dr, hc = start_row, start_col
    for i, (h_txt, col_off) in enumerate([("Bin Range", 0), ("Count", 1), ("Normal Fit", 2)]):
        c = ws.cell(dr, hc + col_off, h_txt)
        c.font = _hdr_font(10); c.fill = _hdr_fill(C_MID_BLUE); c.alignment = _center

    for i in range(nb):
        r = dr + 1 + i
        ws.cell(r, hc, labels[i]).font = _body_font(9)
        ws.cell(r, hc).border = _border; ws.cell(r, hc).alignment = _center
        ws.cell(r, hc + 1, int(counts[i])).font = _body_font()
        ws.cell(r, hc + 1).border = _border; ws.cell(r, hc + 1).alignment = _center
        bc = (edges[i] + edges[i + 1]) / 2.0
        nv = 0
        if sigma > 0:
            nv = (1.0 / (sigma * math.sqrt(2 * math.pi))) * math.exp(-0.5 * ((bc - mu) / sigma) ** 2)
            nv *= bw * nt
        ws.cell(r, hc + 2, round(nv, 2)).font = _body_font()
        ws.cell(r, hc + 2).border = _border; ws.cell(r, hc + 2).alignment = _center

    bar = BarChart()
    bar.type = "col"; bar.title = "Grain Size Distribution"
    bar.y_axis.title = "Number of Grains"
    bar.x_axis.title = f"Grain Area ({au})"
    bar.style = 10; bar.width = 18; bar.height = 12; bar.legend = None
    bar.gapWidth = 0  # No gap between bars

    bar.add_data(Reference(ws, min_col=hc+1, min_row=dr, max_row=dr+nb), titles_from_data=True)
    bar.set_categories(Reference(ws, min_col=hc, min_row=dr+1, max_row=dr+nb))

    # X axis labels outside
    bar.x_axis.tickLblPos = "low"

    if bar.series:
        s = bar.series[0]
        s.graphicalProperties.solidFill = "6478DC"
        s.graphicalProperties.line.solidFill = "1E1E2D"
        s.graphicalProperties.line.width = 6000

    line = LineChart()
    line.add_data(Reference(ws, min_col=hc+2, min_row=dr, max_row=dr+nb), titles_from_data=True)
    line.legend = None
    if line.series:
        s = line.series[0]
        s.graphicalProperties.line.solidFill = "DC3278"
        s.graphicalProperties.line.width = 25000
        s.smooth = True

    bar += line
    ws.add_chart(bar, chart_anchor or f"{get_column_letter(hc+4)}{dr}")

    _set_col_w(ws, hc, 18); _set_col_w(ws, hc+1, 10); _set_col_w(ws, hc+2, 12)
    return dr + nb + 2


def _write_summary_table(ws, result, image_path, start_row, sc=1):
    au, am, du, dm = _choose_unit(result.px_per_um)
    has_cal = result.has_calibration
    r = start_row

    def sr(label, value, unit="", alt=False):
        nonlocal r; fill = _alt_fill(alt)
        ws.cell(r, sc, label).font = _body_font(bold=True); ws.cell(r, sc).fill = fill
        ws.cell(r, sc).border = _border; ws.cell(r, sc).alignment = _left_indent
        ws.cell(r, sc+1, value).font = _body_font(); ws.cell(r, sc+1).fill = fill
        ws.cell(r, sc+1).border = _border; ws.cell(r, sc+1).alignment = _center
        ws.cell(r, sc+2, unit).font = _body_font(color="666666"); ws.cell(r, sc+2).fill = fill
        ws.cell(r, sc+2).border = _border; ws.cell(r, sc+2).alignment = _center
        r += 1

    for col in range(sc, sc+3):
        ws.cell(r, col).fill = _hdr_fill(C_MID_BLUE); ws.cell(r, col).font = _hdr_font(10)
        ws.cell(r, col).alignment = _center
    ws.cell(r, sc, "Statistic"); ws.cell(r, sc+1, "Value"); ws.cell(r, sc+2, "Unit"); r += 1

    sr("Image", os.path.basename(image_path) if image_path else "N/A")
    sr("Total Grains", result.grain_count, "grains", True)
    if has_cal:
        sr("Mean Area", _fmt_num(result.mean_area_um2 * am), au)
        sr("Std Dev Area", _fmt_num(result.std_area_um2 * am), au, True)
        sr("Mean Diameter", _fmt_num(result.mean_diameter_um * dm), du)
        sr("Std Dev Diameter", _fmt_num(result.std_diameter_um * dm), du, True)
    else:
        ap = [g.area_px for g in result.grains] if result.grains else []
        if ap:
            sr("Mean Area", f"{np.mean(ap):.1f}", "px²")
            sr("Std Dev Area", f"{np.std(ap):.1f}", "px²", True)
    sr("Mean Circularity", f"{result.mean_circularity:.4f}", "(0–1)")
    sr("Mean Aspect Ratio", f"{result.mean_aspect_ratio:.4f}", "(1=equiaxed)", True)
    if has_cal and result.total_analyzed_area_um2 > 0:
        sr("Grain Coverage", f"{result.grain_coverage_pct:.2f}", "%")
    return r


def export_to_excel(result, image_path, output_path, params=None,
                    image_bgr=None, n_bins=0):
    wb = openpyxl.Workbook(); wb.remove(wb.active)
    _write_overview(wb, [(result, image_path, image_bgr)], n_bins)
    _write_img_summary(wb, result, image_path, image_bgr, 1, n_bins)
    _write_img_data(wb, result, image_path, 1)
    wb.save(output_path); return output_path


def export_multi_to_excel(analysed_tabs, output_path, n_bins=0):
    wb = openpyxl.Workbook(); wb.remove(wb.active)
    _write_overview(wb, analysed_tabs, n_bins)
    for idx, (result, path, bgr) in enumerate(analysed_tabs, 1):
        _write_img_summary(wb, result, path, bgr, idx, n_bins)
        _write_img_data(wb, result, path, idx)
    wb.save(output_path); return output_path


def _write_overview(wb, tabs, n_bins):
    ws = wb.create_sheet("Overview"); ws.sheet_view.showGridLines = False
    ws.merge_cells("A1:H1"); c = ws["A1"]
    c.value = "SEM Grain Analysis Report"
    c.font = Font(name="Arial", size=18, bold=True, color=C_WHITE)
    c.fill = _hdr_fill(C_DARK_BLUE); c.alignment = _center; ws.row_dimensions[1].height = 36
    ws.merge_cells("A2:H2"); c = ws["A2"]
    c.value = f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {len(tabs)} image(s)"
    c.font = Font(name="Arial", size=10, color=C_WHITE); c.fill = _hdr_fill(C_MID_BLUE); c.alignment = _center

    ag = []; ppu = 0.0
    for res, p, i in tabs:
        ag.extend(res.grains)
        if res.has_calibration: ppu = res.px_per_um
    row = 4
    if ag:
        comb = type(tabs[0][0])(); comb.grains = ag; comb.grain_count = len(ag)
        comb.px_per_um = ppu; comb.has_calibration = ppu > 0
        if comb.has_calibration:
            a = np.array([g.area_um2 for g in ag]); d = np.array([g.equivalent_diameter_um for g in ag])
            comb.mean_area_um2 = float(np.mean(a)); comb.std_area_um2 = float(np.std(a))
            comb.mean_diameter_um = float(np.mean(d)); comb.std_diameter_um = float(np.std(d))
            ta = sum(r.total_analyzed_area_um2 for r, p, i in tabs if r.total_analyzed_area_um2 > 0)
            comb.total_analyzed_area_um2 = ta
            comb.grain_coverage_pct = float(np.sum(a)) / ta * 100 if ta > 0 else 0
        else: comb.grain_coverage_pct = 0
        comb.mean_circularity = float(np.mean([g.circularity for g in ag]))
        comb.mean_aspect_ratio = float(np.mean([g.aspect_ratio for g in ag]))

        ws.merge_cells(f"A{row}:C{row}"); ws.cell(row, 1, "Combined Summary").font = _hdr_font(12)
        ws.cell(row, 1).fill = _hdr_fill(C_DARK_BLUE); ws.cell(row, 1).alignment = _center; row += 1
        row = _write_summary_table(ws, comb, "All Images", row); row += 1
        ws.merge_cells(f"A{row}:C{row}"); ws.cell(row, 1, "Grain Size Distribution").font = _hdr_font(12)
        ws.cell(row, 1).fill = _hdr_fill(C_DARK_BLUE); ws.cell(row, 1).alignment = _center; row += 1
        _write_histogram(ws, ag, ppu, row, n_bins=n_bins)
    _set_col_w(ws, 1, 28); _set_col_w(ws, 2, 18); _set_col_w(ws, 3, 16)


def _write_img_summary(wb, result, image_path, image_bgr, idx, n_bins):
    base = os.path.splitext(os.path.basename(image_path))[0][:18]
    ws = wb.create_sheet(f"{base}-Summary"[:31]); ws.sheet_view.showGridLines = False
    ws.merge_cells("A1:N1"); c = ws["A1"]
    c.value = f"Image {idx}: {os.path.basename(image_path)}"
    c.font = Font(name="Arial", size=14, bold=True, color=C_WHITE)
    c.fill = _hdr_fill(C_DARK_BLUE); c.alignment = _center; ws.row_dimensions[1].height = 30

    ir = 3
    if image_bgr is not None:
        try:
            ws.add_image(XlImage(_save_img(image_bgr, 320)), f"A{ir}")
            if result.overlay_image is not None:
                ws.add_image(XlImage(_save_img(result.overlay_image, 320)), f"F{ir}")
        except Exception: pass

    # Chart positioned to the RIGHT of both images (column K)
    if result.grains:
        _write_histogram(ws, result.grains, result.px_per_um, ir,
                         start_col=15, chart_anchor="K3", n_bins=n_bins)

    # Summary table below images
    hi = image_bgr.shape[0] if image_bgr is not None else 300
    wi = image_bgr.shape[1] if image_bgr is not None else 400
    sc = min(320 / max(wi, 1), 1.0)
    sr = max(20, 3 + int(hi * sc / 15))
    ws.merge_cells(f"A{sr}:C{sr}"); ws.cell(sr, 1, "Summary Statistics").font = _hdr_font(11)
    ws.cell(sr, 1).fill = _hdr_fill(C_DARK_BLUE); ws.cell(sr, 1).alignment = _center
    _write_summary_table(ws, result, image_path, sr + 1)
    _set_col_w(ws, 1, 28); _set_col_w(ws, 2, 18); _set_col_w(ws, 3, 16)


def _write_img_data(wb, result, image_path, idx):
    base = os.path.splitext(os.path.basename(image_path))[0][:18]
    ws = wb.create_sheet(f"{base}-Data"[:31]); ws.sheet_view.showGridLines = False
    ws.freeze_panes = "A3"
    hc = result.has_calibration; au, am, du, dm = _choose_unit(result.px_per_um)

    if hc:
        hdrs = ["Grain ID", f"Area ({au})", f"Equiv. Diam ({du})", f"Major ({du})",
                f"Minor ({du})", f"Perim ({du})", "Circ.", "Asp. Ratio", "Eccen.",
                "Cx (px)", "Cy (px)"]
    else:
        hdrs = ["Grain ID", "Area (px²)", "Diam (px)", "Perim (px)",
                "Circ.", "Asp. Ratio", "Eccen.", "Cx (px)", "Cy (px)"]

    ws.merge_cells(f"A1:{get_column_letter(len(hdrs))}1"); c = ws["A1"]
    c.value = f"Grain Data — {os.path.basename(image_path)}"
    c.font = Font(name="Arial", size=13, bold=True, color=C_WHITE)
    c.fill = _hdr_fill(C_DARK_BLUE); c.alignment = _center

    for col, h in enumerate(hdrs, 1):
        c = ws.cell(2, col, h); c.font = _hdr_font(10); c.fill = _hdr_fill(C_MID_BLUE)
        c.alignment = _center; c.border = _border

    for ri, g in enumerate(result.grains, 3):
        fill = _alt_fill(ri % 2 == 0)
        if hc:
            rd = [g.grain_id, round(g.area_um2*am, 4), round(g.equivalent_diameter_um*dm, 4),
                  round(g.major_axis_um*dm, 4), round(g.minor_axis_um*dm, 4),
                  round(g.perimeter_um*dm, 4), round(g.circularity, 4), round(g.aspect_ratio, 4),
                  round(g.eccentricity, 4), round(g.centroid_x, 1), round(g.centroid_y, 1)]
        else:
            rd = [g.grain_id, round(g.area_px, 1), round(g.equivalent_diameter_px, 2),
                  round(g.perimeter_px, 2), round(g.circularity, 4), round(g.aspect_ratio, 4),
                  round(g.eccentricity, 4), round(g.centroid_x, 1), round(g.centroid_y, 1)]
        for col, val in enumerate(rd, 1):
            c = ws.cell(ri, col, val); c.font = _body_font(); c.fill = fill
            c.border = _border; c.alignment = _center

    ws_list = [10, 16, 16, 14, 14, 14, 12, 12, 12, 12, 12] if hc else [10, 14, 14, 14, 12, 12, 12, 12, 12]
    for i, w in enumerate(ws_list, 1): _set_col_w(ws, i, w)
    ws.auto_filter.ref = f"A2:{get_column_letter(len(hdrs))}{2 + len(result.grains)}"
