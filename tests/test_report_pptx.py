"""Tests for reports.pptx_renderer.render_pptx: slide count/titles/native charts.

D-30 / REP-DESIGN-01: slide 2 (index 1) is now a part-level summary table
(one row per PART, averaged across its lots) + three native bar charts + the
restyled per-image data tables (grouped by part, max 14 rows/slide) --
replacing the old per-image "Executive Summary" table + Combined row + INN-27
KPI tiles. Most slide lookups below search by heading text rather than a
fixed slide index, since the new summary/chart/data-table slides shift
everything that used to sit at a hardcoded index.
"""
import os
import sys
import tempfile

import numpy as np
from pptx import Presentation
from pptx.util import Emu

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import cv2

from tests.conftest import make_mosaic
from core.grain_detector import GrainDetector, DetectionParams
import pytest

from reports.model import ReportModel, ReportImageInput, Section
from reports.pptx_renderer import (
    render_pptx, SLIDE_H, MAX_SUMMARY_ROWS, MAX_DATA_ROWS, MAX_PARTS_COMBINED,
    MARGIN_IN, FOOTER_MAX_CHARS, _part_summary_rows, _chunk, _plan_image_table_slides,
    _max_chars_for_width, _DATA_IMAGE_COL_IN, _footer_text, _percentile_rows,
    _lot_distribution_slide_plan,
)
from reports.charts import PALETTES, series_for

# Matches reports.pptx_renderer._add_footer's footer-bar geometry -- used by
# the FIX-11/FIX-12 tests to assert content never overlaps/overflows into it.
FOOTER_TOP_IN = SLIDE_H.inches - 0.32


def _build_model(tmp_path, n=3, px_per_um=8.0, seeds=None):
    """Each image gets a distinct ``sample_id`` -- with no hierarchy set,
    that means each image is its own PART with a single lot ("L1"). Fine for
    slide-count/callout tests; grouping-specific tests use
    ``_build_parts_model`` instead so "part"/"lot" mean something real."""
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
        items, title="Test Deck", operator="Jack", organization="Acme",
        metadata={"detection_mode": "boundary", "detection_params": {"min_grain_size_px": 50}},
        asset_dir=str(tmp_path / "assets"),
    )


def _overview_slide_count(model, images):
    """Slides contributed by the "overview_table" section -- the part
    summary (+ 3 bar charts) slide(s), and the per-image data-table pages --
    computed with the same helpers the renderer itself uses, so this stays
    correct regardless of how many parts/lots a fixture has. <= 6 parts:
    table + charts share ONE slide. > 6 parts: the (possibly paginated)
    table stands alone, followed by one charts-only slide."""
    if not images:
        return 0
    rows, _au, _du = _part_summary_rows(model, images)
    if len(rows) <= MAX_PARTS_COMBINED:
        n = 1
    else:
        n = len(_chunk(rows, MAX_SUMMARY_ROWS)) + 1
    n += len(_chunk(_percentile_rows(model, images)[0], MAX_DATA_ROWS))
    n += len(_plan_image_table_slides(model, images))
    # UPDATE 4 item 17: per-lot distribution slides + lot-to-lot comparison
    n += len(_lot_distribution_slide_plan(model, images, series_for(model.theme, model.custom_palette)))
    return n


def _expected_slide_count(model, images, want_charts=True, want_methods=True):
    n = 1  # cover
    n += _overview_slide_count(model, images)
    n += (2 if want_charts else 0)
    n += len(images)  # one image (original + overlay) slide each
    n += (1 if want_methods else 0)
    n += 1  # appendix
    return n


def _build_mixed_model(tmp_path):
    """One calibrated image + one uncalibrated image (each its own part)."""
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


def _build_parts_model(tmp_path, n_parts=2, n_lots=2, n_images=2, px_per_um=8.0, seed_start=1,
                        h=96, w=96, n_grains=15):
    """A hierarchy-based fixture with real part/lot grouping: ``n_parts``
    parts, each with ``n_lots`` lots, each with ``n_images`` images."""
    det = GrainDetector()
    hierarchy = [{"key": "sample", "label": "Part Number", "value": ""},
                 {"key": "lot", "label": "Lot", "value": ""}]
    items = []
    seed = seed_start
    for p in range(n_parts):
        part = f"P{p + 1}"
        for l in range(n_lots):
            lot = f"P{p + 1}-L{l + 1}"
            for i in range(n_images):
                gray, _ = make_mosaic(seed=seed, h=h, w=w, n_grains=n_grains)
                bgr = np.repeat(gray[:, :, None], 3, axis=2)
                res = det.analyze(bgr, px_per_um=px_per_um, params=DetectionParams())
                img_path = str(tmp_path / f"p{p}_{l}_{i}_{seed}.png")
                cv2.imwrite(img_path, bgr)
                items.append(ReportImageInput(image_path=img_path, result=res, image_bgr=bgr,
                                              levels={"sample": part, "lot": lot}))
                seed += 1
    return ReportModel.from_results(items, title="Parts Report", hierarchy=hierarchy,
                                    asset_dir=str(tmp_path / "assets"))


def _all_text(slide):
    return "\n".join(
        run.text for shape in slide.shapes if shape.has_text_frame
        for p in shape.text_frame.paragraphs for run in p.runs)


def _heading(slide):
    lines = _all_text(slide).split("\n")
    return lines[0] if lines else ""


def _dist_slide(prs, kind):
    """The (fixed-heading, position-independent) combined-distribution
    slide for ``kind`` ("area"|"diameter") -- these now sit *after* the new
    part-summary/chart/data-table slides, not at a fixed index."""
    label = "Grain Area" if kind == "area" else "Grain Diameter"
    heading = f"Combined {label} Distribution"
    return next(s for s in prs.slides if _heading(s) == heading)


def _find_image_slide(prs, img):
    return next(s for s in prs.slides if _heading(s).startswith(f"Image {img.order}:"))


def _cell_value(text):
    """Extract the leading float from a 'NN.NN unit' table/callout string."""
    return float(text.split()[0])


# ---------------------------------------------------------------------------
# Per-image data-table rows (replaces the old per-image Executive Summary row
# tests -- each image below is its own part/lot, so its data table is a
# single-row slide titled after that part).
# ---------------------------------------------------------------------------

def test_uncalibrated_data_table_row_has_no_zero_size_stats(tmp_path):
    model = _build_model(tmp_path, n=1, px_per_um=0.0)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    slide = next(s for s in prs.slides if _heading(s) == "S0")
    table = [sh for sh in slide.shapes if sh.has_table][0].table
    mean_diam_text = table.cell(1, 4).text
    mean_area_text = table.cell(1, 6).text
    assert _cell_value(mean_diam_text) > 0
    assert _cell_value(mean_area_text) > 0
    assert "px" in mean_diam_text and "px" in mean_area_text


def test_mixed_calibration_data_table_rows_have_correct_units_and_no_zeros(tmp_path):
    model = _build_mixed_model(tmp_path)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    cal_slide = next(s for s in prs.slides if _heading(s) == "S0")
    uncal_slide = next(s for s in prs.slides if _heading(s) == "S1")
    cal_table = [sh for sh in cal_slide.shapes if sh.has_table][0].table
    uncal_table = [sh for sh in uncal_slide.shapes if sh.has_table][0].table
    cal_row = [cal_table.cell(1, c).text for c in range(len(cal_table.columns))]
    uncal_row = [uncal_table.cell(1, c).text for c in range(len(uncal_table.columns))]
    assert _cell_value(cal_row[4]) > 0 and "µm" in cal_row[4]
    assert _cell_value(uncal_row[4]) > 0 and "px" in uncal_row[4]
    assert _cell_value(cal_row[6]) > 0
    assert _cell_value(uncal_row[6]) > 0  # was 0.00 before the fix


def test_uncalibrated_image_slide_metric_callouts_have_no_zeros(tmp_path):
    model = _build_model(tmp_path, n=1, px_per_um=0.0)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    image_slide = _find_image_slide(prs, model.images[0])
    texts = [
        run.text for shape in image_slide.shapes if shape.has_text_frame
        for p in shape.text_frame.paragraphs for run in p.runs
    ]
    joined = " ".join(texts)
    assert "px" in joined
    zero_like = [t for t in texts if t.strip() in ("0.00 px", "0.00 px²")]
    assert zero_like == []


def test_slide_count_for_n_images(tmp_path):
    model = _build_model(tmp_path, n=3)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    assert len(prs.slides) == _expected_slide_count(model, model.ordered_images())


def test_is_16_9(tmp_path):
    model = _build_model(tmp_path, n=1)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    ratio = prs.slide_width / prs.slide_height
    assert abs(ratio - 16 / 9) < 0.01


def test_title_slide_has_report_title(tmp_path):
    model = _build_model(tmp_path, n=1)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    slide0_text = _all_text(prs.slides[0])
    assert "Test Deck" in slide0_text


# ---------------------------------------------------------------------------
# D-30: part-level summary slide (slide index 1)
# ---------------------------------------------------------------------------

def test_summary_slide_is_index_1_with_one_row_per_part_and_correct_counts(tmp_path):
    model = _build_parts_model(tmp_path, n_parts=3, n_lots=3, n_images=2)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    slide = prs.slides[1]
    assert _heading(slide) == "Grain Size Summary"
    table = next(sh for sh in slide.shapes if sh.has_table).table
    assert len(table.rows) == 3 + 1  # header + 3 parts
    headers = [table.cell(0, c).text for c in range(len(table.columns))]
    assert headers == ["Part", "Lots", "Images", "ASTM G", "Mean Diameter", "Mean Area"]
    parts_seen = set()
    for r in range(1, 4):
        parts_seen.add(table.cell(r, 0).text)
        assert table.cell(r, 1).text == "3"   # lots per part
        assert table.cell(r, 2).text == "6"   # images per part (3 lots * 2 images)
    assert parts_seen == {"P1", "P2", "P3"}


def test_summary_slide_has_no_grains_column_or_kpi_hint_text(tmp_path):
    model = _build_parts_model(tmp_path, n_parts=2, n_lots=2, n_images=2)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    slide = prs.slides[1]
    table = next(sh for sh in slide.shapes if sh.has_table).table
    headers = [table.cell(0, c).text for c in range(len(table.columns))]
    assert "Grains" not in headers
    text = _all_text(slide).lower()
    assert "higher = finer" not in text
    assert "kpi" not in text


def test_single_part_report_still_gets_summary_slide(tmp_path):
    model = _build_parts_model(tmp_path, n_parts=1, n_lots=1, n_images=3)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    assert _heading(prs.slides[1]) == "Grain Size Summary"
    table = next(sh for sh in prs.slides[1].shapes if sh.has_table).table
    assert len(table.rows) == 2  # header + 1 part


def test_parts_with_no_name_grouped_as_emdash(tmp_path):
    model = _build_model(tmp_path, n=2)  # no hierarchy, no sample_id override needed
    for img in model.images:
        img.sample_id = ""
        img.lot_number = ""
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    table = next(sh for sh in prs.slides[1].shapes if sh.has_table).table
    assert table.cell(1, 0).text == "—"


def test_part_average_equals_mean_of_lot_means(tmp_path):
    """D-30 averaging rule: each lot's mean is computed first, then the part
    value is the mean of the *lot* means (not a grain-weighted pool)."""
    model = _build_parts_model(tmp_path, n_parts=2, n_lots=3, n_images=4, px_per_um=8.0)
    images = model.ordered_images()
    rows, au, du = _part_summary_rows(model, images)
    assert du == "µm"  # px_per_um=8.0 -> 1000/8=125 >= 50 -> µm, multiplier 1.0

    groups = {}
    for img in images:
        part = img.level_value("sample", "")
        lot = img.level_value("lot", "")
        groups.setdefault(part, {}).setdefault(lot, []).append(img)

    for row in rows:
        lot_means = [float(np.mean([im.mean_diameter_um for im in imgs]))
                     for imgs in groups[row["part"]].values()]
        expected = float(np.mean(lot_means))
        assert abs(row["diam_mean"] - expected) < 1e-6

        lot_area_means = [float(np.mean([im.mean_area_um2 for im in imgs]))
                          for imgs in groups[row["part"]].values()]
        expected_area = float(np.mean(lot_area_means))
        assert abs(row["area_mean"] - expected_area) < 1e-6


def test_part_summary_paginates_when_too_many_parts(tmp_path):
    """> MAX_PARTS_COMBINED parts: the table alone (paginated) fills as many
    "Grain Size Summary" slides as needed -- ALL of them carry the
    "(cont'd i/N)" suffix, including the first, per the coordinator's
    continuation-labelling fix -- followed by one charts-only slide."""
    n = 20
    model = _build_parts_model(tmp_path, n_parts=n, n_lots=1, n_images=1, h=64, w=64, n_grains=10)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    summary_slides = [s for s in prs.slides if _heading(s).startswith("Grain Size Summary")
                      and not any(sh.has_chart for sh in s.shapes)]
    assert len(summary_slides) == 2
    total_rows = 0
    for s in summary_slides:
        table_shape = next(sh for sh in s.shapes if sh.has_table)
        bottom_in = Emu(table_shape.top).inches + Emu(table_shape.height).inches
        assert bottom_in <= SLIDE_H.inches
        table = table_shape.table
        total_rows += len(table.rows) - 1
    assert total_rows == n
    assert _heading(summary_slides[0]) == "Grain Size Summary (cont'd 1/2)"
    assert _heading(summary_slides[1]) == "Grain Size Summary (cont'd 2/2)"

    charts_slides = [s for s in prs.slides if _heading(s) == "Grain Size Summary — Charts"]
    assert len(charts_slides) == 1
    charts = [sh.chart for sh in charts_slides[0].shapes if sh.has_chart]
    assert len(charts) == 3
    assert all(len(list(c.plots[0].categories)) == n for c in charts)


def test_part_summary_small_part_count_still_single_slide(tmp_path):
    model = _build_model(tmp_path, n=3)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    summary_slides = [s for s in prs.slides if _heading(s).startswith("Grain Size Summary")]
    assert len(summary_slides) == 1


# ---------------------------------------------------------------------------
# Coordinator layout review: table + 3 bar charts together on slide 2 (Option
# A layout) when there are few enough parts; beyond MAX_PARTS_COMBINED parts,
# the table alone stays on slide 2 and the charts move to slide 3.
# ---------------------------------------------------------------------------

def test_summary_slide_combines_table_and_charts_when_few_parts(tmp_path):
    model = _build_parts_model(tmp_path, n_parts=3, n_lots=2, n_images=2)
    assert 3 <= MAX_PARTS_COMBINED
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    slide = prs.slides[1]
    assert _heading(slide) == "Grain Size Summary"
    assert any(sh.has_table for sh in slide.shapes)
    charts = [sh.chart for sh in slide.shapes if sh.has_chart]
    assert len(charts) == 3
    titles = [c.chart_title.text_frame.text for c in charts]
    assert any("ASTM" in t for t in titles)
    assert any("Diameter" in t for t in titles)
    assert any("Area" in t for t in titles)
    for c in charts:
        assert len(list(c.plots[0].categories)) == 3
        assert c.category_axis.axis_title.text_frame.text == "Part Number"
        assert c.value_axis.axis_title.text_frame.text  # non-empty
    # no leftover separate chart slides 3/4 for the small-part-count case
    assert not any(sh.has_chart for sh in prs.slides[2].shapes)


def test_summary_chart_uses_hierarchy_part_label(tmp_path):
    model = _build_parts_model(tmp_path, n_parts=2, n_lots=1, n_images=1)
    model.hierarchy[0]["label"] = "Casting Number"
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    chart = next(sh for sh in prs.slides[1].shapes if sh.has_chart).chart
    assert chart.category_axis.axis_title.text_frame.text == "Casting Number"


def test_more_than_six_parts_splits_table_and_charts_across_slides_2_and_3(tmp_path):
    """Coordinator layout review: > ~6 parts -- table stays alone on slide 2,
    the three charts move to their own slide 3."""
    model = _build_parts_model(tmp_path, n_parts=8, n_lots=1, n_images=1, h=64, w=64, n_grains=10)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    table_slide, charts_slide = prs.slides[1], prs.slides[2]
    assert any(sh.has_table for sh in table_slide.shapes)
    assert not any(sh.has_chart for sh in table_slide.shapes)
    table = next(sh for sh in table_slide.shapes if sh.has_table).table
    assert len(table.rows) == 8 + 1

    assert not any(sh.has_table for sh in charts_slide.shapes)
    charts = [sh.chart for sh in charts_slide.shapes if sh.has_chart]
    assert len(charts) == 3
    assert all(len(list(c.plots[0].categories)) == 8 for c in charts)


# ---------------------------------------------------------------------------
# D-30: per-image data-table slides, grouped by part, restyled
# ---------------------------------------------------------------------------

def test_data_table_slides_grouped_by_part_with_continuation_numbering(tmp_path):
    """Coordinator layout review: the FIRST slide of a part that continues
    is also labelled "(continued 1/2)" (not just the later ones)."""
    model = _build_parts_model(tmp_path, n_parts=1, n_lots=1, n_images=20, h=64, w=64, n_grains=10)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    data_slides = [s for s in prs.slides if _heading(s).startswith("P1")]
    assert len(data_slides) == 2  # ceil(20 / 14)
    assert _heading(data_slides[0]) == "P1 (continued 1/2)"
    assert _heading(data_slides[1]) == "P1 (continued 2/2)"
    total_rows = 0
    for s in data_slides:
        table = next(sh for sh in s.shapes if sh.has_table).table
        headers = [table.cell(0, c).text for c in range(len(table.columns))]
        assert headers[0] == "Part" and headers[1] == "Lot"
        assert "Job" not in headers
        n_data_rows = len(table.rows) - 1
        assert n_data_rows <= MAX_DATA_ROWS
        total_rows += n_data_rows
    assert total_rows == 20


def test_data_table_slide_never_mixes_parts(tmp_path):
    model = _build_parts_model(tmp_path, n_parts=3, n_lots=3, n_images=6, h=64, w=64, n_grains=10)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    known_parts = {f"P{p + 1}" for p in range(3)}
    checked = 0
    for s in prs.slides:
        base_part = _heading(s).split(" (continued")[0]
        if base_part not in known_parts:
            continue
        table = next(sh for sh in s.shapes if sh.has_table).table
        parts_in_rows = {table.cell(r, 0).text for r in range(1, len(table.rows))}
        assert parts_in_rows == {base_part}
        checked += 1
    assert checked > 0


def test_data_table_columns_and_no_job_column(tmp_path):
    model = _build_parts_model(tmp_path, n_parts=1, n_lots=1, n_images=2)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    slide = next(s for s in prs.slides if _heading(s) == "P1")
    table = next(sh for sh in slide.shapes if sh.has_table).table
    headers = [table.cell(0, c).text for c in range(len(table.columns))]
    assert headers == ["Part", "Lot", "Image", "Grains", "Mean Diam", "Std Diam",
                       "Mean Area", "Median Area", "Coverage %", "Circularity", "ASTM G"]


def test_data_table_truncates_long_image_name_and_notes_hold_full_name(tmp_path):
    """Coordinator layout review: truncation is based on the (widened) Image
    column's actual fit at the rendered font size, not a fixed character
    count."""
    model = _build_parts_model(tmp_path, n_parts=1, n_lots=1, n_images=1)
    model.images[0].display_name = "A_Very_Long_Image_Display_Name_2026"
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    slide = next(s for s in prs.slides if _heading(s) == "P1")
    table = next(sh for sh in slide.shapes if sh.has_table).table
    shown = table.cell(1, 2).text
    assert shown != "A_Very_Long_Image_Display_Name_2026"
    assert shown.endswith("…")
    assert len(shown) <= _max_chars_for_width(_DATA_IMAGE_COL_IN, 11)
    notes_text = slide.notes_slide.notes_text_frame.text
    assert "A_Very_Long_Image_Display_Name_2026" in notes_text


def test_data_table_only_truncates_names_that_dont_fit(tmp_path):
    """A normal-length display name (comfortably under the widened Image
    column's estimated character budget) is shown in full."""
    model = _build_parts_model(tmp_path, n_parts=1, n_lots=1, n_images=1)
    model.images[0].display_name = "001_img_1_original.png"  # 22 chars
    assert len(model.images[0].display_name) <= _max_chars_for_width(_DATA_IMAGE_COL_IN, 11)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    slide = next(s for s in prs.slides if _heading(s) == "P1")
    table = next(sh for sh in slide.shapes if sh.has_table).table
    assert table.cell(1, 2).text == "001_img_1_original.png"


def test_data_table_no_red_fill_anywhere(tmp_path):
    model = _build_parts_model(tmp_path, n_parts=2, n_lots=2, n_images=3)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)

    def is_reddish(rgb):
        return rgb[0] > 150 and rgb[1] < 100 and rgb[2] < 100

    checked = 0
    for s in prs.slides:
        for sh in s.shapes:
            if not sh.has_table:
                continue
            table = sh.table
            for r in range(len(table.rows)):
                for c in range(len(table.columns)):
                    cell = table.cell(r, c)
                    try:
                        rgb = cell.fill.fore_color.rgb
                    except Exception:
                        continue
                    checked += 1
                    assert not is_reddish(rgb)
    assert checked > 0


def test_data_table_numeric_columns_right_aligned_text_left_aligned(tmp_path):
    from pptx.enum.text import PP_ALIGN
    model = _build_parts_model(tmp_path, n_parts=1, n_lots=1, n_images=1)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    slide = next(s for s in prs.slides if _heading(s) == "P1")
    table = next(sh for sh in slide.shapes if sh.has_table).table
    assert table.cell(1, 2).text_frame.paragraphs[0].alignment == PP_ALIGN.LEFT  # Image
    assert table.cell(1, 3).text_frame.paragraphs[0].alignment == PP_ALIGN.RIGHT  # Grains


def test_data_table_body_font_at_least_10pt(tmp_path):
    model = _build_parts_model(tmp_path, n_parts=1, n_lots=1, n_images=2)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    slide = next(s for s in prs.slides if _heading(s) == "P1")
    table = next(sh for sh in slide.shapes if sh.has_table).table
    for c in range(len(table.columns)):
        for run in table.cell(1, c).text_frame.paragraphs[0].runs:
            assert run.font.size.pt >= 10


def test_data_table_uses_hierarchy_part_and_lot_no_job_column(tmp_path):
    model = _build_hierarchy_model_local(tmp_path, n=1, display_names=["L-44A_01"])
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    slide = next(s for s in prs.slides if _heading(s) == "7718-A")
    table = next(sh for sh in slide.shapes if sh.has_table).table
    headers = [table.cell(0, c).text for c in range(len(table.columns))]
    assert headers[:2] == ["Part", "Lot"]
    assert "Job" not in " ".join(headers)
    row1 = [table.cell(1, c).text for c in range(len(table.columns))]
    assert row1[0] == "7718-A" and row1[1] == "L-44A"
    assert "L-44A_01" in row1


# ---------------------------------------------------------------------------
# Nothing may overflow the slide. Coordinator layout review: the summary
# table's right edge ran past the slide (a column-width arithmetic bug) --
# extend the check to all four sides, and to every content shape (table,
# chart, picture), not just table bottoms. The header/footer/accent bars are
# intentional full-bleed decoration (0-margin by design, unrelated to the
# reported defect) and are excluded by their being exactly slide-width.
# ---------------------------------------------------------------------------

def _is_full_bleed_bar(shape, slide_w_in: float) -> bool:
    return abs(Emu(shape.width).inches - slide_w_in) < 0.05


def _content_shapes(slide, slide_w_in: float):
    for shape in slide.shapes:
        if not (shape.has_table or shape.has_chart or shape.shape_type == 13):
            continue
        if _is_full_bleed_bar(shape, slide_w_in):
            continue
        yield shape


def _assert_content_within_margin(prs, margin: float = MARGIN_IN, tol: float = 0.02) -> int:
    slide_w_in = Emu(prs.slide_width).inches
    slide_h_in = Emu(prs.slide_height).inches
    checked = 0
    for s in prs.slides:
        for shape in _content_shapes(s, slide_w_in):
            left_in = Emu(shape.left).inches
            top_in = Emu(shape.top).inches
            right_in = left_in + Emu(shape.width).inches
            bottom_in = top_in + Emu(shape.height).inches
            assert left_in >= margin - tol, f"left {left_in} < margin {margin}"
            assert top_in >= margin - tol, f"top {top_in} < margin {margin}"
            assert right_in <= slide_w_in - margin + tol, f"right {right_in} > {slide_w_in - margin}"
            assert bottom_in <= slide_h_in - margin + tol, f"bottom {bottom_in} > {slide_h_in - margin}"
            checked += 1
    assert checked > 0
    return checked


def test_content_within_margin_3_parts_3_lots_6_images(tmp_path):
    model = _build_parts_model(tmp_path, n_parts=3, n_lots=3, n_images=6, h=64, w=64, n_grains=10)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    _assert_content_within_margin(prs)


def test_content_within_margin_1_part_40_images(tmp_path):
    model = _build_parts_model(tmp_path, n_parts=1, n_lots=1, n_images=40, h=64, w=64, n_grains=10)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    _assert_content_within_margin(prs)


def test_content_within_margin_many_parts_charts_only_slide(tmp_path):
    """Covers the > MAX_PARTS_COMBINED (table-then-charts-slide) branch."""
    model = _build_parts_model(tmp_path, n_parts=9, n_lots=1, n_images=1, h=64, w=64, n_grains=10)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    _assert_content_within_margin(prs)


def test_summary_table_area_column_shows_squared_unit(tmp_path):
    """Coordinator layout review: "Mean Area" must show the calibrated
    AREA unit (µm²), not the length unit (µm)."""
    model = _build_parts_model(tmp_path, n_parts=2, n_lots=2, n_images=2, px_per_um=8.0)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    table = next(sh for sh in prs.slides[1].shapes if sh.has_table).table
    area_col = [h for h in range(len(table.columns))
               if table.cell(0, h).text == "Mean Area"][0]
    diam_col = [h for h in range(len(table.columns))
               if table.cell(0, h).text == "Mean Diameter"][0]
    for r in range(1, len(table.rows)):
        assert table.cell(r, area_col).text.endswith("µm²")
        assert table.cell(r, diam_col).text.endswith("µm")
        assert not table.cell(r, diam_col).text.endswith("µm²")
    chart = next(sh for sh in prs.slides[1].shapes if sh.has_chart
                and "Area" in sh.chart.chart_title.text_frame.text).chart
    assert chart.value_axis.axis_title.text_frame.text == "Mean Area (µm²)"
    diam_chart = next(sh for sh in prs.slides[1].shapes if sh.has_chart
                      and "Diameter" in sh.chart.chart_title.text_frame.text).chart
    assert diam_chart.value_axis.axis_title.text_frame.text == "Mean Diameter (µm)"


# ---------------------------------------------------------------------------
# Coordinator layout review: the footer must always be a single line under a
# length cap (long/many part/lot names used to wrap and get clipped).
# ---------------------------------------------------------------------------

def _footer_main_textbox(slide, slide_h_in: float):
    cands = [sh for sh in slide.shapes if sh.has_text_frame and sh.text_frame.text.strip()
            and abs(Emu(sh.top).inches - (slide_h_in - 0.32)) < 0.05]
    return min(cands, key=lambda sh: Emu(sh.left).inches)


def test_footer_collapses_to_counts_when_names_dont_fit(tmp_path):
    model = _build_parts_model(tmp_path, n_parts=3, n_lots=3, n_images=2)
    # a single-valued level (Job #) stays named; the multi-valued part/lot
    # levels (3 parts, 9 distinctly-named lots) are what overflow the cap.
    model.hierarchy.insert(0, {"key": "project", "label": "Job #", "value": "24-117"})
    page_num = 2  # slide index 1 (summary) is the 2nd slide created, right after the cover
    footer = _footer_text(model, page_num)
    assert "\n" not in footer
    assert len(footer) <= FOOTER_MAX_CHARS + 20   # + " · page N" suffix
    assert "3 parts" in footer and "9 lots" in footer
    assert "Job # 24-117" in footer

    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    slide_h_in = Emu(prs.slide_height).inches
    box = _footer_main_textbox(prs.slides[1], slide_h_in)
    assert box.text_frame.text == footer
    assert not box.text_frame.word_wrap


def test_footer_keeps_named_values_when_short(tmp_path):
    model = _build_hierarchy_model_local(tmp_path, n=1)
    footer = _footer_text(model, 1)
    assert "Job # 24-117" in footer
    assert "Part Number 7718-A" in footer
    assert "Lot L-44A" in footer
    assert len(footer) <= FOOTER_MAX_CHARS + 20


# ---------------------------------------------------------------------------
# Combined-distribution charts (unchanged content, now searched by heading
# since they no longer sit at a fixed slide index).
# ---------------------------------------------------------------------------

def test_distribution_slides_have_native_charts_with_axis_titles(tmp_path):
    model = _build_model(tmp_path, n=2)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    dist_slides = [_dist_slide(prs, "area"), _dist_slide(prs, "diameter")]
    units_seen = []
    for slide in dist_slides:
        charts = [sh for sh in slide.shapes if sh.has_chart]
        assert len(charts) == 1
        chart = charts[0].chart
        cat_title = chart.category_axis.axis_title.text_frame.text
        val_title = chart.value_axis.axis_title.text_frame.text
        assert val_title == "Number of Grains"
        assert "(" in cat_title and ")" in cat_title  # units present
        units_seen.append(cat_title)
    assert any("Area" in u for u in units_seen)
    assert any("Diameter" in u for u in units_seen)


def test_chart_options_disabling_area_removes_its_slide(tmp_path):
    model = _build_model(tmp_path, n=2)
    model.chart_options = {"area": {"enabled": False}}
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    dist_chart_slides = [s for s in prs.slides
                         if _heading(s).startswith("Combined ") and any(sh.has_chart for sh in s.shapes)]
    assert len(dist_chart_slides) == 1
    chart = next(sh for sh in dist_chart_slides[0].shapes if sh.has_chart).chart
    assert "Diameter" in chart.category_axis.axis_title.text_frame.text


def test_chart_options_normal_fit_off_drops_the_fit_series(tmp_path):
    model = _build_model(tmp_path, n=2)
    model.chart_options = {"normal_fit": False}
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    for slide in (_dist_slide(prs, "area"), _dist_slide(prs, "diameter")):
        chart = next(sh for sh in slide.shapes if sh.has_chart).chart
        names = [s.name for s in chart.series]
        assert names == ["Count"]


def test_chart_options_custom_title_used_as_chart_title(tmp_path):
    model = _build_model(tmp_path, n=2)
    model.chart_options = {"diameter": {"title": "Diameter — coarse fraction"}}
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    chart = next(sh for sh in _dist_slide(prs, "diameter").shapes if sh.has_chart).chart
    assert "coarse fraction" in chart.chart_title.text_frame.text


def test_chart_options_min_max_restricts_binned_grains(tmp_path):
    model = _build_model(tmp_path, n=2)
    out_full = str(tmp_path / "full.pptx")
    render_pptx(model, out_full)
    prs_full = Presentation(out_full)
    diam_full = next(sh for sh in _dist_slide(prs_full, "diameter").shapes if sh.has_chart).chart
    total_full = sum(diam_full.series[0].values)

    model.chart_options = {"diameter": {"min": 0, "max": 7}}
    out_narrow = str(tmp_path / "narrow.pptx")
    render_pptx(model, out_narrow)
    prs_narrow = Presentation(out_narrow)
    diam_narrow = next(sh for sh in _dist_slide(prs_narrow, "diameter").shapes if sh.has_chart).chart
    total_narrow = sum(diam_narrow.series[0].values)
    assert total_narrow < total_full


# ---------------------------------------------------------------------------
# UX-14 fix: unit-aware chart min/max
# ---------------------------------------------------------------------------

def _diam_slide_chart_total(pptx_path: str):
    prs = Presentation(pptx_path)
    chart = next(sh for sh in _dist_slide(prs, "diameter").shapes if sh.has_chart).chart
    return sum(chart.series[0].values)


def test_chart_options_min_max_bound_unit_um_to_nm_keeps_same_grains(tmp_path):
    """UX-14 fix: a diameter bound recorded as entered in µm keeps selecting
    the same physical grains after the report's unit preference switches
    from µm to nm, mirroring the Excel renderer's fix."""
    model = _build_model(tmp_path, n=2, px_per_um=8.0)
    diam_um = [g["diameter_um"] for img in model.images for g in img.grains]
    lo, hi = min(diam_um), (min(diam_um) + max(diam_um)) / 2
    expected = len([d for d in diam_um if lo <= d <= hi])
    assert 0 < expected < len(diam_um)

    model.chart_options = {"diameter": {"min": lo, "max": hi, "bound_unit": "µm"}}
    model.units = "um"
    out_um = str(tmp_path / "um.pptx")
    render_pptx(model, out_um)
    assert _diam_slide_chart_total(out_um) == expected

    model.units = "nm"
    out_nm = str(tmp_path / "nm.pptx")
    render_pptx(model, out_nm)
    assert _diam_slide_chart_total(out_nm) == expected


def test_chart_options_min_max_without_bound_unit_treated_as_current_render_unit(tmp_path):
    """Old report.json chart_options never had a "bound_unit" key -- it must
    keep applying unconverted (matching the pre-fix behaviour) rather than
    being (mis)treated as µm/nm."""
    model = _build_model(tmp_path, n=2, px_per_um=8.0)
    model.units = "nm"
    diam_nm = [g["diameter_um"] * 1000.0 for img in model.images for g in img.grains]
    lo, hi = min(diam_nm), (min(diam_nm) + max(diam_nm)) / 2
    expected = len([d for d in diam_nm if lo <= d <= hi])
    assert 0 < expected < len(diam_nm)

    model.chart_options = {"diameter": {"min": lo, "max": hi}}
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    assert _diam_slide_chart_total(out) == expected


# ---------------------------------------------------------------------------
# Per-image (original + overlay) slides -- unchanged content, found by title.
# ---------------------------------------------------------------------------

def test_image_slides_have_metric_callouts_and_caption(tmp_path):
    model = _build_model(tmp_path, n=2)
    model.images[0].caption = "Notably coarse grains"
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    image_slide = _find_image_slide(prs, model.images[0])
    text = _all_text(image_slide)
    assert "Grains" in text
    assert "Mean Diameter" in text
    assert "Notably coarse grains" in text


def test_overlay_opacity_zero_fades_overlay_to_plain_image(tmp_path):
    """UX-16: opacity 0 blends the overlay picture down to the plain image."""
    model = _build_model(tmp_path, n=1)
    model.overlay_opacity = 0.0
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    orig_arr = cv2.imread(model.images[0].image_path, cv2.IMREAD_COLOR)
    image_slide = _find_image_slide(prs, model.images[0])
    pics = [sh for sh in image_slide.shapes if sh.shape_type == 13]
    overlay_pic = max(pics, key=lambda sh: sh.left)   # overlay sits on the right
    arr = cv2.imdecode(np.frombuffer(overlay_pic.image.blob, np.uint8), cv2.IMREAD_COLOR)
    assert arr.shape == orig_arr.shape
    assert np.array_equal(arr, orig_arr)


def test_overlay_opacity_default_keeps_overlay_colouring(tmp_path):
    model = _build_model(tmp_path, n=1)
    assert model.overlay_opacity == 1.0
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    orig_arr = cv2.imread(model.images[0].image_path, cv2.IMREAD_COLOR)
    image_slide = _find_image_slide(prs, model.images[0])
    pics = [sh for sh in image_slide.shapes if sh.shape_type == 13]
    overlay_pic = max(pics, key=lambda sh: sh.left)
    arr = cv2.imdecode(np.frombuffer(overlay_pic.image.blob, np.uint8), cv2.IMREAD_COLOR)
    assert not np.array_equal(arr, orig_arr)


def test_methods_and_appendix_slides_present(tmp_path):
    model = _build_model(tmp_path, n=1)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    all_text = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                all_text.append(shape.text_frame.text)
    joined = "\n".join(all_text)
    assert "Methods" in joined
    assert "Excel" in joined  # appendix note


def test_disabling_combined_distribution_removes_those_slides(tmp_path):
    model = _build_model(tmp_path, n=2)
    model.get_section("combined_distribution").enabled = False
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    assert len(prs.slides) == _expected_slide_count(model, model.ordered_images(), want_charts=False)


def test_excluding_image_reduces_slide_count(tmp_path):
    model = _build_model(tmp_path, n=3)
    model.images[0].include = False
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    assert len(prs.slides) == _expected_slide_count(model, model.ordered_images())


def test_no_temp_files_leaked(tmp_path):
    model = _build_model(tmp_path, n=2)
    tmpdir = tempfile.gettempdir()
    before = set(os.listdir(tmpdir))
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    after = set(os.listdir(tmpdir))
    leaked = [n for n in (after - before) if "grain_report_pptx" in n]
    assert leaked == []


def _add_custom_text(model, order, title, body):
    sec = Section(id=f"text_{title}", type="custom_text", title=title, enabled=True,
                  order=order, payload={"body": body})
    model.sections.append(sec)
    return sec


# ---------------------------------------------------------------------------
# REP-08: designer edits the renderer must honour
# ---------------------------------------------------------------------------

def test_reordering_top_level_sections_reorders_slides(tmp_path):
    """Move Methods before the distribution slides purely via ``Section.order``."""
    model = _build_model(tmp_path, n=1)
    model.get_section("parameters").order = 1.5   # between overview(1) and charts(2)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    titles = [_heading(s) for s in prs.slides]
    methods_idx = next(i for i, t in enumerate(titles) if "Methods" in t)
    area_idx = next(i for i, t in enumerate(titles) if "Area Distribution" in t)
    assert methods_idx < area_idx
    # Appendix (raw data) is still strictly last.
    assert "Appendix" in titles[-1]


def test_renamed_custom_text_title_used_as_slide_heading(tmp_path):
    model = _build_model(tmp_path, n=1)
    sec = _add_custom_text(model, 1.1, "Sample Preparation", "Etched per ASTM E407.")
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    joined = "\n".join(_all_text(s) for s in prs.slides)
    assert "Sample Preparation" in joined
    assert "Etched per ASTM E407." in joined


def test_disabling_cover_removes_title_slide(tmp_path):
    model = _build_model(tmp_path, n=1)
    model.get_section("cover").enabled = False
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    assert len(prs.slides) == _expected_slide_count(model, model.ordered_images()) - 1
    assert _heading(prs.slides[0]) == "Grain Size Summary"


def test_disabling_overview_table_removes_summary_and_data_table_slides(tmp_path):
    model = _build_model(tmp_path, n=1)
    model.get_section("overview_table").enabled = False
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    assert len(prs.slides) == _expected_slide_count(model, model.ordered_images()) - _overview_slide_count(
        model, model.ordered_images())
    joined = "\n".join(_all_text(s) for s in prs.slides)
    assert "Grain Size Summary" not in joined


@pytest.mark.parametrize("theme_id", list(PALETTES.keys()))
def test_each_palette_recolours_title_slide(tmp_path, theme_id):
    from pptx.dml.color import RGBColor
    model = _build_model(tmp_path, n=1)
    model.theme = theme_id
    out = str(tmp_path / f"deck_{theme_id}.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    title_shape = next(sh for sh in prs.slides[0].shapes if sh.has_text_frame
                        and model.title in sh.text_frame.text)
    run = title_shape.text_frame.paragraphs[0].runs[0]
    expected = RGBColor.from_string(PALETTES[theme_id]["accent"].lstrip("#"))
    assert run.font.color.rgb == expected


def test_custom_palette_recolours_title_slide(tmp_path):
    from pptx.dml.color import RGBColor
    from reports.charts import derive_custom_palette
    model = _build_model(tmp_path, n=1)
    model.theme = "custom:custom-1"
    model.custom_palette = derive_custom_palette(["#123456", "#654321", "#00FF00"], "Lab palette")
    out = str(tmp_path / "deck_custom.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    title_shape = next(sh for sh in prs.slides[0].shapes if sh.has_text_frame
                        and model.title in sh.text_frame.text)
    run = title_shape.text_frame.paragraphs[0].runs[0]
    expected = RGBColor.from_string(model.custom_palette["accent"].lstrip("#"))
    assert run.font.color.rgb == expected


def test_two_custom_text_sections_render_native_text_slides_in_order(tmp_path):
    model = _build_model(tmp_path, n=1)
    _add_custom_text(model, 1.1, "Sample Prep", "Etched per ASTM E407.")
    _add_custom_text(model, 1.2, "Acceptance Criteria", "Grain size must be G >= 5.")
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    titles = [_heading(s) for s in prs.slides]
    assert titles.count("") == 0
    prep_idx = titles.index("Sample Prep")
    accept_idx = titles.index("Acceptance Criteria")
    summary_idx = titles.index("Grain Size Summary")
    area_idx = next(i for i, t in enumerate(titles) if "Area Distribution" in t)
    assert summary_idx < prep_idx < accept_idx < area_idx
    assert len(prs.slides) == _expected_slide_count(model, model.ordered_images()) + 2


def test_disabled_custom_text_section_is_not_rendered(tmp_path):
    model = _build_model(tmp_path, n=1)
    sec = _add_custom_text(model, 1.1, "Draft", "not ready")
    sec.enabled = False
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    assert len(prs.slides) == _expected_slide_count(model, model.ordered_images())
    assert "Draft" not in "\n".join(_all_text(s) for s in prs.slides)


# ---------------------------------------------------------------------------
# HIER-01: user-defined folder hierarchy in the PowerPoint renderer
# ---------------------------------------------------------------------------

_HIERARCHY = [
    {"key": "project", "label": "Job #", "value": "24-117"},
    {"key": "sample", "label": "Part Number", "value": "7718-A"},
    {"key": "lot", "label": "Lot", "value": "L-44A"},
]


def _build_hierarchy_model_local(tmp_path, n=2, display_names=None):
    det = GrainDetector()
    items = []
    for i in range(n):
        gray, _ = make_mosaic(seed=i + 1, h=256, w=256, n_grains=30)
        bgr = np.repeat(gray[:, :, None], 3, axis=2)
        res = det.analyze(bgr, px_per_um=8.0, params=DetectionParams())
        img_path = str(tmp_path / f"src_{i}.png")
        cv2.imwrite(img_path, bgr)
        item = ReportImageInput(image_path=img_path, result=res, image_bgr=bgr)
        if display_names:
            item.display_name = display_names[i]
        items.append(item)
    return ReportModel.from_results(items, title="Job Report", hierarchy=_HIERARCHY,
                                    asset_dir=str(tmp_path / "assets"))


def test_title_slide_shows_hierarchy_lines(tmp_path):
    model = _build_hierarchy_model_local(tmp_path, n=1)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    text = _all_text(prs.slides[0])
    assert "Job #: 24-117" in text
    assert "Part Number: 7718-A" in text
    assert "Lot: L-44A" in text


def test_footer_shows_hierarchy_and_page_number(tmp_path):
    model = _build_hierarchy_model_local(tmp_path, n=1)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    text = _all_text(prs.slides[0])
    assert "Job # 24-117" in text
    assert "Part Number 7718-A" in text
    assert "Lot L-44A" in text
    assert "page 1" in text


def test_image_slide_title_uses_display_name(tmp_path):
    model = _build_hierarchy_model_local(tmp_path, n=1, display_names=["L-44A_01"])
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    image_slide = _find_image_slide(prs, model.images[0])
    text = _all_text(image_slide)
    assert "L-44A_01" in text
    assert "src_0" not in text


def test_legacy_model_without_hierarchy_keeps_old_footer_and_title(tmp_path):
    model = _build_model(tmp_path, n=1)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    text = _all_text(prs.slides[0])
    assert "Sample/Lot:" in text
    assert model.title in text
    assert "page 1" not in text  # legacy footer has no "page n" phrase


def test_sample_output_written_to_scratch():
    scratch = os.path.join(ROOT, "scratch", "reports")
    os.makedirs(scratch, exist_ok=True)
    det = GrainDetector()
    items = []
    for i, seed in enumerate([21, 22, 23]):
        gray, _ = make_mosaic(seed=seed, h=256, w=256, n_grains=30)
        bgr = np.repeat(gray[:, :, None], 3, axis=2)
        res = det.analyze(bgr, px_per_um=8.0, params=DetectionParams())
        img_path = os.path.join(scratch, f"sample_src2_{i}.png")
        cv2.imwrite(img_path, bgr)
        items.append(ReportImageInput(image_path=img_path, result=res, image_bgr=bgr,
                                       sample_id=f"S{i}", lot_number="L1"))
    model = ReportModel.from_results(
        items, title="Sample Grain Report", operator="Jack", organization="Acme",
        metadata={"detection_mode": "boundary"}, asset_dir=os.path.join(scratch, "assets2"),
    )
    model.theme = "slate_teal"
    _add_custom_text(model, 1.1, "Sample Preparation", "Mounted, polished, etched per ASTM E407.")
    _add_custom_text(model, 20_001, "Conclusions", "Grain size meets the acceptance criterion.")
    out = os.path.join(scratch, "sample.pptx")
    render_pptx(model, out)
    assert os.path.exists(out)
    prs = Presentation(out)
    joined = "\n".join(_all_text(s) for s in prs.slides)
    assert "Sample Preparation" in joined
    assert "Conclusions" in joined


# ---------------------------------------------------------------------------
# FIX-11: a long report title used to overlap the subtitle lines below it
# (python-pptx text boxes never grow/shrink to fit their text on save).
# ---------------------------------------------------------------------------

def test_long_title_does_not_overlap_subtitle(tmp_path):
    model = _build_model(tmp_path, n=1)
    model.title = ("Grain Size Report -- Part 718-DSK-220, Lot L-2604-01 (Job 26-031), "
                    "Extended Title Long Enough To Force Wrapping Onto Multiple Lines")
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    shapes = list(prs.slides[0].shapes)
    title_shape = next(sh for sh in shapes if sh.has_text_frame and model.title in sh.text_frame.text)
    title_bottom_in = Emu(title_shape.top).inches + Emu(title_shape.height).inches
    next_box = shapes[shapes.index(title_shape) + 1]
    assert Emu(next_box.top).inches >= title_bottom_in - 0.01


def test_short_title_keeps_original_subtitle_position(tmp_path):
    model = _build_model(tmp_path, n=1)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    shapes = list(prs.slides[0].shapes)
    title_shape = next(sh for sh in shapes if sh.has_text_frame and model.title in sh.text_frame.text)
    title_bottom_in = Emu(title_shape.top).inches + Emu(title_shape.height).inches
    next_box = shapes[shapes.index(title_shape) + 1]
    assert abs(Emu(next_box.top).inches - (title_bottom_in + 0.1)) < 0.01


# ---------------------------------------------------------------------------
# FIX-13: the "Normal Fit" curve used to render as a second set of bars
# instead of a smoothed line overlay.
# ---------------------------------------------------------------------------

def test_distribution_chart_normal_fit_is_a_line_not_bars(tmp_path):
    from pptx.chart.plot import BarPlot, LinePlot

    model = _build_model(tmp_path, n=2)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    area_slide = _dist_slide(prs, "area")
    chart = next(sh for sh in area_slide.shapes if sh.has_chart).chart
    assert len(chart.plots) == 2
    bar_plot, line_plot = chart.plots
    assert isinstance(bar_plot, BarPlot)
    assert isinstance(line_plot, LinePlot)
    assert [s.name for s in bar_plot.series] == ["Count"]
    assert [s.name for s in line_plot.series] == ["Normal Fit"]
    assert os.path.exists(out)


# ---------------------------------------------------------------------------
# FIX-12: the Methods text box used to overflow straight through the footer
# bar (a single fixed-height text box that never shrinks/paginates on its
# own).
# ---------------------------------------------------------------------------

def test_methods_slide_paginates_when_params_dont_fit(tmp_path):
    model = _build_model(tmp_path, n=1)
    model.metadata["detection_params"] = {f"param_{i}": f"value_{i}" for i in range(40)}
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    methods_slides = [s for s in prs.slides if _all_text(s).startswith("Methods")]
    assert len(methods_slides) > 1
    assert "(cont'd)" in _all_text(methods_slides[1])

    all_lines = []
    for s in methods_slides:
        body_box = next(sh for sh in s.shapes if sh.has_text_frame
                         and not sh.text_frame.text.startswith("Methods")
                         and len(sh.text_frame.text) > 20)
        bottom_in = Emu(body_box.top).inches + Emu(body_box.height).inches
        assert bottom_in < FOOTER_TOP_IN  # FIX-12: never runs into the footer
        all_lines.extend(body_box.text_frame.text.split("\n"))
    assert any(l.startswith("param_0:") for l in all_lines)
    assert any(l.startswith("param_39:") for l in all_lines)


def test_methods_slide_short_params_still_single_slide(tmp_path):
    """Regression guard: FIX-12 pagination must not split the common case."""
    model = _build_model(tmp_path, n=1)
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    methods_slides = [s for s in prs.slides if _all_text(s).startswith("Methods")]
    assert len(methods_slides) == 1


# ---------------------------------------------------------------------------
# FIX-07 (PPTX half): ReportModel.calibration rendered in the Methods
# slide(s), mirroring the Excel Methods sheet (commit 790afdb).
# ---------------------------------------------------------------------------

def test_methods_slide_includes_calibration_block_matching_excel(tmp_path):
    model = _build_model(tmp_path, n=1)
    model.calibration = {
        "source": "scale_bar", "px_per_um": 8.0, "check": "manual", "status": "pass",
        "reason": "", "warnings": ["Sample count below target (n=3 of 5)"],
        "text": "Verified 2026-09-24: 8.00 px/um (0.3% RA, target 10%)",
    }
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    joined = "\n".join(_all_text(s) for s in prs.slides)
    assert "Scale Verification: Verified 2026-09-24: 8.00 px/um (0.3% RA, target 10%)" in joined
    assert "Verification Source: scale_bar" in joined
    assert "Verification Warnings: Sample count below target (n=3 of 5)" in joined


def test_no_calibration_means_no_scale_verification_line(tmp_path):
    model = _build_model(tmp_path, n=1)
    assert model.calibration is None
    out = str(tmp_path / "deck.pptx")
    render_pptx(model, out)
    prs = Presentation(out)
    joined = "\n".join(_all_text(s) for s in prs.slides)
    assert "Scale Verification" not in joined


def test_calibration_round_trips_through_report_json_into_pptx(tmp_path):
    """FIX-07: ``ReportModel.calibration`` survives a to_json/from_json
    round-trip (the ``report.json`` re-edit path) and still renders."""
    model = _build_model(tmp_path, n=1)
    model.calibration = {"source": "scale_bar", "text": "Verified: 8.00 px/um"}
    reloaded = ReportModel.from_json(model.to_json())
    out = str(tmp_path / "deck.pptx")
    render_pptx(reloaded, out)
    prs = Presentation(out)
    joined = "\n".join(_all_text(s) for s in prs.slides)
    assert "Scale Verification: Verified: 8.00 px/um" in joined
