"""
HIER-01 UI + DET-05 / INN-05 UI: user-defined levels in the wizard, report
designer, Settings "Folder structure & naming" and the rename dialog; the
SEM info-bar chip / scan area, metadata scale toasts and the scale-bar
prefill.  Offscreen; everything lives in tmp_path.
"""
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pytestqt")

from data.models import AppSettings, read_json  # noqa: E402
from data.settings import save_settings  # noqa: E402
from tests.ui_shell_helpers import mosaic_png  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch, qapp):
    from ui.design.theme import apply_theme, set_reduced_motion
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    root = tmp_path / "ws"
    save_settings(AppSettings(workspace_root=str(root), operator="Tester", theme="dark"))
    set_reduced_motion(True)
    apply_theme(qapp, "dark")
    yield root
    set_reduced_motion(False)


@pytest.fixture
def shell(env, qtbot):
    from ui.app_shell import AppShell
    from ui.app_state import AppState
    state = AppState()
    assert state.root == env
    w = AppShell(state, probe_device=False)
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    yield w
    w.close()


def _make_lot(env, qtbot, tmp_path, n=3):
    """Wizard in a NEW workspace: Job 24-117 › Part 7718-A › Lot L-44A."""
    from ui.app_state import AppState
    from ui.dialogs.new_session_wizard import NewSessionWizard
    state = AppState()
    assert state.profile.images_location == "lot"          # new workspace default
    wiz = NewSessionWizard(state)
    qtbot.addWidget(wiz)
    wiz.fill(project="24-117", sample="7718-A", lot="L-44A", customer="Acme Aerospace",
             heat_number="HT-90211", operator="Tester")
    wiz.add_images([mosaic_png(tmp_path / f"SEM_{i:04d}.png", seed=3 + i) for i in range(n)])
    with qtbot.waitSignal(wiz.session_created, timeout=20000) as blk:
        wiz.create_session()
    return state, wiz, Path(blk.args[0])


# ======================================================================
# Wizard / in-place storage
# ======================================================================

def test_new_workspace_wizard_stores_images_in_the_lot(env, qtbot, tmp_path):
    state, wiz, lot = _make_lot(env, qtbot, tmp_path)
    assert wiz.step_titles()[:3] == ["Job #", "Part Number", "Lot"]
    assert "Session" not in wiz.step_titles()
    assert lot == env / "24-117" / "7718-A" / "L-44A"
    assert (lot / "manifest.json").exists() and (lot / "lot.json").exists()
    m = read_json(lot / "manifest.json")
    assert len(m["images"]) == 3
    assert all((lot / "images" / im["filename"]).exists() for im in m["images"])
    assert read_json(lot / "lot.json").get("heat_number") == "HT-90211"
    assert not [p for p in lot.iterdir() if p.is_dir() and (p / "manifest.json").exists()]


def test_lot_record_opens_and_autosaves_in_place(env, qtbot, tmp_path):
    state, _wiz, lot = _make_lot(env, qtbot, tmp_path, n=2)
    with qtbot.waitSignal(state.session_opened, timeout=20000):
        state.open_session(lot)
    assert state.session.is_lot and state.current_node.kind == "lot"
    assert state.session.title == "L-44A"
    state.set_calibration(2.5)
    state.flush()
    assert read_json(lot / "manifest.json")["px_per_um"] == pytest.approx(2.5)
    # adding images appends into the same lot folder
    with qtbot.waitSignal(state.images_changed, timeout=20000):
        state.add_images([mosaic_png(tmp_path / "extra.png", seed=40)])
    state.flush()
    assert len(read_json(lot / "manifest.json")["images"]) == 3


# ======================================================================
# Report designer: labels, display names, file name
# ======================================================================

def test_report_uses_profile_labels_and_export_name(env, qtbot, tmp_path):
    from ui.pages import report_builder as rb
    state, _wiz, lot = _make_lot(env, qtbot, tmp_path, n=1)
    with qtbot.waitSignal(state.session_opened, timeout=20000):
        state.open_session(lot)
    hd = rb.hierarchy_defaults(state)
    assert [(h["label"], h["value"]) for h in hd["hierarchy"]] == [
        ("Job #", "24-117"), ("Part Number", "7718-A"), ("Lot", "L-44A")]
    assert hd["export_basename"].startswith("24-117_7718-A_L-44A_Grain_Report_")
    assert "7718-A" in hd["title"] and "L-44A" in hd["title"]
    p = rb.default_export_path(lot, "ignored", "xlsx", basename=hd["export_basename"])
    assert p.parent == lot / "exports" and p.name == hd["export_basename"] + ".xlsx"


def test_overview_table_and_apply_profile(mosaic_bgr, tmp_path):
    from core.grain_detector import DetectionParams
    from reports.model import ReportImageInput
    from ui.pages import report_builder as rb
    from ui.workers import analyze_image
    res = analyze_image(mosaic_bgr, 2.0, DetectionParams(detection_mode="boundary"), None,
                        draw_overlay=False)
    src = tmp_path / "SEM_0001.png"
    import cv2
    cv2.imwrite(str(src), mosaic_bgr)
    hier = [{"key": "project", "label": "Job #", "value": "24-117"},
            {"key": "sample", "label": "Part Number", "value": "7718-A"},
            {"key": "lot", "label": "Lot", "value": "L-44A"}]
    m = rb.build_model([ReportImageInput(image_path=str(src), result=res,
                                         display_name="L-44A_01")],
                       title="Auto title", operator="T", hierarchy=hier,
                       export_basename="24-117_7718-A_L-44A_Grain_Report_20260924",
                       asset_dir=str(tmp_path / "assets"))
    header, rows, total = rb.overview_table(m)
    assert header[:6] == ["#", "Image", "File", "Job #", "Part Number", "Lot"]
    assert rows[0][1:6] == ["L-44A_01", "SEM_0001.png", "24-117", "7718-A", "L-44A"]
    assert len(total) == len(header)
    img_sec = [s for s in m.sections if s.type == "image"][0]
    assert img_sec.title == "L-44A_01"
    # levels renamed: labels follow; the auto title follows; a typed name is kept
    m.export_basename = "My own name"
    hier2 = [dict(h) for h in hier]
    hier2[0]["label"] = "Work Order"
    assert rb.apply_profile(m, {"hierarchy": hier2, "export_basename": "WO_x",
                                "title": "New auto title"})
    assert m.hierarchy[0]["label"] == "Work Order"
    assert m.title == "New auto title"
    assert m.export_basename == "My own name"


# ======================================================================
# Settings ▸ Folder structure & naming
# ======================================================================

def test_settings_rename_level_relabels_everything_live(shell, env, qtbot):
    card = shell.settings_page.naming
    assert card.levels[0].name.text() == "Job #"
    assert not card.btn_save.isEnabled()
    card.levels[0].name.setText("Work Order")
    card.levels[0].name.textEdited.emit("Work Order")
    assert card.is_dirty() and card.dirty.isVisibleTo(card) and card.btn_save.isEnabled()
    fired = []
    shell.state.profile_changed.connect(lambda: fired.append(1))
    assert card.save()
    assert fired
    assert shell.state.profile.level("project").label == "Work Order"
    assert read_json(env / "workspace.json")["levels"][0]["label"] == "Work Order"
    assert not card.is_dirty()
    # a new wizard uses the new word
    from ui.dialogs.new_session_wizard import NewSessionWizard
    wiz = NewSessionWizard(shell.state)
    qtbot.addWidget(wiz)
    assert wiz.step_titles()[0] == "Work Order"


def test_open_wizard_relabels_after_settings_change(shell, env, qtbot, tmp_path):
    """HIER-02: a wizard that already exists picks up renamed levels / id
    labels / fields without losing what the operator typed."""
    from ui.dialogs.new_session_wizard import NEW, NewSessionWizard
    wiz = NewSessionWizard(shell.state)
    qtbot.addWidget(wiz)
    wiz.show()
    wiz.fill(project="24-117", sample="7718-A", lot="L-44Z", heat_number="HT-1")
    img = mosaic_png(tmp_path / "SEM_0001.png", seed=5)
    wiz.add_images([img])
    wiz._go(1)
    assert wiz.step_titles()[0] == "Job #"
    card = shell.settings_page.naming
    card.levels[0].name.setText("Work Order")
    card.levels[0].name.textEdited.emit("Work Order")
    le = card.levels[2]
    n = le.table.rowCount()
    le.add_field()
    le.table.item(n, 0).setText("Hardness HRC")
    assert card.save()
    # relabelled live
    assert wiz.step_titles()[0] == "Work Order"
    assert wiz.steps.steps[0][0] == "Work Order"
    assert "Work Order" in wiz.project_combo.itemText(wiz.project_combo.count() - 1)
    assert "hardness_hrc" in wiz.levels["lot"].edits
    # entries kept
    assert wiz._step == 1
    assert wiz.project_combo.currentData() == NEW and wiz.f_project.text() == "24-117"
    assert wiz.f_lot.text() == "L-44Z"
    assert wiz.values()["fields"]["lot"]["heat_number"] == "HT-1"
    assert wiz.images() == [img] and wiz.img_list.count() == 1
    assert wiz.title.text() == wiz.step_titles()[1]


def test_hidden_wizard_relabels_on_show(env, qtbot):
    """HIER-02: a profile change while the wizard is hidden is applied when
    it is shown again."""
    from ui.app_state import AppState
    from ui.dialogs.new_session_wizard import NewSessionWizard
    state = AppState()
    wiz = NewSessionWizard(state)
    qtbot.addWidget(wiz)
    prof = state.profile
    prof.levels[1].label = "Specimen"
    state.set_profile(prof)          # same object mutated in place
    assert wiz.step_titles()[1] == "Part Number"    # hidden: not rebuilt yet
    wiz.show()
    assert wiz.step_titles()[1] == "Specimen"
    assert wiz.refresh_profile() is False            # up to date: no rebuild


def test_settings_template_validation_blocks_save(shell):
    card = shell.settings_page.naming
    card.t_export.edit.setText("{project}_{nonsense}")
    card.t_export.changed.emit(card.t_export.text())
    assert card.t_export.problem.isVisibleTo(card)
    assert "nonsense" in card.problems_lbl.text()
    assert not card.btn_save.isEnabled()
    assert card.save() is False
    card.revert()
    assert not card.is_dirty()


def test_settings_preview_and_token_insert(shell):
    card = shell.settings_page.naming
    card.t_export.edit.setText("")
    card.t_export.insert("{lot}")
    card.t_export.edit.insert("_report")
    assert card.t_export.text() == "{lot}_report"
    assert "L-44A_report.xlsx" in card.t_export.preview.text()   # sample context
    toks = [a.text() for a in card.t_export.menu.actions()]
    assert any("{sample_label}" in t for t in toks)
    assert any("{lot_heat_number}" in t for t in toks)


def test_settings_add_field_and_storage_mode(shell):
    card = shell.settings_page.naming
    le = card.levels[2]
    n = le.table.rowCount()
    le.add_field()
    le.table.item(n, 0).setText("Hardness HRC")
    assert le.fields()[-1].key == "hardness_hrc"
    card.storage.set_current_index(1, animate=False)
    card._on_edit()
    assert card.profile().images_location == "session"
    assert card.save()
    lot = shell.state.profile.level("lot")
    assert lot.fields[-1].label == "Hardness HRC"
    assert not shell.state.lot_mode()


def test_legacy_workspace_keeps_standard_fields(env, qtbot):
    from data.hierarchy import PRESETS, save_profile
    from ui.app_state import AppState
    from ui.pages.naming_settings import NamingCard
    save_profile(env, PRESETS["project_sample_lot_session"])
    state = AppState()
    card = NamingCard(state)
    qtbot.addWidget(card)
    assert card.levels[1].table.rowCount() > 0          # material, grade … shown
    assert not card.is_dirty()
    card.levels[0].name.setText("Programme")
    card.levels[0].name.textEdited.emit("Programme")
    prof = card.profile()
    assert all(not lv.fields for lv in prof.levels)      # untouched fields stay legacy
    assert prof.images_location == "session"


def test_rename_existing_folders_preview_apply_undo(shell, env, qtbot, tmp_path):
    from data.workspace import Workspace
    from ui.dialogs.rename_folders_dialog import RenameFoldersDialog, compute_plan
    ws = Workspace(env)
    pp = ws.create_project("24-117", customer="Acme")
    prof = shell.state.profile
    prof.levels[0].folder_template = "{id} - {customer}"
    shell.state.set_profile(prof)
    plan = compute_plan(env, shell.state.profile)
    assert [(o.name, n.name) for o, n in plan] == [("24-117", "24-117 - Acme")]
    dlg = RenameFoldersDialog(shell.state, shell)
    qtbot.addWidget(dlg)
    qtbot.waitUntil(lambda: dlg.tree.topLevelItemCount() == 1, timeout=5000)
    assert dlg.tree.topLevelItem(0).text(0) == "Job #"
    assert dlg.btn_apply.isEnabled()
    toasts = []
    shell.toasts.show_toast = lambda *a, **k: toasts.append((a, k))
    dlg.renamed.connect(lambda applied: shell.settings_page._after_rename(applied, undo=False))
    with qtbot.waitSignal(dlg.renamed, timeout=10000):
        dlg.apply()
    assert (env / "24-117 - Acme" / "project.json").exists() and not pp.exists()
    title, _body, _sev, action, undo = toasts[-1][0][:5]
    assert title == "Folders renamed" and action == "Undo"
    undo()
    qtbot.waitUntil(lambda: pp.exists(), timeout=10000)
    assert not (env / "24-117 - Acme").exists()


# ======================================================================
# DET-05 / INN-05
# ======================================================================

def _sem_with_bar(path: Path) -> str:
    import cv2
    from ui.demo import synthetic_sem
    cv2.imwrite(str(path), synthetic_sem(h=400, w=560, n_grains=70, seed=5, info_bar=True))
    return str(path)


def test_info_bar_chip_and_use_as_scan_area(shell, env, qtbot, tmp_path):
    from data.catalog import Catalog
    from data.models import ImageEntry
    from data.session_io import save_session
    from data.workspace import Workspace
    ws = Workspace(env)
    pp = ws.create_project("24-117")
    sp = ws.create_sample(pp, "7718-A")
    lp = ws.create_lot(pp, sp, "L-44A")
    save_session(lp, {}, [ImageEntry(source_path=_sem_with_bar(tmp_path / "bar.png"))],
                 in_place=True, catalog=Catalog(env))
    st = shell.state
    with qtbot.waitSignal(st.session_opened, timeout=20000):
        shell.open_session(lp, prefer="analyze")
    im = st.current_image()
    qtbot.waitUntil(lambda: st.info_bar_for(im) is not None, timeout=10000)
    qtbot.waitUntil(lambda: not shell.analyze.ib_row.isHidden(), timeout=5000)
    info = st.info_bar_for(im)
    x, y, w, h = info["bar_rect"]
    assert y > 300 and w > 400                        # the bottom data bar
    assert shell.analyze.canvas.info_bar_rect() == tuple(info["bar_rect"])
    assert shell.analyze.btn_ib_scan.isEnabled()
    toasts = []
    shell.analyze.toasts.show_toast = lambda *a, **k: toasts.append(a)
    shell.analyze._use_info_bar_scan()
    assert st.session.scan_rect == tuple(info["analysis_rect"])
    assert not shell.analyze.btn_ib_scan.isEnabled()
    assert toasts[-1][3] == "Undo"
    toasts[-1][4]()
    assert st.session.scan_rect is None


def test_metadata_scale_toast_batches_and_undoes(shell, env, qtbot, tmp_path):
    from data.catalog import Catalog
    from data.models import ImageEntry
    from data.session_io import save_session
    from data.workspace import Workspace
    ws = Workspace(env)
    lp = ws.create_lot(ws.create_project("J"), ws.create_sample(ws.resolve_project("J"), "P"), "L")
    save_session(lp, {}, [ImageEntry(source_path=mosaic_png(tmp_path / f"m{i}.png", seed=i))
                          for i in range(2)], in_place=True, catalog=Catalog(env))
    st = shell.state
    with qtbot.waitSignal(st.session_opened, timeout=20000):
        shell.open_session(lp)
    toasts = []
    shell.toasts.show_toast = lambda *a, **k: toasts.append(a)
    uids = [im.uid for im in st.images()]
    for uid in uids:                                    # what probe_metadata does on "high"
        st.set_calibration(4.0, uid)
        st.metadata_calibration.emit(uid, 4.0, "Zeiss", "high", 0.0)
    qtbot.waitUntil(lambda: len(toasts) == 1, timeout=3000)
    title, body, sev, action, undo = toasts[0][:5]
    assert title == "Scale read from image metadata" and action == "Undo"
    assert "2 images" in body and "Zeiss" in body
    undo()
    assert all(im.px_override == 0 for im in st.images())
    # medium confidence is only offered
    st.metadata_calibration.emit(uids[0], 3.0, "ImageJ", "medium", 0.0)
    qtbot.waitUntil(lambda: len(toasts) >= 3, timeout=3000)
    offer = [t for t in toasts if t[0] == "Scale found in image metadata"][0]
    assert st.session.image(uids[0]).px_override == 0
    offer[4]()
    assert st.session.image(uids[0]).px_override == pytest.approx(3.0)


def test_analyze_meta_row_offers_metadata_scale(shell, env, qtbot, tmp_path):
    from data.catalog import Catalog
    from data.models import ImageEntry
    from data.session_io import save_session
    from data.workspace import Workspace
    ws = Workspace(env)
    lp = ws.create_lot(ws.create_project("J"), ws.create_sample(ws.resolve_project("J"), "P"), "L")
    save_session(lp, {}, [ImageEntry(source_path=mosaic_png(tmp_path / "m.png"))],
                 in_place=True, catalog=Catalog(env))
    st = shell.state
    with qtbot.waitSignal(st.session_opened, timeout=20000):
        shell.open_session(lp, prefer="analyze")
    im = st.current_image()
    im.cal_suggestion = (5.0, "FEI", "medium")
    st.sem_metadata_ready.emit(im.uid)
    a = shell.analyze
    assert a.meta_row.isVisibleTo(a) and "5 px/µm" in a.meta_lbl.text()
    assert a.btn_meta_cal.isVisibleTo(a)
    a._use_meta_cal()
    assert im.px_override == pytest.approx(5.0)
    assert "in use" in a.meta_lbl.text() and not a.btn_meta_cal.isVisibleTo(a)
    # the Analyze page speaks of the lot, not a session
    assert "lot" in a.st_grains._label.text().lower()


def test_scale_bar_prefill_and_length_suggestion(qapp, tmp_path):
    import cv2
    from core.scale_bar import find_scale_bar_line
    from ui.calibration_dialog import CalibrationDialog, suggest_bar_length_um
    assert suggest_bar_length_um(150, 7.5) == 20.0            # nice number
    assert suggest_bar_length_um(151, 7.5) == 20.0            # within 4 %
    assert suggest_bar_length_um(100, 3.0) == pytest.approx(33.3)
    assert suggest_bar_length_um(0, 3.0) is None
    img = cv2.imread(_sem_with_bar(tmp_path / "b.png"))
    bar = find_scale_bar_line(img)
    assert bar is not None and bar["length_px"] > 50
    dlg = CalibrationDialog(img)
    assert dlg.prefill(bar, 0.5)
    assert dlg.canvas.point_count() == 2
    assert dlg.canvas.pixel_distance() == pytest.approx(bar["rect"][2] - 1, abs=1)
    assert dlg.btn_apply.isEnabled() and dlg.lbl_auto.isVisibleTo(dlg)
    assert dlg.unit_combo.currentText() == "nm" and dlg.length_spin.value() == 500.0
    got = []
    dlg.calibration_set.connect(got.append)
    dlg._apply()
    assert got and got[0] == pytest.approx(dlg.canvas.pixel_distance() / 0.5)
    assert not CalibrationDialog(img).prefill(None)


def test_canvas_draws_info_bar_hatch(qapp):
    from ui.canvas import GrainCanvas
    c = GrainCanvas()
    c.resize(300, 200)
    img = np.full((100, 150, 3), 128, np.uint8)
    c.set_image(img)
    c.set_info_bar_rect((0, 80, 150, 20))
    assert c.info_bar_rect() == (0, 80, 150, 20)
    assert not c.grab().isNull()
    c.set_info_bar_rect(None)
    assert c.info_bar_rect() is None


def test_breadcrumb_uses_level_labels(shell, env, qtbot, tmp_path):
    from ui.app_state import NodeRef
    _state, _wiz, lot = _make_lot(env, qtbot, tmp_path, n=1)
    shell.projects.reload()
    shell.state.set_node(NodeRef("lot", lot))
    segs = shell.crumb.segments()
    assert any("Job # 24-117" in s for s in segs)
    assert any("Part Number 7718-A" in s for s in segs)
