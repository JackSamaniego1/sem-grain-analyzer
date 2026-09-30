"""UPDATE 4 item 5 (UI): the Resolution Profiles card in the Analyze sidebar."""
from __future__ import annotations

import json
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from data.models import AppSettings, ImageEntry  # noqa: E402
from data.resolution_profiles import ProfileStore, get_profiles_path  # noqa: E402
from data.settings import save_settings  # noqa: E402
from tests.conftest import make_mosaic  # noqa: E402

TIMEOUT = 60000


@pytest.fixture
def env(tmp_path, monkeypatch, qapp):
    from ui.ai_probe import reset_device_probe
    from ui.design.theme import apply_theme, set_reduced_motion
    import core.ai_device as aid
    monkeypatch.setattr(aid, "ai_devices", lambda refresh=False: aid.AiDeviceInfo(
        torch_available=True, cpu_available=True, gpu_reason=aid.REASON_NO_GPU_BUILD))
    reset_device_probe()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    root = tmp_path / "ws"
    save_settings(AppSettings(workspace_root=str(root), operator="Tester", theme="dark"))
    set_reduced_motion(True)
    apply_theme(qapp, "dark")
    yield root
    set_reduced_motion(False)
    reset_device_probe()


def _session(root: Path, sizes=((480, 360), (480, 360))) -> Path:
    from data.catalog import Catalog
    from data.session_io import save_session
    from data.workspace import Workspace
    ws = Workspace(root)
    pp = ws.create_project("Proj")
    sp = ws.create_sample(pp, "S-1")
    lp = ws.create_lot(pp, sp, "L-1")
    src = root.parent / "src"
    src.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, (w, h) in enumerate(sizes):
        g, _ = make_mosaic(h=h, w=w, n_grains=40, seed=3 + i)
        p = src / f"img_{i}.png"
        cv2.imwrite(str(p), np.repeat(g[:, :, None], 3, axis=2))
        paths.append(str(p))
    ref = save_session(lp, {"operator": "Tester"}, [ImageEntry(source_path=p) for p in paths],
                       label="Run", catalog=Catalog(root))
    return Path(ref.path)


def _open(qtbot, path: Path):
    from ui.app_shell import AppShell
    from ui.app_state import AppState
    shell = AppShell(AppState(), probe_device=False)
    qtbot.addWidget(shell)
    shell.resize(1500, 950)
    shell.show()
    shell.open_session(path, prefer="analyze")
    qtbot.waitUntil(lambda: shell.state.session is not None, timeout=15000)
    qtbot.waitUntil(lambda: not shell.state.is_loading(), timeout=TIMEOUT)
    return shell


def _card(shell):
    return shell.analyze.profiles_card


def _values(st):
    return [(im.px_override, im.scan_rect, im.profile, im.status) for im in st.images()]


def test_card_off_by_default_and_inert(env, qtbot):
    shell = _open(qtbot, _session(env))
    card, page, st = _card(shell), shell.analyze, shell.state
    assert not card.is_on()
    assert card.body.isHidden() and not card.off_hint.isHidden()
    assert not page.setup_tile.isHidden() and page.profile_tile.isHidden()
    assert not get_profiles_path().exists()          # nothing touched on disk
    # switch persists per PC
    card.set_enabled(True)
    assert st.ui_state["resolution_profiles_on"] is True
    assert not card.body.isHidden()
    card.set_enabled(False)
    assert st.ui_state["resolution_profiles_on"] is False


def test_create_apply_persist_and_label_survives_delete(env, qtbot):
    path = _session(env)
    shell = _open(qtbot, path)
    card, page, st = _card(shell), shell.analyze, shell.state
    card.set_enabled(True)
    im = st.current_image()
    st.set_scan_rect((10, 20, 400, 300), im.uid)
    p = card.create_profile("Zeiss 5kx", 20.0, "µm", 100.0)       # 5 px/µm
    assert p is not None and card.combo.count() == 1
    assert "Zeiss 5kx" in card.combo.itemText(0) and "400 × 300" in card.combo.itemText(0)
    assert p.scan_rect == (10, 20, 400, 300) and p.image_w == 480 and p.image_h == 360
    # apply on the other image
    other = st.images()[1]
    st.set_current_image(other.uid)
    done = card.apply_selected()
    assert done == [other.uid]
    assert st.px_for(other) == pytest.approx(5.0)
    assert st.scan_for(other) == (10, 20, 400, 300)
    assert other.profile["name"] == "Zeiss 5kx"
    assert other.status == "pending"                  # not analysed: stays so
    from ui.pages.image_tree import image_status
    assert image_status(other)[1] == "Not analysed"
    assert page.setup_tile.isHidden() and not page.profile_tile.isHidden()
    assert "Zeiss 5kx" in page.profile_tile.title.text()
    # one undo step restores everything
    assert st.undo()
    assert other.profile is None and st.px_for(other) != pytest.approx(5.0)
    assert not page.setup_tile.isHidden()
    assert st.redo()
    assert other.profile["name"] == "Zeiss 5kx"
    # save + reopen: snapshot restored
    st.save_now()
    st.flush()
    shell.close()
    shell2 = _open(qtbot, path)
    st2 = shell2.state
    o2 = [i for i in st2.images() if i.filename == other.filename][0]
    assert o2.profile and o2.profile["name"] == "Zeiss 5kx"
    assert st2.px_for(o2) == pytest.approx(5.0)
    # deleting the profile keeps the label from the snapshot
    card2 = _card(shell2)
    assert card2.is_on()                              # switch remembered
    assert card2.delete_selected() and card2.selected() is None
    st2.set_current_image(o2.uid)
    assert not shell2.analyze.profile_tile.isHidden()
    assert "Zeiss 5kx" in shell2.analyze.profile_tile.title.text()


def test_size_mismatch_changes_nothing(env, qtbot):
    shell = _open(qtbot, _session(env, sizes=((480, 360), (320, 240))))
    card, st = _card(shell), shell.state
    card.set_enabled(True)
    msgs = []
    shell.analyze.toasts.show_toast = lambda t, m="", s="info", *a, **k: msgs.append((t, m, s))
    assert card.create_profile("Big", 1.0, "µm", 10.0) is not None
    small = [i for i in st.images() if i.shape[:2] == (240, 320)][0]
    st.set_current_image(small.uid)
    before = _values(st)
    assert card.apply_selected() == []
    assert _values(st) == before
    assert any(s == "warning" and "480 x 360" in m for _t, m, s in msgs)


def test_rename_delete_import_export(env, qtbot, tmp_path):
    shell = _open(qtbot, _session(env))
    card = _card(shell)
    card.set_enabled(True)
    msgs = []
    shell.analyze.toasts.show_toast = lambda t, m="", s="info", *a, **k: msgs.append((t, m, s))
    card.create_profile("A", 1.0, "µm", 10.0)
    card.create_profile("B", 500.0, "nm", 50.0)
    assert card.combo.count() == 2
    # duplicate name (case-insensitive) -> ProfileError text shown as-is
    assert card.create_profile("a", 1.0, "µm", 10.0) is None
    assert msgs[-1][2] == "danger" and msgs[-1][1]
    card.combo.setCurrentIndex(card.combo.findData(ProfileStore().find_by_name("A").id))
    assert card.rename_selected("A renamed")
    assert "A renamed" in card.combo.currentText()
    out = tmp_path / "export.json"
    assert card.export_to(out) == 2 and json.loads(out.read_text(encoding="utf-8"))
    assert card.delete_selected()
    assert card.combo.count() == 1
    rep = card.import_from(out)
    assert rep is not None and rep.added == 1 and card.combo.count() == 2


def test_new_profile_runs_auto_find_then_opens_window(env, qtbot):
    shell = _open(qtbot, _session(env))
    card, st = _card(shell), shell.state
    card.set_enabled(True)
    assert card.start_new_profile()
    qtbot.waitUntil(lambda: card.dialog is not None, timeout=TIMEOUT)
    dlg = card.dialog
    assert dlg.scan_rect is not None and dlg.image_size == (480, 360)
    dlg.name.setText("From dialog")
    dlg.length.setValue(10.0)
    dlg.pixels.setValue(50.0)
    dlg._check()
    assert card.store.find_by_name("From dialog") is not None
    assert "From dialog" in card.combo.currentText()


# ======================================================================
# review follow-ups: out-of-date results, profile label, undo, gate
# ======================================================================

class _Toasts:
    def __init__(self):
        self.msgs = []

    def show_toast(self, title, body="", sev="info", *a, **k):
        self.msgs.append((title, body, sev))


def _state(qtbot, path: Path, analyse=()):
    """AppState on ``path``; images at the indexes in ``analyse`` analysed
    (threshold mode) at the session scale 2 px/µm."""
    from core.grain_detector import DetectionParams
    from ui.app_state import AppState
    from ui.workers import analyze_image, read_image
    st = AppState()
    st.open_session(path)
    qtbot.waitUntil(lambda: st.session is not None, timeout=15000)
    qtbot.waitUntil(lambda: not st.is_loading(), timeout=TIMEOUT)
    ims = sorted(st.images(), key=lambda i: i.filename)
    if analyse:
        st.set_calibration(2.0)
    for i in analyse:
        raw = analyze_image(read_image(ims[i].path), 2.0,
                            DetectionParams(detection_mode="threshold"), draw_overlay=False)
        st.set_result(ims[i].uid, raw)
    qtbot.waitUntil(lambda: all(ims[i].status == "done" and ims[i].result is not None
                                for i in analyse) and not st.is_filtering(), timeout=TIMEOUT)
    return st


def _ims(st):
    return sorted(st.images(), key=lambda i: i.filename)


def _profile(name="P", nm_per_px=200.0, unit="µm", rect=(10, 20, 400, 300), size=(480, 360)):
    return ProfileStore().add(name, nm_per_px, unit=unit, scan_rect=rect, image_size=size)


def _full(im):
    return (im.px_override, im.scale_source, im.scan_rect, im.scan_source,
            dict(im.profile) if im.profile else None, im.status, id(im.result))


def _reopen(qtbot, st, path):
    st.save_now()
    st.flush()
    return _state(qtbot, path)


def test_one_undo_step_restores_scale_scan_label_and_status(env, qtbot):
    from ui.resolution_profile_ops import apply_profile
    st = _state(qtbot, _session(env), analyse=(0,))
    a, b = _ims(st)
    before = [_full(a), _full(b)]
    n0 = st.undo_stack.count()
    done, notes = apply_profile(st, _profile(), [a.uid, b.uid])
    assert done == [a.uid, b.uid] and not notes
    assert st.undo_stack.count() == n0 + 1                 # ONE step
    assert a.profile["name"] == "P" and st.px_for(a) == pytest.approx(5.0)
    assert st.stale_reason(a) == "scale and scan area"
    assert st.undo()
    assert [_full(a), _full(b)] == before
    assert st.stale_reason(a) == "" and a.stale == "" and a.status == "done"
    assert st.redo() and a.profile["name"] == "P" and a.stale


def test_apply_refused_for_image_being_analysed(env, qtbot):
    from ui.resolution_profile_ops import apply_profile
    st = _state(qtbot, _session(env), analyse=(0,))
    a, b = _ims(st)
    lock = st.analysis_lock
    lock.bind(busy_uids=lambda: {a.uid}, stop=lambda: None)
    lock.prompt = lambda kind, action: ("continue", False)
    lock.set_active(True)
    try:
        before, n0 = [_full(a), _full(b)], st.undo_stack.count()
        assert apply_profile(st, _profile(), [a.uid, b.uid]) == ([], [])
        assert [_full(a), _full(b)] == before and st.undo_stack.count() == n0
    finally:
        lock.set_active(False)


def test_analysed_image_is_out_of_date_everywhere(env, qtbot):
    """THE rule, profile and by-hand alike: kept result, flagged in the image
    list and results table, left out of reports (named), survives reopen,
    made current again by undo / analysing again."""
    from core.grain_detector import DetectionParams
    from ui.pages import report_builder as rb
    from ui.pages.filmstrip import status_text
    from ui.pages.image_tree import image_status
    from ui.pages.results_table import ResultsTable
    from ui.resolution_profile_ops import apply_profile
    from ui.workers import analyze_image, read_image
    path = _session(env)
    st = _state(qtbot, path, analyse=(0, 1))
    a, b = _ims(st)
    res = a.result
    assert rb.analysed_count(st) == 2 and not rb.stale_note(st)
    apply_profile(st, _profile(rect=None), [a.uid])
    assert a.result is res and a.status == "done"          # nothing deleted
    assert st.stale_reason(a) == "scale" and a.stale == "scale"
    assert image_status(a) == ("warning", "Needs re-analysis")
    assert status_text(a) == ("warning", "Needs re-analysis")
    assert "Needs re-analysis" in a.tooltip()
    table = ResultsTable(st)
    qtbot.addWidget(table)
    rows = {r["uid"]: r for r in table.rows()}
    assert rows[a.uid]["status"] == "Needs re-analysis — scale changed"
    assert rows[b.uid]["status"] == "Analysed"
    assert rb.analysed_count(st) == 1
    assert [i.display_name for i in rb.collect_inputs(st)] == [b.display_name]
    assert a.display_name in rb.stale_note(st)
    # survives save / reopen
    st2 = _reopen(qtbot, st, path)
    a2, b2 = _ims(st2)
    assert a2.result is not None and st2.stale_reason(a2) == "scale" and a2.stale
    assert a2.profile and a2.scale_source == "profile"
    # by hand: the same rule; the old scale back makes it current again
    st2.set_calibration(4.0, b2.uid)
    assert st2.stale_reason(b2) == "scale" and b2.stale
    st2.set_calibration(2.0, b2.uid)
    assert st2.stale_reason(b2) == "" and not b2.stale
    st2.set_scan_rect((0, 0, 100, 100), b2.uid)
    assert st2.stale_reason(b2) == "scan area"
    st2.reset_image_scan_rect(b2.uid)
    assert st2.stale_reason(b2) == ""
    # analysing again clears it
    st2.set_result(a2.uid, analyze_image(read_image(a2.path), st2.px_for(a2),
                                         DetectionParams(detection_mode="threshold")))
    qtbot.waitUntil(lambda: a2.status == "done" and not st2.is_filtering(), timeout=TIMEOUT)
    assert st2.stale_reason(a2) == "" and rb.analysed_count(st2) == 2


def test_apply_to_ticked_images_with_mixed_sizes(env, qtbot):
    from ui.resolution_profile_ops import apply_profile
    st = _state(qtbot, _session(env, sizes=((480, 360), (320, 240), (480, 360))))
    a, small, c = _ims(st)
    s0 = _full(small)
    done, notes = apply_profile(st, _profile(), [a.uid, small.uid, c.uid])
    assert done == [a.uid, c.uid]
    assert len(notes) == 1 and small.display_name in notes[0] and "480 x 360" in notes[0]
    assert _full(small) == s0
    assert st.scan_for(a) == st.scan_for(c) == (10, 20, 400, 300)
    assert st.undo() and a.profile is None and c.profile is None


def test_reload_keeps_profile_drops_stale_label_and_old_session_loads(env, qtbot):
    from ui.resolution_profile_ops import apply_profile
    path = _session(env)
    # a session saved before profiles existed (no key at all) loads without one
    mf = path / "manifest.json"
    d = json.loads(mf.read_text(encoding="utf-8"))
    for e in d["images"]:
        e.pop("resolution_profile", None)
    mf.write_text(json.dumps(d), encoding="utf-8")
    st = _state(qtbot, path)
    assert all(i.profile is None and i.scale_source != "profile" for i in st.images())
    a, b = _ims(st)
    apply_profile(st, _profile(), [a.uid, b.uid])
    b.px_override = 9.0                     # changed outside the ops (older program)
    st._meta_dirty = True
    st2 = _reopen(qtbot, st, path)
    a2, b2 = _ims(st2)
    assert a2.profile["name"] == "P" and a2.scale_source == a2.scan_source == "profile"
    assert b2.profile is None and b2.scale_source != "profile"     # no longer matches


@pytest.mark.parametrize("length,unit,pixels,px_per_um", [
    (500.0, "nm", 50.0, 100.0),            # 10 nm/px
    (20.0, "µm", 100.0, 5.0),              # 200 nm/px
    (0.01, "mm", 100.0, 10.0),             # 100 nm/px
])
def test_unit_conversion(env, qtbot, length, unit, pixels, px_per_um):
    from ui.pages.resolution_profiles_card import ResolutionProfilesCard
    from ui.resolution_profile_ops import apply_profile
    st = _state(qtbot, _session(env))
    a, b = _ims(st)
    st.set_current_image(a.uid)
    card = ResolutionProfilesCard(st, toasts=_Toasts())
    qtbot.addWidget(card)
    p = card.create_profile(f"U {unit}", length, unit, pixels)
    assert p is not None and p.px_per_um == pytest.approx(px_per_um)
    apply_profile(st, p, [b.uid])
    assert st.px_for(b) == pytest.approx(px_per_um)


def test_delete_and_rename_of_profile_in_use(env, qtbot):
    from ui.pages.resolution_profiles_card import ResolutionProfilesCard
    from ui.resolution_profile_ops import apply_profile, profile_of
    st = _state(qtbot, _session(env))
    a, b = _ims(st)
    st.set_current_image(a.uid)
    card = ResolutionProfilesCard(st, toasts=_Toasts())
    qtbot.addWidget(card)
    p = card.create_profile("In use", 20.0, "µm", 100.0)
    apply_profile(st, p, [b.uid])
    assert card.rename_selected("Renamed")
    assert "Renamed" in card.combo.currentText()
    assert profile_of(b)["name"] == "In use"          # the image keeps its snapshot
    assert card.delete_selected() and card.selected() is None
    assert profile_of(b)["name"] == "In use" and st.px_for(b) == pytest.approx(5.0)


def test_manual_change_after_profile_clears_label(env, qtbot):
    from ui.resolution_profile_ops import apply_profile, profile_of
    st = _state(qtbot, _session(env))
    a, b = _ims(st)
    apply_profile(st, _profile(), [a.uid, b.uid])
    st.set_calibration(3.0, a.uid)
    assert profile_of(a) is None and a.scale_source == "manual"
    st.set_scan_rect((0, 0, 50, 50), b.uid)
    assert profile_of(b) is None and b.scan_source == "manual"
    # scale for all images: label gone; its undo brings the label back
    apply_profile(st, _profile(name="Q"), [a.uid])
    st.set_calibration_all(7.0)
    assert profile_of(a) is None
    assert st.undo() and profile_of(a)["name"] == "Q"


def test_read_only_store_and_profile_error_text(env, qtbot):
    from ui.pages.resolution_profiles_card import ResolutionProfilesCard
    f = get_profiles_path()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"schema_version": 999, "profiles": [
        {"id": "x1", "name": "Newer", "nm_per_px": 100.0, "unit": "nm"}]}), encoding="utf-8")
    st = _state(qtbot, _session(env))
    st.set_current_image(_ims(st)[0].uid)
    toasts = _Toasts()
    card = ResolutionProfilesCard(st, toasts=toasts)
    qtbot.addWidget(card)
    card.set_enabled(True)
    reason = card.store.read_only_reason
    assert card.store.read_only and reason
    assert reason in card.hint.text()
    assert not card.btn_new.isEnabled() and not card.btn_rename.isEnabled()
    assert not card.btn_delete.isEnabled()
    assert card.combo.count() == 1 and "Newer" in card.combo.itemText(0)
    assert card.create_profile("X", 1.0, "µm", 10.0) is None
    assert toasts.msgs[-1] == ("Profile not saved", reason, "danger")
    assert not card.rename_selected("Y")
    assert toasts.msgs[-1] == ("Profile not renamed", reason, "danger")
    assert json.loads(f.read_text(encoding="utf-8"))["schema_version"] == 999
