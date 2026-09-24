"""HIER-04: acceptance check that Excel/PowerPoint exports meet the HIER-01
spec (handoff/specs/HIER-01.md) -- hierarchy labels flow from a workspace's
``HierarchyProfile`` (default AND custom, renamed levels) all the way
through to the rendered workbook/deck, and the default export filename
matches ``render_template(export_name_template, context, for_filename=True)``.

This exercises the real production path (``data.hierarchy`` +
``data.workspace`` + ``ui.hierarchy_ui`` helpers, which are plain-Python, no
Qt -- see their module docstrings) rather than hand-building the
``ReportModel.hierarchy`` list the way the renderer-focused unit tests in
``test_report_excel.py``/``test_report_pptx.py`` do.
"""
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
from data.hierarchy import PRESETS, context_for_session
from data.models import ImageEntry
from data.session_io import save_session
from data.workspace import Workspace
from reports import suggest_filename
from reports.excel_renderer import render_excel
from reports.model import ReportImageInput, ReportModel
from reports.pptx_renderer import render_pptx
from ui.hierarchy_ui import export_basename, hierarchy_rows, report_title


def _all_text(slide) -> str:
    out = []
    for shape in slide.shapes:
        if shape.has_text_frame:
            out.append(shape.text_frame.text)
        if shape.has_table:
            for row in shape.table.rows:
                for cell in row.cells:
                    out.append(cell.text)
    return "\n".join(out)


def _analyzed_items(tmp_path, images_dir, n=3):
    det = GrainDetector()
    items = []
    for i in range(n):
        gray, _ = make_mosaic(seed=i + 1, h=256, w=256, n_grains=30)
        bgr = np.repeat(gray[:, :, None], 3, axis=2)
        res = det.analyze(bgr, px_per_um=8.0, params=DetectionParams())
        img_path = os.path.join(images_dir, f"raw_{i}.png")
        cv2.imwrite(img_path, bgr)
        items.append(ReportImageInput(image_path=img_path, result=res, image_bgr=bgr))
    return items


def _build_lot(tmp_path, profile, job="24-117", part="7718-A", lot="L-44A", **level_meta):
    ws = Workspace(tmp_path)
    ws.set_profile(profile)
    job_path = ws.create_project(job)
    part_path = ws.create_sample(job_path, part)
    lot_path = ws.create_lot(job_path, part_path, lot, **level_meta)
    entries = [ImageEntry(image_bgr=np.zeros((8, 8, 3), np.uint8), filename=f"src_{i}.png")
               for i in range(3)]
    save_session(lot_path, {"project": job, "sample_id": part, "lot_number": lot}, entries,
                 in_place=True)
    return ws, lot_path


# ---------------------------------------------------------------------------
# Acceptance line 1: new workspace / job_part_lot folder layout with images
# ---------------------------------------------------------------------------

def test_job_part_lot_folder_layout_matches_spec(tmp_path):
    ws, lot_path = _build_lot(tmp_path, PRESETS["job_part_lot"])
    assert lot_path == tmp_path / "24-117" / "7718-A" / "L-44A"
    assert (lot_path / "images").is_dir()
    assert (lot_path / "results").is_dir()
    assert (lot_path / "manifest.json").exists()


# ---------------------------------------------------------------------------
# Acceptance line 2: default preset -> Excel/PPTX labels + export basename
# ---------------------------------------------------------------------------

def test_default_preset_labels_and_basename_match_spec(tmp_path):
    profile = PRESETS["job_part_lot"]
    ws, lot_path = _build_lot(tmp_path, profile)

    ctx = context_for_session(lot_path, profile)
    ctx["date"] = ctx["date"].replace(year=2026, month=9, day=24)  # pin the date, like the spec example
    hierarchy = hierarchy_rows(profile, ctx)
    basename = export_basename(profile, ctx)
    assert basename == "24-117_7718-A_L-44A_Grain_Report_20260924"

    images_dir = tmp_path / "src_images"
    images_dir.mkdir()
    items = _analyzed_items(tmp_path, str(images_dir))
    model = ReportModel.from_results(
        items, title=report_title(profile, ctx), hierarchy=hierarchy, export_basename=basename,
        asset_dir=str(tmp_path / "assets"),
    )

    xlsx_name = suggest_filename(model, "xlsx")
    pptx_name = suggest_filename(model, "pptx")
    assert xlsx_name == "24-117_7718-A_L-44A_Grain_Report_20260924.xlsx"
    assert pptx_name == "24-117_7718-A_L-44A_Grain_Report_20260924.pptx"

    xlsx_path = str(tmp_path / xlsx_name)
    pptx_path = str(tmp_path / pptx_name)
    render_excel(model, xlsx_path)
    render_pptx(model, pptx_path)

    wb = openpyxl.load_workbook(xlsx_path)
    ws_over = wb["Overview"]
    header = [c.value for c in next(ws_over.iter_rows(min_row=4, max_row=4))]
    assert header[:3] == ["#", "Image", "File"]
    assert "Job #" in header and "Part Number" in header and "Lot" in header
    assert "Sample" not in header
    assert ws_over["A3"].value == "Job #: 24-117 | Part Number: 7718-A | Lot: L-44A"

    prs = Presentation(pptx_path)
    title_text = _all_text(prs.slides[0])
    assert "Job #: 24-117" in title_text
    assert "Part Number: 7718-A" in title_text
    assert "Lot: L-44A" in title_text


# ---------------------------------------------------------------------------
# Acceptance line 3: renaming levels updates the next export (no restart)
# ---------------------------------------------------------------------------

def test_renamed_levels_and_custom_field_flow_into_next_export(tmp_path):
    profile = PRESETS["job_part_lot"]
    profile.level("project").label = "Work Order"
    profile.level("sample").label = "Component"
    profile.level("lot").label = "Heat Batch"
    # a level field with a filesystem-unsafe character, to check the export
    # basename is sanitized for_filename while the on-slide/on-sheet text is
    # left exactly as entered.
    profile.export_name_template = "{project}_{sample}_{lot}_{lot_heat_number}_Grain_Report_{date:%Y%m%d}"

    ws, lot_path = _build_lot(tmp_path, profile, heat_number="H/2201:A")

    reloaded = ws.reload_profile()  # "next export" re-reads workspace.json, no restart
    assert reloaded.level("project").label == "Work Order"

    ctx = context_for_session(lot_path, reloaded)
    ctx["date"] = ctx["date"].replace(year=2026, month=9, day=24)
    hierarchy = hierarchy_rows(reloaded, ctx)
    basename = export_basename(reloaded, ctx)
    # sanitize_name turns "/" and ":" into "_"; no double underscores remain.
    assert basename == "24-117_7718-A_L-44A_H_2201_A_Grain_Report_20260924"
    assert "/" not in basename and ":" not in basename

    images_dir = tmp_path / "src_images"
    images_dir.mkdir()
    items = _analyzed_items(tmp_path, str(images_dir), n=1)
    model = ReportModel.from_results(
        items, title=report_title(reloaded, ctx), hierarchy=hierarchy, export_basename=basename,
        asset_dir=str(tmp_path / "assets"),
    )

    xlsx_path = str(tmp_path / (suggest_filename(model, "xlsx")))
    pptx_path = str(tmp_path / (suggest_filename(model, "pptx")))
    render_excel(model, xlsx_path)
    render_pptx(model, pptx_path)

    wb = openpyxl.load_workbook(xlsx_path)
    header = [c.value for c in next(wb["Overview"].iter_rows(min_row=4, max_row=4))]
    assert "Work Order" in header and "Component" in header and "Heat Batch" in header
    assert "Job #" not in header

    prs = Presentation(pptx_path)
    exec_table = [sh for sh in prs.slides[1].shapes if sh.has_table][0].table
    headers = [exec_table.cell(0, c).text for c in range(len(exec_table.columns))]
    assert "Work Order" in headers and "Component" in headers and "Heat Batch" in headers
    title_text = _all_text(prs.slides[0])
    assert "Work Order: 24-117" in title_text
    assert "Component: 7718-A" in title_text
    assert "Heat Batch: L-44A" in title_text
