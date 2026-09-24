"""UI-11 core: scale-bar end detection for rectangle / level-line snapping.

Synthetic SEM frames: the conftest mosaic with a data bar holding a scale
bar of known position (white-on-black and black-on-white, with 1-px end
ticks, a label above it and detector noise)."""
import cv2
import numpy as np
import pytest

from conftest import make_mosaic
from core.scale_bar import find_scale_bar_line
from core.scale_bar_snap import bar_edges_near, find_bar_ends_in_roi, snap_x


def sem_with_scale_bar(bg="black", length=120, thick=4, seed=0):
    """Return (bgr image, (x0, x1_exclusive, y0, y1_exclusive)) of the bar."""
    g, _ = make_mosaic(seed=seed + 3)
    h, w = g.shape
    bh = 56
    ink = 255 if bg == "black" else 0
    bar = np.full((bh, w), 255 - ink, np.uint8)
    cv2.putText(bar, "SE2 15 kV WD 8.6 mm", (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45, ink, 1,
                cv2.LINE_AA)
    x0 = w - length - 30
    sy = 36
    cv2.rectangle(bar, (x0, sy), (x0 + length - 1, sy + thick - 1), ink, -1)
    cv2.line(bar, (x0, sy - 5), (x0, sy + thick + 4), ink, 1)
    cv2.line(bar, (x0 + length - 1, sy - 5), (x0 + length - 1, sy + thick + 4), ink, 1)
    cv2.putText(bar, "20 um", (x0 + length // 3, sy - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                ink, 1, cv2.LINE_AA)
    rng = np.random.default_rng(seed)
    bar = np.clip(bar.astype(np.int16) + rng.integers(-4, 5, bar.shape), 0, 255).astype(np.uint8)
    out = g.copy()
    out[h - bh:, :] = bar
    top = h - bh + sy
    return cv2.cvtColor(out, cv2.COLOR_GRAY2BGR), (x0, x0 + length, top, top + thick)


@pytest.mark.parametrize("bg", ["black", "white"])
@pytest.mark.parametrize("box", [
    (-3, -3, 3, 3),         # snug
    (-25, -14, 30, 12),     # loose and too wide
    (-60, -20, 45, 16),     # very loose (catches the label and ticks)
    (5, -1, -6, 1),         # slightly too narrow: bar ends just outside
])
def test_roi_detection_finds_bar_ends(bg, box):
    img, (x0, x1, y0, y1) = sem_with_scale_bar(bg)
    dl, dt, dr, db = box
    found = find_bar_ends_in_roi(img, (x0 + dl, y0 + dt, (x1 + dr) - (x0 + dl),
                                       (y1 + db) - (y0 + dt)))
    assert found is not None
    assert found["x0"] == pytest.approx(x0, abs=1)
    assert found["x1"] == pytest.approx(x1, abs=1)
    assert found["length_px"] == pytest.approx(x1 - x0, abs=1)
    assert y0 <= found["cy"] <= y1


def test_roi_detection_is_exact_on_clean_bar():
    img, (x0, x1, _, _) = sem_with_scale_bar("black", length=200, seed=4)
    found = find_bar_ends_in_roi(img, (x0 - 20, 0, x1 - x0 + 40, img.shape[0]))
    # full-height box: only strokes not touching the box sides count
    assert found is not None and (found["x0"], found["x1"]) == (x0, x1)


def test_roi_without_bar_returns_none():
    img, (x0, x1, y0, y1) = sem_with_scale_bar()
    find_bar_ends_in_roi(img, (5, 5, 40, 20))        # grain area: must not raise
    # box entirely inside the bar: its ends are not visible -> nothing to snap to
    assert find_bar_ends_in_roi(img, (x0 + 30, y0 - 2, 40, y1 - y0 + 4), pad=2) is None
    assert find_bar_ends_in_roi(img, (0, 0, 0, 10)) is None


@pytest.mark.parametrize("bg", ["black", "white"])
def test_level_line_end_snaps_within_tolerance(bg):
    img, (x0, x1, y0, y1) = sem_with_scale_bar(bg)
    cy = (y0 + y1) / 2
    for dx in (-5, -2, 0, 3, 5):
        hit = snap_x(img, x0 + dx, cy, tol=6)
        assert hit is not None and hit[0] == pytest.approx(x0, abs=1)
        hit = snap_x(img, x1 + dx, cy + 1, tol=6)
        assert hit is not None and hit[0] == pytest.approx(x1, abs=1)
        assert y0 <= hit[1] <= y1
    # too far away: no snap
    assert snap_x(img, x0 - 15, cy, tol=6) is None
    assert snap_x(img, (x0 + x1) / 2, cy, tol=6) is None
    assert all(abs(e[0] - (x0 - 3)) <= 6 for e in bar_edges_near(img, x0 - 3, cy))


def test_find_scale_bar_line_position_is_exact():
    """The auto-prefill rect starts on the bar's first ink column (an even
    opening kernel used to shift it one pixel right)."""
    img, (x0, x1, y0, y1) = sem_with_scale_bar("black", length=150)
    bar = find_scale_bar_line(img)
    assert bar is not None
    x, y, w, h = bar["rect"]
    assert (x, x + w) == (x0, x1)
