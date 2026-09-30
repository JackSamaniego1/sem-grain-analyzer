"""UPDATE 4 item 4 / item 11 (on-image half): read the SEM data bar with OCR.

Three groups:
  * parser tests on hand-made OCR tokens — always run (no OCR package needed);
  * end-to-end tests on synthetic renders of the two real layouts (JEOL,
    Thermo/Phenom) — skip when ``rapidocr_onnxruntime`` is not installed;
  * optional real-image test over ``scratch/real_sem/`` + ``expected.json``.
"""
import json
import os
import sys
import threading

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import info_bar_ocr as ibo                       # noqa: E402
from core.info_bar_ocr import OcrToken, parse_tokens, read_info_bar  # noqa: E402
from tests.sem_infobar_fixtures import render_jeol, render_thermo  # noqa: E402


def _has_rapidocr():
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


needs_ocr = pytest.mark.skipif(not _has_rapidocr(),
                               reason="rapidocr_onnxruntime not installed")


def T(text, x, y, w, h=20, score=0.98):
    return OcrToken(text=text, bbox=(x, y, w, h), score=score)


# ---------------------------------------------------------------------------
# Parser (engine-independent)
# ---------------------------------------------------------------------------

JEOL_TOKENS = [T("100nm JEOL", 718, 966, 120), T("9/14/2026", 1110, 966, 110),
               T("X30,000", 520, 996, 90), T("7.0kV", 640, 996, 60),
               T("LEI", 725, 996, 36), T("SEM", 785, 996, 40),
               T("WD9.7mm", 850, 996, 96), T("13:42:09", 1150, 996, 100)]
JEOL_BAR = (680, 974, 30, 8)

THERMO_TOKENS = [T("Mag.", 350, 678, 30, 14), T("FW", 430, 678, 20, 14),
                 T("HV", 503, 678, 20, 14), T("Int.", 560, 678, 22, 14),
                 T("Det.", 623, 678, 26, 14), T("WD", 706, 678, 22, 14),
                 T("Vac.", 792, 678, 26, 14), T("2025-04-21", 990, 678, 85, 14),
                 T("15 μm", 145, 696, 40, 14), T("10000x", 355, 696, 50, 14),
                 T("51.8 μm", 430, 696, 50, 14), T("15kV", 505, 696, 32, 14),
                 T("Image", 560, 696, 42, 14), T("BSDFulI", 623, 696, 60, 14),
                 T("8.947mm", 706, 696, 60, 14), T("0.10Pa", 792, 696, 46, 14),
                 T("stainlesssteel-image", 910, 696, 165, 14)]
THERMO_BAR = (8, 684, 316, 2)


def test_parse_jeol_layout():
    r = parse_tokens(JEOL_TOKENS, 1280, JEOL_BAR)
    assert (r.scale.value, r.scale.unit) == (100.0, "nm")
    assert r.magnification.value == 30000
    assert r.hv.value == 7.0 and r.hv.unit == "kV"
    assert r.wd.value == 9.7 and r.wd.unit == "mm"
    assert r.vendor.value == "JEOL"
    assert r.detector.value == "LEI"
    assert r.checks.get("magnification") is True
    # JEOL bars carry no FW: the magnification window alone (60-800 mm,
    # ~13x wide) cannot rule out a decade misread, so the value is flagged
    assert r.needs_confirmation
    assert r.scale.confidence < ibo._CONFIRM_BELOW
    assert "no independent check" in r.scale.note
    assert r.px_per_um == pytest.approx(300.0)


@pytest.mark.parametrize("label,mag", [
    ("10nm", "X30,000"),        # decade misread, outside the mag window
    ("1000nm", "X30,000"),
    ("1µm", "X5,000"),     # 10x misread that still lands inside it
])
def test_jeol_decade_misread_is_flagged(label, mag):
    toks = list(JEOL_TOKENS)
    toks[0] = T(label + " JEOL", 718, 966, 120)
    toks[2] = T(mag, 520, 996, 90)
    r = parse_tokens(toks, 1280, JEOL_BAR)
    assert r.scale.text.startswith(label[:-2])
    assert r.needs_confirmation
    assert r.scale.confidence < ibo._CONFIRM_BELOW


def test_ambiguous_pick_not_cleared_by_fw():
    # a second length label right next to the bar makes the pick ambiguous;
    # FW agreeing with the nearest one must not auto-accept it
    toks = list(THERMO_TOKENS) + [T("20 µm", 190, 696, 40, 14)]
    r = parse_tokens(toks, 1080, THERMO_BAR)
    assert r.scale.value == 15.0
    assert r.checks["field_width"] is True
    assert "several length labels" in r.scale.note
    assert r.needs_confirmation
    assert r.scale.confidence < ibo._CONFIRM_BELOW


def test_ambiguous_pick_cleared_by_exact_metadata():
    from core.sem_metadata import SemMetadata
    toks = list(THERMO_TOKENS) + [T("20 µm", 190, 696, 40, 14)]
    meta = SemMetadata(vendor="Thermo", pixel_size_um_x=15.0 / 316)
    r = parse_tokens(toks, 1080, THERMO_BAR, metadata=meta)
    assert r.checks["metadata"] is True
    assert not r.needs_confirmation


def test_bare_m_only_accepted_next_to_bar():
    toks = list(THERMO_TOKENS)
    toks[8] = T("15 m", 145, 696, 40, 14)            # mu lost, under the bar
    r = parse_tokens(toks, 1080, THERMO_BAR)
    assert (r.scale.value, r.scale.unit) == (15.0, "µm")
    assert r.needs_confirmation
    toks[8] = T("15 m", 560, 696, 40, 14)            # far from the bar
    r = parse_tokens(toks, 1080, THERMO_BAR)
    assert r.scale is None


@pytest.mark.parametrize("raw,val", [("1O0nm JEOL", 100.0), ("l00nm JEOL", 100.0),
                                     ("5O0 nm JEOL", 500.0), ("1|0nm JEOL", 110.0)])
def test_digit_misreads_repaired_and_flagged(raw, val):
    toks = list(JEOL_TOKENS)
    toks[0] = T(raw, 718, 966, 120)
    toks[2] = T("X3O,O00", 520, 996, 90)
    r = parse_tokens(toks, 1280, JEOL_BAR)
    assert r.scale.value == val and r.scale.unit == "nm"
    assert "repaired" in r.scale.note and r.needs_confirmation
    assert r.magnification.value == 30000
    assert r.vendor.value == "JEOL" and r.detector.value == "LEI"


def test_digit_repair_leaves_words_alone():
    for w in ("LEI", "Image", "Vol2", "stainless steel - image", "SE2", "IT500"):
        assert ibo._norm_text(w) == (w, False)


def test_parse_thermo_column_layout():
    r = parse_tokens(THERMO_TOKENS, 1080, THERMO_BAR)
    assert (r.scale.value, r.scale.unit) == (15.0, "µm")
    assert r.magnification.value == 10000
    assert r.hv.value == 15.0
    assert r.wd.value == pytest.approx(8.947) and r.wd.unit == "mm"
    assert (r.field_width.value, r.field_width.unit) == (51.8, "µm")
    assert r.detector.value == "BSD Full"
    assert r.vacuum.value == pytest.approx(0.10) and r.vacuum.unit == "Pa"
    assert r.checks == {"field_width": True, "magnification": True}
    assert not r.needs_confirmation
    assert r.vendor.value == "Thermo Fisher" and r.vendor.needs_confirmation


def test_merged_line_and_separators():
    toks = [T("— 100nm JEOL 9/14/2026", 690, 966, 520),
            T("X 30,000 7.0kV LEI SEM WD 9.7mm 13:42:09", 520, 996, 700)]
    r = parse_tokens(toks, 1280, JEOL_BAR)
    assert (r.scale.value, r.scale.unit) == (100.0, "nm")
    assert r.magnification.value == 30000 and r.wd.value == 9.7
    r = parse_tokens([T("Mag. = 10 000 ×  HFW 51.8 µm  15 µm", 10, 0, 400)],
                     1080, (8, 5, 316, 2))
    assert r.magnification.value == 10000
    assert r.field_width.value == 51.8
    assert r.scale.value == 15.0


@pytest.mark.parametrize("text,unit,fuzzy", [
    ("15 um", "µm", False), ("15µm", "µm", False), ("15 μm", "µm", False),
    ("15 pm", "µm", True), ("500 nrn", "nm", True), ("1 mm", "mm", False)])
def test_unit_variants(text, unit, fuzzy):
    toks = list(THERMO_TOKENS)
    toks[8] = T(text, 145, 696, 40, 14)
    r = parse_tokens(toks, 1080, THERMO_BAR)
    assert r.scale.unit == unit
    if fuzzy:
        assert r.scale.needs_confirmation


def test_wd_and_fw_never_taken_as_scale():
    # scale 100 µm with an equal-looking FW 100 µm and WD 10.0 mm right
    # beside the bar: only the label nearest the bar, unclaimed, is the scale
    toks = [T("FW 100 µm", 40, 700, 80), T("WD 10.0mm", 330, 700, 80),
            T("100 µm", 200, 700, 50)]
    r = parse_tokens(toks, 1080, (150, 690, 150, 3))
    assert r.scale.value == 100 and r.scale.bbox[0] >= 200
    assert r.wd.value == 10.0 and r.field_width.value == 100
    # only WD/FW present: no scale at all
    r = parse_tokens(toks[:2], 1080, (150, 690, 150, 3))
    assert r.scale is None and r.needs_confirmation


def test_label_left_of_bar():
    toks = [T("2µm", 640, 966, 36), T("JEOL", 740, 966, 50)]
    r = parse_tokens(toks, 1280, (686, 974, 30, 8))
    assert (r.scale.value, r.scale.unit) == (2.0, "µm")


def test_fw_disagreement_flags_scale():
    toks = list(THERMO_TOKENS)
    toks[8] = T("150 μm", 145, 696, 40, 14)          # decade misread
    r = parse_tokens(toks, 1080, THERMO_BAR)
    assert r.scale.value == 150
    assert r.checks["field_width"] is False and r.checks["magnification"] is False
    assert r.scale.needs_confirmation


def test_metadata_cross_check():
    from core.sem_metadata import SemMetadata
    good = SemMetadata(vendor="JEOL", pixel_size_um_x=1 / 300.0,
                       magnification=30000, working_distance_mm=9.7)
    r = parse_tokens(JEOL_TOKENS, 1280, JEOL_BAR, metadata=good)
    assert r.checks["metadata"] is True and not r.needs_confirmation
    bad = SemMetadata(vendor="JEOL", pixel_size_um_x=1 / 30.0,
                      working_distance_mm=15.0)
    r = parse_tokens(JEOL_TOKENS, 1280, JEOL_BAR, metadata=bad)
    assert r.checks["metadata"] is False and r.needs_confirmation
    assert r.wd.needs_confirmation


def test_no_bar_located_is_flagged():
    r = parse_tokens(JEOL_TOKENS, 1280, None)
    assert r.scale.value == 100 and r.scale.needs_confirmation
    assert r.px_per_um is None


def test_empty_tokens():
    r = parse_tokens([], 1280, JEOL_BAR)
    assert r.status == "no_text" and r.scale is None


# ---------------------------------------------------------------------------
# Engine-missing / robustness (no OCR package needed)
# ---------------------------------------------------------------------------

@pytest.fixture
def fresh_engine():
    ibo._reset_engine_cache()
    yield
    ibo._reset_engine_cache()


def test_engine_missing_is_graceful(monkeypatch, fresh_engine):
    def boom():
        raise ImportError("No module named 'rapidocr_onnxruntime'")
    monkeypatch.setattr(ibo, "_load_engine", boom)
    img, _ = render_jeol()
    r = read_info_bar(img)
    assert r.status == "engine_missing" and r.available is False
    assert "reinstall" in r.message.lower()
    assert "http" not in r.message.lower() and "download" not in r.message.lower()
    assert r.scale is None and r.needs_confirmation
    assert ibo.is_ocr_available() is False


@pytest.mark.parametrize("bad", [None, np.zeros((0, 0), np.uint8),
                                 "Z:/no/such/file.png", np.zeros((5,), np.uint8)])
def test_unreadable_input_never_raises(bad, monkeypatch, fresh_engine):
    monkeypatch.setattr(ibo, "_load_engine", lambda: (lambda img: []))
    r = read_info_bar(bad)
    assert r.status in ("error", "no_text") and r.scale is None


def test_fake_engine_end_to_end(monkeypatch, fresh_engine):
    """Strip coordinates from the engine are mapped back to the full frame."""
    img, info = render_jeol()
    y0 = img.shape[0] - info["bar_h"]

    def fake(strip):
        assert strip.shape[0] == info["bar_h"]
        return [(np.array([[718, 6], [838, 6], [838, 26], [718, 26]], float),
                 "100nm JEOL", 0.99)]
    monkeypatch.setattr(ibo, "_load_engine", lambda: fake)
    r = read_info_bar(img)
    assert r.scale.value == 100 and r.scale.bbox[1] == y0 + 6
    assert r.info_bar_rect == (0, y0, 1280, 64)
    assert r.scale_bar_px == 30


def _checking_engine(seen):
    def fake(strip):
        seen.append(strip)
        return [(np.array([[718, 6], [838, 6], [838, 26], [718, 26]], float),
                 "100nm JEOL", 0.99)]
    return fake


@pytest.mark.parametrize("kind", ["rgba", "uint16", "gray16"])
def test_rgba_and_16bit_inputs(kind, monkeypatch, fresh_engine):
    import cv2
    img, _ = render_jeol()
    if kind == "rgba":
        img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    elif kind == "uint16":
        img = img.astype(np.uint16) * 257
    else:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.uint16) * 257
        img[0, 0] = 65535            # a stretch would shift levels; >>8 must not
    seen = []
    monkeypatch.setattr(ibo, "_load_engine", lambda: _checking_engine(seen))
    r = read_info_bar(img)
    assert r.status == "ok" and r.scale.value == 100
    assert r.scale_bar_px == 30
    strip = seen[0]
    assert strip.dtype == np.uint8 and strip.ndim == 3 and strip.shape[2] == 3
    assert strip.max() == 255 and np.median(strip) == 0   # black bar, white ink


@needs_ocr
def test_ocr_on_16bit_input():
    img, _ = render_thermo()
    r = read_info_bar(img.astype(np.uint16) * 257)
    assert r.scale.value == 15.0 and not r.needs_confirmation


@pytest.mark.parametrize("shape", [(20, 30), (20, 30, 3), (1, 1), (39, 400, 3)])
def test_tiny_images(shape, monkeypatch, fresh_engine):
    monkeypatch.setattr(ibo, "_load_engine", lambda: (lambda img: []))
    r = read_info_bar(np.full(shape, 90, np.uint8))
    assert r.status in ("no_text", "error") and r.scale is None
    assert r.needs_confirmation


def test_no_info_bar_reads_bottom_strip_and_flags(monkeypatch, fresh_engine):
    img, info = render_jeol()
    micro = np.ascontiguousarray(img[:-info["bar_h"]])     # bar cropped off
    seen = []
    monkeypatch.setattr(ibo, "_load_engine", lambda: _checking_engine(seen))
    r = read_info_bar(micro)
    assert r.info_bar_rect is None and "No data bar" in r.message
    assert seen and seen[0].shape[0] == max(40, round(0.10 * micro.shape[0]))
    assert r.scale.value == 100 and r.needs_confirmation


# ---------------------------------------------------------------------------
# End-to-end with the real engine on synthetic renders
# ---------------------------------------------------------------------------

JEOL_CASES = [
    dict(),
    dict(scale_text="500nm", bar_px=150),
    dict(scale_text="2µm", bar_px=40, bar_side="right", mag_text="X 6,000"),
    dict(scale_text="1mm", bar_px=128, mag_text="X 40", wd_text="WD 10.0mm"),
    dict(scale_text="10µm", bar_px=60, wd_text="WD 10.0mm", mag_text="X 2,000"),
]


@needs_ocr
@pytest.mark.parametrize("kw", JEOL_CASES)
def test_ocr_jeol_layout(kw):
    img, info = render_jeol(**kw)
    r = read_info_bar(img)
    text = kw.get("scale_text", "100nm")
    num = float(text.rstrip("nmµu"))
    unit = "µm" if "µ" in text else text[-2:]
    assert r.status == "ok", r.message
    assert (r.scale.value, r.scale.unit) == (num, unit)
    assert r.scale_bar_px == kw.get("bar_px", 30)
    wd = float(kw.get("wd_text", "WD 9.7mm")[3:-2])
    assert r.wd.value == wd and r.wd.unit == "mm"
    assert r.hv.value == 7.0
    assert r.magnification.value == float(
        kw.get("mag_text", "X 30,000")[2:].replace(",", ""))
    assert r.vendor.value == "JEOL" and r.detector.value == "LEI"
    # the scale label is never WD / magnification text
    assert r.scale.bbox != r.wd.bbox and r.scale.bbox != r.magnification.bbox
    # consistent with magnification, but JEOL has no FW -> still flagged
    assert r.checks.get("magnification") is True
    assert r.needs_confirmation


THERMO_CASES = [
    dict(),
    dict(scale_text="2 µm", bar_x1=8 + 216, mag_value="50 000 ×",
         fw_value="10.0 µm"),
    dict(scale_text="500 nm", bar_x1=8 + 216, mag_value="200 000 ×",
         fw_value="2.50 µm"),
    dict(scale_text="1 mm", bar_x1=8 + 216, mag_value="100 ×",
         fw_value="5.00 mm", wd_value="10.0 mm"),
    # distractor: FW 100 µm is a round, equal-looking length
    dict(scale_text="30 µm", bar_x1=8 + 324, mag_value="5 000 ×",
         fw_value="100 µm", wd_value="10.0 mm"),
]


@needs_ocr
@pytest.mark.parametrize("kw", THERMO_CASES)
def test_ocr_thermo_layout(kw):
    img, _ = render_thermo(**kw)
    r = read_info_bar(img)
    num, unit = kw.get("scale_text", "15 µm").split()
    assert r.status == "ok", r.message
    assert (r.scale.value, r.scale.unit) == (float(num), unit)
    fwn, fwu = kw.get("fw_value", "51.8 µm").split()
    assert (r.field_width.value, r.field_width.unit) == (float(fwn), fwu)
    wdn = float(kw.get("wd_value", "8.947 mm").split()[0])
    assert r.wd.value == pytest.approx(wdn)
    assert r.magnification.value == float(
        kw.get("mag_value", "10 000 ×")[:-2].replace(" ", ""))
    assert r.hv.value == 15.0
    assert r.detector.value == "BSD Full"
    assert r.checks.get("field_width") is True
    assert not r.needs_confirmation, r.scale.note


@needs_ocr
def test_ocr_runs_with_offline_guard_and_no_network(monkeypatch, fresh_engine):
    from core import offline_guard
    events = []
    orig_log = offline_guard._log
    monkeypatch.setattr(offline_guard, "_log",
                        lambda m: (events.append(m), orig_log(m)))
    was = offline_guard.is_installed()
    offline_guard.install()
    try:
        img, _ = render_thermo()
        r = read_info_bar(img)             # engine is (re)loaded under guard
    finally:
        if not was:
            offline_guard.uninstall()
    assert r.status == "ok" and r.scale.value == 15.0
    assert not [e for e in events if "BLOCKED" in e]
    # the engine really loaded under the guard; no swallowed load error
    assert ibo._ENGINE_ERROR is None and ibo._ENGINE is not None


@needs_ocr
def test_ocr_speed_and_thread_safety():
    img_a, _ = render_jeol()
    img_b, _ = render_thermo()
    read_info_bar(img_a)                   # warm-up (engine load)
    r = read_info_bar(img_a)
    assert r.elapsed_s < 1.5, r.elapsed_s
    out = {}

    def work(k, img):
        out[k] = read_info_bar(img)
    th = [threading.Thread(target=work, args=(k, im))
          for k, im in (("a", img_a), ("b", img_b))]
    for t in th:
        t.start()
    for t in th:
        t.join(60)
    assert out["a"].scale.value == 100 and out["b"].scale.value == 15


# ---------------------------------------------------------------------------
# Optional: the user's real exported images
# ---------------------------------------------------------------------------

REAL_DIR = os.path.join(ROOT, "scratch", "real_sem")
_REAL_EXT = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp")


def _real_cases():
    exp_path = os.path.join(REAL_DIR, "expected.json")
    if not os.path.isfile(exp_path):
        return []
    with open(exp_path, encoding="utf-8") as fh:
        expected = json.load(fh)
    return [(name, expected[name]) for name in sorted(os.listdir(REAL_DIR))
            if name.lower().endswith(_REAL_EXT) and name in expected]


@needs_ocr
@pytest.mark.skipif(not _real_cases(),
                    reason="no scratch/real_sem/ images with expected.json")
@pytest.mark.parametrize("name,exp", _real_cases() or [("-", {})])
def test_real_sem_images(name, exp):
    """``scratch/real_sem/expected.json`` maps file name -> any of
    ``scale_value, scale_unit, magnification, hv_kv, wd_mm, vendor,
    detector``; only the keys present are checked."""
    r = read_info_bar(os.path.join(REAL_DIR, name))
    assert r.status == "ok", r.message
    got = {
        "scale_value": r.scale and r.scale.value,
        "scale_unit": r.scale and r.scale.unit.replace("µ", "u"),
        "magnification": r.magnification and r.magnification.value,
        "hv_kv": r.hv and r.hv.value,
        "wd_mm": r.wd and r.wd.value,
        "vendor": r.vendor and r.vendor.value,
        "detector": r.detector and r.detector.value,
    }
    for key, want in exp.items():
        if key not in got:
            continue
        have = got[key]
        if key == "scale_unit":
            want = str(want).replace("µ", "u").replace("μ", "u")
        if isinstance(want, (int, float)):
            assert have == pytest.approx(want, rel=1e-3), (key, have, want)
        else:
            assert str(want).lower() in str(have).lower(), (key, have, want)
