"""core.grain_edit.add_grain - draw a missed grain by hand on the Review page
(UPDATE 4 item 8, core half)."""
import copy
import json

import numpy as np
import pytest
from scipy import ndimage as ndi

from core.grain_detector import AnalysisResult, DetectionParams, GrainDetector
from core.grain_edit import (
    MIN_PIECE_PX, AddGrainOutcome, add_grain, label_offset, remeasure_after_edit,
    split_grain, to_label_coords,
)
from core.metrics import compute_statistics


def _grid(gap=1):
    """3x3 grid of 30x30 square grains (ids 1..9) separated by ``gap``-px
    background lines, on a 100x100 frame.  Grain 5 covers x, y 34..63."""
    lab = np.zeros((100, 100), dtype=np.int32)
    gid = 1
    for r in range(3):
        for c in range(3):
            y, x = 3 + r * (30 + gap), 3 + c * (30 + gap)
            lab[y:y + 30, x:x + 30] = gid
            gid += 1
    return lab


def _raw(lab, ppu=2.0, valid=None):
    p = DetectionParams(min_grain_size_px=0, max_grain_size_px=0)
    r = AnalysisResult(label_image=lab, px_per_um=ppu, has_calibration=ppu > 0,
                       valid_mask=np.ones(lab.shape, bool) if valid is None else valid)
    r.grains = GrainDetector()._measure_grains(lab, p, ppu)
    compute_statistics(r, lab.shape)
    return r


def _square(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _nothing_added(out, lab):
    assert isinstance(out, AddGrainOutcome)
    assert out.added is False and out.grain_id == 0
    assert out.changed == [] and out.op == {}
    assert out.reason and "\n" not in out.reason
    assert np.array_equal(out.labels, lab)


# ---------------------------------------------------------------- basics
def test_add_into_empty_space():
    lab = np.zeros((80, 100), np.int32)
    lab[5:15, 5:15] = 3
    out = add_grain(lab, _square(40, 30, 59, 49))
    assert out.added and out.grain_id == 4 and out.changed == [4] and out.removed == []
    expected = np.zeros_like(lab, bool)
    expected[30:50, 40:60] = True                  # vertices are inclusive pixels
    assert np.array_equal(out.labels == 4, expected)
    assert out.area_px == 400 and out.drawn_px == 400
    assert np.array_equal(out.labels[lab > 0], lab[lab > 0])
    assert out.op["op"] == "add" and out.op["id"] == 4 and out.op["area_px"] == 400
    json.dumps(out.op)                             # JSON-able for grain_edits
    assert lab.max() == 3                          # input untouched


def test_open_stroke_is_closed_like_a_lasso():
    lab = np.zeros((60, 60), np.int32)
    open_stroke = [(10, 10), (40, 10), (40, 40), (10, 40)]       # no return segment
    closed = open_stroke + [(10, 10)]
    a, b = add_grain(lab, open_stroke), add_grain(lab, closed)
    assert a.added and np.array_equal(a.labels, b.labels)
    assert (a.labels[10:41, 10] == 1).all()         # closing edge is part of it


def test_self_crossing_loop_is_filled_solid():
    lab = np.zeros((60, 60), np.int32)
    bowtie = [(10, 10), (50, 50), (50, 10), (10, 50)]
    out = add_grain(lab, bowtie)
    assert out.added
    g = out.labels == out.grain_id
    assert ndi.label(g, structure=np.ones((3, 3)))[1] == 1
    assert np.array_equal(ndi.binary_fill_holes(g), g)


# ---------------------------------------------------------------- neighbours
def test_overlapping_neighbours_are_untouched_pixel_exact():
    lab = _grid(1)
    lab[lab == 5] = 0                               # the detector missed grain 5
    before = lab.copy()
    out = add_grain(lab, _square(20, 20, 78, 78))   # generous outline over 1-9
    assert out.added and out.grain_id == 10
    occupied = before > 0
    assert np.array_equal(out.labels[occupied], before[occupied])
    new = out.labels == 10
    assert not (new & occupied).any()
    # the new grain is the hole + the boundary lines round it, one piece
    assert new[34:64, 34:64].all()
    assert ndi.label(new, structure=np.ones((3, 3)))[1] == 1
    assert np.array_equal(lab, before)


def test_outline_inside_an_existing_grain_adds_nothing():
    lab = _grid(1)
    out = add_grain(lab, _square(40, 40, 55, 55))
    _nothing_added(out, lab)
    assert "already detected" in out.reason


def test_only_largest_free_piece_is_kept():
    lab = np.zeros((60, 100), np.int32)
    lab[:, 30:33] = 7                               # a wall grain splits the outline
    out = add_grain(lab, _square(10, 10, 80, 40))   # left 20 px wide, right 48 px
    assert out.added
    new = out.labels == out.grain_id
    assert not new[:, :30].any() and new[10:41, 33:81].all()
    assert out.area_px == 31 * 48


# ---------------------------------------------------------------- size limit
def test_tiny_scribble_adds_nothing():
    lab = np.zeros((50, 50), np.int32)
    _nothing_added(add_grain(lab, [(10, 10), (11, 10), (11, 11)]), lab)
    _nothing_added(add_grain(lab, [(10, 10), (12, 12)]), lab)         # 2 points
    _nothing_added(add_grain(lab, []), lab)
    _nothing_added(add_grain(lab, [(np.nan, 1), (2, np.inf), (3, 3)]), lab)
    small = add_grain(lab, _square(20, 20, 26, 26), min_area_px=50)   # 49 px
    _nothing_added(small, lab)
    assert "too small" in small.reason and "50" in small.reason
    assert add_grain(lab, _square(20, 20, 26, 26), min_area_px=49).added
    assert MIN_PIECE_PX == 5


# ---------------------------------------------------------------- clipping
def test_outline_partly_outside_the_image_is_clipped():
    lab = np.zeros((50, 60), np.int32)
    out = add_grain(lab, _square(-20, -15, 20, 25))
    expected = np.zeros_like(lab, bool)
    expected[0:26, 0:21] = True
    assert out.added and np.array_equal(out.labels == out.grain_id, expected)


def test_outline_is_clipped_to_the_scan_area():
    lab = np.zeros((50, 60), np.int32)
    valid = np.ones_like(lab, bool)
    valid[30:, :] = False                            # e.g. data bar / black border
    out = add_grain(lab, _square(10, 20, 40, 45), valid_mask=valid)
    new = out.labels == out.grain_id
    assert out.added and not new[30:].any() and new[20:30, 10:41].all()
    outside = add_grain(lab, _square(10, 35, 40, 45), valid_mask=valid)
    _nothing_added(outside, lab)
    assert "scan area" in outside.reason
    _nothing_added(add_grain(lab, _square(100, 100, 140, 140)), lab)


def test_cropped_labels_with_canvas_coordinates():
    lab = np.zeros((50, 60), np.int32)                # analysed crop
    crop = (40, 25, 90, 85)                           # r0, c0, r1, c1 in a 120x100 frame
    off = label_offset(lab.shape, (120, 100), crop)
    canvas = _square(25 + 10, 40 + 10, 25 + 19, 40 + 19)
    out = add_grain(lab, to_label_coords(canvas, off))
    assert out.added and (out.labels[10:20, 10:20] == out.grain_id).all()
    assert out.area_px == 100


# ---------------------------------------------------------------- measurements
def test_measurements_equal_an_identical_detected_grain():
    full = _grid(1)
    ref = _raw(full)
    missed = full.copy()
    missed[missed == 5] = 0
    raw = _raw(missed)
    out = add_grain(missed, _square(34, 34, 63, 63))
    assert out.added and out.grain_id == 10
    assert np.array_equal(out.labels == 10, full == 5)
    edited = remeasure_after_edit(raw, out, missed.shape)
    new = next(g for g in edited.grains if g.grain_id == 10)
    det = next(g for g in ref.grains if g.grain_id == 5)
    for f in ("area_px", "area_um2", "perimeter_px", "perimeter_um",
              "equivalent_diameter_px", "equivalent_diameter_um", "major_axis_um",
              "minor_axis_um", "aspect_ratio", "circularity", "eccentricity",
              "centroid_x", "centroid_y"):
        assert getattr(new, f) == pytest.approx(getattr(det, f)), f
    assert tuple(new.bbox) == tuple(det.bbox)
    # summary statistics are those of the fully detected field
    assert edited.grain_count == ref.grain_count == 9
    for f in ("mean_area_um2", "std_area_um2", "median_area_um2", "mean_diameter_um",
              "grain_coverage_pct", "total_analyzed_area_um2"):
        assert getattr(edited, f) == pytest.approx(getattr(ref, f)), f
    assert edited.grain_coverage_pct > raw.grain_coverage_pct
    assert edited.astm == {} and edited.astm_g is None      # post-filter redoes E112


# ---------------------------------------------------------------- undo / ids
def test_undo_restores_the_exact_previous_state():
    lab = _grid(1)
    lab[lab == 5] = 0
    raw = _raw(lab)
    snap_lab, snap_raw = lab.copy(), copy.deepcopy(raw)
    out = add_grain(raw.label_image, _square(30, 30, 66, 66))
    edited = remeasure_after_edit(raw, out, lab.shape)
    assert edited.grain_count == raw.grain_count + 1
    # undo = put the previous raw result back; it was never mutated
    assert raw.label_image is lab and np.array_equal(lab, snap_lab)
    assert raw.grains == snap_raw.grains and raw.grain_count == snap_raw.grain_count
    assert raw.mean_area_um2 == snap_raw.mean_area_um2
    assert raw.grain_coverage_pct == snap_raw.grain_coverage_pct


def test_ids_stay_unique_after_repeated_add_and_undo():
    lab = np.zeros((130, 130), np.int32)
    lab[:100, :100] = _grid(3)                       # ids 1..9; free L-shaped margin
    s1 = add_grain(lab, _square(0, 105, 50, 115))
    assert s1.grain_id == 10
    s2 = add_grain(s1.labels, _square(105, 0, 115, 90))
    assert s2.grain_id == 11
    undone = s1.labels                               # undo s2
    s3 = add_grain(undone, _square(0, 118, 50, 125))
    assert s3.grain_id == 11 and not (undone == 11).any()
    s4 = add_grain(s3.labels, _square(105, 0, 115, 90))
    assert s4.grain_id == 12
    ids, counts = np.unique(s4.labels[s4.labels > 0], return_counts=True)
    assert ids.tolist() == list(range(1, 13))
    for i in ids:                                    # every id is one region
        assert ndi.label(s4.labels == i, structure=np.ones((3, 3)))[1] == 1
    # a split after an add continues numbering
    sp = split_grain(s4.labels, [(48, 20), (48, 80)])
    assert sp.op["new_ids"] == [13]
    # a caller-chosen id that is in use is refused, not overwritten
    clash = add_grain(s4.labels, _square(60, 105, 90, 115), new_id=3)
    _nothing_added(clash, s4.labels)
