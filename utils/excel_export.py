"""
Excel Export v2.3 — whole-number bins (0-10, 10-20, etc.),
data table below chart, chart beside images, grain area.
"""

import openpyxl
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.utils import get_column_letter
from openpyxl.drawing.image import Image as XlImage
import numpy as np
from datetime import datetime
import os, cv2, tempfile, math

C_DARK_BLUE = "1A2B4A"; C_MID_BLUE = "2E5FA3"; C_LIGHT_GRAY = "F2F4F7"
C_WHITE = "FFFFFF"; C_TEXT_DARK = "1A1A2E"
_thin = Side(style="thin", color="CCCCCC")
_border = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)
_center = Alignment(horizontal="center", vertical="center")
_left_indent = Alignment(horizontal="left", vertical="center", indent=1)

def _hf(c=C_DARK_BLUE): return PatternFill("solid", fgColor=c)
def _hfn(s=11, b=True, c=C_WHITE): return Font(name="Arial", size=s, bold=b, color=c)
def _bf(size=10, bold=False, color=C_TEXT_DARK): return Font(name="Arial", size=size, bold=bold, color=color)
def _af(a): return PatternFill("solid", fgColor="EEF3FF" if not a else C_LIGHT_GRAY)
def _scw(ws, col, w): ws.column_dimensions[get_column_letter(col)].width = w

def _unit(ppu):
    if ppu <= 0: return "px²", 1.0, "px", 1.0
    if 1000.0 / ppu < 50: return "nm²", 1e6, "nm", 1000.0
    return "µm²", 1.0, "µm", 1.0

def _fmt(val):
    if val == 0: return "0"
    av = abs(val)
    if av >= 100: return f"{val:.0f}"
    if av >= 10: return f"{val:.1f}"
    if av >= 1: return f"{val:.2f}"
    if av >= 0.1: return f"{val:.3f}"
    return f"{val:.3g}"

def _simg(bgr, mw=300):
    h, w = bgr.shape[:2]
    if w > mw: s = mw / w; bgr = cv2.resize(bgr, (mw, int(h * s)))
    t = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    cv2.imwrite(t.name, bgr); t.close(); return t.name


def _build_bins(grains, ppu, n_bins=0):
    """Build whole-number bins starting at 0."""
    au, am, _, _ = _unit(ppu)
    if ppu > 0:
        vals = np.array([g.area_um2 * am for g in grains])
    else:
        vals = np.array([g.area_px for g in grains])
    if len(vals) < 2: return [], [], [], au, vals

    vmax = float(np.max(vals))
    if n_bins < 3:
        n_bins = min(max(int(math.sqrt(len(vals))), 5), 25)
    bw = max(1, math.ceil(vmax / n_bins))

    edges = []
    e = 0
    while e <= vmax + bw:
        edges.append(e); e += bw
    edges = np.array(edges, dtype=float)
    counts, _ = np.histogram(vals, bins=edges)

    while len(counts) > 1 and counts[-1] == 0:
        counts = counts[:-1]; edges = edges[:len(counts) + 1]

    labels = [f"{int(edges[i])}-{int(edges[i+1])}{au}" for i in range(len(counts))]
    return labels, counts.tolist(), edges.tolist(), au, vals


def _write_hist(ws, grains, ppu, start_row, sc=1, chart_anchor=None, n_bins=0):
    labels, counts, edges, au, vals = _build_bins(grains, ppu, n_bins)
    if not labels: return start_row
    nb = len(labels)
    mu, sigma = float(np.mean(vals)), float(np.std(vals))
    bw = edges[1] - edges[0]; nt = len(vals)

    # Write data table FIRST (we'll put chart above or at anchor)
    dr = start_row; hc = sc
    ws.cell(dr, hc, "Bin Range").font = _hfn(10); ws.cell(dr, hc).fill = _hf(C_MID_BLUE); ws.cell(dr, hc).alignment = _center
    ws.cell(dr, hc+1, "Count").font = _hfn(10); ws.cell(dr, hc+1).fill = _hf(C_MID_BLUE); ws.cell(dr, hc+1).alignment = _center
    ws.cell(dr, hc+2, "Normal Fit").font = _hfn(10); ws.cell(dr, hc+2).fill = _hf(C_MID_BLUE); ws.cell(dr, hc+2).alignment = _center

    for i in range(nb):
        r = dr + 1 + i
        ws.cell(r, hc, labels[i]).font = _bf(9); ws.cell(r, hc).border = _border; ws.cell(r, hc).alignment = _center
        ws.cell(r, hc+1, int(counts[i])).font = _bf(); ws.cell(r, hc+1).border = _border; ws.cell(r, hc+1).alignment = _center
        bc = (edges[i] + edges[i+1]) / 2.0
        nv = 0
        if sigma > 0:
            nv = (1.0 / (sigma * math.sqrt(2*math.pi))) * math.exp(-0.5*((bc-mu)/sigma)**2) * bw * nt
        ws.cell(r, hc+2, round(nv, 2)).font = _bf(); ws.cell(r, hc+2).border = _border; ws.cell(r, hc+2).alignment = _center

    # Build chart
    bar = BarChart(); bar.type = "col"; bar.title = "Grain Size Distribution"
    bar.y_axis.title = "Number of Grains"; bar.x_axis.title = f"Grain Area ({au})"
    bar.style = 10; bar.width = 18; bar.height = 12; bar.legend = None; bar.gapWidth = 0
    bar.add_data(Reference(ws, min_col=hc+1, min_row=dr, max_row=dr+nb), titles_from_data=True)
    bar.set_categories(Reference(ws, min_col=hc, min_row=dr+1, max_row=dr+nb))
    bar.x_axis.tickLblPos = "low"
    if bar.series:
        s = bar.series[0]; s.graphicalProperties.solidFill = "6478DC"
        s.graphicalProperties.line.solidFill = "1E1E2D"; s.graphicalProperties.line.width = 6000

    line = LineChart()
    line.add_data(Reference(ws, min_col=hc+2, min_row=dr, max_row=dr+nb), titles_from_data=True)
    line.legend = None
    if line.series:
        s = line.series[0]; s.graphicalProperties.line.solidFill = "DC3278"
        s.graphicalProperties.line.width = 25000; s.smooth = True
    bar += line

    # Place chart at anchor (above the data table on summary pages, or inline)
    if chart_anchor:
        ws.add_chart(bar, chart_anchor)
    else:
        ws.add_chart(bar, f"{get_column_letter(hc+4)}{dr}")

    _scw(ws, hc, 18); _scw(ws, hc+1, 10); _scw(ws, hc+2, 12)
    return dr + nb + 2


def _write_summary(ws, res, ip, sr, sc=1):
    au, am, du, dm = _unit(res.px_per_um); hc = res.has_calibration; r = sr

    def row(l, v, u="", a=False):
        nonlocal r; f = _af(a)
        ws.cell(r,sc,l).font=_bf(bold=True); ws.cell(r,sc).fill=f; ws.cell(r,sc).border=_border; ws.cell(r,sc).alignment=_left_indent
        ws.cell(r,sc+1,v).font=_bf(); ws.cell(r,sc+1).fill=f; ws.cell(r,sc+1).border=_border; ws.cell(r,sc+1).alignment=_center
        ws.cell(r,sc+2,u).font=_bf(color="666666"); ws.cell(r,sc+2).fill=f; ws.cell(r,sc+2).border=_border; ws.cell(r,sc+2).alignment=_center
        r += 1

    for c in range(sc, sc+3):
        ws.cell(r,c).fill=_hf(C_MID_BLUE); ws.cell(r,c).font=_hfn(10); ws.cell(r,c).alignment=_center
    ws.cell(r,sc,"Statistic"); ws.cell(r,sc+1,"Value"); ws.cell(r,sc+2,"Unit"); r += 1

    row("Image", os.path.basename(ip) if ip else "N/A")
    row("Total Grains", res.grain_count, "grains", True)
    if hc:
        row("Mean Area", _fmt(res.mean_area_um2*am), au); row("Std Dev Area", _fmt(res.std_area_um2*am), au, True)
        row("Mean Diameter", _fmt(res.mean_diameter_um*dm), du); row("Std Dev Diameter", _fmt(res.std_diameter_um*dm), du, True)
    else:
        ap = [g.area_px for g in res.grains] if res.grains else []
        if ap: row("Mean Area", f"{np.mean(ap):.1f}", "px²"); row("Std Dev Area", f"{np.std(ap):.1f}", "px²", True)
    row("Mean Circularity", f"{res.mean_circularity:.4f}", "(0–1)")
    row("Mean Aspect Ratio", f"{res.mean_aspect_ratio:.4f}", "(1=equiaxed)", True)
    if hc and res.total_analyzed_area_um2 > 0: row("Grain Coverage", f"{res.grain_coverage_pct:.2f}", "%")
    return r


def export_to_excel(result, image_path, output_path, params=None, image_bgr=None, n_bins=0):
    wb = openpyxl.Workbook(); wb.remove(wb.active)
    _write_overview(wb, [(result, image_path, image_bgr)], n_bins)
    _write_img_summary(wb, result, image_path, image_bgr, 1, n_bins)
    _write_img_data(wb, result, image_path, 1)
    wb.save(output_path); return output_path

def export_multi_to_excel(tabs, output_path, n_bins=0):
    wb = openpyxl.Workbook(); wb.remove(wb.active)
    _write_overview(wb, tabs, n_bins)
    for i, (r, p, b) in enumerate(tabs, 1):
        _write_img_summary(wb, r, p, b, i, n_bins)
        _write_img_data(wb, r, p, i)
    wb.save(output_path); return output_path


def _write_overview(wb, tabs, n_bins):
    ws = wb.create_sheet("Overview"); ws.sheet_view.showGridLines = False
    ws.merge_cells("A1:H1"); c = ws["A1"]; c.value = "Grain Analysis Report"
    c.font = Font(name="Arial", size=18, bold=True, color=C_WHITE); c.fill = _hf(C_DARK_BLUE); c.alignment = _center
    ws.row_dimensions[1].height = 36
    ws.merge_cells("A2:H2"); c = ws["A2"]
    c.value = f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {len(tabs)} image(s) | Developer: Jack Samaniego"
    c.font = Font(name="Arial", size=10, color=C_WHITE); c.fill = _hf(C_MID_BLUE); c.alignment = _center

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
            comb.total_analyzed_area_um2 = ta; comb.grain_coverage_pct = float(np.sum(a))/ta*100 if ta > 0 else 0
        else: comb.grain_coverage_pct = 0
        comb.mean_circularity = float(np.mean([g.circularity for g in ag]))
        comb.mean_aspect_ratio = float(np.mean([g.aspect_ratio for g in ag]))
        ws.merge_cells(f"A{row}:C{row}"); ws.cell(row,1,"Combined Summary").font = _hfn(12)
        ws.cell(row,1).fill = _hf(C_DARK_BLUE); ws.cell(row,1).alignment = _center; row += 1
        row = _write_summary(ws, comb, "All Images", row); row += 1
        ws.merge_cells(f"A{row}:C{row}"); ws.cell(row,1,"Grain Size Distribution").font = _hfn(12)
        ws.cell(row,1).fill = _hf(C_DARK_BLUE); ws.cell(row,1).alignment = _center; row += 1
        _write_hist(ws, ag, ppu, row, n_bins=n_bins)
    _scw(ws, 1, 28); _scw(ws, 2, 18); _scw(ws, 3, 16)


def _write_img_summary(wb, result, ip, bgr, idx, n_bins):
    base = os.path.splitext(os.path.basename(ip))[0][:18]
    ws = wb.create_sheet(f"{base}-Summary"[:31]); ws.sheet_view.showGridLines = False
    ws.merge_cells("A1:N1"); c = ws["A1"]
    c.value = f"Image {idx}: {os.path.basename(ip)}"
    c.font = Font(name="Arial", size=14, bold=True, color=C_WHITE); c.fill = _hf(C_DARK_BLUE)
    c.alignment = _center; ws.row_dimensions[1].height = 30

    ir = 3
    if bgr is not None:
        try:
            ws.add_image(XlImage(_simg(bgr, 280)), f"A{ir}")
            if result.overlay_image is not None:
                ws.add_image(XlImage(_simg(result.overlay_image, 280)), f"E{ir}")
        except Exception: pass

    # Chart at column J (right of both images, not overlapping)
    if result.grains:
        # Data table goes at row 22+ (below images), chart anchors at J3
        data_start = 22
        _write_hist(ws, result.grains, result.px_per_um, data_start,
                    sc=1, chart_anchor="J3", n_bins=n_bins)

    # Summary table below images
    hi = bgr.shape[0] if bgr is not None else 300
    wi = bgr.shape[1] if bgr is not None else 400
    sc = min(280 / max(wi, 1), 1.0)
    sr = max(22, 3 + int(hi * sc / 15))

    # If data table is at row 22, put summary after it
    summary_start = sr + 20 if result.grains else sr
    ws.merge_cells(f"A{summary_start}:C{summary_start}")
    ws.cell(summary_start, 1, "Summary Statistics").font = _hfn(11)
    ws.cell(summary_start, 1).fill = _hf(C_DARK_BLUE); ws.cell(summary_start, 1).alignment = _center
    _write_summary(ws, result, ip, summary_start + 1)
    _scw(ws, 1, 28); _scw(ws, 2, 18); _scw(ws, 3, 16)


def _write_img_data(wb, result, ip, idx):
    base = os.path.splitext(os.path.basename(ip))[0][:18]
    ws = wb.create_sheet(f"{base}-Data"[:31]); ws.sheet_view.showGridLines = False; ws.freeze_panes = "A3"
    hc = result.has_calibration; au, am, du, dm = _unit(result.px_per_um)
    if hc:
        hdrs = ["ID", f"Area ({au})", f"Diam ({du})", f"Major ({du})", f"Minor ({du})",
                f"Perim ({du})", "Circ.", "AR", "Eccen.", "Cx", "Cy"]
    else:
        hdrs = ["ID", "Area (px²)", "Diam (px)", "Perim (px)", "Circ.", "AR", "Eccen.", "Cx", "Cy"]

    ws.merge_cells(f"A1:{get_column_letter(len(hdrs))}1"); c = ws["A1"]
    c.value = f"Grain Data — {os.path.basename(ip)}"
    c.font = Font(name="Arial", size=13, bold=True, color=C_WHITE); c.fill = _hf(C_DARK_BLUE); c.alignment = _center
    for col, h in enumerate(hdrs, 1):
        c = ws.cell(2, col, h); c.font = _hfn(10); c.fill = _hf(C_MID_BLUE); c.alignment = _center; c.border = _border

    for ri, g in enumerate(result.grains, 3):
        f = _af(ri % 2 == 0)
        if hc:
            rd = [g.grain_id, round(g.area_um2*am, 2), round(g.equivalent_diameter_um*dm, 2),
                  round(g.major_axis_um*dm, 2), round(g.minor_axis_um*dm, 2),
                  round(g.perimeter_um*dm, 2), round(g.circularity, 4), round(g.aspect_ratio, 4),
                  round(g.eccentricity, 4), round(g.centroid_x, 1), round(g.centroid_y, 1)]
        else:
            rd = [g.grain_id, round(g.area_px, 1), round(g.equivalent_diameter_px, 2),
                  round(g.perimeter_px, 2), round(g.circularity, 4), round(g.aspect_ratio, 4),
                  round(g.eccentricity, 4), round(g.centroid_x, 1), round(g.centroid_y, 1)]
        for col, val in enumerate(rd, 1):
            c = ws.cell(ri, col, val); c.font = _bf(); c.fill = f; c.border = _border; c.alignment = _center
    for i, w in enumerate([8,14,14,14,14,14,10,10,10,10,10] if hc else [8,14,14,14,10,10,10,10,10], 1):
        _scw(ws, i, w)
    ws.auto_filter.ref = f"A2:{get_column_letter(len(hdrs))}{2+len(result.grains)}"
