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
