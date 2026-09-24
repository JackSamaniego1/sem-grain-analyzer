"""INN-05: calibration from SEM metadata (offline, stdlib reader).

Synthetic TIFFs are written with tifffile (``extratags``) carrying the
vendor text blocks the parser reads.  The sample strings are modelled on
real-world files:

* Zeiss SmartSEM ``CZ_SEM`` (tag 34118): alternating parameter-code /
  ``Label = value unit`` lines, CRLF separated, latin-1 ``µ``, ``Mag = 40.00
  K X``, ``Date :24 Mar 2017`` — as in Zeiss Sigma / Supra / EVO TIFFs (the
  layout tifffile's ``read_cz_sem`` and HyperSpy's Zeiss reader expect).
* FEI / Thermo Fisher (tags 34682 ``FEI_HELIOS`` / 34680 ``FEI_SFEG``): INI
  text with ``[User]``, ``[System]``, ``[Beam]``, ``[EBeam]``, ``[Scan]``,
  ``[Detectors]`` sections, SI units (metres, volts) — Helios / Quanta /
  Apreo layout.
* TESCAN (tag 50431 / ``-tif.hdr``): ``[MAIN]`` with ``PixelSizeX`` (m) and
  ``[SEM]`` with ``HV`` (V), ``WD`` (m), ``Magnification`` — MIRA3 / VEGA.
* Hitachi SU-series sidecar ``.txt``: ``[SemImageFile]`` with
  ``PixelSize=`` (nm), ``AcceleratingVoltage=15000 Volt``,
  ``WorkingDistance=8100 um``.
* JEOL sidecar ``.txt``: ``$CM_MAG``, ``$CM_ACCEL_VOLT``, ``$CM_FULL_SIZE``,
  ``$$SM_MICRON_BAR`` (px) / ``$$SM_MICRON_MARKER`` (e.g. ``5um``).
"""
import os

import cv2
import numpy as np
import pytest

tifffile = pytest.importorskip("tifffile")

from core.sem_metadata import (read_sem_metadata, calibration_from_metadata,
                               _length_um, _voltage_kv, _read_tiff_tags)

IMG = (np.arange(64 * 80) % 251).astype(np.uint8).reshape(64, 80)

ZEISS = ("\r\n0\r\nAP_PIXEL_SIZE\r\nImage Pixel Size = 2.791 nm\r\n"
         "AP_WD\r\nWD =  8.5 mm\r\nAP_MAG\r\nMag = 40.00 K X\r\n"
         "AP_ACTUALKV\r\nEHT = 5.00 kV\r\nDP_DETECTOR_CHANNEL\r\n"
         "Signal A = InLens\r\nAP_DATE\r\nDate :24 Mar 2017\r\nAP_TIME\r\n"
         "Time :12:40:39\r\nDP_COLUMN_TYPE\r\nColumn Type = GEMINI\r\n")
ZEISS_UM = ("\r\n0\r\nAP_PIXEL_SIZE\r\nImage Pixel Size = 1.117 µm\r\n"
            "AP_MAG\r\nMag =   100 X\r\nAP_ACTUALKV\r\nEHT = 20.00 kV\r\n"
            "AP_WD\r\nWD = 10.2 mm\r\nDP_DETECTOR_CHANNEL\r\nSignal A = SE2\r\n")
FEI = ("[User]\r\nDate=03/12/2019\r\nTime=10:22:13 AM\r\nUser=supervisor\r\n\r\n"
       "[System]\r\nType=DualBeam\r\nDnumber=D9920\r\nSource=FEG\r\n"
       "Column=Elstar\r\nSystemType=Helios G4 UX\r\nDisplayWidth=0.4\r\n"
       "DisplayHeight=0.3\r\n\r\n[Beam]\r\nHV=5000\r\n\r\n[EBeam]\r\n"
       "Source=FEG\r\nHV=5000\r\nWD=0.00412\r\n\r\n[Scan]\r\n"
       "InternalScan=true\r\nDwelltime=3e-006\r\nPixelWidth=2.0345e-009\r\n"
       "PixelHeight=2.0345e-009\r\nHorFieldsize=2.0833e-006\r\n\r\n"
       "[Detectors]\r\nNumber=1\r\nName=TLD\r\nMode=SE\r\n")
TESCAN = ("[MAIN]\r\nCompany=TESCAN\r\nDate=2019-03-12\r\nTime=10:22:13\r\n"
          "Device=MIRA3\r\nDeviceModel=MIRA3 LMH\r\nPixelSizeX=4.1e-008\r\n"
          "PixelSizeY=4.2e-008\r\n[SEM]\r\nHV=15000.0\r\nWD=0.0100\r\n"
          "Magnification=5000.0\r\nDetector=SE\r\n")
HITACHI = ("[SemImageFile]\r\nInstructName=SU8230\r\nSerialNumber=8230-01\r\n"
           "SampleName=steel\r\nFormat=tif\r\nImageName=img.tif\r\n"
           "Date=2019/03/12\r\nTime=10:22:13\r\nDataSize=1280x960\r\n"
           "PixelSize=9.921875\r\nSignalName=SE(U)\r\n"
           "AcceleratingVoltage=15000 Volt\r\nDecelerationVoltage=0 Volt\r\n"
           "Magnification=10000\r\nWorkingDistance=8100 um\r\n")
JEOL = ("$CM_FORMAT JEOL/SEM\n$CM_VERSION 1.0\n$CM_INSTRUMENT JSM-7800F\n"
        "$CM_SIGNAL SEI\n$CM_MAG 5000\n$CM_ACCEL_VOLT 15.00\n"
        "$CM_DATE 2019/03/12\n$CM_TIME 10:21:55\n$CM_FULL_SIZE 1280 960\n"
        "$$SM_MICRON_BAR 180\n$$SM_MICRON_MARKER 5um\n$$SM_WD 10.0\n")


def _tif(path, tag=None, text=None, **kw):
    extratags = []
    if tag is not None:
        data = text if isinstance(text, bytes) else text.encode("latin-1")
        extratags.append((tag, 1, len(data), data, True))
    tifffile.imwrite(str(path), kw.pop("data", IMG), extratags=extratags, **kw)
    return str(path)


def _approx(a, b, rel=1e-6):
    return a == pytest.approx(b, rel=rel)


# ------------------------------------------------------------- vendors

def test_zeiss_nm(tmp_path):
    p = _tif(tmp_path / "z.tif", 34118, ZEISS)
    md = read_sem_metadata(p)
    assert md.vendor == "Zeiss" and md.confidence == "high"
    assert _approx(md.pixel_size_um_x, 0.002791)
    assert _approx(md.px_per_um, 1 / 0.002791)
    assert md.magnification == 40000
    assert md.accelerating_voltage_kv == 5.0
    assert md.working_distance_mm == pytest.approx(8.5)
    assert md.detector == "InLens"
    assert md.date == "24 Mar 2017 12:40:39"
    assert md.instrument == "GEMINI"
    ppu, src, conf = calibration_from_metadata(p)
    assert _approx(ppu, 1 / 0.002791) and conf == "high" and "34118" in src


def test_zeiss_micrometre_latin1_bigendian_bigtiff(tmp_path):
    for kw in ({"byteorder": ">"}, {"bigtiff": True}):
        p = _tif(tmp_path / "z2.tif", 34118, ZEISS_UM, **kw)
        md = read_sem_metadata(p)
        assert _approx(md.pixel_size_um_x, 1.117), kw
        assert md.magnification == 100 and md.accelerating_voltage_kv == 20.0
        assert md.detector == "SE2"


@pytest.mark.parametrize("tag", [34682, 34680])
def test_fei(tmp_path, tag):
    p = _tif(tmp_path / "f.tif", tag, FEI)
    md = read_sem_metadata(p)
    assert md.vendor == "FEI/Thermo Fisher" and md.confidence == "high"
    assert _approx(md.pixel_size_um_x, 2.0345e-3)          # m -> um
    assert md.accelerating_voltage_kv == 5.0               # V -> kV
    assert md.working_distance_mm == pytest.approx(4.12)   # m -> mm
    assert md.detector == "TLD SE"
    assert md.instrument == "Helios G4 UX"
    assert md.magnification == pytest.approx(0.4 / 2.0833e-6, rel=1e-3)
    assert md.date.startswith("03/12/2019")


def test_fei_ini_in_image_description(tmp_path):
    p = str(tmp_path / "fd.tif")
    tifffile.imwrite(p, IMG, description=FEI.replace("\r\n", "\n"),
                     metadata=None)
    md = read_sem_metadata(p)
    assert md.vendor == "FEI/Thermo Fisher"
    assert _approx(md.pixel_size_um_x, 2.0345e-3)


def test_tescan_tag_with_binary_prefix(tmp_path):
    blob = bytes(range(0, 32)) + b"\xff\xfe" + TESCAN.encode("latin-1")
    p = _tif(tmp_path / "t.tif", 50431, blob)
    md = read_sem_metadata(p)
    assert md.vendor == "TESCAN" and md.confidence == "high"
    assert _approx(md.pixel_size_um_x, 0.041)
    assert _approx(md.pixel_size_um_y, 0.042)
    assert md.accelerating_voltage_kv == 15.0
    assert md.working_distance_mm == pytest.approx(10.0)
    assert md.magnification == 5000 and md.detector == "SE"
    assert md.instrument == "MIRA3 LMH"


def test_tescan_hdr_sidecar(tmp_path):
    p = _tif(tmp_path / "scan01.tif")
    (tmp_path / "scan01-tif.hdr").write_text(TESCAN, encoding="latin-1")
    md = read_sem_metadata(p)
    assert md.vendor == "TESCAN" and "scan01-tif.hdr" in md.source


def test_hitachi_sidecar_png(tmp_path):
    p = str(tmp_path / "img.png")
    cv2.imwrite(p, IMG)
    (tmp_path / "img.txt").write_text(HITACHI, encoding="latin-1")
    md = read_sem_metadata(p)
    assert md.vendor == "Hitachi" and md.confidence == "high"
    assert _approx(md.pixel_size_um_x, 0.009921875)        # nm -> um
    assert md.accelerating_voltage_kv == 15.0
    assert md.working_distance_mm == pytest.approx(8.1)    # um -> mm
    assert md.magnification == 10000 and md.detector == "SE(U)"
    assert md.instrument == "SU8230"
    ppu, src, conf = calibration_from_metadata(p)
    assert ppu == pytest.approx(100.787, rel=1e-4) and "img.txt" in src


def test_jeol_sidecar(tmp_path):
    p = str(tmp_path / "j01.bmp")
    cv2.imwrite(p, IMG)
    (tmp_path / "j01.txt").write_text(JEOL, encoding="latin-1")
    md = read_sem_metadata(p)
    assert md.vendor == "JEOL" and md.confidence == "medium"
    assert _approx(md.pixel_size_um_x, 5.0 / 180)
    assert md.magnification == 5000 and md.accelerating_voltage_kv == 15.0
    assert md.working_distance_mm == pytest.approx(10.0)
    assert md.instrument == "JSM-7800F" and md.detector == "SEI"
    ppu, _, conf = calibration_from_metadata(p)
    assert ppu == pytest.approx(36.0) and conf == "medium"


def test_jeol_resampled_image(tmp_path):
    """$CM_FULL_SIZE 1280 but the saved image is 640 px wide: the pixel is
    twice as large."""
    p = _tif(tmp_path / "j02.tif", data=np.zeros((480, 640), np.uint8))
    (tmp_path / "j02.txt").write_text(JEOL, encoding="latin-1")
    md = read_sem_metadata(p)
    assert _approx(md.pixel_size_um_x, 2 * 5.0 / 180)
    assert "rescaled" in md.source


def test_imagej_calibration(tmp_path):
    p = str(tmp_path / "ij.tif")
    tifffile.imwrite(p, IMG, imagej=True, resolution=(1 / 0.05, 1 / 0.05),
                     metadata={"unit": "nm"})
    md = read_sem_metadata(p)
    assert md.vendor == "ImageJ" and md.confidence == "medium"
    assert _approx(md.pixel_size_um_x, 0.05e-3)


# ------------------------------------------------ generic / low confidence

def test_generic_dpi_is_low_confidence(tmp_path):
    p = _tif(tmp_path / "dpi.tif", resolution=(300, 300),
             resolutionunit="INCH")
    md = read_sem_metadata(p)
    assert md.vendor == "Generic TIFF" and md.confidence == "low"
    assert md.is_probably_dpi
    assert _approx(md.pixel_size_um_x, 25400 / 300)
    assert calibration_from_metadata(p)[2] == "low"
    assert calibration_from_metadata(p, min_confidence="medium") is None


def test_generic_centimetre_physical_like(tmp_path):
    # 5e4 px/cm = 0.2 um pixels: plausible physical, still only "low"
    p = _tif(tmp_path / "cm.tif", resolution=(5e4, 5e4),
             resolutionunit="CENTIMETER")
    md = read_sem_metadata(p)
    assert md.confidence == "low" and not md.is_probably_dpi
    assert _approx(md.pixel_size_um_x, 0.2)


# ------------------------------------------------ garbage / missing

def test_plain_tiff_no_metadata(tmp_path):
    assert read_sem_metadata(_tif(tmp_path / "plain.tif")) is None
    assert calibration_from_metadata(str(tmp_path / "plain.tif")) is None


def test_missing_and_non_image_files(tmp_path):
    assert read_sem_metadata(str(tmp_path / "nope.tif")) is None
    assert read_sem_metadata(None) is None
    assert read_sem_metadata("http://example.com/a.tif") is None
    g = tmp_path / "garbage.tif"
    g.write_bytes(os.urandom(4096))
    assert read_sem_metadata(str(g)) is None
    t = tmp_path / "trunc.tif"
    t.write_bytes(b"II*\x00\xff\xff\xff\x00")               # IFD past EOF
    assert read_sem_metadata(str(t)) is None
    png = tmp_path / "x.png"
    cv2.imwrite(str(png), IMG)
    assert read_sem_metadata(str(png)) is None


def test_garbage_vendor_text(tmp_path):
    p = _tif(tmp_path / "gz.tif", 34118, b"\x00\x01\x02 nothing useful \xff")
    assert read_sem_metadata(p) is None
    p = _tif(tmp_path / "gf.tif", 34682, "[Scan]\r\nPixelWidth=abc\r\n")
    assert read_sem_metadata(p) is None
    (tmp_path / "gs.txt").write_text("hello world\n$ nothing\n")
    cv2.imwrite(str(tmp_path / "gs.png"), IMG)
    assert read_sem_metadata(str(tmp_path / "gs.png")) is None


def test_implausible_pixel_size_rejected(tmp_path):
    p = _tif(tmp_path / "big.tif", 34682, FEI.replace(
        "PixelWidth=2.0345e-009", "PixelWidth=5").replace(
        "PixelHeight=2.0345e-009", "PixelHeight=5"))
    assert calibration_from_metadata(p) is None


# ------------------------------------------------ unit conversions

@pytest.mark.parametrize("text,default,expected", [
    ("2.791 nm", "m", 0.002791),
    ("1.117 µm", "nm", 1.117),
    ("1.117 μm", "nm", 1.117),
    ("3e-009", "m", 0.003),
    ("2.0345e-009", "m", 0.0020345),
    ("8100 um", "mm", 8100.0),
    ("8.5 mm", "um", 8500.0),
    ("9.921875", "nm", 0.009921875),
    ("500 pm", "nm", 0.0005),
    ("5um", "nm", 5.0),
])
def test_length_conversion(text, default, expected):
    assert _length_um(text, default) == pytest.approx(expected)


@pytest.mark.parametrize("text,default,expected", [
    ("15.00 kV", "v", 15.0), ("15000 Volt", "kv", 15.0),
    ("5000", "v", 5.0), ("15.0", "kv", 15.0), ("abc", "v", None),
])
def test_voltage_conversion(text, default, expected):
    assert _voltage_kv(text, default) == expected


def test_tiff_reader_is_stdlib_and_local(tmp_path):
    p = _tif(tmp_path / "r.tif", 34118, ZEISS)
    tags = _read_tiff_tags(p, {256, 34118})
    assert tags[256] == (80,) and tags[34118].startswith(b"\r\n0")
    import core.sem_metadata as sm
    src = open(sm.__file__, encoding="utf-8").read()
    for bad in ("import socket", "urllib", "import http", "requests",
                "tifffile", "PIL"):
        assert bad not in src.split('"""', 2)[-1], bad
