"""INN-29 - calibration verification against a reference standard."""
from __future__ import annotations

import ast
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from core import cal_verify as cv
from data import cal_records as cr
from data.models import AppSettings, SessionMeta

ROOT = Path(__file__).resolve().parents[1]


def _grating(h, w, period, angle_deg=0.0, noise=10.0, grid=False, seed=0):
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w].astype(float)
    t = math.radians(angle_deg)
    u = x * math.cos(t) + y * math.sin(t)
    v = -x * math.sin(t) + y * math.cos(t)
    if grid:
        img = 128 + 40 * np.cos(2 * np.pi * u / period) + 40 * np.cos(2 * np.pi * v / period)
    else:
        img = 128 + 50 * np.cos(2 * np.pi * u / period)
    img = img + rng.normal(0, noise, img.shape)
    return np.clip(img, 0, 255).astype(np.uint8)


def _angle_close(a, b, tol=0.5):
    d = abs((a - b) % 180.0)
    return min(d, 180.0 - d) <= tol


# ----------------------------------------------------------------------
# 1-2: pitch measurement
# ----------------------------------------------------------------------

def test_sinusoidal_grating_period_within_0_3_pct():
    img = _grating(512, 512, 23.7, noise=10)
    r = cv.measure_pitch(img, px_per_um=10.0)
    assert r.pattern == cv.PATTERN_GRATING
    assert abs(r.period_px - 23.7) / 23.7 < 0.003
    assert r.confidence == "high"
    assert abs(r.measured_pitch_um - 2.37) / 2.37 < 0.003
    assert len(r.axes) == 1 and r.axes[0].sub_periods_px


@pytest.mark.parametrize("angle", [13.0, 61.0, 118.0])
def test_rotated_grating(angle):
    img = _grating(480, 640, 23.7, angle_deg=angle, noise=10, seed=3)
    r = cv.measure_pitch(img, pattern=cv.PATTERN_GRATING)
    assert abs(r.period_px - 23.7) / 23.7 < 0.003
    assert _angle_close(r.axes[0].wave_angle_deg, angle)


def test_square_grid_both_axes_rotated_7deg():
    img = _grating(512, 512, 31.0, angle_deg=7.0, noise=10, grid=True, seed=5)
    r = cv.measure_pitch(img, pattern="auto")
    assert r.pattern == cv.PATTERN_GRID
    assert len(r.axes) == 2
    for a in r.axes:
        assert abs(a.period_px - 31.0) / 31.0 < 0.003
    angles = sorted(a.wave_angle_deg for a in r.axes)
    assert _angle_close(angles[0], 7.0) and _angle_close(angles[1], 97.0)
    assert abs(r.period_px - 31.0) / 31.0 < 0.003


def test_black_region_is_ignored_via_valid_mask():
    img = _grating(512, 512, 19.3, noise=8, seed=9)
    img[:, 380:] = 0          # dead / black area (DET-01 invalid)
    r = cv.measure_pitch(img)
    assert abs(r.period_px - 19.3) / 19.3 < 0.003


def test_no_pattern_raises():
    rng = np.random.default_rng(1)
    img = rng.normal(128, 20, (256, 256)).clip(0, 255).astype(np.uint8)
    with pytest.raises(ValueError):
        cv.measure_pitch(img)


def test_period_lines_and_progress():
    img = _grating(256, 256, 20.0, noise=5)
    calls = []
    r = cv.measure_pitch(img, progress=lambda p, m: calls.append((p, m)))
    assert calls and calls[-1][0] == 100
    lines = cv.period_lines(r.axes[0], img.shape)
    assert 10 <= len(lines) <= 14
    # crest lines lie on bright pixels
    (x0, y0), (x1, y1) = lines[len(lines) // 2]
    xm, ym = int(round((x0 + x1) / 2)), int(round((y0 + y1) / 2))
    assert img[min(ym, 255), min(xm, 255)] > 150


def test_manual_pitch():
    assert cv.manual_pitch_px((0, 0), (300, 400), 10) == pytest.approx(50.0)


def test_verify_calibration_one_call():
    img = _grating(512, 512, 25.0, noise=10)
    v = cv.verify_calibration(img, px_per_um=10.0, certified_pitch_um=2.5,
                              cert_expanded_um=0.01)
    assert v.passed and abs(v.error_pct) < 0.3
    assert v.uncertainty["expanded_um"] > 0


# ----------------------------------------------------------------------
# 3: error sign / tolerance boundary
# ----------------------------------------------------------------------

def test_error_sign_and_tolerance_boundary():
    assert cv.error_percent(1.006, 1.0) == pytest.approx(0.6)
    assert cv.error_percent(0.99, 1.0) == pytest.approx(-1.0)
    assert cv.evaluate(1.02, 1.0).passed               # exactly +2.0 %
    assert cv.evaluate(0.98, 1.0).passed               # exactly -2.0 %
    assert not cv.evaluate(1.0201, 1.0).passed         # +2.01 %
    assert not cv.evaluate(0.9799, 1.0).passed         # -2.01 %
    assert cv.evaluate(1.02, 1.0, tolerance_pct=1.5).passed is False


def test_uncertainty_budget_rss():
    b = cv.uncertainty_budget(2.0, cert_expanded_um=0.02, repeatability_um=0.003, span_px=500)
    uq = 0.5 / 500 * 2.0 / math.sqrt(3)
    assert b["u_combined_um"] == pytest.approx(math.sqrt(0.01 ** 2 + 0.003 ** 2 + uq ** 2))
    assert b["expanded_um"] == pytest.approx(2 * b["u_combined_um"])


# ----------------------------------------------------------------------
# 4-5: records + lookup
# ----------------------------------------------------------------------

UTC = timezone.utc


def _std(store, **kw):
    d = dict(name="Grating A", type="line grating", certified_pitch_um=1.0,
             expanded_uncertainty_um=0.005, certificate_no="1234", cert_expiry="2030-01-01")
    d.update(kw)
    return store.add_standard(cr.CalibrationStandard(**d))


def _check(store, std, when, measured=1.006, mag="5000x", instr="SEM-1"):
    c = cr.build_check(standard=std, instrument=instr, magnification=mag,
                       measured_pitch_um=measured, operator="tester")
    c.datetime = when.isoformat()
    return store.record_check(c)


def test_default_off_no_nags(tmp_path):
    """User requirement: optional feature, default off -> nothing shown."""
    assert AppSettings().calibration_verification_enabled is False
    store = cr.CalibrationStore(tmp_path)          # default: not enabled
    lk = store.find_applicable_check("SEM-1", "5000x")
    assert lk.status == cr.STATUS_OFF and lk.reason == "" and not lk.warnings
    assert cr.stamp_text(lk) == ""
    rep = cr.report_calibration(lk, px_per_um=10)
    assert rep["status"] == "off" and rep["text"] == "" and rep["check"] is None
    chip = store.instrument_status("SEM-1")
    assert chip["state"] == "off" and chip["text"] == ""
    meta = SessionMeta()
    cr.apply_to_session(meta, lk)
    assert meta.calibration_check_id is None and meta.calibration_status == ""
    assert not (tmp_path / "calibration").exists()   # nothing written
    # records for ANOTHER instrument do not start nagging this one
    std = _std(store)
    _check(store, std, datetime.now(UTC), instr="SEM-2")
    assert store.find_applicable_check("SEM-1", "5000x").status == cr.STATUS_OFF
    assert store.instrument_status("SEM-1")["state"] == "off"


def test_enabled_without_checks_reports_not_verified(tmp_path):
    store = cr.CalibrationStore(tmp_path, enabled=True)
    lk = store.find_applicable_check("SEM-1", "5000x")
    assert lk.status == cr.STATUS_NOT_VERIFIED and "no calibration check" in lk.reason
    assert cr.stamp_text(lk).startswith("Scale not verified")
    assert store.instrument_status("SEM-1")["text"] == "Cal due"


def test_checks_append_only_and_lookup(tmp_path):
    store = cr.CalibrationStore(tmp_path)
    std = _std(store)
    now = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
    img = tmp_path / "ref.tif"
    img.write_bytes(b"fake image bytes")
    c1 = cr.build_check(standard=std, instrument="SEM-1", magnification="5000x",
                        measured_pitch_um=1.006)
    c1.datetime = (now - timedelta(days=3)).isoformat()
    store.record_check(c1, image_path=img)
    raw1 = store.checks_path.read_bytes()
    assert c1.passed and c1.image_sha256 and (tmp_path / c1.image_copy_path).exists()

    c2 = _check(store, std, now - timedelta(days=1), measured=1.004, mag="5,000 X")
    raw2 = store.checks_path.read_bytes()
    assert raw2.startswith(raw1)                      # never rewritten
    assert len(raw2.splitlines()) == 2

    lk = store.find_applicable_check("sem-1", "5 kx", now=now)
    assert lk.status == cr.STATUS_VERIFIED and lk.check_id == c2.id
    assert store.instrument_status("SEM-1", now=now)["text"] == "Cal ✔ 1 d ago"

    # other magnification -> not verified
    lk = store.find_applicable_check("SEM-1", "10000x", now=now)
    assert lk.status == cr.STATUS_NOT_VERIFIED and lk.check is None

    # older than interval -> not verified
    lk = store.find_applicable_check("SEM-1", "5000x", now=now + timedelta(days=8))
    assert lk.status == cr.STATUS_NOT_VERIFIED and "older than 7 days" in lk.reason
    lk = store.find_applicable_check("SEM-1", "5000x", now=now + timedelta(days=8),
                                     instruments=[{"name": "SEM-1", "check_interval_days": 30}])
    assert lk.verified

    # a newer failing check invalidates
    c3 = _check(store, std, now - timedelta(hours=2), measured=1.05)
    assert not c3.passed
    lk = store.find_applicable_check("SEM-1", "5000x", now=now)
    assert lk.status == cr.STATUS_NOT_VERIFIED and "failed" in lk.reason
    assert store.instrument_status("SEM-1", now=now)["state"] == "failed"

    # a torn last line is skipped and the next append starts a new line
    with open(store.checks_path, "ab") as f:
        f.write(b'{"id": "torn"')
    c4 = _check(store, std, now - timedelta(hours=1), measured=1.001)
    ids = [c.id for c in store.load_checks()]
    assert ids == [c1.id, c2.id, c3.id, c4.id]
    lk = store.find_applicable_check("SEM-1", "5000x", now=now)
    assert lk.check_id == c4.id

    meta = SessionMeta()
    cr.apply_to_session(meta, lk)
    assert meta.calibration_check_id == c4.id and meta.calibration_status == "verified"
    assert SessionMeta.from_dict(meta.to_dict()).calibration_check_id == c4.id


def test_expired_certificate_saved_but_flagged(tmp_path):
    store = cr.CalibrationStore(tmp_path)
    std = _std(store, cert_expiry="2026-01-31", certificate_no="SN 99")
    now = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
    c = _check(store, std, now - timedelta(days=1), measured=1.006)
    assert c.standard_expired and cr.WARN_CERT_EXPIRED in c.flags
    assert store.load_checks()[0].standard_expired
    lk = store.find_applicable_check("SEM-1", "5000x", now=now)
    assert lk.verified and cr.WARN_CERT_EXPIRED in lk.warnings
    rep = cr.report_calibration(lk, source="metadata", px_per_um=10.0)
    assert "standard certificate expired" in rep["text"]


def test_stamp_text_format(tmp_path):
    store = cr.CalibrationStore(tmp_path)
    std = _std(store)
    now = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    _check(store, std, now, measured=1.006)
    lk = store.find_applicable_check("SEM-1", "5000x", now=now + timedelta(hours=1))
    txt = cr.stamp_text(lk)
    assert txt.startswith("Scale verified 2026-09-2")
    assert "error +0.6 % (limit ±2 %)" in txt and "standard SN 1234" in txt


def test_standards_crud(tmp_path):
    store = cr.CalibrationStore(tmp_path)
    s = _std(store)
    assert store.get_standard(s.id).certified_pitch_um == 1.0
    store.update_standard(s.id, certified_pitch_um=0.463)
    assert store.load_standards()[0].certified_pitch_um == 0.463
    data = json.loads(store.standards_path.read_text(encoding="utf-8"))
    assert data["standards"][0]["id"] == s.id
    store.remove_standard(s.id)
    assert store.load_standards() == []
    with pytest.raises(ValueError):
        store.add_standard(cr.CalibrationStandard(name="bad", certified_pitch_um=0))


def test_magnification_normalisation():
    assert cr.normalize_magnification("5,000x") == 5000
    assert cr.normalize_magnification("5 kX") == 5000
    assert cr.normalize_magnification("") is None
    assert cr.same_magnification("5000x", "5010x")
    assert not cr.same_magnification("5000x", "5100x")


def test_no_network_imports_in_new_modules():
    banned = {"socket", "urllib", "http", "requests", "webbrowser", "PySide6", "PyQt6"}
    for rel in ("core/cal_verify.py", "data/cal_records.py"):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                assert n.split(".")[0] not in banned, f"{rel}: {n}"
