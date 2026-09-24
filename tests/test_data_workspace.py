"""Hierarchy CRUD, sanitization/dedupe on disk, trash-not-delete."""
import pytest

from data.workspace import Workspace


def test_create_hierarchy(tmp_path):
    ws = Workspace(tmp_path)
    proj = ws.create_project("Alloy-718 Qualification", customer="Acme")
    sample = ws.create_sample(proj, "S-2024-0917", material="Steel")
    lot = ws.create_lot(proj, sample, "LOT-44A", supplier="Acme Metals")

    assert (proj / "project.json").exists()
    assert (sample / "sample.json").exists()
    assert (lot / "lot.json").exists()

    projects = ws.list_projects()
    assert len(projects) == 1
    assert projects[0].name == "Alloy-718 Qualification"
    assert projects[0].path == str(proj)

    samples = ws.list_samples(proj)
    assert samples[0].sample_id == "S-2024-0917"

    lots = ws.list_lots(proj, sample)
    assert lots[0].lot_number == "LOT-44A"
    assert lots[0].supplier == "Acme Metals"


def test_create_by_display_name_resolution(tmp_path):
    ws = Workspace(tmp_path)
    ws.create_project("Alloy-718", customer="Acme")
    ws.create_sample("Alloy-718", "S-1")
    ws.create_lot("Alloy-718", "S-1", "LOT-1")

    lots = ws.list_lots("Alloy-718", "S-1")
    assert len(lots) == 1


def test_sanitize_and_dedupe_on_create(tmp_path):
    ws = Workspace(tmp_path)
    p1 = ws.create_project("LOT/44:A")
    assert "/" not in p1.name and ":" not in p1.name

    p2 = ws.create_project("LOT/44:A")  # sanitizes to the same folder name
    assert p1 != p2
    assert p2.name.endswith("(2)") or p2.name != p1.name


def test_rename_project_updates_folder_and_meta(tmp_path):
    ws = Workspace(tmp_path)
    proj = ws.create_project("Old Name")
    new_path = ws.rename_project(proj, "New Name")
    assert new_path.exists()
    assert not proj.exists()
    projects = ws.list_projects()
    assert projects[0].name == "New Name"


def test_update_project_meta_partial(tmp_path):
    ws = Workspace(tmp_path)
    proj = ws.create_project("P1", description="orig")
    ws.update_project_meta(proj, description="updated")
    projects = ws.list_projects()
    assert projects[0].description == "updated"
    assert projects[0].name == "P1"  # untouched field preserved


def test_delete_session_moves_to_trash_not_deleted(tmp_path):
    ws = Workspace(tmp_path)
    proj = ws.create_project("P1")
    sample = ws.create_sample(proj, "S1")
    lot = ws.create_lot(proj, sample, "L1")
    session_dir = lot / "2026-01-01_000000"
    session_dir.mkdir()
    (session_dir / "manifest.json").write_text("{}", encoding="utf-8")
    marker = session_dir / "images" / "keepme.txt"
    marker.parent.mkdir(parents=True)
    marker.write_text("data", encoding="utf-8")

    trashed = ws.delete_session(session_dir)
    assert not session_dir.exists()
    assert trashed.exists()
    assert (trashed / "images" / "keepme.txt").read_text(encoding="utf-8") == "data"
    assert (tmp_path / ".trash").exists()


def test_resolve_missing_raises(tmp_path):
    ws = Workspace(tmp_path)
    with pytest.raises(FileNotFoundError):
        ws.resolve_project("does-not-exist")
