"""Per-lot area/size distribution slides + lot-to-lot comparison (UPDATE 4 item 17)."""
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
    render_pptx, PERCENTILE_TITLE, NOT_ENOUGH_GRAINS_NOTE, MAX_LOTS_PER_COMPARISON, _trend_counts,
)

HIER = [{"key": "sample", "label": "Part Number", "value": ""},
        {"key": "lot", "label": "Lot", "value": ""}]
NS = {"c": "http://schemas.openxmlformats.org/drawingml/2006/chart"}


def _img(iid, part, lot, diams_um, ppu=8.0, order=0):
    grains = [{"diameter_um": d, "area_um2": np.pi * (d / 2) ** 2,
               "diameter_px": d * ppu, "area_px": np.pi * (d * ppu / 2) ** 2} for d in diams_um]
    return ImageSummary(id=iid, image_path="", order=order, px_per_um=ppu, has_calibration=True,
                        grain_count=len(grains), grains=grains, levels={"sample": part, "lot": lot})


def _model(images, units="um", bins=None):
    secs = [Section(id="cover", type="cover", title="Cover", order=0),
            Section(id="ov", type="overview_table", title="Overview", order=1)]
    m = ReportModel(title="T", sections=secs, images=images, hierarchy=HIER, units=units)
    if bins:
        m.bins = bins
    return m


def _lots(n, seed=0, per=40):
    rng = np.random.default_rng(seed)
    return [_img(f"i{k}", "P", f"L{k}", rng.normal(2 + k, 0.6, per).clip(0.2), order=k)
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


def _lot_slides(prs):
    return [s for s in prs.slides if _head(s).startswith("Grain Distributions")]


def _cmp_slides(prs):
    return [s for s in prs.slides if _head(s).startswith("Lot-to-Lot Distribution Comparison")]


def _bar_and_line_series(chart):
    bar = chart._chartSpace.findall(".//c:barChart/c:ser", NS)
    line = chart._chartSpace.findall(".//c:lineChart/c:ser", NS)
    return bar, line


def test_one_slide_per_lot_with_two_combo_charts(tmp_path):
    prs = _render(tmp_path, _model(_lots(3)))
    slides = _lot_slides(prs)
    assert len(slides) == 3
    for k, s in enumerate(slides):
        assert f"Lot L{k}" in _head(s)
        charts = _charts(s)
        assert len(charts) == 2
        titles = [c.chart_title.text_frame.text for c in charts]
        assert titles[0].startswith("Grain Area Distribution") and f"Lot L{k}" in titles[0]
        assert titles[1].startswith("Grain Size Distribution")
        axes = [(c.category_axis.axis_title.text_frame.text,
                 c.value_axis.axis_title.text_frame.text) for c in charts]
        assert axes[0] == ("Grain Area (µm²)", "Number of Grains")
        assert axes[1] == ("Equivalent Diameter (µm)", "Number of Grains")
        for c in charts:
            bar, line = _bar_and_line_series(c)
            assert len(bar) == 1 and len(line) == 1
            assert line[0].find("c:smooth", NS).get("val") == "1"


def test_order_after_data_tables_before_image_slides(tmp_path):
    prs = _render(tmp_path, _model(_lots(2)))
    heads = [_head(s) for s in prs.slides]
    i_pct = heads.index(PERCENTILE_TITLE)
    first_lot = next(i for i, h in enumerate(heads) if h.startswith("Grain Distributions"))
    cmp_i = next(i for i, h in enumerate(heads) if h.startswith("Lot-to-Lot Distribution Comparison"))
    assert i_pct < first_lot < cmp_i
    assert heads[cmp_i - 1].startswith("Grain Distributions")
    assert heads[-1] != heads[cmp_i]  # appendix still last


def test_bars_use_shared_binner(tmp_path):
    imgs = _lots(1)
    prs = _render(tmp_path, _model(imgs, bins={"area": 7, "diameter": 9}))
    area, diam = _charts(_lot_slides(prs)[0])
    vals_d = [g["diameter_um"] for g in imgs[0].grains]
    _, counts, _ = build_bins(vals_d, 9)
    assert list(diam.plots[0].series[0].values) == counts
    assert len(list(area.plots[0].categories)) == 7


def test_unit_follows_report_units(tmp_path):
    prs = _render(tmp_path, _model(_lots(1), units="nm"))
    area, diam = _charts(_lot_slides(prs)[0])
    assert area.category_axis.axis_title.text_frame.text == "Grain Area (nm²)"
    assert diam.category_axis.axis_title.text_frame.text == "Equivalent Diameter (nm)"


def test_comparison_series_per_lot_and_shared_categories(tmp_path):
    prs = _render(tmp_path, _model(_lots(4)))
    slides = _cmp_slides(prs)
    assert len(slides) == 1
    charts = _charts(slides[0])
    assert len(charts) == 2
    for c in charts:
        bar, line = _bar_and_line_series(c)
        assert len(bar) == 4 and len(line) == 4
        names = [s.name for s in c.plots[0].series]
        assert names == [f"Lot L{k}" for k in range(4)]
        cats = list(c.plots[0].categories)
        assert len(cats) == len(set(cats)) > 1
        # legend hides the trend series
        assert len(c._chartSpace.findall(".//c:legendEntry", NS)) == 4
        # every series shares the category list
        for ser in c._chartSpace.findall(".//c:ser", NS):
            assert len(ser.findall("c:cat//c:pt", NS)) == len(cats)
    # same bins for area and lots: categories identical between slides' charts of one kind
    assert charts[0].category_axis.axis_title.text_frame.text == "Grain Area (µm²)"
    assert charts[1].value_axis.axis_title.text_frame.text == "Share of Grains (%)"


def test_comparison_paginates_beyond_six_lots_with_same_bins(tmp_path):
    n = MAX_LOTS_PER_COMPARISON + 2
    prs = _render(tmp_path, _model(_lots(n)))
    slides = _cmp_slides(prs)
    assert len(slides) == 2
    assert _head(slides[0]).endswith("(1/2)") and _head(slides[1]).endswith("(2/2)")
    a0, a1 = _charts(slides[0])[0], _charts(slides[1])[0]
    assert len(_bar_and_line_series(a0)[0]) == MAX_LOTS_PER_COMPARISON
    assert len(_bar_and_line_series(a1)[0]) == 2
    assert list(a0.plots[0].categories) == list(a1.plots[0].categories)
    assert len(_lot_slides(prs)) == n


def test_empty_and_single_grain_lots_do_not_crash(tmp_path):
    imgs = _lots(2) + [_img("e", "P", "EMPTY", [], order=5), _img("s", "P", "ONE", [1.5], order=6)]
    prs = _render(tmp_path, _model(imgs))
    slides = {_head(s): s for s in _lot_slides(prs)}
    assert len(slides) == 4
    for name in ("Lot EMPTY", "Lot ONE"):
        s = next(v for k, v in slides.items() if name in k)
        assert not _charts(s)
        assert NOT_ENOUGH_GRAINS_NOTE in _text(s)
    cmp_ = _cmp_slides(prs)[0]
    assert "Not shown (not enough grains)" in _text(cmp_)
    assert "EMPTY" in _text(cmp_) and "ONE" in _text(cmp_)
    assert len(_bar_and_line_series(_charts(cmp_)[0])[0]) == 2


def test_single_lot_has_no_comparison_slide(tmp_path):
    prs = _render(tmp_path, _model(_lots(1)))
    assert len(_lot_slides(prs)) == 1 and not _cmp_slides(prs)


def test_chart_options_disable_one_kind(tmp_path):
    m = _model(_lots(2))
    m.chart_options = {"area": {"enabled": False}}
    prs = _render(tmp_path, m)
    for s in _lot_slides(prs) + _cmp_slides(prs):
        assert len(_charts(s)) == 1
        assert _charts(s)[0].chart_title.text_frame.text.startswith("Grain Size")


def test_trend_is_scaled_to_bar_total_and_robust():
    rng = np.random.default_rng(1)
    v = list(rng.normal(5, 1, 200))
    _, counts, edges = build_bins(v, 12)
    t = _trend_counts(v, edges)
    assert abs(sum(t) - sum(counts)) < 1e-6
    # identical values (KDE singular) and two grains must not raise
    _, _, e = build_bins([3.0, 3.0, 3.0], 5)
    assert len(_trend_counts([3.0, 3.0, 3.0], e)) == 5
    _, _, e2 = build_bins([1.0, 2.0], 5)
    assert len(_trend_counts([1.0, 2.0], e2)) == 5


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
    # cover + contents + summary + percentiles + data tables (1 per part) + lot slides
    # + comparison pages + 1 image slide per lot + appendix
    assert len(_render(tmp_path, _model(_grid(2, 3))).slides) == 1 + 1 + 1 + 1 + 2 + 6 + 1 + 6 + 1
    assert len(_render(tmp_path, _model(_grid(2, 4))).slides) == 1 + 1 + 1 + 1 + 2 + 8 + 2 + 8 + 1
    assert len(_render(tmp_path, _model(_grid(1, 8))).slides) == 1 + 1 + 1 + 1 + 1 + 8 + 2 + 8 + 1


def test_omitted_note_is_capped_to_one_line(tmp_path):
    imgs = _lots(2) + [_img(f"e{k}", "P", f"EMPTY{k}", [], order=10 + k) for k in range(9)]
    cmp_ = _cmp_slides(_render(tmp_path, _model(imgs)))[0]
    note = next(ln for ln in _text(cmp_).split(chr(10)) if ln.startswith("Not shown"))
    assert note.endswith("+6 more") and len(note) < 120


def test_axis_ids_and_series_ids_valid_for_powerpoint(tmp_path):
    prs = _render(tmp_path, _model(_lots(3)))
    for s in _lot_slides(prs) + _cmp_slides(prs):
        for c in _charts(s):
            cs = c._chartSpace
            bar_ax = [a.get("val") for a in cs.findall(".//c:barChart/c:axId", NS)]
            line_ax = [a.get("val") for a in cs.findall(".//c:lineChart/c:axId", NS)]
            assert len(bar_ax) == 2 and bar_ax == line_ax
            sers = cs.findall(".//c:ser", NS)
            idx = [x.find("c:idx", NS).get("val") for x in sers]
            order = [x.find("c:order", NS).get("val") for x in sers]
            assert len(set(idx)) == len(idx) and len(set(order)) == len(order)


def test_trend_values_sum_to_bar_total(tmp_path):
    prs = _render(tmp_path, _model(_lots(2)))
    for c in _charts(_lot_slides(prs)[0]):
        bar, line = _bar_and_line_series(c)
        def vals(ser):
            return [float(v.text) for v in ser.findall("c:val//c:v", NS)]
        assert abs(sum(vals(line[0])) - sum(vals(bar[0]))) < 0.05 * len(vals(bar[0]))
    # comparison: each lot's shares sum to ~100 for bars and trend
    for c in _charts(_cmp_slides(prs)[0]):
        bar, line = _bar_and_line_series(c)
        for b, l in zip(bar, line):
            sb = sum(float(v.text) for v in b.findall("c:val//c:v", NS))
            sl = sum(float(v.text) for v in l.findall("c:val//c:v", NS))
            assert abs(sb - 100) < 1 and abs(sl - 100) < 1


def test_nan_inf_zero_values_do_not_crash(tmp_path):
    bad = _img("b", "P", "BAD", [1.0, 2.0, 3.0, 0.0, 0.0, 4.0], order=3)
    bad.grains += [{"diameter_um": float("nan"), "area_um2": float("inf"),
                    "diameter_px": float("nan"), "area_px": float("inf")},
                   {"diameter_um": None, "area_um2": None, "diameter_px": None, "area_px": None}]
    prs = _render(tmp_path, _model(_lots(2) + [bad]))
    s = next(x for x in _lot_slides(prs) if "BAD" in _head(x))
    assert len(_charts(s)) == 2
    assert "n=6" in _charts(s)[1].chart_title.text_frame.text   # area had 6 finite too
    zero_only = _img("z", "P", "ZERO", [0.0, 0.0, 0.0], order=4)
    prs = _render(tmp_path, _model(_lots(2) + [zero_only]))
    assert len(_lot_slides(prs)) == 3 and len(_cmp_slides(prs)) == 1


def test_lot_entirely_outside_filter_gets_note(tmp_path):
    m = _model(_lots(2))   # lots centred near 2 and 3 um; L0 also below 100
    m.chart_options = {"diameter": {"min": 100.0, "max": 200.0, "bound_unit": "µm"},
                       "area": {"enabled": True}}
    prs = _render(tmp_path, m)
    for s in _lot_slides(prs):
        assert NOT_ENOUGH_GRAINS_NOTE in _text(s)
        assert len(_charts(s)) == 1      # area chart still drawn
    assert not _cmp_slides(prs) or len(_charts(_cmp_slides(prs)[0])) >= 1
