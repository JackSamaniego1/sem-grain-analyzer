"""Tests for reports.multi_lot.build_multi_lot_report_model (UX-13): a
Job > Part > Lot > images hierarchy, per-part lot comparison (reusing
core.lot_compare), raw data with Job/Part/Lot, and single-lot/single-part
reports still rendering cleanly."""
import os
import sys

import numpy as np
import openpyxl
import pytest
from pptx import Presentation

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import cv2

from tests.conftest import make_mosaic
from core.grain_detector import GrainDetector, DetectionParams
from reports.charts import TAB_COLORS
from reports.model import ReportImageInput
from reports.multi_lot import LotGroup, build_multi_lot_report_model
from reports.excel_renderer import render_excel
from reports.pptx_renderer import render_pptx


def _items(tmp_path, n, seed0, px_per_um=8.0):
    det = GrainDetector()
    items = []
    for i in range(n):
        seed = seed0 + i
        gray, _ = make_mosaic(seed=seed, h=200, w=200, n_grains=20)
        bgr = np.repeat(gray[:, :, None], 3, axis=2)
        res = det.analyze(bgr, px_per_um=px_per_um, params=DetectionParams())
        p = str(tmp_path / f"synth_{seed}.png")
        cv2.imwrite(p, bgr)
        items.append(ReportImageInput(image_path=p, result=res, image_bgr=bgr))
    return items


def _two_part_groups(tmp_path):
    return [
        LotGroup(job="24-117", part="7718-A", lot="L-1", items=_items(tmp_path, 2, 1)),
        LotGroup(job="24-117", part="7718-A", lot="L-2", items=_items(tmp_path, 2, 10)),
        LotGroup(job="24-117", part="7718-B", lot="L-1", items=_items(tmp_path, 2, 20)),
        LotGroup(job="24-117", part="7718-B", lot="L-2", items=_items(tmp_path, 2, 30)),
    ]


def _model(tmp_path, groups, **kw):
    return build_multi_lot_report_model(
        groups, title="Job 24-117", operator="Jack", organization="Acme",
        asset_dir=str(tmp_path / "assets"), **kw)


# ---------------------------------------------------------------------------
# Model shape
# ---------------------------------------------------------------------------

def test_builds_one_image_per_item_tagged_with_job_part_lot(tmp_path):
    groups = _two_part_groups(tmp_path)
    model = _model(tmp_path, groups)
    assert len(model.images) == 8
    assert model.hierarchy == [{"key": "job", "label": "Job #"},
                               {"key": "part", "label": "Part Number"},
                               {"key": "lot", "label": "Lot"}]
    tags = {(i.levels["job"], i.levels["part"], i.levels["lot"]) for i in model.images}
    assert tags == {("24-117", "7718-A", "L-1"), ("24-117", "7718-A", "L-2"),
                    ("24-117", "7718-B", "L-1"), ("24-117", "7718-B", "L-2")}
    # legacy sample_id/lot_number columns also populated
    assert {i.sample_id for i in model.images} == {"7718-A", "7718-B"}


def test_does_not_mutate_caller_report_image_inputs(tmp_path):
    items = _items(tmp_path, 1, 1)
    original_levels = dict(items[0].levels)
    _model(tmp_path, [LotGroup(job="J", part="P", lot="L1", items=items)])
    assert items[0].levels == original_levels
    assert items[0].sample_id == ""


def test_sample_statistics_one_per_lot_labelled_with_part_when_multi_part(tmp_path):
    groups = _two_part_groups(tmp_path)
    model = _model(tmp_path, groups)
    labels = {s["label"] for s in model.sample_statistics}
    assert labels == {"7718-A / L-1", "7718-A / L-2", "7718-B / L-1", "7718-B / L-2"}


def test_sample_statistics_label_is_plain_lot_for_a_single_part(tmp_path):
    groups = [g for g in _two_part_groups(tmp_path) if g.part == "7718-A"]
    model = _model(tmp_path, groups)
    labels = {s["label"] for s in model.sample_statistics}
    assert labels == {"L-1", "L-2"}          # no "part / " prefix -- only one part


def test_lot_comparison_section_right_after_overview_raw_data_still_last(tmp_path):
    model = _model(tmp_path, _two_part_groups(tmp_path))
    ordered = sorted(model.sections, key=lambda s: s.order)
    types = [s.type for s in ordered]
    assert types[0] == "cover"
    assert types[1] == "overview_table"
    assert types[2] == "lot_comparison"
    assert types[-1] == "raw_data"
    assert model.validate() == []


def test_single_group_single_lot_single_part_renders_cleanly(tmp_path):
    groups = [LotGroup(job="24-117", part="7718-A", lot="L-1", items=_items(tmp_path, 2, 1))]
    model = _model(tmp_path, groups)
    assert len(model.images) == 2
    assert model.validate() == []
    sec = model.get_section("lot_comparison")
    assert len(sec.payload["parts"]) == 1
    assert sec.payload["parts"][0]["lots"] == ["L-1"]


def test_empty_groups_raises():
    with pytest.raises(ValueError):
        build_multi_lot_report_model([])


def test_baseline_only_applies_to_the_named_part(tmp_path):
    groups = _two_part_groups(tmp_path)
    model = _model(tmp_path, groups, baseline={"7718-A": "L-1"})
    parts = {p["part"]: p for p in model.get_section("lot_comparison").payload["parts"]}
    assert parts["7718-A"]["baseline"] == "L-1"
    assert parts["7718-A"]["comparison"]["equivalence"]      # has TOST verdicts
    assert parts["7718-B"]["baseline"] is None
    assert parts["7718-B"]["comparison"]["equivalence"] == {}


def test_unknown_baseline_label_is_ignored_not_an_error(tmp_path):
    groups = [g for g in _two_part_groups(tmp_path) if g.part == "7718-A"]
    model = _model(tmp_path, groups, baseline={"7718-A": "no-such-lot"})
    part = model.get_section("lot_comparison").payload["parts"][0]
    assert part["baseline"] is None


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------

def test_excel_lot_comparison_sheet_right_after_overview_with_summary_tab_colour(tmp_path):
    model = _model(tmp_path, _two_part_groups(tmp_path), baseline={"7718-A": "L-1"})
    out = str(tmp_path / "multi.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames[0] == "Overview"
    assert wb.sheetnames[1] == "Lot Comparison"
    ws = wb["Lot Comparison"]
    assert "#" + ws.sheet_properties.tabColor.rgb[-6:] == TAB_COLORS["summary"]
    raw = [i for i, n in enumerate(wb.sheetnames) if n.startswith("Raw - ")]
    assert raw == list(range(len(wb.sheetnames) - len(raw), len(wb.sheetnames)))


def test_excel_overview_table_has_job_part_lot_columns(tmp_path):
    model = _model(tmp_path, _two_part_groups(tmp_path))
    out = str(tmp_path / "multi.xlsx")
    render_excel(model, out)
    ws = openpyxl.load_workbook(out)["Overview"]
    rows = [[c.value for c in row] for row in ws.iter_rows()]
    header_row = next(r for r in rows if "Job #" in r)
    assert "Part Number" in header_row and "Lot" in header_row


def test_excel_lot_comparison_sheet_has_both_parts_and_delta_matrix(tmp_path):
    model = _model(tmp_path, _two_part_groups(tmp_path), baseline={"7718-A": "L-1"})
    out = str(tmp_path / "multi.xlsx")
    render_excel(model, out)
    ws = openpyxl.load_workbook(out)["Lot Comparison"]
    values = [c.value for row in ws.iter_rows() for c in row if c.value]
    assert any("7718-A" in str(v) for v in values)
    assert any("7718-B" in str(v) for v in values)
    assert any("ΔG matrix" in str(v) for v in values)               # 7718-B: no baseline
    assert any("Equivalence vs baseline (L-1)" in str(v) for v in values)  # 7718-A: has one


def test_excel_delta_matrix_cells_are_band_coloured(tmp_path):
    model = _model(tmp_path, _two_part_groups(tmp_path))
    out = str(tmp_path / "multi.xlsx")
    render_excel(model, out)
    ws = openpyxl.load_workbook(out)["Lot Comparison"]
    # every numeric (non-header, non-label) cell in the sheet should carry a
    # fill colour (band/cell formats always set bg_color) -- spot check a
    # handful of cells known to hold ΔG matrix values.
    fills = [c.fill.fgColor.rgb for row in ws.iter_rows() for c in row
            if isinstance(c.value, (int, float))]
    assert fills and all(f not in (None, "00000000") for f in fills)


def test_single_part_report_has_no_delta_matrix_or_equivalence_text(tmp_path):
    groups = [LotGroup(job="24-117", part="7718-A", lot="L-1", items=_items(tmp_path, 2, 1))]
    model = _model(tmp_path, groups)
    out = str(tmp_path / "single.xlsx")
    render_excel(model, out)
    wb = openpyxl.load_workbook(out)
    assert "Lot Comparison" in wb.sheetnames
    values = [c.value for row in wb["Lot Comparison"].iter_rows() for c in row if c.value]
    assert not any("ΔG matrix" in str(v) for v in values)
    assert not any("Equivalence" in str(v) for v in values)


# ---------------------------------------------------------------------------
# PowerPoint
# ---------------------------------------------------------------------------

def _slide_titles(prs):
    out = []
    for s in prs.slides:
        texts = [sh.text_frame.text for sh in s.shapes if sh.has_text_frame and sh.text_frame.text]
        out.append(texts[0] if texts else "")
    return out


def test_pptx_one_lot_comparison_slide_per_part_right_after_exec_summary(tmp_path):
    model = _model(tmp_path, _two_part_groups(tmp_path), baseline={"7718-A": "L-1"})
    out = str(tmp_path / "multi.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    titles = _slide_titles(prs)
    assert titles[0] == model.title
    assert titles[1] == "Executive Summary"
    assert "Lot Comparison" in titles[2] and "7718-A" in titles[2]
    assert "Lot Comparison" in titles[3] and "7718-B" in titles[3]


def test_pptx_baseline_part_gets_equivalence_table_other_gets_matrix(tmp_path):
    model = _model(tmp_path, _two_part_groups(tmp_path), baseline={"7718-A": "L-1"})
    out = str(tmp_path / "multi.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    slide_a, slide_b = list(prs.slides)[2], list(prs.slides)[3]
    text_a = " ".join(sh.text_frame.text for sh in slide_a.shapes if sh.has_text_frame)
    text_b = " ".join(sh.text_frame.text for sh in slide_b.shapes if sh.has_text_frame)
    assert "Equivalence vs baseline" in text_a
    assert "ΔG matrix" in text_b
    assert sum(1 for sh in slide_a.shapes if sh.has_table) == 2
    assert sum(1 for sh in slide_b.shapes if sh.has_table) == 2


def test_pptx_single_group_renders_without_crashing(tmp_path):
    groups = [LotGroup(job="24-117", part="7718-A", lot="L-1", items=_items(tmp_path, 2, 1))]
    model = _model(tmp_path, groups)
    out = str(tmp_path / "single.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    titles = _slide_titles(prs)
    assert any("Lot Comparison" in t for t in titles)
    assert "Appendix" in titles[-1]


def test_disabling_lot_comparison_section_removes_its_slides_and_sheet(tmp_path):
    model = _model(tmp_path, _two_part_groups(tmp_path))
    model.get_section("lot_comparison").enabled = False
    out_x = str(tmp_path / "off.xlsx")
    render_excel(model, out_x)
    assert "Lot Comparison" not in openpyxl.load_workbook(out_x).sheetnames

    out_p = str(tmp_path / "off.pptx")
    render_pptx(model, out_p)
    titles = _slide_titles(Presentation(out_p))
    assert not any("Lot Comparison" in t for t in titles)
