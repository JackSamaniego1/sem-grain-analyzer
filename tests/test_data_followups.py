"""DATA-09 follow-ups: trash/restore for project/sample/lot, the CLEAR
sentinel for update_session, per-image calibration before analysis, and
first-class grain-filter persistence (with legacy-shape read compat)."""
import pytest

from core.grain_detector import GrainDetector
from data.catalog import Catalog
from data.models import CLEAR, ImageEntry
from data.session_io import load_session, save_session, update_session
from data.workspace import Workspace


def _analyze(mosaic_bgr):
    return GrainDetector().analyze(mosaic_bgr, px_per_um=2.0)


def _make_lot(tmp_path):
    ws = Workspace(tmp_path)
    proj = ws.create_project("P1")
    sample = ws.create_sample(proj, "S1")
    lot = ws.create_lot(proj, sample, "L1")
    return ws, proj, sample, lot


# ======================================================================
# 1. trash / restore for project, sample, lot
# ======================================================================

def test_trash_lot_moves_and_records_origin(tmp_path):
    ws, proj, sample, lot = _make_lot(tmp_path)
    rel = lot.resolve().relative_to(tmp_path.resolve())

    dest = ws.trash_lot(proj, sample, lot)

    assert not lot.exists()
    assert dest.exists()
    assert dest.parent == ws.root / ".trash"
    assert "__".join(rel.parts) in dest.name
    assert (dest / "lot.json").exists()
    assert (dest / "_trash_origin.json").exists()


def test_trash_project_and_sample(tmp_path):
    ws, proj, sample, lot = _make_lot(tmp_path)

    dest_sample = ws.trash_sample(proj, sample)
    assert not sample.exists()
    assert dest_sample.exists()
    assert (dest_sample / "sample.json").exists()

    ws2, proj2, _, _ = _make_lot(tmp_path / "other")
    dest_project = ws2.trash_project(proj2)
    assert not proj2.exists()
    assert (dest_project / "project.json").exists()


def test_trash_refuses_workspace_root(tmp_path):
    ws = Workspace(tmp_path)
    with pytest.raises(ValueError):
        ws._trash_dir(ws.root)


def test_trash_lot_removes_sessions_from_catalog(tmp_path, mosaic_bgr):
    ws, proj, sample, lot = _make_lot(tmp_path)
    result = _analyze(mosaic_bgr)
    entry = ImageEntry(image_bgr=mosaic_bgr, result=result, filename="a.png")
    catalog = Catalog(tmp_path)
    ref = save_session(lot, {"project": "P1", "sample_id": "S1", "lot_number": "L1"},
                        [entry], catalog=catalog)
    assert catalog.search()  # indexed

    ws.trash_lot(proj, sample, lot, catalog=catalog)

    assert catalog.search() == []
    # rebuild from disk also finds nothing since the manifest moved into .trash
    catalog.rebuild()
    assert catalog.search() == []


def test_list_trash_and_restore_roundtrip(tmp_path):
    ws, proj, sample, lot = _make_lot(tmp_path)
    dest = ws.trash_lot(proj, sample, lot)

    entries = ws.list_trash()
    assert len(entries) == 1
    assert entries[0]["path"] == dest
    assert entries[0]["origin_relpath"]

    restored = ws.restore_from_trash(dest)
    assert restored.exists()
    assert (restored / "lot.json").exists()
    assert not dest.exists()
    assert not (restored / "_trash_origin.json").exists()
    assert ws.list_trash() == []


def test_restore_from_trash_reindexes_catalog(tmp_path, mosaic_bgr):
    ws, proj, sample, lot = _make_lot(tmp_path)
    result = _analyze(mosaic_bgr)
    entry = ImageEntry(image_bgr=mosaic_bgr, result=result, filename="a.png")
    catalog = Catalog(tmp_path)
    save_session(lot, {"project": "P1", "sample_id": "S1", "lot_number": "L1"},
                 [entry], catalog=catalog)

    dest = ws.trash_lot(proj, sample, lot, catalog=catalog)
    assert catalog.search() == []

    ws.restore_from_trash(dest, catalog=catalog)
    assert len(catalog.search()) == 1


def test_restore_from_trash_refuses_when_target_exists(tmp_path):
    ws, proj, sample, lot = _make_lot(tmp_path)
    dest = ws.trash_lot(proj, sample, lot)
    # recreate a lot at the original location
    ws.create_lot(proj, sample, "L1")

    with pytest.raises(FileExistsError) as exc_info:
        ws.restore_from_trash(dest)
    assert exc_info.value.suggested_alternative is not None
    assert exc_info.value.suggested_alternative.name != "L1"


def test_restore_from_trash_requires_origin_marker(tmp_path):
    ws = Workspace(tmp_path)
    trash_root = ws.root / ".trash"
    trash_root.mkdir()
    stray = trash_root / "hand_placed_folder"
    stray.mkdir()
    with pytest.raises(ValueError):
        ws.restore_from_trash(stray)


# ======================================================================
# 2. CLEAR sentinel for update_session
# ======================================================================

def test_clear_sentinel_resets_per_image_fields(tmp_path, mosaic_bgr):
    ws, proj, sample, lot = _make_lot(tmp_path)
    result = _analyze(mosaic_bgr)
    entry = ImageEntry(image_bgr=mosaic_bgr, result=result, filename="a.png",
                        scan_rect=(1, 2, 3, 4), px_per_um=5.0, notes="hello")
    ref = save_session(lot, {}, [entry])

    img = load_session(ref.path).images[0]
    assert img.entry.scan_rect == [1, 2, 3, 4]
    assert img.entry.px_per_um == pytest.approx(5.0)
    assert img.entry.notes == "hello"

    update_session(ref.path, images=[ImageEntry(
        filename="a.png", scan_rect=CLEAR, px_per_um=CLEAR, notes=CLEAR)])

    img2 = load_session(ref.path).images[0]
    assert img2.entry.scan_rect is None
    assert img2.entry.px_per_um == 0.0
    assert img2.entry.notes == ""


def test_none_leaves_per_image_fields_unchanged(tmp_path, mosaic_bgr):
    ws, proj, sample, lot = _make_lot(tmp_path)
    result = _analyze(mosaic_bgr)
    entry = ImageEntry(image_bgr=mosaic_bgr, result=result, filename="a.png",
                        scan_rect=(1, 2, 3, 4), px_per_um=5.0, notes="hello")
    ref = save_session(lot, {}, [entry])

    # Passing an ImageEntry with defaults (None/0.0/"") must not clobber
    # what's already stored.
    update_session(ref.path, images=[ImageEntry(filename="a.png")])

    img = load_session(ref.path).images[0]
    assert img.entry.scan_rect == [1, 2, 3, 4]
    assert img.entry.px_per_um == pytest.approx(5.0)
    assert img.entry.notes == "hello"


def test_clear_sentinel_resets_meta_updates_to_field_default(tmp_path):
    ws, proj, sample, lot = _make_lot(tmp_path)
    ref = save_session(lot, {"notes": "session note", "tags": ["a", "b"],
                              "px_per_um": 3.0}, [])

    update_session(ref.path, meta_updates={"notes": CLEAR, "tags": CLEAR, "px_per_um": CLEAR})

    loaded = load_session(ref.path)
    assert loaded.manifest.notes == ""
    assert loaded.manifest.tags == []
    assert loaded.manifest.px_per_um == 0.0


def test_none_meta_update_leaves_field_unchanged(tmp_path):
    ws, proj, sample, lot = _make_lot(tmp_path)
    ref = save_session(lot, {"notes": "session note"}, [])

    update_session(ref.path, meta_updates={"notes": None, "px_per_um": 9.0})

    loaded = load_session(ref.path)
    assert loaded.manifest.notes == "session note"
    assert loaded.manifest.px_per_um == pytest.approx(9.0)


# ======================================================================
# 3. Per-image calibration before analysis (no result yet)
# ======================================================================

def test_calibrate_image_before_it_has_a_result(tmp_path):
    ws, proj, sample, lot = _make_lot(tmp_path)
    # Image with no analysis result at all yet (e.g. just imported).
    entry = ImageEntry(source_path=None, filename="a.png", image_bgr=None)
    # save_session requires source_path or image_bgr; use import-style entry
    import numpy as np
    blank = (np.ones((16, 16, 3), dtype="uint8") * 128)
    ref = save_session(lot, {}, [ImageEntry(image_bgr=blank, filename="a.png")])

    loaded = load_session(ref.path)
    assert loaded.images[0].entry.has_result is False
    assert loaded.images[0].entry.px_per_um == 0.0

    update_session(ref.path, images=[ImageEntry(
        filename="a.png", result=None, px_per_um=4.25, scan_rect=(0, 0, 8, 8))])

    reloaded = load_session(ref.path)
    img = reloaded.images[0]
    assert img.entry.has_result is False  # still no result
    assert img.entry.px_per_um == pytest.approx(4.25)
    assert img.entry.scan_rect == [0, 0, 8, 8]


# ======================================================================
# 4. Grain-filter persistence: first-class fields + legacy-shape read compat
# ======================================================================

def test_filters_round_trip_first_class_fields(tmp_path, mosaic_bgr):
    ws, proj, sample, lot = _make_lot(tmp_path)
    result = _analyze(mosaic_bgr)
    entry = ImageEntry(image_bgr=mosaic_bgr, result=result, filename="a.png")
    ref = save_session(lot, {"filters": {"min_area_um2": 1.5}}, [entry])

    update_session(ref.path, images=[ImageEntry(
        filename="a.png", result=None,
        filters_override={"min_area_um2": 9.0}, manual_excluded=[3, 7])])

    loaded = load_session(ref.path)
    assert loaded.manifest.filters == {"min_area_um2": 1.5}
    img = loaded.images[0]
    assert img.entry.filters_override == {"min_area_um2": 9.0}
    assert img.entry.manual_excluded == [3, 7]

    # CLEAR resets the per-image override/exclusions.
    update_session(ref.path, images=[ImageEntry(
        filename="a.png", result=None, filters_override=CLEAR, manual_excluded=CLEAR)])
    reloaded = load_session(ref.path)
    img2 = reloaded.images[0]
    assert img2.entry.filters_override is None
    assert img2.entry.manual_excluded == []


def test_legacy_post_filters_shape_is_read_as_first_class_fields(tmp_path, mosaic_bgr):
    """Manifests written by the pre-DATA-09 UI stash filter state under
    detection_params['post_filters'] instead of the new manifest keys;
    load_session must still expose it through filters/filters_override/
    manual_excluded so callers only need to read one shape."""
    ws, proj, sample, lot = _make_lot(tmp_path)
    result = _analyze(mosaic_bgr)
    entry = ImageEntry(image_bgr=mosaic_bgr, result=result, filename="a.png")
    ref = save_session(lot, {}, [entry])

    legacy_post_filters = {
        "options": {"min_area_um2": 2.0},
        "images": {"a.png": {"excluded": {}, "manual": [1, 2], "options": {"min_area_um2": 6.0}}},
    }
    update_session(ref.path, meta_updates={"detection_params": {"post_filters": legacy_post_filters}})

    loaded = load_session(ref.path)
    assert loaded.manifest.filters == {"min_area_um2": 2.0}
    img = loaded.images[0]
    assert img.entry.filters_override == {"min_area_um2": 6.0}
    assert img.entry.manual_excluded == [1, 2]


def test_first_class_filters_take_priority_over_legacy_shape(tmp_path, mosaic_bgr):
    ws, proj, sample, lot = _make_lot(tmp_path)
    result = _analyze(mosaic_bgr)
    entry = ImageEntry(image_bgr=mosaic_bgr, result=result, filename="a.png")
    ref = save_session(lot, {"filters": {"min_area_um2": 1.0}}, [entry])

    legacy_post_filters = {"options": {"min_area_um2": 99.0}, "images": {}}
    update_session(ref.path, meta_updates={"detection_params": {"post_filters": legacy_post_filters}})

    loaded = load_session(ref.path)
    # first-class value wins; legacy shape is only a fallback for empty fields
    assert loaded.manifest.filters == {"min_area_um2": 1.0}
