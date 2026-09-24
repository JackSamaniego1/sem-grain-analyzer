"""Catalog index/search/rebuild, and tolerance of a locked/corrupt DB."""
from data.catalog import Catalog
from data.models import ImageEntry
from data.session_io import save_session
from data.workspace import Workspace


def _make_session(tmp_path, mosaic_bgr, project="P1", sample="S1", lot="L1",
                   operator="jack", notes=""):
    ws = Workspace(tmp_path)
    proj = ws.create_project(project)
    s = ws.create_sample(proj, sample)
    l = ws.create_lot(proj, s, lot)
    from core.grain_detector import GrainDetector
    result = GrainDetector().analyze(mosaic_bgr, px_per_um=1.0)
    entry = ImageEntry(image_bgr=mosaic_bgr, result=result, filename="a.png")
    return save_session(l, {"project": project, "sample_id": sample, "lot_number": lot,
                             "operator": operator, "notes": notes}, [entry])


def test_index_and_search(tmp_path, mosaic_bgr):
    ref = _make_session(tmp_path, mosaic_bgr, notes="qualification run")
    cat = Catalog(tmp_path)
    assert cat.index_session(ref.path)

    results = cat.search("qualification")
    assert len(results) == 1
    assert results[0]["path"] == str(ref.path)
    assert results[0]["grain_count"] > 0

    results = cat.search("", filters={"lot_number": "L1"})
    assert len(results) == 1

    assert cat.search("nonexistent-text-xyz") == []


def test_remove(tmp_path, mosaic_bgr):
    ref = _make_session(tmp_path, mosaic_bgr)
    cat = Catalog(tmp_path)
    cat.index_session(ref.path)
    assert len(cat.search("")) == 1
    cat.remove(ref.path)
    assert len(cat.search("")) == 0


def test_rebuild_scans_all_manifests(tmp_path, mosaic_bgr):
    _make_session(tmp_path, mosaic_bgr, project="P1", sample="S1", lot="L1")
    _make_session(tmp_path, mosaic_bgr, project="P2", sample="S1", lot="L1")
    cat = Catalog(tmp_path)
    count = cat.rebuild()
    assert count == 2
    assert len(cat.search("")) == 2


def test_corrupt_db_falls_back_to_manifest_scan(tmp_path, mosaic_bgr):
    ref = _make_session(tmp_path, mosaic_bgr, notes="corrupt-db-test")
    cat = Catalog(tmp_path)
    cat.index_session(ref.path)

    # Corrupt the DB file directly.
    cat.db_path.write_bytes(b"not a sqlite database at all")

    results = cat.search("corrupt-db-test")
    assert len(results) == 1
    assert results[0]["path"] == str(ref.path)


def test_rebuild_recovers_from_corrupt_db(tmp_path, mosaic_bgr):
    ref = _make_session(tmp_path, mosaic_bgr)
    cat = Catalog(tmp_path)
    cat.db_path.write_bytes(b"garbage")
    count = cat.rebuild()
    assert count == 1
    assert len(cat.search("")) == 1
