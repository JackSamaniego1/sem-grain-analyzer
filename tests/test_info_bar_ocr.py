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
from tests.sem_infobar_fixtures import (render_jeol, render_thermo,  # noqa: E402
                                        render_thermo_databar)


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
    # JEOL bars carry no FW, but with the vendor READ from the bar the
    # JEOL reference width applies: 100 nm * 1280 / 30 px * 30,000 = 128 mm
    # (within 110-135 mm) -> independently confirmed
    assert r.checks.get("magnification_reference") is True
    assert not r.needs_confirmation
    assert r.scale.confidence >= ibo._CONFIRM_BELOW
    assert r.px_per_um == pytest.approx(300.0)


def test_jeol_reference_needs_vendor_read_from_text():
    # no "JEOL" word: only the loose 60-800 mm window applies -> flagged
    toks = [T("100nm", 718, 966, 60)] + JEOL_TOKENS[1:]
    r = parse_tokens(toks, 1280, JEOL_BAR)
    assert r.vendor is None and "magnification_reference" not in r.checks
    assert r.checks.get("magnification") is True
    assert r.needs_confirmation
    assert "no independent check" in r.scale.note


# the user's real JEOL export (1A-1-GS-BM1.jpg): solid 32x15 bar, label
# ~140 px to its right on the same line, 1280 px wide, x30,000
REAL_JEOL_TOKENS = [T("100nm JEOL", 850, 964, 171, 22), T("9/14/2026", 1106, 962, 152, 24),
                    T("X 30,000", 322, 995, 135, 22), T("7.0kV", 546, 995, 86, 21),
                    T("LEI", 661, 996, 54, 21), T("SEM", 816, 996, 55, 21),
                    T("WD", 967, 996, 39, 21), T("9.7mm", 1017, 995, 91, 22),
                    T("13:42:09", 1140, 995, 134, 22)]
REAL_JEOL_BAR = (678, 963, 32, 15)


def test_jeol_label_on_bar_line_is_paired_across_gap():
    r = parse_tokens(REAL_JEOL_TOKENS, 1280, REAL_JEOL_BAR)
    assert (r.scale.value, r.scale.unit) == (100.0, "nm")
    assert "label on the bar's text line" in r.scale.note
    assert "far" not in r.scale.note
    assert r.checks == {"magnification": True, "magnification_reference": True}
    assert not r.needs_confirmation
    # the old mis-pick (a glyph stroke under "LEI") is no longer paired and
    # the magnification reference rejects it anyway -> flagged
    r = parse_tokens(REAL_JEOL_TOKENS, 1280, (664, 1011, 16, 3))
    assert r.needs_confirmation


def test_jeol_word_between_bar_and_label_is_not_paired():
    toks = list(REAL_JEOL_TOKENS) + [T("ABC", 760, 964, 50, 22)]
    r = parse_tokens(toks, 1280, REAL_JEOL_BAR)
    assert "label far from the scale bar" in r.scale.note
    assert r.needs_confirmation


# the user's real Thermo data bar (thermo_databar_logo_100um.png), 768 px
DATABAR_TOKENS = [T("HV", 63, 513, 21, 14), T("curr", 139, 515, 27, 12),
                  T("det", 200, 512, 24, 16), T("HFW", 246, 513, 32, 14),
                  T("100μm", 513, 512, 48, 18), T("15.00kV", 65, 531, 56, 12),
                  T("1.1nA", 140, 530, 43, 14), T("CBS", 201, 530, 28, 14),
                  T("276μm", 246, 530, 51, 16)]
DATABAR_BAR = (397, 520, 279, 1)


def test_hfw_validates_joined_split_line():
    r = parse_tokens(DATABAR_TOKENS, 768, DATABAR_BAR)
    assert (r.scale.value, r.scale.unit) == (100.0, "µm")
    assert (r.field_width.value, r.field_width.unit) == (276.0, "µm")
    # 276 um * 279 / 768 = 100.3 um
    assert r.checks["field_width"] is True
    assert not r.needs_confirmation
    # only one half of the line (the pre-fix measurement) disagrees with HFW
    r = parse_tokens(DATABAR_TOKENS, 768, (397, 520, 114, 1))
    assert r.checks["field_width"] is False and r.needs_confirmation


@pytest.mark.parametrize("toks,want_na,flagged", [
    (DATABAR_TOKENS, 1.1, False),                                     # column
    ([T("curr", 139, 515, 27, 12), T("250 pA", 140, 530, 43, 14)], 0.25, False),
    ([T("Curr: 2.5 µA", 10, 10, 90)], 2500.0, False),                 # inline
    ([T("HV 5 kV 80pA", 10, 10, 90)], 0.08, False),                    # bare unit
    ([T("curr", 139, 515, 27, 12), T("40", 140, 530, 20, 14)], 40.0, True),
])
def test_beam_current_parsing(toks, want_na, flagged):
    r = parse_tokens(toks, 768, None)
    assert r.beam_current is not None
    assert r.beam_current_na == pytest.approx(want_na)
    assert r.beam_current.needs_confirmation is flagged
    assert r.to_dict()["beam_current_na"] == pytest.approx(want_na)
    # a current is never taken as a scale, a magnification or a vacuum
    # ("pA" is not "Pa")
    assert r.magnification is None and r.vacuum is None
    assert r.scale is None or r.scale.text == "100µm"


def test_logo_names_vendor_layout_guess_stays_flagged():
    from core.vendor_logo import LogoMatch
    r = parse_tokens(DATABAR_TOKENS, 768, DATABAR_BAR)
    assert r.vendor.value == "Thermo Fisher" and r.vendor.needs_confirmation
    assert "layout" in r.vendor.note
    logo = LogoMatch("Thermo Fisher", (11, 514, 33, 30), 0.92, 0.57)
    r = parse_tokens(DATABAR_TOKENS, 768, DATABAR_BAR, logo=logo)
    assert r.vendor.value == "Thermo Fisher" and not r.vendor.needs_confirmation
    assert r.vendor.note == "identified from the logo"
    assert r.vendor.bbox == (11, 514, 33, 30)
    # vendor text on the bar wins over the logo
    r = parse_tokens(DATABAR_TOKENS + [T("FEI", 600, 530, 30, 14)], 768,
                     DATABAR_BAR, logo=logo)
    assert r.vendor.value == "FEI/Thermo Fisher" and r.vendor.note == ""


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
# Scale-bar locator and logo (image only, no OCR package needed)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kw", [dict(), dict(bar_px=300, bar_x0=380,
                                             scale_text="50 µm"),
                                dict(line_level=200), dict(line_level=110)])
def test_split_scale_line_is_joined(kw):
    from core.scale_bar import find_scale_bar_line
    img, info = render_thermo_databar(**kw)
    found = find_scale_bar_line(img)
    assert found is not None
    x, y, w, h = found["rect"]
    bx, by, bw, _ = info["bar_rect"]
    assert w == pytest.approx(bw, abs=1) and x == pytest.approx(bx, abs=1)
    assert abs(y - by) <= 1


def _ink(w=400, h=30):
    return np.zeros((h, w), np.uint8)


def test_split_join_needs_label_and_similar_halves():
    from core.scale_bar import _line_candidates
    ink = _ink()
    ink[10, 20:140] = 255
    ink[10, 200:320] = 255
    ink[4:17, 20] = 255                              # end ticks
    ink[4:17, 319] = 255
    # no label in the gap -> two separate lines
    assert [s[2] for s in _line_candidates(ink, 8, 380)] == [120, 120]
    ink[5:16, 150:190:4] = 255                       # label-like ink in the gap
    assert [s[2] for s in _line_candidates(ink, 8, 380)] == [300]
    # very unequal halves (label not centred) are not one bar
    ink2 = _ink()
    ink2[10, 20:44] = 255
    ink2[10, 80:300] = 255
    ink2[4:17, 20] = 255
    ink2[4:17, 299] = 255
    ink2[5:16, 50:75:4] = 255
    assert [s[2] for s in _line_candidates(ink2, 8, 380)][0] == 220


def test_dashes_with_digit_between_are_not_joined():
    """A dimension line / underline "------ 5 ------" without end ticks is
    not a split scale bar; an OCR word box in the gap (OCR path only) is
    evidence enough."""
    from core.scale_bar import _line_candidates
    ink = _ink()
    ink[10:12, 20:140] = 255
    ink[10:12, 200:320] = 255
    ink[4:18, 165:170] = 255                         # the digit
    ink[4:6, 162:173] = 255
    assert [s[2] for s in _line_candidates(ink, 8, 380)] == [120, 120]
    assert [s[2] for s in _line_candidates(ink, 8, 380, join_split=False)] \
        == [120, 120]
    box = [(160, 3, 15, 16)]
    assert [s[2] for s in _line_candidates(ink, 8, 380, text_boxes=box)] == [300]


def _bright_strip_with_bar(tick):
    """Bar-less micrograph with bright grains: a white 200 px bar drawn over
    the bottom strip, grains touching it (the fallback-strip path)."""
    import cv2
    rng = np.random.default_rng(11)
    img = cv2.resize(rng.integers(40, 170, (40, 50)).astype(np.uint8),
                     (1000, 800), interpolation=cv2.INTER_NEAREST)
    for cx in range(110, 300, 22):                  # bright grains on the bar
        cv2.circle(img, (cx, 752 if cx % 44 else 734), 9, 235, -1)
    for cx in range(420, 900, 60):
        cv2.circle(img, (cx, 700), 12, 230, -1)
    if tick:
        img[740:748, 100:300] = 255
        img[728:748, 100:103] = 255
        rect = (100, 740, 200, 8)
    else:
        img[740:743, 100:300] = 255
        rect = (100, 740, 200, 3)
    return img, rect


@pytest.mark.parametrize("tick", [False, True])
def test_fallback_strip_bar_with_touching_grains_still_found(tick):
    from core.infobar import detect_info_bar
    from core.scale_bar import find_scale_bar_line
    img, rect = _bright_strip_with_bar(tick)
    assert detect_info_bar(img) is None
    found = find_scale_bar_line(img, fallback_strip=True)
    assert found is not None and found["source"] == "bottom_strip"
    assert found["rect"] == rect


def test_equal_length_tie_keeps_scan_order():
    from core.scale_bar import _horizontal_segments, _line_candidates
    ink = _ink(200, 40)
    ink[5:7, 20:60] = 255                            # 40 x 2
    ink[25:33, 120:160] = 255                        # 40 x 8
    first = _horizontal_segments(ink, 10, 190)[0]
    assert _line_candidates(ink, 10, 190)[0] == first
    assert _line_candidates(ink, 10, 190, reject_glyphs=False,
                            join_split=False)[0] == first


@pytest.mark.parametrize("kind", ["uint16", "gray16", "rgba", "float"])
def test_scale_bar_locator_accepts_16bit_and_rgba(kind):
    import cv2
    from core.scale_bar import find_scale_bar_line
    img, info = render_jeol()
    if kind == "uint16":
        img = img.astype(np.uint16) * 257
    elif kind == "gray16":
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.uint16) * 257
    elif kind == "rgba":
        img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    else:
        img = img.astype(np.float32) / 255.0
    found = find_scale_bar_line(img)
    assert found is not None and found["rect"] == info["bar_rect"]
    img2, rect = _bright_strip_with_bar(False)
    found = find_scale_bar_line(img2.astype(np.uint16) * 257,
                                fallback_strip=True)
    assert found is not None and found["rect"] == rect


def test_glyph_stroke_is_not_a_bar():
    """A bold "LE" whose feet form a run as long as the bar (the real JEOL
    mis-pick) is rejected; the solid bar above it is found."""
    from core.scale_bar import find_scale_bar_line
    img, info = render_jeol(bar_px=32, bar_thick=15, label_gap=140,
                            decoy_under_det=True)
    found = find_scale_bar_line(img)
    assert found is not None and found["rect"] == info["bar_rect"]
    from core.scale_bar import _line_candidates
    ink = _ink(120, 40)
    ink[20:23, 10:42] = 255                          # 32 px foot
    ink[2:20, 10:13] = 255                           # L stem (end)
    ink[2:20, 24:27] = 255                           # E stem (interior)
    ink[2:5, 24:40] = 255
    ink[11:14, 24:38] = 255
    ink[5:12, 70:102] = 255                          # solid bar
    assert _line_candidates(ink, 10, 110) == [(70, 5, 32, 7)]


def test_bar_candidate_inside_a_word_is_skipped(monkeypatch):
    import core.scale_bar as sb
    decoy = {"length_px": 40, "rect": (664, 1011, 40, 3)}
    bar = {"length_px": 32, "rect": (678, 963, 32, 15)}
    monkeypatch.setattr(sb, "find_scale_bar_candidates",
                        lambda img, ib=None, fb=False, text_boxes=None:
                        [decoy, bar])
    boxes = [t.bbox for t in REAL_JEOL_TOKENS]
    assert ibo._pick_scale_bar(None, object(), boxes) == (678, 963, 32, 15)
    # a split line whose label box covers its middle is NOT "inside a word"
    assert not ibo._inside_word(DATABAR_BAR, [t.bbox for t in DATABAR_TOKENS])


def _gray_bar(img, bar_h):
    import cv2
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    H, W = g.shape
    return g, (0, H - bar_h, W, bar_h)


def test_logo_detected_on_thermo_databar():
    from core.vendor_logo import detect_vendor_logo
    img, info = render_thermo_databar()
    g, rect = _gray_bar(img, info["bar_h"])
    m = detect_vendor_logo(g, rect, 46)
    assert m is not None and m.vendor == "Thermo Fisher"
    cx, cy = info["logo_center"]
    x, y, w, h = m.bbox
    assert x <= cx <= x + w and y <= cy <= y + h
    # a word box over the mark suppresses it
    assert detect_vendor_logo(g, rect, 46, [m.bbox]) is None


@pytest.mark.parametrize("case", ["jeol", "thermo", "databar_no_logo",
                                  "glyphs", "symbols"])
def test_no_false_logo(case):
    from core.infobar import detect_info_bar
    from core.vendor_logo import detect_vendor_logo
    if case == "jeol":
        img, _ = render_jeol()
    elif case == "thermo":
        img, _ = render_thermo()
    elif case == "databar_no_logo":
        img, _ = render_thermo_databar(logo=False)
    elif case == "glyphs":
        img, _ = render_thermo_databar(logo=False, left_glyphs="@8#&B")
    else:
        img, _ = render_thermo_databar(logo=False,
                                       left_glyphs="✱⊕☸")
    import cv2
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ib = detect_info_bar(g)
    assert ib is not None and ib.bars
    for b in ib.bars:
        assert detect_vendor_logo(g, b.rect, b.background_value) is None


def test_atom_shape_scores_separate_logo_from_symbols():
    """Gate values on isolated blobs: the drawn mark passes, radial / ring
    symbols with many holes fail on strokes or on the enclosed cells."""
    import cv2
    from PIL import Image, ImageDraw
    from core.vendor_logo import is_atom_logo
    from tests.sem_infobar_fixtures import _draw_atom_mark, _font

    def blob(gray):
        ink = (gray > 120).astype(np.uint8) * 255
        n, lab, st, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
        if n < 2:                     # glyph missing from the fallback font
            return None
        i = 1 + int(np.argmax(st[1:, 4]))
        x, y, w, h = st[i][:4]
        return (lab[y:y + h, x:x + w] == i).astype(np.uint8) * 255

    g = np.full((60, 60), 46, np.uint8)
    _draw_atom_mark(g, 30, 30, 31)
    assert is_atom_logo(blob(g))[0]
    for ch in ("@", "8", "#", "&", "✱", "⊕", "⊗"):
        im = Image.new("L", (70, 70), 0)
        ImageDraw.Draw(im).text((6, 6), ch, fill=255, font=_font(
            ["seguisym.ttf", "arialbd.ttf"], 34))
        b = blob(np.asarray(im))
        assert b is None or not is_atom_logo(b)[0], ch


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
    # consistent with the loose magnification window; auto-accepted only
    # when the implied reference width FW * M is JEOL's (110-135 mm)
    assert r.checks.get("magnification") is True
    fw_mm = num * ibo._LEN_TO_UM[unit] * 1280 / r.scale_bar_px / 1000.0
    ref_ok = 110 <= fw_mm * r.magnification.value <= 135
    assert r.checks.get("magnification_reference") is ref_ok
    assert r.needs_confirmation is (not ref_ok)


@needs_ocr
def test_ocr_jeol_far_label_and_glyph_decoy():
    """The real-image mis-pick: a solid 32x15 bar ~140 px left of its label
    on line 1, and a 32 px horizontal glyph run under "LEI" on line 2."""
    img, info = render_jeol(bar_px=32, bar_thick=15, label_gap=140,
                            decoy_under_det=True)
    r = read_info_bar(img)
    assert r.scale_bar_rect == info["bar_rect"]
    assert (r.scale.value, r.scale.unit) == (100.0, "nm")
    assert r.vendor.value == "JEOL" and r.detector.value == "LEI"
    assert r.checks.get("magnification_reference") is True
    assert not r.needs_confirmation, r.scale.note


DATABAR_CASES = [
    dict(),
    dict(scale_text="50 µm", bar_px=300, hfw_value="128 µm",
         curr_value="250 pA", bar_x0=380),
    dict(scale_text="10 µm", bar_px=256, hfw_value="30.0 µm",
         curr_value="0.40 nA", bar_x0=420, line_level=200),   # bright line
]


@needs_ocr
@pytest.mark.parametrize("kw", DATABAR_CASES)
def test_ocr_thermo_databar_layout(kw):
    img, info = render_thermo_databar(**kw)
    r = read_info_bar(img)
    num, unit = kw.get("scale_text", "100 µm").split()
    assert r.status == "ok", r.message
    assert (r.scale.value, r.scale.unit) == (float(num), unit)
    assert r.scale_bar_px == pytest.approx(info["bar_rect"][2], abs=1)
    assert r.checks.get("field_width") is True
    assert not r.needs_confirmation, r.scale.note
    assert r.hv.value == 15.0 and r.detector.value == "CBS"
    cv, cu = kw.get("curr_value", "1.1 nA").split()
    assert r.beam_current_na == pytest.approx(
        float(cv) * {"pA": 1e-3, "nA": 1.0}[cu])
    assert r.vendor.value == "Thermo Fisher"
    assert not r.vendor.needs_confirmation
    assert r.vendor.note == "identified from the logo"


@needs_ocr
def test_ocr_thermo_databar_without_logo_vendor_flagged():
    img, _ = render_thermo_databar(logo=False)
    r = read_info_bar(img)
    assert r.vendor.value == "Thermo Fisher" and r.vendor.needs_confirmation
    assert "layout" in r.vendor.note
    assert r.scale.value == 100.0 and not r.needs_confirmation


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
    detector, beam_current_na, scale_bar_px, scale_bar_rect,
    needs_confirmation`` (the scale's flag); ``scale_bar_px_tol`` (px,
    default 2) applies to ``scale_bar_px`` and to every value of
    ``scale_bar_rect``.  Only the keys present are checked."""
    r = read_info_bar(os.path.join(REAL_DIR, name))
    assert r.status == "ok", r.message
    tol = float(exp.get("scale_bar_px_tol", 2))
    if "scale_bar_px" in exp:
        assert r.scale_bar_px is not None, "scale bar not located"
        assert abs(r.scale_bar_px - exp["scale_bar_px"]) <= tol, \
            (r.scale_bar_px, exp["scale_bar_px"], tol)
    if "scale_bar_rect" in exp:
        assert r.scale_bar_rect is not None, "scale bar not located"
        for have, want in zip(r.scale_bar_rect, exp["scale_bar_rect"]):
            assert abs(have - want) <= tol, (r.scale_bar_rect, exp["scale_bar_rect"])
    if "needs_confirmation" in exp:
        assert r.needs_confirmation is bool(exp["needs_confirmation"]), \
            r.scale and r.scale.note
    got = {
        "scale_value": r.scale and r.scale.value,
        "scale_unit": r.scale and r.scale.unit.replace("µ", "u"),
        "magnification": r.magnification and r.magnification.value,
        "hv_kv": r.hv and r.hv.value,
        "wd_mm": r.wd and r.wd.value,
        "vendor": r.vendor and r.vendor.value,
        "detector": r.detector and r.detector.value,
        "beam_current_na": r.beam_current_na,
    }
    for key, want in exp.items():
        if key not in got:
            continue
        assert got[key] is not None, (key, "not read")
        have = got[key]
        if key == "scale_unit":
            want = str(want).replace("µ", "u").replace("μ", "u")
        if isinstance(want, (int, float)):
            assert have == pytest.approx(want, rel=1e-3), (key, have, want)
        else:
            assert str(want).lower() in str(have).lower(), (key, have, want)
