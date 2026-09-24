"""core.grain_edit — lasso selection, merge, split, replay, re-measurement,
coordinate mapping (UI-05 / INN-04)."""
import numpy as np
import pytest

from core.grain_detector import AnalysisResult, DetectionParams, GrainDetector
from core.grain_edit import (
    GrainEditError, MIN_PIECE_PX, grain_under_line, grains_in_polygon, label_offset,
    measure_ids, merge_grains, remeasure_after_edit, replay_edits, split_grain,
    to_label_coords,
)
from core.metrics import compute_statistics
from core.postfilter import PostFilterOptions, apply_post_filters


def _grid(gap=1):
    """3x3 grid of 30x30 square grains (ids 1..9) separated by ``gap``-px
    background lines, on a 100x100 frame."""
    lab = np.zeros((100, 100), dtype=np.int32)
    gid = 1
    for r in range(3):
        for c in range(3):
            y, x = 3 + r * (30 + gap), 3 + c * (30 + gap)
            lab[y:y + 30, x:x + 30] = gid
            gid += 1
    return lab


def _raw(lab, ppu=2.0):
    p = DetectionParams(min_grain_size_px=0, max_grain_size_px=0)
    r = AnalysisResult(label_image=lab, px_per_um=ppu, has_calibration=ppu > 0,
                       valid_mask=np.ones(lab.shape, bool))
    r.grains = GrainDetector()._measure_grains(lab, p, ppu)
    compute_statistics(r, lab.shape)
    return r


# ---------------------------------------------------------------- lasso
def test_lasso_selects_grains_mostly_inside():
    lab = _grid()
    # loop around the top-left 2x2 block (grains 1, 2, 4, 5), clipping grain 3 a little
    poly = [(0, 0), (70, 0), (70, 66), (0, 66)]
    assert grains_in_polygon(lab, poly) == [1, 2, 4, 5]
    assert grains_in_polygon(lab, poly, candidates=[1, 5]) == [1, 5]
    assert grains_in_polygon(lab, [(0, 0), (5, 5)]) == []          # not a loop
    assert grains_in_polygon(lab, [(-50, -50), (-10, -50), (-10, -10)]) == []


# ---------------------------------------------------------------- merge
@pytest.mark.parametrize("gap", [0, 1, 3])
def test_merge_touching_grains_fills_boundary(gap):
    lab = _grid(gap)
    out = merge_grains(lab, [2, 1])
    assert out.changed == [1] and out.removed == [2] and out.op["into"] == 1
    assert not (out.labels == 2).any()
    area = int((out.labels == 1).sum())
    assert area == 1800 + 30 * gap                          # the boundary line is filled
    assert lab.max() == 9 and (lab == 2).sum() == 900       # input untouched
    # nothing outside the two grains changed
    other = ~np.isin(lab, [1, 2]) & (lab > 0)
    assert np.array_equal(out.labels[other], lab[other])


def test_merge_rejects_non_touching_and_single():
    lab = _grid(1)
    with pytest.raises(GrainEditError):
        merge_grains(lab, [1, 3])          # grain 2 lies between them
    with pytest.raises(GrainEditError):
        merge_grains(lab, [1])
    with pytest.raises(GrainEditError):
        merge_grains(lab, [1, 99])


def test_merge_three_in_a_row():
    out = merge_grains(_grid(1), [1, 2, 3])
    assert out.removed == [2, 3] and int((out.labels == 1).sum()) == 2700 + 60


# ---------------------------------------------------------------- split
def test_split_vertical_cut_conserves_area():
    lab = _grid(1)
    # vertical line through the middle of grain 5 (x 34..63, y 34..63)
    out = split_grain(lab, [(48, 20), (48, 80)])
    assert out.op["id"] == 5 and out.op["new_ids"] == [10]
    a5, a10 = int((out.labels == 5).sum()), int((out.labels == 10).sum())
    assert a5 + a10 == 900 and abs(a5 - a10) <= 30
    # two separate pieces, each connected
    from scipy import ndimage as ndi
    assert ndi.label(out.labels == 5)[1] == 1 and ndi.label(out.labels == 10)[1] == 1


def test_split_diagonal_line_separates_8_connected():
    lab = _grid(1)
    out = split_grain(lab, [(30, 30), (70, 70)], grain_id=5)
    assert set(out.changed) == {5, 10}
    assert (out.labels == 10).sum() >= MIN_PIECE_PX


def test_split_must_cross_the_grain():
    lab = _grid(1)
    with pytest.raises(GrainEditError):
        split_grain(lab, [(40, 40), (45, 45)])          # stops inside the grain
    with pytest.raises(GrainEditError):
        split_grain(lab, [(1, 1), (1, 2)])              # no grain
    assert grain_under_line(lab, [(48, 20), (48, 80)]) == 5
    assert grain_under_line(lab, [(48, 20), (48, 80)], candidates=[2]) == 2


# ---------------------------------------------------------------- replay
def test_replay_reproduces_edits_and_skips_stale_ops():
    lab = _grid(1)
    m = merge_grains(lab, [1, 2])
    s = split_grain(m.labels, [(48, 20), (48, 80)])
    ops = [m.op, s.op, {"op": "merge", "ids": [77, 78], "into": 77}]
    out, applied = replay_edits(lab, ops, np.ones(lab.shape, bool))
    assert np.array_equal(out, s.labels)
    assert applied == [m.op, s.op]
    assert replay_edits(None, ops) == (None, [])


# ---------------------------------------------------------------- measurements
def test_remeasure_matches_full_detector_measurement():
    lab = _grid(1)
    raw = _raw(lab)
    out = merge_grains(lab, [4, 5])
    s = split_grain(out.labels, [(20, 0), (20, 40)])       # cut grain 1
    edited = remeasure_after_edit(remeasure_after_edit(raw, out), s)
    ref = _raw(s.labels)
    assert [g.grain_id for g in edited.grains] == [g.grain_id for g in ref.grains]
    for a, b in zip(edited.grains, ref.grains):
        assert a.area_px == b.area_px
        assert a.equivalent_diameter_um == pytest.approx(b.equivalent_diameter_um)
        assert a.centroid_x == pytest.approx(b.centroid_x)
        assert tuple(a.bbox) == tuple(b.bbox)
    assert edited.grain_count == 9          # 9 - 1 (merge) + 1 (split)
    assert edited.mean_area_um2 == pytest.approx(ref.mean_area_um2)
    assert edited.astm == {} and raw.grain_count == 9      # raw untouched


def test_postfilter_recomputes_astm_after_edit():
    lab = _grid(1)
    raw = _raw(lab)
    before = apply_post_filters(raw, np.zeros(lab.shape + (3,), np.uint8), PostFilterOptions())
    edited = remeasure_after_edit(raw, merge_grains(lab, [1, 2, 4, 5]))
    after = apply_post_filters(edited, np.zeros(lab.shape + (3,), np.uint8), PostFilterOptions())
    assert after.result.grain_count == 6
    assert after.result.astm.get("G_primary") is not None
    assert after.result.astm["G_primary"] < before.result.astm["G_primary"]  # bigger grains


def test_measure_ids_ignores_missing():
    assert measure_ids(_grid(), [], 1.0) == []
    assert [g.grain_id for g in measure_ids(_grid(), [3, 500], 1.0)] == [3]


# ---------------------------------------------------------------- coordinates
def test_label_offset_full_frame_and_cropped():
    assert label_offset((100, 80), (100, 80, 3)) == (0, 0)
    # labels cover rows 10..60, cols 20..100 of a 120x140 frame
    assert label_offset((50, 80), (120, 140), (10, 20, 60, 100)) == (20, 10)
    with pytest.raises(GrainEditError):
        label_offset((50, 80), (120, 140), None)
    pts = to_label_coords([(25, 15), (99.5, 59)], (20, 10))
    assert pts.tolist() == [[5.0, 5.0], [79.5, 49.0]]


def test_cropped_labels_edit_with_canvas_coordinates():
    """A cut drawn in full-frame canvas coordinates lands on the right grain
    of a label image that is in analysed-region (cropped) coordinates."""
    lab = _grid(1)                                    # the analysed crop
    crop = (40, 25, 140, 125)                         # r0, c0, r1, c1 in a 200x160 frame
    off = label_offset(lab.shape, (200, 160), crop)
    line_canvas = [(25 + 48, 40 + 20), (25 + 48, 40 + 80)]    # through grain 5
    out = split_grain(lab, to_label_coords(line_canvas, off))
    assert out.op["id"] == 5
    lasso_canvas = [(25 + 0, 40 + 0), (25 + 35, 40 + 0), (25 + 35, 40 + 35), (25, 40 + 35)]
    assert grains_in_polygon(lab, to_label_coords(lasso_canvas, off)) == [1]
