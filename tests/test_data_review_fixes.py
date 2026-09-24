"""Regression tests for the code-review REQUEST_CHANGES round on data/:

1. corrupted summary.json / truncated npz / corrupt grains.json degrade
   instead of crashing session loading.
2. resolve_project/sample/lot, delete_session and _rename_dir refuse a
   path outside the workspace root.
3. sanitize_name caps length and neutralises reserved names with an
   extension.
4. _save_one_image sanitizes the file suffix too, and save_session skips
   a bad image with a warning instead of leaving a half-written session.
5. default_operator() falls back when os.getlogin() raises.
"""
import numpy as np
import pytest

from core.grain_detector import GrainDetector
from data.models import ImageEntry, MAX_NAME_LEN, default_operator, sanitize_name
from data.session_io import _load_result_for_stem, save_session
from data.workspace import Workspace


# ----------------------------------------------------------------------
# 1. corrupt result files degrade instead of crashing
# ----------------------------------------------------------------------

def _session_with_result(tmp_path, mosaic_bgr):
    ws = Workspace(tmp_path)
    proj = ws.create_project("P1")
    sample = ws.create_sample(proj, "S1")
    lot = ws.create_lot(proj, sample, "L1")
    result = GrainDetector().analyze(mosaic_bgr, px_per_um=1.0)
    entry = ImageEntry(image_bgr=mosaic_bgr, result=result, filename="frame1.png")
    return save_session(lot, {}, [entry])


def test_truncated_npz_does_not_crash(tmp_path, mosaic_bgr):
    ref = _session_with_result(tmp_path, mosaic_bgr)
    npz_path = ref.path / "results" / "frame1.labels.npz"
    npz_path.write_bytes(b"not a real npz file, truncated garbage")

    result = _load_result_for_stem(ref.path, "frame1")
    assert result is not None
    assert result.label_image is None
    assert result.valid_mask is None
    assert result.binary_image is None
    # summary/grains were untouched and still load fine
    assert result.grain_count >= 0


def test_malformed_summary_json_does_not_crash(tmp_path, mosaic_bgr):
    ref = _session_with_result(tmp_path, mosaic_bgr)
    summary_path = ref.path / "results" / "frame1.summary.json"
    summary_path.write_text("{not valid json!!!", encoding="utf-8")

    result = _load_result_for_stem(ref.path, "frame1")
    assert result is not None
    assert result.mean_area_um2 == 0.0  # defaulted


def test_malformed_grains_json_does_not_crash(tmp_path, mosaic_bgr):
    ref = _session_with_result(tmp_path, mosaic_bgr)
    grains_path = ref.path / "results" / "frame1.grains.json"
    grains_path.write_text("[[[not json", encoding="utf-8")

    result = _load_result_for_stem(ref.path, "frame1")
    assert result is not None
    assert result.grains == []
    # the arrays are unaffected since only grains.json was corrupted
    assert result.label_image is not None


# ----------------------------------------------------------------------
# 2. containment checks
# ----------------------------------------------------------------------

def test_resolve_project_rejects_path_outside_root(tmp_path):
    root = tmp_path / "workspace"
    outside = tmp_path / "outside_project"
    outside.mkdir()
    (outside / "project.json").write_text('{"name": "Evil"}', encoding="utf-8")

    ws = Workspace(root)
    with pytest.raises(ValueError):
        ws.resolve_project(outside)


def test_delete_session_rejects_path_outside_root(tmp_path):
    root = tmp_path / "workspace"
    ws = Workspace(root)
    outside_session = tmp_path / "outside_session"
    outside_session.mkdir()
    (outside_session / "manifest.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError):
        ws.delete_session(outside_session)
    assert outside_session.exists()  # never touched


def test_rename_dir_rejects_path_outside_root(tmp_path):
    root = tmp_path / "workspace"
    ws = Workspace(root)
    outside = tmp_path / "outside_project"
    outside.mkdir()

    with pytest.raises(ValueError):
        ws.rename_project(outside, "New Name")
    assert outside.exists()


def test_update_session_meta_rejects_path_outside_root(tmp_path):
    root = tmp_path / "workspace"
    ws = Workspace(root)
    outside_session = tmp_path / "outside_session"
    outside_session.mkdir()
    (outside_session / "manifest.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError):
        ws.update_session_meta(outside_session, notes="hacked")


# ----------------------------------------------------------------------
# 3. sanitize_name length cap + reserved names with extension
# ----------------------------------------------------------------------

def test_sanitize_name_length_cap():
    long_name = "A" * 300
    safe = sanitize_name(long_name)
    assert len(safe) <= MAX_NAME_LEN


def test_sanitize_name_length_cap_usable_on_disk(tmp_path):
    long_name = "B" * 300
    ws = Workspace(tmp_path)
    proj = ws.create_project(long_name)  # must not raise WinError 123
    assert proj.exists()
    assert len(proj.name) <= MAX_NAME_LEN


def test_sanitize_name_reserved_with_extension():
    safe = sanitize_name("con.txt")
    assert not safe.upper().startswith("CON.") or safe.startswith("_")
    safe2 = sanitize_name("COM1.png")
    assert safe2 != "COM1.png"
    assert safe2.startswith("_")


# ----------------------------------------------------------------------
# 4. suffix sanitization + skip-with-warning
# ----------------------------------------------------------------------

def test_weird_suffix_sanitized(tmp_path, mosaic_bgr):
    ws = Workspace(tmp_path)
    proj = ws.create_project("P1")
    sample = ws.create_sample(proj, "S1")
    lot = ws.create_lot(proj, sample, "L1")
    entry = ImageEntry(image_bgr=mosaic_bgr, filename="weird<>name.p?ng")

    ref = save_session(lot, {}, [entry])  # must not raise OSError
    files = list((ref.path / "images").iterdir())
    assert len(files) == 1
    assert all(c.isalnum() or c in "._- ()" for c in files[0].name)
    assert files[0].suffix == ".png"


def test_bad_image_entry_skipped_with_warning_not_fatal(tmp_path, mosaic_bgr):
    ws = Workspace(tmp_path)
    proj = ws.create_project("P1")
    sample = ws.create_sample(proj, "S1")
    lot = ws.create_lot(proj, sample, "L1")

    good = ImageEntry(image_bgr=mosaic_bgr, filename="good.png")
    bad = ImageEntry(source_path="C:/does/not/exist/nope.png", filename=None)

    ref = save_session(lot, {}, [good, bad])  # must not raise
    assert (ref.path / "manifest.json").exists()
    assert (ref.path / "images" / "good.png").exists()

    from data.session_io import load_session
    loaded = load_session(ref.path)
    assert len(loaded.manifest.images) == 2
    failed = [i for i in loaded.manifest.images if "FAILED" in i.notes]
    assert len(failed) == 1


# ----------------------------------------------------------------------
# 5. default_operator fallback
# ----------------------------------------------------------------------

def test_default_operator_falls_back_when_getlogin_raises(monkeypatch):
    def _raise():
        raise OSError("no controlling terminal")
    monkeypatch.setattr("os.getlogin", _raise)
    monkeypatch.setattr("getpass.getuser", lambda: "fallback-user")
    assert default_operator() == "fallback-user"


def test_default_operator_returns_string_even_if_both_fail(monkeypatch):
    def _raise():
        raise OSError("nope")
    monkeypatch.setattr("os.getlogin", _raise)

    def _raise_getpass():
        raise Exception("nope either")
    monkeypatch.setattr("getpass.getuser", _raise_getpass)
    assert default_operator() == ""
