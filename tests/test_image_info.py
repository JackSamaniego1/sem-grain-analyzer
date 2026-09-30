"""core.image_info: metadata-first acquisition info with info-bar OCR fallback
(UPDATE 4 item 11, core half).

The OCR engine is replaced by a fake that returns fixed tokens (strip
coordinates) so these tests are fast and do not need rapidocr; one optional
test runs the real engine end to end.
"""
from __future__ import annotations

import json
import os
import sys
import threading

import cv2
import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import image_info as ii                           # noqa: E402
from core import info_bar_ocr as ibo                        # noqa: E402
from core.image_info import ImageInfo, read_image_info      # noqa: E402
from tests.sem_infobar_fixtures import render_jeol, render_thermo  # noqa: E402

JEOL_SIDECAR = ("$CM_FORMAT JEOL/SEM\n$CM_VERSION 1.0\n"
                "$CM_INSTRUMENT JSM-7800F\n$CM_SIGNAL SEI\n$CM_MAG {mag}\n"
                "$CM_ACCEL_VOLT {kv}\n$CM_DATE 2019/03/12\n"
                "$CM_TIME 10:21:55\n$CM_FULL_SIZE 1280 960\n"
                "$$SM_MICRON_BAR 180\n$$SM_MICRON_MARKER 5um\n{wd}")
FEI = ("[User]\r\nDate=03/12/2019\r\nTime=10:22:13 AM\r\n\r\n"
       "[System]\r\nType=DualBeam\r\nSystemType=Helios G4 UX\r\n"
       "DisplayWidth=0.4\r\n\r\n[EBeam]\r\nHV=5000\r\nWD=0.00412\r\n\r\n"
       "[Scan]\r\nPixelWidth=2.0345e-009\r\nPixelHeight=2.0345e-009\r\n"
       "HorFieldsize=2.0833e-006\r\n\r\n[Detectors]\r\nName=TLD\r\n"
       "Mode=SE\r\n")

# (text, x, y, w, h, score) in STRIP coordinates of render_jeol (bar 64 px)
JEOL_STRIP = [("100nm JEOL", 718, 6, 120, 20), ("9/14/2026", 1110, 6, 110, 20),
              ("X30,000", 520, 36, 90, 20), ("7.0kV", 640, 36, 60, 20),
              ("LEI", 725, 36, 36, 20), ("SEM", 785, 36, 40, 20),
              ("WD9.7mm", 850, 36, 96, 20), ("13:42:09", 1150, 36, 100, 20)]
# render_thermo (bar 42 px, label row y=3, value row y=21)
THERMO_STRIP = [("Mag.", 350, 3, 30, 14), ("FW", 430, 3, 20, 14),
                ("HV", 503, 3, 20, 14), ("Det.", 623, 3, 26, 14),
                ("WD", 706, 3, 22, 14), ("15 µm", 145, 21, 40, 14),
                ("10000x", 355, 21, 50, 14), ("51.8 µm", 430, 21, 50, 14),
                ("15kV", 505, 21, 32, 14), ("BSD", 623, 21, 40, 14),
                ("8.947mm", 706, 21, 60, 14)]


def _fake_engine(tokens, score=0.98, overrides=None):
    overrides = overrides or {}

    def run(strip):
        out = []
        for text, x, y, w, h in tokens:
            pts = np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]],
                           float)
            out.append((pts, text, overrides.get(text, score)))
        return out
    return run


@pytest.fixture
def engine(monkeypatch):
    """Install a fake OCR engine; returns a setter."""
    ibo._reset_engine_cache()

    def install(tokens=JEOL_STRIP, **kw):
        ibo._reset_engine_cache()
        monkeypatch.setattr(ibo, "_load_engine",
                            lambda: _fake_engine(tokens, **kw))
    yield install
    ibo._reset_engine_cache()


def _save(path, img):
    ok, buf = cv2.imencode(os.path.splitext(str(path))[1], img)
    assert ok
    buf.tofile(str(path))
    return str(path)


def _jeol_png(tmp_path, name="j.png", sidecar=None):
    img, _ = render_jeol()
    p = _save(tmp_path / name, img)
    if sidecar is not None:
        (tmp_path / (os.path.splitext(name)[0] + ".txt")).write_text(
            sidecar, encoding="latin-1")
    return p, img


def _sidecar(mag=30000, kv="7.00", wd="$$SM_WD 9.7\n"):
    return JEOL_SIDECAR.format(mag=mag, kv=kv, wd=wd)


# --------------------------------------------------------------- metadata

def test_jeol_sidecar_metadata_only(tmp_path):
    p = str(tmp_path / "j01.bmp")
    cv2.imwrite(p, np.zeros((64, 80), np.uint8))
    (tmp_path / "j01.txt").write_text(_sidecar(5000, "15.00", "$$SM_WD 10.0\n"),
                                      encoding="latin-1")
    info = read_image_info(p, use_ocr=False)
    assert info.instrument == "JEOL JSM-7800F" and info.vendor == "JEOL"
    assert info.magnification == 5000
    assert info.accelerating_voltage_kv == 15.0
    assert info.working_distance_mm == pytest.approx(10.0)
    assert info.detector == "SEI"
    for k in ("instrument", "vendor", "magnification", "accelerating_voltage_kv",
              "working_distance_mm", "detector"):
        assert info.source[k] == "metadata" and info.needs_check[k] is False
    assert info.source["scale_label"] is None and info.scale_label_value is None
    assert info.ocr_status == "not_run" and not info.any_needs_check
    assert "j01.txt" in info.metadata_source


@pytest.mark.parametrize("tag", [34682, 34680])
def test_thermo_tiff_tags_metadata_only(tmp_path, tag):
    tifffile = pytest.importorskip("tifffile")
    p = str(tmp_path / "t.tif")
    data = FEI.encode("latin-1")
    tifffile.imwrite(p, np.zeros((64, 80), np.uint8),
                     extratags=[(tag, 1, len(data), data, True)])
    info = read_image_info(p, use_ocr=False)
    assert info.vendor == "FEI/Thermo Fisher"
    assert info.instrument == "FEI/Thermo Fisher Helios G4 UX"
    assert info.accelerating_voltage_kv == 5.0
    assert info.working_distance_mm == pytest.approx(4.12)
    assert info.magnification == pytest.approx(0.4 / 2.0833e-6, rel=1e-3)
    assert info.detector == "TLD SE"
    assert all(info.source[k] == "metadata" for k in
               ("magnification", "accelerating_voltage_kv",
                "working_distance_mm", "detector", "instrument"))


# ------------------------------------------------------ info bar only (OCR)

def test_exported_png_jeol_info_bar_only(tmp_path, engine):
    engine()
    p, _ = _jeol_png(tmp_path)
    info = read_image_info(p)
    assert info.ocr_status == "ok"
    assert info.magnification == 30000
    assert info.accelerating_voltage_kv == 7.0
    assert info.working_distance_mm == pytest.approx(9.7)
    assert info.detector == "LEI"
    assert info.vendor == "JEOL" and info.instrument == "JEOL"
    assert (info.scale_label_value, info.scale_label_unit) == (100.0, "nm")
    assert info.scale_bar_px == 30
    for k in ("magnification", "accelerating_voltage_kv", "working_distance_mm",
              "detector", "vendor", "instrument", "scale_label"):
        assert info.source[k] == "info_bar", k
        assert info.needs_check[k] is False, k
    assert info.metadata_source is None


def test_exported_jpg_thermo_info_bar_only(tmp_path, engine):
    engine(THERMO_STRIP)
    img, _ = render_thermo()
    p = _save(tmp_path / "t.jpg", img)
    info = read_image_info(p)
    assert info.magnification == 10000
    assert info.accelerating_voltage_kv == 15.0
    assert info.working_distance_mm == pytest.approx(8.947)
    assert info.detector == "BSD"
    assert info.source["magnification"] == "info_bar"
    assert (info.scale_label_value, info.scale_label_unit) == (15.0, "µm")


def test_in_memory_image_without_path(engine):
    engine()
    img, _ = render_jeol()
    info = read_image_info(None, image=img)
    assert info.magnification == 30000 and info.path == ""


def test_low_ocr_confidence_is_flagged(tmp_path, engine):
    engine(overrides={"7.0kV": 0.55})
    p, _ = _jeol_png(tmp_path)
    info = read_image_info(p)
    assert info.accelerating_voltage_kv == 7.0
    assert info.needs_check["accelerating_voltage_kv"] is True
    assert info.needs_check["magnification"] is False
    assert info.any_needs_check


# ------------------------------------------------ metadata + OCR together

def test_metadata_and_info_bar_agree(tmp_path, engine):
    engine()
    p, _ = _jeol_png(tmp_path, sidecar=_sidecar(30000, "7.00", "$$SM_WD 9.72\n"))
    info = read_image_info(p)
    assert info.source["magnification"] == "metadata"
    assert info.source["working_distance_mm"] == "metadata"
    assert info.working_distance_mm == pytest.approx(9.72)   # metadata kept
    assert not any(info.needs_check[k] for k in
                   ("magnification", "accelerating_voltage_kv",
                    "working_distance_mm"))
    assert info.instrument == "JEOL JSM-7800F"
    assert info.detector == "SEI"                 # metadata wins over "LEI"
    assert info.source["scale_label"] == "info_bar"


def test_metadata_and_info_bar_disagree(tmp_path, engine):
    engine()
    p, _ = _jeol_png(tmp_path, sidecar=_sidecar(5000, "15.00",
                                                "$$SM_WD 9.7\n"))
    info = read_image_info(p)
    assert info.magnification == 5000             # metadata value kept
    assert info.needs_check["magnification"] is True
    assert "30000" in info.field_notes["magnification"]
    assert info.accelerating_voltage_kv == 15.0
    assert info.needs_check["accelerating_voltage_kv"] is True
    assert info.needs_check["working_distance_mm"] is False


def test_ocr_fills_only_missing_fields(tmp_path, engine):
    engine()
    p, _ = _jeol_png(tmp_path, sidecar=_sidecar(30000, "7.00", ""))
    info = read_image_info(p)
    assert info.source["magnification"] == "metadata"
    assert info.source["working_distance_mm"] == "info_bar"
    assert info.working_distance_mm == pytest.approx(9.7)


def test_metadata_only_skips_ocr(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("OCR must not run with use_ocr=False")
    monkeypatch.setattr(ibo, "read_info_bar", boom)
    p, _ = _jeol_png(tmp_path, sidecar=_sidecar())
    info = read_image_info(p, use_ocr=False)
    assert info.magnification == 30000 and info.ocr_status == "not_run"


# ------------------------------------------------------------ sanity ranges

def test_absurd_metadata_rejected_and_ocr_used(tmp_path, engine):
    engine()
    p, _ = _jeol_png(tmp_path, sidecar=_sidecar(1, "300", "$$SM_WD 900\n"))
    md_only = read_image_info(p, use_ocr=False)
    assert md_only.magnification is None
    assert md_only.accelerating_voltage_kv is None
    assert md_only.working_distance_mm is None
    assert "plausible" in md_only.field_notes["accelerating_voltage_kv"]
    info = read_image_info(p)
    assert info.accelerating_voltage_kv == 7.0
    assert info.source["accelerating_voltage_kv"] == "info_bar"
    assert info.magnification == 30000


def test_absurd_ocr_values_rejected(tmp_path, engine):
    toks = [("X2", 520, 36, 40, 20), ("75kV", 640, 36, 60, 20),
            ("WD950mm", 850, 36, 96, 20)]
    engine(toks)
    p, _ = _jeol_png(tmp_path)
    info = read_image_info(p)
    assert info.magnification is None
    assert info.accelerating_voltage_kv is None
    assert info.working_distance_mm is None
    assert info.source["working_distance_mm"] is None
    assert "plausible" in info.field_notes["working_distance_mm"]


def test_sanity_ranges_documented():
    assert ii.SANITY_RANGES["accelerating_voltage_kv"][1] == 40.0
    assert ii.SANITY_RANGES["working_distance_mm"] == (0.5, 100.0)
    assert ii.SANITY_RANGES["magnification"] == (5.0, 2_000_000.0)


# ------------------------------------------------------ robustness / offline

@pytest.mark.parametrize("use_ocr", [False, True])
def test_missing_file_never_raises(tmp_path, engine, use_ocr):
    engine()
    info = read_image_info(str(tmp_path / "nope.png"), use_ocr=use_ocr)
    assert info.is_empty and "not found" in " ".join(info.notes)
    info = read_image_info(None, use_ocr=use_ocr)
    assert info.is_empty and info.notes


def test_garbage_file_and_missing_sidecar(tmp_path, engine):
    engine([])
    p = tmp_path / "bad.png"
    p.write_bytes(b"not an image at all")
    info = read_image_info(str(p))
    assert info.is_empty and info.ocr_status == "error" and info.notes
    img, _ = render_jeol()
    q = _save(tmp_path / "plain.png", img)          # no sidecar, no text
    info = read_image_info(q)
    assert info.is_empty and info.ocr_status == "no_text"


def _no_engine(monkeypatch, tmp_path, sidecar):
    ibo._reset_engine_cache()

    def boom():
        raise ImportError("No module named 'rapidocr_onnxruntime'")
    monkeypatch.setattr(ibo, "_load_engine", boom)
    try:
        p, _ = _jeol_png(tmp_path, sidecar=sidecar)
        return read_image_info(p)
    finally:
        ibo._reset_engine_cache()


def test_missing_ocr_engine_says_reinstall(tmp_path, monkeypatch):
    # sidecar without WD: OCR would have been needed -> tell the user
    info = _no_engine(monkeypatch, tmp_path, _sidecar(wd=""))
    assert info.ocr_status == "engine_missing"
    assert info.magnification == 30000            # metadata still delivered
    text = " ".join(info.notes).lower()
    assert "reinstall" in text
    assert "http" not in text and "download" not in text


def test_missing_ocr_engine_quiet_when_metadata_complete(tmp_path,
                                                         monkeypatch):
    info = _no_engine(monkeypatch, tmp_path, _sidecar())
    assert info.ocr_status == "engine_missing"
    assert info.working_distance_mm == pytest.approx(9.7)
    assert not any("reinstall" in n.lower() for n in info.notes)


def test_metadata_failure_still_runs_ocr(tmp_path, engine, monkeypatch):
    from core import sem_metadata

    def boom(*a, **k):
        raise ValueError("corrupt sidecar")
    monkeypatch.setattr(sem_metadata, "read_sem_metadata", boom)
    engine()
    p, _ = _jeol_png(tmp_path, sidecar=_sidecar(5000))
    info = read_image_info(p)
    assert info.ocr_status == "ok"
    assert info.magnification == 30000            # from the data bar
    assert info.source["magnification"] == "info_bar"
    assert any("metadata could not be read" in n for n in info.notes)


def test_bar_fallback_flag_adds_note(tmp_path, monkeypatch):
    def fake(image, metadata=None, **k):
        r = ibo.InfoBarReading(status="ok", bar_fallback=True,
                               message="anything at all")
        r.magnification = ibo.ReadingField(30000.0, "x", 0.95, True)
        return r
    monkeypatch.setattr(ibo, "read_info_bar", fake)
    p, _ = _jeol_png(tmp_path)
    info = read_image_info(p)
    assert any("No data bar was found" in n for n in info.notes)
    assert info.needs_check["magnification"] is True
    # the message text alone must not trigger the note
    def fake2(image, metadata=None, **k):
        return ibo.InfoBarReading(status="ok",
                                  message="No data bar detected; read ...")
    monkeypatch.setattr(ibo, "read_info_bar", fake2)
    info = read_image_info(p)
    assert not any("No data bar was found" in n for n in info.notes)


def test_wd_printed_in_micrometres(tmp_path, engine):
    toks = [t if not t[0].startswith("WD") else ("WD9700µm",) + t[1:]
            for t in JEOL_STRIP]
    engine(toks)
    p, _ = _jeol_png(tmp_path)
    info = read_image_info(p)
    assert info.working_distance_mm == pytest.approx(9.7)
    assert info.source["working_distance_mm"] == "info_bar"


def test_wd_in_micrometres_agrees_with_metadata(tmp_path, engine):
    toks = [t if not t[0].startswith("WD") else ("WD9700µm",) + t[1:]
            for t in JEOL_STRIP]
    engine(toks)
    p, _ = _jeol_png(tmp_path, sidecar=_sidecar())
    info = read_image_info(p)
    assert info.source["working_distance_mm"] == "metadata"
    assert info.needs_check["working_distance_mm"] is False


def test_scale_label_in_millimetres(tmp_path, engine):
    toks = [t if not t[0].startswith("100nm") else ("1mm JEOL",) + t[1:]
            for t in JEOL_STRIP]
    engine(toks)
    p, _ = _jeol_png(tmp_path)
    info = read_image_info(p)
    assert (info.scale_label_value, info.scale_label_unit) == (1.0, "mm")
    assert info.source["scale_label"] == "info_bar"
    # 1 mm on a 30 px bar contradicts x30,000 -> must be flagged for checking
    assert info.needs_check["scale_label"] is True
    back = ImageInfo.from_dict(json.loads(json.dumps(info.to_dict())))
    assert back.scale_label_unit == "mm"


def test_unexpected_error_is_contained(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(ibo, "read_info_bar", boom)
    p, _ = _jeol_png(tmp_path, sidecar=_sidecar())
    info = read_image_info(p)
    assert info.magnification == 30000            # filled before the failure
    assert any("could not be read" in n for n in info.notes)


def test_concurrent_ocr_calls(tmp_path, engine):
    engine()
    p, _ = _jeol_png(tmp_path)
    results, errors = [], []

    def work():
        try:
            results.append(read_image_info(p))
        except Exception as exc:                  # pragma: no cover
            errors.append(exc)
    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errors and len(results) == 4
    assert all(r.magnification == 30000 for r in results)


def test_module_has_no_qt_or_network_imports():
    import ast
    tree = ast.parse(open(ii.__file__, encoding="utf-8").read())
    mods = set()
    for node in ast.walk(tree):          # includes imports inside functions
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
            mods.update(f"{node.module}.{a.name}" for a in node.names)
    assert mods, "no imports parsed"
    bad = ("PySide6", "PyQt5", "PyQt6", "shiboken6", "socket", "urllib",
           "http", "requests", "httpx", "aiohttp", "ftplib", "webbrowser",
           "ssl", "smtplib")
    for m in mods:
        root = m.split(".")[0]
        assert root not in bad, m


# ------------------------------------------------------------ persistence

def test_dict_round_trip(tmp_path, engine):
    engine(overrides={"7.0kV": 0.55})
    p, _ = _jeol_png(tmp_path, sidecar=_sidecar(5000, "7.00", ""))
    info = read_image_info(p)
    d = json.loads(json.dumps(info.to_dict()))
    back = ImageInfo.from_dict(d)
    assert back.to_dict() == info.to_dict()
    assert back.needs_check["magnification"] is True
    assert back.source["working_distance_mm"] == "info_bar"


@pytest.mark.parametrize("bad", [None, [], "x", {"magnification": "abc",
                                                 "source": 3,
                                                 "scale_label_unit": "km",
                                                 "notes": "oops"}])
def test_from_dict_is_tolerant(bad):
    info = ImageInfo.from_dict(bad)
    assert info.magnification is None and info.scale_label_unit is None
    assert info.is_empty and info.ocr_status == "not_run"


# ------------------------------------------------------ real engine (optional)

def _has_rapidocr():
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _has_rapidocr(), reason="rapidocr not installed")
def test_real_ocr_on_jeol_png(tmp_path):
    ibo._reset_engine_cache()
    p, _ = _jeol_png(tmp_path)
    info = read_image_info(p)
    assert info.ocr_status == "ok"
    assert info.magnification == 30000
    assert info.accelerating_voltage_kv == 7.0
    assert info.working_distance_mm == pytest.approx(9.7)
