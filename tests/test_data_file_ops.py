"""UI-09 data layer: folder moves, image move / rename / trash / restore."""
from pathlib import Path

import pytest

from data.catalog import Catalog
from data.file_ops import (
    classify, image_files, is_image_trash, move_destinations, move_images, move_node,
    rename_image, restore_any, restore_images, trash_images,
)
from data.models import ImageEntry, read_json
from data.session_io import load_session, save_session
from data.workspace import Workspace
from tests.ui_shell_helpers import make_session, mosaic_png


def _lot_record(root: Path, lot: str, n: int = 2, sample: str = "S-1", project: str = "Proj",
                px: float = 0.0) -> Path:
    ws = Workspace(root)
    try:
        pp = ws.resolve_project(project)
    except FileNotFoundError:
        pp = ws.create_project(project)
    try:
        sp = ws.resolve_sample(pp, sample)
    except FileNotFoundError:
        sp = ws.create_sample(pp, sample)
    lp = ws.create_lot(pp, sp, lot)
    src = root.parent / "src" / lot
    src.mkdir(parents=True, exist_ok=True)
    ents = [ImageEntry(source_path=mosaic_png(src / f"img_{i}.png", seed=3 + i)) for i in range(n)]
    save_session(lp, {"px_per_um": px, "filters": {"min_area": 5} if px else {}}, ents,
                 in_place=True, catalog=Catalog(root))
    return lp


def _names(sd: Path):
    return [e["filename"] for e in read_json(sd / "manifest.json")["images"]]


def test_classify_levels(tmp_path):
    root = tmp_path / "ws"
    sess = make_session(root, 1)
    lot = sess.parent
    assert classify(sess) == "session"
    assert classify(lot) == "lot"
    assert classify(lot.parent) == "sample"
    assert classify(lot.parent.parent) == "project"
    assert classify(tmp_path) is None


def test_move_destinations_same_level_only(tmp_path):
    root = tmp_path / "ws"
    a = _lot_record(root, "A")
    b = _lot_record(root, "B")
    c = _lot_record(root, "C", sample="S-2", project="Other")
    ws = Workspace(root)
    # images -> any other lot, never their own
    dests = move_destinations(ws, "image", [a])
    assert set(dests) == {b, c}
    # lot -> other samples only (never lots / projects / its own sample)
    dests = move_destinations(ws, "lot", [a])
    assert dests == [c.parent]
    assert all(classify(d) == "sample" for d in dests)
    # sample -> other projects
    assert move_destinations(ws, "sample", [a.parent]) == [c.parent.parent]
    # projects cannot be moved anywhere
    assert move_destinations(ws, "project", [a.parent.parent]) == []


def test_move_lot_to_other_sample_relabels_and_reindexes(tmp_path):
    root = tmp_path / "ws"
    a = _lot_record(root, "A")
    other = _lot_record(root, "X", sample="S-9", project="P2").parent
    ws, cat = Workspace(root), Catalog(root)
    new = move_node(ws, a, other, catalog=cat)
    assert not a.exists() and new.parent == other and (new / "lot.json").exists()
    m = read_json(new / "manifest.json")
    assert m["sample_id"] == "S-9" and m["project"] == "P2" and m["lot_number"] == "A"
    rows = cat.search("")
    assert str(new) in {r["path"] for r in rows}
    assert str(a) not in {r["path"] for r in rows}
    # moving back restores the original place (Undo)
    back = move_node(ws, new, a.parent, catalog=cat)
    assert back == a and a.exists()


def test_move_node_rejects_wrong_level_and_dedupes(tmp_path):
    root = tmp_path / "ws"
    a = _lot_record(root, "A")
    b = _lot_record(root, "A", sample="S-2")
    ws = Workspace(root)
    with pytest.raises(ValueError):
        move_node(ws, a, b)                   # lot into a lot
    with pytest.raises(ValueError):
        move_node(ws, a.parent.parent, root)  # projects don't move
    new = move_node(ws, a, b.parent)          # same name at the target → " (2)"
    assert new.name == "A (2)" and b.exists()


def test_move_session_between_lots(tmp_path):
    root = tmp_path / "ws"
    sess = make_session(root, 1, label="Run")
    other = make_session(root, 1, label="Other", lot="L-2").parent
    new = move_node(Workspace(root), sess, other)
    assert new.parent == other
    assert read_json(new / "manifest.json")["lot_number"] == "L-2"


def test_move_images_to_other_lot(tmp_path):
    root = tmp_path / "ws"
    a = _lot_record(root, "A", n=3, px=2.5)
    b = _lot_record(root, "B", n=1)
    ws = Workspace(root)
    first = _names(a)[0]
    n_files = len(image_files(a, first))
    assert n_files >= 2                       # image + thumb at least
    # give B an image with the same name to force a rename
    moved = move_images(ws, a, [first], b, catalog=Catalog(root))
    (old, new), = moved
    assert old == first and new != first      # deduped
    assert first not in _names(a) and new in _names(b)
    assert len(image_files(b, new)) == n_files and not image_files(a, first)
    ent = [e for e in read_json(b / "manifest.json")["images"] if e["filename"] == new][0]
    assert ent["px_per_um"] == 2.5 and ent["filters_override"] == {"min_area": 5}
    loaded = load_session(b)
    assert loaded.image_by_filename(new) is not None
    # Undo: move back under the original name
    back = move_images(ws, b, [new], a, target_names=[first])
    assert back == [(new, first)] and first in _names(a) and new not in _names(b)


def test_move_images_creates_manifest_in_empty_lot(tmp_path):
    root = tmp_path / "ws"
    a = _lot_record(root, "A", n=2)
    ws = Workspace(root)
    empty = ws.create_lot(a.parent.parent, a.parent, "Empty")
    fn = _names(a)[1]
    move_images(ws, a, [fn], empty)
    m = read_json(empty / "manifest.json")
    assert [e["filename"] for e in m["images"]] == [fn]
    assert m["lot_number"] == "Empty"


def test_rename_image_keeps_extension_and_results(tmp_path):
    root = tmp_path / "ws"
    a = _lot_record(root, "A", n=2)
    ws = Workspace(root)
    fn = _names(a)[0]
    ext = Path(fn).suffix
    n = len(image_files(a, fn))
    new = rename_image(ws, a, fn, "Transverse 01")
    assert new == "Transverse 01" + ext
    assert new in _names(a) and fn not in _names(a)
    assert len(image_files(a, new)) == n
    # clash with the other image → deduped, never overwritten
    other = _names(a)[0] if _names(a)[0] != new else _names(a)[1]
    new2 = rename_image(ws, a, other, Path(new).stem)
    assert new2 == "Transverse 01 (2)" + ext


def test_trash_images_and_restore(tmp_path):
    root = tmp_path / "ws"
    a = _lot_record(root, "A", n=3)
    ws = Workspace(root)
    names = _names(a)
    victims = names[:2]
    tp = trash_images(ws, a, victims, catalog=Catalog(root))
    assert tp.parent == root / ".trash" and is_image_trash(tp)
    assert _names(a) == names[2:]
    assert not any(image_files(a, v) for v in victims)
    assert any(Path(t["path"]) == tp for t in ws.list_trash())
    back = restore_any(ws, tp, catalog=Catalog(root))
    assert back == a and sorted(_names(a)) == sorted(names)
    assert all(image_files(a, v) for v in victims) and not tp.exists()


def test_restore_images_never_overwrites(tmp_path):
    root = tmp_path / "ws"
    a = _lot_record(root, "A", n=2)
    ws = Workspace(root)
    fn = _names(a)[0]
    tp = trash_images(ws, a, [fn])
    # a new image with the same name arrives meanwhile
    b = _lot_record(root, "B", n=2)
    move_images(ws, b, [fn], a)
    with pytest.raises(FileExistsError):
        restore_images(ws, tp)
    assert tp.exists()


def test_file_ops_has_no_qt_imports():
    src = (Path(__file__).parent.parent / "data" / "file_ops.py").read_text(encoding="utf-8")
    assert "import PySide6" not in src and "from PySide6" not in src
