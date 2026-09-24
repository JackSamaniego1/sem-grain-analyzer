"""
UI ⇄ DATA-09 persistence: first-class filter fields in the manifest, raw
per-image overrides (CLEAR when following the session), reset to the
session default, legacy nested post_filters still loading, and Projects'
trash going through the Workspace API (with Undo = restore).
"""
from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

from data.models import AppSettings, read_json, write_json_atomic  # noqa: E402
from data.settings import save_settings  # noqa: E402
from tests.ui_shell_helpers import make_session  # noqa: E402

TIMEOUT = 90000


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


def _state_open(qtbot, path):
    from ui.app_state import AppState
    st = AppState()
    st.open_session(path)
    qtbot.waitUntil(lambda: st.session is not None, timeout=15000)
    return st


def _analyse(st, qtbot):
    from core.grain_detector import DetectionParams
    from ui.workers import analyze_image
    for im in st.images():
        raw = analyze_image(im.image_bgr, st.px_for(im), DetectionParams(detection_mode="threshold"),
                            st.scan_for(im))
        st.set_result(im.uid, raw)
    qtbot.waitUntil(lambda: all(i.status == "done" for i in st.images()) and not st.is_filtering(),
                    timeout=TIMEOUT)


def _settle(st, qtbot):
    qtbot.waitUntil(lambda: not st.is_filtering(), timeout=TIMEOUT)
    st.flush()
    qtbot.waitUntil(lambda: st.save_state == "saved" and not st.is_dirty(), timeout=TIMEOUT)


def test_filters_and_manual_ids_are_first_class_manifest_fields(env, qtbot):
    path = make_session(env, 2, label="Fields")
    st = _state_open(qtbot, path)
    st.set_calibration(2.0)
    _analyse(st, qtbot)
    im = st.images()[0]
    opts = st.filter_options()
    opts.exclude_border = True
    st.set_filter_options(opts)
    qtbot.waitUntil(lambda: not st.is_filtering(), timeout=TIMEOUT)
    victim = im.result.grains[0].grain_id
    st.delete_grains(im.uid, [victim])
    _settle(st, qtbot)
    m = read_json(path / "manifest.json")
    assert "post_filters" not in (m.get("detection_params") or {})
    assert m["filters"]["exclude_border"] is True
    e0 = next(e for e in m["images"] if e["filename"] == im.filename)
    assert e0["manual_excluded"] == [victim]
    assert e0["filters_override"] is None
    # images follow the session: the resolved session values are NOT frozen in
    assert e0["px_per_um"] == 0.0 and e0["scan_rect"] is None
    assert m["px_per_um"] == 2.0

    st2 = _state_open(qtbot, path)
    im2 = st2.images()[0]
    assert victim in im2.manual and "manual" in im2.excluded[victim]
    assert any("border" in r for r in im2.excluded.values())   # reasons re-derived
    assert im2.result.grain_count == im.result.grain_count
    assert st2.px_for(im2) == 2.0 and im2.px_override == 0.0
    st2.flush()


def test_per_image_override_persists_before_analysis_and_resets(env, qtbot):
    path = make_session(env, 2, label="Override")
    st = _state_open(qtbot, path)
    st.set_calibration(1.5)
    im = st.images()[1]
    st.set_calibration(3.25, im.uid)                # before any analysis
    st.set_scan_rect((5, 5, 150, 150), im.uid)
    _settle(st, qtbot)
    e = next(x for x in read_json(path / "manifest.json")["images"] if x["filename"] == im.filename)
    assert e["px_per_um"] == 3.25 and e["scan_rect"] == [5, 5, 150, 150]
    st2 = _state_open(qtbot, path)
    im2 = st2.images()[1]
    assert im2.px_override == 3.25 and im2.scan_rect == (5, 5, 150, 150)
    st2.reset_image_calibration(im2.uid)
    st2.reset_image_scan_rect(im2.uid)
    assert st2.px_for(im2) == 1.5 and st2.scan_for(im2) is None
    _settle(st2, qtbot)
    e = next(x for x in read_json(path / "manifest.json")["images"] if x["filename"] == im.filename)
    assert e["px_per_um"] == 0.0 and e["scan_rect"] is None


def test_analyze_page_reset_buttons(env, qtbot):
    from ui.app_shell import AppShell
    from ui.app_state import AppState
    path = make_session(env, 1, label="Buttons")
    shell = AppShell(AppState(), probe_device=False)
    qtbot.addWidget(shell)
    shell.show()
    shell.open_session(path)
    qtbot.waitUntil(lambda: shell.state.session is not None, timeout=15000)
    st, ap = shell.state, shell.analyze
    im = st.current_image()
    assert ap.btn_cal_reset.isHidden()
    st.set_calibration(4.0, im.uid)
    assert not ap.btn_cal_reset.isHidden()
    ap.btn_cal_reset.click()
    assert im.px_override == 0.0 and ap.btn_cal_reset.isHidden()
    ap.scan_this.setChecked(True)
    ap._clear_scan()                                 # "Full image" for this image only
    h, w = im.image_bgr.shape[:2]
    assert im.scan_rect == (0, 0, w, h) and not ap.btn_scan_reset.isHidden()
    ap.btn_scan_reset.click()
    assert im.scan_rect is None
    shell.close()


def test_legacy_nested_post_filters_still_load(env, qtbot):
    path = make_session(env, 1, label="Legacy")
    st = _state_open(qtbot, path)
    _analyse(st, qtbot)
    im = st.images()[0]
    victim = im.result.grains[0].grain_id
    st.delete_grains(im.uid, [victim])
    _settle(st, qtbot)
    kept = im.result.grain_count
    # rewrite the manifest in the pre-DATA-09 shape
    m = read_json(path / "manifest.json")
    m["detection_params"]["post_filters"] = {
        "options": m.pop("filters"),
        "images": {im.filename: {"excluded": {str(victim): ["manual"]}, "manual": [victim],
                                 "options": None}}}
    for e in m["images"]:
        e.pop("manual_excluded", None)
        e.pop("filters_override", None)
    write_json_atomic(path / "manifest.json", m)
    st2 = _state_open(qtbot, path)
    im2 = st2.images()[0]
    assert victim in im2.manual and im2.excluded[victim] == ["manual"]
    assert im2.result.grain_count == kept
    st2.flush()


def test_projects_trash_uses_workspace_api_and_undo_restores(env, qtbot):
    from ui.pages.projects_page import trash_node
    from ui.app_state import AppState
    from ui.pages.projects_page import ProjectsPage
    path = make_session(env, 1, label="Trash")
    lot = path.parent
    from data.workspace import Workspace
    ws = Workspace(env)
    dest = trash_node(ws, env, "lot", lot)
    assert not lot.exists() and dest.parent == env / ".trash"
    assert any(Path(t["path"]) == dest for t in ws.list_trash())
    st = AppState()
    host = ProjectsPage(st, None)
    qtbot.addWidget(host)
    host.restore_from_trash(dest)
    qtbot.waitUntil(lambda: lot.exists(), timeout=15000)
    assert (path / "manifest.json").exists()
