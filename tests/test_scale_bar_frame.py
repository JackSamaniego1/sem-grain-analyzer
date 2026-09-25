"""FIX-17: a rectangle drawn around the scale bar must not be mistaken for
the bar (its top/bottom edges are longer than the bar).  FIX-16: percent
formatting never shows "-0.00 %"."""
import cv2
import numpy as np
import pytest

from conftest import make_mosaic
from core.cal_verify import format_percent
from core.scale_bar import find_scale_bar_line


def sem_with_framed_bar(bg="black", length=120, thick=4, frame_t=1, seed=0,
                        frame=True):
    g, _ = make_mosaic(seed=seed + 3)
    h, w = g.shape
    bh = 70
    ink = 255 if bg == "black" else 0
    bar = np.full((bh, w), 255 - ink, np.uint8)
    cv2.putText(bar, "SE2 15 kV WD 8.6 mm", (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                ink, 1, cv2.LINE_AA)
    x0 = w - length - 40
    sy = 42
    cv2.rectangle(bar, (x0, sy), (x0 + length - 1, sy + thick - 1), ink, -1)
    cv2.line(bar, (x0, sy - 5), (x0, sy + thick + 4), ink, 1)
    cv2.line(bar, (x0 + length - 1, sy - 5), (x0 + length - 1, sy + thick + 4), ink, 1)
    cv2.putText(bar, "20 um", (x0 + length // 3, sy - 9), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                ink, 1, cv2.LINE_AA)
    if frame:
        cv2.rectangle(bar, (x0 - 18, sy - 26), (x0 + length + 17, sy + thick + 12),
                      ink, frame_t)
    rng = np.random.default_rng(seed)
    bar = np.clip(bar.astype(np.int16) + rng.integers(-4, 5, bar.shape), 0, 255
                  ).astype(np.uint8)
    out = g.copy()
    out[h - bh:, :] = bar
    return cv2.cvtColor(out, cv2.COLOR_GRAY2BGR), (x0, h - bh + sy)


@pytest.mark.parametrize("bg", ["black", "white"])
@pytest.mark.parametrize("frame_t", [1, 2])
@pytest.mark.parametrize("length", [90, 160])
def test_bar_inside_frame_is_found(bg, frame_t, length):
    img, (x0, y0) = sem_with_framed_bar(bg, length=length, frame_t=frame_t)
    found = find_scale_bar_line(img)
    assert found is not None
    x, y, cw, ch = found["rect"]
    assert found["length_px"] == pytest.approx(length, abs=1)
    assert x == pytest.approx(x0, abs=1)
    assert y0 - 1 <= y <= y0 + 4


def test_unframed_bar_unchanged():
    img, (x0, _) = sem_with_framed_bar(length=140, frame=False)
    found = find_scale_bar_line(img)
    assert found is not None and found["length_px"] == pytest.approx(140, abs=1)


@pytest.mark.parametrize("value, expected", [
    (-0.0, "0.00 %"), (0.0, "0.00 %"), (-0.001, "0.00 %"), (0.004, "0.00 %"),
    (-0.005001, "-0.01 %"), (1.234, "+1.23 %"), (-2.5, "-2.50 %"),
])
def test_format_percent_no_negative_zero(value, expected):
    assert format_percent(value) == expected


def test_format_percent_options():
    assert format_percent(-0.0, decimals=1) == "0.0 %"
    assert format_percent(3.0, signed=False) == "3.00 %"
