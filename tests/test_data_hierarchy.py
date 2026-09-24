"""HIER-01: user-defined folder hierarchy/naming -- profile load/save,
template rendering/validation, folder-template-driven create, lot-as-session
save/history, catalog field search, and rename-to-template preview/apply/undo."""
from datetime import datetime
from pathlib import Path

import pytest

from data.catalog import Catalog
from data.hierarchy import (
    FieldDef, HierarchyProfile, LevelDef, PRESETS, context_for_session,
    load_profile, render_template, save_profile, validate_template,
)
from data.models import ImageEntry
from data.session_io import load_session, save_session
from data.workspace import Workspace, apply_renames, rename_to_template
from core.grain_detector import AnalysisResult


# ======================================================================
# presets / default rules
# ======================================================================

def test_new_workspace_defaults_to_job_part_lot(tmp_path):
    profile = load_profile(tmp_path)
    assert profile.images_location == "lot"
    assert [lv.key for lv in profile.levels] == ["project", "sample", "lot"]
    assert profile.level("project").label == "Job #"
    assert (tmp_path / "workspace.json").exists()
    # stable on a second call
    profile2 = load_profile(tmp_path)
    assert profile2.images_location == "lot"


def test_existing_workspace_without_workspace_json_gets_legacy_preset(tmp_path):
    # simulate a workspace that already had projects before HIER-01
    proj = tmp_path / "Alloy-718"
    proj.mkdir()
    (proj / "project.json").write_text('{"name": "Alloy-718"}', encoding="utf-8")

    profile = load_profile(tmp_path)
    assert profile.images_location == "session"
    assert profile.name == PRESETS["project_sample_lot_session"].name
    assert (tmp_path / "workspace.json").exists()


def test_presets_are_independent_instances():
    a = PRESETS["job_part_lot"]
    b = PRESETS["job_part_lot"]
    a.levels[0].label = "Mutated"
    assert b.levels[0].label != "Mutated"


def test_save_and_load_profile_roundtrip(tmp_path):
    profile = PRESETS["job_part_lot"]
    profile.levels[0].label = "Work Order"
    save_profile(tmp_path, profile)
    loaded = load_profile(tmp_path)
    assert loaded.level("project").label == "Work Order"
    assert loaded.images_location == "lot"


def test_workspace_json_forward_compat(tmp_path):
    """Unknown keys are ignored, missing keys default -- a future app
    version's workspace.json must not break this one."""
    import json
    d = PRESETS["job_part_lot"].to_dict()
    d["schema_version"] = 999
    d["totally_new_future_key"] = {"whatever": True}
    d["levels"][0]["fields"][0]["future_field_key"] = "ignored"
    (tmp_path / "workspace.json").write_text(json.dumps(d), encoding="utf-8")

    profile = load_profile(tmp_path)
    assert profile.images_location == "lot"
    assert profile.level("project").fields[0].key == "customer"


# ======================================================================
# render_template / validate_template
# ======================================================================

def test_render_template_basic_and_missing_keys():
    assert render_template("{project}_{sample}", {"project": "24-117", "sample": "7718-A"}) \
        == "24-117_7718-A"
    assert render_template("{missing}", {}) == ""
    assert render_template("", {"a": "b"}) == ""


def test_render_template_date_format():
    ctx = {"date": datetime(2026, 9, 24, 13, 5, 9)}
    assert render_template("{date:%Y%m%d}", ctx) == "20260924"
    assert render_template("{date:%Y-%m-%d_%H%M%S}", ctx) == "2026-09-24_130509"
    # non-datetime value with a date format spec: never raises
    assert render_template("{date:%Y%m%d}", {"date": "not-a-date"}) == "not-a-date"


def test_render_template_zero_padding():
    assert render_template("{index:02}", {"index": 3}) == "03"
    assert render_template("{index:03}", {"index": 7}) == "007"
    assert render_template("{index:02}", {"index": 42}) == "42"


def test_render_template_conditional_prefix():
    assert render_template("base{label: - }", {"label": "baseline"}) == "base - baseline"
    assert render_template("base{label: - }", {"label": ""}) == "base"
    assert render_template("base{label: - }", {}) == "base"


def test_render_template_for_filename_sanitizes_and_collapses():
    out = render_template("{a}___{b}", {"a": "x", "b": ""}, for_filename=True)
    assert out == "x"
    out2 = render_template("{project}/{sample}", {"project": "A/B", "sample": "C:D"},
                            for_filename=True)
    assert "/" not in out2 and ":" not in out2


def test_render_template_never_raises_on_injection_or_bad_syntax():
    assert render_template("{a.b}", {"a": "x"}) == ""
    assert render_template("{a[0]}", {"a": ["x"]}) == ""
    assert render_template("{__class__}", {}) == ""
    assert render_template("{__class__.__bases__}", {}) == ""
    assert render_template("{unclosed", {"unclosed": "x"}) == "{unclosed"
    assert render_template("plain text, no tokens", {}) == "plain text, no tokens"


def test_validate_template_reports_problems():
    keys = PRESETS["job_part_lot"].available_keys()
    assert validate_template("{project}_{sample}", keys) == []
    problems = validate_template("{bogus_key}", keys)
    assert any("Unknown key" in p for p in problems)
    problems2 = validate_template("{a.b}", keys)
    assert any("Invalid token" in p for p in problems2)
    problems3 = validate_template("{date:%Q}", keys)
    assert any("Invalid date format" in p for p in problems3)
    problems4 = validate_template("{unclosed", keys)
    assert any("Unbalanced" in p for p in problems4)


# ======================================================================
# create_project/sample/lot with a profile + folder templates
# ======================================================================

def _job_part_lot_ws(tmp_path) -> Workspace:
    ws = Workspace(tmp_path)
    ws.set_profile(PRESETS["job_part_lot"])
    return ws


def test_job_part_lot_creates_hierarchy_with_profile_fields(tmp_path):
    ws = _job_part_lot_ws(tmp_path)
    job = ws.create_project("24-117", customer="Acme Aerospace", po_number="PO-99")
    part = ws.create_sample(job, "7718-A", part_description="Bracket",
                             material_alloy="Ti-6Al-4V")
    lot = ws.create_lot(job, part, "L-44A", heat_number="H-2201", supplier="MetalCo")

    assert job.name == "24-117"
    assert part.name == "7718-A"
    assert lot.name == "L-44A"

    import json
    project_json = json.loads((job / "project.json").read_text(encoding="utf-8"))
    assert project_json["customer"] == "Acme Aerospace"
    assert project_json["po_number"] == "PO-99"
    sample_json = json.loads((part / "sample.json").read_text(encoding="utf-8"))
    assert sample_json["part_description"] == "Bracket"
    lot_json = json.loads((lot / "lot.json").read_text(encoding="utf-8"))
    assert lot_json["heat_number"] == "H-2201"

    # lot-as-session: reserved subfolders pre-created
    assert (lot / "images").is_dir()
    assert (lot / "results").is_dir()


def test_images_in_lot_with_three_images(tmp_path):
    """Acceptance: Job 24-117 > Part 7718-A > Lot L-44A with 3 images
    produces 24-117/7718-A/L-44A/{images,results,manifest.json,...}."""
    ws = _job_part_lot_ws(tmp_path)
    job = ws.create_project("24-117")
    part = ws.create_sample(job, "7718-A")
    lot = ws.create_lot(job, part, "L-44A")

    entries = [ImageEntry(image_bgr=_tiny_bgr(), filename=f"img{i}.png") for i in range(3)]
    ref = save_session(lot, {"project": "24-117", "sample_id": "7718-A", "lot_number": "L-44A"},
                        entries, in_place=True)

    assert ref.path == lot
    assert (lot / "manifest.json").exists()
    assert len(list((lot / "images").iterdir())) == 3
    loaded = load_session(lot)
    assert len(loaded.images) == 3
    assert loaded.lot_meta is not None and loaded.lot_meta.get("lot_number") == "L-44A"

    sessions = ws.list_sessions(job, part, lot)
    assert len(sessions) == 1
    assert sessions[0].path == str(lot)


def _tiny_bgr():
    import numpy as np
    return (np.random.default_rng(1).integers(0, 255, (16, 16, 3))).astype("uint8")


def test_lot_as_session_append_and_update(tmp_path):
    ws = _job_part_lot_ws(tmp_path)
    job = ws.create_project("J1")
    part = ws.create_sample(job, "P1")
    lot = ws.create_lot(job, part, "L1")

    e1 = ImageEntry(image_bgr=_tiny_bgr(), filename="a.png")
    save_session(lot, {}, [e1], in_place=True)
    e2 = ImageEntry(image_bgr=_tiny_bgr(), filename="b.png")
    save_session(lot, {}, [e2], in_place=True)

    loaded = load_session(lot)
    assert {i.filename for i in loaded.images} == {"a.png", "b.png"}


def test_legacy_workspace_unchanged(tmp_path):
    """A pre-existing workspace (project already on disk, no
    workspace.json) keeps its old session-per-run behaviour."""
    ws = Workspace(tmp_path)
    proj = ws.create_project("P1")  # triggers default-profile decision
    assert ws.profile.images_location == "lot"  # brand-new workspace here

    # Now simulate the *actually* pre-existing case: a fresh root that
    # already has a project folder before the profile is ever consulted.
    tmp_path2 = tmp_path / "legacy_root"
    tmp_path2.mkdir()
    manual_proj = tmp_path2 / "OldProject"
    manual_proj.mkdir()
    (manual_proj / "project.json").write_text('{"name": "OldProject"}', encoding="utf-8")

    ws2 = Workspace(tmp_path2)
    assert ws2.profile.images_location == "session"
    sample = ws2.create_sample(manual_proj, "S1")
    lot = ws2.create_lot(manual_proj, sample, "L1")
    ref = save_session(lot, {}, [ImageEntry(image_bgr=_tiny_bgr(), filename="x.png")])
    assert ref.path != lot  # legacy: a new timestamped subfolder
    assert ref.path.parent == lot


# ======================================================================
# history retention (re-analysis in a lot-as-session)
# ======================================================================

def test_history_retention_keeps_last_five(tmp_path):
    ws = _job_part_lot_ws(tmp_path)
    job = ws.create_project("J1")
    part = ws.create_sample(job, "P1")
    lot = ws.create_lot(job, part, "L1")

    for i in range(7):
        result = AnalysisResult(px_per_um=float(i + 1))
        entry = ImageEntry(image_bgr=_tiny_bgr(), filename="a.png", result=result)
        save_session(lot, {}, [entry], in_place=True)

    hist_root = lot / "results" / "_history"
    assert hist_root.exists()
    snapshots = [d for d in hist_root.iterdir() if d.is_dir()]
    # 7 saves -> 6 replacements of an existing result -> capped at 5
    assert len(snapshots) == 5

    loaded = load_session(lot)
    assert loaded.images[0].result.px_per_um == pytest.approx(7.0)


# ======================================================================
# catalog: search by profile field values
# ======================================================================

def test_catalog_search_by_part_number_and_heat_number(tmp_path):
    ws = _job_part_lot_ws(tmp_path)
    job = ws.create_project("24-117")
    part = ws.create_sample(job, "7718-A", part_description="Bracket")
    lot = ws.create_lot(job, part, "L-44A", heat_number="HEAT-9001")
    entry = ImageEntry(image_bgr=_tiny_bgr(), filename="a.png")
    ref = save_session(lot, {"project": "24-117", "sample_id": "7718-A", "lot_number": "L-44A"},
                        [entry], in_place=True)

    cat = Catalog(tmp_path)
    assert cat.index_session(ref.path)

    by_part = cat.search("7718-A")
    assert len(by_part) == 1 and by_part[0]["path"] == str(ref.path)

    by_heat = cat.search("HEAT-9001")
    assert len(by_heat) == 1 and by_heat[0]["path"] == str(ref.path)

    assert cat.search("no-such-value-xyz") == []


# ======================================================================
# rename_to_template / apply_renames / undo
# ======================================================================

def test_rename_preview_apply_and_undo(tmp_path):
    ws = Workspace(tmp_path)
    proj = ws.create_project("OldJobName")
    sample = ws.create_sample(proj, "OldPart")
    lot = ws.create_lot(proj, sample, "OldLot")

    profile = ws.profile
    profile.level("project").folder_template = "{id} - Renamed"
    ws.set_profile(profile)

    plan = rename_to_template(ws, ws.root, profile)
    assert any(old == proj for old, new in plan)
    project_rename = next((old, new) for old, new in plan if old == proj)
    assert project_rename[1].name == "OldJobName - Renamed"

    cat = Catalog(tmp_path)
    applied = apply_renames(ws, plan, catalog=cat)
    assert len(applied) == len(plan)
    new_proj = next(new for old, new in applied if old == proj)
    assert new_proj.exists()
    assert not proj.exists()
    assert (new_proj / "project.json").exists()

    # undo: reverse the plan
    reverse = [(new, old) for old, new in applied]
    undone = apply_renames(ws, reverse, catalog=cat)
    assert len(undone) == len(applied)
    assert proj.exists()
    assert not new_proj.exists()


def test_rename_preview_is_readonly(tmp_path):
    ws = Workspace(tmp_path)
    proj = ws.create_project("Job1")
    profile = ws.profile
    profile.level("project").folder_template = "{id}-X"
    plan = rename_to_template(ws, ws.root, profile)
    assert proj.exists()  # nothing touched disk
    assert len(plan) == 1


def test_rename_apply_is_collision_safe(tmp_path):
    ws = Workspace(tmp_path)
    proj = ws.create_project("A")
    ws.create_project("A-X")  # will collide with A's renamed target
    profile = ws.profile
    profile.level("project").folder_template = "{id}-X"
    plan = rename_to_template(ws, ws.root, profile)
    applied = apply_renames(ws, plan)
    new_path = next(new for old, new in applied if old == proj)
    assert new_path.exists()
    assert new_path.name != "A-X"  # deduped
    assert "(2)" in new_path.name


# ======================================================================
# context_for_session
# ======================================================================

def test_context_for_session_includes_level_fields(tmp_path):
    ws = _job_part_lot_ws(tmp_path)
    job = ws.create_project("24-117", customer="Acme")
    part = ws.create_sample(job, "7718-A", material_alloy="Ti-6Al-4V")
    lot = ws.create_lot(job, part, "L-44A", heat_number="H-1")
    entry = ImageEntry(image_bgr=_tiny_bgr(), filename="a.png")
    save_session(lot, {"project": "24-117", "sample_id": "7718-A", "lot_number": "L-44A",
                        "operator": "jack"}, [entry], in_place=True)

    ctx = context_for_session(lot, ws.profile)
    assert ctx["project"] == "24-117"
    assert ctx["sample"] == "7718-A"
    assert ctx["lot"] == "L-44A"
    assert ctx["project_label"] == "Job #"
    assert ctx["lot_heat_number"] == "H-1"
    assert ctx["sample_material_alloy"] == "Ti-6Al-4V"
    assert ctx["operator"] == "jack"
