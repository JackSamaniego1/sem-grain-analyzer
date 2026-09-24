"""DET-05: SEM data/info bar detection, auto-exclusion and scale-bar line.

Synthetic fixtures: the conftest mosaic with a bottom (or top) data bar
rendered like a real SEM bar — uniform black or white background, a line of
cv2.putText annotation ("SE2 15.00 kV WD 8.6 mm Mag 500 x", modelled on a
Zeiss SmartSEM bar), a scale-bar line with end ticks and its label, a
little detector/JPEG-like noise; bar height 6-15 % of the frame.
"""
import time

import cv2
import numpy as np
import pytest

from conftest import make_mosaic, make_dark_mosaic
from core.infobar import detect_info_bar
from core.grain_detector import GrainDetector, DetectionParams
from core.scale_bar import find_scale_bar_line, detect_scale_bar_length_px


SCALE_LEN = 120   # px, length of the rendered scale-bar line


def add_bar(gray, position="bottom", bg="black", frac=0.10, separator=False,
            seed=0, scale_len=SCALE_LEN):
    """Return (gray_with_bar, bar_rect(x,y,w,h), scale_rect(x,y,w,h))."""
    rng = np.random.default_rng(seed)
    h, w = gray.shape
    bh = int(round(h * frac))
    bar = np.full((bh, w), 0 if bg == "black" else 255, np.uint8)
    ink = 255 if bg == "black" else 0
    fs = max(0.35, bh / 110.0)
    th = 1 if bh < 50 else 2
    cv2.putText(bar, "SE2 15.00 kV WD 8.6 mm Mag 500 x",
                (6, int(bh * 0.45)), cv2.FONT_HERSHEY_SIMPLEX, fs, ink, th,
                cv2.LINE_AA)
    cv2.putText(bar, "Date: 12 Mar 2019  Signal A = SE2",
                (6, int(bh * 0.85)), cv2.FONT_HERSHEY_SIMPLEX, fs * 0.8,
                ink, 1, cv2.LINE_AA)
    sx0 = w - scale_len - 20
    sy = int(bh * 0.60)
    lt = max(3, bh // 14)
    cv2.rectangle(bar, (sx0, sy), (sx0 + scale_len - 1, sy + lt - 1), ink, -1)
    cv2.line(bar, (sx0, sy - 5), (sx0, sy + lt + 4), ink, 1)       # ticks
    cv2.line(bar, (sx0 + scale_len - 1, sy - 5),
             (sx0 + scale_len - 1, sy + lt + 4), ink, 1)
    cv2.putText(bar, "20 um", (sx0 + scale_len // 3, max(10, sy - 8)),
                cv2.FONT_HERSHEY_SIMPLEX, fs * 0.8, ink, 1, cv2.LINE_AA)
    bar = np.clip(bar.astype(np.int16) + rng.integers(-3, 4, bar.shape),
                  0, 255).astype(np.uint8)
    out = gray.copy()
    if position == "bottom":
        y0 = h - bh
        out[y0:, :] = bar
        if separator:
            out[y0 - 2:y0, :] = ink
            y0 -= 2
        rect = (0, y0, w, h - y0)
        scale = (sx0, h - bh + sy, scale_len, lt)
    else:
        out[:bh, :] = bar
        y1 = bh
        if separator:
            out[bh:bh + 2, :] = ink
            y1 += 2
        rect = (0, 0, w, y1)
        scale = (sx0, sy, scale_len, lt)
    return out, rect, scale


@pytest.fixture(scope="module")
def mosaic():
    g, _ = make_mosaic()
    return g


# ---------------------------------------------------------------- detection

@pytest.mark.parametrize("position", ["bottom", "top"])
@pytest.mark.parametrize("bg", ["black", "white"])
@pytest.mark.parametrize("frac", [0.06, 0.10, 0.15])
def test_bar_detected(mosaic, position, bg, frac):
    img, rect, _ = add_bar(mosaic, position, bg, frac)
    res = detect_info_bar(img)
    assert res is not None, (position, bg, frac)
    assert len(res.bars) == 1
    bar = res.bars[0]
    assert bar.position == position
    assert bar.background == bg
    assert res.confidence >= 0.5
    x, y, w, h = bar.rect
    assert (x, w) == (0, img.shape[1])
    assert abs(y - rect[1]) <= 2 and abs(h - rect[3]) <= 2
    ax, ay, aw, ah = res.analysis_rect
    H = img.shape[0]
    if position == "bottom":
        assert ay == 0 and abs((ay + ah) - rect[1]) <= 2
        assert ay + ah <= rect[1] + 1          # never includes bar rows
    else:
        assert abs(ay - rect[3]) <= 2 and ay + ah == H
        assert ay >= rect[3] - 1


@pytest.mark.parametrize("bg", ["black", "white"])
def test_separator_line_is_part_of_bar(mosaic, bg):
    img, rect, _ = add_bar(mosaic, "bottom", bg, 0.08, separator=True)
    res = detect_info_bar(img)
    assert res is not None
    assert abs(res.bars[0].rect[1] - rect[1]) <= 1


def test_bgr_input_and_to_dict(mosaic):
    img, rect, _ = add_bar(mosaic, "bottom", "black", 0.1)
    bgr = np.repeat(img[:, :, None], 3, axis=2)
    res = detect_info_bar(bgr)
    assert res is not None
    d = res.to_dict()
    assert d["bar_rect"] == res.bars[0].rect
    assert set(d) == {"analysis_rect", "bar_rect", "confidence", "bars"}


def test_top_and_bottom_bars(mosaic):
    img, rb, _ = add_bar(mosaic, "bottom", "black", 0.08)
    img, rt, _ = add_bar(img, "top", "white", 0.06, seed=1)
    res = detect_info_bar(img)
    assert res is not None and len(res.bars) == 2
    ax, ay, aw, ah = res.analysis_rect
    assert abs(ay - rt[3]) <= 2 and abs(ay + ah - rb[1]) <= 2


def test_large_image_timing():
    g, _ = make_mosaic(h=768, w=1024, n_grains=200, seed=3)
    g = cv2.resize(g, (2048, 1536), interpolation=cv2.INTER_NEAREST)
    img, rect, _ = add_bar(g, "bottom", "black", 0.07)
    t = time.perf_counter()
    res = detect_info_bar(img)
    dt = time.perf_counter() - t
    assert res is not None and abs(res.bars[0].rect[1] - rect[1]) <= 2
    assert dt < 0.5, dt


# ---------------------------------------------------------- false positives

def test_no_bar_plain_mosaic(mosaic):
    assert detect_info_bar(mosaic) is None


def test_no_bar_dark_mosaic():
    g, _ = make_dark_mosaic()
    assert detect_info_bar(g) is None


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_no_bar_other_mosaics(seed):
    g, _ = make_mosaic(seed=seed, n_grains=40 + 20 * seed)
    assert detect_info_bar(g) is None


@pytest.mark.parametrize("val", [0, 255])
def test_no_bar_blob_at_bottom(mosaic, val):
    """A black (pore / drop-out) or saturated blob touching the bottom edge
    but not spanning the width — even one carrying bright specks."""
    g = mosaic.copy()
    h, w = g.shape
    cv2.ellipse(g, (w // 3, h - 10), (140, 70), 0, 0, 360, val, -1)
    g[h - 30:h - 27, w // 3 - 40:w // 3 + 40] = 255 - val   # "ink" specks
    assert detect_info_bar(g) is None


def test_no_bar_mosaic_darker_at_bottom(mosaic):
    """Vignetting / charging: intensity falls smoothly to near-black."""
    h, w = mosaic.shape
    ramp = np.ones(h)
    ramp[int(h * 0.6):] = np.linspace(1.0, 0.05, h - int(h * 0.6))
    g = (mosaic.astype(np.float64) * ramp[:, None]).astype(np.uint8)
    assert detect_info_bar(g) is None
    # also a step (not a ramp) to a dark but TEXTURED band, no annotation
    g2 = mosaic.copy()
    g2[int(h * 0.88):] = (g2[int(h * 0.88):] * 0.15).astype(np.uint8)
    assert detect_info_bar(g2) is None
    # ramp + bright particles in the dark zone (so "ink" is present)
    g3 = g.copy()
    for i in range(12):
        cv2.circle(g3, (20 + 40 * i, h - 20 - (i % 3) * 12), 3, 255, -1)
    assert detect_info_bar(g3) is None
    g4 = g2.copy()
    cv2.putText(g4, "x", (200, h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, 255, 1)
    assert detect_info_bar(g4) is None


def test_jpeg_compressed_bar(mosaic):
    for bg in ("black", "white"):
        img, rect, _ = add_bar(mosaic, "bottom", bg, 0.08)
        ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 60])
        j = cv2.imdecode(enc, cv2.IMREAD_GRAYSCALE)
        res = detect_info_bar(j)
        assert res is not None and abs(res.bars[0].rect[1] - rect[1]) <= 2


def test_featureless_black_band_left_to_valid_mask(mosaic_with_black_regions):
    """A black band with no annotation is not an *info bar*; DET-01's valid
    mask already excludes it (keeps test_black_regions untouched)."""
    img, _ = mosaic_with_black_regions
    assert detect_info_bar(img) is None


def test_tiny_or_empty_images():
    assert detect_info_bar(np.zeros((20, 20), np.uint8)) is None
    assert detect_info_bar(np.zeros((200, 200), np.uint8)) is None
    assert detect_info_bar(np.full((200, 200), 255, np.uint8)) is None


# ---------------------------------------------------- GrainDetector wiring

def _bgr(g):
    return np.repeat(g[:, :, None], 3, axis=2)


def test_analyze_excludes_bar(mosaic):
    img, rect, _ = add_bar(mosaic, "bottom", "white", 0.12)
    res = GrainDetector().analyze(_bgr(img), px_per_um=2.0)
    assert res.info_bar_rect is not None
    assert abs(res.info_bar_rect[1] - rect[1]) <= 2
    r0, c0, r1, c1 = res.auto_crop_rect
    assert r1 <= rect[1] + 1 and r0 == 0
    assert res.label_image.shape == (r1 - r0, c1 - c0)
    assert res.info_bar["confidence"] >= 0.5
    assert res.grain_count > 20


def test_analyze_flag_off_keeps_full_frame(mosaic):
    img, rect, _ = add_bar(mosaic, "bottom", "black", 0.10)
    p = DetectionParams(auto_exclude_info_bar=False)
    assert p.auto_exclude_info_bar is False
    assert DetectionParams().auto_exclude_info_bar is True
    res = GrainDetector().analyze(_bgr(img), params=p)
    assert res.info_bar_rect is None and res.auto_crop_rect is None
    assert res.label_image.shape == img.shape


def test_plain_mosaic_no_crop(mosaic):
    res = GrainDetector().analyze(_bgr(mosaic))
    assert res.info_bar_rect is None and res.auto_crop_rect is None


def test_auto_crop_legacy_call_matches_analyze(mosaic):
    """ui/workers.py calls det._auto_crop(gray) with no params before
    analyze(); with default params both must agree."""
    img, _, _ = add_bar(mosaic, "top", "black", 0.09)
    det = GrainDetector()
    legacy = det._auto_crop(img)
    res = det.analyze(_bgr(img))
    assert legacy == res.auto_crop_rect


def test_workers_full_frame_mapping(mosaic):
    """End-to-end through the UI helper: labels padded back to full frame
    never fall inside the bar."""
    from ui.workers import analyze_image
    img, rect, _ = add_bar(mosaic, "bottom", "black", 0.10)
    res = analyze_image(_bgr(img), px_per_um=1.0)
    assert res.label_image.shape == img.shape
    assert not res.label_image[rect[1]:, :].any()
    assert res.grain_count > 20


def test_white_border_and_bar(mosaic):
    """Legacy white-border crop still works and composes with the bar."""
    img, rect, _ = add_bar(mosaic, "bottom", "black", 0.10)
    h, w = img.shape
    framed = np.full((h + 80, w + 80), 255, np.uint8)
    framed[40:40 + h, 40:40 + w] = img
    r0, c0, r1, c1 = GrainDetector()._auto_crop(framed)
    assert c0 <= 40 and c1 >= 40 + w - 2
    assert r1 <= 40 + rect[1] + 1 and r1 >= 40 + rect[1] - 3


# ------------------------------------------------------- scale-bar line

@pytest.mark.parametrize("bg", ["black", "white"])
@pytest.mark.parametrize("position", ["bottom", "top"])
def test_scale_bar_line_in_info_bar(mosaic, bg, position):
    img, rect, scale = add_bar(mosaic, position, bg, 0.10)
    found = find_scale_bar_line(_bgr(img))
    assert found is not None
    assert abs(found["length_px"] - SCALE_LEN) <= 2
    x, y, w, h = found["rect"]
    assert abs(x - scale[0]) <= 2 and abs(y - scale[1]) <= 3
    assert found["info_bar_rect"] is not None


def test_scale_bar_line_absent(mosaic):
    assert find_scale_bar_line(_bgr(mosaic)) is None


def test_detect_scale_bar_length_px_uses_info_bar(mosaic):
    img, _, _ = add_bar(mosaic, "top", "black", 0.08)
    n, dbg = detect_scale_bar_length_px(_bgr(img))
    assert n is not None and abs(n - SCALE_LEN) <= 2
    assert dbg.shape == (*img.shape, 3)
