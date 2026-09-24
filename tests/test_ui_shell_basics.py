"""
v3 shell (UI-03), Projects page (DATA-06), New Session wizard (DATA-07),
search, and the DET-03 mask-view fix.  Offscreen; everything in tmp_path.
"""
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pytestqt")

from data.models import AppSettings, read_json  # noqa: E402
from data.settings import save_settings  # noqa: E402
from tests.ui_shell_helpers import make_session, mosaic_png  # noqa: E402


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
    assert state.root == env           # never the real Documents folder
    w = AppShell(state, probe_device=False)
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    yield w
    w.close()


def test_settings_are_isolated(env, tmp_path):
    from data.settings import get_settings_path
    assert str(get_settings_path()).startswith(str(tmp_path))


def test_shell_builds_and_every_page_switches(shell):
    assert shell.rail.keys() == ["projects", "analyze", "review", "reports", "settings"]
    for key in ("analyze", "review", "reports", "settings", "projects"):
        shell.go(key)
        assert shell.current_page() == key
        assert shell.stack.currentWidget() is shell.pages[key]
    # menus keep the v2.3 shortcuts and add the new ones
    from PySide6.QtGui import QAction
    shortcuts = {ks.toString() for a in shell.findChildren(QAction) for ks in a.shortcuts()}
    for s in ("Ctrl+N", "Ctrl+O", "Ctrl+S", "Ctrl+E", "Ctrl+K", "Ctrl+R", "F5", "Ctrl+F5",
              "Ctrl+Z", "Del"):
        assert s in shortcuts, s


def test_first_run_shows_empty_state_with_create_project(shell, qtbot):
    qtbot.waitUntil(lambda: not shell.projects.is_loading(), timeout=5000)
    assert shell.projects.stack.currentIndex() == 2          # empty page
    assert "workspace" in shell.projects.empty.title_label.text().lower()
    shell.projects.empty.action_button.click()
    assert shell.projects.form.isVisible() and shell.projects.form.kind == "project"
    shell.projects.form._edits["name"].setText("Alloy study")
    shell.projects.form.ok.click()
    qtbot.waitUntil(lambda: any(p.name == "Alloy study"
                                for p in shell.state.workspace.list_projects()), timeout=5000)


def test_projects_page_lists_sessions_of_a_lot(shell, env, qtbot):
    from ui.app_state import NodeRef
    p1 = make_session(env, 1, label="First")
    make_session(env, 2, label="Second")
    shell.projects.reload()
    shell.state.set_node(NodeRef("lot", p1.parent))
    qtbot.waitUntil(lambda: not shell.projects.is_loading() and len(shell.projects.cards()) == 2,
                    timeout=10000)
    titles = sorted(c.title_text() for c in shell.projects.cards())
    assert titles == ["First", "Second"]
    # tree shows Project > Sample > Lot
    qtbot.waitUntil(lambda: shell.projects._find_item(p1.parent) is not None, timeout=5000)


def test_wizard_creates_hierarchy_and_remembers_values(env, qtbot, tmp_path):
    from ui.app_state import AppState
    from ui.dialogs.new_session_wizard import NewSessionWizard
    state = AppState()
    wiz = NewSessionWizard(state)
    qtbot.addWidget(wiz)
    wiz.fill(project="Alloy 718", sample="S-014", lot="2026-0917-B", label_text="Transverse",
             material="Inconel 718", supplier="Special Metals", instrument="Sigma 300",
             kv=15.0)
    img = mosaic_png(tmp_path / "a.png")
    wiz.add_images([img])
    with qtbot.waitSignal(wiz.session_created, timeout=15000) as blk:
        wiz.create_session()
    sess = Path(blk.args[0])
    assert sess.exists() and (sess / "manifest.json").exists()
    lot, sample, project = sess.parent, sess.parent.parent, sess.parent.parent.parent
    assert project.parent == env
    assert read_json(project / "project.json")["name"] == "Alloy 718"
    assert read_json(sample / "sample.json")["material"] == "Inconel 718"
    assert read_json(lot / "lot.json")["supplier"] == "Special Metals"
    m = read_json(sess / "manifest.json")
    assert m["label"] == "Transverse" and m["instrument"] == "Sigma 300"
    assert m["accelerating_voltage_kv"] == 15.0
    assert len(m["images"]) == 1 and (sess / "images" / m["images"][0]["filename"]).exists()
    assert state.ui_state["wizard"]["project"] == "Alloy 718"
    # a second wizard reuses the remembered, now existing, project/sample/lot
    wiz2 = NewSessionWizard(AppState())
    qtbot.addWidget(wiz2)
    v = wiz2.values()
    assert v["project_path"] is not None and v["sample_path"] is not None and v["lot_path"] is not None
    assert wiz2.f_instrument.text() == "Sigma 300"


def test_search_finds_a_session_and_opens_it(shell, env, qtbot):
    path = make_session(env, 1, label="Needle haystack")
    make_session(env, 1, label="Other")
    shell._run_search("Needle")
    qtbot.waitUntil(lambda: shell.search_popup.isVisible()
                    and shell.search_popup.list.count() == 1, timeout=10000)
    shell._open_search_item()
    qtbot.waitUntil(lambda: shell.state.session is not None, timeout=10000)
    assert shell.state.session.path == path


def test_mask_view_is_not_all_black(qapp):
    """DET-03 / B2: a 0/255 uint8 mask must not be multiplied by 255."""
    from ui.workers import mask_to_display
    m = np.array([[0, 255], [255, 0]], np.uint8)
    out = mask_to_display(m)
    assert out.max() == 255 and out.dtype == np.uint8
    assert (mask_to_display(m // 255) == out).all()   # 0/1 input gives the same picture


def test_canvas_mask_view_renders_white_grains(qapp, tmp_path):
    import cv2
    from core.grain_detector import DetectionParams
    from ui.canvas import GrainCanvas
    from ui.workers import analyze_image
    bgr = cv2.imread(mosaic_png(tmp_path / "m.png"))
    res = analyze_image(bgr, 0.0, DetectionParams(detection_mode="threshold"))
    c = GrainCanvas()
    c.resize(400, 400)
    c.set_image(bgr, res)
    c.set_view("mask")
    img = c.rendered_image().convertToFormat(c.rendered_image().Format.Format_Grayscale8)
    arr = np.frombuffer(img.constBits(), np.uint8, img.sizeInBytes()).reshape(
        img.height(), img.bytesPerLine())[:, :img.width()]
    assert arr.mean() > 40            # grains are white, not an overflowed black frame
