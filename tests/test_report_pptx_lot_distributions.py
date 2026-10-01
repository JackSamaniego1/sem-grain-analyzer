"""One grain-distribution slide per PART (batch 4D): area + diameter charts with
clustered bars, one series/color per lot of that part, shared equal-width bins."""
import os
import sys
import tempfile

import numpy as np
from pptx import Presentation

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from reports.charts import build_bins
from reports.model import ReportModel, ImageSummary, Section
from reports.pptx_renderer import (
    render_pptx, PERCENTILE_TITLE, NOT_ENOUGH_GRAINS_NOTE, MAX_LOTS_PER_COMPARISON, _even_spans,
)

HIER = [{"key": "sample", "label": "Part Number", "value": ""},
        {"key": "lot", "label": "Lot", "value": ""}]
NS = {"c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}


def _img(iid, part, lot, diams_um, ppu=8.0, order=0):
    grains = [{"diameter_um": d, "area_um2": np.pi * (d / 2) ** 2,
               "diameter_px": d * ppu, "area_px": np.pi * (d * ppu / 2) ** 2} for d in diams_um]
    return ImageSummary(id=iid, image_path="", order=order, px_per_um=ppu, has_calibration=True,
                        grain_count=len(grains), grains=grains, levels={"sample": part, "lot": lot})


def _model(images, units="um", bins=None):
    secs = [Section(id="cover", type="cover", title="Cover", order=0),
            Section(id="ov", type="overview_table", title="Overview", order=1),
            Section(id="ls", type="lot_summary", title="Lot Summary", order=500)]
    m = ReportModel(title="T", sections=secs, images=images, hierarchy=HIER, units=units)
    if bins:
        m.bins = bins
    return m


def _lots(n, seed=0, per=40, part="P"):
    rng = np.random.default_rng(seed)
    return [_img(f"{part}i{k}", part, f"L{k}", rng.normal(2 + k, 0.6, per).clip(0.2), order=k)
            for k in range(n)]


def _head(slide):
    return next(r.text for sh in slide.shapes if sh.has_text_frame
                for p in sh.text_frame.paragraphs for r in p.runs)


def _text(slide):
    return "\n".join(r.text for sh in slide.shapes if sh.has_text_frame
                     for p in sh.text_frame.paragraphs for r in p.runs)


def _charts(slide):
    return [sh.chart for sh in slide.shapes if sh.has_chart]


def _render(tmp_path, model):
    out = str(tmp_path / "d.pptx")
    render_pptx(model, out)
    return Presentation(out)


def _dist_slides(prs):
    return [s for s in prs.slides if _head(s).startswith("Grain Distributions")]


def _series_fill(chart, i):
    ser = chart._chartSpace.findall(".//c:barChart/c:ser", NS)[i]
    return ser.find("c:spPr/a:solidFill/a:srgbClr", NS).get("val")


def test_one_slide_per_part_with_two_clustered_charts(tmp_path):
    imgs = _lots(3, part="PA") + _lots(2, seed=5, part="PB")
    prs = _render(tmp_path, _model(imgs))
    slides = _dist_slides(prs)
    assert [_head(s) for s in slides] == ["Grain Distributions — PA", "Grain Distributions — PB"]
    for s, n in zip(slides, (3, 2)):
        charts = _charts(s)
        assert len(charts) == 2
        titles = [c.chart_title.text_frame.text for c in charts]
        assert titles[0].startswith("Grain Area Distribution")
        assert titles[1].startswith("Grain Size Distribution")
        axes = [(c.category_axis.axis_title.text_frame.text,
                 c.value_axis.axis_title.text_frame.text) for c in charts]
        assert axes[0] == ("Grain Area (µm²)", "Number of Grains")
        assert axes[1] == ("Equivalent Diameter (µm)", "Number of Grains")
        for c in charts:
            assert len(c.plots[0].series) == n                       # ONE series per lot
            assert not c._chartSpace.findall(".//c:lineChart", NS)   # no trendlines
            assert c.has_legend                                      # color key


def test_series_named_after_lots_with_distinct_colors_and_shared_bins(tmp_path):
    prs = _render(tmp_path, _model(_lots(4)))
    (slide,) = _dist_slides(prs)
    charts = _charts(slide)
    for c in charts:
        assert [s.name for s in c.plots[0].series] == [f"Lot L{k}" for k in range(4)]
        fills = [_series_fill(c, i) for i in range(4)]
        assert len(set(fills)) == 4                                   # one color per lot
        cats = list(c.plots[0].categories)
        assert len(cats) == len(set(cats)) > 1
        for ser in c._chartSpace.findall(".//c:ser", NS):             # every series same bins
            assert len(ser.findall("c:cat//c:pt", NS)) == len(cats)
    # same colors in both charts of the slide (the legend key is slide-wide)
    assert [_series_fill(charts[0], i) for i in range(4)] == [_series_fill(charts[1], i) for i in range(4)]


def test_counts_are_raw_and_use_the_shared_binner(tmp_path):
    imgs = _lots(2)
    prs = _render(tmp_path, _model(imgs, bins={"area": 7, "diameter": 9}))
    area, diam = _charts(_dist_slides(prs)[0])
    pooled = [g["diameter_um"] for i in imgs for g in i.grains]
    _, _, edges = build_bins(pooled, 9)
    for ser, im in zip(diam.plots[0].series, imgs):
        expect, _ = np.histogram([g["diameter_um"] for g in im.grains], bins=np.asarray(edges))
        assert list(ser.values) == [float(x) for x in expect]
    assert len(list(area.plots[0].categories)) == 7


def test_unit_follows_report_units(tmp_path):
    prs = _render(tmp_path, _model(_lots(1), units="nm"))
    area, diam = _charts(_dist_slides(prs)[0])
    assert area.category_axis.axis_title.text_frame.text == "Grain Area (nm²)"
    assert diam.category_axis.axis_title.text_frame.text == "Equivalent Diameter (nm)"


def test_order_after_data_tables_before_image_slides_no_comparison_slides(tmp_path):
    prs = _render(tmp_path, _model(_lots(2)))
    heads = [_head(s) for s in prs.slides]
    assert not any(h.startswith("Lot-to-Lot") for h in heads)
    assert not any(h.startswith("Grain Distributions —") and "Lot L" in h for h in heads)
    i_pct = heads.index(PERCENTILE_TITLE)
    dist = next(i for i, h in enumerate(heads) if h.startswith("Grain Distributions"))
    data = heads.index("P")
    assert i_pct < data < dist


def test_more_than_six_lots_split_into_minimum_slides_with_same_bins(tmp_path):
    n = MAX_LOTS_PER_COMPARISON + 2
    prs = _render(tmp_path, _model(_lots(n)))
    slides = _dist_slides(prs)
    assert len(slides) == 2                                   # minimum number of slides
    assert [_head(s) for s in slides] == ["Grain Distributions — P"] * 2   # plain repeated title
    a0, a1 = _charts(slides[0])[0], _charts(slides[1])[0]
    assert [len(c.plots[0].series) for c in (a0, a1)] == [4, 4]
    assert list(a0.plots[0].categories) == list(a1.plots[0].categories)
    names = [s.name for c in (a0, a1) for s in c.plots[0].series]
    assert names == [f"Lot L{k}" for k in range(n)]           # colors/legend keyed by lot
    assert [_series_fill(a0, i) for i in range(4)] != [_series_fill(a1, i) for i in range(4)]


def test_even_spans_is_minimum_and_balanced():
    assert _even_spans(6, 6) == [(0, 6)]
    assert _even_spans(7, 6) == [(0, 4), (4, 7)]
    assert _even_spans(13, 6) == [(0, 5), (5, 10), (10, 13)]
    assert _even_spans(0, 6) == [(0, 0)]


def test_empty_and_single_grain_lots_do_not_crash(tmp_path):
    imgs = _lots(2) + [_img("e", "P", "EMPTY", [], order=5), _img("s", "P", "ONE", [1.5], order=6)]
    prs = _render(tmp_path, _model(imgs))
    (slide,) = _dist_slides(prs)
    assert "Not shown (no grains): Lot EMPTY" in _text(slide)
    names = [s.name for s in _charts(slide)[0].plots[0].series]
    assert names == ["Lot L0", "Lot L1", "Lot ONE"]


def test_part_with_too_few_grains_gets_a_note_not_a_chart(tmp_path):
    prs = _render(tmp_path, _model([_img("s", "P", "ONE", [1.5], order=1)]))
    (slide,) = _dist_slides(prs)
    assert not _charts(slide)
    assert NOT_ENOUGH_GRAINS_NOTE in _text(slide)


def test_chart_options_disable_one_kind(tmp_path):
    m = _model(_lots(2))
    m.chart_options = {"area": {"enabled": False}}
    prs = _render(tmp_path, m)
    for s in _dist_slides(prs):
        assert len(_charts(s)) == 1
        assert _charts(s)[0].chart_title.text_frame.text.startswith("Grain Size")


def test_sample_deck_written_outside_repo():
    imgs = []
    rng = np.random.default_rng(3)
    k = 0
    for part in ("A100", "B200"):
        for lot in range(4):
            for _ in range(2):
                imgs.append(_img(f"i{k}", part, f"L{lot}", rng.lognormal(0.8 + 0.15 * lot, 0.4, 60),
                                 order=k))
                k += 1
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "sample.pptx")
        render_pptx(_model(imgs), out)
        assert os.path.getsize(out) > 0


def _grid(parts, lots, per=30):
    rng = np.random.default_rng(0)
    return [_img(f"i{p}_{l}", f"P{p}", f"L{l}", rng.normal(3, .5, per).clip(.2), order=p * lots + l)
            for p in range(parts) for l in range(lots)]


def test_hardcoded_slide_counts(tmp_path):
    # cover + contents + summary + 3 all-lots charts + lot charts (1 per part) + percentiles + data tables
    # (1 per part) + distributions (1 per part) + job summary table (1) + 1 image slide per image
    assert len(_render(tmp_path, _model(_grid(2, 3))).slides) == 1 + 1 + 1 + 3 + 2 + 1 + 2 + 2 + 1 + 6
    assert len(_render(tmp_path, _model(_grid(2, 4))).slides) == 1 + 1 + 1 + 3 + 2 + 1 + 2 + 2 + 1 + 8
    assert len(_render(tmp_path, _model(_grid(1, 8))).slides) == 1 + 1 + 1 + 3 + 1 + 1 + 1 + 2 + 1 + 8


def test_omitted_note_is_capped_to_one_line(tmp_path):
    imgs = _lots(2) + [_img(f"e{k}", "P", f"EMPTY{k}", [], order=10 + k) for k in range(9)]
    (slide,) = _dist_slides(_render(tmp_path, _model(imgs)))
    note = next(ln for ln in _text(slide).split(chr(10)) if ln.startswith("Not shown"))
    assert note.endswith("+6 more") and len(note) < 120


def test_axis_ids_and_series_ids_valid_for_powerpoint(tmp_path):
    prs = _render(tmp_path, _model(_lots(3)))
    for s in _dist_slides(prs):
        for c in _charts(s):
            cs = c._chartSpace
            sers = cs.findall(".//c:ser", NS)
            idx = [x.find("c:idx", NS).get("val") for x in sers]
            order = [x.find("c:order", NS).get("val") for x in sers]
            assert len(set(idx)) == len(idx) and len(set(order)) == len(order)
            assert len(cs.findall(".//c:barChart/c:axId", NS)) == 2


def test_nan_inf_zero_values_do_not_crash(tmp_path):
    bad = _img("b", "P", "BAD", [1.0, 2.0, 3.0, 0.0, 0.0, 4.0], order=3)
    bad.grains += [{"diameter_um": float("nan"), "area_um2": float("inf"),
                    "diameter_px": float("nan"), "area_px": float("inf")},
                   {"diameter_um": None, "area_um2": None, "diameter_px": None, "area_px": None}]
    prs = _render(tmp_path, _model(_lots(2) + [bad]))
    (slide,) = _dist_slides(prs)
    assert len(_charts(slide)) == 2
    assert len(_charts(slide)[0].plots[0].series) == 3
    zero_only = _img("z", "P", "ZERO", [0.0, 0.0, 0.0], order=4)
    prs = _render(tmp_path, _model(_lots(2) + [zero_only]))
    assert len(_dist_slides(prs)) == 1


def test_lot_entirely_outside_filter_is_listed_as_not_shown(tmp_path):
    m = _model(_lots(2))   # lots centered near 2 and 3 um
    m.chart_options = {"diameter": {"min": 100.0, "max": 200.0, "bound_unit": "µm"},
                       "area": {"enabled": True}}
    prs = _render(tmp_path, m)
    for s in _dist_slides(prs):
        assert "Not shown (no grains)" in _text(s)
        assert len(_charts(s)) == 0   # every lot is outside the diameter filter -> nothing plottable
