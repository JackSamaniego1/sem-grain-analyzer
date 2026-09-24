"""Full save/load round trip against a real GrainDetector().analyze() run,
plus update_session (re-analysis autosave) and import_loose_images."""
import numpy as np
import pytest

from core.grain_detector import GrainDetector
from data.models import ImageEntry
from data.session_io import import_loose_images, load_session, save_session, update_session
from data.workspace import Workspace


def _analyze(mosaic_bgr):
    return GrainDetector().analyze(mosaic_bgr, px_per_um=2.0)


def test_save_and_load_session_roundtrip(tmp_path, mosaic_bgr):
    ws = Workspace(tmp_path)
    proj = ws.create_project("P1")
    sample = ws.create_sample(proj, "S1")
    lot = ws.create_lot(proj, sample, "L1")

    result = _analyze(mosaic_bgr)
    entry = ImageEntry(image_bgr=mosaic_bgr, result=result, filename="frame1.png",
                        notes="first image")

    ref = save_session(lot, {"project": "P1", "sample_id": "S1", "lot_number": "L1",
                              "operator": "jack"}, [entry], label="baseline")
    assert ref.path.exists()
    assert (ref.path / "manifest.json").exists()
    assert (ref.path / "report.json").exists()
    assert (ref.path / "images" / "frame1.png").exists()
    assert (ref.path / "results" / "frame1.labels.npz").exists()
    assert (ref.path / "results" / "frame1.overlay.png").exists()
    assert (ref.path / "results" / "frame1.grains.json").exists()
    assert (ref.path / "results" / "frame1.summary.json").exists()
    assert (ref.path / "thumbs" / "frame1.jpg").exists()

    loaded = load_session(ref.path)
    assert loaded.manifest.operator == "jack"
    assert loaded.manifest.label == "baseline"
    assert len(loaded.images) == 1
    img = loaded.images[0]
    assert img.entry.has_result
    assert img.entry.notes == "first image"

    loaded_result = img.result
    assert loaded_result is not None
    assert loaded_result.grain_count == result.grain_count
    assert len(loaded_result.grains) == len(result.grains)
    for g1, g2 in zip(loaded_result.grains, result.grains):
        assert g1 == g2
    np.testing.assert_array_equal(loaded_result.label_image, result.label_image)
    np.testing.assert_array_equal(loaded_result.valid_mask, result.valid_mask)
    np.testing.assert_array_equal(loaded_result.binary_image, result.binary_image)
    np.testing.assert_array_equal(loaded_result.overlay_image, result.overlay_image)
    assert loaded_result.mean_area_um2 == pytest.approx(result.mean_area_um2)
    assert loaded_result.grain_coverage_pct == pytest.approx(result.grain_coverage_pct)
    assert loaded_result.px_per_um == pytest.approx(result.px_per_um)


def test_load_session_lazy_result_not_loaded_until_accessed(tmp_path, mosaic_bgr):
    ws = Workspace(tmp_path)
    proj = ws.create_project("P1")
    sample = ws.create_sample(proj, "S1")
    lot = ws.create_lot(proj, sample, "L1")
    result = _analyze(mosaic_bgr)
    entry = ImageEntry(image_bgr=mosaic_bgr, result=result, filename="frame1.png")
    ref = save_session(lot, {}, [entry])

    from data.session_io import _UNSET

    loaded = load_session(ref.path)
    img = loaded.images[0]
    assert img._result is _UNSET  # not loaded yet
    _ = img.result
    assert img._result is not _UNSET  # cached after first access


def test_save_session_without_result(tmp_path, mosaic_bgr):
    ws = Workspace(tmp_path)
    proj = ws.create_project("P1")
    sample = ws.create_sample(proj, "S1")
    lot = ws.create_lot(proj, sample, "L1")
    entry = ImageEntry(image_bgr=mosaic_bgr, filename="raw.png")
    ref = save_session(lot, {}, [entry])
    loaded = load_session(ref.path)
    assert loaded.images[0].entry.has_result is False
    assert loaded.images[0].result is None
    assert not (ref.path / "results" / "raw.labels.npz").exists()
    assert (ref.path / "thumbs" / "raw.jpg").exists()  # thumb still made from raw image


def test_update_session_reanalysis_overwrites_result(tmp_path, mosaic_bgr):
    ws = Workspace(tmp_path)
    proj = ws.create_project("P1")
    sample = ws.create_sample(proj, "S1")
    lot = ws.create_lot(proj, sample, "L1")
    result1 = GrainDetector().analyze(mosaic_bgr, px_per_um=1.0)
    entry = ImageEntry(image_bgr=mosaic_bgr, result=result1, filename="frame1.png")
    ref = save_session(lot, {}, [entry])

    result2 = GrainDetector().analyze(mosaic_bgr, px_per_um=3.0)
    new_entry = ImageEntry(image_bgr=mosaic_bgr, result=result2, filename="frame1.png")
    update_session(ref.path, images=[new_entry], meta_updates={"notes": "re-analyzed"})

    loaded = load_session(ref.path)
    assert loaded.manifest.notes == "re-analyzed"
    assert loaded.images[0].result.px_per_um == pytest.approx(3.0)


def test_update_session_report_json(tmp_path, mosaic_bgr):
    ws = Workspace(tmp_path)
    proj = ws.create_project("P1")
    sample = ws.create_sample(proj, "S1")
    lot = ws.create_lot(proj, sample, "L1")
    ref = save_session(lot, {}, [ImageEntry(image_bgr=mosaic_bgr, filename="a.png")])
    update_session(ref.path, report={"title": "My Report", "sections": []})
    loaded = load_session(ref.path)
    assert loaded.report["title"] == "My Report"


def test_dedupe_identical_image_content(tmp_path, mosaic_bgr):
    ws = Workspace(tmp_path)
    proj = ws.create_project("P1")
    sample = ws.create_sample(proj, "S1")
    lot = ws.create_lot(proj, sample, "L1")
    e1 = ImageEntry(image_bgr=mosaic_bgr, filename="dup.png")
    e2 = ImageEntry(image_bgr=mosaic_bgr, filename="dup.png")  # identical content
    ref = save_session(lot, {}, [e1, e2])
    loaded = load_session(ref.path)
    # identical bytes -> the same physical file is reused, not duplicated
    assert len(list((ref.path / "images").iterdir())) == 1
    assert loaded.images[0].entry.sha256 == loaded.images[1].entry.sha256


def test_dedupe_different_content_same_name_suffixed(tmp_path, mosaic_bgr):
    ws = Workspace(tmp_path)
    proj = ws.create_project("P1")
    sample = ws.create_sample(proj, "S1")
    lot = ws.create_lot(proj, sample, "L1")
    other = (mosaic_bgr.astype(np.int16) + 1).clip(0, 255).astype(np.uint8)
    e1 = ImageEntry(image_bgr=mosaic_bgr, filename="same.png")
    e2 = ImageEntry(image_bgr=other, filename="same.png")
    ref = save_session(lot, {}, [e1, e2])
    files = sorted(p.name for p in (ref.path / "images").iterdir())
    assert "same.png" in files
    assert any("(2)" in f for f in files)


def test_import_loose_images_creates_hierarchy_and_session(tmp_path, mosaic_bgr):
    import cv2
    ws = Workspace(tmp_path)
    src_dir = tmp_path / "loose"
    src_dir.mkdir()
    p1 = src_dir / "img1.png"
    cv2.imwrite(str(p1), mosaic_bgr)

    ref = import_loose_images(ws, [p1], "NewProj", "NewSample", "NewLot",
                               session_label="import1")
    assert ref.path.exists()
    loaded = load_session(ref.path)
    assert loaded.images[0].entry.has_result is False
    assert loaded.images[0].entry.source_path.endswith("img1.png")
    projects = ws.list_projects()
    assert any(p.name == "NewProj" for p in projects)
