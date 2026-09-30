"""add_images_to_lot: the single data-layer call behind drag-and-drop onto a lot."""
from pathlib import Path

import cv2
import numpy as np
import pytest

from data.catalog import Catalog
from data.hierarchy import PRESETS
from data.models import ImageEntry, SessionMeta, read_json
from data.session_io import IMAGE_EXTS, add_images_to_lot, load_session, save_session
from data.workspace import Workspace


def _img(path, seed):
    arr = np.random.default_rng(seed).integers(0, 255, (24, 24, 3)).astype("uint8")
    ok, buf = cv2.imencode(Path(path).suffix, arr)   # imwrite mangles unicode paths on Windows
    assert ok
    Path(path).write_bytes(buf.tobytes())
    return path


@pytest.fixture
def lot(tmp_path):
    ws = Workspace(tmp_path / "ws")
    ws.set_profile(PRESETS["job_part_lot"])
    job = ws.create_project("24-117")
    part = ws.create_sample(job, "7718-A")
    return ws.create_lot(job, part, "L-44A")


@pytest.fixture
def src(tmp_path):
    d = tmp_path / "loose"
    d.mkdir()
    return d


def _names(lot):
    return [i.filename for i in SessionMeta.from_dict(read_json(lot / "manifest.json")).images]


def test_ext_list_matches_ui():
    workers = pytest.importorskip("ui.workers")
    assert set(IMAGE_EXTS) == set(workers.IMAGE_EXTS)


def test_empty_lot_copies_and_writes_manifest(lot, src):
    a, b = _img(src / "a.png", 1), _img(src / "b.tif", 2)
    cat = Catalog(lot.parent.parent.parent)
    res = add_images_to_lot(lot, [a, b], catalog=cat)
    assert [x.filename for x in res.added] == ["a.png", "b.tif"]
    assert not res.skipped and not res.rejected
    assert a.exists() and (lot / "images" / "a.png").is_file()  # copied, not moved
    assert _names(lot) == ["a.png", "b.tif"]
    assert (lot / "thumbs" / "a.jpg").exists()
    m = read_json(lot / "manifest.json")
    assert m["images"][0]["original_name"] == "a.png" and m["images"][0]["sha256"]
    assert len(load_session(lot).images) == 2
    assert cat.search("")  # indexed


def test_non_empty_lot_appends(lot, src):
    save_session(lot, {}, [ImageEntry(source_path=str(_img(src / "a.png", 1)))], in_place=True)
    res = add_images_to_lot(lot, [_img(src / "c.png", 3)])
    assert [x.filename for x in res.added] == ["c.png"]
    assert _names(lot) == ["a.png", "c.png"]


def test_unsupported_and_missing_rejected(lot, src):
    t = src / "notes.pdf"
    t.write_text("x")
    res = add_images_to_lot(lot, [t, src / "ghost.png", _img(src / "ok.png", 1)])
    assert len(res.added) == 1
    assert {r.path for r in res.rejected} == {str(t), str(src / "ghost.png")}
    assert "Unsupported" in res.rejected[0].reason
    assert not (lot / "images" / "notes.pdf").exists()


def test_same_content_skipped_and_different_content_suffixed(lot, src, tmp_path):
    a = _img(src / "a.png", 1)
    add_images_to_lot(lot, [a])
    other = tmp_path / "other"
    other.mkdir()
    same = other / "a.png"
    same.write_bytes(a.read_bytes())
    res = add_images_to_lot(lot, [same])
    assert not res.added and res.skipped[0].reason == "Already in this lot."
    diff = _img(other / "a.png", 99)   # same name, new content
    res = add_images_to_lot(lot, [diff])
    assert [x.filename for x in res.added] == ["a-2.png"]
    assert _names(lot) == ["a.png", "a-2.png"]
    assert (lot / "images" / "a.png").read_bytes() == a.read_bytes()  # untouched


def test_duplicates_within_one_drop(lot, src):
    a = _img(src / "a.png", 1)
    res = add_images_to_lot(lot, [a, a])
    assert len(res.added) == 1 and len(res.skipped) == 1


def test_source_already_in_lot_folder(lot, src):
    add_images_to_lot(lot, [_img(src / "a.png", 1)])
    before = (lot / "images" / "a.png").read_bytes()
    res = add_images_to_lot(lot, [lot / "images" / "a.png"])   # registered -> skipped
    assert not res.added and len(res.skipped) == 1
    assert _names(lot) == ["a.png"]
    # an unregistered file sitting in images/ is registered without a copy
    _img(lot / "images" / "z.png", 5)
    res = add_images_to_lot(lot, [lot / "images" / "z.png"])
    assert [x.filename for x in res.added] == ["z.png"]
    assert sorted(p.name for p in (lot / "images").iterdir()) == ["a.png", "z.png"]
    assert (lot / "images" / "a.png").read_bytes() == before


def test_sidecar_copied_with_image(lot, src):
    a = _img(src / "jeol1.tif", 1)
    (src / "jeol1.txt").write_text("$CM_MAG 5000\n")
    (src / "jeol1-tif.hdr").write_text("[Main]\n")
    res = add_images_to_lot(lot, [a])
    assert sorted(res.added[0].sidecars) == ["jeol1-tif.hdr", "jeol1.txt"]
    assert (lot / "images" / "jeol1.txt").read_text() == "$CM_MAG 5000\n"


def test_sidecar_follows_renamed_image(lot, src):
    a = _img(src / "raw.tif", 1)
    (src / "raw.txt").write_text("m")
    res = add_images_to_lot(lot, [a], image_name_template="{lot}_{index:02}",
                            name_context={"lot": "L-44A"})
    name = res.added[0].filename
    assert name == "L-44A_01.tif"
    assert (lot / "images" / "L-44A_01.txt").exists()
    assert read_json(lot / "manifest.json")["images"][0]["original_name"] == "raw.tif"


def test_not_a_lot_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        add_images_to_lot(tmp_path, [])


def _tree(lot):
    return {p.relative_to(lot).as_posix(): p.read_bytes() for p in lot.rglob("*") if p.is_file()}


def test_manifest_write_failure_rolls_back_everything(lot, src, monkeypatch):
    add_images_to_lot(lot, [_img(src / "a.png", 1)])
    before = _tree(lot)
    b = _img(src / "b.png", 2)
    (src / "b.txt").write_text("m")
    import data.session_io as sio
    def boom(*a, **k):
        raise OSError("disk gone")
    monkeypatch.setattr(sio, "write_json_atomic", boom)
    with pytest.raises(OSError):
        add_images_to_lot(lot, [b])
    assert _tree(lot) == before


def test_folder_and_lnk_rejected(lot, src):
    (src / "sub").mkdir()
    (src / "x.lnk").write_text("l")
    res = add_images_to_lot(lot, [src / "sub", src / "x.lnk"])
    assert "Folders aren't supported" in res.rejected[0].reason
    assert "Unsupported" in res.rejected[1].reason


def test_same_name_from_two_folders_in_one_call(lot, tmp_path):
    d1, d2 = tmp_path / "d1", tmp_path / "d2"
    d1.mkdir(); d2.mkdir()
    res = add_images_to_lot(lot, [_img(d1 / "a.png", 1), _img(d2 / "a.png", 2)])
    assert [x.filename for x in res.added] == ["a.png", "a-2.png"]


def test_unicode_filename(lot, src):
    res = add_images_to_lot(lot, [_img(src / "grain_\u00b5m_\u30c6\u30b9\u30c8.png", 1)])
    assert len(res.added) == 1 and (lot / "images" / res.added[0].filename).is_file()


def test_read_only_source(lot, src):
    import os, stat
    a = _img(src / "ro.png", 1)
    os.chmod(a, stat.S_IREAD)
    try:
        res = add_images_to_lot(lot, [a])
        assert len(res.added) == 1
    finally:
        os.chmod(a, stat.S_IWRITE)


def test_stale_different_sidecar_moves_image_to_next_stem(lot, src):
    (lot / "images" / "s.txt").write_text("STALE calibration")
    a = _img(src / "s.tif", 1)
    (src / "s.txt").write_text("fresh")
    res = add_images_to_lot(lot, [a])
    assert res.added[0].filename == "s-2.tif"
    assert (lot / "images" / "s.txt").read_text() == "STALE calibration"
    assert (lot / "images" / "s-2.txt").read_text() == "fresh"


def test_identical_existing_sidecar_left_alone(lot, src):
    (lot / "images" / "s.txt").write_text("same")
    a = _img(src / "s.tif", 1)
    (src / "s.txt").write_text("same")
    res = add_images_to_lot(lot, [a])
    assert res.added[0].filename == "s.tif" and res.added[0].sidecars == []
