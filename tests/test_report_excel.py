"""Tests for reports.excel_renderer.render_excel: sheet order/colours/content."""
import os
import sys
import tempfile

import numpy as np
import openpyxl
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import cv2

from tests.conftest import make_mosaic
from core.grain_detector import GrainDetector, DetectionParams
from reports.model import ReportModel, ReportImageInput, Section
from reports.excel_renderer import render_excel
from reports.charts import PALETTES, TAB_COLORS


def _build_model(tmp_path, n=3, px_per_um=8.0, seeds=None):
    det = GrainDetector()
    seeds = seeds or list(range(1, n + 1))
    items = []
    for i, seed in enumerate(seeds):
        gray, _ = make_mosaic(seed=seed, h=256, w=256, n_grains=30)
        bgr = np.repeat(gray[:, :, None], 3, axis=2)
        res = det.analyze(bgr, px_per_um=px_per_um, params=DetectionParams())
        img_path = str(tmp_path / f"synth_{i}.png")
        cv2.imwrite(img_path, bgr)
        items.append(ReportImageInput(
            image_path=img_path, result=res, image_bgr=bgr,
            sample_id=f"S{i}", lot_number="L1",
        ))
    return ReportModel.from_results(
        items, title="Test Report", operator="Jack", organization="Acme",
        metadata={"detection_mode": "boundary", "detection_params": {"min_grain_size_px": 50},
                  "instrument": "SEM-1"},
        asset_dir=str(tmp_path / "assets"),
    )


def _build_mixed_model(tmp_path):
    """One calibrated image + one uncalibrated image."""
    det = GrainDetector()
    items = []
    for i, (seed, ppu) in enumerate([(1, 8.0), (2, 0.0)]):
        gray, _ = make_mosaic(seed=seed, h=256, w=256, n_grains=30)
        bgr = np.repeat(gray[:, :, None], 3, axis=2)
        res = det.analyze(bgr, px_per_um=ppu, params=DetectionParams())
        img_path = str(tmp_path / f"synth_{i}.png")
        cv2.imwrite(img_path, bgr)
        items.append(ReportImageInput(image_path=img_path, result=res, image_bgr=bgr,
                                       sample_id=f"S{i}", lot_number="L1"))
    return ReportModel.from_results(items, title="Mixed", asset_dir=str(tmp_path / "assets"))


def test_uncalibrated_overview_row_has_no_zero_size_stats(tmp_path):
    model = _build_model(tmp_path, n=1, px_per_um=0.0)
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    ws = wb["Overview"]
    row = next(ws.iter_rows(min_row=5, max_row=5, values_only=True))
    # columns: #, Image, Sample, Lot, Grains, MeanArea, MedianArea, StdArea,
    # MeanDiameter, StdDiameter, Units, Coverage%, Invalid%, Circ, AR, ASTM G
    assert row[4] > 0  # grain count sanity check
    assert row[5] > 0  # mean area must not be zeroed
    assert row[8] > 0  # mean diameter must not be zeroed
    assert row[10] == "px² / px"


def test_mixed_calibration_overview_rows_have_correct_units_and_no_zeros(tmp_path):
    model = _build_mixed_model(tmp_path)
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    ws = wb["Overview"]
    rows = [r for r in ws.iter_rows(min_row=5, max_row=7, values_only=True) if r[1]]
    calibrated_row = next(r for r in rows if r[1] and "synth_0" in r[1])
    uncal_row = next(r for r in rows if r[1] and "synth_1" in r[1])
    assert calibrated_row[5] > 0 and calibrated_row[8] > 0
    assert "µm" in calibrated_row[10]
    assert uncal_row[5] > 0 and uncal_row[8] > 0  # was 0 before the fix
    assert uncal_row[10] == "px² / px"
    combined_row = rows[-1]
    assert combined_row[1] == "Combined (all images)"


def test_sheet_order_and_tab_colours(tmp_path):
    model = _build_model(tmp_path, n=3)
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    names = wb.sheetnames
    assert names[0] == "Overview"
    assert names[1] == "Summary Charts"
    img_sheets = [n for n in names if n.startswith("Img ")]
    raw_sheets = [n for n in names if n.startswith("Raw")]
    assert len(img_sheets) == 3
    assert len(raw_sheets) == 3
    assert "Methods" in names
    # Raw sheets are strictly last.
    raw_start = min(names.index(n) for n in raw_sheets)
    assert raw_start == len(names) - len(raw_sheets)

    def tab_hex(sheet_name):
        return "#" + wb[sheet_name].sheet_properties.tabColor.rgb[-6:]

    assert tab_hex("Overview") == "#1A2B4A"
    assert tab_hex("Summary Charts") == "#2E7D32"
    for n in img_sheets:
        assert tab_hex(n) == "#00796B"
    assert tab_hex("Methods") == "#F9A825"
    for n in raw_sheets:
        assert tab_hex(n) == "#757575"


def test_overview_has_one_row_per_image_and_unit_headers(tmp_path):
    model = _build_model(tmp_path, n=4)
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    ws = wb["Overview"]
    header = [c.value for c in next(ws.iter_rows(min_row=4, max_row=4))]
    assert header[0] == "#"
    assert "µm" in "".join(h for h in header if h)
    # rows: header(4) + 4 images + 1 combined = data rows 5..9
    data_rows = [r for r in ws.iter_rows(min_row=5, max_row=9, values_only=True) if r[1]]
    assert len(data_rows) == 5  # 4 images + combined
    assert data_rows[-1][1] == "Combined (all images)"
    assert ws.freeze_panes == "B5"


def test_overview_hyperlinks_target_image_sheets(tmp_path):
    model = _build_model(tmp_path, n=2)
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    ws = wb["Overview"]
    row5 = ws[5]
    cell = row5[1]
    assert cell.hyperlink is not None
    assert cell.hyperlink.location.startswith("'Img 1")


def test_summary_charts_sheet_has_charts(tmp_path):
    model = _build_model(tmp_path, n=3)
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    ws = wb["Summary Charts"]
    # area hist, diameter hist, per-image mean-diameter bar, grain-count bar
    assert len(ws._charts) == 4


def test_chart_options_disabling_area_removes_its_chart_and_rows(tmp_path):
    """UX-14: only 3 charts (diameter hist + the 2 per-image bars) when the
    area distribution is switched off."""
    model = _build_model(tmp_path, n=3)
    model.chart_options = {"area": {"enabled": False}}
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    ws = openpyxl.load_workbook(out)["Summary Charts"]
    assert len(ws._charts) == 3
    values = [c.value for row in ws.iter_rows() for c in row if c.value]
    assert "Grain Area Distribution" not in values


def test_chart_options_normal_fit_off_removes_fit_column_and_line(tmp_path):
    model = _build_model(tmp_path, n=3)
    model.chart_options = {"normal_fit": False}
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    ws = openpyxl.load_workbook(out)["Summary Charts"]
    values = [c.value for row in ws.iter_rows() for c in row if c.value]
    assert "Normal Fit" not in values
    # still both distributions + the 2 per-image bars, just no fit line series
    assert len(ws._charts) == 4


def test_chart_options_custom_title_used_as_chart_title(tmp_path):
    model = _build_model(tmp_path, n=3)
    model.chart_options = {"diameter": {"title": "Diameter — coarse fraction"}}
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    ws = openpyxl.load_workbook(out)["Summary Charts"]
    titles = [c.title.tx.rich.p[0].r[0].t for c in ws._charts if c.title is not None]
    assert any("coarse fraction" in t for t in titles)


def _diameter_hist_total(xlsx_path: str) -> float:
    """Sum of the "Count" series cached in the diameter distribution chart
    (charts[1]: area hist, diameter hist, per-image mean-diameter bar,
    grain-count bar -- see test_summary_charts_sheet_has_charts)."""
    ws = openpyxl.load_workbook(xlsx_path)["Summary Charts"]
    assert len(ws._charts) == 4, "diameter histogram chart missing -- filter left no data"
    chart = ws._charts[1]
    assert "Diameter" in chart.x_axis.title.tx.rich.p[0].r[0].t
    pts = chart.series[0].val.numRef.numCache.pt
    return sum(pt.v for pt in pts)


def test_chart_options_min_max_restricts_binned_grains(tmp_path):
    """A tight [min, max] on the diameter chart bins fewer grains than the
    unrestricted default -- proves the option actually filters the data."""
    model = _build_model(tmp_path, n=3)
    out_full = str(tmp_path / "full.xlsx")
    render_excel(model, out_full)
    full_total = _diameter_hist_total(out_full)
    assert full_total == sum(i.grain_count for i in model.images)

    model.chart_options = {"diameter": {"min": 0, "max": 7}}
    out_narrow = str(tmp_path / "narrow.xlsx")
    render_excel(model, out_narrow)
    narrow_total = _diameter_hist_total(out_narrow)
    assert narrow_total < full_total


def test_raw_sheets_have_autofilter_and_freeze_panes(tmp_path):
    model = _build_model(tmp_path, n=2)
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    raw_sheets = [n for n in wb.sheetnames if n.startswith("Raw")]
    for name in raw_sheets:
        ws = wb[name]
        assert ws.auto_filter.ref is not None
        assert ws.freeze_panes == "A3"
        header = [c.value for c in next(ws.iter_rows(min_row=2, max_row=2))]
        assert any("µm" in (h or "") for h in header)


def test_uncalibrated_raw_sheet_uses_px_units(tmp_path):
    model = _build_model(tmp_path, n=1, px_per_um=0.0)
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    raw_name = [n for n in wb.sheetnames if n.startswith("Raw")][0]
    ws = wb[raw_name]
    header = [c.value for c in next(ws.iter_rows(min_row=2, max_row=2))]
    assert any("px" in (h or "") for h in header)


def test_disabling_section_removes_summary_charts_sheet(tmp_path):
    model = _build_model(tmp_path, n=2)
    model.get_section("combined_distribution").enabled = False
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    assert "Summary Charts" not in wb.sheetnames


def test_excluding_image_removes_its_sheets(tmp_path):
    model = _build_model(tmp_path, n=3)
    model.images[1].include = False
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    img_sheets = [n for n in wb.sheetnames if n.startswith("Img ")]
    raw_sheets = [n for n in wb.sheetnames if n.startswith("Raw")]
    assert len(img_sheets) == 2
    assert len(raw_sheets) == 2
    ws = wb["Overview"]
    data_rows = [r for r in ws.iter_rows(min_row=5, max_row=8, values_only=True) if r[1]]
    assert len(data_rows) == 3  # 2 images + combined


def test_reorder_images_changes_overview_and_sheet_order(tmp_path):
    model = _build_model(tmp_path, n=3)
    # Reverse display order: img_1(1)->3, img_2(2)->2, img_3(3)->1, so
    # img_3 (synth_2.png) should now render first everywhere.
    for img in model.images:
        img.order = 4 - img.order
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    ws = wb["Overview"]
    first_data_row = next(ws.iter_rows(min_row=5, max_row=5, values_only=True))
    assert first_data_row[0] == 1  # lowest order value now shown first
    assert "synth_2" in first_data_row[1]
    img_sheets = [n for n in wb.sheetnames if n.startswith("Img ")]
    assert img_sheets[0].startswith("Img 1") and "synth_2" in img_sheets[0]


def test_edited_caption_appears_on_image_sheet(tmp_path):
    model = _build_model(tmp_path, n=1)
    model.images[0].caption = "A very distinctive caption"
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    img_sheet = [n for n in wb.sheetnames if n.startswith("Img ")][0]
    ws = wb[img_sheet]
    found = any(
        "A very distinctive caption" in str(cell.value)
        for row in ws.iter_rows() for cell in row if cell.value
    )
    assert found


def test_sheet_names_within_31_chars_and_unique(tmp_path):
    model = _build_model(tmp_path, n=2, seeds=[1, 2])
    # Force a long, collision-prone basename (renderer degrades gracefully
    # when the path itself doesn't resolve to a real image — only sheet
    # naming is under test here).
    for i, img in enumerate(model.images):
        img.image_path = str(tmp_path / f"{'a' * 60}_{i}.png")
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    assert all(len(n) <= 31 for n in wb.sheetnames)
    assert len(wb.sheetnames) == len(set(wb.sheetnames))


def test_no_temp_files_leaked(tmp_path):
    model = _build_model(tmp_path, n=2)
    tmpdir = tempfile.gettempdir()
    before = set(os.listdir(tmpdir))
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    after = set(os.listdir(tmpdir))
    # Allow unrelated pre-existing entries to change, but nothing new that
    # looks like our resize scratch dir should remain.
    leaked = [n for n in (after - before) if "grain_report_xlsx" in n]
    assert leaked == []


def _add_custom_text(model, order, title, body):
    sec = Section(id=f"text_{title}", type="custom_text", title=title, enabled=True,
                  order=order, payload={"body": body})
    model.sections.append(sec)
    return sec


# ---------------------------------------------------------------------------
# REP-08: designer edits the renderer must honour
# ---------------------------------------------------------------------------

def test_reordering_top_level_sections_reorders_sheets(tmp_path):
    """Move Methods before Summary Charts purely via ``Section.order``."""
    model = _build_model(tmp_path, n=2)
    model.get_section("parameters").order = 1.5   # between overview(1) and charts(2)
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    names = wb.sheetnames
    assert names.index("Methods") < names.index("Summary Charts")
    # Raw data is still strictly last, regardless of reordering elsewhere.
    raw_sheets = [n for n in names if n.startswith("Raw")]
    assert min(names.index(n) for n in raw_sheets) == len(names) - len(raw_sheets)


def test_renamed_section_titles_become_sheet_names(tmp_path):
    model = _build_model(tmp_path, n=1)
    model.get_section("combined_distribution").title = "Grain Size Trends"
    model.get_section("parameters").title = "Lab Procedure"
    model.get_section("overview_table").title = "Summary"
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    names = openpyxl.load_workbook(out).sheetnames
    assert "Summary" in names
    assert "Grain Size Trends" in names
    assert "Lab Procedure" in names
    assert "Summary Charts" not in names
    assert "Methods" not in names


def test_disabling_cover_and_overview_table(tmp_path):
    model = _build_model(tmp_path, n=1)
    model.get_section("cover").enabled = False
    model.get_section("overview_table").enabled = False
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    ws = wb["Overview"]
    # No banner, no table — the sheet exists but is essentially empty.
    values = [c.value for row in ws.iter_rows(min_row=1, max_row=4) for c in row]
    assert not any(v for v in values)


def test_disabling_overview_table_keeps_cover_banner(tmp_path):
    model = _build_model(tmp_path, n=1)
    model.get_section("overview_table").enabled = False
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    ws = openpyxl.load_workbook(out)["Overview"]
    assert model.title in [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]


@pytest.mark.parametrize("theme_id", list(PALETTES.keys()))
def test_each_palette_recolours_headers(tmp_path, theme_id):
    model = _build_model(tmp_path, n=1)
    model.theme = theme_id
    out = str(tmp_path / f"report_{theme_id}.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    ws = wb["Overview"]
    title_cell = ws.cell(row=1, column=1)
    fill_hex = "#" + title_cell.fill.fgColor.rgb[-6:]
    assert fill_hex == PALETTES[theme_id]["accent"]
    # Tab colour never changes with the palette — it stays kind-coded.
    assert "#" + ws.sheet_properties.tabColor.rgb[-6:] == TAB_COLORS["overview"]


def test_custom_palette_recolours_headers(tmp_path):
    """UX-15: a user-derived 3-colour palette drives the workbook exactly
    like a built-in one — the tab colour still stays kind-coded."""
    from reports.charts import derive_custom_palette
    model = _build_model(tmp_path, n=1)
    model.theme = "custom:custom-1"
    model.custom_palette = derive_custom_palette(["#123456", "#654321", "#00FF00"], "Lab palette")
    out = str(tmp_path / "report_custom.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    ws = wb["Overview"]
    title_cell = ws.cell(row=1, column=1)
    fill_hex = "#" + title_cell.fill.fgColor.rgb[-6:]
    assert fill_hex == model.custom_palette["accent"]
    assert "#" + ws.sheet_properties.tabColor.rgb[-6:] == TAB_COLORS["overview"]


def test_custom_text_sections_render_as_notes_sheets_in_order(tmp_path):
    model = _build_model(tmp_path, n=1)
    _add_custom_text(model, 1.1, "Sample Prep", "Etched per ASTM E407.")
    _add_custom_text(model, 1.2, "Acceptance Criteria", "Grain size must be G >= 5.")
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    names = wb.sheetnames
    assert "Notes - Sample Prep" in names
    assert "Notes - Acceptance Criteria" in names
    # Positioned between Overview and Summary Charts, per their order.
    assert names.index("Overview") < names.index("Notes - Sample Prep") < names.index("Notes - Acceptance Criteria")
    assert names.index("Notes - Acceptance Criteria") < names.index("Summary Charts")
    ws = wb["Notes - Sample Prep"]
    assert any("Etched per ASTM E407" in str(c.value) for row in ws.iter_rows() for c in row if c.value)
    assert "#" + ws.sheet_properties.tabColor.rgb[-6:] == TAB_COLORS["custom_text"]
    # Raw data is still last even though the notes sections were appended
    # after everything else in the model's section list.
    raw_sheets = [n for n in names if n.startswith("Raw")]
    assert min(names.index(n) for n in raw_sheets) == len(names) - len(raw_sheets)


def test_disabled_custom_text_section_is_not_rendered(tmp_path):
    model = _build_model(tmp_path, n=1)
    sec = _add_custom_text(model, 1.1, "Draft", "not ready")
    sec.enabled = False
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    names = openpyxl.load_workbook(out).sheetnames
    assert "Notes - Draft" not in names


def test_per_grain_notes_appear_as_note_column_in_raw_sheet(tmp_path):
    model = _build_model(tmp_path, n=1)
    model.images[0].grains[0]["note"] = "Possible twin boundary"
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    raw_name = [n for n in wb.sheetnames if n.startswith("Raw")][0]
    ws = wb[raw_name]
    header = [c.value for c in next(ws.iter_rows(min_row=2, max_row=2))]
    assert header[-1] == "Note"
    note_col = len(header)
    values = [r[note_col - 1] for r in ws.iter_rows(min_row=3, values_only=True)]
    assert "Possible twin boundary" in values


# ---------------------------------------------------------------------------
# HIER-01: user-defined folder hierarchy in the Excel renderer
# ---------------------------------------------------------------------------

_HIERARCHY = [
    {"key": "project", "label": "Job #", "value": "24-117"},
    {"key": "sample", "label": "Part Number", "value": "7718-A"},
    {"key": "lot", "label": "Lot", "value": "L-44A"},
]


def _build_hierarchy_model(tmp_path, images_dir, n=2, display_names=None, per_image_levels=None,
                            asset_dir=None):
    det = GrainDetector()
    items = []
    for i in range(n):
        gray, _ = make_mosaic(seed=i + 1, h=256, w=256, n_grains=30)
        bgr = np.repeat(gray[:, :, None], 3, axis=2)
        res = det.analyze(bgr, px_per_um=8.0, params=DetectionParams())
        img_path = os.path.join(images_dir, f"src_{i}.png")
        cv2.imwrite(img_path, bgr)
        item = ReportImageInput(image_path=img_path, result=res, image_bgr=bgr)
        if display_names:
            item.display_name = display_names[i]
        if per_image_levels and i in per_image_levels:
            item.levels = per_image_levels[i]
        items.append(item)
    return ReportModel.from_results(
        items, title="Job Report", hierarchy=_HIERARCHY,
        export_basename="24-117_7718-A_L-44A_Grain_Report_20260924",
        asset_dir=asset_dir or str(tmp_path / "assets"),
    )


def test_overview_headers_use_hierarchy_labels(tmp_path):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    model = _build_hierarchy_model(tmp_path, str(images_dir), n=2,
                                    display_names=["L-44A_01", "L-44A_02"])
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    ws = wb["Overview"]
    header = [c.value for c in next(ws.iter_rows(min_row=4, max_row=4))]
    assert "Job #" in header and "Part Number" in header and "Lot" in header
    assert "Sample" not in header and "Lot" != header[3]  # replaced, not appended
    assert "File" in header
    hier_line = ws["A3"].value
    assert hier_line == "Job #: 24-117 | Part Number: 7718-A | Lot: L-44A"
    row5 = list(ws.iter_rows(min_row=5, max_row=5, values_only=True))[0]
    assert "L-44A_01" in row5  # display_name, not the source filename
    assert "src_0" not in row5


def test_overview_image_links_to_sheet_and_relative_file_when_inside_job_folder(tmp_path):
    lot_dir = tmp_path / "24-117" / "7718-A" / "L-44A"
    images_dir = lot_dir / "images"
    exports_dir = lot_dir / "exports"
    images_dir.mkdir(parents=True)
    exports_dir.mkdir(parents=True)
    model = _build_hierarchy_model(tmp_path, str(images_dir), n=1, display_names=["L-44A_01"],
                                    asset_dir=str(images_dir))
    out = str(exports_dir / "report.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    ws = wb["Overview"]
    image_cell = ws["B5"]
    assert image_cell.hyperlink.location.startswith("'Img 1")
    file_cell = ws["C5"]
    target = file_cell.hyperlink.target
    assert target is not None
    assert not target.lower().startswith("http")
    assert not target.startswith("file:///")  # relative, survives moving the job folder
    assert ".." in target or os.sep in target


def test_overview_file_link_is_absolute_when_image_outside_job_folder(tmp_path):
    lot_dir = tmp_path / "24-117" / "7718-A" / "L-44A"
    exports_dir = lot_dir / "exports"
    exports_dir.mkdir(parents=True)
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    model = _build_hierarchy_model(tmp_path, str(images_dir), n=1, display_names=["L-44A_01"],
                                    asset_dir=str(images_dir))
    out = str(exports_dir / "report.xlsx")
    # Point straight at a UNC path (a different "drive" from the workbook's
    # local C: path) to force the "outside the job folder" branch reliably.
    model.images[0].image_path = r"\\fileserver\share\other\src_0.png"
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    file_cell = wb["Overview"]["C5"]
    target = file_cell.hyperlink.target
    assert target is not None
    assert not target.lower().startswith("http")
    assert "fileserver" in target


def test_multi_lot_report_shows_per_row_hierarchy_overrides(tmp_path):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    model = _build_hierarchy_model(
        tmp_path, str(images_dir), n=2, display_names=["A", "B"],
        per_image_levels={1: {"lot": "L-45B"}},
    )
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    ws = wb["Overview"]
    rows = list(ws.iter_rows(min_row=5, max_row=6, values_only=True))
    row_a = next(r for r in rows if "A" in r)
    row_b = next(r for r in rows if "B" in r)
    assert "L-44A" in row_a
    assert "L-45B" in row_b
    assert "L-44A" not in row_b


def test_methods_sheet_lists_hierarchy(tmp_path):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    model = _build_hierarchy_model(tmp_path, str(images_dir), n=1)
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    ws = openpyxl.load_workbook(out)["Methods"]
    values = [c.value for row in ws.iter_rows() for c in row if c.value]
    assert "Job #" in values and "24-117" in values


def test_per_image_and_raw_sheet_names_use_display_name(tmp_path):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    model = _build_hierarchy_model(tmp_path, str(images_dir), n=1, display_names=["Custom Display Name"])
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    names = openpyxl.load_workbook(out).sheetnames
    assert any("Custom Display Name" in n for n in names if n.startswith("Img "))
    assert any("Custom Display Name" in n for n in names if n.startswith("Raw"))


def test_legacy_model_without_hierarchy_renders_identically(tmp_path):
    """No hierarchy/display_name/export_basename set -> pre-HIER-01 output:
    same headers, same sheet names, no File column."""
    model = _build_model(tmp_path, n=2)
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    ws = wb["Overview"]
    header = [c.value for c in next(ws.iter_rows(min_row=4, max_row=4))]
    assert header[:4] == ["#", "Image", "Sample", "Lot"]
    assert "File" not in header
    # No hierarchy header line inserted above the table.
    assert ws["A3"].value is None
    names = wb.sheetnames
    assert names[0] == "Overview"
    img_sheets = [n for n in names if n.startswith("Img ")]
    assert all("synth_" in n for n in img_sheets)



# ---------------------------------------------------------------------------
# FIX-14: Overview column widths auto-size from content
# ---------------------------------------------------------------------------

def _build_model_with_ids(tmp_path, sample_ids, lot_numbers):
    det = GrainDetector()
    items = []
    for i, (sample, lot) in enumerate(zip(sample_ids, lot_numbers)):
        gray, _ = make_mosaic(seed=i + 1, h=256, w=256, n_grains=30)
        bgr = np.repeat(gray[:, :, None], 3, axis=2)
        res = det.analyze(bgr, px_per_um=8.0, params=DetectionParams())
        img_path = str(tmp_path / f"synth_{i}.png")
        cv2.imwrite(img_path, bgr)
        items.append(ReportImageInput(image_path=img_path, result=res, image_bgr=bgr,
                                       sample_id=sample, lot_number=lot))
    return ReportModel.from_results(items, title="Widths", asset_dir=str(tmp_path / "assets"))


def test_long_lot_number_is_not_truncated(tmp_path):
    # Realistic-but-long lot number -- well past the old fixed width of 12,
    # comfortably under the auto-size clamp, so the fix must widen it fully.
    long_lot = "AEROSPACE-INCONEL718-B02"  # 24 chars
    model = _build_model_with_ids(tmp_path, ["S0"], [long_lot])
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    ws = wb["Overview"]
    assert ws["D5"].value == long_lot
    lot_width = ws.column_dimensions["D"].width
    assert lot_width >= len(long_lot)  # wide enough to show the full value
    assert lot_width <= 40  # still clamped, doesn't blow out the sheet


def test_long_sample_id_is_not_truncated(tmp_path):
    long_sample = "SAMPLE-ID-WITH-LONG-NAME-001"  # 29 chars
    model = _build_model_with_ids(tmp_path, [long_sample], ["L1"])
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    ws = wb["Overview"]
    sample_width = ws.column_dimensions["C"].width
    assert sample_width >= len(long_sample)
    assert sample_width <= 40


def test_pathologically_long_lot_number_is_clamped_not_unbounded(tmp_path):
    """A single extreme outlier must not blow the column out to its full
    length -- it's clamped to a sensible max (still far wider than the old
    fixed 12, so real lot numbers up to ~28 chars still fit in full)."""
    huge_lot = "L" * 200
    model = _build_model_with_ids(tmp_path, ["S0"], [huge_lot])
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    ws = wb["Overview"]
    lot_width = ws.column_dimensions["D"].width
    # xlsxwriter's stored width goes through a pixel round-trip so it isn't
    # byte-identical to the "30" we asked for (openpyxl reads it back as
    # ~30.7) -- assert the clamp held (nowhere near the 200-char content)
    # rather than an exact character count.
    assert 12 < lot_width < 35


def test_short_lot_and_sample_get_sensible_minimum_width(tmp_path):
    model = _build_model_with_ids(tmp_path, ["S0"], ["L1"])
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    ws = wb["Overview"]
    assert ws.column_dimensions["C"].width >= 10
    assert ws.column_dimensions["D"].width >= 10


def test_hierarchy_level_column_widths_size_to_long_values(tmp_path):
    hierarchy = [
        {"key": "project", "label": "Job #", "value": "24-117"},
        {"key": "sample", "label": "Part Number", "value": "7718-A"},
        {"key": "lot", "label": "Lot", "value": "LOT-VERY-LONG-IDENTIFIER-STRING-2026-0917"},
    ]
    det = GrainDetector()
    gray, _ = make_mosaic(seed=1, h=256, w=256, n_grains=30)
    bgr = np.repeat(gray[:, :, None], 3, axis=2)
    res = det.analyze(bgr, px_per_um=8.0, params=DetectionParams())
    img_path = str(tmp_path / "synth_0.png")
    cv2.imwrite(img_path, bgr)
    items = [ReportImageInput(image_path=img_path, result=res, image_bgr=bgr)]
    model = ReportModel.from_results(items, title="Hier Widths", hierarchy=hierarchy,
                                      asset_dir=str(tmp_path / "assets"))
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    ws = wb["Overview"]
    # lead columns for hierarchy models: #, Image, File, Job #, Part Number, Lot
    lot_col_width = ws.column_dimensions["F"].width
    assert lot_col_width >= 30  # long value, clamped at max_w=30 for level columns


# ---------------------------------------------------------------------------
# FIX-07: calibration (INN-29 scale-verification payload) rendered in Excel
# ---------------------------------------------------------------------------

_CAL_PAYLOAD = {
    "source": "metadata", "px_per_um": 5.0, "check": None, "status": "not verified",
    "reason": "no check on file", "warnings": ["stale check"],
    "text": "Scale not verified (no check on file)",
}


def test_calibration_block_appears_on_methods_sheet(tmp_path):
    det = GrainDetector()
    gray, _ = make_mosaic(seed=1, h=256, w=256, n_grains=30)
    bgr = np.repeat(gray[:, :, None], 3, axis=2)
    res = det.analyze(bgr, px_per_um=8.0, params=DetectionParams())
    img_path = str(tmp_path / "synth_0.png")
    cv2.imwrite(img_path, bgr)
    items = [ReportImageInput(image_path=img_path, result=res, image_bgr=bgr)]
    model = ReportModel.from_results(items, title="Cal", asset_dir=str(tmp_path / "assets"),
                                      calibration=_CAL_PAYLOAD)
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    ws = openpyxl.load_workbook(out)["Methods"]
    values = [c.value for row in ws.iter_rows() for c in row if c.value]
    assert "Scale Verification" in values
    assert "Scale not verified (no check on file)" in values
    assert "Verification Warnings" in values
    assert "stale check" in values


def test_no_calibration_block_when_calibration_unset(tmp_path):
    model = _build_model(tmp_path, n=1)
    assert model.calibration is None
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    ws = openpyxl.load_workbook(out)["Methods"]
    values = [c.value for row in ws.iter_rows() for c in row if c.value]
    assert "Scale Verification" not in values


def _embedded_image_at_col(ws, col: int):
    for im in ws._images:
        if im.anchor._from.col == col:
            return cv2.imdecode(np.frombuffer(im._data(), np.uint8), cv2.IMREAD_COLOR)
    return None


def test_overlay_opacity_zero_fades_overlay_to_plain_image(tmp_path):
    """UX-16: opacity 0 blends the overlay all the way down to the plain
    original image (still shown side-by-side at its own column)."""
    model = _build_model(tmp_path, n=1)
    model.overlay_opacity = 0.0
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    orig_arr = cv2.imread(model.images[0].image_path, cv2.IMREAD_COLOR)
    ws = openpyxl.load_workbook(out)[[n for n in openpyxl.load_workbook(out).sheetnames
                                      if n.startswith("Img ")][0]]
    overlay_embedded = _embedded_image_at_col(ws, 6)
    assert overlay_embedded is not None
    assert overlay_embedded.shape == orig_arr.shape
    assert np.array_equal(overlay_embedded, orig_arr)


def test_overlay_opacity_default_keeps_overlay_colouring(tmp_path):
    """The pre-UX-16 default (1.0) embeds the overlay exactly as detected —
    it must NOT be blended down to the plain image."""
    model = _build_model(tmp_path, n=1)
    assert model.overlay_opacity == 1.0
    out = str(tmp_path / "report.xlsx")
    render_excel(model, out)
    orig_arr = cv2.imread(model.images[0].image_path, cv2.IMREAD_COLOR)
    ws = openpyxl.load_workbook(out)[[n for n in openpyxl.load_workbook(out).sheetnames
                                      if n.startswith("Img ")][0]]
    overlay_embedded = _embedded_image_at_col(ws, 6)
    assert overlay_embedded is not None
    assert not np.array_equal(overlay_embedded, orig_arr)


def test_sample_output_written_to_scratch():
    """Definition-of-done artifact for the coordinator to open."""
    scratch = os.path.join(ROOT, "scratch", "reports")
    os.makedirs(scratch, exist_ok=True)
    tmp_root = scratch  # images/assets can live alongside the sample output
    det = GrainDetector()
    items = []
    for i, seed in enumerate([11, 12, 13]):
        gray, _ = make_mosaic(seed=seed, h=256, w=256, n_grains=30)
        bgr = np.repeat(gray[:, :, None], 3, axis=2)
        res = det.analyze(bgr, px_per_um=8.0, params=DetectionParams())
        img_path = os.path.join(tmp_root, f"sample_src_{i}.png")
        cv2.imwrite(img_path, bgr)
        items.append(ReportImageInput(image_path=img_path, result=res, image_bgr=bgr,
                                       sample_id=f"S{i}", lot_number="L1"))
    model = ReportModel.from_results(
        items, title="Sample Grain Report", operator="Jack", organization="Acme",
        metadata={"detection_mode": "boundary"}, asset_dir=os.path.join(tmp_root, "assets"),
    )
    model.theme = "slate_teal"
    model.images[0].grains[0]["note"] = "Edge artifact — verify"
    _add_custom_text(model, 1.1, "Sample Preparation", "Mounted, polished, etched per ASTM E407.")
    _add_custom_text(model, 20_001, "Conclusions", "Grain size meets the acceptance criterion.")
    out = os.path.join(scratch, "sample.xlsx")
    render_excel(model, out)
    assert os.path.exists(out)
    names = openpyxl.load_workbook(out).sheetnames
    assert "Notes - Sample Preparation" in names
    assert "Notes - Conclusions" in names
